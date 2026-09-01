#!/usr/bin/env python3
"""Shard scripts-09-p3: tools that reported a state they never established.

The name is the pattern. Six of the ten scripts in this shard printed a verdict
wider than the evidence behind it:

  - `offboard-exec` removed a DIRECT collaborator grant on three repos, then
    printed "Offboarding complete" -- while an exec reaching those repos through
    org membership (the common case, and the one where the collaborator endpoint
    returns 404) kept full access. The 404 even printed as a reassuring
    "No access found".
  - `partner-scorecard` painted any stage containing the substring "active"
    GREEN, "Inactive" included, into a file /deal-strategy reads as fact.
  - `pipeline-summary` dropped empty interior table cells, shifting every later
    column, so a deal's value silently vanished from the totals the script
    exists to produce.
  - `osint-advanced-sync` reported locally registered tools as "removed
    upstream" every single run, because it never parsed the sections they live
    in -- and marked a healthy HEAD-refusing server BLOCKED.
  - `output-organizer` moved a file onto an existing one and reported "Moved N
    files" with the destroyed file uncounted.
  - `ops-radar ack` printed an error for an unknown key and exited 0.

Two of those six are NOT pinned below, and saying so is the same discipline the
file is named for (`.claude/rules/scope-claims.md`: name what you left out,
because silence about an exclusion reads as coverage). Both are covered, in
files that own the surrounding surface, and `_DELEGATED` at the foot of this
module holds the check that they still are. Added 2026-09-01, after a shard
reconciling this preamble against the sections found a bullet with no test
here and had to go looking for whether one existed anywhere.

Run: .venv/bin/python -m pytest tests/test_a_report_that_says_more_than_it_checked.py -q
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ob = _load("offboard_exec_p9c", "scripts/offboard-exec.py")
ps = _load("partner_scorecard_p9c", "scripts/partner-scorecard.py")
pl = _load("pipeline_summary_p9c", "scripts/pipeline-summary.py")
oas = _load("osint_advanced_sync_p9c", "scripts/osint-advanced-sync.py")
oo = _load("output_organizer_p9c", "scripts/output-organizer.py")


# ============================================================
# 1 - the offboard verdict is not allowed to overstate itself
# ============================================================
def test_residual_org_access_forbids_the_complete_verdict():
    """The collaborator DELETE cannot reach org membership, so a run that found
    org membership still standing has not completed the offboard."""
    complete, reasons = ob.offboard_verdict(
        revoke_ok=True, preserved=True, residual=["org membership in acme-org"])
    assert complete is False
    assert any("org membership in acme-org" in r for r in reasons)


def test_a_failed_collaborator_removal_forbids_the_complete_verdict():
    complete, reasons = ob.offboard_verdict(
        revoke_ok=False, preserved=True, residual=[])
    assert complete is False
    assert any("collaborator removal failed" in r for r in reasons)


def test_unpreserved_contacts_forbid_the_complete_verdict():
    complete, reasons = ob.offboard_verdict(
        revoke_ok=True, preserved=False, residual=[])
    assert complete is False
    assert any("contacts were not preserved" in r for r in reasons)


def test_a_clean_run_may_still_claim_completion():
    complete, reasons = ob.offboard_verdict(True, True, [])
    assert complete is True
    assert reasons == []


def test_the_manual_checklist_names_the_org_removal_the_script_does_not_do(capsys):
    """The script deliberately does NOT delete org membership -- that mutation is
    wider than anything else here. It must therefore hand the operator the exact
    command, or the gap it just reported has nowhere to go."""
    ob.print_manual_checklist("marlow-carter", {"github_user": "mcarter"})
    out = capsys.readouterr().out
    # Two SEPARATE commands. Asserting only "memberships/mcarter" passed even
    # with the org line deleted, because the team command contains the same
    # substring -- so each is matched on its own full shape.
    assert any("orgs/" in ln and "/memberships/mcarter -X DELETE" in ln
               and "/teams/" not in ln for ln in out.splitlines()), out
    assert any("/teams/$t/memberships/mcarter -X DELETE" in ln
               for ln in out.splitlines()), out


# ============================================================
# 2 - a stage colour is decided on words, not substrings
# ============================================================
@pytest.mark.parametrize("stage,expected", [
    ("Active", "GREEN"),
    ("active", "GREEN"),
    ("Inactive", "--"),
    ("INACTIVE", "--"),
    ("Deactivated", "--"),
    ("Parked", "--"),
    ("Demo/PoC", "GREEN"),
    ("Discussions initiated", "YELLOW"),
])
def test_health_matches_whole_words_only(stage, expected):
    """`"active" in "inactive"` is True, so a partner nobody has spoken to since
    March rendered GREEN into partners.md."""
    assert ps._health(stage) == expected


# ============================================================
# 3 - the generated block is inserted literally, backslashes and all
# ============================================================
def test_a_backslash_in_the_table_is_not_read_as_an_escape():
    """As a `re.sub` replacement STRING, `\\g` and friends in a partner name are
    interpreted; a lone trailing backslash raises re.error outright."""
    partners = f"intro\n{ps.BEGIN}\nold\n{ps.END}\noutro\n"
    table = r"| C:\Users\svc | note |"
    out = ps.splice(partners, table)
    assert r"C:\Users\svc" in out
    assert "intro" in out and "outro" in out


# ============================================================
# 4 - an empty middle cell does not shift the columns left
# ============================================================
def test_an_empty_interior_cell_keeps_later_columns_in_place():
    content = "\n".join([
        "## Active Deals",
        "",
        "| Company | Stage | Est. Value | Close |",
        "|---|---|---|---|",
        "| Acme | | $500K | 2026-01-01 |",
        "",
    ])
    rows = pl.parse_table_rows(content, "Active Deals")
    assert len(rows) == 1
    row = rows[0]
    assert row["Company"] == "Acme"
    assert row["Stage"] == ""
    assert row["Est. Value"] == "$500K", row
    assert row["Close"] == "2026-01-01"


def test_a_fully_populated_row_still_parses():
    content = "\n".join([
        "## Active Deals",
        "",
        "| Company | Stage | Est. Value |",
        "|---|---|---|",
        "| Beta | Pilot | $10K |",
        "",
    ])
    rows = pl.parse_table_rows(content, "Active Deals")
    assert rows == [{"Company": "Beta", "Stage": "Pilot", "Est. Value": "$10K"}]


# ============================================================
# 5 - the removed list only names tools that really left
# ============================================================
def test_a_tool_in_a_skipped_section_is_not_reported_as_removed():
    """SKIP_SECTIONS entries used to be dropped during the parse, so every local
    Telegram/Maritime tool showed up as "removed upstream" on every run."""
    upstream_md = "\n".join([
        "## Telegram Tools",
        "* [TeleWatch](https://telewatch.example) - watches channels",
        "## Company Research",
        "* [OpenCorp](https://opencorp.example) - registries",
    ])
    upstream = oas.extract_upstream_tools(upstream_md)
    local = {
        "https://telewatch.example": {"name": "TeleWatch",
                                      "url": "https://telewatch.example"},
        "https://opencorp.example": {"name": "OpenCorp",
                                     "url": "https://opencorp.example"},
    }
    diff = oas.check_upstream(upstream, local)
    assert [t["name"] for t in diff["removed"]] == []


def test_a_tool_that_really_vanished_is_still_reported_as_removed():
    """The guard above must not silence the signal it exists to protect."""
    upstream = oas.extract_upstream_tools(
        "## Company Research\n* [OpenCorp](https://opencorp.example) - registries\n")
    local = {
        "https://opencorp.example": {"name": "OpenCorp",
                                     "url": "https://opencorp.example"},
        "https://deadtool.example": {"name": "DeadTool",
                                     "url": "https://deadtool.example"},
    }
    diff = oas.check_upstream(upstream, local)
    assert [t["name"] for t in diff["removed"]] == ["DeadTool"]


# ============================================================
# 6 - a URL before any heading is a diagnostic, not a NameError
# ============================================================
def test_a_url_line_before_any_heading_does_not_crash(capsys):
    tools = oas.extract_local_tools("- URL: https://orphan.example\n")
    assert tools == {}
    assert "before any" in capsys.readouterr().out


def test_a_url_line_after_a_heading_is_still_captured():
    tools = oas.extract_local_tools(
        "### OpenCorp\n- URL: https://opencorp.example\n")
    assert tools["https://opencorp.example"]["name"] == "OpenCorp"


# ============================================================
# 7 - upstream text cannot break out of the report it lands in
# ============================================================
def test_a_script_tag_from_upstream_is_escaped_in_the_html_report(tmp_path):
    diff = {"new": [], "removed": [], "local_count": 0,
            "upstream_relevant": 0, "upstream_total": 0}
    validation = [{"name": "<script>alert(1)</script>", "status": "WORKING",
                   "detail": "<img onerror=x>"}]
    path = oas.generate_report_html(diff, validation, tmp_path)
    body = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert "<img onerror=x>" not in body


def test_a_pipe_from_upstream_does_not_break_the_markdown_row(tmp_path):
    diff = {"new": [], "removed": [], "local_count": 0,
            "upstream_relevant": 0, "upstream_total": 0}
    validation = [{"name": "Evil | Tool", "status": "WORKING", "detail": "a|b"}]
    path = oas.generate_report_md(diff, validation, tmp_path)
    row = [ln for ln in path.read_text(encoding="utf-8").splitlines()
           if "Evil" in ln][0]
    # Count only UNESCAPED pipes -- those are the cell separators a renderer
    # honours. Four of them means three cells, which is the header's width.
    import re as _re
    assert len(_re.findall(r"(?<!\\)\|", row)) == 4, row
    assert r"Evil \| Tool" in row


# ============================================================
# 8 - one status-to-colour map, shared
# ============================================================
def test_cli_status_is_the_same_colour_everywhere():
    """validate_all painted CLI cyan; validate-one dropped it into the RED
    else-branch, so one healthy tool showed two different colours."""
    from scripts.utils.colors import CYAN, GREEN, RED, YELLOW
    assert oas._status_colour("CLI") == CYAN
    assert oas._status_colour("WORKING") == GREEN
    assert oas._status_colour("BLOCKED") == YELLOW
    assert oas._status_colour("ERROR") == RED
    assert oas._status_colour("something new") == RED


# ============================================================
# 9 - a housekeeping move never destroys the file already there
# ============================================================
def test_a_colliding_destination_gets_a_free_name(tmp_path):
    dst = tmp_path / "report.md"
    dst.write_text("ORIGINAL", encoding="utf-8")
    free = oo._free_name(dst)
    assert free != dst
    assert not free.exists()
    assert free.name == "report-2.md"
    assert dst.read_text(encoding="utf-8") == "ORIGINAL"


def test_a_free_destination_is_returned_unchanged(tmp_path):
    dst = tmp_path / "report.md"
    assert oo._free_name(dst) == dst


def test_the_second_collision_walks_past_the_first_rename(tmp_path):
    (tmp_path / "report.md").write_text("A", encoding="utf-8")
    (tmp_path / "report-2.md").write_text("B", encoding="utf-8")
    assert oo._free_name(tmp_path / "report.md").name == "report-3.md"


# ============================================================
# 10 - the two findings this file delegates, held to the files that own them
# ============================================================
# The preamble lists six findings and pins four. A pointer to somewhere else is
# only worth anything while it still points at something, and a pointer that has
# rotted is the same defect as the over-wide verdicts above: a sentence claiming
# a state nobody re-established. So the delegation is checked, by AST rather
# than by a substring, because `"def name(" in text` is also satisfied by the
# name appearing in a comment or a longer identifier.

_DELEGATED = {
    "ops-radar ack exits non-zero on an unknown key": (
        "tests/test_a_radar_that_refused_to_silence_its_own_alarm.py",
        "test_an_unknown_key_is_still_refused_with_exit_2",
    ),
    "a HEAD-refusing server is retried with GET, not called BLOCKED": (
        "tests/test_a_guard_with_no_negative_case.py",
        "test_a_head_refusal_retries_with_get_and_reports_working",
    ),
}


@pytest.mark.parametrize("finding", sorted(_DELEGATED))
def test_a_delegated_finding_is_still_covered_where_this_file_says_it_is(finding):
    import ast

    rel, node = _DELEGATED[finding]
    path = ROOT / rel
    assert path.is_file(), f"{rel} is gone; {finding!r} is now covered nowhere"

    defined = {n.name for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert node in defined, (
        f"{rel} no longer defines {node}; this file says {finding!r} is covered "
        f"there and it is not")
