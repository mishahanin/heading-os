"""Four state writes that opened a file for truncation and hoped.

`open(path, "w")` empties the file first and writes second. Between the two the
file on disk is EMPTY, and every reader in this workspace treats an unreadable
state file as "no state". The three writes below are each the last copy of
something: an OAuth refresh token, the executive registry during a revocation,
and the data overlay's schema handshake.

1. THE GMAIL OAUTH TOKEN. `scripts/utils/gmail_auth.py` wrote the credential
   with a bare `open(token, "w")` and chmod'd it afterwards, so it was briefly
   both truncated and world-readable. The SEC-006 guard that covers OAuth token
   stores named `gmail-reader.py`, which contains zero `open(` and zero
   `os.makedirs` calls: the dance lives in `gmail_auth.py`, one import away, so
   both AST guards walked an empty tree. `scripts/google-contacts.py` had the
   same write.
2. THE EXECUTIVE REGISTRY, MID-REVOCATION. `emergency-revoke.py` rewrote
   `config/exec-registry.json` with a plain `write_text`, while
   `offboard-exec.py` writes the same file atomically and is the entry
   `tests/test_atomic_scripts.py` pins.
3. THE SCHEMA HANDSHAKE MARKER. `init-data.py` was the third writer of
   `.schema-version`; a test named for the standard checked two of three. A
   half-written marker makes `read_data_schema_version` fall back to "assume
   current", silently skipping every migration the overlay still needs.
4. A TEST THAT CLAIMED ATOMICITY IN ITS NAME. `test_add_load_roundtrip_atomic`
   asserted a save/load roundtrip and nothing else, so it would have passed
   against `open(path, "w")`.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`atomic-write-that-is-not`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import gmail_auth, reminders_store  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402


def _load(relpath: str, name: str):
    """Import a hyphenated script by path."""
    spec = importlib.util.spec_from_file_location(name, str(ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. The OAuth token stores
# ============================================================

def test_the_gmail_token_write_resolves_the_atomic_helper():
    """`co_names` is the cheap proof that the call site reaches the name.

    A source-only grep passes while the import is missing and `get_service`
    dies with NameError on the one line the finding is about.
    """
    names = gmail_auth.get_service.__code__.co_names
    assert "atomic_write_text" in names
    assert "open" not in names, (
        "get_service went back to a plain open(); the refresh token is the "
        "whole Gmail path's credential"
    )
    assert callable(gmail_auth.atomic_write_text)


def test_the_contacts_token_write_resolves_the_atomic_helper():
    """Asked of the FUNCTION that writes the token, not of the file's text.

    This asserted `'open(TOKEN_PATH, "w"' not in src` over the whole module -
    a substring over source text, which is the wrong instrument in both
    directions. It passes on any other spelling of the same truncating write
    (measured 2026-09-01: `Path(TOKEN_PATH).open("w").write(creds.to_json())`
    left this file entirely green while the OAuth refresh token went back to
    being truncated in place), and it would fail on a legitimate re-wrap that
    never touched the safety property. The gmail sibling above already asks
    `co_names` of the function; this one now does too.
    """
    mod = _load("scripts/google-contacts.py", "google_contacts_under_test")
    assert callable(mod.atomic_write_text)
    names = mod.authenticate.__code__.co_names
    assert "atomic_write_text" in names, (
        "authenticate() no longer reaches the atomic helper; the OAuth refresh "
        "token is the whole Google Contacts path's credential"
    )
    assert "open" not in names, (
        "authenticate() went back to a plain open(); a crash mid-write leaves "
        "an empty credential and the next run demands a browser login"
    )


def test_an_oauth_token_write_lands_restricted_and_never_world_readable(tmp_path):
    """The mode is set on the tempfile BEFORE the rename, not after.

    The chmod-afterwards form leaves a window in which the credential exists at
    the process umask. This is the property `atomic_write_text(mode=0o600)`
    buys, driven exactly as gmail_auth drives it.
    """
    token = tmp_path / "google" / "gmail_token.json"
    payload = {"refresh_token": "fixture-value-not-a-credential"}
    atomic_write_text(token, json.dumps(payload), mode=0o600)
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o600
    assert json.loads(token.read_text(encoding="utf-8")) == payload


def test_the_credential_is_CREATED_restricted_never_narrowed_afterwards(tmp_path, monkeypatch):
    """The mechanism behind the test above, which that test cannot see.

    `test_an_oauth_token_write_lands_restricted_and_never_world_readable`
    stats the file after `atomic_write_text` returns, so it reads the FINAL
    mode and nothing else. Every path that ends at 0o600 passes it, including
    ones that put the whole refresh token on disk world-readable first.

    Measured 2026-09-01: replacing `tempfile.mkstemp(dir=...)` with
    `os.open(tmp, O_CREAT|O_WRONLY|O_TRUNC, 0o666)` - so the tempfile is
    created at the process umask, carries the entire credential, and is only
    narrowed by the chmod further down - left `tests/`,
    `tests/security/test_SEC_006_oauth_dir_permissions.py` and
    `tests/test_atomic_scripts.py` all green: 42 passed, 1 skipped. The
    property those tests are named for is supplied by `mkstemp` creating at
    0o600, and nothing asked about it.

    So ask at the earliest observable instant instead: the mode the descriptor
    already carries when the writer opens it. `os.fdopen` is the first thing
    `atomic_write_text` does with the fd, which makes it the seam - and
    `os.fstat` reads the mode through the descriptor, so this cannot be fooled
    by a chmod that happens later.
    """
    seen: list[int] = []
    real_fdopen = os.fdopen

    def _watching_fdopen(fd, *a, **k):
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, *a, **k)

    monkeypatch.setattr("scripts.utils.atomic.os.fdopen", _watching_fdopen)
    token = tmp_path / "google" / "gmail_token.json"
    atomic_write_text(token, json.dumps({"refresh_token": "fixture"}), mode=0o600)

    assert seen, (
        "the observer never fired: atomic_write_text no longer opens its "
        "tempfile through os.fdopen, so this guard measured nothing"
    )
    for observed in seen:
        assert not observed & (stat.S_IRWXG | stat.S_IRWXO), (
            f"the tempfile holding the credential was created mode {observed:#o}: "
            "readable by group or other before any chmod could narrow it"
        )
        assert observed == 0o600, (
            f"expected a 0o600 creation (mkstemp's own), got {observed:#o}"
        )


def test_a_failed_token_write_leaves_the_old_credential_intact(tmp_path, monkeypatch):
    """The truncation window, made visible.

    With `open(path, "w")` the assertion below fails: the file is empty and the
    next run demands a browser login on a headless machine.
    """
    token = tmp_path / "gmail_token.json"
    original = json.dumps({"refresh_token": "keep-me"})
    token.write_text(original, encoding="utf-8")

    def _boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("scripts.utils.atomic.os.replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(token, json.dumps({"refresh_token": "new"}), mode=0o600)

    assert token.read_text(encoding="utf-8") == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gmail_token.json"], (
        "the failed write left an orphan tempfile beside the credential"
    )


# ============================================================
# 2. The executive registry, mid-revocation
# ============================================================

def test_emergency_revoke_resolves_the_atomic_helper():
    mod = _load("scripts/emergency-revoke.py", "emergency_revoke_under_test")
    assert callable(mod.atomic_write_text)
    assert "atomic_write_text" in mod.update_registry_status.__code__.co_names


# ============================================================
# 3. The schema handshake marker
# ============================================================

def test_init_data_resolves_the_atomic_helper():
    mod = _load("scripts/init-data.py", "init_data_under_test")
    assert callable(mod.atomic_write_text)
    assert "atomic_write_text" in mod.init_data.__code__.co_names


def test_init_data_writes_a_complete_marker(tmp_path):
    """Behavioural, not structural: scaffold an overlay and read the marker back."""
    mod = _load("scripts/init-data.py", "init_data_behaviour")
    target = tmp_path / "overlay"
    assert mod.init_data(target) == 0
    marker = target / ".schema-version"
    assert marker.read_text(encoding="utf-8").strip() == str(mod.DATA_SCHEMA_VERSION)
    assert not list(target.glob("*.tmp"))


# ============================================================
# 4. The reminders store, and the test that claimed its name
# ============================================================

def test_a_failed_reminder_save_leaves_the_store_intact(tmp_path, monkeypatch):
    """What `test_add_load_roundtrip_atomic` asserted for a year: nothing.

    A save/load roundtrip passes against `open(path, "w")`. The property the
    name claims is that a FAILED save cannot destroy what is already there.
    """
    store = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders_store, "store_path", lambda: store)
    first = reminders_store.add(
        {"kind": "once", "when": "2026-07-26", "message": "keep me"})
    before = store.read_bytes()

    def _boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("scripts.utils.atomic.os.replace", _boom)
    with pytest.raises(OSError):
        reminders_store.add({"kind": "once", "when": "2026-08-01", "message": "lost"})

    assert store.read_bytes() == before
    assert [r["id"] for r in reminders_store.load()] == [first["id"]]


def test_a_failed_reminder_save_leaves_no_orphan_tempfile(tmp_path, monkeypatch):
    """The hand-rolled writer used a FIXED `reminders.json.tmp` and never removed it.

    A stale sibling from a full disk was then silently renamed over the live
    store by the next successful save.
    """
    store = tmp_path / "reminders.json"
    monkeypatch.setattr(reminders_store, "store_path", lambda: store)
    reminders_store.add({"kind": "once", "when": "2026-07-26", "message": "a"})

    def _boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("scripts.utils.atomic.os.replace", _boom)
    with pytest.raises(OSError):
        reminders_store.add({"kind": "once", "when": "2026-08-01", "message": "b"})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["reminders.json"]


def test_the_reminders_save_resolves_the_atomic_helper():
    assert "atomic_write_text" in reminders_store.save.__code__.co_names
