"""Nine readers whose documented fallback one non-UTF-8 byte walked straight past.

`UnicodeDecodeError` subclasses `ValueError`. That makes it a SIBLING of
`json.JSONDecodeError`, not a member of it, and it is not an `OSError` and not a
`yaml.YAMLError` either. The decode runs inside `read_text()`, inside
`json.load(<text handle>)`, or lazily inside `yaml.safe_load(<text handle>)` --
in every case BEFORE any JSON or YAML parse is entered. So a handler spelled
`except OSError`, `except (json.JSONDecodeError, OSError)` or
`except (OSError, yaml.YAMLError)` cannot see it.

Each of the nine functions below carries a docstring promising a degraded
return: "degrade to empty", "returns {} when unreadable", "{"ok": False, ...}",
"never raises to its caller". For one bad byte the promise was a lie.

WHAT WAS MEASURED, 2026-09-01, driving each real function with a file holding a
lone 0xe9 byte (`/tmp/decode-b2/measure.py`, twelve sites from the triage batch):

    site                                          before              after
    ------------------------------------------------------------------------
    mail.read_email_state                         UnicodeDecodeError  {"messages": []}
    agenda.today_agenda                           UnicodeDecodeError  {...events: []}
    inbox.read_conversation                       UnicodeDecodeError  {"ok": False, ...}
    tasks.list_active_tasks                       UnicodeDecodeError  {...tasks: []}
    fireside-pulse.load_roster_names              UnicodeDecodeError  {}
    council_models._load_config                   UnicodeDecodeError  {}
    council_models.set_model                      UnicodeDecodeError  RuntimeError(named path)
    embeddings._index_config                      UnicodeDecodeError  {}
    memory_ops_log.read_recall_log                UnicodeDecodeError  []

Three more sites from the same batch were driven and WITHDRAWN, because they did
not reproduce:

  - `.claude/hooks/_dispatch.py::_last_operator_prompt` reads the transcript in
    BINARY and its `_loads_or_none` helper already catches
    `(ValueError, UnicodeDecodeError)`. Measured: returned None (a refusal).
  - `scripts/bridge_daemon/_jsonl.py::read_jsonl_capped` reads binary and
    decodes with `errors="replace"`, which cannot raise. Measured: `([], False)`.
  - `scripts/utils/impeccable_engine.py::load_profiles` already carries
    `UnicodeDecodeError` in its handler tuple. Measured: the documented
    screen-only fallback with a warning.

The second half of the defect is SILENCE. A dropped record that nothing reports
turns a count into a lower bound that reads as a total: an unreadable tasks.md
renders as "no tasks due", an unreadable state.json as "inbox clear", an
unreadable recall log as "no recalls happened". So every test here asserts not
only the degraded return but that the SKIPPED FILE IS NAMED -- in the daemon log
for the bridge sources, on stderr for the CLI-side readers, in the returned
structure for `read_conversation` and `set_model`.

THE OVER-REFUSAL ANCHOR. A "fix" that skipped every file containing a high byte
would satisfy all of the above while silently dropping a third of a real corpus:
accented UTF-8 is ordinary in a calendar subject, a contact name and a roster.
So each reader is also driven with REAL, VALID accented UTF-8 (the word
"cafe" with an acute e, written here as an escape so this file stays ASCII) and
must still read it, with nothing logged.

THE CLEAN-PATH ANCHOR. Each reader is also driven with plain ASCII content and
must return exactly what it returned before the widening.

Two of the nine hold operator content and were widened in the REFUSING
direction on purpose:

  - `load_roster_names` answers "who is on the roster". Its `{}` costs `main()`
    its denominator, which then prints "started 12/?" rather than inventing a
    tribe size, and it never widens membership. Asserted below.
  - `set_model` REFUSES to rewrite a config it could not read, so a bad byte
    cannot erase the operator's other pins. It refused before this change too,
    but as a bare `UnicodeDecodeError` naming no path; now it is the documented
    RuntimeError that names the file and the remedy. Asserted below.
"""
import importlib.util
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# One lone 0xe9. Not valid UTF-8 in any position: 0xe9 opens a 3-byte sequence
# and what follows it here is not a continuation byte.
BAD = b'{"a": \xe9}\n'
# REAL accented UTF-8. The over-refusal anchor: this MUST still read.
CAFE = "café"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob if isinstance(blob, bytes) else blob.encode("utf-8"))
    return path


