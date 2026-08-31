"""Eight ways `scripts/email-intelligence.py` broke a contract it documents.

Shard `scripts-06-p1` of the 2026-08-29 engine audit. Every finding here has the
same author: a guard, a convention or a channel discipline that the file applies
correctly in one place and skipped in another, one screen away. The comments in
the script name each sibling; these tests pin the behaviour so the pair cannot
drift apart again.

  1  --verbose printed onto stdout, which carries the machine-read payload
  2  the prior-fetch cache called `.get` on whatever JSON it found
  3  an undo that hit its scan bound with the thread found reported `ok: true`
  4  `load_pipeline_context` let an OSError end the run
  5  a scalar `ignore_patterns` was iterated one character at a time
  6  a missing credential exited 1 with an empty stdout, past the JSON contract
  7  a wrong-typed `stats` counter escaped the "Commit failed" handler
  8  two mode flags together ran one and dropped the other in silence

Every test drives the real function. Exchange, the model and the CRM are
replaced by fakes; nothing here reaches a network, a mailbox or the operator's
overlay, and every write goes to `tmp_path`.
"""

import ast
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts/email-intelligence.py"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ei = _load("email_intelligence_s06", "scripts/email-intelligence.py")


# ============================================================
# Fakes: an Exchange account, and one conversation shaped like a real one
# ============================================================

class _ConvId:
    def __init__(self, cid):
        self.id = cid


class _Item:
    def __init__(self, conv, is_read=True):
        self.conversation_id = _ConvId(conv)
        self.is_read = is_read
        self.saved = 0

    def save(self, update_fields=None):
        self.saved += 1


class _Query:
    """Enough of an exchangelib QuerySet for `set_conversation_read` to walk."""

    def __init__(self, items):
        self.items = items

    def all(self):
        return self

    def filter(self, **kw):
        return _Query([i for i in self.items if i.is_read is kw.get("is_read")])

    def only(self, *a):
        return self

    def order_by(self, *a):
        return self

    def __getitem__(self, sl):
        return _Query(self.items[sl])

    def __iter__(self):
        return iter(self.items)


class _Account:
    def __init__(self, items):
        self.inbox = _Query(items)


def _conversation(conv_id="conv-bond", message_id="<m1@example.com>"):
    """One external conversation, in the shape `build_output` consumes."""
    return {
        "id": conv_id,
        "topic": "Acme Telecom pilot",
        "direction": "incoming",
        "message_count": 1,
        "participants": ["james.bond@example.com"],
        "latest_datetime": "2026-08-24T09:00:00+00:00",
        "is_internal": False,
        "crm_context": None,
        "pipeline_context": None,
        "viraid_overlap": None,
        "raw_emails": [{
            "message_id": message_id,
            "sender_name": "James Bond",
            "sender_email": "james.bond@example.com",
            "to": [], "cc": [],
            "subject": "Pilot scope",
            "body_preview": "preview",
            "datetime": "2026-08-24T09:00:00+00:00",
            "direction": "incoming",
        }],
    }


_ANALYSIS = {"priority": "P2", "summary": "s", "category": "deal",
             "action_required": False}


class _FakeState:
    def __init__(self, *a, **kw):
        self.data = {}


@pytest.fixture
def unread(monkeypatch, tmp_path):
    """`run_unread_mode` with Exchange, the CRM, the model and the state faked.

    Returns a small controller: set `.convs` to the conversation list the
    grouping step should yield, then call `.run()` and read `.stdout`.
    """
    monkeypatch.setattr(ei, "state_file", lambda p=tmp_path / "state.json": p)
    monkeypatch.setattr(ei, "StateManager", _FakeState)
    monkeypatch.setattr(ei, "_load_ignore_patterns", list)
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: _Account([]))
    monkeypatch.setattr(ei, "fetch_emails", lambda *a, **kw: ([], False))
    monkeypatch.setattr(ei, "filter_noise", lambda *a, **kw: ([], 0))
    monkeypatch.setattr(ei, "load_crm_contacts", dict)
    monkeypatch.setattr(ei, "load_pipeline_context", lambda: "")
    monkeypatch.setattr(ei, "load_viraid_state", dict)
    monkeypatch.setattr(ei, "enrich_conversation", lambda *a, **kw: None)

    class _Ctl:
        convs: list = []
        analyzed: list = []
        stdout = ""

        def run(self, verbose=False):
            monkeypatch.setattr(
                ei, "group_conversations",
                lambda emails: {c["id"]: c for c in self.convs})

            def _analyze(to_analyze, *a, **kw):
                self.analyzed.append([c["id"] for c in to_analyze])
                return [dict(_ANALYSIS) for _ in to_analyze]

            monkeypatch.setattr(ei, "analyze_conversations", _analyze)
            buf = io.StringIO()
            with redirect_stdout(buf):
                ei.run_unread_mode(verbose=verbose)
            self.stdout = buf.getvalue()
            return json.loads(self.stdout)

    ctl = _Ctl()
    ctl.fetch_path = tmp_path / "_latest-fetch.json"
    return ctl


