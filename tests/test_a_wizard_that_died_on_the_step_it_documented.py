"""Shard scripts-14-p2: the bootstrap wizard and the two skill gates.

* `scripts/setup.py` step 11 does `from scripts.utils.schedule import ...`, and
  NOTHING in the module ever put the workspace root on sys.path -- the docstring
  said it did. The documented launch `python scripts/setup.py` puts only
  `<repo>/scripts` on sys.path, so the step died with an unhandled
  ModuleNotFoundError; `main()` catches only KeyboardInterrupt, so steps 12 and
  13 never ran, after ten steps had already taken effect. It passed review
  because THIS repo's .venv carries an editable .pth that makes the root
  importable, and that venv is exactly what a fresh exec clone lacks (step 10
  is what creates it).

* `scripts/skill-metadata-check.py` returned early on a frontmatter parse error,
  BEFORE the coverage block, so the skill's triggers.json was never opened. The
  gate its own docstring calls UNCONDITIONAL therefore passed a broken SKILL.md
  shipping a one-case corpus, and the coverage tally printed four zeros for a
  tree it had just walked.

* The same script measured `size_lines` with `raw.count("\\n")` -- line
  TERMINATORS, not lines -- so a 501-line SKILL.md with no trailing newline was
  reported as 500 and cleared the 500-line hard cap.

* `scripts/skill-trigger-test.py` dropped skipped skills before computing
  `unmeasured`, so a typo'd `--skill` name and a `triggers.json` holding `[]`
  both exited 0 under `--strict` -- a clean routing check that judged nothing.
  Its skip line also said "no triggers.json" for a skill whose triggers.json
  exists.

Nothing here installs a scheduled task or calls a judge model.

Run: python3 -m pytest tests/test_a_wizard_that_died_on_the_step_it_documented.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def setup_mod():
    return _load("scripts/setup.py", "setup_under_test")


@pytest.fixture(scope="module")
def smc():
    return _load("scripts/skill-metadata-check.py", "smc_under_test")


@pytest.fixture(scope="module")
def stt():
    return _load("scripts/skill-trigger-test.py", "stt_under_test")


# ============================================================
# The step that died on the import its docstring promised
# ============================================================

def test_the_module_top_level_needs_no_workspace_import(setup_mod):
    """The other half of the promise: importing it must not touch the seam."""
    assert setup_mod.WORKSPACE_ROOT == ROOT


def test_the_root_is_placed_on_sys_path(setup_mod, monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])

    setup_mod._ensure_workspace_importable()

    assert sys.path[0] == str(ROOT)


def test_placing_the_root_twice_does_not_duplicate_it(setup_mod, monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])

    setup_mod._ensure_workspace_importable()
    setup_mod._ensure_workspace_importable()

    assert sys.path.count(str(ROOT)) == 1


def test_the_documented_launch_can_resolve_the_schedule_helper(tmp_path):
    """The real failure, reproduced the way the operator hits it: sys.path[0] is
    `<repo>/scripts` and the repo root is absent, exactly as `python
    scripts/setup.py` leaves it. Nothing is installed - the probe stops at
    resolving the module spec."""
    probe = tmp_path / "probe.py"
    probe.write_text(textwrap.dedent(f"""
        import importlib.util, sys
        REPO = {str(ROOT)!r}
        sys.path[:] = [p for p in sys.path if p not in ("", REPO, REPO + "/")]
        sys.path.insert(0, REPO + "/scripts")
        spec = importlib.util.spec_from_file_location("s", REPO + "/scripts/setup.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        before = importlib.util.find_spec("scripts.utils.schedule") if "scripts" in sys.modules else None
        mod._ensure_workspace_importable()
        print("RESOLVED" if importlib.util.find_spec("scripts.utils.schedule") else "NO")
    """), encoding="utf-8")

    proc = subprocess.run([sys.executable, "-E", "-S", str(probe)],
                          cwd=str(tmp_path), capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "RESOLVED" in proc.stdout


def test_the_step_itself_resolves_the_import_on_the_documented_launch(tmp_path):
    """The seam through the STEP, not through the helper. `install_sentinel` is
    False, so the real schedule module is imported and nothing is installed:
    the import is the whole subject. A step that stops preparing sys.path fails
    here even though every in-process test stubs the module away."""
    probe = tmp_path / "probe_step.py"
    probe.write_text(textwrap.dedent(f"""
        import importlib.util, sys
        REPO = {str(ROOT)!r}
        sys.path[:] = [p for p in sys.path if p not in ("", REPO, REPO + "/")]
        sys.path.insert(0, REPO + "/scripts")
        spec = importlib.util.spec_from_file_location("s", REPO + "/scripts/setup.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        from pathlib import Path
        mod.STATE_FILE = Path({str(tmp_path)!r}) / "setup-state.json"
        got = mod.step_install_sync({{"completed_steps": []}}, {{"slug": "x"}},
                                    install_sentinel=False)
        print("RESULT", got)
    """), encoding="utf-8")

    # -S matters: this venv carries an editable .pth that makes the repo root
    # importable, which is exactly what hid the defect. Without -S the probe
    # would pass with no sys.path work at all.
    proc = subprocess.run([sys.executable, "-E", "-S", str(probe)],
                          cwd=str(tmp_path), capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "RESULT True" in proc.stdout


def test_a_broken_schedule_helper_is_reported_not_raised(setup_mod, monkeypatch,
                                                         capsys):
    """main() catches only KeyboardInterrupt, so a raise here skipped steps 12
    and 13. The step reports and returns False instead."""
    monkeypatch.setitem(sys.modules, "scripts.utils.schedule", None)

    result = setup_mod.step_install_sync({"completed_steps": []}, {"slug": "x"})

    assert result is False
    out = capsys.readouterr().out
    assert "schedule helper" in out
    assert "--reinstall-schedule" in out


def test_the_step_installs_exactly_once_when_the_import_works(setup_mod,
                                                             monkeypatch,
                                                             tmp_path):
    """No real timer is written: the helper module is a stand-in. The state
    file is redirected too - `mark_done` calls `save_state`, so an unredirected
    run writes the workspace's own `.sync/setup-state.json`."""
    calls = []
    fake = types.ModuleType("scripts.utils.schedule")
    fake.install_sentinel_schedule = lambda slug, root: calls.append((slug, root))
    monkeypatch.setitem(sys.modules, "scripts.utils.schedule", fake)
    monkeypatch.setattr(setup_mod, "STATE_FILE", tmp_path / "setup-state.json")
    state = {"completed_steps": []}

    setup_mod.step_install_sync(state, {"slug": "demo"})

    assert calls == [("demo", setup_mod.WORKSPACE_ROOT)]
    assert "install_sync" in state["completed_steps"]


def test_no_sentinel_flag_records_nothing_it_did_not_do(setup_mod, monkeypatch,
                                                        tmp_path):
    """This pinned the OPPOSITE until 2026-08-30, under the name
    `test_no_sentinel_flag_still_marks_the_step_done`.

    Marking the step done turned one flagged run into a permanent opt-out: the
    next plain run read `install_sync`, printed "Scheduled tasks already
    installed", and installed nothing. The operator decided the flag is
    PER-RUN, so a declined install records nothing. The consequence - the next
    plain run really does install it - is driven in
    tests/test_a_setup_flag_that_recorded_a_step_it_had_skipped.py.
    """
    calls = []
    fake = types.ModuleType("scripts.utils.schedule")
    fake.install_sentinel_schedule = lambda slug, root: calls.append(slug)
    monkeypatch.setitem(sys.modules, "scripts.utils.schedule", fake)
    monkeypatch.setattr(setup_mod, "STATE_FILE", tmp_path / "setup-state.json")
    state = {"completed_steps": []}

    setup_mod.step_install_sync(state, {"slug": "demo"}, install_sentinel=False)

    assert calls == []
    assert "install_sync" not in state["completed_steps"]


def test_the_docstring_no_longer_names_a_module_it_never_imports(setup_mod):
    """It advertised `scripts/utils/workspace` as a lazy import. There is none."""
    doc = setup_mod.__doc__

    assert "scripts/utils/workspace" not in doc
    assert "_ensure_workspace_importable" in doc


# ============================================================
# The coverage gate that failed open
# ============================================================

def _tree(tmp_path: Path, name: str, skill_md: str | None,
          corpus: str | None = None) -> Path:
    d = tmp_path / ".claude" / "skills" / name
    d.mkdir(parents=True)
    if skill_md is not None:
        (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if corpus is not None:
        (d / "triggers.json").write_text(corpus, encoding="utf-8")
    return d


GOOD_FM = """---
name: {name}
description: a demo
metadata:
  author: A
  email: a@example.invalid
  version: "1.0"
x-heading-routing:
  category: Operations
  triggers: ["x"]
  exclusions: ["N/A"]
  compound: "No"
  router: auto
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: ["x"]
---
body
"""

GOOD_CORPUS = json.dumps(
    [{"query": f"q{i}", "should_trigger": i < 4} for i in range(6)])


def test_a_malformed_skill_md_is_not_silently_covered(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", "---\ndescription: [unclosed\n---\n", "[]")

    result = smc.check_skill(d)

    assert result["status"] == "ERROR"
    assert result["triggers_status"] == "UNKNOWN"


def test_the_corpus_is_still_measured_on_a_malformed_skill(smc, tmp_path,
                                                          monkeypatch):
    """The corpus does not depend on the frontmatter, so returning early threw
    away a measurement that was available."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    thin = json.dumps([{"query": "x", "should_trigger": True}])
    d = _tree(tmp_path, "demo", "---\ndescription: [unclosed\n---\n", thin)

    result = smc.check_skill(d)

    assert result["corpus_issues"], "the thin corpus was never opened"


def test_a_missing_skill_md_is_also_unknown_not_clean(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", None, GOOD_CORPUS)

    result = smc.check_skill(d)

    assert result["status"] == "ERROR"
    assert result["triggers_status"] == "UNKNOWN"


def test_a_directory_with_no_skill_md_still_measures_its_corpus(smc, tmp_path,
                                                                monkeypatch):
    """UNKNOWN is also the initial value, so asserting it alone proves nothing
    about this path. The corpus measurement is the observable difference."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    thin = json.dumps([{"query": "x", "should_trigger": True}])
    d = _tree(tmp_path, "demo", None, thin)

    result = smc.check_skill(d)

    assert result["corpus_issues"], "the corpus beside the missing SKILL.md was never read"
    assert result["has_valid_corpus"] is False


def test_a_thin_corpus_beside_a_missing_skill_md_fails_the_gate(smc, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    _tree(tmp_path, "demo", None,
          json.dumps([{"query": "x", "should_trigger": True}]))
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py"])

    assert smc.main() == 1


def test_the_unconditional_gate_actually_fails(smc, tmp_path, monkeypatch,
                                               capsys):
    """The whole point: flagless, exactly as CI runs it."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    _tree(tmp_path, "demo", "---\ndescription: [unclosed\n---\n",
          json.dumps([{"query": "x", "should_trigger": True}]))
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py"])

    assert smc.main() == 1


def test_the_gate_fails_even_when_the_corpus_is_perfectly_fine(smc, tmp_path,
                                                              monkeypatch):
    """A good corpus does not redeem a SKILL.md nobody could read: routability
    is what was never established."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    _tree(tmp_path, "demo", "---\ndescription: [unclosed\n---\n", GOOD_CORPUS)
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py"])

    assert smc.main() == 1


def test_the_tally_accounts_for_every_skill_it_walked(smc, tmp_path, monkeypatch,
                                                      capsys):
    """It printed COVERED 0 GRANDFATHERED 0 EXEMPT 0 MISSING 0 for a one-skill
    tree - four zeros summing to nothing."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    _tree(tmp_path, "demo", "---\ndescription: [unclosed\n---\n", GOOD_CORPUS)
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py"])

    smc.main()

    out = capsys.readouterr().out
    assert "UNKNOWN:" in out
    named = [ln for ln in out.splitlines() if "UNKNOWN" in ln and "demo" in ln]
    assert named, "the tally counted it but never named it"


def test_a_well_formed_covered_skill_still_passes(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", GOOD_FM.format(name="demo"), GOOD_CORPUS)

    result = smc.check_skill(d)

    assert result["triggers_status"] == "COVERED"
    assert result["status"] in ("PASS", "WARN")


def test_a_manual_router_skill_is_still_exempt(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo",
              GOOD_FM.format(name="demo").replace("router: auto", "router: manual"))

    result = smc.check_skill(d)

    assert result["triggers_status"] == "EXEMPT"


def test_an_auto_skill_with_no_corpus_is_still_missing(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", GOOD_FM.format(name="demo"))

    result = smc.check_skill(d)

    assert result["triggers_status"] == "MISSING"


def test_a_baselined_skill_is_still_grandfathered(smc, tmp_path, monkeypatch):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", GOOD_FM.format(name="demo"))

    result = smc.check_skill(d, baseline=frozenset({"demo"}))

    assert result["triggers_status"] == "GRANDFATHERED"


def test_the_shipped_skill_tree_still_passes_the_gate(smc, monkeypatch, capsys):
    """The regression that matters: 94 real skills must not start failing."""
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py", "--summary"])

    assert smc.main() == 0


# ============================================================
# The line that was not counted
# ============================================================

@pytest.mark.parametrize("body,expected", [
    ("\n".join(f"line {i}" for i in range(1, 502)), 501),
    ("\n".join(f"line {i}" for i in range(1, 502)) + "\n", 501),
    ("one line", 1),
    ("one line\n", 1),
    ("", 0),
    ("a\nb\nc", 3),
    ("a\nb\nc\n", 3),
])
def test_lines_are_counted_not_terminators(smc, tmp_path, monkeypatch, body,
                                           expected):
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    d = _tree(tmp_path, "demo", body)

    assert smc.check_skill(d)["size_lines"] == expected


def test_a_501_line_skill_without_a_trailing_newline_is_hard(smc, tmp_path,
                                                             monkeypatch):
    """It was reported as 500 and cleared the cap."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    body = "\n".join(f"line {i}" for i in range(1, 502))
    d = _tree(tmp_path, "demo", body)

    assert smc.check_skill(d)["size_status"] == "HARD"


def test_a_500_line_skill_is_still_within_the_cap(smc, tmp_path, monkeypatch):
    """The boundary in the other direction. A cap that moved by one would fail
    every skill sitting exactly on it."""
    monkeypatch.setattr(smc, "get_workspace_root", lambda: tmp_path)
    body = "\n".join(f"line {i}" for i in range(1, 501))
    d = _tree(tmp_path, "demo", body)

    result = smc.check_skill(d)

    assert result["size_lines"] == 500
    assert result["size_status"] != "HARD"


# ============================================================
# The strict gate that judged nothing and said so with a zero
# ============================================================

def _stt_env(stt, monkeypatch, skills_dir: Path | None = None):
    monkeypatch.setattr(stt, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    if skills_dir is not None:
        monkeypatch.setattr(stt, "SKILLS_DIR", skills_dir)


def test_a_typoed_skill_name_is_a_setup_error(stt, monkeypatch, capsys):
    """It printed one skip line and returned 0, including under --strict."""
    _stt_env(stt, monkeypatch)

    assert stt.main(["--skill", "osnit-typo", "--strict"]) == 2


def test_a_typoed_skill_name_is_a_setup_error_without_strict_too(stt, monkeypatch):
    _stt_env(stt, monkeypatch)

    assert stt.main(["--skill", "osnit-typo"]) == 2


def test_a_real_skill_name_is_not_refused(stt, monkeypatch, tmp_path):
    """The guard must not eat the ordinary case. run_skill is stubbed, so no
    judge is called."""
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text(GOOD_CORPUS, encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)
    monkeypatch.setattr(stt, "run_skill", lambda *a: {
        "skill": "demo", "cases": 6, "passed": 6, "errored": 0,
        "results": [], "skipped": False, "skip_reason": ""})

    assert stt.main(["--skill", "demo", "--strict"]) == 0


def test_an_empty_corpus_fails_the_strict_gate(stt, monkeypatch, tmp_path):
    """Selected by --all because the FILE exists, then skipped: the soft gate
    reported a clean routing check having judged nothing."""
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text("[]", encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)

    assert stt.main(["--all", "--strict"]) == 1


def test_an_empty_corpus_is_advisory_without_strict(stt, monkeypatch, tmp_path):
    """Advisory runs stay advisory. A gate that fires without --strict would
    break every informational invocation."""
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text("[]", encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)

    assert stt.main(["--all"]) == 0


def test_the_skip_line_does_not_claim_a_missing_file(stt, monkeypatch, tmp_path,
                                                     capsys):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text("[]", encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)

    stt.main(["--all"])

    out = capsys.readouterr().out
    assert "holds no cases" in out
    assert "no triggers.json" not in out


def test_an_absent_corpus_still_says_so(stt, monkeypatch, tmp_path):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    monkeypatch.setattr(stt, "SKILLS_DIR", skills)

    r = stt.run_skill(None, "m", "rules", "demo")

    assert r["skipped"] is True
    assert r["skip_reason"] == "no triggers.json"


def test_a_present_but_empty_corpus_is_named_differently(stt, monkeypatch,
                                                          tmp_path):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(stt, "SKILLS_DIR", skills)

    r = stt.run_skill(None, "m", "rules", "demo")

    assert r["skip_reason"] == "triggers.json holds no cases"


def test_the_unmeasured_list_names_the_skipped_skill(stt, monkeypatch, tmp_path,
                                                     capsys):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text("[]", encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)

    stt.main(["--all", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["unmeasured"] == ["demo"]


def test_a_measured_pass_is_still_a_pass_under_strict(stt, monkeypatch, tmp_path):
    """The widened `unmeasured` must not swallow a healthy run."""
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text(GOOD_CORPUS, encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)
    monkeypatch.setattr(stt, "run_skill", lambda *a: {
        "skill": "demo", "cases": 6, "passed": 6, "errored": 0,
        "results": [], "skipped": False, "skip_reason": ""})

    assert stt.main(["--all", "--strict"]) == 0


def test_a_measured_breach_still_fails_under_strict(stt, monkeypatch, tmp_path):
    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "triggers.json").write_text(GOOD_CORPUS, encoding="utf-8")
    _stt_env(stt, monkeypatch, skills)
    monkeypatch.setattr(stt, "run_skill", lambda *a: {
        "skill": "demo", "cases": 6, "passed": 1, "errored": 0,
        "results": [], "skipped": False, "skip_reason": ""})

    assert stt.main(["--all", "--strict"]) == 1


def test_the_documented_exit_table_matches_the_code(stt):
    """The table promised 1 for a skill left unmeasured and 2 for a setup
    error. Both were reachable only on paper."""
    doc = stt.__doc__

    assert "skill left unmeasured" in doc
    assert "2 setup error" in doc
