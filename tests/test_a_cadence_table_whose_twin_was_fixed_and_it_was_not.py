#!/usr/bin/env python3
"""`aggregate-crm.py::parse_config` read a file its twin had been guarded for.

Two near-identical `parse_config(config_path)` functions read the same
`crm/config.md` cadence table. `scripts/utils/crm.py::parse_config` carries the
guard and the reason for it in a comment: "an undecodable config.md ended the
health run with a codec error rather than the built-in type defaults".
`scripts/aggregate-crm.py::parse_config` is the copy nobody updated - it checks
`config_path.exists()` and then reads with no `try` at all.

An `exists()` check answers whether the NAME is there, never whether the bytes
can be had. MEASURED 2026-09-01 against the unguarded function:

    one 0xff byte in the table  -> UnicodeDecodeError: 'utf-8' codec can't
                                   decode byte 0xff in position 79
    the file at mode 000        -> PermissionError: [Errno 13] Permission denied

Neither is in any except clause on the path. `main` calls `parse_config` at its
third statement and catches nothing before `FleetRegistryError` much later, so
either one ends the whole fleet aggregation with a traceback - no table, no
`--json` document, no per-exec degradation - over a config file whose only job
is to supply thresholds that already have built-in defaults.

`UnicodeDecodeError` is a `ValueError` and a SIBLING of `json.JSONDecodeError`,
so it is not an `OSError` and no handler shaped for one would have caught it
either.

The two branches on both sides of the read already degrade: an absent file and
an unparseable table each return the full `DEFAULT_CADENCE`. The fix makes the
third branch behave like its neighbours, and NAMES the file on stderr, because a
run that silently falls back to defaults is the other half of this defect class.

Run: .venv/bin/python -m pytest \
     tests/test_a_cadence_table_whose_twin_was_fixed_and_it_was_not.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AGG_PATH = ROOT / "scripts" / "aggregate-crm.py"


@pytest.fixture(scope="module")
def agg():
    spec = importlib.util.spec_from_file_location("aggregate_crm_cadence_twin", AGG_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aggregate_crm_cadence_twin"] = mod
    spec.loader.exec_module(mod)
    return mod


GOOD_TABLE = ("| Type | Cadence | Yellow | Red |\n"
              "|---|---|---|---|\n"
              "| partner | 21 | 14 | 21 |\n")


# ============================================================
# 1 - a config that cannot be READ
# ============================================================

def test_an_undecodable_config_falls_back_instead_of_raising(agg, tmp_path, capsys):
    cfg = tmp_path / "config.md"
    cfg.write_bytes(GOOD_TABLE.encode("utf-8") + b"\xff\xfe not utf-8\n")

    assert agg.parse_config(cfg) == agg.DEFAULT_CADENCE

    err = capsys.readouterr().err
    assert str(cfg) in err, "the degraded read must name the file it dropped"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file anyway")
def test_an_unreadable_config_falls_back_instead_of_raising(agg, tmp_path, capsys):
    cfg = tmp_path / "config.md"
    cfg.write_text(GOOD_TABLE, encoding="utf-8")
    os.chmod(cfg, 0o000)
    try:
        assert cfg.exists(), "the fixture must be present but unreadable"
        assert agg.parse_config(cfg) == agg.DEFAULT_CADENCE
        assert str(cfg) in capsys.readouterr().err
    finally:
        os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR)


def test_the_whole_run_survives_an_undecodable_config(agg, tmp_path, monkeypatch,
                                                     capsys, unguard_main_clone):
    """The blast radius, not just the function: `main` catches nothing here."""
    # `aggregate-crm.main()` opens with `require_main_clone(__file__)`, which
    # exits 2 from a worktree before the blast radius under test is reached.
    # `agg` is module-scoped and this fixture is function-scoped, so it is
    # applied per test, on that loaded module only. The guard keeps its own
    # owners: tests/test_guarded_entry_points_refuse_from_a_worktree.py pins
    # through the AST that the call is main()'s first statement and is passed
    # `__file__`, and tests/test_clone_guard.py pins that it fires.
    unguard_main_clone(agg)
    cfg = tmp_path / "config.md"
    cfg.write_bytes(b"\xff\xfe\n")
    monkeypatch.setattr(agg, "get_crm_config_path", lambda: cfg)
    monkeypatch.setattr(agg, "scan_all_contacts", lambda *a, **k: ([], []))
    monkeypatch.setattr(agg, "load_admin_config", dict)
    monkeypatch.setattr(sys, "argv", ["aggregate-crm.py", "--json"])

    with pytest.raises(SystemExit) as exc:
        agg.main()

    assert exc.value.code == 0
    import json
    assert json.loads(capsys.readouterr().out)["total_contacts"] == 0


# ============================================================
# 2 - the negative cases, ON the line
# ============================================================

def test_a_readable_config_is_still_parsed_and_says_nothing(agg, tmp_path, capsys):
    """The guard widens the fallback; it must not swallow a real table."""
    cfg = tmp_path / "config.md"
    cfg.write_text(GOOD_TABLE, encoding="utf-8")

    parsed = agg.parse_config(cfg)

    assert parsed["partner"] == {"cadence": 21, "yellow": 14, "red": 21}
    assert parsed["media"] == agg.DEFAULT_CADENCE["media"], "the merge was lost"
    assert capsys.readouterr().err == "", "a healthy read must be silent"


def test_an_absent_config_is_still_silent_defaults(agg, tmp_path, capsys):
    """Absent is a different fact from unreadable, and must stay quiet."""
    assert agg.parse_config(tmp_path / "nope.md") == agg.DEFAULT_CADENCE
    assert capsys.readouterr().err == ""


def test_a_config_holding_no_table_is_still_silent_defaults(agg, tmp_path, capsys):
    cfg = tmp_path / "config.md"
    cfg.write_text("prose, no table\n", encoding="utf-8")
    assert agg.parse_config(cfg) == agg.DEFAULT_CADENCE
    assert capsys.readouterr().err == ""


# ============================================================
# 3 - the twin, so the fix cannot land in one copy again
# ============================================================

def test_both_cadence_readers_degrade_on_the_same_two_failures(tmp_path):
    """The shared reader is the one that was already fixed. Drive BOTH."""
    from scripts.utils import crm as crm_utils

    cfg = tmp_path / "config.md"
    cfg.write_bytes(b"\xff\xfe\n")

    assert crm_utils.parse_config(cfg) == {}, "the shared reader regressed"

    spec = importlib.util.spec_from_file_location("agg_twin_probe", AGG_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agg_twin_probe"] = mod
    spec.loader.exec_module(mod)
    assert mod.parse_config(cfg) == mod.DEFAULT_CADENCE


def _read_text_calls_outside_a_try(tree: ast.AST) -> list[int]:
    """Line numbers of `<x>.read_text(...)` with no enclosing `try` in scope."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                guarded.add(id(child))
    bare = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "read_text" and id(node) not in guarded):
            bare.append(node.lineno)
    return sorted(bare)


