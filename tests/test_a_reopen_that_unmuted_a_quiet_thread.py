"""Shard scripts-14-p4: the index a reopen unmuted, and two gates that passed
on evidence they never gathered.

* `thread.py reopen` is the ONLY writer in the file that did not pass
  `quiet_until=` to `add_thread_to_index`. A thread carrying `quiet_until`,
  closed and then reopened, came back into `## Active Threads` as a plain
  active line. MEMORY.md is loaded into every session, so that marker is the
  only thing an index reader has telling it the thread must not be surfaced
  proactively. The failure direction is toward surfacing, which `is_quiet` is
  explicitly written to avoid for anything but a broken date.

* `reindex`, whose stated job is repairing that exact drift, could not see it:
  `read_thread_hook` strips the marker before comparing, and
  `compose_thread_hook` never emits one, so the two always matched and it
  reported `rewrote 0 hook(s)`.

* `reindex` also collapsed every missing-from-index cause into one counter and
  then asserted "expected for closed/on-hold" without ever reading `t.status`,
  which is in hand two lines above. A MEMORY.md whose whole index section had
  been removed produced a clean bill of health and exit 0.

  SUPERSEDED 2026-08-27. All three findings are about a second copy of the
  record, and that copy is gone: `## Active Threads` was retired on 2026-08-20
  on the reader side, and its writer, its readers and `reindex` itself were
  removed seven days later. The twelve tests that pinned marker-on-the-index
  behaviour are replaced below by the invariant underneath them, which does
  survive: a deliberate freeze must outlive a close-and-reopen cycle, and it
  must be visible on the surface a rollup reads. That surface is now
  `thread.py list` (see `tests/test_thread_quiet_period.py`).

* `update-manager check` swallowed every `SourceError` with no message and
  printed "checked N components; 0 waiting" whether every upstream resolved or
  none did.

* `turn-check`'s test lane forgave pytest exit 5 only when something had been
  deselected. A matched file that collects nothing (an empty file at the start
  of a TDD slice) fell into the generic failure branch and blocked the turn
  over a lane in which nothing failed.

Every thread and index here lives in tmp_path. The workspace's own MEMORY.md is
never opened.

Run: python3 -m pytest tests/test_a_reopen_that_unmuted_a_quiet_thread.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.threads_lib import parse_thread_file  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def th():
    return _load("scripts/thread.py", "thread_under_test")


@pytest.fixture(scope="module")
def um():
    return _load("scripts/update-manager.py", "um_under_test")


@pytest.fixture(scope="module")
def tc():
    return _load("scripts/turn-check.py", "tc_under_test")


# ============================================================
# The freeze a reopen dropped
# ============================================================

@pytest.fixture
def workspace(th, tmp_path, monkeypatch):
    """A threads root in tmp. Nothing real is opened.

    It handed back a MEMORY.md too, and patched `_memory_md` to reach it. Both
    went with the index on 2026-08-27.
    """
    threads = tmp_path / "threads"
    (threads / "business").mkdir(parents=True)
    monkeypatch.setattr(th, "_threads_root", lambda: threads)
    # No path here reaches the data root today. Pinned anyway: a mutation
    # that puts the MEMORY.md writer back must land in tmp, not in the
    # operator's live overlay. It did, on 2026-08-27.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return threads


def _open(th, threads, title="Acme pilot review", type_="business"):
    """Open a thread and return the id `cmd_open` actually gave it.

    The id carries the day the thread was opened, so the six tests below spelled
    it as a literal and passed only on the day they were written. They went red
    at midnight on 2026-08-27 with `thread not found in business/ or personal/`,
    which reads like a broken lookup and is not one. Read the id off disk.
    """
    import argparse
    th.cmd_open(argparse.Namespace(type=type_, title=title))
    made = sorted((threads / type_).glob("*.md"))
    assert len(made) == 1, f"expected exactly one thread on disk, found {made}"
    return made[0]


def _cycle(th, thread_id, quiet="2026-09-10"):
    import argparse
    if quiet:
        th.cmd_quiet(argparse.Namespace(thread_id=thread_id, until=quiet,
                                        clear=False, indefinite=False))
    th.cmd_close(argparse.Namespace(thread_id=thread_id, reason="pilot finished"))
    th.cmd_reopen(argparse.Namespace(thread_id=thread_id))


def test_a_reopened_quiet_thread_keeps_its_freeze(th, workspace, capsys):
    """The finding, on the surviving surface.

    The freeze used to have to be re-stamped onto a second file, and `reopen`
    was the one writer that forgot. It is now a frontmatter field that a status
    change must carry through, so the question is whether the round trip
    preserves it.
    """
    path = _open(th, workspace)
    capsys.readouterr()

    _cycle(th, path.stem)

    parsed = parse_thread_file(path)
    assert parsed.status == "active"
    assert parsed.quiet_until == "2026-09-10"


def test_a_reopened_thread_with_no_quiet_gains_no_freeze(th, workspace, capsys):
    """The guard must not stamp a freeze onto a thread that has none."""
    path = _open(th, workspace)
    capsys.readouterr()

    _cycle(th, path.stem, quiet=None)

    parsed = parse_thread_file(path)
    assert parsed.status == "active"
    assert parsed.quiet_until is None
    assert "quiet_until" not in path.read_text(encoding="utf-8")


def test_an_indefinite_freeze_also_survives_the_cycle(th, workspace, capsys):
    """`do_not_remind` is the dateless form and travels the same path."""
    import argparse
    path = _open(th, workspace)
    th.cmd_quiet(argparse.Namespace(thread_id=path.stem, until=None,
                                    clear=False, indefinite=True))
    th.cmd_close(argparse.Namespace(thread_id=path.stem, reason="pilot finished"))
    th.cmd_reopen(argparse.Namespace(thread_id=path.stem))
    capsys.readouterr()

    parsed = parse_thread_file(path)
    assert parsed.status == "active"
    assert parsed.do_not_remind is True


def test_the_reopened_thread_carries_the_reopen_date(th, workspace, capsys):
    from datetime import datetime

    from scripts.utils.workspace import get_default_tz

    path = _open(th, workspace)
    capsys.readouterr()

    _cycle(th, path.stem)

    today = datetime.now(get_default_tz()).date().isoformat()
    assert parse_thread_file(path).last_touched == today


def test_every_status_command_round_trips_the_freeze(th, workspace, capsys):
    """close, hold and reopen all go through one writer, so all three are asked.

    `_set_status` re-reads the file and writes it back. A rebuild that dropped
    an unmodelled or optional field is the older defect this pins against; the
    freeze is the field it ate the first time.
    """
    import argparse
    path = _open(th, workspace)
    th.cmd_quiet(argparse.Namespace(thread_id=path.stem, until="2026-09-10",
                                    clear=False, indefinite=False))
    capsys.readouterr()

    for call in (
        lambda: th.cmd_hold(argparse.Namespace(thread_id=path.stem, reason="parked")),
        lambda: th.cmd_reopen(argparse.Namespace(thread_id=path.stem)),
        lambda: th.cmd_close(argparse.Namespace(thread_id=path.stem, reason="done")),
        lambda: th.cmd_reopen(argparse.Namespace(thread_id=path.stem)),
    ):
        call()
        assert parse_thread_file(path).quiet_until == "2026-09-10"


# ============================================================
# The check that reported success over a total outage
# ============================================================

def test_an_unresolvable_upstream_is_reported_not_swallowed(um, capsys):
    """Silently swallowed, an outage was indistinguishable from being current."""
    from scripts.utils import update_sources

    class _Comp:
        name = "widget"
        latest = {"kind": "nope"}

    def _boom(spec):
        raise update_sources.SourceError("network unreachable")

    original = update_sources.latest_version
    update_sources.latest_version = _boom
    try:
        assert um.resolve_latest(_Comp()) == ""
    finally:
        update_sources.latest_version = original

    err = capsys.readouterr().err
    assert "widget" in err
    assert "network unreachable" in err


def _registry(tmp_path, names) -> Path:
    reg = tmp_path / "registry.yaml"
    body = ["components:", ""]
    for n in names:
        body += [
            f"  {n}:",
            "    tier: observed",
            f"    display: {n.title()}",
            "    current:",
            "      via: shell",
            "      cmd: \"echo 1.0.0\"",
            "      regex: '([0-9.]+)'",
            "    latest:",
            "      via: github_release",
            f"      repo: example/{n}",
            "",
        ]
    reg.write_text("\n".join(body), encoding="utf-8")
    return reg


def _run_check(um, monkeypatch, tmp_path, names, resolver):
    from scripts.utils import update_sources
    monkeypatch.setattr(um, "registry_path", lambda: _registry(tmp_path, names))
    monkeypatch.setattr(um, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(update_sources, "latest_version", resolver)
    import argparse
    return um.cmd_check(argparse.Namespace())


def test_check_fails_when_no_upstream_resolved(um, monkeypatch, tmp_path, capsys):
    from scripts.utils import update_sources

    def _boom(spec):
        raise update_sources.SourceError("down")

    rc = _run_check(um, monkeypatch, tmp_path, ["widget", "gadget"], _boom)

    captured = capsys.readouterr()
    assert rc == 1
    assert "NOT reached" in captured.out
    assert "widget" in captured.out and "gadget" in captured.out


def test_check_passes_when_every_upstream_resolved(um, monkeypatch, tmp_path,
                                                    capsys):
    rc = _run_check(um, monkeypatch, tmp_path, ["widget"], lambda spec: "1.0.0")

    captured = capsys.readouterr()
    assert rc == 0
    assert "NOT reached" not in captured.out


def test_a_partial_outage_is_named_but_not_fatal(um, monkeypatch, tmp_path,
                                                  capsys):
    """One reachable upstream still gives real information."""
    from scripts.utils import update_sources
    seen = []

    def _half(spec):
        seen.append(spec)
        if len(seen) == 1:
            return "1.0.0"
        raise update_sources.SourceError("down")

    rc = _run_check(um, monkeypatch, tmp_path, ["widget", "gadget"], _half)

    captured = capsys.readouterr()
    assert rc == 0
    assert "1 upstream(s) NOT reached" in captured.out


# ============================================================
# The turn blocked over a lane in which nothing failed
# ============================================================

def _lane(tc, monkeypatch, probe, timeout=60):
    """Drive `lane_tests` over a probe outside the repo.

    `matching_tests` maps a changed module to a test file INSIDE `tests/`, so a
    tmp probe never gets picked and the lane returns before it ever runs pytest.
    Stubbing the mapping keeps the subject exactly where the defect was: what
    `lane_tests` does with pytest's exit code.
    """
    monkeypatch.setattr(tc, "matching_tests", lambda paths: [probe])
    return tc.lane_tests([probe], timeout)


def test_an_empty_test_file_does_not_fail_the_lane(tc, tmp_path, monkeypatch):
    """A file created empty at the start of a TDD slice."""
    probe = tmp_path / "test_empty_probe.py"
    probe.write_text('"""Nothing yet."""\n', encoding="utf-8")

    failures, ran, skipped, dropped, empty = _lane(tc, monkeypatch, probe)

    assert failures == []
    assert empty == 1


def test_a_file_holding_only_helpers_does_not_fail_the_lane(tc, tmp_path,
                                                             monkeypatch):
    probe = tmp_path / "test_helpers_only.py"
    probe.write_text("def helper():\n    return 1\n", encoding="utf-8")

    failures, _ran, _skipped, _dropped, empty = _lane(tc, monkeypatch, probe)

    assert failures == []
    assert empty == 1


def test_the_empty_case_is_reported_not_silent(tc):
    """Not failing must not mean saying nothing: a matched file that ran no
    test reads as a passing lane otherwise."""
    note = tc._empty_note({"collected_nothing": 2})

    assert "2 matched file(s) collected no tests" in note


def test_a_lane_that_ran_tests_carries_no_empty_note(tc):
    assert tc._empty_note({"collected_nothing": 0}) == ""


def test_a_real_failure_still_fails_the_lane(tc, tmp_path, monkeypatch):
    """The guard must not swallow a genuine red test."""
    probe = tmp_path / "test_red_probe.py"
    probe.write_text("def test_red():\n    assert False\n", encoding="utf-8")

    failures, _ran, _skipped, _dropped, empty = _lane(tc, monkeypatch, probe)

    assert failures
    assert empty == 0


def test_a_passing_file_still_passes(tc, tmp_path, monkeypatch):
    probe = tmp_path / "test_green_probe.py"
    probe.write_text("def test_green():\n    assert True\n", encoding="utf-8")

    failures, ran, _skipped, _dropped, empty = _lane(tc, monkeypatch, probe)

    assert failures == []
    assert ran == 1
    assert empty == 0


def test_an_all_slow_file_is_deselected_not_empty(tc, tmp_path, monkeypatch):
    """The case the old guard handled, which must not regress into `empty`."""
    probe = tmp_path / "test_slow_probe.py"
    probe.write_text("import pytest\n\n\n@pytest.mark.slow\n"
                     "def test_slow():\n    assert True\n", encoding="utf-8")

    failures, _ran, _skipped, dropped, empty = _lane(tc, monkeypatch, probe)

    assert failures == []
    assert dropped == 1
    assert empty == 0


def test_every_lane_tests_return_has_five_values(tc, tmp_path, monkeypatch):
    """A short tuple unpacks into a ValueError inside the Stop hook."""
    probe = tmp_path / "test_green2.py"
    probe.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    assert len(_lane(tc, monkeypatch, probe)) == 5
    assert len(tc.lane_tests([], 60)) == 5


def test_the_run_result_carries_the_count(tc, monkeypatch, tmp_path):
    """The note is only reachable if `run` threads the value through."""
    probe = tmp_path / "test_empty2.py"
    probe.write_text('"""Nothing."""\n', encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [probe])
    monkeypatch.setattr(tc, "deleted_python_files", list)
    monkeypatch.setattr(tc, "narrow", lambda paths, transcript: (paths, 0))
    monkeypatch.setattr(tc, "write_state", lambda state: None)
    monkeypatch.setattr(tc, "lane_compile", lambda paths: [])
    monkeypatch.setattr(tc, "lane_import", lambda paths: [])
    monkeypatch.setattr(tc, "matching_tests", lambda paths: [probe])

    result = tc.run(60, use_cache=False)

    assert result["collected_nothing"] == 1
    assert result["status"] == "pass"
