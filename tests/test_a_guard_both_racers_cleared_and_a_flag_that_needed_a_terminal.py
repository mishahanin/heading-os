"""The 2026-08-24 Kimi-audit findings for shards 01-p1 through 02-p1.

Grouped because they were found in one pass, not because they share a subject.
Each is verified against the thing it describes, not against a restated copy.

1. `finalizers/crm_log.py` read the dedupe log ABOVE the contact-write lock and
   wrote the marker BELOW it. A double click on one conversation cleared the
   check twice, queued on the lock, and wrote the interaction entry twice. A
   guard both racers pass guards nothing.

2. `bridge_daemon/config.py` ordered snapshots with a plain name sort. A legacy
   name starts with the year and a current one with a zero-padded sequence, so
   `"2" > "0"` sorted every legacy file last: the trim deleted the NEWEST
   snapshots, and `revert_config`'s "index 1" could hand back the current boot's
   own snapshot.

3. `approvals.read_draft` carried a second copy of the path validator, and the
   copy was the looser one: `.lstrip("./")` strips a character SET rather than a
   prefix, and the suffix test lowercased. `./outputs/.../x.MD` read fine and
   could never be marked sent.

4. Three shape classes had a guard written once and applied to one of the nine
   modules with the identical read: `.get` on a `json.loads` that was never
   shape-checked, `entry.get("ts", "")` feeding a sort, and a tombstone tested
   with `is True`. `bridge_daemon/_shapes.py` is now the single home.

5. `pulse._parse_duration_minutes` matched `(\\d+)h` against `1.5h` and took the
   `5h`: a 90-minute meeting became a five-hour block.

6. `pulse.pulse_data` reported summary drift from `pipeline_value_and_deals`,
   which returns `(0, 0)` for a MISSING summary table. A pipeline.md with no
   summary rows was told it disagreed with itself on every render.

7. `pulse.today_activity` took its counts from lists already capped at 20, so a
   day with 25 dismissals reported 20 -- a wrong number, not a short list.

8. `terminal.build_tmux_command` passed `-A`, asserted in its own test as
   "idempotent attach-or-create". With the session present, `-A` turns
   `new-session` into `attach-session`, which needs a terminal the daemon does
   not have. Measured here on tmux 3.4.

9. `tribe._merge_tribe` let two roster rows claim one CRM record, so the same
   person rendered twice carrying the same slug and was counted twice.
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _code(path: Path) -> str:
    """Source with whole-line `#` comments removed.

    Every fix in this file records what it replaced, so the removed code is
    quoted in a comment beside it and a plain grep finds its own tombstone.
    Docstrings are NOT stripped, so an assertion about prose must target a
    docstring and an assertion about behaviour must target this.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


# ============================================================
# 1. a dedupe check both racers cleared
# ============================================================

CRM_LOG = ROOT / "scripts" / "bridge_daemon" / "finalizers" / "crm_log.py"


def _fetch_with(data_root: Path, conv_id: str, slug: str) -> None:
    d = data_root / "outputs" / "operations" / "email-intelligence"
    d.mkdir(parents=True, exist_ok=True)
    (d / "_latest-fetch.json").write_text(json.dumps({"conversations": [
        {"id": conv_id, "topic": "Renewal", "latest_datetime": "2026-08-24T09:00:00",
         "crm_context": {"contact_slug": slug}},
    ]}), encoding="utf-8")
    c = data_root / "crm" / "contacts"
    c.mkdir(parents=True, exist_ok=True)
    (c / f"{slug}.md").write_text(
        "# Contact\n\n**Last Touch:** 2026-01-01\n\n## Interaction Log\n\n",
        encoding="utf-8")


