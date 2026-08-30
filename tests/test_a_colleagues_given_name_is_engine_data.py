"""A colleague's given name is real data, and the gate must see it.

Found 2026-08-24 by running the content gate's own deep-audit mode by hand.
Two real executives' given names were sitting in ENGINE-routed, tracked files:

* ``tests/test_sentinel_telegram_cursor.py`` -- a fixture default argument.
  Committed in ``2031f92`` and ALREADY PUSHED to the public engine repo.
* ``scripts/utils/workspace.py`` -- two given names in a docstring recounting a
  measurement. Committed in ``22e6997``, not pushed.

``content-guard --all`` reported the surface CLEAN both times.

Why it missed them. ``_harvest_executives`` emitted each exec's slug, full name,
github user and data-repo name into the default denylist, and put the BARE
given/family names behind ``--strict``. A file carrying the given name alone,
without the full name beside it, therefore matched nothing. (This paragraph
cannot show the example: the promoted gate now refuses it, which is the point.)
``--strict`` is documented "noisy;
deep-audit only" and no gate runs it -- correctly, because the same flag also
decomposes 300+ CRM contact slugs into ordinary English. Measured 2026-08-24: a
strict sweep prints 1,052 findings and 967 of them are the single word
``security``, which is a generic industry word inside real organisation names.

So the exec roster inherited an opt-in it never earned. It is not that source:
it is a curated handful of real colleagues, and promoting it costs SIX tokens
against the 263 that all of ``strict`` adds.

The rule this encodes: precision is a property of the SOURCE, not of the flag it
happens to share. A low-cardinality curated roster of real people belongs in the
gate that runs; only the source that generates ordinary English stays opt-in.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import build_denylist  # noqa: E402


@pytest.fixture
def overlay(tmp_path):
    """A DATA overlay holding one executive, and nothing else."""
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "executives.json").write_text(json.dumps({
        "executives": [
            {"slug": "james-bond", "name": "James Bond",
             "github_user": "agent007", "data_repo": "heading-os-data-james-bond",
             "status": "active"},
        ]
    }), encoding="utf-8")
    return tmp_path


# --- the bare given name is a token without any flag ------------------------

def test_the_full_name_was_always_covered(overlay):
    """Anchor: the half that already worked."""
    dl = build_denylist(overlay, strict=False)
    assert "james bond" in dl.tokens


def test_the_bare_given_name_is_covered_by_default(overlay):
    dl = build_denylist(overlay, strict=False)
    assert "james" in dl.tokens, (
        "a colleague's given name is only a denylist token under --strict, which "
        "no gate runs; that is how two of them reached engine-routed files"
    )
    assert dl.tokens["james"] == "exec-name"


def test_the_bare_family_name_is_covered_by_default(overlay):
    dl = build_denylist(overlay, strict=False)
    assert "bond" not in dl.tokens or dl.tokens["bond"] == "exec-name"
    # 'bond' is 4 chars, under _MIN_WORD, so it is filtered by length, not by
    # the flag. Prove the mechanism on a name long enough to survive that gate.
    (overlay / "admin" / "executives.json").write_text(json.dumps({
        "executives": [{"slug": "james-moneypenny", "name": "James Moneypenny",
                        "status": "active"}]
    }), encoding="utf-8")
    dl = build_denylist(overlay, strict=False)
    assert dl.tokens.get("moneypenny") == "exec-name"


def test_a_default_scan_finds_the_bare_name_in_engine_text(overlay):
    dl = build_denylist(overlay, strict=False)
    hits = dl.scan_text('def _user(first_name: str = "James"):\n')
    assert [h[2] for h in hits] == ["exec-name"], hits


def test_the_existing_filters_still_apply_to_exec_names(overlay):
    """Promotion must not smuggle past the length, alpha and stopword gates."""
    (overlay / "admin" / "executives.json").write_text(json.dumps({
        "executives": [{"slug": "world-price", "name": "World Price",
                        "status": "active"}]
    }), encoding="utf-8")
    dl = build_denylist(overlay, strict=False)
    assert "world" not in dl.tokens, "a STOPWORD leaked in through the exec harvest"
    assert "price" not in dl.tokens
    assert "world price" in dl.tokens, "the phrase form must survive"


def test_public_identity_is_still_never_flagged(overlay):
    """The operator's own name is deliberately public; it must not become a leak."""
    (overlay / "admin" / "executives.json").write_text(json.dumps({
        "executives": [{"slug": "misha-hanin", "name": "Misha Hanin",
                        "status": "active"}]
    }), encoding="utf-8")
    dl = build_denylist(overlay, strict=False)
    assert "misha" not in dl.tokens and "hanin" not in dl.tokens


# --- the noisy source stays opt-in ------------------------------------------

