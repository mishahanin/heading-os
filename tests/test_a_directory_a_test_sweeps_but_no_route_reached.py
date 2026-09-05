"""A test that globs one directory reached none of the files it opens.

Day mode had four routes to a test: the file is the test, a conftest covers it,
something imports it, or a string constant names it. A directory full of files
that nothing imports and no string names -- `docs/*.html`, the systemd unit
templates under `scripts/templates/systemd/` -- reached none of them, even though
tests open every one of those files by enumerating the directory. A test that
runs `DOCS.glob("*.html")` never spells a single page's name anywhere.

The tree sweep detector did not see them either, and the reason is one line.
`_is_rootish` reads ONE expression and cannot follow a name to its assignment, so
`DOCS = ROOT / "docs"` followed by `DOCS.glob("*.html")` presents a bare `Name`
whose id neither is nor contains `ROOT`. MEASURED 2026-09-05: 68 test files carry
a sweep of that shape, over 47 distinct directory/pattern pairs.

MEASURED 2026-09-05, driving the real `prepush_gate.decide()` against a scratch
clone with one commit per file:

    docs/RULES-REFERENCE.html                          FULL SUITE  ->  158 files
    scripts/templates/systemd/archive-transcripts.service  FULL SUITE  ->  159
    12 of the 16 hyphenated *.service templates        FULL SUITE  ->  159-160

WHY THIS CANNOT SWALLOW THE WIDENING, which is the property that makes the route
safe rather than merely useful. `_scoped_sweep` refuses any sweep whose directory
resolves to the repository root, so the widest edge this route can build is one
subdirectory across. A sweep of the whole tree stays what it was: the mandatory
core, which runs on every invocation and attributes nothing to any single file.
`test_a_root_wide_sweep_never_becomes_a_route` is that assertion.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.day_mode import (  # noqa: E402
    Facts,
    build_index,
    extract,
    select,
    swept_by,
)

DOCS_TEST = '''
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
def test_pages():
    for page in sorted(DOCS.glob("*.html")):
        assert page.read_text()
'''

ROOT_TEST = '''
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
def test_everything():
    for path in ROOT.rglob("*"):
        assert path.exists()
'''


def test_a_sweep_through_a_module_level_constant_is_seen():
    """The exact shape three of the four docs tests use."""
    facts = extract("tests/test_x.py", DOCS_TEST)
    assert ("glob", "docs", "*.html") in facts.scoped


def test_a_root_wide_sweep_never_becomes_a_route():
    """The bound. A sweep of the whole tree is the core's business, not a route.

    Were it a route, every tracked file would reach it, nothing would ever be
    undecided, and `prepush_gate.decide()` would stop widening entirely: a gate
    that always narrows is the one defect this whole design exists to prevent.
    """
    facts = extract("tests/test_y.py", ROOT_TEST)
    assert facts.scoped == frozenset()
    assert facts.sweeps, "it is still counted as a tree sweep, so it joins the core"


@pytest.mark.parametrize(
    "rel,kind,directory,pattern,expected",
    [
        ("docs/index.html", "glob", "docs", "*.html", True),
        # A non-recursive glob does NOT descend. `docs/*.html` never returned a
        # page under `docs/assets/`, and matching the whole string with fnmatch
        # would have handed it one.
        ("docs/assets/x.html", "glob", "docs", "*.html", False),
        ("docs/assets/x.html", "rglob", "docs", "*.html", True),
        ("docs/index.md", "glob", "docs", "*.html", False),
        ("scripts/templates/systemd/ops-radar.service", "glob",
         "scripts/templates/systemd", "*.service", True),
        (".claude/skills/osint/SKILL.md", "glob", ".claude/skills", "*/SKILL.md", True),
        (".claude/skills/osint/references/a.md", "glob", ".claude/skills", "*/SKILL.md", False),
        ("docsx/index.html", "glob", "docs", "*.html", False),
        ("docs", "glob", "docs", "*", False),
    ],
)
def test_swept_by_answers_what_the_glob_would_have_returned(
    rel, kind, directory, pattern, expected
):
    assert swept_by(rel, kind, directory, pattern) is expected


def test_a_docs_page_is_no_longer_undecided():
    """The real index and the real selector, not a constructed one.

    `docs/RULES-REFERENCE.html` is generated from its `.md` sibling, imported by
    nothing and named by no test. Before the subtree route it reached no test at
    all and every push carrying it ran the whole suite.
    """
    index = build_index(ROOT, use_cache=False)
    selection = select(index, ["docs/RULES-REFERENCE.html"])
    assert selection.undecided == []
    reached = [t for t, why in selection.routes.items() if any(r.startswith("subtree:") for r in why)]
    assert reached, "no test was attributed to the page by the subtree route"


def test_a_unit_template_is_no_longer_undecided():
    index = build_index(ROOT, use_cache=False)
    selection = select(index, ["scripts/templates/systemd/archive-transcripts.service"])
    assert selection.undecided == []


def test_the_corpus_of_scoped_sweeps_has_a_floor():
    """A route with no edges passes every assertion above and selects nothing.

    MEASURED 2026-09-05: 68 test files carry a scoped subtree sweep. The floor is
    set well under that so ordinary churn does not fail it, and a collapse to
    zero -- a resolver that stopped resolving -- does.
    """
    index = build_index(ROOT, use_cache=False)
    assert len(index.subtree_sweeps) >= 40, (
        f"only {len(index.subtree_sweeps)} test files carry a scoped sweep; "
        "the resolver has stopped resolving"
    )
    directories = {directory for sweeps in index.subtree_sweeps.values() for _, directory, _ in sweeps}
    assert "" not in directories, "a root-wide sweep leaked into the scoped route"


def test_only_test_files_carry_a_route():
    """A route has to end at something pytest can run, and `select` marks a
    changed file DECIDED the moment any route fires. If a script's glob entered
    this map, a change under the directory that script sweeps would count as
    reached, the push gate would stop widening on it, and the narrowed run would
    contain no test that opens the file. Caught as a surviving mutation on
    2026-09-05: sourcing the map from `index.tracked` instead of
    `index.test_files` changed no assertion in this file until this one existed.
    """
    index = build_index(ROOT, use_cache=False)
    from scripts.utils.day_mode import is_test_file

    offenders = [rel for rel in index.subtree_sweeps if not is_test_file(rel)]
    assert offenders == [], f"non-test files carry a subtree route: {offenders[:5]}"


def test_the_resolver_reads_module_level_bindings_only():
    """Stated as a limit rather than left to be discovered.

    A name bound inside a function is not followed. Every scoped sweep measured
    in this tree on 2026-09-05 reads a module-level constant, and following a
    local would mean a scope analysis for no measured gain.
    """
    source = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "def test_pages():\n"
        "    local = ROOT / 'docs'\n"
        "    assert list(local.glob('*.html'))\n"
    )
    assert extract("tests/test_z.py", source).scoped == frozenset()


def test_facts_defaults_to_no_scoped_sweeps():
    assert Facts().scoped == frozenset()


def test_an_unparseable_file_yields_no_scoped_sweeps():
    assert extract("tests/test_bad.py", "def (:").scoped == frozenset()
    assert isinstance(ast.parse("x = 1"), ast.Module)
