"""Tests for R16 Layer 2 -- rollback-corporate.py.

Pure target validation plus an integration test of the forward-revert + push
(branch-protection-friendly, no force-push) against a bare remote.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))


def _load():
    spec = importlib.util.spec_from_file_location(
        "rollback_corp_mod", WORKSPACE / "scripts" / "rollback-corporate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def R():
    return _load()


# ---- validate_rollback_target (pure) ----

def test_validate_ok(R):
    ok, _ = R.validate_rollback_target(90, 89)
    assert ok


def test_validate_no_previous(R):
    ok, msg = R.validate_rollback_target(90, None)
    assert not ok and "no previous" in msg.lower()


def test_validate_same_build_refused(R):
    ok, msg = R.validate_rollback_target(90, 90)
    assert not ok and "multi-commit" in msg.lower()


# ---- integration: forward revert + push ----

def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=check)


def _cfg(repo):
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit_build(repo, n):
    (repo / "BUILD.json").write_text(json.dumps({"build": n, "version": f"1.{n}.0"}, indent=4) + "\n",
                                     encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"build {n}")


def _build_at_ref(repo, ref):
    out = _git(repo, "show", f"{ref}:BUILD.json").stdout
    return json.loads(out)["build"]


def test_do_rollback_reverts_to_previous_build(R, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
    _cfg(clone)
    _commit_build(clone, 1)
    _git(clone, "branch", "-M", "main")
    _git(clone, "push", "-u", "origin", "main")
    _commit_build(clone, 2)               # single-commit "promote" to build 2
    _git(clone, "push", "origin", "main")

    assert _build_at_ref(clone, "HEAD") == 2
    assert _build_at_ref(clone, "HEAD~1") == 1

    assert R.do_rollback(clone) == 0
    # the forward revert restored build 1 and pushed it
    assert _build_at_ref(clone, "origin/main") == 1
    assert _build_at_ref(clone, "HEAD") == 1
