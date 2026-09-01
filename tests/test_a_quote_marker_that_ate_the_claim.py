#!/usr/bin/env python3
"""`line.lstrip('> ')` takes a character SET, and it ate the ">" in ">100 Gbps".

`scripts/md-to-docx-competitive.py` rendered a markdown blockquote by stripping
the marker with `line.lstrip('> ')`. `str.lstrip` does not remove a PREFIX; it
removes every leading character that appears in the argument. So a callout
written to make a throughput claim came out making a weaker one:

    input                              rendered            correct
    '> >100 Gbps sustained throughput' '100 Gbps ...'      '>100 Gbps ...'
    '> >50% of the market'             '50% of the market' '>50% of the market'
    '>> escalated note'                'escalated note'    'escalated note'
    '>  >2x faster'                    '2x faster'         '>2x faster'

Measured 2026-08-29 by running both forms over the same five lines. In a
competitive-analysis document ">100 Gbps" and "100 Gbps" are different claims,
and the DOCX is the artifact that leaves the building.

Its twin had already been bitten and fixed. `scripts/md-to-docx-proposal.py`
carries a comment at the bullet branch saying `stripped[2:]`, not
`.lstrip('- ')`, because a child item reading `-5% margin` came out as
`5% margin`. That fix landed in one of two files, which is the shape this
repository keeps producing.

The replacement removes markers one at a time and stops at a ">" that is not one
(no space and no further ">" after it), so a nested quote still unwraps and a
claim still survives.

The second half of this file is the rule. Every `lstrip`/`rstrip` in the engine
whose argument is a multi-character literal is either a genuine character set or
a prefix strip in disguise, and the two are indistinguishable by shape, so each
one is declared with the reason its character-set semantics are correct.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources, tracked_paths  # noqa: E402

docx = pytest.importorskip("docx")


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


comp = _load("md-to-docx-competitive", "md_to_docx_competitive_s78")


def _render(callout: str, tmp_path, monkeypatch) -> list[str]:
    """Render a document whose only blockquote is `callout`.

    The eleven-line preamble is load-bearing. `build_docx` discards any `>` line
    at index < 10 as the document's metadata block, so a callout written at the
    top of a short fixture never reaches the branch under test and the fixture
    measures nothing. Found by rendering one and getting a cover page back.
    """
    body = "# Competitive Analysis\n\n" + "Filler paragraph.\n\n" * 5
    src = tmp_path / "in.md"
    src.write_text(f"{body}## Throughput\n\n{callout}\n", encoding="utf-8")
    out = tmp_path / "out.docx"
    monkeypatch.setattr(comp, "input_path", lambda p=str(src): p)
    monkeypatch.setattr(comp, "output_path", lambda p=str(out): p)
    comp.build_docx()
    return [p.text for p in docx.Document(str(out)).paragraphs]


# ============================================================
# The claim survives the marker
# ============================================================

def test_a_callout_keeps_the_greater_than_that_carries_its_claim(tmp_path, monkeypatch):
    """The measured case. `>100 Gbps` is not `100 Gbps`."""
    paragraphs = _render("# Head\n\n> >100 Gbps sustained throughput\n",
                         tmp_path, monkeypatch)
    assert ">100 Gbps sustained throughput" in paragraphs, paragraphs
    assert "100 Gbps sustained throughput" not in paragraphs, (
        "the marker strip ate the '>' that carried the claim")


def test_a_percentage_claim_survives_too(tmp_path, monkeypatch):
    paragraphs = _render("# Head\n\n> >50% of the market\n", tmp_path, monkeypatch)
    assert ">50% of the market" in paragraphs, paragraphs


def test_an_ordinary_callout_still_loses_its_marker(tmp_path, monkeypatch):
    """The mirror case. A fix that stopped stripping at all would pass the two
    tests above and break every callout in the document."""
    paragraphs = _render("# Head\n\n> ordinary callout\n", tmp_path, monkeypatch)
    assert "ordinary callout" in paragraphs, paragraphs
    assert "> ordinary callout" not in paragraphs


def test_a_nested_quote_still_unwraps(tmp_path, monkeypatch):
    """`>>` is two markers, not a marker and a claim: nothing follows the first
    one but another marker, so both go."""
    paragraphs = _render("# Head\n\n>> escalated note\n", tmp_path, monkeypatch)
    assert "escalated note" in paragraphs, paragraphs


def test_extra_spacing_between_the_marker_and_the_claim(tmp_path, monkeypatch):
    paragraphs = _render("# Head\n\n>  >2x faster\n", tmp_path, monkeypatch)
    assert ">2x faster" in paragraphs, paragraphs


def test_a_padded_nested_quote_unwraps_all_the_way(tmp_path, monkeypatch):
    """Padding AND nesting together, which neither case above reaches.

    The loop consumes the space after each marker with `.lstrip(' ')` before
    testing the next one. Deleting that `.lstrip` looks harmless because the
    final `.strip()` cleans up a single marker either way - `"> x"`, `">> x"`
    and `">  >x"` all render identically with or without it, so all five cases
    above stayed green when it was removed. MEASURED 2026-09-01: only `">  > x"`
    separates them, because without the inner strip the loop halts on the two
    spaces and the SECOND marker survives into the rendered text as `"> x"`.

    `>  > note` is an ordinary nested blockquote, so the marker must go.
    """
    paragraphs = _render("# Head\n\n>  > deeply nested note\n", tmp_path, monkeypatch)
    assert "deeply nested note" in paragraphs, paragraphs
    assert "> deeply nested note" not in paragraphs, (
        "the inner marker survived: the space after the outer marker was not "
        "consumed, so the loop stopped one marker early")


def test_a_bare_marker_line_adds_no_empty_callout(tmp_path, monkeypatch):
    """The loop must terminate on a line that is nothing but a marker."""
    paragraphs = _render("# Head\n\n>\n", tmp_path, monkeypatch)
    assert [p for p in paragraphs if p.strip() == ">"] == [], paragraphs


def test_the_old_form_really_did_corrupt_these(tmp_path, monkeypatch):
    """Anti-vacuity. If `lstrip('> ')` agreed with the replacement on every line
    above, none of those tests would be measuring the fix."""
    corrupted = [line for line in ("> >100 Gbps sustained", "> >50% of the market",
                                   ">  >2x faster")
                 if line.lstrip("> ").strip() != _strip_markers(line)]
    assert len(corrupted) == 3, corrupted


def _strip_markers(line: str) -> str:
    """The replacement rule, restated here so the anti-vacuity test above does
    not import the loop it is comparing against."""
    text = line
    while text.startswith(">") and (len(text) == 1 or text[1] in " >"):
        text = text[1:].lstrip(" ")
    return text.strip()


def test_the_generator_no_longer_spells_the_character_set_form():
    """The behaviour tests render one document. This pins the source, so a
    revert is caught even if the branch stops being reachable."""
    src = (ROOT / "scripts" / "md-to-docx-competitive.py").read_text(encoding="utf-8")
    # AST, not substring: the comment above the fix quotes the broken form on
    # purpose, and a substring check fired on the explanation of its own defect.
    assert [s for s in directional_strip_sites(src) if s[1:] == ("lstrip", "> ")] == []


# ============================================================
# And every other multi-character strip is declared
# ============================================================

# Each entry is (relative path, method, argument) -> why the CHARACTER SET is the
# right semantics there. A prefix strip does not belong on this list; it belongs
# rewritten. `strip()` with a punctuation set is not included: removing leading
# and trailing punctuation from a token is what a character set is FOR, and a
# rule that flagged it would be turned off. This registry is `lstrip`/`rstrip`,
# where a directional strip of a multi-character literal is the trap.
DECLARED_DIRECTIONAL_STRIPS = {
    ("scripts/regenerate-docs-html.py", "rstrip", " ,;:-"):
        "trims trailing punctuation off a truncated snippet before the ellipsis; "
        "any mix of those characters may end the window, so the set is meant",
    ("scripts/utils/markdown.py", "lstrip", "\r\n"):
        "drops the blank lines between the closing frontmatter fence and the "
        "body, deliberately, so a body starts at content; the byte-preserving "
        "reader beside it is split_frontmatter_raw",
    ("tests/test_handoff_redaction.py", "rstrip", ".,;:)"):
        "trims trailing punctuation off a path captured out of prose",
    ("tests/test_the_flags_a_tool_accepted_and_never_sent.py", "lstrip", "# "):
        "reads a markdown heading of unknown depth out of a document under test",
    ("tests/test_vps_guide_installs_a_unit_that_starts.py", "lstrip", "> "):
        "unwraps a blockquote in a fixture document whose content is known and "
        "starts with no '>'",
    ("tests/bridge/test_a_prefix_strip_that_ate_a_character_set.py", "lstrip", "./"):
        "the defect fixture of the earlier shard that fixed this shape in five "
        "bridge readers; it must keep spelling the broken form",
    ("tests/test_a_quote_marker_that_ate_the_claim.py", "lstrip", "> "):
        "this file's own anti-vacuity test, which must keep spelling the broken "
        "form to prove the replacement disagrees with it; declared rather than "
        "skipped, because a rule that exempts its own file is a rule with a hole",
    ("scripts/artifact-evaluator.py", "lstrip", "-*"):
        "a 'Consumed by:' label may open its line behind a markdown list marker "
        "of unknown length ('-', '*', '**', '- **'), and the label cleanup two "
        "lines below removes '*', '_' and backtick but NOT '-'. So the '-' has "
        "to come off here or '- Consumed by: /design' never matches. MEASURED "
        "2026-08-31 by narrowing the set each way: dropping '-' reddens "
        "test_the_evaluator_accepts_a_real_pointer[- Consumed by: /design]; "
        "dropping '*' reddens no behavioural test at all, because "
        ".replace('*', '') already absorbs it. The '*' is therefore recorded as "
        "redundancy, not claimed as load-bearing. A prefix slice is the wrong "
        "shape either way: the marker run has no fixed length, and slicing a "
        "fixed count off a line that carries no marker eats the label instead "
        "-- rewriting this as [1:] reddens five tests",
    ("tests/test_two_skill_contracts_that_were_declared_and_never_measured.py",
     "lstrip", "-*"):
        "the CI gate's copy of the line above, deliberately spelled identically "
        "so the advisory single-artifact evaluator and the corpus-wide gate "
        "cannot drift into disagreeing about what counts as a pointer; "
        "test_the_evaluator_agrees_with_the_corpus_gate, in "
        "tests/test_two_gates_that_could_not_see_what_they_checked.py, pins the "
        "two together on the live corpus, so the duplication is held rather "
        "than merely hoped for. Both sites accept '- Consumed by:' and "
        "'**Consumed by:**', which that file's _POINTER_SHAPES pins",
}


def directional_strip_sites(source: str) -> list[tuple[int, str, str]]:
    """(line, method, argument) for each lstrip/rstrip on a multi-char literal."""
    found = []
    for node in ast.walk(ast.parse(source)):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                and func.attr in ("lstrip", "rstrip") and len(node.args) == 1):
            continue
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        if len(set(arg.value)) > 1:
            found.append((node.lineno, func.attr, arg.value))
    return found


_DEFECT_FIXTURE = "text = line.lstrip('> ').strip()\n"
_FIXED_FIXTURE = "text = line[1:].strip()\n"
_CHARSET_FIXTURE = "token = part.strip('.,;:!?')\nname = path.rstrip('/')\n"


def test_the_rule_fires_on_the_shape_that_caused_the_incident():
    assert directional_strip_sites(_DEFECT_FIXTURE) == [(1, "lstrip", "> ")]


def test_the_rule_accepts_the_shape_that_replaced_it():
    assert directional_strip_sites(_FIXED_FIXTURE) == []


def test_the_rule_leaves_strip_and_a_single_character_alone():
    """`.strip(set)` is idiomatic and `rstrip('/')` has one character, so
    neither can be a prefix strip in disguise."""
    assert directional_strip_sites(_CHARSET_FIXTURE) == []


def _scanned() -> list[Path]:
    return tracked_paths(("scripts/**/*.py", ".claude/**/*.py", "tests/**/*.py"))


def _corpus() -> list[tuple[str, str]]:
    """(relative path, source) for every file the two rules below judge.

    A SCAN, so it reads through `read_sources`: the walk and the read are two
    moments, and a scratch `.py` a parallel worker writes into `tests/` and
    removes can sit inside that window. A file that is gone carries no strip
    site, so it is skipped WITH a warning naming it rather than killing the
    sweep with FileNotFoundError. `test_the_sweep_reaches_a_real_corpus` holds
    the floor over what was actually read.

    The `UnicodeDecodeError: continue` branch that stood here is gone on
    purpose. Everything walked is a tracked `.py`; one that is not UTF-8 is a
    real fault about a file that IS there, and skipping it shrank the corpus
    this sweep claims to have judged. `read_sources` draws that line the same
    way and its docstring says why.
    """
    vanished = []
    return [
        (path.relative_to(ROOT).as_posix(), source)
        for path, source in read_sources(_scanned(), vanished)
    ]


# The two rules are pure functions of (corpus, registry) so they can be measured
# on synthetic input. Over the live tree both are green by construction -- the
# tree carries no undeclared site and no stale declaration -- so deleting the
# line that COLLECTS a violation changes no result there and the deletion
# survives. That is not a hypothetical: both mutations survived the first run of
# this file's harness on 2026-08-29, which is the third time this repository has
# produced a guard that is green over an empty corpus.

def undeclared_strip_sites(corpus, declared) -> list[str]:
    out = []
    for rel, source in corpus:
        try:
            sites = directional_strip_sites(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        for line, method, chars in sites:
            if (rel, method, chars) not in declared:
                out.append(f"{rel}:{line}  {method}({chars!r})")
    return out


def live_strip_sites(corpus) -> set[tuple[str, str, str]]:
    live: set[tuple[str, str, str]] = set()
    for rel, source in corpus:
        try:
            sites = directional_strip_sites(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        live.update((rel, method, chars) for _line, method, chars in sites)
    return live


def stale_declarations(declared, live) -> list:
    return sorted(k for k in declared if k not in live)


def declarations_without_a_reason(declared) -> list:
    return sorted(k for k, v in declared.items() if not v.strip())


_SYNTHETIC = [
    ("a/declared.py", "x = s.lstrip('> ')\n"),
    ("b/undeclared.py", "y = s.rstrip('!?')\n"),
    ("c/innocent.py", "z = s.strip('.,')\nw = s.rstrip('/')\n"),
]
_SYNTHETIC_REGISTRY = {("a/declared.py", "lstrip", "> "): "declared in the fixture"}


def test_the_undeclared_rule_names_the_undeclared_site_only():
    found = undeclared_strip_sites(_SYNTHETIC, _SYNTHETIC_REGISTRY)
    assert found == ["b/undeclared.py:1  rstrip('!?')"], found


def test_the_undeclared_rule_is_silent_when_everything_is_declared():
    """The other direction. A rule that always fires is as useless as one that
    never does, and only the pair of cases separates them."""
    registry = dict(_SYNTHETIC_REGISTRY)
    registry[("b/undeclared.py", "rstrip", "!?")] = "declared too"
    assert undeclared_strip_sites(_SYNTHETIC, registry) == []


def test_the_staleness_rule_names_a_declaration_with_no_site():
    live = live_strip_sites(_SYNTHETIC)
    assert live == {("a/declared.py", "lstrip", "> "),
                    ("b/undeclared.py", "rstrip", "!?")}, live
    registry = dict(_SYNTHETIC_REGISTRY)
    registry[("gone/deleted.py", "lstrip", "# ")] = "the file was removed"
    assert stale_declarations(registry, live) == [("gone/deleted.py", "lstrip", "# ")]


def test_the_staleness_rule_is_silent_when_every_declaration_is_live():
    assert stale_declarations(_SYNTHETIC_REGISTRY, live_strip_sites(_SYNTHETIC)) == []


def test_the_sweep_reaches_a_real_corpus():
    """Green over an empty corpus otherwise. 900+ files on 2026-08-29.

    Both the walk AND the read are checked: a corpus builder that returned an
    empty list would make every rule below pass while measuring nothing, and
    the file count alone cannot see that.
    """
    assert len(_scanned()) > 500, f"only {len(_scanned())} files scanned"
    corpus = _corpus()
    assert len(corpus) > 500, f"only {len(corpus)} sources read"
    assert any(rel.endswith("md-to-docx-competitive.py") for rel, _ in corpus)


def test_every_directional_strip_of_a_character_set_is_declared():
    """A new one must be argued for, not inherited.

    This shape has now been fixed three times: `- ` in md-to-docx-proposal.py,
    `./` in five of six bridge readers, and `> ` here. Three is where a
    repository stops relying on the next reader noticing.
    """
    undeclared = undeclared_strip_sites(_corpus(), DECLARED_DIRECTIONAL_STRIPS)
    assert not undeclared, (
        "`lstrip`/`rstrip` takes a character SET, not a prefix. Rewrite these as "
        "a slice, or add an entry to DECLARED_DIRECTIONAL_STRIPS saying why the "
        "set is the right semantics:\n  " + "\n  ".join(undeclared))


def test_the_registry_does_not_outlive_its_sites():
    """A registry naming a site that no longer carries the shape waves through
    whatever is written at that path next."""
    stale = stale_declarations(DECLARED_DIRECTIONAL_STRIPS, live_strip_sites(_corpus()))
    assert stale == [], f"declared strip sites that no longer exist: {stale}"


def test_the_reason_rule_names_a_declaration_with_a_blank_reason():
    """Both directions on synthetic input. Over the live registry every entry
    already carries prose, so a rule that collected nothing would be green there
    and its mutation survived the second harness run on 2026-08-29."""
    assert declarations_without_a_reason({("a.py", "lstrip", "> "): "  "}) == [
        ("a.py", "lstrip", "> ")]
    assert declarations_without_a_reason({("a.py", "lstrip", "> "): "why"}) == []


def test_every_declaration_carries_a_reason():
    empty = declarations_without_a_reason(DECLARED_DIRECTIONAL_STRIPS)
    assert empty == [], f"declared with no reason written down: {empty}"
