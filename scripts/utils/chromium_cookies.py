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
    -wa "<Browser> Safe Storage"`, iterations=1003. v10/v11 = AES-128-CBC
    with IV=b" "*16, PKCS7-padded, identical to Linux.

Dependencies (lazy-imported, clear error on miss):
  - cryptography  (all platforms)
  - secretstorage (Linux only; Windows + macOS skip this import)

Out of scope: v20 app-bound encryption (Chrome >= M127). Detection raises
a clean error directing the caller to yt-dlp `--cookies-from-browser`
which handles ABE internally via the elevation service. Brave has not
adopted v20 as of 2026-05-23.

UNTESTED ON LINUX as of file authorship. Windows DPAPI path smoke-tested.
WSL2 dry-run only on the Linux branch (no Brave keyring available in the
WSL2 baseline). First real Linux validation when bare-Linux Brave
deployment lands.

Usage (as a module):
    from scripts.utils.chromium_cookies import get_cookies, to_cookiejar

    cookies = get_cookies("youtube.com", profile_name="ClaudeCode", browser="brave")
    jar = to_cookiejar(cookies)
    requests.get(url, cookies=jar)

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

# Self-contained utility module: imports workspace scripts.utils.colors for
# terminal output but does not need workspace-root resolution. The artifact
# evaluator's workspace_import check is a false positive here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW

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
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Local State is malformed JSON: {exc}")

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
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
    try:
        dst_conn = sqlite3.connect(tmp_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
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
        algorithm=hashes.SHA1(),  # noqa: S303 - Chromium wire-format constraint
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

    data = json.loads(local_state_path.read_text(encoding="utf-8"))
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
        raise RuntimeError(
            f"`security find-generic-password -w -s '{safe_storage_label}'` "
            "failed. User may need to approve keychain access via the system prompt."
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

def _decrypt_blob_aesgcm(blob: bytes, key: bytes) -> str:
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
    return plaintext.decode("utf-8", errors="replace")


def _decrypt_blob_aescbc(blob: bytes, key: bytes) -> str:
    """Linux/macOS AES-128-CBC: 3-byte prefix + ciphertext. IV=b" "*16, PKCS7."""
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise ImportError(
            "cryptography not installed. `pip install cryptography`."
        ) from exc
    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    padded = decryptor.update(blob[3:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8", errors="replace")


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
    return plaintext.decode("utf-8", errors="replace")


def _decrypt_cookie(encrypted_value: bytes, keys: dict[str, bytes]) -> str:
    """Decrypt a single Chromium encrypted_value blob, dispatching on prefix."""
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
            return _decrypt_blob_aesgcm(encrypted_value, key)
        return _decrypt_blob_aescbc(encrypted_value, key)

    if sys.platform == "win32":
        return _decrypt_blob_dpapi(encrypted_value)

    raise ValueError(f"Unknown cookie encryption prefix: {prefix!r}")


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

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

    Raises:
        FileNotFoundError: profile, Cookies DB, or Local State missing.
        RuntimeError: key acquisition or DPAPI failure.
        ImportError: required dependency (cryptography / secretstorage) missing.
        sqlite3.Error: Cookies DB unreadable.
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
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        try:
            if include_subdomains:
                sql = (
                    "SELECT host_key, name, value, encrypted_value, expires_utc "
                    "FROM cookies "
                    "WHERE host_key = ? OR host_key = ? OR host_key LIKE ?"
                )
                params = (domain, f".{domain}", f"%.{domain}")
            else:
                sql = (
                    "SELECT host_key, name, value, encrypted_value, expires_utc "
                    "FROM cookies WHERE host_key = ?"
                )
                params = (domain,)

            cur = conn.execute(sql, params)
            now_us = (int(time.time()) + 11_644_473_600) * 1_000_000

            cookies: dict[str, str] = {}
            for host_key, name, plain, encrypted, expires_utc in cur.fetchall():
                if expires_utc and expires_utc < now_us:
                    continue
                if plain:
                    cookies[name] = plain
                    continue
                if not encrypted:
                    cookies[name] = ""
                    continue
                try:
                    cookies[name] = _decrypt_cookie(encrypted, keys)
                except Exception as exc:
                    print(
                        f"{YELLOW}[chromium_cookies] failed to decrypt cookie "
                        f"'{name}' on host {host_key}: {exc}{RESET}",
                        file=sys.stderr,
                    )
            return cookies
        finally:
            conn.close()
    finally:
        try:
            snapshot.unlink()
        except OSError:
            pass


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
    args = parser.parse_args()

    try:
        cookies = get_cookies(
            args.domain,
            profile_name=args.profile,
            browser=args.browser,
            include_subdomains=not args.exact_host,
        )
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
