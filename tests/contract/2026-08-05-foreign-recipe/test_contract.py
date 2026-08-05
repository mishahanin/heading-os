"""The contract for `foreign-recipe` — a refused manifest is not a damaged one.

ONE defect, and it is a WORDING defect, not a control defect. `read_freeze`
raises `FreezeCorrupt` for four different reasons: the file is unreadable, it is
not a JSON object, its `recipe` does not match the current one, or its shape is
wrong. Two of those are damage. One of them — the recipe mismatch — is the
ordinary consequence of a deliberate version bump, and it happened on
2026-08-04 when RECIPE went v6 to v7 mid-slice.

Both surfaces that catch the exception wrap it in a headline asserting damage:

    .claude/hooks/_dispatch.py:885
        "The freeze manifest is damaged, so every write is denied fail-closed"
    scripts/canopus.py:2110
        "Every write is denied while the manifest is damaged."

The exception's own sentence, which both already print verbatim, is accurate:
`carries recipe 'canopus-freeze-v6', expected 'canopus-freeze-v7'`. So the
operator is told two contradictory things at once, and the false one comes
first and in the imperative voice. The third surface,
`canopus_gate._freeze_gate` at canopus_gate.py:264, prints `{exc}` with no
headline and is already correct — it is the model the other two should copy,
and it is why this slice is small.

WHAT THIS SLICE DELIBERATELY DOES NOT DO. An earlier design for this defect
added a distinct exception class, a `cmd_release` branch clearing a
version-mismatched manifest without `--force`, and a new retake cause token.
Measurement killed all three: the ledger's single `force_release` row already
carries `reason: "recipe bumped v6 to v7..."` and is followed by a row of kind
`recipe-bumped`, so the ledger is not blind to the difference and never was;
and `--force` already exists as the escape and is named in both messages. Three
RECIPE bumps have ever happened. A cheaper door for a once-a-quarter event, at
the cost of a new class, a new branch and a new token, is not worth its weight.
What is left is the part that was actually wrong: the sentence.

Every test imports the code under test INSIDE its body and takes its own scratch
tree, so nothing here reads the engine's working tree.
"""

import importlib.util
import json
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "_dispatch.py"
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


def _dispatcher(monkeypatch, root: Path):
    spec = importlib.util.spec_from_file_location("foreign_recipe_dispatch", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WORKSPACE", root)
    return module


def _deny_text(monkeypatch, root: Path) -> str:
    module = _dispatcher(monkeypatch, root)
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": str(root / "tests" / "test_alpha.py"),
                              "content": "x"}}
    decision = module.check_canopus_freeze(payload)
    assert decision is not None, "a manifest the reader refuses did not deny the write"
    return json.dumps(decision)


def _found_damage_words(message: str) -> list:
    lowered = message.lower()
    return [word for word in DAMAGE_WORDS if word in lowered]


# ============================================================
# SC-1 — a version mismatch is not reported as damage
# ============================================================

def test_the_write_denial_does_not_call_a_version_mismatch_damage(
    monkeypatch, stale_recipe_tree
):
    """SC-1 [failure-mode]. WHEN the manifest is refused only because its recipe
    is not the current one, THE SYSTEM SHALL NOT tell the operator the manifest
    is damaged.

    Both halves are asserted, and the second is the one that matters. Deleting
    the headline satisfies the first on its own, and a careless deletion would
    take the recipe sentence with it, leaving a denial that says nothing. The
    operator must still learn WHICH recipe was found and which was wanted,
    because that pair is the whole diagnosis.
    """
    message = _deny_text(monkeypatch, stale_recipe_tree)

    assert not _found_damage_words(message), (
        f"the denial claims damage for a deliberate version bump: "
        f"{_found_damage_words(message)} in {message!r}")
    assert "canopus-freeze-v6" in message and "canopus-freeze-v7" in message, (
        f"the denial no longer names both recipes, so the operator cannot tell "
        f"what was found or what was wanted: {message!r}")


def test_the_cli_refusal_does_not_call_a_version_mismatch_damage(
    monkeypatch, capsys, stale_recipe_tree
):
    """SC-1b [failure-mode]. The same for the CLI's own refusal.

    Two surfaces print this, and a fix applied to one is the shape of this
    defect recurring. `verify` is the carrier because it reads the manifest and
    does little else, so the refusal under test is what produces the output.
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
# SC-2 — the escape is still named, at both surfaces
# ============================================================

def test_both_refusals_still_name_the_way_out(monkeypatch, capsys, stale_recipe_tree):
    """SC-2 [happy-path]. WHEN either surface refuses, THE SYSTEM SHALL still
    name the command that clears the manifest.

    This is the property most at risk from the fix. The false headline and the
    escape sit in the same string at both surfaces, and the obvious way to
    delete a wrong sentence is to delete the paragraph it lives in. A refusal
    that denies every write and does not say how to proceed is a worse defect
    than the one being fixed.
    """
    from scripts.canopus import main

    denial = _deny_text(monkeypatch, stale_recipe_tree)
    main(["--root", str(stale_recipe_tree), "verify"])
    cli = capsys.readouterr().err

    for surface, message in (("the write denial", denial), ("the CLI refusal", cli)):
        assert "release" in message and "--force" in message and "--window" in message, (
            f"{surface} no longer names the way out: {message!r}")


# ============================================================
# SC-3 — one sentence, one place
# ============================================================

def test_the_refusal_sentence_is_built_in_one_named_function(stale_recipe_tree):
    """SC-3. THE SYSTEM SHALL build that sentence in ONE named function that
    both surfaces call.

    The defect exists twice today because the sentence was written twice. Fixing
    both copies without merging them leaves the identical trap for the next
    person who edits one. The function takes the exception and returns the
    operator-facing sentence, so the two surfaces cannot disagree about what a
    refused manifest means.

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


# ============================================================
# SC-4 — real damage is still refused, and still readable
# ============================================================

def test_a_genuinely_unreadable_manifest_is_still_refused(monkeypatch, tmp_path):
    """SC-4 [failure-mode]. WHEN the manifest is genuinely unreadable, THE
    SYSTEM SHALL still deny every write and still carry the reader's reason.

    The fix removes a claim; this is the test that it removed only the claim.
    One sentence now serves four causes, and the two that ARE damage must not
    have been made vaguer to accommodate the two that are not. The reader's own
    sentence says "unreadable", so the operator loses nothing by the headline
    going cause-neutral.
    """
    from scripts.utils.canopus_freeze import freeze_state_path

    root = _tree(tmp_path)
    path = freeze_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    message = _deny_text(monkeypatch, root)

    assert "unreadable" in message, (
        f"the denial no longer says why the manifest could not be read, so real "
        f"damage now looks like every other refusal: {message!r}")