def test_the_check_and_the_marker_are_in_the_same_critical_section():
    """Structure, because the race needs both halves inside one lock.

    A behavioural race test can pass by luck on a fast machine. This cannot:
    the dedupe read that decides, and the marker write, must both sit after
    `with _CONTACT_WRITE_LOCK:` and before the function returns.
    """
    lines = _code(CRM_LOG).splitlines()
    lock_line = next(i for i, ln in enumerate(lines)
                     if ln.strip() == "with _CONTACT_WRITE_LOCK:")
    lock_indent = len(lines[lock_line]) - len(lines[lock_line].lstrip())

    def inside(needle: str) -> bool:
        """INDENT, not just line order.

        Order alone is not the property: dedenting the marker write leaves it
        textually below the `with` and semantically outside it, which reopens
        the exact window. A mutation that did precisely that survived the
        order-only version of this test.
        """
        i = next(idx for idx, ln in enumerate(lines)
                 if idx > lock_line and needle in ln)
        return len(lines[i]) - len(lines[i].lstrip()) > lock_indent

    assert inside("read_crm_logged(data_root)"), "the deciding check left the lock"
    assert inside("mark_crm_logged(data_root"), "the marker write left the lock"


def test_two_racing_log_calls_write_one_interaction(tmp_path, monkeypatch):
    from scripts.bridge_daemon.finalizers import crm_log

    _fetch_with(tmp_path, "conv-1", "b-bond")
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def go():
        barrier.wait(timeout=5)
        results.append(crm_log.log_to_crm("conv-1", data_root=tmp_path))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    oks = [r for r in results if r.get("ok")]
    assert len(oks) == 1, f"both clicks logged the same conversation: {results}"
    text = (tmp_path / "crm" / "contacts" / "b-bond.md").read_text(encoding="utf-8")
    assert text.count("Logged from the Inbox dashboard.") == 1


@pytest.mark.slow  # holds the race window open with a real sleep
def test_a_second_click_during_the_marker_write_is_refused(tmp_path, monkeypatch):
    """Deterministic, where the two-thread test is only probable.

    The window is between the contact write and the marker write, and on a
    fast machine it closes before the second thread reaches it -- so the
    unstalled race test passed even with the marker OUTSIDE the lock. Stalling
    the marker holds the window open for as long as the test needs.
    """
    import time

    from scripts.bridge_daemon.finalizers import crm_log

    _fetch_with(tmp_path, "conv-3", "b-bond")
    real_mark = crm_log.mark_crm_logged

    def slow_mark(*a, **kw):
        time.sleep(0.4)
        return real_mark(*a, **kw)

    monkeypatch.setattr(crm_log, "mark_crm_logged", slow_mark)

    results: list[dict] = []
    first = threading.Thread(
        target=lambda: results.append(crm_log.log_to_crm("conv-3", data_root=tmp_path)))
    first.start()
    time.sleep(0.15)   # inside the stalled marker write
    results.append(crm_log.log_to_crm("conv-3", data_root=tmp_path))
    first.join(timeout=10)

    text = (tmp_path / "crm" / "contacts" / "b-bond.md").read_text(encoding="utf-8")
    assert text.count("Logged from the Inbox dashboard.") == 1, (
        "the second click wrote a second interaction while the first was still "
        f"recording its marker: {results}")


def test_a_conv_id_is_stored_the_way_it_was_validated(tmp_path):
    """The guard tests `conv_id.strip()`; the write must use the same value."""
    from scripts.bridge_daemon.finalizers import crm_log
    from scripts.bridge_daemon.sources.inbox import read_crm_logged

    _fetch_with(tmp_path, "conv-2", "b-bond")
    assert crm_log.log_to_crm("  conv-2  ", data_root=tmp_path)["ok"] is True
    assert "conv-2" in read_crm_logged(tmp_path), (
        "the dedupe key was stored with the whitespace the guard had trimmed")


# ============================================================
# 2. a trim that deleted the newest snapshots
# ============================================================

def _history(tmp_path: Path) -> Path:
    from scripts.bridge_daemon.config import CONFIG_HISTORY_DIR
    d = tmp_path / CONFIG_HISTORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


