# mcp-codebase-index: Architecture & Design Document

**Version:** 0.2.0
**Date:** February 2026

---

## 1. What Is mcp-codebase-index?

`mcp-codebase-index` is a structural codebase indexer that parses source files into rich metadata — functions, classes, imports, dependency graphs, and cross-file call chains — then exposes that metadata through 17 query tools via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).

The core idea: AI coding assistants waste enormous amounts of context window reading entire files when they only need to know *what's in* a file, *where a symbol is defined*, or *what depends on what*. This tool gives them surgical access to exactly the structural information they need.

### Key design choices

- **Zero runtime dependencies.** Python analysis uses the stdlib `ast` module. TypeScript/JS uses regex. No tree-sitter, no LSP, no external binaries.
- **In-process indexing.** The entire index lives in memory as Python dataclasses. No database, no filesystem cache. Rebuilds in ~1-2 seconds for typical projects.
- **MCP-native.** Designed from the ground up as an MCP server, not a CLI tool with MCP bolted on. Every query function maps 1:1 to an MCP tool.
- **Backward-compatible output controls.** As of v0.2.0, every tool that can produce large output accepts optional size-limiting parameters. All defaults preserve the original behavior.
- **Dual licensed.** AGPL-3.0 for open-source use, commercial license available for proprietary embedding.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Client                               │
│            (Claude Code, OpenClaw, any MCP-compatible agent)    │
└──────────────────────────────┬──────────────────────────────────┘
                               │ MCP protocol (stdio)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                         server.py                               │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Tool schemas │  │  call_tool   │  │   _format_result      │  │
│  │ (17 tools)   │  │  dispatch    │  │   (JSON serializer)   │  │
│  └──────────────┘  └──────┬───────┘  └───────────────────────┘  │
└───────────────────────────┼─────────────────────────────────────┘
                            │ function calls
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       query_api.py                              │
│                                                                 │
│  create_project_query_functions(index) -> dict[str, Callable]   │
│                                                                 │
│  17 closures bound to a ProjectIndex:                           │
│    get_project_summary, list_files, get_functions,              │
│    get_classes, get_imports, get_function_source,               │
│    get_class_source, find_symbol, get_dependencies,             │
│    get_dependents, get_change_impact, get_call_chain,           │
│    get_file_dependencies, get_file_dependents,                  │
│    search_codebase, get_structure_summary, get_lines            │
└───────────────────────────┬─────────────────────────────────────┘
                            │ reads from
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        models.py                                │
│                                                                 │
│  ProjectIndex          (the whole project)                      │
│    ├── files: dict[str, StructuralMetadata]                     │
│    ├── global_dependency_graph                                  │
│    ├── reverse_dependency_graph                                 │
│    ├── import_graph / reverse_import_graph                      │
│    └── symbol_table: symbol_name -> file_path                   │
│                                                                 │
│  StructuralMetadata    (one per file)                           │
│    ├── lines, total_lines, total_chars                          │
│    ├── functions: list[FunctionInfo]                            │
│    ├── classes: list[ClassInfo]                                 │
│    ├── imports: list[ImportInfo]                                │
│    ├── sections: list[SectionInfo]                              │
│    └── dependency_graph (intra-file)                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │ built by
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    project_indexer.py                           │
│                                                                 │
│  ProjectIndexer                                                 │
│    1. Discover files (glob + exclude patterns)                  │
│    2. Annotate each file (dispatch by extension)                │
│    3. Build global symbol table                                 │
│    4. Build cross-file import graph                             │
│    5. Build reverse import graph                                │
│    6. Build global dependency graph                             │
│    7. Build reverse dependency graph                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │ dispatches to
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       annotator.py                              │
│                     (dispatch layer)                            │
│                                                                 │
│  .py  ──► python_annotator.py    (ast.parse)                    │
│  .ts/.tsx/.js/.jsx ──► typescript_annotator.py  (regex)         │
│  .md/.txt/.rst ──► text_annotator.py  (heading detection)       │
│  other ──► generic_annotator.py  (line counts only)             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Module-by-Module Breakdown

### 3.1 `models.py` — Data Structures

All data is represented as Python `dataclass` objects. No ORM, no protobuf, no serialization framework.

| Model | Purpose | Key fields |
|-------|---------|------------|
| `LineRange` | 1-indexed inclusive line range | `start`, `end` |
| `FunctionInfo` | A function or method | `name`, `qualified_name`, `line_range`, `parameters`, `decorators`, `docstring`, `is_method`, `parent_class` |
| `ClassInfo` | A class | `name`, `line_range`, `base_classes`, `methods` (list of `FunctionInfo`), `decorators`, `docstring` |
| `ImportInfo` | An import statement | `module`, `names`, `alias`, `line_number`, `is_from_import` |
| `SectionInfo` | A heading in a text file | `title`, `level`, `line_range` |
| `StructuralMetadata` | Complete metadata for one file | All of the above, plus raw `lines` and an intra-file `dependency_graph` |
| `ProjectIndex` | The whole project | `files` dict, cross-file graphs, symbol table, stats |

