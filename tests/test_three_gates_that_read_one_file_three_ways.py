"""Three gates that read one file three ways.

Shard 52 of the engine audit. Markdown frontmatter is split by finding a fence.
Three tools looked for the CHARACTERS `---` instead of the LINE, and the fix for
that landed in some copies and not others.

MEASURED 2026-08-28 on `description: drift --- check` inside an otherwise
ordinary SKILL.md:

    generate-skill-router.py    read the whole mapping
    skill-metadata-check.py     dropped every key after the dashes
    artifact-evaluator.py       cut the block at the dashes
    utils.markdown, marp_render, inbox_pulse.rules, and four more   read it whole

The two gates read the SAME corpus, every `.claude/skills/*/SKILL.md`, so they
disagreed about the same file. Measured end to end through `check_skill`, one
` --- ` in a description turned a WARN into a FAIL, named `metadata.author`,
`metadata.version` and `x-heading-orchestration` as missing while all three sat
in the file, and flipped that skill's `triggers_status` from MISSING to EXEMPT,
so the coverage gate stopped asking for a corpus it requires. The router
generator, reading the same bytes for the same purpose, was untroubled.

Two smaller measured things came with it. The "good" copy computed the block as
`text[4:...]`, assuming an opening fence of exactly four characters, so a fence
written `---\\t\\t` left a tab at the start of the block and PyYAML refused a file
whose YAML was fine. And `artifact-evaluator.py` spelled its copy
`parse_yaml_frontmatter`, a name the anti-duplication detector in
tests/test_markdown_frontmatter_single_source.py had never been told about, so
that copy was invisible to the sweep built to find exactly this.

The fix is one splitter, `scripts.utils.markdown.split_frontmatter`, plus
`parse_frontmatter_strict` on top of it: the shared module owns the
CLASSIFICATION, each caller keeps its own WORDING, because the wording is that
caller's user-facing output and the three word it differently.

Nothing here reaches the network or writes outside tmp_path.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import markdown as md  # noqa: E402
from scripts.utils.markdown import (  # noqa: E402
    parse_frontmatter_strict,
    split_frontmatter,
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gates():
    return (_load("smc_s52", "scripts/skill-metadata-check.py"),
            _load("gsr_s52", "scripts/generate-skill-router.py"),
            _load("ae_s52", "scripts/artifact-evaluator.py"))


# ============================================================
# split_frontmatter - finding the fence
# ============================================================

DASHES_IN_SCALAR = ("---\nname: a\ndescription: drift --- check\nrouter: auto\n"
                    "last: yes\n---\nbody\n")


def test_dashes_inside_a_scalar_do_not_end_the_block():
    """THE defect. `text.split("---", 2)` and `re.match(r"---(.*?)---")` both cut
    here, and every key after the dashes vanished from the mapping."""
    block, body, kind = split_frontmatter(DASHES_IN_SCALAR)

    assert kind == md.FM_OK
    assert "router: auto" in block
    assert "last: yes" in block
    assert body == "body\n"


def test_dashes_inside_a_folded_scalar_do_not_end_the_block():
    text = ("---\nname: a\ndescription: >\n  drift --- check\nrouter: auto\n"
            "---\nbody\n")

    block, _body, kind = split_frontmatter(text)

    assert kind == md.FM_OK
    assert "router: auto" in block


@pytest.mark.parametrize("fence", ["---", "--- ", "---\t", "---\t\t", "---   \t "])
def test_the_offset_is_read_from_the_first_line_not_assumed(fence):
    """`text[4:]` assumed the opening fence is four characters.

    MEASURED on `---\\t\\t`: the slice started mid-line, left a tab at the head of
    the block, and PyYAML answered "found character '\\t' that cannot start any
    token" for a file whose YAML was perfectly good.
    """
    block, _body, kind = split_frontmatter(f"{fence}\nname: a\n---\nbody\n")

    assert kind == md.FM_OK
    assert block == "name: a\n"


def test_a_crlf_checkout_is_read_the_same_way():
    """A verdict that depends on core.autocrlf is a verdict about the checkout,
    not about the file."""
    block, _body, kind = split_frontmatter("---\r\nname: a\r\n---\r\nbody\r\n")

    assert kind == md.FM_OK
    assert "name: a" in block


def test_the_block_keeps_the_newline_before_the_closing_fence():
    """This is the one measured difference from `parse_frontmatter`, whose regex
    puts that newline outside its capture group. It shows on 2 of the 94
    SKILL.md files (canopus, census), where the last `x-heading-capability`
    folded scalar loses its trailing newline, and it is why the two CI gates
    would not migrate to the plain parser."""
    block, _body, _kind = split_frontmatter("---\nname: a\n---\nbody\n")
    plain, _ = md.parse_frontmatter("---\nname: a\n---\nbody\n")

    assert block.endswith("\n")
    assert plain == {"name": "a"}, "the plain parser still parses it, just differently"


@pytest.mark.parametrize("text, kind", [
    ("", md.FM_NO_OPENING),
    ("body\n", md.FM_NO_OPENING),
    ("---", md.FM_NO_OPENING),               # a fence with nothing after it
    ("----\nname: a\n---\n", md.FM_NO_OPENING),   # four dashes is not a fence
    ("  ---\nname: a\n---\n", md.FM_NO_OPENING),  # nor is an indented one
    ("---\nname: a\n", md.FM_NO_CLOSING),
    ("---\nname: a\n----\n", md.FM_NO_CLOSING),
])
def test_a_document_with_no_usable_block_says_which_fence_was_missing(text, kind):
    block, body, got = split_frontmatter(text)

    assert got == kind
    assert block is None
    assert body == text, "the whole document comes back untouched"


def test_a_horizontal_rule_in_the_body_is_not_a_second_fence():
    """The closing fence is the FIRST one after the opening, so an `---` rule
    further down the body cannot move it."""
    block, body, kind = split_frontmatter("---\nname: a\n---\nbody\n\n---\n\nmore\n")

    assert kind == md.FM_OK
    assert block == "name: a\n"
    assert body == "body\n\n---\n\nmore\n"


# ============================================================
# parse_frontmatter_strict - keeping the reason
# ============================================================

@pytest.mark.parametrize("text, kind, detail", [
    ("---\nname: a\n---\nbody\n", md.FM_OK, ""),
    ("---\n---\nbody\n", md.FM_EMPTY, ""),
    ("---\n# only a comment\n---\nbody\n", md.FM_EMPTY, ""),
    ("---\n- one\n- two\n---\nbody\n", md.FM_NOT_MAPPING, "list"),
    ("---\njust a string\n---\nbody\n", md.FM_NOT_MAPPING, "str"),
    ("body\n", md.FM_NO_OPENING, ""),
    ("---\nname: a\n", md.FM_NO_CLOSING, ""),
])
def test_every_failure_keeps_its_reason(text, kind, detail):
    """`parse_frontmatter` collapses all of these into ({}, text). That
    collapse is the whole reason three callers kept private copies."""
    data, got_kind, got_detail = parse_frontmatter_strict(text)

    assert got_kind == kind
    assert got_detail == detail
    assert (data is None) is (kind != md.FM_OK)


def test_broken_yaml_is_reported_with_the_parser_message():
    data, kind, detail = parse_frontmatter_strict("---\na: [1, 2\n---\nbody\n")

    assert data is None
    assert kind == md.FM_INVALID_YAML
    assert detail, "the PyYAML message is the detail; an empty one says nothing"


# ============================================================
# The three callers, now reading one document one way
# ============================================================

DOCS = {
    "plain": "---\nname: a\nrouter: auto\nlast: yes\n---\nbody\n",
    "dashes-in-scalar": DASHES_IN_SCALAR,
    "dashes-in-folded": ("---\nname: a\ndescription: >\n  drift --- check\n"
                         "router: auto\nlast: yes\n---\nbody\n"),
    "crlf": "---\r\nname: a\r\nrouter: auto\r\nlast: yes\r\n---\r\nbody\r\n",
    "tab-fence": "---\t\t\nname: a\nrouter: auto\nlast: yes\n---\nbody\n",
    "rule-in-body": "---\nname: a\nrouter: auto\nlast: yes\n---\nb\n\n---\n\nc\n",
}


@pytest.mark.parametrize("label", sorted(DOCS))
def test_all_three_gates_read_the_same_keys(gates, tmp_path, label):
    """One document, three readers, one answer.

    Written as one table fed to all three on purpose: a per-gate test would pass
    while they drifted apart again, which is exactly what happened between
    2026-08-20 and this shard.
    """
    smc, gsr, ae = gates
    f = tmp_path / f"{label}.md"
    f.write_text(DOCS[label], encoding="utf-8")

    from_audit, _ = smc.parse_frontmatter(f)
    from_router, _ = gsr.parse_frontmatter(f)
    from_eval, _ = ae.parse_yaml_frontmatter(DOCS[label])

    expected = {"name", "router", "last"}
    assert expected <= set(from_audit), from_audit
    assert set(from_audit) == set(from_router) == set(from_eval or {})


def test_the_live_skill_corpus_reads_identically_through_both_gates(gates):
    """An equality invariant on real data, NOT the defect test.

    It passed before this change too: MEASURED 2026-08-28, the two gates already
    agreed on all 94 committed SKILL.md, because none of them happens to carry
    ` --- ` inside a scalar today. The defect was latent, and this test says
    only that it stays latent on the corpus as it stands.
    """
    smc, gsr, _ae = gates
    skills = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))

    assert len(skills) >= 50, f"only {len(skills)} SKILL.md found; the glob is wrong"
    differ = [f.parent.name for f in skills
              if smc.parse_frontmatter(f) != gsr.parse_frontmatter(f)]
    assert differ == [], differ


def test_the_audit_no_longer_fails_a_file_over_fields_that_are_present(gates,
                                                                       monkeypatch):
    """The measured end-to-end consequence, both halves of it.

    Before: status FAIL, three required fields reported missing while every one
    of them sat in the file, and `triggers_status` EXEMPT so the coverage gate
    stopped asking for a triggers.json.
    """
    smc, _gsr, _ae = gates
    skill = """---
