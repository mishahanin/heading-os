"""Random localhost bearer token, stored 0600.

Token = sha256(machine_id + workspace_path + random_nonce). Stored at
.daemon-state/token with 0600 perms. Browser reads it via /_bootstrap
(same-origin) and includes Authorization: Bearer <token> on subsequent calls.

**The machine and workspace inputs are NOT binding.** They feed the hash, but
`validate()` is a plain `compare_digest` against the stored string, so a copied
token file works perfectly on another machine and another workspace. The header
used to call the token "workspace-fingerprinted", which tells an auditor to
expect a check that does not exist. The random nonce is the only entropy that
matters; the other two are metadata.
"""
import os
import stat
import time
import logging
import secrets
import hashlib
from pathlib import Path

from ._atomic import atomic_write_text

# --- Short-lived single-use image nonce (F-M1/F-L5) ---
#
# An <img> tag cannot send an Authorization header, so the studio image URL
# historically carried the bearer token as ?t=<token> - which leaks the
# long-lived token into HTTP logs, the Referer header, and browser history.
# Instead the browser mints a short-lived, single-use nonce via the
# bearer-authed POST /studio/image-nonce and passes ?n=<nonce> to the image
# endpoint, so the bearer never appears in an image URL.
#
# The store is in-memory: ephemeral per daemon boot, which is correct because
# nonces have a 30s TTL and the frontend requests a fresh one immediately
# before each <img> render. A restart simply clears stale nonces.
NONCE_TTL = 30.0  # seconds
_image_nonces: dict[str, float] = {}


def mint_image_nonce() -> str:
    """Mint a one-use image nonce valid for NONCE_TTL seconds.

    The value is generated with a CSPRNG (secrets.token_urlsafe).
    """
    # Prune before minting. Expiry was only ever checked at CONSUME time, so a
    # nonce minted and never used stayed in the dict for the life of the
    # process: a frontend retry loop could grow daemon memory at request rate.
    now = time.monotonic()
    for stale in [n for n, exp in _image_nonces.items() if exp <= now]:
        _image_nonces.pop(stale, None)
    nonce = secrets.token_urlsafe(32)
    _image_nonces[nonce] = now + NONCE_TTL
    return nonce


def consume_image_nonce(nonce: str) -> bool:
    """Validate and consume an image nonce.

    Returns True exactly once for a fresh, unexpired nonce; False if the nonce
    is missing, already consumed (replay), or expired. The pop makes it
    single-use: a second call for the same value sees nothing in the store.
    """
    if not nonce:
        return False
    expiry = _image_nonces.pop(nonce, None)
    if expiry is None:
        return False
    return time.monotonic() <= expiry

def _machine_id() -> str:
    if os.name == "nt":
        return os.environ.get("COMPUTERNAME", "unknown")
    return os.uname().nodename

logger = logging.getLogger(__name__)


def generate_token(workspace_root: Path) -> str:
    nonce = secrets.token_hex(16)
    raw = f"{_machine_id()}|{workspace_root}|{nonce}"
    return hashlib.sha256(raw.encode()).hexdigest()

TOKEN_MODE = 0o600


def _enforce_token_mode(token_file: Path) -> None:
    """Narrow an OVER-permissive token file to 0600, and say so when it was.

    Best-effort by design: a filesystem with no POSIX modes cannot honour this,
    and refusing to serve there would be worse than the exposure. The WARNING
    is the point either way -- a token that was readable by other local accounts
    should be treated as disclosed and rotated, and a silent chmod would hide
    the one fact that decides that.

    Only over-permissive. The test used to be `current == TOKEN_MODE`, which is
    false for a mode that is STRICTER than 0600, so a token file at 0400 was
    chmodded to 0600 and the operator was told it "was mode 400" and that
    anything able to read it may hold the bearer token. Both halves were wrong
    in the same direction: this function WIDENED the file it exists to narrow,
    handing back owner-write nobody asked for, and then raised a disclosure
    alarm about a file that had never been exposed. A read-only token is a
    deliberate hardening, and the daemon only ever reads it here.
    """
    try:
        current = stat.S_IMODE(token_file.stat().st_mode)
    except OSError:
        logger.warning("could not read the mode of %s", token_file, exc_info=True)
        return
    # Every bit outside owner read/write: group, other, and the setuid/setgid/
    # sticky triple. If none is set the file is at most as permissive as 0600
    # and there is nothing to narrow, whether it is 0600, 0400 or 0000.
    if not current & ~TOKEN_MODE:
        return
    try:
        os.chmod(token_file, TOKEN_MODE)
    except OSError:
        logger.warning("token file at %s is mode %o and could not be narrowed "
                       "to %o", token_file, current, TOKEN_MODE, exc_info=True)
        return
    logger.warning("token file at %s was mode %o, narrowed to %o. Anything that "
                   "could read it may hold the daemon's bearer token; rotate it "
                   "if that is possible on this machine.",
                   token_file, current, TOKEN_MODE)


