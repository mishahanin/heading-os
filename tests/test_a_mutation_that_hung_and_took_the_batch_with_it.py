"""A mutation that hung and took the batch with it, and an anchor that landed somewhere else.

Two defects in `scripts/utils/mutation_probe.py`, plus one in its sibling
`scripts/utils/mutation_harness.py`. All three are the module's own stated
failure mode: a verdict a reader trusts that was never measured against what its
label names.

**The hang.** `run_mutations` runs each mutation's command with a `timeout` and
did not catch `subprocess.TimeoutExpired`. The BASELINE run three lines above it
catches `SubprocessError`, which covers exactly that - the asymmetry was visible
in one screenful. A mutation exists to break the code, and "break" includes
"never return"; the sibling harness records a paging loop turned endless. When
one hung, the exception left the function, no `Result` was recorded for it, and
every remaining mutation in the batch was dropped: the caller got a traceback
where the signature promises `list[Result]`.

**The ambiguous anchor.** The edit is `current.replace(old, new, 1)`, which
patches the FIRST match. The `anchor not found` half of the check existed; the
`anchor found twice` half did not, so an anchor present in two functions was
patched in whichever came first and the verdict was read off code that was never
mutated. The sibling harness carries the measured incident: three mutations
aimed at one pair of functions landed in another pair and all three were
reported SURVIVED. This module exists to make that impossible.

**The unimportable harness.** `mutation_harness` opened with a bare
`import resource`, which is POSIX-only, so on the platforms its docstring
promises to degrade for ("on other platforms the wall clock is the only bound
and `run_mutations` says so once") the module could not be imported at all and
the note was unreachable. `_clear_pycache` shelled out to `find` with the same
blind spot, which would have made the wipe a silent no-op - and stale bytecode
is what fakes a caught mutation in the first place.
"""
from __future__ import annotations

import builtins
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.mutation_probe import (  # noqa: E402
    INVALID,
    KILLED,
    SURVIVED,
    Mutation,
    run_mutations,
)

PASS = [sys.executable, "-c", "raise SystemExit(0)"]
# The realistic shape: the command RUNS the file being mutated, so the baseline
# is green and only the mutation changes the outcome. A command that ignores the
# tree can only ever produce `baseline-red`.
RUN_TARGET = [sys.executable, "target.py"]
GREEN_TARGET = "default = 0\n\n\ndef a():\n    return default\n\n\nraise SystemExit(0)\n"


def _ok(_sources):
    return None


def _tree(tmp_path: Path, body: str) -> Path:
    (tmp_path / "target.py").write_text(body, encoding="utf-8")
    return tmp_path


# ============================================================
# A hang is a verdict, and the batch continues
# ============================================================

def test_a_hanging_mutation_returns_a_result_instead_of_raising(tmp_path):
    root = _tree(tmp_path, GREEN_TARGET)
    results = run_mutations(
        [Mutation("hangs", (("target.py", "raise SystemExit(0)",
                             "__import__('time').sleep(30)"),), _ok)],
        command=RUN_TARGET, root=root, timeout=2)
    assert [r.verdict for r in results] == [KILLED]
    assert "timeout" in results[0].detail


def test_a_hang_does_not_drop_the_mutations_queued_behind_it(tmp_path, monkeypatch):
    root = _tree(tmp_path, "alpha = 1\nbravo = 2\n")
    calls = {"n": 0}
    real_run = importlib.import_module("subprocess").run

    def fake_run(command, **kwargs):
        # The baseline passes; the first mutation hangs; the second is normal.
        if command is not PASS:
            raise AssertionError("unexpected command")
        calls["n"] += 1
        if calls["n"] == 2:
            import subprocess as sp
            raise sp.TimeoutExpired(cmd=command, timeout=1)
        return real_run(command, **kwargs)

    monkeypatch.setattr("scripts.utils.mutation_probe.subprocess.run", fake_run)
    results = run_mutations(
        [Mutation("first", (("target.py", "alpha = 1", "alpha = 9"),), _ok),
         Mutation("second", (("target.py", "bravo = 2", "bravo = 9"),), _ok)],
        command=PASS, root=root, timeout=1)
    assert [r.label for r in results] == ["first", "second"]
    assert results[0].verdict == KILLED and "timeout" in results[0].detail
    assert results[1].verdict == SURVIVED


def test_the_tree_is_restored_after_a_hang(tmp_path):
    root = _tree(tmp_path, GREEN_TARGET)
    run_mutations(
        [Mutation("hangs", (("target.py", "raise SystemExit(0)",
                             "__import__('time').sleep(30)"),), _ok)],
        command=RUN_TARGET, root=root, timeout=2)
    assert (root / "target.py").read_text(encoding="utf-8") == GREEN_TARGET


# ============================================================
# An ambiguous anchor is invalid, never survived and never killed
# ============================================================

