"""A mail run that quietly drops mail is worse than one that fails.

Shard `scripts-05-p3` of the 2026-08-23/24 engine audit found the same shape in
five scripts: a run that could not do the whole job produced output that looked
exactly like a run that had. A folder that failed to fetch reported zero
messages. A fetch capped at 100 said nothing about message 101. A corrupt state
file was replaced by an empty one. An eval whose `--skill` was a typo exited 0.

Each test below pins one of those, by the behaviour a caller can observe -- not
by the shape of the code that produces it.

Findings covered here (numbering from `/tmp/audit_out3/scripts-05-p3.md`):

  1  fetch cap was invisible              9  cache keyed on count alone
  2  yaml NameError on a machine         10  unread feed never mkdir'd
     without PyYAML                      11  unread feed written non-atomically
  3  corrupt state silently replaced     12  undo scanned 200 and said "ok"
  4  two runs, last write wins           13  inbox fetch error swallowed
  5  `all([])` classified as internal    14  sent fetch error swallowed
  6  prose before `{` failed to parse    15  --inbox-only --sent-only accepted
  7  prose before `[` failed to parse    16  REFUTED -- see the test
  8  `["not an object"]` crashed later   17-20  email-sweep state handling
 22-24 eval-flag traversal, id and tmp collisions
 25-31 eval-outcomes shape, traversal, false green, timeout, tmp
 32-33 eval-query-set --json exit code, query timeout
 34    exchange-task's required group contradicted its own docs
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    """Import a kebab-case script under a python-legal module name.

    Registered in `sys.modules` BEFORE exec: a module using `@dataclass` (or
    anything else that looks itself up during class creation) fails otherwise.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ei = _load("email_intelligence_p5c", "scripts/email-intelligence.py")
sweep = _load("email_sweep_p5c", "scripts/email-sweep.py")
evflag = _load("eval_flag_p5c", "scripts/eval-flag.py")
evout = _load("eval_outcomes_p5c", "scripts/eval-outcomes.py")
evqs = _load("eval_query_set_p5c", "scripts/eval-query-set.py")
extask = _load("exchange_task_p5c", "scripts/exchange-task.py")


# ============================================================
# A fake Exchange, so the fetch path is exercised without a mailbox
# ============================================================

class _Addr:
    def __init__(self, email, name=None):
        self.email_address = email
        self.name = name or email


class _ConvId:
    def __init__(self, cid):
        self.id = cid


class _Item:
    def __init__(self, n, conv="conv-1", is_read=False, sender="them@example.com"):
        self.message_id = f"<m{n}@example.com>"
        self.id = f"item-{n}"
        self.conversation_id = _ConvId(conv)
        self.conversation_topic = "Topic"
        self.subject = f"Subject {n}"
        self.sender = _Addr(sender, "Them")
        self.to_recipients = [_Addr("misha.hanin@31c.io", "Misha")]
        self.cc_recipients = []
        self.text_body = f"body {n}"
        self.body = None
        self.datetime_received = None
        self.datetime_sent = None
        self.in_reply_to = ""
        self.item_class = "IPM.Note"
        self.importance = "Normal"
        self.has_attachments = False
        self.is_read = is_read
        self.saved_fields = []

    def save(self, update_fields=None):
        self.saved_fields.append(tuple(update_fields or ()))


class _QuerySet:
    """Enough of an exchangelib QuerySet for fetch_emails and the read flip."""

    def __init__(self, items):
        self._items = list(items)

    def filter(self, **kw):
        items = self._items
        if "is_read" in kw:
            items = [i for i in items if i.is_read == kw["is_read"]]
        return _QuerySet(items)

    def all(self):
        return _QuerySet(self._items)

    def only(self, *fields):
        return self

    def order_by(self, key):
        return self

    def __getitem__(self, sl):
        return _QuerySet(self._items[sl])

    def __iter__(self):
        return iter(self._items)


class _Account:
    def __init__(self, inbox_items=(), sent_items=()):
        self.inbox = _QuerySet(inbox_items)
        self.sent = _QuerySet(sent_items)


def _analysed_ok(convs, *a, **kw):
    """A stand-in for a SUCCESSFUL analysis, for fixtures about fetch behaviour.

    Both fixtures below used to stand in with `ei._fallback_analysis`, which is
    the placeholder for a conversation the model never analysed. That was
    invisible while success and failure produced identical dicts. Since
    2026-08-29 a placeholder carries `analysis_failed`, and a run containing one
    is `partial` with its message ids deliberately left uncommitted, so using it
    as the stand-in for success would make these fetch tests assert the failure
    path by accident.
    """
    return [{"category": "fyi", "priority": "P3", "summary": c["topic"],
             "proposed_actions": [], "commitments": [],
             "relationship_signal": "stable"} for c in convs]


# ============================================================
# 1 - the fetch cap is measured, not guessed
# ============================================================