# ============================================================
# 1 - the progress lines and the payload shared one channel
# ============================================================

def test_unread_verbose_stdout_is_still_only_the_json_payload(unread):
    """`--unread --verbose` used to put a colored line ahead of the JSON.

    The bridge daemon parses this stdout. Measured before the fix: `json.loads`
    failed at char 0 on "  Unread: 0 conversations (0 cached, 0 to analyze)".
    """
    payload = unread.run(verbose=True)
    assert payload["ok"] is True
    assert unread.stdout.lstrip().startswith("{")


def test_verbose_still_says_something_on_stderr(unread, capsys):
    """Routing to stderr must not have become deleting the line."""
    unread.run(verbose=True)
    assert "Unread:" in capsys.readouterr().err


def _verbose_prints(tree):
    """Every `print(...)` sitting directly under a `verbose` conditional."""
    def mentions_verbose(node):
        return any(
            (isinstance(n, ast.Name) and n.id == "verbose")
            or (isinstance(n, ast.Attribute) and n.attr == "verbose")
            for n in ast.walk(node))

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not mentions_verbose(node.test):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id == "print"):
                found.append(stmt.value)
    return found


def test_every_verbose_print_in_the_script_names_a_stream():
    """The whole class, not only the two paths the fixtures reach.

    `main()`'s six verbose prints need Exchange to reach, so they are pinned
    structurally: each is a `print` under a `verbose` test, and each must carry
    an explicit `file=`. Nothing else in this file can see them.
    """
    calls = _verbose_prints(ast.parse(SCRIPT.read_text(encoding="utf-8")))
    assert len(calls) >= 8, f"corpus collapsed to {len(calls)} verbose prints"
    unrouted = [c.lineno for c in calls
                if not any(kw.arg == "file" for kw in c.keywords)]
    assert unrouted == [], f"verbose prints with no file= at lines {unrouted}"


# ============================================================
# 2 - the cache loader trusted the shape of whatever JSON it read
# ============================================================

def test_a_prior_fetch_file_holding_a_json_list_does_not_kill_the_run(unread):
    """`json.loads` succeeds on `[]`; `.get` on a list is an AttributeError.

    Not in the except tuple, so every bridge tick died with a traceback and no
    JSON envelope until the file was deleted by hand.
    """
    unread.fetch_path.write_text("[]", encoding="utf-8")
    unread.convs = [_conversation()]
    payload = unread.run()
    assert payload["ok"] is True
    assert unread.analyzed == [["conv-bond"]]


def test_a_prior_fetch_file_of_the_right_shape_is_still_a_cache_hit(unread):
    """The guard must refuse the wrong shape, not disable caching."""
    conv = _conversation()
    unread.fetch_path.write_text(json.dumps({
        "conversations": [{
            "id": conv["id"],
            "analysis": dict(_ANALYSIS),
            "raw_emails": [{"message_id": conv["raw_emails"][0]["message_id"]}],
        }],
    }), encoding="utf-8")
    unread.convs = [conv]
    payload = unread.run()
    assert payload["analyzed_fresh"] == 0
    assert unread.analyzed == []


# ============================================================
# 3 - an undo cut off at its bound reported success
# ============================================================

def test_an_undo_that_reached_the_scan_bound_is_not_exhaustive():
    """The thread's newest message is inside the window, 500 older ones are not.

    `exhaustive` used to require `not found`, so this walk returned True and the
    caller said `ok: true` over a half-reverted thread.
    """
    items = ([_Item("conv-bond")]
             + [_Item(f"other-{n}") for n in range(ei.UNDO_SCAN_LIMIT + 500)])
    changed, exhaustive = ei.set_conversation_read(
        _Account(items), "conv-bond", mark_read=False)
    assert changed == 1
    assert exhaustive is False


def test_an_undo_well_inside_the_bound_is_still_exhaustive():
    """The bound must not have become "never exhaustive"."""
    changed, exhaustive = ei.set_conversation_read(
        _Account([_Item("conv-bond")]), "conv-bond", mark_read=False)
    assert (changed, exhaustive) == (1, True)


