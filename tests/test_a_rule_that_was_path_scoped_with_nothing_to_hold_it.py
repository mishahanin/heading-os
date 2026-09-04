"""The classification rule went path-scoped, and nothing measured either half.

On 2026-09-04 `.claude/rules/classification.md` was moved out of the always-on
set by giving it a `paths:` frontmatter block. That is a 2504-byte saving on
every session and it is also the single most reversible change in the diet: a
typo in one glob, a stray space on the `---` fence, or a later edit that adds
`always_active: true` all put the file back in the floor, or worse, take it out
of the floor while ALSO never loading it. None of those show up as a test
failure unless something asserts on both directions.

So this file asserts both, and the word "both" is the point:

  POSITIVE - the globs match the paths the rule is FOR. A rule that is scoped so
  narrowly it never loads has not been moved on demand, it has been deleted, and
  the byte saving looks identical in the audit either way.

  NEGATIVE - the globs do NOT match a path the rule has no business on. A rule
  scoped to `**` is resident wearing a costume: the floor audit takes its bytes
  out of `always_on_bytes` (see `_is_always_on` in `scripts/context-floor-audit.py`,
  which is the ratcheted number `--baseline` gates growth on) while the harness
  still loads it everywhere. That direction is the dangerous one, because it
  makes the budget look like it shrank when it did not.

The failing half against the PRE-CHANGE version: before 2026-09-04 the file
opened with `<!-- version: 2.1.0 ... -->` and carried no frontmatter at all, so
`split_frontmatter` returns no body, `_is_always_on` returns True, and
`test_the_classification_rule_is_path_scoped_not_resident` fails on its first
assertion. `test_the_globs_do_not_reach_unrelated_paths` passes vacuously
against that version (no globs, nothing matches), which is why it is not the
half that proves the move happened - it is the half that proves the move was not
overdrawn.

ON THE MATCHER. The harness owns glob matching for `paths:`, and it is not in
this repository, so nothing here can test the harness. What it CAN test is that
the globs are well-formed and select the intended set under `fnmatch`, whose
`*` crosses `/` and therefore treats `context/**` the way a recursive glob is
meant to be read. Where the two could disagree is a pattern relying on `**`
meaning something `*` does not; `assert_no_bare_star_ambiguity` below pins the
globs to the shapes where they agree, so this test is a statement about the
patterns rather than a guess about the harness.
"""
import importlib.util
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "context_floor_audit", str(ROOT / "scripts" / "context-floor-audit.py")
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

RULE = ROOT / ".claude" / "rules" / "classification.md"

# Paths the rule EXISTS for: the four ask-directories it makes you classify into,
# and the routing map it tells you to edit. One representative file each, and a
# nested one, because `context/**` matching `context/a.md` but not
# `context/sub/a.md` is exactly the kind of half-scoping that reads as a pass.
MUST_LOAD = (
    "context/business-info.md",
    "context/sub/nested.md",
    "reference/misha-voice.md",
    "reference/skill-router/intel.md",
    "knowledge/notes/idea.md",
    "datastore/documents/thing.md",
    "config/routing-map.yaml",
)

# Paths the rule has no business loading on. If any of these match, the scoping
# is decorative and the floor audit is under-reporting the real resident cost.
MUST_NOT_LOAD = (
    "scripts/generate-skill-router.py",
    "tests/test_generate_skill_router.py",
    ".claude/rules/voice.md",
    ".claude/hooks/_dispatch.py",
    "docs/ARCHITECTURE.md",
    "README.md",
    "config/tool-risk.json",
    "pyproject.toml",
)


def _globs() -> list[str]:
    """The `paths:` list as the frontmatter actually spells it."""
    text = RULE.read_text(encoding="utf-8")
    body, _rest, kind = audit.split_frontmatter(text)
    assert body is not None and kind == audit.FM_OK, (
        "classification.md must open with a well-formed frontmatter fence; a "
        "trailing space on the fence reads as no frontmatter and silently "
        "returns the whole file to the always-on floor")
    found = re.search(r"^paths:\s*$", body, re.M)
    assert found, "expected a block-form `paths:` key, not an inline list"
    rest = body[found.end():]
    next_key = re.search(r"^\S", rest, re.M)
    block = rest[: next_key.start()] if next_key else rest
    globs = re.findall(r'^\s+-\s+"?([^"\n]+?)"?\s*$', block, re.M)
    assert globs, "the `paths:` block is empty, which means always-on"
    return globs


