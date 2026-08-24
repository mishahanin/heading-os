"""Shard scripts-00-p3: four tools that reported one thing and did another.

- `artifact-evaluator.py` printed `"status": "fail"` for a plan criterion and
  exited 0, so every gate keyed on the exit code passed the plan it had just
  failed.
- `audit-deps.py --json` wrote a human sentence onto stdout in front of
  pip-audit's JSON document, so the documented machine-readable mode emitted
  something no parser accepts. Its docstring also named an export command
  missing both load-bearing flags.
- `audit-skill-bash-paths.py` said it scanned "bash blocks" and scanned every
  unlabelled fence as well, and its `--check` OK line corrupted `--json`.
- `bootcamp-roster.py` crashed the whole roster build on a whitespace-only
  display name.

Every test here reproduces the defect through the tool's own surface, not
through a reimplementation of it.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(script: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def deps():
    return _load("audit-deps.py", "audit_deps")


@pytest.fixture(scope="module")
def bashpaths():
    return _load("audit-skill-bash-paths.py", "audit_skill_bash_paths")


@pytest.fixture(scope="module")
def roster():
    return _load("bootcamp-roster.py", "bootcamp_roster")


def _code(script: str) -> str:
    """Source with whole-line `#` comments stripped.

    A fix whose comment quotes the code it removed is found by a plain grep for
    that code. The comment must stay a `#` comment: docstrings are NOT stripped.
    """
    lines = (ROOT / "scripts" / script).read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


# ============================================================
# A plan criterion that failed, under an exit code that passed
# ============================================================

def _run_evaluator(*args):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "artifact-evaluator.py"), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    return proc


def test_a_failed_plan_criterion_fails_the_run(tmp_path):
    """The whole defect: "fail" in the JSON, 0 on the exit code."""
    plan = tmp_path / "p.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n"
        "- the widget ships per `scripts/there-is-no-such-script.py`\n",
        encoding="utf-8")
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(plan), "--json")
    body = json.loads(proc.stdout)
    statuses = [c["status"] for c in body["plan_criteria"]]
    assert "fail" in statuses, f"the criterion did not fail: {body['plan_criteria']}"
    assert proc.returncode == 1, (
        "a criterion stamped 'fail' left the exit code at 0, so a pipeline "
        "gating on it passed the plan")


def test_a_criterion_needing_a_human_does_not_fail_the_run(tmp_path):
    """status None is "nobody checked", not "checked and broken"."""
    plan = tmp_path / "p.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n"
        "- the tone reads like a person wrote it\n",
        encoding="utf-8")
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(plan), "--json")
    body = json.loads(proc.stdout)
    assert [c["status"] for c in body["plan_criteria"]] == [None]
    assert proc.returncode == 0, "an unverifiable criterion must not fail the run"


def test_a_satisfied_criterion_leaves_the_run_green(tmp_path):
    """The fix must not fail the case it exists to allow."""
    plan = tmp_path / "p.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n"
        "- the auditor exists per `scripts/audit-deps.py`\n",
        encoding="utf-8")
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(plan), "--json")
    body = json.loads(proc.stdout)
    assert [c["status"] for c in body["plan_criteria"]] == ["pass"]
    assert proc.returncode == 0


def test_a_missing_plan_file_fails_the_run(tmp_path):
    """Being handed a plan that is not there is not a pass."""
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(tmp_path / "gone.md"), "--json")
    body = json.loads(proc.stdout)
    assert body["plan_criteria"][0]["name"] == "plan_exists"
    assert proc.returncode == 1


def test_a_failing_artifact_still_fails_without_any_plan():
    """The other half of the same line: `checks` must keep failing the run."""
    proc = _run_evaluator("--path", "scripts/there-is-no-such-script.py", "--json")
    body = json.loads(proc.stdout)
    assert any(c["status"] == "fail" for c in body["checks"])
    assert "plan_criteria" not in body
    assert proc.returncode == 1


def test_a_clean_artifact_with_no_plan_stays_green():
    proc = _run_evaluator("--path", "scripts/audit-deps.py", "--json")
    assert proc.returncode == 0, proc.stdout[-600:]


def test_the_exit_code_reads_both_lists():
    code = _code("artifact-evaluator.py")
    tail = code[code.index("    sys.exit(1 if") - 400:]
    assert "plan_criteria" in tail, "the exit code ignores the plan again"


# ============================================================
# A machine-readable mode with a sentence in front of it
# ============================================================

def _fake_audit_run(deps, monkeypatch, *, exported=True):
    """Drive main() without pip-audit, uv, or a re-exec."""
    monkeypatch.setattr(deps, "_reexec_in_venv_if_needed", lambda: None)
    monkeypatch.setattr(deps, "_have", lambda _n: True)
    monkeypatch.setattr(deps, "_export_full_requirements",
                        lambda dest: (dest.write_text("x==1\n", encoding="utf-8")
                                      or True) if exported else False)

    class _R:
        returncode = 0

    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: _R())


def test_the_json_mode_puts_nothing_of_its_own_on_stdout(deps, monkeypatch,
                                                         capsys):
    """`--json | json.tool` died on the scope line, which is not JSON."""
    _fake_audit_run(deps, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["audit-deps.py", "--json"])
    assert deps.main() == 0
    cap = capsys.readouterr()
    assert cap.out == "", f"stdout is not the JSON stream's alone: {cap.out!r}"
    assert "pip-audit scope" in cap.err, "the scope line vanished instead of moving"


def test_the_scope_line_still_reaches_the_operator(deps, monkeypatch, capsys):
    """Moving it must not silence it; the human mode needs it too."""
    _fake_audit_run(deps, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["audit-deps.py"])
    assert deps.main() == 0
    assert "full locked graph" in capsys.readouterr().err


def test_the_fallback_scope_is_named_too(deps, monkeypatch, capsys):
    _fake_audit_run(deps, monkeypatch, exported=False)
    monkeypatch.setattr(sys, "argv", ["audit-deps.py"])
    assert deps.main() == 0
    assert "fallback" in capsys.readouterr().err


def test_the_docstring_names_every_flag_the_export_actually_passes(deps):
    """The documented command dropped --all-extras, which is the whole point.

    Anyone cloning the docstring's command into another gate reintroduced
    exactly the blind spot this script exists to close.
    """
    # The COMMAND line, not the docstring at large. Prose two lines below can
    # name a flag the command line still omits, and a mutation that reverted
    # only the command line survived a whole-docstring check.
    command_line = next(ln for ln in deps.__doc__.splitlines()
                        if ln.strip().startswith("1. `uv export"))
    for flag in ("--no-hashes", "--all-extras", "--no-emit-project"):
        assert flag in command_line, (
            f"the documented command omits {flag}: {command_line.strip()}")


def test_the_documented_flags_are_the_ones_in_the_code(deps):
    """Not a paraphrase of the code: the same strings, both places."""
    src = _code("audit-deps.py")
    body = src[src.index("def _export_full_requirements"):]
    call = body[body.index('["uv", "export"'):body.index("cwd=str(ROOT)")]
    for flag in ("--no-hashes", "--all-extras", "--no-emit-project"):
        assert flag in call, f"the export no longer passes {flag}"


# ============================================================
# "bash blocks" that were every fence
# ============================================================

_ARROW = "-> threads/{layer}/{slug}.md"


def test_an_unlabelled_fence_is_not_scanned_as_bash(bashpaths, tmp_path):
    """A diagram arrow was counted as a data-path misroute candidate."""
    sk = tmp_path / "SKILL.md"
    sk.write_text(f"# s\n\n```\n{_ARROW}\n```\n", encoding="utf-8")
    assert bashpaths.scan_skill(sk) == []


def test_a_bash_fence_is_still_scanned(bashpaths, tmp_path):
    """Narrowing the scan must not switch it off."""
    sk = tmp_path / "SKILL.md"
    sk.write_text("# s\n\n```bash\npython scripts/x.py > outputs/y.md\n```\n",
                  encoding="utf-8")
    hits = bashpaths.scan_skill(sk)
    assert len(hits) == 1 and "outputs/y.md" in hits[0][1]


@pytest.mark.parametrize("lang", ["sh", "shell", "BASH"])
def test_the_other_shell_labels_are_still_scanned(bashpaths, tmp_path, lang):
    sk = tmp_path / "SKILL.md"
    sk.write_text(f"# s\n\n```{lang}\npython scripts/x.py > outputs/y.md\n```\n",
                  encoding="utf-8")
    assert len(bashpaths.scan_skill(sk)) == 1


@pytest.mark.parametrize("lang", ["python", "json", "text", "yaml"])
def test_a_non_shell_label_is_not_scanned(bashpaths, tmp_path, lang):
    sk = tmp_path / "SKILL.md"
    sk.write_text(f"# s\n\n```{lang}\npython scripts/x.py > outputs/y.md\n```\n",
                  encoding="utf-8")
    assert bashpaths.scan_skill(sk) == []


def test_the_empty_language_is_gone_from_the_fence_set(bashpaths):
    assert "" not in bashpaths._BASH_FENCES


def test_the_calibrate_baseline_went_with_the_widening(bashpaths):
    """Its only hit was the arrow. A stale entry hides the next real one."""
    assert "calibrate" not in bashpaths.BASELINE


def test_the_real_skill_tree_still_has_no_unlabelled_fence_hits(bashpaths):
    """Guards the narrowing itself: if hits reappear, the fence set drifted."""
    from scripts.utils.workspace import get_workspace_root
    found = bashpaths.scan_all(get_workspace_root())
    assert "calibrate" not in found, found.get("calibrate")


def test_json_and_check_together_emit_one_parseable_document(tmp_path):
    """The OK line landed after the JSON, so `| json.tool` died on it."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit-skill-bash-paths.py"),
         "--json", "--check"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)          # the assertion is that this parses
    assert "counts" in body and "baseline" in body
    assert "OK" in proc.stderr, "the OK line vanished instead of moving"


