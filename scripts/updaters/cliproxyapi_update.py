#!/usr/bin/env python3
"""Apply script for the `cliproxyapi` update-manager component.

Codifies the manual 2026-07-20 procedure: fetch latest linux_amd64 tarball +
checksums, verify sha256, back up the current binary, atomic-swap, restart the
systemd-user service, health-check, and roll back on failure. Reads no secrets;
config.yaml (chmod 600, outside the repo) is never touched.

Exit codes:
  0  already current, or the new version is healthy
  1  the swap or the health gate failed and a rollback was attempted
  2  could not resolve, fetch, or stage a release; NOTHING was swapped
  3  checksum missing or mismatched; refused an unverified swap

The old line read "Exit 0 on healthy new version; non-zero on rollback",
which described 2 and 3 -- both of which roll back nothing -- as rollbacks.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils import update_sources  # noqa: E402

CPX_DIR = Path.home() / "cliproxyapi"
BIN = CPX_DIR / "cli-proxy-api"
# The operator's own wrapper, installed outside this repo. Nothing here creates
# it, so a host can run the updater without it -- see `_health_ok` and the
# pre-flight in `main`.
HEALTH_PROBE = Path.home() / ".local" / "bin" / "cliproxy"
REPO = "router-for-me/CLIProxyAPI"
SERVICE = "cliproxyapi.service"
HEALTH_TIMEOUT_S = 30.0
HEALTH_INTERVAL_S = 1.0
# The release tarball is single-digit MB; this is a runaway guard, not a fit.
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _download(url: str, dest: Path) -> None:
    # Unlike update_sources, this URL is not a literal: it arrives as
    # `browser_download_url` inside the GitHub API response, so the SCHEME is
    # remote-controlled data. urlopen honours `file:` and custom schemes, which
    # would turn a hijacked API response into a local-file read staged as the
    # new binary. Checked here rather than suppressed.
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https download URL: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "heading-os-update-manager"})  # noqa: S310 - scheme checked above
    # Capped. `copyfileobj` streamed without limit, and the URL is API-response
    # data -- an endless chunked body fills the disk, and the staging dir is
    # frequently a small tmpfs.
    written = 0
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:  # noqa: S310 - scheme checked above
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"download exceeded {MAX_DOWNLOAD_BYTES} bytes; refusing: {url!r}")
            fh.write(chunk)


def select_binary_member(members) -> tuple:
    """The one regular file named exactly `cli-proxy-api`, or (None, reason).

    `endswith("cli-proxy-api")` also matched `docs/old-cli-proxy-api`, a
    DIRECTORY of that name, and `not-cli-proxy-api` -- and the `next()` around
    it took whichever came first in archive order, so the wrong member could be
    staged as the new binary. That `next()` also sat outside the try/except, so
    an archive with no match died on StopIteration instead of refusing.
    """
    candidates = [m for m in members
                  if m.isfile() and Path(m.name).name == "cli-proxy-api"]
    if not candidates:
        return None, "no `cli-proxy-api` file in the release tarball; refusing the swap"
    if len(candidates) > 1:
        names = ", ".join(m.name for m in candidates)
        return None, (f"{len(candidates)} members named `cli-proxy-api` in the "
                      f"tarball ({names}); refusing an ambiguous swap")
    return candidates[0], ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# `expected HTTP 200, got HTTP 502` CONTAINS "HTTP 200". The old substring gate
# therefore declared a dead service healthy on the canonical failure message,
# turning a bad swap into a reported success and skipping the rollback. Matching
# stderr as well as stdout widened the same hole.
_HEALTH_FAIL_RE = re.compile(r"\b(?:got|actual|received)\b[^\n]*\bHTTP\s+\d{3}",
                             re.IGNORECASE)
_HEALTH_OK_RE = re.compile(r"(?<![\w-])HTTP\s+200\b")


def _health_ok() -> bool:
    # No shell: the path was interpolated unquoted into `bash -c`, so a home
    # directory with a space broke the probe and every update ended in a
    # spurious rollback.
    # Raises FileNotFoundError (an OSError) when the wrapper is absent. Both
    # callers sit AFTER the swap, so that exception must never escape to main's
    # pre-swap handler: `main` pre-flights the probe, and the tail below catches
    # what a mid-run deletion could still raise.
    res = subprocess.run([str(HEALTH_PROBE), "health"],
                         capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return False
    text = res.stdout or ""
    if _HEALTH_FAIL_RE.search(text) or _HEALTH_FAIL_RE.search(res.stderr or ""):
        return False
    return bool(_HEALTH_OK_RE.search(text))


def _wait_healthy(timeout_s: float = HEALTH_TIMEOUT_S,
                  interval_s: float = HEALTH_INTERVAL_S) -> bool:
    """Poll the health gate instead of probing it once.

    `systemctl start` returns when the process is spawned, not when it listens.
    A single immediate probe read that gap as a dead service and rolled a good
    binary back (observed 2026-07-28 on the 7.2.92 -> 7.2.104 bump: the journal
    shows the port bound in the same second the rollback fired). Only a service
    that never answers inside the budget counts as a failure.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if _health_ok():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)