def test_an_anchor_present_twice_is_invalid(tmp_path):
    root = _tree(tmp_path, "def a():\n    return default\n\n"
                           "def b():\n    return default\n")
    results = run_mutations(
        [Mutation("ambiguous", (("target.py", "return default", "return None"),), _ok)],
        command=PASS, root=root, timeout=30)
    assert results[0].verdict == INVALID
    assert "2 places" in results[0].detail
    assert results[0].trustworthy is False


def test_an_ambiguous_anchor_leaves_the_file_untouched(tmp_path):
    body = "def a():\n    return default\n\ndef b():\n    return default\n"
    root = _tree(tmp_path, body)
    run_mutations(
        [Mutation("ambiguous", (("target.py", "return default", "return None"),), _ok)],
        command=PASS, root=root, timeout=30)
    assert (root / "target.py").read_text(encoding="utf-8") == body


def test_an_absent_anchor_is_still_invalid(tmp_path):
    root = _tree(tmp_path, "value = 1\n")
    results = run_mutations(
        [Mutation("missing", (("target.py", "nowhere", "here"),), _ok)],
        command=PASS, root=root, timeout=30)
    assert results[0].verdict == INVALID
    assert "anchor not found" in results[0].detail


def test_a_unique_anchor_still_produces_a_real_verdict(tmp_path):
    root = _tree(tmp_path, GREEN_TARGET)
    survived = run_mutations(
        [Mutation("unique", (("target.py", "default = 0", "default = 1"),), _ok)],
        command=RUN_TARGET, root=root, timeout=30)
    killed = run_mutations(
        [Mutation("unique", (("target.py", "raise SystemExit(0)",
                              "raise SystemExit(1)"),), _ok)],
        command=RUN_TARGET, root=root, timeout=30)
    assert survived[0].verdict == SURVIVED and survived[0].trustworthy
    assert killed[0].verdict == KILLED and killed[0].trustworthy


# ============================================================
# The sibling harness imports where its docstring says it degrades
# ============================================================

HARNESS = "scripts.utils.mutation_harness"
_MISSING = object()


def _exec_harness_privately(spec):
    """Run the harness module body into an object `sys.modules` never sees.

    `importlib.import_module` cannot do this. It registers its result under
    `scripts.utils.mutation_harness`, so the test has to unregister the entry
    first and put it back after - and "put it back" restores whatever object
    was there BEFORE, which is a different object than the one just built.
    `importlib.reload` on the fresh object then raises `module ... not in
    sys.modules`, so the test passed alone and failed the moment any other file
    in the same process had already imported the harness (under xdist, most of
    them). MEASURED 2026-08-30: green alone, red as

        pytest tests/test_a_harness_that_took_the_machine_with_it.py \
               tests/test_a_mutation_that_hung_and_took_the_batch_with_it.py

    Executing the spec directly touches `sys.modules` not at all, so the reading
    is the same whichever files ran first.
    """
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_imports_without_the_posix_only_resource_module(monkeypatch):
    """Hide `resource` from the import system and run the module body fresh."""
    spec = importlib.util.find_spec(HARNESS)
    assert spec is not None and spec.loader is not None
    real_import = builtins.__import__
    registered_before = sys.modules.get(HARNESS, _MISSING)
    tried = []

    def blocked(name, *args, **kwargs):
        if name == "resource":
            tried.append(name)
            raise ImportError("no resource module on this platform")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as hidden:
        hidden.setattr(builtins, "__import__", blocked)
        hidden.delitem(sys.modules, "resource", raising=False)
        without = _exec_harness_privately(spec)

    # Not vacuous: the body really reached for `resource` and was refused, so
    # `None` is the fallback firing rather than an unconditional assignment.
    assert "resource" in tried
    assert without.resource is None

    # Same body, same process, `resource` reachable again: the fallback is the
    # platform's answer, not a permanent None.
    with_resource = _exec_harness_privately(spec)
    assert with_resource.resource is not None

    # And the test leaves the interpreter as it found it. Both halves matter:
    # a restored `__import__` (or every later import in this worker is routed
    # through a dead closure) and an untouched registration (or a later test
    # importing the harness gets a `resource is None` module on a POSIX host).
    assert builtins.__import__ is real_import
    assert sys.modules.get(HARNESS, _MISSING) is registered_before


def test_the_pycache_wipe_works_with_no_find_on_path(tmp_path, monkeypatch):
    from scripts.utils import mutation_harness as mh

    cache = tmp_path / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "stale.pyc").write_bytes(b"\x00")
    monkeypatch.setattr(mh.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        mh.subprocess, "run",
        lambda *a, **k: pytest.fail("shelled out with no `find` available"))
    mh._clear_pycache(tmp_path)
    assert not cache.exists()


def test_the_pycache_wipe_still_uses_find_when_it_is_there(tmp_path):
    from scripts.utils import mutation_harness as mh

    if not shutil.which("find"):
        pytest.skip("no `find` on this host, measured by shutil.which")
    cache = tmp_path / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "stale.pyc").write_bytes(b"\x00")
    mh._clear_pycache(tmp_path)
    assert not cache.exists()
