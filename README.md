<!-- mcp-name: io.github.MikeRecognex/mcp-codebase-index -->
# mcp-codebase-index

[![PyPI version](https://img.shields.io/pypi/v/mcp-codebase-index)](https://pypi.org/project/mcp-codebase-index/)
[![CI](https://github.com/MikeRecognex/mcp-codebase-index/actions/workflows/ci.yml/badge.svg)](https://github.com/MikeRecognex/mcp-codebase-index/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)]()

A structural codebase indexer with an [MCP](https://modelcontextprotocol.io) server for AI-assisted development. Zero runtime dependencies — uses Python's `ast` module for Python analysis and regex-based parsing for TypeScript/JS, Go, and Rust. Requires Python 3.11+.

## What It Does

Indexes codebases by parsing source files into structural metadata -- functions, classes, imports, dependency graphs, and cross-file call chains -- then exposes 18 query tools via the Model Context Protocol, enabling Claude Code and other MCP clients to navigate codebases efficiently without reading entire files.

**Automatic incremental re-indexing:** In git repositories, the index stays up to date automatically. Before every query, the server checks `git diff` and `git status` (~1-2ms). If files changed, only those files are re-parsed and the dependency graph is rebuilt. No need to manually call `reindex` after edits, branch switches, or pulls.

## Language Support

| Language | Method | Extracts |
|----------|--------|----------|
| Python (`.py`) | AST parsing | Functions, classes, methods, imports, dependency graph |
| TypeScript/JS (`.ts`, `.tsx`, `.js`, `.jsx`) | Regex-based | Functions, arrow functions, classes, interfaces, type aliases, imports |
| Go (`.go`) | Regex-based | Functions, methods (receiver-based), structs, interfaces, type aliases, imports, doc comments |
| Rust (`.rs`) | Regex-based | Functions (`pub`/`async`/`const`/`unsafe`), structs, enums, traits, impl blocks, use statements, attributes, doc comments, macro_rules |
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

All 18 tools will be available to your agent.

**Performance note:** The server automatically detects file changes via `git diff` before every query (~1-2ms) and incrementally re-indexes only what changed. However, OpenClaw's default MCP integration via mcporter spawns a fresh server process per tool call, which discards the in-memory index and forces a full rebuild each time (~1-2s for small projects, longer for large ones). This is a mcporter process lifecycle limitation, not a server limitation. For persistent connections, use the [openclaw-mcp-adapter](https://github.com/androidStern-personal/openclaw-mcp-adapter) plugin, which connects once at startup and keeps the server running:

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

### Tip: Encourage the AI to Use Indexed Tools

By default, AI assistants may still read entire files instead of using the indexed tools. Add this to your project's `CLAUDE.md` (or equivalent instructions file) to nudge it:

```
Prefer using codebase-index MCP tools (get_project_summary, find_symbol, get_function_source,
get_class_source, get_dependencies, get_dependents, get_change_impact, get_call_chain, etc.)
over reading entire files when navigating the codebase.
```

This ensures the AI reaches for surgical indexed queries first, which saves tokens and context window.

### Available Tools (18)

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
| `reindex` | Force full re-index (rarely needed — incremental updates happen automatically in git repos) |
| `get_usage_stats` | Session efficiency stats: tool calls, characters returned vs total source, estimated token savings |

## Benchmarks

Tested across four real-world projects on an M-series MacBook Pro, from a small project to CPython itself (1.1 million lines):

### Index Build Performance

| Project | Files | Lines | Functions | Classes | Index Time | Peak Memory |
|---------|------:|------:|----------:|--------:|-----------:|------------:|
| RMLPlus | 36 | 7,762 | 237 | 55 | 0.9s | 2.4 MB |
| FastAPI | 2,556 | 332,160 | 4,139 | 617 | 5.7s | 55 MB |
| Django | 3,714 | 707,493 | 29,995 | 7,371 | 36.2s | 126 MB |
| **CPython** | **2,464** | **1,115,334** | **59,620** | **9,037** | **55.9s** | **197 MB** |

### Query Response Size vs Total Source

Querying CPython — 41 million characters of source code:

| Query | Response | Total Source | Reduction |
|-------|-------:|------------:|----------:|
| `find_symbol("TestCase")` | 67 chars | 41,077,561 chars | **99.9998%** |
| `get_dependencies("compile")` | 115 chars | 41,077,561 chars | **99.9997%** |
| `get_change_impact("TestCase")` | 16,812 chars | 41,077,561 chars | **99.96%** |
| `get_function_source("compile")` | 4,531 chars | 41,077,561 chars | **99.99%** |
| `get_function_source("run_unittest")` | 439 chars | 41,077,561 chars | **99.999%** |

`find_symbol` returns 54-67 characters regardless of whether the project is 7K lines or 1.1M lines. Response size scales with the answer, not the codebase.

`get_change_impact("TestCase")` on CPython found **154 direct dependents and 492 transitive dependents** in 0.45ms — the kind of query that's impossible without a dependency graph. Use `max_direct` and `max_transitive` to cap output to your token budget.

### Query Response Time

All targeted queries return in sub-millisecond time, even on CPython's 1.1M lines:

| Query | RMLPlus | FastAPI | Django | CPython |
|-------|--------:|--------:|-------:|--------:|
| `find_symbol` | 0.01ms | 0.01ms | 0.03ms | 0.08ms |
| `get_dependencies` | 0.00ms | 0.00ms | 0.00ms | 0.01ms |
| `get_change_impact` | 0.02ms | 0.00ms | 2.81ms | 0.45ms |
| `get_function_source` | 0.01ms | 0.02ms | 0.03ms | 0.10ms |

Run the benchmarks yourself: `python benchmarks/benchmark.py`

## How Is This Different from LSP?

LSP answers "where is this function?" — mcp-codebase-index answers "what happens if I change it?" LSP is point queries: one symbol, one file, one position. It can tell you where `LLMClient` is defined and who references it. But ask "what breaks transitively if I refactor `LLMClient`?" and LSP has nothing. This tool returns 11 direct dependents and 31 transitive impacts in a single call — 204 characters. To get the same answer from LSP, the AI would need to chain dozens of find-reference calls recursively, reading files at every step, burning thousands of tokens to reconstruct what the dependency graph already knows.

LSP also requires you to install a separate language server for every language in your project — pyright for Python, vtsls for TypeScript, gopls for Go. Each one is a heavyweight binary with its own dependencies and configuration. mcp-codebase-index is zero dependencies, handles Python + TypeScript/JS + Go + Rust + Markdown out of the box, and every response has built-in token budget controls (`max_results`, `max_lines`). LSP was built for IDEs. This was built for AI.

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
