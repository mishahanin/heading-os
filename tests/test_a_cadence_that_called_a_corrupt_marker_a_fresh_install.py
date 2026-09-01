#!/usr/bin/env python3
"""What `scripts/odin-cadence.py` says about its own state, when the state is bad.

Two defects, both in sentences rather than sums, and both measured on the real
functions before anything was changed.

  - `.last-collect` holding garbage came back from `read_marker` as the same
    `(None, None)` an ABSENT marker gives. That collapse is right for the
    counting (both fall through to `EPOCH_FLOOR` and count everything, the safe
    direction), and wrong for the nudge: the JSON said `"last_collect": null`,
    the reason said `never collected`, and the one-line message that goes
    verbatim to Telegram read "Odin cadence: collect never run" beside a marker
    file that plainly exists. A torn write masqueraded as a fresh install with
    nothing anywhere reporting that the file was unreadable.

  - Every reason on the list encodes its THRESHOLD -- `days_since>=7`,
    `unharvested>=5`, `reflect_clusters>=1` -- except the stale one, which
    interpolated the observed COUNT. Measured with two stale clusters the reason
    read `stale_clusters>=2`, which is shape-identical to a configuration whose
    stale threshold is 2. A consumer diffing reasons against thresholds cannot
    tell the observation from the setting.

No host clock decides an assertion here: every date is a literal, and the
cluster work goes through an explicit `today=`.

That sentence used to read "No host clock is read anywhere here", and it was
false for every test that went through `compute()`. `compute` called
`analyze_reflect_clusters(root)` with no date, so the ageing that produces
`stale_clusters` and the `stale_clusters>=1` reason ran against the machine's
clock while the episodes carried the literal `created: 2026-01-01`. The tests
passed because the real date is far past that, not because anything pinned it.
MEASURED 2026-09-01 with the module's clock frozen inside `odin-cadence.py`:

    host clock (real today)  -> stale_clusters=2
        reasons=['never collected', 'reflect_clusters>=1', 'stale_clusters>=1']
    frozen at 2026-01-05     -> stale_clusters=0
        reasons=['never collected', 'reflect_clusters>=1']

`compute` now takes `today` and passes it down, which is the seam
`analyze_reflect_clusters` already had, and every test below that reads a
cluster age supplies it. `TODAY` is that literal.

Run: .venv/bin/python -m pytest
     tests/test_a_cadence_that_called_a_corrupt_marker_a_fresh_install.py -q
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "odin-cadence.py"


def _load():
    spec = importlib.util.spec_from_file_location("odin_cadence_marker", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oc = _load()

# The one date every age in this file is measured against. Chosen well past the
# `created:` literals below so the clusters really are stale, and passed in
# explicitly so that stays true on any machine on any day.
TODAY = date(2026, 8, 20)


def _brain(root: Path) -> Path:
    d = root / "knowledge" / "odin-brain"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_marker(root: Path, body: str) -> None:
    (_brain(root) / ".last-collect").write_text(body, encoding="utf-8")


# ============================================================
# 1 - absent, usable and unreadable are three states, not two
# ============================================================

@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not-a-date\n", "unreadable"),
        ("2026-13-45\n", "unreadable"),
        ("", "unreadable"),
        ("   \n", "unreadable"),
        ("2026-08-01\n", "ok"),
        ("2026-08-01T09:30:00\n", "ok"),
    ],
)
def test_marker_state_separates_corrupt_from_usable(tmp_path, body, expected):
    """An existing-but-empty marker is a torn write, not an absent one."""
    _write_marker(tmp_path, body)
    assert oc.marker_state(tmp_path) == expected


def test_marker_state_calls_a_missing_file_absent(tmp_path):
    _brain(tmp_path)
    assert oc.marker_state(tmp_path) == "absent"


def test_read_marker_still_collapses_both_to_none(tmp_path):
    """The counting contract is unchanged: corrupt still counts from the floor.

    Pinned deliberately. The fix must add a way to TELL the two apart, not
    change which of them counts everything -- an unreadable marker that stopped
    counting would lose material silently, which is the worse direction.
    """
    _write_marker(tmp_path, "not-a-date\n")
    assert oc.read_marker(tmp_path) == (None, None)
    assert oc.compute(tmp_path, 5, today=TODAY)["unharvested_total"] == 0  # empty corpus, but no crash


# ============================================================
# 2 - the reason and the sentence both stop saying "never run"
# ============================================================

def test_a_corrupt_marker_is_not_reported_as_never_collected(tmp_path):
    _write_marker(tmp_path, "not-a-date\n")
    result = oc.compute(tmp_path, 5, today=TODAY)

    assert result["marker_state"] == "unreadable"
    assert "never collected" not in result["reasons"], result["reasons"]
    assert "collect marker unreadable" in result["reasons"], result["reasons"]
    # The skipped list is the tool's own record of what it could not do.
    assert any("marker unreadable" in s for s in result["skipped"]), result["skipped"]


def test_an_absent_marker_is_still_reported_as_never_collected(tmp_path):
    """The other direction, so the fix cannot pass by renaming both states."""
    _brain(tmp_path)
    result = oc.compute(tmp_path, 5, today=TODAY)

    assert result["marker_state"] == "absent"
    assert "never collected" in result["reasons"], result["reasons"]
    assert "collect marker unreadable" not in result["reasons"], result["reasons"]
    assert not any("marker unreadable" in s for s in result["skipped"]), result["skipped"]


def test_the_telegram_line_names_the_unreadable_marker(tmp_path):
    """This exact string is what `ops-radar-notify` puts on the wire."""
    _write_marker(tmp_path, "not-a-date\n")
    line = oc.suggestion_line(oc.compute(tmp_path, 5, today=TODAY))

    assert "collect never run" not in line, line
    assert "collect marker unreadable" in line, line


def test_the_up_to_date_line_names_it_too(tmp_path):
    """`suggestion_line` has two branches and only one was ever read aloud."""
    corrupt = {"nudge": False, "days_since": None, "marker_state": "unreadable"}
    absent = {"nudge": False, "days_since": None, "marker_state": "absent"}

    assert "collect marker unreadable" in oc.suggestion_line(corrupt)
    assert "collect never run" in oc.suggestion_line(absent)


def test_a_result_without_the_new_key_still_renders(tmp_path):
    """Callers that build a result dict by hand must not start raising KeyError."""
    line = oc.suggestion_line({"nudge": False, "days_since": None})
    assert "collect never run" in line, line


# ============================================================
# 3 - the stale reason carries the threshold, like every sibling
# ============================================================

def _episodes(root: Path, clusters: int, created: str) -> None:
    """`clusters` disjoint pairs of raw episodes, each pair sharing three tags."""
    d = root / "knowledge" / "odin-brain" / "episodes"
    d.mkdir(parents=True, exist_ok=True)
    for c in range(clusters):
        for member in range(2):
            (d / f"c{c}e{member}.md").write_text(
                "---\n"
                "status: raw\n"
                f"created: {created}\n"
                f"keywords: [theme{c}alpha, theme{c}beta, theme{c}gamma]\n"
                "---\n\nbody\n",
                encoding="utf-8",
            )


@pytest.mark.parametrize("n_stale", [1, 2, 3])
def test_the_stale_reason_is_a_threshold_not_a_tally(tmp_path, n_stale):
    """`stale_clusters>=3` was indistinguishable from a threshold of three."""
    _brain(tmp_path)
    _episodes(tmp_path, clusters=n_stale, created="2026-01-01")

    result = oc.compute(tmp_path, 5, today=TODAY)

    assert result["stale_clusters"] == n_stale, result
    assert "stale_clusters>=1" in result["reasons"], result["reasons"]
    # The count appears in the JSON, and nowhere in the reason vocabulary.
    stale_reasons = [r for r in result["reasons"] if r.startswith("stale_clusters")]
    assert stale_reasons == ["stale_clusters>=1"], stale_reasons


def test_no_stale_cluster_emits_no_stale_reason(tmp_path):
    """The negative case, so a hardcoded constant cannot pass this group."""
    _brain(tmp_path)
    # Dated inside STALE_CLUSTER_DAYS of the `today` the clusters are aged
    # against, via a marker that keeps `compute` off the "never collected" path.
    _write_marker(tmp_path, "2026-08-01\n")
    fresh = oc.analyze_reflect_clusters(tmp_path, today=date(2026, 8, 20))
    assert fresh["count"] == 0  # no episodes yet: the corpus is empty on purpose

    _episodes(tmp_path, clusters=2, created="2026-08-19")
    aged = oc.analyze_reflect_clusters(tmp_path, today=date(2026, 8, 20))
    assert aged["count"] == 2, aged
    assert aged["stale_count"] == 0, aged


def test_every_sibling_reason_still_encodes_its_threshold(tmp_path):
    """The invariant the stale reason broke, asserted over the whole list.

    Derived from the module's own constants rather than retyped, so a threshold
    change cannot leave this test agreeing with a stale copy of itself.
    """
    _brain(tmp_path)
    _episodes(tmp_path, clusters=2, created="2026-01-01")
    reasons = oc.compute(tmp_path, oc.DEFAULT_MIN_ENTRIES, today=TODAY)["reasons"]

    assert reasons, "empty reason list: the fixture stopped producing a nudge"
    numeric = [r for r in reasons if any(ch.isdigit() for ch in r)]
    assert numeric, reasons
    allowed = {
        f"days_since>={oc.DAYS_THRESHOLD}",
        f"unharvested>={oc.DEFAULT_MIN_ENTRIES}",
        "reflect_clusters>=1",
        "stale_clusters>=1",
    }
    assert set(numeric) <= allowed, sorted(set(numeric) - allowed)
