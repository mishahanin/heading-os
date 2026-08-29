#!/usr/bin/env python3
"""`docs/EMERGENCY-PROCEDURES.md` documents a Deputy Admin no code implements.

MEASURED 2026-08-29. The page tells an exec, during a CEO outage:

    **Bridge mode:** If the CEO designates a Deputy Admin in advance (via
    `config/admin.json` role field), the deputy can: ...

and, four lines down, "Admin authority flows from `config/admin.json`, not from
group consensus." Nothing reads a `role` field from that file. Parsed across
every tracked `scripts/**/*.py` and `.claude/**/*.py` that calls
`load_admin_config()`, the complete set of keys the code reads off its result is:

    scripts/utils/workspace.py   admin_slugs, github_org
    scripts/aggregate-crm.py     admin_slugs, github_org
    scripts/merge-contacts.py    admin_slugs

`role` is real, but it lives on two other objects: on an exec-registry entry, and
on `.workspace-identity.json`, which is the file `is_admin()` actually reads.
Admin authority therefore needs three separate grants, and only one of them is in
`config/admin.json`:

  1. `role: "admin"` in the gitignored `.workspace-identity.json` at the engine
     root, which `is_admin()` reads;
  2. the identity's `slug` inside the `admin_slugs` array of `config/admin.json`,
     which `validate_admin()` checks second;
  3. GitHub push rights on the corporate repo, which no workspace file grants.

An operator who follows the documented procedure in an emergency edits one file
that grants nothing, and finds out during the outage.

WHY THE PAGE IS STILL WRONG AS THIS FILE LANDS. `docs/EMERGENCY-PROCEDURES.md` is
not authored in this repository. It is mirrored by `scripts/sync-docs.py` from
`templates/EMERGENCY-PROCEDURES.md`, which lives in the operator's private data
overlay, and a PreToolUse guard (`check_protect_docs` in `.claude/hooks/_dispatch.py`)
blocks a direct write to the engine copy because the next sync would silently
revert it. The correction belongs in the template. The live-tree rule below is
therefore marked `xfail(strict=True)`: it fails today, and it turns the suite RED
the moment the template is fixed, which is the signal to delete the marker. A
non-strict marker would let this decay into permanent invisible debt.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

WORKSPACE_PY = ROOT / "scripts" / "utils" / "workspace.py"
LOADER = "load_admin_config"

# A claim is read off a line that names the config file. "field" and "key" are
# the two nouns the prose uses for a JSON member.
_FIELD_CLAIM_RE = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+(?:field|key)\b")
# A negated claim is the correct kind of sentence about a key the code ignores
# ("it carries no role field"), so it must not be reported as the defect.
#
# Case-INSENSITIVE, and that is the whole point. The correction written for this
# defect opens a sentence with "No code reads a `role` field from
# `config/admin.json`", and a case-sensitive negator did not see the capital,
# so the rule reported the sentence that FIXES the defect as the defect. A rule
# that a correct document cannot satisfy gets the document reverted.
_NEGATOR_RE = re.compile(r"\b(?:no|not|never|nothing|without)\b[^.]{0,40}$",
                         re.IGNORECASE)


# ============================================================
# Which keys the code actually reads off load_admin_config()
# ============================================================

def admin_config_keys_read(source: str) -> set[str]:
    """Every string key read off the dict `load_admin_config()` returns.

    Three read shapes are recognised, because all three are in the tree:
    `cfg.get("k")`, `cfg["k"]`, and `"k" in cfg`. The chained
    `load_admin_config().get("k")` form is recognised too, so a caller that
    never binds the result cannot slip a key past this.
    """
    tree = ast.parse(source)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        if isinstance(func, ast.Name) and func.id == LOADER:
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))

    def _str(node) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def _is_config(node) -> bool:
        if isinstance(node, ast.Name):
            return node.id in bound
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == LOADER)

    found: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args and _is_config(node.func.value)):
            key = _str(node.args[0])
            if key:
                found.add(key)
        elif isinstance(node, ast.Subscript) and _is_config(node.value):
            key = _str(node.slice)
            if key:
                found.add(key)
        elif (isinstance(node, ast.Compare) and len(node.ops) == 1
              and isinstance(node.ops[0], ast.In) and _is_config(node.comparators[0])):
            key = _str(node.left)
            if key:
                found.add(key)
    return found


_READS_ALL_THREE_SHAPES = (
    "cfg = load_admin_config()\n"
    "a = cfg.get('admin_slugs')\n"
    "b = cfg['github_org']\n"
    "c = 'admin_slugs' in cfg\n"
)
_READS_CHAINED = "d = load_admin_config().get('github_org')\n"
_READS_A_DIFFERENT_DICT = (
    "other = load_exec_registry()\n"
    "e = other.get('role')\n"
    "f = identity['role']\n"
)


def test_the_key_reader_finds_every_read_shape_in_the_tree():
    assert admin_config_keys_read(_READS_ALL_THREE_SHAPES) == {"admin_slugs", "github_org"}


def test_the_key_reader_follows_an_unbound_call():
    assert admin_config_keys_read(_READS_CHAINED) == {"github_org"}


def test_the_key_reader_ignores_keys_read_off_another_object():
    """The other direction, and the one that matters here: `role` IS read in this
    engine, off the exec registry and off the workspace identity. A reader that
    swept every `.get("role")` in the file would report the documented claim as
    true and leave the defect in place."""
    assert admin_config_keys_read(_READS_A_DIFFERENT_DICT) == set()


# ============================================================
# The live tree: two keys, and role is not one of them
# ============================================================

def _admin_config_readers() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in tracked_paths(("scripts/**/*.py", ".claude/**/*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - not a python source file
            continue
        if LOADER not in source:
            continue
        try:
            keys = admin_config_keys_read(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        if keys:
            out[path.relative_to(ROOT).as_posix()] = keys
    return out


def live_admin_config_keys() -> set[str]:
    return set().union(*_admin_config_readers().values())


def test_admin_json_supplies_exactly_two_keys_to_the_code():
    assert live_admin_config_keys() == {"admin_slugs", "github_org"}, _admin_config_readers()


def test_no_code_reads_a_role_field_from_admin_json():
    """The claim the emergency procedure rests on. Stated separately from the set
    equality above so a failure names the defect rather than a diff."""
    assert "role" not in live_admin_config_keys(), _admin_config_readers()


def test_is_admin_reads_role_from_the_workspace_identity_file():
    """What DOES grant admin authority, pinned so the corrected procedure stays
    true. `is_admin()` asks `get_workspace_identity()` for `role`, and that
    resolver reads `.workspace-identity.json`."""
    tree = ast.parse(WORKSPACE_PY.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "is_admin" in funcs and "get_workspace_identity" in funcs

    reads_role = [
        n for n in ast.walk(funcs["is_admin"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get" and n.args
        and isinstance(n.args[0], ast.Constant) and n.args[0].value == "role"
        and isinstance(n.func.value, ast.Call)
        and isinstance(n.func.value.func, ast.Name)
        and n.func.value.func.id == "get_workspace_identity"
    ]
    assert reads_role, "is_admin() no longer reads role off get_workspace_identity()"

    identity_src = ast.get_source_segment(
        WORKSPACE_PY.read_text(encoding="utf-8"), funcs["get_workspace_identity"])
    assert ".workspace-identity.json" in identity_src


def test_validate_admin_also_checks_the_admin_slugs_allow_list():
    """Grant 2 of the three. `is_admin()` alone is not enough."""
    source = WORKSPACE_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    body = ast.get_source_segment(source, funcs["validate_admin"])
    assert "is_admin()" in body
    assert "get_admin_slugs()" in body


# ============================================================
# The documentation predicate, measured on synthetic input both ways
# ============================================================

def documents_a_key_the_code_ignores(corpus, known_keys) -> list[str]:
    """(path:line) for every affirmative claim that admin.json carries a key the
    code does not read.

    Pure, so the rule can be exercised on synthetic prose. Over a corrected tree
    it returns nothing, and deleting the line that appends a finding would change
    no live result.
    """
    out = []
    for rel, text in corpus:
        for number, line in enumerate(text.splitlines(), start=1):
            if "admin.json" not in line:
                continue
            for match in _FIELD_CLAIM_RE.finditer(line):
                key = match.group(1)
                if key in known_keys:
                    continue
                if _NEGATOR_RE.search(line[:match.start()]):
                    continue
                out.append(f"{rel}:{number}  claims a {key!r} field")
    return out


_KNOWN = {"admin_slugs", "github_org"}
_SYNTHETIC_DEFECT = [(
    "docs/X.md",
    "If the CEO designates a Deputy Admin in advance (via `config/admin.json` "
    "role field), the deputy can:\n",
)]
_SYNTHETIC_CORRECT = [(
    "docs/X.md",
    "`config/admin.json` supplies two keys the code reads. It carries no role "
    "field, no deputy, and no expiry.\n",
)]
_SYNTHETIC_KNOWN_KEY = [(
    "docs/X.md",
    "Add the slug to the `admin_slugs` field of `config/admin.json`.\n",
)]
_SYNTHETIC_ELSEWHERE = [(
    "docs/X.md",
    "The `role` field of `.workspace-identity.json` decides admin authority.\n",
)]


def test_the_doc_rule_fires_on_the_measured_sentence():
    found = documents_a_key_the_code_ignores(_SYNTHETIC_DEFECT, _KNOWN)
    assert found == ["docs/X.md:1  claims a 'role' field"], found


def test_the_doc_rule_accepts_the_sentence_that_replaces_it():
    """A rule that fired on "carries no role field" would forbid the only honest
    way to say what the file does not do."""
    assert documents_a_key_the_code_ignores(_SYNTHETIC_CORRECT, _KNOWN) == []


def test_the_doc_rule_accepts_a_negation_that_opens_a_sentence():
    """Measured 2026-08-29: the corrected page opens with "No code reads a `role`
    field from `config/admin.json`", and a case-sensitive negator reported that
    sentence as the defect it was written to remove. A rule a correct document
    cannot satisfy gets the document reverted, so the capital is a real case."""
    capitalised = [("docs/X.md",
                    "2026-08-29. No code reads a `role` field from "
                    "`config/admin.json`, so an exec got nothing.\n")]
    assert documents_a_key_the_code_ignores(capitalised, _KNOWN) == []


def test_the_doc_rule_still_fires_when_the_negation_belongs_to_another_sentence():
    """The mirror. The negator window stops at a full stop, so a negation that
    ended before the claim began must not excuse it."""
    split = [("docs/X.md",
              "There is no problem here. The `role` field of `config/admin.json` "
              "promotes a deputy.\n")]
    assert documents_a_key_the_code_ignores(split, _KNOWN) == [
        "docs/X.md:1  claims a 'role' field"]


def test_the_doc_rule_leaves_a_real_key_alone():
    assert documents_a_key_the_code_ignores(_SYNTHETIC_KNOWN_KEY, _KNOWN) == []


def test_the_doc_rule_ignores_a_role_field_on_another_file():
    """`role` on `.workspace-identity.json` is the true mechanism, and the
    corrected page must be free to say so."""
    assert documents_a_key_the_code_ignores(_SYNTHETIC_ELSEWHERE, _KNOWN) == []


# ============================================================
# The live documentation
# ============================================================

def _doc_corpus() -> list[tuple[str, str]]:
    out = []
    for path in tracked_paths(("docs/**/*.md", "*.md", "reference/**/*.md",
                               ".claude/**/*.md")):
        try:
            out.append((path.relative_to(ROOT).as_posix(),
                        path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:  # pragma: no cover - not text
            continue
    return out


def test_no_engine_document_claims_admin_json_carries_a_key_the_code_ignores():
    """This carried a strict xfail until the corrected page landed on 2026-08-29.

    The page is mirrored by `sync-docs.py` from `templates/EMERGENCY-PROCEDURES.md`
    in the private data overlay, so the correction had to land there first. It
    did, and the mirror followed.
    """
    found = documents_a_key_the_code_ignores(_doc_corpus(), live_admin_config_keys())
    assert found == [], found


def test_the_corrected_page_still_names_the_key_the_code_does_not_read():
    """The correction is a NEGATED sentence about `role`, which is exactly the
    shape the rule must accept. If it stopped accepting one, the page above would
    go green by losing its explanation rather than by being right."""
    corrected = dict(_doc_corpus())["docs/EMERGENCY-PROCEDURES.md"]
    assert "`role` field" in corrected
    assert "config/admin.json" in corrected


# ============================================================
# The sweep reaches a real corpus
# ============================================================

def test_the_sweep_reaches_a_real_corpus():
    """Green over an empty corpus otherwise: no readers means an empty key set,
    and no documents means no findings. 3 readers and 200+ pages on 2026-08-29."""
    readers = _admin_config_readers()
    assert len(readers) >= 3, readers
    assert "scripts/utils/workspace.py" in readers, sorted(readers)

    corpus = _doc_corpus()
    assert len(corpus) > 100, f"only {len(corpus)} documents read"
    assert any(rel == "docs/EMERGENCY-PROCEDURES.md" for rel, _ in corpus)
    assert any("admin.json" in text for _rel, text in corpus)
