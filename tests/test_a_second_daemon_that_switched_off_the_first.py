"""Shard scripts-00-p4: the bridge daemon entry point.

Seven defects, one of which turns a healthy daemon invisible:

- Nothing stopped a second `--start`. It bound the next port, took over the
  singleton `.daemon-state/port`, and its exit deleted that file while the
  first daemon was still bound and serving. `--health` then said "not running".
- `_sweep_non_gated_cards` auto-applied EVERY notify-tier card while its
  docstring scoped auto-apply to `pipeline_update`, so a `config/tool-risk.json`
  edit alone could flip CEO-facing cards to `applied` with nothing executed.
- That same docstring said it disposed autonomous cards (it never did) and
  pointed at a "send executor below" removed on 2026-06-27.
- `show_status` promised tab-separated output for `cut -f` and joined with two
  spaces, under a field list missing two of the fields it prints.
- A comment in the cleanup clause said the probe-then-bind race was still open.
  The held-listener design in the same file is what closed it.
- `check_health` took exit 2 ("neither could be read") on a corrupt port file
  without ever reading the heartbeat that was sitting right there.
- `rotate_token` printed the first 16 characters of a live bearer token.

The daemon is stopped and disabled by operator decision, so nothing here starts
one, binds a real port, or reaches the network.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DAEMON = ROOT / "scripts" / "bridge-daemon.py"


@pytest.fixture(scope="module")
def entry():
    spec = importlib.util.spec_from_file_location("bridge_daemon_entry_p4", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_daemon_entry_p4"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def state_dir(entry, tmp_path, monkeypatch):
    """Point the module's WORKSPACE_ROOT at a scratch tree."""
    (tmp_path / ".daemon-state").mkdir()
    monkeypatch.setattr(entry, "WORKSPACE_ROOT", tmp_path)
    return tmp_path / ".daemon-state"


def _source() -> str:
    """Source with whole-line `#` comments stripped. Docstrings survive."""
    lines = DAEMON.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def _func_source(name: str) -> str:
    tree = ast.parse(DAEMON.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} is gone")


# ============================================================
# The second daemon that switched off the first
# ============================================================

def test_a_live_daemon_is_detected_from_the_port_file(entry, state_dir,
                                                      monkeypatch):
    (state_dir / "port").write_text("31415\n", encoding="utf-8")
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(url, timeout=None):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _open)
    assert entry._live_daemon_port() == 31415
    assert seen["url"] == "http://127.0.0.1:31415/health"


def test_a_stale_port_file_does_not_block_the_next_start(entry, state_dir,
                                                         monkeypatch):
    """A crashed daemon leaves the file behind. That must not wedge --start."""
    (state_dir / "port").write_text("31415\n", encoding="utf-8")

    def _refused(url, timeout=None):
        raise ConnectionRefusedError("nothing there")

    monkeypatch.setattr("urllib.request.urlopen", _refused)
    assert entry._live_daemon_port() is None


def test_no_port_file_is_not_a_live_daemon(entry, state_dir):
    assert entry._live_daemon_port() is None


@pytest.mark.parametrize("blob", ["garbage", "", "0", "70000", "-1", "31415x"])
def test_an_unusable_port_file_is_not_a_live_daemon(entry, state_dir, blob):
    (state_dir / "port").write_text(blob, encoding="utf-8")
    assert entry._live_daemon_port() is None


def test_start_refuses_when_a_daemon_is_already_serving(entry, monkeypatch,
                                                        capsys):
    """The whole defect: instance B's exit unlinked instance A's port file."""
    monkeypatch.setattr(entry, "_live_daemon_port", lambda: 31415)
    with pytest.raises(SystemExit) as exc:
        entry.start_daemon()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "already running" in err and "31415" in err


def test_the_refusal_comes_before_anything_is_bound_or_written():
    """Order is the fix. Checking after the port file is written is no check."""
    src = _func_source("start_daemon")
    guard = src.index("_live_daemon_port")
    for later in ("_pick_port", "_verify_port_free", "atomic_write_text"):
        assert guard < src.index(later), f"{later} runs before the guard"


# ============================================================
# A tier that auto-applied whatever the config called notify
# ============================================================

class _FakeAQ:
    """Stands in for scripts.bridge_daemon.sources.action_queue."""

    def __init__(self, items):
        self._items = items
        self.applied: list[tuple[str, str]] = []

    def list_action_queue(self, _root):
        return {"items": self._items}

    def apply_status(self, _root, aid, status, event=None):
        self.applied.append((aid, event))


def _card(aid, atype, status="pending"):
    return {"id": aid, "action_type": atype, "status": status}


