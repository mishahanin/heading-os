"""Seven readers caught `OSError` around a `read_text` and let the decode out.

`UnicodeDecodeError` is a `ValueError`. It is not an `OSError`, and it is not a
`json.JSONDecodeError` either: the decode fails inside `read_text` BEFORE
`json.loads` is ever called, so a handler naming those two types never sees it.
Every reader below promised a degraded answer for a file it could not read, and
delivered that answer for a truncated one, a missing one and a wrongly-shaped
one, while a single byte that is not UTF-8 raised straight through.

MEASURED 2026-09-01, each by writing one file holding `b"\\xff"` and calling the
reader:

    action_queue._load_queue      UnicodeDecodeError, and NOT quarantined
    sessions.read_registry        UnicodeDecodeError
    auth.get_or_create_token      UnicodeDecodeError
    refreshers.pulse.read_snapshot UnicodeDecodeError
    crm_log.log_to_crm            UnicodeDecodeError (both of its two reads)
    admin-health.find_shared_contacts  UnicodeDecodeError
    sentinel._load_business_context    UnicodeDecodeError

Two of the seven are worse than a 500. `_load_queue`'s exception skipped
`_quarantine_corrupt_queue`, so the one corruption that arrives from a torn
write stayed in the live path for the next writer to overwrite, which is the
whole loss that function exists to prevent. `get_or_create_token` runs during
daemon startup, so the bridge did not boot at all.

`scripts/bridge_daemon/config.py` already named `UnicodeDecodeError` in its
tuple, and `sources/capabilities.py` and `sources/contacts.py` already named it
in theirs. This is the same fix applied to the copies that had not had it.

The other jaw is held throughout: every reader below also gets a well-formed
input in the same test, so a handler widened into a blanket swallow fails here.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# One byte that no UTF-8 decoder accepts, in a position a torn write reaches.
BAD_BYTE = b"\xff"


def _load_script(module_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(module_name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1 - the action queue, which also lost its quarantine
# ============================================================

def test_an_undecodable_queue_is_quarantined_rather_than_raised(tmp_path, caplog):
    from scripts.bridge_daemon.sources import action_queue as aq

    q = tmp_path / aq.QUEUE_FILE
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_bytes(b'{"actions": [{"id": "card-1", "body": "' + BAD_BYTE + b'"}]}')

    with caplog.at_level(logging.ERROR):
        loaded = aq._load_queue(tmp_path)

    assert loaded["actions"] == []
    assert not q.exists(), "the wreck must not still be in the live path"
    wrecks = list(q.parent.glob(".quarantine/queue.json.corrupt-*"))
    assert len(wrecks) == 1, (
        "an undecodable queue skipped the quarantine entirely, so the next "
        "write overwrites every pending draft"
    )
    assert b"card-1" in wrecks[0].read_bytes(), "the bytes must survive"
    assert "unreadable" in caplog.text


def test_a_readable_queue_is_still_returned_untouched(tmp_path):
    """The widened handler must not have become a blanket swallow."""
    from scripts.bridge_daemon.sources import action_queue as aq

    q = tmp_path / aq.QUEUE_FILE
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(json.dumps({"version": 1, "actions": [{"id": "a"}]}),
                 encoding="utf-8")

    assert aq._load_queue(tmp_path)["actions"] == [{"id": "a"}]
    assert q.exists()
    assert not list(q.parent.glob(".quarantine/*"))


# ============================================================
# 2 - the session registry behind /launch
# ============================================================

def test_an_undecodable_session_registry_reads_as_empty(tmp_path):
    from scripts.bridge_daemon import sessions

    reg = tmp_path / "active-sessions.json"
    reg.write_bytes(b'{"s1": {"cwd": "' + BAD_BYTE + b'"}}')

    assert sessions.read_registry(reg) == {}
    assert sessions.session_for_cwd(reg, "/anywhere") is None


def test_a_readable_session_registry_still_resolves(tmp_path):
    from scripts.bridge_daemon import sessions

    reg = tmp_path / "active-sessions.json"
    reg.write_text(json.dumps({
        "s1": {"session_id": "s1", "cwd": "/w",
               "started_at": "2026-08-25T00:00:00+00:00"},
    }), encoding="utf-8")

    assert sessions.session_for_cwd(reg, "/w") == "s1"


# ============================================================
# 3 - the token file, read during daemon startup
# ============================================================

def test_an_undecodable_token_file_is_regenerated(tmp_path, caplog):
    from scripts.bridge_daemon import auth

    token_file = tmp_path / ".daemon-state" / "token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_bytes(BAD_BYTE + b"\xfe not a token")

    with caplog.at_level(logging.WARNING):
        token = auth.get_or_create_token(tmp_path)

    assert token, "the daemon could not boot at all over this file"
    assert auth.validate(token, token)
    assert "unreadable" in caplog.text


def test_a_readable_token_file_is_still_left_alone(tmp_path):
    from scripts.bridge_daemon import auth

    token_file = tmp_path / ".daemon-state" / "token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("a-real-token", encoding="utf-8")

    assert auth.get_or_create_token(tmp_path) == "a-real-token"


# ============================================================
# 4 - the pulse snapshot behind the dashboard's first page
# ============================================================

def test_an_undecodable_pulse_snapshot_is_a_miss(tmp_path):
    from scripts.bridge_daemon.refreshers import pulse as pulse_ref

    snap = pulse_ref.snapshot_path(tmp_path)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(b'{"data": {"x": "' + BAD_BYTE + b'"}, "computed_at": "x"}')

    assert pulse_ref.read_snapshot(tmp_path) is None


def test_a_readable_pulse_snapshot_is_still_returned(tmp_path):
    from scripts.bridge_daemon.refreshers import pulse as pulse_ref

    snap = pulse_ref.snapshot_path(tmp_path)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps({"data": {"x": 1},
                                "computed_at": "2026-08-24T00:00:00+00:00"}),
                    encoding="utf-8")

    got = pulse_ref.read_snapshot(tmp_path)
    assert got is not None and got["data"] == {"x": 1}


# ============================================================
# 5 - the CRM-log finalizer, both of its reads
# ============================================================

def _crm_fixture(root: Path, conv_id: str, slug: str) -> None:
    from scripts.bridge_daemon.sources.inbox import LATEST_FETCH_FILE

    fetch = root / LATEST_FETCH_FILE
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(json.dumps({"conversations": [{
        "id": conv_id,
        "topic": "A placeholder subject",
        "latest_datetime": "2026-05-20T09:00:00+00:00",
        "crm_context": {"contact_slug": slug},
    }]}), encoding="utf-8")
    contacts = root / "crm" / "contacts"
    contacts.mkdir(parents=True, exist_ok=True)
    (contacts / f"{slug}.md").write_text(
        f"---\nslug: {slug}\n---\n\n## Interaction Log\n", encoding="utf-8")


def test_an_undecodable_fetch_file_answers_rather_than_raising(tmp_path):
    from scripts.bridge_daemon.finalizers import crm_log
    from scripts.bridge_daemon.sources.inbox import LATEST_FETCH_FILE

    _crm_fixture(tmp_path, "conv-1", "james-bond")
    (tmp_path / LATEST_FETCH_FILE).write_bytes(
        b'{"conversations": [{"id": "' + BAD_BYTE + b'"}]}')

    result = crm_log.log_to_crm("conv-1", data_root=tmp_path)

    assert result["ok"] is False
    assert "unreadable" in result["error"]


def test_an_undecodable_contact_file_answers_rather_than_raising(tmp_path):
    from scripts.bridge_daemon.finalizers import crm_log

    _crm_fixture(tmp_path, "conv-2", "james-bond")
    (tmp_path / "crm" / "contacts" / "james-bond.md").write_bytes(
        b"---\nslug: james-bond\nnote: " + BAD_BYTE + b"\n---\n")

    result = crm_log.log_to_crm("conv-2", data_root=tmp_path)

    assert result["ok"] is False
    assert "CRM write failed" in result["error"]


def test_a_readable_contact_still_gets_its_interaction_logged(tmp_path):
    from scripts.bridge_daemon.finalizers import crm_log

    _crm_fixture(tmp_path, "conv-3", "james-bond")

    result = crm_log.log_to_crm("conv-3", data_root=tmp_path)

    assert result["ok"] is True, result
    text = (tmp_path / "crm" / "contacts" / "james-bond.md").read_text(
        encoding="utf-8")
    assert "2026-05-20" in text
    assert "A placeholder subject" in text


# ============================================================
# 6 - the fleet dashboard, which died on the row it could not decode
# ============================================================

@pytest.fixture(scope="module")
def health():
    return _load_script("admin_health_utf8_probe", "scripts/admin-health.py")


def test_one_undecodable_contact_does_not_kill_the_fleet_dashboard(
        health, tmp_path, monkeypatch, capsys):
    """Same shape as the `collect_exec_state` NameError this script was fixed
    for: one bad row, and the whole table is gone rather than one row."""
    dirs = {}
    for slug in ("bond", "leiter"):
        d = tmp_path / slug / "crm" / "contacts"
        d.mkdir(parents=True)
        (d / "shared.md").write_text("---\nname: Vesper Lynd\n---\n",
                                     encoding="utf-8")
        dirs[slug] = d
    (dirs["bond"] / "mojibake.md").write_bytes(b"---\nname: " + BAD_BYTE + b"\n---\n")

    monkeypatch.setattr(health, "get_per_exec_contacts_dir", lambda s: dirs[s])

    shared = health.find_shared_contacts([("bond", tmp_path / "b"),
                                          ("leiter", tmp_path / "l")])

    assert shared == 1, "the readable rows must still be counted"
    assert "unreadable contact" in capsys.readouterr().err


# ============================================================
# 7 - the always-on daemon's business context
# ============================================================

def test_an_undecodable_context_file_does_not_stop_the_daemon(tmp_path, caplog):
    sn = _load_script("sentinel_utf8_probe", "scripts/sentinel.py")

    good = tmp_path / "strategy.md"
    good.write_text("The heading for this quarter.\n", encoding="utf-8")
    bad = tmp_path / "people.md"
    bad.write_bytes(b"# People\n" + BAD_BYTE + b"\n")

    analyzer = sn.UrgencyAnalyzer.__new__(sn.UrgencyAnalyzer)
    analyzer.logger = logging.getLogger("probe")
    analyzer.business_context = ""
    analyzer._resolve_context_file = lambda rel: tmp_path / rel

    with caplog.at_level(logging.WARNING):
        analyzer._load_business_context(["strategy.md", "people.md"])

    assert "The heading for this quarter." in analyzer.business_context, (
        "the readable context file must still reach the scoring prompt")
    assert "people.md" not in analyzer.business_context
    assert "Could not read context file people.md" in caplog.text
