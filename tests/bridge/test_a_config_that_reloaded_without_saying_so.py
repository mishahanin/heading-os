"""A silent reload, a case-sensitive hostname, and a token nobody re-checked.

Covers the k3 audit shard `scripts-01-p1` for `scripts/bridge_daemon/config.py`,
`scripts/bridge_daemon/app.py` and `scripts/bridge_daemon/auth.py`. Nothing here
starts the daemon; it is stopped and disabled on this machine on purpose.

*A config push applied in total silence.* `ConfigState`'s own docstring quotes
the spec: "if mtime is newer than loaded, reload and log config_reloaded
version=N". `reconcile()` did the reload and contained no logging call at all.
`last_reload_at` and `reload_count` live in memory, so a restart takes the only
evidence with it -- and a restart is usually what follows the behaviour change
somebody is trying to explain.

*A stat that could raise out of a timer.* `_config_mtimes` asked `is_file()`
and then `stat()`, and a config deleted between the two raises FileNotFoundError
straight out of the 60-second reconciliation tick. Every other read in that file
treats a missing layer as absent and says so; this one call was the exception,
in the function that runs unattended.

*A header that described a no-op.* The module docstring said `revert_config()`
"restores the most recent snapshot". The code restores index 1, the most recent
PRIOR snapshot, because index 0 is the one this boot just took. An operator
reading only the header would expect `--revert-config` to change nothing.

*A loopback guard that rejected loopback.* DNS hostnames are case-insensitive
(RFC 4343) and `_bare_host` never lowercased, so `curl -H "Host: LOCALHOST"`
against a loopback-bound server got 421. The Origin half of the same middleware
never had the bug, because `urlsplit(...).hostname` lowercases for you.

*A promise enforced only at creation.* `auth.py`'s header says the token is
"Stored at .daemon-state/token with 0600 perms", and `mode=0o600` was passed on
the write path alone. A file that arrived another way -- copied from another
machine or restored from backup, both of which that header documents as
supported -- kept whatever mode it came with, forever. That token is the whole
auth boundary for every endpoint.

*Refuted, and recorded here so no later audit re-derives it.* The same report
called `POST /refresh` a missing `state.bump` on the pulse success path. It is
not: `refreshers.pulse.refresh` bumps "pulse" itself on every one of its paths,
including its internal compute failure. The finding rested on an assumption its
own risks section flagged, and the assumption is false. What WAS wrong is the
comment above the branch, which read as a promise the function does not make.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.bridge_daemon import auth, config  # noqa: E402
from scripts.bridge_daemon.refreshers import pulse as pulse_refresher  # noqa: E402


# ============================================================
# 1. The reload that said nothing
# ============================================================

def _layers(tmp_path: Path, corporate: str | None = None,
            user: str | None = None) -> Path:
    if corporate is not None:
        corp = tmp_path / "corporate" / "daemon"
        corp.mkdir(parents=True, exist_ok=True)
        (corp / "config.yaml").write_text(corporate, encoding="utf-8")
    if user is not None:
        state = tmp_path / ".daemon-state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "config.yaml").write_text(user, encoding="utf-8")
    return tmp_path


def _touch_newer(path: Path, text: str) -> None:
    """Rewrite and force a distinct mtime; a same-second write can match."""
    path.write_text(text, encoding="utf-8")
    bumped = path.stat().st_mtime + 10
    os.utime(path, (bumped, bumped))


def test_a_reload_emits_the_line_the_spec_names(tmp_path, caplog):
    root = _layers(tmp_path, corporate="version: 1\n")
    state = config.ConfigState(root)
    with caplog.at_level(logging.INFO, logger="scripts.bridge_daemon.config"):
        _touch_newer(root / "corporate" / "daemon" / "config.yaml", "version: 2\n")
        assert state.reconcile() is True
    assert any("config_reloaded" in r.getMessage() for r in caplog.records), \
        "the reload happened and nothing recorded it"


def test_the_line_carries_the_version(tmp_path, caplog):
    root = _layers(tmp_path, corporate="version: 1\n")
    state = config.ConfigState(root)
    with caplog.at_level(logging.INFO, logger="scripts.bridge_daemon.config"):
        _touch_newer(root / "corporate" / "daemon" / "config.yaml", "version: 7\n")
        state.reconcile()
    assert any("version=7" in r.getMessage() for r in caplog.records)


def test_the_line_names_which_layer_moved(tmp_path, caplog):
    """"config_reloaded" alone does not say corporate or local override."""
    root = _layers(tmp_path, corporate="version: 1\n", user="version: 1\n")
    state = config.ConfigState(root)
    with caplog.at_level(logging.INFO, logger="scripts.bridge_daemon.config"):
        _touch_newer(root / ".daemon-state" / "config.yaml", "version: 2\n")
        state.reconcile()
    messages = [r.getMessage() for r in caplog.records]
    assert any("layers=user" in m for m in messages), messages


def test_a_tick_that_changes_nothing_stays_quiet(tmp_path, caplog):
    """Sixty seconds apart, all day. A no-op must not write a log line."""
    root = _layers(tmp_path, corporate="version: 1\n")
    state = config.ConfigState(root)
    with caplog.at_level(logging.INFO, logger="scripts.bridge_daemon.config"):
        assert state.reconcile() is False
        assert state.reconcile() is False
    assert [r for r in caplog.records if "config_reloaded" in r.getMessage()] == []


# ============================================================
# 2. The stat that could raise out of a timer
# ============================================================

def test_a_missing_layer_reads_as_absent(tmp_path):
    assert config._mtime_or_none(tmp_path / "gone.yaml") is None


def test_a_directory_is_not_a_config_layer(tmp_path):
    """`stat()` succeeds on a directory where `is_file()` said no, and a
    directory's mtime moves whenever a file lands inside it."""
    d = tmp_path / "config.yaml"
    d.mkdir()
    assert config._mtime_or_none(d) is None


