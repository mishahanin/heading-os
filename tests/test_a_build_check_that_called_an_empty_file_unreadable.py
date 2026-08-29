#!/usr/bin/env python3
"""`scripts/check-build.py` diagnosing a file it read perfectly well.

`load_json` returns `None` and nothing else to mean "this file cannot be used".
Both readers in this script tested `if not <parsed>` instead, which is also true
for a `BUILD.json` that decoded cleanly to `{}`.

Measured before the change, with `{}` at the corporate path:

    ERROR: Cannot read corporate BUILD.json
      Expected at: <path>

The file was read. Its problem is that it holds no keys -- and the branch
immediately below, added on 2026-08-23 and precise enough to print the keys it
DID find, was unreachable for exactly the empty-object case it best describes.
The operator was sent after paths and permission bits instead.

The per-exec row carried the same conflation, in the copy that was not fixed
alongside it: an exec `BUILD.json` of `{}` printed "not found" for a file
sitting on disk, so `_build_detail`'s "no 'build' key" row could never be
reached from `main`.

Nothing here reads the clock, the network, or a real fleet: `load_fleet` and
`get_per_exec_repo_path` are stubbed, and every name is invented.

Run: .venv/bin/python -m pytest
     tests/test_a_build_check_that_called_an_empty_file_unreadable.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "check-build.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_build_empty", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cb = _load()


class _Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.execs = []

    def corporate(self, text):
        path = self.tmp / "corporate-BUILD.json"
        path.write_text(text, encoding="utf-8")
        self.monkeypatch.setattr(cb, "CORPORATE_BUILD", path)
        return self

    def add_exec(self, slug, name, build_text):
        repo = self.tmp / slug
        (repo / "corporate").mkdir(parents=True, exist_ok=True)
        if build_text is not None:
            (repo / "corporate" / "BUILD.json").write_text(build_text, encoding="utf-8")
        self.execs.append({"slug": slug, "name": name,
                           "is_heading_os_user": True, "data_repo": f"{slug}-data"})
        return self

    def run(self):
        self.monkeypatch.setattr(cb, "load_fleet", lambda: list(self.execs))
        self.monkeypatch.setattr(cb, "get_per_exec_repo_path", lambda slug: self.tmp / slug)
        buf = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buf):
                cb.main()
        except SystemExit as exc:  # main() exits on the corporate-side errors
            code = exc.code or 0
        return code, buf.getvalue()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return _Harness(tmp_path, monkeypatch)


# ============================================================
# 1 - the corporate side
# ============================================================

def test_an_empty_object_is_reported_as_missing_keys_not_unreadable(harness):
    code, out = harness.corporate("{}").run()

    assert code == 1, out
    assert "Cannot read" not in out, out
    assert "missing 'build' and/or 'version'" in out, out
    assert "Found keys: []" in out, out


def test_a_partial_object_still_names_the_keys_it_found(harness):
    code, out = harness.corporate(json.dumps({"version": "1.4"})).run()

    assert code == 1, out
    assert "Cannot read" not in out, out
    assert "missing 'build' and/or 'version'" in out, out
    assert "'version'" in out, out


@pytest.mark.parametrize("text", ["", "not json at all", "{"])
def test_a_genuinely_unreadable_file_is_still_called_unreadable(harness, text):
    """The other direction: `load_json` returning None must keep its message."""
    code, out = harness.corporate(text).run()

    assert code == 1, out
    assert "Cannot read corporate BUILD.json" in out, out


def _no_fleet():
    """A named stub, not the `list` builtin: `list(x)` would swallow an argument."""
    return []


def test_a_missing_file_is_still_called_unreadable(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CORPORATE_BUILD", tmp_path / "nowhere" / "BUILD.json")
    monkeypatch.setattr(cb, "load_fleet", _no_fleet)
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc, contextlib.redirect_stdout(buf):
        cb.main()
    assert exc.value.code == 1
    assert "Cannot read corporate BUILD.json" in buf.getvalue()


@pytest.mark.parametrize(("text", "shape"), [("[]", "list"), ("0", "int"), ('""', "str")])
def test_a_non_object_is_named_rather_than_mis_diagnosed(harness, text, shape):
    """A JSON array decodes fine and has no `in` semantics this script can use."""
    code, out = harness.corporate(text).run()

    assert code == 1, out
    assert "is not a JSON object" in out, out
    assert shape in out, out


def test_a_well_formed_corporate_build_still_prints_the_header(harness):
    code, out = harness.corporate(json.dumps({"build": 42, "version": "1.4"})).run()

    assert code == 0, out
    assert "Build 42 (v1.4)" in out, out


# ============================================================
# 2 - the per-exec row, which carried the same conflation
# ============================================================

def test_an_empty_exec_build_is_malformed_not_missing(harness):
    harness.corporate(json.dumps({"build": 42, "version": "1.4"}))
    harness.add_exec("felix-leiter", "Felix Leiter", "{}")
    code, out = harness.run()

    assert code == 0, out
    assert "not found" not in out, out
    assert "malformed build no 'build' key" in out, out


def test_an_absent_exec_build_is_still_not_found(harness):
    """The other direction, so a blanket rename cannot pass this pair."""
    harness.corporate(json.dumps({"build": 42, "version": "1.4"}))
    harness.add_exec("vesper-lynd", "Vesper Lynd", None)
    code, out = harness.run()

    assert code == 0, out
    assert "not found" in out, out
    assert "malformed" not in out, out


def test_a_non_object_exec_build_does_not_crash_the_table(harness):
    """A list has no `.get`; the row must name it and the next row must print."""
    harness.corporate(json.dumps({"build": 42, "version": "1.4"}))
    harness.add_exec("rene-mathis", "Rene Mathis", "[1, 2, 3]")
    harness.add_exec("tanner-hale", "Tanner Hale", json.dumps({"build": 42}))
    code, out = harness.run()

    assert code == 0, out
    assert "not a JSON object: list" in out, out
    assert "Tanner Hale" in out and "up to date" in out, out


def test_a_healthy_exec_row_is_unchanged(harness):
    harness.corporate(json.dumps({"build": 44, "version": "1.6"}))
    harness.add_exec("tanner-hale", "Tanner Hale", json.dumps({"build": 42, "version": "1.5"}))
    code, out = harness.run()

    assert code == 0, out
    assert "2 builds behind" in out, out
