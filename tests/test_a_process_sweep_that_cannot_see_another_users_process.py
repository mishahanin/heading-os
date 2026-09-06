#!/usr/bin/env python3
"""What the process sweep cannot see, pinned as a decision instead of a gap.

`processes_in` asks the kernel which processes stand inside a directory by
reading `/proc/<pid>/cwd`. For a process this user does not own, that readlink
raises `EACCES`, the process is skipped, and a directory held open only by such
a process reads as EMPTY. The deletion guard's condition 3 rests on that sweep.

MEASURED 2026-09-06 on this machine, 73 processes: 37 answered the readlink, 36
raised `EACCES`, and 29 of those were owned by root. The obvious repair --
count them and say "36 could not be inspected" -- was considered and REJECTED,
for a reason that is itself the point: `EACCES` on the readlink means the cwd is
unknown, so not one of the 36 can be placed inside or outside the directory
being asked about. A count printed beside a yard's name would turn an unknown
into a suspicion, and there is nowhere honest to print it anyway, because the
guard says nothing at all on the path where the answer would matter.

So the behaviour stands and this file holds it still. What it asserts is the
distinction the module actually promises, which is a different one and is load
bearing: an unreadable PROCESS is skipped, while an unreadable `/proc` returns
None. The first says "not this one", the second says "I could not look", and a
caller that confuses them deletes a checkout with a session in it.

Both directions in every case: the invisible process is skipped AND the visible
one beside it is still found, so a sweep that had simply stopped at the first
failure would fail here.

Run: .venv/bin/python -m pytest \\
     tests/test_a_process_sweep_that_cannot_see_another_users_process.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.proc_cwd import processes_in  # noqa: E402


def _process(proc_root: Path, pid: int, cwd: Path | None, comm: str,
             environ: bytes | None = None) -> Path:
    """One entry in a bench `/proc`.

    `cwd=None` writes a REGULAR FILE where the symlink belongs, so `os.readlink`
    raises `OSError` exactly as it does for a process this user may not inspect.
    The errno differs (`EINVAL` rather than `EACCES`) and the module's handler
    does not distinguish them, which is what this bench is allowed to stand in
    for; a test cannot make the kernel deny it a link it owns.
    """
    task = proc_root / str(pid)
    task.mkdir(parents=True)
    (task / "comm").write_text(comm + "\n", encoding="utf-8")
    if cwd is None:
        (task / "cwd").write_text("not a symlink", encoding="utf-8")
    else:
        (task / "cwd").symlink_to(cwd)
    if environ is not None:
        (task / "environ").write_bytes(environ)
    return task


@pytest.fixture
def bench(tmp_path):
    yard = tmp_path / "yard"
    (yard / "scripts").mkdir(parents=True)
    return tmp_path, yard


def test_a_process_that_cannot_be_inspected_is_skipped_not_counted(bench):
    """The documented behaviour, and the one this file decided to keep."""
    tmp_path, yard = bench
    proc = tmp_path / "proc"
    _process(proc, 100, None, "someone-elses")
    _process(proc, 200, yard, "claude")

    found = processes_in(yard, proc_root=proc)

    assert found is not None, "an unreadable process turned into `could not look`"
    assert [p.pid for p in found] == [200], (
        "the sweep either counted a process whose cwd it never read, or stopped "
        "walking at the first one that refused it")


def test_the_walk_continues_past_it(bench):
    """The failing half of the same branch, asked with the order reversed.

    A sweep that returned at the first `OSError` passes the case above by luck
    when the unreadable entry happens to sort last.
    """
    tmp_path, yard = bench
    proc = tmp_path / "proc"
    _process(proc, 1, yard, "first-here")
    _process(proc, 2, None, "opaque")
    _process(proc, 3, yard / "scripts", "deep-inside")
    _process(proc, 4, None, "opaque-too")
    _process(proc, 5, yard, "last-here")

    found = processes_in(yard, proc_root=proc, nested=True)

    assert sorted(p.pid for p in found) == [1, 3, 5], [p.pid for p in found]


def test_a_yard_held_only_by_an_invisible_process_reads_as_empty(bench):
    """The consequence, written down rather than left for someone to discover.

    This is the case the deletion guard cannot see. It is asserted so that a
    future change claiming to close it has something that goes red.
    """
    tmp_path, yard = bench
    proc = tmp_path / "proc"
    _process(proc, 100, None, "someone-elses")
    _process(proc, 101, tmp_path, "elsewhere")

    assert processes_in(yard, proc_root=proc) == [], (
        "if this now finds something, the sweep gained a way to see a process "
        "it does not own, and the docstring's boundary is out of date")


def test_an_unreadable_proc_is_none_and_not_an_empty_list(tmp_path):
    """The distinction the module exists to keep. Never merge these two."""
    assert processes_in(tmp_path / "yard",
                        proc_root=tmp_path / "no-proc-here") is None


def test_an_environment_that_cannot_be_read_is_none_not_empty(bench):
    """The same distinction one level down, for the ownership clause.

    `env=None` means "could not read it" and `env={}` means "read it, the
    variable is not set". The deletion guard treats the first as FOREIGN, so
    collapsing them would exempt a process nobody could identify.
    """
    tmp_path, yard = bench
    proc = tmp_path / "proc"
    task = _process(proc, 300, yard, "no-environ")
    _process(proc, 301, yard, "has-environ", environ=b"HERDR_WORKSPACE_ID=w5W\0")
    assert not (task / "environ").exists()

    found = {p.pid: p.env for p in processes_in(
        yard, proc_root=proc, env_names=("HERDR_WORKSPACE_ID",))}

    assert found[300] is None, "an unreadable environment came back as empty"
    assert found[301] == {"HERDR_WORKSPACE_ID": "w5W"}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