name: driftcheck
description: {desc}
allowed-tools: "Read"
metadata:
  author: A B
  email: a@example.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: ["drift"]
x-heading-routing:
  category: Operations
  triggers: ["drift"]
  exclusions: ["N/A"]
  compound: "No"
  router: auto
---

# Body
"""
    results = {}
    for label, desc in [("clean", "handles drift and state check"),
                        ("dashes", "handles drift --- state check")]:
        root = Path(tempfile.mkdtemp())
        monkeypatch.setenv("WORKSPACE_ROOT", str(root))
        d = root / "driftcheck"
        d.mkdir()
        (d / "SKILL.md").write_text(skill.format(desc=desc), encoding="utf-8")
        results[label] = smc.check_skill(d, frozenset())

    dashes = results["dashes"]
    assert dashes["missing_required"] == [], dashes["missing_required"]
    assert dashes["is_auto_routable"] is True
    assert dashes["triggers_status"] == "MISSING", "EXEMPT would silence the gate"
    assert dashes["status"] == results["clean"]["status"]


# ============================================================
# Each caller keeps its own words
# ============================================================

@pytest.mark.parametrize("text, expected", [
    ("body\n", "no frontmatter (missing opening ---)"),
    ("---\nname: a\n", "malformed frontmatter (missing closing ---)"),
    ("---\n---\nb\n", "empty frontmatter"),
    ("---\n- one\n---\nb\n", "frontmatter must be a mapping, got list"),
])
def test_the_audit_wording_is_unchanged(gates, tmp_path, text, expected):
    smc, _gsr, _ae = gates
    f = tmp_path / "s.md"
    f.write_text(text, encoding="utf-8")

    assert smc.parse_frontmatter(f)[1] == expected


def test_the_audit_still_says_yaml_parse_error(gates, tmp_path):
    smc, _gsr, _ae = gates
    f = tmp_path / "s.md"
    f.write_text("---\na: [1, 2\n---\nb\n", encoding="utf-8")

    assert smc.parse_frontmatter(f)[1].startswith("YAML parse error: ")


def test_the_router_still_says_invalid_yaml_frontmatter(gates, tmp_path):
    """The two gates word this differently and both wordings are preserved: the
    shared module classifies, the caller phrases."""
    _smc, gsr, _ae = gates
    f = tmp_path / "s.md"
    f.write_text("---\na: [1, 2\n---\nb\n", encoding="utf-8")

    assert gsr.parse_frontmatter(f)[1].startswith("invalid YAML frontmatter: ")


@pytest.mark.parametrize("text, expected", [
    ("# just a heading\n", "No YAML frontmatter found"),
    ("---\nname: a\n", "Invalid frontmatter format"),
    ("---\n- one\n---\nb\n", "Frontmatter must be a YAML dictionary"),
])
def test_the_evaluator_wording_is_unchanged(gates, text, expected):
    _smc, _gsr, ae = gates

    assert ae.parse_yaml_frontmatter(text)[1] == expected


# ============================================================
# The registry that is supposed to catch this
# ============================================================

def test_the_three_migrated_files_left_the_survivor_list():
    registry = _load("reg_s52", "tests/test_markdown_frontmatter_single_source.py")

    for path in ("scripts/generate-skill-router.py",
                 "scripts/skill-metadata-check.py",
                 "scripts/artifact-evaluator.py"):
        assert path not in registry.ALLOWED_SURVIVORS, path
    offenders, _total = registry._own_parsers()
    assert "scripts/artifact-evaluator.py" not in offenders


def test_the_detector_now_knows_the_spelling_it_was_blind_to():
    """`artifact-evaluator.py` called its copy `parse_yaml_frontmatter`, which
    was not in PARSER_NAMES, so the sweep built to find duplicate parsers never
    saw the one carrying the defect. A name-keyed detector only sees the
    spellings it was told about; this closes one and does not prove there are no
    others."""
    registry = _load("reg_names_s52", "tests/test_markdown_frontmatter_single_source.py")

    assert "parse_yaml_frontmatter" in registry.PARSER_NAMES
    tree = ast.parse((ROOT / "scripts" / "artifact-evaluator.py").read_text(
        encoding="utf-8"))
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "parse_yaml_frontmatter" in names, "the spelling this test guards moved"