LEGACY = "20260519T154808_123456Z.yaml"     # pre-sequence naming
SEQ = ["000000001_20260820T000000_000000Z.yaml",
       "000000002_20260821T000000_000000Z.yaml",
       "000000003_20260822T000000_000000Z.yaml"]


def test_a_legacy_snapshot_is_older_than_every_sequenced_one(tmp_path):
    from scripts.bridge_daemon.config import list_snapshots
    d = _history(tmp_path)
    for name in [LEGACY, *SEQ]:
        (d / name).write_text("user: {}\n", encoding="utf-8")
    names = [p.name for p in list_snapshots(tmp_path)]
    assert names == [*reversed(SEQ), LEGACY], (
        "a plain name sort called the legacy file the newest, so revert's "
        f"index 1 pointed at the wrong snapshot: {names}")


def test_the_trim_keeps_the_newest_not_the_lexicographically_largest(tmp_path):
    from scripts.bridge_daemon.config import SNAPSHOT_KEEP, snapshot_config
    d = _history(tmp_path)
    for name in [LEGACY, *SEQ]:
        (d / name).write_text("user: {}\n", encoding="utf-8")
    written = snapshot_config(tmp_path, {"version": 1})
    remaining = sorted(p.name for p in d.glob("*.yaml"))
    assert len(remaining) == SNAPSHOT_KEEP
    assert written.name in remaining, "the trim deleted the snapshot it just wrote"
    assert LEGACY not in remaining, (
        "the oldest file survived while newer ones were deleted")


def test_the_docstring_no_longer_claims_an_mtime_sort():
    """Anchored on the CLAIM, not on the two words.

    The corrected docstring quotes the phrase it replaced, so a bare
    `"by mtime" not in doc` passes against the correction itself.
    """
    from scripts.bridge_daemon.config import snapshot_config
    doc = snapshot_config.__doc__
    assert "newest 3 by mtime" not in doc, (
        "nothing in this module reads an mtime; the docstring said it did")
    assert "SEQUENCE PREFIX" in doc, "it no longer names what the order IS"


# ============================================================
# 3. one validator for the reader and the writers
# ============================================================

@pytest.mark.parametrize("rel", [
    "./outputs/communications/email/x.md",   # .lstrip("./") accepted this
    "outputs/communications/email/x.MD",     # .suffix.lower() accepted this
])
def test_the_reader_refuses_what_the_writer_refuses(tmp_path, rel):
    from scripts.bridge_daemon.sources.approvals import (
        mark_sent, read_draft, validate_draft_rel_path,
    )
    assert validate_draft_rel_path(rel) is not None
    assert mark_sent(tmp_path, rel)["ok"] is False
    assert read_draft(tmp_path, rel)["ok"] is False, (
        "the reader still accepts a path its own writers reject")


def test_read_draft_holds_no_second_copy_of_the_validator():
    code = _code(ROOT / "scripts" / "bridge_daemon" / "sources" / "approvals.py")
    body = code[code.index("def read_draft("):]
    nxt = body.find("\ndef ", 1)
    if nxt != -1:
        body = body[:nxt]
    # Docstring dropped as well as comments. `_code` leaves docstrings in place
    # by design, and read_draft's docstring QUOTES the `.lstrip("./")` it
    # replaced, so a search over the whole body would find the tombstone and
    # report the defect as still present.
    body = body[body.index('"""', body.index('"""') + 3) + 3:]
    assert "validate_draft_rel_path(" in body
    for reimplemented in ('lstrip("./")', "EMAIL_DRAFTS_DIR + \"/\"", '== ".."'):
        assert reimplemented not in body, (
            f"read_draft re-implements {reimplemented!r} instead of calling the "
            "shared validator, which is how the two drifted apart")


def test_a_good_draft_still_reads(tmp_path):
    """Anchor: refusing everything would pass every assertion above."""
    from scripts.bridge_daemon.sources.approvals import EMAIL_DRAFTS_DIR, read_draft
    d = tmp_path / EMAIL_DRAFTS_DIR
    d.mkdir(parents=True)
    (d / "ok.md").write_text("**To:** a@b.c\n\n---\n\nbody\n", encoding="utf-8")
    out = read_draft(tmp_path, f"{EMAIL_DRAFTS_DIR}/ok.md")
    assert out["ok"] is True and "body" in out["content"]