def _named(text: str, path: Path) -> bool:
    """Is this file named in that message? Path, not just the bare basename."""
    return str(path) in text


# ======================================================================
# 1. scripts/bridge_daemon/refreshers/mail.py :: read_email_state
# ======================================================================

def _state_json(root: Path) -> Path:
    return root / "outputs" / "operations" / "email-intelligence" / "state.json"


def test_mail_state_that_will_not_decode_degrades_and_names_the_file(tmp_path, caplog):
    from scripts.bridge_daemon.refreshers.mail import read_email_state
    f = _write(_state_json(tmp_path), BAD)
    with caplog.at_level(logging.WARNING):
        assert read_email_state(tmp_path) == {"messages": []}
    assert _named(caplog.text, f), caplog.text


def test_mail_state_with_real_accented_utf8_still_reads(tmp_path, caplog):
    from scripts.bridge_daemon.refreshers.mail import read_email_state
    _write(_state_json(tmp_path), json.dumps({"messages": [{"from": CAFE}]}))
    with caplog.at_level(logging.WARNING):
        got = read_email_state(tmp_path)
    assert got == {"messages": [{"from": CAFE}]}
    assert caplog.text == "", "a valid accented file must not be skipped"


def test_mail_state_clean_path_unchanged(tmp_path):
    from scripts.bridge_daemon.refreshers.mail import read_email_state
    _write(_state_json(tmp_path), '{"messages": [1, 2]}')
    assert read_email_state(tmp_path) == {"messages": [1, 2]}
    # And the two pre-existing degraded paths still degrade the same way.
    assert read_email_state(tmp_path / "nope") == {"messages": []}
    _write(_state_json(tmp_path), "[1, 2]")
    assert read_email_state(tmp_path) == {"messages": []}


# ======================================================================
# 2. scripts/bridge_daemon/sources/agenda.py :: today_agenda
# ======================================================================

def _cal(root: Path, now) -> Path:
    from scripts.utils.workspace import get_default_tz
    ds = now.astimezone(get_default_tz()).strftime("%Y-%m-%d")
    return root / "outputs" / "_sync" / "calendar" / f"{ds}.md", ds


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_calendar_that_will_not_decode_degrades_and_names_the_file(tmp_path, caplog):
    from scripts.bridge_daemon.sources.agenda import today_agenda
    path, ds = _cal(tmp_path, NOW)
    f = _write(path, b"| 09:00 | board \xe9 review | - | 60m |\n")
    with caplog.at_level(logging.WARNING):
        got = today_agenda(tmp_path, now=NOW)
    assert got == {"date": ds, "events": [], "data_time": None}
    assert _named(caplog.text, f), caplog.text


def test_calendar_with_real_accented_utf8_still_reads(tmp_path, caplog):
    from scripts.bridge_daemon.sources.agenda import today_agenda
    path, _ds = _cal(tmp_path, NOW)
    _write(path, f"| 09:00 | {CAFE} standup | - | 60m |\n")
    with caplog.at_level(logging.WARNING):
        got = today_agenda(tmp_path, now=NOW)
    assert [e["subject"] for e in got["events"]] == [f"{CAFE} standup"]
    assert caplog.text == "", "a valid accented calendar must not be skipped"


def test_calendar_clean_path_unchanged(tmp_path):
    from scripts.bridge_daemon.sources.agenda import today_agenda
    path, ds = _cal(tmp_path, NOW)
    _write(path, "| 09:00 | Standup | - | 15m |\n")
    got = today_agenda(tmp_path, now=NOW)
    assert got["date"] == ds
    assert [e["subject"] for e in got["events"]] == ["Standup"]
    assert got["data_time"] is not None
    # The absent-file path is untouched.
    assert today_agenda(tmp_path / "nope", now=NOW) == {
        "date": ds, "events": [], "data_time": None}


