#!/usr/bin/env python3
"""The engine's prose never names an engine path that does not exist.

Documentation rot is silent: a script is renamed, the prose that names it keeps
pointing at nothing, and the next reader pastes a command that fails. The
2026-08-21 sweep found eight such sites accumulated over months -- among them a
`/odin` ingest command with a dead path and a `docs/SECURITY-MODEL.md` paragraph
describing two hook files deleted in ba1affd.

This asserts the gate that scripts/check-path-references.py enforces, plus the
two properties that keep the gate honest: it must actually detect a dangling
path, and it must NOT flag a path that lives in the private overlay (absent on a
public clone, so its absence is not evidence -- see .claude/rules/scope-claims.md).
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

_SRC = get_workspace_root() / "scripts" / "check-path-references.py"
_spec = importlib.util.spec_from_file_location("check_path_references", _SRC)
cpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpr)


def test_engine_prose_names_no_missing_engine_path():
    """The real gate: nothing dangling beyond the frozen baseline."""
    # The corpus floor, and it is not decoration. MEASURED 2026-09-01 by pointing
    # `git ls-files` at a glob that matches nothing: `tracked_markdown` returns
    # [], `scan` returns {}, and this assertion passes over zero files. It was
    # caught only by `test_baseline_carries_no_entry_that_is_already_clean`
    # below, which needs BASELINE to be non-empty -- and BASELINE is a ratchet
    # that only shrinks, so on the day it reaches zero both tests go blind
    # together and nothing says so. Measured 341 tracked .md files on
    # 2026-09-01; the floor only catches a collapse.
    corpus = cpr.tracked_markdown(get_workspace_root())
    assert len(corpus) >= 250, (
        f"only {len(corpus)} tracked Markdown file(s) were scanned; the corpus "
        "collapsed and this gate is reading almost nothing"
    )
    found = cpr.scan(get_workspace_root())
    new = {p: sites for p, sites in found.items() if p not in cpr.BASELINE}
    assert not new, (
        "engine prose names path(s) that do not exist:\n"
        + "\n".join(
            f"  {p}  ({', '.join(f'{r}:{n}' for r, n in sites[:3])})"
            for p, sites in sorted(new.items())
        )
        + "\nFix the path, or add it to BASELINE with the reason it should not exist."
    )


def test_detector_flags_a_planted_dangling_path(tmp_path, monkeypatch):
    """A regex that matches nothing would pass everything. Prove it still bites."""
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["planted.md"])
    (tmp_path / "planted.md").write_text(
        "Run `python scripts/definitely-not-a-real-script.py --now` to do the thing.\n",
        encoding="utf-8",
    )
    found = cpr.scan(tmp_path)
    assert "scripts/definitely-not-a-real-script.py" in found


def test_detector_skips_a_path_that_routes_to_the_overlay(tmp_path, monkeypatch):
    """A private-overlay path is absent on a public clone; absence is not rot."""
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["planted.md"])
    (tmp_path / "planted.md").write_text(
        "Voice guide: `reference/misha-voice.md`.\n", encoding="utf-8"
    )
    found = cpr.scan(tmp_path)
    assert "reference/misha-voice.md" not in found


def test_detector_skips_a_path_that_gitignore_covers(tmp_path, monkeypatch):
    """Runtime state is on the operator's disk and in no clone; naming it is correct.

    This is the 2026-08-21 CI break: `reference/scheduled-tasks.md` names
    `.claude/scheduled_tasks.json`, which exists locally and never in a clone, so
    the run failed on the runner while passing on the operator's machine. BASELINE
    cannot hold such a path -- it reads stale in one place and dangling in the other.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".claude/scheduled_tasks.json\n", encoding="utf-8")
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["planted.md"])
    (tmp_path / "planted.md").write_text(
        "Active tasks live in `.claude/scheduled_tasks.json`.\n", encoding="utf-8"
    )
    found = cpr.scan(tmp_path)
    assert ".claude/scheduled_tasks.json" not in found