# ============================================================
# 4. three shape guards, one home
# ============================================================

def test_no_module_reads_a_tombstone_by_identity():
    """`"undo": 1` is a tombstone. `is True` said it was an active entry."""
    modules = sorted((ROOT / "scripts" / "bridge_daemon").rglob("*.py"))
    # A floor under the corpus. "No module does X" is green over zero modules,
    # so a renamed package or a changed suffix would turn this guard off and
    # report a pass. 45 modules on 2026-08-26.
    assert len(modules) >= 20, f"the daemon package collapsed to {len(modules)} modules"
    hits = [p.name for p in modules if 'entry.get("undo") is True' in _code(p)]
    assert hits == [], f"identity-tested tombstones came back in {hits}"


def test_a_hand_edited_undo_one_still_removes_the_entry(tmp_path):
    from scripts.bridge_daemon.sources.inbox import DISMISS_LOG_FILE, read_dismiss_log
    log = tmp_path / DISMISS_LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"conv_id": "c1", "ts": "2026-08-24T01:00:00Z"}) + "\n"
        + json.dumps({"conv_id": "c1", "undo": 1, "ts": "2026-08-24T02:00:00Z"}) + "\n",
        encoding="utf-8")
    assert read_dismiss_log(tmp_path) == set(), (
        "an operator's hand-typed 1 resurrected a dismissed conversation")


def test_a_null_ts_does_not_hide_every_good_row(tmp_path):
    from scripts.bridge_daemon.sources.inbox import DISMISS_LOG_FILE, dismiss_log_recent
    log = tmp_path / DISMISS_LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"conv_id": "good", "ts": "2026-08-24T01:00:00Z"}) + "\n"
        + json.dumps({"conv_id": "bad", "ts": None}) + "\n",
        encoding="utf-8")
    rows = dismiss_log_recent(tmp_path)
    assert {r["conv_id"] for r in rows} == {"good", "bad"}, (
        "one unsortable row took the whole footer down")


def test_a_fetch_file_holding_a_bare_list_is_a_miss_not_a_crash(tmp_path):
    """`json.JSONDecodeError` does not catch valid JSON of the wrong shape, and
    `.get` on a list is an AttributeError that walks past the caller's guard."""
    from scripts.bridge_daemon.sources.inbox import read_inbox
    d = tmp_path / "outputs" / "operations" / "email-intelligence"
    d.mkdir(parents=True)
    (d / "_latest-fetch.json").write_text("[]", encoding="utf-8")
    out = read_inbox(tmp_path)
    assert out["bands"] == {"needs-you": [], "fyi": [], "noise": []}


def test_critical_reads_the_shared_guards_not_a_private_copy():
    code = _code(ROOT / "scripts" / "bridge_daemon" / "sources" / "critical.py")
    assert "from scripts.bridge_daemon._shapes import" in code
    assert "def _entry_ts(" not in code, (
        "the module kept its own copy of the guard _shapes exists to hold")


# ============================================================
# 5. a fractional hour read as five
# ============================================================

@pytest.mark.parametrize("text,minutes", [
    ("1.5h", 90), ("0.5h", 30), ("1h30m", 90), ("1h 30m", 90),
    # The minute branch carries the same lookbehind and needs the same
    # proof: without it `(\\d+)m` takes the `25m` out of `1.25m`.
    ("1.25m", 1),
    ("90m", 90), ("2h", 120), ("", 30), ("no duration", 30),
])
def test_a_duration_is_read_as_written(text, minutes):
    from scripts.bridge_daemon.sources.pulse import _parse_duration_minutes
    assert _parse_duration_minutes(text) == minutes


