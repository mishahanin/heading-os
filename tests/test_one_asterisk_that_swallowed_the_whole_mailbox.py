"""Shard `scripts-05-p2`: the mail path, where a quiet loss looks like a quiet inbox.

`email-intelligence.py` and `email-sweep.py` are both heavily repaired already;
every function carries a comment naming a defect someone fixed. These are what
was left, and each shares a shape: the failure produces a plausible, empty,
successful-looking result.

  - `_matches_ignore` reduced `*` and `**` to `"" in addr`, true of every
    address. ONE stray asterisk in `sentinel_config.yaml`'s `ignore_patterns`
    filtered the entire mailbox as noise: an empty digest, `noise_filtered`
    counting every message, and no error anywhere. Measured 2026-08-24 against
    three unrelated addresses; all three were ignored.
  - `StateManager._load` quarantines a CORRUPT state file. A file that is valid
    JSON, a valid object, and simply missing `processed_message_ids` sailed past
    it and met `self.data[...]` in `is_processed` as a KeyError on the run's
    first message. `merge_state`, thirty lines above the class, already read
    every one of those keys with `.get(...) or []`.
  - `commit_state` iterated `message_ids` without typing it, and a string is
    iterable: `"message_ids": "abc"` marked `a`, `b` and `c` processed. It never
    raises, and the only symptom of a poisoned dedupe set is mail that is
    silently never re-analysed.
  - Two call-site comments in `run_unread_mode` still described the cache key as
    `message_count` - the exact defect `_cache_key`'s own docstring says it was
    changed to fix. A reader trusting the comments would restore the bug.
  - `build_output` zipped conversations against analyses without `strict=`,
    while `analyze_conversations` in the same file zips with it. A short
    `analyses` list dropped the trailing conversations from the digest silently.

And in the sweep state machine:

  - `failed` was a DEAD END. It is not in `TERMINAL`, so `pending` kept listing
    it as work that is left, and no target accepted it as a source, so nothing
    could move it. A send that failed could be neither retried nor abandoned and
    the resume set never emptied. The file's own comment calls `skip` "the
    intended decline escape hatch"; it did not reach the one state that needs it.
  - `cmd_propose` grew a malformed-entry guard and `_mutate_ids` never did, so
    `approve` met `a["id"]` as a KeyError - a traceback out of the command an
    operator runs to approve a send.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# email-intelligence: the ignore filter must not match everything
# ===========================================================================

@pytest.fixture(scope="module")
def ei():
    return _load("email_intel_shard", "scripts/email-intelligence.py")


_ADDRS = ["ceo@31c.io", "investor@sequoia.com", "wife@gmail.com"]


@pytest.mark.parametrize("pattern", ["*", "**", "***", "****"])
def test_an_all_wildcard_pattern_never_matches(ei, pattern, capsys):
    """One asterisk in the config filtered the whole mailbox as noise."""
    matched = [a for a in _ADDRS if ei._matches_ignore(a, [pattern])]
    assert matched == [], (
        f"pattern {pattern!r} ignored {matched}; every address matches and the "
        "digest comes back empty with nothing reported"
    )
    assert "matches every address" in capsys.readouterr().err, (
        "silently dropping the pattern is the same defect one level quieter"
    )


def test_the_refusal_names_the_pattern_and_the_file(ei, capsys):
    ei._matches_ignore("a@b.c", ["*"])
    err = capsys.readouterr().err
    assert "'*'" in err
    assert "sentinel_config.yaml" in err, "say where to fix it"


def test_a_real_wildcard_pattern_still_matches(ei):
    """Anchor: the guard must not disarm the filter it protects."""
    assert ei._matches_ignore("bot@linkedin.com", ["*@linkedin.com"])
    assert ei._matches_ignore("noreply@anywhere.io", ["noreply@*"])
    assert ei._matches_ignore("weekly-newsletter@x.com", ["*newsletter*"])
    assert ei._matches_ignore("exact@match.com", ["exact@match.com"])


def test_a_real_pattern_does_not_over_match(ei):
    assert not ei._matches_ignore("ceo@31c.io", ["*@linkedin.com"])
    assert not ei._matches_ignore("ceo@31c.io", ["noreply@*"])


def test_an_empty_pattern_is_not_treated_as_a_wildcard(ei, capsys):
    """`""` never matched anything and must keep not matching, without the
    warning: it is not the asterisk mistake, just an empty config row."""
    assert not ei._matches_ignore("a@b.c", [""])
    assert "matches every address" not in capsys.readouterr().err


def test_a_bad_pattern_does_not_disarm_the_good_ones(ei, capsys):
    """`continue`, not `return`: the rest of the list still has to work."""
    assert ei._matches_ignore("bot@linkedin.com", ["*", "*@linkedin.com"])
    assert "matches every address" in capsys.readouterr().err


# --- StateManager: a valid object that is not this schema -------------------

@pytest.fixture()
def state_path(tmp_path):
    return tmp_path / "state.json"


@pytest.mark.parametrize("body", ['{"version": 1}', "{}",
                                  '{"version": 1, "conversations": {}}'])
def test_a_state_missing_its_collections_does_not_raise(ei, state_path, body):
    """It met `self.data["processed_message_ids"]` as a KeyError on the first
    message of the run, past a quarantine built for a CORRUPT file."""
    state_path.write_text(body, encoding="utf-8")
    sm = ei.StateManager(path=state_path)
    assert sm.is_processed("m1") is False
    sm.mark_processed("m1")
    sm.mark_conversation("c1", "topic")
    assert sm.is_processed("m1") is True


def test_the_schema_fill_does_not_replace_a_present_value(ei, state_path):
    """Anchor: only a MISSING key is filled. Overwriting a present one would
    discard real history, which is what the quarantine exists to prevent."""
    state_path.write_text(json.dumps({"processed_message_ids": ["kept"]}),
                          encoding="utf-8")
    sm = ei.StateManager(path=state_path)
    assert sm.data["processed_message_ids"] == ["kept"]
    assert sm.is_processed("kept")


def test_a_corrupt_state_is_still_quarantined(ei, state_path, capsys):
    """Anchor: the schema fill must not swallow the corrupt-file path."""
    state_path.write_text("{ not json", encoding="utf-8")
    ei.StateManager(path=state_path)
    err = capsys.readouterr().err
    assert "unusable" in err
    assert list(state_path.parent.glob("state.json.corrupt-*")), (
        "the damaged file must be kept, not replaced"
    )


def test_a_state_that_is_a_list_is_still_quarantined(ei, state_path, capsys):
    state_path.write_text("[]", encoding="utf-8")
    ei.StateManager(path=state_path)
    assert "not an object" in capsys.readouterr().err


# --- commit_state: an id list is a list -------------------------------------

@pytest.fixture()
def fresh(ei, tmp_path):
    return ei.StateManager(path=tmp_path / "s.json")


def test_a_string_of_ids_is_refused_not_iterated_per_letter(ei, fresh):
    """It marked `a`, `b` and `c` processed and poisoned the dedupe set."""
    with pytest.raises(ValueError, match="message_ids is a str"):
        ei.commit_state(fresh, {"message_ids": "abc", "conversations": []})
    assert fresh.data["processed_message_ids"] == []


def test_a_non_list_conversations_block_is_refused(ei, fresh):
    with pytest.raises(ValueError, match="conversations is a"):
        ei.commit_state(fresh, {"message_ids": [], "conversations": {"id": "x"}})


def test_a_conversation_without_an_id_is_skipped_not_a_keyerror(ei, fresh):
    ei.commit_state(fresh, {"message_ids": [],
                            "conversations": [{}, "junk", {"id": "c1", "topic": "t"}]})
    assert list(fresh.data["conversations"]) == ["c1"]


def test_a_non_string_id_is_not_marked_processed(ei, fresh):
    ei.commit_state(fresh, {"message_ids": ["good", None, 42, ""],
                            "conversations": []})
    assert fresh.data["processed_message_ids"] == ["good"]


def test_a_real_payload_still_commits(ei, fresh):
    """Anchor: the typing must not refuse the shape the script itself builds."""
    ei.commit_state(fresh, {"message_ids": ["m1", "m2"],
                            "conversations": [{"id": "c1", "topic": "Deal"}],
                            "inbox_count": 2, "sent_count": 0,
                            "cutoff": "2026-08-24T00:00:00+00:00",
                            "status": "complete"})
    assert fresh.data["processed_message_ids"] == ["m1", "m2"]
    assert fresh.data["conversations"]["c1"]["topic"] == "Deal"
    assert fresh.data["last_run_status"] == "complete"
    assert fresh.data["stats"]["total_runs"] == 1


def test_a_partial_run_status_is_carried_not_overwritten(ei, fresh):
    """Anchor on the existing fix: the status is the RUN's, not a constant."""
    ei.commit_state(fresh, {"message_ids": [], "conversations": [],
                            "status": "partial"})
    assert fresh.data["last_run_status"] == "partial"