def _normalise_version(raw: str) -> str:
    """Bare dotted digits, from either side of the comparison.

    `_current_version` was grep-normalised to `[0-9.]+` while the GitHub tag was
    used verbatim, so a `v7.2.104` tag never equalled a `7.2.104` binary: every
    scheduled run stopped the service, swapped the SAME version in, restarted,
    and overwrote the backup -- forever, with a rollback roulette each time.
    """
    m = re.search(r"\d[\d.]*", raw or "")
    return m.group(0).rstrip(".") if m else ""


def _version_tuple(v: str) -> tuple:
    """Dotted version as a comparable tuple of ints."""
    return tuple(int(part) for part in v.split(".") if part.isdigit())


def _current_version() -> str:
    # No shell, and no grep -P (GNU-only): the path was interpolated unquoted
    # into `bash -c`, so a home directory with a space broke this too.
    try:
        res = subprocess.run([str(BIN), "-version"],
                             capture_output=True, text=True, check=False)
    except OSError as exc:
        # A fresh machine, a renamed binary, or a broken prior run raises
        # FileNotFoundError here. This call sits ABOVE `main`'s try block, so it
        # died with a traceback and exit 1 - the code this module's docstring
        # defines as "the swap or the health gate failed and a rollback was
        # attempted". Neither happened. The empty string sends `main` down the
        # ordinary unknown-version path, and the `shutil.copy2` guard below,
        # which already documents this exact scenario and returns 2, is finally
        # reachable.
        print(f"could not run {BIN} -version: {exc}")
        return ""
    for line in ((res.stdout or "") + (res.stderr or "")).splitlines():
        m = re.search(r"Version:\s*([0-9][0-9.]*)", line)
        if m:
            return m.group(1)
    return ""


def main() -> int:
    latest = update_sources.latest_version({"via": "github_release", "repo": REPO})
    if not latest:
        print("could not resolve latest version")
        return 2
    current_n = _normalise_version(_current_version())
    latest_n = _normalise_version(latest)
    if current_n and latest_n and current_n == latest_n:
        print(f"already {latest}")
        return 0
    if current_n and latest_n and _version_tuple(latest_n) < _version_tuple(current_n):
        # `==` is not an ordering. A retracted release or an API quirk resolving
        # "latest" to something OLDER made this cheerfully downgrade, restart
        # included.
        print(f"refusing a downgrade: installed {current_n}, 'latest' is {latest_n}")
        return 2

    asset = update_sources.github_asset_url({"repo": REPO}, "amd64")
    if not asset:
        print("no linux_amd64 asset found")
        return 2
    checksums_url = asset.rsplit("/", 1)[0] + "/checksums.txt"

    # Pre-flight the health probe, for the same reason the backup copy is
    # pre-flighted below: the gate that decides whether to keep the new binary
    # must exist BEFORE the old one is replaced. Without this the missing probe
    # surfaced as FileNotFoundError from `_wait_healthy`, was caught by the
    # handler under this block, and printed "update aborted before any swap"
    # with exit 2 -- the code the docstring above defines as "NOTHING was
    # swapped" -- while the swap had in fact completed and no health gate had
    # run. Refusing here is the only exit 2 that tells the truth.
    if not os.access(HEALTH_PROBE, os.X_OK):
        print(f"health probe {HEALTH_PROBE} is missing or not executable; "
              f"refusing to swap a binary whose health cannot be checked")
        return 2

    try:
        return _fetch_verify_and_swap(asset, checksums_url, latest, current_n)
    except (OSError, ValueError, tarfile.TarError, UnicodeDecodeError) as exc:
        # Every step below -- the two downloads, reading checksums.txt, opening
        # the tarball -- could raise straight out as a traceback with exit 1,
        # against the exit contract in this module's docstring. A 404 on the
        # derived checksums URL and an HTML error page served with HTTP 200 are
        # the two that actually happen.
        print(f"update aborted before any swap ({type(exc).__name__}: {exc})")
        return 2


