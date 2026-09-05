#!/usr/bin/env python3
"""Every follow-up added a blank line, and every log entry ate the next heading.

MEASURED 2026-09-05 in HELM, in-process against the real functions, before the
fix. Four follow-ups appended one after another to a fresh thread body:

    '# Thread\\n\\n## Open follow-ups\\n\\n\\n\\n- [ ] item1\\n...'
                                      ^^^^^^^^^^ three blank lines, not one

The gap grows by one line per append, so a thread with nine follow-ups carries
eight blank lines under its heading. `_append_under_section` takes the section
body starting at `m.end()`, which is the position BEFORE the header's own
newline, so the slice always begins with `"\\n"`. `rstrip("\\n")` removes trailing
newlines and leaves that leading one, and the `"\\n"` the function then prepends
is a second copy. Each pass keeps what the last pass left.

The comment above that line is right about why the newline is mandatory: without
it an empty section concatenates into `## Header- [ ] item` and the next regex
match fails. The fix is not to drop it but to stop keeping the previous one, so
`strip("\\n")` rather than `rstrip("\\n")`, and the prepended newline stays.

SECOND DEFECT, same file, different shape. `_prepend_log_entry` closes with a
single `"\\n"` before the rest of the document, so the newest entry butts
straight against whatever heading follows:

    '- 2026-09-05 second\\n- 2026-09-05 first\\n## Notes\\n'
                                             ^^ no blank line

Markdown tolerates that; a human reading the file does not, and neither does a
future parser looking for a blank line before a level-2 heading.

WHY THIS IS ASSERTED ON THE SHAPE AND NOT ON A RENDERED STRING. A test comparing
the whole body against a fixed expected string would go red on any unrelated
edit to the thread template, which teaches people to re-bless the expected value
without reading it. These count the blank lines between two known anchors, so
they fail on the defect and on nothing else.

Run: .venv/bin/python -m pytest \\
     tests/test_a_thread_writer_that_grew_a_blank_line_every_time.py -q
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: How many appends the growth cases perform. Named and asserted rather than
#: inlined, because one append cannot show growth: the defect is that the gap
#: scales with the number of writes, so a corpus of one would pass over it.
APPENDS = 4

FOLLOWUPS = "## Open follow-ups"
LOG = "## Log (newest first)"


@pytest.fixture(scope="module")
def thread():
    """The real `scripts/thread.py`, loaded by path (its name has a hyphen)."""
    spec = importlib.util.spec_from_file_location(
        "thread_under_test", ROOT / "scripts" / "thread.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blank_lines_after(body: str, header: str) -> int:
    """Blank lines between `header` and the first line of content under it."""
    match = re.search(rf"^{re.escape(header)}$(\n+)", body, re.MULTILINE)
    assert match, f"{header!r} not found in:\n{body!r}"
    return len(match.group(1)) - 1


# ============================================================
# The growth
# ============================================================

def test_repeated_appends_leave_exactly_one_blank_line(thread):
    """THE GUARD. One blank line under the heading, however many appends."""
    body = f"# Thread\n\n{FOLLOWUPS}\n\n## Notes\n\ntail\n"
    for index in range(APPENDS):
        body = thread._append_under_section(body, FOLLOWUPS, f"- [ ] item{index}")

    assert APPENDS > 1, "one append cannot demonstrate growth"
    assert _blank_lines_after(body, FOLLOWUPS) == 1, (
        f"after {APPENDS} appends the heading is followed by "
        f"{_blank_lines_after(body, FOLLOWUPS)} blank line(s), not 1. The gap "
        f"grows by one per append:\n{body!r}")


def test_every_appended_item_is_still_present_and_in_order(thread):
    """The other direction. A fix that collapses whitespace by dropping
    content would satisfy the count above and lose the follow-ups."""
    body = f"# Thread\n\n{FOLLOWUPS}\n\n## Notes\n"
    for index in range(APPENDS):
        body = thread._append_under_section(body, FOLLOWUPS, f"- [ ] item{index}")

    positions = [body.index(f"- [ ] item{i}") for i in range(APPENDS)]

    assert len(positions) == APPENDS, "an item vanished"
    assert positions == sorted(positions), (
        "append order was not preserved; --done <N> indexes shift under the "
        f"operator: {positions}")


def test_the_mandatory_newline_is_still_there(thread):
    """The regression the current code's own comment warns about.

    Without a newline after the header an EMPTY section renders as
    `## Header- [ ] item`, and the next `^## ` match fails, so the section is
    never found again. The fix must not reach that by removing the prepend.
    """
    body = f"# Thread\n\n{FOLLOWUPS}\n\n## Notes\n"
    body = thread._append_under_section(body, FOLLOWUPS, "- [ ] only")

    assert f"{FOLLOWUPS}- [ ]" not in body, (
        f"header and item concatenated, so the section can never be matched "
        f"again:\n{body!r}")
    assert re.search(rf"^{re.escape(FOLLOWUPS)}$", body, re.MULTILINE), (
        "the heading is no longer matchable as its own line")


def test_a_missing_section_is_created_with_one_blank_line(thread):
    """The branch that INJECTS the section, which is a different code path."""
    body = "# Thread\n\n## Notes\n\ntail\n"
    body = thread._append_under_section(body, FOLLOWUPS, "- [ ] first")

    assert _blank_lines_after(body, FOLLOWUPS) == 1, (
        f"the injected section has {_blank_lines_after(body, FOLLOWUPS)} blank "
        f"line(s) under its heading:\n{body!r}")


# ============================================================
# The log entry that ate the next heading
# ============================================================

def test_a_log_entry_does_not_touch_the_following_heading(thread):
    """THE SECOND GUARD. A blank line separates the log from what follows."""
    body = f"# T\n\n{LOG}\n\n## Notes\n"
    body = thread._prepend_log_entry(body, "- 2026-09-05 first")
    body = thread._prepend_log_entry(body, "- 2026-09-05 second")

    assert "\n\n## Notes" in body, (
        f"the newest log entry butts against the next heading with no blank "
        f"line:\n{body!r}")


def test_the_log_stays_newest_first(thread):
    """The other direction: separation must not cost the ordering the
    function's whole name is about."""
    body = f"# T\n\n{LOG}\n\n## Notes\n"
    body = thread._prepend_log_entry(body, "- older")
    body = thread._prepend_log_entry(body, "- newer")

    assert body.index("- newer") < body.index("- older"), (
        f"log order inverted:\n{body!r}")


def test_a_log_entry_at_end_of_file_does_not_gain_a_trailing_void(thread):
    """No following heading at all. The fix must not append blank lines to a
    file that simply ends, which is the shape every thread has on day one."""
    body = f"# T\n\n{LOG}\n"
    body = thread._prepend_log_entry(body, "- only")

    assert not body.endswith("\n\n\n"), (
        f"trailing blank lines accumulated at end of file:\n{body!r}")
    assert body.endswith("\n"), "the file lost its final newline"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
