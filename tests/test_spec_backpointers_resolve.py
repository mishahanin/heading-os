#!/usr/bin/env python3
"""Every `docs/...#anchor` pointer in the code must land on a real heading.

Design prose that outgrows a docstring moves into the design archive and leaves
a back-pointer behind, so the reasoning stays one click from the code without
sitting in every context window that loads the module. The relocation of
2026-08-20 moved 1,453 lines out of `scripts/utils/canopus_contract.py` and
`scripts/utils/canopus_nullstub.py` this way and left 84 pointers.

**The archive is private.** `docs/superpowers/` and `docs/design/` both route
`private` and live in the DATA overlay: the engine is public, and how it was
built is not (CEO decision, 2026-08-20). So a pointer is written with the
`.heading-os-data/` prefix and resolves against the DATA root, and on a public
clone with no overlay this test skips those pointers rather than failing them.
An engine-relative `docs/...` pointer still resolves against the engine root,
because a public page like `docs/DOCS-PIPELINE.md` is a legitimate target.

A pointer is only worth what it resolves to. Rename a heading in the spec and
every pointer into it rots silently: the file still exists, the link still looks
right, and the reader lands at the top of a 114 KB document with no idea which
section was meant. That failure has no error message, which is why it needs a
test rather than a convention.

The anchor rule matches GitHub's: lowercase, punctuation dropped, spaces to
hyphens. The test is deliberately about EXISTENCE, not about whether the prose
under the heading is still the right prose - no test can hold that.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POINTER_RE = re.compile(r"((?:\.heading-os-data/)?docs/[\w./-]+\.md)#([\w-]+)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
SEARCH_ROOTS = ("scripts", ".claude")


def _anchors(md: Path) -> set[str]:
    text = md.read_text(encoding="utf-8", errors="ignore")
    out = set()
    for heading in HEADING_RE.findall(text):
        slug = re.sub(r"[^\w\s-]", "", heading.lower()).strip().replace(" ", "-")
        out.add(slug)
    # An explicit <a id="..."> or {#...} also defines an anchor.
    out.update(re.findall(r'<a\s+(?:id|name)="([\w-]+)"', text))
    out.update(re.findall(r"\{#([\w-]+)\}", text))
    return out


def _pointers():
    for root in SEARCH_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in (".py", ".md", ".sh") or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for doc, anchor in POINTER_RE.findall(text):
                yield path, doc, anchor


OVERLAY_PREFIX = ".heading-os-data/"


def _resolve(doc: str) -> Path | None:
    """Engine-relative pointers resolve against the engine root; overlay
    pointers against the DATA root. Returns None when no overlay backs this
    checkout, which is the public-clone case and not a defect.

    The absence test is `data_overlay_present()`, NOT `get_data_root().exists()`.
    `get_data_root()` never returns a missing path: with no overlay it falls back
    to the bundled read-only `examples/` tree, which exists and contains no
    specs. So the `.exists()` form skipped nothing, resolved all 84 pointers
    against `examples/`, and failed every one of them on CI while printing
    "0 skipped: private overlay absent" in the same breath. Green here, red
    there, for three days.
    """
    if not doc.startswith(OVERLAY_PREFIX):
        return ROOT / doc
    from scripts.utils.paths import data_overlay_present, get_data_root

    if not data_overlay_present():
        return None
    return Path(get_data_root()) / doc[len(OVERLAY_PREFIX):]


def test_every_anchor_pointer_into_docs_resolves():
    cache: dict[str, set[str]] = {}
    broken = []
    total = 0
    skipped = 0

    for path, doc, anchor in _pointers():
        total += 1
        target = _resolve(doc)
        if target is None:
            skipped += 1
            continue
        if not target.exists():
            broken.append(f"{path.relative_to(ROOT)} -> {doc} (file missing)")
            continue
        if doc not in cache:
            cache[doc] = _anchors(target)
        if anchor not in cache[doc]:
            broken.append(f"{path.relative_to(ROOT)} -> {doc}#{anchor} (no such heading)")

    assert not broken, (
        f"{len(broken)} of {total} back-pointers do not resolve "
        f"({skipped} skipped: private overlay absent):\n  "
        + "\n  ".join(broken[:20])
    )
    if skipped:
        # Never silent about a narrowed check. A guard that quietly skips most
        # of its subject reads exactly like one that passed it.
        print(f"\n{total - skipped} of {total} pointers checked; "
              f"{skipped} skipped (private DATA overlay absent).")


def test_the_detector_actually_finds_pointers():
    """A matcher that matches nothing passes everything."""
    assert sum(1 for _ in _pointers()) >= 20