def test_the_cadence_reader_no_longer_reads_outside_a_try():
    """A floor on the function itself, so the guard cannot be deleted quietly.

    Scoped to `parse_config` rather than the whole module: this file's finding is
    that ONE function read unguarded, and a module-wide count would fail on every
    unrelated read someone adds.
    """
    tree = ast.parse(AGG_PATH.read_text(encoding="utf-8"), filename=str(AGG_PATH))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "parse_config")

    reads = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "read_text"]
    assert len(reads) == 1, (
        f"{len(reads)} read_text calls in parse_config; the floor below models one")
    assert _read_text_calls_outside_a_try(fn) == [], (
        "parse_config reads its config file outside a try again")


def test_the_floor_can_fail(tmp_path):
    """A guard with no negative case is not a guard. Bind the detector to a
    synthetic unguarded reader, not to itself."""
    unguarded = ast.parse(
        "def parse_config(p):\n"
        "    if not p.exists():\n"
        "        return {}\n"
        "    return p.read_text(encoding='utf-8')\n"
    )
    assert _read_text_calls_outside_a_try(unguarded) == [4]

    guarded = ast.parse(
        "def parse_config(p):\n"
        "    try:\n"
        "        return p.read_text(encoding='utf-8')\n"
        "    except (OSError, UnicodeDecodeError):\n"
        "        return {}\n"
    )
    assert _read_text_calls_outside_a_try(guarded) == []