def test_a_capped_fetch_says_it_was_capped():
    """101 unread against a cap of 100 must not read as 100 unread."""
    account = _Account(inbox_items=[_Item(n, conv=f"c{n}") for n in range(101)])
    results, truncated = ei.fetch_emails(account, "inbox", cutoff=None,
                                         limit=100, unread_only=True)
    assert len(results) == 100
    assert truncated is True, "the cap was hit and the caller was not told"


def test_an_uncapped_fetch_does_not_cry_wolf():
    account = _Account(inbox_items=[_Item(n, conv=f"c{n}") for n in range(100)])
    results, truncated = ei.fetch_emails(account, "inbox", cutoff=None,
                                         limit=100, unread_only=True)
    assert len(results) == 100
    assert truncated is False, "exactly-at-the-cap is not truncation"


# ============================================================
# 2 - the ignore-pattern fallback survives a missing PyYAML
# ============================================================

def test_missing_pyyaml_falls_back_instead_of_raising_nameerror(monkeypatch, tmp_path):
    """The `except` tuple named `yaml.YAMLError` while `yaml` was unbound.

    Resolving the tuple to match the ImportError raised NameError from inside
    the handler, so the documented fallback to DEFAULT_IGNORE_PATTERNS never
    ran and the whole script died.
    """
    cfg = tmp_path / "sentinel_config.yaml"
    cfg.write_text("email:\n  ignore_patterns:\n    - '*@spam.test'\n", encoding="utf-8")
    monkeypatch.setattr(ei, "sentinel_config", lambda p=cfg: p)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def no_yaml(name, *a, **kw):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", no_yaml)
    patterns = ei._load_ignore_patterns()
    assert patterns == list(ei.DEFAULT_IGNORE_PATTERNS)


def test_with_pyyaml_the_config_patterns_are_still_added(monkeypatch, tmp_path):
    """The fallback must not have become the only path."""
    cfg = tmp_path / "sentinel_config.yaml"
    cfg.write_text("email:\n  ignore_patterns:\n    - '*@spam.test'\n", encoding="utf-8")
    monkeypatch.setattr(ei, "sentinel_config", lambda p=cfg: p)
    assert "*@spam.test" in ei._load_ignore_patterns()


# ============================================================
# 3 - a corrupt state file is kept, not replaced
# ============================================================

def test_corrupt_state_is_moved_aside_and_reported(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text('{"processed_message_ids": ["<a@x>"', encoding="utf-8")  # truncated

    state = ei.StateManager(path=path)

    err = capsys.readouterr().err
    assert "unusable" in err
    # `.quarantine/`, not a sibling: the sibling name matched no gitignore rule.
    # See tests/test_a_wreck_file_that_no_gitignore_rule_matched.py.
    kept = list(tmp_path.glob(".quarantine/state.json.corrupt-*"))
    assert len(kept) == 1, f"the damaged file was not kept: {list(tmp_path.iterdir())}"
    assert "<a@x>" in kept[0].read_text(encoding="utf-8")
    assert state.data["processed_message_ids"] == []


def test_a_state_file_that_is_a_list_is_also_quarantined(tmp_path):
    """Valid JSON of the wrong shape used to sail through and crash later."""
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    state = ei.StateManager(path=path)
    assert list(tmp_path.glob(".quarantine/state.json.corrupt-*"))
    assert isinstance(state.data, dict)


def test_a_healthy_state_file_is_left_exactly_alone(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "processed_message_ids": ["<a@x>"]}),
                    encoding="utf-8")
    state = ei.StateManager(path=path)
    assert state.data["processed_message_ids"] == ["<a@x>"]
    assert not list(tmp_path.glob(".quarantine/state.json.corrupt-*"))


# ============================================================
# 4 - two overlapping runs, and neither one's work disappears
# ============================================================

def test_the_other_runs_message_ids_survive_this_runs_save(tmp_path):
    """THE REGRESSION. Load, wait (minutes of LLM calls), save -- twice, overlapping."""
    path = tmp_path / "state.json"
    a = ei.StateManager(path=path)
    b = ei.StateManager(path=path)  # both loaded the same empty state

    a.mark_processed("<from-a@x>")
    b.mark_processed("<from-b@x>")
    a.save()
    b.save()  # used to replace a's write wholesale

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk["processed_message_ids"]) == {"<from-a@x>", "<from-b@x>"}


def test_merge_keeps_both_conversation_sets_and_prefers_the_later_entry():
    on_disk = {"conversations": {"c1": {"topic": "old", "last_seen": "2026-08-01"},
                                 "c2": {"topic": "theirs", "last_seen": "2026-08-02"}}}
    mine = {"conversations": {"c1": {"topic": "new", "last_seen": "2026-08-03"}}}
    merged = ei.merge_state(on_disk, mine)
    assert merged["conversations"]["c1"]["topic"] == "new"
    assert merged["conversations"]["c2"]["topic"] == "theirs"


