#!/usr/bin/env python3
"""A `Tests:` pointer a reader could not act on.

`scripts/classification-health.py` cited exactly one test file, and its name
describes a chronicle defect: `test_a_topic_list_shredded_into_single_letters`
is named for a model reply whose `"topics"` string was iterated character by
character in `scripts/chronicle.py`. Shard `scripts-04-p1` F4 read that as a
copy-paste from chronicle's own docstring and filed it as a wrong pointer.

Resolved by opening the file rather than trusting the name: its last four tests
import `classification-health.py` and drive `print_outputs_drift`, so the
pointer was TRUE and unusable at the same time. A reader following it skimmed a
module-summary bug, found nothing about classification, and had no way to reach
this script's own two regression files. The header now names all three.

This binds the property that made the original confusing: every path in that
header exists on disk and is about the script that cites it. It says nothing
about the list being complete, which no mechanism can check, and it is
deliberately scoped to this one file rather than every `Tests:` header in the
tree, because a sweep over all of them is a different piece of work with a
different failure mode.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "classification-health.py"

# The token a covering test has to mention. The script is invoked by path
# everywhere (its name carries a hyphen, so it is never imported by module
# name), which makes the filename the one spelling a covering test must use.
SCRIPT_MENTION = "classification-health"


def _cited_test_paths() -> list[str]:
    """Paths under the `Tests:` header of the script's module docstring.

    Read out of the SOURCE, not an import: the script resolves the workspace
    root at import time and this check has no business needing an overlay.
    """
    doc = ast.get_docstring(ast.parse(SCRIPT.read_text(encoding="utf-8")))
    assert doc, f"{SCRIPT.name} has no module docstring"
    lines = doc.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("Tests:")),
        None,
    )
    assert start is not None, f"{SCRIPT.name} cites no tests at all"

    cited: list[str] = []
    for line in lines[start:]:
        found = re.findall(r"tests/[\w./-]+\.py", line)
        if not found and cited:
            break  # the header ends at the first line carrying no path
        cited.extend(found)
    return cited


def test_the_script_cites_at_least_one_test_file():
    assert _cited_test_paths(), (
        "the `Tests:` header lists no test path; an empty pointer is the state "
        "this check exists to notice"
    )


@pytest.mark.parametrize("rel", _cited_test_paths())
def test_every_cited_test_file_is_on_disk(rel):
    assert (REPO_ROOT / rel).is_file(), f"{SCRIPT.name} cites a missing file: {rel}"


@pytest.mark.parametrize("rel", _cited_test_paths())
def test_every_cited_test_file_is_about_this_script(rel):
    """The F4 claim, asked of the file instead of its name.

    A test file that never mentions this script cannot be validating a change
    to it, whatever its title suggests. That is the wrong-pointer defect: a
    reader is sent somewhere that proves nothing about the code they touched.
    """
    body = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert SCRIPT_MENTION in body, (
        f"{SCRIPT.name} cites {rel}, which never mentions {SCRIPT_MENTION}; the "
        f"pointer sends a reader to a test that exercises another module"
    )


def test_the_chronicle_named_file_really_does_cover_this_script():
    """Why F4 is closed rather than fixed by deleting the citation.

    Pinned so that removing those tests from the chronicle-named file, or
    moving them, fails HERE and not silently in the docstring.
    """
    rel = "tests/test_a_topic_list_shredded_into_single_letters.py"
    assert rel in _cited_test_paths()
    body = (REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "print_outputs_drift" in body, (
        "the chronicle-named file no longer drives print_outputs_drift, so the "
        "citation in classification-health.py has stopped being true"
    )
