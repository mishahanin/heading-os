#!/usr/bin/env python3
"""SessionStart hook + CLI: reconcile the native harness memory store with the
canonical data-root auto-memory.

Why this exists
---------------
Claude Code's native file-memory feature loads `MEMORY.md` + individual memories
from `~/.claude/projects/<cwd-hash>/memory/` -- a store keyed to the session's
LAUNCH DIRECTORY. After the HEADING OS engine/data split, the same data is reached
from two launch paths (the transitional `ceo-main`, and the engine clone
`.heading-os`), each of which hashes to a DIFFERENT native store. A memory written
or seeded under one launch path is invisible from the other, so a fresh session in
the new ecosystem loaded an empty/stale store (symptom: wrong name, missing facts).

The canonical, durable home for memory is DATA: `<data-root>/auto-memory/` (lives in
the data repo, survives, indexed by memory-index). The native per-launch store is a
runtime cache. This hook keeps the two in sync, both directions, newest-wins, at every
SessionStart -- so whatever directory a session launches from, its native store is
seeded from canonical, and any memory written during a session is persisted back to
canonical for the next launch (from any path).

No symlinks (CEO directive): the bridge is an explicit per-clone reconcile, not a
filesystem link. Deletions are NOT propagated (a file present on only one side is
copied to the other, never deleted) -- this fails safe against accidental mass-loss;
prune a retired memory on both sides by hand.

Two sessions launched from different directories both fire this hook, and BY DESIGN
each syncs a DIFFERENT native store against the SAME canonical one, so the canonical
side has two concurrent writers. Serialisation and per-file atomicity are therefore
part of the fail-safe promise above, not an optimisation: see `_canonical_lock` and
`_copy_atomic`.

Usage:
    # SessionStart hook (reads hook JSON on stdin; resolves native store from
    # transcript_path, canonical from get_data_root()):
    python3 .claude/hooks/memory-reconcile.py

    # CLI (explicit dirs -- used for one-off cutover seeding and tests):
    python3 .claude/hooks/memory-reconcile.py --native DIR --canonical DIR [--quiet]
"""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# `checkpoint_paths`, when this clone has it. Located by walking for
# `scripts/utils/` rather than counting parents, the same way `bridge-hook.py`
# and `checkpoint-inject.py` do, so a copy of this hook shipped inside a plugin
# bundle finds the copy bundled beside it.
#
# OPTIONAL on purpose. A hook that cannot import a helper must still reconcile
# the two stores: booting a session on a stale memory store is the loss this
# file exists to prevent, and an ImportError is not a reason to accept it. So
# `_CP` stays None, `_canonical_lock` degrades to a no-op, and one line goes to
# stderr saying the sync is unserialised. Loud degradation, never a silent one.
_CP = None
for _candidate in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
    if (_candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
        sys.path.insert(0, str(_candidate))
        try:
            from scripts.utils import checkpoint_paths as _CP  # noqa: E402
        except Exception as _exc:  # noqa: BLE001 - reported, never fatal
            print(f"[memory-reconcile] checkpoint_paths unavailable ({_exc}); "
                  f"the two-way sync is not serialised", file=sys.stderr)
        break


def _lock_path(canonical: Path) -> Path:
    """Where two racing sessions meet, keyed by the store they SHARE.

    Keyed on the canonical directory and never on the native one, because the
    native store is what differs between the racers: session A syncs the
    `ceo-main` store and session B the `.heading-os` store, against one
    `<data-root>/auto-memory`. A lock keyed on the native side would hand each
    session its own lock and serialise nothing.

    The sidecar lives under the per-user `~/.claude/state/`, the same runtime
    directory `bridge-hook.py` keeps its session registry in, rather than beside
    the store it guards. Both alternatives put an untracked empty file inside a
    git-tracked tree: `<data-root>/auto-memory.lock` in the data repo, or a
    dotfile inside `auto-memory/` itself. The data overlay's .gitignore covers
    lock sidecars under `outputs/` and names nothing here, and this file may not
    widen it. A digest of the resolved canonical path is the shared name, so two
    sessions pointed at one store agree while two operators (or an exec overlay)
    never collide.
    """
    try:
        key = str(canonical.resolve())
    except OSError:  # pragma: no cover - resolve() is non-strict; belt and braces
        key = str(canonical)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "~")
    return home.expanduser() / ".claude" / "state" / f"memory-reconcile-{digest}.lock"


@contextlib.contextmanager
def _canonical_lock(canonical: Path):
    """Serialise one whole reconcile pass over the canonical store.

    `_copy_atomic` makes each WRITE indivisible, which is a DIFFERENT guarantee
    from making a read and its following write indivisible. The loop in
    `reconcile` reads both sides, compares mtimes, and only then copies; two
    sessions overlapping in that span can each decide against the other's
    half-applied state, and nothing errors when they do.

    Bounded and degrading, never blocking: `file_lock` waits a couple of seconds,
    then proceeds unlocked with a line on stderr. A SessionStart hook that waits
    forever is worse than one that races.

    Found by the 2026-08-31 audit of the hooks family. The auditor reasoned it
    rather than measuring it, and said so, because reproducing it means writing
    into the operator's real overlay and one run settles nothing about timing.
    What the code showed plainly is that no lock was taken and no write was
    atomic, and `.claude/hooks/bridge-hook.py` had been fixed the same way hours
    earlier for the same shape.
    """
    if _CP is None:
        yield False
        return
    with _CP.file_lock(_lock_path(canonical), label="memory-reconcile") as held:
        yield held


