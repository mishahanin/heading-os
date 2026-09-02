#!/usr/bin/env python3
"""`list_tribe` served contacts `read_contact` refuses to open.

`read_contact` has refused a symlinked contact and a body over 500,000 bytes
for as long as it has existed. The walker in `list_tribe`, over the same
`crm/contacts/*.md`, had neither guard: a link planted in that directory was
read through and its display name, role and frontmatter were published on
/tribe, while clicking the row answered "symlinks not allowed" for the same
slug. `threads.py` took this exact fix on its own walker on 2026-08-31 and
`tribe.py` received only the `UnicodeDecodeError` half of it, which is why this
survived a campaign that found and closed the sibling.

The size half is the cheaper of the two and still real: the listing pulled
every contact body in whole on every poll of the page, with a cap sitting a
hundred lines below in the same module saying that is not allowed.

The guards refuse; the ANCHOR tests below say what must still be served. A
guard with no anchor is satisfied by refusing everything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import tribe  # noqa: E402
from tests.code_only import strip_comments  # noqa: E402

CONTACT = """---
relationship_type: tribe
last_touch: 2026-05-15
role: Engineer
---

# Ada Lovelace

## Active Commitments
- ships the analytical engine
"""


@pytest.fixture
def data_root(tmp_path):
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    return tmp_path


def _write(data_root: Path, slug: str, body: str = CONTACT) -> Path:
    p = data_root / "crm" / "contacts" / f"{slug}.md"
    p.write_text(body, encoding="utf-8")
    return p


def _slugs(payload) -> set:
    return {m["slug"] for m in payload["members"] if m.get("slug")}


def test_a_plain_contact_is_still_listed(data_root):
    """ANCHOR. Without this, a walker that skipped everything would pass."""
    _write(data_root, "ada-lovelace")
    assert "ada-lovelace" in _slugs(tribe.list_tribe(data_root=data_root))


def test_a_symlinked_contact_is_not_listed(data_root):
    _write(data_root, "ada-lovelace")
    real = data_root / "outside.md"
    real.write_text(CONTACT, encoding="utf-8")
    (data_root / "crm" / "contacts" / "planted.md").symlink_to(real)

    listed = _slugs(tribe.list_tribe(data_root=data_root))
    assert "planted" not in listed, (
        "the listing published a contact read_contact refuses to open, so a "
        "row on /tribe answers 'symlinks not allowed' when it is clicked")
    assert "ada-lovelace" in listed, "the walk stopped instead of skipping one file"


def test_an_oversized_contact_is_not_listed(data_root):
    _write(data_root, "ada-lovelace")
    _write(data_root, "huge", CONTACT + "x" * (tribe.CONTACT_MAX_BYTES + 1))

    listed = _slugs(tribe.list_tribe(data_root=data_root))
    assert "huge" not in listed, (
        "the listing read a body over the cap read_contact applies, on every "
        "poll of the page")
    assert "ada-lovelace" in listed


def test_a_contact_just_under_the_cap_is_still_listed(data_root):
    """ANCHOR for the size guard: the boundary is `>`, not `>=`."""
    body = CONTACT
    body += "x" * (tribe.CONTACT_MAX_BYTES - len(body.encode("utf-8")))
    _write(data_root, "borderline", body)
    assert "borderline" in _slugs(tribe.list_tribe(data_root=data_root))


@pytest.mark.parametrize("slug", ["planted", "huge"])
def test_the_two_readers_agree_on_what_they_refuse(data_root, slug):
    """The defect was never one guard: it was two readers disagreeing.

    Whatever the listing drops for these reasons, `read_contact` must also
    refuse, and the reverse. Asking both in one test is what would have caught
    the original state, where the listing said yes and the detail view said no.
    """
    _write(data_root, "ada-lovelace")
    real = data_root / "outside.md"
    real.write_text(CONTACT, encoding="utf-8")
    (data_root / "crm" / "contacts" / "planted.md").symlink_to(real)
    _write(data_root, "huge", CONTACT + "x" * (tribe.CONTACT_MAX_BYTES + 1))

    listed = slug in _slugs(tribe.list_tribe(data_root=data_root))
    readable = tribe.read_contact(data_root, slug).get("ok", False)
    assert listed == readable, (
        f"/tribe lists {slug}={listed} while read_contact serves it "
        f"{readable}; one of the two readers of crm/contacts/ has a guard the "
        "other does not")


def test_the_cap_is_one_constant_not_two_literals():
    """`read_contact` carried a bare 500_000 and the walker carried nothing.

    A cap with no name is a cap with no second caller, which is the mechanism
    of this defect rather than a stylistic note. Asked of the source so a
    future reader cannot reintroduce the second spelling.
    """
    src = (ROOT / "scripts" / "bridge_daemon" / "sources" / "tribe.py").read_text(
        encoding="utf-8")
    assert tribe.CONTACT_MAX_BYTES == 500_000
    # Comments and docstrings are asked to EXPLAIN the number, so the count is
    # taken over executable source only. `strip_comments` leaves string
    # literals alone, which is right here: the cap is never spelled in one.
    code = strip_comments(src)
    assert code.count("500_000") == 1, (
        "the byte cap is spelled as a literal somewhere other than the "
        "CONTACT_MAX_BYTES definition; that is how the two readers came to "
        "disagree in the first place")
