#!/usr/bin/env python3
"""Shard 53: a health engine that scanned a name list and called it the tree.

`scripts/knowledge-health.py` bounded its scan with a hardcoded list of eight
directory names and printed `Notes: N total`. MEASURED 2026-08-28 against the
live knowledge root: one directory held a note and the list spelled that
directory's name in the plural, so the note was absent from the total, the
status and type counts, the stale-seed list, the orphan set, the schema-issue
list, the keyword frequency, `--json`, and the generated INDEX.md. Nothing said
so.

Alongside it, the same file aged a seed with `date.fromisoformat(str(created))`
and swallowed the failure with a bare `pass`. `scripts/odin-brain-health.py` --
the OTHER health engine over the same knowledge root, applying the same
`status: seed` + `created:` rule -- had already fixed both halves: it branched on
the value's type, and it printed a warning instead of swallowing. A fix that
landed in one of two copies.

The test that matters here is `test_both_health_engines_age_the_same_shapes`: it
feeds one table of `created` shapes to BOTH engines and requires them to agree.
A per-engine test passes while the two drift apart, which is exactly what
happened.

Example data is invented throughout. No real entity appears in this file.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_python_files  # noqa: E402

from scripts.utils.markdown import frontmatter_date, parse_frontmatter  # noqa: E402

PY = sys.executable


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kh = _load("knowledge-health", "knowledge_health_s53")


# ============================================================
# Fixture builders -- invented data only
# ============================================================

ZK_NOTE = """---
id: "{nid}"
title: {title}
type: {ntype}
status: seed
created: {created}
keywords: [gadget, dossier]
confidence: medium
---

# {title}

Body text.
"""

BRAIN_SOURCE = """---
id: "{nid}"
title: {title}
type: source
format: fleeting
author: J. Bond
ingested: 2020-01-01
confidence: medium
keywords: [gadget]
status: seed
created: {created}
---

