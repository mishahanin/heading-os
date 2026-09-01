#!/usr/bin/env python3
"""Seven surfaces described /scrutinize's flags, and no two of them agreed.

MEASURED 2026-08-29, by counting the flags each surface enumerates and holding
the counts against `references/flags.md`, which is the catalog every other
surface is a copy of:

    surface                                              flags   missing
    references/flags.md (the catalog)                      5     --
    SKILL.md argument-hint                                 2     low-confidence, ambiguous, no-code-review
    SKILL.md x-heading-capability.how                      2     low-confidence, ambiguous, no-code-review
    SKILL.md x-heading-routing.label                       4     no-code-review
    .claude/rules/skill-router.md (generated from label)   4     no-code-review
    reference/skill-router/operations.md (same)            4     no-code-review
    docs/skills-operations-quality.html, the h3 heading    2     low-confidence, ambiguous, no-code-review
    docs/skills-operations-quality.html, "Customize"       4     no-code-review, and it said "four flags"

`--no-code-review` landed on 2026-08-20 in commit 0ce4506 and reached one of the
six copies. SKILL.md then asserted "One optional `target` plus five flags ...
`argument-hint` in the frontmatter carries the same list in one line", which was
false about a file three lines above it.

The second half of the same drift is a flag that never existed at all. Both
`docs/skills-operations-quality.html` and `references/bias-mitigation.md`
documented `--judge-family={claude|kimi}` as the knob that pins a judge family,
and the docs page went further and said Gemini and Grok were "reachable" through
it. Measured the same day against `scripts/scrutinize-dispatch.py --help`: the
argument is spelled `--family`, its argparse `choices` are exactly
`{claude, kimi}`, and no argument anywhere reaches Gemini or Grok. In
`bias-mitigation.md` the phantom sat in a table headed "Config knobs (CEO
overrides)", one row below a knob that is honest about being prose-only, so it
read as the row that IS wired.

Both halves are the same failure: a flag list is copied, the original moves, and
nothing compares the copies. So this file derives the truth twice, from the code
rather than from prose. The implemented dispatcher arguments come from the real
`argparse.ArgumentParser` that `main()` builds, captured before it parses, so a
mode flag added inside a loop is found the same as a literal one. The skill's own
flags come from the catalog table. Every documented surface is then held against
one or the other, in BOTH directions: a flag documented and not implemented
fails, and a flag implemented and not documented fails.

The predicates are pure functions, unit-tested on synthetic input below, because
over a clean tree the sweep passes whether or not it collects anything.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources, tracked_paths  # noqa: E402


def _sources(paths, what: str) -> list[tuple[Path, str]]:
    """`(path, text)` for a walked list, or a failure naming what disappeared.

    The walk and the read are two moments and a file can go missing between
    them, which used to raise FileNotFoundError from inside the sweep.
    `read_sources` absorbs that, but a QUIET skip is wrong for both callers
    here, so this helper retries once and then fails naming the file:

    * the corpus below is a completeness claim ("every /scrutinize prose file"),
      and a dropped file is a flag list nobody compared to the catalog while the
      test still printed clean;
    * the option derivation is the set a cross-reference is checked against, so
      a dropped script turns a real flag into a reported phantom - an answer
      that is wrong, not merely narrower.
    """
    lost: list[Path] = []
    out = list(read_sources(list(paths), lost))
    if lost:
        still_gone: list[Path] = []
        out += list(read_sources(lost, still_gone))
        if still_gone:
            raise AssertionError(
                f"{what} disappeared between the walk and the read and is still "
                "gone on retry; the flag-list comparison cannot be made over a "
                "file nobody read: " + ", ".join(str(p) for p in still_gone))
    return out


SKILL = ROOT / ".claude" / "skills" / "scrutinize" / "SKILL.md"
CATALOG = ROOT / ".claude" / "skills" / "scrutinize" / "references" / "flags.md"
ROUTER = ROOT / ".claude" / "rules" / "skill-router.md"
ROUTER_CATEGORY = ROOT / "reference" / "skill-router" / "operations.md"
DOCS_CARD_PAGE = ROOT / "docs" / "skills-operations-quality.html"
DISPATCHER = ROOT / "scripts" / "scrutinize-dispatch.py"

# Long options in the /scrutinize corpus that belong to a third-party tool, not
# to this workspace. Each one is declared with the tool that owns it, because an
# allowlist without a reason is how the phantom got in.
EXTERNAL_TOOL_FLAGS = {
    "--no-verify": "git commit, named as the thing never to pass",
    "--oneline": "git log",
    "--porcelain": "git status",
    "--since": "git log",
    "--user": "systemctl --user",
    "--no-cov": "pytest-cov",
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# ============================================================
# Pure predicates (unit-tested on synthetic input at the bottom)
# ============================================================
_FLAG = re.compile(r"--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_CHOICES = re.compile(r"[{\[]([a-z0-9.]+(?:\s*[|,]\s*[a-z0-9.]+)+)[}\]]")


def long_flags(text: str) -> set[str]:
    """Every long option token in a documentation surface.

    HTML entities are unescaped first, so `&lt;--x&gt;` reads the same as the
    markdown form. A bare `--` used as punctuation yields nothing, because the
    pattern needs a letter after the dashes.
    """
    return set(_FLAG.findall(html.unescape(text)))


def flag_drift(documented, implemented) -> tuple[list[str], list[str]]:
    """(documented but not implemented, implemented but not documented).

    Both directions, because a stale copy fails in one of them and a new flag
    that reached only the catalog fails in the other.
    """
    doc, impl = set(documented), set(implemented)
    return sorted(doc - impl), sorted(impl - doc)


def phantom_flags(text: str, allowed) -> list[str]:
    """The long options in ``text`` that no derived source accounts for."""
    return sorted(long_flags(text) - set(allowed))


def enumerated_choices(text: str, flag: str) -> list[set[str]]:
    """Every `{a|b}` (or `{a,b}`) enumeration written right after ``flag``.

    Tolerates the markdown-escaped pipe (`{claude\\|kimi}`), an `=` between the
    flag and the brace, and HTML entities around either.
    """
    plain = html.unescape(text).replace("\\|", "|")
    found: list[set[str]] = []
    for match in re.finditer(re.escape(flag) + r"[=\s]*", plain):
        tail = plain[match.end():]
        enumeration = _CHOICES.match(tail)
        if enumeration:
            found.append({part.strip() for part in re.split(r"[|,]", enumeration.group(1))})
    return found


def spelled_counts(text: str, noun: str = "flags") -> list[int]:
    """Every "plus five flags" style count claimed in ``text``."""
    plain = html.unescape(text).lower()
    pattern = r"\b(" + "|".join(NUMBER_WORDS) + r")\s+" + re.escape(noun) + r"\b"
    return [NUMBER_WORDS[word] for word in re.findall(pattern, plain)]


def docs_card(page: str, anchor: str) -> str:
    """The `<section class="skill">` block whose heading carries ``anchor``."""
    at = page.find(f'id="{anchor}"')
    if at < 0:
        return ""
    start = page.rfind("<section", 0, at)
    end = page.find("</section>", at)
    if start < 0 or end < 0:
        return ""
    return page[start:end]


# ============================================================
# Derived truth
# ============================================================
class _Captured(Exception):
    """Raised out of the probed parse_args once the parser is in hand."""


class _ArgparseShim:
    """argparse, with ArgumentParser swapped. Module-local, so nothing global moves."""

    def __init__(self, real, parser_cls):
        self._real = real
        self.ArgumentParser = parser_cls

    def __getattr__(self, name):
        return getattr(self._real, name)


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("scrutinize_dispatch_flags", DISPATCHER)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the module's dataclasses resolve annotations through
    # sys.modules, per tests/test_scrutinize_dispatch.py.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _dispatcher_options() -> dict[str, set[str] | None]:
    """Every long option the dispatcher's real parser defines, mapped to its choices.

    Captured from the live `argparse.ArgumentParser` that `main()` builds, not
    from the source text. Six of the eleven mode flags are registered inside a
    `for` loop, so a reader that scanned for `add_argument("--literal")` would
    miss them and call them phantoms.
    """
    mod = _load_dispatcher()
    captured: dict[str, argparse.ArgumentParser] = {}

    class _Probe(argparse.ArgumentParser):
        def parse_args(self, *args, **kwargs):  # noqa: ARG002
            captured["parser"] = self
            raise _Captured

    real = mod.argparse
    mod.argparse = _ArgparseShim(real, _Probe)
    try:
        with pytest.raises(_Captured):
            mod.main([])
    finally:
        mod.argparse = real

    parser = captured["parser"]
    options: dict[str, set[str] | None] = {}
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public reader
        for option in action.option_strings:
            if option.startswith("--"):
                options[option] = set(action.choices) if action.choices else None
    return options


def _catalog_flags() -> set[str]:
    """The flags the /scrutinize catalog table documents, which is the source of truth."""
    body = CATALOG.read_text(encoding="utf-8").split("## Flags", 1)[-1]
    flags: set[str] = set()
    for line in body.splitlines():
        row = re.match(r"\|\s*`(--[a-z0-9-]+)`\s*\|", line.strip())
        if row:
            flags.add(row.group(1))
    return flags


def _skill_frontmatter() -> dict:
    text = SKILL.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def _router_row(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| `/scrutinize "):
            return line
    return ""


def _enumerating_surfaces() -> dict[str, str]:
    """Every surface that presents itself as the complete /scrutinize flag list."""
    front = _skill_frontmatter()
    page = DOCS_CARD_PAGE.read_text(encoding="utf-8")
    card = docs_card(page, "s-scrutinize")
    heading = card.split("</h3>", 1)[0]
    customize = next(
        (para for para in card.split("<p>") if "<strong>Customize.</strong>" in para), ""
    )
    return {
        "SKILL.md argument-hint": front["argument-hint"],
        "SKILL.md x-heading-capability.how": front["x-heading-capability"]["how"],
        "SKILL.md x-heading-routing.label": front["x-heading-routing"]["label"],
        ".claude/rules/skill-router.md row": _router_row(ROUTER),
        "reference/skill-router/operations.md row": _router_row(ROUTER_CATEGORY),
        "docs/skills-operations-quality.html h3": heading,
        "docs/skills-operations-quality.html Customize": customize,
    }


def _corpus() -> dict[str, str]:
    """Every /scrutinize prose file, plus the skill's card on the public docs site."""
    files = {
        str(path.relative_to(ROOT)): text
        for path, text in _sources(
            tracked_paths([".claude/skills/scrutinize/**/*.md"]),
            "a /scrutinize prose file")
    }
    card = docs_card(DOCS_CARD_PAGE.read_text(encoding="utf-8"), "s-scrutinize")
    files["docs/skills-operations-quality.html#s-scrutinize"] = card
    return files