# ======================================================================
# 3. scripts/bridge_daemon/sources/inbox.py :: read_conversation
#    plus the three siblings in the same file that read the same two files
# ======================================================================

def _fetch(root: Path) -> Path:
    from scripts.bridge_daemon.sources.inbox import LATEST_FETCH_FILE
    return root / LATEST_FETCH_FILE


def test_conversation_fetch_that_will_not_decode_refuses_and_names_the_file(tmp_path):
    from scripts.bridge_daemon.sources.inbox import read_conversation
    f = _write(_fetch(tmp_path), BAD)
    got = read_conversation(tmp_path, "c1")
    assert got["ok"] is False
    assert _named(got["error"], f), got["error"]


def test_conversation_with_real_accented_utf8_still_reads(tmp_path, caplog):
    from scripts.bridge_daemon.sources.inbox import read_conversation
    _write(_fetch(tmp_path), json.dumps(
        {"conversations": [{"id": "c1", "topic": f"{CAFE} sync", "raw_emails": []}]}))
    with caplog.at_level(logging.WARNING):
        got = read_conversation(tmp_path, "c1")
    assert got["ok"] is True
    assert got["conversation"]["topic"] == f"{CAFE} sync"
    assert caplog.text == "", "a valid accented fetch must not be skipped"


def test_conversation_clean_path_unchanged(tmp_path):
    from scripts.bridge_daemon.sources.inbox import read_conversation
    _write(_fetch(tmp_path), json.dumps(
        {"conversations": [{"id": "c1", "topic": "Sync", "raw_emails": []}]}))
    assert read_conversation(tmp_path, "c1")["conversation"]["topic"] == "Sync"
    assert read_conversation(tmp_path, "")["ok"] is False
    assert read_conversation(tmp_path, "missing")["ok"] is False
    assert read_conversation(tmp_path / "nope", "c1")["ok"] is False


def test_the_state_fallback_read_conversation_degrades_onto_is_widened_too(tmp_path, caplog):
    """The sibling that finding #1 of this campaign is about.

    `read_conversation` falls back to `_read_state_conversation` when the fetch
    file is absent. Widening the first reader and not the second leaves the
    promise unkept on the very path it degrades ONTO: with no fetch file at all
    and a state.json holding one bad byte, the pre-fix tree raised.
    """
    from scripts.bridge_daemon.sources.inbox import read_conversation
    f = _write(_state_json(tmp_path), BAD)
    with caplog.at_level(logging.WARNING):
        got = read_conversation(tmp_path, "c1")
    assert got["ok"] is False
    assert _named(caplog.text, f), caplog.text


def test_fetch_topics_sibling_degrades_and_names_the_file(tmp_path, caplog):
    from scripts.bridge_daemon.sources import inbox
    f = _write(_fetch(tmp_path), BAD)
    with caplog.at_level(logging.WARNING):
        assert inbox._fetch_topics(tmp_path) == {}
    assert _named(caplog.text, f), caplog.text


def test_dismiss_log_recent_sibling_degrades_and_names_the_file(tmp_path, caplog):
    from scripts.bridge_daemon.sources import inbox
    inbox.mark_dismissed(tmp_path, "c1", "noise")
    f = _write(_fetch(tmp_path), BAD)
    with caplog.at_level(logging.WARNING):
        rows = inbox.dismiss_log_recent(tmp_path)
    # Degraded, not empty: the rows survive, only the readable label is lost.
    assert [r["conv_id"] for r in rows] == ["c1"]
    assert _named(caplog.text, f), caplog.text


# ======================================================================
# 4. scripts/bridge_daemon/sources/tasks.py :: list_active_tasks
# ======================================================================

