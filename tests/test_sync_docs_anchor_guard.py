"""sync-docs hook refuses to propagate a template that lost a required anchor.

Regression guard for the recurring failure where an edit silently drops a
load-bearing section (e.g. the uv-dependency docs) from GETTING-STARTED and the
sync faithfully propagates the deletion into the distributed docs/ copy.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "sync-docs.py"

GOOD_TEMPLATE = """# Getting started

## Windows setup
- Install Python dependencies via `uv sync --all-groups`

> Dependencies are managed by uv. See `docs/security/DEPENDENCY-POLICY.md`.
"""

BAD_TEMPLATE = """# Getting started

## Windows setup
- self-contained (no pip install needed)
"""


def _run(project_dir: Path):
    payload = {
        "cwd": str(project_dir),
        "tool_input": {"file_path": str(project_dir / "templates" / "GETTING-STARTED.md")},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc


def _setup(tmp_path: Path, template_body: str) -> Path:
    (tmp_path / "templates").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "templates" / "GETTING-STARTED.md").write_text(template_body, encoding="utf-8")
    # docs/ already holds the known-good copy
    (tmp_path / "docs" / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    return tmp_path


def test_good_template_syncs(tmp_path):
    _setup(tmp_path, GOOD_TEMPLATE)
    proc = _run(tmp_path)
    assert proc.returncode == 0
    synced = (tmp_path / "docs" / "GETTING-STARTED.md").read_text()
    assert "uv sync" in synced  # propagated
    out = json.loads(proc.stdout)
    assert "Auto-synced" in out["additionalContext"]


def test_docs_target_follows_template_root_not_cwd(tmp_path):
    """The docs/ copy must land beside the TEMPLATE (data overlay), even when the
    edit is made from a different cwd (the engine clone). Regression for the
    silent push failure: a cwd-relative docs/ wrote the CEO-only guide into the
    engine tree, which the push leak-wall refused.
    """
    # template root (e.g. the data overlay) — has templates/ + docs/
    troot = tmp_path / "data-overlay"
    (troot / "templates").mkdir(parents=True)
    (troot / "docs").mkdir()
    (troot / "templates" / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    (troot / "docs" / "GETTING-STARTED.md").write_text(GOOD_TEMPLATE, encoding="utf-8")
    # a DIFFERENT cwd (e.g. the engine clone) — must NOT receive the docs copy
    cwd_root = tmp_path / "engine"
    (cwd_root / "docs").mkdir(parents=True)

    payload = {
        "cwd": str(cwd_root),
        "tool_input": {"file_path": str(troot / "templates" / "GETTING-STARTED.md")},
    }
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    # synced beside the template…
    assert "uv sync" in (troot / "docs" / "GETTING-STARTED.md").read_text()
    # …and the engine cwd's docs/ was left untouched (no leak into the engine tree)
    assert not (cwd_root / "docs" / "GETTING-STARTED.md").exists()


def test_template_missing_anchor_is_not_propagated(tmp_path):
    _setup(tmp_path, BAD_TEMPLATE)
    proc = _run(tmp_path)
    assert proc.returncode == 0
    # docs/ must STILL hold the good copy — the deletion was NOT propagated
    preserved = (tmp_path / "docs" / "GETTING-STARTED.md").read_text()
    assert "uv sync" in preserved
    assert "self-contained" not in preserved
    # and the hook shouted about the missing anchor
    out = json.loads(proc.stdout)
    ctx = out["additionalContext"].lower()
    assert "blocked" in ctx or "missing" in ctx
    assert "uv sync" in out["additionalContext"]
