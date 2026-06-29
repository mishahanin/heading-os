#!/usr/bin/env python3
"""Guard: the destructive workspace-sync engine cannot return.

Regression wall for plans/2026-06-26-retire-workspace-sync-disk-import.md. The
old `scripts/workspace-sync.py` orphan-delete wiped the engine tree on clean
exec deploys. These tests assert the script is gone, its destructive surface
(`_delete_orphans`) and the install side of its scheduler (`install_sync_schedule`,
`run_dry_run_validation`) appear nowhere under `scripts/`, while the teardown
helper `uninstall_sync_schedule` survives so offboarding can still remove legacy
timers. They also import-smoke the two provisioning entrypoints so a dangling
`from scripts.utils.schedule import install_sync_schedule` cannot slip back in.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))


def _iter_py_files():
    return [p for p in SCRIPTS.rglob("*.py")]


def test_workspace_sync_script_is_gone():
    assert not (SCRIPTS / "workspace-sync.py").exists(), \
        "scripts/workspace-sync.py must not exist after retirement"


def test_check_exec_orphans_script_is_gone():
    assert not (SCRIPTS / "check-exec-orphans.py").exists(), \
        "scripts/check-exec-orphans.py (orphan-model audit) must be removed"


def test_delete_orphans_symbol_absent():
    """The orphan-delete surface must not exist anywhere under scripts/."""
    offenders = [p for p in _iter_py_files()
                 if "_delete_orphans" in p.read_text(encoding="utf-8")]
    assert not offenders, f"_delete_orphans resurfaced in: {offenders}"


def test_install_sync_schedule_symbol_absent():
    """The install side of the retired sync scheduler must be gone.

    Word-boundary match so `uninstall_sync_schedule` (kept for teardown) does
    NOT count as a hit.
    """
    install_re = re.compile(r"(?<!un)install_sync_schedule")
    dryrun_re = re.compile(r"run_dry_run_validation")
    offenders = []
    for p in _iter_py_files():
        text = p.read_text(encoding="utf-8")
        if install_re.search(text) or dryrun_re.search(text):
            offenders.append(p)
    assert not offenders, \
        f"retired sync-install symbols resurfaced in: {offenders}"


def test_uninstall_sync_schedule_survives():
    """Teardown of a legacy 31c-sync-* timer must still be possible."""
    from scripts.utils.schedule import uninstall_sync_schedule  # noqa: F401


def _load_by_path(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_provisioning_entrypoints_import_clean():
    """setup.py and provision-exec.py must import with no dangling reference to
    the removed install_sync_schedule / run_dry_run_validation (py_compile would
    not catch a bad `from ... import` target; an actual import does)."""
    _load_by_path("setup_smoke", "scripts/setup.py")
    _load_by_path("provision_exec_smoke", "scripts/provision-exec.py")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
