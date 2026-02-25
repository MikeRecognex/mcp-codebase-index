#!/usr/bin/env python3
"""Generate markdown comparison reports from benchmark results.

Usage:
    python benchmarks/report.py results/runner_TIMESTAMP.jsonl [results/proxy_TIMESTAMP.jsonl]

Can also be imported and called programmatically by runner.py.
"""

import argparse
import json
import os
import time
from collections import defaultdict


def load_jsonl(path: str) -> list[dict]:
    """Load records from a JSONL file."""
    records = []
    if not path or not os.path.exists(path):
        return records
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def merge_token_data(
    runner_records: list[dict],
    proxy_records: list[dict],
) -> list[dict]:
    """Merge proxy token data into runner records by run_id.

    In proxy mode, the proxy captures exact token counts per API call.
    Multiple proxy records may exist per run_id (multiple API round-trips).
    We aggregate them by summing tokens per run_id.
    """
    # Aggregate proxy data by run_id
    proxy_by_run: dict[str, dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "api_calls": 0,
    })

    for rec in proxy_records:
        run_id = rec.get("run_id", "unknown")
        proxy_by_run[run_id]["input_tokens"] += rec.get("input_tokens", 0)
        proxy_by_run[run_id]["output_tokens"] += rec.get("output_tokens", 0)
        proxy_by_run[run_id]["cache_read_input_tokens"] += rec.get(
            "cache_read_input_tokens", 0
        )
        proxy_by_run[run_id]["cache_creation_input_tokens"] += rec.get(
            "cache_creation_input_tokens", 0
        )
        proxy_by_run[run_id]["api_calls"] += 1

    # Merge into runner records
    for rec in runner_records:
        run_id = rec.get("run_id", "")
        if run_id in proxy_by_run:
            rec["proxy_data"] = proxy_by_run[run_id]
            rec["total_tokens"] = (
                proxy_by_run[run_id]["input_tokens"]
                + proxy_by_run[run_id]["output_tokens"]
            )
        elif "estimated_input_tokens" in rec:
            rec["total_tokens"] = (
                rec["estimated_input_tokens"] + rec["estimated_output_tokens"]
            )

    return runner_records


def compute_task_stats(records: list[dict]) -> dict:
    """Compute per-task statistics across modes and repeats.

    Returns: {task_id: {mode: {metric: value}}}
    """
    # Group by task_id and mode
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        grouped[rec["task_id"]][rec["mode"]].append(rec)

    stats = {}
    for task_id, modes in grouped.items():
        stats[task_id] = {}
        for mode, runs in modes.items():
            tokens = [r.get("total_tokens", 0) for r in runs if r.get("total_tokens")]
            times = [r.get("wall_time_s", 0) for r in runs if r.get("wall_time_s")]
            api_calls = [
                r.get("proxy_data", {}).get("api_calls", 0) for r in runs
            ]
            input_tokens = [
                r.get("proxy_data", {}).get("input_tokens", 0)
                or r.get("estimated_input_tokens", 0)
                for r in runs
            ]
            output_tokens = [
                r.get("proxy_data", {}).get("output_tokens", 0)
                or r.get("estimated_output_tokens", 0)
                for r in runs
            ]

            stats[task_id][mode] = {
                "runs": len(runs),
                "median_tokens": _median(tokens),
                "median_time_s": _median(times),
                "median_input_tokens": _median(input_tokens),
                "median_output_tokens": _median(output_tokens),
                "median_api_calls": _median(api_calls),
                "all_tokens": tokens,
                "all_times": times,
            }

    return stats


