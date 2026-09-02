"""`check_reference_validation` pointed at a table that has never existed.

The check read the ENGINE `CLAUDE.md` for a "Reference Resources" heading. That
file carries four sections and none of them is that one; `git log -S` finds no
commit that ever added it. So the loop ran zero times, and for months the
section printed an unconditional green over an empty set. An earlier pass made
the sentence honest ("0 paths checked (this section verified nothing)") without
making the check useful.

The reference index the workspace actually keeps lives in the private data
overlay, resolved through the data-root seam. These tests point the check at it
and hold three properties:

  - a run that examined zero paths can never read as success, in either of the
    two ways zero happens (no overlay at all, and an index naming no paths);
  - a path is checked against BOTH roots, because the index names engine files
    and data-overlay files in the same bullet list;
  - the count printed is the count examined.

Every test but one builds its own fixture index under `tmp_path` and points the
data root there, so nothing here depends on the operator's overlay content,
which changes daily. The single exception,
`test_the_live_reference_index_is_checked_or_skipped`, is named in
`LIVE_TREE_TESTS` below, asserts only shape and never content, and skips
cleanly when no overlay is present.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "workspace-health.py"

# The one test that reads the operator's real overlay. Named here so the module
# docstring above cannot quietly stop being true.
LIVE_TREE_TESTS = ("test_the_live_reference_index_is_checked_or_skipped",)


@pytest.fixture
def wh():
    """A fresh module object per test: it binds directories at import time."""
    spec = importlib.util.spec_from_file_location(
        "workspace_health_refcheck_under_test", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["workspace_health_refcheck_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _plain(captured: str) -> str:
    """Output without ANSI colour, so assertions read the words not the escapes."""
    return re.sub(r"\x1b\[[0-9;]*m", "", captured)


@pytest.fixture
def two_roots(wh, tmp_path, monkeypatch):
    """An engine root and a data overlay, both scratch, with an empty index.

    Returns (engine, data, write_index). `write_index` takes the markdown body.
    """
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    (data / "reference").mkdir(parents=True)
    engine.mkdir()
    monkeypatch.setattr(wh, "WORKSPACE", engine)
    monkeypatch.setattr(wh, "get_data_root", lambda: data)

    def write_index(body: str) -> None:
        (data / "reference" / "workspace-overview.md").write_text(
            body, encoding="utf-8")

    return engine, data, write_index


# ============================================================
# The load-bearing property: zero examined is never a pass
# ============================================================

def test_no_data_overlay_skips_and_never_reads_as_success(wh, tmp_path,
                                                          monkeypatch, capsys):
    """A public engine clone has no overlay, so the index is simply absent.

    That is a first-class outcome, not a failure and not a green. The sibling
    `check_docs_sync` already treats a missing `templates/` this way.
    """
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path / "engine")
    monkeypatch.setattr(wh, "get_data_root", lambda: tmp_path / "absent")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, "an absent overlay is not a failure"
    assert "0 paths checked" in out
    assert "this section verified nothing" in out
    assert "OK" not in out, out
    assert "resolve" not in out, out


def test_an_index_naming_no_paths_is_not_a_pass(wh, two_roots, capsys):
    """The other way zero happens: the file is there and names nothing.

    Distinct message from the absent-overlay case, because the remediation is
    different, and the operator was handed the wrong one before.

    It REFUSES, and that is the change of 2026-09-02. Saying "verified nothing"
    while returning 0 left `/push-updates` reading exit 0 as a clean gate, so
    the prose and the exit code told the operator opposite things. There IS a
    corpus here (the index is present and readable) and this section read none
    of it; the absent-overlay case above, where no corpus exists at all, stays
    inconclusive on purpose.
    """
    _engine, _data, write_index = two_roots
    write_index("# Workspace Overview\n\nProse with no backticked paths.\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1
    assert "refuses to report clean" in out
    assert "0 paths checked" in out
    assert "this section verified nothing" in out
    assert "OK" not in out, out
    assert "names no workspace-relative file paths" in out


def test_the_two_zero_outcomes_do_not_share_a_message(wh, tmp_path,
                                                      monkeypatch, capsys):
    """Both say "verified nothing"; neither may be mistaken for the other."""
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path / "engine")
    monkeypatch.setattr(wh, "get_data_root", lambda: tmp_path / "absent")
    wh.check_reference_validation()
    absent = _plain(capsys.readouterr().out)

    data = tmp_path / "data"
    (data / "reference").mkdir(parents=True)
    (data / "reference" / "workspace-overview.md").write_text("# x\n",
                                                              encoding="utf-8")
    monkeypatch.setattr(wh, "get_data_root", lambda: data)
    wh.check_reference_validation()
    empty = _plain(capsys.readouterr().out)

    assert absent != empty
    assert "not present" in absent
    assert "not present" not in empty


# ============================================================
# The check actually checks: a broken path is reported
# ============================================================

def test_a_broken_path_in_the_index_is_reported_missing(wh, two_roots, capsys):
    """The negative direction. Without this, `return 0` satisfies every test
    above.
    """
    engine, _data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "present.py").write_text("x", encoding="utf-8")
    write_index(
        "# Workspace Overview\n\n"
        "- **Here:** `scripts/present.py`\n"
        "- **Gone:** `scripts/retired-long-ago.py`\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 1
    assert "Missing: scripts/retired-long-ago.py" in out
    assert "1 of 2 reference path(s) resolve" in out
    assert "All " not in out, "a run with a miss may not print an all-clear"


def test_every_path_resolving_prints_the_count_it_examined(wh, two_roots,
                                                           capsys):
    engine, data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "one.py").write_text("x", encoding="utf-8")
    (data / "context").mkdir()
    (data / "context" / "two.md").write_text("x", encoding="utf-8")
    write_index("- `scripts/one.py`\n- `context/two.md`\n")

    assert wh.check_reference_validation() == 0
    assert "2 of 2 reference path(s) resolve" in _plain(capsys.readouterr().out)


# ============================================================
# Which root a path is relative to: both, or the wall of false misses
# ============================================================

def test_a_path_is_resolved_against_either_root(wh, two_roots, capsys):
    """The index names engine files and overlay files in one list.

    An engine-only rule reports every data path missing; a data-only rule
    reports every engine path missing. Both directions are pinned here so a
    later "simplification" to a single root turns this red.
    """
    engine, data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "engine-only.py").write_text("x", encoding="utf-8")
    (data / "crm").mkdir()
    (data / "crm" / "overlay-only.md").write_text("x", encoding="utf-8")
    write_index("`scripts/engine-only.py` and `crm/overlay-only.md`\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert "Missing" not in out, out


def test_an_engine_clone_wearing_the_data_root_does_not_double_count(
        wh, tmp_path, monkeypatch, capsys):
    """`get_data_root()` can resolve to the engine root itself.

    The root list is de-duplicated, so the one-root case behaves like the
    two-root case rather than checking everything twice.
    """
    root = tmp_path / "single"
    (root / "reference").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts" / "here.py").write_text("x", encoding="utf-8")
    (root / "reference" / "workspace-overview.md").write_text(
        "- `scripts/here.py`\n- `scripts/absent.py`\n", encoding="utf-8")
    monkeypatch.setattr(wh, "WORKSPACE", root)
    monkeypatch.setattr(wh, "get_data_root", lambda: root)

    assert wh.check_reference_validation() == 1
    assert "1 of 2 reference path(s) resolve" in _plain(capsys.readouterr().out)


# ============================================================
# What counts as a path at all, and saying what was left out
# ============================================================

# One fixture per REASON a token is not a literal path, and each date-stamp
# fixture carries exactly ONE of `_NOT_A_LITERAL_PATH`'s alternatives.
#
# It read `outputs/report-YYYY-MM-DD.md` for all of them until 2026-09-01, which
# is a surviving-twin: that one string is matched by `YYYY` AND by `MM-DD`, so
# deleting either alternative left the suite green because the other still
# accepted the fixture. MEASURED, three mutations against this file:
#
#   `\.\.|MM-DD|HH-MM`  (YYYY deleted)    25 passed, 1 skipped -- SURVIVED
#   `\.\.|YYYY|HH-MM`   (MM-DD deleted)   25 passed, 1 skipped -- SURVIVED
#   `\.\.|YYYY|MM-DD`   (HH-MM deleted)   25 passed, 1 skipped -- SURVIVED
#
# `HH-MM` was the worse of the two shapes: no fixture in this file contained it
# at all, so that alternative had never been executed by any test.
_SKIP_TOKENS = [
    ("SKILL.md", "a bare filename: no directory to root"),
    (".env", "same, and the index says it 11 times"),
    ("scripts/*.py", "a glob"),
    (".claude/skills/{name}/SKILL.md", "a template placeholder"),
    ("outputs/report-YYYY.md", "a year stamp; `YYYY` alone"),
    ("outputs/report-MM-DD.md", "a day stamp; `MM-DD` alone"),
    ("outputs/log-HH-MM.txt", "a clock stamp; `HH-MM` alone"),
    ("/etc/somewhere.conf", "absolute"),
    ("~/.config/thing.json", "home-relative"),
    ("../outside/thing.md", "parent traversal in an example"),
    ("<data-root>/config/thing.yaml", "the documentation form of the seam"),
]


def test_every_date_stamp_alternative_has_a_fixture_only_it_catches(wh):
    """The anti-twin control, derived from the pattern rather than listed.

    Splitting the compiled source on `|` means a NEW alternative added to
    `_NOT_A_LITERAL_PATH` fails here until someone writes the token that
    exercises it, instead of riding into the tree on a fixture some older
    alternative already matched. Each alternative must be the ONLY one its
    fixture triggers, which is the property the three mutations above broke.
    """
    alternatives = wh._NOT_A_LITERAL_PATH.pattern.split("|")
    assert len(alternatives) >= 2, alternatives

    tokens = [tok for tok, _ in _SKIP_TOKENS]
    for alt in alternatives:
        sole = [t for t in tokens
                if re.search(alt, t)
                and not any(re.search(other, t)
                            for other in alternatives if other != alt)]
        assert sole, (
            f"no fixture in _SKIP_TOKENS is caught by {alt!r} and by nothing "
            f"else, so deleting that alternative leaves this file green")


@pytest.mark.parametrize("token", [t for t, _ in _SKIP_TOKENS],
                         ids=[r for _, r in _SKIP_TOKENS])
def test_a_token_that_is_not_a_literal_path_is_never_reported_missing(
        wh, two_roots, capsys, token):
    """The old regex was written for a markdown TABLE in a different file.

    Applied to this index it matches hundreds of tokens that no root can
    resolve, and every one becomes a false "Missing:" line. That is how a
    useful check gets switched off.

    The fixture carries one real, resolving path beside the token under test.
    Without it the index names zero literal paths, and since 2026-09-02 that
    trips the section's zero-inspected floor, which would turn every case here
    red for a reason that has nothing to do with the token.
    """
    engine, _data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "anchor.py").write_text("x", encoding="utf-8")
    write_index(f"- see `{token}` for detail, and `scripts/anchor.py` to run it\n")

    issues = wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    assert issues == 0, out
    assert "Missing" not in out, out
    assert "1 of 1 reference path(s) resolve" in out, (
        f"{token!r} was counted as a literal path")


def test_the_skipped_tokens_are_counted_out_loud(wh, two_roots, capsys):
    """Silence about an exclusion reads as coverage
    (`.claude/rules/scope-claims.md`). A path-shaped token that cannot be
    existence-checked is reported, not swallowed.
    """
    engine, _data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "real.py").write_text("x", encoding="utf-8")
    write_index("`scripts/real.py` `scripts/*.py` `~/.config/thing.json`\n")

    assert wh.check_reference_validation() == 0
    out = _plain(capsys.readouterr().out)
    assert "2 path-shaped token(s) skipped" in out
    assert "1 of 1 reference path(s) resolve" in out


def test_a_repeated_path_is_examined_once(wh, two_roots, capsys):
    """The index cites the same file up to nine times; the count is of files."""
    engine, _data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "hot.py").write_text("x", encoding="utf-8")
    write_index("`scripts/hot.py` again `scripts/hot.py` and `scripts/hot.py`\n")

    assert wh.check_reference_validation() == 0
    assert "1 of 1 reference path(s) resolve" in _plain(capsys.readouterr().out)


def test_paths_are_read_from_prose_not_from_table_rows(wh, two_roots, capsys):
    """The old loop only looked at lines containing `|`.

    The index writes its paths in bullets and sentences, so a table-row filter
    finds none of them.
    """
    engine, _data, write_index = two_roots
    (engine / "scripts").mkdir()
    (engine / "scripts" / "bullet.py").write_text("x", encoding="utf-8")
    write_index("Run `scripts/bullet.py` when the daemon is down.\n")

    assert wh.check_reference_validation() == 0
    assert "1 of 1 reference path(s) resolve" in _plain(capsys.readouterr().out)


# ============================================================
# Structural: the target moved, and it moved through the seam
# ============================================================

def test_the_check_no_longer_reads_the_engine_claude_md(wh) -> None:
    """The table it looked for has never existed in that file.

    Asserted over the parsed function rather than a grep, so a rename or a
    comment mentioning CLAUDE.md does not decide the verdict.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef)
              and n.name == "check_reference_validation")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}

    assert "CLAUDE_MD" not in names
    assert "get_data_root" in names, (
        "the overlay must be resolved through the seam, never a literal path")


