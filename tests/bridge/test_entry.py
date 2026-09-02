"""Unit tests for the daemon entry point CLI. Focused on testable pure-Python
helpers; the full daemon lifecycle is exercised by manual smoke test (Task 18
plan step 2) and end-to-end smoke (Task 24)."""
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

# scripts/bridge-daemon.py contains a hyphen which is illegal in Python module
# names; load it via importlib so the test can still import _pick_port.
_ENTRY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bridge-daemon.py"


def _load_entry_module():
    spec = importlib.util.spec_from_file_location("bridge_daemon_entry", _ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bridge_daemon_entry"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def entry_module():
    return _load_entry_module()


def test_pick_port_returns_free_port(entry_module):
    """_pick_port returns a port within the requested range when one is free."""
    p, sock = entry_module._pick_port(40000)
    try:
        assert 40000 <= p < 40050
        assert sock.getsockname()[1] == p, "the held socket IS the returned port"
    finally:
        sock.close()


def test_pick_port_holds_the_port_it_returns(entry_module):
    """The point of the change: nothing can take the port after the pick.

    Probing with connect_ex left a window between "this port is free" and
    uvicorn's bind. A second binder could win it, and the daemon then died with
    the port file already advertising a port it never held.
    """
    p, sock = entry_module._pick_port(40100)
    try:
        with (socket.socket(socket.AF_INET, socket.SOCK_STREAM) as thief,
              pytest.raises(OSError)):
            thief.bind(("127.0.0.1", p))
    finally:
        sock.close()


def test_pick_port_releases_the_port_when_the_socket_is_closed(entry_module):
    """Held while open, free once closed. BOTH halves, in that order.

    The close-then-bind half alone asserted nothing about `_pick_port`: an
    unbound port is trivially bindable, so the test stayed green even with the
    `_bind_listener` call replaced by a bare unbound socket. The hold has to be
    observed first, or the release below is a statement about the OS rather
    than about this function.
    """
    p, sock = entry_module._pick_port(40200)
    try:
        with (socket.socket(socket.AF_INET, socket.SOCK_STREAM) as thief,
              pytest.raises(OSError)):
            thief.bind(("127.0.0.1", p))
    finally:
        sock.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as after:
        after.bind(("127.0.0.1", p))  # must not raise


def test_pick_port_skips_occupied_port(entry_module):
    """When the starting port is occupied, _pick_port advances to the next free port."""
    # Bind a listening socket on a known port to force _pick_port to skip it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupier.bind(("127.0.0.1", 41000))
        occupier.listen(1)
        chosen, sock = entry_module._pick_port(41000)
        try:
            # Must not have returned the occupied port.
            assert chosen != 41000
            assert 41000 < chosen < 41050
        finally:
            sock.close()


def test_pick_port_raises_when_range_exhausted(entry_module):
    """If all 50 ports in the range are occupied, _pick_port raises RuntimeError.

    Instead of binding 50 sockets (slow + flaky), make every bind fail. The
    patch target moved with the implementation: the pick BINDS now, so patching
    `connect_ex` -- which the old probe used -- would silently find a real free
    port and the test would pass while asserting nothing.
    """
    import unittest.mock as mock

    with mock.patch.object(socket.socket, "bind", side_effect=OSError("in use")):
        with pytest.raises(RuntimeError, match="no free port"):
            entry_module._pick_port(42000)


# Phase S - --port override + _verify_port_free tests.


def test_verify_port_free_returns_port_when_free(entry_module):
    """A free port is returned unchanged, with its socket held."""
    port, sock = entry_module._verify_port_free(43000)
    try:
        assert port == 43000
        assert sock.getsockname()[1] == 43000
    finally:
        sock.close()


def test_verify_port_free_raises_when_port_busy(entry_module):
    """An occupied port raises RuntimeError with the port number in the message."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupier.bind(("127.0.0.1", 43100))
        occupier.listen(1)
        with pytest.raises(RuntimeError, match="already in use"):
            entry_module._verify_port_free(43100)


def test_verify_port_free_rejects_out_of_range(entry_module):
    """Ports outside 1..65535 are rejected (fail-fast on bad CLI input)."""
    for bad in (0, -1, 65536, 99999):
        with pytest.raises(RuntimeError, match="out of range"):
            entry_module._verify_port_free(bad)


def test_version_flag_prints_and_exits(entry_module, capsys):
    """--version prints 'bridge-daemon <version>' and exits 0 (argparse standard)."""
    import importlib
    import scripts.bridge_daemon.version as ver_mod
    importlib.reload(ver_mod)  # ensure fresh import
    # `main()` reads sys.argv via argparse, so the flag is injected there.
    #
    # Fixed 2026-08-30. This used to be a ternary:
    #
    #     entry_module.main.__wrapped__() if hasattr(main, "__wrapped__") else (
    #         _run_main_with_args(entry_module, ["--version"]))
    #
    # and the `--version` injection lived only in the `else` arm. Decorating
    # `main` with anything that uses `functools.wraps` would give it a
    # `__wrapped__`, and the test would then call it bare: argparse would parse
    # PYTEST'S OWN argv, exit 2 on the unrecognised arguments, and the assertion
    # below would fail on correct code. Calling `__wrapped__` also skips
    # whatever the decorator does, so even a green run would not be testing
    # `main` as production invokes it. The branch was dead today and would have
    # detonated the first time anyone decorated `main`.
    with pytest.raises(SystemExit) as exc:
        _run_main_with_args(entry_module, ["--version"])
    # argparse exits 0 on --version.
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "bridge-daemon" in out
    assert ver_mod.__version__ in out


def _run_main_with_args(mod, argv):
    """Helper: run mod.main() with sys.argv replaced."""
    import sys as _sys
    saved = _sys.argv
    try:
        _sys.argv = ["bridge-daemon.py", *argv]
        mod.main()
    finally:
        _sys.argv = saved


# Phase W - --status CLI flag tests.


def test_status_exits_1_when_neither_port_nor_heartbeat(entry_module, tmp_path, monkeypatch, capsys):
    """No daemon ever started -> --status exits 1 with stderr note."""
    (tmp_path / ".daemon-state").mkdir()  # dir exists but empty
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.show_status()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not started" in err.lower()


def test_status_prints_full_fields_when_both_present(entry_module, tmp_path, monkeypatch, capsys):
    """Port file + heartbeat -> grep-friendly single line with all fields."""
    import json as _json
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("31415")
    hb = {
        "pid": 12345,
        "version": "0.1.0",
        "config_loaded_version": "7",
        "uptime_s": 3600,
        "last_heartbeat": "2026-05-20T12:00:00+00:00",
        "active_sessions": 2,
        "recent_error_count": 0,
    }
    (daemon_state / "heartbeat.json").write_text(_json.dumps(hb))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    # All fields land on one line, tab/space-separated.
    assert "\n" not in out.rstrip("\n")
    for fragment in ["port=31415", "pid=12345", "uptime=3600s", "version=0.1.0",
                     "config_v=7", "sessions=2", "errors=0"]:
        assert fragment in out


def test_status_uses_dashes_when_heartbeat_missing(entry_module, tmp_path, monkeypatch, capsys):
    """Port file exists but no heartbeat yet -> port populated, other fields dash."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("31999")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=31999" in out
    # Heartbeat-derived fields all '-'
    assert "pid=-" in out
    assert "version=-" in out
    assert "config_v=-" in out


def test_status_uses_dashes_for_port_when_only_heartbeat(entry_module, tmp_path, monkeypatch, capsys):
    """Heartbeat survives but port file missing (daemon crashed mid-write) ->
    port='-', heartbeat fields populated."""
    import json as _json
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "heartbeat.json").write_text(_json.dumps({"pid": 1, "version": "0.1.0"}))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=-" in out
    assert "pid=1" in out
    assert "version=0.1.0" in out


