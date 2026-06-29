#!/usr/bin/env python3
"""Chromium-family browser automation helper (CDP-attach pattern).

Default browser for all Claude-driven browser automation: **Brave** (cross-
platform, yt-dlp-native, ClaudeCode profile pre-authenticated). Comet
(Perplexity's Chromium-based browser, Windows + macOS only) is retained as
an opt-in fallback via `--browser comet`; new automation defaults to Brave.

Renamed from `scripts/comet_browser.py` on 2026-05-24. Most internal
function names retain the `comet` suffix (`launch_comet`, `stop_comet`)
for backward compatibility; `launch_browser()` is the preferred alias.

Why CDP-attach pattern (works for Comet AND Brave):
  - `launch_persistent_context(executable_path=...)` fails — Comet closes
    the CDP-controlled tab immediately; some Brave builds do the same.
  - Vivaldi rejects Playwright's launch flags outright (do not use it for
    automation; see `feedback_never_suggest_vivaldi`).
  - `connect_over_cdp` against an externally-launched browser is stable.

Critical rules:
  - NEVER call `browser.close()` on a CDP-attached session — it kills the
    whole browser. Let the `sync_playwright()` context exit naturally.
  - Comet's Perplexity sidecar injects tabs (`perplexity.ai/sidecar`,
    `perplexity.ai/b/home`). Brave does not have a sidecar but other
    extensions can inject background tabs. Filter tabs by URL substring,
    not `ctx.pages[0]`.
  - The profile display name "ClaudeCode" maps to folder name "Default" in
    both Comet and Brave.

Usage:
    from scripts.browser import launch_browser, attach, pick_tab

    launch_browser("brave", port=9222, initial_url="https://zoom.us/signin")
    with attach(port=9222) as (browser, ctx):
        page = pick_tab(ctx, "zoom.us")
        page.bring_to_front()
        page.goto("https://zoom.us/profile/setting", wait_until="domcontentloaded")
        # ... do work ...

CLI:
    python scripts/browser.py launch --url https://zoom.us/signin
    python scripts/browser.py status
    python scripts/browser.py stop
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple
from urllib.request import urlopen

# Allow `from scripts.browser import ...` whether imported by a skill
# script or invoked directly as `python scripts/browser.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402

WORKSPACE_ROOT = get_workspace_root()

# Browser configuration table. Per-platform paths for each supported
# Chromium-family browser the workspace knows how to launch with CDP.
# Comet is the default on Windows/macOS (Perplexity-native, has Linux NO build).
# Brave is the cross-platform fallback (Linux/macOS/Windows) — useful on Linux
# where Comet does not exist, or as a clean second profile on any OS.
_BROWSER_CONFIGS = {
    "comet": {
        "win32": {
            "exe": r"C:\Program Files\Perplexity\Comet\Application\comet.exe",
            "user_data": r"~\AppData\Local\Perplexity\Comet\User Data",
            "process_name": "comet.exe",
        },
        "darwin": {
            "exe": "/Applications/Comet.app/Contents/MacOS/Comet",
            "user_data": "~/Library/Application Support/Perplexity/Comet",
            "process_name": "Comet",
        },
        # Linux: no Comet build exists. Selecting comet on Linux raises.
    },
    "brave": {
        "win32": {
            "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            "user_data": r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data",
            "process_name": "brave.exe",
        },
        "darwin": {
            "exe": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "user_data": "~/Library/Application Support/BraveSoftware/Brave-Browser",
            "process_name": "Brave Browser",
        },
        "linux": {
            "exe": "/usr/bin/brave-browser",  # also /usr/bin/brave on some distros
            "user_data": "~/.config/BraveSoftware/Brave-Browser",
            "process_name": "brave",
        },
    },
}

DEFAULT_BROWSER = "brave"
COMET_PROFILE_FOLDER = "Default"  # display name "ClaudeCode" lives in this folder
DEFAULT_PORT = 9222
LOCK_FILE = get_outputs_dir() / "browser" / "browser-cdp.json"
_LEGACY_LOCK_FILE = get_outputs_dir() / "browser" / "comet-cdp.json"


def _active_lock_file() -> Optional[Path]:
    """Return whichever lock file exists, preferring the new name.

    Backward-compat read: a workspace that already had a CDP session
    running when the comet-cdp.json -> browser-cdp.json rename landed
    keeps the legacy name until that session stops.
    """
    if LOCK_FILE.exists():
        return LOCK_FILE
    if _LEGACY_LOCK_FILE.exists():
        return _LEGACY_LOCK_FILE
    return None


def _browser_paths(browser: str = DEFAULT_BROWSER) -> dict:
    """Resolve per-OS exe + user_data + process_name for a browser.

    Returns a dict {exe: Path, user_data: Path, process_name: str}.
    Raises ValueError if the browser is unknown or unsupported on this OS.
    Resolves at call time so importing this module on a platform without the
    browser does not crash.
    """
    cfg = _BROWSER_CONFIGS.get(browser.lower())
    if cfg is None:
        raise ValueError(
            f"Unknown browser '{browser}'. Supported: {sorted(_BROWSER_CONFIGS)}"
        )
    plat_cfg = cfg.get(sys.platform)
    if plat_cfg is None:
        # Linux has no Comet build, etc.
        raise ValueError(
            f"Browser '{browser}' is not supported on platform '{sys.platform}'. "
            f"Try one of: {sorted(_BROWSER_CONFIGS)}"
        )
    return {
        "exe": Path(plat_cfg["exe"]).expanduser(),
        "user_data": Path(plat_cfg["user_data"]).expanduser(),
        "process_name": plat_cfg["process_name"],
    }


def _log(msg: str, color: str = CYAN) -> None:
    print(f"{color}[browser]{RESET} {msg}", flush=True)


def _cdp_ready(port: int, timeout: float = 1.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def is_running(browser: str = DEFAULT_BROWSER) -> bool:
    """Return True if the named browser has at least one running process.

    Cross-platform: uses `tasklist` on Windows, `pgrep` on POSIX.
    """
    try:
        process_name = _browser_paths(browser)["process_name"]
    except ValueError:
        return False

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(["tasklist"], text=True, errors="ignore")
        except Exception:
            return False
        target = process_name.lower()
        return any(line.lower().startswith(target) for line in out.splitlines())

    # POSIX: match against the comm field (basename, no flag), not the full
    # command-line. With `-f` even an unrelated process whose argv contains the
    # substring 'brave' (a path component, a python import line, anything) would
    # return a false positive — verified empirically 2026-05-23 on WSL2.
    try:
        result = subprocess.run(
            ["pgrep", process_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except FileNotFoundError:
        # pgrep not available; very minimal Linux. Skip detection.
        return False


def launch_comet(
    port: int = DEFAULT_PORT,
    initial_url: Optional[str] = None,
    profile_folder: str = COMET_PROFILE_FOLDER,
    wait_timeout: float = 30.0,
    browser: str = DEFAULT_BROWSER,
) -> int:
    """Launch the chosen browser externally with CDP enabled. Returns PID.

    The `browser` parameter selects between supported Chromium-family browsers
    (default: 'comet'; pass 'brave' on Linux where Comet has no build).

    Refuses to launch if the browser is already running — CDP won't attach to
    an already-owned profile.

    Note: function name retained for backward compatibility with existing
    callers. For new code, prefer `launch_browser()`.
    """
    if _cdp_ready(port):
        _log(f"CDP already ready on port {port}; reusing.", GREEN)
        return 0

    paths = _browser_paths(browser)

    if is_running(browser):
        _log(f"{browser} is already running. Close it fully (including tray) "
             "before launching with CDP. Aborting.", RED)
        raise RuntimeError(f"{browser} already running; CDP attach requires fresh launch")

    exe = paths["exe"]
    if not exe.exists():
        raise FileNotFoundError(f"{browser} not found at {exe}")

    cmd = [
        str(exe),
        f"--remote-debugging-port={port}",
        f"--profile-directory={profile_folder}",
        f"--user-data-dir={paths['user_data']}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if initial_url:
        cmd.append(initial_url)

    _log(f"Launching: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd)
    _log(f"{browser} PID: {proc.pid}", GREEN)

    deadline = time.time() + wait_timeout
    while time.time() < deadline and not _cdp_ready(port):
        time.sleep(0.5)
    if not _cdp_ready(port):
        raise TimeoutError(f"CDP did not become ready on port {port} within {wait_timeout}s")

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({"port": port, "pid": proc.pid, "browser": browser}, indent=2))
    _log(f"CDP ready on http://127.0.0.1:{port}", GREEN)
    return proc.pid


def launch_browser(
    name: str,
    port: int = DEFAULT_PORT,
    initial_url: Optional[str] = None,
    profile_folder: str = COMET_PROFILE_FOLDER,
    wait_timeout: float = 30.0,
) -> int:
    """Launch a named browser via CDP. Thin alias over launch_comet(browser=name)."""
    return launch_comet(
        port=port,
        initial_url=initial_url,
        profile_folder=profile_folder,
        wait_timeout=wait_timeout,
        browser=name,
    )


@contextlib.contextmanager
def attach(port: int = DEFAULT_PORT) -> Iterator[Tuple[object, object]]:
    """Attach Playwright to an externally-launched Comet. Yields (browser, context).

    On exit, drops the CDP connection but does NOT close Comet. Calling
    `browser.close()` on a CDP-attached Comet terminates the browser — avoid.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError("playwright not installed. `pip install playwright && python -m playwright install chromium`") from e

    if not _cdp_ready(port):
        raise ConnectionError(
            f"No CDP endpoint on port {port}. Call launch_comet() first, "
            f"or ensure Comet was launched with --remote-debugging-port={port}."
        )

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        try:
            yield browser, ctx
        finally:
            pass  # context manager exit drops the connection; Comet stays alive