def test_a_partial_undo_says_it_was_partial_and_not_that_it_did_nothing(
        monkeypatch, capsys):
    """One error string used to cover two different outcomes.

    "nothing was changed" is false about a thread the walk reached and partly
    reverted, so the wording has to follow `changed`.
    """
    items = ([_Item("conv-bond")]
             + [_Item(f"other-{n}") for n in range(ei.UNDO_SCAN_LIMIT + 500)])
    monkeypatch.setattr(ei, "_connect_with_retries", lambda: _Account(items))

    with pytest.raises(SystemExit) as exc:
        ei.run_mark_read_mode("conv-bond", mark_read=False)

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 1
    assert payload["ok"] is False
    assert payload["messages_changed"] == 1
    assert "nothing was changed" not in payload["error"]
    assert "may still be read" in payload["error"]


# ============================================================
# 4 - a context loader that could end the run
# ============================================================

def test_an_unreadable_pipeline_file_degrades_the_digest_it_does_not_end_it(
        monkeypatch, tmp_path, capsys):
    """A directory where a file is expected: `exists()` is True, the read is an
    OSError. Deterministic at any uid, unlike a chmod that root ignores."""
    trap = tmp_path / "pipeline.md"
    trap.mkdir()
    monkeypatch.setattr(ei, "pipeline_file", lambda p=trap: p)

    assert ei.load_pipeline_context() == ""
    assert "pipeline context unreadable" in capsys.readouterr().err


def test_a_readable_pipeline_file_is_still_returned_whole(monkeypatch, tmp_path):
    """The handler must not have swallowed the happy path with it."""
    src = tmp_path / "pipeline.md"
    src.write_text("| Acme Telecom | pilot | 90k |\n" * 200, encoding="utf-8")
    monkeypatch.setattr(ei, "pipeline_file", lambda p=src: p)
    assert ei.load_pipeline_context() == src.read_text(encoding="utf-8")


# ============================================================
# 5 - a string is iterable
# ============================================================

def test_a_scalar_ignore_patterns_is_refused_not_spelled_out(
        monkeypatch, tmp_path, capsys):
    """`ignore_patterns: "noreply@*"` became nine one-character patterns.

    The operator's pattern never took effect, and the stray '*' among them made
    `_matches_ignore` warn once per message checked.
    """
    cfg = tmp_path / "sentinel_config.yaml"
    cfg.write_text('email:\n  ignore_patterns: "noreply@*"\n', encoding="utf-8")
    monkeypatch.setattr(ei, "sentinel_config", lambda p=cfg: p)

    patterns = ei._load_ignore_patterns()
    assert set(ei.DEFAULT_IGNORE_PATTERNS), "the default corpus is empty"
    extra = [p for p in patterns if p not in ei.DEFAULT_IGNORE_PATTERNS]
    assert extra == []
    assert "*" not in patterns
    assert "not a list" in capsys.readouterr().err


def test_a_list_of_ignore_patterns_is_still_loaded(monkeypatch, tmp_path):
    """The type check must refuse the scalar, not the configuration."""
    cfg = tmp_path / "sentinel_config.yaml"
    cfg.write_text("email:\n  ignore_patterns:\n    - '*@spam.example'\n",
                   encoding="utf-8")
    monkeypatch.setattr(ei, "sentinel_config", lambda p=cfg: p)
    assert "*@spam.example" in ei._load_ignore_patterns()


# ============================================================
# 6 - a missing credential left the bridge nothing to parse
# ============================================================

