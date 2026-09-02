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
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# The four sibling shards of this campaign all carry this line; this file both
# omitted it and imports `scripts.utils.workspace` by package path, so under
# the `pytest` console script (which does not prepend the cwd the way
# `python -m pytest` does) that one test raised ModuleNotFoundError - an ERROR
# in an otherwise green shard, of the shape that reads as an environment
# problem rather than a defect. Added 2026-08-30.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.code_only import code_of, strip_comments  # noqa: E402


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
    """Source with its comments stripped, string literals left intact.

    A fix whose comment quotes the code it removed is found by a plain grep for
    that code. The comment must stay a `#` comment: docstrings are NOT stripped.

    Routed through `tests/code_only.py` on 2026-09-02. The implementation was
    `ln for ln in lines if not ln.lstrip().startswith("#")`, which drops any
    PHYSICAL line beginning with `#` - including one inside a triple-quoted
    docstring, where a `#` is string content and not a comment at all. So the
    sentence above was false of the code beneath it, and the three sweeps built
    on this helper could not see a `#`-leading line of a docstring in
    `artifact-evaluator.py`, `audit-deps.py` or `bootcamp-roster.py`. The
    shared helper asks `tokenize`, which is the front end's own answer to which
    `#` opens a comment, and it refuses rather than passing untouched source
    back when a file will not parse.
    """
    return code_of(ROOT / "scripts" / script)


def test_the_code_stripper_removes_comments_and_keeps_docstrings():
    """The helper's own contract, which nothing measured until 2026-09-02.

    Row 2 is the one the line-based predecessor got wrong: a `#`-leading line
    INSIDE a docstring is string content, and dropping it made the sentence in
    `_code`'s own docstring false.
    """
    module = (
        '"""Title.\n'
        '\n'
        '# plan_criteria regression sentinel\n'
        '"""\n'
        '# a real whole-line comment naming plan_criteria\n'
        'FLAG = "--no-hashes"  # a trailing comment naming plan_criteria\n'
    )
    stripped = strip_comments(module, where="<synthetic>")

    assert "# plan_criteria regression sentinel" in stripped, (
        "a `#` line inside a docstring was stripped; it is string content, not "
        "a comment, and this helper promises docstrings survive")
    assert "a real whole-line comment" not in stripped
    assert "a trailing comment" not in stripped
    assert 'FLAG = "--no-hashes"' in stripped, (
        "the code before a trailing comment must survive byte for byte")


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


def test_a_plan_that_is_not_utf8_is_still_graded(tmp_path):
    """A traceback is not a verdict, and this gate's whole output is verdicts.

    Every read in `artifact-evaluator.py` used a bare utf-8 decode, so one stray
    byte in the plan - the operator-authored input here - raised
    UnicodeDecodeError, a ValueError caught nowhere between the read and `main`.
    MEASURED 2026-09-01: no JSON, no report, exit 1 on a stack trace, and exit 1
    is the code this gate uses for "a criterion failed".

    The criterion below names a script that does not exist, so the graded answer
    is knowable: it must still come out `fail`, not vanish.
    """
    plan = tmp_path / "p.md"
    plan.write_bytes(
        "# Plan\n\n## Success Criteria\n\n"
        "- caf\xe9: the widget ships per `scripts/there-is-no-such-script.py`\n"
        .encode("latin-1"))
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(plan), "--json")
    assert "Traceback" not in proc.stderr, proc.stderr
    body = json.loads(proc.stdout)
    assert "fail" in [c["status"] for c in body["plan_criteria"]], body["plan_criteria"]
    assert proc.returncode == 1


def test_an_artifact_that_is_not_utf8_is_still_reported_on(tmp_path):
    """The same read, on the artifact side rather than the plan side."""
    script = tmp_path / "tool.py"
    script.write_bytes(b"#!/usr/bin/env python3\n# caf\xe9\nprint('hi')\n")
    proc = _run_evaluator("--path", str(script), "--json")
    assert "Traceback" not in proc.stderr, proc.stderr
    body = json.loads(proc.stdout)
    assert body["checks"], "the evaluator produced no checks at all"


def test_a_utf8_plan_is_unchanged_by_the_replacement(tmp_path):
    """Anchor: `errors="replace"` must not move an ordinary verdict."""
    plan = tmp_path / "p.md"
    plan.write_text(
        "# Plan\n\n## Success Criteria\n\n"
        "- the auditor exists per `scripts/audit-deps.py`\n",
        encoding="utf-8")
    proc = _run_evaluator("--path", "scripts/audit-deps.py",
                          "--plan", str(plan), "--json")
    assert [c["status"] for c in json.loads(proc.stdout)["plan_criteria"]] == ["pass"]
    assert proc.returncode == 0


def test_the_exit_code_reads_both_lists():
    code = _code("artifact-evaluator.py")
    tail = code[code.index("    sys.exit(1 if") - 400:]
    assert "plan_criteria" in tail, "the exit code ignores the plan again"


# ============================================================
# A machine-readable mode with a sentence in front of it
# ============================================================

