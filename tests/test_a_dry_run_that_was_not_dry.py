#!/usr/bin/env python3
"""Shard scripts-11-p1: Sentinel, where "test" meant "live".

The headline: `python scripts/sentinel.py --test` was documented as a dry run
and was not one. It muted Telegram and nothing else, so a first "safe" run
against a real Exchange account really accepted meeting invites, really sent
decline replies to real people, and permanently consumed real state -- marking
emails processed and advancing Telegram cursors, so the production daemon went
blind to everything the test had seen, silently.

NOT touched here: the auto-accept / auto-decline POLICY itself. That design is
the operator's, it is frozen, and it is raised with him separately. Nothing
below changes when Sentinel decides to accept or decline; it changes whether a
DRY RUN carries that decision out.

The rest:
  - `analyze_batch` fell off the end returning None on JSON that parsed to a
    bare string or number, and `extend(None)` then killed the daemon.
  - `urgency_score` was trusted to be an int; `{"urgency_score": "8"}` raised
    TypeError at the first comparison and poisoned every later digest.
  - Emails were marked processed BEFORE analysis, so an LLM outage or the
    per-cycle notification cap dropped them forever.
  - `telegram.enabled: false` was ANDed away by `or not dry_run`, so an
    email-only operator could not boot without Telegram credentials.
  - `--stop` verified only that the PID was alive, then SIGKILLed it: after a
    crash and PID reuse that is an unrelated process.
  - A failed escalation notify still marked the invite processed, so the
    highest-value invites could vanish on a transient error.
  - The engine's system prompt hardcoded one operator's name.
  - `setup-fireside-healthchecks.py` read the timezone at module scope with
    .env unloaded -- the exact bug its sibling script documents as measured.

TWO OF THOSE CLAIMS WERE READ OFF THE SOURCE, NOT OFF A RUN, and both were
false as measured. Mutation-tested 2026-09-01 against the full suite (20326
passing tests, 33 pre-existing failures, unchanged by either edit):

  - `elif decision == "accept"` on the auto-accept branch changed to `if`. The
    dry-run guard no longer short-circuits, so `--test` really accepts a real
    invite. `test_the_invite_path_short_circuits_in_dry_run` compares the
    POSITIONS of three strings in the source and cannot see it. SURVIVED.
  - the `continue` deleted from the policy branch's escalation-failure arm, so a
    VIP invite nobody was told about is marked processed and never returns.
    `test_a_failed_escalation_leaves_the_invite_unprocessed` slices 1200
    characters from the FIRST escalation site, forty lines above this one.
    SURVIVED.

Both source-position tests are kept, because they say something the behavioural
tests do not (that the guard is written first, and that the arm reads as one
piece), and both mutations are now killed by the runs added beside them.

Run: .venv/bin/python -m pytest tests/test_a_dry_run_that_was_not_dry.py -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.code_only import strip_comments  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sen = _load("sentinel_p11a", "scripts/sentinel.py")
sfh = _load("setup_fireside_hc_p11a", "scripts/setup-fireside-healthchecks.py")


# ============================================================
# 1 - a dry run writes no state
# ============================================================
def test_a_read_only_state_manager_never_writes(tmp_path):
    path = tmp_path / "state.json"
    st = sen.StateManager(state_path=path, read_only=True)
    st.mark_email_processed("msg-1")
    st.save()
    assert not path.exists(), "a dry run persisted state"
    # The in-memory mark still stands, which is what stops one cycle
    # double-processing the same item.
    assert st.is_email_processed("msg-1")


def test_a_normal_state_manager_still_writes(tmp_path):
    path = tmp_path / "state.json"
    st = sen.StateManager(state_path=path)
    st.mark_email_processed("msg-1")
    st.save()
    assert path.exists()
    assert sen.StateManager(state_path=path).is_email_processed("msg-1")


def test_the_dry_run_state_is_wired_to_the_flag():
    """Pinned at the seam: the constructor must receive the flag, or the guard
    above is unreachable in the only place it matters."""
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    assert "StateManager(read_only=dry_run)" in src


# ============================================================
# 2 - a dry run takes no calendar action
# ============================================================
def test_the_invite_path_short_circuits_in_dry_run():
    """The accept/decline calls had no dry_run guard at all: `--test` accepted
    invites and sent decline replies for real."""
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    body = src.split("# Execute decision", 1)[1].split("self.state.mark_invite_processed", 1)[0]
    guard = body.index('if self.dry_run and decision in ("accept", "decline"):')
    accept = body.index("self.invite_source.accept_invite")
    decline = body.index("self.invite_source.decline_invite")
    assert guard < accept and guard < decline, "the dry-run guard is not first"


class _InviteSource:
    """The Exchange seam, recording instead of acting."""

    def __init__(self, invites):
        self._invites = invites
        self.accepted = []
        self.declined = []

    def check_new_invites(self):
        return self._invites

    def get_existing_events(self, start, end):
        return []

    def accept_invite(self, item):
        self.accepted.append(item)

    def decline_invite(self, item, message):
        self.declined.append((item, message))


class _Policy:
    def __init__(self, decision, raise_on=None):
        self.decision = decision

    def evaluate(self, invite, existing_events):
        return {"decision": self.decision, "reasons": ["a reason"],
                "is_tribe": False, "proposed_alternative": None}


class _Config:
    def __init__(self, **calendar):
        from zoneinfo import ZoneInfo
        self.timezone = ZoneInfo("UTC")
        self.calendar = {"auto_accept": True, "auto_decline": True, **calendar}


def _invite(invite_id="inv-1", recurring=False):
    return {"invite_id": invite_id, "subject": "Quarterly review",
            "item": object(), "is_recurring": recurring,
            "sender": "J Bond", "sender_email": "j.bond@universal-exports.invalid",
            "start": "2026-08-24T10:00", "end": "2026-08-24T11:00",
            "duration_minutes": 60, "location": ""}


def _sentinel(tmp_path, *, dry_run, decision, invites=None, escalates=True,
              recurring=False):
    """A Sentinel wired to stubs, with the two notify seams shadowed.

    `_escalate_invite` and `_notify_invite_decision` are replaced per instance
    rather than through the notifier, because what these tests measure is what
    reaches the CALENDAR and what reaches STATE, and both notify paths have
    their own cases further down this file.
    """
    import logging

    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.dry_run = dry_run
    obj.logger = logging.getLogger("sentinel-invite-loop-test")
    obj.config = _Config()
    obj.policy_engine = _Policy(decision)
    obj.invite_source = _InviteSource(
        invites if invites is not None else [_invite(recurring=recurring)])
    obj.state = sen.StateManager(state_path=tmp_path / "state.json")
    obj.escalations = []

    async def _escalate(invite, reasons):
        obj.escalations.append((invite["invite_id"], list(reasons)))
        return escalates

    async def _notify(invite, label, reasons, alternative=None):
        return None

    obj._escalate_invite = _escalate
    obj._notify_invite_decision = _notify
    return obj


def _processed(obj) -> list[str]:
    return obj.state.data.get("calendar", {}).get("processed_invite_ids", [])


@pytest.mark.parametrize("decision", ["accept", "decline"])
def test_a_dry_run_never_reaches_the_calendar(tmp_path, decision):
    """The headline defect, driven instead of grepped.

    Its predecessor read the source and asserted the dry-run guard's TEXT sat
    before the two calendar calls' TEXT. Measured 2026-09-01: changing the
    `elif` on the accept branch to a plain `if` leaves that ordering untouched,
    breaks the short circuit, and makes `--test` accept a real invite for real
    -- and the whole suite (20326 passing tests at that revision) stayed green.
    A claim about what a run DOES has to be measured by running it.
    """
    obj = _sentinel(tmp_path, dry_run=True, decision=decision)

    import asyncio
    asyncio.run(obj._process_meeting_invites())

    assert obj.invite_source.accepted == [], "a dry run accepted a real invite"
    assert obj.invite_source.declined == [], "a dry run sent a real decline"
    assert obj.escalations, "the dry run told nobody either"
    assert _processed(obj) == ["inv-1"]


@pytest.mark.parametrize("decision, attr", [("accept", "accepted"),
                                            ("decline", "declined")])
def test_a_live_run_still_reaches_the_calendar(tmp_path, decision, attr):
    """The other jaw. A guard that short-circuited everything would pass above
    while disabling the daemon's whole purpose."""
    obj = _sentinel(tmp_path, dry_run=False, decision=decision)

    import asyncio
    asyncio.run(obj._process_meeting_invites())

    assert len(getattr(obj.invite_source, attr)) == 1
    assert obj.escalations == [], "a clean auto-action must not escalate"
    assert _processed(obj) == ["inv-1"]


