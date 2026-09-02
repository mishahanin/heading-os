"""The bearer token's mode guard, which widened a hardened file and then
raised a disclosure alarm about it.

`scripts/bridge_daemon/auth.py` re-asserts the token file's mode on every read,
because the token IS the whole auth boundary for every daemon endpoint and a
file that arrived by copy or restore commonly lands 0644. That part is right.

The test it used was `if current == TOKEN_MODE: return`, and equality is not the
question. A mode STRICTER than 0600 is not equal to it either, so an operator
who had hardened the token to 0400 got two wrong things at once:

  * the file was chmodded to 0600, so the guard whose job is to NARROW handed
    back owner-write nobody had asked for. The daemon only ever reads this file.
  * a WARNING was logged saying the file "was mode 400" and that anything able
    to read it may hold the bearer token and should be rotated. That alarm is
    the one signal deciding whether a human rotates a credential, and it fired
    over a file that had never been exposed to anyone.

An alarm that cries wolf on a hardening is worse than no alarm, because the next
real one is read as noise.

The second defect in this file is separate and shares only the function.
`get_or_create_token` gated on `Path.exists()`, which is True for a DIRECTORY.
The read then raised `IsADirectoryError`, the handler swallowed it as
"unreadable, regenerating", and the write raised the same error uncaught, so
daemon startup died on a bare traceback instead of naming the path. Nothing this
function could do there is safe: writing a token over the path would destroy
whatever is there, and picking another path would authenticate against a file
the operator does not know about. So it refuses, by name.

Both were verified present in the tree on 2026-09-02 before this file was
written.
"""
import os
import stat

import pytest

from scripts.bridge_daemon.auth import (TOKEN_MODE, _enforce_token_mode,
                                        get_or_create_token)

#: Mode bits are a POSIX concept. On a filesystem that does not carry them the
#: guard is documented best-effort, so asserting on them there measures the
#: filesystem rather than the code.
posix_mode_bits = pytest.mark.skipif(
    os.name != "posix", reason="POSIX mode bits are not honoured here")


def _token_at(workspace_root, mode: int):
    """A token file on disk at an exact mode, with the parent in place."""
    path = workspace_root / ".daemon-state" / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a-token-value", encoding="utf-8")
    os.chmod(path, mode)
    return path


# ============================================================
# The guard must narrow, and only narrow
# ============================================================

@posix_mode_bits
@pytest.mark.parametrize("mode", [0o400, 0o200, 0o000])
def test_a_mode_stricter_than_0600_is_left_exactly_as_it_is(
        workspace_root, mode, caplog):
    """The founding case. 0400 is a hardening, not an exposure.

    Parametrized across the three shapes that are strictly narrower than 0600,
    because the fix is a bitmask and a fix written as `if current < TOKEN_MODE`
    would pass on 0400 and fail on 0000.
    """
    path = _token_at(workspace_root, mode)
    with caplog.at_level("WARNING"):
        _enforce_token_mode(path)

    assert stat.S_IMODE(path.stat().st_mode) == mode, (
        f"the guard changed a {mode:o} token file. It exists to NARROW an "
        f"over-permissive file; {mode:o} is already at most as permissive as "
        f"{TOKEN_MODE:o}, so widening it hands back access nobody asked for")
    assert not caplog.records, (
        "a disclosure warning was logged about a file that was never exposed; "
        f"the records were {[r.getMessage() for r in caplog.records]}")


@posix_mode_bits
@pytest.mark.parametrize("mode", [0o644, 0o664, 0o666, 0o604, 0o640, 0o777])
def test_an_over_permissive_mode_is_narrowed_and_announced(
        workspace_root, mode, caplog):
    """The other direction, so the fix above cannot be a blanket 'do nothing'.

    Without this, `_enforce_token_mode` could be emptied to `return` and the
    test above would still pass while the guard protected nothing.
    """
    path = _token_at(workspace_root, mode)
    with caplog.at_level("WARNING"):
        _enforce_token_mode(path)

    assert stat.S_IMODE(path.stat().st_mode) == TOKEN_MODE, (
        f"a {mode:o} token file was left readable by another local account")
    assert caplog.records, (
        f"a {mode:o} token file was narrowed in silence. The warning is the "
        f"point: it is what tells a human to rotate a token that other "
        f"accounts could have read")
    assert "rotate" in " ".join(r.getMessage() for r in caplog.records).lower()


@posix_mode_bits
def test_the_setuid_triple_counts_as_over_permissive(workspace_root):
    """A bit outside owner read/write, that is not a group or other bit.

    The mask is `current & ~TOKEN_MODE`, so it catches the setuid, setgid and
    sticky bits too. A narrower fix written as `current & 0o077` would pass
    every case above and miss this one.
    """
    path = _token_at(workspace_root, 0o600 | stat.S_ISUID)
    _enforce_token_mode(path)
    assert stat.S_IMODE(path.stat().st_mode) == TOKEN_MODE


@posix_mode_bits
def test_a_file_already_at_0600_is_not_touched_and_says_nothing(
        workspace_root, caplog):
    """The common path, pinned so the warning cannot become routine."""
    path = _token_at(workspace_root, TOKEN_MODE)
    with caplog.at_level("WARNING"):
        _enforce_token_mode(path)
    assert stat.S_IMODE(path.stat().st_mode) == TOKEN_MODE
    assert not caplog.records


def test_an_unreadable_mode_degrades_rather_than_raising(workspace_root, caplog):
    """Documented best-effort: a stat that fails must not take the daemon down.

    Driven through a path that does not exist, which is the cheapest way to
    make `Path.stat` raise `OSError` without depending on a filesystem feature.
    """
    missing = workspace_root / ".daemon-state" / "not-here"
    missing.parent.mkdir(parents=True, exist_ok=True)
    with caplog.at_level("WARNING"):
        _enforce_token_mode(missing)
    assert caplog.records, "a failed stat was swallowed in silence"


# ============================================================
# A token path that is not a regular file
# ============================================================

def test_a_directory_at_the_token_path_is_refused_by_name(workspace_root):
    """It must not be written over, and it must not be a bare traceback.

    Before the fix: `Path.exists()` is True for a directory, `read_text` raised
    `IsADirectoryError`, the handler logged "unreadable; regenerating", and
    `atomic_write_text` then raised the same error uncaught out of daemon
    startup. The operator saw a traceback from a write and no statement of what
    was actually wrong.
    """
    token_path = workspace_root / ".daemon-state" / "token"
    token_path.mkdir(parents=True)

    with pytest.raises(RuntimeError) as exc:
        get_or_create_token(workspace_root)

    message = str(exc.value)
    assert str(token_path) in message, (
        "the refusal did not name the path the operator has to move")
    assert "not a regular file" in message
    assert token_path.is_dir(), (
        "the directory at the token path was destroyed; whatever was in it was "
        "not put there by this daemon and must not be written over")


def test_the_normal_create_and_reread_path_still_works(workspace_root):
    """The guard above sits on the entry path of every daemon boot, so the
    ordinary case is pinned beside it."""
    first = get_or_create_token(workspace_root)
    second = get_or_create_token(workspace_root)
    assert first == second
    assert first
