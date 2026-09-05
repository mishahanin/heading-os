"""Day mode's literal filter dropped whole classes of real filenames as prose.

`_looks_like_a_reference` keeps the string constants that could name a file and
drops English. Until 2026-09-05 its shape test was a trailing extension of at
most five characters, or a chain of dotted Python identifiers. Both halves miss,
and they miss in a way that looks arbitrary from outside: a hyphen defeats the
identifier chain and length defeats the extension, so `sentinel.service` was
accepted and `bridge-daemon.service` was not, and `.gitignore` -- a name with no
extension at all -- was read as prose.

A rejected literal never becomes an edge, so no test can be attributed to that
file, and `prepush_gate.decide()` widens to the whole 24,965-test suite. MEASURED
in HELM 2026-09-05 on a real push: 307s total, of which 294.8s was the full suite
running because one changed `.secrets.baseline` could not be routed.

MEASURED HERE 2026-09-05, driving the real `prepush_gate.decide()` against a
scratch clone, one commit per file, for each of the 36 tracked files whose
basename the filter rejected:

    before   33 of 36 returned FULL SUITE,  3 narrowed
    after     4 of 36 returned FULL SUITE, 32 narrowed

The four that still widen are `LICENSE`, `NOTICE`, `.secrets.baseline` and
`.worktreeinclude`. The first two have neither a dot nor a separator and are
indistinguishable by shape from an ordinary word; the second two are accepted by
the filter now but named by no test in the tree, so no route reaches them and
widening is the correct answer. Accepting bare hyphenated and ALL-CAPS words was
measured too: it grew the literal set from 14,453 to 21,192 and moved two files.
Rejected as a trade.

This file is the LITERAL FILTER half. The subtree-sweep route that fixed the
`*.service` templates is
`tests/test_a_directory_a_test_sweeps_but_no_route_reached.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.day_mode import (  # noqa: E402
    _looks_like_a_reference,
    build_index,
    select,
    tracked_files,
)

# Every shape the fix exists to accept, with the tracked file that motivated it.
MUST_ACCEPT = [
    (".gitignore", "a dotfile whose dot opens the name rather than a suffix"),
    (".gitattributes", "the same, one character longer than the old bound"),
    (".gitkeep", "four tracked copies, in four different directories"),
    (".python-version", "a dotfile carrying a hyphen"),
    (".worktreeinclude", "a dotfile longer than any extension in the tree"),
    (".secrets.baseline", "a dotfile AND a long extension at once"),
    (".env.example", "a seven-character extension on a dotfile"),
    ("bridge-daemon.service", "a hyphen defeats the dotted-identifier route"),
    ("nightly-refresh.service", "one of 21 tracked *.service unit templates"),
    ("docs/DOCS-PIPELINE.md", "a path, accepted before and after"),
    ("conftest.py", "an ordinary basename, accepted before and after"),
    ("scripts.utils.paths", "a dotted module name, accepted before and after"),
]

# Prose the filter must keep dropping. A filter that accepts everything puts every
# English word in the repository into the cache and compares changed paths against
# sentences, which is the failure the filter was written to prevent.
MUST_REJECT = [
    ("the quick brown fox", "a sentence: spaces"),
    ("", "the empty string"),
    ("x" * 201, "longer than the 200-character bound"),
    ("one\ntwo", "carries a newline"),
    ("LICENSE", "a bare word, deliberately still rejected"),
    ("pre-push", "a bare hyphenated word, deliberately still rejected"),
    ("Sentinel", "a single capitalised identifier, no dot and no separator"),
    (".", "a lone dot"),
    (".1234", "a leading dot followed by digits, not a name"),
]


@pytest.mark.parametrize("text,why", MUST_ACCEPT, ids=[t for t, _ in MUST_ACCEPT])
def test_the_filter_accepts_every_filename_shape_this_tree_carries(text, why):
    assert _looks_like_a_reference(text), why


@pytest.mark.parametrize("text,why", MUST_REJECT, ids=[repr(t)[:24] for t, _ in MUST_REJECT])
def test_the_filter_still_drops_prose(text, why):
    assert not _looks_like_a_reference(text), why


def test_the_extension_bound_covers_the_longest_extension_in_this_tree():
    """The bound is a number, so the tree is what has to be asked, not the number.

    MEASURED 2026-09-05 over 2375 tracked files: the longest extension on a
    non-dotfile basename is `.destinations`, 12 characters after the dot. If a
    longer one lands, this fails rather than silently reading it as prose.
    """
    plain = re.compile(r"\.[A-Za-z][A-Za-z0-9]*\Z")
    suffixes = {
        Path(rel).suffix
        for rel in tracked_files(ROOT)
        if not Path(rel).name.startswith(".") and plain.fullmatch(Path(rel).suffix or "")
    }
    assert len(suffixes) >= 20, f"corpus floor: only {len(suffixes)} extensions found"
    for suffix in sorted(suffixes):
        assert _looks_like_a_reference(f"anything{suffix}"), suffix


def test_a_hyphenated_unit_template_is_no_longer_read_as_prose():
    """The asymmetry that made the bug look random, asserted in both directions."""
    assert _looks_like_a_reference("sentinel.service"), "was already accepted"
    assert _looks_like_a_reference("bridge-daemon.service"), "the regression"
    assert _looks_like_a_reference("odin-cadence.timer")


def test_a_dotfile_change_now_reaches_the_tests_that_name_it():
    """The real index, not the filter alone: an accepted literal becomes an edge.

    MEASURED 2026-09-05: a `.gitignore` change selected 198 test files where
    before it selected none and the push gate widened to the whole suite.
    """
    index = build_index(ROOT, use_cache=False)
    selection = select(index, [".gitignore"])
    assert ".gitignore" not in selection.undecided
    named = [t for t, why in selection.routes.items() if any(r.startswith("literal:") for r in why)]
    assert named, "no test was attributed to .gitignore by the literal route"


def test_a_file_no_test_names_is_still_undecided():
    """The direction that matters: the gate must still widen where nothing reaches.

    A fix that routed every file somewhere would be worse than the bug it
    replaced, because the push would then run a narrow selection while the guard
    that covers the change never ran. `LICENSE` is the anchor: tracked, changed
    by an ordinary commit, and named by no test and covered by no directory glob.
    """
    index = build_index(ROOT, use_cache=False)
    selection = select(index, ["LICENSE"])
    assert selection.undecided == ["LICENSE"]
