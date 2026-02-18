#!/usr/bin/env python3
"""Benchmark mcp-codebase-index across small, medium, and large codebases.

Usage:
    # Benchmark against Django and FastAPI (cloned automatically):
    python benchmarks/benchmark.py

    # Benchmark against your own project:
    python benchmarks/benchmark.py /path/to/project

    # Benchmark against multiple projects:
    python benchmarks/benchmark.py /path/to/project1 /path/to/project2

The script will automatically discover symbols to query in each project.
"""

import json
import os
import subprocess
import sys
import time
import tracemalloc

from mcp_codebase_index.project_indexer import ProjectIndexer
from mcp_codebase_index.query_api import create_project_query_functions


# Default projects to benchmark (cloned into /tmp if not present)
DEFAULT_PROJECTS = [
    {
        "name": "FastAPI",
        "path": "/tmp/bench-fastapi",
        "clone_url": "https://github.com/tiangolo/fastapi.git",
    },
    {
        "name": "Django",
        "path": "/tmp/bench-django",
        "clone_url": "https://github.com/django/django.git",
    },
]


def ensure_cloned(project):
    """Clone a project if it doesn't exist locally."""
    if os.path.exists(project["path"]):
        return
    url = project.get("clone_url")
    if not url:
        return
    print(f"  Cloning {project['name']} from {url}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", url, project["path"]],
        capture_output=True,
    )


