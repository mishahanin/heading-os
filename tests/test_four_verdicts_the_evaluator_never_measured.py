"""Four verdicts `scripts/artifact-evaluator.py` printed without measuring them.

Found by the 2026-09-02 engine audit. The evaluator's whole product is the
sentence "this artifact is in this state", so each of these is the same defect
wearing a different hat: a status that reports something the code never
established.

1. **A CLI flag was resolved as a file path, and failed the run.**
   `evaluate_plan_criteria` pulled the first inline-code span out of a success
   criterion and did `ROOT / span`. MEASURED 2026-09-02 on the criterion "The
   CLI supports `--json` output": the evaluator returned
   `{"status": "fail", "detail": "... file NOT found: --json"}`, and `main`'s
   `sys.exit(1 if failed else 0)` reads plan criteria, so a plan that merely
   mentions a flag failed the gate for a file nobody meant to exist.

2. **A trigger test that never ran was reported as one that passed.** Exit code
   3 from `scripts/skill-trigger-test.py` means no API key and no SDK, so
   nothing was evaluated. `run_trigger_test` returned `check(..., True, ...)`,
   which renders `"pass"` and counts toward `passed` and `score`. The file
   already carried a non-verdict for exactly this shape (`passed=None` ->
   `status: None`, used by the manual plan criteria), and the skip did not use
   it. A degraded host therefore scored HIGHER on every skill than a host that
   ran the test and found a routing miss, because the miss is a `warn` and the
   skip was a `pass`.

3. **A near-threshold warning that could never fire.** Both call sites computed
   `warn=(line_count >= 450 and ok)`, true only when `passed` is true, while
   `check` emitted `"warn"` only on `warn and not passed`. The two conditions
   are mutually exclusive. MEASURED 2026-09-02 before the fix:
   `check("x", True, "d", warn=True)["status"] == "pass"`, so no SKILL.md
   approaching 500 lines and no rule approaching its budget had ever drawn the
   warning, from the initial import onward. The warning exists to flag a
   PASSING artifact that is running out of room, and `check`'s `warn` means the
   opposite thing (a failure that is only advisory), so the fix is a second
   keyword rather than a change to what `warn` means.

4. **The kebab-case check accepted what it forbids.** `name_format` read
   `re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name) or (len(name) == 1 and
   name.isalpha())`. The regex needs two characters, so the escape hatch is
   load-bearing for a genuine one-character skill name; but `str.isalpha` is
   true for `"A"` and for `"é"`-style non-ASCII letters, so `A` and
   `é` were both stamped "is kebab-case". A check that passes the thing it
   names is worse than no check, because the pass is read as coverage.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "artifact-evaluator.py"


def _evaluator():
    spec = importlib.util.spec_from_file_location("artifact_evaluator_verdicts", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AE = _evaluator()


def _plan(tmp_path: Path, *criteria: str) -> Path:
    plan = tmp_path / "plan.md"
    body = "".join(f"- {c}\n" for c in criteria)
    plan.write_text(f"# Plan\n\n## Success Criteria\n\n{body}", encoding="utf-8")
    return plan


def _statuses(results) -> list:
    return [r["status"] for r in results]


# ============================================================
# 1. An inline-code span is not automatically a file path
# ============================================================

def test_a_criterion_naming_a_cli_flag_is_not_a_missing_file(tmp_path):
    """The measured symptom: `--json` looked up as `ROOT / "--json"`."""
    results = AE.evaluate_plan_criteria(
        _plan(tmp_path, "The CLI supports `--json` output"))
    assert _statuses(results) == [None], results
    assert "NOT found" not in results[0]["detail"], results[0]["detail"]


def test_a_criterion_naming_an_identifier_is_not_a_missing_file(tmp_path):
    """The same shape without a leading dash: a function, a key, a status word.

    These are what plan criteria are mostly written in, and every one of them
    used to be looked up on disk and stamped `fail`.
    """
    results = AE.evaluate_plan_criteria(_plan(
        tmp_path,
        "`get_data_root()` is the only resolver",
        "the tier resolves `gated`",
        "`parallel_safe` is set on every skill",
    ))
    assert _statuses(results) == [None, None, None], results


def test_a_criterion_naming_a_whole_command_is_not_a_missing_file(tmp_path):
    """A span holding whitespace is a command line, not a path.

    `ROOT / "python scripts/x.py --json"` is a single directory entry with a
    space in it, and it has never existed, so this failed the run too.
    """
    results = AE.evaluate_plan_criteria(
        _plan(tmp_path, "`python scripts/artifact-evaluator.py --json` exits 0"))
    assert _statuses(results) == [None], results


def test_a_criterion_naming_a_real_path_still_passes(tmp_path):
    """The case the resolution exists for must survive the narrowing.

    Both signals are covered: a slash-bearing path, and an extension-bearing
    name at the repository root.
    """
    results = AE.evaluate_plan_criteria(_plan(
        tmp_path,
        "the auditor exists per `scripts/audit-deps.py`",
        "`README.md` exists",
    ))
    assert _statuses(results) == ["pass", "pass"], results


def test_a_criterion_naming_a_missing_path_still_fails(tmp_path):
    """The other direction. A narrowing that stops failing anything is not a
    narrowing, it is a disabled check."""
    results = AE.evaluate_plan_criteria(_plan(
        tmp_path,
        "the widget ships per `scripts/there-is-no-such-script.py`",
        "`no-such-file.md` exists",
    ))
    assert _statuses(results) == ["fail", "fail"], results


def test_the_flag_criterion_no_longer_fails_the_process(tmp_path):
    """End to end, because the harm was the exit code rather than the wording.

    `main` folds plan criteria into `sys.exit(1 if failed else 0)`, so the
    stamped `fail` above was a gate refusing a plan for a flag it read as a
    filename.
    """
    plan = _plan(tmp_path, "The CLI supports `--json` output")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", "scripts/audit-deps.py",
         "--plan", str(plan), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout[-800:]


# ============================================================
# 2. A check that could not run is not a check that passed
# ============================================================

def _skill_with_triggers(tmp_path: Path) -> Path:
    d = tmp_path / "someskill"
    d.mkdir(exist_ok=True)
    (d / "triggers.json").write_text("[]", encoding="utf-8")
    return d


def _done(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_a_trigger_test_that_never_ran_is_not_a_pass(tmp_path, monkeypatch):
    """Exit 3 is "no API key, no SDK". Nothing was evaluated."""
    monkeypatch.setattr(AE.subprocess, "run", lambda *a, **k: _done(3))
    res = AE.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] is None, res
    assert "skipped" in res["detail"], res


def test_a_skipped_check_is_not_counted_as_a_failure_either(tmp_path,
                                                            monkeypatch):
    """The other half. Moving a false pass to a false fail is not a fix.

    `main` exits 1 on any `"fail"`, so a degraded host with no API key must not
    start failing every skill it evaluates.
    """
    monkeypatch.setattr(AE.subprocess, "run", lambda *a, **k: _done(3))
    res = AE.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] != "fail", res


def test_a_skipped_check_leaves_the_score_denominator_alone():
    """A non-verdict must not be scored in either direction.

    Counted in `total` it drags the score down exactly as a failure would;
    counted in `passed` it inflates it. It belongs in neither, and the summary
    has to say how many were skipped or the missing denominator is invisible.
    """
    checks = [
        {"name": "a", "status": "pass", "detail": ""},
        {"name": "b", "status": "pass", "detail": ""},
        {"name": "c", "status": None, "detail": "skipped"},
    ]
    body = AE.build_json_output("p", "skill", checks)
    summary = body["summary"]
    assert summary["total"] == 2, summary
    assert summary["passed"] == 2, summary
    assert summary["score"] == 1.0, summary
    assert summary["skipped"] == 1, summary
    assert summary["failed"] == 0, summary


def test_the_terminal_gives_a_skipped_check_no_verdict(capsys):
    """The report a human reads must not print PASS over a check that did not run."""
    AE.print_report("artifact", "skill", [
        {"name": "trigger_test", "status": None, "detail": "trigger-test skipped"},
    ])
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "trigger-test skipped" in ln)
    assert "PASS" not in line and "FAIL" not in line and "WARN" not in line, line
    assert "Score: 0/0" in out, out


def test_a_real_routing_verdict_is_still_scored(tmp_path, monkeypatch):
    """Anchor. A run that DID happen keeps its pass/warn verdict."""
    import json as _json
    monkeypatch.setattr(AE.subprocess, "run", lambda *a, **k: _done(
        0, stdout=_json.dumps({"overall_rate": 1.0, "total_passed": 8,
                               "total_cases": 8})))
    assert AE.run_trigger_test(_skill_with_triggers(tmp_path))["status"] == "pass"
    monkeypatch.setattr(AE.subprocess, "run", lambda *a, **k: _done(
        0, stdout=_json.dumps({"overall_rate": 0.5, "total_passed": 4,
                               "total_cases": 8})))
    assert AE.run_trigger_test(_skill_with_triggers(tmp_path))["status"] == "warn"


# ============================================================
# 3. The near-threshold warning had to be able to fire
# ============================================================

def test_a_passing_check_can_carry_an_advisory_warning():
    """The mutually exclusive pair, asked of `check` directly.

    `warn` downgrades a FAILURE; the near-threshold notice raises a PASS. Both
    have to exist, and reusing one keyword for both is what made the second one
    unreachable.
    """
    assert AE.check("x", True, "d", advisory=True)["status"] == "warn"
    assert AE.check("x", True, "d", advisory=False)["status"] == "pass"


def test_the_advisory_flag_does_not_change_what_warn_means():
    """Every existing call site passes `warn=True` as a severity downgrade. If
    that started meaning "warn even on a pass", each of them would flip."""
    assert AE.check("x", True, "d", warn=True)["status"] == "pass"
    assert AE.check("x", False, "d", warn=True)["status"] == "warn"
    assert AE.check("x", False, "d", warn=False)["status"] == "fail"
    assert AE.check("x", None, "d", advisory=True)["status"] is None


def test_a_skill_approaching_the_line_cap_draws_a_warning(tmp_path):
    """The behaviour, through `evaluate_skill` rather than through `check`."""
    skill = tmp_path / "near"
    skill.mkdir()
    body = "\n".join(f"line {i}" for i in range(470))
    (skill / "SKILL.md").write_text(
        "---\nname: near\ndescription: d\n---\n\n# Near\n\n" + body + "\n",
        encoding="utf-8")
    line_count = next(c for c in AE.evaluate_skill(skill) if c["name"] == "line_count")
    assert line_count["status"] == "warn", line_count


def test_a_skill_well_under_the_line_cap_does_not(tmp_path):
    """Negative case, or the assertion above passes on a check that always warns."""
    skill = tmp_path / "short"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: short\ndescription: d\n---\n\n# Short\n", encoding="utf-8")
    line_count = next(c for c in AE.evaluate_skill(skill) if c["name"] == "line_count")
    assert line_count["status"] == "pass", line_count


def test_a_skill_over_the_line_cap_still_fails(tmp_path):
    """The advisory keyword must not soften the hard limit into a warning."""
    skill = tmp_path / "over"
    skill.mkdir()
    body = "\n".join(f"line {i}" for i in range(600))
    (skill / "SKILL.md").write_text(
        "---\nname: over\ndescription: d\n---\n\n# Over\n\n" + body + "\n",
        encoding="utf-8")
    line_count = next(c for c in AE.evaluate_skill(skill) if c["name"] == "line_count")
    assert line_count["status"] == "fail", line_count


def test_a_rule_approaching_its_budget_draws_a_warning(tmp_path):
    """The second call site. A plain rule's budget is 80 lines, warn at 60."""
    rule = tmp_path / "plain-rule.md"
    rule.write_text("# Rule\n\n" + "\n".join(f"line {i}" for i in range(65)) + "\n",
                    encoding="utf-8")
    concise = next(c for c in AE.evaluate_rule(rule) if c["name"] == "concise")
    assert concise["status"] == "warn", concise