def test_the_overlay_location_is_never_a_literal_in_the_check() -> None:
    """Engine law: this repo is public.

    The overlay's directory name is operator topology. It comes from
    `get_data_root()` at runtime and must not be spelled in the check. Scoped
    to the two functions this shard owns rather than the whole file: a sibling
    docstring elsewhere in the script names the overlay directory to explain a
    routing split, which predates this work and is not this test's business.
    """
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    owned = [n for n in tree.body
             if isinstance(n, ast.FunctionDef)
             and n.name in ("check_reference_validation",
                            "_reference_index_paths")]
    assert len(owned) == 2, "a function this shard pins was renamed or removed"

    for fn in owned:
        body = ast.get_source_segment(text, fn) or ""
        assert "heading-os-data" not in body, fn.name
        assert "/home/" not in body, fn.name


# ============================================================
# The one live-tree reader
# ============================================================

def test_the_live_reference_index_is_checked_or_skipped(wh, capsys) -> None:
    """Shape only, never content: the operator's index changes daily.

    Either the overlay is absent, in which case the run must skip and say it
    verified nothing, or it is present, in which case the run must name a
    non-zero count. What it must never do is print an all-clear over zero.
    """
    index = wh.get_data_root() / wh.REFERENCE_INDEX_RELPATH
    if not index.exists():
        pytest.skip("no data overlay on this clone")

    wh.check_reference_validation()
    out = _plain(capsys.readouterr().out)

    counted = re.search(r"(\d+) of (\d+) reference path\(s\) resolve", out) \
        or re.search(r"All (\d+) reference path\(s\) resolve", out)
    assert counted, out
    assert int(counted.groups()[-1]) > 0, "a live index that names no paths"


