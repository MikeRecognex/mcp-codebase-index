# mcp-codebase-index

A structural codebase indexer with an [MCP](https://modelcontextprotocol.io) server for AI-assisted development. Zero runtime dependencies — uses Python's `ast` module for Python analysis and regex for TypeScript/JS. Requires Python 3.11+.

## What It Does

Indexes codebases by parsing source files into structural metadata -- functions, classes, imports, dependency graphs, and cross-file call chains -- then exposes 17 query tools via the Model Context Protocol, enabling Claude Code and other MCP clients to navigate codebases efficiently without reading entire files.

## Language Support

| Language | Method | Extracts |
|----------|--------|----------|
| Python (`.py`) | AST parsing | Functions, classes, methods, imports, dependency graph |
| TypeScript/JS (`.ts`, `.tsx`, `.js`, `.jsx`) | Regex-based | Functions, arrow functions, classes, interfaces, type aliases, imports |
| Markdown/Text (`.md`, `.txt`, `.rst`) | Heading detection | Sections (# headings, underlines, numbered, ALL-CAPS) |
| Other | Generic | Line counts only |

## Installation

```bash
pip install "mcp-codebase-index[mcp]"
```

The `[mcp]` extra includes the MCP server dependency. Omit it if you only need the programmatic API.

For development (from a local clone):

```bash
pip install -e ".[dev,mcp]"
```

## MCP Server

### Running

```bash
# As a console script
PROJECT_ROOT=/path/to/project mcp-codebase-index

# As a Python module
PROJECT_ROOT=/path/to/project python -m mcp_codebase_index.server
```

`PROJECT_ROOT` specifies which directory to index. Defaults to the current working directory.

### Configuring with Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "codebase-index": {
      "command": "mcp-codebase-index",
      "env": {
        "PROJECT_ROOT": "/path/to/project"
      }
    }
  }
}
```

Or using the Python module directly (useful if installed in a virtualenv):

```json
{
  "mcpServers": {
    "codebase-index": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["-m", "mcp_codebase_index.server"],
      "env": {
        "PROJECT_ROOT": "/path/to/project"
      }
    }
  }
}
```

### Available Tools (17)

| Tool | Description |
|------|-------------|
| `get_project_summary` | File count, packages, top classes/functions |
| `list_files` | List indexed files with optional glob filter |
| `get_structure_summary` | Structure of a file or the whole project |
| `get_functions` | List functions with name, lines, params |
| `get_classes` | List classes with name, lines, methods, bases |
| `get_imports` | List imports with module, names, line |
| `get_function_source` | Full source of a function/method |
| `get_class_source` | Full source of a class |
| `find_symbol` | Find where a symbol is defined (file, line, type) |
| `get_dependencies` | What a symbol calls/uses |
| `get_dependents` | What calls/uses a symbol |
| `get_change_impact` | Direct + transitive dependents |
| `get_call_chain` | Shortest dependency path (BFS) |
| `get_file_dependencies` | Files imported by a given file |
| `get_file_dependents` | Files that import from a given file |
| `search_codebase` | Regex search across all files (max 100 results) |
| `reindex` | Re-index the project after file changes (MCP server only) |

## Programmatic Usage

```python
from mcp_codebase_index.project_indexer import ProjectIndexer
from mcp_codebase_index.query_api import create_project_query_functions

indexer = ProjectIndexer("/path/to/project", include_patterns=["**/*.py"])
index = indexer.index()
query_funcs = create_project_query_functions(index)

# Use query functions
print(query_funcs["get_project_summary"]())
print(query_funcs["find_symbol"]("MyClass"))
print(query_funcs["get_change_impact"]("some_function"))
```

## Development

```bash
pip install -e ".[dev,mcp]"
pytest tests/ -v
ruff check src/ tests/
```

## References

The structural indexer was originally developed as part of the [RMLPlus](https://github.com/MikeRecognex/RMLPlus) project, an implementation of the [Recursive Language Models](https://arxiv.org/abs/2512.24601) framework.

## License

MIT