def test_status_handles_malformed_heartbeat_gracefully(entry_module, tmp_path, monkeypatch, capsys):
    """Malformed heartbeat JSON -> treated as missing, all fields dash (no raise)."""
    daemon_state = tmp_path / ".daemon-state"
    daemon_state.mkdir()
    (daemon_state / "port").write_text("32000")
    (daemon_state / "heartbeat.json").write_text("{not json")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.show_status()
    out = capsys.readouterr().out
    assert "port=32000" in out
    assert "pid=-" in out


def test_check_health_handles_missing_port_file(entry_module, tmp_path, monkeypatch, capsys):
    """When .daemon-state/port AND heartbeat.json are absent, --health exits 2.

    Phase 1.161: --health splits its exit codes:
    - 0 live probe succeeded
    - 1 live probe failed, heartbeat.json fallback used
    - 2 neither path worked (no port file + no heartbeat)
    """
    (tmp_path / ".daemon-state").mkdir()
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2
    assert "not running" in capsys.readouterr().err.lower()


def test_check_health_falls_back_to_heartbeat_when_no_port(entry_module, tmp_path, monkeypatch, capsys):
    """Phase 1.161: heartbeat.json fallback when port file is missing."""
    import json
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "heartbeat.json").write_text(json.dumps({
        "pid": 1234, "version": "0.1.0", "config_loaded_version": "v1",
        "uptime_s": 600, "last_heartbeat": "2026-05-19T15:00:00Z",
        "last_error": None, "recent_error_count": 0, "active_sessions": 0,
    }))
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "1234" in captured.out  # heartbeat pid surfaced on stdout


