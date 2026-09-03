"""Integration tests for aggregate-crm.py per-exec aggregation (new HEADING OS model).

Hard-cut to the two-part topology: the fleet registry is admin/executives.json
(under the DATA root), each exec's CRM lives in a full data overlay
heading-os-data-{slug}/crm/contacts/, and the CEO clones each exec's data repo as
a sibling of the workspace. The legacy 31c-crm-{slug} model is retired.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _write_contact(path: Path, name: str, company: str, last_touch: str = "2026-04-20"):
    path.write_text(f"""---
name: {name}
company: {company}
type: prospect
last_touch: {last_touch}
---

# {name}
""", encoding="utf-8")


# Both cases drive `scripts/aggregate-crm.py` as a CHILD process, and its
# `main()` calls `require_main_clone(__file__)` and exits 2 from a worktree
# before any aggregation happens. A child process is out of reach of an
# in-process monkeypatch, and `clone_guard.py` refuses an environment escape
# hatch by design, so there is no honest way to run these from a YARD.

def test_aggregate_reads_new_data_repo_model(main_clone_only, tmp_path):
    """aggregate-crm.py reads admin/executives.json and pulls heading-os-data-{slug}/crm/contacts/."""
    workspace = tmp_path / "main-workspace"
    workspace.mkdir()
    (workspace / "crm" / "contacts").mkdir(parents=True)
    _write_contact(workspace / "crm" / "contacts" / "ceo-contact.md", "Alice", "AcmeCo")

    # New model: exec data overlay with crm/contacts/, named .heading-os-data-{slug}
    # (dotted — matches provision_exec.py + the data-root seam; one clone per exec).
    exec_repo = tmp_path / ".heading-os-data-test-exec"
    (exec_repo / "crm" / "contacts").mkdir(parents=True)
    _write_contact(exec_repo / "crm" / "contacts" / "exec-contact.md", "Bob", "BetaCo")

    # New model: fleet registry under admin/, not config/exec-registry.json.
    admin_dir = workspace / "admin"
    admin_dir.mkdir()
    (admin_dir / "executives.json").write_text(json.dumps({
        "version": 1,
        "executives": [
            {"slug": "test-exec", "role": "exec", "status": "active",
             "github_user": "test-exec", "data_repo": "heading-os-data-test-exec"},
        ]
    }), encoding="utf-8")

    config_dir = workspace / "config"
    config_dir.mkdir()
    (config_dir / "admin.json").write_text(json.dumps({
        "admin_slugs": ["misha-hanin"],
        "github_org": "mishahanin",
    }), encoding="utf-8")
    (workspace / ".workspace-identity.json").write_text(json.dumps({
        "role": "admin", "slug": "misha-hanin", "type": "ceo-master",
    }), encoding="utf-8")
    (workspace / "crm" / "config.md").write_text("# CRM Configuration\n", encoding="utf-8")

    real_workspace = Path(__file__).resolve().parent.parent.parent
    script = real_workspace / "scripts" / "aggregate-crm.py"

    # Point the data-root seam at the tmp workspace: WORKSPACE_ROOT makes
    # get_workspace_root()/identity resolve there, and HEADING_OS_DATA makes
    # get_data_root() (registry, CEO contacts, admin config) resolve there.
    env = dict(os.environ, WORKSPACE_ROOT=str(workspace), HEADING_OS_DATA=str(workspace))
    proc = subprocess.run(
        [sys.executable, str(script), "--skip-clone", "--workspace-root", str(workspace)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"

    aggregated = workspace / "crm" / "aggregated"
    assert aggregated.exists(), "aggregated/ dir not created"
    assert (aggregated / "company-radar.md").exists()
    assert (aggregated / "by-company.md").exists()
    assert (aggregated / "ownership-map.md").exists()
    assert (aggregated / "shared-contacts.md").exists()

    radar = (aggregated / "company-radar.md").read_text()
    assert "Alice" in radar, "CEO own contact missing"
    assert "Bob" in radar, "exec contact (new data-repo model) missing"


def test_aggregate_ceo_only_flag_skips_exec_clones(main_clone_only, tmp_path):
    """--ceo-only aggregates only CEO own contacts, and the exec is REALLY there.

    This registered an EMPTY fleet and asserted only that the CEO's own contact
    appeared. With no executives in the roster and no exec overlay on disk, both
    halves of the flag - `exec_slugs = []` in `main` and the early return in
    `scan_all_contacts` - changed nothing that could be observed, so a
    `--ceo-only` that was silently ignored passed. MEASURED 2026-09-01: turning
    both halves into no-ops left all 361 tests that name aggregate-crm green.

    So the fleet here is the same shape as the test above: one active exec, one
    overlay, one contact in it. The flag now has something to suppress, and its
    other direction is the first test in this file, where the identical fixture
    WITHOUT `--ceo-only` reports Bob.
    """
    workspace = tmp_path / "main-workspace"
    workspace.mkdir()
    (workspace / "crm" / "contacts").mkdir(parents=True)
    _write_contact(workspace / "crm" / "contacts" / "ceo-only.md", "Carol", "GammaCo")

    exec_repo = tmp_path / ".heading-os-data-test-exec"
    (exec_repo / "crm" / "contacts").mkdir(parents=True)
    _write_contact(exec_repo / "crm" / "contacts" / "exec-contact.md", "Bob", "BetaCo")

    admin_dir = workspace / "admin"
    admin_dir.mkdir()
    (admin_dir / "executives.json").write_text(json.dumps({
        "version": 1,
        "executives": [
            {"slug": "test-exec", "role": "exec", "status": "active",
             "github_user": "test-exec", "data_repo": "heading-os-data-test-exec"},
        ],
    }), encoding="utf-8")

    config_dir = workspace / "config"
    config_dir.mkdir()
    (config_dir / "admin.json").write_text(json.dumps({"admin_slugs": ["misha-hanin"]}),
                                            encoding="utf-8")
    (workspace / ".workspace-identity.json").write_text(json.dumps({
        "role": "admin", "slug": "misha-hanin", "type": "ceo-master",
    }), encoding="utf-8")
    (workspace / "crm" / "config.md").write_text("# CRM\n", encoding="utf-8")

    real_workspace = Path(__file__).resolve().parent.parent.parent
    script = real_workspace / "scripts" / "aggregate-crm.py"

    env = dict(os.environ, WORKSPACE_ROOT=str(workspace), HEADING_OS_DATA=str(workspace))
    proc = subprocess.run(
        [sys.executable, str(script), "--ceo-only", "--skip-clone",
         "--workspace-root", str(workspace)],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"

    radar = (workspace / "crm" / "aggregated" / "company-radar.md").read_text()
    assert "Carol" in radar, "CEO own contact missing"
    assert "Bob" not in radar, (
        "--ceo-only pulled the exec overlay anyway; the flag exists to keep "
        "the aggregate off every other repository on the machine")
    ownership = (workspace / "crm" / "aggregated" / "ownership-map.md").read_text()
    assert "test-exec" not in ownership, ownership


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
