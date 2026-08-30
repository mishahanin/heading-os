"""Shard scripts-15-p2: the health check's own health.

`scripts/workspace-health.py` is the tool the operator runs to ask "is this
workspace sound?". Ten of its thirteen sections answered a narrower question
than the one they printed.

The sharpest three:

- Outputs inventory globbed the TOP LEVEL and labelled the result "Total files"
  and "Total size". On a tree organised one subdirectory per deliverable it
  reported 1 file / 0.0 MB over 6,140 files / 1.26 GB.
- Reference validation prints "All reference paths resolve to existing files"
  after examining zero paths, because the section of CLAUDE.md it reads has not
  existed for a long time.
- `--section extras` printed "All checks passed." having run one section whose
  own docstring says it is informational and always returns 0.

The rest are the same shape: a directory counted as a file, an unreadable file
counted as synced, a prefix counted as a whole name, a missing document counted
as nothing at all, and an absent token file reported as proof the daemon never
started.

These tests drive the real functions against scratch trees, with two DELIBERATE
exceptions that do read the live workspace:

  - `test_the_live_router_still_covers_every_live_skill` runs
    `check_skill_router_coverage()` against the real `.claude/rules/skill-router.md`
    and `.claude/skills/`, because an orphaned real skill is the regression the
    boundary tightening could plausibly cause and a scratch tree cannot show it.
  - `test_a_single_section_never_claims_all_checks_passed` runs the real
    `check_extras_importability` against the live workspace, so a genuinely
    broken extra in the operator's tree turns it red.

Both are named in `LIVE_TREE_TESTS` below and the pairing is asserted, so
renaming one without revisiting this paragraph fails rather than drifting. This
docstring claimed "None of them asserts on the live workspace's data" until
2026-08-30, which was false of exactly those two -- and the first of them says
so in its own docstring, one screen down.
"""
from __future__ import annotations

import importlib.util
import os
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The two tests the module docstring declares as live-tree readers. Named here
# so the paragraph above cannot quietly stop being true.
LIVE_TREE_TESTS = (
    "test_the_live_router_still_covers_every_live_skill",
    "test_a_single_section_never_claims_all_checks_passed",
)

# `os.chmod` on Windows honours only the read-only bit: 0o777, 0o644 and 0o600
# cannot be set as distinct modes, so the permission tests below would fail
# against correct production code. The sibling shard
# `test_a_harness_that_took_the_machine_with_it.py` already uses this idiom.
posix_only = pytest.mark.skipif(
    os.name != "posix", reason="chmod mode bits are not honoured on Windows")