def test_gitignored_filter_stays_quiet_outside_a_git_repo(tmp_path):
    """No repo means no evidence; filter nothing rather than hide a real dangling path."""
    assert cpr.gitignored(tmp_path, ["scripts/whatever.py"]) == set()


def test_every_baseline_entry_states_a_reason():
    """An entry without a reason is indistinguishable from rot someone gave up on."""
    unexplained = [p for p, reason in cpr.BASELINE.items() if not (reason or "").strip()]
    assert not unexplained, f"BASELINE entries missing a reason: {unexplained}"


def test_baseline_carries_no_entry_that_is_already_clean():
    """The ratchet only shrinks; a stale entry hides a path that could be re-broken."""
    found = cpr.scan(get_workspace_root())
    stale = sorted(p for p in cpr.BASELINE if p not in found)
    assert not stale, (
        "BASELINE lists path(s) the prose no longer names -- drop them:\n"
        + "\n".join(f"  {p}" for p in stale)
    )


# --- coverage: which engine code no prose describes -------------------------
#
# Phase 3 of the semantic-index spec asked for a persisted (prose_file, line,
# code_path) edge table in the DATA store. Measured before building it, that table
# would hold 28,067 rows, 59% of them from `outputs/` and `plans/` -- handoff
# summaries that MENTION a path rather than document it -- and would answer
# nothing cheaper things do not: a point lookup is `grep -rn` at 0.33 s, and the
# aggregate answer is a 57-line list computed in 0.6 s from the extraction this
# file already tests. So Phase 3 ships as a report, and the operator approved the
# reduction on 2026-08-21 on condition it was proved.
#
# Three properties carry the report's honesty and are tested rather than assumed.

def test_a_file_named_only_in_an_archive_still_counts_as_undocumented(tmp_path, monkeypatch):
    """A handoff summary quoting a filename does not document that file.

    Without this exclusion the report reads "everything is documented", because
    `outputs/` quotes nearly every path in the tree at some point.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "lonely.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(cpr, "tracked_markdown",
                        lambda root: ["outputs/operations/handoff.md", "docs/real.md"])
    (tmp_path / "outputs" / "operations").mkdir(parents=True)
    (tmp_path / "outputs" / "operations" / "handoff.md").write_text(
        "Ran `scripts/lonely.py` during the session.\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "real.md").write_text("Nothing relevant here.\n", encoding="utf-8")

    cov = cpr.coverage(tmp_path, None)
    assert "scripts/lonely.py" in cov["undocumented"]


def test_real_documentation_clears_a_file(tmp_path, monkeypatch):
    """The other half: prose outside the archive trees DOES count."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "described.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["docs/EXTENDING.md"])
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "EXTENDING.md").write_text(
        "Run `scripts/described.py` to do the thing.\n", encoding="utf-8")

    assert cpr.coverage(tmp_path, None)["undocumented"] == []


def test_package_markers_are_dropped_and_the_drop_is_counted(tmp_path, monkeypatch):
    """A narrowed check that prints like a complete one is the defect
    `.claude/rules/scope-claims.md` exists to stop. Report what was dropped."""
    (tmp_path / "scripts" / "pkg").mkdir(parents=True)
    (tmp_path / "scripts" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "pkg" / "real.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: [])

    cov = cpr.coverage(tmp_path, None)
    assert cov["package_markers_skipped"] == 1
    assert cov["undocumented"] == ["scripts/pkg/real.py"]
    assert cov["code_files"] == 1


def test_the_report_says_whether_it_saw_the_overlay(tmp_path, monkeypatch):
    """Absence of the overlay NARROWS the claim; it must not read as coverage.

    On a public clone a file documented only in the private overlay reads as
    undocumented, and a caller cannot tell unless the report says so.
    """
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: [])
    assert cpr.coverage(tmp_path, None)["overlay_scanned"] is False
    assert cpr.coverage(tmp_path, tmp_path)["overlay_scanned"] is True
