#!/usr/bin/env python3
"""Refuse a test run whose binary fixtures are unresolved Git LFS pointers.

`.gitattributes` routes `*.docx`, `*.pptx`, `*.xlsx`, `*.pdf`, `*.zip`, and the
image types through Git LFS. A clone made on a host without git-lfs checks those
out as ~130-byte POINTER files, and any test reading one measures the pointer
rather than the document.

The tests themselves now skip in that case, with the fix named in the skip reason
(`tests/integration/test_convert_to_md.py`). That is right for a contributor on a
laptop and wrong for CI: a job whose fixture tests all skipped reports green while
proving nothing. This guard is the CI half of that pair -- it fails loudly when the
blobs are missing, so `lfs: true` cannot be dropped from a checkout without anyone
noticing.

Scope: `tests/`, because that is the tree whose green result is a claim. Run it
with no arguments; exit 0 when every fixture is a real blob, exit 1 with the list
of pointers otherwise -- and exit 1 when the tree itself is absent, which is the
same claim with nothing behind it at all. That case printed a note and exited 0
until 2026-08-25.

A file that DISAPPEARS between the listing and the read is reported and does not
refuse: it is not in the tree, so it holds no unresolved fixture. Only a file
that is present and cannot be read leaves this check incomplete.

Tests: tests/test_a_guard_that_was_green_over_an_absent_tree.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCANNED = ROOT / "tests"

# The first line of a v1 pointer file, per the Git LFS spec.
_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/v1"

# A pointer is small by construction; anything larger is a real blob and is not
# worth opening. The spec caps the pointer at 1024 bytes.
_POINTER_MAX_BYTES = 1024


def is_pointer(path: Path) -> bool:
    """True when `path` holds an unresolved LFS pointer instead of its content.

    Raises OSError for a file it cannot read. It used to catch that itself,
    print a warning to stderr and answer False, after which `main` printed
    "no pointer files under tests/" and exited 0. A file that was never opened
    is not evidence of anything, and this guard exists because "a green run
    would prove nothing" - so it must not be the thing printing a green line
    over a check it did not finish (`.claude/rules/scope-claims.md`).
    """
    if path.stat().st_size > _POINTER_MAX_BYTES:
        return False
    with path.open("rb") as fh:
        return fh.read(len(_POINTER_MAGIC)) == _POINTER_MAGIC


def scan(base: Path) -> tuple[list[Path], list[tuple[Path, str]], list[Path]]:
    """(pointer files, files that could not be read, files that vanished).

    The three outcomes are distinct and only the middle one is a hole in the
    check. A file that no longer EXISTS carries no fixture: there is nothing
    unresolved behind it, and nothing for a later test to measure. Folding it
    into `unreadable` made this guard fail whenever anything wrote to `tests/`
    while it ran - which the test suite itself does, so the guard was flaky
    against its own repository rather than wrong about it.

    It is still reported. `.claude/rules/scope-claims.md` asks for the drop
    count, not silence: the sentence says the file is gone, which is what the
    method established, and never that it was checked.
    """
    pointers: list[Path] = []
    unreadable: list[tuple[Path, str]] = []
    vanished: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            if is_pointer(path):
                pointers.append(path)
        except FileNotFoundError:
            # Listed by rglob, gone by the time it was opened. A dangling
            # symlink cannot reach here: `is_file()` follows the link and
            # answers False, so this is only ever a genuine concurrent delete.
            vanished.append(path)
        except OSError as exc:
            unreadable.append((path, str(exc)))
    return pointers, unreadable, vanished


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    if not SCANNED.is_dir():
        # Exit 1, not 0. An absent tests/ tree is the MAXIMAL version of the
        # failure this guard exists to catch: every fixture blob is missing.
        # Reporting green there is the same "skipped everything, proved
        # nothing" the docstring above says the guard is the answer to, and a
        # sparse checkout or a tree move produces it silently.
        print(f"REFUSED: {SCANNED} does not exist, so no fixture could be "
              f"checked. A green result here would claim a check that did not "
              f"happen.", file=sys.stderr)
        return 1

    pointers, unreadable, vanished = scan(SCANNED)
    if vanished:
        print(f"note: {len(vanished)} file(s) under tests/ were listed and then "
              f"deleted before this guard read them, so they were not checked. "
              f"They are not in the tree now, so they carry no fixture:",
              file=sys.stderr)
        for p in vanished:
            print(f"  {_rel(p)}", file=sys.stderr)
    if not pointers and not unreadable:
        print("LFS fixtures resolved: no pointer files under tests/.")
        return 0

    if pointers:
        print(
            f"{len(pointers)} unresolved Git LFS pointer(s) under tests/. Any test reading "
            f"these skips or measures the pointer, so a green run would prove nothing:",
            file=sys.stderr,
        )
        for p in pointers:
            print(f"  {_rel(p)}", file=sys.stderr)
    if unreadable:
        print(
            f"{len(unreadable)} file(s) under tests/ could not be read, so their "
            f"LFS state is UNKNOWN and this check is not complete:",
            file=sys.stderr,
        )
        for p, why in unreadable:
            print(f"  {_rel(p)}: {why}", file=sys.stderr)
    print(
        "\nFix locally: `git lfs install && git lfs pull`. "
        "Fix in CI: `lfs: true` on the actions/checkout step.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