def test_merge_re_applies_the_caps_it_inherits():
    """A union of two already-capped lists can exceed the cap."""
    on_disk = {"processed_message_ids": [f"<a{i}>" for i in range(ei.MAX_PROCESSED_IDS)]}
    mine = {"processed_message_ids": [f"<b{i}>" for i in range(ei.MAX_PROCESSED_IDS)]}
    merged = ei.merge_state(on_disk, mine)
    assert len(merged["processed_message_ids"]) == ei.MAX_PROCESSED_IDS

    on_disk_c = {"conversations": {f"c{i}": {"last_seen": f"2026-01-{i:02d}"}
                                   for i in range(1, ei.MAX_CONVERSATIONS + 1)}}
    mine_c = {"conversations": {f"d{i}": {"last_seen": f"2026-02-{i:02d}"}
                                for i in range(1, ei.MAX_CONVERSATIONS + 1)}}
    assert len(ei.merge_state(on_disk_c, mine_c)["conversations"]) == ei.MAX_CONVERSATIONS


def test_merge_takes_the_later_run_stamp():
    merged = ei.merge_state({"last_run": "2026-08-24T10:00:00+00:00"},
                            {"last_run": "2026-08-24T09:00:00+00:00"})
    assert merged["last_run"] == "2026-08-24T10:00:00+00:00"


# ============================================================
# 5 - no address is not evidence of an internal thread
# ============================================================

def _msg(mid, sender="", to=(), cc=(), conv="conv-1"):
    return {
        "message_id": mid, "conversation_id": conv, "conversation_topic": "T",
        "item_class": "IPM.Note", "subject": "S", "sender_email": sender,
        "sender_name": sender or "?", "to": [{"email": e, "name": e} for e in to],
        "cc": [{"email": e, "name": e} for e in cc], "body_preview": "p", "body": "b",
        "datetime": "2026-08-24T00:00:00+00:00", "direction": "incoming",
    }


def test_a_conversation_with_no_addresses_is_not_called_internal():
    convs = ei.group_conversations([_msg("<x@y>", sender="", to=(), cc=())])
    assert convs["conv-1"]["is_internal"] is False, "all([]) is True; an empty thread is not internal"


def test_a_genuinely_internal_conversation_is_still_called_internal():
    """The guard must not have inverted the real classification."""
    m = _msg("<x@y>", sender=f"a@{ei.INTERNAL_DOMAIN}", to=(f"b@{ei.INTERNAL_DOMAIN}",))
    assert ei.group_conversations([m])["conv-1"]["is_internal"] is True


def test_a_mixed_conversation_is_external():
    m = _msg("<x@y>", sender=f"a@{ei.INTERNAL_DOMAIN}", to=("outside@example.com",))
    assert ei.group_conversations([m])["conv-1"]["is_internal"] is False


# ============================================================
# 6, 7 - prose before the JSON
# ============================================================

@pytest.mark.parametrize("text,expected", [
    ('Here is the result: {"category":"fyi"}', {"category": "fyi"}),
    ('{"category":"fyi"}', {"category": "fyi"}),
    ('```json\n{"category":"fyi"}\n```', {"category": "fyi"}),
    ('Note: {"note": "a } inside a string"}', {"note": "a } inside a string"}),
])
def test_an_object_is_found_whatever_precedes_it(text, expected):
    assert ei._extract_json_object(text) == expected


@pytest.mark.parametrize("text,expected", [
    ('Result: [{"category":"fyi"}]', [{"category": "fyi"}]),
    ('[{"category":"fyi"}]', [{"category": "fyi"}]),
    ('```json\n[1, 2]\n```', [1, 2]),
    ('see [the docs] for [1, 2]', [1, 2]),
])
def test_an_array_is_found_whatever_precedes_it(text, expected):
    assert ei._extract_json_array(text) == expected


def test_a_response_with_no_json_still_raises():
    """The extractor must not have started inventing values."""
    with pytest.raises(json.JSONDecodeError):
        ei._extract_json_object("there is no object here")
    with pytest.raises(json.JSONDecodeError):
        ei._extract_json_array("there is no array here")


# ============================================================
# 8 - a length match is not a shape match
# ============================================================

class _Result:
    def __init__(self, text):
        self.text = text
        self.fallback_triggered = False
        self.vendor = "anthropic"
        self.primary_error = None


def _one_conv():
    return {
        "id": "conv-1", "topic": "T", "direction": "incoming", "message_count": 1,
        "participants": [], "latest_datetime": "2026-08-24T00:00:00+00:00",
        "is_internal": False, "raw_emails": [{
            "message_id": "<a@x>", "sender_name": "N", "sender_email": "n@x",
            "to": [], "cc": [], "subject": "S", "body_preview": "p", "body": "b",
            "datetime": "2026-08-24T00:00:00+00:00", "direction": "incoming",
        }],
    }


