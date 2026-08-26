"""End-to-end smoke test: boot daemon, hit each endpoint, verify shape.

Skipped on non-Windows: the Phase 1 terminal launcher is Win32-only
(uses wt.exe). macOS path lands in Phase 2.

Skipped if a daemon is already running on the configured port (refuse
to clobber active state).

Uses urllib.request (stdlib) instead of httpx to avoid adding a workspace
dep just for this test."""
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[2]
DAEMON = WORKSPACE / "scripts" / "bridge-daemon.py"


def _wait_port(port: int, timeout: float = 10) -> bool:
    """Poll the port until a TCP connection succeeds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _get(url: str, headers: dict | None = None, timeout: float = 5) -> tuple[int, dict | None]:
    """GET via urllib.request. Returns (status, json_body_or_None)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "boots a REAL daemon in the live workspace and wipes .daemon-state/ "
        "around it; enabling that off Windows is the operator's call, not this "
        "file's"
    ),
)
def test_smoke_boot_and_endpoints():
    # The gate above used to say "Phase 1 daemon launcher is Windows-only
    # (wt.exe)". That is not true and has not been for a long time: `--start`
    # in scripts/bridge-daemon.py is pure Python with no shell and no `wt.exe`
    # anywhere in the file, and the canonical installer for this workspace is
    # the WSL2 systemd-user unit. Checked 2026-08-26.
    #
    # The gate stays anyway, with the real reason written above it, because the
    # cost is not portability. This test boots a live daemon in the workspace
    # root, deletes `.daemon-state/` before it starts and again in `finally`,
    # and the operator deliberately stopped and disabled that daemon. A
    # platform check standing in for a policy decision is what made the reason
    # drift in the first place, so the reason now names the policy.
    """End-to-end smoke. Uses the real workspace .daemon-state/ but cleans
    up artifacts in finally so subsequent runs don't see stale state."""
    state_dir = WORKSPACE / ".daemon-state"
    port_file = state_dir / "port"
    # If an active daemon is running, skip rather than clobber its state.
    #
    # The bar for "safe to wipe" is a REFUSED connection: nothing is listening.
    # Until 2026-08-23 any exception fell through to the wipe, so a daemon that
    # was merely busy -- a 1 s probe timeout is easy to hit -- read as dead and
    # this test deleted its live token, heartbeat, usage.jsonl and
    # config-history. A test that can eat production state is worse than a test
    # that skips.
    if port_file.exists():
        try:
            running_port = int(port_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            pytest.skip(f"unreadable port file ({exc}); refuse to guess whether a daemon is live")
        try:
            status, _ = _get(f"http://127.0.0.1:{running_port}/health", timeout=5)
            pytest.skip(f"something answers on port {running_port} (HTTP {status}); refuse to clobber")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if not isinstance(reason, ConnectionRefusedError):
                pytest.skip(f"port {running_port} did not refuse cleanly ({reason!r}); "
                            f"a busy daemon is not a dead one")
        except OSError as exc:
            if not isinstance(exc, ConnectionRefusedError):
                pytest.skip(f"probe of port {running_port} failed ({exc!r}); refuse to clobber")

    # Pre-clean any leftover state from a prior aborted run. Reached only when
    # the port file is absent, or the port refused the connection outright.
    if state_dir.exists():
        shutil.rmtree(state_dir, ignore_errors=True)

    proc = subprocess.Popen(
        [sys.executable, str(DAEMON), "--start"],
        cwd=str(WORKSPACE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for the daemon to write its port file (5s budget).
        for _ in range(50):
            if port_file.exists():
                break
            time.sleep(0.1)
        assert port_file.exists(), "daemon did not write port file within 5s"

        port = int(port_file.read_text(encoding="utf-8").strip())
        assert _wait_port(port, timeout=10), f"daemon did not bind to port {port} within 10s"

        token = (state_dir / "token").read_text(encoding="utf-8").strip()
        auth = {"Authorization": f"Bearer {token}"}

        # /health unauthenticated
        status, body = _get(f"http://127.0.0.1:{port}/health")
        assert status == 200, f"/health returned {status}"
        assert "pid" in body and "uptime_s" in body

        # /_bootstrap unauthenticated
        status, body = _get(f"http://127.0.0.1:{port}/_bootstrap")
        assert status == 200 and body["token"] == token

        # /version authed
        status, body = _get(f"http://127.0.0.1:{port}/version", headers=auth)
        assert status == 200 and "global" in body and "components" in body

        # /pulse + /inbox authed
        for path in ("/pulse", "/inbox"):
            status, body = _get(f"http://127.0.0.1:{port}{path}", headers=auth)
            assert status == 200, f"{path} returned {status}"
            assert body is not None and "data_time" in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Always remove .daemon-state/ artifacts so the next run sees a clean slate.
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)
