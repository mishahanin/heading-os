"""The lint and the recall graph disagreed about which notes exist.

`scripts/odin_brain_lint.py` carried this comment from the day it was written:

    Reuse the PageRank resolver primitives so "what counts as resolvable" is
    defined in exactly one place -- a wiki-link the lint flags is precisely one
    the recall graph (scripts/odin_pagerank.py) would also fail to wire an edge
    for.

It imported the primitives and then rebuilt the note universe by hand, narrower
on two axes. `collect_brain_files` walks six named SUBDIRS with a NON-recursive
`glob("*.md")` and drops any note whose frontmatter is missing or empty.
`odin_pagerank.build_graph` walks the whole brain root with `rglob` and registers
a node keyed by `id or stem` whether or not frontmatter is present.

MEASURED 2026-08-31 on a four-note fixture (one nested a level deeper, two with
no frontmatter): the graph resolved 4 tokens and the lint 1. Three real links
would have been reported as dangling.

The divergence ran ONE way only, which is why it produced false warnings rather
than missed ones, and why nothing caught it: no note in the live brain links to
an affected file yet. A false warning is still expensive. `dangling_wikilink` is
a WARN precisely so the backlog stays visible, and warnings nobody can trust are
warnings people stop reading.

This file pins the parity itself, not one example of it. It compares the two
resolvers against each other over a fixture built to hit every axis of the old
divergence, so a future narrowing of either side fails here rather than being
found by a reader wondering why a link they can see is called dangling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import odin_pagerank as pr  # noqa: E402
from scripts.odin_brain_lint import check_dangling_wikilinks, collect_brain_files  # noqa: E402


@pytest.fixture
def brain(tmp_path):
    """A brain hitting all three axes the old lint was narrow on.

    * `nested/bar.md` sits one level below a SUBDIR, which the non-recursive
      `glob` never saw.
    * `baz.md` has no frontmatter, which the old `if not fm: continue` dropped.
    * `INDEX.md` sits at the brain ROOT, outside every SUBDIR.

    `foo.md` links to all three, so each one is a separate would-be warning.
    """
    root = tmp_path / "odin-brain"
    (root / "principles" / "nested").mkdir(parents=True)
    (root / "principles" / "foo.md").write_text(
        "---\nid: foo\ntitle: Foo\ntype: principle\n---\n\n"
        "Body links [[bar]], [[baz]] and [[INDEX]].\n",
        encoding="utf-8")
    (root / "principles" / "nested" / "bar.md").write_text(
        "---\nid: bar\ntitle: Bar\ntype: principle\n---\n\nnested note\n",
        encoding="utf-8")
    (root / "principles" / "baz.md").write_text(
        "a note with no frontmatter at all\n", encoding="utf-8")
    (root / "INDEX.md").write_text(
        "a root-level note with no frontmatter\n", encoding="utf-8")
    return root


def test_the_graph_resolves_every_token_the_lint_calls_dangling(brain):
    """The assertion the whole file exists for.

    Not "the lint reports zero warnings", which would pass over a lint that
    reports nothing at all. This asks the two resolvers the SAME question about
    the same tokens and requires the same answer.
    """
    graph = pr.build_graph(brain, brain)
    files_by_subdir, _ids, _slugs = collect_brain_files(brain)
    issues = check_dangling_wikilinks(files_by_subdir, brain)

    flagged = {issue["target"] for issue in issues}
    resolvable_by_graph = {t for t in ("bar", "baz", "INDEX") if graph.resolve(t)}

    assert resolvable_by_graph == {"bar", "baz", "INDEX"}, (
        f"the fixture is wrong, not the code: the graph should resolve all three "
        f"and resolved {sorted(resolvable_by_graph)}")
    assert not (flagged & resolvable_by_graph), (
        f"the lint called {sorted(flagged & resolvable_by_graph)} dangling while "
        f"the recall graph wires edges for them. The two note universes have "
        f"diverged again; make the lint ask BrainGraph.resolve rather than "
        f"rebuild its own set.")


def test_a_genuinely_absent_target_is_still_flagged(brain):
    """The other direction, so the test above cannot pass over a lint that
    stopped flagging anything at all.

    A parity check alone is satisfied by two resolvers that both resolve
    everything, which would be a lint that never warns.
    """
    (brain / "principles" / "foo.md").write_text(
        "---\nid: foo\ntitle: Foo\ntype: principle\n---\n\n"
        "This links [[a-note-that-does-not-exist]].\n",
        encoding="utf-8")
    files_by_subdir, _ids, _slugs = collect_brain_files(brain)
    issues = check_dangling_wikilinks(files_by_subdir, brain)
    assert {i["target"] for i in issues} == {"a-note-that-does-not-exist"}
    assert all(i["severity"] == "warn" for i in issues)


def test_the_resolver_rule_lives_in_one_place():
    """`BrainGraph.resolve` must be what applies the `_slug` fallback.

    The rule was `g._resolver.get(tok) or g._resolver.get(_slug(tok))`, written
    out twice: once in `build_graph`'s second pass and once, differently, in the
    lint. Reproducing a rule is how two copies drift, so this asserts the method
    exists and that the fallback is inside it rather than at the call sites.
    """
    graph = pr.BrainGraph()
    graph._resolver["some-slug"] = "node-key"

    assert graph.resolve("some-slug") == "node-key"
    # A token that only matches after slugification proves the fallback is
    # inside `resolve`, not bolted on by whoever calls it.
    assert graph.resolve("Some Slug") == "node-key", (
        "resolve() did not apply the _slug fallback, so every caller has to "
        "remember it and the two that existed already disagreed")
    assert graph.resolve("nothing-like-it") is None


def test_the_lint_no_longer_builds_its_own_token_set():
    """Asked of the source, because the behavioural test above can be satisfied
    by a hand-built set that happens to agree on this fixture.

    What must not come back is the pattern, not this fixture's outcome.
    """
    import ast

    source = (ROOT / "scripts" / "odin_brain_lint.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "check_dangling_wikilinks"),
        None,
    )
    assert target is not None, "check_dangling_wikilinks is gone; this test is stale"

    calls = {
        n.func.attr if isinstance(n.func, ast.Attribute) else
        (n.func.id if isinstance(n.func, ast.Name) else "")
        for n in ast.walk(target) if isinstance(n, ast.Call)
    }
    assert "resolve" in calls, (
        "check_dangling_wikilinks does not call resolve(); it is deciding "
        "resolvability by itself again")
    assert "build_graph" in calls, (
        "check_dangling_wikilinks does not build the graph, so its note universe "
        "is not the graph's")