# {title}
"""


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _data_root(tmp_path: Path) -> Path:
    """A minimal data overlay: a knowledge root and a crm dir, nothing real."""
    data = tmp_path / "data"
    (data / "knowledge").mkdir(parents=True)
    (data / "crm").mkdir()
    return data


def _run_engine(script: str, data: Path, *args) -> subprocess.CompletedProcess:
    """Run one health engine against `data`, with the real root explicitly out.

    `HEADING_OS_DATA` is set AND `HEADING_OS_TZ` is dropped, so the child cannot
    inherit the operator's live overlay or clock configuration.
    """
    env = dict(os.environ, HEADING_OS_DATA=str(data))
    env.pop("HEADING_OS_TZ", None)
    return subprocess.run(
        [PY, str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=180,
    )


# ============================================================
# A -- the shared coercion
# ============================================================

@pytest.mark.parametrize("value,expected", [
    (dt.date(2026, 1, 2), dt.date(2026, 1, 2)),
    # NAIVE on purpose. `yaml.safe_load` produces a naive datetime for
    # `created: 2026-01-02 09:30:00`, and that is the exact value the old
    # reader could not read. A tz-aware fixture would not exercise the shape.
    (dt.datetime(2026, 1, 2, 9, 30), dt.date(2026, 1, 2)),        # noqa: DTZ001
    (dt.datetime(2026, 1, 2, 23, 59, 59), dt.date(2026, 1, 2)),   # noqa: DTZ001
    ("2026-01-02", dt.date(2026, 1, 2)),
    ("  2026-01-02  ", dt.date(2026, 1, 2)),
    ("2026-01-02T09:30:00", dt.date(2026, 1, 2)),
    ("2026-01-02 09:30:00", dt.date(2026, 1, 2)),
    ("2026-01-02T09:30:00+04:00", dt.date(2026, 1, 2)),
    ("20260102", dt.date(2026, 1, 2)),
])
def test_frontmatter_date_reads_every_shape_yaml_can_produce(value, expected):
    assert frontmatter_date(value) == expected


def test_a_quoted_datetime_reads_the_same_as_an_unquoted_one():
    """Quoting a field must not change whether its value can be read.

    `date.fromisoformat` takes date forms only on Python 3.11, so before the
    datetime fallback the same instant was readable when YAML typed it and
    unreadable when the author put quotes around it.
    """
    unquoted = parse_frontmatter("---\ncreated: 2026-01-02 09:30:00\n---\n")[0]
    quoted = parse_frontmatter('---\ncreated: "2026-01-02T09:30:00"\n---\n')[0]
    assert isinstance(unquoted["created"], dt.datetime)
    assert isinstance(quoted["created"], str)
    assert frontmatter_date(unquoted["created"]) == frontmatter_date(quoted["created"])


def test_the_plain_date_branch_is_a_fast_path_not_a_behaviour():
    """Recorded because a mutation removing that branch cannot be killed.

    `date.__str__` IS `isoformat()`, so with the `isinstance(value, date)` branch
    gone a plain date falls through to `date.fromisoformat(str(value).strip())`
    and comes back identical. MEASURED over 3665 distinct dates strided across
    0001-01-01..9999-12-31 plus both bounds: 0 differing. The branch stays for
    readability and speed; this test says why no test can distinguish it, so a
    future mutation run does not read the survivor as a coverage gap.

    The count was recomputed 2026-08-30; the docstring said 3668 and the loop
    it documents visits 3664. `(date.max - date.min).days` is 3,652,058, and
    with stride 997 the body runs for k = 0..3663, i.e. 3664 strided dates. The
    "plus both bounds" clause then double-counts: `date.min` IS the k=0 stride
    point, so the bounds loop adds exactly one new date (`date.max`) for 3665
    distinct. Nothing failed on the wrong number, which is the problem with a
    measured claim nobody recomputes -- this file's own
    `test_the_registry_does_not_outlive_its_sites` says stale measured claims
    mislead the next mutation run. The arithmetic is now derived below and
    asserted, so the sentence cannot drift from the loop again.
    """
    d = dt.date(1, 1, 1)
    last = dt.date(9999, 12, 31)
    checked = 0
    seen = set()
    while True:
        assert frontmatter_date(d) == dt.date.fromisoformat(str(d).strip()) == d
        checked += 1
        seen.add(d)
        if (last - d).days < 997:
            break
        d += dt.timedelta(days=997)
    assert checked > 3000, f"the stride stopped covering the domain: {checked}"

    expected_strided = (last - dt.date(1, 1, 1)).days // 997 + 1
    assert checked == expected_strided == 3664, (checked, expected_strided)

    for bound in (dt.date.min, dt.date.max):
        assert frontmatter_date(bound) == dt.date.fromisoformat(str(bound)) == bound
        seen.add(bound)
    assert len(seen) == 3665, (
        f"the docstring's measured count is stale: {len(seen)} distinct dates")


def test_a_broken_date_is_refused_not_truncated():
    """The divergence the ten-character slice created.

    `date.fromisoformat(str(value)[:10])` reads `"2026-01-02garbage"` as
    2026-01-02: a mistyped field becomes a confident date. MEASURED over ten
    shapes, that was the single input on which the slice and this function
    disagreed.
    """
    assert dt.date.fromisoformat("2026-01-02garbage"[:10]) == dt.date(2026, 1, 2)
    with pytest.raises(ValueError):
        frontmatter_date("2026-01-02garbage")


def test_the_census_oracle_refuses_a_broken_date_instead_of_inventing_one():
    """`_iso`'s docstring rules out exactly what its slice did.

    A ground-truth oracle answering with a WRONG date is worse than one naming a
    refusal, and the docstring committed to the refusal while the code invented
    the date.
    """
    from scripts.utils.census_oracles import UnreadableCorpus, _iso
    assert _iso("2026-01-02") == dt.date(2026, 1, 2)
    assert _iso(None) is None
    assert _iso("") is None
    with pytest.raises(UnreadableCorpus):
        _iso("2026-01-02garbage")
    with pytest.raises(UnreadableCorpus):
        _iso("not-a-date")


@pytest.mark.parametrize("value", [
    "not-a-date", "", "   ", None, 12345, 3.5, True, ["2026-01-02"], {"a": 1},
    "2026-13-01", "2026/01/02", "01-02-2026",
])
def test_frontmatter_date_raises_value_error_and_nothing_else(value):
    """ValueError only. The docstring claims TypeError is unreachable; a caller
    that catches TypeError would be naming a case this function cannot produce."""
    with pytest.raises(ValueError):
        frontmatter_date(value)


def test_frontmatter_date_never_raises_type_error_on_yaml_output():
    """The exhaustive set of types `yaml.safe_load` can put on a scalar field."""
    for value in [None, True, False, 0, 1, -1, 2.5, "x", [], {}, (),
                  dt.date(2026, 1, 1),
                  dt.datetime(2026, 1, 1, 1, 1)]:   # noqa: DTZ001 - naive is the shape under test
        try:
            frontmatter_date(value)
        except ValueError:
            pass
        except TypeError as exc:  # pragma: no cover - the claim under test
            pytest.fail(f"frontmatter_date({value!r}) raised TypeError: {exc}")


def test_the_old_form_is_what_could_not_read_its_own_input():
    """The defect, through the REAL parser rather than a hand-built value.

    A bare `created:` with a time reaches the reader as a datetime, and the old
    `date.fromisoformat(str(value))` rejects it.
    """
    doc = "---\nid: \"1\"\ncreated: 2026-01-02 09:30:00\n---\n\nbody\n"
    fm, _body = parse_frontmatter(doc)
    assert isinstance(fm["created"], dt.datetime), "fixture no longer exercises the defect"

    with pytest.raises(ValueError):
        dt.date.fromisoformat(str(fm["created"]))          # the old form
    assert frontmatter_date(fm["created"]) == dt.date(2026, 1, 2)   # the new one


# ============================================================
# A2 -- the ratchet: every remaining old-form site is DECLARED
# ============================================================

# `date.fromisoformat(str(x))` and its `[:10]` variant, everywhere they survive
# under scripts/ and .claude/. Each entry states what shape reaches that call, so
# the next author has to answer the question rather than inherit the assumption.
# MEASURED 2026-08-28 by the sweep below: five sites, and the two consolidated by
# this change are gone from the list.
#
# A registry, not a fix list. Two of these are provably safe on their input and
# the other three are latent gaps in files this shard did not open; widening the
# diff to reach them would have cost each one its own measurement.
DECLARED_OLD_FORM_SITES = {
    # The last one, and it must STAY. This hook compares two full TIMESTAMPS
    # (`last_compact_at` against the previous one), so a date-returning coercion
    # would make two compactions on the same day compare equal. Migrating it
    # would be a defect, not a fix. Pinned by
    # tests/test_a_digest_that_read_a_card_the_schema_had_left.py.
    ".claude/hooks/checkpoint-offer.py": "compares timestamps, not dates; a date coercion loses the hour",
    # Three entries left on 2026-08-28 (shard 54): generate-newsletter-html.py,
    # email-intelligence.py and generate-dashboard.py all route through
    # frontmatter_date now. Their measurements are in that shard's test file.
}


# EXACT receiver names, not a substring.
#
# The test used to be `"date" not in ast.unparse(node.func.value)`, which is
# true of any receiver whose text merely CONTAINS "date" -- `candidate`,
# `update`, `my_date_col`. Those are now excluded.
#
# Both `date` and `datetime` stay in scope, and that is deliberate. The
# 2026-08-30 audit shard argued the sweep should be narrowed to `date` alone,
# on the reasoning that `datetime.fromisoformat(str(x))` is the CORRECT
# coercion for a value that may carry a time and so is swept in wrongly. That
# reasoning is half right and the conclusion is wrong: narrowing it was tried
# here and immediately dropped the registry's only entry,
# `.claude/hooks/checkpoint-offer.py:487`, which is
# `datetime.fromisoformat(str(compacted_at))` -- declared, justified, and
# pinned. The registry has always encoded a `str()`-coercion ratchet over BOTH
# receivers, and the entry's own note ("compares timestamps, not dates") is an
# argument for keeping a datetime site, not evidence it was swept in by
# accident. So the docstring below was the narrow half, not the code.
_DATE_RECEIVERS = {
    "date", "dt.date", "datetime.date",
    "datetime", "dt.datetime", "datetime.datetime",
}


def _is_str_coercion(node) -> bool:
    """`str(x)`, or any method chain hanging off one: `str(x).strip()`.

    The chain case is the blind spot that mattered. Only a bare `ast.Name`
    call named `str` used to qualify, so `date.fromisoformat(str(created).strip())`
    -- the most plausible way the defect comes back, since the replacement
    helper itself strips -- had `inner.func` as an `ast.Attribute` and was never
    reported. The ratchet sat green over the banned pattern. This file's own
    `test_the_plain_date_branch_is_a_fast_path_not_a_behaviour` contains exactly
    that shape, which is how the hole was demonstrated.
    """
    import ast
    seen = 0
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        node = node.func.value          # unwrap .strip() / .lower() / ...
        seen += 1
        if seen > 8:                    # pathological chain; stop rather than spin
            return False
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "str")


def _old_form_sites():
    """Every `date`/`datetime`.`fromisoformat(str(...))`, incl. `[:10]` and `.strip()`.

    The docstring used to scope this to `date.fromisoformat` alone while the
    code swept both receivers. The code was right -- see `_DATE_RECEIVERS` --
    and this sentence is the correction.

    Keys are POSIX-relative paths. They used to be `str(path.relative_to(ROOT))`,
    which produces backslash-separated keys on Windows while
    `DECLARED_OLD_FORM_SITES` and the `consolidated` parametrize values are all
    forward-slash. The two sets could never intersect there, so on Windows the
    declared-site tests went false-RED on a clean tree and
    `test_the_three_consolidated_readers_no_longer_carry_the_form` -- which
    asserts `consolidated not in _old_form_sites()` -- passed VACUOUSLY even
    with the banned form restored to all three files. Same `as_posix()` defect
    class this audit pins as a production bug in `check-path-references.py`.
    """
    import ast
    found = {}
    for path in tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's job
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fromisoformat"
                    and node.args):
                continue
            if ast.unparse(node.func.value) not in _DATE_RECEIVERS:
                continue
            arg = node.args[0]
            inner = arg.value if isinstance(arg, ast.Subscript) else arg
            if _is_str_coercion(inner):
                found.setdefault(path.relative_to(ROOT).as_posix(), []).append(node.lineno)
    return found


def test_every_str_coerced_date_parse_is_declared():
    """A new one must be argued for, not inherited.

    The form is what could not read its own input: `yaml.safe_load` produces a
    `datetime` for a bare `created:` with a time, and `str()` of that is a string
    `date.fromisoformat` rejects on Python 3.11.
    """
    found = _old_form_sites()
    undeclared = sorted(set(found) - set(DECLARED_OLD_FORM_SITES))
    assert undeclared == [], (
        "new `date.fromisoformat(str(...))` site(s): "
        + ", ".join(f"{f}:{found[f]}" for f in undeclared)
        + ". Use scripts.utils.markdown.frontmatter_date, or add an entry to "
          "DECLARED_OLD_FORM_SITES stating what shape reaches the call.")


def test_the_registry_does_not_outlive_its_sites():
    """A registry naming files that no longer carry the form passes everything.

    The parser-duplication sweep in
    tests/test_markdown_frontmatter_single_source.py carried three stale entries
    for exactly this reason until 2026-08-28.
    """
    found = _old_form_sites()
    stale = sorted(set(DECLARED_OLD_FORM_SITES) - set(found))
    assert stale == [], f"DECLARED_OLD_FORM_SITES entries with no matching site: {stale}"


@pytest.mark.parametrize("consolidated", [
    "scripts/knowledge-health.py",
    "scripts/odin-brain-health.py",
    "scripts/utils/census_oracles.py",
])
def test_the_three_consolidated_readers_no_longer_carry_the_form(consolidated):
    assert consolidated not in _old_form_sites()


# ============================================================
# B -- the two engines must agree
# ============================================================

def test_both_health_engines_age_the_same_shapes(tmp_path):
    """One table of `created` shapes, both engines, one answer required.

    Each engine reads a different subtree of one knowledge root and applies the
    same rule (`status: seed` plus a `created:` date older than 7 days). Before
    this change they disagreed: the bare date aged in both, the datetime aged in
    only one, and the unreadable value was warned about by only one.
    """
    data = _data_root(tmp_path)
    kd = data / "knowledge"

    shapes = {
        "bare date": "2020-01-01",
        "datetime": "2020-01-03 09:30:00",
        "quoted string": '"2020-01-05"',
    }
    unreadable = "not-a-date"

    for i, (label, created) in enumerate(shapes.items()):
        _write(kd, f"research/2020010100000{i}-zk.md",
               ZK_NOTE.format(nid=f"2020010100000{i}", title=f"zk {label}",
                              ntype="research", created=created))
        _write(kd, f"odin-brain/sources/{i}-brain.md",
               BRAIN_SOURCE.format(nid=str(i), title=f"brain {label}", created=created))
    _write(kd, "research/20200101000099-zk.md",
           ZK_NOTE.format(nid="20200101000099", title="zk unreadable",
                          ntype="research", created=unreadable))
    _write(kd, "odin-brain/sources/99-brain.md",
           BRAIN_SOURCE.format(nid="99", title="brain unreadable", created=unreadable))
    for sub in ("principles", "positions", "episodes", "conflicts", "reference"):
        (kd / "odin-brain" / sub).mkdir(parents=True, exist_ok=True)

    zk = _run_engine("knowledge-health.py", data, "--json")
    brain = _run_engine("odin-brain-health.py", data, "--compile")
    assert zk.returncode == 0, zk.stderr
    assert brain.returncode == 0, brain.stderr

    zk_aged = {s["title"].removeprefix("zk ") for s in json.loads(zk.stdout)["stale_seeds"]}
    brain_aged = {s["title"].removeprefix("brain ")
                  for s in json.loads(brain.stdout)["stale_seeds"]}

    assert zk_aged == set(shapes), f"knowledge-health aged {zk_aged}, wanted {set(shapes)}"
    assert brain_aged == zk_aged, (
        f"the two engines disagree: knowledge-health aged {zk_aged}, "
        f"odin-brain-health aged {brain_aged}")

    # And both SAY what they could not read, rather than dropping it silently.
    for label, proc in (("knowledge-health", zk), ("odin-brain-health", brain)):
        assert "unreadable `created:`" in proc.stderr, f"{label} said nothing"
        assert "not-a-date" in proc.stderr, f"{label} did not name the value"


# ============================================================
# C -- the scan boundary comes from disk
# ============================================================

def _point_at(monkeypatch, root: Path):
    monkeypatch.setattr(kh, "knowledge_root", lambda p=root: p)


def test_a_directory_absent_from_the_order_list_is_still_scanned(monkeypatch, tmp_path):
    """The defect's core: `signal/` on disk, `signals` in the list."""
    (tmp_path / "signal").mkdir()
    (tmp_path / "technology").mkdir()
    _point_at(monkeypatch, tmp_path)
    assert "signal" in kh.note_subdirs()
    assert "signal" not in kh.SUBDIR_ORDER, "fixture no longer exercises the mismatch"