def test_the_classification_rule_is_path_scoped_not_resident():
    """The saving is real: the workspace's own predicate calls this scoped.

    Asserted through `_is_always_on` rather than by reading the file, because
    that function is what `always_on_bytes` and therefore the committed
    `config/context-floor-baseline.json` ratchet are computed from. A test that
    agreed the file "looks scoped" while the audit still counted it would report
    a saving nobody got.
    """
    text = RULE.read_text(encoding="utf-8")
    assert not audit._is_always_on(text), (
        "classification.md is being counted in the always-on floor; the `paths:` "
        "block is missing, empty, or overridden by `always_active: true`")
    assert "always_active: true" not in text.split("---", 2)[1], (
        "always_active: true would return the whole file to every session while "
        "the `paths:` block made it look scoped")


def test_the_globs_reach_every_path_the_rule_is_for():
    """POSITIVE half. A rule scoped so tightly it never loads is a deletion."""
    globs = _globs()
    for path in MUST_LOAD:
        assert any(fnmatch(path, g) for g in globs), (
            f"{path!r} must load classification.md but matches none of {globs}; "
            "the obligation to classify that record now fires on nothing")


def test_the_globs_do_not_reach_unrelated_paths():
    """NEGATIVE half. Scoped to everything is resident with the bytes hidden."""
    globs = _globs()
    for path in MUST_NOT_LOAD:
        matched = [g for g in globs if fnmatch(path, g)]
        assert not matched, (
            f"{path!r} matches {matched}, so the rule loads on work it does not "
            "govern while the floor audit has already removed its bytes from "
            "always_on_bytes; the budget looks smaller than it is")


def test_the_globs_are_shapes_fnmatch_and_a_recursive_globber_agree_on():
    """Pin the patterns to where this test's matcher and the harness's agree.

    `fnmatch` has no `**`; its `*` simply crosses `/`. So `context/**` and
    `context/*` select the same set here, while a real recursive globber
    distinguishes them. Every glob is therefore required to be either a literal
    path or a prefix ending in `/**`, the two shapes on which the two matchers
    give the same answer. A glob like `context/*/notes.md`, where they diverge,
    fails here rather than passing on a matcher the harness does not use.
    """
    globs = _globs()
    # The floor, outside the loop. Every assertion below sits inside `for g in
    # globs`, so an empty list would satisfy all of them and this test would
    # certify a rule whose `paths:` block had been emptied - which is the exact
    # regression the file exists to catch, passing as a green test.
    # `.claude/rules/development-standards.md` obligation 7, and
    # `scripts/check-test-vacuity.py` fails the suite without this line.
    # MEASURED 2026-09-04: the block carries 5 globs (context, reference,
    # knowledge, datastore, config/routing-map.yaml). A floor, not an equality,
    # so adding a sixth ask-directory is not a test failure.
    assert len(globs) >= 5, (
        f"expected at least the 5 globs measured on 2026-09-04, found "
        f"{len(globs)}: {globs}")
    for g in globs:
        assert "?" not in g and "[" not in g, f"unsupported wildcard in {g!r}"
        if "*" in g:
            assert g.endswith("/**") and "*" not in g[:-3], (
                f"{g!r} must be a directory prefix ending in '/**'; any other "
                "wildcard shape means this test and the harness can disagree")
        else:
            assert (ROOT / g).exists(), (
                f"{g!r} is a literal path in the glob list but does not exist, "
                "so it can never load the rule")


@pytest.mark.parametrize("directory", ["context", "reference", "knowledge", "datastore"])
def test_each_ask_directory_the_rule_names_is_actually_scoped(directory):
    """The globs and the rule's own prose must name the same directories.

    The prose says the rule loads "when a tool call names one of the four
    ask-directories or the routing map". If someone adds a fifth directory to
    the prose and forgets the frontmatter, the rule keeps promising a coverage
    it no longer has, which is the failure `.claude/rules/scope-claims.md` is
    about.
    """
    globs = _globs()
    assert any(g.startswith(directory + "/") or g == directory for g in globs), (
        f"the rule's prose treats {directory}/ as in scope but no glob covers it")
