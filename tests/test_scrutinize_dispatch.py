"""Contract for the /scrutinize judge dispatcher.

Written RED, before `scripts/scrutinize-dispatch.py` exists, per Step 1 of
`plans/2026-08-09-scrutinize-record-roles-currency.md`.

Three properties this file exists to hold. Family assignment belongs to the
dispatcher, not to the reviewing model, so the never-same-family rule survives a
model that would rather not comply. The sensitivity gate consults
`sensitivity_is_declared()` and never `is_sensitive()`, whose unset fail-closed
default would refuse every proxy call on an ordinary machine and kill the k3 side
of the two-family roster permanently. And a role lens fires from a path match
rather than from discretion.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load():
    """Import the kebab-case scripts/scrutinize-dispatch.py as a module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "scrutinize-dispatch.py"
    spec = importlib.util.spec_from_file_location("scrutinize_dispatch", path)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: `@dataclass` resolves its annotations through
    # `sys.modules[cls.__module__]`, so a module executed while absent from the
    # table raises `AttributeError: 'NoneType' object has no attribute '__dict__'`
    # at class-creation time.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


disp = _load()


@pytest.fixture
def runs(tmp_path, monkeypatch):
    from scripts.utils import scrutinize_record as rec
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    return path


def _rows(path):
    """Rows written so far. A missing file means none were, which is a real
    outcome here: the refusal paths are asserted by the ABSENCE of a row."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ============================================================
# Family assignment - the never-same-family rule
# ============================================================
def test_skeptic_and_meta_judge_are_never_the_same_family():
    for swap in (False, True):
        assign = disp.assign_families(swap=swap)
        assert assign["skeptic"] != assign["meta"]


def test_both_swap_states_use_both_families():
    a = disp.assign_families(swap=False)
    b = disp.assign_families(swap=True)
    assert {a["skeptic"], a["meta"]} == {"claude", "kimi"}
    assert a["skeptic"] == b["meta"] and a["meta"] == b["skeptic"]


def test_swap_bit_is_derived_from_the_run_not_from_the_model():
    """Same run id yields the same side assignment; different ids differ.

    The first assertion used to be the whole test, and `swap_for_run` is a pure
    function of one argument over an unsalted sha256, so `f(x) == f(x)` could not
    fail whatever the body did. `return False` passed it, and `assign_families`
    then pinned every run to the same three seats forever - Claude never sitting
    as skeptic - with the suite green. Only the SECOND half, that the bit tracks
    the id, has any content.
    """
    assert disp.swap_for_run("r1") == disp.swap_for_run("r1"), "not stable per run"
    assert isinstance(disp.swap_for_run("r1"), bool)

    bits = {disp.swap_for_run(f"run-{i}") for i in range(24)}
    assert bits == {False, True}, (
        "24 distinct run ids produced one side assignment; the bit is a constant "
        "and every run seats the same family as skeptic")


# ============================================================
# The sensitivity gate - declaration, never the fail-closed default
# ============================================================
def test_no_proxy_call_when_sensitivity_is_declared(runs, monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "on")
    called = []
    monkeypatch.setattr(disp, "call_model", lambda *a, **k: called.append(a))
    rc = disp.judge(run_id="r1", target="file:x", finding_id="H1", pass_="2.5a",
                    brief="does this hold?", family="kimi")
    assert called == []
    assert rc != 0
    rows = [r for r in _rows(runs) if r["kind"] == "degraded"]
    assert rows and "SENSITIVE_MODE" in rows[0]["degraded"]


def test_a_proxy_call_is_attempted_when_sensitive_mode_is_unset(runs, monkeypatch):
    """The machine default must not disable the roster.

    `is_sensitive()` is fail-closed and resolves True when unset, so a dispatcher
    consulting it would refuse here. This asserts the successor is consulted.
    """
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    called = []

    def _fake(model, prompt, **kw):
        called.append(model)
        return "REFUTATION_FAILED - the finding stands"

    monkeypatch.setattr(disp, "call_model", _fake)
    rc = disp.judge(run_id="r1", target="file:x", finding_id="H1", pass_="2.5a",
                    brief="does this hold?", family="kimi")
    assert called, "the unset default must not gate the proxy call"
    assert rc == 0


def test_cleared_sensitive_mode_also_permits_the_call(runs, monkeypatch):
    monkeypatch.setenv("SENSITIVE_MODE", "off")
    called = []
    monkeypatch.setattr(disp, "call_model", lambda m, p, **k: called.append(m) or "CORRECT")
    disp.judge(run_id="r1", target="file:x", finding_id="H1", pass_="2.5a",
               brief="b", family="kimi")
    assert called


def test_an_unreachable_proxy_writes_its_own_degraded_cause(runs, monkeypatch):
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)

    def _boom(model, prompt, **kw):
        raise RuntimeError("proxy refused connection")

    monkeypatch.setattr(disp, "call_model", _boom)
    rc = disp.judge(run_id="r1", target="file:x", finding_id="H1", pass_="2.5a",
                    brief="b", family="kimi")
    assert rc != 0
    rows = [r for r in _rows(runs) if r["kind"] == "degraded"]
    assert rows and "proxy" in rows[0]["degraded"].lower()
    assert "SENSITIVE_MODE" not in rows[0]["degraded"]


def test_a_claude_side_judge_never_calls_the_proxy(runs, monkeypatch):
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    called = []
    monkeypatch.setattr(disp, "call_model", lambda *a, **k: called.append(a))
    disp.judge(run_id="r1", target="file:x", finding_id="H1", pass_="2.5a",
               brief="b", family="claude", verdict="CORRECT")
    assert called == []
    verdicts = [r for r in _rows(runs) if r["kind"] == "verdict"]
    assert verdicts and verdicts[0]["judge_family"] == "claude"


# ============================================================
# Role lenses - a path match, not a judgement call
# ============================================================
@pytest.mark.parametrize("path,lens", [
    ("scripts/templates/systemd/reminders.timer", "ops"),
    ("scripts/templates/systemd/chronicle.service", "ops"),
    ("scripts/install-router-accuracy-timer.sh", "ops"),
    ("config/routing-map.yaml", "boundary"),
    (".claude/hooks/_dispatch.py", "boundary"),
    ("config/tool-risk.json", "boundary"),
])
def test_trigger_table_resolves_a_path_to_its_lens(path, lens):
    assert lens in disp.lenses_for([path])


def test_a_non_matching_path_fires_no_lens():
    assert disp.lenses_for(["docs/QUICKSTART.md"]) == []


def test_scheduler_lens_fires_on_an_apscheduler_import(tmp_path):
    f = tmp_path / "daemon.py"
    f.write_text("from apscheduler.schedulers.asyncio import AsyncIOScheduler\n")
    assert "scheduler" in disp.lenses_for([str(f)])


def test_scheduler_lens_fires_on_a_plain_import(tmp_path):
    f = tmp_path / "daemon.py"
    f.write_text("import apscheduler\n")
    assert "scheduler" in disp.lenses_for([str(f)])


def test_scheduler_lens_fires_on_an_add_job_call(tmp_path):
    f = tmp_path / "sched.py"
    f.write_text("def go(s):\n    s.add_job(tick, 'interval', minutes=1)\n")
    assert "scheduler" in disp.lenses_for([str(f)])


def test_scheduler_lens_does_not_fire_on_a_file_that_merely_mentions_scheduling(tmp_path):
    """The self-match the lens opened its life with, 2026-08-09 /scrutinize.

    A marker table holding the strings, a docstring about APScheduler, and a test
    fixture naming add_job are all files ABOUT scheduling, not files that
    schedule. A substring scan cannot tell them apart; the AST can.
    """
    f = tmp_path / "lens_table.py"
    f.write_text(
        '"""Fires on apscheduler imports and add_job calls."""\n'
        '_MARKERS = ("apscheduler", "add_job")\n'
        'NOTE = "the daemon must call add_job with a grace time"\n')
    assert "scheduler" not in disp.lenses_for([str(f)])


def test_the_dispatcher_no_longer_fires_the_scheduler_lens_on_itself():
    """The regression in its literal form: the lens flagged its own definition."""
    here = Path(__file__).resolve().parent.parent / "scripts" / "scrutinize-dispatch.py"
    assert "scheduler" not in disp.lenses_for([str(here)])


def test_scheduler_lens_ignores_an_unparsable_file(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def (((:\n")
    assert disp.lenses_for([str(f)]) == []


def test_role_scan_writes_one_row_per_firing_lens(runs):
    disp.role_scan(run_id="r1", target="dir:scripts",
                   paths=["scripts/templates/systemd/reminders.timer",
                          "config/routing-map.yaml"])
    fired = {r["role"] for r in _rows(runs) if r["kind"] == "role"}
    assert fired == {"ops", "boundary"}


# ============================================================
# Currency - version currency, and never fatal
# ============================================================
def test_currency_maps_an_import_to_its_distribution():
    assert disp.distribution_for("apscheduler") == "APScheduler"
    assert disp.distribution_for("yaml") == "PyYAML"


def test_currency_writes_ok_when_the_pin_matches_latest(runs, monkeypatch):
    monkeypatch.setattr(disp, "pinned_version", lambda dist: "3.11.0")
    monkeypatch.setattr(disp, "latest_version", lambda dist: "3.11.0")
    disp.currency(run_id="r1", target="file:x", imports=["apscheduler"])
    row = [r for r in _rows(runs) if r["kind"] == "currency"][0]
    assert row["currency"]["result"] == "ok"
    assert row["currency"]["distribution"] == "APScheduler"


def test_currency_writes_mismatch_when_the_pin_is_behind(runs, monkeypatch):
    monkeypatch.setattr(disp, "pinned_version", lambda dist: "3.10.0")
    monkeypatch.setattr(disp, "latest_version", lambda dist: "3.11.0")
    disp.currency(run_id="r1", target="file:x", imports=["apscheduler"])
    row = [r for r in _rows(runs) if r["kind"] == "currency"][0]
    assert row["currency"]["result"] == "mismatch"


def test_currency_degrades_to_inconclusive_and_exits_zero(runs, monkeypatch):
    def _boom(dist):
        raise RuntimeError("context7 unreachable")

    monkeypatch.setattr(disp, "pinned_version", lambda dist: "3.10.0")
    monkeypatch.setattr(disp, "latest_version", _boom)
    rc = disp.currency(run_id="r1", target="file:x", imports=["apscheduler"])
    assert rc == 0
    row = [r for r in _rows(runs) if r["kind"] == "currency"][0]
    assert row["currency"]["result"] == "inconclusive"


def test_currency_skips_stdlib_imports(runs):
    disp.currency(run_id="r1", target="file:x", imports=["pathlib", "json", "argparse"])
    assert [r for r in _rows(runs) if r["kind"] == "currency"] == []


# ============================================================
# Reproduction - the harness runs the command, never the model
# ============================================================
def test_reproduce_runs_the_command_and_records_the_exit(runs):
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-c", "import sys; sys.exit(3)"])
    assert rc == 0
    row = [r for r in _rows(runs) if r["kind"] == "reproduction"][0]
    assert row["verdict"] == "REPRODUCED"
    assert row["reproduction"]["exit_before"] == 3


def test_reproduce_refuses_a_command_that_already_passes(runs):
    """A command that exits 0 before the fix reproduces nothing."""
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-c", "pass"])
    assert rc != 0
    assert [r for r in _rows(runs) if r["kind"] == "reproduction"] == []


def test_promote_joins_the_stored_exit_before(runs):
    disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                   cmd=["python3", "-c", "import sys; sys.exit(3)"])
    rc = disp.promote(run_id="r1", target="file:x", finding_id="H1",
                      cmd=["python3", "-c", "pass"])
    assert rc == 0
    rows = [r for r in _rows(runs) if r["verdict"] == "FALSIFIED"]
    assert rows
    repro = rows[0]["reproduction"]
    assert repro["cmd"] == "python3 -c pass"
    assert repro["exit_before"] == 3
    assert repro["exit_after"] == 0


def test_promote_without_a_prior_reproduction_is_refused(runs):
    rc = disp.promote(run_id="r1", target="file:x", finding_id="H9",
                      cmd=["python3", "-c", "pass"])
    assert rc != 0
    assert [r for r in _rows(runs) if r["verdict"] == "FALSIFIED"] == []


# ============================================================
# A non-zero exit is evidence only if the check actually ran
# ============================================================
# Found by /scrutinize on 2026-08-13. `REPRODUCED` was written on ANY non-zero
# exit, refusing only exit 0, so every way a command can fail BEFORE reaching its
# check produced the same number the verdict reads as proof. Three rows of that
# run's own record carry artifact exits from pipelines that never piped. The
# property below is the one the verdict needs: an exit code counts only when the
# intended check is what produced it.

def test_a_pipeline_is_refused_because_no_shell_will_honour_it(runs):
    """`--cmd "a | b"` shlex-splits to a literal `|` argument.

    The pipeline never happens, the child chokes on the stray operator, and the
    non-zero exit that comes back says nothing about the finding.
    """
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-c", "pass", "|", "grep", "x"])
    assert rc == 4
    assert [r for r in _rows(runs) if r["kind"] == "reproduction"] == []


@pytest.mark.parametrize("operator", ["|", "&&", ";", ">", "<", "||", "&"])
def test_every_shell_operator_is_caught_not_just_the_pipe(operator):
    assert disp._reject_shell_syntax(["true", operator, "false"]) is not None


def test_a_plain_command_carries_no_shell_syntax():
    assert disp._reject_shell_syntax(["python3", "-m", "pytest", "-q"]) is None


def test_a_missing_executable_is_not_a_reproduction(runs):
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["definitely-not-a-real-binary-31c"])
    assert rc == 4
    assert [r for r in _rows(runs) if r["kind"] == "reproduction"] == []


def test_pytest_collecting_nothing_is_not_a_reproduction(runs, tmp_path):
    """Exit 5 is pytest's "no tests collected" - the mistyped-path artifact."""
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-m", "pytest", str(tmp_path), "-q"])
    assert rc == 4
    assert [r for r in _rows(runs) if r["kind"] == "reproduction"] == []