# MEASURED 2026-09-01: deleting EITHER lookbehind left all 50 tests in this file
# green. The table above cannot see them, because adding `(?:\.\d+)?` to the
# number is on its own enough to fix `1.5h` - the match starts at the `1` and
# swallows the `.5`, lookbehind or not - and every other row is an integer. The
# source check below could not see them either; see its own docstring.
#
# These are the inputs where the two spellings actually disagree, each verified
# against both versions of the regex before being written down.
@pytest.mark.parametrize("text,minutes,without", [
    # A leading-dot decimal. Five hours for a half-hour meeting, which is
    # verbatim the symptom section 5 is named for, reached by another spelling.
    (".5h", 30, 300),
    (".75h", 30, 4500),
    # Two dots: the match restarts inside the second decimal.
    ("1.5.5h", 30, 330),
    # The minute branch, which had no distinguishing case at all.
    (".5m", 30, 5),
    ("x.5m", 30, 5),
])
def test_a_match_cannot_start_in_the_middle_of_a_number(text, minutes, without):
    from scripts.bridge_daemon.sources.pulse import _parse_duration_minutes
    assert _parse_duration_minutes(text) == minutes, (
        f"{text!r} read as {_parse_duration_minutes(text)} minutes; without the "
        f"lookbehind it reads as {without}")


def test_the_hour_match_cannot_start_inside_a_decimal():
    """BOTH lookbeholds, counted.

    This asserted `r"(?<![\\d.])" in src`, and the token appears twice - once on
    the hour pattern and once on the minute pattern. Deleting either one left
    the other in the file and this check green, which is why two mutations
    survived it on 2026-09-01. A substring test over a file cannot tell one
    occurrence from two.
    """
    from scripts.bridge_daemon.sources import pulse
    src = _code(Path(pulse.__file__))
    guarded = re.findall(r"\(\?<!\[\\d\.\]\)\(\\d\+\(\?:\\\.\\d\+\)\?\)([hm])", src)
    assert sorted(guarded) == ["h", "m"], (
        f"the hour and minute patterns must each carry the lookbehind; "
        f"found it on {sorted(guarded)}")


# ============================================================
# 6. drift reported against a table that is not there
# ============================================================

def test_a_pipeline_with_no_summary_rows_reports_no_drift(tmp_path):
    from scripts.bridge_daemon.sources.pulse import pipeline_summary_stated
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text("## Active Deals\n\nno summary table here\n",
                                     encoding="utf-8")
    assert pipeline_summary_stated(tmp_path) == {}, (
        "a missing summary row was reported as a stated figure of 0, which the "
        "drift comparison then called a disagreement")


def test_a_summary_row_that_says_zero_is_still_a_stated_figure(tmp_path):
    from scripts.bridge_daemon.sources.pulse import pipeline_summary_stated
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text("| Total active deals | 0 |\n", encoding="utf-8")
    assert pipeline_summary_stated(tmp_path) == {"deals": 0}, (
        "absent and zero collapsed back into one answer")


def test_the_assembled_payload_reports_no_drift_without_a_summary(tmp_path):
    """The call site, not just the helper.

    `pipeline_summary_stated` can be right while `pulse_data` still consults
    the flattened `pipeline_value_and_deals`, which is exactly the shape the
    defect had. This asserts the payload the browser receives.
    """
    from scripts.bridge_daemon.sources.pulse import pulse_data
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text(
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Acme | AE | Lead | $1,000,000 | 2026-08-01 | MH | call | 2026-09-01 |\n",
        encoding="utf-8")
    payload = pulse_data(tmp_path)
    assert payload["kpi"]["pipeline_summary_drift"] == {}, (
        "a pipeline.md with no summary table was reported as disagreeing "
        "with its own deal rows")


