"""Four reads the Stop hook made without checking, the loudest being its own sink.

`.claude/hooks/checkpoint-offer.py` is the Stop hook. Four defects measured in
its own process on 2026-08-31, all four reproduced before anything was changed.

**1. The stall notice never sent, because the target lives only in `.env`.**
`_notify_stall` walked `CHECKPOINT_TELEGRAM_TARGET`, `OPS_RADAR_TELEGRAM_TARGET`
and `ODIN_CADENCE_TELEGRAM_TARGET` straight out of `os.environ` and returned
when none was set. Those names reach `os.environ` only through `load_env()`, and
nothing on this hook's path called it first. MEASURED by importing the hook and
resolving the target the same way it does, without calling it:

    after importing checkpoint-offer.py, targets visible:
      {'CHECKPOINT_TELEGRAM_TARGET': False, 'OPS_RADAR_TELEGRAM_TARGET': False,
       'ODIN_CADENCE_TELEGRAM_TARGET': False}
    _notify_stall would resolve a target: False -> returns early: True
    after load_env():
      {'CHECKPOINT_TELEGRAM_TARGET': False, 'OPS_RADAR_TELEGRAM_TARGET': False,
       'ODIN_CADENCE_TELEGRAM_TARGET': True}
    configured names: ['ODIN_CADENCE_TELEGRAM_TARGET']
    TELEGRAM_NOTIFY_BOT_TOKEN set: True

The third name IS configured and the bot token is set, so the send would
otherwise have gone through. It was invisible because it works BY ACCIDENT on
one path: `_handoff_since` calls `CP.local_now()`, which reaches
`get_default_tz_name()`, which calls `load_env()`. That path needs the mode on,
`used >= hard`, and an unspent bucket, so the notice arrived on exactly the
pause that is not the ordinary one, and the operator who ends a stretch with
`--done` or hits the continuation ceiling at 03:00 got nothing. The docstring
justified the deferred import with "an engine with no Telegram set up never pays
for it", the exact inversion: the target was configured and the code could not
see it.

**2. `_queue_pending` could be poisoned permanently, through the other door.**
`owed` rose on an `enqueue` carrying `/compact` and fell only on a CONTENTLESS
`remove`/`dequeue`. A consuming record CARRYING `/compact` hit the first branch
and continued without paying, so the operator deleting the hook's own queued
`/compact` from the input line left `owed` at 1 for the session. MEASURED on a
synthetic four-record transcript: `_queue_pending` True before the fix, False
after. LATENT, not live: replaying the same accounting over the six newest
transcripts of this project found 49 `/compact` enqueues and ZERO `/compact`
records of any other operation, and the fixed accounting answers identically on
all six.

**3. The hysteresis bucket was the one unvalidated numeric read on the path.**
Four bare `int(state.get("offer_bucket") or ...)` sites, while every sibling
read is defended. MEASURED by calling `_driven_pending` directly:

    offer_bucket='abc'  -> RAISED ValueError: invalid literal for int()
    offer_bucket=[1]    -> RAISED TypeError: int() argument must be a string...
    offer_bucket=''     -> True
    offer_bucket=None   -> True
    offer_bucket='12'   -> True

The raise reaches `main()` uncaught, so one hand-edited value kills the Stop
hook on EVERY turn until the operator finds the file.

**4. The docstring contradicted the code, and the docstring was the wrong one.**
`_context_was_rebuilt` promised "unparseable or missing timestamps answer YES"
while its first branch answers NO on a missing `last_compact_at`. MEASURED, all
three branches:

    no last_compact_at -> False    no previous_at -> True
    unparseable pair   -> True     compact newer  -> True

The CODE is right: no compaction ever happened means nothing was rebuilt. A
maintainer trusting the sentence would have "fixed" the working branch.

**What these tests pin, in both directions.** That the target resolves from a
`.env` and only from there; that a `.env` with no target still sends NOTHING;
that a load failure is logged rather than swallowed; that the poison sequence
clears while a real queued message still halts the run; that a corrupt bucket
degrades to 0 while a valid one is still PARSED (a `_bucket` that always
answered 0 would pass the crash test and break the once-per-bucket
suppression); and that all four timestamp branches behave as the corrected
sentence now says.

**No test here sends anything.** The three target names are removed from the
environment and re-supplied only from a `tmp_path` `.env`; `telegram_notify.notify`
is REPLACED for every call, so the real transport body never runs; and
`TELEGRAM_NOTIFY_BOT_TOKEN` is asserted blank on every send path, which is the
second ring - the real `notify()` returns False on a falsy token before it opens
a socket. A third ring stands behind both and belongs to no test here: the autouse
egress guard in tests/conftest.py refuses any non-loopback `connect` from a test
that does not carry the `network` marker, and nothing in this file carries it.
The guard is never removed to prove it works: the refusal is tested by asking it
to refuse.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OFFER = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"

# The three names `_notify_stall` walks, in its order. Spelled out rather than
# read off the function, for the same reason
# tests/test_telegram_send_containment.py spells them out: a list derived from
# the code under test agrees with it by construction.
STALL_TARGETS = (
    "CHECKPOINT_TELEGRAM_TARGET",
    "OPS_RADAR_TELEGRAM_TARGET",
    "ODIN_CADENCE_TELEGRAM_TARGET",
)

FIXTURE_SINK = "fixture-sink-9911"


def _offer_module():
    spec = importlib.util.spec_from_file_location("offer_own_target_mod", OFFER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["offer_own_target_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


MOD = _offer_module()


@pytest.fixture()
def sealed_notifier(monkeypatch):
    """Every `_notify_stall` call in this file goes through here.

    It replaces `telegram_notify.notify` with a recorder, so the real transport
    body is unreachable, and it blanks the bot token as the second ring: the
    real `notify()` returns False on a falsy token before opening a socket
    (tests/conftest.py records why both rings exist). The recorder is what the
    tests assert on, so a send is measured by its RESOLUTION and never by
    delivery.
    """
    from scripts.utils import telegram_notify

    sent: list[tuple[str, str]] = []

    def _recorder(target, message, **kwargs):
        sent.append((target, message))
        return True

    monkeypatch.setattr(telegram_notify, "notify", _recorder)
    monkeypatch.setenv("TELEGRAM_NOTIFY_BOT_TOKEN", "")
    yield sent
    assert not os.environ.get("TELEGRAM_NOTIFY_BOT_TOKEN", "").strip(), (
        "the bot token became live during the test; the second ring is gone"
    )


def _env_only_in_dotenv(monkeypatch, tmp_path, body: str) -> Path:
    """A workspace root whose `.env` is the ONLY source of the target names.

    `load_env` uses `setdefault`, and tests/conftest.py sets the three names to
    the empty string for the whole session, so they have to be REMOVED here or
    nothing in a fixture file could ever reach `os.environ`. Removing them is
    also what makes the measurement honest: after this, a target the hook
    resolves came from the fixture file and from nowhere else.
    """
    root = tmp_path / "fixture-workspace"
    root.mkdir()
    (root / ".env").write_text(body, encoding="utf-8")
    for name in STALL_TARGETS:
        # Blank first, THEN delete. `delenv(name, raising=False)` on a name that
        # is ALREADY absent records no undo entry at all - pytest's
        # `MonkeyPatch.delitem` returns early before the `_setitem` append - so
        # the `load_env()` these tests exist to drive would CREATE the name from
        # the fixture `.env` and leave it set for the rest of the session.
        # MEASURED 2026-08-31: this helper left CHECKPOINT_TELEGRAM_TARGET and
        # OPS_RADAR_TELEGRAM_TARGET at 'fixture-sink-9911', and
        # tests/test_telegram_send_containment.py failed on both in a full
        # serial run while passing on its own. `setenv` always records, so the
        # pair restores whether the name was present or absent.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("WORKSPACE_ROOT", str(root))
    return root


# ============================================================
# Defect 1 - the notice that could not see its own target
# ============================================================


def test_the_stall_notice_resolves_a_target_that_exists_only_in_dotenv(
    monkeypatch, tmp_path, sealed_notifier
):
    """The whole defect, stated as the one thing the hook must do.

    The target is written to a `tmp_path` `.env` and removed from the
    environment, which is the operator's real situation: `.env` is gitignored
    and nothing on this hook's path had loaded it. Without `load_env()` as the
    first statement of `_notify_stall` the walk sees nothing, the function
    returns, and the recorder stays empty.
    """
    _env_only_in_dotenv(
        monkeypatch, tmp_path, f"CHECKPOINT_TELEGRAM_TARGET={FIXTURE_SINK}\n"
    )

    assert not os.environ.get("CHECKPOINT_TELEGRAM_TARGET"), (
        "the target was already in the environment; this test would then pass "
        "without the hook reading .env at all"
    )

    MOD._notify_stall("the plan is finished: pinned by a test")

    assert sealed_notifier, (
        "no notice was resolved. The target was in .env and nowhere else, which "
        "is exactly the operator's configuration, so a silent return here is "
        "the 03:00 stall he only learns about from the status line at breakfast."
    )
    target, message = sealed_notifier[0]
    assert target == FIXTURE_SINK
    assert "unattended run stopped" in message
    assert "the plan is finished: pinned by a test" in message


def test_a_dotenv_with_no_target_still_sends_nothing(
    monkeypatch, tmp_path, sealed_notifier, capsys
):
    """The other direction, and it is why the guard is tested by refusing.

    Loading `.env` must not become "send somewhere". An engine clone with no
    Telegram configured resolves no target and the function returns, which is
    the behaviour the deferred import was written for and the behaviour the fix
    must leave alone.
    """
    _env_only_in_dotenv(monkeypatch, tmp_path, "SOMETHING_ELSE=1\n")

    MOD._notify_stall("reached the ceiling of 100 continuations")

    assert not sealed_notifier, (
        f"an unconfigured engine resolved a target: {sealed_notifier}"
    )
    assert "not loaded" not in capsys.readouterr().err


def test_a_dotenv_that_cannot_be_loaded_is_logged_never_swallowed(
    monkeypatch, tmp_path, sealed_notifier, capsys
):
    """A bundled clone, or an unreadable file, ends the Stop rather than the turn.

    The handler has to LOG. A silent swallow here would put this function back
    where it started, unable to notify and unable to say why. The raise is
    injected at `scripts.utils.paths.load_env`, the name the deferred import
    resolves at call time, so a hook that never calls it produces no stderr line
    either and this test goes red with the previous one.
    """
    _env_only_in_dotenv(
        monkeypatch, tmp_path, f"CHECKPOINT_TELEGRAM_TARGET={FIXTURE_SINK}\n"
    )

    from scripts.utils import paths as paths_mod

    def _refuse(workspace_root=None):
        raise OSError("fixture: .env unreadable")

    monkeypatch.setattr(paths_mod, "load_env", _refuse)

    MOD._notify_stall("the plan is finished: unreadable environment")

    err = capsys.readouterr().err
    assert "checkpoint-offer: .env not loaded for the stall notice" in err, (
        f"the load failure was swallowed. stderr was: {err!r}"
    )
    assert "fixture: .env unreadable" in err
    assert not sealed_notifier, (
        "a target was resolved although .env never loaded; the walk must see "
        "only what actually reached the environment"
    )


def test_the_pause_path_reaches_the_notice_with_the_stop_reason(
    monkeypatch, tmp_path, sealed_notifier
):
    """End to end from `_pause_unattended`, which is the caller that matters.

    The ordinary stop is a `--done` marker or the continuation ceiling, and both
    arrive here. This drives the real state write as well, so the notice is
    proven to fire on the same call that records `unattended_paused_at` rather
    than on some path a unit call invented.
    """
    _env_only_in_dotenv(
        monkeypatch, tmp_path, f"OPS_RADAR_TELEGRAM_TARGET={FIXTURE_SINK}\n"
    )
    state_path = tmp_path / "checkpoint-session.json"
    state_path.write_text(json.dumps({"session_unattended": True}), encoding="utf-8")

    assert MOD._pause_unattended({}, state_path, "reached the ceiling of 100") == 0

    written = json.loads(state_path.read_text(encoding="utf-8"))
    assert written.get("unattended_stop_reason") == "reached the ceiling of 100"
    assert written.get("unattended_paused_at")
    assert sealed_notifier, (
        "the stretch was recorded as stopped and the operator was never told"
    )
    assert sealed_notifier[0][0] == FIXTURE_SINK


# ============================================================
# Defect 2 - the queue debt that only one door could pay
# ============================================================


def _transcript(tmp_path, records, name="transcript.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


def _queue_record(operation, content=None, session="queue-session"):
    entry = {"type": "queue-operation", "operation": operation,
             "sessionId": session}
    if content is not None:
        entry["content"] = content
    return entry


@pytest.mark.parametrize("consuming", ["remove", "dequeue"])
def test_deleting_our_own_queued_compact_does_not_poison_the_count(
    tmp_path, consuming
):
    """The mirror image of the bug the `owed` ledger was added to fix.

    The operator sees the hook's own `/compact` sitting in the input and deletes
    it. `remove` always carries `content`, so the record lands in the
    `/compact` branch; before 2026-08-31 that branch paid nothing, `owed` stayed
    at 1 for the session, his next real message's contentless `dequeue` was
    charged to the debt instead of to `pending`, and `pending` stuck at 1
    forever. `_queue_pending` then answered True at every pause,
    `_wait_out_the_grace` returned at once, and unattended mode halted in
    silence for the rest of the session.

    Both consuming operations are covered because both can carry the content;
    the parser must not depend on which one the harness happens to write.
    """
    path = _transcript(tmp_path, [
        _queue_record("enqueue", "/compact"),
        _queue_record(consuming, "/compact"),
        _queue_record("enqueue", "a real operator message"),
        _queue_record("dequeue"),
    ])

    assert MOD._queue_pending(path, "queue-session") is False, (
        "the count is poisoned: a consumed /compact left the debt unpaid, so "
        "the operator's consumed message is still counted as waiting and "
        "unattended mode halts at every later pause"
    )


def test_a_message_the_operator_really_typed_still_halts_the_run(tmp_path):
    """The direction that makes the test above a measurement rather than a wish.

    Paying the debt on a consuming record must not make the counter deaf. A
    genuine `enqueue` with nothing consuming it is the expensive case the whole
    function exists for: continuing here talks over an instruction already sent.
    """
    path = _transcript(tmp_path, [
        _queue_record("enqueue", "/compact"),
        _queue_record("remove", "/compact"),
        _queue_record("enqueue", "do this instead"),
    ])

    assert MOD._queue_pending(path, "queue-session") is True, (
        "a queued operator message was read as an empty queue"
    )


def test_our_own_compact_consumed_the_ordinary_way_still_nets_out(tmp_path):
    """The balanced case, unchanged, which is what the real transcripts hold.

    `enqueue` carries `content`, the consuming `dequeue` does not, and the debt
    cancels it. Replayed over the six newest transcripts of this project on
    2026-08-31 the fixed accounting answered identically to the old one, which
    is why this defect is reported as latent.
    """
    path = _transcript(tmp_path, [
        _queue_record("enqueue", "/compact"),
        _queue_record("dequeue"),
    ])

    assert MOD._queue_pending(path, "queue-session") is False


# ============================================================
# Defect 3 - the one unvalidated numeric read
# ============================================================


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("abc", 0),
        ([1], 0),
        ({}, 0),
        ("", 0),
        (None, 0),
        ("12", 12),
        (7, 7),
    ],
    ids=["text", "list", "dict", "empty", "absent", "numeric-string", "int"],
)
def test_the_bucket_read_refuses_to_raise_and_still_parses(raw, expected):
    """Both halves at once, and the second half is the one that binds.

    A `_bucket` that always answered 0 would pass every crash test and silently
    break the once-per-bucket hysteresis, so the valid rows are as load-bearing
    as the corrupt ones. 0 is the fallback because it is what an absent key
    already gave.
    """
    assert MOD._bucket({"offer_bucket": raw}) == expected


def test_the_bucket_read_falls_back_to_current_bucket():
    """The `or` chain the four call sites carried, preserved by the helper."""
    assert MOD._bucket({"current_bucket": 5}) == 5
    assert MOD._bucket({"offer_bucket": 0, "current_bucket": 5}) == 5
    assert MOD._bucket({}) == 0


def test_a_corrupt_bucket_does_not_crash_the_driven_precheck():
    """`_driven_pending` is where the raise was measured, so it is asserted here."""
    state = {"session_unattended": True, "offer_bucket": "abc"}
    assert MOD._driven_pending(state) is True


def test_a_valid_bucket_still_suppresses_a_second_request_for_it():
    """The hysteresis, which a 0-always fallback would have destroyed."""
    state = {
        "session_unattended": True,
        "offer_bucket": "12",
        "compact_requested_bucket": 12,
    }
    assert MOD._driven_pending(state) is False, (
        "the bucket was not parsed as 12, so the same compaction would be "
        "requested again at every pause"
    )


def test_the_stop_hook_survives_a_corrupt_bucket_on_a_real_turn(tmp_path):
    """Driven as a subprocess, because the cost of this defect is per turn.

    An uncaught ValueError out of `_driven_pending` reaches `main()` and the
    process dies, so the checkpoint system is entirely dead - no offer, no
    continuation, no save - and the operator gets a traceback at every pause
    while the state file stays that way.
    """
    from scripts.utils import checkpoint_paths as CP

    session = "cccccccc-1111-2222-3333-444444444444"
    project = tmp_path / "project"
    project.mkdir()
    state_path = CP.state_path(project, CP.safe_slug(session))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"session_unattended": True, "offer_bucket": "abc"}),
        encoding="utf-8",
    )

    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_HANDOFF")}
    env["HEADING_OS_DATA"] = str(tmp_path / "data")
    # `_notify_stall` reads exactly these three, blanked at the seam as the
    # sibling suites do. This path never reaches it; the blanking is the belt.
    for name in STALL_TARGETS:
        env[name] = ""

    result = subprocess.run(
        [sys.executable, str(OFFER)],
        input=json.dumps({
            "session_id": session,
            "cwd": str(project),
            "stop_hook_active": True,
        }),
        capture_output=True, text=True, env=env, cwd=str(project), timeout=120,
    )

    assert result.returncode == 0, (
        f"the Stop hook died on a corrupt bucket. stderr:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "ValueError" not in result.stderr, result.stderr


# ============================================================
# Defect 4 - the docstring that promised the opposite of the code
# ============================================================


@pytest.mark.parametrize(
    "state, previous, expected",
    [
        ({}, "2026-08-31T00:00:00", False),
        ({"last_compact_at": ""}, "2026-08-31T00:00:00", False),
        ({"last_compact_at": "2026-08-31T01:00:00"}, None, True),
        ({"last_compact_at": "nonsense"}, "also-nonsense", True),
        ({"last_compact_at": "2026-08-31T02:00:00"}, "2026-08-31T01:00:00", True),
        ({"last_compact_at": "2026-08-31T01:00:00"}, "2026-08-31T02:00:00", False),
    ],
    ids=["no-compaction", "blank-compaction", "no-previous", "unparseable",
         "compacted-since", "compacted-before"],
)
def test_the_rebuild_question_answers_what_its_docstring_now_says(
    state, previous, expected
):
    """All three branches, so "fixing" the code to match the old prose goes red.

    The correct sentence is: a missing `last_compact_at` answers NO, and a
    missing previous timestamp or an unparseable pair answers YES. No compaction
    ever happening means nothing was rebuilt, which is not a failure case at all
    and does not belong under the asymmetric-cost argument.
    """
    assert MOD._context_was_rebuilt(state, previous) is expected


def test_the_rebuild_docstring_states_the_branch_it_actually_takes():
    """The defect was the prose, so the prose is what this pins.

    A maintainer reads the docstring before the branches. "Unparseable or
    missing timestamps answer YES" describes a function this file has never
    had, and acting on it would have replaced a correct branch with a wrong one.
    """
    doc = MOD._context_was_rebuilt.__doc__ or ""
    assert "A missing `last_compact_at` answers NO" in doc, (
        "the docstring no longer names the branch it takes on a session that "
        "has never compacted"
    )
    assert "Unparseable or missing timestamps answer YES" not in doc, (
        "the over-promising sentence is back: it claims a YES on the one input "
        "the first branch answers NO to"
    )