def test_a_genuine_test_failure_still_reproduces(runs, tmp_path):
    """The guard must not eat the case it exists to protect.

    pytest exit 1 means tests ran and failed, which is a real reproduction.
    """
    (tmp_path / "test_red.py").write_text("def test_red():\n    assert False\n")
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-m", "pytest", str(tmp_path), "-q"])
    assert rc == 0
    row = [r for r in _rows(runs) if r["kind"] == "reproduction"][0]
    assert row["reproduction"]["exit_before"] == 1


def test_a_signal_death_is_not_a_reproduction():
    run = disp._run(["python3", "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
    assert run.unusable is not None
    assert "signal" in run.unusable


def test_a_command_that_never_returns_is_not_a_reproduction(monkeypatch, runs):
    monkeypatch.setattr(disp, "REPRODUCTION_TIMEOUT_S", 1)
    rc = disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                        cmd=["python3", "-c", "import time; time.sleep(30)"])
    assert rc == 4
    assert [r for r in _rows(runs) if r["kind"] == "reproduction"] == []


def test_the_recorded_row_carries_the_output_that_justified_it(runs):
    """A bare exit code cannot be audited later; the tail can."""
    disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                   cmd=["python3", "-c",
                        "import sys; print('boom'); sys.stderr.write('why'); sys.exit(2)"])
    repro = [r for r in _rows(runs) if r["kind"] == "reproduction"][0]["reproduction"]
    assert repro["stdout_tail"] == "boom"
    assert repro["stderr_tail"] == "why"


