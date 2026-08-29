#!/usr/bin/env python3
"""`crm_autolog.atomic_write` gave every writer of a record the same tmp name.

Shard `scripts-utils-01-p2`, finding 2, of the 2026-08-23 engine audit. The
temp path was `path.with_suffix(path.suffix + ".tmp")` - one fixed name per
target file, so `james-bond.md` and `james-bond.md` from two processes both
resolved to `james-bond.md.tmp`. `os.replace` is atomic with respect to the
TARGET; it says nothing about two writers sharing the source. B truncates and
rewrites the scratch file while A still believes it owns it, so A's rename
installs B's half-written bytes over the contact record, or raises
`FileNotFoundError` because B renamed the scratch file away first. That
exception escapes `log_outbound`, which never raises on any other path.

Two writers is the ordinary case, not a contrived one. `sync-exchange.py` calls
`bump_inbound` on a schedule, and `send-email.py` calls `log_outbound` from the
operator's terminal; nothing coordinates them. `daemon_heartbeat.py` and
`dead_letter.py` in the same tree already use `tempfile.mkstemp` for this, so
the fixed name was a lapse rather than a house convention.

Measured 2026-08-29: over the operator's live corpus of 334 records this defect
leaves no trace to count, because a lost write looks exactly like a write that
never happened. It is bound below by simulation instead.

Fixed 2026-08-29.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import crm_autolog  # noqa: E402


@pytest.fixture
def record(tmp_path):
    p = tmp_path / "james-bond.md"
    p.write_text("---\nname: James Bond\nlast_touch: 2026-01-01\n---\n\nbody\n",
                 encoding="utf-8")
    return p


def test_a_second_writer_does_not_steal_the_first_writers_scratch_file(record):
    """The core defect: the scratch path must not be a function of the target.

    Writer A is suspended mid-write. Writer B runs to completion, which under
    the fixed name removed the very file A is about to rename. A then finishes.
    """
    seen: list[Path] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen.append(Path(src))
        return real_replace(src, dst)

    # A writes its scratch file but has not renamed yet. The suspension is a
    # NO-OP replace, not a raised exception: `atomic_write` unlinks its own tmp
    # file on any exception, which is correct behaviour and would delete the
    # very file this test needs to still be there. Returning without renaming
    # is the faithful shape of "A is between the write and the rename".
    a_paths: list[Path] = []

    def capture_only(src, dst):
        a_paths.append(Path(src))          # A's scratch file, left in place
        return None

    os.replace = capture_only
    try:
        crm_autolog.atomic_write(record, "A wrote this\n")
    finally:
        os.replace = real_replace

    assert len(a_paths) == 1
    a_tmp = a_paths[0]
    assert a_tmp.exists(), "writer A's scratch file vanished before its rename"
    assert a_tmp.read_text(encoding="utf-8") == "A wrote this\n"

    # B now runs a complete write of the same record.
    os.replace = spy_replace
    try:
        crm_autolog.atomic_write(record, "B wrote this\n")
    finally:
        os.replace = real_replace

    assert seen == [seen[0]]
    assert seen[0] != a_tmp, (
        "B used the same scratch path as the in-flight writer A; under the old "
        "fixed `<name>.md.tmp` these are one file and A's rename installs B's "
        "bytes or dies on FileNotFoundError"
    )
    assert a_tmp.exists(), "B's write destroyed writer A's scratch file"
    assert record.read_text(encoding="utf-8") == "B wrote this\n"

    a_tmp.unlink()


def test_two_sequential_writes_do_not_reuse_one_scratch_name(record):
    """Every call gets its own scratch path, not just concurrent ones."""
    names: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        names.append(Path(src).name)
        return real_replace(src, dst)

    os.replace = spy
    try:
        crm_autolog.atomic_write(record, "one\n")
        crm_autolog.atomic_write(record, "two\n")
    finally:
        os.replace = real_replace

    assert len(names) == 2
    assert names[0] != names[1], f"both writes used {names[0]}"
    assert record.read_text(encoding="utf-8") == "two\n"


def test_the_scratch_file_stays_beside_the_record(record):
    """A tmp file on another filesystem makes `os.replace` non-atomic."""
    captured: list[Path] = []
    real_replace = os.replace

    def spy(src, dst):
        captured.append(Path(src))
        return real_replace(src, dst)

    os.replace = spy
    try:
        crm_autolog.atomic_write(record, "x\n")
    finally:
        os.replace = real_replace

    assert captured[0].parent == record.parent


def test_a_failed_write_leaves_no_scratch_file_behind(record, monkeypatch):
    """mkstemp creates eagerly, so the error path has to clean up after itself."""
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        crm_autolog.atomic_write(record, "never lands\n")

    leftovers = [p.name for p in record.parent.iterdir() if p != record]
    assert leftovers == [], f"scratch files left behind: {leftovers}"


def test_the_records_permissions_survive_the_write(record):
    """mkstemp opens 0600 and a rename carries the source's mode with it."""
    record.chmod(0o644)
    crm_autolog.atomic_write(record, "still readable\n")
    assert oct(record.stat().st_mode & 0o777) == "0o644"


def test_the_write_is_still_the_content_it_was_given(record):
    """Anchor: a change that stopped writing at all would pass the tests above."""
    crm_autolog.atomic_write(record, "---\nname: James Bond\n---\n\nnew body\n")
    assert record.read_text(encoding="utf-8").endswith("new body\n")