def pick_tab(ctx, url_substring: str, bring_to_front: bool = True):
    """Pick a tab from `ctx.pages` whose URL contains `url_substring`.

    Never trust `ctx.pages[0]` — Comet's Perplexity sidecar injects tabs at
    unpredictable positions.

    Raises LookupError if no matching tab is found.
    """
    for p in ctx.pages:
        u = p.url or ""
        if url_substring in u and "chrome-error" not in u:
            if bring_to_front:
                try:
                    p.bring_to_front()
                except Exception as exc:
                    print(f"browser: bring_to_front failed: {exc}", file=sys.stderr)
            return p
    open_urls = [p.url for p in ctx.pages]
    raise LookupError(
        f"No tab matching '{url_substring}'. Open tabs: {open_urls}"
    )


def safe_goto(page, url: str, wait_until: str = "domcontentloaded", timeout_ms: int = 45000) -> bool:
    """Navigate with a soft-fail and settle pause. Returns True on success."""
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        time.sleep(2)
        return True
    except Exception as e:
        _log(f"goto soft-fail for {url}: {e}", YELLOW)
        return False


def stop_comet() -> bool:
    """Send SIGTERM to the tracked PID (if any) and clear the lock file."""
    lock = _active_lock_file()
    if lock is None:
        _log("No lock file; nothing tracked to stop.", YELLOW)
        return False
    try:
        state = json.loads(lock.read_text())
        pid = state.get("pid")
        if pid:
            os.kill(pid, signal.SIGTERM)
            _log(f"Sent SIGTERM to PID {pid}", GREEN)
    except Exception as e:
        _log(f"stop failed: {e}", RED)
        return False
    finally:
        with contextlib.suppress(Exception):
            lock.unlink()
    return True


