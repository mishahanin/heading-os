#!/usr/bin/env python3
"""Read cookies from Chromium-family browsers (Brave, Chrome, Chromium, Edge).

Cross-platform encrypted-cookie reader. Decrypts via DPAPI on Windows,
libsecret (secretstorage) on Linux, Keychain on macOS. Mirrors the API
surface of scripts.utils.firefox_cookies so callers can swap browser
families with a one-line change.

Algorithm summary:
  - Windows: 32-byte AES key from os_crypt.encrypted_key in Local State,
    DPAPI-decrypted. v10/v11 prefix = AES-256-GCM (12-byte nonce +
    ciphertext + 16-byte tag). Legacy prefix = direct DPAPI on the blob.
  - Linux: 16-byte AES key from PBKDF2-HMAC-SHA1(password, salt=b"saltysalt",
    iterations=1, dklen=16). Password is "peanuts" (v10 fallback) or the
    libsecret-stored "<Browser> Safe Storage" entry (v11 keyring). Both
    keys are derived; the encrypted_value prefix dispatches.
  - macOS: 16-byte AES key from PBKDF2 of `security find-generic-password
    -w -s "<Browser> Safe Storage"`, iterations=1003. v10/v11 = AES-128-CBC
    with IV=b" "*16, PKCS7-padded, identical to Linux.

Since Chromium schema version 24 the decrypted plaintext of a v10/v11 cookie
begins with a 32-byte SHA-256 of the host, which is NOT part of the value. The
schema version is read from the `meta` table of the cookie DB and the prefix is
stripped when it is 24 or higher. This file did neither until 2026-08-28, and
because the plaintext was decoded with `errors="replace"` the hash came back as
replacement characters glued to the front of every value rather than as an
error: on this machine's own Brave profile (`meta.version = 24`, 130 cookies,
all `v10`) every exported token was unusable and the CLI printed a green
success line over it.

Dependencies (lazy-imported, clear error on miss):
  - cryptography  (all platforms)
  - secretstorage (Linux only, OPTIONAL; without it only v10 cookies decrypt)

Out of scope: v20 app-bound encryption (Chrome >= M127). Each v20 blob raises,
the reader reports it per cookie and again in its closing count, and the CLI
exits non-zero rather than reporting an empty profile. Recovery is yt-dlp
`--cookies-from-browser`, which handles ABE via the elevation service. Brave
has not adopted v20 as of 2026-05-23.

UNTESTED ON LINUX as of file authorship. Windows DPAPI path smoke-tested.
WSL2 dry-run only on the Linux branch (no Brave keyring available in the
WSL2 baseline). First real Linux validation when bare-Linux Brave
deployment lands.

Usage (as a module):
    from scripts.utils.chromium_cookies import get_cookies, to_cookiejar

    cookies = get_cookies("youtube.com", profile_name="ClaudeCode", browser="brave")
    jar = to_cookiejar(cookies, domain="youtube.com")
    requests.get(url, cookies=jar)

Pass the domain. Without it every cookie in the jar is unscoped, and requests
will then offer these session tokens to whatever host the call reaches,
including the host at the end of a redirect.

Usage (as a CLI; prints cookie NAMES only by default to avoid leaking
session tokens to terminals, logs, or screen shares):
    python scripts/utils/chromium_cookies.py youtube.com --profile ClaudeCode --browser brave
    python scripts/utils/chromium_cookies.py youtube.com --profile ClaudeCode --browser brave --values
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The bootstrap comes FIRST, because both workspace imports below need it. It sat
# between them, so `from scripts.utils.sqlite_uri import ...` ran before the path
# it depends on existed: the CLI line this module's own docstring documents died
# with `ModuleNotFoundError: No module named 'scripts'` on any interpreter
# without the repo root already on sys.path. It worked under `.venv/bin/python`
# only because the editable install drops a `.pth` there, and `requirements.txt`
# is a dependency export that installs no such thing -- so the venv+pip clone
# path the setup docs still offer could not run this at all.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.cookie_domains import host_match_sql, pick_per_name  # noqa: E402
from scripts.utils.sqlite_uri import read_only_uri  # noqa: E402

# Chromium schema version at which the decrypted plaintext of a v10/v11 cookie
# gained a 32-byte SHA-256 host prefix ahead of the value.
HASH_PREFIX_SCHEMA_VERSION = 24
HASH_PREFIX_LEN = 32

_SUPPORTED_BROWSERS = ("brave", "chrome", "chromium", "edge")

# Per-browser, per-OS configuration:
#   user_data: dir containing "Local State" + per-profile subdirs.
#   safe_storage_label: macOS Keychain item name + Linux schema label.
#   safe_storage_app: Linux libsecret schema "application" attribute.
_BROWSER_CONFIGS = {
    "brave": {
        "win32": {
            "user_data": r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data",
            "safe_storage_label": "Brave Safe Storage",
            "safe_storage_app": "brave",
        },
        "darwin": {
            "user_data": "~/Library/Application Support/BraveSoftware/Brave-Browser",
            "safe_storage_label": "Brave Safe Storage",
            "safe_storage_app": "brave",
        },
        "linux": {
            "user_data": "~/.config/BraveSoftware/Brave-Browser",
            "safe_storage_label": "Brave Safe Storage",
            "safe_storage_app": "brave",
        },
    },
    "chrome": {
        "win32": {
            "user_data": r"~\AppData\Local\Google\Chrome\User Data",
            "safe_storage_label": "Chrome Safe Storage",
            "safe_storage_app": "chrome",
        },
        "darwin": {
            "user_data": "~/Library/Application Support/Google/Chrome",
            "safe_storage_label": "Chrome Safe Storage",
            "safe_storage_app": "chrome",
        },
        "linux": {
            "user_data": "~/.config/google-chrome",
            "safe_storage_label": "Chrome Safe Storage",
            "safe_storage_app": "chrome",
        },
    },
    "chromium": {
        "win32": {
            "user_data": r"~\AppData\Local\Chromium\User Data",
            "safe_storage_label": "Chromium Safe Storage",
            "safe_storage_app": "chromium",
        },
        "darwin": {
            "user_data": "~/Library/Application Support/Chromium",
            "safe_storage_label": "Chromium Safe Storage",
            "safe_storage_app": "chromium",
        },
        "linux": {
            "user_data": "~/.config/chromium",
            "safe_storage_label": "Chromium Safe Storage",
            "safe_storage_app": "chromium",
        },
    },
    "edge": {
        "win32": {
            "user_data": r"~\AppData\Local\Microsoft\Edge\User Data",
            "safe_storage_label": "Microsoft Edge Safe Storage",
            "safe_storage_app": "edge",
        },
        "darwin": {
            "user_data": "~/Library/Application Support/Microsoft Edge",
            "safe_storage_label": "Microsoft Edge Safe Storage",
            "safe_storage_app": "edge",
        },
        "linux": {
            "user_data": "~/.config/microsoft-edge",
            "safe_storage_label": "Microsoft Edge Safe Storage",
            "safe_storage_app": "edge",
        },
    },
}


def _browser_cfg(browser: str) -> dict:
    """Resolve per-OS config dict for a Chromium-family browser.

    Resolved at call time (not import) so importing this module on a
    platform without the browser does not crash.
    """
    name = browser.lower()
    if name not in _SUPPORTED_BROWSERS:
        raise ValueError(
            f"Unknown browser '{browser}'. Supported: {list(_SUPPORTED_BROWSERS)}"
        )
    cfg = _BROWSER_CONFIGS[name].get(sys.platform)
    if cfg is None:
        raise ValueError(
            f"Browser '{browser}' is not supported on platform '{sys.platform}'."
        )
    return {**cfg, "user_data": Path(cfg["user_data"]).expanduser()}


def _resolve_user_data(browser: str) -> Path:
    cfg = _browser_cfg(browser)
    root = cfg["user_data"]
    if not root.is_dir():
        raise FileNotFoundError(
            f"{browser} user_data not found at {root}. Is the browser installed "
            "under this user profile?"
        )
    return root


def find_profile_folder(user_data: Path, profile_name: str) -> str:
    """Map a Chromium display name to its on-disk folder name.

    Chromium stores profile display names in Local State's
    profile.info_cache map. Keys are folder names ("Default", "Profile 1",
    ...) and the "name" field is the user-facing display name.

    Falls back to a direct folder-name match for callers that already
    know the folder ("Default", "Profile 1", ...).
    """
    local_state = user_data / "Local State"
    if not local_state.is_file():
        raise FileNotFoundError(f"Local State not found at {local_state}")

    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
    except ValueError as exc:
        # `ValueError`, not `json.JSONDecodeError`. `read_text(encoding="utf-8")`
        # raises `UnicodeDecodeError`, which is a SIBLING of JSONDecodeError
        # under ValueError rather than a subclass, so a Local State torn
        # mid-write escaped this handler and the caller got a raw decode
        # traceback instead of the sentence this branch exists to raise.
        # `_merge_playwright` in this same module already spells the wider form.
        raise RuntimeError(f"Local State is unreadable: {exc}") from exc

    info_cache = data.get("profile", {}).get("info_cache", {})
    for folder, meta in info_cache.items():
        if meta.get("name") == profile_name:
            return folder

    if (user_data / profile_name).is_dir():
        return profile_name

    available = [
        f"{folder} ({meta.get('name', '?')})"
        for folder, meta in info_cache.items()
    ]
    raise FileNotFoundError(
        f"No profile matching '{profile_name}'. Available: {available}"
    )


def _cookies_db_path(profile_dir: Path) -> Path:
    """Locate the Cookies SQLite file.

    Chromium M96+ moved the cookie DB under Network/. Older builds keep it
    at the profile root. Check both.
    """
    network = profile_dir / "Network" / "Cookies"
    if network.is_file():
        return network
    legacy = profile_dir / "Cookies"
    if legacy.is_file():
        return legacy
    raise FileNotFoundError(
        f"No Cookies DB in {profile_dir}. Checked Network/Cookies and Cookies."
    )


def _snapshot_db(src: Path) -> Path:
    """Online-backup copy of a possibly-live SQLite file.

    Uses the SQLite Online Backup API so WAL-mode DBs being written to by
    a running browser yield a clean snapshot. Caller deletes the returned
    temp file.
    """
    tmp_fd, tmp_path_str = tempfile.mkstemp(prefix="chromium_cookies_", suffix=".sqlite")
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    # The caller's cleanup starts only once this returns -- `snapshot =
    # _snapshot_db(db_path)` sits OUTSIDE its own try/finally -- so a failure in
    # here left the temp file behind with nobody able to remove it. The CLI's
    # own advice ("close the browser fully and retry") means this path is
    # expected to be hit repeatedly, one orphan in /tmp each time.
    try:
        src_conn = sqlite3.connect(read_only_uri(src), uri=True, timeout=5)
        try:
            dst_conn = sqlite3.connect(tmp_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt during a backup of a
        # live browser profile is the most likely interruption of all, and it
        # must not be the one that leaks.
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


# ------------------------------------------------------------
# Key acquisition (per-OS)
# ------------------------------------------------------------

def _derive_pbkdf2(password: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA1 with Chromium's well-known parameters."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise ImportError(
            "cryptography not installed. `pip install cryptography`."
        ) from exc

    # SHA1 is mandated by Chromium's storage format - this is a wire-format
    # compatibility constraint, not a security decision we control. Decrypting
    # cookies the browser wrote requires using the exact algorithm it used to
    # write them. Changing SHA1 here means we cannot read Chromium cookies.
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),  # noqa: S303  # nosec B303 - Chromium wire-format constraint
        length=16,
        salt=b"saltysalt",
        iterations=iterations,
    )
    return kdf.derive(password)


def _get_keys_win(local_state_path: Path) -> dict[str, bytes]:
    """Return {"v10": key, "v11": key} where key is the 32-byte AES-GCM key.

    Same key handles both v10 and v11 on Windows; Chromium kept the prefix
    naming for compatibility but only the encrypted_key in Local State
    matters.
    """
    import base64
    import ctypes
    from ctypes import wintypes

    # Same guard, same sentence, as `find_profile_folder` above, which reads the
    # SAME file. That one turns an unreadable Local State into a named
    # RuntimeError; this one let a `UnicodeDecodeError` or a
    # `json.JSONDecodeError` out raw, so the two readers of one file on disk
    # disagreed about what "unreadable" looks like to the caller. Windows-only
    # by platform, and the branch where a bad answer costs the whole key.
    try:
        data = json.loads(local_state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Local State at {local_state_path} is unreadable: {exc}"
        ) from exc
    encrypted_b64 = data.get("os_crypt", {}).get("encrypted_key")
    if not encrypted_b64:
        raise RuntimeError(
            "Local State has no os_crypt.encrypted_key. Browser may never "
            "have launched, or profile is not initialised."
        )
    blob = base64.b64decode(encrypted_b64)
    if blob[:5] != b"DPAPI":
        raise RuntimeError(f"Unexpected prefix in encrypted_key: {blob[:5]!r}")
    blob = blob[5:]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf_in = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(
        len(blob),
        ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = DATA_BLOB()

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        err = ctypes.get_last_error()
        raise RuntimeError(
            f"CryptUnprotectData failed (Win32 error {err}). Verify you are "
            "running as the same Windows user that owns the browser profile."
        )

    key = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.WinDLL("kernel32").LocalFree(blob_out.pbData)
    if len(key) != 32:
        raise RuntimeError(f"Expected 32-byte AES key, got {len(key)} bytes.")
    return {"v10": key, "v11": key}


def _get_keys_linux(safe_storage_app: str) -> dict[str, bytes]:
    """Return {"v10": peanuts_key, "v11": keyring_key?}.

    Both keys are derived when libsecret/D-Bus is reachable. v10 is always
    available (peanuts fallback Chromium uses when no keyring is
    configured). v11 only when the matching libsecret entry is unlocked
    and readable.
    """
    keys = {"v10": _derive_pbkdf2(b"peanuts", iterations=1)}

    try:
        import secretstorage  # type: ignore
    except ImportError:
        print(
            f"{YELLOW}[chromium_cookies] secretstorage not installed; v11 "
            f"(keyring-encrypted) cookies cannot be decrypted. "
            f"`pip install secretstorage` to enable.{RESET}",
            file=sys.stderr,
        )
        return keys

    try:
        bus = secretstorage.dbus_init()
    except Exception as exc:
        print(
            f"{YELLOW}[chromium_cookies] D-Bus session bus unavailable ({exc}); "
            f"v11 cookies cannot be decrypted. Ensure dbus is running.{RESET}",
            file=sys.stderr,
        )
        return keys

    # Every call below can raise: get_default_collection on a missing or
    # unreadable collection, is_locked and search_items on a D-Bus round trip,
    # get_secret on a keyring that answers with a prompt. Only ImportError and
    # dbus_init were guarded, so those raises escaped and killed the whole read
    # - while the docstring promises v11 is best-effort and every other failure
    # here degrades to v10 with a warning. The split was accidental: it followed
    # which lines happened to sit inside an earlier try, not which failures are
    # recoverable. They all are; none of them stops v10 from working.
    try:
        collection = secretstorage.get_default_collection(bus)
        if collection.is_locked():
            print(
                f"{YELLOW}[chromium_cookies] secret collection is locked; v11 "
                f"cookies cannot be decrypted. Unlock keyring "
                f"(gnome-keyring-daemon / kwalletd) and retry.{RESET}",
                file=sys.stderr,
            )
            return keys

        found = False
        for item in collection.search_items({"application": safe_storage_app}):
            keys["v11"] = _derive_pbkdf2(item.get_secret(), iterations=1)
            found = True
            break
        if not found:
            print(
                f"{YELLOW}[chromium_cookies] no libsecret entry for "
                f"application='{safe_storage_app}'; v11 cookies cannot be "
                f"decrypted.{RESET}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"{YELLOW}[chromium_cookies] keyring lookup for "
            f"application='{safe_storage_app}' failed ({exc}); v11 cookies "
            f"cannot be decrypted. v10 cookies are unaffected.{RESET}",
            file=sys.stderr,
        )
    finally:
        bus.close()

    return keys


def _get_keys_mac(safe_storage_label: str) -> dict[str, bytes]:
    """Return {"v10": key, "v11": key} via `security` CLI + PBKDF2(iter=1003)."""
    try:
        password = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", safe_storage_label],
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        # `security` writes the real reason to stderr - no such keychain item,
        # user denied the prompt, keychain locked - and stderr=PIPE captured it.
        # The message used to discard that and assert one cause of several,
        # which is what `.claude/rules/scope-claims.md` forbids: a sentence the
        # method never established. Report what the tool said, then the hint.
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"`security find-generic-password -w -s '{safe_storage_label}'` "
            f"failed (exit {exc.returncode})"
            + (f": {detail}" if detail else " with no message on stderr")
            + ". If the prompt was denied, approve keychain access and retry."
        ) from exc
    except FileNotFoundError:
        raise RuntimeError("`security` CLI not found on PATH (macOS only).")

    key = _derive_pbkdf2(password, iterations=1003)
    return {"v10": key, "v11": key}


def _get_keys(browser: str, user_data: Path) -> dict[str, bytes]:
    cfg = _browser_cfg(browser)
    if sys.platform == "win32":
        return _get_keys_win(user_data / "Local State")
    if sys.platform == "darwin":
        return _get_keys_mac(cfg["safe_storage_label"])
    return _get_keys_linux(cfg["safe_storage_app"])


# ------------------------------------------------------------
# Decryption
# ------------------------------------------------------------

def _finish_plaintext(plaintext: bytes, hash_prefix: bool) -> str:
    """Strip the schema-24 host hash if present, then decode STRICTLY.

    Both halves used to be wrong together, and each hid the other. The prefix
    was never stripped, and `errors="replace"` then turned those 32 binary bytes
    into replacement characters rather than raising - so a value that could not
    possibly work came back looking like a string, and every layer above
    reported success. Measured on a synthetic blob in the exact stored format:
    an 18-character token came back as 48 characters and the CLI printed its
    green line over it.

    Strict decoding is also how the reference implementation detects a wrong
    key: a cookie value is `cookie-octet` per RFC 6265 and is therefore always
    decodable, so a UnicodeDecodeError means the bytes are not the value.
    """
    if hash_prefix:
        plaintext = plaintext[HASH_PREFIX_LEN:]
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "decrypted bytes are not a valid cookie value; the key is probably "
            "wrong, or the schema version was misread"
        ) from exc


def _decrypt_blob_aesgcm(blob: bytes, key: bytes, hash_prefix: bool = False) -> str:
    """Windows AES-256-GCM: 3-byte prefix + 12-byte nonce + ciphertext + 16-byte tag."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ImportError(
            "cryptography not installed. `pip install cryptography`."
        ) from exc
    nonce = blob[3:15]
    ciphertext = blob[15:]
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    return _finish_plaintext(plaintext, hash_prefix)


