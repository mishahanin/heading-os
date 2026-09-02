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

Tests: tests/test_an_allowlist_that_admitted_a_flag.py
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
from typing import Iterator, List, Optional, Tuple
from urllib.request import urlopen

# Allow `from scripts.browser import ...` whether imported by a skill
# script or invoked directly as `python scripts/browser.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402

WORKSPACE_ROOT = get_workspace_root()

# Browser configuration table. Per-platform paths for each supported
# Chromium-family browser the workspace knows how to launch with CDP.
# Brave is the default on every platform (see DEFAULT_BROWSER below and the
# module docstring). Comet is a Windows/macOS-only opt-in fallback and has no
# Linux build at all. This comment said the reverse until 2026-08-24, which
# contradicted both the constant twenty lines down and the module header —
# anyone editing the table from the comment would have reasoned backwards.
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


def lock_file() -> Path:
    """Resolved at call time, never at import.

    `get_outputs_dir()` reads `HEADING_OS_DATA` on every call, so it follows the
    environment for a caller that asks after the environment moved. As a
    module-level constant it asked once, during its own import, and stored the
    answer, so a test that imported this module and then repointed the data root
    still governed a lock and a log inside the operator's real overlay. The
    `mkdir` in `launch_comet` is not among the primitives `tests/conftest.py`
    wraps, so a stray directory there drew no refusal.
    """
    return get_outputs_dir() / "browser" / "browser-cdp.json"


def _legacy_lock_file() -> Path:
    return get_outputs_dir() / "browser" / "comet-cdp.json"


def launch_log() -> Path:
    # Browser stdout/stderr goes here instead of the caller's streams. Truncated
    # on every launch, so it holds the current session only and cannot grow
    # unbounded.
    return get_outputs_dir() / "browser" / "browser-launch.log"


def _active_lock_file() -> Optional[Path]:
    """Return whichever lock file exists, preferring the new name.

    Backward-compat read: a workspace that already had a CDP session
    running when the comet-cdp.json -> browser-cdp.json rename landed
    keeps the legacy name until that session stops.
    """
    if lock_file().exists():
        return lock_file()
    if _legacy_lock_file().exists():
        return _legacy_lock_file()
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


def _write_lock(port: int, pid: int, browser: str) -> None:
    """Record the CDP session so `stop` can find it. Atomic.

    A plain `write_text` was the write here until 2026-08-24, against the
    workspace's own no-non-atomic-state-writes rule. A crash or a concurrent
    read mid-write leaves truncated JSON; `stop_comet` swallows the parse error
    into an empty state, and on Windows — where `_pids_for_cdp_port` cannot
    recover the owner from `ps` — that empty state leaves nothing to signal, so
    the session can never be stopped through this tool again.
    """
    atomic_write_text(
        lock_file(),
        json.dumps({"port": port, "pid": pid, "browser": browser}, indent=2) + "\n",
    )


def _adopt_running_cdp(port: int, browser: str) -> int:
    """Reuse a CDP endpoint that is already up, or refuse it.

    `_cdp_ready` answers for ANY Chromium-family process serving
    `/json/version` on the port. Until 2026-08-24 that was the whole check, so
    launching Brave while a stray Chrome (or Comet) owned 9222 "succeeded": it
    returned 0 rather than the PID its docstring promised, wrote no lock file,
    and every later `attach()` silently drove the wrong browser while `stop`
    reported "nothing tracked to stop".
    """
    owners = _pids_for_cdp_port(port)
    mine = [pid for pid in owners if _pid_is_browser(pid, browser)]
    if owners and not mine:
        raise RuntimeError(
            f"Port {port} already serves CDP, but PID {owners[0]} is not "
            f"{browser}. Stop that browser, or launch on another --port."
        )
    if not mine:
        # No `ps` to read (Windows) or the owner's cmdline is unreadable. The
        # endpoint answers and we cannot say whose it is; say that, rather than
        # record a lock naming a browser nothing verified.
        _log(f"CDP already ready on port {port}, but this platform cannot "
             f"identify its owner; reusing it without a lock file.", YELLOW)
        return 0
    pid = mine[0]
    _write_lock(port, pid, browser)
    _log(f"CDP already ready on port {port} (PID {pid}, {browser}); reusing.", GREEN)
    return pid


