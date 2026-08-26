"""Three defects in the artifact evaluator, one of them worse than reported.

Found by the 2026-08-23 engine audit. All three make the evaluator's verdict
disagree with what it measured, which is the only thing an evaluator sells.

1. **Manual plan criteria were stamped "fail", and then printed as "PASS".**
   `evaluate_plan_criteria` calls `check(name, None, "... requires manual
   verification")`, and `check` computes `status = "pass" if passed else "fail"`.
   `None` is falsy, so every unverifiable criterion became `"fail"` in `--json`.

   The terminal made the opposite mistake in the same feature. `print_report`
   reads `STATUS_SYMBOLS.get("pass" if c["status"] else "fail")`, and
   `c["status"]` is now a STRING. `"fail"` is truthy, so a genuinely failed
   criterion rendered as `PASS`. Measured 2026-08-23: with `status` in
   `{"pass", "fail"}` both take the `"pass"` branch, and the
   `if c["status"] is None` arm one line above is dead code -- `check` can
   never emit None. A reader of the terminal saw everything pass; a pipeline
   reading `--json` saw unverifiable items fail. The audit reported the second
   half; the first is the one a CEO reads.

2. **CRLF frontmatter failed to parse.** `re.match(r"^---\\n(.*?)\\n---")` after
   a `text.startswith("---")` gate. A `SKILL.md` checked out with
   `core.autocrlf=true` returns "Invalid frontmatter format", and because the
   required-field and metadata checks only run when frontmatter parses, one
   regex decides several results. The evaluator's verdict on identical content
   depended on the checkout OS.

3. **`detect_type` was anchored to the wrong directory.** `main()` resolves
   `artifact_path = ROOT / args.path` and then calls `detect_type(args.path)` --
   the RAW string. Its `p.is_dir()` test resolves against the process cwd, so
   running the evaluator from anywhere but the workspace root with a relative
   skill directory returned "unknown" and exited 1 on a perfectly valid skill.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "artifact-evaluator.py"


def _evaluator():
    spec = importlib.util.spec_from_file_location("artifact_evaluator_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AE = _evaluator()


# --- 1. a manual criterion is neither a pass nor a failure -------------------

def test_check_passes_none_through_as_unverifiable():
    assert AE.check("c", None, "manual")["status"] is None
    assert AE.check("c", True, "ok")["status"] == "pass"
    assert AE.check("c", False, "no")["status"] == "fail"


def test_a_manual_plan_criterion_is_not_reported_as_a_failure(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n- The operator agrees it reads well\n",
        encoding="utf-8")
    results = AE.evaluate_plan_criteria(plan)
    assert results, "no criteria parsed; the fixture no longer exercises this"
    assert all(r["status"] is None for r in results), results


def test_a_criterion_naming_a_real_file_still_passes(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n- `README.md` exists\n", encoding="utf-8")
    results = AE.evaluate_plan_criteria(plan)
    assert [r["status"] for r in results] == ["pass"], results


def test_a_criterion_naming_a_missing_file_still_fails(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n- `no-such-file.md` exists\n",
        encoding="utf-8")
    results = AE.evaluate_plan_criteria(plan)
    assert [r["status"] for r in results] == ["fail"], results


def test_the_terminal_renders_a_failed_criterion_as_a_failure(capsys):
    """The half the audit missed. `"fail"` is a truthy string, so the old
    `"pass" if c["status"] else "fail"` sent EVERY status to the pass symbol."""
    AE.print_report("artifact", "skill", [], plan_criteria=[
        {"name": "c0", "status": "fail", "detail": "a real failure"},
        {"name": "c1", "status": "pass", "detail": "a real pass"},
        {"name": "c2", "status": None, "detail": "unverifiable"},
    ])
    out = capsys.readouterr().out
    fail_line = next(ln for ln in out.splitlines() if "a real failure" in ln)
    pass_line = next(ln for ln in out.splitlines() if "a real pass" in ln)
    manual_line = next(ln for ln in out.splitlines() if "unverifiable" in ln)
    assert "FAIL" in fail_line, f"a failed criterion rendered as: {fail_line!r}"
    assert "PASS" in pass_line, pass_line
    assert "FAIL" not in manual_line and "PASS" not in manual_line, (
        f"an unverifiable criterion was given a verdict: {manual_line!r}"
    )


# --- 2. line endings must not decide the verdict -----------------------------

def test_crlf_frontmatter_parses_the_same_as_lf():
    lf, lf_err = AE.parse_yaml_frontmatter("---\nname: x\ndescription: d\n---\nbody")
    crlf, crlf_err = AE.parse_yaml_frontmatter(
        "---\r\nname: x\r\ndescription: d\r\n---\r\nbody")
    assert lf_err is None and crlf_err is None, (lf_err, crlf_err)
    assert lf == crlf == {"name": "x", "description": "d"}


def test_text_without_frontmatter_is_still_rejected():
    data, err = AE.parse_yaml_frontmatter("# Just a heading\n")
    assert data is None and err


# --- 3. detection and evaluation must anchor to the same directory -----------

def test_detect_type_agrees_from_any_working_directory(tmp_path):
    probe = (
        "import importlib.util, sys, pathlib;"
        f"spec = importlib.util.spec_from_file_location('ae', {str(SCRIPT)!r});"
        "m = importlib.util.module_from_spec(spec); sys.modules['ae'] = m;"
        "spec.loader.exec_module(m);"
        "print('TYPE=' + str(m.detect_type(m.ROOT / '.claude/skills/dream')))"
    )
    from_root = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=120)
    from_tmp = subprocess.run([sys.executable, "-c", probe], cwd=str(tmp_path),
                              capture_output=True, text=True, timeout=120)
    assert from_root.returncode == 0, from_root.stderr[-400:]
    assert from_tmp.returncode == 0, from_tmp.stderr[-400:]
    assert from_root.stdout.strip() == from_tmp.stdout.strip(), (
        f"detect_type answers differently by cwd: root={from_root.stdout.strip()!r} "
        f"tmp={from_tmp.stdout.strip()!r}"
    )
    assert "TYPE=skill" in from_root.stdout


def test_the_cli_evaluates_a_relative_skill_path_from_another_directory(tmp_path):
    """The end-to-end symptom: exit 1, 'Cannot detect artifact type'.

    The absent-string check alone could not tell a fixed run from a run that
    failed some OTHER way, or from one that timed out: none of those prints the
    message either. It now has to finish cleanly and show it identified the
    artifact, which is the behaviour the fix restored.
    """
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", ".claude/skills/dream"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300)
    combined = p.stdout + p.stderr

    assert "Cannot detect artifact type" not in combined, combined[-400:]
    assert p.returncode == 0, combined[-1500:]
    assert "Type: skill" in p.stdout, (
        f"the evaluator never named the artifact it evaluated: {p.stdout[:800]}")
