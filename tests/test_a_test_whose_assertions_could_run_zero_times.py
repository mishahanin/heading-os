"""`scripts/check-test-vacuity.py` -- the gate under the campaign's own defect.

A test that discovers a corpus, loops over it, and asserts only inside the loop
passes when the corpus is empty. The loop body never runs, so no assertion is
evaluated, and pytest reports green. The 10-day campaign that ended 2026-09-02
found this shape 23 times, and one sweep found 182 loop-only assertions in a
single pass. Nothing in a code review catches it: the corpus is real, the loop
is real, the assertion is real, and the code is correct.

This file holds the gate to the same standard it enforces.

* Every rule case runs against SYNTHETIC source, so the rule is exercised rather
  than the tree. A rule that can only be tested by the tree it guards has no
  negative case.
* Every refusal case has an anchor beside it: the source that must still pass. A
  gate that flagged every loop would satisfy every positive case and break every
  honest test in the repository.
* The CLI is driven through `main()` and asserted on its EXIT CODE, not on a
  restated copy of the rule. A restated copy would pass while the real command
  still exited 0.
* The tree-level check asserts the CURRENT verdict, so the gate cannot go green
  by scanning nothing.

Measured 2026-09-02 over the tree at 835c146: 1,038 test files read, 126 sites
found, all 126 frozen in `config/test-vacuity-baseline.json`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    """Import the kebab-case CLI script, which is not an importable module name."""
    path = ROOT / "scripts" / "check-test-vacuity.py"
    spec = importlib.util.spec_from_file_location("check_test_vacuity", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_test_vacuity"] = module
    spec.loader.exec_module(module)
    return module


vac = _load_module()


def _find(source: str) -> list[str]:
    return vac.vacuous_tests("tests/test_x.py", source)


# ============================================================
# The rule fires on the shape it was written for
# ============================================================

VACUOUS = '''
import pathlib

def test_every_file_declares_a_header():
    for path in pathlib.Path("docs").rglob("*.md"):
        assert path.read_text().startswith("#")
'''


def test_the_rule_fires_on_a_loop_over_a_discovered_corpus():
    assert _find(VACUOUS) == ["tests/test_x.py::test_every_file_declares_a_header"]


def test_the_rule_fires_when_the_corpus_is_a_local_name_from_a_call():
    source = '''
import pathlib

def test_every_file_declares_a_header():
    corpus = sorted(pathlib.Path("docs").rglob("*.md"))
    for path in corpus:
        assert path.read_text().startswith("#")
'''
    assert _find(source) == ["tests/test_x.py::test_every_file_declares_a_header"]


def test_an_unresolvable_name_is_treated_as_discovered():
    """The over-reporting direction `.claude/rules/scope-claims.md` requires.

    A name this script cannot resolve might be a literal and might be a glob.
    A false flag costs one baseline line; a false pass costs a test that can
    never fail.
    """
    source = '''
from somewhere import CORPUS

def test_every_entry_is_sane():
    for item in CORPUS:
        assert item
'''
    assert _find(source) == ["tests/test_x.py::test_every_entry_is_sane"]


# ============================================================
# The anchors: what must still pass
# ============================================================

def test_a_floor_outside_the_loop_clears_the_finding():
    """The fix the campaign applied by hand. A gate that still flagged this
    would be telling people the correct code is wrong."""
    source = '''
import pathlib

def test_every_file_declares_a_header():
    corpus = sorted(pathlib.Path("docs").rglob("*.md"))
    assert len(corpus) >= 40, "measured 2026-09-02: 47 pages"
    for path in corpus:
        assert path.read_text().startswith("#")
'''
    assert _find(source) == []


def test_a_loop_over_a_literal_is_exempt():
    source = '''
def test_both_directions():
    for value in ("a", "b"):
        assert value
'''
    assert _find(source) == []


def test_a_loop_over_a_module_name_bound_to_a_literal_is_exempt():
    """Measured 2026-09-02: without resolving the name this flagged 147 sites,
    including `for page in DEAD_PAGES` in
    `tests/bridge/test_an_allowlist_that_drifted_from_the_page_it_guards.py` and
    `for anchor in _REQUIRED_ANCHORS` in
    `tests/security/test_security_constitution_exists.py`. Both names are
    module-level lists and neither test is vacuous. Resolving one binding took
    the count to 126.
    """
    source = '''
DEAD_PAGES = ["signals", "spaces"]

def test_the_dead_pages_really_have_no_renderer():
    for page in DEAD_PAGES:
        assert page not in ROUTES
'''
    assert _find(source) == []


def test_a_test_with_no_assertions_at_all_is_not_this_finding():
    """A different defect with a different fix. Reporting it here would make the
    baseline a dumping ground for two unrelated shapes."""
    source = '''
import pathlib

def test_it_runs():
    for path in pathlib.Path("docs").rglob("*.md"):
        path.read_text()
'''
    assert _find(source) == []


@pytest.mark.parametrize("wrapper", ["with open('f') as fh:", "try:"])
def test_with_and_try_do_not_guard_an_assertion(wrapper):
    """Neither gates execution on a corpus being non-empty, so an assertion
    under one is reached whatever the corpus holds."""
    tail = "\n    except OSError:\n        pass" if wrapper == "try:" else ""
    source = f'''
import pathlib

def test_something():
    {wrapper}
        assert True{tail}
    for path in pathlib.Path("docs").rglob("*.md"):
        assert path.name
'''
    assert _find(source) == []


def test_naming_ast_if_in_the_guard_tuple_changes_no_verdict():
    """An equivalent mutant, recorded so the next run does not re-chase it.

    Removing `ast.If` from the guard tuple in `unguarded_assertions` SURVIVES
    mutation, and it survives because the two paths converge: an assertion under
    an `if` is either marked guarded and not appended, or never descended into
    and not appended. Same output, both ways.

    MEASURED 2026-09-02 on three shapes -- an assert directly inside a top-level
    `if`, an assert inside an `if` inside a `with`, and a `for` nested inside a
    top-level `if` -- with the entry present and then removed. All six runs
    returned the identical finding list.

    The entry stays because it states the intent: an `if` body is conditional,
    like a loop body. But it is not observable today, and leaving an unkillable
    mutation in a harness teaches the next run to expect survivors, which is how
    a real survivor gets waved through. So it is removed from the set, and this
    test is where the reason lives.
    """
    both_ways = '''
import pathlib

def test_x():
    if True:
        assert 1
    for p in pathlib.Path(".").rglob("*"):
        assert p
'''
    assert _find(both_ways) == ["tests/test_x.py::test_x"]


def test_an_if_does_guard_an_assertion():
    """The mirror of the case above. An `if` can be false for every item, so an
    assertion that only runs under one is not a floor."""
    source = '''
import pathlib

def test_something():
    for path in pathlib.Path("docs").rglob("*.md"):
        if path.suffix == ".md":
            assert path.name
'''
    assert _find(source) == ["tests/test_x.py::test_something"]


# ============================================================
# The corpus floor: this gate must not be green over nothing
# ============================================================

def test_the_scanner_refuses_a_corpus_below_the_floor(tmp_path, monkeypatch, capsys):
    """The gate committing its own defect is the thing to prevent first.

    A renamed `tests/` directory, a broken glob, a wrong root: any of them makes
    the walk return nothing, and without this floor the command would print OK
    and exit 0 over a tree it never read.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_one.py").write_text("def test_a():\n    assert True\n")
    monkeypatch.setattr(vac, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check-test-vacuity.py", "--check"])

    assert vac.main() == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_floor_is_read_at_call_time_not_frozen_into_a_default():
    """Defect shape 6 of the campaign, applied to this file.

    `def scan(root=ROOT)` would bind the real workspace at import, and the
    monkeypatch above would silently scan the operator's live tree instead of
    the fixture. The parameter must have no default.
    """
    import inspect

    signature = inspect.signature(vac.scan)
    assert signature.parameters["root"].default is inspect.Parameter.empty


# ============================================================
# The CLI, through the real entry point, on its exit code
# ============================================================

def _tree_with(tmp_path: Path, body: str, *, count: int) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_offender.py").write_text(body, encoding="utf-8")
    for index in range(count):
        (tests / f"test_filler_{index}.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
    return tmp_path


def test_the_command_exits_one_on_a_new_site(tmp_path, monkeypatch, capsys):
    root = _tree_with(tmp_path, VACUOUS, count=vac.MIN_CORPUS)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"frozen": []}), encoding="utf-8")
    monkeypatch.setattr(vac, "ROOT", root)
    monkeypatch.setattr(vac, "BASELINE_PATH", baseline)
    monkeypatch.setattr(sys, "argv", ["check-test-vacuity.py", "--check"])

    assert vac.main() == 1
    assert "1 new vacuous test" in capsys.readouterr().out


