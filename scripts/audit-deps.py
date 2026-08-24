#!/usr/bin/env python3
"""Audit the FULL resolved dependency graph for known CVEs.

A bare `pip-audit --requirement requirements.txt` audits only the runtime
export (`uv export --no-dev`). That export omits dev dependencies and their
transitive deps, so a CVE in a dev-only tool slips straight past it. That gap
is exactly how GHSA-6v7p-g79w-8964 (msgpack, a transitive dep of the pip-audit
dev tool itself, pulled via cachecontrol) went unflagged.

This entrypoint closes the gap: it audits the COMPLETE locked dependency set --
dev and transitive included -- by exporting the full `uv.lock`.

Where it actually runs: the `dependency-audit` GitHub workflow, and a hand
invocation. NOT pre-commit. This paragraph read "the single auditing primitive
shared by the pre-commit hook and the scheduled CI workflow" until 2026-08-25,
and the commit-time gate is a different thing entirely -- the inline
`pip-audit-cve` hook in `.pre-commit-config.yaml`, which audits
`requirements.txt` alone. `requirements.txt` is the `--no-dev` export, so the
commit gate has precisely the dev-and-transitive blind spot described above.
That is a real gap; the sentence claiming otherwise was the reason nobody was
looking at it.

Dependency set, in resolution order:
  1. `uv export --no-hashes --all-extras --no-emit-project --format requirements-txt`
     (full graph: dev + transitive + every optional extra). Both extra flags are
     load-bearing, and this line named neither of them until 2026-08-24: without
     `--all-extras` the export drops every optional package, which is the blind
     spot this script exists to close; without `--no-emit-project` the export
     carries `-e .` and pip-audit refuses the file.
  2. fallback: the live virtualenv (`pip-audit` over installed packages) when
     `uv` is not on PATH AT ALL. A `uv` that is present and whose export FAILS
     is a different fact and gets a different answer -- exit 2, see below.

Exit codes:
  0  clean, OR tooling absent (graceful skip so a commit is never blocked on a
     machine that has not `pip install -r requirements-dev.txt`)
  1  one or more known vulnerabilities found
  2  the intended scope could not be assembled (`uv` is installed and `uv
     export` failed: a corrupt `uv.lock`, a `uv` too old for one of the flags).
     Until 2026-08-25 this shared the fallback with case 2 above, so a broken
     lockfile silently downgraded the audit to "whatever is installed in the
     active environment" -- which on a CI runner or a bare interpreter is a
     fraction of the locked graph -- and the run then printed `uv unavailable`,
     sending anyone reading the log to look for a missing binary that was
     there. A security gate reporting clean over a scope it never assembled is
     worse than one that fails, so this refuses instead.

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
    # Both layouts. Windows venvs put the interpreter at Scripts/python.exe, so
    # checking only bin/python meant this function returned silently there and
    # the CVE gate took the graceful-skip path on every Windows machine -- the
    # exact silent skip the docstring above says it exists to eliminate, across
    # a whole platform.
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    venv_py = next((c for c in candidates if c.exists()), None)
    if venv_py is not None and Path(sys.executable).resolve() != venv_py.resolve():
        os.environ["_AUDIT_DEPS_REEXEC"] = "1"
        # Safe: venv_py is a workspace-local path, sys.argv[1:] is from the same process.
        # No shell, no user input, all arguments are trusted paths.
        os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])  # noqa: S606


EXPORT_OK = "ok"
EXPORT_NO_UV = "no-uv"
EXPORT_FAILED = "failed"

# 0 and 1 come straight from pip-audit's own exit code and are never written
# here, so only the code this script originates gets a name.
EXIT_SCOPE_UNAVAILABLE = 2


def _export_full_requirements(dest: Path) -> str:
    """Export the complete locked dependency graph (incl dev + all extras) to ``dest``.

    Returns EXPORT_OK, EXPORT_NO_UV, or EXPORT_FAILED.

    Three outcomes, not two. This returned a bool, and the caller read False as
    "uv is unavailable" -- so an export that failed WITH uv installed took the
    virtualenv fallback and was reported as the missing-binary case. Absent
    tooling deserves a graceful skip; a lockfile that will not export does not,
    because the scope actually audited is then whatever happens to be installed.
    """
    if shutil.which("uv") is None:
        return EXPORT_NO_UV
    proc = subprocess.run(
        # --all-extras: F-7.1 moved heavy deps into optional-dependencies; without
        # this flag the export (and thus the CVE audit) would silently drop every
        # optional package. Keep the full graph in scope.
        # --no-emit-project: the engine is an installed package (F-10.1 item 4), so
        # a bare export emits `-e .`, which pip-audit rejects. Audit the deps, not
        # the local editable project.
        ["uv", "export", "--no-hashes", "--all-extras", "--no-emit-project",
         "--format", "requirements-txt"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return EXPORT_FAILED
    dest.write_text(proc.stdout, encoding="utf-8")
    return EXPORT_OK


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
        state = _export_full_requirements(reqs)
        if state == EXPORT_FAILED:
            print(
                "audit-deps: `uv` is installed and `uv export` failed, so the "
                "full locked graph could not be assembled (see the uv error "
                "above). Refusing to audit the active virtualenv instead and "
                "report the result as this script's scope.",
                file=sys.stderr,
            )
            return EXIT_SCOPE_UNAVAILABLE
        if state == EXPORT_OK:
            cmd += ["--requirement", str(reqs)]
            scope = "full locked graph incl extras (uv export --all-extras -- dev + transitive)"
        else:
            scope = "active virtualenv (uv not on PATH -- fallback)"
        # stderr, always. With --json this line used to land on stdout directly
        # in front of pip-audit's JSON document, so the mode the docstring calls
        # "machine-readable" emitted something no parser accepts.
        print(f"pip-audit scope: {scope}", file=sys.stderr)
        return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