DISPATCHER_OPTIONS = _dispatcher_options()
CATALOG_FLAGS = _catalog_flags()
SURFACES = _enumerating_surfaces()
CORPUS = _corpus()


# ============================================================
# The rules, over the live tree
# ============================================================
def test_every_complete_flag_list_carries_exactly_the_catalog():
    """Both directions. A stale copy and an over-eager copy both fail here."""
    broken = {}
    for name, text in SURFACES.items():
        undocumented, missing = flag_drift(long_flags(text), CATALOG_FLAGS)
        if undocumented or missing:
            broken[name] = {"names a flag the catalog does not": undocumented,
                            "omits a catalogued flag": missing}
    assert not broken, (
        "flag lists disagree with .claude/skills/scrutinize/references/flags.md:\n"
        + "\n".join(f"  {name}: {detail}" for name, detail in broken.items())
    )


def test_no_surface_names_a_flag_no_code_reads():
    """The `--judge-family` shape: a plausible flag that argparse never defined."""
    allowed = set(CATALOG_FLAGS) | set(DISPATCHER_OPTIONS) | set(EXTERNAL_TOOL_FLAGS)
    allowed |= _other_script_options()
    phantoms = {
        name: found
        for name, text in CORPUS.items()
        if (found := phantom_flags(text, allowed))
    }
    assert not phantoms, (
        "long options no argparse parser in this repo defines, and no declared "
        f"external tool owns:\n{phantoms}\n"
        "Either the flag is misspelled, or it was never implemented. If a third "
        "party owns it, declare it in EXTERNAL_TOOL_FLAGS with the tool's name."
    )