def _tasks_md(root: Path) -> Path:
    return root / "outputs" / "operations" / "viraid" / "tasks.md"


ROW = "- [ ] **2026-09-01** | `P1` | {body}\n"


def test_tasks_that_will_not_decode_degrades_and_names_the_file(tmp_path, caplog):
    from scripts.bridge_daemon.sources.tasks import list_active_tasks
    f = _write(_tasks_md(tmp_path),
               b"## Active\n" + ROW.format(body="ship \xe9 it").encode("latin-1"))
    with caplog.at_level(logging.WARNING):
        got = list_active_tasks(tmp_path)
    assert got["tasks"] == [] and got["data_time"] is None
    assert _named(caplog.text, f), caplog.text


def test_tasks_with_real_accented_utf8_still_reads(tmp_path, caplog):
    from scripts.bridge_daemon.sources.tasks import list_active_tasks
    _write(_tasks_md(tmp_path), "## Active\n" + ROW.format(body=f"book {CAFE}"))
    with caplog.at_level(logging.WARNING):
        got = list_active_tasks(tmp_path)
    assert len(got["tasks"]) == 1
    assert CAFE in got["tasks"][0]["description"]
    assert caplog.text == "", "a valid accented tasks.md must not be skipped"


def test_tasks_clean_path_unchanged(tmp_path):
    from scripts.bridge_daemon.sources.tasks import list_active_tasks
    _write(_tasks_md(tmp_path), "## Active\n" + ROW.format(body="ship it"))
    got = list_active_tasks(tmp_path)
    assert [t["description"] for t in got["tasks"]] == ["ship it"]
    assert got["data_time"] is not None
    absent = list_active_tasks(tmp_path / "nope")
    assert absent["tasks"] == [] and absent["data_time"] is None


# ======================================================================
# 5. scripts/fireside-pulse.py :: load_roster_names  (REFUSING direction)
# ======================================================================

@pytest.fixture()
def fp(tmp_path, monkeypatch):
    mod = _load("fireside_pulse_decode_b2", "scripts/fireside-pulse.py")
    monkeypatch.setattr(mod, "state_dir", lambda: tmp_path / "fireside-state")
    return mod


def test_roster_that_will_not_decode_degrades_and_names_the_file(fp, tmp_path, capsys):
    f = _write(fp.state_dir() / "tribe-roster.json", BAD)
    assert fp.load_roster_names() == {}
    err = capsys.readouterr().err
    assert _named(err, f), err


def test_roster_degrades_in_the_refusing_direction(fp, tmp_path):
    """`{}`, so the caller has NO denominator, rather than a guessed one.

    A reader of a membership file that cannot read it must not widen who is on
    the roster and must not carry a previous answer forward. `main()` turns a
    falsy `tribe_size` into the string "?" rather than a number nothing
    measured; that is the whole point of returning the empty mapping.
    """
    _write(fp.state_dir() / "tribe-roster.json", BAD)
    names = fp.load_roster_names()
    assert names == {}
    tribe_size = len(names)
    assert (str(tribe_size) if tribe_size else "?") == "?"


def test_roster_with_real_accented_utf8_still_reads(fp, capsys):
    _write(fp.state_dir() / "tribe-roster.json",
           json.dumps({"k1": {"telegram_user_id": 7, "name": CAFE}}))
    assert fp.load_roster_names() == {7: CAFE}
    assert capsys.readouterr().err == "", "a valid accented roster must not be skipped"


def test_roster_clean_path_unchanged(fp, capsys):
    path = fp.state_dir() / "tribe-roster.json"
    _write(path, json.dumps({"k1": {"telegram_user_id": 7, "name": "Ann"}}))
    assert fp.load_roster_names() == {7: "Ann"}
    # Absent file: silent {}. Non-object: {} with a message. Both pre-existing.
    path.unlink()
    assert fp.load_roster_names() == {}
    assert capsys.readouterr().err == ""
    _write(path, "[1, 2]")
    assert fp.load_roster_names() == {}
    assert "not an" in capsys.readouterr().err