def _fake_audit_run(deps, monkeypatch, *, exported=True):
    """Drive main() without pip-audit, uv, or a re-exec.

    `_export_full_requirements` returns one of three strings since 2026-08-25,
    not a bool. It returned False for both "uv is absent" and "uv is here and
    the export failed", and the caller read both as the first -- so a corrupt
    lockfile silently downgraded the audit to the active virtualenv. The
    `exported=False` case here is the ABSENT one, which still falls back;
    the failed one is covered in
    `tests/test_a_gate_that_reported_a_scope_it_never_assembled.py`.
    """
    monkeypatch.setattr(deps, "_reexec_in_venv_if_needed", lambda: None)
    monkeypatch.setattr(deps, "_have", lambda _n: True)
    def _export(dest):
        # A named function, not `write_text(...) or EXPORT_OK`. `write_text`
        # returns the CHARACTER COUNT, which is truthy, so `or` short-circuits
        # on it. The old form returned 5 where it meant True and got away with
        # it only because the caller tested truthiness.
        if not exported:
            return deps.EXPORT_NO_UV
        dest.write_text("x==1\n", encoding="utf-8")
        return deps.EXPORT_OK

    monkeypatch.setattr(deps, "_export_full_requirements", _export)

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


def test_a_skill_that_is_not_utf8_is_still_scanned(bashpaths, tmp_path):
    """`scan_skill` read with a bare utf-8 decode and this audit walks the whole
    skill tree, so one stray byte anywhere raised UnicodeDecodeError - a
    ValueError, caught nowhere on the path - and ended the run naming no file.

    The bash fence below the bad byte must still be scanned: a fix that skipped
    the file would swap a loud crash for a silent gap in a `--check` gate.
    """
    sk = tmp_path / "SKILL.md"
    sk.write_bytes(
        b"# s\n\ncaf\xe9\n\n```bash\npython scripts/x.py > outputs/y.md\n```\n")
    hits = bashpaths.scan_skill(sk)
    assert len(hits) == 1 and "outputs/y.md" in hits[0][1], hits


def test_a_decodable_skill_scans_the_same_as_before(bashpaths, tmp_path):
    """Anchor: the replacement must not change an ordinary file's line numbers."""
    sk = tmp_path / "SKILL.md"
    sk.write_text("# s\n\n```bash\npython scripts/x.py > outputs/y.md\n```\n",
                  encoding="utf-8")
    hits = bashpaths.scan_skill(sk)
    assert len(hits) == 1 and hits[0][0] == 4, hits


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
    """Both --check report lines go to stderr, so --json stays parseable.

    The name promised both lines and the body located only the OK one: the
    FAIL line was never found, never sliced and never checked, and the sole
    behavioural companion asserted `returncode == 0` first, so it only ever
    ran the branch where the FAIL line is not printed. A regression putting
    the FAIL line back on stdout corrupted `--json --check` on a failing tree
    while every test here stayed green.
    """
    import ast

    tree = ast.parse((ROOT / "scripts" / "audit-skill-bash-paths.py")
                     .read_text(encoding="utf-8"))
    checks = [n for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and isinstance(n.test, ast.Attribute)
              and n.test.attr == "check"]
    assert len(checks) == 1, (
        f"expected one `if args.check:` block, found {len(checks)}")

    def to_stderr(call: ast.Call) -> bool:
        return any(kw.arg == "file"
                   and isinstance(kw.value, ast.Attribute)
                   and kw.value.attr == "stderr"
                   for kw in call.keywords)

    prints = [n for n in ast.walk(checks[0])
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == "print"]
    assert len(prints) >= 4, (
        f"the --check block prints {len(prints)} lines; the OK line, the FAIL "
        "header, the per-regression line and the remedy line are all expected")
    on_stdout = [ast.unparse(n)[:80] for n in prints if not to_stderr(n)]
    assert not on_stdout, (
        f"these --check report lines are on stdout again: {on_stdout}")


def test_json_and_check_on_a_failing_tree_still_emit_one_parseable_document(tmp_path):
    """The behavioural half: exit 1, and stdout is still nothing but JSON.

    The source pins above say where the print goes; this drives the tool at a
    scratch workspace carrying a skill that is not in BASELINE, which is a
    regression by definition, and reads stdout back through `json.loads`.
    Without it the FAIL branch has no test that ever executes it.

    WORKSPACE_ROOT and cwd both point at the scratch tree: `get_workspace_root`
    reads the variable, and a child with neither would scan the operator's own
    `.claude/skills/`.
    """
    skill = tmp_path / ".claude" / "skills" / "a-skill-not-in-the-baseline"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# Demo\n\n```bash\npython scripts/demo.py --out outputs/demo/report.md\n```\n",
        encoding="utf-8")

    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit-skill-bash-paths.py"),
         "--json", "--check"],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(tmp_path), env=env)

    assert proc.returncode == 1, (
        f"a skill outside BASELINE is a regression; got rc={proc.returncode}\n"
        f"{proc.stdout}\n{proc.stderr}")
    payload = json.loads(proc.stdout)
    assert payload["counts"] == {"a-skill-not-in-the-baseline": 1}
    assert "misroute candidate" in proc.stderr, (
        "the FAIL line did not reach stderr at all")


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
