"""The air gap's whole traversal paragraph was held in place by nothing.

`scripts/utils/air_gap.is_denied` is the one predicate deciding what the
associative memory index, the chronicle, `/odin collect`, the commit-source
reader and the symbol-source reader are allowed to read. Its docstring commits
to four things about `..`:

  1. `..` is collapsed lexically BEFORE any deny check, so
     `threads/business/../../_secure/x` resolves to `_secure/x` and trips the
     vault prefix;
  2. a path that still escapes its root after the collapse is denied fail-closed;
  3. the bare string `..` is one of those;
  4. the transform is pure, with no filesystem access.

The audit shard reported that `is_denied("..")` returns False. **That claim is
wrong**, and is recorded here so the next audit does not chase it: measured on
2026-08-29 the function returns the right answer for every traversal input.

    input                                     is_denied
    ----------------------------------------  ---------
    ".."                                      True
    "../x"                                    True
    "../../_secure/x"                         True
    "threads/business/../../_secure/x"        True
    "..\\x"                                   True
    "."                                       False
    ""                                        False
    "..."                                     False
    "..foo"                                   False
    "x/.."                                    False

What the shard got right, and understated, is the coverage. Not one of the four
commitments was executed by a test. Measured the same day against the nine test
files that import `air_gap` or drive a caller of it:

    mutation to scripts/utils/air_gap.py                    verdict
    ------------------------------------------------------  ---------
    drop `norm == ".."` from the traversal branch            SURVIVED
    delete the traversal branch entirely                     SURVIVED
    drop `os.path.normpath`, compare the raw path            SURVIVED
    delete the `norm == "."` root guard                      SURVIVED
                                                             0 of 4 caught

262 air-gap assertions were green over a predicate whose traversal handling
could be removed wholesale. Deleting the normpath call is the sharp one: it
reopens `threads/business/../../_secure/x` on a reader that never touches the
filesystem, which is exactly the shape the vault deny exists to refuse.

Verifying the fix widened the set to twelve mutations and turned up two more
gaps this file also closes. Dropping the FIRST `replace("\\", "/")` looked
harmless, because the second one runs on the normpath result and still fixes a
bare `..\\x`; it is load-bearing only when a backslash path needs COLLAPSING,
where `threads\\business\\..\\..\\_secure\\x.md` reaches `normpath` as one
opaque segment and comes out allowed. And the `norm == "."` early return is dead
under the hard-coded denies alone, so no mutation on it could be caught until it
is driven with the caller config that makes it matter.

No production behaviour changes here. The predicate is correct; what was missing
is the pin.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import air_gap  # noqa: E402
from scripts.utils.air_gap import is_denied  # noqa: E402

AIR_GAP_SRC = ROOT / "scripts" / "utils" / "air_gap.py"


# ============================================================
# The bare `..`, and every other way out of the root
# ============================================================

ESCAPES = [
    ("..", "the bare traversal, alone"),
    ("../x", "one level out, then a file"),
    ("../../x", "two levels out"),
    ("../..", "two levels out, nothing after"),
    ("a/../../b", "collapses to ../b"),
    ("./..", "a leading dot does not change where it lands"),
    ("..\\x", "the Windows spelling of ../x"),
    ("threads\\business\\..\\..\\_secure\\x.md",
     "backslashes normalised BEFORE the collapse, or nothing collapses"),
    ("../_SECURE/x", "escape and vault at once"),
    ("../Personal/notes.md", "escape and a personal segment at once"),
]


@pytest.mark.parametrize("rel,why", ESCAPES, ids=[e[0] for e in ESCAPES])
def test_a_path_that_escapes_its_root_is_denied(rel, why):
    """Fail-closed. A workspace-relative ingest path never legitimately leaves
    the workspace, so one that does is refused rather than resolved."""
    assert is_denied(rel) is True, f"{rel!r} ({why}) was allowed"


@pytest.mark.parametrize("rel,why", ESCAPES, ids=[e[0] for e in ESCAPES])
def test_an_escape_is_denied_even_with_the_callers_config_emptied(rel, why):
    """The traversal deny is hard-coded alongside the vault prefix, so a broken
    or emptied config cannot open it."""
    assert is_denied(rel, deny_prefixes=(), deny_segments=()) is True


# ============================================================
# The other direction: what a traversal check must NOT eat
# ============================================================

ALLOWED = [
    (".", "the workspace root itself"),
    ("", "the empty path normalises to the root"),
    ("...", "three dots is a directory name, not a traversal"),
    ("..foo", "a leading pair of dots inside a name"),
    ("foo..bar", "dots in the middle of a name"),
    ("x/..", "collapses back to the root, which is not an escape"),
    ("a/./b.md", "a single dot segment collapses away"),
    ("crm/contacts/james-bond.md", "an ordinary ingest path"),
    ("knowledge/odin-brain/principles/x.md", "another ordinary ingest path"),
    ("impersonal/notes.md", "`personal` as a substring of a segment"),
    ("personalities/x.md", "`personal` as a prefix of a segment"),
    ("_secured/x.md", "`_secure` as a prefix of a longer directory"),
]


@pytest.mark.parametrize("rel,why", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_an_ordinary_path_is_not_mistaken_for_a_traversal(rel, why):
    """A guard that denies everything is as useless as one that denies nothing,
    and only this direction separates the two."""
    assert is_denied(rel) is False, f"{rel!r} ({why}) was denied"


# ============================================================
# The collapse happens BEFORE the deny check
# ============================================================

COLLAPSE_INTO_A_DENY = [
    ("threads/business/../../_secure/x.md", "the docstring's own example"),
    ("threads/business/../personal/notes.md", "collapses onto a personal segment"),
    ("a/b/../../_secure/vault.md", "two levels back onto the vault prefix"),
    ("./_secure/../_secure/x.md", "a redundant hop that still lands in the vault"),
    ("crm/../_SECURE/x.md", "case-folded after the collapse, not before"),
]


@pytest.mark.parametrize("rel,why", COLLAPSE_INTO_A_DENY,
                         ids=[c[0] for c in COLLAPSE_INTO_A_DENY])
def test_a_path_that_walks_back_into_a_denied_tree_is_denied(rel, why):
    """Without the lexical collapse, `startswith("_secure/")` reads the literal
    first segment and a `..` hop walks straight past the vault deny."""
    assert is_denied(rel) is True, f"{rel!r} ({why}) reached a denied tree"


def test_the_collapse_does_not_manufacture_a_deny():
    """The other direction for the collapse: it must resolve paths, not deny
    them. `a/../b/c.md` is `b/c.md`, which nothing denies."""
    assert is_denied("a/../b/c.md") is False


# ============================================================
# The root guard
# ============================================================

def test_the_root_is_allowed_and_an_escape_from_it_is_not():
    """`.` and `..` differ by one character and by the entire decision. Held as
    a pair, because a guard tested only on `.` proves the root is readable and
    says nothing about the step above it."""
    assert is_denied(".") is False
    assert is_denied("..") is True


@pytest.mark.parametrize("rel", ["/", "//", "/./"])
def test_an_absolute_spelling_of_the_root_is_still_the_root(rel):
    """Leading slashes are stripped before the collapse, so an absolute-looking
    path is treated as workspace-relative rather than denied outright."""
    assert is_denied(rel) is False


def test_an_absolute_escape_is_still_denied():
    assert is_denied("/../etc/passwd") is True


def test_no_caller_config_can_turn_the_root_itself_into_a_deny():
    """The `norm == "."` early return, driven by the only config that reaches it.

    Under the hard-coded denies alone the branch is redundant: `.` matches
    neither the `_secure/` prefix nor the `personal` segment, so deleting it
    changes no answer and no mutation on it could ever be caught. It becomes
    load-bearing the moment a caller's config contains a `.`, which is when the
    root would otherwise be denied to the caller that owns it. That is the
    invariant: the workspace root is readable whatever the config says.
    """
    assert is_denied(".", deny_prefixes=["."], deny_segments=["."]) is False
    assert is_denied("", deny_prefixes=["."]) is False
    assert is_denied("x/..", deny_segments=["."]) is False


def test_the_root_carve_out_does_not_soften_the_rest_of_that_config():
    """The other direction. Allowing the root must not turn into allowing
    everything under a config that contains a dot."""
    assert is_denied("_secure/x.md", deny_prefixes=["."]) is True
    assert is_denied("threads/personal/x.md", deny_segments=["."]) is True
    assert is_denied("outputs/x.md", deny_prefixes=["outputs/"]) is True


# ============================================================
# Purity: the predicate never touches the filesystem
# ============================================================

_FS_CALLS = ["os.stat", "os.lstat", "os.listdir", "os.path.exists",
             "os.path.realpath", "os.path.isdir", "os.path.abspath"]


@pytest.mark.parametrize("target", _FS_CALLS)
def test_the_predicate_reaches_no_filesystem_call(target):
    """Asked of the runtime, not grepped from the source.

    The predicate is imported by a hook that runs on every tool call and by an
    indexer that walks tens of thousands of paths. A stat per path would be a
    cost; a `realpath` would also be a correctness change, because it follows
    symlinks and the deny is meant to be lexical.
    """
    def _boom(*_args, **_kwargs):
        raise AssertionError(f"{target} was called")

    with mock.patch(target, side_effect=_boom):
        assert is_denied("threads/business/../../_secure/x.md") is True
        assert is_denied("crm/contacts/james-bond.md") is False
        assert is_denied("..") is True


_FS_FUNCTION_NAMES = frozenset({
    "stat", "lstat", "listdir", "scandir", "walk", "exists", "lexists",
    "isdir", "isfile", "islink", "realpath", "abspath", "readlink", "open",
    "access", "getsize", "getmtime", "samefile",
})


def test_the_module_binds_no_filesystem_function_of_its_own():
    """The bypass the parametrized patch above cannot see.

    `mock.patch("os.path.realpath")` swaps an attribute on the `os.path` module
    object. A `from os.path import realpath` at the top of `air_gap.py` creates
    a SECOND reference, in air_gap's own namespace, that the patch never
    reaches: the predicate could stat the disk on every path while all seven
    purity cases stayed green. So the namespace itself is asked.
    """
    offenders = sorted(
        name for name, value in vars(air_gap).items()
        if not name.startswith("__") and callable(value)
        and (name in _FS_FUNCTION_NAMES
             or getattr(value, "__name__", "") in _FS_FUNCTION_NAMES)
    )
    assert offenders == [], (
        f"air_gap binds {offenders} directly; patching the global os.* leaves "
        f"those references live, so the purity tests above stop measuring")


def test_the_module_imports_nothing_that_could_reach_the_disk():
    """The whole import surface, stated exactly.

    A namespace check catches a binding that already exists; this catches the
    import that would create one, including `pathlib`, `shutil`, `io` and an
    aliased `from os.path import realpath as rp` that no name-based sweep would
    recognise. The predicate is a pure string transform and needs exactly one
    module.
    """
    tree = ast.parse(AIR_GAP_SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}
    assert imported == {"os"}, imported


def test_a_planted_secret_is_denied_in_every_spelling(tmp_path):
    """The guard still REFUSES, proven against a file that really exists.

    The purity tests establish that the predicate never looks at the disk. This
    one establishes that not looking does not soften the answer: the denied file
    is planted under tmp_path, its workspace-relative path is derived from the
    file rather than typed, and every spelling of it has to come back True -
    including `perſonal`, the U+017F LATIN SMALL LETTER LONG S fold that walked
    straight through the `.lower()` comparison this module used until
    2026-08-30.
    """
    planted = tmp_path / "threads" / "personal" / "diary.md"
    planted.parent.mkdir(parents=True)
    planted.write_text("nothing here may be indexed\n", encoding="utf-8")
    assert planted.is_file()
    rel = planted.relative_to(tmp_path).as_posix()
    assert rel == "threads/personal/diary.md"

    for spelling in (
        rel,
        "./" + rel,
        "outputs/x/../../" + rel,
        rel.replace("personal", "Personal"),
        rel.replace("personal", "PERSONAL"),
        rel.replace("personal", "perſonal"),
        rel.replace("/", "\\"),
    ):
        assert is_denied(spelling) is True, spelling

    vault = tmp_path / "_secure" / "keys.md"
    vault.parent.mkdir(parents=True)
    vault.write_text("nor this\n", encoding="utf-8")
    vrel = vault.relative_to(tmp_path).as_posix()
    for spelling in (vrel, "./" + vrel, "threads/business/../../" + vrel,
                     vrel.replace("_secure", "_SECURE")):
        assert is_denied(spelling) is True, spelling

    # The other direction, on a file planted the same way: an ordinary path
    # under the same root is still allowed, so the deny above is a decision and
    # not a predicate that says True to everything.
    ordinary = tmp_path / "threads" / "business" / "deal.md"
    ordinary.parent.mkdir(parents=True)
    ordinary.write_text("routine\n", encoding="utf-8")
    assert is_denied(ordinary.relative_to(tmp_path).as_posix()) is False


def test_the_predicate_answers_for_a_path_that_does_not_exist(tmp_path):
    """A lexical predicate has no opinion about what is on disk. Both answers
    are asserted, so a version that started returning False for everything
    absent would fail."""
    missing = "threads/personal/never-created-" + tmp_path.name + ".md"
    assert is_denied(missing) is True
    assert is_denied("threads/business/never-created.md") is False
    assert not os.path.exists(ROOT / missing)