def test_a_long_tail_is_truncated_from_the_front(runs):
    run = disp._run(["python3", "-c",
                     f"import sys; print('x' * {disp.OUTPUT_TAIL_CHARS * 2}); sys.exit(1)"])
    assert run.unusable is None
    assert run.stdout_tail.startswith("...")
    assert len(run.stdout_tail) == disp.OUTPUT_TAIL_CHARS + 3


def test_promote_refuses_an_unusable_run_rather_than_reading_it_as_still_broken(runs):
    """The mirror defect: promote read any non-zero as "the fix did not work"."""
    disp.reproduce(run_id="r1", target="file:x", finding_id="H1",
                   cmd=["python3", "-c", "import sys; sys.exit(3)"])
    rc = disp.promote(run_id="r1", target="file:x", finding_id="H1",
                      cmd=["python3", "-c", "pass", "|", "grep", "x"])
    assert rc == 4
    assert [r for r in _rows(runs) if r["verdict"] == "FALSIFIED"] == []


# ============================================================
# flag-as-fp writes ONE channel (2026-08-09 scrutiny, H2)
# ============================================================
# The plan said "one channel instead of two"; the first implementation kept
# writing the legacy `_fp_log.jsonl` beside the record and called the second
# write transitional. That legacy log is the one whose permanent emptiness
# justified deleting its aggregator in the same change.
def _load_flag_fp(tmp_path, monkeypatch):
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "scrutinize_flag_fp", root / "scripts" / "scrutinize-flag-fp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "scrutiny_dir", lambda p=tmp_path: p)
    return mod


