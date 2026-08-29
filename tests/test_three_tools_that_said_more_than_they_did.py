"""Three tools whose words and whose code disagreed, each in the same direction.

Every one of them told the operator something the run had not established, and
`.claude/rules/scope-claims.md` names that shape as the defect: a sentence a
reader trusts, acts on, and quotes back later as fact.

`memory.py` is a facade over six backing scripts, so it parses with
`parse_known_args` to let a passthrough flag reach the child. Three subcommands
never read that pocket. `memory.py retire --dry-run feedback_foo.md` DISCARDED
the flag and performed a real all-store retire, and `retire-memory.py` has no
`--dry-run` at all, so the operator's belief was wrong twice over.
`reconcile --queit` ran loud and said nothing. The same facade already refuses a
silently-misparsed `recall` (see `leading_flag_error`); it now refuses here too.

`modem-tune.py generate` is documented as "No SSH, no ledger write" in the module
docstring and again in its own subcommand help. It went through `_device_ctx`,
which calls `resolve_device`, which probes the router over SSH in BOTH branches,
including the explicit `--device` one where the probe only fills in the text of
a display line. On an unreachable router the command stalled through two
15-second timeouts before printing a number it had from config all along.

`migrate-data.py --apply` let an exception from a migration escape as a raw
traceback, four lines below a handler that catches the same exception in the
dry-run branch and explains itself. The moment a migration dies half way is the
moment the operator most needs to be told which version the overlay reached.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


memory = _load("memory_cli", "memory.py")
modem = _load("modem_tune_cli", "modem-tune.py")
migrate = _load("migrate_data_cli", "migrate-data.py")


# ============================================================
# memory.py: a flag that was pocketed and thrown away
# ============================================================

@pytest.mark.parametrize("argv", [
    ["retire", "--dry-run", "x.md"],
    ["retire", "x.md", "--dry-run"],
    ["reconcile", "--queit"],
    ["status", "--json"],
])
def test_a_discarded_flag_is_refused(argv, capsys):
    assert memory.main(argv) == 2
    err = capsys.readouterr().err
    assert "unrecognised argument" in err
    assert "DISCARDED" in err


def test_the_refusal_names_the_flag_it_refused(capsys):
    memory.main(["retire", "--dry-run", "x.md"])
    assert "--dry-run" in capsys.readouterr().err


def test_a_clean_retire_is_not_refused(monkeypatch):
    """The other direction. A refusal that fires on the correct call is worse
    than none: it stops the operator doing the thing the tool is for."""
    seen = {}

    def fake_run(script, *args):
        seen["call"] = (script, args)
        return 0

    monkeypatch.setattr(memory, "_run", fake_run)
    assert memory.main(["retire", "a.md", "b.md"]) == 0
    assert seen["call"] == ("scripts/retire-memory.py", ("a.md", "b.md"))


@pytest.mark.parametrize("argv,expect", [
    (["recall", "a query", "--top-k", "3"], ["--top-k", "3"]),
    (["promote", "--note", "x.md"], ["--note", "x.md"]),
    (["hygiene", "--json"], ["--json"]),
])
def test_a_passthrough_subcommand_still_passes_through(argv, expect, monkeypatch):
    seen = {}
    monkeypatch.setattr(memory, "_run",
                        lambda script, *args: seen.setdefault("args", list(args)) and 0 or 0)
    assert memory.main(argv) == 0
    for token in expect:
        assert token in seen["args"]


def test_refusing_is_the_default_for_a_subcommand_nobody_classified():
    """A subcommand added later inherits the safe value, so forgetting to think
    about it produces friction rather than a silent drop."""
    parser = memory.build_parser()
    for command in ("status", "retire", "reconcile"):
        args, _ = parser.parse_known_args([command] + (["x.md"] if command == "retire" else []))
        assert args.passthrough is False, command


def test_the_three_passthrough_subcommands_opted_in():
    parser = memory.build_parser()
    for command, extra in (("recall", ["q"]), ("promote", []), ("hygiene", [])):
        args, _ = parser.parse_known_args([command] + extra)
        assert args.passthrough is True, command


# ============================================================
# modem-tune.py: "No SSH" that opened SSH
# ============================================================

def test_an_explicit_device_needs_no_probe():
    assert modem.resolve_device_offline("xe300", {"devices": {}}) == "xe300"


def test_a_single_configured_device_is_used():
    cfg = {"devices": {"xe300": {"host": "192.0.2.10"}}}
    assert modem.resolve_device_offline(None, cfg) == "xe300"


def test_several_configured_devices_are_refused_not_guessed():
    cfg = {"devices": {"xe300": {"host": "192.0.2.10"}, "e5800": {"host": "192.0.2.11"}}}
    with pytest.raises(SystemExit) as exc:
        modem.resolve_device_offline(None, cfg)
    assert exc.value.code == 2


def test_no_configured_device_at_all_is_refused():
    with pytest.raises(SystemExit) as exc:
        modem.resolve_device_offline(None, {"devices": {}})
    assert exc.value.code == 2


def test_generate_never_reaches_the_network(monkeypatch, capsys):
    """The measured symptom was two 15-second SSH timeouts before a number the
    command already had. Anything that reaches the router now raises."""
    def explode(*_a, **_k):
        raise AssertionError("generate contacted the router")

    monkeypatch.setattr(modem, "_probe_model", explode)
    monkeypatch.setattr(modem, "resolve_device", explode)
    monkeypatch.setattr(modem, "_device_ctx", explode)
    monkeypatch.setattr(modem, "driver_for", explode)
    monkeypatch.setattr(modem, "ssh", explode)
    monkeypatch.setattr(modem, "load_config",
                        lambda: {"devices": {"xe300": {"host": "192.0.2.10",
                                                       "tac": "35291612"}}})
    monkeypatch.setattr(modem.mc, "load_ledger", lambda _p: {"used": []})

    args = types.SimpleNamespace(device="xe300")
    assert modem.cmd_generate(args) == 0
    printed = capsys.readouterr()
    assert printed.out.strip().isdigit()
    assert len(printed.out.strip()) == 15


def test_generate_says_the_device_came_from_config(monkeypatch, capsys):
    monkeypatch.setattr(modem, "load_config",
                        lambda: {"devices": {"xe300": {"host": "192.0.2.10",
                                                       "tac": "35291612"}}})
    monkeypatch.setattr(modem.mc, "load_ledger", lambda _p: {"used": []})
    modem.cmd_generate(types.SimpleNamespace(device=None))
    assert "from config" in capsys.readouterr().err


# ============================================================
# migrate-data.py: a traceback where a report belonged
# ============================================================

def _stub_migration(name: str, on_apply):
    mod = types.ModuleType(name)
    mod.up = on_apply
    return mod


@pytest.fixture()
def overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(migrate, "data_root_is_demo", lambda: False)
    monkeypatch.setattr(migrate, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(migrate, "read_data_schema_version", lambda: 1)
    written = []
    monkeypatch.setattr(migrate, "_write_version",
                        lambda root, version: written.append(version))
    return written


def test_a_failing_apply_is_reported_not_raised(overlay, monkeypatch, capsys):
    def boom(_root, dry_run):
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrate, "registered_migrations",
                        lambda: [(2, _stub_migration("0002_x", boom))])
    assert migrate.cmd_apply(dry_run=False) == 1
    err = capsys.readouterr().err
    assert "apply FAILED" in err
    assert "disk full" in err
    assert overlay == [], "the version marker advanced past a step that failed"


def test_the_report_names_the_version_the_overlay_reached(overlay, monkeypatch, capsys):
    calls = []

    def up(_root, dry_run):
        calls.append(dry_run)
        if len(calls) == 2:
            raise RuntimeError("boom")

    monkeypatch.setattr(migrate, "registered_migrations",
                        lambda: [(2, _stub_migration("0002_a", up)),
                                 (3, _stub_migration("0003_b", up))])
    assert migrate.cmd_apply(dry_run=False) == 1
    assert overlay == [2], "the completed step should still be marked"
    assert "v2" in capsys.readouterr().err


def test_a_clean_apply_still_applies(overlay, monkeypatch, capsys):
    monkeypatch.setattr(migrate, "registered_migrations",
                        lambda: [(2, _stub_migration("0002_x", lambda _r, dry_run: None))])
    assert migrate.cmd_apply(dry_run=False) == 0
    assert overlay == [2]
    assert "done" in capsys.readouterr().out


def test_the_dry_run_branch_is_untouched(overlay, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        migrate, "registered_migrations",
        lambda: [(2, _stub_migration("0002_x", lambda _r, dry_run: seen.append(dry_run)))])
    assert migrate.cmd_apply(dry_run=True) == 0
    assert seen == [True]
    assert overlay == [], "a dry run must not stamp a version"