def test_check_health_handles_corrupted_port_file(entry_module, tmp_path, monkeypatch, capsys):
    """Corrupted port file -> exit 2 (no fallback attempted at this stage)."""
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("not-a-number")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2
    assert "corrupted" in capsys.readouterr().err.lower()


def test_check_health_rejects_out_of_range_port(entry_module, tmp_path, monkeypatch, capsys):
    """A port outside the TCP valid range (e.g., 99999) is rejected as corruption."""
    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("99999")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 2


def test_a_foreign_server_on_the_port_still_shows_the_heartbeat(
        entry_module, tmp_path, monkeypatch, capsys):
    """Exit 1 means "fell back to heartbeat", and this branch never fell back.

    Found by the 2026-08-24 campaign (shard `scripts-00-p4`, finding 2). A
    stale port file plus another process now bound to that port is the
    daemon-probably-dead case the fallback exists for, and it was the one path
    that took exit 1 without reading `heartbeat.json` - losing the last known
    pid, version, uptime and `last_heartbeat`, the field that says WHEN the
    real daemon died. The corrupt-port-file branch above it carries a comment
    reading "Every other failure path here falls back first; this one now does
    too"; this one did not.
    """
    import json

    class _Body:
        def read(self):
            return b'{"status": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("31415", encoding="utf-8")
    (state_dir / "heartbeat.json").write_text(json.dumps({
        "pid": 4321, "version": "0.9.9",
        "last_heartbeat": "2026-08-24T09:00:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: _Body())

    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "not this daemon" in captured.err, (
        "the operator must still be told the responder is a stranger")
    docs = [json.loads(chunk) for chunk in _json_documents(captured.out)]
    assert {"status": "ok"} in docs, (
        f"what answered must still be shown so the operator can identify it: "
        f"{captured.out!r}")
    assert any(d.get("pid") == 4321 for d in docs), (
        f"the heartbeat was never read, so exit 1 lied about what it did: "
        f"{captured.out!r}")


def _json_documents(text: str):
    """Split a stdout stream holding several pretty-printed JSON documents.

    `json.dumps(..., indent=2)` starts every document at column 0 with `{` and
    ends it at column 0 with `}`, so the closing brace on its own line is the
    boundary. Written rather than assuming one document, because this path now
    prints two and asserting on the concatenation would pass on either alone.
    """
    buf: list[str] = []
    for line in text.splitlines():
        buf.append(line)
        if line == "}":
            yield "\n".join(buf)
            buf = []


def test_a_foreign_server_with_no_heartbeat_still_exits_one(
        entry_module, tmp_path, monkeypatch, capsys):
    """The fallback is an addition, not a replacement.

    With no heartbeat on disk there is nothing to add, and the branch must
    still refuse the stranger rather than being softened into exit 2 or into
    printing the stranger as this daemon.
    """
    import json

    class _Body:
        def read(self):
            return b'{"status": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    state_dir = tmp_path / ".daemon-state"
    state_dir.mkdir()
    (state_dir / "port").write_text("31415", encoding="utf-8")
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: _Body())

    with pytest.raises(SystemExit) as exc:
        entry_module.check_health()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "not this daemon" in captured.err
    assert json.loads(captured.out) == {"status": "ok"}


def test_a_hand_edited_max_per_tick_does_not_take_the_daemon_down(
        entry_module, tmp_path, monkeypatch):
    """One optional knob on an optional, default-off feature killed boot.

    Found by the 2026-08-24 campaign (shard `scripts-00-p4`, finding 1). Every
    other config read in `_register_spine_jobs` is coerced defensively;
    `int(crit.get("max_per_tick", 3) or 3)` was bare. `.daemon-state/config.yaml`
    is hand-edited, so `max_per_tick: "lots"` raised ValueError and
    `max_per_tick: [3]` raised TypeError, out of `_register_spine_jobs` and into
    `start_daemon`'s `except Exception: ... raise`. The whole daemon then failed
    to start: no observer, no scheduler, no uvicorn. Four docstrings in that
    file promise the spine jobs self-disable and that a failure "must never take
    the daemon down".
    """
    pytest.importorskip("apscheduler")

    class _Sched:
        def __init__(self):
            self.jobs = {}

        def add_job(self, fn, *a, **kw):
            self.jobs[kw.get("id")] = kw.get("args")

    for bad in ("lots", [3], {"n": 3}, "3.5"):
        sched = _Sched()
        cfg = {"daemon": {"critique": {"enabled": True, "max_per_tick": bad}}}
        entry_module._register_spine_jobs(
            sched, cfg, tmp_path, object(), data_root=tmp_path)
        assert "critique" in sched.jobs, (
            f"max_per_tick={bad!r} stopped the critique job being scheduled at "
            f"all; the knob is optional and the job is not what it configures")
        # args = [workspace_root, max_per_tick, model, data_root]
        assert sched.jobs["critique"][1] == 3, (
            f"max_per_tick={bad!r} should fall back to the documented default "
            f"of 3, got {sched.jobs['critique'][1]!r}")


def test_a_valid_max_per_tick_is_still_honoured(entry_module, tmp_path):
    """The anchor. Without it the guard passes by always returning 3.

    A string is included because YAML quotes numbers freely and `int("7")` is
    the coercion this line was written for; refusing it would be the guard
    overshooting into a new defect.
    """
    pytest.importorskip("apscheduler")

    class _Sched:
        def __init__(self):
            self.jobs = {}

        def add_job(self, fn, *a, **kw):
            self.jobs[kw.get("id")] = kw.get("args")

    for good, expected in ((7, 7), ("7", 7), (0, 3)):
        sched = _Sched()
        cfg = {"daemon": {"critique": {"enabled": True, "max_per_tick": good}}}
        entry_module._register_spine_jobs(
            sched, cfg, tmp_path, object(), data_root=tmp_path)
        assert sched.jobs["critique"][1] == expected, (good, sched.jobs)


def test_rotate_token_prints_restart_warning(entry_module, tmp_path, monkeypatch, capsys):
    """rotate_token prints a clear warning that the running daemon must be restarted."""
    (tmp_path / ".daemon-state").mkdir()
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.rotate_token()
    out = capsys.readouterr().out.lower()
    assert "restart" in out
    assert "warning" in out
    # And the token file actually got written.
    token_file = tmp_path / ".daemon-state" / "token"
    assert token_file.exists()
    assert len(token_file.read_text()) > 16


def test_rotate_token_actually_rotates(entry_module, tmp_path, monkeypatch, capsys):
    """The one thing the test above cannot see: whether the token CHANGED.

    It starts from an empty `.daemon-state`, so `get_or_create_token` creates
    a token either way and every assertion it makes (file exists, longer than
    16 characters, the warning text) holds for a rotate that rotated nothing.
    `rotate_token` unlinks the old file precisely because
    `get_or_create_token` RETURNS AN EXISTING TOKEN rather than replacing it.
    Deleting those two lines was measured on 2026-08-31:

        owner tests/bridge/test_entry.py: 26 passed in 0.67s
        tests/bridge                    : 1312 passed, 1 skipped in 51.38s
        VERDICT: SURVIVED

    measured over the owning file and all of `tests/bridge`. That is the worst
    direction for this particular no-op: rotation is what the operator runs
    after a suspected disclosure, and `.claude/rules/security.md` lists the
    daemon token under credentials to rotate on compromise. A `--rotate-token`
    that printed "new token written", printed a 4-character tail of the OLD
    secret as confirmation, and left the disclosed token in place would end
    the incident response with the operator believing the opposite of the
    truth.

    The printed tail is asserted against the NEW token for the same reason:
    it is the only confirmation the operator gets, and it would otherwise be
    the old secret's tail.
    """
    state = tmp_path / ".daemon-state"
    state.mkdir()
    token_file = state / "token"
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.rotate_token()
    first = token_file.read_text().strip()
    capsys.readouterr()

    entry_module.rotate_token()
    second = token_file.read_text().strip()
    out = capsys.readouterr().out

    assert second != first, (
        "rotate_token returned the existing token; the old secret is still "
        "the live one")
    assert len(second) == 64, f"not a sha256 hex digest: {second!r}"
    assert f"...{second[-4:]}" in out, (
        f"the confirmation tail names a different token than the one on disk: "
        f"{out!r}")
    assert first[-4:] not in out or first[-4:] == second[-4:], (
        "the old token's tail was printed as confirmation of the new one")


def test_rotate_token_narrows_an_over_permissive_predecessor(entry_module, tmp_path,
                                                             monkeypatch):
    """A rotation must not inherit the old file's mode.

    The replaced file is the one whose mode may be wrong (the header of
    `auth.py` names a copy from another machine and a restore from backup,
    both of which land 0644). Since `rotate_token` unlinks first and
    `atomic_write_text` sets the mode before the rename, the new token is
    0600 regardless; nothing asserted it, and a rotation that answers a
    disclosure by writing the replacement world-readable has not answered it.
    """
    import os
    import stat

    state = tmp_path / ".daemon-state"
    state.mkdir()
    token_file = state / "token"
    token_file.write_text("an-old-token-left-world-readable", encoding="utf-8")
    os.chmod(token_file, 0o644)
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)

    entry_module.rotate_token()

    if os.name == "posix":
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600, oct(
            stat.S_IMODE(token_file.stat().st_mode))
    assert token_file.read_text().strip() != "an-old-token-left-world-readable"


# Phase Y - revert_to_prior_config CLI wrapper tests.
# The Phase 1.154 + 1.159 + 1.165 tests cover the config.py functions
# directly (revert_config, revert_config_to). Until now the CLI wrapper
# (revert_to_prior_config in scripts/bridge-daemon.py) had zero
# coverage, so a regression in the user-facing output, sys.exit
# semantics, or the listing-with-marker logic would slip past the suite.


def test_revert_to_prior_config_exits_1_when_no_snapshots(entry_module, tmp_path, monkeypatch, capsys):
    """No snapshots on disk -> stderr note + sys.exit(1)."""
    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no snapshots" in err.lower()


def test_revert_to_prior_config_default_restores_index_1(entry_module, tmp_path, monkeypatch, capsys):
    """With >= 2 snapshots and no target_name, restores index 1 (most-recent
    prior). Output marks index 0 'current boot' and index 1 'will restore'."""
    # No sleep between snapshots: snapshot_config names files
    # '{seq:09d}_{stamp}.yaml' with a monotonic sequence prefix, so the
    # newest-first sort revert_to_prior_config relies on is correct by
    # construction. The sleep(1.05) here until 2026-08-20 was guarding a
    # wall-clock-only filename that no longer exists.
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"refresh": {"email": 100}})
    snapshot_config(tmp_path, {"refresh": {"email": 200}})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.revert_to_prior_config()  # must not raise

    out = capsys.readouterr().out
    assert "current boot" in out
    assert "will restore" in out
    assert "Restored" in out
    assert "WARNING" in out
    # User override config now exists with the older value.
    user_cfg = tmp_path / ".daemon-state" / "config.yaml"
    assert user_cfg.exists()


