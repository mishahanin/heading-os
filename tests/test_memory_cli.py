"""scripts/memory.py is a thin facade: prove each subcommand dispatches to the
correct existing script with the right args, that reconcile uses CLI mode (never a
bare no-op hook call), and that --help lists every shipped subcommand.

subprocess.run is monkeypatched so no backing script actually runs; the test asserts
the argv the facade WOULD have executed.
"""
from __future__ import annotations

import pytest

from scripts import memory as cli

PY = cli.PY
ROOT = cli.ROOT


def _capture(monkeypatch):
    calls: list[list[str]] = []

    class _Result:
        returncode = 0

    def fake_run(argv, *a, **k):
        calls.append(argv)
        return _Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return calls


def _invoke(argv: list[str]) -> int:
    # Mirror main(): parse_known_args so passthrough flags land in extras.
    args, extras = cli.build_parser().parse_known_args(argv)
    args.extras = extras
    return args.func(args)


def _script(rel: str) -> str:
    return str(ROOT / rel)


def test_recall_dispatches_to_memory_index_query(monkeypatch):
    calls = _capture(monkeypatch)
    assert _invoke(["recall", "sovereign packet"]) == 0
    assert calls == [[PY, _script("scripts/memory-index.py"), "query", "sovereign packet"]]


def test_recall_passes_through_extra_flags(monkeypatch):
    calls = _capture(monkeypatch)
    _invoke(["recall", "q", "--top-k", "3"])
    assert calls[0] == [PY, _script("scripts/memory-index.py"), "query", "q", "--top-k", "3"]


def test_retire_dispatches_to_retire_memory(monkeypatch):
    calls = _capture(monkeypatch)
    _invoke(["retire", "feedback_foo.md", "bar.md"])
    assert calls[0] == [PY, _script("scripts/retire-memory.py"), "feedback_foo.md", "bar.md"]


def test_promote_passes_through_flags(monkeypatch):
    calls = _capture(monkeypatch)
    _invoke(["promote", "--note", "knowledge/n.md", "--type", "signals"])
    assert calls[0] == [PY, _script("scripts/promote-knowledge.py"), "--note", "knowledge/n.md", "--type", "signals"]


def test_hygiene_passes_through_flags(monkeypatch):
    calls = _capture(monkeypatch)
    _invoke(["hygiene", "--json"])
    assert calls[0] == [PY, _script("scripts/memory-hygiene.py"), "--json"]


def test_status_aggregates_fast_read_only_signals(monkeypatch):
    calls = _capture(monkeypatch)
    assert _invoke(["status"]) == 0
    scripts_called = [c[1] for c in calls]
    assert _script("scripts/memory-index.py") in scripts_called
    assert _script("scripts/knowledge-health.py") in scripts_called
    # hygiene is intentionally NOT run in status (it compiles the ODIN brain and is
    # slow); it stays a dedicated subcommand so status is responsive.
    assert _script("scripts/memory-hygiene.py") not in scripts_called


def test_reconcile_uses_cli_mode_not_bare_call(monkeypatch):
    calls = _capture(monkeypatch)
    assert _invoke(["reconcile"]) == 0
    argv = calls[0]
    assert argv[1] == _script(cli.RECONCILE_HOOK)
    assert "--native" in argv and "--canonical" in argv
    # resolved dirs are non-empty (a bare hook call would carry neither flag).
    assert argv[argv.index("--native") + 1]
    assert argv[argv.index("--canonical") + 1].endswith("auto-memory")


def test_missing_backing_script_degrades(monkeypatch):
    _capture(monkeypatch)
    monkeypatch.setattr(cli.Path, "exists", lambda self: False)
    # _run returns 3 (plain message) when the target script is absent.
    assert cli._run("scripts/does-not-exist.py") == 3


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for name in ("status", "recall", "promote", "retire", "reconcile", "hygiene"):
        assert name in out
