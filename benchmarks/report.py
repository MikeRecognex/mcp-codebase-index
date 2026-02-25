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
    """Merge proxy token data into runner records by run_id."""
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

    for rec in runner_records:
        run_id = rec.get("run_id", "")
        if run_id in proxy_by_run:
            rec["proxy_data"] = proxy_by_run[run_id]

    return runner_records


def compute_task_stats(records: list[dict]) -> dict:
    """Compute per-task statistics across modes and repeats.

    Returns: {task_id: {mode: {metric: value}}}
    """
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        grouped[rec["task_id"]][rec["mode"]].append(rec)

    stats = {}
    for task_id, modes in grouped.items():
        stats[task_id] = {}
        for mode, runs in modes.items():
            costs = [r.get("total_cost_usd", 0) for r in runs if r.get("total_cost_usd")]
            times = [r.get("wall_time_s", 0) for r in runs if r.get("wall_time_s")]
            turns = [r.get("num_turns", 0) for r in runs if r.get("num_turns")]
            output_tokens = [r.get("output_tokens", 0) for r in runs if r.get("output_tokens")]

            # Cache breakdown
            cache_reads = [
                r.get("cache_read_input_tokens", 0) for r in runs
            ]
            cache_creates = [
                r.get("cache_creation_input_tokens", 0) for r in runs
            ]
            uncached = [
                r.get("input_tokens_uncached", 0) for r in runs
            ]

            stats[task_id][mode] = {
                "runs": len(runs),
                "median_cost_usd": _median(costs),
                "median_time_s": _median(times),
                "median_turns": _median(turns),
                "median_output_tokens": _median(output_tokens),
                "median_cache_read": _median(cache_reads),
                "median_cache_create": _median(cache_creates),
                "median_uncached": _median(uncached),
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


def _fmt_cost(c: float) -> str:
    """Format USD cost for display."""
    if c == 0:
        return "—"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.3f}"


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
    """Generate a markdown comparison report."""
    runner_records = load_jsonl(runner_results_path)
    proxy_records = load_jsonl(proxy_results_path) if proxy_results_path else []

    if not runner_records:
        return "# No results found\n\nNo benchmark data available."

    model = runner_records[0].get("model", "unknown")
    repeats = max(r.get("repeat", 0) for r in runner_records) + 1

    records = merge_token_data(runner_records, proxy_records)
    stats = compute_task_stats(records)

    tasks_meta = {}
    for rec in runner_records:
        if rec["task_id"] not in tasks_meta:
            tasks_meta[rec["task_id"]] = {
                "category": rec.get("task_category", ""),
            }

    lines = []
    lines.append("# mcp-codebase-index Benchmark Results")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Model:** {model}")
    lines.append(f"**Repeats:** {repeats} per task/mode")
    lines.append("")

    # Primary results table — cost and turns
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Task | Category "
        "| New Context (with) | New Context (without) | Context Savings "
        "| Cost (with) | Cost (without) | Cost Savings "
        "| Turns | Time Savings |"
    )
    lines.append(
        "|------|----------"
        "|-------------------|----------------------|----------------"
        "|------------|---------------|-------------"
        "|-------|-------------|"
    )

    all_with_costs = []
    all_without_costs = []
    all_with_times = []
    all_without_times = []
    all_with_ctx = []
    all_without_ctx = []

    for task_id in stats:
        cat = tasks_meta.get(task_id, {}).get("category", "")
        w = stats[task_id].get("with_index", {})
        wo = stats[task_id].get("without_index", {})

        w_cost = w.get("median_cost_usd", 0)
        wo_cost = wo.get("median_cost_usd", 0)
        w_turns = w.get("median_turns", 0)
        wo_turns = wo.get("median_turns", 0)
        w_time = w.get("median_time_s", 0)
        wo_time = wo.get("median_time_s", 0)
        w_ctx = w.get("median_cache_create", 0)
        wo_ctx = wo.get("median_cache_create", 0)

        if w_cost:
            all_with_costs.append(w_cost)
        if wo_cost:
            all_without_costs.append(wo_cost)
        if w_time:
            all_with_times.append(w_time)
        if wo_time:
            all_without_times.append(wo_time)
        if w_ctx:
            all_with_ctx.append(w_ctx)
        if wo_ctx:
            all_without_ctx.append(wo_ctx)

        lines.append(
            f"| {task_id} | {cat} "
            f"| {_fmt_tokens(w_ctx)} | {_fmt_tokens(wo_ctx)} "
            f"| {_pct_savings(w_ctx, wo_ctx)} "
            f"| {_fmt_cost(w_cost)} | {_fmt_cost(wo_cost)} "
            f"| {_pct_savings(w_cost, wo_cost)} "
            f"| {int(w_turns) if w_turns else '—'}→{int(wo_turns) if wo_turns else '—'} "
            f"| {_pct_savings(w_time, wo_time)} |"
        )

    # Aggregate
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")

    if all_with_ctx and all_without_ctx:
        med_w_c = _median(all_with_ctx)
        med_wo_c = _median(all_without_ctx)
        lines.append(
            f"- **Median new-context savings:** {_pct_savings(med_w_c, med_wo_c)}"
        )
    if all_with_costs and all_without_costs:
        med_w = _median(all_with_costs)
        med_wo = _median(all_without_costs)
        lines.append(f"- **Median cost savings:** {_pct_savings(med_w, med_wo)}")
    if all_with_times and all_without_times:
        med_w_t = _median(all_with_times)
        med_wo_t = _median(all_without_times)
        lines.append(f"- **Median wall-time savings:** {_pct_savings(med_w_t, med_wo_t)}")

    # Detailed breakdown with cache info
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
            if s["median_cost_usd"]:
                lines.append(f"- Cost: {_fmt_cost(s['median_cost_usd'])}")
            if s["median_turns"]:
                lines.append(f"- API turns: {int(s['median_turns'])}")
            lines.append(f"- Wall time: {_fmt_time(s['median_time_s'])}")
            lines.append(f"- Output tokens: {_fmt_tokens(s['median_output_tokens'])}")
            lines.append("- Input breakdown:")
            lines.append(f"  - Uncached: {_fmt_tokens(s['median_uncached'])}")
            lines.append(
                f"  - Prompt cache read: {_fmt_tokens(s['median_cache_read'])}"
            )
            lines.append(
                f"  - Prompt cache create: {_fmt_tokens(s['median_cache_create'])}"
            )
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
