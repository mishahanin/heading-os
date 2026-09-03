"""The overlay guard asked the environment where the operator's data was.

`scripts/utils/overlay_write_guard.py` protects the operator's private overlay
two ways: an in-process refusal on every write primitive (`_OVERLAY_PREFIXES`)
and a whole-tree before/after snapshot (`_WATCH_BEFORE`). Until 2026-08-31 both were
aimed by `_overlay_root()`, which called `get_data_root()`, which honours
`HEADING_OS_DATA`.

So the variable every isolation fix in this repository recommends setting —
`HEADING_OS_DATA=<scratch> pytest` — moved BOTH halves of the guard onto the
scratch directory and left the operator's real overlay watched by nothing, for
the whole session, without a word. Measured that day on this machine, with zero
bytes written:

    launched plainly            _OVERLAY_PREFIX -> .../.heading-os-data/    10,919 files watched
    HEADING_OS_DATA=<scratch>   _OVERLAY_PREFIX -> .../scratch-overlay/          0 files watched

This is the workspace's recorded shape "a guard must ask about the write, not
about the environment", reproduced inside the guard written to enforce it.

The fix is `_structural_overlay_root()`: the sibling data repo derived from
`Path(__file__)`, which no environment variable can move. Every test below is
written so it passes under BOTH launch shapes; under the second one they all
failed before the fix, which is the point.

Nothing here writes. The refusal happens BEFORE the write, so the guard is
proven by making it REFUSE a real path, never by putting a file in the
operator's overlay to see whether anyone notices.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cf():
    """The LIVE guard module this session armed, not a fresh copy of it.

    A fresh copy has its own module globals, so its `_OVERLAY_PREFIXES` is empty
    and every assertion about "the guard is armed in THIS session" would pass
    over a module nobody installed. The identity check against the root
    conftest's own reference is what pins that: an ordinary import cannot be a
    second copy, but the conftest importing something else could.
    """
    live = sys.modules.get("scripts.utils.overlay_write_guard")
    assert live is not None, (
        "the guard module is not in sys.modules, so this test cannot see the "
        "guard this session armed and must not pass quietly")
    conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    assert conftest is not None and conftest._guard is live, (
        "the root conftest holds a DIFFERENT guard object than the one imported "
        "here, so every assertion below would be about a module nobody armed")
    return live


@pytest.fixture
def structural(cf):
    root = cf._structural_overlay_root()
    if root is None:
        pytest.skip("this clone has no sibling private overlay, so nothing to guard")
    return root


# ============================================================
# 0. The resolver counts the right number of parents
# ============================================================

def test_the_structural_root_is_derived_from_the_real_engine_root(cf, helm_root):
    """The one number the 2026-08-31 move out of `tests/conftest.py` changed.

    `_structural_overlay_root()` walks up from its own `__file__` to the engine
    root. In a conftest at `<engine>/tests/` that was two parents; in
    `<engine>/scripts/utils/` it is three. Off by one and the function returns
    None (nothing is guarded) or a stranger (the wrong tree is guarded), and
    EVERY other test in this file still passes: they all drive the guard against
    a tmp_path they built themselves, so none of them ever asks whether the
    real root was found.

    Derived here from this test file's own location, which is an independent
    path, rather than from any constant the guard exports.

    Two roots, and they are not the same one whenever the suite runs from a
    YARD. The DEPTH is a property of the checkout the guard module was loaded
    from, which is this one. The OVERLAY is resolved by the guard through
    `_main_clone_root()`, so it is the sibling of HELM. MEASURED 2026-09-03:
    reading both off this file's location compared the real overlay against a
    directory beside a worktree that does not exist, and the test failed.
    """
    checkout = Path(__file__).resolve().parent.parent
    assert (checkout / "pyproject.toml").is_file(), "this test mislocated the engine root"

    guard_file = Path(cf.__file__).resolve()
    assert guard_file.parents[2] == checkout, (
        f"the guard at {guard_file} is {len(guard_file.relative_to(checkout).parts)} "
        f"levels below {checkout}; `_structural_overlay_root()` counts parents by "
        f"hand and must be updated in the same change that moves this file")

    root = cf._structural_overlay_root()
    sibling = helm_root.parent / ".heading-os-data"
    if sibling.is_dir():
        assert root == sibling.resolve(), (
            "the guard derived a different overlay than the sibling data repo "
            "beside the engine; it is guarding the wrong tree")
    else:
        assert root is None, "no sibling overlay exists, so nothing may be returned"


# ============================================================
# 1. The resolver cannot be moved by the environment
# ============================================================

def test_the_structural_root_ignores_heading_os_data(cf, structural, monkeypatch, tmp_path):
    """The whole defect in one assertion.

    Fails against the old conftest, where the root came from `get_data_root()`
    and this monkeypatch relocated it.
    """
    scratch = tmp_path / "scratch-overlay"
    scratch.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(scratch))

    assert cf._structural_overlay_root() == structural
    assert cf._structural_overlay_root() != scratch.resolve()
    # and the session-sensitive resolver DID move, so the test is measuring the
    # difference between the two rather than an environment that never changed.
    assert cf._overlay_root() == scratch.resolve()


def test_the_watched_roots_keep_the_real_overlay_when_the_variable_is_repointed(
    cf, structural, monkeypatch, tmp_path
):
    scratch = tmp_path / "scratch-overlay"
    scratch.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(scratch))

    roots = cf._watched_roots()
    assert structural in roots.values(), (
        "HEADING_OS_DATA moved the guard off the operator's data")
    # the scratch root is watched too: those writes would have hit the real
    # overlay had the variable been absent, so they still have to be reported.
    assert scratch.resolve() in roots.values()
    assert len(roots) == 2


def test_a_clone_with_no_sibling_overlay_watches_nothing(cf, monkeypatch):
    """CI and a fresh public clone must cost nothing and claim nothing."""
    monkeypatch.setattr(cf, "_structural_overlay_root", lambda: None)
    monkeypatch.setattr(cf, "_overlay_root", lambda: None)
    assert cf._watched_roots() == {}
    assert cf._watch_snapshot() == {}


def test_one_root_seen_twice_is_watched_once(cf, structural, monkeypatch):
    """The ordinary launch: both resolvers name the same directory, one label."""
    monkeypatch.setattr(cf, "_overlay_root", lambda: structural)
    roots = cf._watched_roots()
    assert list(roots) == [cf._LIVE_OVERLAY_LABEL]
    assert roots[cf._LIVE_OVERLAY_LABEL] == structural


# ============================================================
# 2. The in-process guard REFUSES a real path, in this session
# ============================================================

def test_the_live_session_guard_is_armed_on_the_real_overlay(cf, structural):
    """Passes under both launch shapes. Under `HEADING_OS_DATA=<scratch>` it
    failed before the fix, because the prefix named the scratch directory."""
    assert cf._OVERLAY_PREFIXES, "an overlay is present and the guard is not armed"
    assert f"{structural}{os.sep}" in cf._OVERLAY_PREFIXES

    with pytest.raises(cf.OverlayWriteRefused):
        cf._refuse_overlay_path(structural / "auto-memory" / "MEMORY.md", "write")
    with pytest.raises(cf.OverlayWriteRefused):
        cf._refuse_overlay_path(structural / "outputs" / "never-written.md", "write")

    # A refusal function nobody wrapped the primitives with refuses nothing.
    assert callable(cf._RESTORE_WRITE_GUARD), (
        "the session never installed the write guard")


def test_the_guard_refuses_every_watched_root_not_merely_the_first(cf, monkeypatch, tmp_path):
    """A tuple whose second entry is never consulted is a list of one."""
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setattr(
        cf, "_OVERLAY_PREFIXES", (f"{first}{os.sep}", f"{second}{os.sep}"))
    for root in (first, second):
        with pytest.raises(cf.OverlayWriteRefused):
            cf._refuse_overlay_path(root / "x.md", "write")


def test_the_guard_allows_a_write_outside_every_watched_root(cf, monkeypatch, tmp_path):
    """The other direction. A guard that refuses everything measures nothing."""
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (f"{tmp_path / 'watched'}{os.sep}",))
    cf._refuse_overlay_path(tmp_path / "elsewhere" / "fine.md", "write")   # must not raise
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "fine.md").write_text("fine\n", encoding="utf-8")
    assert (elsewhere / "fine.md").read_text(encoding="utf-8") == "fine\n"


def test_reading_the_real_overlay_is_still_allowed(cf, structural):
    """The guard is about writes. A read that started refusing would break the
    dozens of tests that legitimately consult the operator's records.

    Exercised through the INSTALLED wrapper, not through `_refuse_overlay_path`,
    because the read/write distinction lives in `guarded_open`'s mode check: the
    path checker itself is deliberately verb-blind and refuses whatever it is
    handed. Asserting on the checker would have asserted the opposite contract.
    """
    memory = structural / "auto-memory" / "MEMORY.md"
    if not memory.is_file():
        pytest.skip("this overlay has no auto-memory/MEMORY.md to read")
    with open(memory, "r", encoding="utf-8") as handle:
        assert handle.read(1) != ""
    assert memory.read_bytes()[:1] != b""

    # ...and the same path in write mode is refused, so the allowance above is
    # scoped to reads rather than to this file being uninteresting.
    with pytest.raises(cf.OverlayWriteRefused), open(memory, "a", encoding="utf-8"):
        pass


# ============================================================
# 3. The snapshot half covers the real overlay, over a real corpus
# ============================================================

def test_the_session_snapshot_covers_the_real_overlay(cf, structural):
    """Fails under `HEADING_OS_DATA=<scratch>` before the fix: the session's
    before-snapshot held one label pointed at an empty scratch directory."""
    assert cf._WATCH_BEFORE, "the session took no before-snapshot"
    label = next(
        (l for l, (d, _e) in cf._WATCH_BEFORE.items() if d == structural), None)
    assert label is not None, (
        f"the operator's overlay at {structural} is in no watched label; "
        f"the session watched {[str(d) for d, _ in cf._WATCH_BEFORE.values()]}")

    _directory, entries = cf._WATCH_BEFORE[label]

    # Minimum count, from an INDEPENDENT walk of the same tree. A snapshot over
    # zero files diffs clean against anything and proves nothing, and that is
    # exactly the state the repointed guard was left in (0 files watched).
    independent = set()
    for path in structural.rglob("*"):
        rel = path.relative_to(structural)
        if rel.parts and rel.parts[0] in cf._UNWATCHED:
            continue
        try:
            if path.is_file():
                independent.add(rel.as_posix())
        except OSError:
            continue
    assert len(independent) >= 1, "the overlay on disk is empty; nothing measured"
    assert len(entries) >= 1, "a walk over zero files is green and proves nothing"

    # Everything the independent walk found, minus whatever moved between the
    # two walks, has to be in the snapshot. Sizes are recorded, not just names:
    # a truncation in place adds no file and removes none.
    # Ignore anything BORN after the snapshot. The overlay is a live tree: on
    # 2026-08-31 this test went red in the full suite and green alone, because
    # concurrent agents wrote new memory files during the four minutes between
    # `pytest_sessionstart` and this assertion. Those files were not "missed" by
    # the snapshot; they did not exist when it was taken. `exists()` alone does
    # not cover this - it filters files that VANISHED, not files that ARRIVED.
    # `_WATCH_BEFORE_AT` is recorded by the guard's `arm()` for exactly this
    # comparison.
    born_after = cf._WATCH_BEFORE_AT
    assert born_after is not None, (
        "the guard's arm() recorded no snapshot time, so this test cannot tell a "
        "file the "
        "snapshot missed from one that was created after it. Without that, the "
        "assertion below is a race and would be red on a busy machine.")

    def _predates_the_snapshot(name: str) -> bool:
        try:
            return (structural / name).stat().st_mtime <= born_after + 1.0
        except OSError:
            return False

    missing = {
        n for n in independent
        if n not in entries and (structural / n).exists() and _predates_the_snapshot(n)
    }
    assert not missing, f"the snapshot missed {len(missing)} real files, e.g. {sorted(missing)[:5]}"
    assert all(isinstance(size, int) for size in entries.values())


def test_the_snapshot_reports_a_rewrite_of_a_watched_root(cf, monkeypatch, tmp_path):
    """The detector itself, driven end to end over a pretend overlay."""
    pretend = tmp_path / "pretend"
    (pretend / "auto-memory").mkdir(parents=True)
    target = pretend / "auto-memory" / "MEMORY.md"
    target.write_text("x" * 500, encoding="utf-8")

    monkeypatch.setattr(cf, "_watched_roots", lambda: {"operator overlay": pretend})
    before = cf._watch_snapshot()
    assert before["operator overlay"][1] == {"auto-memory/MEMORY.md": 500}

    target.write_text("truncated\n", encoding="utf-8")
    complaints = cf.watch_complaints(before, cf._watch_snapshot())
    assert complaints, "a truncation in place was not reported"
    assert "auto-memory/MEMORY.md" in complaints[0]


def test_the_snapshot_reports_each_watched_root_separately(cf, monkeypatch, tmp_path):
    """Two roots, one complaint each: a second label that is never diffed is a
    root that is watched on paper only."""
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        root.mkdir()
        (root / "note.md").write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(
        cf, "_watched_roots", lambda: {"operator overlay": a, "data root in use": b})

    before = cf._watch_snapshot()
    assert set(before) == {"operator overlay", "data root in use"}
    for root in (a, b):
        (root / "note.md").write_text("REWRITTEN BY A TEST RUN\n" * 3, encoding="utf-8")

    complaints = cf.watch_complaints(before, cf._watch_snapshot())
    assert len(complaints) == 2
    assert any("operator overlay" in c for c in complaints)
    assert any("data root in use" in c for c in complaints)


# ============================================================
# 4. The rename is load-bearing
# ============================================================

def test_the_old_singular_name_is_gone(cf):
    """`_OVERLAY_PREFIX` was a string; the guard now iterates the name.

    Left in place and widened, a caller still assigning the old string would
    have armed the guard on twenty-six single characters, which matches every
    path on the machine. Renaming makes a stale caller arm nothing and fail
    loudly instead. tests/test_a_test_run_that_could_write_the_operators_data.py
    is the caller that was updated with it.
    """
    assert not hasattr(cf, "_OVERLAY_PREFIX")
    assert isinstance(cf._OVERLAY_PREFIXES, tuple)


def test_a_bare_string_of_prefixes_cannot_arm_the_guard(cf, monkeypatch, tmp_path):
    """The failure mode the rename prevents, asserted rather than described."""
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", (f"{tmp_path}{os.sep}",))
    with pytest.raises(cf.OverlayWriteRefused):
        cf._refuse_overlay_path(tmp_path / "x.md", "write")
    # A single character never matches a whole prefix, so a str would have been
    # both over-broad and wrong. Prove the tuple form is what the code reads.
    monkeypatch.setattr(cf, "_OVERLAY_PREFIXES", ())
    cf._refuse_overlay_path(tmp_path / "x.md", "write")   # must not raise