def _copy_atomic(src: Path, dst: Path) -> None:
    """`shutil.copy2` semantics, minus the window where `dst` is half a file.

    copy2 TRUNCATES the destination and then streams into it, so for the length
    of that stream a concurrent reader sees a short file, and a reader fast
    enough sees an empty one. This hook IS that reader: the loop below compares
    `fa.read_bytes()` against `fb.read_bytes()` and resolves any difference by
    mtime, so a truncated read is not merely a bad read. It is content, and it
    can win the comparison, because copy2 stamps the source's mtime only after
    the stream finishes.

    MEASURED 2026-08-31 in a scratch sandbox, one writer process looping the copy
    while this process read the destination, on a 4000-byte file (the shape of a
    real memory: 248 of them, largest 18713 bytes). With copy2, 224456 of 566664
    reads returned a size other than 4000 and the smallest was 0, an entirely
    empty store entry. With the copy below, 0 of 420183.

    Copy to a sibling the `*.md` glob cannot see, then `os.replace`, which is
    atomic within a directory. A reader holds either the whole old file or the
    whole new one. mtime and mode both survive: copy2 stamps them onto the
    temporary and a rename does not touch them, so the tie rule in `reconcile`
    reads exactly what it read before.
    """
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        # Clean up the orphan, then re-raise: the caller's per-entry handler
        # reports the failure and syncs the remaining files. Only the cleanup
        # error is suppressed, never the one that brought us here.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def reconcile(dir_a: Path, dir_b: Path) -> tuple[int, int]:
    """Bidirectional newest-wins sync of *.md between two memory dirs.

    Returns (a_updated, b_updated). `_copy_atomic` preserves mtime so newest-wins
    is stable across repeated runs (an unchanged pair never re-copies). Deletions
    are never propagated.

    An EXACT mtime tie between two differing files goes to `dir_b`, which main()
    always passes as the canonical data-root store. A tie is not a race between
    two edits: a memory edited natively during a session carries the wall clock
    of that edit and is strictly newer. The tie arises when a pair copy2 seeded
    from one mtime then diverged WITHOUT the clock moving, which is what an
    access-metadata bump does by design (see scripts/utils/memory_touch.py --
    it restores mtime so a bump cannot masquerade as a content edit). Resolving
    that toward the durable store keeps the bump; resolving it toward the
    per-launch cache would discard the counter at every SessionStart.
    """
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in dir_a.glob("*.md")} | {p.name for p in dir_b.glob("*.md")}
    a_upd = b_upd = 0
    for name in sorted(names):
        fa, fb = dir_a / name, dir_b / name
        # Per-entry, not per-run. One unreadable file, a directory that happens
        # to end in `.md`, or a file that vanishes between exists() and
        # read_bytes() used to raise straight out of this loop; main() caught it
        # once and returned, so a single bad entry left every REMAINING memory
        # unsynced. Found by the 2026-08-23 audit. Skipping the entry and
        # continuing syncs the other N-1; failing the run syncs none of them.
        try:
            if fa.exists() and not fb.exists():
                _copy_atomic(fa, fb)
                b_upd += 1
            elif fb.exists() and not fa.exists():
                _copy_atomic(fb, fa)
                a_upd += 1
            else:
                if fa.read_bytes() == fb.read_bytes():
                    continue
                if fa.stat().st_mtime > fb.stat().st_mtime:
                    _copy_atomic(fa, fb)
                    b_upd += 1
                else:
                    _copy_atomic(fb, fa)
                    a_upd += 1
        except OSError as exc:
            print(f"[memory-reconcile] skipped {name}: {exc}", file=sys.stderr)
    return a_upd, b_upd


