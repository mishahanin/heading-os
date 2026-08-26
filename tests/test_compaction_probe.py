"""The compaction probe, and the two ways its assertions could pass vacuously.

Both negative cases below pin defects found by the 2026-08-19 scrutiny pass, and
neither is optional decoration.

The first: `--assert-handoff-precedes-compaction` looks for a handoff written
BEFORE a boundary, and `checkpoint-save.py` writes one AFTER every compaction
with its stamp truncated to %H%M%S. Without the kind filter every boundary is
satisfied by the archive it caused, so the assertion passes for any session that
ever compacted - and the test asserting it passes too.

The second: `--assert-driven-compaction` used to test `trigger == "manual"`.
`trigger` records HOW the harness was asked, not WHO asked. This workspace's
transcripts hold 78 hand-typed manual boundaries, 52 of them in the same token
band a driven compaction lands in, so a trigger test cannot separate the driven
path from an operator following the offer prompt.
"""
import importlib.util
import json
import sys
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "compaction_probe", str(ROOT / "scripts" / "compaction-probe.py")
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

SESSION = "aaaaaaaa-1111-2222-3333-444444444444"


def _boundary(timestamp, trigger, pre, post):
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "timestamp": timestamp,
        "compactMetadata": {
            "trigger": trigger,
            "preTokens": pre,
            "postTokens": post,
            "cumulativeDroppedTokens": pre - post,
            "durationMs": 1000,
        },
    }


