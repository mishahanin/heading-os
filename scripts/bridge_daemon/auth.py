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
    """Narrow an over-permissive token file to 0600, and say so when it was.

    Best-effort by design: a filesystem with no POSIX modes cannot honour this,
    and refusing to serve there would be worse than the exposure. The WARNING
    is the point either way -- a token that was readable by other local accounts
    should be treated as disclosed and rotated, and a silent chmod would hide
    the one fact that decides that.
    """
    try:
        current = stat.S_IMODE(token_file.stat().st_mode)
    except OSError:
        logger.warning("could not read the mode of %s", token_file, exc_info=True)
        return
    if current == TOKEN_MODE:
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
    if token_file.exists():
        try:
            existing = token_file.read_text(encoding="utf-8").strip()
        except OSError:
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
    if not expected:
        return False
    return secrets.compare_digest(provided or "", expected)
