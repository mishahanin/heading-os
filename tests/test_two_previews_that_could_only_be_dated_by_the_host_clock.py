"""Two Pulse previews banded a day count they gave no way to pin.

`tribe_state_preview` bands each member on `days_since_touch <=
TRIBE_ON_WATCH_DAYS`, and `list_tribe` has always accepted a `today` to date
that count from. The preview never forwarded it. `threads_state_preview` had the
same shape one function away: it read `datetime.now(get_default_tz()).date()`
inside the body with no injection point.

So the only way to exercise either boundary was a fixture built relative to
"now", which leaves the assertion one midnight tick from flipping. The live
exposure is a microsecond-wide race; the real cost was that nothing could test
the band at all. A test that reads the host clock is a test of the day it ran
on, and one such test in this repo was weekend-broken from the day it was
written because nobody ran the suite on a weekend.

Both functions take `today` now. Every date below is a literal.

Run: python3 -m pytest tests/test_two_previews_that_could_only_be_dated_by_the_host_clock.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import pulse as PULSE  # noqa: E402

TODAY = date(2026, 8, 20)  # a Thursday, fixed; nothing here recomputes from it


def _contact(root: Path, slug: str, last_touch: str) -> None:
    d = root / "crm" / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        "---\n"
        "relationship_type: tribe\n"
        f"email: {slug}@example.invalid\n"
        f"last_touch: {last_touch}\n"
        "status: active\n"
        "---\n"
        f"# {slug.replace('-', ' ').title()}\n",
        encoding="utf-8")


def _thread(root: Path, tid: str, last_touched: str) -> None:
    d = root / "threads" / "business"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.md").write_text(
        "---\n"
        f"id: {tid}\n"
        f"title: {tid.replace('-', ' ')}\n"
        "status: active\n"
        f"last_touched: {last_touched}\n"
        "---\n"
        "body\n",
        encoding="utf-8")


# ==========================================================================
# tribe_state_preview
# ==========================================================================

def test_the_watch_band_can_be_dated_from_an_argument(tmp_path):
    """THE case: the boundary is exercised without touching the host clock."""
    _contact(tmp_path, "moneypenny", "2026-08-13")   # exactly 7 days: ON
    _contact(tmp_path, "felix-leiter", "2026-08-12")  # 8 days: OFF

    out = PULSE.tribe_state_preview(tmp_path, today=TODAY)

    assert out is not None, "the fixture produced no preview at all"
    by_slug = {m["slug"]: m for m in out["members"]}
    assert by_slug["moneypenny"]["days_since"] == 7
    assert by_slug["moneypenny"]["presence"] == "on"
    assert by_slug["felix-leiter"]["days_since"] == 8
    assert by_slug["felix-leiter"]["presence"] == "off"
    assert out["on_watch"] == 1


def test_the_band_moves_when_the_supplied_day_moves(tmp_path):
    """Proves the argument is what dates it, not something else that happens to
    agree today. Same fixture, two different days, two different answers."""
    _contact(tmp_path, "moneypenny", "2026-08-13")

    on_time = PULSE.tribe_state_preview(tmp_path, today=date(2026, 8, 20))
    a_day_late = PULSE.tribe_state_preview(tmp_path, today=date(2026, 8, 21))

    assert on_time["on_watch"] == 1
    assert a_day_late["on_watch"] == 0
    assert a_day_late["members"][0]["days_since"] == 8


@pytest.mark.parametrize("last_touch,days,presence", [
    ("2026-08-20", 0, "on"),    # touched today
    ("2026-08-14", 6, "on"),
    ("2026-08-13", 7, "on"),    # ON the line
    ("2026-08-12", 8, "off"),   # one past it
    ("2026-06-01", 80, "off"),
])
def test_each_side_of_the_boundary(tmp_path, last_touch, days, presence):
    """A bound needs a case ON the line, not only far either side of it."""
    _contact(tmp_path, "q-branch", last_touch)

    out = PULSE.tribe_state_preview(tmp_path, today=TODAY)

    assert out["members"][0]["days_since"] == days
    assert out["members"][0]["presence"] == presence


def test_omitting_the_day_still_works(tmp_path):
    """The negative control. The argument is optional, and every existing caller
    passes nothing; a required parameter would break all five of them."""
    _contact(tmp_path, "moneypenny", "2026-08-13")

    out = PULSE.tribe_state_preview(tmp_path)

    assert out is not None
    assert out["total"] == 1
    assert isinstance(out["members"][0]["days_since"], int)


# ==========================================================================
# threads_state_preview
# ==========================================================================

def test_thread_ages_can_be_dated_from_an_argument(tmp_path):
    _thread(tmp_path, "universal-exports-renewal", "2026-08-18")
    _thread(tmp_path, "skyfall-estate", "2026-07-21")

    out = PULSE.threads_state_preview(tmp_path, today=TODAY)

    assert out is not None
    ages = {t["id"]: t["days_since"] for t in out["threads"]}
    assert ages["universal-exports-renewal"] == 2
    assert ages["skyfall-estate"] == 30


def test_thread_ages_move_with_the_supplied_day(tmp_path):
    _thread(tmp_path, "universal-exports-renewal", "2026-08-18")

    near = PULSE.threads_state_preview(tmp_path, today=date(2026, 8, 20))
    far = PULSE.threads_state_preview(tmp_path, today=date(2026, 9, 20))

    assert near["threads"][0]["days_since"] == 2
    assert far["threads"][0]["days_since"] == 33


def test_threads_omitting_the_day_still_works(tmp_path):
    """The negative control for the sibling."""
    _thread(tmp_path, "universal-exports-renewal", "2026-08-18")

    out = PULSE.threads_state_preview(tmp_path)

    assert out is not None
    assert out["active_total"] == 1