@pytest.fixture
def wh():
    """A fresh module object per test: it binds directories at import time."""
    spec = importlib.util.spec_from_file_location(
        "workspace_health_under_test", ROOT / "scripts" / "workspace-health.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["workspace_health_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _plain(captured: str) -> str:
    """Output without ANSI colour, so assertions read the words not the escapes."""
    return re.sub(r"\x1b\[[0-9;]*m", "", captured)


def test_the_module_docstring_names_the_live_tree_readers_that_exist() -> None:
    """The docstring's exception list, held to the file it describes.

    NEW 2026-08-30, with the docstring correction it guards. The paragraph used
    to say "None of them asserts on the live workspace's data" while two tests
    did, one of them saying so in its own docstring. A prose correction that
    nothing checks is the next drift, so both directions are pinned: each named
    test must still be defined under that name, and the docstring must still
    name each of them.
    """
    defined = {name for name in globals() if name.startswith("test_")}
    doc = __doc__ or ""
    for name in LIVE_TREE_TESTS:
        assert name in defined, (
            f"{name} was renamed or removed; the module docstring still cites it")
        assert name in doc, f"{name} reads the live tree and the docstring omits it"


# ============================================================
# Outputs inventory -- "Total" must mean total
# ============================================================

def test_the_outputs_total_counts_the_whole_tree(wh, tmp_path, monkeypatch, capsys):
    """One loose file and three nested ones. The old glob saw one."""
    (tmp_path / "loose.md").write_text("x" * 100)
    nested = tmp_path / "deal" / "acme"
    nested.mkdir(parents=True)
    for i in range(3):
        (nested / f"doc{i}.md").write_text("y" * 1000)
    monkeypatch.setattr(wh, "OUTPUTS_DIR", tmp_path)

    wh.check_outputs_inventory()

    out = _plain(capsys.readouterr().out)
    assert "Total files (recursive): 4" in out
    # Bytes as well as count: the old top-level glob summed 100 bytes and
    # called it the total. 3 KB is the nested three; 0 KB would be the bug.
    assert ".md: 4 files (3 KB)" in out


def test_the_organise_nag_fires_on_loose_files_not_on_the_whole_tree(
        wh, tmp_path, monkeypatch, capsys):
    """"Consider organizing into subdirectories" is advice about clutter at the
    top level. Firing it on the recursive count would scold a tidy tree of
    thousands of files forever, which is the tree the operator convention
    produces."""
    for i in range(40):
        (tmp_path / f"sub{i}").mkdir()
        (tmp_path / f"sub{i}" / "a.md").write_text("x")
    monkeypatch.setattr(wh, "OUTPUTS_DIR", tmp_path)

    issues = wh.check_outputs_inventory()

    out = _plain(capsys.readouterr().out)
    assert "Total files (recursive): 40" in out
    assert "consider organizing" not in out
    assert issues == 0


def test_the_organise_nag_still_fires_on_real_clutter(wh, tmp_path, monkeypatch, capsys):
    """The fix must not have removed the warning it re-scoped."""
    for i in range(31):
        (tmp_path / f"f{i}.md").write_text("x")
    monkeypatch.setattr(wh, "OUTPUTS_DIR", tmp_path)

    issues = wh.check_outputs_inventory()

    assert "31 loose files at the top level" in _plain(capsys.readouterr().out)
    assert issues == 1


def test_a_file_that_vanishes_mid_walk_does_not_abort_the_section(
        wh, tmp_path, monkeypatch, capsys):
    """A recursive walk over a live outputs tree races with whatever writes it.
    An unstattable entry must cost its bytes, not the whole inventory.

    A dangling symlink does NOT reach this guard -- `is_file()` drops it before
    any `stat()` -- so the race is simulated where it actually happens: the file
    passes the filter and is gone by the time its size is read.
    """
    (tmp_path / "gone.md").write_text("z" * 50)
    (tmp_path / "real.md").write_text("z" * 50)
    monkeypatch.setattr(wh, "OUTPUTS_DIR", tmp_path)

    real_stat = Path.stat
    seen = {"gone.md": 0}

    def _stat(self, *a, **k):
        # `is_file()` stats too, so the first call must succeed: the window this
        # guard exists for is the one between the filter and the size read.
        if self.name in seen:
            seen[self.name] += 1
            if seen[self.name] > 1:
                raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _stat)

    issues = wh.check_outputs_inventory()

    out = _plain(capsys.readouterr().out)
    assert issues == 0
    # Counted (it was there when the walk saw it), its bytes skipped.
    assert "Total files (recursive): 2" in out
    assert ".md: 2 files (0 KB)" in out


# ============================================================
# Reference validation -- retired 2026-08-30, see below
# ============================================================
#
# Three tests stood here. They monkeypatched `wh.CLAUDE_MD` and drove the
# `## Reference Resources` heading scan, and all three passed for months while
# measuring nothing that existed: that heading is in no file in either repo, and
# `git log -S "Reference Resources" -- CLAUDE.md` finds it was never there. The
# check was a survivor of the pre-split single workspace, and these tests kept
# its fixture alive rather than its subject.
#
# On 2026-08-30 the check was pointed at the reference index that does exist,
# `<data-root>/reference/workspace-overview.md`, and it now examines 697 paths.
# `wh.CLAUDE_MD` is gone, so these three could not be repaired in place; the
# fixture they patched no longer has anything to patch.
#
# Their one real claim, that a run examining zero paths must never read as a
# pass, is carried forward by
# `tests/test_a_reference_check_that_verified_nothing_for_months.py`, along with
# the absent-overlay case they never had.
#
# ============================================================
# DataStore -- a directory is not a document
# ============================================================

