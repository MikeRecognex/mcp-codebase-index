# mcp-codebase-index - Structural codebase indexer with MCP server
# Copyright (C) 2026 Michael Doyle
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing available. See COMMERCIAL-LICENSE.md for details.

"""MCP server for the structural codebase indexer.

Exposes project-wide structural query functions as MCP tools,
enabling Claude Code to navigate codebases efficiently without
reading entire files into context.

Usage:
    PROJECT_ROOT=/path/to/project python -m mcp_codebase_index.server
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
import traceback

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import mcp.types as types

from mcp_codebase_index.git_tracker import is_git_repo, get_head_commit, get_changed_files
from mcp_codebase_index.project_indexer import ProjectIndexer
from mcp_codebase_index.query_api import create_project_query_functions

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

server = Server("mcp-codebase-index")

_project_root: str = ""
_indexer: ProjectIndexer | None = None
_query_fns: dict | None = None
_is_git: bool = False

# Session usage stats
_session_start: float = time.time()
_tool_call_counts: dict[str, int] = {}
_total_chars_returned: int = 0


def _format_result(value: object) -> str:
    """Format a query result as readable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, default=str)
    return str(value)


def _format_usage_stats() -> str:
    """Format session usage statistics."""
    elapsed = time.time() - _session_start
    total_calls = sum(_tool_call_counts.values())
    # Don't count get_usage_stats itself in the query total
    query_calls = total_calls - _tool_call_counts.get("get_usage_stats", 0)

    # Calculate total source size from the index
    source_chars = 0
    if _indexer and _indexer._project_index:
        source_chars = sum(m.total_chars for m in _indexer._project_index.files.values())

    lines = [
        f"Session duration: {_format_duration(elapsed)}",
        f"Total queries: {query_calls}",
    ]

    if _tool_call_counts:
        lines.append("")
        lines.append("Queries by tool:")
        for tool_name, count in sorted(_tool_call_counts.items(), key=lambda x: -x[1]):
            if tool_name == "get_usage_stats":
                continue
            lines.append(f"  {tool_name}: {count}")

    lines.append("")
    lines.append(f"Total chars returned: {_total_chars_returned:,}")

    if source_chars > 0:
        lines.append(f"Total source in index: {source_chars:,} chars")
        if query_calls > 0 and source_chars > _total_chars_returned:
            # Each query could have required reading the full source
            naive_chars = source_chars * query_calls
            reduction = (1 - _total_chars_returned / naive_chars) * 100 if naive_chars > 0 else 0
            lines.append(
                f"Estimated without indexer: {naive_chars:,} chars "
                f"({naive_chars // 4:,} tokens) over {query_calls} queries"
            )
            lines.append(
                f"Estimated with indexer: {_total_chars_returned:,} chars "
                f"({_total_chars_returned // 4:,} tokens)"
            )
            lines.append(f"Estimated token savings: {reduction:.1f}%")

    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _build_index() -> None:
    """Build (or rebuild) the project index and query functions."""
    global _project_root, _indexer, _query_fns, _is_git

    _project_root = os.environ.get("PROJECT_ROOT", os.getcwd())
    print(f"[mcp-codebase-index] Indexing project: {_project_root}", file=sys.stderr)

    _indexer = ProjectIndexer(_project_root)
    index = _indexer.index()
    _query_fns = create_project_query_functions(index)

    _is_git = is_git_repo(_project_root)
    if _is_git:
        index.last_indexed_git_ref = get_head_commit(_project_root)

    print(
        f"[mcp-codebase-index] Indexed {index.total_files} files, "
        f"{index.total_lines} lines, "
        f"{index.total_functions} functions, "
        f"{index.total_classes} classes "
        f"in {index.index_build_time_seconds:.2f}s",
        file=sys.stderr,
    )


def _matches_include_patterns(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any of the include glob patterns."""
    normalized = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def _maybe_incremental_update() -> None:
    """Check git for changes and incrementally update the index if needed."""
    if not _is_git or _indexer is None or _indexer._project_index is None:
        return

    idx = _indexer._project_index
    changeset = get_changed_files(_project_root, idx.last_indexed_git_ref)
    if changeset.is_empty:
        return

    total_changes = len(changeset.modified) + len(changeset.added) + len(changeset.deleted)

    # Large changeset threshold: full rebuild for branch switches etc.
    if total_changes > 20 and total_changes > idx.total_files * 0.5:
        print(
            f"[mcp-codebase-index] Large changeset ({total_changes} files), "
            f"doing full rebuild",
            file=sys.stderr,
        )
        _build_index()
        return

    # Process deletions
    for path in changeset.deleted:
        if path in idx.files:
            _indexer.remove_file(path)

    # Process modifications and additions
    for path in changeset.modified + changeset.added:
        if _indexer._is_excluded(path):
            continue
        if not _matches_include_patterns(path, _indexer.include_patterns):
            continue
        abs_path = os.path.join(_project_root, path)
        if not os.path.isfile(abs_path):
            continue
        _indexer.reindex_file(path, skip_graph_rebuild=True)

    # Rebuild cross-file graphs once
    _indexer.rebuild_graphs()

    # Update the git ref
    idx.last_indexed_git_ref = get_head_commit(_project_root)

    n_mod = len(changeset.modified)
    n_add = len(changeset.added)
    n_del = len(changeset.deleted)
    print(
        f"[mcp-codebase-index] Incremental update: "
        f"{n_mod} modified, {n_add} added, {n_del} deleted",
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
    Tool(
        name="get_usage_stats",
        description="Session efficiency stats: tool calls, characters returned vs total source, estimated token savings.",
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
    global _query_fns, _total_chars_returned

    # Track tool call counts (including reindex/stats themselves)
    _tool_call_counts[name] = _tool_call_counts.get(name, 0) + 1

    try:
        # Handle reindex separately since it rebuilds state
        if name == "reindex":
            _build_index()
            return [TextContent(type="text", text="Project re-indexed successfully.")]

        # Handle usage stats
        if name == "get_usage_stats":
            return [TextContent(type="text", text=_format_usage_stats())]

        _maybe_incremental_update()

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

        formatted = _format_result(result)
        _total_chars_returned += len(formatted)
        return [TextContent(type="text", text=formatted)]

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
