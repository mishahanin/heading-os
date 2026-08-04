#!/usr/bin/env python3
"""Canopus freeze primitive: hashing, manifest construction, verification.

The Canopus standard freezes the test contract before a build starts so the
builder cannot move the target it is measured against. This module is the pure
logic half.

Three layers, and the distinctions are load-bearing:

  * The PreToolUse deny is a CONVENIENCE. It only sees Write, Edit, MultiEdit,
    and NotebookEdit tool calls, so a Bash `sed -i`, a `python -c`, or a subagent
    with its own toolset walks straight past it. It exists to refuse the write at
    the moment of the attempt and save a wasted loop iteration.
  * The manifest is the GUARANTEE. Verification recomputes digests from disk and
    catches a change made by any route, including routes nobody anticipated.
  * The test gate is what makes the guarantee FIRE. tests/conftest.py runs the
    check at pytest session start and scripts/run-tests.py runs it before the
    suite, so a build cannot reach green while its contract is moved — not even
    through the bare `pytest tests/test_thing.py` inner loop. An unrun verify
    fails 100% of the time no matter how well its expected value is protected.

Never reason that the deny makes verification optional.

The realistic adversary is not an evader. The builder is Claude, and the failure
this catches is tampering by helpfulness: the model hits a red assertion,
concludes in good faith that the assertion is wrong, and edits it. A verification
that merely runs catches that completely.

Recipe `canopus-freeze-v7`, named in every manifest so a future algorithm change
breaks loudly instead of silently:

    file digest  = sha256(LF-normalized bytes)
    dir digest   = sha256("".join(f"{relpath}\\n" for relpath in sorted members))
    root hash    = sha256(canonical JSON of
                          {recipe, anchor, anchor_repo, files, dirs, baseline,
                           plugins, content_names})
    enforcer pin = sha256(canonical JSON of the `content` map)

TWO hashes, because the manifest carries two claims that have nothing to do with
each other. The root is the CONTRACT: what the builder is measured against, and
what a human commits an approval over. The pin is the ENFORCER: the bytes of the
code that does the measuring. An enforcer edit moves the pin and leaves the root
alone, so it costs a `repin` rather than a whole re-approval, and it is still
never silent — see CONTENT_KEY.

The enforcer NAMES are the one part of the enforcer claim that stays in the root,
and the split shipped without them. The names say WHICH files are being watched;
the digests say what those files currently are. Only the second is what a repin
is for. See `root_hash_payload`.

where a directory's members are those whose BASENAME matches one of the entry's
recorded `names` patterns, and a member that is itself a directory is rendered
with a trailing `/` so a file and a directory of the same name are two lines.

Per-file bytes are LF-normalized (\\r\\n -> \\n) so a CRLF working copy and a
fresh LF checkout agree, matching the recipe already proven in
scripts/verify-skills-lock.py. The root hash covers the recipe, the anchor path,
and the content maps. Neither the label, the freeze timestamp, nor the recorded
git sha enters it, so re-freezing identical content against the same anchor
yields an identical root hash. That is deliberate: identical content means
nothing was tampered with, and the re-freeze is recorded in the ledger anyway.

Stdlib only (plus scripts.utils.atomic, itself stdlib only), and never
subprocess: .claude/hooks/_dispatch.py imports this module on every Write/Edit
and must not drag the workspace utility chain in.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Optional, Sequence, Tuple

from scripts.utils.atomic import atomic_write_text

# v7 from the enforcer-set-bound slice: the enforcer NAMES came back into the
# root-hash payload, which v6 had taken out along with their bytes. v6 from the
# manifest-split slice before it: the enforcer bytes LEFT the payload for a map
# of their own. Same reasoning every time, and as v5 (the plugin baseline) and v2
# (the per-file baseline) — a new payload shape without a bump reads as LOSS OF
# LOCK on a tree where nothing moved, and sends an operator hunting a file that
# never changed. The bump turns that into a NAMED refusal at `read_freeze`
# ("carries recipe canopus-freeze-v6").
RECIPE = "canopus-freeze-v7"

# The manifest key holding the ENFORCER map: the bytes of the code that does the
# measuring, hashed per file exactly like `files`, and deliberately OUTSIDE the
# root-hash payload.
#
# One manifest hash used to cover two claims that have nothing to do with each
# other. The CONTRACT is what the builder is measured against; the ENFORCER is
# what does the measuring. Both lived in `files`, so touching a single enforcer
# byte moved the root, the approval a human COMMITTED stopped matching, and
# `freeze` refused until the whole approve/commit/freeze cycle was repeated.
# Measured over the 39 `anchor_replaced` records in the ledger on 2026-08-03:
# 21 of them were exactly that, the largest class of retake in the standard's
# history, and not one was a contract that had changed.
#
# Split, not dropped. `enforcer_pin` digests this map, `verify_manifest` reports
# `enforcer_moved` beside `changed`, and a moved enforcer with no re-pin does NOT
# read LOCK HELD. What the split removes is the price, not the detection.
#
# The split took the enforcer NAMES out with the bytes, and that was a hole
# rather than a trade. Measured 2026-08-04: a freeze over ten enforcers and a
# freeze over nine compute the SAME root, so `release --window` followed by a
# `freeze` with a shorter `--content` list drops an enforcer, leaves the
# COMMITTED approval matching, and reads LOCK HELD and APPROVED. `enforcer_moved`
# cannot see it either: it diffs the RECORDED map against disk, and a name that
# was never recorded is in neither. From v7 the names are back inside the
# root-hash payload while the digests stay outside it, so a change to the SET
# costs a re-approval and a change to the BYTES still costs only a `repin`.
CONTENT_KEY = "content"

# What `recompute` records for an enforcer the manifest names and disk no longer
# has. NOT a digest, and it cannot be mistaken for one: `file_digest` answers 64
# hex characters and never the empty string, so `verify_manifest` reads this as
# moved for every recorded file and can never read it as unchanged.
#
# It exists so the recomputed enforcer NAME SET is always the RECORDED one. Drop
# a vanished file instead and the recomputed root stops matching the stored one,
# so deleting an enforcer would read as a moved CONTRACT and send the operator to
# a re-approval when the cure is to restore the file or take a new freeze. The
# name set is a recorded expectation, like the baseline and the plugin set beside
# it, and not something disk can be re-measured for.
ABSENT_ENFORCER = ""
FREEZE_DIRNAME = ".canopus"
FREEZE_FILENAME = "freeze.json"
HISTORY_FILENAME = "history.jsonl"
ANCHOR_PREFIX = "canopus-anchor:"
# The line `approve` writes above an anchor line when the freeze it is approving
# was accepted under `--contract-satisfied`. Deliberately NOT a prefix of, and
# not prefixed by, ANCHOR_PREFIX: read_anchor matches with
# `startswith(ANCHOR_PREFIX)` and takes everything after it as the digest, so a
# waiver written on the anchor line would be parsed as part of the hash.
SATISFIED_PREFIX = "canopus-contract-satisfied:"

# Where the anchor's repository stands RIGHT NOW, as measured by canopus_git and
# judged by repo_binding_state below. Defined here rather than in canopus_git
# because the judging half is pure and this module may never import the half that
# runs subprocess. The last two values coincide with canopus_git's anchor-read
# statuses on purpose: they describe the same fact about the world, and one
# spelling is better than two.
REPO_PRESENT = "in_repo"
REPO_ABSENT = "no_repo"
REPO_UNKNOWN = "no_git"

# What a manifest records when the anchor was NOT inside a repository at freeze
# time, and what an anchorless manifest carries. Never consulted in the second
# case: resolve_anchor returns before the binding when there is no anchor.
#
# Read-only on purpose. This is a module-level fallback every binding reader
# reaches for, so one in-place mutation would change it for the whole process:
# every stored unbound root would stop matching and verify would report LOSS OF
# LOCK over a tree where nothing moved. Callers that put it in a manifest copy it
# with dict(), so what gets stored and serialized is always a plain dict.
ANCHOR_REPO_UNBOUND = MappingProxyType({"in_repo": False, "identity": ""})

# Tool-generated caches that live INSIDE a source tree. A recursive freeze that
# captured these would bind the lock to artifacts no version control tracks: the
# build loop rewrites them on its own, and any fresh checkout or cache clean
# removes them, so the composition digest would report LOSS OF LOCK for a change
# nobody made. Measured at the first real use of the tool on itself, 2026-07-25.
#
# A named set rather than "ask the VCS what it ignores": this module is imported
# by the PreToolUse dispatcher on every write and stays stdlib-only, never
# calling subprocess. The boundary is deliberate and stated rather than implied.
# An ignored artifact outside this set still freezes, and discovering one is a
# reason to widen the set, never a reason to route around the lock.
CACHE_DIRNAMES = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
})
CACHE_SUFFIXES = frozenset({".pyc", ".pyo"})

# The directory names no guard ever measures, as ONE constant rather than a
# local rebuilt per call. `_members` filters with it and `watched_directory`
# answers with it, and those two are the measure and the deny: a set spelled
# twice is how the deny refuses a path the measurement ignores, or excuses one
# it watches.
SKIPPED_DIRNAMES = CACHE_DIRNAMES | {FREEZE_DIRNAME}

# Which basenames a directory guard watches, as fnmatch patterns.
#
# A guard is a question, and the three questions are different. A frozen
# directory asks "did anything at all move in here", so it watches everything.
# An ANCESTOR of a frozen path asks the narrow question the guard was invented
# for: did a conftest.py appear above the contract. That one file is what pytest
# imports without being told to, and it is where a stub reaches sys.path. The
# TREE ROOT asks a third question, because pyproject declares it on sys.path
# (`pythonpath = ["."]`): did an importable module appear at the entry the
# contract's own imports resolve against.
#
# The narrowing is not timidity, it is what makes the guard usable. Watching the
# full composition of an ancestor put 201 of this repository's 296 test files
# under a write deny and made the builder's next ordinary unit test report LOSS
# OF LOCK. A guard that fires on the builder doing its job is a guard that gets
# routed around, and a routed-around guard protects nothing.
GUARD_NAMES_ALL = ("*",)
GUARD_NAMES_ANCESTOR = ("conftest.py",)
GUARD_NAMES_TREE_ROOT = ("*.py",)

# Stated rather than implied, because the boundary is where the next hole lives.
#
# CLOSED in wire 2.3: the tree-root guard's composition now lists importable
# SUBDIRECTORIES beside `*.py` files, each rendered with a trailing `/`, so a
# package directory dropped at the root (`plug/__init__.py` shadowing an
# installed `plug`) moves the guard. `_members` carries the rule; `member_rel`
# carries the mark.
#
# PYTEST ADDS NO SECOND IN-TREE sys.path ENTRY, and that is measured, not
# assumed. pyproject sets `addopts = "--strict-markers --import-mode=importlib"`,
# and under importlib pytest inserts NOTHING for a collected test file: on a
# scratch tree carrying that setting, `sys.path[:2]` at run time inside
# `tests/contract/<slice>/test_contract.py` was the tree root twice (the
# `pythonpath = ["."]` entry and the rootdir) and nothing else. A package at
# `tests/plug/` and a package beside the contract were both `ImportError: No
# module named …`; only the root-level package imported. So the root guard is
# the whole surface pytest contributes, and there is no second entry to widen to.
#
# Said exactly that wide. It is NOT the claim that nothing importable lives
# under the tree: `.venv/…/site-packages` does, which is the reason
# `canopus_gate._library_dirs` exists to tell an interpreter library from an
# in-tree file. What is measured is that pytest adds no tree-owned entry beyond
# the one `pythonpath = ["."]` already declares.
#
# An earlier revision of this note derived the same conclusion from pytest's
# PREPEND mode inserting a test file's basedir, and stated a trigger ("it
# becomes live if the slice directory gains an `__init__.py`") that can never
# fire. That measurement had been taken on a scratch tree missing this
# repository's own addopts. It is corrected here rather than quietly dropped,
# because a false reason left standing is what the next slice reasons from.
#
# Two cases stay open, and neither is closed by this primitive:
#
#   * A change to the IMPORT MODE re-opens the surface. `--import-mode=prepend`
#     (the pytest default) inserts each collected test file's basedir, which
#     puts `tests/contract/<slice>/` on sys.path — measured on the same scratch
#     tree with the flag removed. That switch is one line in `pyproject.toml`,
#     which is neither frozen by content nor watched by this guard, since the
#     root guard watches `*.py` and importable directories. What holds the case
#     today is that the contract lives in its own directory under
#     tests/contract/, which freezes RECURSIVELY, so a package appearing beside
#     it moves the composition anyway (measured: `plug/__init__.py` there
#     reports added; an EMPTY `plug/` there does not, the recursive arm lists
#     files).
#   * A directory the BUILD generates at the root. The three present on this
#     machine are `dist/` (written by scripts/dev/build-plugins.py), `outputs/`
#     and `plans/`, but the real surface is the ignore list, not today's disk:
#     `.gitignore` carries 18 identifier-shaped root-level entries, 17 of them
#     outside CACHE_DIRNAMES — `htmlcov` (which a single `--cov-report=html`
#     run writes at the root), `threads`, `crm`, `knowledge`, `context`,
#     `datastore`, `corporate`, `personal`, `_archive`, `slash`, `_secure`,
#     `Desktop`, `LauncherFolder`, `MyDocuments` and the three above. Every one
#     is importable, so every one is watched on purpose, and the cost is that a
#     `git clean -xfd`, a first `build-plugins.py` run, or one HTML coverage
#     report moves the root composition with no source edited. Accepted rather
#     than excluded, because an exclusion set wide enough to cover seventeen
#     names is exactly where a real shadowing directory would hide, and the
#     failure is loud and instantly explicable rather than silent.
#
# Widening the guard further is a reason to change this set, never a reason to
# route around the lock.


class FreezeError(Exception):
    """A freeze operation was refused."""


# ============================================================
# Hashing
# ============================================================

def file_digest(path: Path) -> str:
    """sha256 over LF-normalized file bytes."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def dir_member_name(rel_posix: str) -> str:
    """The composition's spelling of a DIRECTORY member: its name plus `/`.

    One function, because the mark is an invariant and not a formatting choice.
    The composition is a digest over rendered names, so without the trailing
    slash a directory `plug` and a file `plug` produce the same line and one
    replacing the other moves nothing. The measurement (`member_rel`) spells it
    here, and any future reader of the composition should too.
    """
    return f"{rel_posix}/"