def test_a_real_file_still_reports_its_mtime(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("version: 1\n", encoding="utf-8")
    assert config._mtime_or_none(f) == f.stat().st_mtime


def test_the_layer_is_stat_ed_exactly_once(tmp_path, monkeypatch):
    """The race itself, and the only way to see it.

    Deleting the file inside a patched `stat` does NOT reproduce it, because
    `Path.is_file()` calls `stat()` too and swallows the OSError -- so the
    pre-check absorbed the deletion and the buggy form returned None like the
    fixed one. The distinguishing fact is the CALL COUNT: one question to the
    filesystem cannot disagree with itself. Here the second call fails, which
    is exactly what a deletion between the two looks like.
    """
    f = tmp_path / "config.yaml"
    f.write_text("version: 1\n", encoding="utf-8")
    real_stat = Path.stat
    expected = real_stat(f).st_mtime
    calls = {"n": 0}

    def _second_call_fails(self, *a, **k):
        if self == f:
            calls["n"] += 1
            if calls["n"] > 1:
                raise FileNotFoundError(2, "No such file or directory", str(f))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _second_call_fails)
    assert config._mtime_or_none(f) == expected
    assert calls["n"] == 1, "the layer was stat'ed twice; that gap IS the race"


def test_a_layer_that_is_gone_by_the_only_stat_reads_as_absent(tmp_path,
                                                               monkeypatch):
    """And when the single stat does fail, the answer is absent, not a crash."""
    f = tmp_path / "config.yaml"
    f.write_text("version: 1\n", encoding="utf-8")
    real_stat = Path.stat

    def _vanishing(self, *a, **k):
        if self == f:
            f.unlink(missing_ok=True)
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _vanishing)
    assert config._mtime_or_none(f) is None


def test_the_reconcile_tick_survives_a_vanishing_layer(tmp_path, monkeypatch):
    """The blast radius: this runs on a 60-second timer, unattended."""
    root = _layers(tmp_path, corporate="version: 1\n")
    state = config.ConfigState(root)
    corp = root / "corporate" / "daemon" / "config.yaml"
    real_stat = Path.stat

    def _vanishing(self, *a, **k):
        if self == corp:
            corp.unlink(missing_ok=True)
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _vanishing)
    assert state.reconcile() is True          # the layer went away: that is a change


def test_the_pre_check_is_gone_from_the_source():
    """Two questions to the filesystem is the defect, not the exception type."""
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "config.py").read_text(
        encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    assert "st_mtime if corp.is_file()" not in code
    assert "st_mtime if user.is_file()" not in code


# ============================================================
# 3. The header that described a no-op
# ============================================================

def test_the_module_header_matches_what_revert_config_does():
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "config.py").read_text(
        encoding="utf-8")
    header = src.split('"""')[1]
    assert "most recent PRIOR snapshot" in header
    # And the code it describes: index 1, never index 0.
    body = src.split("def revert_config(")[1].split("\ndef ")[0]
    assert "snaps[1]" in body


# ============================================================
# 4. The loopback guard that rejected loopback
# ============================================================

@pytest.fixture(scope="module")
def bare_host():
    """`_bare_host` is closed over inside `build_app`, so lift it by source.

    Importing `app.py` pulls fastapi and builds the whole application; this
    test needs one pure string function, and re-declaring it here would test a
    copy. Executing the real definition keeps the source of truth singular.
    """
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "app.py").read_text(
        encoding="utf-8")
    start = src.index("    def _bare_host(raw: str) -> str:")
    end = src.index("    @app.middleware", start)
    block = "\n".join(ln[4:] if ln.startswith("    ") else ln
                      for ln in src[start:end].split("\n"))
    ns: dict = {}
    exec(compile(block, "app.py:_bare_host", "exec"), ns)   # noqa: S102
    return ns["_bare_host"]


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.mark.parametrize("header", ["LOCALHOST", "Localhost", "LocalHost:31415",
                                    "LOCALHOST:31415"])