def _transcript(directory: Path, session: str, records: list) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A scratch transcript directory, handoff archive and state directory."""
    transcripts = tmp_path / "projects" / "mangled"
    archive = tmp_path / "handoff"
    archive.mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()

    monkeypatch.setattr(probe.CP, "transcript_dir", lambda _project: transcripts)
    monkeypatch.setattr(probe.CP, "handoff_dir", lambda *a, **kw: archive)
    monkeypatch.setattr(
        probe.CP, "state_path", lambda _project, slug: state / f"checkpoint-{slug}.json"
    )
    return {"transcripts": transcripts, "archive": archive, "state": state,
            "project": tmp_path}


def _scan_all(tree) -> list:
    events = []
    for path in sorted(tree["transcripts"].glob("*.jsonl")):
        found, problem = probe._scan(path)
        assert problem is None, problem
        events.extend(found)
    return events


def _write_state(tree, session, **payload):
    slug = probe.CP.safe_slug(session)
    (tree["state"] / f"checkpoint-{slug}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.fixture()
def utc_clock(monkeypatch):
    """Pin the probe's display zone to UTC, whatever the host is set to.

    The two handoff tests below invent an archive stamp in the same numeric
    frame as the boundary, which the section comment further down already names
    as the assumption every early test makes. It is only TRUE when the operator's
    zone is UTC, and until 2026-08-27 nothing said so: running the suite at
    UTC-12 turned `2026-08-19-094500` into a stamp four-and-a-bit hours AFTER a
    boundary it was written to precede, and the test failed on code that was
    right. Stating the frame is cheaper than converting the fixtures, and it
    keeps these two tests measuring the ordering rather than the host.
    """
    monkeypatch.setattr(probe, "get_default_tz", lambda: timezone.utc)


def _archive(tree, session, kind, stamp):
    slug = probe.CP.safe_slug(session)
    (tree["archive"] / f"{stamp}_handoff_{kind}_{slug}.md").write_text(
        "body", encoding="utf-8"
    )


# ============================================================
# Parsing
# ============================================================

def test_parsing_reads_both_records_and_counts_assistant_turns(tree):
    _transcript(tree["transcripts"], SESSION, [
        {"type": "assistant"},
        _boundary("2026-08-19T07:31:51.814Z", "auto", 617013, 15772),
        {"type": "assistant"},
        {"type": "assistant"},
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    events = _scan_all(tree)
    assert [e["trigger"] for e in events] == ["auto", "manual"]
    assert [e["preTokens"] for e in events] == [617013, 324190]
    assert [e["assistant_turn"] for e in events] == [1, 3], (
        "the turn index is what --assert-no-cascade measures gaps in"
    )


def test_an_unreadable_transcript_is_reported_not_dropped(tree):
    """scope-claims: a narrowed scan must not print like a complete one."""
    events, problem = probe._scan(tree["transcripts"] / "does-not-exist.jsonl")
    assert events == []
    assert problem, "an unreadable transcript vanished silently"


# ============================================================
# --assert-driven-compaction
# ============================================================

def test_driven_passes_when_the_state_file_carries_the_request(tree):
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    _write_state(tree, SESSION, compact_requests=[
        {"at": "2026-08-19T09:49:00+00:00", "bucket": 45}
    ])
    assert probe.assert_driven(_scan_all(tree), tree["project"]) == []


def test_driven_fails_on_a_hand_typed_manual_compaction(tree):
    """THE negative case. A boundary can read `manual` and still not be ours."""
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    _write_state(tree, SESSION, session_id=SESSION)
    violations = probe.assert_driven(_scan_all(tree), tree["project"])
    assert violations, (
        "a hand-typed /compact passed the driven assertion, which means the "
        "assertion is reading trigger rather than state"
    )


def test_driven_ignores_a_request_made_after_the_boundary(tree):
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    _write_state(tree, SESSION, compact_requested_at="2026-08-19T11:00:00+00:00")
    assert probe.assert_driven(_scan_all(tree), tree["project"]), (
        "a request recorded after the boundary was accepted as its cause"
    )


# ============================================================
# --assert-handoff-precedes-compaction
# ============================================================

def test_handoff_passes_with_a_pre_compaction_archive(tree, utc_clock):
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    _archive(tree, SESSION, "auto", "2026-08-19-094500")
    assert probe.assert_handoff_precedes(_scan_all(tree), tree["project"]) == []


def test_handoff_fails_when_only_the_post_compaction_archive_exists(tree):
    """THE other negative case, and the reason the kind filter exists.

    No clock pin here, deliberately. This one is decided by the KIND filter -
    a `compact-auto` archive is the one the compaction caused, so it never
    counts as a candidate and the violation stands at every offset on earth.
    A mutation removing a pin from this test survived at UTC-12, UTC+4 and
    UTC+14 on 2026-08-27, which is the proof: a fixture that cannot change an
    outcome measures nothing, and stating that it is unnecessary is worth more
    than carrying it.

    Reproduces session 31cea474: the boundary at 07:31:51.814 had an archive
    named ...-073151_, whose stamp reads earlier than the event it FOLLOWED
    because checkpoint-save.py truncates to %H%M%S.
    """
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T07:31:51.814Z", "auto", 617013, 15772),
    ])
    _archive(tree, SESSION, "compact-auto", "2026-08-19-073151")
    assert probe.assert_handoff_precedes(_scan_all(tree), tree["project"]), (
        "a compaction was satisfied by the archive it caused"
    )


# ============================================================
# The two clocks
# ============================================================
#
# Found by the 2026-08-23 audit, and invisible to every test above because they
# all invent an archive stamp in the same numeric frame as the boundary.
#
# A boundary timestamp comes out of the transcript in UTC. An archive FILENAME
# stamp comes out of `checkpoint-save.py`, which uses `CP.local_now()` — the
# operator's own zone, deliberately, so a handoff written at 02:56 Dubai time
# does not land on yesterday's date. Comparing the two as strings is comparing
# two different clocks, and on Asia/Dubai the archive reads four hours late.
#
# Measured before the fix: `--assert-handoff-precedes-compaction` reported 40
# violations on this workspace's real transcripts, including session c2f703f7 at
# 2026-08-21T13:08:05Z, whose `auto` handoff sits at `2026-08-21-170420` — that
# is 13:04:20 UTC, four minutes BEFORE the boundary it was accused of missing.


@pytest.fixture()
def dubai(monkeypatch):
    """Pin the probe's display zone to UTC+4, whatever the host is set to."""
    from datetime import timedelta, timezone as _tz

    monkeypatch.setattr(probe, "get_default_tz", lambda: _tz(timedelta(hours=4)))


