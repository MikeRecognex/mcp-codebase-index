#!/usr/bin/env python3
"""Orchestrates A/B benchmark runs: codebase-index vs built-in tools only.

Usage:
    python benchmarks/runner.py [--repeats N] [--task TASK_ID] [--target-repo PATH]
                                [--clone-url URL] [--tasks-file PATH]

Runs each task in two modes (with_index, without_index), captures token usage,
and generates a comparison report.

Two measurement modes (auto-detected):
  - Proxy mode: when ANTHROPIC_API_KEY is set, starts a local reverse proxy
    that intercepts real token counts from the API.
  - Estimation mode: when using a Pro/Max subscription (no API key), parses
    Claude Code CLI JSON output and estimates tokens from character counts.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

BENCHMARKS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BENCHMARKS_DIR)

# Ensure project root is on sys.path so `from benchmarks.report` works when run as a script
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
RESULTS_DIR = os.path.join(BENCHMARKS_DIR, "results")
TASKS_FILE = os.path.join(BENCHMARKS_DIR, "tasks.json")
RUN_ID_FILE = os.path.join(RESULTS_DIR, ".current_run_id")

DEFAULT_TARGET_REPO = "/tmp/bench-django"

DEFAULT_MODEL = "claude-sonnet-4-20250514"

MODES = ["with_index", "without_index"]


def ensure_repo(target_repo: str, clone_url: str | None = None):
    """Ensure target repo exists, optionally cloning from a URL."""
    if os.path.exists(target_repo):
        print(f"  Target repo exists: {target_repo}")
        return
    if not clone_url:
        print(f"Error: Target repo '{target_repo}' does not exist.")
        print("  Provide --clone-url to clone it automatically.")
        sys.exit(1)
    print(f"  Cloning {clone_url} to {target_repo} (shallow)...")
    subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, target_repo],
        check=True,
    )


def prebuild_index(target_repo: str):
    """Pre-build the codebase index so indexing time isn't included in benchmarks."""
    print("  Pre-building codebase index...")
    try:
        from mcp_codebase_index.project_indexer import ProjectIndexer
        indexer = ProjectIndexer(target_repo)
        index = indexer.index()
        print(f"  Index built: {index.total_files} files, {index.total_lines:,} lines")
    except Exception as e:
        print(f"  Warning: Could not pre-build index: {e}")


def detect_auth_mode() -> str:
    """Detect whether we have an API key (proxy mode) or subscription (estimation mode)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "proxy"
    return "estimation"


def write_run_id(run_id: str):
    """Write the current run ID for the proxy to read."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RUN_ID_FILE, "w") as f:
        f.write(run_id)


def make_mcp_config(mode: str, target_repo: str) -> str:
    """Create a temporary MCP config file with placeholders replaced."""
    config_name = f"{mode}.json"
    config_path = os.path.join(BENCHMARKS_DIR, "configs", config_name)

    with open(config_path) as f:
        config_text = f.read()

    config_text = config_text.replace("__PROJECT_ROOT__", PROJECT_ROOT)
    config_text = config_text.replace("__TARGET_REPO__", target_repo)

    # Write to a temp file
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix=f"mcp_{mode}_", delete=False
    )
    tmp.write(config_text)
    tmp.close()
    return tmp.name


def start_proxy(output_path: str) -> subprocess.Popen | None:
    """Start the token-counting proxy in the background."""
    print("  Starting token proxy on port 8082...")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "benchmarks.proxy.token_proxy",
            "--port", "8082",
            "--output", output_path,
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Give it a moment to start
    time.sleep(1.5)
    if proc.poll() is not None:
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        print(f"  Warning: Proxy failed to start: {stderr}")
        return None
    print("  Proxy started.")
    return proc


def stop_proxy(proc: subprocess.Popen | None):
    """Stop the proxy process."""
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("  Proxy stopped.")


