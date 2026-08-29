"""Every tree a page reads must bump that page's version.

Found by the 2026-08-23 audit. `watcher.PATH_TO_COMPONENT` mapped a path prefix
to ONE component, and four trees the rest of the daemon already declares had no
mapping at all:

- `outputs/documents/` and `outputs/content/tribe/` are both listed in
  `sources/pulse.IN_FLIGHT_DIRS` and `sources/studio.IN_FLIGHT_DIRS`. Writing a
  document never bumped the in-flight count, so the number sat stale until a
  manual refresh.
- `threads` is in `state.COMPONENTS` under a comment claiming it "gained
  Watchdog/refresh coverage". Nothing mapped to it, and it is not in
  `REFRESHER_COMPONENTS` either - the comment was the entire coverage.
- `studio` had no mapping at all, though the Studio page reads the same trees
  plus `datastore/content/linkedin-archive/`.

The in-flight prefixes are now derived from `sources.pulse.IN_FLIGHT_DIRS`
instead of retyped, and a prefix maps to a TUPLE, because one write can
invalidate several pages at once.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("watchdog")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.watcher import (  # noqa: E402
    PATH_TO_COMPONENTS,
    REFRESHER_COMPONENTS,
    WATCHED_COMPONENTS,
    _Handler,
    classify_path,
)
from scripts.bridge_daemon.sources.pulse import IN_FLIGHT_DIRS  # noqa: E402
from scripts.bridge_daemon.sources import studio as studio_src  # noqa: E402


# ------------------------------------------------------- the missing mappings

@pytest.mark.parametrize("rel,expected", [
    ("outputs/documents/2026-08-23_letter.pdf", "inflight"),
    ("outputs/content/tribe/monday.md", "inflight"),
    ("threads/business/2026-08-05-something.md", "threads"),
    ("datastore/content/linkedin-archive/post-1/post.md", "studio"),
])
def test_the_four_unmapped_trees_now_bump_a_component(rel: str, expected: str):
    assert expected in classify_path(rel), (rel, classify_path(rel))


def test_an_unrelated_path_still_bumps_nothing():
    assert classify_path("README.md") == ()
    assert classify_path("scripts/utils/workspace.py") == ()


# ----------------------------------------------- derived, not hand-maintained

def test_every_in_flight_dir_bumps_both_pages_that_read_it():
    """pulse and studio scan the same trees; both must invalidate."""
    for directory in IN_FLIGHT_DIRS:
        components = classify_path(f"{directory}/probe.md")
        assert "inflight" in components, directory
        assert "studio" in components, directory


def test_the_two_in_flight_lists_still_agree():
    """studio.py carries a copy under a 'Must stay in sync' comment.

    A third copy in the watcher is what went stale. This pins the two that
    remain, so deriving from `pulse` cannot silently under-cover studio.
    """
    studio_dirs = {d for d, _label in studio_src.IN_FLIGHT_DIRS}
    assert studio_dirs == set(IN_FLIGHT_DIRS), (
        studio_dirs.symmetric_difference(set(IN_FLIGHT_DIRS))
    )


def test_the_specific_pages_kept_their_mappings():
    """Deriving must not have dropped the narrower assignments."""
    assert "inbox" in classify_path("outputs/operations/email-intelligence/d.md")
    assert "investors" in classify_path("outputs/operations/fundraising/f.md")
    assert classify_path("context/pipeline.md") == ("pipeline",)
    assert classify_path("crm/contacts/a.md") == ("tribe",)
    assert classify_path(".claude/skills/osint/SKILL.md") == ("capabilities",)
    assert classify_path("knowledge/note.md") == ("library",)
    assert classify_path("outputs/operations/viraid/v.md") == ("tasks",)
    assert classify_path("outputs/communications/email/e.md") == ("approvals",)
    assert classify_path("outputs/_sync/calendar/c.json") == ("day",)


def test_the_longest_prefix_wins():
    """`outputs/content/tribe/` must not be shadowed by a shorter entry.

    The literal value on both sides, not `f(a) == f(b)`. Comparing the function
    to itself is an identity: if `classify_path` started returning `()` for
    everything, both sides would be `()` and the assertion would hold. It said
    exactly that until 2026-08-27, in a test named for a shadowing rule it never
    exercised - there is no shorter `outputs/` entry in the shipped map, so the
    collision it guards against was not even present.
    """
    expected = ("inflight", "studio")
    assert classify_path("outputs/content/tribe/x.md") == expected
    assert classify_path("outputs/content/tribe/nested/deep/x.md") == expected


def test_a_shorter_prefix_does_not_shadow_a_deeper_one(monkeypatch):
    """The collision itself, constructed. The shipped map has no shorter
    `outputs/` key, so nothing in the tree can trigger the rule this file names.
    Add one and check the deeper entry still wins."""
    import scripts.bridge_daemon.watcher as watcher

    monkeypatch.setattr(watcher, "PATH_TO_COMPONENTS", {
        "outputs/": ("catch-all",),
        "outputs/content/tribe/": ("inflight", "studio"),
    })
    assert watcher.classify_path("outputs/content/tribe/x.md") == \
        ("inflight", "studio")
    assert watcher.classify_path("outputs/elsewhere/x.md") == ("catch-all",)


def test_windows_separators_are_normalised():
    assert "threads" in classify_path("threads\\business\\x.md")


# --------------------------------------------- the claim state.py makes is true

def test_threads_is_actually_covered_now():
    """state.COMPONENTS says it is; before the fix nothing backed that."""
    assert "threads" in WATCHED_COMPONENTS
    assert "threads" not in REFRESHER_COMPONENTS, (
        "if a refresher now covers threads, the watcher mapping is the wrong fix"
    )
    assert any("threads" in c for c in PATH_TO_COMPONENTS.values())


def test_studio_is_covered():
    assert "studio" in WATCHED_COMPONENTS


def test_every_watched_component_is_a_real_component():
    """A typo'd component name would bump a version nothing renders."""
    from scripts.bridge_daemon.state import COMPONENTS

    unknown = sorted(WATCHED_COMPONENTS - set(COMPONENTS))
    assert unknown == [], unknown


# ---------------------------------------------------------- the handler wiring

class _Created:
    """The one field `on_any_event` reads on a non-move event."""

    is_directory = False

    def __init__(self, src):
        self.src_path = str(src)


def _recording_handler(root: Path):
    scheduled: list[str] = []
    bumper = type("_Recorder", (), {"schedule": staticmethod(scheduled.append)})()
    return _Handler(root, bumper), scheduled


def test_a_single_write_schedules_every_matching_component(tmp_path):
    """The handler used to schedule at most one.

    Until 2026-08-29 this test never touched the handler. It ran
    `for component in classify_path(...): bumper.schedule(component)` in its own
    body and asserted against that loop, which is the fan-out re-implemented in
    the test rather than measured in the code. `_Handler` was not even imported
    here. Truncating the real comprehension to `self._classify(p)[:1]` left this
    file at 15 passed and all of `tests/bridge` at 1210 passed, so the one write
    to `outputs/documents/` that must bump BOTH the Pulse in-flight count and
    the Studio page was unguarded: write a document, in-flight moves, Studio
    stays stale until a manual refresh. That is the exact failure this module's
    docstring says it was written to close.
    """
    handler, scheduled = _recording_handler(tmp_path)
    handler.on_any_event(_Created(tmp_path / "outputs/documents/x.pdf"))
    assert sorted(scheduled) == ["inflight", "studio"], scheduled
