"""Scratch files must not survive a crash, and an absent measurement must not read as zero.

Two defects found by the 2026-08-31 review.

F12. `run_eval.py::run_single_query` writes a scratch command file into the real
project's ``.claude/commands/``. That location is not a mistake - `claude -p`
discovers project commands there, so a temp directory outside the repository
would measure nothing. Up to ten parallel workers each create one and unlink it
in a ``finally``. The names are uuid-suffixed, so there is no collision, and the
normal paths are clean.

A SIGKILL or a machine crash is not a normal path. ``.claude/commands/`` is a
tracked directory in this repository (``git check-ignore`` returns nothing for
it, and it already holds two committed commands), so surviving litter is litter
in a committed tree, and the next ``git add`` sweeps it in. The fix keeps the
required location and sweeps stale scratch files at the START of a run, naming
what it removed rather than cleaning up in silence: a file left behind is
evidence that a previous run died, and hiding it discards that.

F10. `aggregate_benchmark.py::aggregate_results` computed the delta as
``primary_mean - baseline_mean`` with ``{}`` standing in for a configuration
that produced no runs. ``0 - 0`` formats as ``"+0.00"``, so "this configuration
was never run" and "these two performed identically" printed the same string,
against the single keep-or-discard decision the benchmark exists to support.
The ``if not runs:`` branch had the same shape, emitting a full set of 0.0
statistics for a configuration nobody measured.

This is precisely the lesson `run_eval.py`'s own module docstring records - "a
run that never happened is not a negative result" - learned in one module and
never applied in the aggregator that consumes its output.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_CREATOR = ROOT / ".claude" / "skills" / "skill-creator"
SKILL_SCRIPTS = SKILL_CREATOR / "scripts"


def _load_shadowed(name: str, path: Path):
    """Import a skill-creator script without leaving the host's `scripts` broken.

    The skill's own package is also called `scripts` and this repo's is already
    in `sys.modules` by the time pytest reaches here. Same dance as
    `tests/test_skill_creator_run_eval_reports_a_dead_cli.py`.
    """
    saved = {k: v for k, v in sys.modules.items() if k == "scripts" or k.startswith("scripts.")}
    for key in saved:
        del sys.modules[key]
    # Snapshot and restore the WHOLE path, not one `remove`. The script being
    # loaded runs its own `sys.path.insert(0, <skill-creator>)` at import, so a
    # single `remove` of that string takes one of the two copies and leaves the
    # other on the path for the rest of the xdist worker - where the skill's own
    # `scripts/` package shadows this repo's for every later test.
    saved_path = sys.path[:]
    sys.path.insert(0, str(SKILL_CREATOR))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for key in [k for k in sys.modules if k == "scripts" or k.startswith("scripts.")]:
            del sys.modules[key]
        sys.modules.update(saved)


run_eval = _load_shadowed("_run_eval_scratch_under_test", SKILL_SCRIPTS / "run_eval.py")
agg = _load_shadowed("_agg_absence_under_test", SKILL_SCRIPTS / "aggregate_benchmark.py")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError("a test in this file attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


class _FakeSubprocess:
    """A stand-in for the `subprocess` MODULE, swapped into the module under test.

    Never `monkeypatch.setattr(mod.subprocess, "Popen", ...)`: `mod.subprocess`
    IS the shared stdlib module object, so that rebinds it interpreter-wide for
    the duration and any concurrently-collected test that spawns a process sees
    the fake. That cost another agent 18 unrelated failures today, visible only
    under random order. Replacing the module REFERENCE inside the module under
    test touches nothing global.
    """

    TimeoutExpired = subprocess.TimeoutExpired
    PIPE = subprocess.PIPE

    def __init__(self, popen=None, run=None):
        self._popen = popen or self._refuse
        self._run = run or self._refuse

    @staticmethod
    def _refuse(*args, **kwargs):
        raise AssertionError("a test in this file attempted to spawn a subprocess")

    def Popen(self, *args, **kwargs):  # noqa: N802 - mirrors the stdlib name
        return self._popen(*args, **kwargs)

    def run(self, *args, **kwargs):
        return self._run(*args, **kwargs)


@pytest.fixture(autouse=True)
def _never_spawn_claude(monkeypatch):
    """No test here may start the `claude` CLI or any other subprocess."""
    monkeypatch.setattr(run_eval, "subprocess", _FakeSubprocess())


# ---------------------------------------------------------------- F12


def _fake_project(tmp_path: Path) -> Path:
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    return tmp_path


def test_stale_scratch_command_files_are_swept(tmp_path):
    project = _fake_project(tmp_path)
    commands = project / ".claude" / "commands"
    litter = commands / "example-skill-skill-deadbeef.md"
    litter.write_text("left behind by a killed run\n", encoding="utf-8")

    removed = run_eval.sweep_stale_command_files(project)

    assert not litter.exists()
    assert removed == [litter.name]


def test_the_sweep_leaves_the_operators_own_commands_alone(tmp_path):
    """The negative case. A sweep with no spared case is a directory delete."""
    project = _fake_project(tmp_path)
    commands = project / ".claude" / "commands"
    keepers = [
        commands / "compact-at.md",
        commands / "unattended.md",
        commands / "my-skill.md",
        commands / "notes-skill-notes.md",          # no 8-hex suffix
        commands / "thing-skill-DEADBEEF.md",       # uppercase is not the mint's alphabet
        commands / "thing-skill-deadbeef.txt",      # not markdown
    ]
    for k in keepers:
        k.write_text("real content\n", encoding="utf-8")
    scratch = commands / "thing-skill-0a1b2c3d.md"
    scratch.write_text("scratch\n", encoding="utf-8")

    removed = run_eval.sweep_stale_command_files(project)

    assert removed == [scratch.name]
    for k in keepers:
        assert k.exists(), f"the sweep removed {k.name}, which it did not create"


def test_the_sweep_matches_the_name_run_single_query_actually_mints(tmp_path, monkeypatch):
    """Derive the scratch name from the minting code, never from a hand-typed twin.

    A pattern written out by hand in the test would keep passing after the
    minting side changed shape.
    """
    project = _fake_project(tmp_path)
    commands = project / ".claude" / "commands"

    minted = {}

    class _Boom(RuntimeError):
        pass

    def _explode_after_write(*args, **kwargs):
        minted["files"] = sorted(p.name for p in commands.iterdir())
        raise _Boom("stop before the CLI would start")

    monkeypatch.setattr(run_eval, "subprocess", _FakeSubprocess(popen=_explode_after_write))
    with pytest.raises(_Boom):
        run_eval.run_single_query(
            query="anything",
            skill_name="example",
            skill_description="a description",
            timeout=1,
            project_root=str(project),
        )

    assert len(minted["files"]) == 1
    name = minted["files"][0]
    # The finally-clause already removed it; put an identical one back and prove
    # the sweep would have caught it.
    (commands / name).write_text("crash litter\n", encoding="utf-8")
    assert run_eval.sweep_stale_command_files(project) == [name]


def test_run_eval_sweeps_before_it_scores(tmp_path, monkeypatch):
    """Through the public entry point, not by inspecting the source."""
    project = _fake_project(tmp_path)
    litter = project / ".claude" / "commands" / "example-skill-abcd1234.md"
    litter.write_text("left behind\n", encoding="utf-8")

    monkeypatch.setattr(run_eval, "require_claude_cli", lambda: None)
    out = run_eval.run_eval(
        eval_set=[],
        skill_name="example",
        description="a description",
        num_workers=1,
        timeout=1,
        project_root=project,
    )

    assert not litter.exists(), "run_eval scored a run without sweeping earlier litter"
    assert out["summary"]["total"] == 0


def test_a_missing_commands_directory_is_not_an_error(tmp_path):
    assert run_eval.sweep_stale_command_files(tmp_path) == []


# ---------------------------------------------------------------- F10


def _runs(pass_rate: float, seconds: float = 1.0, tokens: int = 10) -> list[dict]:
    return [{
        "eval_id": 0,
        "run_number": 1,
        "pass_rate": pass_rate,
        "passed": 1,
        "failed": 0,
        "total": 1,
        "time_seconds": seconds,
        "tokens": tokens,
        "expectations": [],
        "notes": [],
    }]


def _is_a_number(value) -> bool:
    try:
        float(str(value).replace("s", ""))
    except (TypeError, ValueError):
        return False
    return True


def test_a_genuine_tie_still_renders_as_a_measured_zero():
    """The other direction. Without this, "never render zero" would satisfy the
    suite by never rendering a delta at all."""
    summary = agg.aggregate_results({
        "with_skill": _runs(0.75),
        "without_skill": _runs(0.75),
    })
    # noqa S105: ruff reads the literal beside a name containing "pass" as a
    # credential. It is a formatted delta.
    assert summary["delta"]["pass_rate"] == "+0.00"  # noqa: S105


def test_an_unmeasured_configuration_does_not_render_a_delta_of_zero():
    summary = agg.aggregate_results({"with_skill": [], "without_skill": []})
    delta = summary["delta"]
    for key in ("pass_rate", "time_seconds", "tokens"):
        assert not _is_a_number(delta[key]), (
            f"delta[{key}] = {delta[key]!r} reads as a measurement of an unmeasured run"
        )


def test_a_missing_baseline_does_not_render_a_delta_of_zero():
    summary = agg.aggregate_results({"with_skill": _runs(0.9)})
    assert not _is_a_number(summary["delta"]["pass_rate"])


def test_no_configurations_at_all_does_not_render_a_delta_of_zero():
    summary = agg.aggregate_results({})
    assert not _is_a_number(summary["delta"]["pass_rate"])


def test_an_unmeasured_configuration_does_not_carry_zeroed_statistics():
    summary = agg.aggregate_results({"with_skill": [], "without_skill": _runs(0.5)})
    unmeasured = summary["with_skill"]
    assert unmeasured.get("pass_rate") != {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    assert unmeasured.get("measured") is False
    assert summary["without_skill"]["measured"] is True


def test_the_markdown_report_names_the_absence_instead_of_printing_a_percentage():
    benchmark = agg.generate_benchmark_from_results({"with_skill": [], "without_skill": []})
    markdown = agg.generate_markdown(benchmark)
    pass_rate_row = next(line for line in markdown.splitlines() if line.startswith("| Pass Rate"))
    assert "0%" not in pass_rate_row, f"an unmeasured configuration printed as a rate: {pass_rate_row}"
    assert agg.NOT_MEASURED in pass_rate_row


def test_the_markdown_report_still_prints_real_numbers_when_there_are_any():
    benchmark = agg.generate_benchmark_from_results({
        "with_skill": _runs(1.0),
        "without_skill": _runs(0.5),
    })
    markdown = agg.generate_markdown(benchmark)
    pass_rate_row = next(line for line in markdown.splitlines() if line.startswith("| Pass Rate"))
    assert "100%" in pass_rate_row and "50%" in pass_rate_row
    assert "+0.50" in pass_rate_row


def test_the_generated_json_is_still_valid_json_when_nothing_was_measured(tmp_path):
    benchmark = agg.generate_benchmark_from_results({"with_skill": [], "without_skill": []})
    out = tmp_path / "benchmark.json"
    out.write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["run_summary"]["delta"]["pass_rate"] == agg.NOT_MEASURED