def test_a_summary_that_really_disagrees_is_still_reported(tmp_path):
    """Anchor: reporting nothing ever would pass the test above."""
    from scripts.bridge_daemon.sources.pulse import pulse_data
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text(
        "| Total active deals | 29 |\n\n"
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| Acme | AE | Lead | $1,000,000 | 2026-08-01 | MH | call | 2026-09-01 |\n",
        encoding="utf-8")
    drift = pulse_data(tmp_path)["kpi"]["pipeline_summary_drift"]
    assert drift["deals"] == {"stated": 29, "actual": 1}
    assert "value" not in drift, "a row that is absent was reported as drifted"


def test_the_tuple_reader_still_answers_for_its_callers(tmp_path):
    from scripts.bridge_daemon.sources.pulse import pipeline_value_and_deals
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text(
        "| Total pipeline value (priced) | $11,000,000 |\n"
        "| Total active deals | 28 |\n", encoding="utf-8")
    assert pipeline_value_and_deals(tmp_path) == (11_000_000, 28)


# ============================================================
# 7. a count taken after the cap
# ============================================================

def test_the_day_count_is_the_days_count_not_the_page_size(tmp_path, monkeypatch):
    from scripts.bridge_daemon.sources import pulse
    from scripts.bridge_daemon.sources.inbox import DISMISS_LOG_FILE

    cap = pulse.TODAY_ACTIVITY_ENTRY_CAP
    today = "2026-08-24"
    log = tmp_path / DISMISS_LOG_FILE
    log.parent.mkdir(parents=True)
    log.write_text("".join(
        json.dumps({"conv_id": f"c{i}", "date": today,
                    "ts": f"2026-08-24T{i:02d}:00:00Z"}) + "\n"
        for i in range(cap + 5)), encoding="utf-8")

    from datetime import date
    out = pulse.today_activity(tmp_path, today=date(2026, 8, 24))
    assert out["inbox_dismissed"] == cap + 5, (
        "the recap reported the capped list length as the day's total")
    assert len(out["entries"]["inbox_dismissed"]) == cap, (
        "the payload cap stopped working while the count was fixed")
    assert out["total"] == cap + 5


# ============================================================
# 8. a flag that needed a terminal the daemon does not have
# ============================================================

TERMINAL = ROOT / "scripts" / "bridge_daemon" / "terminal.py"


def test_the_launcher_does_not_ask_tmux_to_attach():
    from scripts.bridge_daemon.terminal import build_tmux_command
    cmd = build_tmux_command("bond", "t", "/tmp", "osint", None)  # noqa: S108
    assert "-A" not in cmd, (
        "-A makes new-session behave like attach-session when the session "
        "exists, and attaching needs a controlling terminal")
    assert "-d" in cmd and "new-session" in cmd


@pytest.mark.skipif(not __import__("shutil").which("tmux"),
                    reason="tmux not installed")
def test_tmux_really_does_refuse_the_second_attach_without_a_tty():
    """The measurement behind the fix, not a restatement of it.

    Runs the OLD command shape twice with no controlling terminal. If a future
    tmux makes `-A -d` idempotent, this test fails and the fix can be revisited
    on evidence rather than on this comment.
    """
    session = "31c-selftest-probe"
    subprocess.run(["tmux", "kill-session", "-t", session],
                   capture_output=True, check=False)
    old_shape = ["setsid", "--wait", "tmux", "new-session", "-A", "-d",
                 "-s", session, "-n", "w", "-c", "/tmp", "sleep 30"]  # noqa: S108
    try:
        first = subprocess.run(old_shape, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=20, check=False)
        assert first.returncode == 0, first.stderr
        second = subprocess.run(old_shape, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=20, check=False)
        assert second.returncode != 0, (
            "tmux now tolerates -A -d on an existing session with no tty; the "
            "reason for dropping -A no longer holds")
        assert "not a terminal" in second.stderr
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session],
                       capture_output=True, check=False)


def test_an_existing_session_is_a_success_with_nothing_spawned(monkeypatch):
    from scripts.bridge_daemon import terminal as T
    monkeypatch.setattr(T, "_tmux_has_session", lambda session: True)

    def refuse(*a, **kw):
        raise AssertionError("tmux was spawned for a session that already exists")

    monkeypatch.setattr(T.subprocess, "Popen", refuse)
    T._run_tmux_session(["tmux", "new-session", "-d", "-s", "31c-bond"])


