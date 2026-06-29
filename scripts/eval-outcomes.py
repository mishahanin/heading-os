#!/usr/bin/env python3
"""eval-outcomes.py - binary OUTCOME grading for skills (R13).

Grades a side-effect, not the model's prose. Reads cases from
``.claude/skills/{name}/evals/outcomes/*.json`` - a SEPARATE directory the
prose harness (run-skill-eval.py) and the eval-drift daemon never glob, so
outcome cases are structurally invisible to both and cannot pollute
``benchmark.json`` or be replayed against an empty check set. Each case carries
an ``outcome`` block and is dispatched to a small assertor registry:

  - ``crm_log``       the email->CRM finalizer (`log_to_crm`) produced the right
                      log - right slug, right date, idempotent (a second call is
                      rejected). The real finalizer runs against a throwaway
                      sandbox workspace built from the case fixtures.
  - ``doctype_render``  the doctype data dict has the expected field-presence
                      result (``validate_required_fields == expect_missing``).
                      Default path - in-process, no subprocess, no browser. On
                      ``--render`` it additionally invokes the real renderer and
                      asserts an output file was produced (using the doctype's
                      non-PDF format so the check stays browser-free).

Binary pass/fail per case. No model call - this script imports neither
``anthropic`` nor ``langfuse``. Console-first, browser-free.

Usage:
  python scripts/eval-outcomes.py --skill email-intel
  python scripts/eval-outcomes.py --all
  python scripts/eval-outcomes.py --skill proposal --render
  python scripts/eval-outcomes.py --all --json
  python scripts/eval-outcomes.py --skill email-intel --no-write

Exit codes: 0 all checks passed, 1 one or more checks failed, 2 setup error
(a malformed case, an unknown outcome type, or an assertor that could not run).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_workspace_root

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"


# ============================================================
# Case loading (the isolation boundary)
# ============================================================

def load_outcome_cases(skill_dir: Path) -> list[dict]:
    """Glob skill_dir/evals/outcomes/*.json (top-level only - never recursive,
    so the _staged/ draft subdir is excluded). Stamps case['_path']."""
    out_dir = skill_dir / "evals" / "outcomes"
    if not out_dir.exists():
        return []
    cases: list[dict] = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            cases.append({"id": path.stem, "_path": str(path), "_load_error": str(e)})
            continue
        case["_path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        cases.append(case)
    return cases


def list_skills_with_outcomes() -> list[str]:
    """Skills carrying a non-empty evals/outcomes/ directory."""
    out: list[str] = []
    if not SKILLS_DIR.exists():
        return out
    for child in SKILLS_DIR.iterdir():
        if not child.is_dir():
            continue
        od = child / "evals" / "outcomes"
        if od.exists() and any(od.glob("*.json")):
            out.append(child.name)
    return sorted(out)


def _check(name: str, passed: bool, detail: str = "") -> dict:
    """A result item in the same shape run_checks produces (check/passed/detail)."""
    return {"check": name, "passed": bool(passed), "detail": detail}


# ============================================================
# Outcome assertors
# ============================================================

_MINIMAL_CONTACT = """---
entity_ref: {slug}
relationship_type: prospect
last_touch: 2025-01-01
created: 2025-01-01
---

# {slug}

## Interaction Log
"""


def _assert_crm_log(case: dict, sandbox_root: Path, render: bool) -> list[dict]:
    """Run the real log_to_crm finalizer against a sandbox built from the case.

    outcome fields: conv_id, conversations (list for _latest-fetch.json),
    create_contacts (slugs to seed under crm/contacts/), expect_ok,
    expected_slug, expected_date, expect_error, expect_idempotent.
    """
    from scripts.bridge_daemon.finalizers.crm_log import log_to_crm

    o = case["outcome"]
    results: list[dict] = []

    fetch_dir = sandbox_root / "outputs" / "operations" / "email-intelligence"
    fetch_dir.mkdir(parents=True, exist_ok=True)
    (fetch_dir / "_latest-fetch.json").write_text(
        json.dumps({"conversations": o.get("conversations", [])}), encoding="utf-8"
    )

    contacts_dir = sandbox_root / "crm" / "contacts"
    contacts_dir.mkdir(parents=True, exist_ok=True)
    for slug in o.get("create_contacts", []):
        (contacts_dir / f"{slug}.md").write_text(
            _MINIMAL_CONTACT.format(slug=slug), encoding="utf-8"
        )

    conv_id = o["conv_id"]
    # Sandbox IS the data root for the eval; pass it explicitly so the finalizer
    # reads/writes the sandbox tree, not the real data root (get_data_root()).
    res = log_to_crm(sandbox_root, conv_id, data_root=sandbox_root)

    expect_ok = o["expect_ok"]
    results.append(_check(
        f"log_to_crm ok=={expect_ok}", res.get("ok") is expect_ok, f"got {res}"
    ))
    if expect_ok:
        if "expected_slug" in o:
            results.append(_check(
                f"slug=={o['expected_slug']!r}", res.get("slug") == o["expected_slug"],
                f"got slug={res.get('slug')!r}",
            ))
        if "expected_date" in o:
            results.append(_check(
                f"date=={o['expected_date']!r}", res.get("date") == o["expected_date"],
                f"got date={res.get('date')!r}",
            ))
    else:
        if "expect_error" in o:
            results.append(_check(
                f"error=={o['expect_error']!r}", res.get("error") == o["expect_error"],
                f"got error={res.get('error')!r}",
            ))

    if o.get("expect_idempotent"):
        res2 = log_to_crm(sandbox_root, conv_id, data_root=sandbox_root)
        ok2 = (res2.get("ok") is False
               and res2.get("error") == "conversation already logged to CRM")
        results.append(_check(
            "idempotent (second call rejected, no double-log)", ok2, f"got {res2}"
        ))

    return results


def _render_formats(doctype: str, registry) -> list[str]:
    """Pick the doctype's non-PDF render format(s) for the --render path, so the
    real-file assertion stays browser-free (PDF needs Playwright/chromium)."""
    default = registry[doctype]["formats"]
    non_pdf = [f for f in default if f != "pdf"]
    return non_pdf or default


def _assert_doctype_render(case: dict, sandbox_root: Path, render: bool) -> list[dict]:
    """Assert field-presence (default), and on --render that a file is produced.

    outcome fields: doctype, data (the dict), expect_missing (list).
    """
    from scripts.utils.doctype_renderer import TEMPLATE_REGISTRY, validate_required_fields

    o = case["outcome"]
    doctype = o["doctype"]
    data = o["data"]
    expect_missing = o.get("expect_missing", [])
    results: list[dict] = []

    missing = validate_required_fields(doctype, data)
    results.append(_check(
        f"validate_required_fields({doctype}) == expect_missing",
        sorted(missing) == sorted(expect_missing),
        f"missing={sorted(missing)} expected={sorted(expect_missing)}",
    ))

    # The real-render assertion is only meaningful for a positive (complete)
    # fixture; a negative fixture is intentionally incomplete and the renderer
    # correctly refuses it, so we never attempt to render one.
    if render and not expect_missing:
        out_dir = sandbox_root / "render-out"
        out_dir.mkdir(parents=True, exist_ok=True)
        data_path = sandbox_root / "data.json"
        data_path.write_text(json.dumps(data), encoding="utf-8")
        fmts = _render_formats(doctype, TEMPLATE_REGISTRY)
        render_script = ROOT / "scripts" / "render-doctype.py"
        # No env= override: X31C_TRACE_ID is inherited from os.environ
        # automatically (trace-id.md). Non-PDF format keeps it browser-free.
        proc = subprocess.run(
            [sys.executable, str(render_script), "--type", doctype,
             "--data", str(data_path), "--out", str(out_dir),
             "--formats", ",".join(fmts)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        produced = list(out_dir.rglob("*"))
        produced_files = [p for p in produced if p.is_file()]
        results.append(_check(
            f"render produced a {'/'.join(fmts)} file (exit {proc.returncode})",
            proc.returncode == 0 and len(produced_files) > 0,
            f"exit={proc.returncode} files={[p.name for p in produced_files]} "
            f"stderr={proc.stderr.strip()[:300]}",
        ))

    return results


OUTCOME_ASSERTORS = {
    "crm_log": _assert_crm_log,
    "doctype_render": _assert_doctype_render,
}


# ============================================================
# Runner
# ============================================================

def run_one_case(case: dict, render: bool) -> tuple[list[dict], bool]:
    """Grade one outcome case. Returns (results, setup_error)."""
    if case.get("_load_error"):
        return [_check("case loads", False, f"unreadable: {case['_load_error']}")], True
    outcome = case.get("outcome")
    if not isinstance(outcome, dict) or "type" not in outcome:
        return [_check("case has an outcome block", False,
                       "outcome case missing an 'outcome.type' block")], True
    assertor = OUTCOME_ASSERTORS.get(outcome["type"])
    if assertor is None:
        return [_check("known outcome type", False,
                       f"unknown outcome.type {outcome['type']!r}")], True
    sandbox = Path(tempfile.mkdtemp(prefix="eval-outcome-"))
    try:
        results = assertor(case, sandbox, render)
    except Exception as e:  # noqa: BLE001 - report as a setup failure, never crash the runner
        return [_check("assertor ran", False, f"assertor raised: {e!r}")], True
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return results, False


def run_skill(skill: str, case_filter: str | None, render: bool,
              write_benchmark: bool) -> tuple[int, int, bool]:
    """Run all outcome cases for one skill. Returns (passed, total, setup_error)."""
    skill_dir = SKILLS_DIR / skill
    cases = load_outcome_cases(skill_dir)
    if case_filter:
        cases = [c for c in cases if c.get("id") == case_filter]

    print(f"\n{BOLD}{CYAN}{skill}{RESET}  ({len(cases)} outcome case(s))")
    skill_passed = skill_total = 0
    setup_error = False
    bench_cases: list[dict] = []

    for case in cases:
        results, case_setup_err = run_one_case(case, render)
        setup_error = setup_error or case_setup_err
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        skill_passed += passed
        skill_total += total
        cid = case.get("id", case.get("_path", "?"))
        mark = f"{GREEN}PASS{RESET}" if passed == total else f"{RED}FAIL{RESET}"
        print(f"  {mark} {cid}  {passed}/{total}")
        for r in results:
            if not r["passed"]:
                print(f"      {RED}x{RESET} {r['check']}: {GRAY}{r['detail']}{RESET}")
        bench_cases.append({
            "id": cid, "passed": passed, "total": total,
            "failures": [r for r in results if not r["passed"]],
        })

    if write_benchmark and cases:
        _write_benchmark(skill_dir, skill_passed, skill_total, bench_cases)

    return skill_passed, skill_total, setup_error


def _write_benchmark(skill_dir: Path, passed: int, total: int, cases: list[dict]) -> None:
    """Write evals/benchmark-outcomes.json (distinct filename from the prose
    benchmark.json, so the harness benchmark is never touched)."""
    path = skill_dir / "evals" / "benchmark-outcomes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    last_run = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": "(outcome - no model call)",
        "passed_total": passed,
        "check_total": total,
        "cases": cases,
    }
    existing["last_run"] = last_run
    if "baseline" not in existing:
        existing["baseline"] = last_run.copy()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Binary OUTCOME grading for skills (R13).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--skill", help="grade one skill's evals/outcomes/ cases")
    group.add_argument("--all", action="store_true", help="grade every skill with evals/outcomes/")
    parser.add_argument("--case", help="run only the case with this id")
    parser.add_argument("--render", action="store_true",
                        help="opt-in: actually render doctype cases and assert a file is produced (browser-free, non-PDF format)")
    parser.add_argument("--no-write", action="store_true", help="do not write the benchmark sidecar")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.all:
        skills = list_skills_with_outcomes()
    else:
        skills = [args.skill]
    if not skills:
        print(f"{YELLOW}No skills with evals/outcomes/ found.{RESET}", file=sys.stderr)
        return 2

    overall_passed = overall_total = 0
    any_setup_error = False
    per_skill: dict[str, dict] = {}

    for skill in skills:
        passed, total, setup_error = run_skill(
            skill, args.case, args.render, write_benchmark=not args.no_write
        )
        overall_passed += passed
        overall_total += total
        any_setup_error = any_setup_error or setup_error
        per_skill[skill] = {"passed": passed, "total": total, "setup_error": setup_error}

    if args.json:
        print(json.dumps({
            "skills": per_skill,
            "passed_total": overall_passed,
            "check_total": overall_total,
            "setup_error": any_setup_error,
        }, indent=2))
    else:
        print(f"\n{BOLD}Total: {overall_passed}/{overall_total} checks passed"
              f"{RESET}{'  ' + RED + '(setup errors present)' + RESET if any_setup_error else ''}")

    if any_setup_error:
        return 2
    if overall_total == 0:
        return 0
    return 0 if overall_passed == overall_total else 1


if __name__ == "__main__":
    sys.exit(main())
