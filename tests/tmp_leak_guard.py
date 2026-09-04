"""Count the scratch entries this suite leaves in the SHARED temp directory.

WHY THIS EXISTS. MEASURED 2026-09-04 on the operator's laptop: `/tmp` held
50,225 top-level entries, of which 10,751 `odin-cad*`, 5,143
`pytest-wall-rate-*`, 1,431 `marp-cli-*`, 967 `skill_report_example-skill_*` and
~878 default-prefixed `tmp*`, spanning seven days. Separately
`/tmp/pytest-of-administrator` had reached 216 session directories, 4,940,079
files and 53 GB, and the cost was wall clock: back to back on one commit, the
suite took 907.1 s against the default basetemp and 432.6 s with `--basetemp`
pointed at a fresh directory.

The families above are NOT the basetemp problem, and that distinction is the
whole reason for this module. They are created DIRECTLY in the shared temp
directory, so they sit outside pytest's numbered-directory retention: pytest's
`make_numbered_dir_with_cleanup(keep=3)` never sees them, no `--basetemp` moves
them, and no cleanup reclaims them. A test that writes outside `tmp_path` has
opted out of the pytest lifecycle, and this module is what says so out loud.

WHAT IT ESTABLISHES, AND WHAT IT CANNOT.

Two halves, deliberately unequal, and the split is the same one
`pytest_sessionfinish` already settled for the data overlay.

  ENFORCED, because it is attributable: an in-process record of every temp entry
  THIS interpreter created outside the managed tree and did not remove. The
  wrappers see the creating call, so the path arrives with the nodeid of the
  test that made it. There is no race with anything: another process on this
  machine cannot add to this list, because the list is built from calls made
  here.

  REPORTED ONLY, because it is not attributable: a before/after walk of the
  shared temp directory's top level. That catches what the wrappers cannot -- a
  CHILD process leaving its own scratch behind, which is where the 1,431
  `marp-cli-*` directories come from, since `marp-cli` is node and creates them
  itself. But this machine has permanent competing writers in /tmp: two other
  worktrees, the main clone, the daemons and every browser. "This run created
  it" is a claim a subtraction cannot make, so the diff is printed as an
  observation and never fails anything, and the report says which half is which.

Do not promote the diff to the ratchet. It would flap on whatever else the
machine happened to do, and a gate that goes red for reasons nobody can act on
gets ignored, which is worse than no gate.

WHAT THE WRAPPERS COVER. `tempfile.mkdtemp`, `tempfile.mkstemp` and
`tempfile.NamedTemporaryFile`, all public API. Between them that is every
stdlib path that leaves a NAME on disk: `TemporaryDirectory` calls `mkdtemp`
through the module global, and `TemporaryFile`/`SpooledTemporaryFile` leave no
name to leak (on Linux they are unlinked immediately or opened `O_TMPFILE`).
Patching the private `_mkstemp_inner` would collapse two of the three into one
wrapper and was rejected: it is private, so a Python upgrade could remove it and
the guard would go quiet exactly when nobody was looking.

A path is judged at SESSION END, not at creation. Creating a scratch file in the
shared temp directory and removing it is not a leak, and a guard that counted
the creation would push authors toward `dir=` in places where the removal was
already correct.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# The nodeid of the test currently running in this process, set by the
# `pytest_runtest_logstart` hook in conftest. Import-time and fixture-time
# allocations land under this too when they happen inside a test's own setup;
# module-import allocations happen during collection and are reported under the
# placeholder, which is honest -- collection has no single owning test.
CURRENT_TEST = "<import or collection>"

# path -> nodeid, for every entry created outside the managed tree. A dict
# rather than a list so a name reused after a removal does not double-count.
_UNMANAGED: dict[str, str] = {}

# Absolute prefix of the tree pytest reclaims on its own. Everything under it is
# the suite's business and is not counted.
_MANAGED_PREFIX: str | None = None

_RESTORE: list = []


def _is_managed(path: str) -> bool:
    if _MANAGED_PREFIX is None:
        return False
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return False
    return resolved == _MANAGED_PREFIX or resolved.startswith(_MANAGED_PREFIX + os.sep)


def _note(path) -> None:
    text = os.fspath(path)
    if not _is_managed(text):
        _UNMANAGED[os.path.realpath(text)] = CURRENT_TEST


def arm(managed_root: Path) -> None:
    """Wrap the three named-temp creators. Idempotent within a process.

    `managed_root` is the tree pytest reclaims -- the PARENT of the numbered
    session directories (`/tmp/pytest-of-<user>`), not the session directory
    itself. The parent, because `make_numbered_dir_with_cleanup(keep=3)` prunes
    at that level, so anything under it dies on pytest's schedule whether or not
    it belongs to this run.
    """
    global _MANAGED_PREFIX
    if _RESTORE:
        return
    _MANAGED_PREFIX = os.path.realpath(managed_root)

    real_mkdtemp = tempfile.mkdtemp
    real_mkstemp = tempfile.mkstemp
    real_named = tempfile.NamedTemporaryFile

    def mkdtemp(*a, **kw):
        made = real_mkdtemp(*a, **kw)
        _note(made)
        return made

    def mkstemp(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        _note(name)
        return fd, name

    def named(*a, **kw):
        handle = real_named(*a, **kw)
        # `delete=True` still leaves a name for the file's lifetime, but it is
        # removed on close, so it cannot be a survivor and noting it is
        # harmless: the survivor filter at session end is what decides.
        name = getattr(handle, "name", None)
        if isinstance(name, str):
            _note(name)
        return handle

    tempfile.mkdtemp = mkdtemp
    tempfile.mkstemp = mkstemp
    tempfile.NamedTemporaryFile = named
    _RESTORE.append((real_mkdtemp, real_mkstemp, real_named))


def disarm() -> None:
    if not _RESTORE:
        return
    real_mkdtemp, real_mkstemp, real_named = _RESTORE.pop()
    tempfile.mkdtemp = real_mkdtemp
    tempfile.mkstemp = real_mkstemp
    tempfile.NamedTemporaryFile = real_named


def survivors() -> list[tuple[str, str]]:
    """(path, nodeid) for every unmanaged entry that still exists right now.

    Sorted for a stable report. `os.path.exists` rather than `lexists`: a
    dangling symlink in the shared temp directory is somebody else's problem and
    this suite creates none.
    """
    return sorted((p, who) for p, who in _UNMANAGED.items() if os.path.exists(p))


def survivors_by_test(limit: int) -> list[tuple[str, int, str]]:
    """(nodeid, count, one example path), worst first, capped at `limit`.

    The cap is a wire budget as much as a display one: these rows are pickled
    back through execnet from each xdist worker.
    """
    tally: dict[str, list] = {}
    for path, who in survivors():
        row = tally.setdefault(who, [0, path])
        row[0] += 1
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [(who, count, example) for who, (count, example) in ranked[:limit]]


def merge_rows(rows, into: dict) -> None:
    """Fold one worker's `survivors_by_test` rows into a controller-side map."""
    for who, count, example in rows or ():
        row = into.setdefault(who, [0, example])
        row[0] += int(count)