def test_a_documented_choice_set_equals_the_argparse_choices():
    """`--family {claude|kimi}` is checked against argparse, never against prose.

    The page that carried this clause said Gemini and Grok were reachable through
    the flag. Nothing in the parser has ever offered them.
    """
    checked = 0
    wrong = []
    with_choices = {flag: choices for flag, choices in DISPATCHER_OPTIONS.items() if choices}
    for name, text in CORPUS.items():
        for flag, choices in with_choices.items():
            for written in enumerated_choices(text, flag):
                checked += 1
                if written != choices:
                    wrong.append(f"{name}: {flag} documented as {sorted(written)}, "
                                 f"argparse offers {sorted(choices)}")
    assert not wrong, "\n".join(wrong)
    assert checked, (
        "no documented choice set was compared; the rule measured nothing. "
        f"Dispatcher flags carrying choices: {sorted(with_choices)}"
    )


def test_a_claimed_flag_count_matches_the_catalog():
    """"plus four flags" outlived the fifth flag by nine days."""
    expected = len(CATALOG_FLAGS)
    wrong = {
        name: claimed
        for name, text in SURFACES.items()
        if (claimed := [n for n in spelled_counts(text) if n != expected])
    }
    assert not wrong, f"flag counts claimed in prose, against {expected} catalogued: {wrong}"