def test_a_datastore_subdir_of_folders_reports_its_documents(wh, tmp_path, monkeypatch, capsys):
    """`brand/` held five subfolders and 192 documents, and was reported as
    "5 file(s)" -- neither number."""
    (tmp_path / "INDEX.md").write_text("index")
    for d in ("brand", "content", "corporate", "events", "intelligence",
              "investment", "operations", "products"):
        (tmp_path / d).mkdir()
    for i in range(5):
        sub = tmp_path / "brand" / f"kit{i}"
        sub.mkdir()
        (sub / "logo.svg").write_text("<svg/>")
    monkeypatch.setattr(wh, "DATASTORE_DIR", tmp_path)

    wh.check_datastore()

    out = _plain(capsys.readouterr().out)
    assert "brand/: 5 document(s)" in out


def test_a_subdir_holding_only_empty_folders_is_reported_empty(wh, tmp_path, monkeypatch, capsys):
    """It used to escape the "awaiting documents" warning by counting its own
    empty children as files."""
    (tmp_path / "INDEX.md").write_text("index")
    for d in ("brand", "content", "corporate", "events", "intelligence",
              "investment", "operations", "products"):
        (tmp_path / d).mkdir()
    (tmp_path / "events" / "placeholder").mkdir()
    (tmp_path / "brand" / "a.svg").write_text("x")
    monkeypatch.setattr(wh, "DATASTORE_DIR", tmp_path)

    wh.check_datastore()

    assert "events/: empty - awaiting documents" in _plain(capsys.readouterr().out)


# ============================================================
# Docs sync -- an unreadable copy is not a synced copy
# ============================================================

def test_an_unreadable_docs_copy_counts_as_an_issue(wh, tmp_path, monkeypatch, capsys):
    """It was a WARN that did not count, so a run where every comparison failed
    to read still returned 0 and the summary read "All checks passed."."""
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    names = ["GETTING-STARTED.md", "GETTING-STARTED.html",
             "CEO-ADMIN-GUIDE.md", "CEO-ADMIN-GUIDE.html",
             "EMERGENCY-PROCEDURES.md", "EMERGENCY-PROCEDURES.html"]
    for n in names:
        (templates / n).write_text("same")
        (docs / n).write_text("same")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)
    monkeypatch.setattr(wh, "_docs_path", lambda name: docs / name)

    real_read = Path.read_bytes

    def _read(self, *a, **k):
        if "docs" in self.parts:
            raise OSError(13, "Permission denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", _read)

    issues = wh.check_docs_sync()

    out = _plain(capsys.readouterr().out)
    assert issues == len(names), "every unverifiable comparison must count"
    assert "sync NOT verified" in out


def test_a_readable_matching_pair_still_passes(wh, tmp_path, monkeypatch, capsys):
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    for n in ["GETTING-STARTED.md", "GETTING-STARTED.html",
              "CEO-ADMIN-GUIDE.md", "CEO-ADMIN-GUIDE.html",
              "EMERGENCY-PROCEDURES.md", "EMERGENCY-PROCEDURES.html"]:
        (templates / n).write_text("same")
        (docs / n).write_text("same")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)
    monkeypatch.setattr(wh, "_docs_path", lambda name: docs / name)

    assert wh.check_docs_sync() == 0
    assert "synced" in _plain(capsys.readouterr().out)


# ============================================================
# Skill router -- a prefix is not a name
# ============================================================

