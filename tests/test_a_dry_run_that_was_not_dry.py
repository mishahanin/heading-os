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

Run: .venv/bin/python -m pytest tests/test_a_dry_run_that_was_not_dry.py -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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
    -- never retried, never in the digest, never notified."""
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    pre = src.split("# Pre-process:", 1)[1].split("if not items_to_analyze:", 1)[0]
    assert "mark_email_processed" not in pre, pre


def test_the_terminal_path_marks_the_email():
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    body = src.split("self.state.record_digest_item(item, score)", 1)[1][:400]
    assert "_mark_item_processed(item)" in body, body


def test_mark_item_processed_ignores_non_email_sources(tmp_path):
    st = sen.StateManager(state_path=tmp_path / "s.json")
    obj = sen.Sentinel.__new__(sen.Sentinel)
    obj.state = st
    obj._mark_item_processed({"source": "telegram", "message_id": "t-1"})
    assert not st.is_email_processed("t-1")
    obj._mark_item_processed({"source": "email", "message_id": "e-1"})
    assert st.is_email_processed("e-1")


# ============================================================
# 6 - telegram.enabled: false actually disables Telegram
# ============================================================
def test_the_startup_condition_reads_only_the_enabled_flag():
    """`enabled OR not dry_run` is always true in live mode, so connect() ran
    anyway and raised ValueError on absent credentials with no handler."""
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
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