### 3.2 `annotator.py` — Language Dispatch

A thin routing layer that inspects file extensions and calls the appropriate annotator:

| Extension | Annotator | Technique |
|-----------|-----------|-----------|
| `.py`, `.pyw` | `python_annotator.py` | `ast.parse()` — full AST walk. Extracts functions, classes, methods, decorators, docstrings, parameters, and builds an intra-file dependency graph by analyzing name references in function bodies. |
| `.ts`, `.tsx`, `.js`, `.jsx` | `typescript_annotator.py` | Regex pattern matching. Detects `function`, `const ... = () =>`, `class`, `interface`, `type`, and `import` statements. No AST parsing — fast and dependency-free. |
| `.md`, `.txt`, `.rst` | `text_annotator.py` | Heading detection: `#`-style markdown, `===`/`---` underlines, numbered sections (`1.2.3`), and ALL-CAPS lines. |
| Everything else | `generic_annotator.py` | Line counts only. |

### 3.3 `project_indexer.py` — The Indexing Pipeline

`ProjectIndexer` is the heart of the system. Given a root directory, it:

1. **Discovers files** — Walks the directory tree using `pathlib.Path.glob()` against include patterns (`**/*.py`, `**/*.ts`, etc.), filtering out excludes (`__pycache__`, `node_modules`, `.git`, `.venv`). Respects a max file size (default 500KB).

2. **Annotates each file** — Dispatches to the appropriate annotator, producing a `StructuralMetadata` for each file.

3. **Builds the global symbol table** — Maps every function, method, and class name to the file where it's defined. `MyClass.run` -> `src/engine.py`. First-found wins for duplicates.

4. **Builds the import graph** — For each file's imports, resolves the module path to an actual project file. Python resolution converts dots to slashes and checks `src/`, `lib/` prefixes. TypeScript resolution handles relative paths (`./utils`) and path aliases (`@/lib/utils`).

5. **Builds the reverse import graph** — Inverts the import graph so you can ask "what files import from this file?"

6. **Builds the global dependency graph** — Merges per-file dependency graphs and resolves cross-file references via the symbol table. Also scans function/class bodies for references to imported names that the per-file AST analysis misses.

7. **Builds the reverse dependency graph** — Inverts the dependency graph so you can ask "what calls this function?"

The result is a `ProjectIndex` — a single in-memory object that contains everything needed to answer structural queries about the codebase.

### 3.4 `query_api.py` — Query Functions

Two factory functions produce dictionaries of query closures:

- `create_file_query_functions(metadata)` — 13 functions for single-file queries
- `create_project_query_functions(index)` — 17 functions for project-wide queries (these are the ones exposed as MCP tools)

The closure pattern means each function captures a reference to the index and needs no additional state. Query functions return plain `dict`, `list`, or `str` values — no custom objects, no serialization needed.

### 3.5 `server.py` — MCP Server

The MCP server is a thin layer that:

1. Defines 17 tool schemas (name, description, JSON Schema for parameters)
2. On startup, builds the project index from `PROJECT_ROOT`
3. Routes incoming `call_tool` requests to the appropriate query function
4. Formats results as JSON text and returns `TextContent` responses
5. Provides a `reindex` tool to rebuild the index after file changes

Communication happens over stdio using the MCP SDK's `stdio_server()`.

---

## 4. The 17 MCP Tools

### Project Overview

| Tool | What it returns |
|------|-----------------|
| `get_project_summary` | High-level stats: file count, packages, top classes/functions |
| `list_files` | Sorted list of indexed file paths, optional glob filter |

### Code Structure

| Tool | What it returns |
|------|-----------------|
| `get_structure_summary` | Per-file or project-level summary of functions, classes, imports |
| `get_functions` | All functions with name, qualified name, line range, params, file |
| `get_classes` | All classes with name, line range, methods, base classes, file |
| `get_imports` | All imports with module, names, line number, file |
| `get_function_source` | Full source code of a function/method (auto-locates the file) |
| `get_class_source` | Full source code of a class (auto-locates the file) |
| `get_lines` | Specific line range from a file |

### Dependency Analysis

| Tool | What it returns |
|------|-----------------|
| `find_symbol` | Where a symbol is defined: `{file, line, type}` |
| `get_dependencies` | What a symbol calls/uses (outgoing edges) |
| `get_dependents` | What calls/uses a symbol (incoming edges) |
| `get_change_impact` | Direct + transitive dependents (cascading impact) |
| `get_call_chain` | Shortest dependency path between two symbols (BFS) |
| `get_file_dependencies` | Files this file imports from |
| `get_file_dependents` | Files that import from this file |