def test_revert_to_prior_config_explicit_target_uses_revert_to(entry_module, tmp_path, monkeypatch, capsys):
    """With target_name, restores that specific snapshot and marks it in the
    listing (no 'current boot' marker)."""
    from scripts.bridge_daemon.config import list_snapshots, snapshot_config
    snapshot_config(tmp_path, {"refresh": {"email": 100}})
    snapshot_config(tmp_path, {"refresh": {"email": 200}})
    snapshot_config(tmp_path, {"refresh": {"email": 300}})
    snaps = list_snapshots(tmp_path)
    oldest_name = snaps[-1].name  # newest-first sort, so [-1] is oldest

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    entry_module.revert_to_prior_config(target_name=oldest_name)

    out = capsys.readouterr().out
    assert oldest_name in out
    # When target_name is explicit, 'current boot' marker is suppressed.
    assert "current boot" not in out
    assert "will restore" in out


def test_revert_to_prior_config_exits_1_on_runtime_error(entry_module, tmp_path, monkeypatch, capsys):
    """If revert_config_to raises RuntimeError (e.g. unknown snapshot name),
    print 'revert failed:' to stderr and sys.exit(1)."""
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"a": 1})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config(target_name="does-not-exist.yaml")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "revert failed" in err.lower()
    assert "not found" in err.lower()