# ---------------------------------------------------------------------------
# The unattributable half: a before/after walk of the shared temp directory
# ---------------------------------------------------------------------------


def top_level_snapshot() -> set[str]:
    """Names directly inside the shared temp directory.

    Names, not paths, and one level only. The tree has six-figure entry counts
    on this machine, so a recursive walk would cost more than the run it is
    measuring, and the leak this is looking for is by definition a TOP-LEVEL
    entry: something created with no `dir=` lands exactly there.

    Errors are swallowed to an empty set on purpose. This half of the guard
    fails nothing, so a temp directory that cannot be read should degrade to
    "no observation" rather than take the session down.
    """
    try:
        with os.scandir(tempfile.gettempdir()) as it:
            return {e.name for e in it}
    except OSError:
        return set()


def appeared_and_survived(before: set[str], after: set[str]) -> list[str]:
    """Entries present at the end that were not present at the start.

    NOT an accusation. Every other process on this machine writes here too, so
    this is an upper bound on what the run left behind and never a claim about
    which test did it. The attributable answer is `survivors()`.
    """
    root = Path(tempfile.gettempdir())
    return sorted(name for name in after - before if (root / name).exists())


def family(name: str) -> str:
    """Collapse a temp entry name to its prefix, for a readable report.

    `odin-cad-clu-7f3a` and `odin-cad-clu-91bc` are one finding, not two.
    Trailing random suffixes are stripped by taking everything up to the last
    separator, and a name with no separator is its own family.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    for sep in ("-", "_"):
        if sep in stem:
            head = stem.rsplit(sep, 1)[0]
            if head:
                return head + sep + "*"
    return name


def summarise(names) -> list[str]:
    """`family xN` strings, worst first."""
    counts: dict[str, int] = {}
    for name in names:
        counts[family(name)] = counts.get(family(name), 0) + 1
    return [f"{fam} x{n}" for fam, n in
            sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