def run_claude_task(
    prompt: str,
    mode: str,
    target_repo: str,
    auth_mode: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Run a single Claude Code CLI invocation and return results."""
    config_path = make_mcp_config(mode, target_repo)

    try:
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", model,
            "--mcp-config", config_path,
            "--strict-mcp-config",
            "-p", prompt,
        ]

        env = os.environ.copy()
        # Unset CLAUDECODE so nested `claude` CLI doesn't refuse to start
        env.pop("CLAUDECODE", None)
        if auth_mode == "proxy":
            env["ANTHROPIC_BASE_URL"] = "http://localhost:8082"

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=target_repo,
                capture_output=True,
                text=True,
                env=env,
                timeout=600,  # 10 minute timeout per task
            )
        except subprocess.TimeoutExpired:
            wall_time = time.time() - start_time
            print(f"  TIMEOUT after {wall_time:.0f}s")
            return {
                "wall_time_s": round(wall_time, 2),
                "exit_code": -1,
                "timed_out": True,
            }
        wall_time = time.time() - start_time

        output = {
            "wall_time_s": round(wall_time, 2),
            "exit_code": result.returncode,
        }

        # Parse JSON output — claude --print --output-format json includes
        # real token counts in the `usage` field
        if result.stdout:
            try:
                cli_output = json.loads(result.stdout)
                output["cli_output"] = cli_output

                if isinstance(cli_output, dict):
                    # Extract token usage from CLI JSON output
                    # input_tokens is only the non-cached portion;
                    # real consumption includes cache_read and cache_creation
                    usage = cli_output.get("usage", {})
                    if usage:
                        uncached = usage.get("input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)
                        cache_create = usage.get("cache_creation_input_tokens", 0)
                        output_tok = usage.get("output_tokens", 0)

                        output["input_tokens_uncached"] = uncached
                        output["cache_read_input_tokens"] = cache_read
                        output["cache_creation_input_tokens"] = cache_create
                        output["output_tokens"] = output_tok
                        # Total input = all input token types
                        output["input_tokens"] = uncached + cache_read + cache_create
                        output["total_cost_usd"] = cli_output.get("total_cost_usd", 0)

                    output["num_turns"] = cli_output.get("num_turns", 0)
                    output["duration_api_ms"] = cli_output.get("duration_api_ms", 0)
                    if cli_output.get("session_id"):
                        output["session_id"] = cli_output["session_id"]

                    # Extract response text for quality scoring
                    if cli_output.get("result"):
                        output["response_text"] = cli_output["result"]
            except json.JSONDecodeError:
                output["raw_stdout_len"] = len(result.stdout)

        if result.stderr:
            output["stderr_snippet"] = result.stderr[:500]

        return output

    finally:
        # Clean up temp config
        try:
            os.unlink(config_path)
        except OSError:
            pass


def judge_responses(
    task: dict,
    with_response: str,
    without_response: str,
    with_grounding: int = 0,
    without_grounding: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """Use an LLM to judge the quality of both responses for a task.

    Returns dict with 'with_index' and 'without_index' scores,
    each containing accuracy, depth, grounding, completeness (1-10).
    """
    if not with_response or not without_response:
        return None

    prompt = f"""You are an expert code reviewer judging the quality of two AI-generated answers about a codebase.

TASK: {task['description']}
QUESTION: {task['prompt']}

AUTOMATED METRICS (already measured — do NOT re-judge these):
- Answer A references {with_grounding} unique code artifacts (files, symbols, line numbers)
- Answer B references {without_grounding} unique code artifacts (files, symbols, line numbers)

=== ANSWER A (with_index) ===
{with_response[:8000]}

=== ANSWER B (without_index) ===
{without_response[:8000]}

Rate each answer 1-10 on these criteria (judge ONLY from the text — you cannot verify code existence):
- **Specificity**: Does the answer reference specific files, classes, methods, and line numbers rather than speaking in generalities? Count concrete code references, not whether they're "correct".
- **Depth**: How thorough is the analysis? Does it trace through actual code paths vs give a surface-level overview?
- **Completeness**: Does it fully answer all parts of the question?
- **Usefulness**: Would a developer find this answer actionable? Could they navigate to the exact code based on this answer?

IMPORTANT scoring guidelines:
- An answer that names specific files like `django/db/models/query.py:742` and traces code paths scores HIGH on specificity (7-10)
- An answer that says "Django's ORM handles this" without naming files scores LOW on specificity (1-3)
- An answer with 2 API turns that gives a general overview scores LOW on depth
- An answer with 10+ turns that explored the actual codebase scores HIGH on depth

Respond with ONLY a JSON object in this exact format, no other text:
{{"with_index": {{"specificity": N, "depth": N, "completeness": N, "usefulness": N}}, "without_index": {{"specificity": N, "depth": N, "completeness": N, "usefulness": N}}}}"""

    # Write prompt to a temp file and pipe via stdin to avoid hanging
    prompt_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="judge_prompt_", delete=False
    )
    prompt_file.write(prompt)
    prompt_file.close()

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        with open(prompt_file.name) as pf:
            result = subprocess.run(
                [
                    "claude",
                    "--print",
                    "--model", model,
                    "--strict-mcp-config",
                    "--mcp-config", os.path.join(BENCHMARKS_DIR, "configs", "without_index.json"),
                ],
                capture_output=True,
                text=True,
                env=env,
                stdin=pf,
                timeout=120,
            )
        if result.returncode != 0:
            print(f"  Judge failed (exit {result.returncode})")
            if result.stderr:
                print(f"  Judge stderr: {result.stderr[:300]}")
            return None

        # Extract JSON from response text (may have markdown fences or prose around it)
        text = result.stdout.strip()
        json_match = re.search(r'\{[^{}]*"with_index"[^{}]*\{[^}]+\}[^{}]*"without_index"[^{}]*\{[^}]+\}[^{}]*\}', text)
        if json_match:
            scores = json.loads(json_match.group())
            return scores
        else:
            print(f"  Judge: could not find JSON in response ({len(text)} chars)")
            print(f"  Judge response preview: {text[:300]}")
    except subprocess.TimeoutExpired:
        print("  Judge error: timed out after 120s")
    except json.JSONDecodeError as e:
        print(f"  Judge error: JSON parse failed: {e}")
        if json_match:
            print(f"  Judge matched text: {json_match.group()[:200]}")
    except Exception as e:
        print(f"  Judge error ({type(e).__name__}): {e}")
    finally:
        try:
            os.unlink(prompt_file.name)
        except OSError:
            pass
    return None


def load_tasks(tasks_file: str, task_filter: str | None = None) -> list[dict]:
    """Load task definitions, optionally filtering to a specific task."""
    with open(tasks_file) as f:
        tasks = json.load(f)
    if task_filter:
        tasks = [t for t in tasks if t["id"] == task_filter]
        if not tasks:
            print(f"Error: No task found with id '{task_filter}'")
            sys.exit(1)
    return tasks


def run_benchmark(
    tasks: list[dict],
    target_repo: str,
    repeats: int,
    auth_mode: str,
    model: str,
) -> list[dict]:
    """Run the full benchmark matrix: tasks x repeats, both modes per iteration.

    Groups modes together so we can compare responses and run quality scoring.
    """
    from benchmarks.report import score_grounding

    all_results = []
    total_runs = len(tasks) * len(MODES) * repeats
    run_num = 0

    for task in tasks:
        for repeat in range(repeats):
            pair_results = {}

            for mode in MODES:
                run_num += 1
                run_id = f"{mode}__{task['id']}__r{repeat}"
                write_run_id(run_id)

                print(f"\n[{run_num}/{total_runs}] {run_id}")
                print(f"  Task: {task['description']}")
                print(f"  Mode: {mode}")

                result = run_claude_task(
                    prompt=task["prompt"],
                    mode=mode,
                    target_repo=target_repo,
                    auth_mode=auth_mode,
                    model=model,
                )

                # Score grounding from response text
                response_text = result.get("response_text", "")
                grounding = score_grounding(
                    response_text,
                    num_turns=result.get("num_turns", 1),
                )
                result["grounding_score"] = grounding["composite"]
                result["grounding_detail"] = grounding

                record = {
                    "run_id": run_id,
                    "task_id": task["id"],
                    "task_category": task["category"],
                    "mode": mode,
                    "repeat": repeat,
                    "auth_mode": auth_mode,
                    "model": model,
                    **result,
                }

                pair_results[mode] = record
                all_results.append(record)

                inp = result.get("input_tokens", "?")
                out = result.get("output_tokens", "?")
                cost = result.get("total_cost_usd")
                turns = result.get("num_turns", "?")
                cost_str = f" | ${cost:.4f}" if cost else ""
                print(
                    f"  Wall time: {result['wall_time_s']}s | "
                    f"Exit: {result['exit_code']} | "
                    f"Tokens: {inp} in / {out} out | "
                    f"Turns: {turns}{cost_str} | "
                    f"Grounding: {grounding['composite']}"
                )

            # Run LLM judge on the pair
            w_rec = pair_results.get("with_index")
            wo_rec = pair_results.get("without_index")
            if w_rec and wo_rec:
                w_text = w_rec.get("response_text", "")
                wo_text = wo_rec.get("response_text", "")
                print(f"\n  Judge check: with_text={len(w_text)} chars, without_text={len(wo_text)} chars")
                if w_text and wo_text:
                    print(f"\n  Judging responses for {task['id']} r{repeat}...")
                    scores = judge_responses(
                        task, w_text, wo_text,
                        with_grounding=w_rec.get("grounding_score", 0),
                        without_grounding=wo_rec.get("grounding_score", 0),
                        model=model,
                    )
                    if scores:
                        for mode_key in ("with_index", "without_index"):
                            mode_scores = scores.get(mode_key, {})
                            rec = pair_results[mode_key]
                            rec["judge_specificity"] = mode_scores.get("specificity")
                            rec["judge_depth"] = mode_scores.get("depth")
                            rec["judge_completeness"] = mode_scores.get("completeness")
                            rec["judge_usefulness"] = mode_scores.get("usefulness")
                            dims = [v for v in mode_scores.values() if isinstance(v, (int, float))]
                            rec["judge_avg"] = round(sum(dims) / len(dims), 1) if dims else None
                        print(
                            f"  Scores — with: {scores.get('with_index', {})} | "
                            f"without: {scores.get('without_index', {})}"
                        )
                    else:
                        print("  Judge returned no scores")

    return all_results


def save_results(results: list[dict], output_path: str):
    """Save results to a JSONL file, writing response text to separate files."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    responses_dir = os.path.join(os.path.dirname(output_path), "responses")
    os.makedirs(responses_dir, exist_ok=True)

    with open(output_path, "w") as f:
        for record in results:
            # Write response text to a separate file
            response_text = record.get("response_text", "")
            if response_text:
                response_file = os.path.join(responses_dir, f"{record['run_id']}.txt")
                with open(response_file, "w") as rf:
                    rf.write(response_text)
                record["response_file"] = response_file

            # Remove large fields from saved JSONL
            save_record = {
                k: v for k, v in record.items()
                if k not in ("cli_output", "response_text")
            }
            f.write(json.dumps(save_record) + "\n")
    print(f"\nResults saved to {output_path}")
    print(f"Response files saved to {responses_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="mcp-codebase-index A/B token usage benchmark"
    )
    parser.add_argument(
        "--repeats", type=int, default=3,
        help="Number of repeat runs per task/mode (default: 3)",
    )
    parser.add_argument(
        "--task", type=str, default=None,
        help="Run only a specific task ID (e.g., find_symbol)",
    )
    parser.add_argument(
        "--target-repo", type=str, default=DEFAULT_TARGET_REPO,
        help=f"Target repository to benchmark against (default: {DEFAULT_TARGET_REPO})",
    )
    parser.add_argument(
        "--clone-url", type=str, default=None,
        help="Git URL to shallow-clone if target-repo doesn't exist",
    )
    parser.add_argument(
        "--tasks-file", type=str, default=TASKS_FILE,
        help=f"Path to tasks JSON file (default: {TASKS_FILE})",
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--no-prebuild", action="store_true",
        help="Skip pre-building the codebase index",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  mcp-codebase-index Token Savings Benchmark")
    print("=" * 60)

    # Setup
    target_repo = os.path.abspath(args.target_repo)
    ensure_repo(target_repo, clone_url=args.clone_url)

    if not args.no_prebuild:
        prebuild_index(target_repo)

    auth_mode = detect_auth_mode()
    print(f"\n  Auth mode: {auth_mode}")
    print(f"  Model: {args.model}")
    print(f"  Repeats: {args.repeats}")
    print(f"  Target: {target_repo}")

    # Check claude CLI is available
    if not shutil.which("claude"):
        print("\nError: 'claude' CLI not found. Install Claude Code first.")
        sys.exit(1)

    # Load tasks
    tasks = load_tasks(args.tasks_file, args.task)
    print(f"  Tasks: {len(tasks)}")

    # Timestamp for this run
    run_timestamp = time.strftime("%Y%m%d_%H%M%S")
    runner_results_path = os.path.join(RESULTS_DIR, f"runner_{run_timestamp}.jsonl")
    proxy_results_path = os.path.join(RESULTS_DIR, f"proxy_{run_timestamp}.jsonl")

    # Start proxy if in API key mode
    proxy_proc = None
    if auth_mode == "proxy":
        proxy_proc = start_proxy(proxy_results_path)

    try:
        results = run_benchmark(
            tasks=tasks,
            target_repo=target_repo,
            repeats=args.repeats,
            auth_mode=auth_mode,
            model=args.model,
        )
        save_results(results, runner_results_path)
    finally:
        stop_proxy(proxy_proc)

    # Generate report
    print("\nGenerating report...")
    try:
        from benchmarks.report import generate_report
        report_path = os.path.join(RESULTS_DIR, f"report_{run_timestamp}.md")
        generate_report(
            runner_results_path=runner_results_path,
            proxy_results_path=proxy_results_path if auth_mode == "proxy" else None,
            output_path=report_path,
        )
        # Also write as latest report
        latest_path = os.path.join(RESULTS_DIR, "report.md")
        shutil.copy2(report_path, latest_path)
        print(f"Report: {report_path}")
        print(f"Latest: {latest_path}")
    except Exception as e:
        print(f"Warning: Could not generate report: {e}")
        import traceback
        traceback.print_exc()

    print("\nBenchmark complete!")


if __name__ == "__main__":
    main()