def test_the_module_docstring_names_the_live_tree_reader_that_exists() -> None:
    """The docstring's exception list, held to the file it describes."""
    defined = {name for name in globals() if name.startswith("test_")}
    doc = __doc__ or ""
    for name in LIVE_TREE_TESTS:
        assert name in defined, (
            f"{name} was renamed or removed; the module docstring still cites it")
        assert name in doc, f"{name} reads the live tree and the docstring omits it"


# ============================================================
# The summary, one level up: a pass line over a section that verified nothing
# ============================================================

def test_the_summary_never_calls_an_inconclusive_run_a_pass(wh, tmp_path,
                                                            monkeypatch, capsys):
    """The section was honest and the SUMMARY undid it four lines later.

    `check_reference_validation` printed "0 paths checked (this section verified
    nothing)" and returned 0, because nothing was wrong; it just had nothing to
    look at. `main()` counts issues, saw zero, and printed "Section 'refs'
    passed." Two opposite statements a few lines apart, and only the confident
    one survives being skim-read.

    The registry is the fix: a check that produced no verdict says so out of
    band, because an issue COUNT cannot carry "I had no answer" and pretending
    it can is what produced the contradiction.
    """
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path / "engine")
    monkeypatch.setattr(wh, "get_data_root", lambda: tmp_path / "absent")
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "refs"])

    with pytest.raises(SystemExit):   # main() exits on the issue count
        wh.main()

    out = _plain(capsys.readouterr().out)
    assert "verified nothing" in out
    assert "INCONCLUSIVE" in out
    assert "Section 'refs' passed." not in out, (
        "the summary called a run that examined zero paths a pass")


