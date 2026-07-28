#!/usr/bin/env python3
"""The working tree's state: what an attestation perishes on.

An attestation used to bind to the FROZEN bytes and to nothing else, and the
code under test is by design not frozen, so a green record survived every change
to the thing the contract exists to judge. Measured: break the implementation,
run nothing at all, and `verify` still read ATTESTED.

The first design recorded the files the run IMPORTED. It was withdrawn before
any freeze on a measurement taken in this repository:
`tests/test_alert_no_import_cycle.py` deletes every module whose name contains
`alert`, including its own, so a file that ran was already absent from
`sys.modules` at session finish. `exec(open(path).read())`, a fixture that tears
a module down, a script invoked as a subprocess, and a JSON fixture are the same
shape. There is no set to arrange here, because the state is the tree.

Defined relative to git rather than by walking the filesystem, because a walk
cannot know what is ignored, and a state that carried `.venv` and every build
artifact would be permanently red. The cost of that choice is stated in the
design's ceiling: a change to a gitignored file the run reads does not perish
the record.
"""
import hashlib
from pathlib import Path
from typing import Optional

from scripts.utils.canopus_freeze import TREE_RECIPE
from scripts.utils.canopus_git import git_output


def _porcelain_paths(raw: str) -> list[str]:
    """Every path in a `--porcelain=v1 -z` status, renames counted at both ends.

    The `-z` form exists so a path never has to be quoted or escaped, which is
    why this parser splits on NUL and never on whitespace: `a file.py` and a
    path with a newline in it both survive.

    A rename or a copy ships TWO fields, the new path then the old one. A parser
    that read only the first would lose the old path, and the old path is the
    one whose disappearance a reader needs to see.
    """
    fields = [field for field in raw.split("\0") if field]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if ("R" in code or "C" in code) and index < len(fields):
            paths.append(fields[index])
            index += 1
    return paths


def _flagged_paths(root: Path) -> Optional[list[str]]:
    """Root-relative paths git's index marks assume-unchanged or skip-worktree.

    `git status --porcelain` is DEFINED to hide both. Assume-unchanged tells
    git to trust the cached stat information without even checking the file;
    skip-worktree tells git the worktree copy is expected to differ from the
    recorded blob, and neither bit's path participates in status output of any
    kind while it is set. `dirty` below is built entirely from status, so a
    path carrying either bit could be edited on disk -- the implementation
    broken, in the case this closes -- and status would print nothing,
    forever, until the bit is cleared. Reproduced: a green record, then
    `git update-index --assume-unchanged path`, then a broken edit and no test
    run at all, and `verify` still read ATTESTED. Reproduced identically for
    `--skip-worktree`.

    `git ls-files -v` is the one place both bits surface. Per git-ls-files(1),
    the tag is `H` for an ordinary cached path and `S` for one carrying
    skip-worktree, and the tag is LOWERCASED, whichever base letter it is,
    when assume-unchanged is ALSO set -- so `h` marks assume-unchanged alone,
    `S` marks skip-worktree alone, and `s` marks both. Measured against a real
    index rather than taken from the manual page: `H`/`S`/`h`/`s` is the exact
    sequence four successive `update-index` calls produced on one file here.
    A lowercase letter therefore always means assume-unchanged, regardless of
    the base tag, which is why the test below is `code.islower() or
    code == "S"` rather than a fixed set of letter pairs.

    Paths from `git ls-files` are relative to the directory git was invoked
    IN, unlike `git status --porcelain`, which git-status(1) documents as
    toplevel-relative unconditionally. `git_output` always calls `-C root`, so
    these come back root-relative, and `tree_state` converts each one against
    the toplevel before it is used as a `dirty` key -- the same toplevel this
    module already resolves for the porcelain paths, and the same bug class
    `test_tree_state_through_a_subdirectory_hashes_against_the_toplevel`
    exists to keep shut: joining a root-relative path onto the wrong base
    hashes nothing and reports no drift for a file that changed.

    `-z` for the same reason `_porcelain_paths` needs it: a path never has to
    be quoted or escaped, so an embedded space or newline survives. Routed
    through `git_output`, the one runner this module already uses everywhere
    else, rather than a second hand-rolled subprocess call.

    None on a git failure, matching every other reader in this module: a
    check that could not run is not a check that found nothing, and the
    caller must refuse rather than report a tree state this half never
    examined.
    """
    raw = git_output(root, "ls-files", "-v", "-z")
    if raw is None:
        return None
    flagged: list[str] = []
    for entry in (field for field in raw.split("\0") if field):
        if len(entry) < 3:
            continue
        code, path = entry[0], entry[2:]
        if code.islower() or code == "S":
            flagged.append(path)
    return flagged