### Search & Maintenance

| Tool | What it returns |
|------|-----------------|
| `search_codebase` | Regex search across all files: `[{file, line_number, content}]` |
| `reindex` | Rebuilds the entire index (for use after file changes) |

---

## 5. Output Size Controls (v0.2.0)

### The problem

When used inside messaging-based AI agents like OpenClaw, tool responses become part of the conversation history that's sent on every subsequent turn. A single `get_functions()` call on a large project can return thousands of entries. A `get_class_source()` call can return hundreds of lines. These unbounded responses accumulate in the conversation and blow up token budgets.

### The solution

Every tool that can produce large output now accepts optional size-limiting parameters. All defaults preserve the original behavior — existing clients see no change.

### Parameter types

**`max_results`** — Caps the number of items in list-returning tools.

```
get_functions(max_results=20)        → at most 20 function entries
list_files(pattern="*.py", max_results=10)  → at most 10 file paths
search_codebase("TODO", max_results=5)      → at most 5 matches
```

**`max_lines`** — Caps the number of source lines in source-returning tools. When truncation occurs, a message is appended:

```
get_function_source("process_data", max_lines=10)

→ def process_data(items):
      """Process a list of items."""
      results = []
      for item in items:
          validated = validate(item)
          if validated:
              transformed = transform(validated)
              results.append(transformed)
          else:
              logger.warning("Invalid item: %s", item)
  ... (truncated to 10 lines)
```

**`max_direct` / `max_transitive`** — Independently caps the two lists returned by `get_change_impact`:

```
get_change_impact("helper", max_direct=5, max_transitive=10)

→ {
    "direct": [...at most 5 items...],
    "transitive": [...at most 10 items...]
  }
```

### Complete parameter reference

| Tool | Parameter | Type | Default | Meaning |
|------|-----------|------|---------|---------|
| `list_files` | `max_results` | int | 0 (unlimited) | Max file paths returned |
| `get_functions` | `max_results` | int | 0 (unlimited) | Max function entries |
| `get_classes` | `max_results` | int | 0 (unlimited) | Max class entries |
| `get_imports` | `max_results` | int | 0 (unlimited) | Max import entries |
| `get_dependencies` | `max_results` | int | 0 (unlimited) | Max dependency entries |
| `get_dependents` | `max_results` | int | 0 (unlimited) | Max dependent entries |
| `get_file_dependencies` | `max_results` | int | 0 (unlimited) | Max file paths |
| `get_file_dependents` | `max_results` | int | 0 (unlimited) | Max file paths |
| `search_codebase` | `max_results` | int | 100 | Max search matches |
| `get_function_source` | `max_lines` | int | 0 (unlimited) | Max source lines |
| `get_class_source` | `max_lines` | int | 0 (unlimited) | Max source lines |
| `get_change_impact` | `max_direct` | int | 0 (unlimited) | Max direct dependents |
| `get_change_impact` | `max_transitive` | int | 0 (unlimited) | Max transitive dependents |

**Convention:** `0` means unlimited (no truncation). Any positive integer caps the result.

### Tools without size controls

These tools already produce bounded output and don't need size controls:

- `get_project_summary` — Returns a fixed-format summary string
- `get_structure_summary` — Returns a fixed-format summary string
- `get_lines` — Already bounded by the `start`/`end` parameters
- `find_symbol` — Returns a single `{file, line, type}` dict
- `get_call_chain` — Returns a single path (typically short)
- `reindex` — Returns a confirmation message

---

## 6. How AI Agents Use It

### The typical workflow

An AI agent connected to this MCP server follows a drill-down pattern:

1. **Orient** — Call `get_project_summary()` to understand the project layout
2. **Locate** — Call `find_symbol("ClassName")` to find where something is defined
3. **Understand structure** — Call `get_structure_summary("src/module.py")` to see what's in a file without reading it
4. **Read selectively** — Call `get_function_source("process")` to read only the function it needs
5. **Trace dependencies** — Call `get_dependents("process")` or `get_change_impact("process")` to understand what would be affected by changes
6. **Search** — Call `search_codebase("TODO|FIXME")` to find patterns across the project

This is dramatically cheaper than the alternative of reading entire files into context. A 500-line file costs 500 lines of context. A `get_structure_summary` call for that file costs ~10-20 lines.

### Token budget management with size controls

For agents operating under tight token budgets (e.g., OpenClaw with full conversation history), the recommended approach is:

```
# First pass: get an overview with small limits
get_functions(max_results=10)

# Drill into specific items
get_function_source("interesting_function", max_lines=30)

# If you need more, increase the limit
get_function_source("interesting_function", max_lines=100)
```

---

## 7. Installation & Configuration

### Install from PyPI

```bash
pip install "mcp-codebase-index[mcp]"
```

The `[mcp]` extra installs the MCP server dependency. Omit it for programmatic-only use.

### Configure with Claude Code

Add to `.mcp.json` in your project root:

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

### Configure with OpenClaw

Add to your `openclaw.json`:

```json
{
  "agents": {
    "list": [{
      "id": "main",
      "mcp": {
        "servers": [{
          "name": "codebase-index",
          "command": "mcp-codebase-index",
          "env": {
            "PROJECT_ROOT": "/path/to/project"
          }
        }]
      }
    }]
  }
}
```

### Programmatic usage (no MCP)

```python
from mcp_codebase_index.project_indexer import ProjectIndexer
from mcp_codebase_index.query_api import create_project_query_functions

indexer = ProjectIndexer("/path/to/project")
index = indexer.index()
fns = create_project_query_functions(index)

print(fns["get_project_summary"]())
print(fns["find_symbol"]("MyClass"))
print(fns["get_function_source"]("process", max_lines=20))
print(fns["get_change_impact"]("helper", max_direct=5))
```

---

## 8. Project Structure

```
mcp-codebase-index/
├── pyproject.toml                          # Package config, version, dependencies
├── README.md                               # User-facing documentation
├── LICENSE                                 # MIT
├── docs/
│   └── architecture.md                     # This document
├── src/mcp_codebase_index/
│   ├── __init__.py                         # Package version
│   ├── models.py                           # Data structures (dataclasses)
│   ├── annotator.py                        # Language dispatch layer
│   ├── python_annotator.py                 # Python AST-based annotator
│   ├── typescript_annotator.py             # TypeScript/JS regex annotator
│   ├── text_annotator.py                   # Markdown/text heading annotator
│   ├── generic_annotator.py                # Fallback (line counts only)
│   ├── project_indexer.py                  # Project-wide indexing pipeline
│   ├── query_api.py                        # Query functions (17 tools)
│   └── server.py                           # MCP server entry point
└── tests/
    ├── test_markup_python.py               # Python annotator tests
    ├── test_markup_text.py                 # Text annotator tests
    ├── test_markup_typescript.py            # TypeScript annotator tests
    ├── test_project_indexer.py             # Indexer integration tests
    └── test_query_api.py                   # Query API + output size control tests
```

---

## 9. Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | Jan 2026 | Initial release. 17 MCP tools, Python/TS/Markdown support. |
| 0.1.1 | Jan 2026 | README improvements, development setup instructions. |
| 0.1.2 | Jan 2026 | OpenClaw configuration instructions. |
| 0.1.3 | Feb 2026 | Expanded OpenClaw setup docs with Docker and performance notes. |
| **0.2.0** | **Feb 2026** | **Output size controls.** Added `max_results`, `max_lines`, `max_direct`, `max_transitive` parameters to 13 tools. All backward-compatible with defaults preserving original behavior. 11 new tests. |

---

## 10. Design Decisions & Trade-offs

### Why closures instead of a class?

The query API uses a factory function that returns a `dict[str, Callable]` rather than a class with methods. This was deliberate:
- The MCP server dispatches by tool name string, so a dict lookup is natural
- No `self` parameter means the function signatures match the MCP tool schemas exactly
- Single-file and project-wide query functions share the same interface

### Why regex for TypeScript?

A full TypeScript parser (tree-sitter, swc, etc.) would add native dependencies and complicate installation. Regex catches the most common patterns (`function`, `class`, `interface`, `type`, `import`, arrow functions) reliably enough for structural navigation. The trade-off is missing some edge cases (computed property names, complex generic syntax), but for the use case of "help an AI navigate a codebase," this is more than sufficient.

### Why in-memory indexing?

The index rebuilds in ~1-2 seconds for projects under 10K files. Persisting to disk would add complexity (cache invalidation, format versioning, startup I/O) for negligible benefit. The MCP server stays running and the index stays warm in memory. The `reindex` tool handles file changes.

### Why `0` for unlimited instead of `None`?

MCP tool parameters are JSON Schema typed. Using `0` as the "unlimited" sentinel keeps the parameter type as `integer` (simple, universal) rather than requiring a nullable integer or a union type. It also means callers never need to explicitly pass `None` — they just omit the parameter.

### Why truncate after collection instead of limiting during iteration?

For most tools, the full result is collected first and then sliced. This is simpler and avoids duplicating iteration logic. The one exception is `search_codebase`, which early-exits during iteration because the regex scan across all files can be expensive — no point continuing once the limit is reached.