def test_a_pipeline_update_is_still_auto_applied(entry, tmp_path):
    aq = _FakeAQ([_card("a1", "pipeline_update")])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 1
    assert aq.applied == [("a1", "auto_apply")]


def test_an_unlisted_notify_type_is_left_for_the_ceo(entry, tmp_path,
                                                     monkeypatch, caplog):
    """A config edit alone could flip a card to `applied` with nothing done."""
    from scripts.utils import tool_risk
    # The ledger is data; this simulates the config edit, not a code change.
    monkeypatch.setattr(tool_risk, "tier_for",
                        lambda t: tool_risk.NOTIFY if t == "crm_note"
                        else tool_risk.GATED)
    aq = _FakeAQ([_card("a2", "crm_note")])
    with caplog.at_level("WARNING"):
        assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == [], "a type the daemon knows nothing about was applied"
    assert "crm_note" in caplog.text, "it was dropped silently"


def test_the_allowlist_is_the_code_not_the_config(entry):
    assert set(entry._AUTO_APPLY_TYPES) == {"pipeline_update"}


def test_an_autonomous_card_is_never_touched(entry, tmp_path):
    """CEO decision 2026-06-04: notes are surfaced, not swept."""
    aq = _FakeAQ([_card("n1", "note"), _card("n2", "alert")])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == []


def test_the_tier_check_outranks_the_allowlist(entry, tmp_path, monkeypatch):
    """Two conditions on one arm, and the tier one carries the decision.

    Simulates the mistake the CEO decision of 2026-06-04 exists to prevent:
    `note` added to `_AUTO_APPLY_TYPES`. The membership test then passes and
    only `tier == NOTIFY` stands between a note card and `applied`.
    """
    monkeypatch.setattr(entry, "_AUTO_APPLY_TYPES", frozenset({"note"}))
    aq = _FakeAQ([_card("n1", "note")])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == [], "an autonomous card was auto-applied"


def test_no_auto_apply_type_resolves_outside_notify(entry):
    """What the deleted autonomous branch only claimed to guard.

    An explicit `if tier == AUTONOMOUS: continue` sat above the dispatch until
    2026-08-24. It was behaviour-neutral -- the arm below matches `notify`
    alone, so an autonomous card fell through either way -- and mutation
    testing could not kill it, because there was nothing to kill. Deleting dead
    code without replacing what it was supposed to protect would be the wrong
    half of the trade, so the protection lands here: resolved through the real
    ledger, on a check that can fail.
    """
    from scripts.utils import tool_risk
    for atype in sorted(entry._AUTO_APPLY_TYPES):
        tier = tool_risk.tier_for(atype)
        assert tier == tool_risk.NOTIFY, (
            f"{atype!r} is auto-applied by the daemon but the ledger resolves "
            f"it to {tier!r}. Today the sweep would simply never apply it; the "
            "day the tier check moves or loosens, a card the CEO must read or "
            "approve gets applied by a scheduler tick.")


def test_a_gated_card_is_never_touched(entry, tmp_path):
    """The lethal-trifecta control. Nothing here may move a send."""
    aq = _FakeAQ([_card("g1", "email_send")])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == []


def test_a_card_that_is_not_pending_is_skipped(entry, tmp_path):
    aq = _FakeAQ([_card("a3", "pipeline_update", status="applied")])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == []


def test_a_card_missing_its_id_or_type_is_skipped(entry, tmp_path):
    aq = _FakeAQ([{"status": "pending", "action_type": "pipeline_update"},
                  {"status": "pending", "id": "a4"}])
    assert entry._sweep_non_gated_cards(tmp_path, aq) == 0
    assert aq.applied == []


def test_the_sweep_docstring_no_longer_claims_a_send_executor(entry):
    doc = entry._sweep_non_gated_cards.__doc__
    assert "send executor below" not in doc, (
        "the docstring still points at a component removed 2026-06-27")
    assert "REMOVED 2026-06-27" in doc


def test_the_sweep_docstring_no_longer_claims_to_dispose_notes(entry):
    doc = entry._sweep_non_gated_cards.__doc__
    summary = doc.split("\n\n", 1)[0]
    assert "dispose autonomous" not in summary, (
        "the summary contradicts its own body and the CEO decision below it")


# ============================================================
# `cut -f` on a line with no tabs
# ============================================================

def _write_heartbeat(state_dir, **over):
    payload = {"pid": 4242, "uptime_s": 90, "version": "9.9",
               "config_loaded_version": "3", "active_sessions": 2,
               "recent_error_count": 0, "last_heartbeat": "2026-08-24T00:00:00Z"}
    payload.update(over)
    (state_dir / "heartbeat.json").write_text(json.dumps(payload),
                                              encoding="utf-8")