def tree_state(root: Path) -> Optional[dict]:
    """`{"recipe", "head", "dirty"}`, or None when git cannot answer.

    `dirty` maps every path git reports as changed, added, deleted or
    untracked, PLUS every path the index marks assume-unchanged or
    skip-worktree (see `_flagged_paths`), to the sha256 of its current bytes,
    or to None for a path that is gone or unreadable. Together with `head`
    that is a complete description of the tree relative to git, covering
    Python, YAML, JSON, templates and markdown alike.

    `--untracked-files=all` is load-bearing rather than thorough. Default
    porcelain collapses an untracked directory to `newdir/` and stops, and a
    directory name carries no hash, so a state recording it would not move when
    the file inside it changed. A new module dropped into a new package is
    exactly the shape a builder reaches for.

    Porcelain paths are relative to the repository TOPLEVEL, never to *root*:
    `git status --porcelain=v1` documents that explicitly, unlike the long
    format's cwd-relative paths. When *root* is a subdirectory of the
    repository, joining a porcelain path to *root* reads bytes from a path that
    does not exist, so every entry hashes to None and two different tree states
    compare equal -- a green attestation surviving a change to the thing under
    test. Measured, not theorised: `tree_state(repo / "pkg")` after two
    successive edits to `pkg/a.py` returned `{"pkg/a.py": None}` both times.
    So every path is hashed against the resolved toplevel, not *root*.

    None rather than an exception, and None rather than an empty state: this is
    read from the recorder at session finish, where a raise takes the session's
    exit code with it, and `build_attestation` reads None as "this run could not
    describe the tree it ran against" and refuses.
    """
    root = Path(root)
    head = git_output(root, "rev-parse", "HEAD")
    if head is None:
        return None
    top = git_output(root, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    # rstrip only git's trailing line ending, never `.strip()`: a repository
    # whose toplevel path genuinely ends in a space or a tab is real, and
    # stripping it resolves every later join against the WRONG directory --
    # every porcelain path then hashes to None and two different tree states
    # compare equal, the exact silent-green failure this module exists to
    # prevent. `\r\n` is included because git on a CRLF checkout can emit it;
    # `\r\n`.rstrip("\r\n") strips both characters in one pass regardless of
    # order, so a bare `\n` and a `\r\n` line ending are both fully removed.
    top = top.rstrip("\r\n")
    if not top:
        # Empty output on exit 0 is git's "not really a repository" case --
        # canopus_git.repo_identity guards the identical call the same way,
        # because `Path("")` is `Path(".")`, and joining porcelain paths onto
        # that would silently hash whatever the PROCESS happens to sit in
        # instead of refusing. Answered as None, the same posture `head` and
        # `status` already take a few lines either side of this one.
        return None
    toplevel = Path(top)
    status = git_output(root, "status", "--porcelain=v1",
                        "--untracked-files=all", "-z")
    if status is None:
        return None
    dirty: dict = {}
    for rel in _porcelain_paths(status):
        try:
            dirty[rel] = hashlib.sha256((toplevel / rel).read_bytes()).hexdigest()
        except (OSError, ValueError):
            # A deleted path, an unreadable one, and a directory all land here.
            # None is a recorded GAP rather than a dropped entry: dropping it
            # would make a deletion read as a smaller, wholly clean tree, which
            # is the greener of the two.
            dirty[rel] = None

    # `git status` will not name a path carrying either bit, no matter what
    # happens to its bytes -- that is the whole reason the bits exist, and the
    # whole reason `dirty` cannot rely on status alone. Every such path is
    # merged in here, hashed the same way and refused the same way, so an
    # attestation can perish on an edit status was told to ignore.
    flagged = _flagged_paths(root)
    if flagged is None:
        return None
    resolved_root = Path(root).resolve()
    try:
        # ONE computation, for *root* itself against *toplevel* -- a fact
        # about the repository this function was asked about, not about any
        # single flagged path. git just answered --show-toplevel and -C root
        # from the same invocation of the same repository, so this is not
        # expected to fire in practice. Refused as the whole state (matching
        # the fail-closed posture the rest of this function already takes)
        # rather than skipped in case it ever does: a root this function
        # cannot place is exactly the gap the merge below exists to close,
        # and dropping it silently would reopen it.
        offset = resolved_root.relative_to(toplevel)
    except ValueError:
        return None
    for rel in flagged:
        # Purely lexical: *offset* and *rel* are joined as path STRINGS,
        # never touching the filesystem, so this can never follow a symlink
        # that IS *rel*. The previous form -- `(resolved_root / rel)
        # .resolve()` -- resolved every component of the joined path,
        # including the final one, so a tracked, flagged symlink `alias.py`
        # pointing at `real.py` inside the same repo was recorded under the
        # key `real.py` instead of `alias.py`, and one pointing OUTSIDE the
        # repository resolved outside `toplevel` entirely: `.relative_to`
        # then raised, and the whole function returned None -- one flagged
        # symlink cost the ENTIRE tree state, for every path in the
        # repository, not just its own. Reproduced: a tracked,
        # assume-unchanged symlink pointing outside the repo, sitting beside
        # an ordinary tracked-file edit elsewhere in the tree -- the edit was
        # real, and the collapse hid it completely.
        #
        # A path this function cannot read now costs only ITS OWN key, the
        # same gap a deleted or unreadable path already gets two blocks up:
        # recorded as None under the key GIT reported, never dropped and
        # never let to sink the whole read.
        key = (offset / rel).as_posix()
        if key in dirty:
            continue  # already hashed above, identically, by the status loop
        try:
            dirty[key] = hashlib.sha256((toplevel / key).read_bytes()).hexdigest()
        except (OSError, ValueError):
            dirty[key] = None
    return {"recipe": TREE_RECIPE, "head": head.strip(), "dirty": dirty}
