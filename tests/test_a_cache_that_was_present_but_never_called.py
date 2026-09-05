#!/usr/bin/env python3
"""A correct cache, a green suite, and a gate that never called it.

WHAT HAPPENED, 2026-09-05. `scripts/utils/content_scan_cache.py` was written,
tested and committed as `c9acf0d`, and the 53 lines of call site in
`scripts/content-guard.py` were lost before the commit: a one-byte experimental
edit to that file was undone with `git checkout scripts/content-guard.py`, which
reverts the WHOLE file, and the commit went out without it. The shipped tree
held a cache nothing called.

MEASURED on the committed tree, in HELM: cold 77.52 s, warm 66.20 s. No
speed-up. On the same file with the wiring restored, and on a loaded box so the
absolute numbers are not comparable to those: cold 82.21 s, warm 8.27 s.

WHY THIS FILE EXISTS BESIDE THE OTHER ONE.
`tests/test_a_leak_scan_that_reproved_a_tree_it_had_already_proved.py` did catch
it -- 5 of its 27 went red on the broken tree, and the premise that all 27 passed
is not what pytest reports. But 4 of those 5 caught it by reading a SENTENCE out
of stdout ("N of M unchanged since a clean scan"), and a sentence is the cheapest
thing in the system to satisfy. A gate that printed that line from a counter it
incremented without ever consulting the store would pass every one of them while
scanning all 2330 files, which is precisely the shape of the defect being
guarded: the cache is present, the words about it are present, and the work still
happens.

So this file asserts the WORK, not the words. It counts calls to
`Denylist.scan_text` -- the function the whole cache exists to avoid, and the
function `content_scan_cache`'s own docstring measures at 98 s of the gate's wall
clock -- made by the real `scripts/content-guard.py` running as a real
subprocess. The count is taken from inside that subprocess by a `sitecustomize`
the test puts on its `PYTHONPATH`, so nothing in the gate is imported, stubbed or
re-implemented here.

WHAT IS NOT CLAIMED. This does not assert the files were not READ. They are, on
every run, because the cache key is a digest of their content and a digest needs
the bytes; `content-guard.py`'s docstring states the same. Reading 2330 files
costs 0.2 s and scanning them costs 98 s, so the read is not what was ever worth
saving. A test asserting "the files are not re-read" would be asserting a
property the design does not have and should not have.

MUTATION-VERIFIED 2026-09-05: with the `cache.is_clean(...)` branch deleted from
`scripts/content-guard.py` -- the exact state `c9acf0d` shipped --
`test_the_second_run_over_an_unchanged_tree_does_no_scanning` fails with 3 scans
where it requires 0. The source was restored from a digest-checked backup.

Run: python3 -m pytest tests/test_a_cache_that_was_present_but_never_called.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GUARD = ROOT / "scripts" / "content-guard.py"

#: The invented company the sandbox overlay is built around, matching the
#: sibling gate tests. Fictional by construction: the engine carries no real
#: entity, so a gate test must invent the thing it is driven with.
ENTITY = "Spectre Holdings"

#: How many clean files the sandbox holds. Asserted OUTSIDE every loop below,
#: per development-standards obligation 7: a scan count of zero is the PASSING
#: value of the central assertion here, so an empty corpus would satisfy it
#: while measuring nothing at all.
CORPUS = 3

#: Imported by the child interpreter at startup, before `content-guard.py`'s
#: `__main__` runs, so the class is already wrapped by the time the gate builds
#: its denylist. It appends one line per `scan_text` call.
#:
#: The wrapper does NOT change what the scan returns and does not touch any file
#: the scanner key is taken over: it lives in the pytest tmp tree, outside the
#: repository, so `_repo_module_files` filters it out of the closure and the key
#: is identical with and without this probe. That is what makes a count taken
#: under instrumentation a count of the uninstrumented run.
_SITECUSTOMIZE = '''
import os
import sys

sys.path.insert(0, os.environ["PROBE_REPO_ROOT"])
_ledger = os.environ["PROBE_LEDGER"]

from scripts.utils.content_denylist import Denylist

_real = Denylist.scan_text


def _counted(self, text):
    with open(_ledger, "a", encoding="utf-8") as handle:
        handle.write("scan\\n")
    return _real(self, text)


Denylist.scan_text = _counted
'''


@pytest.fixture()
def probe(tmp_path):
    """A `sitecustomize` that counts scans, and the ledger it writes to."""
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    return probe_dir, tmp_path / "scans.log"


@pytest.fixture()
def sandbox(tmp_path, probe):
    """A throwaway engine + overlay whose gate runs under the scan counter."""
    probe_dir, ledger = probe

    engine = tmp_path / "engine"
    (engine / ".claude").mkdir(parents=True)
    (engine / "CLAUDE.md").write_text("# marker\n", encoding="utf-8")
    (engine / "config").mkdir()
    shutil.copy2(ROOT / "config" / "routing-map.yaml",
                 engine / "config" / "routing-map.yaml")
    (engine / "docs").mkdir()

    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "content-denylist.yaml").write_text(
        f"companies:\n  - {ENTITY}\n", encoding="utf-8")

    env = dict(os.environ,
               WORKSPACE_ROOT=str(engine),
               HEADING_OS_DATA=str(data),
               PYTHONPATH=str(probe_dir),
               PROBE_REPO_ROOT=str(ROOT),
               PROBE_LEDGER=str(ledger))
    return engine, data, env, ledger


def _run(sandbox, *rels, extra=()):
    engine, data, env, _ledger = sandbox
    return subprocess.run(
        [sys.executable, str(GUARD), *extra, "--files", *rels,
         "--data-root", str(data)],
        capture_output=True, text=True, timeout=300, check=False,
        cwd=str(engine), env=env)


def _scans(sandbox) -> int:
    """How many times the gate called `scan_text` since the last reset."""
    _engine, _data, _env, ledger = sandbox
    if not ledger.exists():
        return 0
    return len(ledger.read_text(encoding="utf-8").split())


def _reset(sandbox) -> None:
    _engine, _data, _env, ledger = sandbox
    ledger.unlink(missing_ok=True)


def _seed(sandbox) -> list[str]:
    engine, _data, _env, _ledger = sandbox
    rels = []
    for index in range(CORPUS):
        rel = f"docs/file{index}.md"
        (engine / rel).write_text(f"Ordinary prose {index}.\n", encoding="utf-8")
        rels.append(rel)
    return rels


# ============================================================
# The probe measures something before anything is concluded from it
# ============================================================

def test_the_probe_sees_the_scans_of_a_cold_run(sandbox):
    """The experiment before the measurement.

    Every assertion below is about a scan count, so a probe that silently
    counted nothing would make all of them pass over a gate doing all the work.
    """
    rels = _seed(sandbox)
    _reset(sandbox)

    proc = _run(sandbox, *rels)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped" not in proc.stdout, proc.stdout
    assert _scans(sandbox) == CORPUS, (
        f"a cold run over {CORPUS} files should scan {CORPUS} of them; the "
        f"probe counted {_scans(sandbox)}")


# ============================================================
# The finding: present but never called
# ============================================================

def test_the_second_run_over_an_unchanged_tree_does_no_scanning(sandbox):
    """THE GUARD. A cache that is present but unwired fails exactly here.

    Not "the clean line mentions reuse" and not "a cache module is importable":
    the observable consequence, which is that the expensive function the cache
    exists to avoid is not entered at all.
    """
    rels = _seed(sandbox)
    _run(sandbox, *rels)
    _reset(sandbox)

    proc = _run(sandbox, *rels)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _scans(sandbox) == 0, (
        f"the tree did not change, so nothing needed scanning, but the gate "
        f"scanned {_scans(sandbox)} file(s). The verdict cache is present and "
        f"is not being consulted.")


def test_only_the_changed_file_is_scanned_again(sandbox):
    """The count tracks reality rather than merely collapsing to zero.

    A gate that skipped the scan unconditionally would pass the test above and
    ship every leak. One byte changes in one file, and exactly one file is
    scanned.
    """
    engine, _data, _env, _ledger = sandbox
    rels = _seed(sandbox)
    _run(sandbox, *rels)
    (engine / rels[1]).write_text("Ordinary prose, edited.\n", encoding="utf-8")
    _reset(sandbox)

    proc = _run(sandbox, *rels)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _scans(sandbox) == 1, (
        f"one of {CORPUS} files changed, so exactly one needed scanning; the "
        f"probe counted {_scans(sandbox)}")


def test_the_skipped_scan_is_not_a_skipped_verdict(sandbox):
    """The reused verdict still has to be a verdict.

    A file proved clean, then a real-entity token written into it. The bytes
    moved, so the row is out of reach, the file is scanned, and the gate blocks.
    A cache keyed on anything cheaper -- a path, an mtime, a "seen it" set --
    reports clean here and ships the leak.
    """
    engine, _data, _env, _ledger = sandbox
    rels = _seed(sandbox)
    assert _run(sandbox, *rels).returncode == 0
    (engine / rels[0]).write_text(
        f"A note about {ENTITY}.\n", encoding="utf-8")
    _reset(sandbox)

    proc = _run(sandbox, *rels)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BLOCKED" in proc.stdout, proc.stdout
    assert _scans(sandbox) == 1, (
        f"only the edited file needed scanning; the probe counted "
        f"{_scans(sandbox)}")


def test_no_cache_really_does_scan_everything_again(sandbox):
    """The other direction, and the control on the zero above.

    If `--no-cache` also counted zero, the zero in the guard would be a broken
    probe rather than a working cache.
    """
    rels = _seed(sandbox)
    _run(sandbox, *rels)
    _reset(sandbox)

    proc = _run(sandbox, *rels, extra=("--no-cache",))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _scans(sandbox) == CORPUS, (
        f"--no-cache must re-prove every file; the probe counted "
        f"{_scans(sandbox)} of {CORPUS}")


def test_a_scanner_change_re_scans_every_file(sandbox):
    """The scanner-code half of the key, asserted as work rather than as a hex string.

    The denylist token set is half of `scanner_key`. Adding a company to the
    overlay must make every previously-clean verdict unreachable, so the warm
    run does the full cold amount of work again.
    """
    engine, data, _env, _ledger = sandbox
    rels = _seed(sandbox)
    _run(sandbox, *rels)
    (data / "config" / "content-denylist.yaml").write_text(
        f"companies:\n  - {ENTITY}\n  - Meridian Systems\n", encoding="utf-8")
    _reset(sandbox)

    proc = _run(sandbox, *rels)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _scans(sandbox) == CORPUS, (
        f"the scanner changed, so every file must be re-proved; the probe "
        f"counted {_scans(sandbox)} of {CORPUS}")
