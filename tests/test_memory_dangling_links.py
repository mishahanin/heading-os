"""A `[[wikilink]]` in auto-memory must resolve, or say where it points instead.

Written 2026-08-19, after a /dream sweep repaired nine dangling links by hand.
Three of them were not merely broken pointers: `no-exec-sync-until-ceo-cutover`
was cited by five files as the reason work was "still deferred" months after the
deferral was lifted, and `rlm-deep-read-sandbox-open` marked a decision as parked
after it had shipped as /census. A dangling link is cheap on its own; it is
expensive because the PREMISE around it goes stale with it and nothing notices.

Advisory by construction: an unresolved link is legitimate when it marks a
memory worth writing later. What this guard refuses is a link that names a
memory nobody intends to write -- so it reports, and `/dream` triages.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.utils.memory_health import scan_dangling_links

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _write(d: Path, name: str, body: str) -> None:
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: t\n---\n\n{body}\n", encoding="utf-8")


def test_resolving_links_are_not_flagged(tmp_path):
    _write(tmp_path, "alpha", "See [[beta]] for the rest.")
    _write(tmp_path, "beta", "The rest.")
    assert scan_dangling_links(tmp_path)["flagged"] == []


def test_a_link_to_a_memory_that_does_not_exist_is_flagged(tmp_path):
    _write(tmp_path, "alpha", "Still deferred, see [[gamma]].")
    flagged = scan_dangling_links(tmp_path)["flagged"]
    assert [f["target"] for f in flagged] == ["gamma"]
    assert flagged[0]["cited_by"] == ["alpha.md"]


def test_a_link_cited_by_several_files_reports_every_citer(tmp_path):
    """Breadth is the signal: five citers is a shared stale premise, not a typo."""
    _write(tmp_path, "one", "see [[ghost]]")
    _write(tmp_path, "two", "also [[ghost]]")
    flagged = scan_dangling_links(tmp_path)["flagged"]
    assert flagged[0]["cited_by"] == ["one.md", "two.md"]


def test_thread_and_timestamp_targets_are_not_memory_links(tmp_path):
    """`[[thread:...]]` and bare-id links address other namespaces, not memory."""
    _write(tmp_path, "alpha", "see [[thread:2026-05-25-something]] and [[20260702120100]]")
    assert scan_dangling_links(tmp_path)["flagged"] == []


def test_missing_directory_reports_rather_than_raises(tmp_path):
    result = scan_dangling_links(tmp_path / "nope")
    assert result["ok"] is False
    assert result["flagged"] == []


def test_the_real_auto_memory_store_has_no_dangling_links():
    """The live store, held at zero by the 2026-08-19 sweep.

    Skipped where the data overlay is absent (a bare public engine clone).
    """
    from scripts.utils.workspace import get_data_root
    mem_dir = get_data_root() / "auto-memory"
    if not mem_dir.is_dir():
        pytest.skip("no data overlay on this clone")
    flagged = scan_dangling_links(mem_dir)["flagged"]
    assert flagged == [], (
        "dangling memory links found; repoint each at the record it means "
        f"(a file path, a thread) or write the memory: {flagged}")