@pytest.mark.parametrize("dry_run, decision, recurring, why", [
    (False, "escalate", False, "the policy route"),
    (True, "accept", False, "the dry-run route"),
    (False, "accept", True, "the recurring route"),
])
def test_an_undelivered_escalation_leaves_the_invite_for_the_next_cycle(
        tmp_path, dry_run, decision, recurring, why):
    """Every route, because the fix reached them one at a time.

    Its predecessor sliced the source from the FIRST `escalated = await
    self._escalate_invite` and searched 1200 characters for `if not escalated:`
    and `continue`. That window covers the recurring branch and stops roughly
    forty lines above the policy branch, so deleting the policy branch's
    `continue` -- which marks an invite processed after nobody was told about it,
    the exact loss this section is named for -- survived the entire suite on
    2026-09-01.
    """
    obj = _sentinel(tmp_path, dry_run=dry_run, decision=decision,
                    escalates=False, recurring=recurring)

    import asyncio
    asyncio.run(obj._process_meeting_invites())

    assert obj.escalations, f"{why}: nothing tried to escalate"
    assert _processed(obj) == [], (
        f"{why}: the invite was consumed after a failed escalation and will "
        f"never be seen again")


def test_a_delivered_escalation_does_consume_the_invite(tmp_path):
    """The anchor for the three rows above: leaving EVERY invite unprocessed
    would satisfy them and re-escalate the same meeting forever."""
    obj = _sentinel(tmp_path, dry_run=False, decision="escalate", escalates=True)

    import asyncio
    asyncio.run(obj._process_meeting_invites())

    assert obj.escalations
    assert _processed(obj) == ["inv-1"]