def get_or_create_token(workspace_root: Path) -> str:
    """Read the daemon's bearer token, regenerating an unusable one.

    An existing file used to be trusted blindly. One truncated write left `""`
    on disk, `validate()` fails closed on an empty expected value, and the
    daemon then answered 401 to every authenticated request forever, with no
    log line naming the cause and no path back except deleting the file by
    hand. An empty token is not a token.

    The MODE is re-asserted on every read, not only at creation. This module's
    header says the token is "Stored at .daemon-state/token with 0600 perms",
    and `mode=0o600` was passed on the write path only -- so a file that
    arrived some other way kept whatever mode it came with, forever. The header
    itself documents two such ways: a copy from another machine, and a restore
    from backup. Both commonly land 0644. This token is the whole auth boundary
    for every endpoint, so a group- or world-readable one hands the daemon to
    any other local account, and nothing in the system would have noticed.
    """
    token_file = workspace_root / ".daemon-state" / "token"
    # ONE stat, and everything below reads it. `Path.exists()` was the test
    # here, and it is True for a DIRECTORY, so the read raised
    # `IsADirectoryError`, the handler swallowed it as "unreadable,
    # regenerating", and `atomic_write_text` then raised the same error uncaught
    # out of daemon startup. The operator saw a traceback from a write and no
    # statement of what was wrong.
    #
    # It is one stat rather than `exists()` plus `is_file()` because those are
    # two, and the file can go between them. `is_file()` swallows the resulting
    # OSError and answers False, which reads as "exists but is not a regular
    # file" and would turn a concurrent token rotation into the fatal refusal
    # below. MEASURED 2026-09-02: written as two calls, this raised RuntimeError
    # in `test_a_token_that_vanishes_before_the_mode_check_still_serves`, a test
    # whose whole subject is that a vanished token must still serve.
    try:
        token_stat = token_file.stat()
    except OSError:
        # Absent is the normal first-boot case. Unreadable for any other reason
        # falls through to the read below, which logs and regenerates; that path
        # already exists and is the right degradation.
        token_stat = None
    # Refusing, by name, is the whole fix for the non-regular case. Nothing this
    # function can do is safe: writing a token over the path would destroy
    # whatever is there, and choosing a different path would authenticate
    # against a file the operator does not know about. The daemon cannot serve
    # without a token, so this is fatal either way. The difference is that the
    # operator is told what to move and why.
    if token_stat is not None and not stat.S_ISREG(token_stat.st_mode):
        raise RuntimeError(
            f"the daemon token path {token_file} exists but is not a regular "
            f"file. Move or remove it by hand, then start the daemon again. "
            f"Nothing is written over it here, because whatever is there was "
            f"not put there by this daemon.")
    if token_stat is not None:
        try:
            existing = token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            # `UnicodeDecodeError` is a `ValueError`, not an `OSError`, so a
            # token file holding bytes that are not UTF-8 raised out of here
            # instead of being regenerated. This runs during daemon startup,
            # so the whole bridge failed to boot over a file this handler's
            # own message says it knows how to replace. A restore from backup,
            # a half-written file, or a stray editor save is enough.
            logger.warning("token file at %s is unreadable; regenerating",
                           token_file, exc_info=True)
            existing = ""
        if existing:
            _enforce_token_mode(token_file)
            return existing
        logger.warning("token file at %s was empty; regenerating it", token_file)
    token = generate_token(workspace_root)
    atomic_write_text(token_file, token, mode=0o600)
    return token

def validate(provided: str, expected: str) -> bool:
    """Constant-time token compare that cannot be made to raise.

    Both sides are encoded to bytes first, and that is the whole fix.
    `secrets.compare_digest` accepts two `str` arguments only while BOTH are
    ASCII; hand it one non-ASCII character and it raises `TypeError:
    comparing strings with non-ASCII characters is not supported`. The
    caller is `app._require_token`, which slices the value straight out of
    the `Authorization` header, and uvicorn decodes header bytes as latin-1,
    so any byte above 0x7F in that header arrives here as a non-ASCII `str`.
    Measured 2026-08-31 against the real ASGI app, driving the scope uvicorn
    builds:

        raw 0xFF byte          -> UNCAUGHT TypeError: comparing strings with
                                  non-ASCII characters is not supported
        utf-8 cyrillic bytes   -> UNCAUGHT TypeError: (same)
        valid ascii wrong      -> 401
        100k ascii             -> 401

    An uncaught exception out of a route handler is a 500, so an
    unauthenticated request could pick the auth boundary's answer between
    "401 invalid token" and "500 Internal Server Error" by flipping one bit
    in the header. Nothing was disclosed and nothing was let through (the
    request still failed), but a crash is a worse answer than a refusal on
    the one boundary that is reachable before any check, and the error path
    that reports it is far noisier than a 401.

    `compare_digest` on `bytes` takes any byte value and keeps the timing
    property, so encoding is the fix rather than a pre-filter that rejects
    non-ASCII (which would answer 401 for a different reason and leave the
    crash one refactor away). `surrogateescape` is there because a lone
    surrogate would make a plain `encode` raise the same way, so the
    encoding step itself cannot become the new crash.

    An over-long token needs no cap here: it resolves 401 (measured above at
    100,000 characters) and h11 already bounds the header size upstream.
    """
    if not expected:
        return False
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    return secrets.compare_digest(
        provided.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"),
    )
