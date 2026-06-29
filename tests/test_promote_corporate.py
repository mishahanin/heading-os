"""Tests for R16 Layer 2 -- promote-corporate.py.

Pure gate logic (soak / freshness / smoke / eval-warning / canary-slug resolution)
plus an integration test of the --ff-only merge + push against a bare remote.
The kebab-case script is loaded via importlib (same pattern as
test_memory_index_ranking.py).
"""

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))


def _load():
    spec = importlib.util.spec_from_file_location(
        "promote_corp_mod", WORKSPACE / "scripts" / "promote-corporate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def P():
    return _load()


NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def _status(last_pull, smoke="healthy", eval_status="healthy"):
    return {"last_pull_at": last_pull.isoformat(), "smoke_status": smoke, "eval_status": eval_status}


# ---- resolve_canary_slug ----

def test_resolve_canary_slug_found(P):
    registry = {"executives": [
        {"slug": "a", "canary": False},
        {"slug": "alex-rivera", "canary": True},
    ]}
    assert P.resolve_canary_slug(registry) == "alex-rivera"


def test_resolve_canary_slug_none(P):
    assert P.resolve_canary_slug({"executives": [{"slug": "a"}]}) is None


# ---- evaluate_gates ----

def test_gate_blocks_soak_incomplete(P):
    commit = NOW - timedelta(hours=2)
    g = P.evaluate_gates(NOW, commit, _status(commit))
    assert g["blocked"] and "soak-incomplete" in g["reasons"]
    assert g["canary_fresh"] and g["smoke_ok"]


def test_gate_blocks_canary_stale(P):
    commit = NOW - timedelta(hours=6)
    pulled_before = commit - timedelta(hours=1)
    g = P.evaluate_gates(NOW, commit, _status(pulled_before))
    assert g["blocked"] and "canary-stale" in g["reasons"]
    assert g["soak_ok"]


def test_gate_blocks_smoke_failed(P):
    commit = NOW - timedelta(hours=6)
    g = P.evaluate_gates(NOW, commit, _status(NOW, smoke="canary-blocked"))
    assert g["blocked"] and "smoke-blocked" in g["reasons"]


def test_gate_all_pass(P):
    commit = NOW - timedelta(hours=6)
    g = P.evaluate_gates(NOW, commit, _status(NOW))
    assert not g["blocked"] and g["reasons"] == []


def test_gate_eval_regression_is_warning_only(P):
    commit = NOW - timedelta(hours=6)
    g = P.evaluate_gates(NOW, commit, _status(NOW, eval_status="canary-eval-regression"))
    assert not g["blocked"]
    assert g["eval_status"] == "canary-eval-regression"


# ---- integration: --ff-only merge + push ----

def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=check)


def _cfg(repo):
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _init_clone(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
    _cfg(clone)
    (clone / "f.txt").write_text("A")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "A")
    _git(clone, "branch", "-M", "main")
    _git(clone, "push", "-u", "origin", "main")
    return remote, clone


def test_do_promote_ff_only_success(P, tmp_path):
    remote, clone = _init_clone(tmp_path)
    # staging is a clean fast-forward of main (A -> B)
    _git(clone, "checkout", "-b", "staging")
    (clone / "f.txt").write_text("B")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "B")
    _git(clone, "push", "-u", "origin", "staging")
    _git(clone, "checkout", "main")

    assert P.do_promote(clone) == 0
    main_sha = _git(clone, "rev-parse", "origin/main").stdout.strip()
    stg_sha = _git(clone, "rev-parse", "origin/staging").stdout.strip()
    assert main_sha == stg_sha  # main fast-forwarded to the staging tip


def test_do_promote_non_ff_rejected(P, tmp_path):
    remote, clone = _init_clone(tmp_path)
    # staging branches from A with commit B
    _git(clone, "checkout", "-b", "staging")
    (clone / "s.txt").write_text("B")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "B")
    _git(clone, "push", "-u", "origin", "staging")
    # main diverges with commit C -> not a fast-forward
    _git(clone, "checkout", "main")
    (clone / "m.txt").write_text("C")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "C")
    _git(clone, "push", "origin", "main")

    assert P.do_promote(clone) == 11  # ff-only merge refused
