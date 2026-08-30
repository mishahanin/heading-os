#!/usr/bin/env python3
"""`scan_for_archive` skipped a bad thread file, but only for two error classes.

`parse_thread_file` opens with `path.read_text(encoding="utf-8")`. The scan
caught `ValueError` and `yaml.YAMLError` around it, which covers a malformed
frontmatter and (since `UnicodeDecodeError` subclasses `ValueError`) an
undecodable file. It did not cover `OSError`, and the `glob` above it is a
snapshot: an entry that vanishes, turns unreadable, or is a DIRECTORY named
`*.md` raises out of the read.

Measured 2026-08-30 against `threads/business/notafile.md/`:
`IsADirectoryError` aborted the whole scan and BOTH type directories returned
zero candidates. One bad entry must cost one entry.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.threads_lib import scan_for_archive  # noqa: E402

TODAY = date(2026, 8, 30)          # pinned; never the host clock
LONG_CLOSED = "2026-01-01"         # 241 days before TODAY, past the 90-day bar


def _thread(dirpath: Path, slug: str, status: str, last_touched: str,
            type_: str = "business") -> Path:
    path = dirpath / f"{slug}.md"
    path.write_text(
        "---\n"
        f"id: {slug}\n"
        f"title: {slug}\n"
        f"status: {status}\n"
        f"type: {type_}\n"
        "classification: ceo-only\n"
        "opened: '2025-11-04'\n"
        f"last_touched: '{last_touched}'\n"
        "links: {}\n"
        "tags: []\n"
        "---\n\n# body\n",
        encoding="utf-8",
    )
    return path


def _roots(tmp_path: Path) -> Path:
    (tmp_path / "business").mkdir()
    (tmp_path / "personal").mkdir()
    return tmp_path


def test_a_directory_named_like_a_thread_costs_one_entry_not_the_scan(tmp_path):
    """The measured case: IsADirectoryError took the whole scan down."""
    root = _roots(tmp_path)
    _thread(root / "business", "the-golden-gun-deal", "closed", LONG_CLOSED)
    (root / "business" / "notafile.md").mkdir()

    found = scan_for_archive(root, today=TODAY)

    assert found, "the scan returned nothing; the good thread was lost with the bad"
    assert [c.path.name for c in found] == ["the-golden-gun-deal.md"]
    assert found[0].action == "archive"


def test_a_bad_entry_in_one_type_dir_does_not_hide_the_other(tmp_path):
    """The blast radius: both directories returned zero, not just the broken one."""
    root = _roots(tmp_path)
    (root / "business" / "broken.md").mkdir()
    _thread(root / "personal", "the-moonraker-matter", "closed", LONG_CLOSED,
            type_="personal")

    found = scan_for_archive(root, today=TODAY)
    assert [c.path.name for c in found] == ["the-moonraker-matter.md"]


def test_a_malformed_thread_is_still_skipped_quietly(tmp_path):
    """The pre-existing behaviour must survive the widened clause."""
    root = _roots(tmp_path)
    (root / "business" / "nofrontmatter.md").write_text("just prose\n", encoding="utf-8")
    _thread(root / "business", "the-golden-gun-deal", "closed", LONG_CLOSED)

    found = scan_for_archive(root, today=TODAY)
    assert [c.path.name for c in found] == ["the-golden-gun-deal.md"]


def test_a_fresh_thread_is_not_proposed_for_anything(tmp_path):
    """The negative case: without it every assertion above could pass vacuously."""
    root = _roots(tmp_path)
    _thread(root / "business", "the-golden-gun-deal", "active", "2026-08-29")
    assert scan_for_archive(root, today=TODAY) == []
