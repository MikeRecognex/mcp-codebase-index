"""MCP server for the structural codebase indexer.

Exposes project-wide structural query functions as MCP tools,
enabling Claude Code to navigate codebases efficiently without
reading entire files into context.

Usage:
    PROJECT_ROOT=/path/to/project python -m mcp_codebase_index.server
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import mcp.types as types

from mcp_codebase_index.project_indexer import ProjectIndexer
from mcp_codebase_index.query_api import create_project_query_functions

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

server = Server("mcp-codebase-index")

_project_root: str = ""
_indexer: ProjectIndexer | None = None
_query_fns: dict | None = None


def _format_result(value: object) -> str:
    """Format a query result as readable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, default=str)
    return str(value)


def _build_index() -> None:
    """Build (or rebuild) the project index and query functions."""
    global _project_root, _indexer, _query_fns

    _project_root = os.environ.get("PROJECT_ROOT", os.getcwd())
    print(f"[mcp-codebase-index] Indexing project: {_project_root}", file=sys.stderr)

    _indexer = ProjectIndexer(_project_root)
    index = _indexer.index()
    _query_fns = create_project_query_functions(index)

    print(
        f"[mcp-codebase-index] Indexed {index.total_files} files, "
        f"{index.total_lines} lines, "
        f"{index.total_functions} functions, "
        f"{index.total_classes} classes "
        f"in {index.index_build_time_seconds:.2f}s",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="get_project_summary",
        description="High-level overview of the project: file count, packages, top classes/functions.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="list_files",
        description="List indexed files. Optional glob pattern to filter (e.g. '*.py', 'src/**/*.ts').",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to filter files (uses fnmatch).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
        },
    ),
    Tool(
        name="get_structure_summary",
        description="Structure summary for a file (functions, classes, imports, line counts) or the whole project if no file specified.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to a file in the project. Omit for project-level summary.",
                },
            },
        },
    ),
    Tool(
        name="get_function_source",
        description="Get the full source code of a function or method by name. Uses the symbol table to locate the file automatically.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Function or method name (e.g. 'my_func' or 'MyClass.my_method').",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional file path to narrow the search.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of source lines to return (0 = unlimited, default 0).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_class_source",
        description="Get the full source code of a class by name. Uses the symbol table to locate the file automatically.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Class name.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional file path to narrow the search.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of source lines to return (0 = unlimited, default 0).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_functions",
        description="List all functions (with name, lines, params, file). Filter to a specific file or get all project functions.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to filter to a single file. Omit for all project functions.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
        },
    ),
    Tool(
        name="get_classes",
        description="List all classes (with name, lines, methods, bases, file). Filter to a specific file or get all project classes.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to filter to a single file. Omit for all project classes.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
        },
    ),
    Tool(
        name="get_imports",
        description="List all imports (with module, names, line). Filter to a specific file or get all project imports.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to filter to a single file. Omit for all project imports.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
        },
    ),
    Tool(
        name="find_symbol",
        description="Find where a symbol (function, method, class) is defined. Returns file path, line number, and type.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to find (e.g. 'ProjectIndexer', 'annotate', 'MyClass.run').",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_dependencies",
        description="What does this symbol call/use? Returns list of symbols referenced by the named function or class.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_dependents",
        description="What calls/uses this symbol? Returns list of symbols that reference the named function or class.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_change_impact",
        description="Analyze the impact of changing a symbol. Returns direct dependents and transitive (cascading) dependents.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to analyze.",
                },
                "max_direct": {
                    "type": "integer",
                    "description": "Maximum number of direct dependents to return (0 = unlimited, default 0).",
                },
                "max_transitive": {
                    "type": "integer",
                    "description": "Maximum number of transitive dependents to return (0 = unlimited, default 0).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_call_chain",
        description="Find the shortest dependency path between two symbols (BFS through the dependency graph).",
        inputSchema={
            "type": "object",
            "properties": {
                "from_name": {
                    "type": "string",
                    "description": "Starting symbol name.",
                },
                "to_name": {
                    "type": "string",
                    "description": "Target symbol name.",
                },
            },
            "required": ["from_name", "to_name"],
        },
    ),
    Tool(
        name="get_file_dependencies",
        description="List files that this file imports from (file-level import graph).",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="get_file_dependents",
        description="List files that import from this file (reverse import graph).",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (0 = unlimited, default 0).",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="search_codebase",
        description="Regex search across all indexed files. Returns up to 100 matches with file, line number, and content.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 100, 0 = unlimited).",
                },
            },
            "required": ["pattern"],
        },
    ),
    Tool(
        name="reindex",
        description="Re-index the entire project. Use after making significant file changes to refresh the structural index.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    global _query_fns

    try:
        # Handle reindex separately since it rebuilds state
        if name == "reindex":
            _build_index()
            return [TextContent(type="text", text="Project re-indexed successfully.")]

        if _query_fns is None:
            return [TextContent(type="text", text="Error: index not built yet. Call reindex first.")]

        # Dispatch to the appropriate query function
        if name == "get_project_summary":
            result = _query_fns["get_project_summary"]()

        elif name == "list_files":
            pattern = arguments.get("pattern")
            max_results = arguments.get("max_results", 0)
            result = _query_fns["list_files"](pattern, max_results=max_results)

        elif name == "get_structure_summary":
            file_path = arguments.get("file_path")
            result = _query_fns["get_structure_summary"](file_path)

        elif name == "get_function_source":
            max_lines = arguments.get("max_lines", 0)
            result = _query_fns["get_function_source"](
                arguments["name"],
                arguments.get("file_path"),
                max_lines=max_lines,
            )

        elif name == "get_class_source":
            max_lines = arguments.get("max_lines", 0)
            result = _query_fns["get_class_source"](
                arguments["name"],
                arguments.get("file_path"),
                max_lines=max_lines,
            )

        elif name == "get_functions":
            file_path = arguments.get("file_path")
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_functions"](file_path, max_results=max_results)

        elif name == "get_classes":
            file_path = arguments.get("file_path")
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_classes"](file_path, max_results=max_results)

        elif name == "get_imports":
            file_path = arguments.get("file_path")
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_imports"](file_path, max_results=max_results)

        elif name == "find_symbol":
            result = _query_fns["find_symbol"](arguments["name"])

        elif name == "get_dependencies":
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_dependencies"](arguments["name"], max_results=max_results)

        elif name == "get_dependents":
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_dependents"](arguments["name"], max_results=max_results)

        elif name == "get_change_impact":
            max_direct = arguments.get("max_direct", 0)
            max_transitive = arguments.get("max_transitive", 0)
            result = _query_fns["get_change_impact"](
                arguments["name"], max_direct=max_direct, max_transitive=max_transitive
            )

        elif name == "get_call_chain":
            result = _query_fns["get_call_chain"](
                arguments["from_name"],
                arguments["to_name"],
            )

        elif name == "get_file_dependencies":
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_file_dependencies"](
                arguments["file_path"], max_results=max_results
            )

        elif name == "get_file_dependents":
            max_results = arguments.get("max_results", 0)
            result = _query_fns["get_file_dependents"](
                arguments["file_path"], max_results=max_results
            )

        elif name == "search_codebase":
            max_results = arguments.get("max_results", 100)
            result = _query_fns["search_codebase"](arguments["pattern"], max_results=max_results)

        else:
            return [TextContent(type="text", text=f"Error: unknown tool '{name}'")]

        return [TextContent(type="text", text=_format_result(result))]

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[mcp-codebase-index] Error in {name}: {tb}", file=sys.stderr)
        return [TextContent(type="text", text=f"Error: {e}")]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    _build_index()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main_sync():
    """Synchronous entry point for console_scripts."""
    import asyncio

    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
