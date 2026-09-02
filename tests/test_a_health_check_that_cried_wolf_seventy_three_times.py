"""Reference Validation reported 73 action items and almost none was one.

MEASURED 2026-09-02, before the fix: `scripts/workspace-health.py` printed 140
OK lines, 5 WARN lines and 73 lines reading `ACTION  Missing: <path>`, exited 1,
and had done so for long enough that `.claude/rules/documentation.md` still
says the check runs before `/push-updates` while everyone stepped over it. A
health check nobody can act on is a check nobody reads, and this one was hiding
whatever real drift sat inside the 73.

Four different things were being called the same word:

  - a path written from the directory that HOLDS the two repo roots, so neither
    root resolved it even though the file is on disk;
  - a runtime artefact whose absence is the normal state (`.fireside/daemon.pid`);
  - a stand-in inside a sentence about paths (`scripts/x.py`), naming nothing;
  - a genuinely stale reference: a plan that moved into `plans/archive/<year>/`,
    or a script that was deleted and is still described as live.

Only the fourth is an action item. These tests hold one class each, plus the
two properties that keep the separation honest: the exclusion rules may not
widen into the findings, and a section that inspected nothing refuses instead of
reporting clean.

The load-bearing one is `test_a_path_ignored_by_only_one_root_is_still_a_finding`.
The engine ignores whole DATA directories (`plans/`, `outputs/`, `threads/`)
because they belong to the overlay, not because anything about them is runtime,
so deriving "runtime" from ANY root's ignore rules swallows the findings instead
of the noise. MEASURED over the live index the same day: union 28 paths,
intersection 3, and 25 of the 28 were the archived-plan and stale-thread
references this section exists to report.

Every test builds its own scratch workspace under `tmp_path`. Two read the
operator's real overlay, assert only shape, and skip when no overlay is present:
`test_every_registry_entry_is_still_named_by_the_live_index` and
`test_the_live_section_states_what_it_inspected_beside_what_it_flagged`.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "workspace-health.py"

# The tests that read the operator's real overlay, named so the docstring above
# cannot quietly stop being true.
LIVE_TREE_TESTS = (
    "test_every_registry_entry_is_still_named_by_the_live_index",
    "test_the_live_section_states_what_it_inspected_beside_what_it_flagged",
)


@pytest.fixture
def wh():
    """A fresh module object per test: it binds directories at import time."""
    spec = importlib.util.spec_from_file_location(
        "workspace_health_criedwolf_under_test", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["workspace_health_criedwolf_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _plain(captured: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", captured)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def roots(wh, tmp_path, monkeypatch):
    """Two scratch roots, each a real git repository.

    Real repositories, not fakes: the runtime-artefact rule asks `git
    check-ignore`, and a stub that answered from a list would test the list
    rather than the rule. Returns (engine, data, write_index, ignore), where
    `ignore` appends lines to one root's `.gitignore`.
    """
    if shutil.which("git") is None:                     # pragma: no cover
        pytest.skip("git is not installed")
    engine, data = tmp_path / ".engine", tmp_path / ".engine-data"
    (data / "reference").mkdir(parents=True)
    engine.mkdir()
    for root in (engine, data):
        _git(root, "init", "-q")
    monkeypatch.setattr(wh, "WORKSPACE", engine)
    monkeypatch.setattr(wh, "get_data_root", lambda: data)

    def write_index(body: str) -> None:
        (data / "reference" / "workspace-overview.md").write_text(
            body, encoding="utf-8")

    def ignore(root: Path, *lines: str) -> None:
        with (root / ".gitignore").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    return engine, data, write_index, ignore


def _anchor(engine: Path) -> str:
    """One resolving path, so a fixture never trips the zero-inspected floor
    for a reason the test under it is not about."""
    (engine / "scripts").mkdir(exist_ok=True)
    (engine / "scripts" / "anchor.py").write_text("x", encoding="utf-8")
    return "`scripts/anchor.py`"


# ============================================================
# Class 1 - a path one of the roots really does hold
# ============================================================

def test_a_path_written_from_above_the_roots_resolves(wh, roots, capsys):
    """The index distinguishes the two stores by naming each repo directory.

    `<data-dir>/.memory-index/index.db` beside `<engine-dir>/.memory-index-code/
    index.db` is the shape, and neither is relative to either root, so both read
    as missing while both files sit on disk. The leading component is matched
    against each root's OWN name, so nothing here depends on what the clones are
    called.
    """
    engine, data, write_index, _ignore = roots
    (engine / "store").mkdir()
    (engine / "store" / "code.db").write_text("x", encoding="utf-8")
    (data / "store").mkdir()
    (data / "store" / "content.db").write_text("x", encoding="utf-8")
    write_index(
        f"- code in `{engine.name}/store/code.db`, "
        f"content in `{data.name}/store/content.db` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert "Missing" not in out, out
    assert "3 of 3 reference path(s) resolve" in out


def test_a_leading_component_that_is_not_a_root_name_is_not_stripped(
        wh, roots, capsys):
    """The negative control for the rule above.

    Stripping any first component would resolve `scripts/gone.py` through
    `gone.py` at a root and report a deleted script as present. Only a component
    that spells a root's directory name is removed.
    """
    engine, _data, write_index, _ignore = roots
    (engine / "gone.py").write_text("x", encoding="utf-8")
    write_index(f"- `scripts/gone.py` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert "Missing: scripts/gone.py" in out


def test_with_no_overlay_the_paths_are_not_checkable_never_missing(
        wh, tmp_path, monkeypatch, capsys):
    """A public engine clone carries no data overlay and therefore no index.

    It must say so in the words that describe the evidence it has. "Missing"
    would assert that the files are gone; nothing on this clone establishes
    that.
    """
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path / "engine")
    monkeypatch.setattr(wh, "get_data_root", lambda: tmp_path / "absent")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0
    assert "not checkable on this clone" in out
    assert "Missing" not in out, out


# ============================================================
# Class 2 - a runtime artefact, absent as its normal state
# ============================================================

def test_a_path_no_root_would_ever_track_is_not_an_action_item(wh, roots,
                                                               capsys):
    """`.fireside/daemon.pid` exists while a daemon runs and not otherwise.

    Derived, not listed: if every root's ignore rules exclude a path, nothing in
    this workspace ever tracks it, so its absence is not a documentation defect.
    """
    engine, data, write_index, ignore = roots
    for root in (engine, data):
        ignore(root, ".fireside/")
    write_index(f"- pid at `.fireside/daemon.pid` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert "Missing" not in out, out
    assert "absent by design, not checked: .fireside/daemon.pid" in out
    assert "1 absent by design" in out


def test_a_path_ignored_by_only_one_root_is_still_a_finding(wh, roots, capsys):
    """The rule is EVERY root, and this is why.

    The engine ignores `plans/` because plans are overlay data, not because a
    plan is a runtime file. A union rule reads that single exclusion as "runtime"
    and deletes the finding. MEASURED over the live index on 2026-09-02: union
    28 paths, intersection 3, and 25 of the 28 were real stale references.
    """
    engine, _data, write_index, ignore = roots
    ignore(engine, "plans/")
    write_index(f"- design in `plans/2026-01-01-a-plan.md` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert "Missing: plans/2026-01-01-a-plan.md" in out
    assert "0 absent by design" in out


def test_a_listed_absent_by_design_path_is_excluded_with_its_reason(
        wh, roots, capsys):
    """The small list for what git cannot derive, and every entry carries why.

    `outputs/operations/handoff.md` is written only when a session hands off and
    is moved to the archive directory on the next start. The data repo tracks
    `outputs/`, so no ignore rule can say this.
    """
    engine, _data, write_index, _ignore = roots
    entry = "outputs/operations/handoff.md"
    assert entry in wh.ABSENT_BY_DESIGN_PATHS
    write_index(f"- handoff at `{entry}` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert f"absent by design, not checked: {entry}" in out
    assert wh.ABSENT_BY_DESIGN_PATHS[entry] in out, (
        "the reason must be printed, not held")


# ============================================================
# Class 2b - a path the catalogue names BECAUSE it was deleted
# ============================================================

def test_a_path_named_in_a_removal_record_is_excluded_with_its_reason(
        wh, roots, capsys):
    """A catalogue that silently drops a retired subsystem is how someone
    re-creates it, so a removal gets a bullet naming what went and in which
    commit. Every one of those bullets then read here as `Missing:`.

    MEASURED 2026-09-02: of 27 flagged paths, 24 were removal records, each a
    red ACTION line the operator can do nothing about. A check whose findings
    are mostly unactionable gets skimmed, and the three real ones were in the
    same list.
    """
    engine, _data, write_index, _ignore = roots
    entry = "scripts/slice-rollback.py"
    assert entry in wh.REMOVED_AND_RECORDED_PATHS
    write_index(f"- rollback CLI, deleted: `{entry}` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert f"removed and recorded, not checked: {entry}" in out
    assert wh.REMOVED_AND_RECORDED_PATHS[entry] in out, (
        "the reason must be printed, not held")


def test_a_recorded_removal_that_exists_again_is_a_finding(wh, roots, capsys):
    """The floor. This registry may not outlive its sites.

    A path listed as removed that is back on disk is either a re-creation
    nobody updated the record for, or an entry added to silence a finding that
    was real. Both are worse than the noise the registry removes. Without this
    test the registry is an unbounded mute button: anything added to it stops
    being checked, forever, whatever the tree does afterwards.
    """
    engine, _data, write_index, _ignore = roots
    entry = "scripts/slice-rollback.py"
    revived = engine / entry
    revived.parent.mkdir(parents=True, exist_ok=True)
    revived.write_text("# somebody built it again\n", encoding="utf-8")
    write_index(f"- rollback CLI, deleted: `{entry}` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues > 0, (
        "a path recorded as removed is present again and the check said "
        f"nothing:\n{out}")
    assert "present again" in out
    assert entry in out


# ============================================================
# Class 3 - a stand-in inside a sentence about paths
# ============================================================

def test_a_prose_placeholder_is_excluded_with_its_reason(wh, roots, capsys):
    """`scripts/x.py` stands in for any root-relative script, in the shell-drift
    guard's own description. It names no file and never has."""
    engine, _data, write_index, _ignore = roots
    write_index(f"- a command running `scripts/x.py` fails {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert "placeholder, not checked: scripts/x.py" in out
    assert wh.PLACEHOLDER_PATHS["scripts/x.py"] in out
    assert "1 prose placeholder(s)" in out


@pytest.mark.parametrize("neighbour", [
    "scripts/xx.py",            # one character more
    "scripts/x.pyc",            # one character more, other end
    "utils/scripts/x.py",       # the registry token as a suffix
    "scripts/x-ray.py",         # shares the stem's first character
])
def test_the_placeholder_rule_cannot_widen_to_a_real_path(wh, roots, capsys,
                                                          neighbour):
    """The exclusion is an exact token and must stay one.

    "ignore anything that looks like an example" is the rule this section was
    told not to grow, because it is the rule that makes a real deletion
    invisible. A neighbour of a registry entry is reported like any other path.
    """
    engine, _data, write_index, _ignore = roots
    write_index(f"- see `{neighbour}` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert f"Missing: {neighbour}" in out


def test_no_registry_entry_carries_a_pattern_character(wh):
    """A glob or a regex metacharacter in either registry would be a shape.

    Both registries are consulted by exact membership, so a `*` would simply
    never match; this fails the entry at review time instead, where the
    intention behind it can still be corrected.
    """
    for name, registry in (("PLACEHOLDER_PATHS", wh.PLACEHOLDER_PATHS),
                           ("ABSENT_BY_DESIGN_PATHS",
                            wh.ABSENT_BY_DESIGN_PATHS)):
        assert registry, f"{name} is empty; the rule it encodes was deleted"
        for token, reason in registry.items():
            assert not set(token) & set("*?[]{}()|^$\\"), f"{name}: {token}"
            assert len(reason.split()) >= 6, (
                f"{name}: {token} carries a fragment, not a reason")


# ============================================================
# Class 4 - the findings this section exists to produce
# ============================================================

def test_a_deleted_script_still_described_as_live_is_reported(wh, roots, capsys):
    """The whole point. Everything above must not reach this one.

    The finding is an INVENTED name, deliberately. It used to be
    `scripts/workspace-sync.py`, a real retirement, and on 2026-09-02 that path
    entered `REMOVED_AND_RECORDED_PATHS`, so this test's one finding became an
    exclusion and the test measured nothing. A fixture built on a real path is a
    fixture a later registry entry can silence; a name nothing will ever add
    cannot be.
    """
    engine, _data, write_index, ignore = roots
    unknown = "scripts/nonexistent-widget.py"
    assert unknown not in wh.REMOVED_AND_RECORDED_PATHS
    assert unknown not in wh.ABSENT_BY_DESIGN_PATHS
    assert unknown not in wh.PLACEHOLDER_PATHS
    for root in roots[:2]:
        ignore(root, ".fireside/")
    write_index(
        "- runtime `.fireside/daemon.pid`, stand-in `scripts/x.py`, "
        f"transient `outputs/operations/handoff.md`, {_anchor(engine)}\n"
        f"- and the still-described `{unknown}`\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert f"Missing: {unknown}" in out
    assert out.count("Missing:") == 1, out


def test_an_archived_plan_is_reported_with_where_it_went(wh, roots, capsys):
    """Archiving is the normal end of every plan
    (`.claude/rules/documentation.md` § Plans Lifecycle), so the index's pointer
    at an active plan breaks on the day the work finishes. The file still
    exists, so "Missing" is the wrong word and the destination is the fix."""
    engine, data, write_index, _ignore = roots
    archived = data / "plans" / "archive" / "2026"
    archived.mkdir(parents=True)
    (archived / "2026-06-03-now-phase-spine.md").write_text("x", encoding="utf-8")
    write_index(
        f"- built from `plans/2026-06-03-now-phase-spine.md` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert ("Moved: plans/2026-06-03-now-phase-spine.md is now "
            "plans/archive/2026/2026-06-03-now-phase-spine.md") in out
    assert "Missing" not in out, "the file exists; only the pointer is stale"
    assert "1 moved, 0 missing" in out


def test_the_archive_lookup_answers_only_for_plans(wh, roots, capsys):
    """The archive is the plans directory's convention and nothing else's.

    A lookup keyed on the basename alone would report a deleted script as
    "Moved" into `plans/archive/` the moment some archived plan happened to
    share its filename, which is a wrong destination printed with the same
    confidence as a right one. Found by mutation: deleting the `plans/` guard
    from `_archived_plan` left every test in this file green.
    """
    engine, data, write_index, _ignore = roots
    archived = data / "plans" / "archive" / "2026"
    archived.mkdir(parents=True)
    (archived / "collision.md").write_text("x", encoding="utf-8")
    write_index(f"- see `docs/collision.md` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert "Missing: docs/collision.md" in out
    assert "Moved" not in out, out


def test_a_plan_that_is_in_no_archive_is_reported_missing(wh, roots, capsys):
    """The negative control for the archive lookup: it may resolve a real
    destination, never invent one."""
    engine, data, write_index, _ignore = roots
    (data / "plans" / "archive" / "2026").mkdir(parents=True)
    write_index(f"- see `plans/2026-06-03-never-existed.md` {_anchor(engine)}\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1, out
    assert "Missing: plans/2026-06-03-never-existed.md" in out
    assert "Moved" not in out, out


# ============================================================
# The floor, and the sentence that makes it visible
# ============================================================

def test_an_index_of_nothing_but_exclusions_refuses(wh, roots, capsys):
    """Zero inspected is the shape of a checker that stopped reading.

    Excluding a path is not inspecting it, so a run whose every path landed in
    an exclusion bucket has settled nothing and must refuse, the floor
    `scripts/ste-check.py` and `scripts/validate-crm-schema.py` already carry
    over their own corpora.
    """
    engine, data, write_index, ignore = roots
    for root in (engine, data):
        ignore(root, ".fireside/")
    write_index("- `scripts/x.py` and `.fireside/daemon.pid` and nothing else\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues >= 1, out
    assert "refuses to report clean" in out
    assert "0 inspected" in out


def test_the_section_states_what_it_inspected_beside_what_it_flagged(
        wh, roots, capsys):
    """`.claude/rules/scope-claims.md`: a tool says only what its method
    established. A flag count alone cannot distinguish a clean corpus from a
    corpus that was never read, so the two numbers are printed together."""
    engine, _data, write_index, ignore = roots
    for root in roots[:2]:
        ignore(root, ".fireside/")
    (engine / "scripts").mkdir()
    (engine / "scripts" / "here.py").write_text("x", encoding="utf-8")
    write_index(
        "- `scripts/here.py`, `scripts/gone.py`, `scripts/x.py`, "
        "`.fireside/daemon.pid`\n")

    wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert ("1 of 4 reference path(s) resolve; 2 inspected, 1 flagged "
            "(0 moved, 1 missing, 0 recorded-but-present); excluded: "
            "1 absent by design, 0 removed and recorded, "
            "1 prose placeholder(s)") in out, out


# ============================================================
# The two live-tree readers
# ============================================================

def test_every_registry_entry_is_still_named_by_the_live_index(wh) -> None:
    """A registry entry is cover for a reference the index actually writes.

    One nobody writes any more guards nothing, and sits there ready to hide the
    next real path that collides with it. Policed here rather than inside the
    check, so a scratch fixture is not judged against the operator's index.
    """
    index = wh.get_data_root() / wh.REFERENCE_INDEX_RELPATH
    if not index.exists():
        pytest.skip("no data overlay on this clone")
    named, _skipped = wh._reference_index_paths(index.read_text(encoding="utf-8"))

    for kind, registry in (("placeholder", wh.PLACEHOLDER_PATHS),
                           ("absent-by-design", wh.ABSENT_BY_DESIGN_PATHS),
                           ("removed-and-recorded",
                            wh.REMOVED_AND_RECORDED_PATHS)):
        for token in registry:
            assert token in named, (
                f"{kind} registry entry {token!r} is no longer named by the "
                f"reference index; drop it")


def test_the_live_section_states_what_it_inspected_beside_what_it_flagged(
        wh, capsys) -> None:
    """Shape only, never content: the operator's index changes daily."""
    index = wh.get_data_root() / wh.REFERENCE_INDEX_RELPATH
    if not index.exists():
        pytest.skip("no data overlay on this clone")

    wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    counted = re.search(r"(\d+) inspected, (\d+) flagged", out)
    assert counted, out
    inspected, flagged = (int(g) for g in counted.groups())
    assert inspected > 0, "a live index the section inspected nothing of"
    assert flagged <= inspected


def test_the_module_docstring_names_the_live_tree_readers_that_exist() -> None:
    """The docstring's exception list, held to the file it describes."""
    defined = {name for name in globals() if name.startswith("test_")}
    doc = __doc__ or ""
    for name in LIVE_TREE_TESTS:
        assert name in defined, f"{name} was renamed; the docstring cites it"
        assert name in doc, f"{name} reads the live tree and the docstring omits it"
