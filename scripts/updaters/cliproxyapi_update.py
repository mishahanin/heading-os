#!/usr/bin/env python3
"""Apply script for the `cliproxyapi` update-manager component.

Codifies the manual 2026-07-20 procedure: fetch latest linux_amd64 tarball +
checksums, verify sha256, back up the current binary, atomic-swap, restart the
systemd-user service, health-check, and roll back on failure. Reads no secrets;
config.yaml (chmod 600, outside the repo) is never touched.

Exit 0 on healthy new version; non-zero on rollback.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils import update_sources  # noqa: E402

CPX_DIR = Path.home() / "cliproxyapi"
BIN = CPX_DIR / "cli-proxy-api"
REPO = "router-for-me/CLIProxyAPI"
SERVICE = "cliproxyapi.service"


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "heading-os-update-manager"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:  # noqa: S310
        shutil.copyfileobj(resp, fh)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _current_version() -> str:
    res = subprocess.run(["bash", "-c",
                          f"{BIN} -version 2>&1 | grep -oP 'Version: \\K[0-9.]+' | head -1"],
                         capture_output=True, text=True, check=False)
    return res.stdout.strip()


def main() -> int:
    latest = update_sources.latest_version({"via": "github_release", "repo": REPO})
    if not latest:
        print("could not resolve latest version")
        return 2
    if _current_version() == latest:
        print(f"already {latest}")
        return 0

    asset = update_sources.github_asset_url({"repo": REPO}, "amd64")
    if not asset:
        print("no linux_amd64 asset found")
        return 2
    checksums_url = asset.rsplit("/", 1)[0] + "/checksums.txt"

    with tempfile.TemporaryDirectory(prefix="cpx-upd.") as td:
        stage = Path(td)
        tarball = stage / "cpx.tar.gz"
        checksums = stage / "checksums.txt"
        _download(asset, tarball)
        _download(checksums_url, checksums)

        # checksums.txt lines are `<sha256>  <asset_name>` (verified 2026-07-20).
        # A format change makes `want` None -> refuse (availability, not integrity).
        want = None
        asset_name = asset.rsplit("/", 1)[1]
        for line in checksums.read_text().splitlines():
            if line.strip().endswith(asset_name):
                want = line.split()[0]
                break
        if not want:
            print("checksum for asset not found; refusing unverified swap")
            return 3
        got = _sha256(tarball)
        if got != want:
            print(f"sha256 mismatch: {got} != {want}")
            return 3

        with tarfile.open(tarball) as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("cli-proxy-api"))
            # filter="data" needs Python >= 3.12 (backported to 3.11.4/3.10.12).
            # The workspace runs modern Python; if targeting older, drop the kwarg.
            tf.extract(member, stage, filter="data")
            newbin = stage / member.name

        backup = CPX_DIR / f"cli-proxy-api.{_current_version() or 'prev'}.bak"
        shutil.copy2(BIN, backup)

        def _restore() -> None:
            subprocess.run(["systemctl", "--user", "stop", SERVICE], check=False)
            shutil.copy2(backup, BIN)
            BIN.chmod(0o755)
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)

        # The stop -> swap -> start window is where the service could be left
        # without a binary. Any failure here restores the backup, not just a
        # health-gate failure.
        try:
            subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)
            shutil.copymode(BIN, newbin)
            shutil.move(str(newbin), str(BIN))
            BIN.chmod(0o755)
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=True)
        except Exception as exc:  # noqa: BLE001 - any swap failure must restore
            print(f"swap failed ({type(exc).__name__}: {exc}); restoring backup")
            _restore()
            return 1

        health = subprocess.run(["bash", "-c", f"{Path.home()}/.local/bin/cliproxy health"],
                               capture_output=True, text=True, check=False)
        if "HTTP 200" in (health.stdout + health.stderr):
            print(f"cliproxyapi updated -> {latest}")
            return 0

        _restore()
        print("health failed; rolled back to previous binary")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