def _median(values: list) -> float:
    """Compute median of a list of numbers."""
    values = sorted(v for v in values if v)
    if not values:
        return 0
    n = len(values)
    if n % 2 == 1:
        return values[n // 2]
    return (values[n // 2 - 1] + values[n // 2]) / 2


def _fmt_tokens(n: float) -> str:
    """Format token count for display."""
    if n == 0:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n)}"


def _fmt_time(s: float) -> str:
    """Format time in seconds for display."""
    if s == 0:
        return "—"
    if s >= 60:
        return f"{s / 60:.1f}m"
    return f"{s:.1f}s"


def _pct_savings(with_val: float, without_val: float) -> str:
    """Compute percentage savings."""
    if without_val == 0:
        return "—"
    savings = (1 - with_val / without_val) * 100
    if savings > 0:
        return f"**{savings:.0f}%**"
    return f"{savings:.0f}%"


def generate_report(
    runner_results_path: str,
    proxy_results_path: str | None = None,
    output_path: str | None = None,
) -> str:
    """Generate a markdown comparison report.

    Returns the report as a string. Optionally writes to output_path.
    """
    runner_records = load_jsonl(runner_results_path)
    proxy_records = load_jsonl(proxy_results_path) if proxy_results_path else []

    if not runner_records:
        return "# No results found\n\nNo benchmark data available."

    # Determine measurement type
    auth_mode = runner_records[0].get("auth_mode", "estimation")
    model = runner_records[0].get("model", "unknown")
    measurement_note = (
        "Exact token counts via API proxy"
        if auth_mode == "proxy"
        else "Estimated tokens (~4 chars/token)"
    )

    # Merge and compute
    records = merge_token_data(runner_records, proxy_records)
    stats = compute_task_stats(records)

    # Task metadata for display
    tasks_meta = {}
    for rec in runner_records:
        if rec["task_id"] not in tasks_meta:
            tasks_meta[rec["task_id"]] = {
                "category": rec.get("task_category", ""),
            }

    # Build report
    lines = []
    lines.append("# mcp-codebase-index Token Savings Benchmark")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Model:** {model}")
    lines.append(f"**Measurement:** {measurement_note}")
    lines.append(f"**Repeats:** {runner_records[0].get('repeat', 0) + 1} per task/mode")
    lines.append("")

    # Per-task results table
    lines.append("## Per-Task Results")
    lines.append("")
    lines.append(
        "| Task | Category | With Index | Without Index | Token Savings | Time Savings |"
    )
    lines.append(
        "|------|----------|-----------|---------------|---------------|-------------|"
    )

    all_with_tokens = []
    all_without_tokens = []
    all_with_times = []
    all_without_times = []

    for task_id in stats:
        cat = tasks_meta.get(task_id, {}).get("category", "")
        with_s = stats[task_id].get("with_index", {})
        without_s = stats[task_id].get("without_index", {})

        with_tok = with_s.get("median_tokens", 0)
        without_tok = without_s.get("median_tokens", 0)
        with_time = with_s.get("median_time_s", 0)
        without_time = without_s.get("median_time_s", 0)

        if with_tok:
            all_with_tokens.append(with_tok)
        if without_tok:
            all_without_tokens.append(without_tok)
        if with_time:
            all_with_times.append(with_time)
        if without_time:
            all_without_times.append(without_time)

        tok_savings = _pct_savings(with_tok, without_tok)
        time_savings = _pct_savings(with_time, without_time)

        lines.append(
            f"| {task_id} | {cat} | {_fmt_tokens(with_tok)} "
            f"| {_fmt_tokens(without_tok)} | {tok_savings} | {time_savings} |"
        )

    # Aggregate
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")

    if all_with_tokens and all_without_tokens:
        med_with = _median(all_with_tokens)
        med_without = _median(all_without_tokens)
        lines.append(
            f"- **Median token savings:** {_pct_savings(med_with, med_without)}"
        )
    if all_with_times and all_without_times:
        med_with_t = _median(all_with_times)
        med_without_t = _median(all_without_times)
        lines.append(
            f"- **Median wall-time savings:** {_pct_savings(med_with_t, med_without_t)}"
        )

    # Detailed breakdown
    lines.append("")
    lines.append("## Detailed Breakdown")
    lines.append("")

    for task_id in stats:
        lines.append(f"### {task_id}")
        lines.append("")

        for mode in ["with_index", "without_index"]:
            s = stats[task_id].get(mode, {})
            if not s:
                continue
            label = "With codebase-index" if mode == "with_index" else "Without codebase-index"
            lines.append(f"**{label}:**")
            lines.append(f"- Median total tokens: {_fmt_tokens(s['median_tokens'])}")
            lines.append(f"- Median input tokens: {_fmt_tokens(s['median_input_tokens'])}")
            lines.append(f"- Median output tokens: {_fmt_tokens(s['median_output_tokens'])}")
            lines.append(f"- Median wall time: {_fmt_time(s['median_time_s'])}")
            if s['median_api_calls']:
                lines.append(f"- Median API calls: {int(s['median_api_calls'])}")
            lines.append(f"- Runs: {s['runs']}")
            lines.append("")

    report = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(report)

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate benchmark comparison report")
    parser.add_argument("runner_results", help="Path to runner results JSONL file")
    parser.add_argument("proxy_results", nargs="?", default=None,
                        help="Path to proxy results JSONL file (optional)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output path for markdown report")
    args = parser.parse_args()

    output_path = args.output or os.path.join(
        os.path.dirname(args.runner_results), "report.md"
    )
    report = generate_report(args.runner_results, args.proxy_results, output_path)
    print(report)
    print(f"\nReport written to: {output_path}")


if __name__ == "__main__":
    main()