def _other_script_options() -> set[str]:
    """Long options defined by any other script here, so a cross-reference is not a phantom.

    Derived by AST from the tracked tree, never listed by hand.
    """
    import ast

    found: set[str] = set()
    for _path, source in _sources(tracked_paths(["scripts/**/*.py"]), "a script"):
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                        found.add(arg.value)
    return found


# ============================================================
# The sweep reaches something (a rule green over nothing is not a rule)
# ============================================================
def test_the_sweep_reaches_a_real_non_empty_corpus():
    assert len(CORPUS) >= 15, f"only {len(CORPUS)} /scrutinize files swept"
    assert all(text.strip() for text in CORPUS.values()), \
        f"empty corpus members: {[k for k, v in CORPUS.items() if not v.strip()]}"
    tokens = set().union(*(long_flags(text) for text in CORPUS.values()))
    assert len(tokens) >= 20, f"the corpus yielded only {len(tokens)} flag tokens: {sorted(tokens)}"
    assert "docs/skills-operations-quality.html#s-scrutinize" in CORPUS
    assert "--judge-family" not in tokens, "the phantom is back"


def test_every_enumerating_surface_resolved_to_text():
    """A surface that silently extracted to "" passes every rule above."""
    assert len(SURFACES) == 7, sorted(SURFACES)
    empty = [name for name, text in SURFACES.items() if not text.strip()]
    assert not empty, f"surfaces that extracted to nothing: {empty}"
    flagless = [name for name, text in SURFACES.items() if not long_flags(text)]
    assert not flagless, f"surfaces that carried no flag at all, so the equality was vacuous: {flagless}"