def discover_symbols(index, query_fns):
    """Automatically discover interesting symbols to benchmark against."""
    symbols = {}

    # Find the largest class for find_symbol and get_change_impact
    all_classes = query_fns["get_classes"](None)
    if all_classes:
        # Pick the class with the most methods
        best_class = max(all_classes, key=lambda c: c.get("method_count", 0))
        symbols["find_symbol"] = best_class["name"]
        symbols["get_change_impact"] = best_class["name"]

    # Find a class method for get_dependencies
    all_functions = query_fns["get_functions"](None)

    def line_count(f):
        lines = f.get("lines", [0, 0])
        if isinstance(lines, list) and len(lines) == 2:
            return lines[1] - lines[0]
        return 0

    methods = [f for f in all_functions if "." in f["name"]]
    if methods:
        mid_methods = sorted(methods, key=line_count)
        symbols["get_dependencies"] = mid_methods[len(mid_methods) // 2]["name"]
    elif all_functions:
        symbols["get_dependencies"] = all_functions[0]["name"]

    # Find a standalone function for get_function_source
    standalone = [f for f in all_functions if "." not in f["name"] and line_count(f) > 5]
    if standalone:
        symbols["get_function_source"] = standalone[0]["name"]
    elif all_functions:
        symbols["get_function_source"] = all_functions[0]["name"]

    return symbols


def measure_index(name, path):
    """Index a project and return timing + stats."""
    print(f"\n{'='*60}")
    print(f"  Benchmarking: {name}")
    print(f"  Path: {path}")
    print(f"{'='*60}")

    tracemalloc.start()
    start = time.perf_counter()
    indexer = ProjectIndexer(path)
    index = indexer.index()
    elapsed = time.perf_counter() - start
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    stats = {
        "name": name,
        "files": index.total_files,
        "lines": index.total_lines,
        "functions": index.total_functions,
        "classes": index.total_classes,
        "index_time_s": round(elapsed, 3),
        "peak_memory_mb": round(peak_mem / 1024 / 1024, 2),
    }

    print(f"\n  Files: {stats['files']}")
    print(f"  Lines: {stats['lines']:,}")
    print(f"  Functions: {stats['functions']}")
    print(f"  Classes: {stats['classes']}")
    print(f"  Index time: {stats['index_time_s']}s")
    print(f"  Peak memory: {stats['peak_memory_mb']} MB")

    return index, stats


def measure_queries(index, symbols):
    """Run key queries and measure response sizes and times."""
    q = create_project_query_functions(index)
    results = {}

    # get_project_summary
    start = time.perf_counter()
    summary = q["get_project_summary"]()
    elapsed = time.perf_counter() - start
    summary_str = summary if isinstance(summary, str) else json.dumps(summary, default=str)
    results["get_project_summary"] = {
        "time_ms": round(elapsed * 1000, 2),
        "response_chars": len(summary_str),
    }

    # find_symbol
    sym = symbols.get("find_symbol")
    if sym:
        start = time.perf_counter()
        result = q["find_symbol"](sym)
        elapsed = time.perf_counter() - start
        result_str = json.dumps(result, default=str)
        results["find_symbol"] = {
            "symbol": sym,
            "time_ms": round(elapsed * 1000, 2),
            "response_chars": len(result_str),
        }

    # get_dependencies
    sym = symbols.get("get_dependencies")
    if sym:
        start = time.perf_counter()
        result = q["get_dependencies"](sym)
        elapsed = time.perf_counter() - start
        result_str = json.dumps(result, default=str)
        results["get_dependencies"] = {
            "symbol": sym,
            "time_ms": round(elapsed * 1000, 2),
            "response_chars": len(result_str),
            "count": len(result) if isinstance(result, list) else 0,
        }

    # get_change_impact
    sym = symbols.get("get_change_impact")
    if sym:
        start = time.perf_counter()
        result = q["get_change_impact"](sym)
        elapsed = time.perf_counter() - start
        result_str = json.dumps(result, default=str)
        direct_count = len(result.get("direct", [])) if isinstance(result, dict) else 0
        transitive_count = len(result.get("transitive", [])) if isinstance(result, dict) else 0
        results["get_change_impact"] = {
            "symbol": sym,
            "time_ms": round(elapsed * 1000, 2),
            "response_chars": len(result_str),
            "direct": direct_count,
            "transitive": transitive_count,
        }

    # get_function_source
    sym = symbols.get("get_function_source")
    if sym:
        start = time.perf_counter()
        result = q["get_function_source"](sym)
        elapsed = time.perf_counter() - start
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        results["get_function_source"] = {
            "symbol": sym,
            "time_ms": round(elapsed * 1000, 2),
            "response_chars": len(result_str),
        }

    # get_functions (all)
    start = time.perf_counter()
    result = q["get_functions"](None)
    elapsed = time.perf_counter() - start
    result_str = json.dumps(result, default=str)
    results["get_functions"] = {
        "time_ms": round(elapsed * 1000, 2),
        "response_chars": len(result_str),
        "count": len(result) if isinstance(result, list) else 0,
    }

    # Total source size for comparison
    total_source_chars = 0
    for file_path in index.files:
        full_path = os.path.join(index.root_path, file_path)
        try:
            with open(full_path, "r", errors="replace") as f:
                total_source_chars += len(f.read())
        except (OSError, UnicodeDecodeError):
            pass
    results["_total_source_chars"] = total_source_chars

    print(f"\n  Query Results:")
    print(f"  {'Query':<25} {'Time':>8} {'Response':>10} {'Detail'}")
    print(f"  {'-'*70}")
    for query_name, data in results.items():
        if query_name.startswith("_"):
            continue
        detail = ""
        if "symbol" in data:
            detail = f"({data['symbol']})"
        if "count" in data:
            detail += f" [{data['count']} items]"
        if "direct" in data:
            detail += f" [direct:{data['direct']}, transitive:{data['transitive']}]"
        print(f"  {query_name:<25} {data['time_ms']:>6.1f}ms {data['response_chars']:>8,} chars  {detail}")

    print(f"\n  Total source size: {total_source_chars:,} chars")

    return results


def print_summary(all_stats, all_results):
    """Print markdown-formatted summary tables."""
    print(f"\n\n{'='*80}")
    print("  BENCHMARK RESULTS")
    print(f"{'='*80}\n")

    print("### Index Build Performance\n")
    print("| Project | Files | Lines | Functions | Classes | Index Time | Peak Memory |")
    print("|---------|------:|------:|----------:|--------:|-----------:|------------:|")
    for s in all_stats:
        print(f"| {s['name']} | {s['files']:,} | {s['lines']:,} | {s['functions']:,} | {s['classes']:,} | {s['index_time_s']}s | {s['peak_memory_mb']} MB |")

    print("\n### Query Response Size (chars)\n")
    header = "| Query | " + " | ".join(s["name"] for s in all_stats) + " |"
    sep = "|-------|" + "|".join("---:" for _ in all_stats) + "|"
    print(header)
    print(sep)

    queries = ["find_symbol", "get_dependencies", "get_change_impact", "get_function_source"]
    for query in queries:
        row = f"| `{query}` |"
        for i in range(len(all_stats)):
            r = all_results[i].get(query, {})
            chars = r.get("response_chars", "—")
            row += f" {chars:,} |" if isinstance(chars, int) else f" {chars} |"
        print(row)

    row = "| **Total source (all files)** |"
    for i in range(len(all_stats)):
        total = all_results[i].get("_total_source_chars", 0)
        row += f" {total:,} |"
    print(row)

    print("\n### Query Response Time\n")
    print(header)
    print(sep)
    for query in queries:
        row = f"| `{query}` |"
        for i in range(len(all_stats)):
            r = all_results[i].get(query, {})
            t = r.get("time_ms", "—")
            row += f" {t}ms |" if isinstance(t, (int, float)) else f" {t} |"
        print(row)

    print("\n### Change Impact Analysis\n")
    print("| Project | Symbol | Direct | Transitive | Response |")
    print("|---------|--------|-------:|-----------:|---------:|")
    for i, s in enumerate(all_stats):
        r = all_results[i].get("get_change_impact", {})
        print(f"| {s['name']} | `{r.get('symbol', '—')}` | {r.get('direct', 0)} | {r.get('transitive', 0)} | {r.get('response_chars', 0):,} chars |")


def main():
    all_stats = []
    all_results = []

    if len(sys.argv) > 1:
        # User-specified projects
        projects = []
        for path in sys.argv[1:]:
            path = os.path.abspath(path)
            name = os.path.basename(path)
            projects.append({"name": name, "path": path})
    else:
        # Default projects
        projects = DEFAULT_PROJECTS
        for p in projects:
            ensure_cloned(p)

    for project in projects:
        if not os.path.exists(project["path"]):
            print(f"\nSkipping {project['name']} — path not found: {project['path']}")
            continue

        try:
            index, stats = measure_index(project["name"], project["path"])
            q = create_project_query_functions(index)
            symbols = discover_symbols(index, q)
            print(f"\n  Auto-discovered symbols: {json.dumps(symbols, indent=4)}")
            results = measure_queries(index, symbols)
            all_stats.append(stats)
            all_results.append(results)
        except Exception as e:
            print(f"\nError benchmarking {project['name']}: {e}")
            import traceback
            traceback.print_exc()

    if all_stats:
        print_summary(all_stats, all_results)


if __name__ == "__main__":
    main()
