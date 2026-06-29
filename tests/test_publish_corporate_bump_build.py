"""Tests for R16 H1 -- publish-corporate.py --bump-build BUILD.json increment.

The bump is additive (a new mode; default --preview/--copy/--verify unchanged).
Loads the kebab-case script via importlib and points its CORPORATE_ROOT at a
temp dir so the real corporate repo is never touched.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))


def _load():
    spec = importlib.util.spec_from_file_location(
        "publish_corp_mod", WORKSPACE / "scripts" / "publish-corporate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def M(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "CORPORATE_ROOT", tmp_path)
    return mod


def _build(mod):
    return json.loads((mod.CORPORATE_ROOT / "BUILD.json").read_text(encoding="utf-8"))


def test_patch_bump(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.56.0", "build": 89}), encoding="utf-8")
    assert M.bump_build(summary="content tweak") == 0
    b = _build(M)
    assert b["build"] == 90
    assert b["version"] == "1.56.1"          # PATCH
    assert b["summary"] == "content tweak"
    assert b["publisher"] == "misha-hanin"
    assert "timestamp" in b


def test_structural_bump_is_minor(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.56.0", "build": 89}), encoding="utf-8")
    assert M.bump_build(structural=True) == 0
    b = _build(M)
    assert b["build"] == 90
    assert b["version"] == "1.57.0"          # MINOR


def test_preserves_history(M):
    (M.CORPORATE_ROOT / "BUILD.json").write_text(
        json.dumps({"version": "1.0.0", "build": 5,
                    "history": [{"event": "force-promote", "build": 4}]}),
        encoding="utf-8")
    M.bump_build()
    b = _build(M)
    assert b["build"] == 6
    assert b["history"] == [{"event": "force-promote", "build": 4}]


def test_initialises_when_absent(M):
    assert M.bump_build() == 0
    b = _build(M)
    assert b["build"] == 1
    assert b["version"] == "0.0.1"