def test_flag_fp_writes_only_the_record(tmp_path, monkeypatch, runs):
    mod = _load_flag_fp(tmp_path, monkeypatch)
    mod.append_records([{
        "scrutiny_id": "2026-08-09-x", "finding_id": "H1",
        "confidence": 88, "target_type": "trajectory",
    }])
    rows = _rows(runs)
    assert [(r["kind"], r["finding_id"], r["writer"]) for r in rows] == [
        ("fp_flag", "H1", "flag-fp")]
    assert not (tmp_path / "_fp_log.jsonl").exists()


def test_flag_fp_tally_counts_severity_from_the_record(tmp_path, monkeypatch, runs, capsys):
    mod = _load_flag_fp(tmp_path, monkeypatch)
    mod.append_records([
        {"scrutiny_id": "s1", "finding_id": "H1", "confidence": 90, "target_type": "file"},
        {"scrutiny_id": "s1", "finding_id": "M2", "confidence": 70, "target_type": "file"},
    ])
    mod.print_running_tally()
    out = capsys.readouterr().out
    assert "2 recorded" in out and "HIGH=1" in out and "MEDIUM=1" in out


def test_flag_fp_tally_on_an_empty_record(tmp_path, monkeypatch, runs, capsys):
    mod = _load_flag_fp(tmp_path, monkeypatch)
    mod.print_running_tally()
    assert "0 FPs recorded" in capsys.readouterr().out


def test_a_collection_error_is_not_a_reproduced_finding(tmp_path):
    """pytest exit 2 means the check never ran, so it may not be REPRODUCED.

    A test module with a bad import returns 2. Until the 2026-08-13 audit only 4
    and 5 were carved out, so a broken import was recorded as a reproduced
    finding for a check that never executed - the failure mode this guard exists
    to close, one exit code short of closed.
    """
    broken = tmp_path / "test_broken_import.py"
    broken.write_text("import a_module_that_does_not_exist\n\ndef test_x():\n    pass\n",
                      encoding="utf-8")
    run = disp._run([sys.executable, "-m", "pytest", str(broken), "-q"])
    assert run.exit_code == 2
    assert run.unusable is not None
    assert "no test ran" in run.unusable


def test_a_genuine_test_failure_is_still_usable(tmp_path):
    """Exit 1 is the only non-zero code that means the check ran and failed."""
    failing = tmp_path / "test_fails.py"
    failing.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    run = disp._run([sys.executable, "-m", "pytest", str(failing), "-q"])
    assert run.exit_code == 1
    assert run.unusable is None
