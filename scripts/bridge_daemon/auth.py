"""Workspace-fingerprinted localhost token.

Token = sha256(machine_id + workspace_path + random_nonce). Stored at
.daemon-state/token with 0600 perms. Browser reads it via /_bootstrap
(same-origin) and includes Authorization: Bearer <token> on subsequent calls.
"""
import os
import time
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
    nonce = secrets.token_urlsafe(32)
    _image_nonces[nonce] = time.monotonic() + NONCE_TTL
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

def generate_token(workspace_root: Path) -> str:
    nonce = secrets.token_hex(16)
    raw = f"{_machine_id()}|{workspace_root}|{nonce}"
    return hashlib.sha256(raw.encode()).hexdigest()

def get_or_create_token(workspace_root: Path) -> str:
    token_file = workspace_root / ".daemon-state" / "token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = generate_token(workspace_root)
    atomic_write_text(token_file, token, mode=0o600)
    return token

def validate(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return secrets.compare_digest(provided or "", expected)