def test_the_ok_line_and_the_fail_line_use_the_same_stream():
    code = _code("audit-skill-bash-paths.py")
    tail = code[code.index("    if args.check:"):]
    ok = tail.index("no SKILL bash data-path regressions")
    assert "file=sys.stderr" in tail[ok:ok + 200], "the OK line is on stdout again"


# ============================================================
# A name of spaces that took the roster with it
# ============================================================

@pytest.mark.parametrize("name", ["   ", "\t", " \n ", ""])
def test_a_blank_display_name_is_not_in_the_prelim_list(roster, name):
    """"   ".split() is [], and [0] on it killed the whole roster build."""
    assert roster.in_prelim(name, "someone", {"james"}) is False


def test_a_real_name_still_matches(roster):
    assert roster.in_prelim("James Bond", "jbond", {"james"}) is True


def test_a_last_name_still_matches(roster):
    assert roster.in_prelim("James Bond", "jbond", {"bond"}) is True


def test_an_initial_form_still_matches(roster):
    assert roster.in_prelim("James Bond", "jbond", {"j. bond"}) is True


def test_a_single_word_name_has_no_last_name_to_match(roster):
    assert roster.in_prelim("Bond", "bond", {"bond"}) is True
    assert roster.in_prelim("Bond", "bond", {"j. bond"}) is False


@pytest.mark.parametrize("entry", ["b. bond", "b.bond"])
def test_a_single_word_name_builds_no_initial_form(roster, entry):
    """One word is a first name, never also its own surname.

    Dropping the `len(parts) > 1` guard makes last_name == first_name, so
    "Bond" starts matching the initial form "B. Bond" -- a prelim entry that
    names a DIFFERENT person whose surname happens to be Bond.
    """
    assert roster.in_prelim("Bond", "bond", {entry}) is False


def test_the_guard_splits_once_and_checks_the_parts():
    code = _code("bootcamp-roster.py")
    body = code[code.index("def in_prelim("):code.index("def in_prelim(") + 900]
    assert "if not parts:" in body, "the guard reads the raw string again"
