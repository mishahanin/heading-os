"""The end-of-turn check, and the mapping that makes it worth running.

`scripts/turn-check.py` exists because on 2026-08-09 a constant rename in
`scripts/wizard-verify-key.py` broke four tests and nothing noticed until a full
suite was run by hand much later. The piece that makes it catch that specific
case is unglamorous: the changed file is `wizard-verify-key.py` and its tests
live in `test_wizard_verify_key.py`, so the stem match only works once hyphens
are normalised to underscores. That mapping is tested here by name.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "turn_check_mod", ROOT / "scripts" / "turn-check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


tc = _load()


def test_hyphenated_script_maps_to_its_underscored_test_file():
    """The exact pair the script was written for."""
    target = ROOT / "scripts" / "wizard-verify-key.py"
    if not target.exists():
        pytest.skip("wizard-verify-key.py is gone; the mapping example moved")
    matched = {p.name for p in tc.matching_tests([target])}
    assert "test_wizard_verify_key.py" in matched, matched


def test_a_changed_test_file_selects_itself():
    target = ROOT / "tests" / "test_turn_check.py"
    assert target in tc.matching_tests([target])


def test_stem_match_does_not_drag_in_unrelated_neighbours():
    """`test_<stem>_*` is allowed, a mere prefix of a longer word is not.

    Without the underscore, a change to `crm.py` would pull in every
    `test_crm_*.py` AND anything starting with the letters `crm`, which turns a
    seconds-long check into a suite run and teaches people to skip it.
    """
    picked = {p.name for p in tc.matching_tests([ROOT / "scripts" / "utils" / "crm.py"])}
    for name in picked:
        body = name[len("test_"): -len(".py")]
        assert body == "crm" or body.startswith("crm_"), name


def test_only_library_packages_are_import_probed():
    """A top-level CLI script may re-exec the interpreter through `ensure_venv`
    at module scope, which a Stop hook must never trigger."""
    assert tc.module_name(ROOT / "scripts" / "utils" / "tool_risk.py") == "scripts.utils.tool_risk"
    assert tc.module_name(ROOT / "scripts" / "push-all.py") is None
    assert tc.module_name(ROOT / "scripts" / "utils" / "__init__.py") is None


def test_compile_lane_names_the_file_and_leaves_no_artefact(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    failures = tc.lane_compile([bad])
    assert failures and "broken.py" in failures[0]
    assert not (tmp_path / "broken.py.turncheck.pyc").exists()


def test_compile_lane_is_silent_on_valid_source(tmp_path):
    good = tmp_path / "fine.py"
    good.write_text("VALUE = 1\n", encoding="utf-8")
    assert tc.lane_compile([good]) == []


def test_fingerprint_tracks_content_not_mtime(tmp_path):
    """A save that changes no bytes is not a new thing to check."""
    import os

    f = tmp_path / "a.py"
    f.write_text("X = 1\n", encoding="utf-8")
    monkey = tc.ROOT
    tc.ROOT = tmp_path
    try:
        first = tc.fingerprint([f])
        os.utime(f, (1_000_000, 1_000_000))
        assert tc.fingerprint([f]) == first
        f.write_text("X = 2\n", encoding="utf-8")
        assert tc.fingerprint([f]) != first
    finally:
        tc.ROOT = monkey


def test_an_unreadable_file_does_not_raise(tmp_path):
    """The check is a warning system; it never becomes the reason work stops."""
    missing = tmp_path / "gone.py"
    monkey = tc.ROOT
    tc.ROOT = tmp_path
    try:
        assert tc.fingerprint([missing])
    finally:
        tc.ROOT = monkey


def test_import_lane_reports_a_broken_module(tmp_path, monkeypatch):
    """One subprocess, and its traceback is what the operator is shown."""
    monkeypatch.setattr(tc, "module_name", lambda p: "definitely_not_a_real_module_xyz")
    failures = tc.lane_import([tmp_path / "x.py"])
    assert failures and "definitely_not_a_real_module_xyz" in failures[0]