def member_rel(path: Path, base: Path, *, is_dir: bool) -> str:
    """A member's *base*-relative POSIX name, marked when it is a directory.

    ONE renderer, shared by `dir_members_digest`, `dir_member_rels` and the sort
    order all three agree on, for the reason `matches_guard` is shared by the
    measure and the deny: two hand-rolled copies is how the digest and the
    member list end up disagreeing about the same directory.

    *is_dir* is REQUIRED and carried in from `_members`, never re-derived with a
    second `is_dir()` call. This module argues that `recompute` must carry the
    manifest's fields through rather than re-measure them; the same discipline
    applies four lines down. Re-statting also gave a wrong answer for a real
    interleaving: a directory removed between the classification and the
    rendering came out unmarked, so the digest and the member list disagreed
    about the same entry. The mark cannot come from the Path itself, which is
    why it is a parameter: `Path("plug/") == Path("plug")`.
    """
    rel = path.relative_to(base).as_posix()
    return dir_member_name(rel) if is_dir else rel


def guard_watches_directories(names: Sequence[str]) -> bool:
    """Does this guard's pattern set watch subdirectories at all?

    The discriminator `watched_directory` keys on, split out so `cmd_status` can
    print a guard's real scope without a second copy of it. `status` printed
    `watching *.py` for the tree root and said nothing about the importable
    directories the same guard measures, which UNDER-states the scope: the
    inverse of the misreading that filter was added to prevent.

    Spelled `tuple(names) == ...` for the reason argued in `watched_directory`:
    the build path passes the TUPLE and the recompute path passes the LIST that
    JSON round-tripped it into.
    """
    return tuple(names) == GUARD_NAMES_TREE_ROOT


def watched_directory(name: str, names: Sequence[str]) -> bool:
    """Would a subdirectory called *name* join a shallow guard's composition?

    The rule the tree-root guard measures with, as one predicate. `_members`
    calls it to list the directories that are already there, and `cmd_status`
    reaches it through `guard_watches_directories` to print the real scope. The
    write-deny does NOT call it: refusing the write that CREATES such a
    directory was tried in wire 2.3 and withdrawn, for the reason `frozen_reason`
    records. So this predicate now serves DETECTION only, and the gap between
    what is watched and what is prevented is on the open list rather than closed.

    The pattern-set test is spelled `tuple(names) == ...` deliberately.
    `_guard_ancestors` passes the TUPLE GUARD_NAMES_TREE_ROOT and `recompute`
    passes the LIST the manifest round-tripped through JSON. Written with `is`,
    or with a bare `==` against the tuple, it is true on the build path and
    false on the recompute path: directories enter the stored digest and never
    the recomputed one, and the tree reports LOSS OF LOCK forever with nothing
    moved. That is wire 2.2's blocker B1 verbatim.

    `str.isidentifier()` is most of the rest, and deliberately not a denylist of
    bad names: `.git`, `.venv` and `.canopus` fall out because a leading dot is
    not an identifier. A directory named for a Python keyword (`class`,
    `import`) passes and is watched, which is the safe direction, and so does a
    non-ASCII identifier like `café`. What `isidentifier()` does NOT do is
    separate an authored directory from a generated one — `__pycache__` passes
    it — which is why SKIPPED_DIRNAMES is consulted here too.
    """
    return (
        guard_watches_directories(names)
        and name.isidentifier()
        and name not in SKIPPED_DIRNAMES
    )