def _decrypt_blob_aescbc(blob: bytes, key: bytes, hash_prefix: bool = False) -> str:
    """Linux/macOS AES-128-CBC: 3-byte prefix + ciphertext. IV=b" "*16, PKCS7."""
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # content-guard: ok
    except ImportError as exc:
        raise ImportError(
            "cryptography not installed. `pip install cryptography`."
        ) from exc
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))  # content-guard: ok
    decryptor = cipher.decryptor()  # content-guard: ok
    padded = decryptor.update(blob[3:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return _finish_plaintext(plaintext, hash_prefix)


def _decrypt_blob_dpapi(blob: bytes) -> str:
    """Windows legacy (pre-v10) — entire blob is DPAPI-encrypted."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf_in = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(
        len(blob),
        ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = DATA_BLOB()

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise RuntimeError("DPAPI CryptUnprotectData failed on legacy cookie blob.")
    plaintext = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.WinDLL("kernel32").LocalFree(blob_out.pbData)
    # No hash prefix here: the legacy form predates schema 24 by years.
    return _finish_plaintext(plaintext, hash_prefix=False)


def _decrypt_cookie(
    encrypted_value: bytes,
    keys: dict[str, bytes],
    hash_prefix: bool = False,
) -> str:
    """Decrypt a single Chromium encrypted_value blob, dispatching on prefix.

    `hash_prefix` says whether this DB's schema version puts a 32-byte host
    hash ahead of the value. It reaches the two AES paths only; the legacy
    DPAPI form predates the change.
    """
    if not encrypted_value:
        return ""

    prefix = encrypted_value[:3]

    if prefix == b"v20":
        raise ValueError(
            "App-bound v20 encrypted cookie detected -- not yet supported. "
            "Use yt-dlp `--cookies-from-browser brave` for affected workflows."
        )

    if prefix in (b"v10", b"v11"):
        key = keys.get(prefix.decode("ascii"))
        if key is None:
            raise RuntimeError(
                f"No key available for {prefix.decode('ascii')} cookies on this platform."
            )
        if sys.platform == "win32":
            return _decrypt_blob_aesgcm(encrypted_value, key, hash_prefix)
        return _decrypt_blob_aescbc(encrypted_value, key, hash_prefix)

    if sys.platform == "win32":
        return _decrypt_blob_dpapi(encrypted_value)

    raise ValueError(f"Unknown cookie encryption prefix: {prefix!r}")


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def _schema_version(conn) -> int:
    """Chromium cookie-DB schema version, or 0 when the meta row is absent.

    The version decides whether a decrypted plaintext carries the 32-byte host
    hash. Reading it wrong in the SAFE direction (0) reproduces the old
    behaviour on that one DB rather than corrupting a modern one, so a missing
    or unreadable row degrades to 0 instead of raising.
    """
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
    except sqlite3.Error:
        return 0
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        return 0


# Chromium stores the SameSite attribute as an enum in the `samesite` column:
# -1 UNSPECIFIED, 0 NO_RESTRICTION, 1 LAX_MODE, 2 STRICT_MODE. Playwright wants
# the spelled-out string. UNSPECIFIED maps to Lax because that is the behaviour
# Chromium applies to a cookie that declared nothing, so the exported session
# behaves the way the browser's own session did.
_SAMESITE_TO_PLAYWRIGHT = {-1: "Lax", 0: "None", 1: "Lax", 2: "Strict"}


def _cookie_attrs(path, is_secure, is_httponly, samesite) -> dict:
    """The row's real Playwright-shaped attributes, never a stamped constant.

    `_merge_playwright` used to emit `"path": "/", "secure": True,
    "httpOnly": False, "sameSite": "Lax"` for every cookie, and the SELECT did
    not read the columns that hold the truth. Two of the four constants are
    wrong in a direction that matters:

      * a genuinely HttpOnly session token exported as `httpOnly: false` becomes
        readable by page JavaScript inside the automated context - a widening of
        exactly the session material this module protects everywhere else
        (hidden values, 0600 store, refusal to write a partial store);
      * a cookie the browser set WITHOUT Secure, exported as `secure: true`, is
        never sent over `http://`, so the imported session silently does not
        authenticate while the CLI has already printed a green success line.

    It is the same class as the `domain` defect `_merge_playwright`'s own
    docstring narrates fixing: the browser's scoping is a boundary it maintains
    on purpose, and the export was quietly redrawing it. `path` is here for the
    same reason - a cookie scoped to `/admin` exported at `/` is sent to paths
    the browser would never have sent it to.

    A `samesite` value outside the known enum degrades to Lax rather than
    raising, matching `_schema_version`'s "degrade in the safe direction".
    """
    return {
        "path": str(path) if path else "/",
        "secure": bool(is_secure),
        "httpOnly": bool(is_httponly),
        "sameSite": _SAMESITE_TO_PLAYWRIGHT.get(samesite, "Lax"),
    }


def _read_cookies(
    domain: str,
    profile_name: str = "ClaudeCode",
    browser: str = "brave",
    include_subdomains: bool = True,
) -> tuple[dict[str, tuple[str, str, dict]], list[tuple[str, str, str]]]:
    """The real reader. Returns ({name: (host_key, value, attrs)}, failures).

    `attrs` is the row's own `path` / `secure` / `httpOnly` / `sameSite`, in
    Playwright's spelling - see `_cookie_attrs` for what stamping constants
    there instead cost.

    `failures` is [(name, host_key, reason)] for every cookie that matched but
    could not be decrypted. It used to exist only as stderr noise, so a caller
    could not tell "this profile has no cookies for that domain" from "every
    cookie was there and none of them could be read" - which are the two
    opposite diagnoses, and the CLI printed the first one for both.

    `get_cookies` flattens this to the documented {name: value} map.
    """
    if not domain:
        raise ValueError("domain must be non-empty")
    if not profile_name:
        raise ValueError("profile_name must be non-empty")

    user_data = _resolve_user_data(browser)
    folder = find_profile_folder(user_data, profile_name)
    profile_dir = user_data / folder
    db_path = _cookies_db_path(profile_dir)

    keys = _get_keys(browser, user_data)

    snapshot = _snapshot_db(db_path)
    try:
        conn = sqlite3.connect(read_only_uri(snapshot), uri=True)
        try:
            where, params = host_match_sql("host_key", domain, include_subdomains)
            # noqa S608: `where` carries no caller data. It is built from the
            # column literal on this line, which `host_match_sql` checks with
            # `isidentifier()`, plus `?` placeholders and an ESCAPE clause. The
            # domain is always a bound parameter.
            sql = (
                "SELECT host_key, name, value, encrypted_value, expires_utc, "  # noqa: S608  # nosec B608 - `where` is host_match_sql output: a checked column literal, `?` placeholders and an ESCAPE clause
                "path, is_secure, is_httponly, samesite "
                f"FROM cookies WHERE {where}"
            )

            hash_prefix = _schema_version(conn) >= HASH_PREFIX_SCHEMA_VERSION
            cur = conn.execute(sql, params)
            now_us = (int(time.time()) + 11_644_473_600) * 1_000_000

            live = [
                (host_key, name,
                 (plain, encrypted, _cookie_attrs(path, secure, httponly, samesite)))
                for (host_key, name, plain, encrypted, expires_utc,
                     path, secure, httponly, samesite) in cur.fetchall()
                if not (expires_utc and expires_utc < now_us)
            ]

            # Resolve the name collisions BEFORE decrypting, so a cookie that
            # loses is never decrypted at all, and so the winner is the one the
            # browser would send rather than the last row of the table scan.
            winners, dropped = pick_per_name(live, domain)
            for name, loser, keeper in dropped:
                print(
                    f"{YELLOW}[chromium_cookies] cookie '{name}' exists on both "
                    f"{loser} and {keeper}; kept {keeper}.{RESET}",
                    file=sys.stderr,
                )

            cookies: dict[str, tuple[str, str, dict]] = {}
            failures: list[tuple[str, str, str]] = []
            for name, (host_key, (plain, encrypted, attrs)) in winners.items():
                if plain:
                    cookies[name] = (host_key, plain, attrs)
                    continue
                if not encrypted:
                    cookies[name] = (host_key, "", attrs)
                    continue
                try:
                    cookies[name] = (
                        host_key,
                        _decrypt_cookie(encrypted, keys, hash_prefix),
                        attrs,
                    )
                except Exception as exc:
                    failures.append((name, host_key, str(exc)))
                    print(
                        f"{YELLOW}[chromium_cookies] failed to decrypt cookie "
                        f"'{name}' on host {host_key}: {exc}{RESET}",
                        file=sys.stderr,
                    )
            return cookies, failures
        finally:
            conn.close()
    finally:
        try:
            snapshot.unlink()
        except OSError as exc:
            # Named, not swallowed. The snapshot is a full copy of the cookie
            # store; the operator is entitled to know one is still on disk.
            print(
                f"{YELLOW}[chromium_cookies] could not remove the temporary "
                f"cookie snapshot {snapshot}: {exc}{RESET}",
                file=sys.stderr,
            )


def get_cookies(
    domain: str,
    profile_name: str = "ClaudeCode",
    browser: str = "brave",
    include_subdomains: bool = True,
) -> dict[str, str]:
    """Return cookies for a domain as {name: value}.

    Args:
        domain: Cookie host (no scheme), e.g. "youtube.com".
        profile_name: Chromium display name; defaults to "ClaudeCode".
            Falls back to folder-name match ("Default", "Profile 1", ...).
        browser: One of brave, chrome, chromium, edge.
        include_subdomains: True matches example.com, .example.com, and
            sub.example.com. False = exact host_key match only.

    Expired cookies are filtered out (Chromium expires_utc is microseconds
    since 1601-01-01 UTC). Session cookies (expires_utc=0) are kept since
    they remain live for the browser session.

    When one name exists on several matched hosts, the host-only row for the
    asked-for domain wins, then its domain row, then the shallowest subdomain;
    the losers are named on stderr. See `scripts/utils/cookie_domains.py`.

    Callers that need the host each value came from, or the list of cookies
    that failed to decrypt, use `_read_cookies` instead.

    Raises:
        FileNotFoundError: profile, Cookies DB, or Local State missing.
        RuntimeError: key acquisition or DPAPI failure.
        ImportError: `cryptography` missing. NOT `secretstorage`, which is
            optional: `_get_keys_linux` catches its ImportError, warns, and
            returns the v10-only key map, so a v11 (keyring-encrypted) cookie
            comes back in `failures` instead. This line used to name both, so a
            caller wrapping `get_cookies` in `except ImportError` to detect the
            degraded state was writing dead code, and one trusting the contract
            believed a v11-only profile explodes loudly when it actually
            succeeds with a partial map plus stderr noise - the absent-versus-
            unreadable ambiguity the `failures` list exists to kill.
        sqlite3.Error: Cookies DB unreadable.
    """
    cookies, _failures = _read_cookies(
        domain,
        profile_name=profile_name,
        browser=browser,
        include_subdomains=include_subdomains,
    )
    return {name: value for name, (_host, value, _attrs) in cookies.items()}


def to_cookiejar(cookies: dict[str, str], domain: str | None = None):
    """Convert {name: value} dict to a requests CookieJar."""
    try:
        from requests.cookies import RequestsCookieJar
    except ImportError as exc:
        raise ImportError(
            "requests must be installed to use to_cookiejar(). "
            "pip install requests"
        ) from exc
    jar = RequestsCookieJar()
    for name, value in cookies.items():
        if domain:
            jar.set(name, value, domain=domain, path="/")
        else:
            jar.set(name, value)
    return jar


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def _merge_playwright(
    store: Path,
    domain: str,
    cookies: dict,
    include_subdomains: bool = True,
) -> list:
    """Playwright cookie objects for `domain`, merged into an existing store.

    `cookies` is {name: (host_key, value, attrs)} - the host each value really
    came from, not the domain that was asked for, and the row's own `path` /
    `secure` / `httpOnly` / `sameSite` rather than four stamped constants (see
    `_cookie_attrs`). Every entry used to be stamped
    `domain: ".{domain}"`, which WIDENED a host-only cookie: a token the browser
    scoped to `accounts.google.com` alone was exported as `.google.com`, so
    Playwright then offered it to `mail.google.com` and every other subdomain.
    Host-only scoping is a boundary the browser maintains on purpose, and the
    real host was already selected by the query and thrown away.

    Cookies for other domains are preserved; cookies for THIS domain are
    replaced, because a stale value for a name we just re-read is wrong. Only
    what was actually re-read is evicted: with `--exact-host` the read is the
    exact host alone, so the stored SUBDOMAIN entries are left where they are.
    Evicting them was silent session loss on a flag combination the CLI
    advertises, and the docstring's own justification did not cover it - those
    names were never re-read.

    A store that cannot be parsed is treated as empty rather than as a reason to
    fail: losing another domain's session is bad, but refusing to import is
    worse, and the caller is re-running this precisely because the store is
    unusable. That catch named `json.JSONDecodeError` and `OSError`, which miss
    the `UnicodeDecodeError` a store holding non-UTF-8 bytes raises, so the
    documented recovery crashed on one of the two ways a file is unparseable.
    `ValueError` covers both, since `JSONDecodeError` and `UnicodeDecodeError`
    are each a subclass of it.
    """
    existing = []
    if store.is_file():
        try:
            loaded = json.loads(store.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, ValueError):
            existing = []
    suffix = domain.lstrip(".").lower()

    def _is_this_domain(raw: object) -> bool:
        """True for `x.com` and `sub.x.com`, false for `netflix.com`.

        A bare `endswith(suffix)` had no dot boundary, so importing `x.com` --
        a live target of this workspace, via /x-pulse -- deleted the stored
        cookies of every domain merely ENDING in those characters:
        "netflix.com".endswith("x.com") is True, and so is
        "myyoutube.com".endswith("youtube.com"). The docstring above promises
        the opposite, and the caller had already truncated the file by then.
        """
        got = str(raw).lstrip(".").lower()
        if not include_subdomains:
            return got == suffix
        return got == suffix or got.endswith("." + suffix)

    kept = [c for c in existing
            if isinstance(c, dict) and not _is_this_domain(c.get("domain", ""))]
    fresh = [{"name": n, "value": v, "domain": _playwright_domain(host, suffix),
              **attrs}
             for n, (host, v, attrs) in sorted(cookies.items())]
    return kept + fresh


def _playwright_domain(host_key: str, suffix: str) -> str:
    """Keep the browser's own scoping: host-only stays host-only.

    Chromium writes a leading dot for a domain cookie and none for a host-only
    one. Playwright reads the same convention, so passing `host_key` through
    preserves the distinction. A blank host_key (which the schema should never
    produce) falls back to the asked-for domain rather than exporting an entry
    with no scope at all.
    """
    host = (host_key or "").strip()
    return host if host else f".{suffix}"


def _write_secret_json(out: Path, payload) -> str:
    """Write `payload` to `out` at 0600, atomically. Return the measured mode.

    Two defects, one line apart. The write was `os.open(..., O_CREAT|O_TRUNC,
    0o600)` in place: that mode argument applies ONLY when O_CREAT creates the
    file, so a store already at 0644 - which is what an editor, a `cp`, or a
    checkout of the data overlay leaves - stayed 0644 while the success line
    printed "mode 0600" over a file of live session tokens. And an in-place
    O_TRUNC is not an atomic state write, which
    `~/.claude/CLAUDE.md` requires: an interrupted run leaves a truncated store
    where a whole one was.

    A fresh 0600 temp beside the target, then `os.replace`, fixes both. The
    temp is CREATED each time, so `os.open`'s mode does apply to it, and
    `os.replace` carries that mode onto the destination whatever the
    destination's mode used to be. The returned string is read back off the file
    with `stat`, so the caller states what is true rather than what was intended.
    """
    tmp = out.with_name(out.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return oct(out.stat().st_mode & 0o777)


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Read cookies from a Chromium-family browser profile.",
    )
    parser.add_argument("domain", help="Cookie domain, e.g. youtube.com")
    parser.add_argument(
        "--profile",
        default="ClaudeCode",
        help="Profile display name as shown in browser UI (default: ClaudeCode)",
    )
    parser.add_argument(
        "--browser",
        default="brave",
        choices=sorted(_SUPPORTED_BROWSERS),
        help="Browser family (default: brave)",
    )
    parser.add_argument(
        "--values",
        action="store_true",
        help="Print cookie values too. OFF by default to avoid leaking session "
             "tokens to terminals, logs, or screen shares.",
    )
    parser.add_argument(
        "--exact-host",
        action="store_true",
        help="Match only the exact host (no subdomains).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable.",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Write the {name: value} map to PATH (0600) and print only the "
             "count. The safe way to move live cookies: --values prints session "
             "tokens into the terminal, and under an agent the terminal is the "
             "transcript.",
    )
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="With --out/--store: write Playwright cookie objects and MERGE into "
             "an existing store, keeping other domains. Lets one command do the "
             "whole import, so no cookie value ever passes through a caller.",
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Shorthand for --out <the workspace Playwright cookie store> "
             "--playwright. Resolves the path through the data-root seam, so no "
             "caller has to spell a bare outputs/ path in a shell command.",
    )
    args = parser.parse_args()
    if args.store:
        # Imported here, not at module scope: this file is otherwise
        # self-contained and needs no workspace-root resolution (see the note by
        # the imports). Only --store does.
        from scripts.utils.workspace import get_outputs_dir
        args.out = str(get_outputs_dir() / "browser" / "cookies.json")
        args.playwright = True

    try:
        detailed, failures = _read_cookies(
            args.domain,
            profile_name=args.profile,
            browser=args.browser,
            include_subdomains=not args.exact_host,
        )
        cookies = {name: value for name, (_host, value, _attrs) in detailed.items()}
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"{RED}ERROR: {exc}{RESET}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"{RED}ERROR: SQLite failure reading Cookies DB: {exc}{RESET}",
              file=sys.stderr)
        print(
            f"{YELLOW}Tip: close the browser fully (including tray) and retry; "
            f"the Cookies DB is held open during normal use.{RESET}",
            file=sys.stderr,
        )
        return 2
    except ImportError as exc:
        print(f"{RED}ERROR: {exc}{RESET}", file=sys.stderr)
        return 3

    # A cookie that matched and could not be decrypted is NOT an absent cookie,
    # and every surface below used to report the two the same way. Refusing the
    # write is the same call the empty-read guard already makes, for the same
    # reason: a partial store silently replaces a working one. On a Chrome
    # M127+ profile, where v20 blobs sit beside older ones, partial is the
    # normal outcome, so the guard that only covered total failure covered the
    # rarer half.
    if failures:
        reasons = sorted({reason for _n, _h, reason in failures})
        print(
            f"{RED}ERROR: {len(failures)} of {len(failures) + len(cookies)} "
            f"cookie(s) for {args.domain} could not be decrypted.{RESET}",
            file=sys.stderr,
        )
        for reason in reasons:
            print(f"{YELLOW}  cause: {reason}{RESET}", file=sys.stderr)
        if args.out:
            print(f"{YELLOW}{Path(args.out).expanduser()} left untouched; a "
                  f"partial store would replace a working session with an "
                  f"incomplete one.{RESET}", file=sys.stderr)
        return 4

    if args.out:
        out = Path(args.out).expanduser()
        # Nothing read means nothing to write, and writing anyway DESTROYS what
        # is there: the merge drops this domain's stored entries and the write
        # below replaces the file. An empty read is the normal outcome of a
        # wrong profile or a wrong domain, and it used to end in a green line
        # and exit 0 while the session it was meant to refresh was gone.
        if not cookies:
            print(f"{YELLOW}No cookies found for {args.domain} "
                  f"(profile={args.profile}, browser={args.browser}). "
                  f"{out} left untouched.{RESET}", file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        if args.playwright:
            payload = _merge_playwright(
                out, args.domain, detailed, include_subdomains=not args.exact_host
            )
            # Both numbers. The store total alone said nothing about whether
            # THIS import worked, which is the only question the caller has.
            note = (f"{len(cookies)} cookie(s) imported for {args.domain}; "
                    f"{len(payload)} in the store")
        else:
            payload = cookies
            note = f"{len(cookies)} cookie(s)"
        mode = _write_secret_json(out, payload)
        # Names and a count only. The values are the whole point of the file and
        # must not also appear on stdout. The mode is the one just measured off
        # the file, never the one that was asked for: `os.open`'s mode argument
        # applies only when O_CREAT actually creates, so an existing 0644 store
        # kept 0644 while this line claimed 0600 over live session tokens.
        print(f"{GREEN}{note} written to {out}{RESET} "
              f"{GRAY}(mode {mode}; values not printed){RESET}")
        return 0

    if args.json:
        payload = cookies if args.values else sorted(cookies.keys())
        print(json.dumps(payload, indent=2))
        return 0

    print(f"{BOLD}{CYAN}Cookies for {args.domain}{RESET} "
          f"{GRAY}(profile={args.profile}, browser={args.browser}){RESET}")
    print(f"{GRAY}{'-' * 60}{RESET}")
    if not cookies:
        print(f"{YELLOW}No cookies found. Is the profile logged in?{RESET}")
        return 0
    for name in sorted(cookies.keys()):
        if args.values:
            print(f"  {GREEN}{name}{RESET} = {cookies[name]}")
        else:
            print(f"  {GREEN}{name}{RESET} {GRAY}(value hidden, use --values to print){RESET}")
    print(f"{GRAY}{'-' * 60}{RESET}")
    print(f"{len(cookies)} cookie(s)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
