#!/usr/bin/env python3
"""Run trigger evaluation for a skill description.

Tests whether a skill's description causes Claude to trigger (read the skill)
for a set of queries. Outputs results as JSON.

A RUN THAT NEVER HAPPENED IS NOT A NEGATIVE RESULT (2026-08-23). Until this
date a missing or misconfigured `claude` produced a fully-formed, plausible
score instead of an error:

- absent from PATH: `Popen` raised FileNotFoundError, the worker loop caught
  `Exception`, appended `False`, and every `should_trigger: false` case
  PASSED. The report read "N passed" where N was the negative-case count.
- present but failing (auth, bad --model, wrong version): no exception at all.
  stderr went to DEVNULL, stdout carried nothing parseable, and every query
  scored 0/1 triggers - identical to a description that genuinely never fires.

Both are the `.claude/rules/scope-claims.md` shape: the method established
nothing, the output asserted a measurement. Two guards now stand:

1. `main()` refuses to start when `claude` is not on PATH (exit 2).
2. A run whose subprocess emitted no parseable stream event at all raises
   EvalRunError carrying the exit code and the stderr tail. Errored runs are
   counted and reported separately, never folded into a trigger rate, and any
   error makes the process exit 2.

Guarded by tests/test_skill_creator_run_eval_reports_a_dead_cli.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Resolve `scripts.*` to THIS skill's package, not the workspace's.
# `python -m scripts.<name>` from the skill root already does; running the
# file by path (`python scripts/<name>.py`) puts scripts/ on sys.path[0]
# instead of the skill root, so the absolute name resolves to whatever
# other `scripts` package is importable - in this workspace the repo root's,
# pinned there by an editable install. Measured 2026-08-23: all four
# intra-skill importers died on import under `python scripts/<name>.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import parse_skill_md


class EvalRunError(RuntimeError):
    """The `claude` subprocess produced no usable stream. Not a negative result."""


def require_claude_cli() -> None:
    """Refuse to score anything when the CLI under test is not installed.

    Called by `run_eval` itself, so `run_loop` inherits it rather than
    iterating a description forever against a subprocess that never starts.
    """
    if shutil.which("claude") is None:
        raise EvalRunError(
            "`claude` is not on PATH. Without it every query scores zero "
            "triggers, which reads as a real result: the negative cases all "
            "'pass' and the report looks like a partial score. Install the "
            "Claude Code CLI, or put it on PATH, then re-run."
        )


def _no_stream_reason(process, stderr_file) -> str:
    """Explain a run that emitted nothing, quoting the CLI's own stderr."""
    rc = process.returncode
    tail = ""
    try:
        stderr_file.seek(0)
        tail = stderr_file.read().decode("utf-8", errors="replace").strip()
    except OSError:
        pass
    if len(tail) > 500:
        tail = "..." + tail[-500:]
    return (
        f"`claude` emitted no parseable stream event (exit code {rc}). "
        "This is a broken run, not a query that failed to trigger. "
        f"stderr: {tail or '(empty)'}"
    )