def test_missing_credentials_raise_instead_of_exiting_the_process(monkeypatch):
    """`sys.exit(1)` from inside the connect function outran every JSON handler."""
    monkeypatch.setattr(ei, "load_env", lambda *a, **kw: None)
    for name in ("EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ei.MissingExchangeCredentials) as exc:
        ei.connect_exchange()
    assert "EXCHANGE_PASSWORD" in str(exc.value)


def test_the_bridge_mode_reports_a_missing_credential_as_json(monkeypatch, capsys):
    """Every other failure on this path is a JSON object on stdout. This one
    used to be an empty stdout and exit 1."""
    monkeypatch.setattr(ei, "load_env", lambda *a, **kw: None)
    for name in ("EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exc:
        ei.run_mark_read_mode("conv-bond", mark_read=True)

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 1
    assert payload["ok"] is False
    assert "credentials" in payload["error"]


def test_a_missing_credential_is_not_retried(monkeypatch):
    """`.env` does not fill itself in two seconds; three attempts is six wasted."""
    monkeypatch.setattr(ei, "load_env", lambda *a, **kw: None)
    for name in ("EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"):
        monkeypatch.delenv(name, raising=False)
    slept = []
    monkeypatch.setattr(ei.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(ei.MissingExchangeCredentials):
        ei._connect_with_retries()
    assert slept == []


def test_a_transient_connect_failure_is_still_retried(monkeypatch):
    """The fast path must be scoped to the credential case."""
    attempts = []

    def _boom():
        attempts.append(1)
        raise TimeoutError("EWS timed out")

    monkeypatch.setattr(ei, "connect_exchange", _boom)
    monkeypatch.setattr(ei.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        ei._connect_with_retries()
    assert len(attempts) == 3


# ============================================================
# 7 - the "Commit failed" promise did not cover a wrong-typed counter
# ============================================================

def _state_with(stats):
    state = _FakeState()
    state.data = {"stats": stats, "processed_message_ids": [], "conversations": {}}
    state.mark_processed = lambda mid: None
    state.mark_conversation = lambda cid, topic: None
    return state


@pytest.mark.parametrize("stats", [
    {"total_runs": "5"},
    {"total_conversations": None},
    {"total_filtered": [1]},
    None,
    [],
])
def test_a_wrong_typed_stats_counter_is_a_valueerror(stats):
    """TypeError and AttributeError both escape `main`'s except tuple.

    Measured: `"total_runs": "5"` raised TypeError on `+ 1`, `"stats": null`
    raised AttributeError on `.get`, and the deferred-commit path died on a
    traceback instead of the clean exit it promises.
    """
    with pytest.raises(ValueError):
        ei.commit_state(_state_with(stats), {
            "message_ids": [], "conversations": [], "noise_filtered": 0})


def test_a_well_formed_state_still_commits():
    """The type check must not have refused the ordinary case."""
    state = _state_with({"total_runs": 4, "total_conversations": 9,
                         "total_filtered": 2})
    ei.commit_state(state, {"message_ids": ["<m1@example.com>"],
                            "conversations": [], "noise_filtered": 3})
    assert state.data["stats"] == {"total_runs": 5, "total_conversations": 9,
                                   "total_filtered": 5}


def test_commit_state_from_file_reports_a_wrong_typed_counter_cleanly(
        monkeypatch, tmp_path, capsys):
    """End to end through the handler that promises "Commit failed"."""
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"state_commit": {
        "message_ids": [], "conversations": [], "noise_filtered": 0}}),
        encoding="utf-8")
    monkeypatch.setattr(ei, "StateManager",
                        lambda *a, **kw: _state_with({"total_runs": "5"}))
    monkeypatch.setattr(sys, "argv",
                        ["email-intelligence.py", "--commit-state", str(run)])

    with pytest.raises(SystemExit) as exc:
        ei.main()
    assert exc.value.code == 1
    assert "Commit failed" in capsys.readouterr().err


# ============================================================
# 8 - two modes given, one run, nothing said about the other
# ============================================================

def test_two_mode_flags_together_are_refused_by_argparse(monkeypatch, capsys):
    """`--unread --mark-read ABC` marked the conversation read and discarded the
    unread feed without a word."""
    ran = []
    monkeypatch.setattr(ei, "run_mark_read_mode",
                        lambda cid, mark_read: ran.append(("mark", cid)))
    monkeypatch.setattr(ei, "run_unread_mode",
                        lambda verbose=False: ran.append(("unread",)))
    monkeypatch.setattr(sys, "argv",
                        ["email-intelligence.py", "--unread", "--mark-read", "ABC"])

    with pytest.raises(SystemExit) as exc:
        ei.main()
    assert exc.value.code == 2
    assert ran == []
    assert "not allowed with" in capsys.readouterr().err


@pytest.mark.parametrize("argv,expected", [
    (["--unread"], ("unread",)),
    (["--mark-read", "ABC"], ("mark", "ABC")),
    (["--mark-unread", "ABC"], ("unmark", "ABC")),
])
def test_each_mode_flag_alone_still_runs_its_mode(monkeypatch, argv, expected):
    """The group must refuse the pair, not the flags."""
    ran = []
    monkeypatch.setattr(
        ei, "run_mark_read_mode",
        lambda cid, mark_read: ran.append(("mark" if mark_read else "unmark", cid)))
    monkeypatch.setattr(ei, "run_unread_mode",
                        lambda verbose=False: ran.append(("unread",)))
    monkeypatch.setattr(sys, "argv", ["email-intelligence.py", *argv])

    ei.main()
    assert ran == [expected]