def test_a_short_rule_does_not(tmp_path):
    rule = tmp_path / "plain-rule.md"
    rule.write_text("# Rule\n\nshort.\n", encoding="utf-8")
    concise = next(c for c in AE.evaluate_rule(rule) if c["name"] == "concise")
    assert concise["status"] == "pass", concise


def test_a_long_rule_still_fails(tmp_path):
    rule = tmp_path / "plain-rule.md"
    rule.write_text("# Rule\n\n" + "\n".join(f"line {i}" for i in range(120)) + "\n",
                    encoding="utf-8")
    concise = next(c for c in AE.evaluate_rule(rule) if c["name"] == "concise")
    assert concise["status"] == "fail", concise


# ============================================================
# 4. The kebab-case check must refuse what it names
# ============================================================

def _name_format(tmp_path: Path, name: str):
    skill = tmp_path / "s"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n\n# S\n", encoding="utf-8")
    return next((c for c in AE.evaluate_skill(skill) if c["name"] == "name_format"), None)


def test_a_single_uppercase_letter_is_not_kebab_case(tmp_path):
    """`"A".isalpha()` is true, so the escape hatch waved it through."""
    assert _name_format(tmp_path, "A")["status"] == "fail"


def test_a_single_non_ascii_letter_is_not_kebab_case(tmp_path):
    """`str.isalpha` is true across the whole Unicode letter category, so an
    accented character passed a check whose regex is ASCII-only."""
    assert _name_format(tmp_path, "é")["status"] == "fail"


def test_a_single_lowercase_letter_is_still_kebab_case(tmp_path):
    """Why the escape hatch is load-bearing rather than removable: the regex
    `^[a-z0-9][a-z0-9-]*[a-z0-9]$` needs two characters, so a legitimate
    one-character name fails it. The hatch is narrowed, not deleted."""
    assert _name_format(tmp_path, "x")["status"] == "pass"


def test_an_ordinary_kebab_name_is_unaffected(tmp_path):
    assert _name_format(tmp_path, "meeting-prep")["status"] == "pass"
    assert _name_format(tmp_path, "zk")["status"] == "pass"


def test_an_uppercase_multi_character_name_still_fails(tmp_path):
    """Anchor on the regex arm, so a fix to the hatch cannot quietly widen it."""
    assert _name_format(tmp_path, "MeetingPrep")["status"] == "fail"
