#!/usr/bin/env python3
"""Generate markdown comparison reports from benchmark results.

Usage:
    python benchmarks/report.py results/runner_TIMESTAMP.jsonl [results/proxy_TIMESTAMP.jsonl]

Can also be imported and called programmatically by runner.py.
"""

import argparse
import json
import os
import re
import time
from collections import defaultdict


def score_grounding(response_text: str, num_turns: int = 1) -> dict:
    """Score grounding with structured sub-metrics.

    Returns a dict with file_refs, symbol_refs, line_refs, code_snippets,
    test_file_refs, refs_per_turn, and a weighted composite score.

    Line refs and test file refs are strong exploration signals — hard to
    hallucinate from training data alone.
    """
    empty = {
        "file_refs": 0, "symbol_refs": 0, "line_refs": 0,
        "code_snippets": 0, "test_file_refs": 0,
        "refs_per_turn": 0.0, "composite": 0.0,
    }
    if not response_text:
        return empty

    # File paths: word/word/word.ext patterns (at least 2 segments)
    file_refs = set(re.findall(
        r'\b(?:[a-zA-Z_][\w-]*/)+[a-zA-Z_][\w-]*\.(?:py|js|ts|go|rs|java|c|h|cpp|rb)\b',
        response_text
    ))

    # Code symbols: ClassName.method_name or module.function patterns
    symbol_refs = set(re.findall(
        r'\b[A-Z][a-zA-Z0-9_]*\.[a-z_][a-zA-Z0-9_]*(?:\(\))?',
        response_text
    ))

    # Line number references: "line 42", ".py:42", "L42"
    line_refs = set(re.findall(
        r'(?:line\s+\d+|\.py:\d+|\.js:\d+|\.ts:\d+|\bL\d+\b)',
        response_text
    ))

    # Test file references: tests/foo.py, test_foo.py
    test_file_refs = set(re.findall(
        r'\btests?/\w+\.py\b',
        response_text
    )) | set(re.findall(
        r'\btest_\w+\.py\b',
        response_text
    ))

    # Fenced code blocks
    code_snippets = len(re.findall(r'```', response_text)) // 2

    n_file = len(file_refs)
    n_symbol = len(symbol_refs)
    n_line = len(line_refs)
    n_test = len(test_file_refs)
    n_snippets = code_snippets

    turns = max(num_turns, 1)
    refs_per_turn = (n_file + n_symbol) / turns

    # Weighted composite — line refs and test files are strong signals
    composite = (
        n_line * 5
        + n_test * 3
        + n_snippets * 2
        + n_file
        + n_symbol * 0.5
    )

    # Density penalty: suspiciously many refs per turn suggests training data
    if refs_per_turn > 6:
        composite *= max(0.3, 1.0 - (refs_per_turn - 6) * 0.05)

    return {
        "file_refs": n_file,
        "symbol_refs": n_symbol,
        "line_refs": n_line,
        "code_snippets": n_snippets,
        "test_file_refs": n_test,
        "refs_per_turn": round(refs_per_turn, 1),
        "composite": round(composite, 1),
    }


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

            # Quality metrics (use 'is not None' to preserve valid zeroes)
            grounding_scores = [
                r["grounding_score"] for r in runs
                if r.get("grounding_score") is not None
            ]
            judge_avgs = [
                r["judge_avg"] for r in runs
                if r.get("judge_avg") is not None
            ]

            # Structured grounding sub-metrics
            line_refs = [
                r["grounding_detail"]["line_refs"] for r in runs
                if r.get("grounding_detail")
            ]
            refs_per_turn = [
                r["grounding_detail"]["refs_per_turn"] for r in runs
                if r.get("grounding_detail")
            ]
            test_file_refs = [
                r["grounding_detail"]["test_file_refs"] for r in runs
                if r.get("grounding_detail")
            ]
            code_snippets = [
                r["grounding_detail"]["code_snippets"] for r in runs
                if r.get("grounding_detail")
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
                "median_grounding": _median(grounding_scores),
                "median_line_refs": _median(line_refs),
                "median_refs_per_turn": _median(refs_per_turn),
                "median_test_file_refs": _median(test_file_refs),
                "median_code_snippets": _median(code_snippets),
                "median_judge_avg": _median(judge_avgs),
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


def load_response_text(record: dict) -> str | None:
    """Load response text from the response_file path in a record."""
    path = record.get("response_file", "")
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def extract_excerpt(text: str, pattern: str, chars: int = 200) -> str | None:
    """Find first paragraph containing pattern, return surrounding context."""
    match = re.search(pattern, text)
    if not match:
        return None
    # Find paragraph boundaries around the match
    start = text.rfind("\n\n", 0, match.start())
    start = start + 2 if start != -1 else max(0, match.start() - chars // 2)
    end = text.find("\n\n", match.end())
    end = end if end != -1 else min(len(text), match.end() + chars // 2)
    excerpt = text[start:end].strip()
    if len(excerpt) > chars:
        excerpt = excerpt[:chars] + "..."
    return excerpt


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

    # Primary results table — cost, turns, and quality
    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Task | Category "
        "| Cost (with) | Cost (without) | Cost Savings "
        "| Quality (with) | Quality (without) "
        "| Grounding (with) | Grounding (without) "
        "| Turns | Time Savings |"
    )
    lines.append(
        "|------|----------"
        "|------------|---------------|-------------"
        "|----------------|-------------------"
        "|------------------|---------------------"
        "|-------|-------------|"
    )

    all_with_costs = []
    all_without_costs = []
    all_with_times = []
    all_without_times = []
    all_with_ctx = []
    all_without_ctx = []
    all_with_quality = []
    all_without_quality = []
    all_with_grounding = []
    all_without_grounding = []

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
        w_quality = w.get("median_judge_avg", 0)
        wo_quality = wo.get("median_judge_avg", 0)
        w_grounding = w.get("median_grounding", 0)
        wo_grounding = wo.get("median_grounding", 0)

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
        if w_quality:
            all_with_quality.append(w_quality)
        if wo_quality:
            all_without_quality.append(wo_quality)
        if w_grounding:
            all_with_grounding.append(w_grounding)
        if wo_grounding:
            all_without_grounding.append(wo_grounding)

        w_q_str = f"{w_quality:.1f}/10" if w_quality else "—"
        wo_q_str = f"{wo_quality:.1f}/10" if wo_quality else "—"
        w_g_str = f"{w_grounding:.0f}" if w_grounding else "—"
        wo_g_str = f"{wo_grounding:.0f}" if wo_grounding else "—"

        lines.append(
            f"| {task_id} | {cat} "
            f"| {_fmt_cost(w_cost)} | {_fmt_cost(wo_cost)} "
            f"| {_pct_savings(w_cost, wo_cost)} "
            f"| {w_q_str} | {wo_q_str} "
            f"| {w_g_str} | {wo_g_str} "
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
    if all_with_quality and all_without_quality:
        med_w_q = _median(all_with_quality)
        med_wo_q = _median(all_without_quality)
        lines.append(f"- **Median quality (with):** {med_w_q:.1f}/10 vs **(without):** {med_wo_q:.1f}/10")
    if all_with_grounding and all_without_grounding:
        med_w_g = _median(all_with_grounding)
        med_wo_g = _median(all_without_grounding)
        lines.append(f"- **Median grounding composite (with):** {med_w_g:.0f} vs **(without):** {med_wo_g:.0f}")

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
            if s.get("median_judge_avg"):
                lines.append(f"- Quality score: {s['median_judge_avg']:.1f}/10")
            if s.get("median_grounding"):
                lines.append(f"- Grounding composite: {s['median_grounding']:.0f}")
                lines.append(f"  - Line refs: {int(s.get('median_line_refs', 0))}")
                lines.append(f"  - Test file refs: {int(s.get('median_test_file_refs', 0))}")
                lines.append(f"  - Code snippets: {int(s.get('median_code_snippets', 0))}")
                lines.append(f"  - Refs/turn: {s.get('median_refs_per_turn', 0):.1f}")
            lines.append(f"- Runs: {s['runs']}")
            lines.append("")

    # Response comparison section
    # Group records by task_id and mode, pick first repeat with response_file
    grouped_recs: dict[str, dict[str, dict]] = defaultdict(dict)
    for rec in records:
        mode = rec.get("mode", "")
        tid = rec.get("task_id", "")
        if mode and tid and mode not in grouped_recs[tid]:
            grouped_recs[tid][mode] = rec

    has_comparisons = False
    comparison_lines = []
    comparison_lines.append("## Response Comparison")
    comparison_lines.append("")

    for task_id in stats:
        w_rec = grouped_recs.get(task_id, {}).get("with_index")
        wo_rec = grouped_recs.get(task_id, {}).get("without_index")
        if not w_rec or not wo_rec:
            continue

        w_text = load_response_text(w_rec)
        wo_text = load_response_text(wo_rec)
        if not w_text or not wo_text:
            continue

        w_detail = w_rec.get("grounding_detail", {})
        wo_detail = wo_rec.get("grounding_detail", {})
        if not w_detail or not wo_detail:
            # Recompute if missing (old results)
            w_detail = score_grounding(w_text, w_rec.get("num_turns", 1))
            wo_detail = score_grounding(wo_text, wo_rec.get("num_turns", 1))

        has_comparisons = True
        comparison_lines.append(f"### {task_id} — Response Comparison")
        comparison_lines.append("")
        comparison_lines.append(
            "| Metric | With Index | Without Index |"
        )
        comparison_lines.append(
            "|--------|-----------|---------------|"
        )
        comparison_lines.append(
            f"| Exact line refs | {w_detail.get('line_refs', 0)} "
            f"| {wo_detail.get('line_refs', 0)} |"
        )
        comparison_lines.append(
            f"| Refs per turn | {w_detail.get('refs_per_turn', 0)} "
            f"| {wo_detail.get('refs_per_turn', 0)} |"
        )
        comparison_lines.append(
            f"| Code snippets | {w_detail.get('code_snippets', 0)} "
            f"| {wo_detail.get('code_snippets', 0)} |"
        )
        comparison_lines.append(
            f"| Test file refs | {w_detail.get('test_file_refs', 0)} "
            f"| {wo_detail.get('test_file_refs', 0)} |"
        )
        comparison_lines.append("")

        # Grounded excerpt: paragraph with line numbers
        grounded = extract_excerpt(w_text, r'\.py:\d+|line\s+\d+')
        if grounded:
            comparison_lines.append("**With Index** (grounded):")
            comparison_lines.append(f"> {grounded}")
            comparison_lines.append("")

        # Ungrounded excerpt: paragraph without file paths or line numbers
        # Find paragraphs in without_index that lack specific refs
        paragraphs = re.split(r'\n\n+', wo_text)
        ungrounded = None
        for para in paragraphs:
            para = para.strip()
            if len(para) < 40:
                continue
            if not re.search(r'\.py:\d+|line\s+\d+|/\w+\.py', para):
                ungrounded = para[:200] + ("..." if len(para) > 200 else "")
                break
        if ungrounded:
            comparison_lines.append("**Without Index** (from training data):")
            comparison_lines.append(f"> {ungrounded}")
            comparison_lines.append("")

    if has_comparisons:
        lines.append("")
        lines.extend(comparison_lines)

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
