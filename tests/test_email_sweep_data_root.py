"""Regression: email-sweep.py must root its state file on the DATA overlay.

Bug (pre-public audit, 2026-06-23): email-sweep.py rooted STATE_DIR on
get_workspace_root() (the engine clone) instead of get_data_root() (the private
data repo). A `propose` run therefore wrote
outputs/operations/email-intelligence/sweep-actions-*.json INTO the engine tree
(found as a stray gitignored artifact during the pre-public cleanliness audit).
This pins the state file landing under the data root, never the engine root.
"""
import importlib.util
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT = WORKSPACE / "scripts" / "email-sweep.py"


def _load_module():
    """Import scripts/email-sweep.py (hyphenated, not importable by name)."""
    spec = importlib.util.spec_from_file_location("email_sweep_cli", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_propose_writes_under_data_root_not_engine(tmp_path, monkeypatch):
    # HEADING_OS_DATA is the first-priority data-root override (scripts/utils/paths.py).
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(data_root))

    payload = tmp_path / "proposed.json"
    payload.write_text(json.dumps([{"type": "task", "title": "regression check"}]), encoding="utf-8")

    mod = _load_module()
    monkeypatch.setattr(sys, "argv", ["email-sweep.py", "propose", "--file", str(payload), "--date", "2026-01-01"])
    rc = mod.main()
    assert rc == 0

    expected = data_root / "outputs" / "operations" / "email-intelligence" / "sweep-actions-2026-01-01.json"
    assert expected.exists(), f"state file should land under the data root at {expected}"

    # And must NOT have leaked into the engine clone.
    engine_stray = WORKSPACE / "outputs" / "operations" / "email-intelligence" / "sweep-actions-2026-01-01.json"
    assert not engine_stray.exists(), "state file must never be written into the engine tree"