def _native_from_hook(data: dict) -> Path | None:
    """Resolve the native harness memory dir from SessionStart hook input.

    Prefer transcript_path (authoritative: its parent IS the project dir). Fall back
    to deriving the project-hash from cwd the way Claude Code does (each '/' and '.'
    in the absolute path becomes '-').

    The fallback is POSIX-only, and says so rather than guessing. On Windows
    `Path(cwd).resolve()` yields `C:\\Users\\...`: the backslashes and the drive
    colon are not covered by the two replacements, so the computed slug never
    matched a real store and the hook reconciled against an invented directory,
    creating it. Found by the 2026-08-23 audit.

    Returning None is the right answer there, not a repaired guess. The caller
    already treats None as "nothing to reconcile" and exits 0, and the correct
    Windows slug format is not something this file can verify. transcript_path
    is present in practice, so the fallback is the rare path either way.
    """
    # `isinstance`, not truthiness. A payload field is whatever the sender put
    # there, and `Path()` raises TypeError on anything that is not a string or
    # os.PathLike. MEASURED 2026-08-31 against the live hook: `{"transcript_path":
    # 3}` exited 1 with `TypeError: expected str, bytes or os.PathLike object,
    # not int`, and `[1]`, `{"a": 1}` and `true` each did the same with their own
    # type name. This call sat OUTSIDE main()'s try, so the SessionStart hook died
    # with a traceback. `[]` and `{}` slipped past the old truthiness test only
    # because they are falsy, which is luck rather than a guard.
    #
    # tests/test_every_hook_survives_a_malformed_payload.py feeds only top-level
    # non-object payloads, so a wrong FIELD type was covered nowhere. That is why
    # this survived the 2026-08-23 sweep.
    tp = data.get("transcript_path")
    if isinstance(tp, str) and tp:
        return Path(tp).expanduser().parent / "memory"

    # One owner for the harness project-slug rule, and it returns None off
    # POSIX rather than guessing a Windows form nobody here can verify.
    # The raw string, not a Path: off POSIX `Path()` raises before the
    # resolver can refuse.
    #
    # Read off the module-level `_CP` rather than imported here, so a clone
    # without `scripts/utils/checkpoint_paths.py` refuses in the same place as
    # the lock does instead of raising ImportError out of this function.
    if _CP is None:
        print("[memory-reconcile] no usable transcript_path and no slug "
              "resolver on this clone; skipping rather than guessing a store "
              "path", file=sys.stderr)
        return None
    project = _CP.transcript_dir(data.get("cwd") or os.getcwd())
    if project is None:
        print("[memory-reconcile] no transcript_path and the cwd-slug fallback is "
              "POSIX-only; skipping rather than guessing a store path",
              file=sys.stderr)
        return None
    return project / "memory"


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile native harness memory with canonical data auto-memory.")
    ap.add_argument("--native", help="native harness memory dir (CLI mode)")
    ap.add_argument("--canonical", help="canonical data auto-memory dir (CLI mode)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if bool(args.native) != bool(args.canonical):
        # Half a CLI invocation used to fall through to HOOK mode: the directory
        # the operator named was discarded without a word, stdin was read (empty
        # during a cutover, so `{}`), and the two LIVE stores were reconciled
        # instead. `--quiet` then suppressed the summary, so nothing at all was
        # printed about what had been touched. The docstring advertises CLI mode
        # for "one-off cutover seeding and tests" - the two situations where
        # operating on the live stores is most damaging.
        ap.error("--native and --canonical must be given together")

    if args.native and args.canonical:
        native = Path(args.native).expanduser()
        canonical = Path(args.canonical).expanduser()
    else:
        # Hook mode: read SessionStart JSON on stdin.
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except (json.JSONDecodeError, ValueError):
            data = {}
        # A payload that is valid JSON but not an object still reaches `.get`.
        # `[]`, `"x"`, `3` and `null` all parse, then raise an uncaught
        # AttributeError. Swept 2026-08-23 across every stdin hook: six crashed
        # on all four shapes. Same defect checkpoint-inject.py fixed on
        # 2026-08-20; the sweep is how the rest were found.
        if not isinstance(data, dict):
            data = {}
        # Inside the try, not above it. Resolving the native store reads a
        # payload field this file does not control, and until 2026-08-31 the
        # only guarded half of the resolve was the data root.
        try:
            native = _native_from_hook(data)
            from scripts.utils.paths import data_root_is_demo
            from scripts.utils.workspace import get_data_root
            if data_root_is_demo():
                # `get_data_root()` falls through to `<workspace_root>/examples`
                # when there is no overlay - a fresh PUBLIC engine clone. That
                # directory is git-TRACKED, so reconciling into it copies the
                # harness's private memory store into the working tree of a repo
                # whose whole premise is code only, one `git add -A` from being
                # committed. Every other writer refuses this root by name;
                # `scripts/migrate-data.py` prints "Refusing to migrate a demo
                # (read-only examples) overlay". This hook took the value bare.
                print("[memory-reconcile] data root is the bundled read-only "
                      "examples overlay; refusing to write private memory into "
                      "the tracked engine tree.", file=sys.stderr)
                return 0
            canonical = get_data_root() / "auto-memory"
        except Exception as e:  # never break the session over a memory sync
            # "store resolve", not "data-root resolve": the try now also covers
            # the native side, and naming only the data root would send the
            # reader to the wrong half.
            print(f"[memory-reconcile] store resolve failed: {e}", file=sys.stderr)
            return 0
        if native is None:
            return 0

    try:
        with _canonical_lock(canonical):
            a_upd, b_upd = reconcile(native, canonical)
    except OSError as e:
        print(f"[memory-reconcile] failed: {e}", file=sys.stderr)
        return 0

    if not args.quiet and (a_upd or b_upd):
        print(f"[memory-reconcile] native +{a_upd}, canonical +{b_upd}  "
              f"({native} <-> {canonical})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