def test_a_lost_create_race_is_not_reported_as_a_failure(monkeypatch):
    from scripts.bridge_daemon import terminal as T
    monkeypatch.setattr(T, "_tmux_has_session", lambda session: False)

    class _Dup:
        returncode = 1

        def communicate(self, timeout=None):
            self.returncode = 1
            return b"", b"duplicate session: 31c-bond"

    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: _Dup())
    T._run_tmux_session(["tmux", "new-session", "-d", "-s", "31c-bond"])


def test_a_real_failure_quotes_tmux_instead_of_guessing(monkeypatch):
    from scripts.bridge_daemon import terminal as T
    monkeypatch.setattr(T, "_tmux_has_session", lambda session: False)

    class _Fail:
        returncode = 1

        def communicate(self, timeout=None):
            self.returncode = 1
            return b"", b"no such file or directory: /gone"

    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **kw: _Fail())
    with pytest.raises(T.TerminalUnavailable) as exc:
        T._run_tmux_session(["tmux", "new-session", "-d", "-s", "31c-bond"])
    assert "no such file or directory: /gone" in str(exc.value)
    assert "stale socket" not in str(exc.value), (
        "the message still asserts a cause it did not observe")


def test_the_prompt_builder_does_not_claim_to_be_ascii():
    from scripts.bridge_daemon.terminal import _build_initial_prompt
    doc = _build_initial_prompt.__doc__
    assert "shell-safe" in doc.lower()
    first_line = doc.splitlines()[0]
    assert "ASCII-safe" not in first_line, (
        "the summary contradicts _safe_for_shell_arg, which keeps non-ASCII "
        "printables deliberately")


# ============================================================
# 9. one CRM record, one roster row
# ============================================================

def test_two_roster_rows_cannot_both_claim_one_contact():
    from scripts.bridge_daemon.sources.tribe import _merge_tribe
    crm = [{"slug": "b-bond", "name": "B Bond", "email": "b@31c.io",
            "role": "tribe", "last_touch": "2026-08-01", "days_since_touch": 23,
            "status": "warm"}]
    roster = [
        {"name": "B Bond", "email": "b@31c.io", "title": "Engineer"},
        {"name": "B  Bond", "email": "", "title": "Engineer (dupe row)"},
    ]
    merged = _merge_tribe(crm, roster)
    assert len(merged) == 2, "a roster row was dropped instead of de-linked"
    slugs = [m["slug"] for m in merged if m["slug"]]
    assert slugs == ["b-bond"], (
        f"one CRM contact was attached to two rows: {slugs}")
    assert merged[1]["last_touch"] is None


def test_a_roster_of_distinct_people_still_links_each_one():
    """Anchor: refusing every second match would pass the test above."""
    from scripts.bridge_daemon.sources.tribe import _merge_tribe
    crm = [
        {"slug": "b-bond", "name": "B Bond", "email": "b@31c.io", "role": "tribe",
         "last_touch": "2026-08-01", "days_since_touch": 23, "status": "warm"},
        {"slug": "q-branch", "name": "Q Branch", "email": "q@31c.io", "role": "tribe",
         "last_touch": "2026-08-10", "days_since_touch": 14, "status": "warm"},
    ]
    roster = [{"name": "B Bond", "email": "b@31c.io"},
              {"name": "Q Branch", "email": "q@31c.io"}]
    merged = _merge_tribe(crm, roster)
    assert [m["slug"] for m in merged] == ["b-bond", "q-branch"]


# ============================================================
# 10. guards that stopped at one of two readers
# ============================================================