# --- commit_state_from_file: the block is an object -------------------------

def test_a_state_commit_that_is_not_an_object_is_refused(ei, tmp_path):
    """`.get` on a list is an AttributeError, past main's ValueError handler."""
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"state_commit": ["not", "an", "object"]}),
                   encoding="utf-8")
    with pytest.raises(ValueError, match="not an object"):
        ei.commit_state_from_file(run)


def test_a_run_file_that_is_not_an_object_is_refused(ei, tmp_path):
    run = tmp_path / "run.json"
    run.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a run object"):
        ei.commit_state_from_file(run)


def test_a_run_without_the_block_is_still_refused(ei, tmp_path):
    """Anchor: the pre-existing refusal must survive the new ones."""
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"conversations": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no state_commit block"):
        ei.commit_state_from_file(run)


# --- the comments that described the defect ---------------------------------

def test_the_cache_comments_no_longer_name_message_count(ei):
    """`_cache_key`'s docstring says a COUNT is not an identity and that it was
    changed for that reason. Both call sites went on describing the count as the
    design, so the file argued with itself and the wrong side was nearer the
    code a reader would edit."""
    src = (ROOT / "scripts" / "email-intelligence.py").read_text(encoding="utf-8")
    body = src.split("def run_unread_mode", 1)[1]
    assert "same message_count" not in body, (
        "a call-site comment still describes the cache key as the message count"
    )
    assert "SET OF MESSAGE IDS" in body