def test_a_string_where_an_analysis_belongs_becomes_a_fallback(monkeypatch):
    """`["not an object"]` matched the one-conversation batch length exactly.

    The length test passed, the string went into `all_results`, and
    `build_output` called `.get("priority")` on it and died with
    AttributeError -- after the API call had already been paid for.
    """
    monkeypatch.setattr(ei, "load_api_key", lambda name: "k")

    class _Client:
        pass

    # The `setitem` below is what stubs the client. `email-intelligence.py` does
    # `import anthropic` inside the function (line 872), so the local import
    # reads sys.modules and a module attribute on `ei` is shadowed. A
    # `monkeypatch.setattr(ei, "anthropic", ..., raising=False)` used to sit
    # here as well; it bound a name nothing looks up, and its `raising=False`
    # is what kept that quiet. The sibling test below stubs with the setitem
    # alone and works, which is the proof the setattr was carrying nothing.
    monkeypatch.setitem(sys.modules, "anthropic",
                        type("m", (), {"Anthropic": staticmethod(lambda **kw: _Client())}))
    monkeypatch.setattr(ei, "call_anthropic_with_fallback",
                        lambda **kw: _Result('["not an object"]'))

    conv = _one_conv()
    analyses = ei.analyze_conversations([conv], {}, "")

    assert len(analyses) == 1
    assert isinstance(analyses[0], dict), "a bare string reached the output"
    # And it must survive the step that used to crash.
    out = ei.build_output([conv], analyses, {"mode": "test"})
    assert out["conversations"][0]["priority"]


def test_a_well_formed_batch_response_is_still_used(monkeypatch):
    """The shape guard must not have replaced every real answer with a fallback."""
    monkeypatch.setattr(ei, "load_api_key", lambda name: "k")
    monkeypatch.setitem(sys.modules, "anthropic",
                        type("m", (), {"Anthropic": staticmethod(lambda **kw: object())}))
    monkeypatch.setattr(
        ei, "call_anthropic_with_fallback",
        lambda **kw: _Result('[{"category":"deal","priority":"P1","summary":"real"}]'),
    )
    analyses = ei.analyze_conversations([_one_conv()], {}, "")
    assert analyses[0]["summary"] == "real"


def test_build_output_survives_an_analysis_that_is_not_an_object():
    """`["not an object"]` matched the batch length and crashed downstream."""
    conv = {
        "id": "conv-1", "topic": "T", "direction": "incoming", "message_count": 1,
        "participants": [], "latest_datetime": "2026-08-24T00:00:00+00:00",
        "is_internal": False, "raw_emails": [{
            "message_id": "<a@x>", "sender_name": "N", "sender_email": "n@x",
            "to": [], "cc": [], "subject": "S", "body_preview": "p",
            "datetime": "2026-08-24T00:00:00+00:00", "direction": "incoming",
        }],
    }
    out = ei.build_output([conv], [ei._fallback_analysis(conv)], {"mode": "test"})
    assert out["conversations"][0]["priority"]


# ============================================================
# 9 - the cache key is an identity, not a count
# ============================================================

def _conv(count, latest, ids):
    return {"message_count": count, "latest_datetime": latest,
            "raw_emails": [{"message_id": i} for i in ids]}


def test_same_count_different_messages_is_a_cache_miss():
    """One unread read in Outlook, one new unread arrives: count still 1."""
    before = _conv(1, "2026-08-24T09:00:00+00:00", ["<old@x>"])
    after = _conv(1, "2026-08-24T10:00:00+00:00", ["<new@x>"])
    assert ei._cache_key(before) != ei._cache_key(after)


def test_an_unchanged_conversation_is_still_a_cache_hit():
    """The key must not have become "always miss", which would just burn tokens."""
    a = _conv(2, "2026-08-24T09:00:00+00:00", ["<a@x>", "<b@x>"])
    b = _conv(2, "2026-08-24T09:00:00+00:00", ["<b@x>", "<a@x>"])  # order is not identity
    assert ei._cache_key(a) == ei._cache_key(b)


# ============================================================
# 12 - the undo scan says when it did not find the thread
# ============================================================

def test_undo_reports_when_the_conversation_was_outside_the_scan_bound():
    """`ok: true, messages_changed: 0` used to mean both "done" and "never looked"."""
    noise = [_Item(n, conv=f"other-{n}", is_read=True) for n in range(ei.UNDO_SCAN_LIMIT)]
    account = _Account(inbox_items=noise)
    changed, exhaustive = ei.set_conversation_read(account, "conv-target", mark_read=False)
    assert changed == 0
    assert exhaustive is False


def test_undo_inside_the_bound_is_reported_as_exhaustive():
    account = _Account(inbox_items=[_Item(1, conv="conv-target", is_read=True)])
    changed, exhaustive = ei.set_conversation_read(account, "conv-target", mark_read=False)
    assert (changed, exhaustive) == (1, True)


def test_marking_read_scans_the_unread_set_and_is_always_exhaustive():
    account = _Account(inbox_items=[_Item(1, conv="conv-target", is_read=False)])
    changed, exhaustive = ei.set_conversation_read(account, "conv-target", mark_read=True)
    assert (changed, exhaustive) == (1, True)


def test_the_undo_command_fails_loudly_when_it_never_found_the_thread(monkeypatch, capsys):
    """The bound has to reach the CALLER's JSON, or the bridge still reads ok."""
    noise = [_Item(n, conv=f"other-{n}", is_read=True) for n in range(ei.UNDO_SCAN_LIMIT)]
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: _Account(inbox_items=noise))

    with pytest.raises(SystemExit) as exc:
        ei.run_mark_read_mode("conv-target", mark_read=False)

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 1
    assert payload["ok"] is False
    assert "not found" in payload["error"]