# ======================================================================
# 6/7. scripts/utils/council_models.py :: _load_config and set_model
# ======================================================================

@pytest.fixture()
def cm(tmp_path, monkeypatch):
    from scripts.utils import council_models
    cfg = tmp_path / "config" / "council-models.json"
    monkeypatch.setattr(council_models, "config_path", lambda: cfg)
    return council_models, cfg


def test_council_config_that_will_not_decode_degrades_and_names_the_file(cm, capsys):
    council_models, cfg = cm
    _write(cfg, BAD)
    assert council_models._load_config() == {}
    err = capsys.readouterr().err
    assert _named(err, cfg), err


def test_council_set_model_refuses_over_a_file_it_cannot_decode(cm):
    """The REFUSING direction, and the reason it matters.

    `set_model` preserves the operator's other pins by reading them first. A
    config it cannot read must not be rewritten from `{}`, which would erase
    every other pin. It refused before this widening too -- but as a bare
    `UnicodeDecodeError` naming no path and offering no remedy.
    """
    council_models, cfg = cm
    _write(cfg, BAD)
    with pytest.raises(RuntimeError) as excinfo:
        council_models.set_model("kimi", "kimi-k3")
    assert _named(str(excinfo.value), cfg), str(excinfo.value)
    assert cfg.read_bytes() == BAD, "the unreadable config must be left alone"


def test_council_config_with_real_accented_utf8_still_reads(cm, capsys):
    council_models, cfg = cm
    _write(cfg, json.dumps({"kimi": f"kimi-{CAFE}"}, ensure_ascii=False))
    assert council_models._load_config() == {"kimi": f"kimi-{CAFE}"}
    assert capsys.readouterr().err == "", "a valid accented config must not be skipped"


def test_council_clean_paths_unchanged(cm, capsys):
    council_models, cfg = cm
    # Absent: silent {}, and set_model creates it.
    assert council_models._load_config() == {}
    assert capsys.readouterr().err == ""
    council_models.set_model("kimi", "kimi-k3")
    assert council_models._load_config() == {"kimi": "kimi-k3"}
    # A second pin preserves the first.
    council_models.set_model("grok", "grok-9")
    assert council_models._load_config() == {"kimi": "kimi-k3", "grok": "grok-9"}
    # Malformed-but-decodable still warns and falls back, as before.
    _write(cfg, "{not json")
    assert council_models._load_config() == {}
    assert "could not read" in capsys.readouterr().err
    with pytest.raises(ValueError):
        council_models.set_model("nobody", "x")


# ======================================================================
# 8. scripts/utils/embeddings.py :: _index_config  (yaml over a text handle)
# ======================================================================

def _index_yaml(root: Path) -> Path:
    return root / "config" / "memory-index.yaml"


def test_index_config_that_will_not_decode_degrades_and_names_the_file(tmp_path, capsys):
    from scripts.utils.embeddings import _index_config
    f = _write(_index_yaml(tmp_path), b"host: caf\xe9\n")
    assert _index_config(root=tmp_path) == {}
    err = capsys.readouterr().err
    assert _named(err, f), err


def test_index_config_with_real_accented_utf8_still_reads(tmp_path, capsys):
    from scripts.utils.embeddings import _index_config
    _write(_index_yaml(tmp_path), f"host: {CAFE}\n")
    assert _index_config(root=tmp_path) == {"host": CAFE}
    assert capsys.readouterr().err == "", "a valid accented config must not be skipped"


def test_index_config_clean_paths_unchanged(tmp_path):
    from scripts.utils.embeddings import _index_config
    _write(_index_yaml(tmp_path), "host: 127.0.0.1:11434\nchunk: 400\n")
    assert _index_config(root=tmp_path) == {"host": "127.0.0.1:11434", "chunk": 400}
    # Absent file and an empty file both stay {}.
    assert _index_config(root=tmp_path / "nope") == {}
    _write(_index_yaml(tmp_path), "")
    assert _index_config(root=tmp_path) == {}