def test_the_cache_key_is_the_id_set(ei):
    """The behaviour those comments now describe, asserted rather than trusted."""
    a = {"raw_emails": [{"message_id": "m1"}, {"message_id": "m2"}]}
    b = {"raw_emails": [{"message_id": "m2"}, {"message_id": "m1"}]}
    c = {"raw_emails": [{"message_id": "m1"}, {"message_id": "m3"}]}
    assert ei._cache_key(a) == ei._cache_key(b), "order must not matter"
    assert ei._cache_key(a) != ei._cache_key(c), (
        "same COUNT, different mail: this is the case the count could not see"
    )


# --- build_output: no conversation leaves the digest in silence -------------

def _conv(cid: str) -> dict:
    return {"id": cid, "topic": f"topic {cid}", "direction": "incoming",
            "message_count": 1, "participants": [], "latest_datetime": "",
            "is_internal": False, "raw_emails": []}


def test_a_short_analyses_list_is_an_error_not_a_silent_drop(ei):
    """The run reported N processed and the reader saw fewer, with nothing to
    say which were missing."""
    with pytest.raises(ValueError):
        ei.build_output([_conv("a"), _conv("b")], [{"priority": "P1"}], {})


def test_matched_lists_still_build(ei):
    """Anchor: strict must not break the shape the script actually produces."""
    out = ei.build_output([_conv("a"), _conv("b")],
                          [{"priority": "P3"}, {"priority": "P1"}], {})
    assert [c["id"] for c in out["conversations"]] == ["b", "a"], (
        "P1 sorts first"
    )