def test_handoff_reads_the_archive_stamp_on_the_archive_clock(tree, dubai):
    """The real c2f703f7 case: a handoff four minutes early, filed four hours on."""
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-21T13:08:05.956Z", "manual", 324190, 10929),
    ])
    _archive(tree, SESSION, "auto", "2026-08-21-170420")
    assert probe.assert_handoff_precedes(_scan_all(tree), tree["project"]) == [], (
        "a handoff written before the boundary was read as written after it, "
        "because the event stamp is UTC and the filename stamp is local"
    )


def test_handoff_still_fails_when_the_archive_really_is_later(tree, dubai):
    """The fix must not simply widen the window by four hours.

    17:09:00 local is 13:09:00 UTC — one minute AFTER the boundary. This is the
    mutation that catches a fix that converts in the wrong direction.
    """
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-21T13:08:05.956Z", "manual", 324190, 10929),
    ])
    _archive(tree, SESSION, "auto", "2026-08-21-170900")
    assert probe.assert_handoff_precedes(_scan_all(tree), tree["project"]), (
        "an archive written after the boundary satisfied the assertion"
    )


def test_the_driven_assertion_stays_on_the_utc_clock(tree, dubai):
    """The other half of the split, and the reason for two helpers.

    `compact_requests[].at` is written with `CP.utc_now()`, so THAT comparison
    was always right. Converting the event stamp globally would break it: a
    request at 13:07:00Z would be read against a 17:08:05 event stamp and every
    later request would look like it preceded the boundary.
    """
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-21T13:08:05.956Z", "manual", 324190, 10929),
    ])
    _write_state(tree, SESSION, compact_requests=[
        {"at": "2026-08-21T13:07:00+00:00", "bucket": 45}
    ])
    assert probe.assert_driven(_scan_all(tree), tree["project"]) == []

    _write_state(tree, SESSION, compact_requests=[
        {"at": "2026-08-21T13:09:00+00:00", "bucket": 45}
    ])
    assert probe.assert_driven(_scan_all(tree), tree["project"]), (
        "a request made after the boundary was accepted, so the driven check "
        "is now reading the event on the display clock"
    )


# ============================================================
# --assert-no-native-compaction and --assert-no-cascade
# ============================================================

def test_native_fails_on_auto_and_passes_on_an_all_manual_window(tree):
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T07:31:51.814Z", "auto", 617013, 15772),
    ])
    assert probe.assert_no_native(_scan_all(tree))

    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T09:50:47.749Z", "manual", 324190, 10929),
    ])
    assert probe.assert_no_native(_scan_all(tree)) == []


def test_cascade_fails_inside_the_turn_gap_and_passes_outside_it(tree):
    tight = [
        _boundary("2026-08-19T07:57:11.479Z", "auto", 171608, 20262),
        {"type": "assistant"},
        {"type": "assistant"},
        _boundary("2026-08-19T07:58:19.997Z", "auto", 118635, 29049),
    ]
    _transcript(tree["transcripts"], SESSION, tight)
    assert probe.assert_no_cascade(_scan_all(tree), 3)

    roomy = tight[:1] + [{"type": "assistant"}] * 5 + tight[-1:]
    _transcript(tree["transcripts"], SESSION, roomy)
    assert probe.assert_no_cascade(_scan_all(tree), 3) == []


def test_a_bare_run_asserts_nothing(tree, monkeypatch, capsys):
    """CAP-3: reporting is not asserting. A bare invocation exits 0."""
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T07:31:51.814Z", "auto", 617013, 15772),
    ])
    monkeypatch.setattr(probe, "get_workspace_root", lambda: tree["project"])
    monkeypatch.setattr(sys, "argv", ["compaction-probe.py"])
    assert probe.main() == 0
    assert "617013" in capsys.readouterr().out


def test_an_assertion_flag_sets_the_exit_code(tree, monkeypatch):
    _transcript(tree["transcripts"], SESSION, [
        _boundary("2026-08-19T07:31:51.814Z", "auto", 617013, 15772),
    ])
    monkeypatch.setattr(probe, "get_workspace_root", lambda: tree["project"])
    monkeypatch.setattr(
        sys, "argv", ["compaction-probe.py", "--assert-no-native-compaction"]
    )
    assert probe.main() == 1