def test_crm_slug_decomposition_is_still_behind_strict(overlay):
    """The measured reason the flag exists. Promoting THIS source would print
    967 findings on the word `security` alone."""
    (overlay / "crm" / "contacts").mkdir(parents=True)
    (overlay / "crm" / "contacts" / "rupert-security.md").write_text(
        "---\ncompany: Placeholder\n---\n# Rupert\n", encoding="utf-8")
    default = build_denylist(overlay, strict=False)
    strict = build_denylist(overlay, strict=True)
    assert "rupert" not in default.tokens
    assert "rupert" in strict.tokens


# --- the engine surface is clean, and provably so ----------------------------

def _run_gate(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "content-guard.py"), "--all", *extra],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)


@pytest.mark.skipif(
    not (ROOT.parent / ".heading-os-data" / "admin" / "executives.json").is_file(),
    reason="no DATA overlay on this machine; the gate no-ops and proves nothing")
def test_the_whole_engine_surface_passes_the_default_gate():
    """The end-to-end statement: with exec given names live, nothing in the
    engine tree names a colleague."""
    p = _run_gate()
    assert p.returncode == 0, p.stdout[-3000:]


# --- no engine file names a real colleague, checked structurally -------------

_EXEC_FIXTURE_NAMES = {"james", "bond", "moneypenny", "agent007"}

# A whole string literal that reads as a person's name: one to three
# capitalised words and nothing else. "Alice Smith" matches; "Anchor: the half
# that already worked." does not, and neither does a path or a JSON key.
_NAME_SHAPED = re.compile(r"^[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){0,2}$")


def _string_constants(src: str) -> list[str]:
    """Every string constant in `src` except docstrings.

    Docstrings are excluded because they are prose about the fixtures, not
    fixture data, and scanning them is what makes a widened person-name check
    flag ordinary sentence-initial words.
    """
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def _name_shaped_literals(src: str) -> set[str]:
    """The capitalised words of every name-shaped string constant in `src`."""
    return {word
            for text in _string_constants(src)
            if _NAME_SHAPED.match(text.strip())
            for word in text.strip().split()}


def test_the_name_extractor_can_actually_fail():
    """Drive `_name_shaped_literals` with input that must and must not trip it.

    The live check below runs over a corpus that is correct today, so on its
    own it would pass identically if the extractor returned the empty set.
    """
    # Bond scaffolding throughout, because this file's own guard vets every
    # person-shaped literal it contains, this one included.
    assert _name_shaped_literals('x = "James Bond"') == {"James", "Bond"}
    assert _name_shaped_literals(
        'x = {"name": "Rupert Moneypenny"}') == {"Rupert", "Moneypenny"}
    # Prose, paths and identifiers are not person-shaped.
    assert _name_shaped_literals('x = "Anchor: the half that already worked."') == set()
    assert _name_shaped_literals('x = "outputs/Reports/Latest.md"') == set()
    # A docstring is not fixture data.
    assert _name_shaped_literals(
        'def f():\n    """Rupert Moneypenny wrote this."""\n') == set()


def test_this_test_names_only_fictional_people():
    """The guard on the guard: a test written to prove no real name is in the
    engine must not itself carry one. Every person-shaped literal here is Bond
    scaffolding, which the operator nominated as the placeholder on 2026-08-24."""
    # The pattern used to be `re.findall(r'"([A-Z][a-z]{3,})"', body)`: a whole
    # double-quoted string of exactly ONE capitalised word. Every person-shaped
    # literal this file actually contains is two words - "James Bond",
    # "James Moneypenny", "World Price", "Misha Hanin" - and a space where the
    # pattern demands a closing quote matches nothing. So the docstring's claim
    # was being checked against an empty set, and pasting a real two-word
    # colleague name into the overlay fixture passed in silence: precisely the
    # leak this file exists to stop.
    #
    # Extraction is now by `ast` over the real string constants, with
    # docstrings excluded. A regex over the raw text cannot tell a triple-quote
    # from a pair of empty strings, and reading prose out of docstrings is what
    # made the first widened attempt flag "Anchor" and "Promotion".
    names = _name_shaped_literals(Path(__file__).read_text(encoding="utf-8"))
    # One roster, not two. `_EXEC_FIXTURE_NAMES` above is this same list in
    # lowercase, and it used to sit beside a second hand-maintained copy here,
    # which is the drift shape this file condemns elsewhere.
    allowed = {name.capitalize() for name in _EXEC_FIXTURE_NAMES} | {
        "World", "Price", "Misha", "Hanin", "Rupert", "Placeholder"}
    # A floor: the extractor must actually see the Bond scaffolding. Without
    # this, an extractor that silently returns nothing passes the check below.
    assert {"James", "Bond"} <= names, (
        f"the extractor did not find the known fixture names; it is broken: {names}")
    assert names <= allowed, (
        f"an unvetted person-shaped literal appeared: {sorted(names - allowed)}")