def test_the_undo_command_still_reports_ok_when_it_did_the_work(monkeypatch, capsys):
    monkeypatch.setattr(
        ei, "_connect_with_retries",
        lambda: _Account(inbox_items=[_Item(1, conv="conv-target", is_read=True)]),
    )
    ei.run_mark_read_mode("conv-target", mark_read=False)
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "conv_id": "conv-target",
                       "is_read": False, "messages_changed": 1}


# ============================================================
# 10, 11 - the dashboard feed is created, and never half-written
# ============================================================

@pytest.fixture
def unread_run(monkeypatch, tmp_path):
    """run_unread_mode() with Exchange, the LLM and the CRM all replaced."""
    state_file = tmp_path / "nested" / "deeper" / "state.json"
    monkeypatch.setattr(ei, "state_file", lambda p=state_file: p)
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: _Account())
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    monkeypatch.setattr(ei, "fetch_emails",
                        lambda a, f, cutoff=None, unread_only=False: ([_msg("<u@x>")], False))
    return state_file.parent / "_latest-fetch.json"


def test_the_first_unread_run_creates_its_own_directory(unread_run, capsys):
    """The directory does not exist on a fresh workspace, and the write raised
    FileNotFoundError AFTER the fetch and the whole analysis were paid for."""
    assert not unread_run.parent.exists()
    ei.run_unread_mode()
    capsys.readouterr()
    assert unread_run.exists()
    assert json.loads(unread_run.read_text(encoding="utf-8"))["conversations"]


def test_an_interrupted_feed_write_leaves_the_last_good_feed_intact(unread_run, monkeypatch, capsys):
    """The bridge reads this file on a timer. A plain write_text truncates in
    place, so an interrupted run left the dashboard parsing a half document."""
    unread_run.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"conversations": [{"id": "previous-good"}]})
    unread_run.write_text(good, encoding="utf-8")

    import scripts.utils.atomic as atomic_mod

    def no_replace(src, dst):
        raise OSError("disk went away mid-write")

    monkeypatch.setattr(atomic_mod.os, "replace", no_replace)

    with pytest.raises(SystemExit):
        ei.run_unread_mode()
    capsys.readouterr()

    assert unread_run.read_text(encoding="utf-8") == good, "the previous feed was clobbered"


# ============================================================
# 13, 14, 15, 16 - what a whole run reports about itself
# ============================================================

@pytest.fixture
def offline_run(monkeypatch, tmp_path):
    """Run main() end to end with Exchange and the LLM replaced."""
    monkeypatch.setattr(ei, "state_file", lambda p=tmp_path / "state.json": p)
    monkeypatch.setattr(ei, "connect_exchange", lambda: _Account())
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    monkeypatch.setattr(ei, "analyze_conversations", _analysed_ok)
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)

    class _Runner:
        def __init__(self):
            self.state_path = tmp_path / "state.json"

        def __call__(self, *argv):
            monkeypatch.setattr(sys, "argv", ["email-intelligence.py", *argv])
            ei.main()

    return _Runner()


def test_a_failed_inbox_fetch_is_reported_not_swallowed(offline_run, monkeypatch, capsys):
    def boom(account, folder, cutoff):
        if folder == "inbox":
            raise RuntimeError("EWS blew up")
        return ([], False)

    monkeypatch.setattr(ei, "fetch_emails", boom)
    offline_run("--json")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["run_info"]["folder_errors"]["inbox"] == "EWS blew up"
    assert payload["run_info"]["status"] == "partial"
    assert "Inbox fetch FAILED" in captured.err, "the error was verbose-only"


def test_a_failed_sent_fetch_is_reported_not_swallowed(offline_run, monkeypatch, capsys):
    def boom(account, folder, cutoff):
        if folder == "sent":
            raise RuntimeError("EWS blew up")
        return ([], False)

    monkeypatch.setattr(ei, "fetch_emails", boom)
    offline_run("--json")
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["run_info"]["folder_errors"]["sent"] == "EWS blew up"
    assert payload["run_info"]["status"] == "partial"
    assert "Sent fetch FAILED" in captured.err


def test_a_clean_run_is_not_labelled_partial(offline_run, monkeypatch, capsys):
    """The status flag must not have become "always partial"."""
    monkeypatch.setattr(ei, "fetch_emails", lambda a, f, c: ([], False))
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_info"]["status"] == "complete"
    assert payload["run_info"]["folder_errors"] == {}


def test_a_truncated_fetch_makes_the_run_partial(offline_run, monkeypatch, capsys):
    monkeypatch.setattr(ei, "fetch_emails",
                        lambda a, f, c: ([], True) if f == "inbox" else ([], False))
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_info"]["truncated_folders"] == ["inbox"]
    assert payload["run_info"]["status"] == "partial"


