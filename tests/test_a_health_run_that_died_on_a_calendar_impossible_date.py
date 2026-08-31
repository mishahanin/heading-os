"""Four ways `scripts/workspace-health.py` said more, or less, than it measured.

Covers the k3 audit shards `scripts-15-p4` (findings 4 and 7) and
`scripts-16-p1` (findings 4 and 5).

*A date shaped right and impossible.* `check_context_freshness` matched
`(\\d{4}-\\d{2}-\\d{2})`, which validates digit shape and not the calendar, then
handed the string to `strptime`. `> Last verified: 2026-02-31` in any context
file raised ValueError, and `main` runs the sections in an unguarded loop, so
one bad marker aborted every later section with a traceback and no summary, in
front of `/push-updates`. The sibling `check_doc_versions` already wrapped the
identical parse.

*A remediation stricter than the rule.* `check_doc_versions` searches the first
THREE lines for the version marker and told the operator it belonged "on line
1". A compliant marker on line 2 passes silently while the failure message
names a contract nothing enforces.

*A section detector that was a substring test.* `check_reference_validation`
flipped into "in the Reference Resources table" on any line containing that
phrase, prose included, and stayed there until the next `## `. Every following
table row with a backticked dotted token was then existence-checked and failed
the run over paths nobody claimed were references. SUPERSEDED 2026-08-30: the
check no longer reads `CLAUDE.md`, and the heading it anchored to exists in no
file in either repo. Its two tests were retired in place, with the reasoning
kept where they stood.

*A title promising a comparison nobody wrote.* `check_agent_counts` printed
under "Agent Count Verification" and its docstring said "compare to CLAUDE.md".
No line in it opens CLAUDE.md.

Everything here runs against `tmp_path`; the module's globals are monkeypatched
per test so the operator's live tree is never read or written.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "workspace-health.py"


@pytest.fixture
def wh():
    """A fresh module per test: these checks read module-level globals."""
    spec = importlib.util.spec_from_file_location(
        "workspace_health_mod", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# check_context_freshness - a calendar-impossible date
# ============================================================

def test_a_calendar_impossible_date_does_not_kill_the_run(wh, tmp_path,
                                                          monkeypatch):
    """The measured crash: `2026-02-31` reached strptime and raised ValueError."""
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "strategy.md").write_text(
        "# Strategy\n> Last verified: 2026-02-31\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)

    assert list(ctx.glob("*.md")), "empty corpus proves nothing"

    # No exception, and the unparseable marker is an issue rather than silence.
    assert wh.check_context_freshness(30) == 1


def test_the_malformed_date_is_named_not_swallowed(wh, tmp_path, monkeypatch,
                                                   capsys):
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "pipeline.md").write_text(
        "# Pipeline\n> Last verified: 2026-13-01\n", encoding="utf-8")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)

    wh.check_context_freshness(30)
    out = capsys.readouterr()
    body = out.out + out.err

    assert "2026-13-01" in body
    assert "pipeline.md" in body
    assert "NOT checked" in body


def test_a_valid_date_is_still_measured(wh, tmp_path, monkeypatch, capsys):
    """The negative direction: the guard must not swallow real freshness.

    The date is PINNED, and the pin's property is asserted rather than assumed:
    it is far enough in the past that any host clock at or after 2026-01-01
    reads it as stale. A test that took today's date would pass or fail by the
    day it ran on.
    """
    from datetime import date

    pinned = "2020-01-01"
    assert (date(2026, 1, 1) - date.fromisoformat(pinned)).days > 30

    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "people.md").write_text(
        f"# People\n> Last verified: {pinned}\n", encoding="utf-8")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)

    assert wh.check_context_freshness(30) == 1
    body = capsys.readouterr().out
    assert pinned in body
    assert "malformed" not in body


# ============================================================
# check_doc_versions - the remediation names the window it searched
# ============================================================

def _templates(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    for name in ("GETTING-STARTED.md", "CEO-ADMIN-GUIDE.md",
                 "EMERGENCY-PROCEDURES.md", "CLAUDE.md.template"):
        (d / name).write_text(
            "<!-- version: 1.0.0 | last-updated: 2026-08-01 -->\n# T\n",
            encoding="utf-8")
    return d


def test_the_missing_marker_message_names_the_window_it_searched(
        wh, tmp_path, monkeypatch, capsys):
    templates = _templates(tmp_path)
    (templates / "GETTING-STARTED.md").write_text("# No marker here\n",
                                                  encoding="utf-8")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)

    assert wh.check_doc_versions(90) == 1
    body = capsys.readouterr().out

    assert "first 3 lines" in body
    assert "on line 1" not in body, (
        "the message names a stricter contract than the search window")


def test_a_marker_on_line_two_passes_and_says_so(wh, tmp_path, monkeypatch,
                                                 capsys):
    """The behaviour the message must describe: line 2 is accepted."""
    templates = _templates(tmp_path)
    (templates / "GETTING-STARTED.md").write_text(
        "# Title\n<!-- version: 2.1.0 | last-updated: 2026-08-01 -->\n",
        encoding="utf-8")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)

    assert wh.check_doc_versions(90) == 0
    assert "v2.1.0" in capsys.readouterr().out


# ============================================================
# check_reference_validation - retired 2026-08-30
# ============================================================
#
# Two tests stood here, pinning that the section flag opened on a markdown
# HEADING and not on any prose sentence carrying the phrase. The finding behind
# them was real when written: a sentence flipped the flag and an unrelated table
# row was existence-checked and failed the run.
#
# It cannot recur, and not because the anchoring was kept. On 2026-08-30 the
# check stopped reading `CLAUDE.md` at all. It reads
# `<data-root>/reference/workspace-overview.md`, the index that actually holds
# the paths, and the WHOLE file is the reference section, so there is no heading
# to anchor to and no flag to flip. `wh.CLAUDE_MD` no longer exists, so these
# two could not be repaired in place.
#
# The reasoning is not lost: it is recorded in the new docstring of
# `check_reference_validation`, together with why it does not apply any more.
# The live behaviour is covered by
# `tests/test_a_reference_check_that_verified_nothing_for_months.py`.
#
# ============================================================
# check_agent_counts - the title matches the method
# ============================================================

def test_the_agent_count_section_does_not_promise_a_comparison(wh):
    """No CLAUDE.md comparison happens, so neither the docstring nor the printed
    header may claim one.

    Asserted over the parsed function, not a grep of the file, and only over the
    docstring's SUMMARY line. The body of that docstring quotes the wrong claim
    on purpose, to record what was fixed; a rule that punished it for saying so
    would teach the next author to delete the explanation.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "check_agent_counts")

    summary = (ast.get_docstring(fn) or "").splitlines()[0].lower()
    reads_claude_md = any(
        isinstance(n, ast.Name) and n.id == "CLAUDE_MD"
        for n in ast.walk(fn))

    assert not reads_claude_md, (
        "the function now reads CLAUDE.md; the docstring may claim a comparison "
        "again, and this test should assert the comparison instead")
    assert "compare" not in summary, summary
    assert "claude.md" not in summary, summary

    header_args = [n.args[0].value for n in ast.walk(fn)
                   if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "header"
                   and n.args and isinstance(n.args[0], ast.Constant)]
    assert header_args, "the section prints no header"
    assert "verification" not in header_args[0].lower()


def test_the_agent_count_section_still_counts_and_still_flags(wh, tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """The negative direction: renaming the header must not empty the check."""
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "prime.md").write_text("x\n", encoding="utf-8")
    skills = tmp_path / ".claude" / "skills"
    (skills / "good").mkdir(parents=True)
    (skills / "good" / "SKILL.md").write_text("x\n", encoding="utf-8")
    (skills / "sloppy").mkdir(parents=True)
    (skills / "sloppy" / "skill.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(wh, "COMMANDS_DIR", commands)
    monkeypatch.setattr(wh, "SKILLS_DIR", skills)

    assert wh.check_agent_counts() == 1
    body = capsys.readouterr().out
    assert "Commands found: 1" in body
    assert "Skills found: 2" in body
    assert "sloppy" in body