def find_project_root() -> Path:
    """Find the project root by walking up from cwd looking for .claude/.

    Mimics how Claude Code discovers its project root, so the command file
    we create ends up where claude -p will look for it.
    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".claude").is_dir():
            return parent
    return current


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
) -> bool:
    """Run a single query and return whether the skill was triggered.

    Creates a command file in .claude/commands/ so it appears in Claude's
    available_skills list, then runs `claude -p` with the raw query.
    Uses --include-partial-messages to detect triggering early from
    stream events (content_block_start) rather than waiting for the
    full assistant message, which only arrives after tool execution.
    """
    unique_id = uuid.uuid4().hex[:8]
    clean_name = f"{skill_name}-skill-{unique_id}"
    project_commands_dir = Path(project_root) / ".claude" / "commands"
    command_file = project_commands_dir / f"{clean_name}.md"
    stderr_file = None

    try:
        project_commands_dir.mkdir(parents=True, exist_ok=True)
        # Use YAML block scalar to avoid breaking on quotes in description
        indented_desc = "\n  ".join(skill_description.split("\n"))
        command_content = (
            f"---\n"
            f"description: |\n"
            f"  {indented_desc}\n"
            f"---\n\n"
            f"# {skill_name}\n\n"
            f"This skill handles: {skill_description}\n"
        )
        command_file.write_text(command_content)

        cmd = [
            "claude",
            "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if model:
            cmd.extend(["--model", model])

        # Remove CLAUDECODE env var to allow nesting claude -p inside a
        # Claude Code session. The guard is for interactive terminal conflicts;
        # programmatic subprocess usage is safe.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        # stderr to a temp file, not DEVNULL: when the CLI fails, its reason is
        # the only thing that distinguishes "misconfigured" from "no trigger".
        # A file rather than a PIPE so a chatty failure cannot deadlock us.
        # noqa SIM115: a `with` cannot express this lifetime. The handle is
        # handed to Popen here and read at line 269 after the loop; the
        # enclosing try/finally (line 127 / 271) closes it on every path.
        stderr_file = tempfile.TemporaryFile()  # noqa: SIM115 - closed in the finally at the end of this function
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            cwd=project_root,
            env=env,
        )

        saw_event = False
        triggered = False
        start_time = time.time()
        # Track state for stream event detection
        pending_tool_name = None
        accumulated_json = ""

        # Use a thread to read lines from stdout (select.select doesn't
        # work with pipes on Windows). The reader pushes complete lines
        # into a list protected by a lock, and the main thread polls it.
        lines_ready: list[str] = []
        lines_lock = threading.Lock()
        reader_done = threading.Event()

        def _reader():
            try:
                for raw_line in process.stdout:
                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        with lines_lock:
                            lines_ready.append(decoded)
            except Exception as exc:
                print(f"run_eval: stdout reader thread error: {exc}", file=sys.stderr)
            finally:
                reader_done.set()

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            while time.time() - start_time < timeout:
                # Grab any lines the reader has produced
                with lines_lock:
                    batch = list(lines_ready)
                    lines_ready.clear()

                if not batch and reader_done.is_set():
                    break
                if not batch:
                    time.sleep(0.1)
                    continue

                for line in batch:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    saw_event = True

                    # Early detection via stream events
                    if event.get("type") == "stream_event":
                        se = event.get("event", {})
                        se_type = se.get("type", "")

                        if se_type == "content_block_start":
                            cb = se.get("content_block", {})
                            if cb.get("type") == "tool_use":
                                tool_name = cb.get("name", "")
                                if tool_name in ("Skill", "Read"):
                                    pending_tool_name = tool_name
                                    accumulated_json = ""
                                else:
                                    return False

                        elif se_type == "content_block_delta" and pending_tool_name:
                            delta = se.get("delta", {})
                            if delta.get("type") == "input_json_delta":
                                accumulated_json += delta.get("partial_json", "")
                                if clean_name in accumulated_json:
                                    return True

                        elif se_type in ("content_block_stop", "message_stop"):
                            if pending_tool_name:
                                return clean_name in accumulated_json
                            if se_type == "message_stop":
                                return False

                    # Fallback: full assistant message
                    elif event.get("type") == "assistant":
                        message = event.get("message", {})
                        for content_item in message.get("content", []):
                            if content_item.get("type") != "tool_use":
                                continue
                            tool_name = content_item.get("name", "")
                            tool_input = content_item.get("input", {})
                            if tool_name == "Skill" and clean_name in tool_input.get("skill", "") or tool_name == "Read" and clean_name in tool_input.get("file_path", ""):
                                triggered = True
                            return triggered

                    elif event.get("type") == "result":
                        return triggered
        finally:
            # Clean up process on any exit path (return, exception, timeout)
            if process.poll() is None:
                process.kill()
                process.wait()
            reader_thread.join(timeout=2)

        # Every early return above happened because we parsed an event, so this
        # is the only path where "no stream at all" can surface.
        if not saw_event:
            raise EvalRunError(_no_stream_reason(process, stderr_file))
        return triggered
    finally:
        if stderr_file is not None:
            stderr_file.close()
        if command_file.exists():
            command_file.unlink()


def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    project_root: Path,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: str | None = None,
) -> dict:
    """Run the full eval set and return results."""
    require_claude_cli()
    results = []

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    description,
                    timeout,
                    str(project_root),
                    model,
                )
                future_to_info[future] = (item, run_idx)

        query_triggers: dict[str, list[bool]] = {}
        query_errors: dict[str, list[str]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            query_triggers.setdefault(query, [])
            query_errors.setdefault(query, [])
            try:
                query_triggers[query].append(future.result())
            except Exception as e:
                # NOT `append(False)`. A run that never happened is not a run
                # that failed to trigger; folding it in silently turns a dead
                # CLI into a scored result. See the module docstring.
                print(f"Error: query run failed: {e}", file=sys.stderr)
                query_errors[query].append(str(e))

    for query, triggers in query_triggers.items():
        item = query_items[query]
        errors = query_errors.get(query, [])
        should_trigger = item["should_trigger"]
        if not triggers:
            # Every run of this query errored. There is nothing to score.
            results.append({
                "query": query,
                "should_trigger": should_trigger,
                "trigger_rate": None,
                "triggers": 0,
                "runs": 0,
                "errors": len(errors),
                "error_message": errors[0] if errors else "",
                "pass": None,
            })
            continue
        trigger_rate = sum(triggers) / len(triggers)
        did_pass = (trigger_rate >= trigger_threshold if should_trigger
                    else trigger_rate < trigger_threshold)
        results.append({
            "query": query,
            "should_trigger": should_trigger,
            "trigger_rate": trigger_rate,
            "triggers": sum(triggers),
            "runs": len(triggers),
            "errors": len(errors),
            "pass": did_pass,
        })

    passed = sum(1 for r in results if r["pass"] is True)
    failed = sum(1 for r in results if r["pass"] is False)
    errored = sum(1 for r in results if r["pass"] is None)
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errored": errored,
            "runs_errored": sum(len(v) for v in query_errors.values()),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run trigger evaluation for a skill description")
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument("--description", default=None, help="Override description to test")
    parser.add_argument("--num-workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per query in seconds")
    parser.add_argument("--runs-per-query", type=int, default=3, help="Number of runs per query")
    parser.add_argument("--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold")
    parser.add_argument("--model", default=None, help="Model to use for claude -p (default: user's configured model)")
    parser.add_argument("--verbose", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    try:
        require_claude_cli()
    except EvalRunError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    name, original_description, content = parse_skill_md(skill_path)
    description = args.description or original_description
    project_root = find_project_root()

    if args.verbose:
        print(f"Evaluating: {description}", file=sys.stderr)

    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        project_root=project_root,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        model=args.model,
    )

    summary = output["summary"]
    if args.verbose:
        print(f"Results: {summary['passed']}/{summary['total']} passed", file=sys.stderr)
        for r in output["results"]:
            status = {True: "PASS", False: "FAIL", None: "ERROR"}[r["pass"]]
            rate_str = f"{r['triggers']}/{r['runs']}"
            print(f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:70]}", file=sys.stderr)

    print(json.dumps(output, indent=2))

    if summary["errored"]:
        print(
            f"Error: {summary['errored']} of {summary['total']} queries produced "
            f"no usable run ({summary['runs_errored']} run(s) failed). The score "
            "above is incomplete - do not read it as a measurement of the "
            "description.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
