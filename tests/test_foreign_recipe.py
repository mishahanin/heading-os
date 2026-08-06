"""A manifest the reader refuses is not necessarily a damaged one.

Retired from `tests/contract/2026-08-05-foreign-recipe/`, all five IDs promoted
unchanged apart from the root path, which is `parents[1]` here rather than
`parents[3]`. Two of the five, SC-1 and SC-4, asserted the sentence at the
PreToolUse deny path, which was removed with the freeze check itself; the three
that remain assert it at the CLI surface and in the one function that builds it.

The defect. `read_freeze` raises `FreezeCorrupt` for four reasons: the file is
unreadable, it is not a JSON object, its `recipe` is not the current one, or its
shape is wrong. Two are damage. The recipe mismatch is the ordinary consequence
of a deliberate RECIPE bump, and it happened on 2026-08-04 when the version went
v6 to v7 mid-slice. Both surfaces that caught the exception then opened with a
headline asserting the manifest was damaged, one line above the reader's own
accurate `carries recipe 'canopus-freeze-v6', expected 'canopus-freeze-v7'`. The
operator was told two contradictory things at once and the false one came first.

Why these three stay in the ordinary suite. Each pins a property of the sentence
that is easy to lose to a later edit and invisible when lost: that the headline
does not assert a cause it cannot know, that both recipes still travel with it,
that the escape survives, and that one function builds it. The preservation
tests were green before the slice and are the whole point afterwards, because
both properties live in the exact string that was rewritten.
"""

import json
from pathlib import Path

import pytest

STAMP = "2026-01-01T00:00:00+00:00"

# The vocabulary a refusal may not use unless it is true. Checked as substrings
# of a lowercased message, so "corrupted" and "damaging" are caught too.
DAMAGE_WORDS = ("damag", "corrupt")


def _tree(tmp_path: Path) -> Path:
    """A scratch tree with the test gate `_resolve_root` insists on.

    `scripts/run-tests.py` must exist or `--root` is refused before the manifest
    is ever read, and the CLI tests below would then be red for a reason that
    has nothing to do with this contract. Measured at probe time: without it,
    two of the four red tests failed on "a tree with no test gate cannot enforce
    a freeze" and would have passed against any implementation at all.
    """
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "run-tests.py").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n", encoding="utf-8")
    return path


@pytest.fixture
def stale_recipe_tree(tmp_path: Path, anchor: Path) -> Path:
    """A tree holding a VALID freeze whose recipe is one version behind.

    Built by writing a real manifest and then editing the single `recipe` key,
    so everything else about it is exactly what `build_manifest` produces. A
    hand-rolled dict would let the test pass against a reader that refuses for
    some other reason entirely.
    """
    from scripts.utils.canopus_freeze import build_manifest, freeze_state_path, write_freeze

    root = _tree(tmp_path)
    write_freeze(root, build_manifest(
        [root / "tests" / "test_alpha.py"], root,
        label="demo", frozen_at=STAMP, anchor=anchor,
    ))
    path = freeze_state_path(root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["recipe"] = "canopus-freeze-v6"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


def _found_damage_words(message: str) -> list:
    lowered = message.lower()
    return [word for word in DAMAGE_WORDS if word in lowered]


# ============================================================
# SC-1 — a version mismatch is not reported as damage
# ============================================================

def test_the_cli_refusal_does_not_call_a_version_mismatch_damage(
    capsys, stale_recipe_tree
):
    """SC-1b [failure-mode]. WHEN the manifest is refused only because its
    recipe is not the current one, THE SYSTEM SHALL NOT tell the operator the
    manifest is damaged.

    `verify` is the carrier because it reads the manifest and does little else,
    so the refusal under test is what produces the output. Both halves are
    asserted, and the second is the one that matters: deleting the headline
    satisfies the first on its own, and a careless deletion would take the
    recipe sentence with it, leaving a refusal that says nothing. The operator
    must still learn WHICH recipe was found and which was wanted, because that
    pair is the whole diagnosis.
    """
    from scripts.canopus import main

    exit_code = main(["--root", str(stale_recipe_tree), "verify"])
    message = capsys.readouterr().err

    assert exit_code == 1, "a manifest the reader refuses did not fail the command"
    assert not _found_damage_words(message), (
        f"the CLI claims damage for a deliberate version bump: "
        f"{_found_damage_words(message)} in {message!r}")
    assert "canopus-freeze-v6" in message and "canopus-freeze-v7" in message, (
        f"the CLI no longer names both recipes: {message!r}")


# ============================================================
# SC-2 — the escape is still named
# ============================================================

def test_the_refusal_still_names_the_way_out(capsys, stale_recipe_tree):
    """SC-2 [happy-path]. WHEN the surface refuses, THE SYSTEM SHALL still name
    the command that clears the manifest.

    This is the property most at risk from the fix. The false headline and the
    escape sat in the same string, and the obvious way to delete a wrong
    sentence is to delete the paragraph it lives in. A refusal that fails every
    command and does not say how to proceed is a worse defect than the one that
    was fixed.
    """
    from scripts.canopus import main

    main(["--root", str(stale_recipe_tree), "verify"])
    cli = capsys.readouterr().err

    assert "release" in cli and "--force" in cli and "--window" in cli, (
        f"the CLI refusal no longer names the way out: {cli!r}")


# ============================================================
# SC-3 — one sentence, one place
# ============================================================

def test_the_refusal_sentence_is_built_in_one_named_function(stale_recipe_tree):
    """SC-3. THE SYSTEM SHALL build that sentence in ONE named function that
    both surfaces call.

    The defect existed twice for exactly one reason: the sentence was written
    twice. Fixing both copies without merging them leaves the identical trap for
    the next person who edits one. The function takes the exception and returns
    the operator-facing sentence, so the two surfaces cannot disagree about what
    a refused manifest means.

    Asserted through behaviour rather than by grepping the two files for a call:
    the function must produce a sentence carrying the exception verbatim and
    naming the escape, which is exactly what both surfaces need from it. SC-1
    and SC-2 then decide, at each surface, that it is the sentence being used.
    """
    from scripts.utils.canopus_freeze import FreezeCorrupt, refused_manifest_notice

    exc = FreezeCorrupt("freeze manifest at /x carries recipe 'a', expected 'b'")
    notice = refused_manifest_notice(exc)

    assert str(exc) in notice, (
        f"the notice drops the reason the manifest was refused: {notice!r}")
    assert "release" in notice and "--force" in notice and "--window" in notice, (
        f"the notice does not name the way out: {notice!r}")
    assert not _found_damage_words(notice), (
        f"the shared notice hard-codes a cause it cannot know: {notice!r}")
