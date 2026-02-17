# mcp-codebase-index

[![PyPI version](https://img.shields.io/pypi/v/mcp-codebase-index)](https://pypi.org/project/mcp-codebase-index/)
[![CI](https://github.com/MikeRecognex/mcp-codebase-index/actions/workflows/ci.yml/badge.svg)](https://github.com/MikeRecognex/mcp-codebase-index/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)]()

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

### Configuring with OpenClaw

Install the package on the machine where OpenClaw is running:

```bash
# Local install
pip install "mcp-codebase-index[mcp]"

# Or inside a Docker container / remote VPS
docker exec -it openclaw bash
pip install "mcp-codebase-index[mcp]"
```

Add the MCP server to your OpenClaw agent config (`openclaw.json`):

```json
{
  "agents": {
    "list": [{
      "id": "main",
      "mcp": {
        "servers": [
          {
            "name": "codebase-index",
            "command": "mcp-codebase-index",
            "env": {
              "PROJECT_ROOT": "/path/to/project"
            }
          }
        ]
      }
    }]
  }
}
```

Restart OpenClaw and verify the connection:

```bash
openclaw mcp list
```

All 17 tools will be available to your agent.

**Performance note:** OpenClaw's default MCP integration via mcporter spawns a fresh server process per tool call, which means the index is rebuilt each time (~1-2s for small projects, longer for large ones). For persistent connections, use the [openclaw-mcp-adapter](https://github.com/androidStern-personal/openclaw-mcp-adapter) plugin, which connects once at startup and keeps the server running:

```bash
pip install openclaw-mcp-adapter
```

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

This project is dual-licensed:

- **AGPL-3.0** for open-source use — see [LICENSE](LICENSE)
- **Commercial License** for proprietary use — see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)

If you're using mcp-codebase-index as a standalone MCP server for development, the AGPL-3.0 license applies at no cost. If you're embedding it in a proprietary product or offering it as part of a hosted service, you'll need a commercial license. See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) for details.