def test_the_command_exits_zero_when_the_site_is_frozen(tmp_path, monkeypatch, capsys):
    """The anchor for the CLI. A gate that exits 1 unconditionally passes the
    test above and blocks every commit in the repository."""
    root = _tree_with(tmp_path, VACUOUS, count=vac.MIN_CORPUS)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(
        {"frozen": ["tests/test_offender.py::test_every_file_declares_a_header"]}),
        encoding="utf-8")
    monkeypatch.setattr(vac, "ROOT", root)
    monkeypatch.setattr(vac, "BASELINE_PATH", baseline)
    monkeypatch.setattr(sys, "argv", ["check-test-vacuity.py", "--check"])

    assert vac.main() == 0
    assert "OK" in capsys.readouterr().out


def test_a_malformed_baseline_refuses_rather_than_reading_as_empty(tmp_path, monkeypatch):
    """An empty baseline and an unreadable one are different states, and a gate
    that cannot tell them apart reports every frozen site as new -- or, with the
    comparison the other way round, reports a real finding as frozen."""
    root = _tree_with(tmp_path, VACUOUS, count=vac.MIN_CORPUS)
    baseline = tmp_path / "baseline.json"
    baseline.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(vac, "ROOT", root)
    monkeypatch.setattr(vac, "BASELINE_PATH", baseline)
    monkeypatch.setattr(sys, "argv", ["check-test-vacuity.py", "--check"])

    with pytest.raises(SystemExit):
        vac.main()