def test_other_schema_directories_are_excluded(monkeypatch, tmp_path):
    for name in ("odin-brain", "shared", "research"):
        (tmp_path / name).mkdir()
    _point_at(monkeypatch, tmp_path)
    assert kh.note_subdirs() == ["research"]


def test_dotted_directories_are_excluded(monkeypatch, tmp_path):
    (tmp_path / ".memory-index").mkdir()
    (tmp_path / "research").mkdir()
    _point_at(monkeypatch, tmp_path)
    assert kh.note_subdirs() == ["research"]


def test_files_at_the_root_are_not_mistaken_for_subdirs(monkeypatch, tmp_path):
    (tmp_path / "INDEX.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "research").mkdir()
    _point_at(monkeypatch, tmp_path)
    assert kh.note_subdirs() == ["research"]


def test_known_names_keep_their_order_and_unknown_ones_follow(monkeypatch, tmp_path):
    for name in ("zulu", "technology", "alpha", "fleeting"):
        (tmp_path / name).mkdir()
    _point_at(monkeypatch, tmp_path)
    # fleeting and technology are 1st and 8th in SUBDIR_ORDER; alpha/zulu are new.
    assert kh.note_subdirs() == ["fleeting", "technology", "alpha", "zulu"]


def test_a_missing_knowledge_root_yields_no_subdirs(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path / "absent")
    assert kh.note_subdirs() == []
    assert kh.unread_note_files() == []


# ============================================================
# D -- the reconciliation names what was left out
# ============================================================

def test_a_note_deeper_than_the_scan_looks_is_reported(monkeypatch, tmp_path):
    _write(tmp_path, "research/top.md", "x\n")
    _write(tmp_path, "research/nested/deep.md", "x\n")
    _point_at(monkeypatch, tmp_path)
    assert [p.name for p in kh.unread_note_files()] == ["deep.md"]


def test_root_level_markdown_is_not_reported_as_unread(monkeypatch, tmp_path):
    (tmp_path / "INDEX.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    _point_at(monkeypatch, tmp_path)
    assert kh.unread_note_files() == []


@pytest.mark.parametrize("owner", ["odin-brain", "shared"])
def test_another_readers_corpus_is_not_reported_as_unread(monkeypatch, tmp_path, owner):
    _write(tmp_path, f"{owner}/sources/a.md", "x\n")
    _write(tmp_path, f"{owner}/b.md", "x\n")
    _point_at(monkeypatch, tmp_path)
    assert kh.unread_note_files() == []


def test_nothing_is_reported_when_everything_was_read(monkeypatch, tmp_path):
    _write(tmp_path, "research/a.md", "x\n")
    _write(tmp_path, "signal/b.md", "x\n")
    _point_at(monkeypatch, tmp_path)
    assert kh.unread_note_files() == []


def test_read_plus_unread_plus_excluded_is_every_markdown_file(monkeypatch, tmp_path):
    """The partition. Without it a file could fall through both sets silently."""
    for rel in ("INDEX.md", "research/a.md", "signal/b.md", "research/nested/c.md",
                "odin-brain/sources/d.md", "shared/signals/e.md", "newdir/f.md"):
        _write(tmp_path, rel, "x\n")
    _point_at(monkeypatch, tmp_path)

    read = {p for _sub, p in kh.scanned_note_files()}
    unread = set(kh.unread_note_files())
    excluded = {
        p for p in tmp_path.rglob("*.md")
        if len(p.relative_to(tmp_path).parts) == 1
        or p.relative_to(tmp_path).parts[0] in kh.OTHER_SCHEMA_DIRS
    }
    assert read & unread == set(), "a file is both read and reported unread"
    assert read | unread | excluded == set(tmp_path.rglob("*.md"))


def test_the_read_set_has_one_definition(monkeypatch, tmp_path):
    """`scan_notes` must iterate the same helper the reconciliation uses.

    A second derivation of the walk is what lets the two drift, and a drifted
    pair reports "nothing unread" over a file it never opened.
    """
    import ast
    src = (ROOT / "scripts" / "knowledge-health.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "scan_notes")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "scanned_note_files" in called, (
        "scan_notes no longer iterates scanned_note_files, so unread_note_files "
        "is reconciling against a walk that nothing guarantees matches")
    globbers = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("glob", "rglob", "iterdir")]
    assert globbers == [], "scan_notes walks the tree itself again"


# ============================================================
# E -- end to end, through the real CLI
# ============================================================

@pytest.fixture
def four_shapes(tmp_path):
    """One note per defect, in a data overlay of invented content."""
    data = _data_root(tmp_path)
    kd = data / "knowledge"
    _write(kd, "technology/20200101000001-a.md",
           ZK_NOTE.format(nid="20200101000001", title="Listed note",
                          ntype="technology", created="2020-01-01"))
    _write(kd, "signal/20200101000002-b.md",
           ZK_NOTE.format(nid="20200101000002", title="Unlisted directory note",
                          ntype="signal", created="2020-01-02"))
    _write(kd, "research/20200101000003-c.md",
           ZK_NOTE.format(nid="20200101000003", title="Datetime created",
                          ntype="research", created="2020-01-03 09:30:00"))
    _write(kd, "strategy/20200101000004-d.md",
           ZK_NOTE.format(nid="20200101000004", title="Broken created",
                          ntype="strategy", created="not-a-date"))
    _write(kd, "research/nested/deep.md",
           ZK_NOTE.format(nid="20200101000005", title="Nested note",
                          ntype="research", created="2020-01-05"))
    (kd / "INDEX.md").write_text("# stub\n", encoding="utf-8")
    _write(kd, "odin-brain/sources/ignored.md", "not a zk note\n")
    return data


def test_the_total_counts_the_unlisted_directory(four_shapes):
    out = json.loads(_run_engine("knowledge-health.py", four_shapes, "--json").stdout)
    assert out["total"] == 4
    assert out["by_type"].get("signal") == 1
    titles = {n["title"] for n in out["notes"]}
    assert "Unlisted directory note" in titles


def test_the_json_states_its_scope(four_shapes):
    out = json.loads(_run_engine("knowledge-health.py", four_shapes, "--json").stdout)
    assert set(out["scanned_subdirs"]) == {"research", "signal", "strategy", "technology"}
    assert [Path(p).name for p in out["unread_files"]] == ["deep.md"]


def test_the_datetime_seed_is_aged_and_the_broken_one_is_named(four_shapes):
    proc = _run_engine("knowledge-health.py", four_shapes, "--json")
    aged = {s["title"] for s in json.loads(proc.stdout)["stale_seeds"]}
    assert aged == {"Listed note", "Unlisted directory note", "Datetime created"}
    assert "Broken created" not in aged
    assert "unreadable `created:`" in proc.stderr
    assert "not-a-date" in proc.stderr


def test_the_terminal_report_names_what_it_did_not_read(four_shapes):
    stdout = _run_engine("knowledge-health.py", four_shapes).stdout
    assert "Not read by this scan (1)" in stdout
    assert "deep.md" in stdout
    assert "Unlisted directory note" in stdout


def test_the_generated_index_agrees_with_its_own_total(four_shapes):
    """The stats table and the All Notes section are one file; they must match.

    Grouping All Notes by a fixed name list meant a note counted in the table
    above could be absent from the section below.
    """
    proc = _run_engine("knowledge-health.py", four_shapes, "--update-index")
    assert proc.returncode == 0, proc.stderr
    index = (four_shapes / "knowledge" / "INDEX.md").read_text(encoding="utf-8")

    total_line = next(ln for ln in index.splitlines() if "| Total notes |" in ln)
    total = int(total_line.split("|")[2].strip())
    listed = [ln for ln in index.splitlines() if ln.startswith("- [")]
    assert total == 4
    assert len(listed) == total, f"table says {total}, All Notes lists {len(listed)}"
    assert any("signal/" in ln for ln in listed)


# ============================================================
# F -- the shared tier had the same defect
# ============================================================

def test_shared_notes_are_not_bounded_by_a_four_name_list(monkeypatch, tmp_path):
    """`scan_shared_notes` carried a list of four names, a subset of the eight.

    A corporate note published into any other directory was counted by neither
    scan, while the report printed "Corporate Shared Knowledge: N notes".
    """
    shared = tmp_path / "shared"
    _write(shared, "decisions/20200101000001-x.md",
           ZK_NOTE.format(nid="20200101000001", title="Shared decision",
                          ntype="decision", created="2020-01-01"))
    _write(shared, "signals/20200101000002-y.md",
           ZK_NOTE.format(nid="20200101000002", title="Shared signal",
                          ntype="signal", created="2020-01-02"))
    monkeypatch.setattr(kh, "shared_knowledge_dir", lambda p=shared: p)
    monkeypatch.setattr(kh, "knowledge_root", lambda p=tmp_path: p)
    titles = {n["title"] for n in kh.scan_shared_notes()}
    assert titles == {"Shared decision", "Shared signal"}


def test_the_shared_tree_is_not_double_counted(monkeypatch, tmp_path):
    """`shared/` sits under the knowledge root on the operator workspace, so the
    main scan must leave it to `scan_shared_notes`."""
    _write(tmp_path, "shared/signals/a.md", "x\n")
    _write(tmp_path, "research/b.md", "x\n")
    _point_at(monkeypatch, tmp_path)
    assert [p.name for p in (p for _s, p in kh.scanned_note_files())] == ["b.md"]


# ============================================================
# A2b -- the sweep's own reach, measured on planted source
# ============================================================

def _sweep_over(source: str, tmp_path, monkeypatch) -> dict:
    """Run `_old_form_sites` over one synthetic file."""
    target = tmp_path / "planted.py"
    target.write_text(source, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "tracked_python_files",
                        lambda: [target], raising=True)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path, raising=True)
    return _old_form_sites()