# ===========================================================================
# email-sweep: a failed action must be resolvable
# ===========================================================================

@pytest.fixture(scope="module")
def sw():
    return _load("email_sweep_shard", "scripts/email-sweep.py")


class _A:
    def __init__(self, **kw):
        self.__dict__.update({"date": "2026-08-24", "note": None, "json": False,
                              **kw})


_PAYLOAD = [{"type": "send_reply", "title": "Reply to X"},
            {"type": "crm_log", "title": "Log the call"}]


@pytest.fixture()
def sweep(sw, tmp_path):
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(_PAYLOAD), encoding="utf-8")
    assert sw.cmd_propose(tmp_path, _A(file=str(payload))) == 0
    return tmp_path


def _status(sw, root, action_id: int) -> str:
    data = sw._load(root, "2026-08-24")
    return next(a for a in data["actions"] if a["id"] == action_id)["status"]


def _fail(sw, root, action_id: int) -> None:
    assert sw.cmd_approve(root, _A(ids=[action_id])) == 0
    assert sw.cmd_set(root, _A(id=action_id, status="failed")) == 0
    assert _status(sw, root, action_id) == "failed"


def test_a_failed_action_can_be_retried(sw, sweep):
    """It was a dead end: no target accepted `failed` as a source, so a send
    that failed could not be tried again."""
    _fail(sw, sweep, 1)
    assert sw.cmd_approve(sweep, _A(ids=[1])) == 0
    assert _status(sw, sweep, 1) == "approved"


def test_a_failed_action_can_be_abandoned(sw, sweep):
    """`skip` is this file's own named 'decline escape hatch' and it did not
    reach the one state an operator needs to escape."""
    _fail(sw, sweep, 1)
    assert sw.cmd_skip(sweep, _A(ids=[1], note="giving up")) == 0
    assert _status(sw, sweep, 1) == "skipped"


def test_a_retried_action_can_reach_done(sw, sweep):
    """The retry has to lead somewhere, or it just moves the dead end."""
    _fail(sw, sweep, 1)
    sw.cmd_approve(sweep, _A(ids=[1]))
    assert sw.cmd_set(sweep, _A(id=1, status="done", note="sent")) == 0
    assert _status(sw, sweep, 1) == "done"


def test_a_failed_action_is_pending_until_someone_resolves_it(sw, sweep, capsys):
    """It must STAY in the resume set. The dead end had two possible fixes and
    only one is right: opening the transitions keeps a failed send visible until
    a human retries or abandons it, while moving `failed` into TERMINAL would
    have emptied `pending` by hiding the failure instead of resolving it."""
    _fail(sw, sweep, 1)
    sw.cmd_skip(sweep, _A(ids=[2], note=None))
    capsys.readouterr()
    assert sw.cmd_pending(sweep, _A()) == 0
    out = capsys.readouterr().out
    assert "1 pending" in out, f"a failed send dropped out of the resume set: {out}"
    assert "[failed]" in out


def test_a_failed_action_leaves_pending_once_resolved(sw, sweep, capsys):
    """And the resume set does empty, once the failure has been dealt with."""
    _fail(sw, sweep, 1)
    sw.cmd_skip(sweep, _A(ids=[1, 2], note=None))
    capsys.readouterr()
    assert sw.cmd_pending(sweep, _A()) == 0
    assert "nothing pending" in capsys.readouterr().out


def test_no_state_became_a_dead_end(sw):
    """The property, not the two edges: every non-terminal status must have
    somewhere to go, or the sweep can strand work again in a new way."""
    stranded = []
    for src in ("proposed", "approved", "executing", "failed"):
        outs = [t for t, froms in sw._ALLOWED_FROM.items()
                if src in froms and t != src]
        if not outs:
            stranded.append(src)
    assert not stranded, f"no transition leaves {stranded}"