def test_a_skill_whose_name_prefixes_another_is_not_covered_by_it(
        wh, tmp_path, monkeypatch, capsys):
    """`/osint` matched inside `/osint-advanced`, and `/queue` inside
    `/queue-draft`. Both pairs are live in this repo, so for those names the
    check could not detect the orphaning it advertises."""
    router = tmp_path / ".claude" / "rules"
    router.mkdir(parents=True)
    (router / "skill-router.md").write_text(
        "| `/osint-advanced` | never auto-trigger |\n"
        "| `/queue-draft` | explicit only |\n")
    skills = tmp_path / ".claude" / "skills"
    for name in ("osint", "osint-advanced", "queue", "queue-draft"):
        (skills / name).mkdir(parents=True)
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    issues = wh.check_skill_router_coverage()

    out = _plain(capsys.readouterr().out)
    assert issues == 2, "osint and queue are orphaned; only the long names are listed"
    assert "osint: not mentioned in skill-router.md" in out
    assert "queue: not mentioned in skill-router.md" in out
    assert "osint-advanced: mentioned" in out


def test_a_genuinely_listed_skill_still_passes(wh, tmp_path, monkeypatch):
    router = tmp_path / ".claude" / "rules"
    router.mkdir(parents=True)
    (router / "skill-router.md").write_text("| `/osint` | investigate |\n")
    (tmp_path / ".claude" / "skills" / "osint").mkdir(parents=True)
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    assert wh.check_skill_router_coverage() == 0


def test_the_live_router_still_covers_every_live_skill(wh):
    """The boundary match must not have orphaned a real skill. This one does
    read the live tree on purpose: it is the regression the tightening could
    plausibly cause."""
    assert wh.check_skill_router_coverage() == 0


# ============================================================
# Doc versions -- absent is not the same as fresh
# ============================================================

