#!/usr/bin/env python3
"""A test module put a directory on `sys.path` and never took it off again.

`sys.path` is process-global and an xdist worker runs many test modules in one
process, so an entry added at import time is added for every module collected
after it. When the directory is `<repo>/scripts`, every file in it becomes a
top-level name: `scripts/firecrawl.py` shadows the installed `firecrawl` SDK,
`scripts/setup.py` shadows nothing yet but could, and the victim is whichever
test happens to land later on that worker. The failure is order-dependent, so
the file that caused it passes on its own and the file that reported it looks
flaky.

Two real instances, both found on 2026-08-31:

  `tests/test_a_rule_that_classified_its_own_files_by_hand.py`
      module-level `sys.path.insert(0, str(ROOT / "scripts"))`, no removal
      anywhere, so `<repo>/scripts` stood for the rest of the worker. It broke
      `tests/test_impeccable_engine.py::test_cap1_default_run_does_not_invoke_
      the_deep_engine`, whose first assertion is exactly this leak guard, on
      every run where the two landed together. The fix is the repo ROOT plus
      `from scripts.utils.workspace import ...`, which is what the other six
      importers of that function already do.

  `tests/test_skill_creator_{description_loop_guards,eval_scratch_and_absence,
   run_eval_reports_a_dead_cli}.py`
      a `_load_shadowed` helper that inserted `<repo>/.claude/skills/skill-
      creator` and removed it in a `finally`, which LOOKS balanced. It is not:
      the script it loads runs its own `sys.path.insert` of the same directory
      at import, so two copies go on and `list.remove` takes one. The survivor
      puts a second `scripts/` package ahead of this repo's. The fix is to
      snapshot `sys.path[:]` and restore the whole list.

The guard below is behavioural, not a grep. It collects the entire suite in a
child interpreter and compares `sys.path` before and after, so it sees a leak
whatever syntax produced it - an insert, an append, a slice assignment, a
`.pth`, or an import side effect three modules deep. A source-text rule keyed
on `sys.path.insert` would have passed the skill-creator trio, which spells the
cleanup out.

`--collect-only` is the right depth for this: both instances leaked at IMPORT
time, and importing every module is precisely what collection does. A test body
that mutates `sys.path` and restores it under `monkeypatch` (several here do)
is correct and invisible to this guard, which is the intent.
"""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed interpreter, repo-owned driver script
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Collect a target, then report every `sys.path` entry that was not there
#: before. Run in a child so the residue cannot contaminate this process, and
#: through `pytest.main` rather than a nested `-m pytest` so the comparison is
#: against the child's own starting path.
_DRIVER = textwrap.dedent(
    """
    import json, sys
    import pytest

    baseline = set(sys.path)
    code = pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider",
                        *sys.argv[1:]])
    residue = [p for p in sys.path if p not in baseline]
    print("COLLECT_RC " + str(int(code)))
    print("SYSPATH_RESIDUE " + json.dumps(residue))
    """
)


def _collect_and_report(driver_dir: Path, cwd: Path, *targets: str
                        ) -> tuple[int, list[str]]:
    """Return (pytest collect rc, sys.path entries the collection left behind)."""
    driver = driver_dir / "collect_probe.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    # Scrub PYTEST_* so the child is a clean run rather than an echo of the
    # xdist worker this test is executing inside. Same discipline as
    # `scripts/utils/canopus_contract.pytest_child_env`, inlined to keep the
    # guard free of a dependency on the thing it audits.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    proc = subprocess.run(  # nosec B603 - fixed interpreter + generated driver
        [sys.executable, str(driver), *targets],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=900,
    )
    rc = None
    residue = None
    for line in proc.stdout.splitlines():
        if line.startswith("COLLECT_RC "):
            rc = int(line.split(" ", 1)[1])
        elif line.startswith("SYSPATH_RESIDUE "):
            residue = json.loads(line.split(" ", 1)[1])
    assert rc is not None and residue is not None, (
        "the probe did not report; stdout=%r stderr=%r" % (proc.stdout[-4000:],
                                                           proc.stderr[-4000:]))
    return rc, residue


@pytest.mark.slow
def test_collecting_the_whole_suite_leaves_sys_path_as_it_found_it(tmp_path):
    """The guard. Importing every test module must add nothing to `sys.path`.

    A module that needs the repo importable has `pythonpath = ["."]` in
    `pyproject.toml` doing it already; anything beyond that has to be undone
    before the module finishes importing.
    """
    rc, residue = _collect_and_report(tmp_path, ROOT, "tests")

    assert rc == 0, "the suite no longer collects cleanly; fix that first"
    assert residue == [], (
        "collecting the suite left these entries on sys.path, where they hold "
        "for every test that runs later in the same xdist worker: "
        f"{residue}. Find the module that adds one and restore the path before "
        "it finishes importing - snapshot `sys.path[:]` and assign it back, "
        "rather than `remove()`ing a single string, because the module you are "
        "loading may well have inserted the same directory itself."
    )


def test_the_guard_can_actually_see_a_leak(tmp_path):
    """The negative case, without which the test above is green over nothing.

    A synthetic module with the exact defect - a module-level insert of a
    directory that is never removed - must be REPORTED. If this fails, the
    guard above proves only that the probe is blind.
    """
    pkg = tmp_path / "leaky"
    pkg.mkdir()
    leaked = tmp_path / "some-directory-nothing-removes"
    leaked.mkdir()
    (pkg / "test_leaks_a_path_entry.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(leaked)!r})\n"
        "\n"
        "def test_nothing():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    rc, residue = _collect_and_report(tmp_path, tmp_path, "leaky")

    # Pytest puts the collected package's own parent on the path under
    # importlib mode; that is pytest's bookkeeping, not the module's. Only the
    # directory the module itself inserted is the subject here.
    assert rc == 0, "the synthetic module failed to collect at all"
    assert str(leaked) in residue, (
        "the probe did not report a module-level sys.path insert that is never "
        f"undone; it saw {residue}")


def test_the_guard_stays_quiet_on_a_module_that_cleans_up(tmp_path):
    """The other direction: a module that inserts and RESTORES must not be
    reported. Without this, a guard that reported every module unconditionally
    would still pass the negative case above."""
    pkg = tmp_path / "tidy"
    pkg.mkdir()
    borrowed = tmp_path / "borrowed-for-one-import"
    borrowed.mkdir()
    (pkg / "test_restores_the_path.py").write_text(
        "import sys\n"
        "_saved = sys.path[:]\n"
        f"sys.path.insert(0, {str(borrowed)!r})\n"
        "try:\n"
        "    pass\n"
        "finally:\n"
        "    sys.path[:] = _saved\n"
        "\n"
        "def test_nothing():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    rc, residue = _collect_and_report(tmp_path, tmp_path, "tidy")

    assert rc == 0
    assert str(borrowed) not in residue, (
        f"a module that restored sys.path was reported: {residue}")
