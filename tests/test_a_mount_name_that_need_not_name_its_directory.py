"""The mount-name test counted names and never asked where a name came from.

`scripts/utils/sandbox._mount_names` decides where each corpus directory lands
inside the `/census` bubblewrap box. Two rules matter, and neither is cosmetic:

  * a name DERIVES from its directory, either from the scope name the operator
    typed (`preferred`) or from the directory's own basename;
  * two distinct directories never share a name, or one mounts over the other
    and the traversal reads one corpus where the operator asked for two.

Only the second rule was tested. `test_colliding_basenames_get_distinct_mounts`
in `tests/test_census_sandbox.py` asserted `len(set(names.values())) == 2` and
nothing else, so any function returning two distinct strings satisfied it.

Measured 2026-08-29 against that single test, mutating
`scripts/utils/sandbox.py`:

    mutation                                       verdict
    ---------------------------------------------  ---------
    base = "mount"                                 SURVIVED
    base = f"mount-{i}" over enumerate(paths)      SURVIVED
                                                   0 of 2 caught

A mount table that named every corpus `mount-0`, `mount-1` passed the test whose
whole subject is mount naming.

Widened to the two census test files, a third mutation was measured:

    mutation                                       verdict
    ---------------------------------------------  ---------
    base = "mount" (drops preferred AND basename)  caught
    base = path.resolve().name (drops preferred)   SURVIVED

`preferred` won a name from the basename in exactly zero tests. The one test
that passes a preferred name,
`test_an_air_gapped_child_of_a_mounted_scope_is_blanked`, builds its corpus at
`tmp_path / "threads"` and then asks for the preferred name `"threads"`, so the
two agree and the argument proves nothing. That argument
exists for the case where they DISAGREE: `census.resolve_corpus` mounts the
scope `threads` at its data-root-relative path `threads/business`, because the
directory basename `business` would send a traversal written against
`/data/threads` into an empty tree and return zero, which is a wrong answer with
no error anywhere. The docstring records that this happened on the first live
run, 2026-08-13. Nothing since then held the fix in place.

No production behaviour changes here. `_mount_names` is correct; the tests were
measuring less than they claimed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import sandbox  # noqa: E402


# ============================================================
# The derivation rule, pure so it can be measured on synthetic input
# ============================================================
#
# Over a correct mount table this returns empty, so a correct tree cannot tell a
# working rule from a deleted one. The synthetic cases below are what prove it
# still discriminates.

def names_that_do_not_derive(names: dict[Path, str],
                             preferred: dict[Path, str]) -> list[str]:
    """Every mount name that its own directory cannot explain.

    A name is explained when it equals the base it should have come from, or
    that base plus the `-N` suffix the collision loop appends. The base is the
    operator's scope name when one was given, otherwise the directory basename.
    """
    bad = []
    for path, name in names.items():
        base = preferred.get(path) or path.name or "corpus"
        if name == base:
            continue
        suffix = name[len(base):]
        if name.startswith(base) and suffix.startswith("-") and suffix[1:].isdigit():
            continue
        bad.append(f"{path} -> {name} (expected {base} or {base}-N)")
    return sorted(bad)


_A = Path("/synthetic/one/contacts")
_B = Path("/synthetic/two/contacts")
_C = Path("/synthetic/data/threads/business")


def test_the_derivation_rule_names_a_mount_that_ignored_its_directory():
    counters = {_A: "mount-0", _B: "mount-1"}
    assert names_that_do_not_derive(counters, {}) == [
        "/synthetic/one/contacts -> mount-0 (expected contacts or contacts-N)",
        "/synthetic/two/contacts -> mount-1 (expected contacts or contacts-N)",
    ]


def test_the_derivation_rule_is_silent_on_a_table_that_derives():
    """The other direction. A rule that always fires is as useless as one that
    never does, and only the pair of cases separates them."""
    assert names_that_do_not_derive({_A: "contacts", _B: "contacts-2"}, {}) == []


def test_the_derivation_rule_names_a_mount_that_ignored_the_operators_scope():
    """When a scope name was asked for, the basename is no longer an excuse."""
    assert names_that_do_not_derive({_C: "business"},
                                    {_C: "threads/business"}) == [
        "/synthetic/data/threads/business -> business "
        "(expected threads/business or threads/business-N)"
    ]


def test_the_derivation_rule_accepts_the_operators_scope():
    assert names_that_do_not_derive({_C: "threads/business"},
                                    {_C: "threads/business"}) == []


def test_the_derivation_rule_rejects_a_suffix_that_is_not_a_number():
    """`contacts-two` is not what the collision loop produces, and a rule that
    waved it through would wave through `contacts-anything`."""
    assert names_that_do_not_derive({_A: "contacts-two"}, {}) != []


# ============================================================
# The rule applied to the real _mount_names
# ============================================================

def test_a_mount_name_is_the_directory_basename(tmp_path):
    corpus = tmp_path / "contacts"
    corpus.mkdir()
    names = sandbox._mount_names([corpus])
    assert names == {corpus: "contacts"}


def test_colliding_basenames_are_distinct_and_still_derive(tmp_path):
    """The original assertion plus the one it was missing.

    Distinctness alone is satisfied by a counter. Distinctness AND derivation
    together are only satisfied by the collision loop that is actually there.
    """
    a = tmp_path / "one" / "contacts"
    b = tmp_path / "two" / "contacts"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    names = sandbox._mount_names([a, b])

    assert len(set(names.values())) == 2, names
    assert names_that_do_not_derive(names, {}) == [], names
    assert sorted(names.values()) == ["contacts", "contacts-2"], names


def test_a_third_collision_keeps_counting(tmp_path):
    """`-2` alone would pass a two-path test and shadow the third corpus."""
    paths = []
    for parent in ("one", "two", "three"):
        p = tmp_path / parent / "contacts"
        p.mkdir(parents=True)
        paths.append(p)

    names = sandbox._mount_names(paths)

    assert sorted(names.values()) == ["contacts", "contacts-2", "contacts-3"]
    assert names_that_do_not_derive(names, {}) == [], names


def test_the_operators_scope_name_beats_the_directory_basename(tmp_path):
    """The case `preferred` exists for, and the case no test had.

    `--corpus threads` resolves to `<data>/threads/business`. Mounted at
    `/data/business`, a traversal written against `/data/threads` reads an empty
    tree and returns zero: a wrong answer that looks exactly like a correct one.
    """
    corpus = tmp_path / "threads" / "business"
    corpus.mkdir(parents=True)

    names = sandbox._mount_names([corpus], {corpus: "threads/business"})

    assert names == {corpus: "threads/business"}, names
    assert names[corpus] != corpus.name, (
        "the preferred name was dropped and the basename used instead"
    )


def test_an_unpreferred_path_still_falls_back_to_its_basename(tmp_path):
    """The other direction: `preferred` covering one path must not rename the
    rest of the table."""
    named = tmp_path / "threads" / "business"
    named.mkdir(parents=True)
    plain = tmp_path / "crm"
    plain.mkdir()

    names = sandbox._mount_names([named, plain], {named: "threads/business"})

    assert names == {named: "threads/business", plain: "crm"}, names


def test_a_preferred_name_that_collides_is_still_made_unique(tmp_path):
    """Two scopes the operator spelled the same way must not mount over each
    other either. `census.resolve_corpus` keys its mount map by Path, so two
    distinct paths reaching one name is a silent shadow, not an error."""
    a = tmp_path / "one"
    b = tmp_path / "two"
    a.mkdir()
    b.mkdir()

    names = sandbox._mount_names([a, b], {a: "threads", b: "threads"})

    assert sorted(names.values()) == ["threads", "threads-2"], names
    assert len(names) == 2


def test_every_corpus_path_gets_exactly_one_entry(tmp_path):
    paths = []
    for name in ("crm", "context", "outputs"):
        p = tmp_path / name
        p.mkdir()
        paths.append(p)

    names = sandbox._mount_names(paths)

    assert set(names) == set(paths)
    assert len(set(names.values())) == 3


def test_a_directory_with_no_basename_falls_back_to_corpus():
    """The filesystem root resolves to an empty `name`. Without the fallback the
    mount path would be `/data/`, which is the whole corpus root."""
    names = sandbox._mount_names([Path("/")])
    assert names == {Path("/"): "corpus"}


def test_a_mount_name_derives_from_the_resolved_directory(tmp_path):
    """`_mount_names` resolves before taking the basename, so a path spelled
    through `..` still names the directory it actually reaches."""
    real = tmp_path / "crm"
    real.mkdir()
    spelled = tmp_path / "crm" / ".." / "crm"

    names = sandbox._mount_names([spelled])

    assert names == {spelled: "crm"}, names


def test_a_trailing_traversal_names_the_directory_and_not_the_dots(tmp_path):
    """The case that separates `path.resolve().name` from `path.name`.

    `<tmp>/one/contacts/..` has the literal basename `..`, and mounting a corpus
    at `/data/..` is a mount name pointing one level above the corpus root. The
    resolve is what turns it into the directory the path actually reaches.
    Split out on 2026-08-29: the `..`-in-the-middle case above cannot tell the
    two apart, because `Path.name` already skips it.
    """
    real = tmp_path / "one" / "contacts"
    real.mkdir(parents=True)
    spelled = real / ".."

    names = sandbox._mount_names([spelled])

    assert spelled.name == "..", "the fixture no longer exercises the resolve"
    assert names == {spelled: "one"}, names