def test_the_summary_still_says_passed_when_the_run_really_checked_something(
        wh, two_roots, monkeypatch, capsys):
    """The negative control, and it carries the test above.

    Printing INCONCLUSIVE unconditionally, or deleting the pass line outright,
    satisfies the previous test and makes the summary useless. A run that did
    examine paths and found them all present must still read as a pass.
    """
    engine, _data, write_index = two_roots
    (engine / "reference").mkdir(exist_ok=True)
    (engine / "reference" / "present.md").write_text("x", encoding="utf-8")
    write_index("- see `reference/present.md` for the thing\n")
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "refs"])

    with pytest.raises(SystemExit):   # main() exits on the issue count
        wh.main()

    out = _plain(capsys.readouterr().out)
    assert "INCONCLUSIVE" not in out
    assert "Section 'refs' passed." in out


def test_the_registry_does_not_leak_between_runs(wh, two_roots, monkeypatch,
                                                 capsys):
    """A module-level list outlives one call, so `main` must clear it.

    Without the clear, a second `main()` in one process inherits the first
    run's verdict and reports a clean run as inconclusive forever after. That
    is how a warning becomes background noise.
    """
    engine, _data, write_index = two_roots
    (engine / "reference").mkdir(exist_ok=True)
    (engine / "reference" / "present.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "refs"])

    write_index("nothing path shaped here\n")
    with pytest.raises(SystemExit):   # main() exits on the issue count
        wh.main()
    assert "INCONCLUSIVE" in _plain(capsys.readouterr().out)

    write_index("- see `reference/present.md` for the thing\n")
    with pytest.raises(SystemExit):   # main() exits on the issue count
        wh.main()
    assert "INCONCLUSIVE" not in _plain(capsys.readouterr().out)
