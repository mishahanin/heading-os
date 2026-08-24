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
of pointers otherwise.
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


def scan(base: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """(pointer files, files that could not be read) under `base`."""
    pointers: list[Path] = []
    unreadable: list[tuple[Path, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            if is_pointer(path):
                pointers.append(path)
        except OSError as exc:
            unreadable.append((path, str(exc)))
    return pointers, unreadable


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    if not SCANNED.is_dir():
        print(f"nothing to check: {SCANNED} does not exist", file=sys.stderr)
        return 0

    pointers, unreadable = scan(SCANNED)
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