def test_inbox_only_and_sent_only_together_are_refused(offline_run, monkeypatch):
    """Together they skipped BOTH folders and reported a complete empty scan."""
    monkeypatch.setattr(ei, "fetch_emails", lambda a, f, c: ([], False))
    with pytest.raises(SystemExit) as exc:
        offline_run("--json", "--inbox-only", "--sent-only")
    assert exc.value.code == 2


@pytest.mark.parametrize("flag", ["--inbox-only", "--sent-only"])
def test_each_folder_flag_alone_still_works(offline_run, monkeypatch, capsys, flag):
    monkeypatch.setattr(ei, "fetch_emails", lambda a, f, c: ([], False))
    offline_run("--json", flag)
    assert json.loads(capsys.readouterr().out)["run_info"]["status"] == "complete"


def test_internal_only_message_ids_do_reach_state_commit(offline_run, monkeypatch, capsys):
    """FINDING 16, REFUTED.

    The audit read `message_ids` as coming from a set that excludes internal
    threads, and concluded internal mail was re-fetched forever. It does not:
    `clean` is the output of `filter_noise`, which never looks at
    `is_internal`; the internal split happens later, at `external_convs`. So
    an internal-only thread contributes no CONVERSATION to the commit payload
    -- correctly, there is nothing to analyse -- but its message ids are there,
    and the next run's Layer 5 filter drops them.

    Pinned as a test rather than left as a reading, because a future change to
    the order of those two steps would reintroduce exactly the bug the audit
    described.
    """
    internal = _msg("<inside@31c.io>", sender=f"a@{ei.INTERNAL_DOMAIN}",
                    to=(f"b@{ei.INTERNAL_DOMAIN}",), conv="conv-internal")
    monkeypatch.setattr(ei, "fetch_emails",
                        lambda a, f, c: ([internal], False) if f == "inbox" else ([], False))
    offline_run("--json")
    payload = json.loads(capsys.readouterr().out)

    assert payload["state_commit"]["message_ids"] == ["<inside@31c.io>"]
    assert payload["state_commit"]["conversations"] == []
    assert payload["run_info"]["internal_skipped"] == 1


# ============================================================
# 17-20 - email-sweep state handling
# ============================================================

@pytest.mark.parametrize("bad", ["../../../x", "2026-13-45", "20260824", "", "2026-08-24/../x"])
def test_a_sweep_date_that_is_not_a_date_is_refused(bad):
    with pytest.raises(ValueError):
        sweep._valid_date(bad)


def test_a_real_sweep_date_is_accepted():
    assert sweep._valid_date("2026-08-24") == "2026-08-24"


def test_an_unreadable_sweep_is_not_replaced_by_propose(tmp_path, capsys):
    root = tmp_path
    day = "2026-08-24"
    path = sweep._state_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"actions": [', encoding="utf-8")  # truncated approval queue

    class _Args:
        file = str(tmp_path / "payload.json")
        date = day

    Path(_Args.file).write_text('[{"type": "task", "title": "x"}]', encoding="utf-8")
    rc = sweep.cmd_propose(root, _Args())

    assert rc == 1
    assert path.read_text(encoding="utf-8") == '{"actions": ['
    assert "refusing to overwrite" in capsys.readouterr().err


def test_a_sweep_of_the_wrong_shape_is_not_replaced_either(tmp_path, capsys):
    """Valid JSON, wrong shape. `_load` returned None for this too, so
    `_load(...) or {new sweep}` treated it as "no sweep yet" and replaced it."""
    root = tmp_path
    day = "2026-08-24"
    path = sweep._state_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"date": day, "actions": "not a list"})
    path.write_text(original, encoding="utf-8")

    class _Args:
        file = str(tmp_path / "payload.json")
        date = day

    Path(_Args.file).write_text('[{"type": "task", "title": "x"}]', encoding="utf-8")
    assert sweep.cmd_propose(root, _Args()) == 1
    assert path.read_text(encoding="utf-8") == original
    assert "refusing to overwrite" in capsys.readouterr().err


