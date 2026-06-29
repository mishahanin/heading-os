#!/usr/bin/env python3
"""ensure_on_staging() switches a canary corporate clone onto `staging`.

Closes the canary branch-switch gap left by retiring `workspace-sync.py --branch`
(plans/2026-06-26-retire-workspace-sync-disk-import.md). The function is the
git-native replacement: fetch + plain checkout, best-effort, never `-B`.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_canary():
    spec = importlib.util.spec_from_file_location(
        "canary_smoke", ROOT / "scripts" / "canary-smoke.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def canary_clone(tmp_path):
    """A bare origin with main + staging, cloned (lands on main)."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "seed"
    work.mkdir()
    _git(["init", "-b", "main"], work)
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "T"], work)
    (work / "BUILD.json").write_text('{"build": 1}\n', encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "main seed"], work)
    _git(["checkout", "-b", "staging"], work)
    (work / "BUILD.json").write_text('{"build": 2}\n', encoding="utf-8")
    _git(["commit", "-am", "staging build"], work)
    _git(["checkout", "main"], work)
    _git(["init", "--bare", "-b", "main", str(origin)], tmp_path)
    _git(["remote", "add", "origin", str(origin)], work)
    _git(["push", "origin", "main", "staging"], work)

    clone = tmp_path / "corp-clone"
    _git(["clone", str(origin), str(clone)], tmp_path)
    return clone


def test_switches_main_clone_to_staging(canary_clone):
    mod = _load_canary()
    assert mod.current_branch(canary_clone) == "main"

    ok, msg = mod.ensure_on_staging(canary_clone)
    assert ok, msg
    assert mod.current_branch(canary_clone) == "staging"


def test_idempotent_when_already_on_staging(canary_clone):
    mod = _load_canary()
    mod.ensure_on_staging(canary_clone)  # first switch
    ok, msg = mod.ensure_on_staging(canary_clone)
    assert ok
    assert "already on staging" in msg
    assert mod.current_branch(canary_clone) == "staging"