def test_the_listing_refuses_an_oversize_source_the_detail_view_refuses(tmp_path):
    from scripts.bridge_daemon.sources import studio
    root = tmp_path / studio.ARTIFACT_ROOT / "posts" / "2026-08-24-big"
    root.mkdir(parents=True)
    (root / "2026-08-24-big.md").write_text(
        "x" * (studio.ARTIFACT_MD_MAX_BYTES + 1), encoding="utf-8")
    assert studio.list_artifacts(tmp_path)["total"] == 0, (
        "the listing read a source the detail view refuses, with no cap")
    assert studio.read_artifact(tmp_path, "post", "2026-08-24-big")["ok"] is False


def test_a_normal_artifact_still_lists(tmp_path):
    """Anchor: a guard that drops everything passes the assertion above."""
    from scripts.bridge_daemon.sources import studio
    root = tmp_path / studio.ARTIFACT_ROOT / "posts" / "2026-08-24-ok"
    root.mkdir(parents=True)
    (root / "2026-08-24-ok.md").write_text(
        "---\ntitle: Ok\n---\n\nbody\n", encoding="utf-8")
    out = studio.list_artifacts(tmp_path)
    assert out["total"] == 1 and out["artifacts"][0]["title"] == "Ok"


def test_the_inflight_reader_promises_no_fallback_it_does_not_have():
    from scripts.bridge_daemon.sources.studio import recent_inflight_items
    doc = recent_inflight_items.__doc__
    assert "REQUIRED" in doc
    with pytest.raises(TypeError):
        recent_inflight_items()


# ============================================================
# 11. docstrings that described a different function
# ============================================================

def test_the_pipeline_payload_has_one_shape_on_every_path(tmp_path):
    from scripts.bridge_daemon.sources.pipeline import list_pipeline
    missing = list_pipeline(tmp_path)
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text("## Active Deals\n", encoding="utf-8")
    present = list_pipeline(tmp_path)
    assert set(missing) == set(present), (
        f"the degraded path is missing {set(present) - set(missing)}")
    assert "touched_total" in missing


def test_the_pipeline_docstring_names_every_key_it_returns(tmp_path):
    from scripts.bridge_daemon.sources.pipeline import list_pipeline
    doc = list_pipeline.__doc__
    for key in list_pipeline(tmp_path):
        assert f'"{key}"' in doc, f"{key} is returned and never documented"


def test_the_touch_log_docstring_names_the_display_name():
    from scripts.bridge_daemon.sources.pipeline import read_touch_log
    assert "company}" in read_touch_log.__doc__.splitlines()[0]


def test_the_search_docstring_lists_every_category_the_code_can_emit():
    from scripts.bridge_daemon.sources import search
    doc = search.search.__doc__
    emitted = set(re.findall(r'categories\["([a-z_]+)"\]', _code(Path(search.__file__))))
    assert emitted, "the detector found no categories; it would pass anything"
    for name in sorted(emitted):
        assert f'"{name}": [...]' in doc, f"{name} is emitted and undocumented"


def test_the_inflight_scanner_docstring_stops_naming_directories():
    """The OPENING claim is what a reader acts on.

    The rest of the docstring names the wrong directories on purpose, to record
    what it used to promise, so this targets the first paragraph alone.
    """
    mod = importlib.import_module("scripts.bridge_daemon.refreshers.inflight")
    opening = mod.__doc__.split("\n\n")[1]
    assert "SCAN_DIRS" in opening
    assert "outputs/" not in opening, (
        f"the opening line hand-lists directories again: {opening!r}")


def test_the_library_skip_is_described_at_the_depth_it_applies():
    src = (ROOT / "scripts" / "bridge_daemon" / "sources" / "library.py").read_text(
        encoding="utf-8")
    head = src[:src.index('"""', 3) + 3]
    assert "SKIP_NAMES" in head and "any depth" in head


def test_the_calendar_pipe_guards_agree():
    from scripts.bridge_daemon.sources import pulse
    counts = set(re.findall(r'count\("\|"\) > (\d+)', _code(Path(pulse.__file__))))
    assert counts == {"5"}, f"the four guards disagree again: {counts}"