def test_a_sweep_in_the_wrong_encoding_is_not_replaced_either(tmp_path, capsys):
    """The third unreadable shape, and the one the handler could not see.

    `_load` caught `(OSError, json.JSONDecodeError)`. `UnicodeDecodeError` is a
    `ValueError` and a SIBLING of `JSONDecodeError`, not a subclass, so a sweep
    saved as UTF-16 or truncated mid-character went past it. `main` catches
    ValueError, so the file was still never overwritten; what the operator got
    was a raw codec message instead of the named refusal, on the one file this
    module says "must never be silently discarded". Both eval loaders already
    carried `(ValueError, OSError)` with this exact reasoning written above
    them; the sweep was the copy the fix missed. MEASURED 2026-09-01 with a
    UTF-16 sweep: `_load` raised UnicodeDecodeError, not SweepUnreadable.
    """
    root = tmp_path
    day = "2026-08-24"
    path = sweep._state_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"date": day, "actions": []}).encode("utf-16")
    path.write_bytes(original)

    class _Args:
        file = str(tmp_path / "payload.json")
        date = day

    Path(_Args.file).write_text('[{"type": "task", "title": "x"}]', encoding="utf-8")

    assert sweep.cmd_propose(root, _Args()) == 1
    assert path.read_bytes() == original, "the approval queue was replaced"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_a_propose_payload_in_the_wrong_encoding_is_a_clean_refusal(tmp_path, capsys):
    """The same tuple, in the same file, on the payload rather than the sweep."""
    root = tmp_path
    payload = tmp_path / "payload.json"
    payload.write_bytes('[{"type": "task", "title": "x"}]'.encode("utf-16"))

    class _Args:
        file = str(payload)
        date = "2026-08-24"

    assert sweep.cmd_propose(root, _Args()) == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_a_readable_sweep_is_still_merged_into(tmp_path, capsys):
    """The anchor over both. A `_load` that refused everything would satisfy
    them and make `propose` impossible to run twice in one day."""
    root = tmp_path
    day = "2026-08-24"
    path = sweep._state_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": day, "actions": [
        {"id": 1, "type": "task", "title": "already here", "status": "pending"}]}),
        encoding="utf-8")

    class _Args:
        file = str(tmp_path / "payload.json")
        date = day

    Path(_Args.file).write_text('[{"type": "task", "title": "new"}]', encoding="utf-8")

    assert sweep.cmd_propose(root, _Args()) == 0
    capsys.readouterr()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [a["title"] for a in saved["actions"]] == ["already here", "new"]


def test_a_sweep_with_a_non_numeric_id_is_a_clean_refusal_not_a_typeerror(tmp_path, capsys):
    root = tmp_path
    day = "2026-08-24"
    path = sweep._state_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": day, "actions": [{"id": "7", "type": "task", "title": "x"}]}),
                    encoding="utf-8")

    class _Args:
        file = str(tmp_path / "payload.json")
        date = day

    Path(_Args.file).write_text('[{"type": "task", "title": "y"}]', encoding="utf-8")
    assert sweep.cmd_propose(root, _Args()) == 1
    assert "non-numeric action id" in capsys.readouterr().err


def test_two_sweep_writers_do_not_share_one_scratch_path(tmp_path, monkeypatch):
    """A fixed `<name>.tmp` let one writer's os.replace move the other's file."""
    seen = []
    real_mkstemp = sweep.tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(sweep.tempfile, "mkstemp", spy)
    for _ in range(3):
        sweep._save(tmp_path, "2026-08-24", {"date": "2026-08-24", "actions": []})
    assert len(set(seen)) == 3, f"scratch names collided: {seen}"


# ============================================================
# 22-24 - eval-flag
# ============================================================

@pytest.mark.parametrize("bad", ["../../outside", "/etc", "a/b", "Upper", "-lead", ""])
def test_eval_flag_refuses_a_skill_that_is_a_path(bad):
    with pytest.raises(ValueError):
        evflag._valid_skill(bad)


def test_eval_flag_accepts_a_real_skill_name():
    assert evflag._valid_skill("email-intel") == "email-intel"
    assert evflag._staged_dir("email-intel").is_relative_to(evflag.SKILLS_DIR)


def test_two_captures_in_one_second_are_two_drafts():
    """Second-resolution ids plus a slug meant the second overwrote the first."""
    a = evflag._new_draft("same description", "in", "t", "cli", "prose")
    b = evflag._new_draft("same description", "in", "t", "cli", "prose")
    assert a["id"] != b["id"]


def test_the_draft_id_still_carries_the_slug_and_a_timestamp():
    """The unique suffix must not have replaced what made the id readable."""
    d = evflag._new_draft("wrong crm slug", "in", "t", "cli", "prose")
    assert d["id"].startswith("flag-")
    assert "wrong-crm-slug" in d["id"]


def test_eval_flag_uses_a_unique_scratch_name(tmp_path, monkeypatch):
    staged = tmp_path / "_staged"
    monkeypatch.setattr(evflag, "_staged_dir", lambda skill: staged)
    seen = []
    real_mkstemp = evflag.tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(evflag.tempfile, "mkstemp", spy)
    for i in range(3):
        evflag._stage_draft("x", {"id": f"d{i}"})
    assert len(set(seen)) == 3


# ============================================================
# 25-31 - eval-outcomes
# ============================================================

@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "42"])
def test_a_case_file_of_the_wrong_shape_becomes_a_load_error(tmp_path, body):
    """`case["_path"] = ...` on a list raised TypeError and killed the runner."""
    out_dir = tmp_path / "evals" / "outcomes"
    out_dir.mkdir(parents=True)
    (out_dir / "bad.json").write_text(body, encoding="utf-8")

    cases = evout.load_outcome_cases(tmp_path)
    assert len(cases) == 1
    assert "_load_error" in cases[0]

    results, setup_error = evout.run_one_case(cases[0], render=False)
    assert setup_error is True
    assert results[0]["passed"] is False


