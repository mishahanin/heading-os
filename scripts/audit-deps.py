#!/usr/bin/env python3
"""Audit the FULL resolved dependency graph for known CVEs.

A bare `pip-audit --requirement requirements.txt` audits only the runtime
export (`uv export --no-dev`). That export omits dev dependencies and their
transitive deps, so a CVE in a dev-only tool slips straight past it. That gap
is exactly how GHSA-6v7p-g79w-8964 (msgpack, a transitive dep of the pip-audit
dev tool itself, pulled via cachecontrol) went unflagged.

This entrypoint closes the gap: it audits the COMPLETE locked dependency set --
dev and transitive included -- by exporting the full `uv.lock`. It is the single
auditing primitive shared by the pre-commit hook and the scheduled CI workflow.

Dependency set, in resolution order:
  1. `uv export --no-hashes --format requirements-txt`  (full graph, incl dev)
  2. fallback: the live virtualenv (`pip-audit` over installed packages) when
     `uv` is not on PATH.

Exit codes:
  0  clean, OR tooling absent (graceful skip so a commit is never blocked on a
     machine that has not `pip install -r requirements-dev.txt`)
  1  one or more known vulnerabilities found

Usage:
    python scripts/audit-deps.py            # strict audit, human-readable
    python scripts/audit-deps.py --json     # machine-readable pip-audit JSON
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _reexec_in_venv_if_needed() -> None:
    """If the current interpreter lacks pip_audit but the project ``.venv`` has
    it, re-exec there once.

    The pre-commit framework runs ``language: system`` hooks under whatever
    ``python3`` is on PATH -- typically the bare system interpreter without the
    dev dependencies. Without this, the commit-time gate would silently skip on
    exactly the machines where it matters. Guarded by an env flag so the re-exec
    can happen at most once; if the venv interpreter still lacks pip_audit, the
    normal graceful-skip path in main() takes over.
    """
    if _have("pip_audit") or os.environ.get("_AUDIT_DEPS_REEXEC"):
        return
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        os.environ["_AUDIT_DEPS_REEXEC"] = "1"
        # Safe: venv_py is a workspace-local path, sys.argv[1:] is from the same process.
        # No shell, no user input, all arguments are trusted paths.
        os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])  # noqa: S606


def _export_full_requirements(dest: Path) -> bool:
    """Export the complete locked dependency graph (incl dev) to ``dest``.

    Returns True on success, False if ``uv`` is unavailable or the export fails.
    """
    if shutil.which("uv") is None:
        return False
    proc = subprocess.run(
        ["uv", "export", "--no-hashes", "--format", "requirements-txt"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return False
    dest.write_text(proc.stdout, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audit the full locked dependency graph for known CVEs."
    )
    ap.add_argument("--json", action="store_true", help="emit pip-audit JSON")
    args = ap.parse_args()

    _reexec_in_venv_if_needed()

    if not _have("pip_audit"):
        print(
            "pip-audit not installed -- skipping CVE audit "
            "(pip install -r requirements-dev.txt to enable)."
        )
        return 0  # graceful skip; mirrors the pre-commit hook's degrade contract

    cmd = [sys.executable, "-m", "pip_audit", "--strict"]
    if args.json:
        cmd += ["--format", "json"]

    with tempfile.TemporaryDirectory() as td:
        reqs = Path(td) / "full-requirements.txt"
        if _export_full_requirements(reqs):
            cmd += ["--requirement", str(reqs)]
            scope = "full locked graph (uv export -- dev + transitive)"
        else:
            scope = "active virtualenv (uv unavailable -- fallback)"
        print(f"pip-audit scope: {scope}")
        return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