def _abandon_launch(proc: subprocess.Popen, port: int, browser: str) -> None:
    """Kill a browser we launched that never opened its CDP port.

    Until 2026-08-24 the timeout path raised and left the child running: an
    untracked browser on the pre-authenticated ClaudeCode profile, holding an
    unauthenticated loopback debug port any local process could attach to, with
    no lock file — so `stop` answered "nothing tracked to stop" while the
    process kept the port. The next `launch` then took the "CDP already ready"
    branch, and the session was permanently unstoppable through this tool.
    """
    for pid in _pids_for_cdp_port(port):
        if pid == proc.pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            _log(f"Launch timed out; sent SIGTERM to CDP owner PID {pid}", YELLOW)
        except ProcessLookupError:
            pass
        except OSError as e:
            _log(f"Launch timed out; SIGTERM to PID {pid} failed: {e}", RED)
    with contextlib.suppress(OSError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
        _log(f"Launch timed out; killed {browser} launcher PID {proc.pid}", YELLOW)
    else:
        _log(f"Launch timed out; terminated {browser} launcher PID {proc.pid}", YELLOW)


def launch_comet(
    port: int = DEFAULT_PORT,
    initial_url: Optional[str] = None,
    profile_folder: str = COMET_PROFILE_FOLDER,
    wait_timeout: float = 30.0,
    browser: str = DEFAULT_BROWSER,
) -> int:
    """Launch the chosen browser externally with CDP enabled. Returns PID.

    The `browser` parameter selects between supported Chromium-family browsers.
    It defaults to `DEFAULT_BROWSER` ('brave'), which is the primary on every
    platform; 'comet' is a Windows/macOS-only opt-in with no Linux build. This
    docstring said the default was 'comet' until 2026-08-24, and the signature
    had not agreed with it for months.

    Refuses to launch if the browser is already running — CDP won't attach to
    an already-owned profile — and refuses to reuse a CDP port owned by some
    OTHER browser.

    The returned PID is 0 in exactly one case: the port already served CDP and
    this platform could not identify the owning process (see
    `_adopt_running_cdp`).

    Note: function name retained for backward compatibility with existing
    callers. For new code, prefer `launch_browser()`.
    """
    if _cdp_ready(port):
        return _adopt_running_cdp(port, browser)

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
    # Detach the browser's standard streams from the caller's. A browser that
    # inherits them holds the caller's stdout for its ENTIRE lifetime, so a
    # pipeline never reaches EOF: on 2026-07-28 a
    # `browser.py launch ... 2>&1 | tail -20` shell stayed alive 12.5 hours
    # after `timeout 90` had already killed python, because /usr/bin/brave-browser
    # is a shell wrapper whose `cat` helpers keep that write end open long after
    # the launcher itself exits. Writing to a file gives the child its own fds.
    log_path = launch_log()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log
        )
    _log(f"{browser} PID: {proc.pid} (output -> {log_path})", GREEN)

    deadline = time.time() + wait_timeout
    while time.time() < deadline and not _cdp_ready(port):
        time.sleep(0.5)
    if not _cdp_ready(port):
        _abandon_launch(proc, port, browser)
        raise TimeoutError(f"CDP did not become ready on port {port} within {wait_timeout}s")

    # Record the process that actually owns the CDP port, not Popen's PID. On
    # Debian/Ubuntu `/usr/bin/brave-browser` is a shell wrapper that forks the
    # real binary and exits, so Popen's PID is dead by now (and its number is
    # free to be recycled onto an unrelated process).
    owners = _pids_for_cdp_port(port)
    real_pid = owners[0] if owners else proc.pid
    if real_pid != proc.pid:
        _log(f"{browser} re-parented: launcher {proc.pid} -> browser {real_pid}", GRAY)

    _write_lock(port, real_pid, browser)
    _log(f"CDP ready on http://127.0.0.1:{port}", GREEN)
    return real_pid


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
    """Attach Playwright to an externally-launched browser. Yields (browser, context).

    On exit, drops the CDP connection but does NOT close the browser. Calling
    `browser.close()` on a CDP-attached session terminates the whole browser —
    avoid. (Named Comet here until 2026-08-24; Brave has been the default on
    every platform since the 2026-05-24 rename.)
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ImportError("playwright not installed. `pip install playwright && python -m playwright install chromium`") from e

    if not _cdp_ready(port):
        raise ConnectionError(
            f"No CDP endpoint on port {port}. Call launch_browser() first, "
            f"or ensure the browser was launched with --remote-debugging-port={port}."
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


def _pid_cmdline(pid: int) -> str:
    """Full command line of a PID, or "" if it cannot be read.

    Reads /proc where available (Linux) and falls back to `ps` (macOS).
    """
    proc_file = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_file.exists():
            return proc_file.read_bytes().replace(b"\0", b" ").decode(errors="ignore")
    except OSError:
        return ""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, check=False,
        )
        return out.stdout.strip()
    except (FileNotFoundError, OSError):
        return ""


def _pid_is_browser(pid: int, browser_name: str = DEFAULT_BROWSER) -> bool:
    """True only if `pid` is live AND really is that browser.

    Guards against PID reuse: the launcher wrapper exits within milliseconds,
    so a stale tracked PID can be recycled onto an unrelated process. Signalling
    it blind would kill a bystander.
    """
    try:
        process_name = _browser_paths(browser_name)["process_name"]
    except ValueError:
        return False

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}"], text=True, errors="ignore"
            )
        except Exception:
            return False
        return process_name.lower() in out.lower()

    cmdline = _pid_cmdline(pid)
    if not cmdline:
        return False
    # Match the executable only (argv[0]), never the whole command line: a URL
    # or a path component containing the browser name would false-positive.
    return _exe_name_matches(Path(cmdline.split()[0]).name, process_name)


def _exe_name_matches(exe_name: str, process_name: str) -> bool:
    """True when `exe_name` names `process_name`'s binary, at a name boundary.

    A plain `in` test was the guard here until 2026-08-24, and it defeated the
    PID-reuse protection this function exists to be: `"comet" in "competent"`
    and `"brave" in "unbrave-daemon"` are both True, so a recycled PID landing
    on any process whose basename merely CONTAINS the browser name verified as
    the browser and was then SIGTERMed by `stop_comet`.

    Equality alone is not the fix, because it breaks the common case: on
    Debian/Ubuntu the configured `process_name` is `brave` while the binary on
    disk is `brave-browser`, so an `==` guard would make `stop` ignore every
    tracked PID on Linux. The boundary rule keeps that and refuses the rest —
    the name matches, or it matches as a prefix followed by a separator.
    """
    exe = exe_name.lower()
    want = process_name.lower()
    if exe == want:
        return True
    return exe.startswith(want) and not exe[len(want):len(want) + 1].isalnum()


def _parse_cdp_owner_pids(ps_output: str, port: int, self_pid: int) -> List[int]:
    """Pick the main browser PIDs out of `ps -eo pid=,args=` output.

    Three kinds of process carry `--remote-debugging-port=<port>` in their
    argv, and only the second is the one to signal (verified on WSL2,
    2026-07-27):

    * the launcher wrapper — on Debian/Ubuntu `/usr/bin/brave-browser` is a
      bash script. It is the PID `Popen` reports, and killing it orphans the
      browser instead of stopping it.
    * the browser itself — no `--type=` flag.
    * renderer / zygote / gpu / utility children — they inherit the flag in
      their command line and are identified by `--type=`. They die with the
      browser, so signalling them is noise at best.
    """
    flag = f"--remote-debugging-port={port}"
    pids = []
    for line in ps_output.splitlines():
        line = line.strip()
        if flag not in line:
            continue
        head, _, rest = line.partition(" ")
        if not head.isdigit():
            continue
        pid = int(head)
        if pid == self_pid:
            continue
        rest = rest.strip()
        if "--type=" in rest:            # renderer / zygote / gpu / utility
            continue
        argv0 = rest.split()[0] if rest else ""
        if Path(argv0).name in ("sh", "bash", "ps", "grep", "env", "timeout"):
            continue                      # launcher wrapper or an observer
        pids.append(pid)
    return pids


def _pids_for_cdp_port(port: int) -> List[int]:
    """PIDs of the main browser process(es) holding `port` for CDP.

    Authoritative because it survives the Debian/Ubuntu wrapper forking away
    from the PID that Popen reported.

    Windows returns an empty list — there the launcher is the browser exe
    itself, so the verified tracked PID is already correct.
    """
    if sys.platform == "win32":
        return []
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False
        )
    except (FileNotFoundError, OSError):
        return []
    return _parse_cdp_owner_pids(out.stdout, port, os.getpid())


def _wait_until_cdp_down(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while True:
        if not _cdp_ready(port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


def _clear_lock(lock) -> None:
    """Remove the lock file if there is one. No-op when stopping by port alone."""
    if lock is None:
        return
    with contextlib.suppress(OSError):
        lock.unlink()


def stop_comet(port: Optional[int] = None, timeout: float = 10.0) -> bool:
    """Stop the CDP browser session. Returns True only if it actually stopped.

    Terminates every process holding the CDP port (plus the tracked PID when it
    verifies as the browser), waits for the port to close, escalates to SIGKILL
    if SIGTERM is ignored, and clears the lock file only on confirmed shutdown.
    A surviving browser leaves the lock in place so the caller can retry.

    `port` alone is a complete instruction ON POSIX: with no lock file this
    stops whatever holds that port, because `_pids_for_cdp_port` can name the
    owner from `ps`. It is NOT complete on Windows, where that function returns
    an empty list by design and the owner can only come from the lock.

    That qualifier was missing, and the sentence was false on exactly the
    platform that creates the state it described. `_adopt_running_cdp` writes no
    lock precisely when it cannot identify an owner, which on Windows is always,
    so `stop --port N` there found no lock, no tracked PID and no port owner:
    both signal rounds iterated an empty list, `_wait_until_cdp_down` burned the
    full timeout twice, and the call returned False after about fifteen seconds
    having signalled nothing at all. It refuses in one line now, and says what
    the operator has to do instead.
    """
    lock = _active_lock_file()
    # An explicit `port` is enough on its own. A missing lock used to end the
    # function here, which made a session UNSTOPPABLE from the CLI in the two
    # states this file already documents: `_adopt_running_cdp` reuses a live
    # endpoint "without a lock file", and an unreadable lock leaves no tracked
    # PID to recover on Windows. With a port named, there is something to aim
    # at whether or not a lock exists.
    if lock is None and port is None:
        _log("No lock file and no port given; nothing tracked to stop. "
             "Pass --port to stop an untracked session.", YELLOW)
        return False

    state = {}
    if lock is None:
        _log(f"No lock file; stopping whatever holds CDP port {port}.", YELLOW)
    else:
        try:
            state = json.loads(lock.read_text())
        except (OSError, ValueError) as e:
            # Never silent: an unreadable lock means the tracked PID is gone, and
            # on Windows `_pids_for_cdp_port` cannot recover it, so `stop` is
            # about to signal nothing at all. Say which file and why.
            _log(f"Lock {lock.name} is unreadable ({e}); falling back to whatever "
                 f"holds the CDP port.", YELLOW)
        if not isinstance(state, dict):
            _log(f"Lock {lock.name} does not hold an object; ignoring its contents.", YELLOW)
            state = {}
    port = port or state.get("port") or DEFAULT_PORT
    browser_name = state.get("browser") or DEFAULT_BROWSER
    tracked = state.get("pid")

    targets = list(_pids_for_cdp_port(port))
    if tracked and tracked not in targets and _pid_is_browser(tracked, browser_name):
        targets.append(tracked)
    elif tracked and tracked not in targets:
        _log(f"Tracked PID {tracked} is not {browser_name}; ignoring it.", GRAY)

    if not targets and not _cdp_ready(port):
        _log("Browser already stopped; clearing lock." if lock
             else f"Nothing is holding CDP port {port}.", GREEN)
        _clear_lock(lock)
        return True

    # Serving, and nothing to aim at. On POSIX that is a transient race worth
    # signalling through; on Windows it is a permanent state, because
    # `_pids_for_cdp_port` returns [] there by design and the tracked PID is the
    # only owner this file can ever recover. Signalling an empty list twice and
    # waiting out both timeouts told the operator nothing after fifteen seconds.
    # Refuse in one line instead, and name the two ways out.
    if not targets and sys.platform == "win32":
        _log(f"CDP port {port} is serving but this platform cannot identify "
             f"the process holding it, and no usable lock file names one. "
             f"Close the browser window, or stop the process by hand "
             f"(`netstat -ano | findstr :{port}` gives its PID).", RED)
        return False

    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    for sig, label, wait in ((signal.SIGTERM, "SIGTERM", timeout),
                             (sigkill, "SIGKILL", min(timeout, 5.0))):
        for pid in targets:
            try:
                os.kill(pid, sig)
                _log(f"Sent {label} to PID {pid}", GREEN)
            except ProcessLookupError:
                _log(f"PID {pid} already gone", GRAY)
            except OSError as e:
                _log(f"{label} to PID {pid} failed: {e}", RED)
        if _wait_until_cdp_down(port, wait):
            _log(f"CDP port {port} closed; browser stopped.", GREEN)
            _clear_lock(lock)
            return True
        if sig is not sigkill:
            _log(f"Still up after {label}; escalating.", YELLOW)

    _log(f"Browser still answering on port {port}; lock kept for retry.", RED)
    return False


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


def _lock_state() -> dict:
    """The recorded session, or an empty dict. Never raises."""
    lock = _active_lock_file()
    if lock is None:
        return {}
    try:
        state = json.loads(lock.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def cmd_status(args: argparse.Namespace) -> int:
    # Probe the port the session is actually on. `status` probed DEFAULT_PORT
    # unconditionally until 2026-08-24 and offered no --port, so a session
    # launched with `--port 9333` reported "not listening / not reachable" and
    # exited 2 — a false "down" reading on a healthy browser.
    port = args.port or _lock_state().get("port") or DEFAULT_PORT
    running = is_running(args.browser)
    port_open = _port_listening(port)
    cdp = _cdp_ready(port)
    _log(f"{args.browser} running: {running}")
    _log(f"CDP port {port} listening: {port_open}")
    _log(f"CDP endpoint reachable: {cdp}")
    lock = _active_lock_file()
    if lock is not None:
        # Guarded like `_lock_state` and `stop_comet` already guard the same
        # read. `_active_lock_file()` checks existence and this reads a moment
        # later, so a lock removed in between - or one this user cannot read -
        # crashed a health command with a traceback instead of reporting.
        #
        # `ValueError` sits beside `OSError` because the two siblings in this
        # module catch exactly that pair and this one caught only half of it.
        # `read_text()` decodes as UTF-8, and `UnicodeDecodeError` is a
        # `ValueError` rather than an `OSError`, so it walked straight past this
        # handler. MEASURED 2026-09-01 on a lock file carrying one 0xe9 byte:
        # `cmd_status` died with `UnicodeDecodeError: 'utf-8' codec can't decode
        # byte 0xe9 in position 23`, while `_lock_state` and `stop_comet` reading
        # the SAME file degraded politely. The fix that added the pair landed in
        # two of the three readers.
        try:
            body = lock.read_text().strip()
        except (OSError, ValueError) as exc:
            body = f"<unreadable: {exc}>"
        _log(f"Lock file ({lock.name}): {body}")
    return 0 if cdp else 2


def cmd_stop(args: argparse.Namespace) -> int:
    # `--port`, for the same reason `status` grew one on 2026-08-24. `stop_comet`
    # has always taken a port and this command always discarded its args, so a
    # session on a non-default port was UNSTOPPABLE from the CLI in exactly the
    # states the rest of this file engineers around: `_adopt_running_cdp` reuses
    # a live endpoint without writing a lock file, and on Windows `stop_comet`
    # cannot recover an owner from an unreadable lock. With no lock and no way
    # to name the port, `stop` printed "nothing tracked to stop" and exited 1
    # while the browser kept serving.
    #
    # The Windows half of that is a REFUSAL, not a fix, and this comment read as
    # though `--port` had settled it. `_pids_for_cdp_port` returns [] on Windows
    # by design, so a port with no lock behind it still cannot be stopped there;
    # `stop_comet` now says so in one line rather than signalling an empty list
    # twice and waiting out both timeouts. On POSIX `--port` really is enough.
    return 0 if stop_comet(port=getattr(args, "port", None)) else 1


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
    sp.add_argument("--port", type=int, default=None,
                    help=f"CDP port to probe (default: the lock file's port, "
                         f"else {DEFAULT_PORT})")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("stop", help="Terminate the tracked browser CDP session")
    sp.add_argument("--port", type=int, default=None,
                    help=f"CDP port to stop (default: the lock file's port, "
                         f"else {DEFAULT_PORT})")
    sp.set_defaults(func=cmd_stop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