# ============================================================
# The baseline can only shrink
# ============================================================

def test_the_writer_never_adds_a_new_site(tmp_path, monkeypatch, capsys):
    """Otherwise re-running the writer launders a fresh vacuous test into the
    frozen set, which is how a ratchet stops being a ratchet."""
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(vac, "BASELINE_PATH", baseline)

    vac.write_baseline(frozen={"a::x"}, current=["a::x", "b::y"])
    written = json.loads(baseline.read_text(encoding="utf-8"))

    assert written["frozen"] == ["a::x"]
    assert "b::y" not in written["frozen"]


def test_the_writer_drops_a_site_that_no_longer_fires(tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(vac, "BASELINE_PATH", baseline)

    vac.write_baseline(frozen={"a::x", "gone::y"}, current=["a::x"])
    written = json.loads(baseline.read_text(encoding="utf-8"))

    assert written["frozen"] == ["a::x"]
    assert "gone" in capsys.readouterr().out


# ============================================================
# The live tree
# ============================================================

def test_the_engine_tree_carries_no_unfrozen_vacuous_test():
    """The verdict this gate exists to hold. It is asserted here as well as in
    pre-commit so a clone that never installed the hooks still measures it."""
    findings, files_read = vac.scan(ROOT)
    assert files_read >= vac.MIN_CORPUS, (
        f"read {files_read} test files, below the floor of {vac.MIN_CORPUS}; "
        f"the walk found less than the tree holds")

    frozen = json.loads(vac.BASELINE_PATH.read_text(encoding="utf-8"))["frozen"]
    new = sorted(set(findings) - set(frozen))
    assert new == [], (
        f"{len(new)} test(s) whose assertions can all run zero times: {new[:5]}")


def test_the_baseline_is_a_measurement_of_this_tree_not_a_wish_list():
    """Every frozen entry must still be a finding. A baseline carrying sites that
    no longer exist reads as a bigger debt than there is, and the next person
    trusts it less."""
    findings, _ = vac.scan(ROOT)
    frozen = json.loads(vac.BASELINE_PATH.read_text(encoding="utf-8"))["frozen"]
    stale = sorted(set(frozen) - set(findings))
    assert stale == [], f"{len(stale)} baseline entries no longer fire: {stale[:5]}"