def test_a_well_formed_case_still_loads(tmp_path):
    out_dir = tmp_path / "evals" / "outcomes"
    out_dir.mkdir(parents=True)
    (out_dir / "ok.json").write_text(json.dumps({"id": "c1", "outcome": {"type": "crm_log"}}),
                                     encoding="utf-8")
    cases = evout.load_outcome_cases(tmp_path)
    assert cases[0]["id"] == "c1"
    assert "_load_error" not in cases[0]


@pytest.mark.parametrize("bad", ["../../outside", "/etc", "a/b", "Upper", ""])
def test_eval_outcomes_refuses_a_skill_that_is_a_path(bad):
    with pytest.raises(ValueError):
        evout.valid_skill_name(bad)


def _run_evout(*argv):
    proc = subprocess.run(
        [sys.executable, "scripts/eval-outcomes.py", *argv],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    return proc


def test_a_misspelled_skill_is_a_setup_error_not_a_green_run():
    """Zero cases took the `overall_total == 0` branch and exited 0."""
    proc = _run_evout("--skill", "does-not-exist", "--no-write")
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_a_skill_that_is_a_path_is_refused_before_anything_is_read():
    proc = _run_evout("--skill", "../../outside", "--no-write")
    assert proc.returncode == 2
    assert "bare skill name" in proc.stderr


def test_a_case_filter_that_matches_nothing_is_a_setup_error():
    proc = _run_evout("--skill", "email-intel", "--case", "no-such-case", "--no-write")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "matched no outcome case" in proc.stderr


def test_a_real_skill_with_real_cases_still_passes():
    """The three guards above must not have made every run a setup error."""
    proc = _run_evout("--skill", "email-intel", "--no-write")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_pdf_only_doctype_refuses_the_browser_free_render_path():
    """`non_pdf or default` rendered PDF anyway, which is what --render avoids."""
    with pytest.raises(ValueError):
        evout._render_formats("only-pdf", {"only-pdf": {"formats": ["pdf"]}})


def test_a_doctype_with_a_non_pdf_format_still_picks_it():
    fmts = evout._render_formats("d", {"d": {"formats": ["pdf", "docx"]}})
    assert fmts == ["docx"]


def test_the_benchmark_uses_a_unique_scratch_name(tmp_path, monkeypatch):
    seen = []
    real_mkstemp = evout.tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(evout.tempfile, "mkstemp", spy)
    for _ in range(3):
        evout._write_benchmark(tmp_path, 1, 1, [])
    assert len(set(seen)) == 3


def test_the_render_subprocess_is_bounded():
    assert isinstance(evout.RENDER_TIMEOUT_S, (int, float))
    assert evout.RENDER_TIMEOUT_S > 0


# ============================================================
# 32, 33 - eval-query-set
# ============================================================

def test_json_mode_returns_the_same_verdict_as_terminal_mode(monkeypatch, tmp_path, capsys):
    """The same below-bar measurement exited 1 in one mode and 0 in the other."""
    set_file = tmp_path / "set.md"
    # The row shape `load_set` parses: a number, the query, then the target in
    # backticks.
    set_file.write_text(
        "## Set A\n"
        "| 1 | a question | `deadbee` |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(evqs, "get_data_root", lambda: tmp_path)
    monkeypatch.setitem(evqs.PHASES, "1", {"rel": "set.md", "layer": "commit-engine", "bar_a": 0.80})
    monkeypatch.setattr(evqs, "query", lambda *a, **kw: [])  # every case misses

    monkeypatch.setattr(sys, "argv", ["eval-query-set.py"])
    terminal_rc = evqs.main()
    monkeypatch.setattr(sys, "argv", ["eval-query-set.py", "--json"])
    json_rc = evqs.main()
    capsys.readouterr()

    assert terminal_rc == 1
    assert json_rc == terminal_rc, "--json reported green on a failing index"


def test_the_index_query_is_bounded():
    assert isinstance(evqs.QUERY_TIMEOUT_S, (int, float))
    assert evqs.QUERY_TIMEOUT_S > 0


# ============================================================
# 34 - exchange-task's own documented command
# ============================================================

def _parse_task_args(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["exchange-task.py", *argv])
    return extask.parse_args()


def test_the_documented_create_command_parses(monkeypatch):
    """The usage block says --subject creates; the parser required a mode flag."""
    args = _parse_task_args(monkeypatch, ["--subject", "Follow up", "--due", "2026-04-29"])
    assert args.subject == "Follow up"
    assert args.list is False and args.complete is None


@pytest.mark.parametrize("argv", [["--list"], ["--complete", "Follow up"], ["--create", "--subject", "x"]])
def test_the_explicit_modes_still_parse(monkeypatch, argv):
    assert _parse_task_args(monkeypatch, argv) is not None


def test_a_bare_invocation_is_refused_before_it_reaches_exchange(monkeypatch):
    """Dropping required=True must not let an empty create through."""
    with pytest.raises(SystemExit) as exc:
        _parse_task_args(monkeypatch, [])
    assert exc.value.code == 2


def test_two_modes_together_are_still_refused(monkeypatch):
    with pytest.raises(SystemExit):
        _parse_task_args(monkeypatch, ["--list", "--complete", "x"])
