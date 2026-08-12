"""A thread in a deliberate quiet period must say so where rollups read it.

Before this, a freeze was prose only: `do_not_remind: true` sat in one thread's
frontmatter, nothing read it, and `write_thread_file` rebuilt frontmatter from a
fixed field list so the next `/thread log` would have deleted it outright. The
MEMORY.md index -- which loads into every session -- listed a frozen thread with
a live hook, indistinguishable from work that wants attention.

Governed by .claude/rules/memory-discipline.md.
"""
from __future__ import annotations

from datetime import date

import pytest

from scripts.utils.threads_lib import (
    ThreadFile,
    add_thread_to_index,
    ensure_active_threads_section,
    is_quiet,
    parse_thread_file,
    quiet_hook_prefix,
    read_thread_hook,
    scan_for_archive,
    update_thread_hook,
    write_thread_file,
)


def _thread(**over) -> ThreadFile:
    base = {
        "id": "2026-05-20-example-alliance",
        "title": "Example alliance",
        "status": "active",
        "type": "business",
        "classification": "ceo-only",
        "opened": "2026-05-20",
        "last_touched": "2026-06-22",
        "links": {"crm": [], "pipeline": [], "outputs": [], "knowledge": []},
        "tags": [],
        "body": "# Example alliance\n\n## Log (newest first)\n",
    }
    base.update(over)
    return ThreadFile(**base)


@pytest.fixture()
def memory_md(tmp_path):
    p = tmp_path / "MEMORY.md"
    p.write_text("# Memory index\n", encoding="utf-8")
    ensure_active_threads_section(p)
    return p


# --- frontmatter survives a rewrite -------------------------------------


def test_quiet_until_survives_a_write_read_round_trip(tmp_path):
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread(quiet_until="2026-08-25"))
    assert parse_thread_file(path).quiet_until == "2026-08-25"


def test_a_thread_without_a_quiet_period_writes_no_field(tmp_path):
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread())
    assert "quiet_until" not in path.read_text(encoding="utf-8")
    assert parse_thread_file(path).quiet_until is None


# --- the semantic --------------------------------------------------------


def test_is_quiet_is_true_up_to_and_including_the_lift_date():
    t = _thread(quiet_until="2026-08-25")
    assert is_quiet(t, date(2026, 8, 12)) is True
    assert is_quiet(t, date(2026, 8, 25)) is True


def test_is_quiet_is_false_after_the_lift_date_and_when_unset():
    assert is_quiet(_thread(quiet_until="2026-08-25"), date(2026, 8, 26)) is False
    assert is_quiet(_thread(), date(2026, 8, 12)) is False


def test_an_unparseable_lift_date_is_not_treated_as_quiet():
    """Fail toward surfacing: a broken date must not silence a thread forever."""
    assert is_quiet(_thread(quiet_until="soon"), date(2026, 8, 12)) is False


def test_an_indefinite_freeze_is_quiet_on_any_date():
    """Some freezes have no date -- they lift when the operator raises it."""
    t = _thread(do_not_remind=True)
    assert is_quiet(t, date(2026, 8, 12)) is True
    assert is_quiet(t, date(2030, 1, 1)) is True


def test_an_indefinite_freeze_survives_a_write_read_round_trip(tmp_path):
    """The field that carried the only existing freeze must not be rebuilt away."""
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread(do_not_remind=True))
    assert parse_thread_file(path).do_not_remind is True
    assert "do_not_remind: true" in path.read_text(encoding="utf-8")


def test_an_unmodelled_frontmatter_key_survives_a_rewrite(tmp_path):
    """The general form of the bug: a rewrite must not delete what it cannot name."""
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread())
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("tags: []\n", "tags: []\nfrozen: '2026-06-25'\n"), encoding="utf-8")

    t = parse_thread_file(path)
    assert t.extra["frozen"] == "2026-06-25"
    t.last_touched = "2026-08-12"
    write_thread_file(path, t)
    assert parse_thread_file(path).extra["frozen"] == "2026-06-25"


def test_an_indefinite_freeze_is_never_reported_as_expired(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-06-01", do_not_remind=True),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2030, 1, 1))}
    assert actions == set()


# --- the always-loaded index --------------------------------------------


def test_the_index_line_carries_the_quiet_marker(memory_md):
    add_thread_to_index(
        memory_md, type_="business", title="Example alliance",
        path="threads/business/2026-05-20-example-alliance.md",
        hook="waiting on the September restart", quiet_until="2026-08-25",
    )
    line = memory_md.read_text(encoding="utf-8")
    assert "[quiet until 2026-08-25]" in line
    assert "waiting on the September restart" in line


def test_logging_to_a_quiet_thread_does_not_drop_the_marker(memory_md):
    rel = "threads/business/2026-05-20-example-alliance.md"
    add_thread_to_index(memory_md, type_="business", title="Example alliance",
                        path=rel, hook="opened", quiet_until="2026-08-25")
    update_thread_hook(memory_md, path=rel, hook="a new event", quiet_until="2026-08-25")
    text = memory_md.read_text(encoding="utf-8")
    assert "[quiet until 2026-08-25] a new event" in text
    assert text.count("[quiet until") == 1, "marker must not stack on repeated writes"


def test_clearing_the_quiet_period_removes_the_marker(memory_md):
    rel = "threads/business/2026-05-20-example-alliance.md"
    add_thread_to_index(memory_md, type_="business", title="Example alliance",
                        path=rel, hook="opened", quiet_until="2026-08-25")
    update_thread_hook(memory_md, path=rel, hook="restart is live", quiet_until=None)
    text = memory_md.read_text(encoding="utf-8")
    assert "[quiet until" not in text
    assert "restart is live" in text


def test_read_thread_hook_returns_the_hook_without_the_marker(memory_md):
    rel = "threads/business/2026-05-20-example-alliance.md"
    add_thread_to_index(memory_md, type_="business", title="Example alliance",
                        path=rel, hook="waiting on September", quiet_until="2026-08-25")
    assert read_thread_hook(memory_md, path=rel) == "waiting on September"


def test_quiet_hook_prefix_is_empty_without_a_date():
    assert quiet_hook_prefix(None) == ""
    assert quiet_hook_prefix("2026-08-25") == "[quiet until 2026-08-25] "


# --- the loop closes: an expired quiet gets surfaced --------------------


def test_the_hygiene_scan_reports_a_quiet_period_that_has_expired(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-08-20", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 26))}
    assert "quiet-expired" in actions


def test_the_hygiene_scan_stays_silent_while_the_quiet_period_holds(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-08-10", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 12))}
    assert "quiet-expired" not in actions


def test_a_quiet_thread_is_not_nagged_as_stale(tmp_path):
    """The 60-day 'propose on-hold' nudge is exactly the noise a quiet suppresses."""
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-06-01", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 12))}
    assert "propose-on-hold" not in actions