def _members(
    directory: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> list[tuple[Path, bool]]:
    """Members of *directory* matching *names*, sorted, each with its kind.

    Returns `(path, is_dir)` pairs rather than bare paths: the classification is
    made once here, where the entry is already being examined, and carried to
    the renderer. Every caller is in this module.

    Regular files always, plus — under a shallow walk with the tree-root
    patterns — subdirectories `watched_directory` accepts, because
    `pythonpath = ["."]` makes such a directory an importable package at the
    entry the contract's own imports resolve against. The `not recursive` half
    is a guard, not decoration: the discriminator keys on the PATTERN SET, so
    without it a recursive walk asked for the root patterns would pull every
    nested directory into the composition.

    Symlinks are excluded (the workspace forbids them), anything under the
    freeze state directory is excluded so the manifest never hashes itself, and
    tool-generated caches are excluded so the lock never binds to an artifact the
    build regenerates (see CACHE_DIRNAMES).

    *names* is a tuple of fnmatch patterns matched against the BASENAME, so a
    recursive guard's default `("*",)` keeps every nested member while an
    ancestor guard keeps only the one file it has a reason to watch. Matching the
    basename rather than the relative path is what lets the same filter serve a
    flat listing and a nested one without two spellings of every pattern.
    """
    watch_dirs = not recursive
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    found: list[tuple[Path, bool]] = []
    for p in candidates:
        if p.is_symlink():
            continue
        if not SKIPPED_DIRNAMES.isdisjoint(p.relative_to(directory).parts):
            continue
        if p.is_dir():
            if watch_dirs and watched_directory(p.name, names):
                found.append((p, True))
        elif (
            p.is_file()
            and p.suffix not in CACHE_SUFFIXES
            and matches_guard(p.name, names)
        ):
            found.append((p, False))
    return sorted(found, key=lambda pair: member_rel(pair[0], directory, is_dir=pair[1]))


def matches_guard(name: str, names: Sequence[str]) -> bool:
    """True when *name* matches any of the guard's fnmatch patterns.

    One predicate shared by the measurement path (_members) and the write-deny
    path (frozen_reason). Two hand-rolled copies is how the hook ends up denying
    a file verify no longer watches, or watching one it never denies.
    """
    return any(fnmatch.fnmatch(name, pattern) for pattern in names)


def dir_members_digest(
    directory: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> str:
    """sha256 over the sorted POSIX relative paths of a directory's members.

    Composition only, not content: this is what detects a file appearing beside
    a frozen one (the conftest.py case), while per-file digests detect edits.
    """
    lines = "".join(
        f"{member_rel(p, directory, is_dir=is_dir)}\n"
        for p, is_dir in _members(directory, recursive=recursive, names=names)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def dir_member_rels(
    directory: Path, root: Path, *, recursive: bool, names: Sequence[str] = GUARD_NAMES_ALL
) -> list[str]:
    """The directory's members as sorted root-relative POSIX paths.

    Recorded in the manifest beside the composition digest. A digest proves
    something moved; it cannot say WHAT, because a hash is not invertible. The
    guard on a file's parent covers members that were never frozen individually
    (a sibling test), so `added` and `removed` cannot be derived from the file
    map alone — without this list every pre-existing sibling reads as newly
    added and the guard cries wolf on its first use.
    """
    return sorted(
        member_rel(p, root, is_dir=is_dir)
        for p, is_dir in _members(directory, recursive=recursive, names=names)
    )


def anchor_binding(manifest: dict) -> dict:
    """The manifest's anchor_repo binding, as a plain dict, never raising.

    ONE accessor for every reader of that key, because the alternative has now
    been measured eight times on this project: a guard applied to the function in
    front of its author and not to the one beside it. `repo_binding_state` grew an
    isinstance check in wire 2.2 while `root_hash` and `recompute` kept
    `dict(manifest.get("anchor_repo") or ANCHOR_REPO_UNBOUND)`, and `dict` raises
    on a string, a list and an integer alike (ValueError for the first two, a
    TypeError for the third).

    "read_freeze validates the shape first" is not an answer, and it is the same
    answer that was rejected at `repo_binding_state`: a validator is a guarantee
    about a DIFFERENT function. These three are called with manifests that never
    passed through read_freeze — a dict built in a test, one handed in by a
    caller, one carried across a version — and two of them sit under `freeze_gate`
    and under the PreToolUse dispatcher, where a raise fails OPEN.

    A malformed binding reads as UNBOUND rather than raising, which is the same
    direction `repo_binding_state` already took: an unbound reading is judged
    BROKEN the moment the anchor is found inside a repository, so nothing is
    softened by answering instead of raising.

    Returns a copy, always. The fallback is a MappingProxyType shared by the whole
    process, and callers store what they get here into manifests they then
    serialize.

    An EMPTY dict reads as unbound too, and that is preservation rather than
    taste: the three call sites all spelled `manifest.get("anchor_repo") or
    ANCHOR_REPO_UNBOUND`, so `{}` already fell back to the default. Dropping the
    falsy arm here would change the payload `root_hash` covers for such a
    manifest, which is LOSS OF LOCK over a tree where nothing moved.
    """
    binding = manifest.get("anchor_repo")
    if not isinstance(binding, dict) or not binding:
        return dict(ANCHOR_REPO_UNBOUND)
    return dict(binding)


def manifest_plugins(manifest: dict) -> list:
    """The plugin set a freeze captured, as a sorted list of NAMES.

    Names only, and never the origins they were read from. An origin is an
    absolute path inside `.venv`: it differs per machine and per clone, so
    comparing it would redden every fresh checkout, and it would put an
    operator's home directory inside a hash this repository commits against —
    the engine repository is public. The names are the identities
    `canopus_gate.process_facts` derives, which already carry their provenance
    (`dist:`, `intree:`), so nothing a comparison can use is lost.

    Accepts a mapping (whose keys are the names) or any iterable of names, so a
    caller holding the recorder's `{identity: origin}` map and a caller holding
    the captured list both write the same payload.
    """
    plugins = manifest.get("plugins") or ()
    return sorted({str(name) for name in plugins})


def enforcer_map(manifest: dict) -> dict:
    """The manifest's enforcer map, as a plain sorted dict, never raising.

    ONE accessor, for the reason `anchor_binding` states at length beside it: a
    guard written into the function in front of its author and not into the one
    beside it is this project's most-repeated defect. Every reader of
    CONTENT_KEY comes through here, so a manifest carrying a string, a list or
    nothing at all reads as an EMPTY enforcer set in all of them at once.

    Empty rather than raising, because two of the readers sit under `freeze_gate`
    and under the PreToolUse dispatcher, where a raise fails OPEN. Nothing is
    softened by answering: an empty recorded map compared against a disk that
    has the files reports every one of them moved, which is the red direction.
    """
    content = manifest.get(CONTENT_KEY)
    if not isinstance(content, dict):
        return {}
    return dict(sorted((str(k), str(v)) for k, v in content.items()))


def root_hash_payload(manifest: dict) -> dict:
    """Exactly what the contract root is computed over, as a dict.

    Split out of `root_hash` so the payload can be ASSERTED rather than inferred
    from two hashes comparing equal. A test that only compares roots passes over
    an implementation that stopped hashing the enforcer for any reason at all,
    including dropping both maps; naming the payload is what tells "the enforcer
    left the hash" from "the hash stopped covering anything".

    The baseline is in here deliberately. Outside the hash it could be edited
    down to 1 with no indicator moving, and a per-file expected item count that
    can be silently lowered is worse than none: it reports rigour it is not
    delivering.

    The binding is in here for the same reason the baseline is: outside the hash
    a builder edits `anchor_repo` to `in_repo: false`, wins the working-copy
    fallback permanently, and the committed approval still matches. Inside it,
    the edit changes the root and the approval stops matching.

    The plugin baseline is in here for that same reason a third time: it is the
    set every later attestation is compared against, so outside the hash a
    builder appends the name of the plugin that skips the contract and no
    indicator moves.

    CONTENT_KEY is NOT in here, and that is the whole of the manifest-split
    slice. Everything else that was in the payload stays in it.

    The enforcer NAMES are, and that is the whole of the slice after it. The two
    are not in tension: the names say WHICH files are being watched, the digests
    say what those files currently ARE, and only the second is what a `repin`
    corrects. With the names out, a re-freeze over a shorter `--content` list
    computed the same root as the approval it was checked against, so an enforcer
    could be dropped out of the set without any indicator moving and edited under
    a green lock from then on. With them in, the same act computes a different
    root and `freeze` refuses it against the COMMITTED approval, while an
    enforcer EDIT still leaves the root exactly where it was.

    Read the guarantee narrowly, because it is a comparison and not a rule: it
    binds a freeze to the set its committed approval recorded. An anchorless
    manifest has nothing to be compared against, so nothing there refuses a
    narrowing — which is equally true of `files`, the baseline and the plugin
    set, and is the reason the CLI will not take an anchorless freeze.
    """
    return {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "anchor_repo": anchor_binding(manifest),
        "files": dict(sorted(manifest["files"].items())),
        "dirs": dict(sorted(manifest["dirs"].items())),
        "baseline": dict(sorted((manifest.get("baseline") or {}).items())),
        "plugins": manifest_plugins(manifest),
        # Names ONLY, and derived from the one accessor rather than stored
        # beside the map. A `content_names` key of its own in the manifest would
        # be one fact with two spellings, and the two would drift the first time
        # somebody edited the map without it.
        "content_names": sorted(enforcer_map(manifest)),
    }


def _canonical_digest(payload) -> str:
    """sha256 over the canonical JSON of *payload*. One spelling, two hashes."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def root_hash(manifest: dict) -> str:
    """The CONTRACT digest: sha256 over `root_hash_payload`."""
    return _canonical_digest(root_hash_payload(manifest))


def enforcer_pin(manifest: dict) -> str:
    """The ENFORCER digest: sha256 over the content map alone.

    The second of the two hashes. It moves when an enforcer byte moves and at no
    other time, which is what makes `repin` a statement about one thing.

    An empty content map still yields a digest rather than "", because a caller
    comparing pins must be able to tell "the enforcer set is empty" from "this
    manifest has no pin at all", and an empty string collapses the two.
    """
    return _canonical_digest(enforcer_map(manifest))


# ============================================================
# Path validation
# ============================================================

def validate_freeze_path(path: Path, root: Path) -> Path:
    """Resolve *path* and refuse anything that cannot be safely frozen."""
    resolved_root = Path(root).resolve()
    path = Path(path)
    if path.is_symlink():
        raise FreezeError(f"{path} is a symlink; symlinks cannot be frozen")
    resolved = path.resolve()
    if not resolved.exists():
        raise FreezeError(
            f"{path} does not exist; a contract cannot freeze a path that is not there"
        )
    try:
        rel = resolved.relative_to(resolved_root)
    except ValueError:
        raise FreezeError(
            f"{path} resolves outside the working tree at {resolved_root}"
        ) from None
    if not rel.parts:
        # The tree ROOT itself, and it is refused for the reason `matches_guard`
        # exists: the deny and the measurement must watch the same set. A root
        # freeze records `dirs["."]` (the POSIX spelling of an empty relative
        # path), while `_guard_ancestors` spells the same directory `""`, and
        # `frozen_reason` matches neither against a normal relative path -- so
        # the PreToolUse deny went silently inert over the WHOLE frozen set while
        # `verify` kept catching the change. Measured, not reasoned: with
        # `dirs["."]` recursive, `frozen_reason("tests/test_a.py", manifest)`
        # answers None.
        #
        # Refusing rather than teaching the matcher a second spelling of the root
        # removes the divergence instead of patching one of its two sides, and it
        # costs nothing real: a recursive root freeze also hashes `.venv/` and
        # `.git/`, which the build rewrites on its own. Freeze the subdirectories
        # that hold the contract.
        raise FreezeError(
            f"{path} IS the working tree root; freezing it whole would bind the "
            f"lock to every build artifact in the tree and the write-deny cannot "
            f"see the resulting guard. Freeze the paths that hold the contract."
        )
    if rel.parts[0] == FREEZE_DIRNAME:
        raise FreezeError(
            f"{FREEZE_DIRNAME}/ holds the freeze state itself and cannot be frozen"
        )
    return resolved


def validate_anchor_path(path: Path, root: Path) -> Path:
    """Resolve an anchor artifact and refuse one the build could own.

    An anchor inside the working tree is not an anchor: the build writes there.
    """
    resolved_root = Path(root).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FreezeError(f"anchor artifact {path} does not exist or is not a file")
    if resolved.is_relative_to(resolved_root):
        raise FreezeError(
            f"anchor artifact {path} lies inside the working tree; an anchor inside "
            f"the build's own tree is not an anchor"
        )
    return resolved


# ============================================================
# Manifest construction
# ============================================================

CONFTEST_NAME = "conftest.py"


def _conftest_chain(target: Path, root: Path) -> list[Path]:
    """Every conftest.py from *target*'s directory up to *root*, inclusive.

    Walks the resolved ancestry rather than string prefixes, stops at the tree
    root, and skips symlinks (the workspace forbids them) so the chain can never
    reach outside the tree it is protecting.
    """
    start = target if target.is_dir() else target.parent
    found: list[Path] = []
    current = start
    while True:
        candidate = current / CONFTEST_NAME
        if candidate.is_file() and not candidate.is_symlink():
            found.append(candidate)
        if current == root:
            break
        parent = current.parent
        if parent == current:  # defensive: never loop at a filesystem root
            break
        current = parent
    return found


def _guard_ancestors(target: Path, root: Path, dirs: dict) -> None:
    """Install a filtered composition guard on every ancestor of *target*.

    Measured at the wire 2 intent audit, and this is the hole it closes. A
    directory freeze guarded only the target directory, so a `conftest.py`
    created in an ANCESTOR was invisible: verify held with nothing changed,
    added or removed. That conftest can put a stub module on `sys.path`, and
    because the mandated authoring rule resolves the code under test INSIDE the
    test body at RUN time, the frozen contract goes from red to green with every
    frozen byte intact. The item count is unchanged, so the baseline matches and
    the run attests: LOCK HELD and ATTESTED over a hijacked contract.

    Composition ONLY, never content. The guard answers "did an importable file
    appear above the contract", not "did anything under tests/ change" --
    freezing an ancestor's contents would stop a builder editing its own unit
    tests, and a primitive that forbids that gets routed around in its first
    week.

    The walk INCLUDES the tree root, under a different filter. The first version
    of this guard stopped below the root, reasoning that guarding the root's
    composition would deny every new top-level file. The reasoning was sound and
    the conclusion was wrong: pyproject declares `pythonpath = ["."]`, so the
    root is the first sys.path entry the contract's own imports resolve against,
    and stopping there left a plain `target.py` at the root able to flip the
    contract green while verify printed LOCK HELD. Watching what is IMPORTABLE
    there answers the objection instead of surrendering to it: `*.py` files, and
    subdirectories whose name is an identifier, so a package directory shadows
    nothing quietly, while a note or a lockfile written during the slice does
    not move the guard.

    An ancestor that already carries a recursive guard keeps it.
    """
    for ancestor in target.parents:
        if not ancestor.is_relative_to(root):
            break  # defensive: never walk above the tree being protected
        at_root = ancestor == root
        rel = "" if at_root else ancestor.relative_to(root).as_posix()
        names = GUARD_NAMES_TREE_ROOT if at_root else GUARD_NAMES_ANCESTOR
        if rel not in dirs:
            dirs[rel] = {
                "mode": "members",
                "names": list(names),
                "hash": dir_members_digest(ancestor, recursive=False, names=names),
                "members": dir_member_rels(ancestor, root, recursive=False, names=names),
            }
        if at_root:
            break


def build_manifest(
    paths: Iterable[Path],
    root: Path,
    *,
    label: str,
    frozen_at: str,
    anchor: Optional[Path] = None,
    content_only: Iterable[Path] = (),
    baseline: Optional[dict] = None,
    anchor_repo: Optional[dict] = None,
    plugins: Optional[Iterable[str]] = None,
) -> dict:
    """Build a freeze manifest over *paths*, all relative to *root*.

    A directory freezes recursively: every member file gets a content digest and
    the directory gets a recursive composition digest.

    A file freezes itself and additionally installs a NON-recursive composition
    guard on its parent directory, which is what catches a conftest.py dropped
    beside a frozen test. The tree root gets a guard too, filtered down to what
    is importable there (see `_guard_ancestors`): guarding the root's whole
    composition would deny every new top-level file and make the tool something
    people route around.

    A `content_only` path freezes its BYTES into the `content` map, NOT into
    `files`, and installs no composition guard on its parent. That map is
    outside the root-hash payload, so an edit to an enforcer moves the pin and
    leaves the contract root alone; `verify_manifest` still reports it, by name,
    as `enforcer_moved`. This is how the enforcer files are frozen. Freezing
    scripts/run-tests.py as an ordinary file would guard scripts/, and a build
    that cannot create a file under scripts/ cannot build anything, so the
    required practice would be unenforceable. A new file appearing beside
    run-tests.py does not change what run-tests.py does; an edit to its bytes
    does. The composition guard answers a question the enforcers do not ask.

    Every `conftest.py` on the path from each frozen path up to the tree root is
    added by CONTENT. A composition guard records member paths only, so a
    conftest sitting beside a frozen test was listed and never hashed, and that
    file is precisely where a good-faith edit changes what the contract measures
    without moving anything the guard watches: filtering inside
    `pytest_collection_modifyitems` fires no deselection hook, so the
    attestation's arithmetic still balances on a shrunken set. The tree root is
    included here even though the composition guard skips it, because a
    repository-root conftest is the cheapest place to filter collection from.
    """
    resolved_root = Path(root).resolve()
    files: dict[str, str] = {}
    content: dict[str, str] = {}
    dirs: dict[str, dict] = {}

    for raw in paths:
        target = validate_freeze_path(Path(raw), resolved_root)
        rel = target.relative_to(resolved_root).as_posix()
        if target.is_dir():
            dirs[rel] = {
                "mode": "recursive",
                "names": list(GUARD_NAMES_ALL),
                "hash": dir_members_digest(target, recursive=True),
                "members": dir_member_rels(target, resolved_root, recursive=True),
            }
            # A recursive walk yields files only, so the kind is discarded here.
            for member, _is_dir in _members(target, recursive=True):
                files[member.relative_to(resolved_root).as_posix()] = file_digest(member)
        else:
            files[rel] = file_digest(target)
        _guard_ancestors(target, resolved_root, dirs)
        for conftest in _conftest_chain(target, resolved_root):
            files.setdefault(
                conftest.relative_to(resolved_root).as_posix(), file_digest(conftest)
            )

    # Positional paths are processed first, deliberately: a path given BOTH ways
    # keeps the parent guard its positional form installed.
    for raw in content_only:
        target = validate_freeze_path(Path(raw), resolved_root)
        if target.is_dir():
            raise FreezeError(
                f"{target} is a directory; --content freezes file bytes only. "
                f"Pass it positionally to freeze it recursively."
            )
        content[target.relative_to(resolved_root).as_posix()] = file_digest(target)
        # The conftest chain lands in `files`, never in `content`, and the
        # OVERLAP that produces is deliberate. A conftest is where a good-faith
        # edit changes what the contract measures, so it belongs to the contract
        # root; an enforcer path passed with --content is also on the chain of
        # its own conftests. A file reached both ways is therefore carried in
        # BOTH maps and reddens the lock through both, which is the strict
        # direction: letting --content REMOVE a path from `files` would let a
        # conftest edit stop moving the root, and that is the hole the standard
        # exists to hold shut.
        for conftest in _conftest_chain(target, resolved_root):
            files.setdefault(
                conftest.relative_to(resolved_root).as_posix(), file_digest(conftest)
            )

    manifest = {
        "recipe": RECIPE,
        "label": label,
        "frozen_at": frozen_at,
        "anchor": str(validate_anchor_path(anchor, resolved_root)) if anchor else "",
        "anchor_repo": dict(anchor_repo or ANCHOR_REPO_UNBOUND),
        "git_sha": "",
        "baseline": dict(sorted((baseline or {}).items())),
        # The plugin set the contract run loaded, captured rather than derived:
        # `recompute` cannot re-run pytest, and a field inside the hash that the
        # recompute path cannot reproduce is a permanent LOSS OF LOCK on an
        # untouched tree. That is wire 2.2's blocker B1 verbatim, and the
        # per-file baseline beside it is carried for the same reason.
        "plugins": manifest_plugins({"plugins": plugins}),
        "files": dict(sorted(files.items())),
        CONTENT_KEY: dict(sorted(content.items())),
        "dirs": dict(sorted(dirs.items())),
    }
    manifest["root"] = root_hash(manifest)
    return manifest


# ============================================================
# Verification
# ============================================================

LOCK_HELD = "LOCK HELD"
LOSS_OF_LOCK = "LOSS OF LOCK"
LOCK_UNCONFIRMED = "LOCK UNCONFIRMED"


def recompute(manifest: dict, root: Path) -> dict:
    """Rebuild the manifest's content keys from current disk state.

    Same key set as *manifest*, and for `files` a file that vanished is simply
    absent from the result, which is what makes the recomputed root hash differ.
    The enforcer map is the ONE exception and the loop below states why.
    """
    resolved_root = Path(root).resolve()
    files: dict[str, str] = {}
    for rel in manifest["files"]:
        candidate = resolved_root / rel
        if candidate.is_file() and not candidate.is_symlink():
            files[rel] = file_digest(candidate)
    dirs: dict[str, dict] = {}
    for rel, entry in manifest["dirs"].items():
        candidate = resolved_root / rel
        recursive = entry["mode"] == "recursive"
        # Read from the manifest, never re-derived from the path. Re-deriving
        # would let a re-measurement widen or narrow a guard the approval was
        # taken over, which is the one thing recompute must not be able to do.
        names = entry["names"]
        alive = candidate.is_dir()
        dirs[rel] = {
            "mode": entry["mode"],
            "names": list(names),
            "hash": (
                dir_members_digest(candidate, recursive=recursive, names=names)
                if alive else ""
            ),
            "members": (
                dir_member_rels(candidate, resolved_root, recursive=recursive, names=names)
                if alive else []
            ),
        }
    # The enforcer map, re-measured from disk exactly like `files`, with ONE
    # deliberate difference: a vanished enforcer is recorded as ABSENT_ENFORCER
    # rather than dropped, so the recomputed NAME set is always the recorded one.
    # `files` behaves the opposite way on purpose. For the contract, absence must
    # move the root, because a frozen test that is gone is a contract that
    # changed. For an enforcer it must not: from v7 the names are inside the
    # payload, so dropping a vanished one would make deleting an enforcer read as
    # a moved CONTRACT and send the operator to a re-approval when the cure is to
    # restore the file or take a new freeze. The deletion is not softened by
    # this — it still reddens `enforcer_moved`, by name, on its own axis.
    #
    # An enforcer the manifest ALSO carries in `files` is the exception, and it is
    # not a leak: deleting it moves the root as well, because it is a frozen
    # contract file too. `build_manifest` puts every `conftest.py` on the chain
    # into `files` deliberately and says why, so the live engine freeze carries
    # `tests/conftest.py` in both maps. Stated here because the sentence above
    # reads absolute without it, and an absolute sentence that has an exception is
    # what the next reader reasons from.
    content: dict[str, str] = {}
    for rel in enforcer_map(manifest):
        candidate = resolved_root / rel
        if candidate.is_file() and not candidate.is_symlink():
            content[rel] = file_digest(candidate)
        else:
            content[rel] = ABSENT_ENFORCER

    return {
        "recipe": manifest["recipe"],
        "anchor": manifest.get("anchor") or "",
        "anchor_repo": anchor_binding(manifest),
        "files": dict(sorted(files.items())),
        CONTENT_KEY: dict(sorted(content.items())),
        "dirs": dict(sorted(dirs.items())),
        # Carried through verbatim, all three: the baseline and the plugin set
        # are recorded expectations, the binding is a recorded measurement, and
        # none of them is something disk can be re-measured for. Any key
        # root_hash reads and recompute omits makes the recomputed root differ
        # from the stored one forever, which reads as LOSS OF LOCK over a tree
        # where nothing moved.
        "baseline": dict(sorted((manifest.get("baseline") or {}).items())),
        "plugins": manifest_plugins(manifest),
    }


def verify_manifest(manifest: dict, root: Path) -> dict:
    """Compare disk against *manifest* and report what moved.

    `held` is True only when the recomputed root hash matches AND no file
    changed, was added, or was removed AND no enforcer moved. Every condition is
    checked rather than inferred from the others, so a future recipe change
    cannot quietly turn a real difference into a pass.

    The enforcer term is named in that sentence rather than left to the paragraph
    about it further down. This module's own standard, written where `recompute`
    argues its one exception, is that an absolute sentence with an unstated
    exception is what the next reader reasons from — and this sentence had been
    absolute since before the enforcer axis existed. The arithmetic itself now
    lives in `content_held`, which `enforcer_is_sole_cause` reads too, so the
    definition has one spelling and the two callers cannot drift apart.

    The first of those two is reported on its own as `root_moved`, because every
    caller that has to EXPLAIN a red lock needs to tell "the contract moved" from
    "something the root does not cover moved", and re-deriving that from the file
    lists gets it wrong: the lists can be empty while the roots disagree.

    `added` and `removed` are diffed against each guarded directory's RECORDED
    member list, never against the file map. A guard on a frozen file's parent
    deliberately covers siblings that were not frozen individually, so a file
    map comparison would report every pre-existing sibling as newly added.

    `enforcer_moved` is the enforcer map's own diff, reported as its OWN list
    beside `changed` and `removed` rather than merged into them. That separation
    is the whole point of the split: an operator who cannot tell "the enforcer
    moved" from "your contract moved" is back to one undifferentiated red, and
    the two have different cures — `repin` for the first, a re-approval for the
    second. It is folded into `held` and NOT into the recomputed root, so drift
    reddens the lock while the committed approval keeps matching.

    A DELETED enforcer counts as moved. The natural implementation hashes what
    it finds and reports nothing for a file that is gone, which would make
    removing the checker quieter than editing it.
    """
    resolved_root = Path(root).resolve()
    current = recompute(manifest, resolved_root)

    changed = sorted(
        rel for rel, digest in current["files"].items()
        if manifest["files"][rel] != digest
    )

    recorded_content = enforcer_map(manifest)
    current_content = current[CONTENT_KEY]
    enforcer_moved = sorted(
        rel for rel, digest in recorded_content.items()
        if current_content.get(rel) != digest
    )

    added: set[str] = set()
    vanished: set[str] = set()
    for rel, entry in manifest["dirs"].items():
        was = set(entry["members"])
        now = set(current["dirs"][rel]["members"])
        added |= now - was
        vanished |= was - now

    removed = sorted((set(manifest["files"]) - set(current["files"])) | vanished)

    recomputed = root_hash(current)
    # The root comparison as a NAMED field, not left for each reader to redo.
    # Three of them ask this question — `lock_state`, the pytest-session gate and
    # the CLI — and `loss_of_lock_sentences` answered it from the file lists
    # instead, which is not the same question: it told an operator "the ENFORCER
    # moved, NOT the contract" over a tree whose stored and recomputed roots
    # disagreed, so the cure it named was the cheap one and the lock stayed red
    # after they took it. One fact, one spelling, read by all of them.
    root_moved = recomputed != manifest["root"]
    report = {
        "recomputed_root": recomputed,
        "root_moved": root_moved,
        "changed": changed,
        "added": sorted(added),
        "removed": removed,
        "enforcer_moved": enforcer_moved,
    }
    report["held"] = content_held(report)
    return report


def content_held(report: dict) -> bool:
    """The CONTENT axis's one definition of green, over a verify report.

    One spelling, two readers. `verify_manifest` builds a report and asks it;
    `enforcer_is_sole_cause` asks it again over a copy with the enforcer axis
    emptied. A second copy of this arithmetic is how the two come to disagree,
    and the disagreement would be silent: both would still return a bool.

    `enforcer_moved` is read here EXPLICITLY and cannot be inferred from the root
    comparison, because by construction it does not move the root. Leaving it out
    is the one way the manifest split becomes a weakening: the lock would read
    green over a checker somebody edited.

    `root_moved` defaults to True when absent, which is the fail-red direction
    for a report built before the field existed. Not proved unmoved is not proved
    innocent.
    """
    return not (report.get("root_moved", True)
                or report["changed"] or report["added"]
                or report["removed"] or report["enforcer_moved"])


def enforcer_is_sole_cause(report: dict, anchor_status, anchor_value) -> bool:
    """True when a moved ENFORCER is the only thing keeping this lock red.

    The question `freeze_gate` asks before it permits a pytest session over a
    broken lock, and the narrowest question that unblocks the documented cure.
    Answered by asking `lock_state` what the state WOULD be with the enforcer
    axis emptied, never by re-deriving redness here: a widening of `lock_state`
    then narrows this automatically, where a second copy would keep permitting
    what the wider rule had started refusing.

    The whole anchor axis is asked, not just the content report. `lock_state`
    reaches red from four causes and three of them leave the contract exactly
    where it was, so a sole-cause test written against the content report alone
    would call a freeze nobody approved "permitted".

    Both anchor arguments are REQUIRED. `lock_state` shipped its pair as optional
    and the greener reading became the default, so a caller that forgot them was
    told LOCK HELD over an unapproved freeze. Repeating that shape here would
    repeat that defect one axis over.

    The relaxed state is compared against LOSS OF LOCK, never against LOCK HELD,
    and the difference is a whole reachable state rather than a nicety. The gate
    refuses a session on exactly one state and permits the other two, so "would
    this have been permitted without the enforcer" is the question asked here;
    "would it have been GREEN" is a stricter one nobody needs. LOCK UNCONFIRMED
    is the documented window between freezing and writing the hash down
    (`read_anchor`), it is amber, and the gate already exits 0 on it — so on such
    a tree a moved enforcer IS the only thing turning a permitted session into a
    refused one. Measured 2026-08-04 against `== LOCK_HELD`: an unrecorded anchor
    plus one edited enforcer exited 0 before the edit and 1 after it, so the
    original deadlock survived untouched inside that window while this function,
    the CHANGELOG and both documents all said it was cured.
    """
    if not report.get("enforcer_moved"):
        return False
    relaxed = dict(report, enforcer_moved=[])
    relaxed["held"] = content_held(relaxed)
    return lock_state(relaxed, anchor_status, anchor_value) != LOSS_OF_LOCK


# ============================================================
# The anchor
# ============================================================

ANCHOR_NONE = "none"
ANCHOR_MISSING = "missing"
ANCHOR_UNRECORDED = "unrecorded"
ANCHOR_RECORDED = "recorded"
ANCHOR_UNBOUND = "unbound"


def read_anchor(anchor_path: Path) -> Tuple[str, Optional[str]]:
    """Read the expected root hash out of a committed anchor artifact.

    Returns (ANCHOR_MISSING, None) when the artifact is gone,
    (ANCHOR_UNRECORDED, None) when it exists but carries no `canopus-anchor:`
    line, and (ANCHOR_RECORDED, hash) otherwise.

    The distinction matters. Unrecorded is the expected state between freezing
    and writing the hash down, so it is amber. Missing means a recorded anchor
    disappeared, which is a stronger signal than one that was never written, so
    it is red.
    """
    path = Path(anchor_path)
    if not path.is_file():
        return (ANCHOR_MISSING, None)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # Unreadable is not "absent": treat it like a vanished anchor.
        #
        # ValueError covers UnicodeDecodeError, which is the one an OSError-only
        # handler misses: a gate artifact holding a single non-UTF-8 byte raised
        # straight through anchor_state, resolve_anchor and freeze_gate, and a
        # raise in the gate fails OPEN. Measured on this tree, not reasoned:
        # one latin-1 byte in the artifact crashed the pytest session start.
        return (ANCHOR_MISSING, None)
    found: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(ANCHOR_PREFIX):
            value = stripped[len(ANCHOR_PREFIX):].strip().lower()
            if value:
                # LAST wins. A replaced anchor appends rather than overwriting,
                # so the artifact keeps the whole approval trail; pinning to the
                # first line would make every legitimate re-freeze read as a
                # disagreement forever.
                found = value
    return (ANCHOR_RECORDED, found) if found else (ANCHOR_UNRECORDED, None)


def parse_anchor_waiver(text: str, root_digest: str) -> str:
    """The waiver reason recorded beside *root_digest* in *text*, or "".

    `--contract-satisfied` accepts a wholly green contract, which is the right
    answer for a retake and the wrong one for a first freeze. Recording it only
    in `.canopus/history.jsonl` left it in a gitignored directory one `rm -rf`
    removes, so a claim about a waived refusal had no durable artifact behind
    it. `approve` therefore writes it onto the anchor beside the approval it
    belongs to, and this reads it back.

    BOUND to a hash, never "the last waiver in the file". An anchor accumulates
    one approval per retake, so a waiver taken three retakes ago must not be
    reported against a freeze that earned its redness honestly. Full digests,
    compared whole: a prefix comparison here would look rigorous and is not.

    Takes TEXT rather than a path, because the artifact has two copies and the
    committed one governs. Reading the blob is git's half of the job and lives in
    canopus_git, which this module may never import; the parsing is the same
    either way, and one parser is what keeps the two copies from being read by
    two subtly different rules.
    """
    wanted = (root_digest or "").strip().lower()
    if not wanted:
        return ""
    found = ""
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(SATISFIED_PREFIX):
            pending = stripped[len(SATISFIED_PREFIX):].strip()
        elif stripped.startswith(ANCHOR_PREFIX):
            # The pending waiver belongs to THIS approval and to no later one,
            # so it is consumed here whether or not the hash matched.
            if stripped[len(ANCHOR_PREFIX):].strip().lower() == wanted:
                found = pending
            pending = ""
    return found


def read_anchor_waiver(anchor_path: Path, root_digest: str) -> str:
    """The waiver in the artifact's WORKING copy, or "".

    The FALLBACK reader, and named as one. A waiver on the working file is an
    uncommitted diff in another repository: visible to a human who looks, and
    erasable with one `sed -i` or `git checkout --`. Measured, not reasoned:
    deleting the `canopus-contract-satisfied:` line from the working artifact
    dropped CONTRACT WAIVED off `canopus pack` while HEAD still carried it and
    the lock and approval lines did not move. The reader that prefers the
    COMMITTED copy is `resolve_anchor_waiver` in canopus_git; this one answers
    where there is no committed copy to consult, and it is what `approve` wrote.

    Answers rather than raising, matching read_anchor: an unreadable or non-UTF-8
    artifact reads as "no waiver recorded", and the reader that decides the LOCK
    has already called that artifact missing.
    """
    path = Path(anchor_path)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    return parse_anchor_waiver(text, root_digest)


def anchor_state(
    manifest: dict, override: Optional[str] = None
) -> Tuple[str, str, Optional[str]]:
    """Resolve (anchor path, status, recorded hash) for a manifest.

    One producer for the status string, shared by the CLI and the test gate. A
    second hand-rolled copy of this derivation is how a typo in one of them
    silently degrades every state to LOSS OF LOCK, because `lock_state` reads
    an unrecognised status as "recorded but disagreeing".

    ANCHOR_NONE is the manifest-carries-no-anchor case. The CLI refuses an
    anchorless freeze, so it is reachable only from a manifest written by the
    library directly or by an older CLI.
    """
    anchor = override or manifest.get("anchor") or ""
    if not anchor:
        return ("", ANCHOR_NONE, None)
    status, value = read_anchor(Path(anchor))
    return (anchor, status, value)


BINDING_INTACT = "intact"
BINDING_BROKEN = "broken"


def repo_binding_state(
    manifest: dict, current_status: str, current_identity: str
) -> Tuple[str, str]:
    """Is the anchor still in the repository the freeze was taken against?

    Pure string work. The measurement arrives from the caller for the same
    reason approval_state's git status does: this module is imported by the
    PreToolUse dispatcher on every write and may never reach for subprocess.

    Five outcomes, and the third and fifth are the whole point of wire 2.2:

      bound, same repository        intact
      unbound, still no repository  intact (the plain-folder operator)
      bound, a DIFFERENT repository broken
      bound, no repository or no git broken, and this is the blinding case
      unbound, now IN a repository  broken, because the freeze was taken blind

    The fourth line is why RED and not amber. Amber exits 0, so a builder who
    blinds the gate would still convert a red exit 1 into an amber exit 0 and
    keep the entire payoff. The bypass has to cost more than doing nothing.

    A manifest with no binding key at all reads as unbound rather than raising:
    this is called from the gate, and a raise in the gate fails OPEN.
    """
    # Anything that is not a mapping reads as unbound, for the same reason the
    # missing key does. A manifest that reached this function WITHOUT passing
    # through read_freeze is exactly the case the guard exists for, and
    # "_validate_manifest_shape already refuses that" is a guarantee about a
    # DIFFERENT function. Measured: a bare truthiness test let a string, a list
    # and an int through to `.get` and raised AttributeError, which the
    # PreToolUse dispatcher does not catch, so the hook logged an advisory and
    # CONTINUED while writes to frozen paths sailed past. The guard now lives in
    # `anchor_binding`, shared with root_hash and recompute, because it was
    # written here alone and its two siblings raised on the same input.
    binding = anchor_binding(manifest)
    was_bound = bool(binding.get("in_repo"))
    recorded = str(binding.get("identity") or "")

    if not was_bound:
        if current_status == REPO_PRESENT:
            return (BINDING_BROKEN,
                    "the freeze recorded the anchor OUTSIDE any repository and it "
                    "is inside one now, so the freeze was taken blind: release "
                    "and re-freeze")
        return (BINDING_INTACT, "")

    if current_status != REPO_PRESENT:
        return (BINDING_BROKEN,
                f"the freeze recorded the anchor inside a repository and git now "
                f"answers {current_status}, so the approval cannot be attributed")
    if current_identity != recorded:
        return (BINDING_BROKEN,
                "the anchor is inside a different repository than the freeze "
                "recorded")
    return (BINDING_INTACT, "")


def lock_state(
    report: dict,
    anchor_status: Optional[str] = None,
    anchor_value: Optional[str] = None,
) -> str:
    """Resolve the three-state indicator from a verify report plus the anchor.

    Omitting the anchor asks the CONTENT question alone: has anything the
    manifest recorded moved on disk. Every caller in this repository passes all
    three and gets the full answer; the two-axis reading exists for a caller
    holding a report and no anchor resolution, which is the shape a library user
    and every unit test have.

    Say the hazard rather than trust the default. The content-only reading is
    the GREENER of the two — it cannot see a missing anchor or one recording a
    different hash — so a caller that has an anchor and forgets to pass it is
    told LOCK HELD over a freeze nobody approved. `None` is the sentinel rather
    than ANCHOR_NONE precisely so that "no anchor was recorded" (amber, and a
    real state a manifest can carry) stays distinguishable from "the caller did
    not ask about the anchor".

    No prefix comparison anywhere: a truncated digest that looks rigorous and is
    not is worse than a full one, because a builder with a shell can brute-force
    a short prefix by appending whitespace to a frozen file.

    ANCHOR_UNBOUND is listed explicitly rather than left to the final line's
    `anchor_value == recomputed_root` comparison. It would fall red there today,
    by arithmetic on a None, and an invariant that holds by accident is one
    refactor away from not holding.
    """
    if not report["held"]:
        return LOSS_OF_LOCK
    if anchor_status is None:
        return LOCK_HELD
    if anchor_status in (ANCHOR_MISSING, ANCHOR_UNBOUND):
        return LOSS_OF_LOCK
    if anchor_status in (ANCHOR_NONE, ANCHOR_UNRECORDED):
        return LOCK_UNCONFIRMED
    return LOCK_HELD if anchor_value == report["recomputed_root"] else LOSS_OF_LOCK


# ============================================================
# The approval axis
# ============================================================

APPROVED = "APPROVED"
APPROVAL_UNVERIFIED = "APPROVAL UNVERIFIED"

_UNVERIFIED_REASONS = {
    "committed": "the committed artifact records a different hash",
    # Covers both worlds this status spans: an untracked artifact, and a tracked
    # one whose HEAD copy carries no line. Naming only the first would be false
    # for the second, which is the commoner case during a build.
    "uncommitted": "no approval is recorded in the committed state of the gate artifact",
    "no_repo": "the gate artifact is not in a repository, so the approval cannot be attributed",
    "no_git": "git is unavailable, so the approval cannot be read",
}


def approval_state(
    manifest_root: str, committed_status: str, committed_hash: Optional[str]
) -> Tuple[str, str]:
    """Was the frozen artifact the one a human approved?

    A third axis beside the lock and the attestation, and it answers a question
    neither of them can. The lock says the contract has not moved since the
    freeze. The attestation says it ran. Neither says the freeze was ever
    approved, and before this axis existed the tool wrote the anchor line itself
    and then verified the line it had written.

    Pure string work, and the git status arrives from the caller, because this
    module is imported by the PreToolUse dispatcher on every write and may never
    reach for subprocess.

    An unrecognised status resolves UNVERIFIED with the status named, rather than
    raising: this is called from the test gate, which must never raise.

    The truthiness guard on committed_hash is deliberate. Without it, two empty
    or two None values compare equal and the axis reads APPROVED over nothing at
    all. read_committed_anchor never returns that pair today, so the guard is
    insurance rather than a fix, and it is here because this is the function that
    decides whether a human approved something: it should not depend on a
    distant module's invariant to avoid a false green.
    """
    if committed_status == "committed" and committed_hash and committed_hash == manifest_root:
        return (APPROVED, "")
    reason = _UNVERIFIED_REASONS.get(
        committed_status, f"unrecognised approval status {committed_status!r}"
    )
    if committed_status == "committed" and committed_hash:
        reason = f"{reason}: {committed_hash}"
    return (APPROVAL_UNVERIFIED, reason)


# ============================================================
# Membership (consumed by the PreToolUse dispatcher)
# ============================================================

def frozen_reason(rel_posix: str, manifest: dict) -> Optional[str]:
    """Why *rel_posix* is frozen, or None. Pure string work, no disk access.

    The dispatcher calls this on every Write/Edit, so it must stay cheap: no
    hashing, no stat calls.

    Two questions, and a third that was tried and WITHDRAWN. Wire 2.3 briefly
    also refused any Write that would CREATE a watched top-level directory, on
    the argument that a guard watching directories should refuse the one Write
    that installs one, since the Write tool makes missing parents. The argument
    is sound and the deny was not: measured under a held freeze, an ordinary
    note written under the workspace's private `threads/` tree was refused,
    because that name is an identifier-shaped top-level directory which is
    data-routed and absent from a fresh engine clone. `check_canopus_freeze`
    also runs BEFORE `check_protect_personal_threads` in the dispatcher's chain,
    so the deny took writes the workspace's own design expects to reach that
    later check, and it did so for every frozen slice. Detection at `verify` is
    kept and prevention is not: a guard that reddens on ordinary work is one an
    operator learns to release around, which is worse than no guard. The
    asymmetry is real, recorded on the open list in `docs/EXTENDING.md`, and
    deliberate rather than overlooked.

    So the deny is basename-shaped: a path is refused when it IS a frozen file,
    when it sits inside a recursively frozen directory, or when its own basename
    would join a guard's watched composition. A path whose FIRST component does
    not exist yet is not this function's business.

    ENFORCER paths are deliberately NOT refused, from the manifest-split slice
    onward. They live in CONTENT_KEY, which this function does not read, so an
    edit to the code that does the measuring reaches disk and is caught by
    `verify_manifest` as `enforcer_moved` instead. That is the trade the split
    makes on purpose: denying the write made every enforcer fix cost a release
    window plus a full re-approval, which is what 21 of the ledger's 39 retakes
    were. The change still cannot pass unnoticed, and the cure (`repin`) refuses
    until the new bytes are COMMITTED, so it lands as a readable diff in git
    rather than as a hash line in a gitignored directory.
    """
    if rel_posix in manifest["files"]:
        return f"{rel_posix} is a frozen contract file"
    parent, _, name = rel_posix.rpartition("/")
    for dir_rel, entry in manifest["dirs"].items():
        # The tree root is stored as the empty string by `_guard_ancestors`, and
        # printing it raw made the deny read "the guarded composition of /" --
        # the filesystem root, which is not what is guarded. `cmd_status` already
        # spells the same entry `./`; one spelling in both places.
        shown = dir_rel or "."
        if entry["mode"] == "recursive":
            if rel_posix == dir_rel or rel_posix.startswith(dir_rel + "/"):
                return f"{rel_posix} is inside the frozen directory {shown}/"
            continue
        if parent == dir_rel and matches_guard(name, entry["names"]):
            # The same filter verify measures with. A deny wider than the
            # measurement refuses writes nothing would have reported, which is
            # how a discipline tool becomes an obstacle.
            return f"{rel_posix} would join the guarded composition of {shown}/"
    return None


# ============================================================
# On-disk state
# ============================================================

class FreezeCorrupt(FreezeError):
    """A freeze manifest exists but is unreadable or carries an unknown recipe.

    Handled fail-closed by the dispatcher: a corrupt manifest denies every write
    rather than silently unlocking the contract, matching the house convention
    where a broken routing map forces `private` and an unknown action type floors
    at `gated`.

    The escape is `release --force`, which is LOGGED. An escape that leaves no
    record turns every false alarm into a routine of unlogged deletions, and
    after the third one the operator stops reading the alarm at all. Alert
    fatigue is the adversary here, not the model.
    """


def freeze_state_path(root: Path) -> Path:
    return Path(root) / FREEZE_DIRNAME / FREEZE_FILENAME


def history_state_path(root: Path) -> Path:
    return Path(root) / FREEZE_DIRNAME / HISTORY_FILENAME


_DIR_MODES = ("members", "recursive")
_STR_SCALAR_KEYS = ("label", "frozen_at", "anchor", "git_sha", "root")


def _require(condition: bool, message: str) -> None:
    """Raise FreezeCorrupt with *message* unless *condition* holds."""
    if not condition:
        raise FreezeCorrupt(message)


def _validate_manifest_shape(manifest: dict, path: Path) -> None:
    """Validate every key and value type build_manifest() ever produces.

    read_freeze() calls this right after JSON decoding and the recipe check,
    before recompute()/verify_manifest()/frozen_reason() dereference a single
    key.

    The rule is validate the whole closed shape, not one escape at a time.
    build_manifest()'s output is small and closed, so a single complete pass is
    proportionate, and it is what stops the next unchecked corner from becoming
    the next defect. `entry["hash"]` is validated even though no caller in this
    repo reads it: leaving one sibling key unchecked is precisely the gap this
    function exists to close.

    Every violation raises FreezeCorrupt naming the offending key or path and
    the type expected, so a syntactically valid-but-wrong-shaped manifest
    fails here -- not as an uncaught TypeError/AttributeError deep in a caller
    that only expects FreezeCorrupt/OSError (the PreToolUse dispatcher catches
    exactly those two and denies fail-closed; anything else falls through its
    outer catch-all, logs an advisory, and continues -- fail OPEN).
    """
    for key in (*_STR_SCALAR_KEYS, "files", CONTENT_KEY, "dirs", "baseline",
                "plugins", "anchor_repo"):
        _require(key in manifest, f"freeze manifest at {path} is missing {key!r}")

    for key in _STR_SCALAR_KEYS:
        value = manifest[key]
        _require(
            isinstance(value, str),
            f"freeze manifest at {path} has a non-string {key!r} value "
            f"({type(value).__name__}), expected a string",
        )

    # Both digest maps, checked by ONE loop rather than by a copy each. The
    # enforcer map reaches `enforcer_map`, which answers EMPTY for anything that
    # is not a mapping — the right posture where a raise fails open, and the
    # wrong one here, where an empty map would quietly stop `verify` reporting
    # any enforcer at all. read_freeze is the layer that gets to refuse.
    for key in ("files", CONTENT_KEY):
        digests = manifest[key]
        _require(
            isinstance(digests, dict),
            f"freeze manifest at {path} has a non-dict {key!r} value "
            f"({type(digests).__name__}), expected a dict",
        )
        for rel, digest in digests.items():
            _require(
                isinstance(rel, str),
                f"freeze manifest at {path} has a non-string key in {key!r} "
                f"({type(rel).__name__}), expected a string",
            )
            _require(
                isinstance(digest, str),
                f"freeze manifest at {path} has a non-string value for "
                f"{key}[{rel!r}] ({type(digest).__name__}), expected a string",
            )

    dirs = manifest["dirs"]
    _require(
        isinstance(dirs, dict),
        f"freeze manifest at {path} has a non-dict 'dirs' value "
        f"({type(dirs).__name__}), expected a dict",
    )
    for rel, entry in dirs.items():
        _require(
            isinstance(rel, str),
            f"freeze manifest at {path} has a non-string key in 'dirs' "
            f"({type(rel).__name__}), expected a string",
        )
        # A dir entry without its recorded member list would make every existing
        # member read as newly added. Refuse it rather than report a false alarm.
        _require(
            isinstance(entry, dict)
            and "mode" in entry and "hash" in entry
            and "members" in entry and "names" in entry,
            f"freeze manifest at {path} has an incomplete entry for directory {rel!r}",
        )
        mode = entry["mode"]
        # mode gates the recompute()/frozen_reason() branch between "recursive"
        # and "members" handling. An unrecognised mode string does not crash --
        # recompute() just checks `entry["mode"] == "recursive"` -- it silently
        # falls through to shallow handling, downgrading a recursive directory
        # freeze to a shallow one without any error. That silent weakening of
        # the guarantee is worse than a crash, so it is refused here explicitly.
        _require(
            isinstance(mode, str) and mode in _DIR_MODES,
            f"freeze manifest at {path} has an unrecognised 'mode' {mode!r} for "
            f"directory {rel!r}, expected one of {_DIR_MODES}",
        )
        _require(
            isinstance(entry["hash"], str),
            f"freeze manifest at {path} has a non-string 'hash' for directory "
            f"{rel!r} ({type(entry['hash']).__name__}), expected a string",
        )
        # An empty or non-list 'names' would make matches_guard() answer False
        # for every basename, silently reducing the guard to nothing while the
        # manifest still reports a guarded directory. Refused, like an
        # unrecognised mode, because a guard that watches nothing and says it
        # watches something is worse than no guard at all.
        names = entry["names"]
        _require(
            isinstance(names, list) and names,
            f"freeze manifest at {path} has an empty or non-list 'names' for "
            f"directory {rel!r}, expected a non-empty list of patterns",
        )
        for pattern in names:
            _require(
                isinstance(pattern, str),
                f"freeze manifest at {path} has a non-string pattern in 'names' "
                f"for directory {rel!r} ({type(pattern).__name__}), expected a string",
            )

        members = entry["members"]
        _require(
            isinstance(members, list),
            f"freeze manifest at {path} has a non-list 'members' for directory "
            f"{rel!r} ({type(members).__name__}), expected a list",
        )
        for member in members:
            _require(
                isinstance(member, str),
                f"freeze manifest at {path} has a non-string member in 'members' "
                f"for directory {rel!r} ({type(member).__name__}), expected a string",
            )

    baseline = manifest["baseline"]
    _require(
        isinstance(baseline, dict),
        f"freeze manifest at {path} has a non-dict 'baseline' value "
        f"({type(baseline).__name__}), expected a dict",
    )
    for rel, count in baseline.items():
        _require(
            isinstance(rel, str),
            f"freeze manifest at {path} has a non-string key in 'baseline' "
            f"({type(rel).__name__}), expected a string",
        )
        # bool is a subclass of int; a JSON `true` here would silently compare
        # equal to a collected count of 1.
        _require(
            isinstance(count, int) and not isinstance(count, bool),
            f"freeze manifest at {path} has a non-integer 'baseline' value for "
            f"{rel!r} ({type(count).__name__}), expected an integer",
        )

    # A non-list here would reach `manifest_plugins`, which iterates it: a JSON
    # string iterates as characters, so `"xdist"` would become a five-name
    # baseline and every attestation would refuse for five plugins nobody
    # loaded. Refused with the key named, like every other shape above.
    plugins = manifest["plugins"]
    _require(
        isinstance(plugins, list),
        f"freeze manifest at {path} has a non-list 'plugins' value "
        f"({type(plugins).__name__}), expected a list",
    )
    for name in plugins:
        _require(
            isinstance(name, str),
            f"freeze manifest at {path} has a non-string entry in 'plugins' "
            f"({type(name).__name__}), expected a string",
        )

    binding = manifest["anchor_repo"]
    _require(
        isinstance(binding, dict),
        f"freeze manifest at {path} has a non-dict 'anchor_repo' value "
        f"({type(binding).__name__}), expected a dict",
    )
    _require(
        "in_repo" in binding and isinstance(binding["in_repo"], bool),
        f"freeze manifest at {path} has a missing or non-boolean "
        f"anchor_repo['in_repo']",
    )
    _require(
        "identity" in binding and isinstance(binding["identity"], str),
        f"freeze manifest at {path} has a missing or non-string "
        f"anchor_repo['identity']",
    )


def read_freeze(root: Path) -> Optional[dict]:
    """Load the active freeze manifest, or None when none is active.

    Raises FreezeCorrupt when a manifest exists but cannot be trusted. The
    caller must never treat that as "no freeze".
    """
    path = freeze_state_path(root)
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FreezeCorrupt(f"freeze manifest at {path} is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise FreezeCorrupt(f"freeze manifest at {path} is not a JSON object")
    if manifest.get("recipe") != RECIPE:
        raise FreezeCorrupt(
            f"freeze manifest at {path} carries recipe {manifest.get('recipe')!r}, "
            f"expected {RECIPE!r}"
        )
    _validate_manifest_shape(manifest, path)
    return manifest


def write_freeze(root: Path, manifest: dict) -> None:
    """Write the manifest atomically (tmp file plus os.replace)."""
    atomic_write_text(freeze_state_path(root), json.dumps(manifest, indent=2) + "\n")


def clear_freeze(root: Path) -> None:
    """Remove the active manifest AND the attestation that spoke for it.

    Idempotent, and never parses either file, so it works on a damaged one.

    The attestation goes with the manifest because the root hash is
    deterministic over frozen content plus the anchor path: leaving the record
    behind meant that re-freezing identical test content revived it, and a
    brand-new freeze with zero runs since printed ATTESTED. Reproduced, not
    theorised. A record that outlives the contract it attests is a false green
    waiting for a coincidence.
    """
    freeze_state_path(root).unlink(missing_ok=True)
    attestation_state_path(root).unlink(missing_ok=True)


def append_history(
    root: Path,
    event: str,
    *,
    digest: str,
    label: str,
    reason: str = "",
    kind: str = "",
    extra: Optional[dict] = None,
) -> dict:
    """Append one line to the ledger. Never rewrites, never truncates.

    A separate file from the manifest on purpose: the logged escape has to work
    when the manifest cannot be parsed.

    WHAT THE LEDGER RECORDS, stated so the gap is a known property rather than
    a discovered one. It records operator intent through the CLI: `freeze`,
    `release`, `force_release`, `repin`, and a failing `verify`. It does NOT record the
    test gate. A passing gate writes nothing, so the ABSENCE of a `verify_fail`
    line is ambiguous between "verified clean many times" and "never verified
    at all" — the gate's evidence is its exit code in the test output, not a
    line here, and adding a line per gate run would bury the four events that
    matter under noise.

    It also lives in the same gitignored directory as the manifest, so it is
    evidence against an EDIT to freeze.json, never against deletion of the
    directory: `rm -rf .canopus` takes the ledger with it. The durable record
    of an approved contract is the anchor artifact, committed in another
    repository.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "root": digest,
        "label": label,
        "reason": reason,
        # Which KIND of release this was, for the two release events and empty
        # for every other one. A release you will close and the end of a slice
        # looked identical in this ledger before wire 2.2, so nothing could act
        # on the difference and the gate stayed silent over an unlocked tree.
        "kind": kind,
    }
    # `extra` carries the fields ONE event kind needs and the others have no use
    # for: a `repin` records the pin it replaced, the pin it took, the enforcer
    # files that moved and the commit carrying them. Merged UNDER the six common
    # keys rather than over them, so a caller cannot overwrite the timestamp,
    # the event name or the label — the three fields every reader of this ledger
    # keys on.
    entry = {**(extra or {}), **entry}
    path = history_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not atomic_write_text: that primitive is a full-file
    # tmp-plus-replace overwrite, incompatible with append-only growth, and
    # append-only is the entire point of this ledger.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


REPIN_EVENT = "repin"


def repin_enforcer(root: Path, *, reason: str, git_sha: str = "") -> dict:
    """Re-record the ENFORCER map under the freeze that is already held.

    The cheap half of what used to be a six-command retake. It recomputes the
    content map from disk, writes it into the freeze state, appends one `repin`
    line to the ledger, and clears the attestation. It does NOT touch `files`,
    `dirs`, `baseline` or `root`.

    That last sentence is the security property, not a description of the
    implementation's shape. A `repin` that also refreshed the contract digests
    would let a builder edit a frozen test, run one command, and hold a green
    lock over a contract nobody re-approved — the one thing the whole standard
    exists to prevent, delivered by its own convenience feature. The contract
    root is left exactly as the committed approval recorded it, so a contract
    edit stays red through a re-pin and every re-pin after it.

    The attestation goes WHEN SOMETHING MOVED, and that is correct rather than
    merely tolerable. The enforcer set holds the test runner, the interpreter
    chooser and `conftest.py`: a green run recorded before those bytes changed
    was produced by a DIFFERENT checker, so keeping it would let an edited
    enforcer inherit the previous run's word. The suite runs again. That cost is
    irreducible while the runner is inside the set, and the saving is the other
    five commands.

    When NOTHING moved it stays, and the reason is the same sentence read
    honestly: the checker's bytes are identical, so the recorded run was produced
    by exactly this checker and still speaks for it. Clearing it anyway charged a
    full suite re-run to an operator who had done nothing but CHECK, which is a
    tax on the one behaviour this command exists to make cheap.

    Refused without a REASON. An unexplained re-pin is indistinguishable from a
    re-baseline, which is the sentence `approve --replace` already uses for the
    same act one layer up; a recorded pin with no account of why is a log entry,
    not evidence.

    Refused when a recorded enforcer file is GONE. Rewriting the map from what
    is on disk would silently drop the missing path out of the enforcer set, so
    the checker somebody deleted would stop being watched from then on — the
    silent narrowing this module refuses everywhere else. A genuinely changed
    enforcer set is a new freeze, and the ledger already has a cause for it
    (`frozen-set-wrong`).

    ACCEPTED when nothing moved. The pin simply does not change, and the line
    still lands. Refusing would be the tidier rule and the wrong one: `repin` is
    what an operator reaches for when they believe the enforcer moved, and
    telling them "nothing to do" on a tree they have not looked at closely is a
    worse answer than recording that they checked.

    *git_sha* is passed IN, never derived. This module is imported by the
    PreToolUse dispatcher on every write and may never reach for subprocess; the
    caller that can run git (`scripts/canopus.py`) is also the caller that
    refuses the re-pin while the changed bytes are uncommitted.
    """
    reason = " ".join((reason or "").split())
    if not reason:
        raise FreezeError(
            "a re-pin needs a reason: it re-records the bytes of the code that "
            "does the measuring, and an unexplained one is indistinguishable "
            "from a quiet re-baseline"
        )
    manifest = read_freeze(root)
    if manifest is None:
        raise FreezeError(
            "no freeze is held here, so there is no enforcer pin to replace; "
            "`repin` corrects a held lock and cannot create one"
        )

    resolved_root = Path(root).resolve()
    recorded = enforcer_map(manifest)
    current: dict[str, str] = {}
    missing: list[str] = []
    for rel in recorded:
        candidate = resolved_root / rel
        if candidate.is_file() and not candidate.is_symlink():
            current[rel] = file_digest(candidate)
        else:
            missing.append(rel)
    if missing:
        raise FreezeError(
            f"the freeze records an enforcer file that is not on disk: "
            f"{', '.join(missing)}. Re-pinning would drop it out of the "
            f"enforcer set and stop watching it; restore it, or take a new "
            f"freeze if the set itself is wrong."
        )

    previous_pin = enforcer_pin(manifest)
    updated = dict(manifest)
    updated[CONTENT_KEY] = dict(sorted(current.items()))
    pin = enforcer_pin(updated)
    changed = sorted(rel for rel, digest in recorded.items() if current[rel] != digest)

    # The ledger first, then the state, for the reason `cmd_release` states: an
    # unwritable ledger must leave the tree exactly as it was rather than move
    # the pin with no line saying it moved.
    event = append_history(
        root, REPIN_EVENT,
        digest=manifest.get("root", ""), label=manifest.get("label", ""),
        reason=reason,
        extra={"previous_pin": previous_pin, "pin": pin, "changed": changed,
               "git_sha": git_sha},
    )
    write_freeze(root, updated)
    if changed:
        attestation_state_path(root).unlink(missing_ok=True)
    return event


def read_ledger(root: Path) -> list[dict]:
    """Every readable line of the append-only ledger, oldest first.

    Damaged lines are skipped rather than raising: the ledger is evidence, and a
    reader that refuses to show the other nine entries because one is corrupt is
    less useful than one that shows nine.

    Lives beside the writer from wire 2.2, because the gate reads it now and the
    gate may never import the pack (the pack reaches for git through
    canopus_git, and this module is loaded by the PreToolUse dispatcher on every
    write). Stdlib only, like everything else here.
    """
    path = history_state_path(root)
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError, and OSError alone did not. A
        # ledger holding one non-UTF-8 byte raised straight through this reader,
        # so `canopus pack` tracebacked on a module whose docstring promises to
        # answer rather than raise, and from wire 2.2 the same call is on
        # freeze_gate's path, where a raise fails OPEN.
        return []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


RELEASE_EVENTS = ("release", "force_release")
# The events that take the lock or give it back. Everything else the ledger
# carries (`approve`, `anchor_replaced`, `verify_fail`) describes a state without
# changing who holds it, so it is stepped over rather than read as an answer.
LOCK_EVENTS = ("freeze", *RELEASE_EVENTS)


def last_lock_event(entries: Sequence[dict]) -> Optional[dict]:
    """The most recent entry that took the lock or gave it back, or None.

    One walk, read by both questions below. They are two readings of the SAME
    fact and were one function's early return before wire 2.2: `open_release_window`
    answered None the moment it saw a `freeze`, which is correct for its own
    question and threw away the other answer entirely. That is how deleting
    `.canopus/freeze.json` became QUIETER than releasing it, inverting the
    incentive the ledger exists to create.
    """
    for entry in reversed(list(entries)):
        if entry.get("event") in LOCK_EVENTS:
            return entry
    return None


def open_release_window(entries: Sequence[dict]) -> Optional[dict]:
    """The release window still standing open, or None.

    A window is open when the LAST lock event in the ledger is a release that
    named itself a window. A later freeze closes it, which is what makes the
    gate's amber line self-clearing rather than something an operator learns to
    dismiss.

    An entry with no `kind` reads as a ship. Every entry written before wire 2.2
    has none, and reading those as windows would turn a quiet past amber
    retroactively on the first pytest run after the update.
    """
    entry = last_lock_event(entries)
    if entry is None or entry.get("event") == "freeze":
        return None
    return entry if entry.get("kind") == "window" else None


def unreleased_freeze(entries: Sequence[dict]) -> Optional[dict]:
    """The freeze the ledger says is still held, or None.

    Read beside a MISSING manifest and nowhere else, where the pair is the whole
    signal: the ledger records a freeze that no release closed, and the manifest
    that freeze wrote is not on disk. `rm .canopus/freeze.json` produces exactly
    that pair, and before wire 2.2 nothing read it, so the sanctioned
    `release --window` printed an amber line at every later pytest session start
    while the deletion printed nothing at all.

    `rm -rf .canopus` still says nothing, and that is a property of the
    directory rather than a gap here: the ledger goes with it. This reader closes
    the cheaper half, where the evidence survives.
    """
    entry = last_lock_event(entries)
    return entry if entry is not None and entry.get("event") == "freeze" else None


# ============================================================
# Attestation: did the frozen tests actually run?
# ============================================================
#
# The manifest answers "did the contract move". It cannot answer "did the
# contract run", and a builder that cannot edit a frozen test can simply decline
# to run it: pytest -k, --deselect, --ignore, --lf and a bare path argument all
# reach green with every frozen byte intact.
#
# From wire 2 a bare path or a node id is ALSO caught, for any file carrying a
# freeze-time baseline: the record compares what was collected against how many
# items that file yields when collected whole, so a subset reports 1 of 7. A
# frozen test file with no baseline entry keeps the wire 1 comparison.
#
# This records, it does not block, and nothing here is fatal. Failing a filtered
# run would charge every inner-loop iteration for a hole that a passive record
# closes at the point of comparison, and a primitive that forbids `pytest -k`
# gets routed around in its first week. An earlier revision kept one fatal case
# and measurement removed it: telling "removed from collection" apart from "the
# operator named one path" needs option sniffing, and option sniffing is exactly
# what proved unreliable.

ATTEST_FILENAME = "attest.json"
# v3 from wire 3.2: the record now carries a `tree` key describing the whole
# working copy at finish, not just the frozen bytes, and is refused when it
# cannot be described or when it moved between start and finish. A v2 record
# reads NOT ATTESTED through the recipe check in `attestation_state` below,
# which is the fail-closed direction and needs no migration: a record written
# before the tree was captured cannot testify about a comparison it never made.
ATTEST_RECIPE = "canopus-attest-v3"
ATTESTED = "ATTESTED"
NOT_ATTESTED = "NOT ATTESTED"
# The recipe `canopus_tree.tree_state` stamps into its own state dict. Kept
# here, beside ATTEST_RECIPE, and imported by canopus_tree.py rather than the
# other way round, so the two modules cannot come to disagree about the name
# without canopus_freeze's import tail growing a subprocess dependency it may
# never carry.
TREE_RECIPE = "canopus-tree-v1"


def attestation_state_path(root: Path) -> Path:
    """Where the attestation record lives, beside the manifest it attests."""
    return Path(root).resolve() / FREEZE_DIRNAME / ATTEST_FILENAME


def frozen_test_files(manifest: dict, patterns: Sequence[str]) -> list[str]:
    """Frozen paths that pytest would collect as test modules.

    Patterns come from the running pytest config (`python_files`) rather than a
    hardcoded `test_*.py`, so a repo that renames its convention does not
    silently attest an empty set while reporting green.
    """
    files = manifest.get("files") or {}
    return sorted(
        rel for rel in files
        if any(fnmatch.fnmatch(PurePosixPath(rel).name, pattern) for pattern in patterns)
    )


def tally_collection(frozen: Sequence[str], collected_rels: Iterable[str]) -> dict:
    """Seed the per-file counters from what pytest actually collected.

    Every frozen test file gets an entry even when nothing was collected for it,
    because a zero is the signal that matters: it means the file was filtered
    out, ignored, or removed from collection entirely.
    """
    counts = {
        rel: {"collected": 0, "passed": 0, "failed": 0, "skipped": 0, "deselected": 0}
        for rel in frozen
    }
    for rel in collected_rels:
        if rel in counts:
            counts[rel]["collected"] += 1
    return counts


def build_attestation(
    *,
    root_digest: str,
    frozen_tests: dict,
    exit_status: int,
    attested_at: str,
    enforcer_moved: Iterable[str],
    baseline: Optional[dict] = None,
    process: Optional[dict] = None,
    plugin_baseline: Optional[Iterable[str]] = None,
    tree_at_start: Optional[dict] = None,
    tree_at_finish: Optional[dict] = None,
) -> dict:
    """Assemble the record written at session finish. Pure: no disk, no pytest.

    Measures COLLECTION, never INVOCATION. An earlier revision classified pytest
    options (-k, -m, --ignore and friends) and shipped four defects: it marked
    the canonical gate's own `-m "not acceptance"` as a filter and so could never
    attest anything, it could not see a bare path argument at all, its fatal
    branch fired on ordinary single-test runs, and it knew nothing about parallel
    workers. Counting what was collected, deselected, and reported answers all
    four without knowing how pytest was called.

    Every false condition leaves a plain-language string in `reasons`, because an
    operator reading NOT ATTESTED at the sign-off gate needs to know which one it
    was.

    Skips are counted and deliberately do not void the record: a frozen test's
    own platform guard is legitimate and its bytes are frozen, while a skip
    injected from an unfrozen sibling conftest is the composition guard's job.

    A freeze carrying no test files attests nothing rather than everything. The
    same rule already governs verify, which refuses to print a green line when
    there is no contract to check.

    The node-id subset case IS caught, from wire 2 onward. A contract frozen with
    --contract carries a per-file item count taken at freeze time, so `pytest
    tests/contract/s/test_a.py::test_one` reports 1 against 7 and does not
    attest. A frozen test file with no baseline entry keeps the wire 1
    behaviour, where collected is compared only against what was reported.

    `process` describes what CONFIGURED the interpreter this record speaks for:
    the registered plugins, the parsed `-p` option, the PYTEST_ names in the
    environment, the launcher, and each xdist worker's plugin list. None when
    nothing described the process, which is every caller written before the
    field existed, and which reads as damage rather than as innocence. A single
    WORKER entry that is not a list of names says the same thing about one
    worker, and is read the same way.

    `plugin_baseline` is the plugin set the freeze captured. The comparison
    against it is ONE refusal, not a list of blocked routes: an entry-point
    plugin, a `-p` on argv, a `-p` inside PYTEST_ADDOPTS and a `-p` inside an
    ini `addopts` all leave a name the freeze never saw. That is why `-p` is
    never banned here: banning it forbade PYTEST_DISABLE_PLUGIN_AUTOLOAD plus an
    explicit `-p` per allowed plugin, the only measured cure for the entry-point
    route, so the first design banned its own cure.

    Said no wider than it is true, because this repository has already had to
    retract one claim of this shape. The comparison covers every plugin from a
    DISTRIBUTION and every in-tree plugin pytest did not register by COLLECTION,
    whatever route loaded it. A COLLECTED conftest outside the frozen contract
    directory is recorded as provenance and not compared, because which conftests
    collection loads depends on what was collected. Closing that one needs a different instrument, and it
    is on the open list rather than covered by this sentence.

    Both directions are refused. A plugin that VANISHED changed what the run
    measured just as surely as one that appeared, and a comparison that only
    looked for additions would call that honest.

    The names compared are the identities `process_facts` derives, never pytest
    registration names, and the set it hands over is every `dist:` identity plus
    every `intree:` identity pytest did not register as a collected conftest.
    Both are wire 2.3 measurements rather than taste, and both are argued where
    they are computed (`canopus_gate._plugin_identity`,
    `canopus_gate.process_facts`).
    """
    reasons: list[str] = []
    # First, because it disqualifies the WHOLE record rather than one file. The
    # enforcer set holds the test runner, the interpreter chooser and
    # `conftest.py`, so a run taken while those bytes differ from the frozen ones
    # was produced by a DIFFERENT checker and cannot speak for this freeze.
    #
    # This is the price of the gate's one relaxation, and it is the reason that
    # relaxation is safe. `freeze_gate` now permits a pytest session when a moved
    # enforcer is the sole red cause, because the documented cure needs a session
    # to reach a commit. Permitting the RUN while refusing the CLAIM is what
    # keeps that from being a hole.
    #
    # The root hash cannot carry this. `manifest-split` took the enforcer digests
    # out of the payload on purpose, so a moved enforcer leaves `root_digest`
    # exactly where it was and the record's root comparison sees nothing.
    #
    # REQUIRED, never defaulted: an optional argument here would default every
    # un-updated caller to "nothing moved", which is the greener reading and the
    # exact fail-open shape `lock_state`'s optional anchor pair shipped.
    moved = sorted(enforcer_moved)
    if moved:
        reasons.append(
            "an enforcer file did not match the freeze while this run was in "
            f"progress, so a different checker produced it: {', '.join(moved)}")
    if not frozen_tests:
        reasons.append("the freeze contains no test files to attest")
    expected_counts = baseline or {}
    for rel, counts in sorted(frozen_tests.items()):
        collected = counts.get("collected", 0)
        reported = counts.get("passed", 0) + counts.get("skipped", 0)
        expected = expected_counts.get(rel)
        if not collected:
            reasons.append(f"frozen test file collected nothing: {rel}")
        elif counts.get("deselected", 0):
            reasons.append(
                f"frozen test file had {counts['deselected']} items deselected: {rel}"
            )
        elif expected is not None and collected != expected:
            reasons.append(
                f"frozen test file collected {collected} of {expected}: {rel}"
            )
        if counts.get("failed", 0):
            reasons.append(f"frozen test file reported failures: {rel}")
        elif collected and reported != collected:
            reasons.append(
                f"frozen test file reported an incomplete tally: {rel} "
                f"({reported} of {collected})"
            )
    if not isinstance(process, dict):
        reasons.append(
            "the process configuration was not recorded, so this run cannot be "
            "distinguished from one configured to lie about it"
        )
    elif plugin_baseline is None:
        # The same rule a freeze with no test files already gets: with nothing to
        # compare against, every plugin set is equally acceptable, which is the
        # state this exists to end.
        reasons.append("the freeze recorded no plugin baseline to compare against")
    else:
        realized = set(process.get("plugins") or {})
        baseline_plugins = set(plugin_baseline)
        for name in sorted(realized - baseline_plugins):
            reasons.append(f"a plugin the freeze did not record was loaded: {name}")
        for name in sorted(baseline_plugins - realized):
            reasons.append(f"a plugin the freeze recorded was absent: {name}")
        for index, worker in enumerate(process.get("workers") or ()):
            # Against the BASELINE, never against the controller. Under -n auto
            # the workers execute the tests the controller only records, so a
            # controller-side reading describes an interpreter that ran nothing;
            # and the controller legitimately carries plugins no worker does
            # (xdist's dsession and terminaldistreporter) while the workers carry
            # the nested conftests only a collecting process loads. Measured on
            # 16 workers. Under the identity normalisation both fold into the
            # same `dist:` entries, so this comparison is the stricter one it
            # reads as: every worker must match the freeze, and therefore its
            # siblings. The controller is held to the same set two loops above.
            if not isinstance(worker, (list, tuple, set, frozenset)):
                # The recorder ships None for a worker it could not describe, and
                # this is the same rule the missing process block gets a few
                # lines above: an interpreter nobody could describe is damage,
                # not innocence. Under -n auto it is the interpreter that RAN the
                # frozen tests, so silence here is worse than silence there.
                reasons.append(
                    f"xdist worker {index} could not be described, so what it "
                    f"loaded is unknown"
                )
                continue
            if set(worker) != baseline_plugins:
                reasons.append(
                    f"xdist worker {index} loaded a different plugin set than the "
                    f"freeze recorded: {sorted(set(worker) ^ baseline_plugins)}"
                )
    if not _usable_tree_state(tree_at_finish) or not _usable_tree_state(tree_at_start):
        # An attestation binds to the frozen bytes and to nothing else, and the
        # code under test is by design NOT frozen. Without this, breaking the
        # implementation and running nothing at all left `verify` reading
        # ATTESTED: measured, on a scratch tree, before any of this was argued.
        reasons.append(
            "this run recorded no usable description of the tree it ran "
            "against, so this record cannot perish when the code moves")
    else:
        drifted = tree_drift(tree_at_start, tree_at_finish)
        # `path`, not `moved`: `moved` is bound at the top of this function to the
        # moved-ENFORCER list, and reusing it here would leave one name carrying
        # two unrelated meanings in one scope. Harmless today, because the first
        # binding's only reader finishes above -- and that is exactly the kind of
        # latent trap the next person to add a check down here walks into.
        for path in drifted[:5]:
            reasons.append(f"the tree changed while the run was in progress: {path}")
        if len(drifted) > 5:
            # Five is a display bound, not a claim about how many there were --
            # the same rule `_print_attestation` states for its own truncation
            # of this list on the way out. Left silent, a caller that reads
            # `reasons` directly (or a CLI whose own "(and N more)" is computed
            # from this already-capped list) sees 5 of however many actually
            # moved and has no way to know the difference.
            reasons.append(
                f"the tree changed while the run was in progress: "
                f"(and {len(drifted) - 5} more, not named here)")
    if exit_status != 0:
        reasons.append(f"pytest exited {exit_status}")

    return {
        "recipe": ATTEST_RECIPE,
        "root": root_digest,
        "attested": not reasons,
        "reasons": reasons,
        "exit_status": exit_status,
        "attested_at": attested_at,
        "frozen_tests": {rel: dict(counts) for rel, counts in sorted(frozen_tests.items())},
        "process": process,
        "tree": tree_at_finish if _usable_tree_state(tree_at_finish) else None,
    }


def _counters_are_numeric(frozen_tests) -> bool:
    """True when `frozen_tests` is the dict-of-int-counters shape readers assume.

    The reporting side sums these counters (`sum(entry["passed"] ...)`), and a
    record whose counter is a string turned `canopus verify` into a raw
    TypeError traceback: the reporter's exception type is outside the
    FreezeError/FreezeCorrupt/OSError set main() catches, so it escaped the
    handler that exists precisely to stop the guarantee layer printing a stack
    trace. Validating the shape here keeps that fix in ONE place for every
    reader, and matches this function's stated posture -- damage reads as
    absence, which can only ever be NOT ATTESTED.
    """
    if not isinstance(frozen_tests, dict):
        return False
    for entry in frozen_tests.values():
        if not isinstance(entry, dict):
            return False
        if any(not isinstance(value, int) for value in entry.values()):
            return False
    return True


def read_attestation(root: Path) -> Optional[dict]:
    """Read the attestation record, or None when absent or unusable.

    Unlike the manifest, a damaged attestation is NOT fail-closed. It can never
    make a state greener than NOT ATTESTED, so treating damage as absence is both
    safe and quieter than raising on a path that only ever reports.
    """
    path = attestation_state_path(root)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"canopus: unusable attestation at {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"canopus: unusable attestation at {path}: not an object", file=sys.stderr)
        return None
    if "frozen_tests" in data and not _counters_are_numeric(data["frozen_tests"]):
        print(f"canopus: unusable attestation at {path}: 'frozen_tests' is not a "
              f"mapping of integer counters", file=sys.stderr)
        return None
    return data


def write_attestation(root: Path, attestation: dict) -> None:
    """Persist the record atomically, beside the manifest."""
    path = attestation_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(attestation, indent=2, sort_keys=True) + "\n")


# The two "this record does not even apply here" reasons `attestation_state`
# returns, named rather than spelled inline. `canopus.py:_print_attestation`
# reads this pair back to decide whether to print the tree's own half of the
# story (see the branch's own comment there), and a SECOND spelling of either
# string is a rename in one module away from that branch going permanently
# silent -- measured as a live defect during the wire 3.2 scrutiny pass:
# rewording the recipe reason here left the CLI's literal tuple stale and
# nothing failed, because nothing compared the two. One constant, imported
# where the second copy used to live, removes the class rather than adding a
# test that only pins today's spelling.
REASON_DIFFERENT_RECIPE = "the attestation was written by a different recipe"
REASON_DIFFERENT_ROOT = "the attestation was recorded against a different root hash"


def attestation_state(
    attestation: Optional[dict], recomputed_root: str, current_tree
) -> Tuple[str, str]:
    """The second indicator axis, as (state, reason). Never raises.

    Two things make the record perishable, and they perish on different events.
    The recomputed root covers the FROZEN bytes. The tree state covers
    everything else in the working copy, which is where the code under test
    lives. The second is the one that matters day to day: without it, a green
    record survived breaking the implementation, and survived breaking it and
    running nothing at all.

    `current_tree` is required rather than defaulted. A default would let a
    caller that forgot it skip the comparison and print green, which is the
    fail-open shape this check exists to close.
    """
    if not isinstance(attestation, dict) or not attestation:
        return NOT_ATTESTED, "no run has attested this freeze yet"
    if attestation.get("recipe") != ATTEST_RECIPE:
        return NOT_ATTESTED, REASON_DIFFERENT_RECIPE
    if attestation.get("root") != recomputed_root:
        return NOT_ATTESTED, REASON_DIFFERENT_ROOT
    if not attestation.get("attested"):
        return NOT_ATTESTED, "the attesting run did not qualify"
    drift = tree_drift(attestation.get("tree"), current_tree)
    if drift:
        tail = f" (and {len(drift) - 1} more)" if len(drift) > 1 else ""
        return NOT_ATTESTED, f"{drift[0]}{tail}"
    return ATTESTED, ""


def _usable_tree_state(candidate) -> bool:
    """The shape every reader here assumes, checked once rather than four times."""
    return (isinstance(candidate, dict)
            and candidate.get("recipe") == TREE_RECIPE
            and isinstance(candidate.get("head"), str)
            and isinstance(candidate.get("dirty"), dict))


def tree_drift(recorded, current) -> list[str]:
    """Reasons the recorded tree state no longer describes the tree. Never raises.

    A PURE comparison of two structures: this module runs no git and reads no
    file, so its import tail stays stdlib plus `atomic`, which is what lets the
    gate call it at every pytest session start.

    Damage on either side is a reason rather than a pass, for the rule wire 3.1
    settled over an empty claim set: not proved is not proved innocent.

    Four kinds get four sentences. An operator who reads one string for all of
    them cannot tell a new file from a deleted one from an edit from a commit.
    """
    if not _usable_tree_state(recorded):
        return ["this run recorded no usable description of the tree it ran against"]
    if not _usable_tree_state(current):
        return ["the tree could not be described now, so the record cannot be checked"]
    reasons: list[str] = []
    if recorded["head"] != current["head"]:
        reasons.append(
            f"HEAD moved since the attesting run: {recorded['head']} to "
            f"{current['head']}")
    was, now = recorded["dirty"], current["dirty"]
    # `_usable_tree_state` checks that `dirty` IS a dict, never that its keys
    # are strings -- `tree_state` only ever writes string keys, but this
    # function's contract is "never raises" over whatever a hostile or
    # hand-edited record carries, and a bare `sorted()` raises TypeError the
    # moment `was` and `now` disagree on key TYPE (str here, int there: Python
    # orders neither against the other, nor anything against None). The key
    # below sorts by (type name, repr) instead, both of which are defined for
    # every Python value, so the ordering is TOTAL and the walk proceeds. That
    # is the chosen half of the two options: let the comparison run rather than
    # drop the offending key, because a dropped key is a path that stops being
    # compared, which is the same silent-narrowing failure `dirty[rel] = None`
    # above refuses for a deleted path.
    for rel in sorted(set(was) | set(now), key=lambda rel: (type(rel).__name__, repr(rel))):
        if rel not in now:
            reasons.append(f"a path the attesting run saw is no longer reported: {rel}")
        elif rel not in was:
            reasons.append(f"a path appeared since the attesting run: {rel}")
        elif was[rel] != now[rel]:
            reasons.append(f"a path changed since the attesting run: {rel}")
    return reasons