def cmd_launch(args: argparse.Namespace) -> int:
    try:
        launch_comet(
            port=args.port,
            initial_url=args.url,
            profile_folder=args.profile,
            browser=args.browser,
        )
    except Exception as e:
        _log(str(e), RED)
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    running = is_running(args.browser)
    port_open = _port_listening(DEFAULT_PORT)
    cdp = _cdp_ready(DEFAULT_PORT)
    _log(f"{args.browser} running: {running}")
    _log(f"CDP port {DEFAULT_PORT} listening: {port_open}")
    _log(f"CDP endpoint reachable: {cdp}")
    lock = _active_lock_file()
    if lock is not None:
        _log(f"Lock file ({lock.name}): {lock.read_text().strip()}")
    return 0 if cdp else 2


def cmd_stop(_: argparse.Namespace) -> int:
    return 0 if stop_comet() else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Chromium-family browser CDP launcher/attach helper.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("launch", help="Launch a Chromium-family browser with CDP enabled")
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--url", default=None, help="Initial URL to load")
    sp.add_argument("--profile", default=COMET_PROFILE_FOLDER,
                    help="Browser profile folder name (default 'Default' — display 'ClaudeCode')")
    sp.add_argument("--browser", default=DEFAULT_BROWSER,
                    choices=sorted(_BROWSER_CONFIGS),
                    help=f"Which browser to launch (default '{DEFAULT_BROWSER}'). "
                         "Brave is the cross-platform primary; 'comet' is a "
                         "Windows/macOS-only opt-in fallback.")
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser("status", help="Report CDP endpoint state")
    sp.add_argument("--browser", default=DEFAULT_BROWSER,
                    choices=sorted(_BROWSER_CONFIGS),
                    help="Which browser to check (default '%(default)s')")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("stop", help="Terminate tracked Comet CDP session")
    sp.set_defaults(func=cmd_stop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
