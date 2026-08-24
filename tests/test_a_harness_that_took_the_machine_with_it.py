"""The mutation harness bounds its own child, because once it did not.

`scripts/utils/mutation_harness.py` exists because every scratch harness before
it spawned `pytest` with a bare `subprocess.run(...)`: no timeout, no memory
cap. On 2026-08-24 one mutation turned a paging loop in
`scripts/gmail-reader.py` into an endless one. The stub server answered
forever, the pytest child reached 47 GB against a 48 GB WSL allocation, and the
kernel OOM-killer took the child and then all of `init.scope` -- the agent
session, the terminal manager, every shell. The distro re-initialised, which
wiped `/tmp` and destroyed 58 generated audit reports.

A mutation's whole job is to break the code under test, and "break" includes
"never returns" and "allocates without bound". So the bounds are the contract,
not a nicety, and these tests hold them:

- a child that hangs is CAUGHT, on the clock, and labelled as a timeout;
- a child that allocates past the cap dies as a MemoryError inside itself,
  never as an OOM-kill outside itself;
- the source file is restored after every mutation, including the ones that
  hang, that fail to apply, and that raise.

Each test builds a tiny throwaway repo under `tmp_path` and runs the harness
against it with `sys.executable`. Nothing here touches the real tree.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.utils.mutation_harness import run_mutations, run_tests


@pytest.fixture
def repo(tmp_path):
    """A minimal repo: one source module and one test that imports it."""
    (tmp_path / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "test_src.py").write_text(textwrap.dedent("""
        import importlib.util
        from pathlib import Path

        def _load():
            path = Path(__file__).resolve().parent / "src.py"
            spec = importlib.util.spec_from_file_location("src", str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        def test_value_is_one():
            assert _load().VALUE == 1
    """), encoding="utf-8")
    return tmp_path


def _run(repo, mutations, **kw):
    kw.setdefault("timeout", 30)
    kw.setdefault("memory_limit_gb", 2)
    return run_mutations(repo, ["test_src.py"], mutations,
                         python=sys.executable, **kw)


# ============================================================
# The bounds
# ============================================================

@pytest.mark.slow
def test_a_mutation_that_hangs_is_caught_on_the_clock(repo, capsys):
    """An endless loop is a behaviour change, so it counts as detection.

    Before the timeout existed this run never returned, and the child grew
    until the kernel killed the whole session.
    """
    mutations = [("H1", "src.py", "VALUE = 1",
                  "import time\nwhile True:\n    time.sleep(0.05)\nVALUE = 1")]
    assert _run(repo, mutations, timeout=5) == 0
    out = capsys.readouterr().out
    assert "caught (timeout)" in out, out
    assert "SURVIVED" not in out


@pytest.mark.slow
@pytest.mark.skipif(os.name != "posix", reason="RLIMIT_AS is POSIX only")
def test_a_mutation_that_allocates_without_bound_dies_inside_the_child(repo,
                                                                      capsys):
    """The child gets MemoryError; the machine does not get an OOM-killer."""
    mutations = [("M1", "src.py", "VALUE = 1",
                  'VALUE = 1\nBALLAST = bytearray(3 * 1024 ** 3)')]
    assert _run(repo, mutations, memory_limit_gb=1, timeout=60) == 0
    out = capsys.readouterr().out
    assert "caught" in out and "SURVIVED" not in out


def test_the_timeout_is_reported_distinctly_from_a_failed_assertion(repo,
                                                                    capsys):
    """A hang and a red test are both caught, and must not read the same."""
    mutations = [("F1", "src.py", "VALUE = 1", "VALUE = 2")]
    assert _run(repo, mutations) == 0
    out = capsys.readouterr().out
    assert "caught" in out
    assert "timeout" not in out, "a plain assertion failure is not a hang"


# ============================================================
# Restoration
# ============================================================

def test_the_source_is_restored_after_a_caught_mutation(repo):
    _run(repo, [("F1", "src.py", "VALUE = 1", "VALUE = 2")])
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not list(repo.glob("*.mutbak"))


@pytest.mark.slow
def test_the_source_is_restored_after_a_mutation_that_hung(repo):
    """The finally has to survive the timeout path too."""
    _run(repo, [("H1", "src.py", "VALUE = 1",
                 "import time\nwhile True:\n    time.sleep(0.05)")],
         timeout=5)
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not list(repo.glob("*.mutbak"))


def test_the_source_is_restored_after_a_missing_anchor(repo):
    _run(repo, [("X1", "src.py", "NOT PRESENT", "whatever")])
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not list(repo.glob("*.mutbak"))


def test_every_source_is_restored_across_a_multi_file_run(repo):
    (repo / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
    _run(repo, [("F1", "src.py", "VALUE = 1", "VALUE = 2"),
                ("F2", "other.py", "OTHER = 1", "OTHER = 2")])
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (repo / "other.py").read_text(encoding="utf-8") == "OTHER = 1\n"
    assert not list(repo.glob("*.mutbak"))


# ============================================================
# Verdicts
# ============================================================

def test_a_mutation_no_test_notices_is_a_survivor(repo, capsys):
    mutations = [("S1", "src.py", "VALUE = 1", "VALUE = 1\nUNUSED = 2")]
    assert _run(repo, mutations) == 1
    assert "SURVIVED" in capsys.readouterr().out


def test_a_missing_anchor_counts_as_a_survivor(repo, capsys):
    """A mutation that never applied proved nothing, so it is not a pass."""
    assert _run(repo, [("X1", "src.py", "NOT PRESENT", "whatever")]) == 1
    out = capsys.readouterr().out
    assert "ANCHOR MISSING" in out
    assert "anchor missing" in out


def test_a_red_baseline_stops_before_any_mutation(repo, capsys):
    """Mutating against a broken baseline reports noise as findings."""
    (repo / "src.py").write_text("VALUE = 99\n", encoding="utf-8")
    assert _run(repo, [("F1", "src.py", "VALUE = 99", "VALUE = 98")]) == 2
    out = capsys.readouterr().out
    assert "BASELINE FAIL" in out
    assert "F1" not in out
    assert (repo / "src.py").read_text(encoding="utf-8") == "VALUE = 99\n"


def test_a_clean_sweep_exits_zero(repo):
    assert _run(repo, [("F1", "src.py", "VALUE = 1", "VALUE = 2")]) == 0


# ============================================================
# run_tests on its own
# ============================================================

def test_run_tests_reports_pass_fail_and_timeout(repo):
    assert run_tests(repo, ["test_src.py"], timeout=30,
                     memory_limit_bytes=2 * 1024 ** 3,
                     python=sys.executable) == "pass"

    (repo / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert run_tests(repo, ["test_src.py"], timeout=30,
                     memory_limit_bytes=2 * 1024 ** 3,
                     python=sys.executable) == "fail"


@pytest.mark.slow
def test_run_tests_returns_timeout_rather_than_blocking(repo):
    (repo / "src.py").write_text(
        "import time\nwhile True:\n    time.sleep(0.05)\n", encoding="utf-8")
    assert run_tests(repo, ["test_src.py"], timeout=3,
                     memory_limit_bytes=2 * 1024 ** 3,
                     python=sys.executable) == "timeout"


def test_a_single_test_path_may_be_a_bare_string(repo):
    assert run_tests(repo, "test_src.py", timeout=30,
                     memory_limit_bytes=2 * 1024 ** 3,
                     python=sys.executable) == "pass"


# ============================================================
# The contract, in source
# ============================================================

def _library_source() -> str:
    path = Path(__file__).resolve().parent.parent / "scripts" / "utils" / \
        "mutation_harness.py"
    text = path.read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n")
                     if not ln.lstrip().startswith("#"))


def test_the_child_is_spawned_with_both_bounds():
    src = _library_source()
    assert "timeout=timeout" in src, "a child with no clock can run forever"
    assert "resource.setrlimit(resource.RLIMIT_AS" in src, (
        "a child with no memory cap can take the machine, not just itself"
    )
    assert "preexec_fn" in src, "the limit must land on the CHILD, not on us"
