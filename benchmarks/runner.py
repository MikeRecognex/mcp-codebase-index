#!/usr/bin/env python3
"""Orchestrates A/B benchmark runs: codebase-index vs built-in tools only.

Usage:
    python benchmarks/runner.py [--repeats N] [--task TASK_ID] [--target-repo PATH]

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

DJANGO_CLONE_PATH = "/tmp/bench-django"
DJANGO_CLONE_URL = "https://github.com/django/django.git"

DEFAULT_MODEL = "claude-sonnet-4-20250514"

MODES = ["with_index", "without_index"]


def ensure_django(target_repo: str):
    """Clone Django if not already present."""
    if os.path.exists(target_repo):
        print(f"  Target repo exists: {target_repo}")
        return
    print(f"  Cloning Django to {target_repo} (shallow)...")
    subprocess.run(
        ["git", "clone", "--depth", "1", DJANGO_CLONE_URL, target_repo],
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
            "-p", prompt,
        ]

        env = os.environ.copy()
        # Unset CLAUDECODE so nested `claude` CLI doesn't refuse to start
        env.pop("CLAUDECODE", None)
        if auth_mode == "proxy":
            env["ANTHROPIC_BASE_URL"] = "http://localhost:8082"

        start_time = time.time()
        result = subprocess.run(
            cmd,
            cwd=target_repo,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,  # 5 minute timeout per task
        )
        wall_time = time.time() - start_time

        output = {
            "wall_time_s": round(wall_time, 2),
            "exit_code": result.returncode,
        }

        # Try to parse JSON output for estimation mode
        if result.stdout:
            try:
                cli_output = json.loads(result.stdout)
                output["cli_output"] = cli_output

                # In estimation mode, estimate tokens from character counts
                if auth_mode == "estimation":
                    output_text = ""
                    if isinstance(cli_output, dict):
                        output_text = cli_output.get("result", "")
                    elif isinstance(cli_output, str):
                        output_text = cli_output

                    # Estimate: ~4 chars per token
                    input_chars = len(prompt)
                    output_chars = len(output_text) if isinstance(output_text, str) else 0
                    output["estimated_input_tokens"] = max(input_chars // 4, 1)
                    output["estimated_output_tokens"] = max(output_chars // 4, 1)
            except json.JSONDecodeError:
                output["raw_stdout_len"] = len(result.stdout)
                if auth_mode == "estimation":
                    output["estimated_input_tokens"] = max(len(prompt) // 4, 1)
                    output["estimated_output_tokens"] = max(len(result.stdout) // 4, 1)

        if result.stderr:
            output["stderr_snippet"] = result.stderr[:500]

        return output

    finally:
        # Clean up temp config
        try:
            os.unlink(config_path)
        except OSError:
            pass


def load_tasks(task_filter: str | None = None) -> list[dict]:
    """Load task definitions, optionally filtering to a specific task."""
    with open(TASKS_FILE) as f:
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
    """Run the full benchmark matrix: tasks x modes x repeats."""
    all_results = []
    total_runs = len(tasks) * len(MODES) * repeats
    run_num = 0

    for task in tasks:
        for mode in MODES:
            for repeat in range(repeats):
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

                all_results.append(record)
                print(f"  Wall time: {result['wall_time_s']}s | Exit: {result['exit_code']}")

                if auth_mode == "estimation":
                    inp = result.get("estimated_input_tokens", "?")
                    out = result.get("estimated_output_tokens", "?")
                    print(f"  Estimated tokens: {inp} in / {out} out")

    return all_results


def save_results(results: list[dict], output_path: str):
    """Save results to a JSONL file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        for record in results:
            # Remove large cli_output from saved results to keep file manageable
            save_record = {k: v for k, v in record.items() if k != "cli_output"}
            f.write(json.dumps(save_record) + "\n")
    print(f"\nResults saved to {output_path}")


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
        "--target-repo", type=str, default=DJANGO_CLONE_PATH,
        help=f"Target repository to benchmark against (default: {DJANGO_CLONE_PATH})",
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
    ensure_django(target_repo)

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
    tasks = load_tasks(args.task)
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