def test_a_mixed_case_loopback_host_is_accepted(bare_host, header):
    """curl sends the hostname as typed; browsers happen to lowercase it."""
    assert bare_host(header) in LOOPBACK


def test_an_ipv6_literal_is_still_returned_whole(bare_host):
    assert bare_host("::1") == "::1"
    assert bare_host("[::1]:31415") == "::1"


def test_an_ipv6_literal_is_lowercased_but_not_otherwise_normalised(bare_host):
    """Hex in an IPv6 literal is case-insensitive, so it is lowercased.

    RENAMED 2026-08-30. The name was
    `test_an_uppercase_ipv6_literal_lands_in_the_set` and the docstring
    justified it by case-insensitivity, but the body asserts the return value
    is the string `"::0001"` -- which is NOT an element of `LOOPBACK`. Zero
    padding is not letter case, and no amount of lowercasing turns `::0001`
    into `::1`. The assertion was right about the code and the name was not;
    `_bare_host` lowercases and splits, and does no address normalisation at
    all. Both properties are now stated separately, and the consequence has its
    own test below rather than being implied by a name.
    """
    assert bare_host("[::0001]:31415") == "::0001"
    assert bare_host("[FE80::1]") == "fe80::1"


def test_a_zero_padded_loopback_literal_is_currently_REFUSED(bare_host):
    """What the old name asserted, measured: it does NOT land in the set.

    `[::0001]` is the same address as `::1` under RFC 4291, so a client that
    spells it that way is talking to loopback and the Host guard 421s it. That
    is the CURRENT behaviour, pinned here so it is a decision rather than an
    accident, and so the suite stops claiming coverage of a case it refuses.

    Normalising with `ipaddress` would fix it and would also WIDEN the guard:
    `127.0.0.2` is loopback under RFC 1122's 127.0.0.0/8, and
    `test_a_foreign_host_is_still_foreign` in this file deliberately refuses it.
    That trade is the operator's to make, not this test's, so nothing in
    `app.py` was changed.
    """
    assert bare_host("[::0001]:31415") not in LOOPBACK
    assert bare_host("[::1]:31415") in LOOPBACK, (
        "the ordinary spelling must still be accepted, or this is not a "
        "normalisation gap but a broken guard")


def test_an_ordinary_host_and_port_still_splits(bare_host):
    assert bare_host("127.0.0.1:31415") == "127.0.0.1"


def test_a_foreign_host_is_still_foreign(bare_host):
    """Lowercasing must not widen the set it feeds."""
    assert bare_host("EVIL.EXAMPLE.COM") not in LOOPBACK
    assert bare_host("127.0.0.2:31415") not in LOOPBACK


# ============================================================
# 5. The token mode nobody re-checked
# ============================================================

def _token(tmp_path: Path, value: str, mode: int) -> Path:
    state = tmp_path / ".daemon-state"
    state.mkdir(parents=True, exist_ok=True)
    f = state / "token"
    f.write_text(value, encoding="utf-8")
    os.chmod(f, mode)
    return f


def test_an_over_permissive_token_is_narrowed_on_read(tmp_path):
    """A copied or restored token file commonly lands 0644 and stayed there."""
    f = _token(tmp_path, "deadbeef", 0o644)
    assert auth.get_or_create_token(tmp_path) == "deadbeef"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_the_narrowing_is_said_out_loud(tmp_path, caplog):
    """A silent chmod hides the fact that decides whether to rotate."""
    _token(tmp_path, "deadbeef", 0o644)
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.auth"):
        auth.get_or_create_token(tmp_path)
    messages = [r.getMessage() for r in caplog.records]
    assert any("rotate" in m for m in messages), messages


def test_a_group_readable_token_is_narrowed_too(tmp_path):
    f = _token(tmp_path, "deadbeef", 0o640)
    auth.get_or_create_token(tmp_path)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_an_already_correct_token_is_left_alone_and_quiet(tmp_path, caplog):
    f = _token(tmp_path, "deadbeef", 0o600)
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.auth"):
        assert auth.get_or_create_token(tmp_path) == "deadbeef"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert [r for r in caplog.records if "narrowed" in r.getMessage()] == []