def _fetch_verify_and_swap(asset: str, checksums_url: str, latest: str,
                           current_n: str) -> int:
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
            parts = line.split()
            if len(parts) == 2 and parts[1] == asset_name:
                want = parts[0]
                break
        if not want:
            print("checksum for asset not found; refusing unverified swap")
            return 3
        got = _sha256(tarball)
        if got != want:
            print(f"sha256 mismatch: {got} != {want}")
            return 3

        with tarfile.open(tarball) as tf:
            member, why = select_binary_member(tf.getmembers())
            if member is None:
                print(why)
                return 2
            # filter="data" needs Python >= 3.12 (backported to 3.11.4/3.10.12).
            # The workspace runs modern Python; if targeting older, drop the kwarg.
            tf.extract(member, stage, filter="data")
            newbin = stage / member.name

        backup = CPX_DIR / f"cli-proxy-api.{current_n or 'prev'}.bak"
        try:
            shutil.copy2(BIN, backup)
        except OSError as exc:
            # A fresh machine, a renamed binary, or a broken prior run raised
            # FileNotFoundError here with no handler in sight -- and this is the
            # copy that makes the rollback below possible at all.
            print(f"cannot back up {BIN} ({exc}); refusing to swap without a "
                  f"restore point")
            return 2

        def _restore() -> bool:
            """Put the backup back. False when it could not be done.

            It returns rather than raises because every caller is already
            handling a failure: an exception from here escaped to `main`'s
            pre-swap handler and was printed as "update aborted before any
            swap", which is the opposite of what had happened. The two ways it
            fails -- a read-only filesystem and a deleted backup -- both leave
            the service on whatever binary is in place, so the caller must be
            able to say that out loud.
            """
            subprocess.run(["systemctl", "--user", "stop", SERVICE], check=False)
            try:
                shutil.copy2(backup, BIN)
                BIN.chmod(0o755)
            except OSError as exc:
                print(f"ROLLBACK FAILED ({type(exc).__name__}: {exc}); {BIN} is "
                      f"NOT the known-good binary -- investigate now")
                subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
                return False
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
            return True

        # The stop -> swap -> start window is where the service could be left
        # without a binary. Any failure here restores the backup, not just a
        # health-gate failure.
        try:
            subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)
            shutil.copymode(BIN, newbin)
            # Stage BESIDE the target, then os.replace. `shutil.move` from the
            # tempdir degrades to copy-and-delete across filesystems (TMPDIR is
            # commonly tmpfs), so the "atomic-swap" this file's docstring
            # promises was not one: a kill mid-copy left a PARTIAL binary with
            # the service stopped, and no exception for `_restore` to catch.
            side = BIN.with_name(BIN.name + ".incoming")
            shutil.copy2(newbin, side)
            side.chmod(0o755)
            os.replace(side, BIN)
            subprocess.run(["systemctl", "--user", "start", SERVICE], check=True)
        except Exception as exc:  # noqa: BLE001 - any swap failure must restore
            print(f"swap failed ({type(exc).__name__}: {exc}); restoring backup")
            _restore()
            return 1

        # Everything from here runs AFTER the binary was replaced, so no
        # exception may escape to the pre-swap handler in `main`, which would
        # print "update aborted before any swap" with exit 2 over a completed
        # swap. `_restore` now reports its own failure instead of raising; this
        # catch is for the health probe, which can be deleted between the
        # pre-flight and this line.
        try:
            if _wait_healthy():
                print(f"cliproxyapi updated -> {latest}")
                return 0

            if not _restore():
                return 1
            if _wait_healthy():
                print("health failed; rolled back to the previous binary, "
                      "which is now healthy")
            else:
                # The rollback runs check=False throughout, so "rolled back" was
                # printed whether or not the old binary came back up. If the cause
                # was environmental -- a port conflict, a bad config -- the service
                # is still down and the log used to say otherwise.
                print("health failed; rolled back to the previous binary AND IT IS "
                      "STILL NOT HEALTHY -- the service is down, investigate now")
        except OSError as exc:
            print(f"the health gate or the rollback failed after the swap "
                  f"({type(exc).__name__}: {exc}) -- {BIN} was ALREADY replaced "
                  f"and its state is now unknown; investigate now")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
