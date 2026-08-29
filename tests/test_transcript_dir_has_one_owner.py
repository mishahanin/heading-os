"""The project-slug rule lives in ONE place, and it refuses off POSIX.

Found by the 2026-08-23 engine audit, which reported the Windows half. The
duplication underneath it is the reason the Windows half existed.

**The slug rule had three copies.** `scripts/utils/checkpoint_paths.py` owns
`transcript_dir(project)` and its docstring says why: "it lives here rather
than in either caller because the second copy of a path-mangling rule is the one
that stops being fixed." `scripts/archive-transcripts.py` then wrote a third
copy anyway, with a docstring pointing at a FOURTH place (`scripts/calibrate.py`)
as the authority. The prediction in the shared docstring came true inside its own
repository.

**Every copy was wrong on Windows.** The rule replaces `/` and `.` with `-`.
A Windows workspace path is `C:\\Users\\...`: the backslashes survive and the
drive colon survives, so `Path.home()/".claude"/"projects"/slug` names a
directory that cannot exist. `archive()` then hits
`if not source_dir.is_dir(): return counts` and reports success-zero on every
run; `--status` prints `live 0 file(s)`. The script exits 0 while archiving
nothing, indefinitely -- which is the silent transcript loss it was written to
prevent. Its own header records that transcripts live where no backup reaches.

The correct Windows slug is not something this repository can verify, so the
resolver **returns None rather than guessing one**, the same choice
`.claude/hooks/memory-reconcile.py:_native_from_hook` made the same day. Each
caller says so out loud instead of reporting an empty directory as an empty
archive: a monitor that guesses is worse than one that abstains, and one that
abstains silently is worse than both.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ARCH = _load("scripts/archive-transcripts.py", "archive_transcripts_under_test")


# --- one owner ---------------------------------------------------------------

def test_only_one_module_implements_the_slug_rule():
    """Any file that mangles a path into the harness project-dir name is a copy.

    Scoped to the two-replacement form; `scripts/census.py` mangles a corpus
    name with a single replacement for a different purpose and is not a copy.
    """
    paths = [
        path
        for path in tracked_paths(("scripts/**/*.py", ".claude/**/*.py"))
        if path.name != "checkpoint_paths.py"
    ]
    # "no offenders" is green over zero files, so a renamed directory or a
    # changed suffix would switch this guard off without failing anything.
    # Measured 2026-08-26: 428 files across the two patterns.
    assert len(paths) >= 260, f"the scan collapsed to {len(paths)} files"
    offenders = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if '.replace("/", "-").replace(".", "-")' in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "the project-slug rule is duplicated outside its owner "
        "(scripts/utils/checkpoint_paths.py):\n  " + "\n  ".join(offenders)
    )


def test_the_archiver_uses_the_shared_resolver():
    src = (ROOT / "scripts" / "archive-transcripts.py").read_text(encoding="utf-8")
    assert "checkpoint_paths" in src, (
        "archive-transcripts.py no longer imports the shared resolver"
    )


def test_the_owner_still_exists():
    """A guard that points at a moved owner proves nothing."""
    cp = _load("scripts/utils/checkpoint_paths.py", "checkpoint_paths_under_test")
    assert callable(cp.transcript_dir)


# --- it resolves on POSIX ----------------------------------------------------

def test_it_resolves_a_real_directory_name_on_posix():
    cp = _load("scripts/utils/checkpoint_paths.py", "cp_posix")
    got = cp.transcript_dir(Path("/home/x/ai/.heading-os"))
    assert got is not None
    assert got.name == "-home-x-ai--heading-os", got
    assert got.parent == Path.home() / ".claude" / "projects"


def test_the_archiver_agrees_with_the_owner():
    cp = _load("scripts/utils/checkpoint_paths.py", "cp_agree")
    from scripts.utils.workspace import get_workspace_root
    assert ARCH.transcript_dir() == cp.transcript_dir(get_workspace_root())


# --- it refuses off POSIX ----------------------------------------------------

_PROBE = """
import os, sys, importlib.util, shutil, pathlib
os.name = 'nt'
spec = importlib.util.spec_from_file_location('cp', {path!r})
m = importlib.util.module_from_spec(spec); sys.modules['cp'] = m
spec.loader.exec_module(m)
print('RESULT=' + repr(m.transcript_dir(pathlib.PurePosixPath('/home/x/w'))))
"""


def test_the_resolver_refuses_rather_than_guessing_off_posix():
    """Run in a SUBPROCESS. `os.name` cannot be patched in place: `pathlib`
    picks WindowsPath off it and `shutil` imports `nt` off it, so the patch has
    to land after both are loaded and die with the child."""
    probe = _PROBE.format(path=str(ROOT / "scripts" / "utils" / "checkpoint_paths.py"))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, f"probe failed: {out.stderr[-800:]}"
    assert "RESULT=None" in out.stdout, (
        "the resolver produced a path on a non-POSIX platform. The backslashes "
        "and the drive colon are not handled, so the result names no real "
        f"directory. Got: {out.stdout.strip()!r}"
    )


# --- and the callers say so out loud -----------------------------------------

def test_status_marks_the_directory_unresolved(monkeypatch):
    """`status()` is a data function; it reports the fact, main() shouts it."""
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    s = ARCH.status()
    assert s["unresolved"] is True
    assert s["live_count"] == 0


def test_the_cli_reports_the_refusal_instead_of_zero(monkeypatch, capsys):
    """The defect was not the wrong slug. It was that a wrong slug produced a
    clean `live 0 file(s)` and exit 0 on every run, forever."""
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    rc = ARCH.main(["--status"])
    out = capsys.readouterr()
    assert rc != 0, "--status exited 0 with no transcript directory"
    assert "could not be resolved" in (out.out + out.err).lower(), (
        f"nothing told the operator; got {out.out + out.err!r}"
    )
    assert "live " not in out.out, "it still printed a live count it could not know"


def test_archive_reports_the_refusal_too(monkeypatch, capsys):
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    counts = ARCH.archive()
    text = (capsys.readouterr().out + capsys.readouterr().err).lower()
    assert counts.get("archived", 0) == 0
    assert "could not be resolved" in text or counts.get("unresolved"), (
        "archive() silently returned zero for an unresolvable directory"
    )


# --- status() survives the deletion this script exists to outrun -------------

def test_status_skips_a_file_that_vanishes_between_glob_and_stat(monkeypatch, tmp_path):
    """`archive()` guards each file; `status()` did not, so the harness pruning
    a transcript mid-scan produced an uncaught traceback from a READ-ONLY
    command. That pruning is the whole reason this script exists."""
    live = tmp_path / "live"
    live.mkdir()
    (live / "a.jsonl").write_text("{}\n", encoding="utf-8")
    (live / "b.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: live)
    monkeypatch.setattr(ARCH, "archive_root", lambda: tmp_path / "arch")

    real_stat = Path.stat
    gone = live / "a.jsonl"

    def flaky(self, *a, **kw):
        if self == gone:
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky)
    s = ARCH.status()
    assert s["live_count"] == 2, "the glob result should still list both"
    assert s["live_bytes"] > 0, "the surviving file's size was dropped too"


def test_status_reports_real_sizes_when_nothing_vanishes(monkeypatch, tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "a.jsonl").write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: live)
    monkeypatch.setattr(ARCH, "archive_root", lambda: tmp_path / "arch")
    assert ARCH.status()["live_bytes"] == 100