def test_an_empty_token_is_still_regenerated(tmp_path):
    """The earlier fix in this function must survive the new one."""
    f = _token(tmp_path, "   ", 0o644)
    token = auth.get_or_create_token(tmp_path)
    assert token.strip()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_a_fresh_token_is_created_at_the_right_mode(tmp_path):
    token = auth.get_or_create_token(tmp_path)
    f = tmp_path / ".daemon-state" / "token"
    assert f.read_text(encoding="utf-8").strip() == token
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_an_unchmoddable_token_still_serves(tmp_path, monkeypatch, caplog):
    """Best-effort by design: refusing to serve is worse than the exposure."""
    _token(tmp_path, "deadbeef", 0o644)
    monkeypatch.setattr(auth.os, "chmod",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.auth"):
        assert auth.get_or_create_token(tmp_path) == "deadbeef"
    assert any("could not be narrowed" in r.getMessage() for r in caplog.records)


# ============================================================
# 6. The refutation, pinned so it is not re-derived
# ============================================================

def test_the_pulse_refresher_bumps_its_own_component(tmp_path, monkeypatch):
    """The k3 finding said `POST /refresh` misses a bump. The refresher has it."""
    class _State:
        def __init__(self):
            self.bumps = []

        def bump(self, component, **kw):
            self.bumps.append(component)

    class _Cfg:
        config: dict = {}

    state = _State()
    monkeypatch.setattr(pulse_refresher, "pulse_data", lambda *a, **k: {"x": 1})
    pulse_refresher.refresh(tmp_path, state, _Cfg(), data_root=tmp_path)
    assert state.bumps == ["pulse"]


def test_the_pulse_refresher_bumps_even_when_compute_fails(tmp_path, monkeypatch):
    class _State:
        def __init__(self):
            self.bumps = []

        def bump(self, component, **kw):
            self.bumps.append(component)

    class _Cfg:
        config: dict = {}

    def _boom(*a, **k):
        raise RuntimeError("compute failed")

    state = _State()
    monkeypatch.setattr(pulse_refresher, "pulse_data", _boom)
    pulse_refresher.refresh(tmp_path, state, _Cfg(), data_root=tmp_path)
    assert state.bumps == ["pulse"]


def test_the_refresh_comment_names_where_each_bump_comes_from():
    """The comment, not the code, was the defect. Keep it specific."""
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "app.py").read_text(
        encoding="utf-8")
    block = src.split("def refresh(body: RefreshBody")[1].split("return {")[0]
    assert "the refresher bumped" in block
    assert "refresher RAISED" in block
    # The old wording survives in the file, quoted as the thing that misled the
    # audit. What must not come back is the wording ASSERTED, so pin the
    # quoting clause in front of it rather than banning the words.
    assert block.index("the previous wording") < block.index(
        "state.bump still fires")


# ------------------------------------------------------------
# The two failure paths inside that same function, added 2026-08-31
# ------------------------------------------------------------
#
# Section 5 above measures the chmod that FAILS and the token that is empty. Two
# sibling branches in the same two functions had no case at all, and branch
# coverage over `tests/bridge` reported both as never taken: the unreadable
# token file in `get_or_create_token`, and the stat that fails inside
# `_enforce_token_mode` before any chmod is attempted. Both are the fail-safe
# side of the whole auth boundary, so both need a case rather than a comment.

def test_an_unreadable_token_file_is_regenerated(tmp_path, monkeypatch, caplog):
    """A token that exists and cannot be read is not a token.

    Without this branch the OSError leaves `get_or_create_token` on the way out
    of a daemon boot, and the daemon does not start at all.
    """
    planted = "deadbeef"
    f = _token(tmp_path, planted, 0o600)
    real_read = Path.read_text

    def _unreadable(self, *a, **k):
        if self == f:
            raise OSError(13, "Permission denied", str(f))
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _unreadable)
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.auth"):
        regenerated = auth.get_or_create_token(tmp_path)

    assert regenerated.strip(), "no usable token came back"
    assert regenerated != planted, "the unreadable value was somehow returned"
    assert any("unreadable" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_a_token_that_vanishes_before_the_mode_check_still_serves(tmp_path,
                                                                  monkeypatch,
                                                                  caplog):
    """`_enforce_token_mode` stats before it chmods, and that stat can fail.

    The second stat on the token path is where the mode check happens: the file
    was read successfully and then removed, which is what a concurrent rotation
    looks like. The token in hand is still the right answer; refusing to serve
    over a mode question would be worse than the exposure the chmod is for.
    """
    f = _token(tmp_path, "deadbeef", 0o644)
    real_stat = Path.stat
    calls = {"n": 0}

    def _second_stat_fails(self, *a, **k):
        if self == f:
            calls["n"] += 1
            if calls["n"] > 1:
                raise FileNotFoundError(2, "No such file or directory", str(f))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _second_stat_fails)
    with caplog.at_level(logging.WARNING, logger="scripts.bridge_daemon.auth"):
        assert auth.get_or_create_token(tmp_path) == "deadbeef"

    assert calls["n"] > 1, "the mode check never stat'ed the token, so nothing " \
                           "below is measured"
    assert any("could not read the mode" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
