#!/usr/bin/env python3
"""`scripts/visual-discipline-check.py` speaking for files it never looked at.

Two defects, both of the shape "an incomplete run produced a complete-sounding
verdict".

  - `baseline record` guarded on `resolve_cli() is None` -- an UNRESOLVABLE deep
    CLI -- and then discarded `_run_audit`'s third return value with `_, _`. A
    CLI that resolves and fails at RUNTIME (bad npx pin, non-zero exit, timeout)
    reports that through `deep_note`, not through `resolve_cli`. Measured with
    `deep_findings` returning `([], "impeccable timed out after 120s; ...")` and
    `resolve_cli` returning a path:

        Baseline recorded: 0 finding(s) across 0 file(s).
        rc = 0

    A regex-only freeze, reported as complete. `baseline check` always runs deep
    and refuses its OWN degraded runs, so every later check then failed on
    pre-existing deep debt the freeze never recorded. The comment above the
    guard already names that asymmetry as the thing the branch exists to
    prevent; the guard stopped one degradation short of it.

  - The exit-code table reserves 1 for "findings present" and 2 for "script
    error". A file the walk could not open set `any_fail`, so a permission bit
    exited 1 under a summary line reading "0 error(s), 0 warning(s)" -- and a CI
    caller keying on the documented contract read that as visual debt.

The deep engine is stubbed at the `impeccable_engine` seam in every test here;
nothing spawns npx, and nothing reaches the network.

Run: .venv/bin/python -m pytest
     tests/test_a_baseline_frozen_from_a_run_that_half_happened.py -q
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "visual-discipline-check.py"

DEGRADED = "impeccable timed out after 120s; deep design checks skipped"


def _load():
    spec = importlib.util.spec_from_file_location("vdc_partial_run", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vdc = _load()


@pytest.fixture
def site(tmp_path):
    """A one-file scan root. Non-empty on purpose: an empty corpus proves nothing."""
    (tmp_path / "index.html").write_text(
        "<html><body><h1>Quantum</h1></body></html>", encoding="utf-8")
    assert list(vdc._iter_files(tmp_path, True)), "fixture produced no scannable files"
    return tmp_path


@pytest.fixture
def engine(monkeypatch):
    """Stub the deep engine and the baseline store; record what gets frozen."""
    eng = vdc.impeccable_engine
    frozen = {}

    monkeypatch.setattr(eng, "resolve_cli", lambda *a, **k: Path("/usr/bin/true"))
    monkeypatch.setattr(eng, "deep_findings", lambda *a, **k: ([], ""))
    monkeypatch.setattr(eng, "load_baseline", lambda *a, **k: {})
    monkeypatch.setattr(eng, "apply_baseline", lambda findings, base: list(findings))

    def record(findings):
        frozen["findings"] = list(findings)
        return {}

    monkeypatch.setattr(eng, "record_baseline", record)
    return type("Engine", (), {"mod": eng, "frozen": frozen, "monkeypatch": monkeypatch})


def _args(path, action):
    return argparse.Namespace(path=str(path), strict=False, profile=None,
                              include_internal=True, action=action, deep=False,
                              json=False, no_baseline=False)


def _unreadable(path: Path) -> None:
    path.chmod(0)
    if os.access(path, os.R_OK):  # root, or a filesystem ignoring the mode bit
        pytest.skip("this filesystem/user cannot make a file unreadable")


# ============================================================
# 1 - `record` refuses a degraded deep run, as `check` already did
# ============================================================

def test_record_refuses_when_the_deep_engine_degraded_at_runtime(site, engine, capsys):
    engine.monkeypatch.setattr(engine.mod, "deep_findings", lambda *a, **k: ([], DEGRADED))

    rc = vdc._cmd_baseline(_args(site, "record"))

    assert rc == 2
    assert "findings" not in engine.frozen, "a degraded run still froze a baseline"
    assert "refusing to record" in capsys.readouterr().err


def test_record_still_refuses_an_unresolvable_cli(site, engine, capsys):
    """The guard that already existed, pinned so the new one cannot replace it."""
    engine.monkeypatch.setattr(engine.mod, "resolve_cli", lambda *a, **k: None)

    rc = vdc._cmd_baseline(_args(site, "record"))

    assert rc == 2
    assert "findings" not in engine.frozen
    assert "degraded run" in capsys.readouterr().err


def test_record_refuses_when_a_file_could_not_be_read(site, engine, capsys):
    """A file the walk skipped is missing from the freeze the same way."""
    _unreadable(site / "index.html")
    try:
        rc = vdc._cmd_baseline(_args(site, "record"))
    finally:
        (site / "index.html").chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert rc == 2
    assert "findings" not in engine.frozen
    assert "refusing to record" in capsys.readouterr().err


def test_a_healthy_run_still_records(site, engine, capsys):
    """The other direction. A guard that refuses everything freezes nothing."""
    engine.monkeypatch.setattr(
        engine.mod, "deep_findings",
        lambda *a, **k: ([{"file": "index.html", "type": "impeccable:contrast",
                           "severity": "error", "tell": "3.1:1", "line": 1,
                           "context": "h1"}], ""))

    rc = vdc._cmd_baseline(_args(site, "record"))

    assert rc == 0
    assert engine.frozen["findings"], "a healthy run recorded an empty baseline"
    assert "Baseline recorded" in capsys.readouterr().out


# ============================================================
# 2 - `check` refuses a partial walk too
# ============================================================

def test_check_refuses_when_a_file_could_not_be_read(site, engine, capsys):
    _unreadable(site / "index.html")
    try:
        rc = vdc._cmd_baseline(_args(site, "check"))
    finally:
        (site / "index.html").chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert rc == 2
    assert "partial scan" in capsys.readouterr().err


def test_check_still_refuses_a_degraded_deep_run(site, engine, capsys):
    engine.monkeypatch.setattr(engine.mod, "deep_findings", lambda *a, **k: ([], DEGRADED))
    rc = vdc._cmd_baseline(_args(site, "check"))
    assert rc == 2
    assert "refusing a baseline check" in capsys.readouterr().err


def test_a_clean_check_still_passes(site, engine, capsys):
    rc = vdc._cmd_baseline(_args(site, "check"))
    assert rc == 0
    assert "No findings above the baseline" in capsys.readouterr().out


# ============================================================
# 3 - the exit-code contract: 1 is findings, 2 is a script error
# ============================================================

def _main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["visual-discipline-check.py", *argv])
    with pytest.raises(SystemExit) as exc:
        vdc.main()
    return exc.value.code


def test_an_unreadable_file_exits_two_not_one(site, monkeypatch, capsys):
    _unreadable(site / "index.html")
    try:
        code = _main(monkeypatch, [str(site), "--include-internal"])
        out = capsys.readouterr()
    finally:
        (site / "index.html").chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert code == 2, out.out
    # The summary that made exit 1 a lie is still printed; only the code moved.
    assert "0 error(s), 0 warning(s)" in out.out


def test_a_real_finding_still_exits_one(site, monkeypatch, capsys):
    """The exit code the table calls "findings present", left where it was."""
    (site / "index.html").write_text(
        '<html><body class="bg-gradient-to-r from-purple-600 to-pink-500">'
        "<h1>Quantum</h1></body></html>", encoding="utf-8")

    code = _main(monkeypatch, [str(site), "--include-internal", "--no-baseline"])
    out = capsys.readouterr().out

    assert code == 1, out
    assert "gradient_purple_pink" in out, out


def test_a_clean_scan_still_exits_zero(site, monkeypatch, capsys):
    code = _main(monkeypatch, [str(site), "--include-internal", "--no-baseline"])
    assert code == 0, capsys.readouterr().out