def test_a_missing_shared_template_counts_as_an_issue(wh, tmp_path, monkeypatch, capsys):
    """It WARNed and `continue`d, so an entirely missing template set returned
    0 and the run exited 0 with "All checks passed."."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "GETTING-STARTED.md").write_text(
        "<!-- version: 1.0.0 | last-updated: 2026-08-20 -->\n")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)

    issues = wh.check_doc_versions()

    out = _plain(capsys.readouterr().out)
    assert issues == 3, "three of the four tracked templates are absent"
    assert "missing from templates/" in out


def test_an_absent_templates_tree_is_a_note_not_three_failures(wh, tmp_path, monkeypatch, capsys):
    """A bare public engine clone has no data overlay. That is a legitimate
    state, and must not read as a broken sync set."""
    monkeypatch.setattr(wh, "get_templates_dir", lambda: tmp_path / "nope")

    issues = wh.check_doc_versions()

    assert issues == 0
    assert "0 docs version-checked" in _plain(capsys.readouterr().out)


def test_a_stale_marker_is_counted_out_loud_without_blocking(wh, tmp_path, monkeypatch, capsys):
    """Staleness stays advisory -- it is a refresh signal, not a defect -- but
    it may not be silent either."""
    templates = tmp_path / "templates"
    templates.mkdir()
    for n in ("GETTING-STARTED.md", "CEO-ADMIN-GUIDE.md",
              "EMERGENCY-PROCEDURES.md", "CLAUDE.md.template"):
        (templates / n).write_text("<!-- version: 1.0.0 | last-updated: 2020-01-01 -->\n")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)

    issues = wh.check_doc_versions()

    out = _plain(capsys.readouterr().out)
    assert issues == 0
    assert "4 of 4 shared doc(s) past the 90-day threshold" in out


def test_the_doc_version_docstring_states_the_scope_it_has(wh):
    """It claimed "every file in templates/ AND docs/", including .html. It
    opens four .md/.template files under templates/ and nothing else."""
    doc = wh.check_doc_versions.__doc__
    assert "four" in doc
    assert "opens nothing" in doc


# ============================================================
# Daemon token -- absence attributes nothing
# ============================================================

def test_an_absent_token_is_not_evidence_the_daemon_never_started(
        wh, tmp_path, monkeypatch, capsys):
    """The old line printed OK and blamed a cause no input to the function
    could establish."""
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    issues = wh.check_daemon_token_perms()

    out = _plain(capsys.readouterr().out)
    assert issues == 0
    assert "daemon never started)" not in out
    assert "permissions NOT checked" in out
    assert "cannot tell which" in out


@posix_only
def test_a_world_writable_state_dir_is_flagged_even_with_no_token(
        wh, tmp_path, monkeypatch, capsys):
    """The parent check sat after the early return, so it never ran in the
    state this workspace is actually in."""
    state = tmp_path / ".daemon-state"
    state.mkdir()
    state.chmod(0o777)
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    issues = wh.check_daemon_token_perms()

    assert issues == 1
    assert "world-writable" in _plain(capsys.readouterr().out)


@posix_only
def test_a_loose_token_is_still_flagged(wh, tmp_path, monkeypatch, capsys):
    state = tmp_path / ".daemon-state"
    state.mkdir()
    token = state / "token"
    token.write_text("secret-placeholder")
    token.chmod(0o644)
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    assert wh.check_daemon_token_perms() == 1
    assert "expected 0o600" in _plain(capsys.readouterr().out)


@posix_only
def test_a_correct_token_passes(wh, tmp_path, monkeypatch, capsys):
    state = tmp_path / ".daemon-state"
    state.mkdir()
    state.chmod(0o700)
    token = state / "token"
    token.write_text("secret-placeholder")
    token.chmod(stat.S_IRUSR | stat.S_IWUSR)
    monkeypatch.setattr(wh, "WORKSPACE", tmp_path)

    assert wh.check_daemon_token_perms() == 0
    assert "is 0600" in _plain(capsys.readouterr().out)


# ============================================================
# People -- say what the count counted
# ============================================================

def test_a_tbd_outside_an_email_column_is_not_called_a_missing_email(
        wh, tmp_path, monkeypatch, capsys):
    """The count is a whole-file substring count with no column context."""
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "people.md").write_text(
        "| Name | Role | Email |\n|---|---|---|\n| A | TBD | a@example.com |\n")
    monkeypatch.setattr(wh, "CONTEXT_DIR", ctx)

    wh.check_people_completeness()

    out = _plain(capsys.readouterr().out)
    assert "missing email/contact entries" not in out
    assert "placeholder(s) anywhere in people.md" in out


# ============================================================
# The summary -- one section is not "all checks"
# ============================================================

def _run_main(wh, monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", *argv])
    with pytest.raises(SystemExit) as exc:
        wh.main()
    return exc.value.code, _plain(capsys.readouterr().out)


def test_a_single_section_never_claims_all_checks_passed(wh, monkeypatch, capsys):
    """`--section extras` ran one informational check that always returns 0, and
    then printed the sentence a full clean run prints."""
    code, out = _run_main(wh, monkeypatch, capsys, ["--section", "extras"])

    assert code == 0
    assert "All checks passed." not in out
    assert "Section 'extras' passed." in out
    assert "did not run" in out


def test_a_single_failing_section_says_what_did_not_run(wh, monkeypatch, capsys):
    monkeypatch.setitem(wh.__dict__, "check_extras_importability", lambda: 3)
    code, out = _run_main(wh, monkeypatch, capsys, ["--section", "extras"])

    assert code == 1
    assert "3 issue(s) found in section 'extras'" in out
    assert "the other 12 section(s) did not run" in out


def test_a_full_clean_run_still_says_all_checks_passed(wh, monkeypatch, capsys):
    """The wording a full pass earns must survive: 13 sections, all green."""
    # `check_context_freshness` is called with max_days, the rest with nothing.
    for name in list(wh.__dict__):
        if name.startswith("check_"):
            monkeypatch.setitem(wh.__dict__, name, lambda *a, **k: 0)
    code, out = _run_main(wh, monkeypatch, capsys, [])

    assert code == 0
    assert "All 13 checks passed." in out