def test_a_done_action_stays_done(sw, sweep):
    """Anchor: opening `failed` must not open the terminal states with it."""
    sw.cmd_approve(sweep, _A(ids=[1]))
    sw.cmd_set(sweep, _A(id=1, status="done"))
    assert sw.cmd_approve(sweep, _A(ids=[1])) == 1
    assert _status(sw, sweep, 1) == "done"


def test_a_proposed_action_still_cannot_jump_to_done(sw, sweep):
    """Anchor: the state machine's point is that approval comes first."""
    assert sw.cmd_set(sweep, _A(id=1, status="done")) == 1


# --- the mutate path is shape-guarded like propose --------------------------

def _corrupt(sw, root, actions: list) -> None:
    p = sw._state_path(root, "2026-08-24")
    data = json.loads(p.read_text(encoding="utf-8"))
    data["actions"] = actions
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("entry", [{"title": "no id"}, "a string", None, 42,
                                   {"id": "7"}])
def test_a_malformed_entry_is_refused_not_a_traceback(sw, sweep, entry, capsys):
    """`approve` is the command an operator runs to approve a SEND, and it met
    `a["id"]` as a KeyError. `cmd_propose` grew this guard; this path did not."""
    _corrupt(sw, sweep, [entry])
    assert sw.cmd_approve(sweep, _A(ids=[1])) == 1
    assert "malformed action entr" in capsys.readouterr().err


def test_list_can_still_show_a_file_with_a_malformed_entry(sw, sweep, capsys):
    """`list` is how you find out WHICH entry is broken, and it was the command
    that could not run."""
    _corrupt(sw, sweep, [{"id": 1, "title": "fine", "type": "crm_log",
                          "status": "proposed"}, {"title": "broken"}])
    assert sw.cmd_list(sweep, _A()) == 0
    out = capsys.readouterr().out
    assert "fine" in out
    assert "malformed entry" in out


@pytest.mark.parametrize("entry", [{"title": "broken"}, "a string", None, 42])
def test_pending_counts_a_malformed_entry_as_unresolved(sw, sweep, entry, capsys):
    """Dropping it would let the resume set read empty while the file still
    holds something nobody has dealt with. The NON-DICT cases are the ones that
    matter: a broken dict still answers `.get("status")` with None and stays
    pending by accident, so a guard written as `isinstance(a, dict) and ...`
    looks correct against it and silently drops a string or a null."""
    _corrupt(sw, sweep, [entry])
    assert sw.cmd_pending(sweep, _A()) == 0
    assert "1 pending" in capsys.readouterr().out


def test_a_clean_sweep_still_mutates(sw, sweep):
    """Anchor: the guard must not refuse a well-formed file."""
    assert sw.cmd_approve(sweep, _A(ids=[1, 2])) == 0
    assert _status(sw, sweep, 1) == "approved"


# --- a refused batch says nothing was applied -------------------------------

def test_a_refused_batch_reports_that_nothing_changed(sw, sweep, capsys):
    """`_save` is after the loop, so a mid-batch refusal discards the earlier
    mutations too. Right behaviour, and it was invisible: re-running the whole
    batch is only safe if the first ids did NOT go through."""
    sw.cmd_approve(sweep, _A(ids=[1]))
    sw.cmd_set(sweep, _A(id=1, status="done"))
    capsys.readouterr()
    assert sw.cmd_approve(sweep, _A(ids=[2, 1])) == 1
    assert "nothing was changed" in capsys.readouterr().err
    assert _status(sw, sweep, 2) == "proposed", "and nothing was, in fact"


def test_an_unknown_id_also_reports_that_nothing_changed(sw, sweep, capsys):
    assert sw.cmd_approve(sweep, _A(ids=[1, 99])) == 1
    assert "nothing was changed" in capsys.readouterr().err
    assert _status(sw, sweep, 1) == "proposed"


def test_an_unknown_type_still_floors_at_gated(sw):
    """Anchor on the lethal-trifecta convention this table shares with the
    R3 ledger: unknown means friction-maximal."""
    assert sw._tier_for("something_new") == ("gated", "send-gated")
    assert sw._tier_for("send_reply")[0] == "gated"