def test_revert_to_prior_config_exits_1_when_only_one_snapshot(entry_module, tmp_path, monkeypatch, capsys):
    """Default mode (no target_name) needs >= 2 snapshots. With only 1,
    revert_config raises RuntimeError -> wrapper prints + exits 1."""
    from scripts.bridge_daemon.config import snapshot_config
    snapshot_config(tmp_path, {"a": 1})

    monkeypatch.setattr(entry_module, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        entry_module.revert_to_prior_config()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "revert failed" in err.lower()
    assert "at least 2" in err.lower()


# _prime_all_components - regression guard for the 2026-05-20 fix.
# Before this fix, the dashboard's freshness indicator showed '-' for
# 11 of 20 components because they were never bumped at boot. The fix:
# iterate COMPONENTS at boot so every component gets a data_time.


def test_prime_all_components_bumps_every_component(entry_module):
    """Every COMPONENT in state.COMPONENTS gets a non-None data_time after
    _prime_all_components(). This is the regression guard - if a future
    component is added to state.COMPONENTS, this test ensures the boot
    primer keeps up automatically (the loop iterates COMPONENTS so a
    NEW component is covered for free)."""
    from scripts.bridge_daemon.state import COMPONENTS, State
    state = State()
    # Pre-condition: all data_times start None
    snap_before = state.snapshot()
    assert all(snap_before["data_times"][c] is None for c in COMPONENTS)

    entry_module._prime_all_components(state)

    snap_after = state.snapshot()
    for c in COMPONENTS:
        assert snap_after["data_times"][c] is not None, \
            f"component {c} not bumped at boot - dashboard will show '-' for freshness"
        assert snap_after["components"][c] == 1, \
            f"component {c} version not at 1 after single bump"


def test_prime_all_components_covers_known_late_additions(entry_module):
    """Spot-check the late-added components that were specifically missing
    from the original boot-list. If any of these regress to null
    data_time, the dashboard's freshness indicator silently breaks for
    that page."""
    from scripts.bridge_daemon.state import State
    state = State()
    entry_module._prime_all_components(state)
    snap = state.snapshot()
    # These 11 components were added to COMPONENTS in Phases 1.5+ but
    # never to the original boot-bump list. All must now show non-null.
    late_additions = ["inflight", "investors", "approvals", "calendar",
                      "crm", "prime", "status", "conversations",
                      "threads", "signals", "critical"]
    for c in late_additions:
        assert snap["data_times"][c] is not None, \
            f"late-added component {c} not primed at boot"