# ======================================================================
# 9. scripts/utils/memory_ops_log.py :: read_recall_log
# ======================================================================

@pytest.fixture()
def mol(tmp_path, monkeypatch):
    from scripts.utils import memory_ops_log
    log = tmp_path / "memory-ops" / "recall.jsonl"
    monkeypatch.setattr(memory_ops_log, "_recall_log_path", lambda: log)
    return memory_ops_log, log


def test_recall_log_that_will_not_decode_degrades_and_names_the_file(mol, capsys):
    memory_ops_log, log = mol
    _write(log, BAD)
    assert memory_ops_log.read_recall_log() == []
    err = capsys.readouterr().err
    assert _named(err, log), err


def test_recall_log_with_real_accented_utf8_still_reads(mol, capsys):
    memory_ops_log, log = mol
    _write(log, json.dumps({"query_snippet": CAFE}, ensure_ascii=False) + "\n")
    assert memory_ops_log.read_recall_log() == [{"query_snippet": CAFE}]
    assert capsys.readouterr().err == "", "a valid accented log must not be skipped"


def test_recall_log_clean_paths_unchanged(mol, capsys):
    memory_ops_log, log = mol
    _write(log, '{"a": 1}\n\n{"b": 2}\n')
    assert memory_ops_log.read_recall_log() == [{"a": 1}, {"b": 2}]
    # Absent file: silent []. A torn line still costs only itself.
    log.unlink()
    assert memory_ops_log.read_recall_log() == []
    assert capsys.readouterr().err == ""
    _write(log, '{"a": 1}\n{torn\n{"b": 2}\n')
    assert memory_ops_log.read_recall_log() == [{"a": 1}, {"b": 2}]


# ======================================================================
# The three sites driven and WITHDRAWN. Anchored so a later sweep that
# re-reports them can be answered with a run rather than an argument.
# ======================================================================

def test_withdrawn_dispatch_last_operator_prompt_already_refuses(tmp_path):
    d = _load("dispatch_decode_b2", ".claude/hooks/_dispatch.py")
    t = _write(tmp_path / "transcript.jsonl",
               b'{"type":"last-prompt","lastPrompt":"\xe9 hi"}\n')
    assert d._last_operator_prompt(str(t)) is None
    # And a VALID accented prompt still comes back verbatim.
    t2 = _write(tmp_path / "ok.jsonl",
                json.dumps({"type": "last-prompt",
                            "lastPrompt": f"go to {CAFE}"}) + "\n")
    assert d._last_operator_prompt(str(t2)) == f"go to {CAFE}"


def test_withdrawn_read_jsonl_capped_decodes_with_replace(tmp_path):
    from scripts.bridge_daemon._jsonl import read_jsonl_capped
    p = _write(tmp_path / "log.jsonl", BAD)
    assert read_jsonl_capped(p, 1 << 20) == ([], False)
    p2 = _write(tmp_path / "ok.jsonl",
                json.dumps({"who": CAFE}, ensure_ascii=False) + "\n")
    assert read_jsonl_capped(p2, 1 << 20) == ([{"who": CAFE}], False)


def test_withdrawn_load_profiles_already_carries_unicodedecodeerror(tmp_path):
    from scripts.utils.impeccable_engine import load_profiles
    profiles, warning = load_profiles(_write(tmp_path / "p.json", BAD))
    assert profiles["default"] == "screen"
    assert "unreadable" in warning


# ======================================================================
# The class itself, asserted once rather than assumed nine times.
# ======================================================================

def test_unicodedecodeerror_is_a_sibling_of_jsondecodeerror_not_a_subclass():
    assert issubclass(UnicodeDecodeError, ValueError)
    assert issubclass(json.JSONDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, json.JSONDecodeError)
    assert not issubclass(UnicodeDecodeError, OSError)
    import yaml
    assert not issubclass(UnicodeDecodeError, yaml.YAMLError)