def test_the_derived_truth_is_not_empty():
    assert len(CATALOG_FLAGS) >= 5, f"catalog parsed {len(CATALOG_FLAGS)} flags: {CATALOG_FLAGS}"
    assert "--no-code-review" in CATALOG_FLAGS
    assert len(DISPATCHER_OPTIONS) >= 10, sorted(DISPATCHER_OPTIONS)
    # Registered inside a loop; an AST literal scan cannot see these.
    for mode in ("--pass-start", "--judge", "--role-scan", "--currency", "--reproduce", "--promote"):
        assert mode in DISPATCHER_OPTIONS, f"{mode} missing from the captured parser"
    assert DISPATCHER_OPTIONS["--family"] == {"claude", "kimi"}
    assert "--judge-family" not in DISPATCHER_OPTIONS


# ============================================================
# The predicates, on synthetic input, in both directions
# ============================================================
def test_flag_drift_reports_a_documented_flag_that_is_not_implemented():
    assert flag_drift({"--a", "--ghost"}, {"--a"}) == (["--ghost"], [])


def test_flag_drift_reports_an_implemented_flag_that_is_not_documented():
    assert flag_drift({"--a"}, {"--a", "--fresh"}) == ([], ["--fresh"])


def test_flag_drift_is_silent_when_the_two_sides_agree():
    assert flag_drift({"--a", "--b"}, {"--b", "--a"}) == ([], [])
    assert flag_drift(set(), set()) == ([], [])


def test_flag_drift_reports_both_directions_at_once():
    assert flag_drift({"--a", "--ghost"}, {"--a", "--fresh"}) == (["--ghost"], ["--fresh"])


def test_long_flags_reads_the_shapes_the_surfaces_actually_use():
    assert long_flags("[--relentless] [--no-refute]") == {"--relentless", "--no-refute"}
    assert long_flags("<code>--family {claude|kimi}</code>") == {"--family"}
    assert long_flags("`--judge-family={claude\\|kimi}`") == {"--judge-family"}
    assert long_flags("file:&lt;path&gt; --include-ambiguous") == {"--include-ambiguous"}


def test_long_flags_ignores_a_bare_double_dash():
    assert long_flags("a sentence -- with punctuation") == set()
    assert long_flags("`git commit --` then nothing") == set()


def test_phantom_flags_finds_the_token_no_source_accounts_for():
    allowed = {"--relentless", "--family"}
    assert phantom_flags("use --relentless with --family", allowed) == []
    assert phantom_flags("pin it with --judge-family", allowed) == ["--judge-family"]


def test_enumerated_choices_reads_a_written_choice_set():
    assert enumerated_choices("`--family {claude\\|kimi}`", "--family") == [{"claude", "kimi"}]
    assert enumerated_choices("--family={claude|kimi|gemini}", "--family") == [
        {"claude", "kimi", "gemini"}
    ]
    assert enumerated_choices("--pass {2.5a,2.5b}", "--pass") == [{"2.5a", "2.5b"}]


def test_enumerated_choices_is_empty_when_no_set_is_written():
    assert enumerated_choices("pass --family to pin one", "--family") == []
    assert enumerated_choices("--other {a|b}", "--family") == []


def test_spelled_counts_reads_a_claim_and_ignores_an_unrelated_number():
    assert spelled_counts("Six target selectors plus five flags: ...") == [5]
    assert spelled_counts("plus four flags") == [4]
    assert spelled_counts("Six target selectors and nothing else") == []


def test_docs_card_extracts_one_card_and_nothing_when_absent():
    page = (
        '<section class="skill"><h3 id="s-first"><code>/first</code></h3><p>a</p></section>\n'
        '<section class="skill"><h3 id="s-second"><code>/second --x</code></h3><p>b</p></section>'
    )
    card = docs_card(page, "s-second")
    assert "/second" in card and "/first" not in card
    assert docs_card(page, "s-missing") == ""