@pytest.mark.parametrize("call", [
    "date.fromisoformat(str(created))",
    "date.fromisoformat(str(created)[:10])",
    "date.fromisoformat(str(created).strip())",
    "dt.date.fromisoformat(str(created).strip().lower())",
    "datetime.fromisoformat(str(created))",
])
def test_the_sweep_sees_every_shape_of_the_old_form(call, tmp_path, monkeypatch):
    """The case ON the line, and the reason the sweep was widened.

    Only a BARE `str(...)` used to qualify: `_is_str_coercion`'s predecessor
    required `inner.func` to be an `ast.Name` named `str`, so any method chain
    hanging off the coercion -- `.strip()` above all, since the replacement
    helper itself strips -- had an `ast.Attribute` there and was never
    reported. The ratchet was green while the banned pattern was present, in
    the exact shape the defect most plausibly returns as.
    """
    found = _sweep_over(f"x = {call}\n", tmp_path, monkeypatch)
    assert found == {"planted.py": [1]}, f"{call} escaped the sweep"


@pytest.mark.parametrize("call", [
    "candidate.fromisoformat(str(x))",      # receiver merely CONTAINS "date"
    "update.fromisoformat(str(x))",
    "my_date_col.fromisoformat(str(x))",
    "date.fromisoformat(created)",          # no str() coercion at all
    "date.fromisoformat(created.isoformat())",
])
def test_the_sweep_does_not_fire_on_things_that_are_not_the_old_form(
        call, tmp_path, monkeypatch):
    """The other direction. A sweep that flags everything is not a ratchet.

    The receiver test was the substring `"date" in ast.unparse(...)`, so the
    first three of these were swept in as old-form sites and would have forced
    a bogus DECLARED_OLD_FORM_SITES entry apiece.
    """
    assert _sweep_over(f"x = {call}\n", tmp_path, monkeypatch) == {}, \
        f"{call} was flagged and is not the old form"


def test_the_sweep_keys_are_posix_on_every_platform(tmp_path, monkeypatch):
    """Keys are compared against forward-slash literals in two tests and a
    parametrize, so a backslash key can never match. On Windows that made the
    declared-site tests false-red AND made
    `test_the_three_consolidated_readers_no_longer_carry_the_form` -- which
    asserts `consolidated not in _old_form_sites()` -- pass vacuously even with
    the banned form restored to all three files.
    """
    nested = tmp_path / "scripts" / "deep"
    nested.mkdir(parents=True)
    target = nested / "reader.py"
    target.write_text("x = date.fromisoformat(str(created))\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "tracked_python_files",
                        lambda: [target], raising=True)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path, raising=True)

    keys = list(_old_form_sites())
    assert keys == ["scripts/deep/reader.py"], keys
    assert not any("\\" in k for k in keys), keys
