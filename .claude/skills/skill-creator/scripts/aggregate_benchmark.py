#!/usr/bin/env python3
"""
Aggregate individual run results into benchmark summary statistics.

Reads grading.json files from run directories and produces:
- run_summary with mean, stddev, min, max for each metric
- delta between with_skill and without_skill configurations

Usage:
    python aggregate_benchmark.py <benchmark_dir>

Example:
    python aggregate_benchmark.py benchmarks/2026-01-15T10-30-00/

The script supports two directory layouts:

    Workspace layout (from skill-creator iterations):
    <benchmark_dir>/
    └── eval-N/
        ├── with_skill/
        │   ├── run-1/grading.json
        │   └── run-2/grading.json
        └── without_skill/
            ├── run-1/grading.json
            └── run-2/grading.json

    Legacy layout (with runs/ subdirectory):
    <benchmark_dir>/
    └── runs/
        └── eval-N/
            ├── with_skill/
            │   └── run-1/grading.json
            └── without_skill/
                └── run-1/grading.json
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def calculate_stats(values: list[float]) -> dict:
    """Calculate mean, stddev, min, max for a list of values."""
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}

    n = len(values)
    mean = sum(values) / n

    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0

    return {
        "mean": round(mean, 4),
        "stddev": round(stddev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4)
    }


def load_run_results(benchmark_dir: Path) -> dict:
    """
    Load all run results from a benchmark directory.

    Returns dict keyed by config name (e.g. "with_skill"/"without_skill",
    or "new_skill"/"old_skill"), each containing a list of run results.
    """
    # Support both layouts: eval dirs directly under benchmark_dir, or under runs/
    runs_dir = benchmark_dir / "runs"
    if runs_dir.exists():
        search_dir = runs_dir
    elif list(benchmark_dir.glob("eval-*")):
        search_dir = benchmark_dir
    else:
        print(f"No eval directories found in {benchmark_dir} or {benchmark_dir / 'runs'}")
        return {}

    results: dict[str, list] = {}

    for eval_idx, eval_dir in enumerate(sorted(search_dir.glob("eval-*"))):
        metadata_path = eval_dir / "eval_metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path) as mf:
                    eval_id = json.load(mf).get("eval_id", eval_idx)
            except (json.JSONDecodeError, OSError):
                eval_id = eval_idx
        else:
            try:
                eval_id = int(eval_dir.name.split("-")[1])
            except ValueError:
                eval_id = eval_idx

        # Discover config directories dynamically rather than hardcoding names
        for config_dir in sorted(eval_dir.iterdir()):
            if not config_dir.is_dir():
                continue
            # Skip non-config directories (inputs, outputs, etc.)
            if not list(config_dir.glob("run-*")):
                continue
            config = config_dir.name
            if config not in results:
                results[config] = []

            for run_dir in sorted(config_dir.glob("run-*")):
                run_number = int(run_dir.name.split("-")[1])
                grading_file = run_dir / "grading.json"

                if not grading_file.exists():
                    print(f"Warning: grading.json not found in {run_dir}")
                    continue

                try:
                    with open(grading_file) as f:
                        grading = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON in {grading_file}: {e}")
                    continue

                # Extract metrics
                result = {
                    "eval_id": eval_id,
                    "run_number": run_number,
                    "pass_rate": grading.get("summary", {}).get("pass_rate", 0.0),
                    "passed": grading.get("summary", {}).get("passed", 0),
                    "failed": grading.get("summary", {}).get("failed", 0),
                    "total": grading.get("summary", {}).get("total", 0),
                }

                # Extract timing -- check grading.json first, then sibling timing.json
                timing = grading.get("timing", {})
                result["time_seconds"] = timing.get("total_duration_seconds", 0.0)
                timing_file = run_dir / "timing.json"
                if result["time_seconds"] == 0.0 and timing_file.exists():
                    try:
                        with open(timing_file) as tf:
                            timing_data = json.load(tf)
                        result["time_seconds"] = timing_data.get("total_duration_seconds", 0.0)
                        result["tokens"] = timing_data.get("total_tokens", 0)
                    except json.JSONDecodeError:
                        pass

                # Extract metrics if available
                metrics = grading.get("execution_metrics", {})
                result["tool_calls"] = metrics.get("total_tool_calls", 0)
                if not result.get("tokens"):
                    result["tokens"] = metrics.get("output_chars", 0)
                result["errors"] = metrics.get("errors_encountered", 0)

                # Extract expectations -- viewer requires fields: text, passed, evidence
                raw_expectations = grading.get("expectations", [])
                for exp in raw_expectations:
                    if "text" not in exp or "passed" not in exp:
                        print(f"Warning: expectation in {grading_file} missing required fields (text, passed, evidence): {exp}")
                result["expectations"] = raw_expectations

                # Extract notes from user_notes_summary
                notes_summary = grading.get("user_notes_summary", {})
                notes = []
                notes.extend(notes_summary.get("uncertainties", []))
                notes.extend(notes_summary.get("needs_review", []))
                notes.extend(notes_summary.get("workarounds", []))
                result["notes"] = notes

                results[config].append(result)

    return results


# The two roles a config directory can play. The delta the benchmark exists to
# report is always PRIMARY minus BASELINE.
#
# Until 2026-08-23 the roles were taken from `sorted()` discovery order, which
# is correct for the new-skill layout ("with_skill" < "without_skill", "new" <
# "old") and INVERTED for the improve-an-existing-skill layout that
# references/running-evals.md prescribes: the baseline saves to `old_skill/`,
# and "old_skill" sorts before "with_skill". A skill that genuinely improved
# reported a negative pass-rate delta, against the one decision the whole
# benchmark supports. `eval-viewer/viewer.html` already knew the answer:
#   isBaseline = config === "without_skill" || config === "old_skill"
BASELINE_CONFIGS = ("without_skill", "old_skill")
PRIMARY_CONFIGS = ("with_skill", "new_skill")


def split_configs(configs: list[str]) -> tuple[str | None, str | None]:
    """Return (primary, baseline) config names, resolved by role.

    Falls back to discovery order only when neither name is recognised, so an
    unnamed layout keeps its old behaviour instead of silently reporting 0.
    """
    primary = next((c for c in configs if c in PRIMARY_CONFIGS), None)
    baseline = next((c for c in configs if c in BASELINE_CONFIGS), None)
    if primary is None and baseline is None:
        primary = configs[0] if configs else None
        baseline = configs[1] if len(configs) >= 2 else None
    elif primary is None:
        primary = next((c for c in configs if c != baseline), None)
    elif baseline is None:
        baseline = next((c for c in configs if c != primary), None)
    return primary, baseline


# What a metric prints when nobody measured it. It must NOT parse as a number:
# until 2026-08-31 an unmeasured configuration produced `{}`, `0 - 0` formatted
# as "+0.00", and "this configuration was never run" rendered identically to
# "these two performed identically". The distinction is the whole point of the
# benchmark - it supports exactly one keep-or-discard decision.
#
# `run_eval.py`'s module docstring already carries the lesson in its first line:
# a run that never happened is not a negative result. It was learned there and
# never applied here, in the module that consumes its output.
NOT_MEASURED = "not measured"


def _empty_stats() -> None:
    """The statistics of a configuration nobody ran. Deliberately not zeros."""
    return None


def aggregate_results(results: dict) -> dict:
    """
    Aggregate run results into summary statistics.

    Returns run_summary with stats for each configuration and delta. A
    configuration with no runs carries `measured: False` and null statistics,
    never a zeroed set that reads as a measurement.
    """
    run_summary = {}
    configs = list(results.keys())

    for config in configs:
        runs = results.get(config, [])

        if not runs:
            run_summary[config] = {
                "measured": False,
                "runs": 0,
                "pass_rate": _empty_stats(),
                "time_seconds": _empty_stats(),
                "tokens": _empty_stats(),
            }
            continue

        pass_rates = [r["pass_rate"] for r in runs]
        times = [r["time_seconds"] for r in runs]
        tokens = [r.get("tokens", 0) for r in runs]

        run_summary[config] = {
            "measured": True,
            "runs": len(runs),
            "pass_rate": calculate_stats(pass_rates),
            "time_seconds": calculate_stats(times),
            "tokens": calculate_stats(tokens)
        }

    # Delta is primary MINUS baseline, and the roles come from the config
    # NAMES, never from discovery order. See split_configs.
    primary_name, baseline_name = split_configs(configs)
    primary = run_summary.get(primary_name, {}) if primary_name else {}
    baseline = run_summary.get(baseline_name, {}) if baseline_name else {}

    missing = [
        label
        for label, name, summary in (
            ("primary", primary_name, primary),
            ("baseline", baseline_name, baseline),
        )
        if name is None or not summary.get("measured")
    ]

    if missing:
        run_summary["delta"] = {
            "pass_rate": NOT_MEASURED,
            "time_seconds": NOT_MEASURED,
            "tokens": NOT_MEASURED,
            "unmeasured": missing,
        }
        return run_summary

    delta_pass_rate = primary["pass_rate"]["mean"] - baseline["pass_rate"]["mean"]
    delta_time = primary["time_seconds"]["mean"] - baseline["time_seconds"]["mean"]
    delta_tokens = primary["tokens"]["mean"] - baseline["tokens"]["mean"]

    run_summary["delta"] = {
        "pass_rate": f"{delta_pass_rate:+.2f}",
        "time_seconds": f"{delta_time:+.1f}",
        "tokens": f"{delta_tokens:+.0f}"
    }

    return run_summary


def generate_benchmark(benchmark_dir: Path, skill_name: str = "", skill_path: str = "") -> dict:
    """
    Generate complete benchmark.json from a benchmark directory on disk.
    """
    return generate_benchmark_from_results(
        load_run_results(benchmark_dir), skill_name, skill_path
    )


def generate_benchmark_from_results(results: dict, skill_name: str = "", skill_path: str = "") -> dict:
    """
    Generate complete benchmark.json from already-loaded run results.

    Split out from `generate_benchmark` so the reporting path can be exercised
    without laying a directory tree, and so a caller that already holds the
    results does not re-read them.
    """
    run_summary = aggregate_results(results)

    # Build runs array for benchmark.json
    runs = []
    for config in results:
        for result in results[config]:
            runs.append({
                "eval_id": result["eval_id"],
                "configuration": config,
                "run_number": result["run_number"],
                "result": {
                    "pass_rate": result["pass_rate"],
                    "passed": result["passed"],
                    "failed": result["failed"],
                    "total": result["total"],
                    "time_seconds": result["time_seconds"],
                    "tokens": result.get("tokens", 0),
                    "tool_calls": result.get("tool_calls", 0),
                    "errors": result.get("errors", 0)
                },
                "expectations": result["expectations"],
                "notes": result["notes"]
            })

    # Determine eval IDs from results
    eval_ids = sorted(set(
        r["eval_id"]
        for config in results.values()
        for r in config
    ))

    benchmark = {
        "metadata": {
            "skill_name": skill_name or "<skill-name>",
            "skill_path": skill_path or "<path/to/skill>",
            "executor_model": "<model-name>",
            "analyzer_model": "<model-name>",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evals_run": eval_ids,
            "runs_per_configuration": 3
        },
        "runs": runs,
        "run_summary": run_summary,
        "notes": []  # To be filled by analyzer
    }

    return benchmark


def generate_markdown(benchmark: dict) -> str:
    """Generate human-readable benchmark.md from benchmark data."""
    metadata = benchmark["metadata"]
    run_summary = benchmark["run_summary"]

    # Determine config names (excluding "delta"). Column A is the primary and
    # column B the baseline, matching the Delta column's own subtraction —
    # otherwise the table shows baseline-first with a primary-minus-baseline
    # delta beside it, and reads backwards.
    configs = [k for k in run_summary if k != "delta"]
    primary_name, baseline_name = split_configs(configs)
    config_a = primary_name or "config_a"
    config_b = baseline_name or "config_b"
    label_a = config_a.replace("_", " ").title()
    label_b = config_b.replace("_", " ").title()

    lines = [
        f"# Skill Benchmark: {metadata['skill_name']}",
        "",
        f"**Model**: {metadata['executor_model']}",
        f"**Date**: {metadata['timestamp']}",
        f"**Evals**: {', '.join(map(str, metadata['evals_run']))} ({metadata['runs_per_configuration']} runs each per configuration)",
        "",
        "## Summary",
        "",
        f"| Metric | {label_a} | {label_b} | Delta |",
        "|--------|------------|---------------|-------|",
    ]

    a_summary = run_summary.get(config_a, {})
    b_summary = run_summary.get(config_b, {})
    delta = run_summary.get("delta", {})

    def cell(summary: dict, metric: str, fmt: str, scale: float = 1.0, unit: str = "") -> str:
        """Render one mean +/- stddev cell, or say the run never happened.

        `.get('mean', 0)` was the defect in table form: an absent metric printed
        `0% +/- 0%`, which is a claim about a measurement nobody took.
        """
        stat = summary.get(metric)
        if not stat or stat.get("mean") is None:
            return NOT_MEASURED
        mean = format(stat["mean"] * scale, fmt)
        stddev = format(stat.get("stddev", 0) * scale, fmt)
        return f"{mean}{unit} +/- {stddev}{unit}"

    def delta_cell(metric: str, unit: str = "") -> str:
        value = delta.get(metric, "--")
        return value if value in (NOT_MEASURED, "--") else f"{value}{unit}"

    lines.append(
        f"| Pass Rate | {cell(a_summary, 'pass_rate', '.0f', 100, '%')} "
        f"| {cell(b_summary, 'pass_rate', '.0f', 100, '%')} "
        f"| {delta_cell('pass_rate')} |"
    )
    lines.append(
        f"| Time | {cell(a_summary, 'time_seconds', '.1f', 1.0, 's')} "
        f"| {cell(b_summary, 'time_seconds', '.1f', 1.0, 's')} "
        f"| {delta_cell('time_seconds', 's')} |"
    )
    lines.append(
        f"| Tokens | {cell(a_summary, 'tokens', '.0f')} "
        f"| {cell(b_summary, 'tokens', '.0f')} "
        f"| {delta_cell('tokens')} |"
    )

    # Notes section
    if benchmark.get("notes"):
        lines.extend([
            "",
            "## Notes",
            ""
        ])
        for note in benchmark["notes"]:
            lines.append(f"- {note}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate benchmark run results into summary statistics"
    )
    parser.add_argument(
        "benchmark_dir",
        type=Path,
        help="Path to the benchmark directory"
    )
    parser.add_argument(
        "--skill-name",
        default="",
        help="Name of the skill being benchmarked"
    )
    parser.add_argument(
        "--skill-path",
        default="",
        help="Path to the skill being benchmarked"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output path for benchmark.json (default: <benchmark_dir>/benchmark.json)"
    )

    args = parser.parse_args()

    if not args.benchmark_dir.exists():
        print(f"Directory not found: {args.benchmark_dir}")
        sys.exit(1)

    # Generate benchmark
    benchmark = generate_benchmark(args.benchmark_dir, args.skill_name, args.skill_path)

    # Determine output paths
    output_json = args.output or (args.benchmark_dir / "benchmark.json")
    output_md = output_json.with_suffix(".md")

    # Write benchmark.json
    with open(output_json, "w") as f:
        json.dump(benchmark, f, indent=2)
    print(f"Generated: {output_json}")

    # Write benchmark.md
    markdown = generate_markdown(benchmark)
    with open(output_md, "w") as f:
        f.write(markdown)
    print(f"Generated: {output_md}")

    # Print summary
    run_summary = benchmark["run_summary"]
    configs = [k for k in run_summary if k != "delta"]
    delta = run_summary.get("delta", {})

    print("\nSummary:")
    for config in configs:
        label = config.replace("_", " ").title()
        stat = run_summary[config].get("pass_rate")
        if not stat or stat.get("mean") is None:
            # Never "0.0% pass rate" for a configuration nobody ran.
            print(f"  {label}: {NOT_MEASURED} (no runs found)")
            continue
        print(f"  {label}: {stat['mean']*100:.1f}% pass rate")
    print(f"  Delta:         {delta.get('pass_rate', '--')}")
    if delta.get("unmeasured"):
        print(f"  (delta unavailable: {', '.join(delta['unmeasured'])} never ran)")


if __name__ == "__main__":
    main()