def test_status_is_tab_separated_as_documented(entry, state_dir, capsys):
    (state_dir / "port").write_text("31415", encoding="utf-8")
    _write_heartbeat(state_dir)
    entry.show_status()
    line = capsys.readouterr().out.strip()
    assert "\t" in line, "the documented `cut -f` still has no delimiter"
    assert line.split("\t")[0] == "port=31415"


def test_every_documented_field_is_on_the_line_in_order(entry, state_dir,
                                                        capsys):
    """The docstring's field list stopped two fields short of the output."""
    (state_dir / "port").write_text("31415", encoding="utf-8")
    _write_heartbeat(state_dir)
    entry.show_status()
    keys = [f.split("=", 1)[0] for f in capsys.readouterr().out.strip().split("\t")]
    assert keys == ["port", "pid", "uptime", "version", "config_v",
                    "sessions", "errors", "last_hb"]
    # The FORMAT LINE, not the docstring at large. Prose elsewhere in the
    # docstring names the fields too, and a mutation that shortened only the
    # format line survived a whole-docstring check.
    doc = entry.show_status.__doc__
    fmt = next(ln for ln in doc.splitlines() if ln.strip().startswith("port"))
    assert fmt.split() == keys, f"the documented format line is {fmt.strip()!r}"


def test_cut_f_now_returns_one_field(entry, state_dir, capsys):
    """The literal command the docstring advertises."""
    (state_dir / "port").write_text("31415", encoding="utf-8")
    _write_heartbeat(state_dir)
    entry.show_status()
    line = capsys.readouterr().out.rstrip("\n")
    assert line.split("\t")[1] == "pid=4242"


def test_status_without_either_file_exits_one(entry, state_dir, capsys):
    with pytest.raises(SystemExit) as exc:
        entry.show_status()
    assert exc.value.code == 1
    assert "daemon not started" in capsys.readouterr().err


# ============================================================
# A diagnostic that hid the diagnostics
# ============================================================

def test_a_corrupt_port_file_still_shows_the_heartbeat(entry, state_dir,
                                                       capsys):
    """Exit 2 is documented as "neither could be read". One was right there."""
    (state_dir / "port").write_text("garbage", encoding="utf-8")
    _write_heartbeat(state_dir)
    with pytest.raises(SystemExit) as exc:
        entry.check_health()
    assert exc.value.code == 1, "exit 2 claims the heartbeat was unreadable"
    cap = capsys.readouterr()
    assert "corrupted port file" in cap.err
    assert json.loads(cap.out)["pid"] == 4242


def test_a_corrupt_port_file_with_no_heartbeat_is_still_exit_two(entry,
                                                                 state_dir,
                                                                 capsys):
    (state_dir / "port").write_text("garbage", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        entry.check_health()
    assert exc.value.code == 2
    assert "corrupted port file" in capsys.readouterr().err


# ============================================================
# Sixteen characters of a live bearer token
# ============================================================

def test_rotation_prints_four_characters_not_sixteen(entry, state_dir,
                                                     monkeypatch, capsys):
    # Not a credential: a shaped literal so the assertion below can say
    # exactly which four characters the rotation is allowed to print.
    secret = "SECRETPREFIX_middle_TAIL"  # noqa: S105  # pragma: allowlist secret
    monkeypatch.setattr(entry, "get_or_create_token", lambda _root: secret)
    entry.rotate_token()
    out = capsys.readouterr().out
    assert secret[:16] not in out, "16 characters of the token are in scrollback"
    assert "TAIL" in out, "the operator cannot confirm the rotation"
    assert secret not in out


def test_rotation_still_warns_about_the_running_daemon(entry, state_dir,
                                                       monkeypatch, capsys):
    monkeypatch.setattr(entry, "get_or_create_token", lambda _root: "x" * 40)
    entry.rotate_token()
    assert "old token in memory" in capsys.readouterr().out


# ============================================================
# A comment describing an architecture the file no longer has
# ============================================================

def test_the_cleanup_comment_no_longer_claims_an_open_race():
    raw = DAEMON.read_text(encoding="utf-8")
    assert "This does NOT close the probe-then-bind race" not in raw, (
        "the comment describes a pre-rewrite architecture; _pick_port hands "
        "uvicorn a bound listener")


def test_the_listener_really_is_handed_to_uvicorn():
    """The premise behind removing that comment, pinned."""
    src = _source()
    assert "sockets=[listener]" in src
    assert "return p, _bind_listener(p)" in src