def test_every_failed_escalation_site_hands_back_to_the_loop():
    """Derived, so a sixth call site is covered the day it is written.

    The parametrised rows above reach five call sites through five routes; this
    asks the AST the same question of all of them at once. `continue` is what
    skips `mark_invite_processed`, so a site that logs and falls through has
    logged the invite away.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8"))
    sites, bad = 0, []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
                continue
            fn = stmt.value.func
            if not (isinstance(fn, ast.Attribute)
                    and fn.attr == "_unprocessed_after_failed_escalation"):
                continue
            sites += 1
            following = body[i + 1] if i + 1 < len(body) else None
            if not isinstance(following, ast.Continue):
                bad.append(stmt.lineno)
    assert sites >= 5, f"only {sites} escalation-failure sites found"
    assert bad == [], (
        f"lines {bad} log an undelivered escalation and then fall through to "
        f"mark_invite_processed, which is the loss the log line describes")


def test_the_help_text_no_longer_promises_only_muted_notifications():
    """Rendered, not grepped: the string is split across source lines, and the
    thing that must be true is what the operator READS."""
    import subprocess
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sentinel.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60).stdout
    flat = " ".join(out.split())
    assert "neither accepted nor declined" in flat, flat
    assert "state is read but never written back" in flat, flat


# ============================================================
# 3 - a malformed model response does not kill the daemon
# ============================================================
class _StubAnalyzer(sen.UrgencyAnalyzer):
    def __init__(self, payload):
        self.payload = payload
        self.model = "stub"
        self.max_tokens = 100
        self.logger = __import__("logging").getLogger("stub")
        self.client = None
        self.business_context = ""
        self.operator_name = "Test Operator"
        self.individual_calls = 0

    def analyze(self, item):
        self.individual_calls += 1
        return {"urgency_score": 5, "reason": "fallback", "summary": "",
                "recommended_action": ""}

    def _get_client(self):
        return object()  # never used: the transport is stubbed out below


@pytest.mark.parametrize("payload", ['"just a string"', "42", "true", "null"])
def test_a_scalar_json_response_falls_back_instead_of_returning_none(
        payload, monkeypatch):
    """Neither a list nor a dict: the function used to fall off the end, and
    `all_analyses.extend(None)` raised TypeError with nothing catching it."""
    a = _StubAnalyzer(payload)

    class _Resp:
        text = payload

    monkeypatch.setattr(sen, "call_anthropic_with_fallback",
                        lambda **kwargs: _Resp())
    out = a.analyze_batch([{"subject": "x"}, {"subject": "y"}])
    assert out is not None
    assert isinstance(out, list) and len(out) == 2


# ============================================================
# 4 - an urgency score is always a usable 1-10 int
# ============================================================
@pytest.mark.parametrize("raw,expected", [
    (8, 8),
    ("8", 8),
    (8.7, 8),
    (99, 10),
    (-3, 1),
    (None, 5),
    ("high", 5),
    ({}, 5),
])
def test_the_score_is_coerced_and_clamped(raw, expected):
    assert sen.UrgencyAnalyzer._clamp_score(raw) == expected


# ============================================================
# 5 - an email is marked only after a terminal outcome
# ============================================================
def test_the_pre_pass_no_longer_consumes_emails():
    """Marking before analysis meant an LLM outage dropped the message for good
    -- never retried, never in the digest, never notified.

    The pre-pass marks in exactly ONE place: the already-notified branch, where
    the content has already reached the operator and there is nothing left to
    do with it. Everything else must reach a terminal outcome first, so the
    state writer must not be called directly here.
    """
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    pre = src.split("# Pre-process:", 1)[1].split("if not items_to_analyze:", 1)[0]
    assert "mark_email_processed" not in pre, pre
    assert "set_telegram_last_id" not in pre, pre
    assert pre.count("_mark_item_processed(item)") == 1, pre


def test_a_duplicate_is_marked_so_it_stops_coming_back():
    """The dedup branch is a terminal outcome and must say so in state.

    An unmarked duplicate is re-fetched and re-hashed every cycle until the
    hash ages out, and Telegram - whose memory is a per-chat cursor rather than
    a per-message id - never advances past it at all, so the dialog and every
    message behind the duplicate are re-read forever.
    """
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    branch = src.split("if self.state.is_already_notified(content_hash):", 1)[1]
    branch = branch.split("continue", 1)[0]
    assert "_mark_item_processed(item)" in branch, branch


def test_the_terminal_path_marks_the_email():
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    body = src.split("self.state.record_digest_item(item, score)", 1)[1][:400]
    assert "_mark_item_processed(item)" in body, body


def test_mark_item_processed_writes_each_source_its_own_memory(tmp_path):
    """One writer, two memories: an id set for email, a cursor for Telegram.

    This asserted that a telegram item was IGNORED here, which was true only
    because Telegram advanced its cursor inside the fetch instead - the same
    consume-before-analysis defect the email path above was fixed for. A
    telegram item now carries `cursor_id` and is written here like its twin.
    """
    st = sen.StateManager(state_path=tmp_path / "s.json")
    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.state = st

    # An email id never lands in the telegram cursor map, and vice versa.
    obj._mark_item_processed({"source": "telegram", "chat_id": "7",
                              "chat_name": "James", "cursor_id": 105})
    assert not st.is_email_processed("t-1")
    assert st.get_telegram_last_id("7") == 105

    obj._mark_item_processed({"source": "email", "message_id": "e-1"})
    assert st.is_email_processed("e-1")

    # A source this function does not know writes nothing at all, rather than
    # landing in whichever branch happens to be last.
    obj._mark_item_processed({"source": "signal", "chat_id": "7",
                              "cursor_id": 999, "message_id": "s-1"})
    assert st.get_telegram_last_id("7") == 105
    assert not st.is_email_processed("s-1")


def test_a_telegram_item_without_a_cursor_writes_nothing(tmp_path):
    """Vacuity guard on the `and item.get("cursor_id")` half.

    A telegram item that never carried a cursor must not write one: a missing
    key read as 0 would rewind the chat to the beginning of its history and
    re-report every message in it.
    """
    st = sen.StateManager(state_path=tmp_path / "s.json")
    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.state = st
    st.set_telegram_last_id("7", "James", 105)

    obj._mark_item_processed({"source": "telegram", "chat_id": "7"})
    assert st.get_telegram_last_id("7") == 105


# ============================================================
# 6 - telegram.enabled: false actually disables Telegram
# ============================================================
def test_the_startup_condition_reads_only_the_enabled_flag():
    """`enabled OR not dry_run` is always true in live mode, so connect() ran
    anyway and raised ValueError on absent credentials with no handler."""
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    code = strip_comments(src)
    assert 'if self.config.telegram.get("enabled", True) or not self.dry_run:' not in code
    assert 'if self.config.telegram.get("enabled", True):' in code


# ============================================================
# 7 - --stop kills this daemon, or nothing
# ============================================================
def test_a_live_pid_that_is_not_sentinel_is_refused():
    """Liveness is not identity. After a crash the PID is reused, and SIGKILL on
    the number in a stale file destroys whatever inherited it."""
    import os
    assert sen._pid_is_sentinel(os.getpid()) is False


def test_an_unknown_pid_is_refused_rather_than_assumed():
    assert sen._pid_is_sentinel(999999) is False


def test_a_corrupt_pid_file_reads_as_none(tmp_path, monkeypatch):
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    assert sen._read_pid_file() is None
    pid_file.write_text("not-a-number\n", encoding="utf-8")
    assert sen._read_pid_file() is None
    pid_file.write_text("4242\n", encoding="utf-8")
    assert sen._read_pid_file() == 4242


def test_a_pid_file_of_undecodable_bytes_also_reads_as_none(tmp_path, monkeypatch):
    """`except OSError` does not catch `UnicodeDecodeError`, which is a ValueError.

    The row above covers a file whose TEXT is not a number. This covers a file
    that is not text, which is the residue this reader was written for: a crash
    mid-write leaves half a byte sequence, `read_text(encoding="utf-8")` refuses
    it, and the exception went straight past the handler into all three CLI
    paths as the traceback this function exists to remove. Measured 2026-09-01.
    """
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_bytes(b"\xff\xfe42")
    with pytest.raises(UnicodeDecodeError):
        pid_file.read_text(encoding="utf-8")
    monkeypatch.setattr(sen, "PID_FILE", pid_file)
    assert sen._read_pid_file() is None


def test_no_cli_path_parses_the_pid_file_by_hand():
    import ast
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    # Parsed, not grepped. The fix's own docstring quotes the expression it
    # removed, so every text search finds its own tombstone and passes forever.
    tree = ast.parse(src)
    offenders = [
        ast.unparse(node) for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "int"
        and "PID_FILE" in ast.unparse(node)
    ]
    assert offenders == [], (
        f"a CLI path still parses the PID file directly ({offenders}); a "
        f"truncated file tracebacks out of main again")


# ============================================================
# 8 - an undelivered escalation is retried
# ============================================================
def test_a_failed_escalation_leaves_the_invite_unprocessed():
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    body = src.split("escalated = await self._escalate_invite", 1)[1][:1200]
    assert "if not escalated:" in body
    assert "continue" in body


def test_escalate_invite_reports_delivery():
    import inspect
    sig = inspect.signature(sen.Sentinel._escalate_invite)
    assert sig.return_annotation is bool or sig.return_annotation == "bool", sig


# ============================================================
# 9 - the engine prompt belongs to whoever runs it
# ============================================================
def test_the_system_prompt_names_no_hardcoded_operator():
    prompt = sen.UrgencyAnalyzer.SYSTEM_PROMPT
    assert "{operator}" in prompt
    assert "Misha" not in prompt, prompt[:400]


def test_the_prompt_renders_with_a_substituted_operator():
    out = sen.UrgencyAnalyzer.SYSTEM_PROMPT.format(
        business_context="CTX", operator="James Bond")
    assert "James Bond" in out
    assert "{operator}" not in out


# ============================================================
# 10 - the fireside checks read the zone after .env is loaded
# ============================================================
def test_the_checks_are_built_at_call_time_not_import_time():
    """A module-scope `get_default_tz_name()` ran with .env unloaded and
    registered every cron check in UTC while the daemon fired at local time --
    the failure the sibling script documents and forbids repeating."""
    assert hasattr(sfh, "build_checks")
    assert not hasattr(sfh, "CHECKS"), "the module-scope list is back"


def test_every_cron_check_carries_a_zone():
    checks = sfh.build_checks()
    assert checks
    zoned = [c for c in checks if "schedule" in c or "tz" in c]
    assert zoned, checks
    for c in zoned:
        assert c.get("tz"), c


def test_main_loads_env_before_building_the_checks():
    src = (ROOT / "scripts" / "setup-fireside-healthchecks.py").read_text(encoding="utf-8")
    body = src.split("def main(", 1)[1]
    assert body.index("load_env(") < body.index("build_checks()"), body


def test_a_delivered_escalation_reports_true():
    """The annotation is not the behaviour: stripping both `return` statements
    made every escalation read as undelivered, so every invite was retried
    forever. This exercises the value."""
    import asyncio
    import logging

    class _Notifier:
        def __init__(self):
            self.sent = []

        async def send_digest(self, msg):
            self.sent.append(msg)

    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.notifier = _Notifier()
    obj.logger = logging.getLogger("t")
    invite = {"sender": "A", "sender_email": "a@example.com", "subject": "S",
              "start": "2026-08-24T10:00", "end": "2026-08-24T11:00",
              "duration_minutes": 60, "location": ""}
    assert asyncio.run(obj._escalate_invite(invite, ["reason"])) is True
    assert obj.notifier.sent


def test_a_failed_escalation_reports_false():
    import asyncio
    import logging

    class _Broken:
        async def send_digest(self, msg):
            raise RuntimeError("telegram down")

    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.notifier = _Broken()
    obj.logger = logging.getLogger("t")
    invite = {"sender": "A", "sender_email": "a@example.com", "subject": "S",
              "start": "", "end": "", "duration_minutes": 30, "location": ""}
    assert asyncio.run(obj._escalate_invite(invite, ["reason"])) is False


def test_no_notifier_at_all_does_not_block_the_invite_forever(caplog):
    """A missing notifier is permanent, not transient. Returning False for it
    would retry the same invite on every cycle and never clear it."""
    import asyncio
    import logging

    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.notifier = None
    obj.logger = logging.getLogger("sentinel-test-nonotifier")
    with caplog.at_level(logging.WARNING, logger="sentinel-test-nonotifier"):
        assert asyncio.run(obj._escalate_invite({"subject": "S"}, ["r"])) is True
    assert "NOBODY WAS TOLD" in caplog.text
