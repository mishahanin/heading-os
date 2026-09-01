"""Session transcripts are archived off the harness's own 30-day clock.

Why this exists. Claude Code deletes transcripts under `~/.claude/projects/`
after `cleanupPeriodDays`, which defaults to 30 and was unset here. Measured
2026-08-22: of 258 Chronicle entries, 177 (69%) already pointed at a transcript
file that no longer existed, and the oldest surviving one was dated 2026-07-22 —
exactly the 30-day edge. The Chronicle entry keeps the DECISION; the transcript
is the only place the reasoning behind it survives. So 69% of "how did we get
here" was already unrecoverable, and one more day of it went every day.

The retention window was raised the same day, which stops the loss but does not
protect it: the transcripts live outside both repositories, so no git and no
`push-all.py` touches them, and a dead disk still takes everything.

This archiver copies each finished transcript into the DATA overlay, compressed,
where the normal backup already runs. It is append-only by construction: a
finished transcript never changes, so an archived file is never rewritten.
"""
import calendar
import gzip
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

# Loaded by path, not imported: the script is a standalone CLI, so the workspace
# naming convention gives it a hyphen, which is not a legal module name.
_spec = importlib.util.spec_from_file_location(
    "archive_transcripts_mod", WORKSPACE / "scripts" / "archive-transcripts.py"
)
arch = importlib.util.module_from_spec(_spec)
sys.modules["archive_transcripts_mod"] = arch
_spec.loader.exec_module(arch)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake transcript source and a fake DATA root."""
    source = tmp_path / "projects" / "-some-workspace"
    source.mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(arch, "transcript_dir", lambda: source)
    monkeypatch.setattr(arch, "archive_root", lambda: data / "chronicle" / "transcripts")
    return source, data / "chronicle" / "transcripts"


def _write(source: Path, name: str, lines: int = 3, when: str = "2026-08-01") -> Path:
    """A transcript whose FIRST LINE is dated `when`, and whose mtime matches.

    The first line is what decides the archive path, because it is the one date
    that never moves. The mtime is set too, so the settle-window tests can drive
    it independently.
    """
    import calendar
    import os

    path = source / f"{name}.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"timestamp": f"{when}T10:0{i}:00Z", "n": i})
            for i in range(lines)
        ),
        encoding="utf-8",
    )
    stamp = calendar.timegm(tuple(int(p) for p in when.split("-")) + (12, 0, 0, 0, 0, 0))
    os.utime(path, (stamp, stamp))
    return path


def test_archives_a_transcript_compressed_and_readable(tree):
    source, dest = tree
    src = _write(source, "aaaa1111", lines=4)

    result = arch.archive(now=1_800_000_000.0)

    assert result["archived"] == 1
    out = next(dest.rglob("*.jsonl.gz"))
    assert out.name.startswith("2026-08-01-aaaa1111")
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert fh.read() == src.read_text(encoding="utf-8")


def test_the_archive_is_dated_so_a_year_is_browsable(tree):
    source, dest = tree
    _write(source, "bbbb2222", when="2026-07-15")
    arch.archive(now=1_800_000_000.0)
    out = next(dest.rglob("*.jsonl.gz"))
    assert out.parent.name == "2026", f"expected a year directory, got {out.parent}"


def test_a_second_run_does_not_rewrite_an_already_archived_file(tree):
    source, dest = tree
    _write(source, "cccc3333")

    first = arch.archive(now=1_800_000_000.0)
    out = next(dest.rglob("*.jsonl.gz"))
    stamp = out.stat().st_mtime_ns

    second = arch.archive(now=1_800_000_000.0)

    assert first["archived"] == 1
    assert second["archived"] == 0 and second["skipped"] == 1
    assert out.stat().st_mtime_ns == stamp, "an unchanged transcript was rewritten"


def test_a_transcript_that_grew_is_re_archived_in_place(tree):
    """A resumed session replaces its archive; it must not leave a second one.

    Resuming rewrites the mtime. While the archive path was derived from the
    mtime, the longer transcript landed under a NEW date and the first,
    truncated copy stayed behind forever — two archives for one session, the
    older one silently wrong. The path now comes from the session's start
    timestamp, which never moves.
    """
    source, dest = tree
    src = _write(source, "dddd4444", lines=2, when="2026-08-01")
    arch.archive(now=1_800_000_000.0)

    src.write_text(src.read_text(encoding="utf-8") + "\n" + json.dumps({"n": 99}),
                   encoding="utf-8")
    # TIME BOMB DEFUSED 2026-08-30. `write_text` stamps the file with the REAL
    # wall clock while `now` is pinned at 1_800_000_000.0 (2027-01-15 UTC), so
    # the settle window (`now - mtime >= SETTLE_SECONDS`) was satisfied only
    # because real time still trailed the fake `now`. Once the clock passes
    # `1_800_000_000 - SETTLE_SECONDS` the grown transcript reads as "too
    # fresh", `result["archived"]` becomes 0, and this test fails on a
    # predictable date for a reason unrelated to any code change. Every other
    # test in this file pins the mtime with `os.utime`; this one did not.
    #
    # The 2026-08-30 repair stamped it back to the SAME 2026-08-01 noon the
    # fixture uses, which defused the bomb and disarmed the test with it: with
    # the mtime and the first-line timestamp agreeing, both possible sources for
    # the archive path give 2026-08-01, so the single-archive assertion below
    # could no longer tell them apart. That is this file's central claim.
    # MEASURED 2026-09-01: `_session_date` reverted to the mtime left the whole
    # file green at 12 passed. The stamp now MOVES, exactly as resuming a real
    # session moves it, while staying pinned (no wall clock) and comfortably
    # settled: 2026-08-20 is months before `now - SETTLE_SECONDS`.
    grown_stamp = calendar.timegm((2026, 8, 20, 12, 0, 0, 0, 0, 0))
    assert 1_800_000_000.0 - grown_stamp >= arch.SETTLE_SECONDS, (
        "the re-stamp must stay inside the settle window, or this test measures "
        "the window instead of the archive path")
    os.utime(src, (grown_stamp, grown_stamp))
    result = arch.archive(now=1_800_000_000.0)

    assert result["archived"] == 1
    archives = list(dest.rglob("*.jsonl.gz"))
    assert len(archives) == 1, f"one session left {len(archives)} archives: {archives}"
    assert archives[0].name == "2026-08-01-dddd4444.jsonl.gz"
    with gzip.open(archives[0], "rt", encoding="utf-8") as fh:
        assert '"n": 99' in fh.read()


def test_the_archive_path_follows_the_session_start_not_the_mtime(tree):
    """The claim the whole file rests on, asked directly.

    `_write` sets the first line's timestamp AND the mtime to the same date, so
    every other test here passes whichever of the two `_session_date` reads.
    This one drives them apart, which is exactly what resuming a session does:
    the transcript still STARTED on 2026-08-01 and was last written on
    2026-08-20. If the path came from the mtime the archive would land under a
    second date and the earlier, truncated copy would stay behind forever.
    """
    source, dest = tree
    src = _write(source, "7777cccc", when="2026-08-01")
    resumed = calendar.timegm((2026, 8, 20, 12, 0, 0, 0, 0, 0))
    os.utime(src, (resumed, resumed))

    result = arch.archive(now=1_800_000_000.0)

    assert result["archived"] == 1, result
    out = next(dest.rglob("*.jsonl.gz"))
    assert out.name == "2026-08-01-7777cccc.jsonl.gz", (
        "the archive was dated from the mtime (2026-08-20) rather than the "
        f"session's first timestamp (2026-08-01): {out.name}")
    assert out.parent.name == "2026"


def test_the_live_session_is_left_alone(tree):
    """A transcript written seconds ago is still being appended to.

    Archiving it would store a half-conversation and then re-store it on the next
    run, so the settle window keeps the archive one-write-per-session.
    """
    source, dest = tree
    src = _write(source, "eeee5555")
    import os
    os.utime(src, (1_800_000_000.0, 1_800_000_000.0))

    result = arch.archive(now=1_800_000_000.0 + 60)  # one minute old

    assert result["archived"] == 0
    assert result["too_fresh"] == 1
    assert not list(dest.rglob("*.gz"))


def test_a_settled_transcript_is_archived(tree):
    source, dest = tree
    src = _write(source, "ffff6666")
    import os
    os.utime(src, (1_800_000_000.0, 1_800_000_000.0))

    result = arch.archive(now=1_800_000_000.0 + arch.SETTLE_SECONDS + 1)

    assert result["archived"] == 1


def test_dry_run_writes_nothing(tree):
    source, dest = tree
    _write(source, "9999aaaa")
    result = arch.archive(now=1_800_000_000.0, dry_run=True)
    assert result["archived"] == 1, "a dry run still reports what it would do"
    assert not dest.exists() or not list(dest.rglob("*.gz"))


def test_an_unreadable_transcript_does_not_stop_the_others(tree, monkeypatch):
    """One bad file must not cost the whole run; the failure is counted, not hidden."""
    source, dest = tree
    _write(source, "aaaa0001")
    bad = _write(source, "bbbb0002")

    real_open = arch.gzip.open

    def explode(path, *a, **kw):
        if "bbbb0002" in str(path):
            raise OSError("disk on fire")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(arch.gzip, "open", explode)
    result = arch.archive(now=1_800_000_000.0)

    assert result["archived"] == 1
    assert result["failed"] == 1
    assert len(list(dest.rglob("*.gz"))) == 1


def test_empty_source_is_not_an_error(tree):
    result = arch.archive(now=1_800_000_000.0)
    assert result == {"archived": 0, "skipped": 0, "too_fresh": 0, "failed": 0}


def test_the_archive_lands_in_the_data_overlay_never_the_engine():
    """Transcripts carry everything, including personal threads. DATA only.

    Skipped where there is no overlay, because there the archiver REFUSES and
    the test below is what proves it. Until 2026-08-26 this test asserted
    unconditionally and failed every CI run from 2026-08-22 onward: the runner
    has no overlay, `get_data_root()` fell back to `<engine>/examples`, and the
    guard fired correctly on a real defect nobody had read the log for.
    """
    from scripts.utils.workspace import (
        data_root_is_demo,
        get_data_root,
        get_workspace_root,
    )

    if data_root_is_demo():
        pytest.skip("no private overlay: the archiver refuses, see the test below")

    root = arch.archive_root()
    assert str(root).startswith(str(get_data_root()))
    assert not str(root).startswith(str(get_workspace_root()) + "/"), (
        "an archived transcript must never land in the engine tree"
    )


def test_the_archiver_refuses_when_there_is_no_private_overlay(monkeypatch):
    """The other half, and the one that runs everywhere.

    `get_data_root()`'s documented last resort is `<workspace_root>/examples`,
    inside the engine clone, and the engine repository is public. An archiver
    that followed it would copy whole session transcripts into the tree that
    gets pushed. So the refusal is the behaviour, not an accident of the
    environment, and it is asserted rather than skipped.
    """
    from scripts.utils import paths as _paths
    from scripts.utils.workspace import DataRootError

    monkeypatch.setattr(_paths, "data_root_is_demo", lambda: True)

    with pytest.raises(DataRootError) as refused:
        arch.archive_root()
    assert "examples" in str(refused.value).lower() or "data folder" in str(refused.value).lower()


def test_the_cli_says_why_it_refused_instead_of_raising(monkeypatch, capsys):
    """A timer reads the exit code, never the traceback."""
    from scripts.utils.workspace import DataRootError

    def _refuse():
        raise DataRootError("No private data folder found - running on read-only examples.")

    monkeypatch.setattr(arch, "archive_root", _refuse)

    assert arch.main([]) == 2
    err = capsys.readouterr().err
    assert "archive-transcripts:" in err
    assert "read-only examples" in err
