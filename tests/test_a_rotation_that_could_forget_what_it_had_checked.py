"""`scripts/audit-rotation.py` -- the ledger that replaces the campaign.

The 10-day campaign that ended 2026-09-02 ran 144 commits long because the debt
had accumulated unnoticed for months. The fix is not a better campaign; it is a
rotation that never lets a campaign accumulate. Each pass audits a slice and
records the verdict against the artifact's CONTENT HASH.

Three properties carry the whole design, and each has a test here that fails
without it:

* keying on the hash, not the date, so a changed file re-enters the queue by
  itself and a verdict can never describe bytes that are gone;
* deriving the inventory from `git ls-files` on every run, so a new artifact
  enters without anyone maintaining a list;
* refusing to print a coverage number over an inventory too small to be this
  repository, because a percentage over nothing reads as progress.

Every case runs on synthetic entries and a synthetic tree, so the rules have
negative cases. The tree-level tests at the bottom assert the CURRENT state.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = ROOT / "scripts" / "audit-rotation.py"
    spec = importlib.util.spec_from_file_location("audit_rotation", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_rotation"] = module
    spec.loader.exec_module(module)
    return module


rot = _load_module()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ============================================================
# What is in the rotation
# ============================================================

@pytest.mark.parametrize("rel", [
    "scripts/foo.py",
    "scripts/utils/bar.py",
    "scripts/install-x.sh",
    ".claude/hooks/_dispatch.py",
    ".claude/skills/osint/SKILL.md",
    ".claude/rules/security.md",
    ".claude/agents/crm-reader.md",
])
def test_these_artifacts_are_in_the_rotation(rel):
    assert rot.is_auditable(rel) is True


@pytest.mark.parametrize("rel", [
    "tests/test_x.py",
    "docs/ARCHITECTURE.md",
    "README.md",
    "config/routing-map.yaml",
    ".claude/skills/osint/references/sources.md",
    "scripts/templates/systemd/x.service",
])
def test_these_are_out_of_scope_and_named_rather_than_forgotten(rel):
    """The anchor. A predicate that returned True for everything would satisfy
    every case above and put the whole tree in a queue nobody can drain."""
    assert rot.is_auditable(rel) is False


# ============================================================
# The hash is the record
# ============================================================

def test_an_artifact_audited_at_its_current_bytes_is_verified():
    """Until 2026-09-02 this entry carried no `verdict` key and still counted.

    The operator then required that a finding be fixed rather than filed, which
    made the verdict load-bearing: a hash match alone would count an artifact
    whose audit found three defects and closed none. The entry below now says
    what the audit concluded, and `test_an_entry_with_no_verdict_at_all_does_
    not_count` holds the other side.
    """
    current = {"scripts/a.py": _digest("v1")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "date": "2026-09-02",
                                "verdict": "clean"}}
    assert rot.verified(entries, current) == {"scripts/a.py"}


def test_an_artifact_that_changed_since_its_audit_is_not_verified():
    """The property that makes staleness self-detecting. A date-keyed ledger
    would still call this verified while the verdict described bytes that no
    longer exist."""
    current = {"scripts/a.py": _digest("v2")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "date": "2026-09-02"}}
    assert rot.verified(entries, current) == set()


def test_an_entry_with_no_hash_at_all_is_not_verified():
    """Absent and matching are different states. An entry that lost its hash
    must not read as a pass."""
    current = {"scripts/a.py": _digest("v1")}
    assert rot.verified({"scripts/a.py": {"date": "2026-09-02"}}, current) == set()


# ============================================================
# A finding must be fixed, not filed
# ============================================================
#
# Operator instruction, 2026-09-02: an artifact whose audit found defects is not
# a checked artifact. Recording the defect and moving on is the failure this
# rotation exists to prevent, one level up from the code it audits.

def test_an_open_verdict_does_not_count_as_verified():
    """Even with a matching hash. The audit happened; the repair did not."""
    current = {"scripts/a.py": _digest("v1")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "date": "2026-09-02",
                                "verdict": "open",
                                "findings": [{"summary": "x", "severity": "high",
                                              "estimate_minutes": 30}]}}
    assert rot.verified(entries, current) == set()


@pytest.mark.parametrize("verdict", ["clean", "fixed", "not-applicable"])
def test_a_closed_verdict_does_count(verdict):
    """The anchor. A rule that refused every verdict would satisfy the test
    above and leave the coverage number stuck at zero forever."""
    current = {"scripts/a.py": _digest("v1")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "date": "2026-09-02",
                                "verdict": verdict}}
    assert rot.verified(entries, current) == {"scripts/a.py"}


def test_an_entry_with_no_verdict_at_all_does_not_count():
    current = {"scripts/a.py": _digest("v1")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "date": "2026-09-02"}}
    assert rot.verified(entries, current) == set()


def test_open_findings_are_reported():
    current = {"scripts/a.py": _digest("v1")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "verdict": "open",
                                "findings": [{"summary": "boom", "severity": "high",
                                              "estimate_minutes": 30}]}}
    reported = rot.open_findings(entries, current)
    assert [rel for rel, _ in reported] == ["scripts/a.py"]
    assert reported[0][1]["summary"] == "boom"


def test_a_finding_against_bytes_that_changed_is_dropped():
    """The file it described is gone. Reporting it would send the operator to
    read a line that no longer exists, and the artifact is already back in the
    audit queue by the hash rule."""
    current = {"scripts/a.py": _digest("v2")}
    entries = {"scripts/a.py": {"sha256": _digest("v1"), "verdict": "open",
                                "findings": [{"summary": "boom", "severity": "high",
                                              "estimate_minutes": 30}]}}
    assert rot.open_findings(entries, current) == []


def test_findings_are_ordered_by_severity():
    current = {"a.py": _digest("1"), "b.py": _digest("2"), "c.py": _digest("3")}
    entries = {
        "a.py": {"sha256": _digest("1"), "verdict": "open",
                 "findings": [{"summary": "l", "severity": "low", "estimate_minutes": 5}]},
        "b.py": {"sha256": _digest("2"), "verdict": "open",
                 "findings": [{"summary": "h", "severity": "high", "estimate_minutes": 5}]},
        "c.py": {"sha256": _digest("3"), "verdict": "open",
                 "findings": [{"summary": "m", "severity": "medium", "estimate_minutes": 5}]},
    }
    assert [f["severity"] for _, f in rot.open_findings(entries, current)] == [
        "high", "medium", "low"]


def test_a_missing_estimate_counts_zero_rather_than_being_guessed():
    """A guessed number inside a figure the operator schedules around is a
    fabricated specific, which `.claude/rules/scope-claims.md` forbids."""
    assert rot.total_minutes([("a.py", {"summary": "x", "severity": "low"})]) == 0


def test_the_estimate_total_is_the_sum():
    findings = [("a.py", {"estimate_minutes": 30}), ("b.py", {"estimate_minutes": 45})]
    assert rot.total_minutes(findings) == 75


# ============================================================
# Recording a finding
# ============================================================

def test_a_well_formed_finding_parses():
    assert rot.parse_finding("the summary|high|90") == {
        "summary": "the summary", "severity": "high", "estimate_minutes": 90}


@pytest.mark.parametrize("raw,expected", [
    ("only two|high", "expected 'summary|severity|minutes'"),
    ("a|b|c|d", "expected 'summary|severity|minutes'"),
    ("summary|urgent|30", "severity must be one of"),
    ("summary|high|soon", "minutes must be a positive whole number"),
    ("summary|high|0", "minutes must be a positive whole number"),
    ("|high|30", "a finding needs a summary"),
])
def test_a_malformed_finding_is_refused(raw, expected):
    """Matched on the MESSAGE, not on the fact that something raised.

    MEASURED 2026-09-02: with `match=` absent, deleting the field-count check
    SURVIVED mutation. Tuple unpacking raises its own ValueError on a two-field
    or four-field input, so `pytest.raises(ValueError)` was satisfied by an
    accident of Python rather than by the guard. A refusal test that cannot say
    WHY it refused proves the code crashed, not that it checked.
    """
    with pytest.raises(ValueError, match=re.escape(expected)):
        rot.parse_finding(raw)


def test_an_open_verdict_with_no_finding_is_refused(tmp_path, monkeypatch, capsys):
    """A file marked broken with no way to know what to fix is worse than an
    unaudited file: it looks like progress and carries no instruction."""
    root = _tree(tmp_path, 3)
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f0.py", "--verdict", "open"])
    assert rot.main() == 2
    assert "needs at least one --finding" in capsys.readouterr().err


def test_a_finding_beside_a_closed_verdict_is_refused(tmp_path, monkeypatch, capsys):
    """It would never be reported and never be fixed, which is the silent
    swallow this whole change exists to remove."""
    root = _tree(tmp_path, 3)
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f0.py", "--verdict", "clean",
                                      "--finding", "x|low|5"])
    assert rot.main() == 2
    assert "only for --verdict open" in capsys.readouterr().err


def test_an_open_artifact_stays_out_of_the_verified_count_end_to_end(
        tmp_path, monkeypatch):
    root = _tree(tmp_path, 4)
    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", ledger)
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f0.py", "--verdict", "open",
                                      "--finding", "boom|high|20"])
    assert rot.main() == 0

    entries = rot.load_ledger(ledger)
    current = rot.inventory(root)
    assert rot.verified(entries, current) == set()
    assert rot.total_minutes(rot.open_findings(entries, current)) == 20


# ============================================================
# Selection order
# ============================================================

def test_a_never_audited_artifact_comes_first():
    current = {"new.py": _digest("x"), "old.py": _digest("y")}
    entries = {"old.py": {"sha256": _digest("stale"), "date": "2020-01-01"}}
    picked = rot.select(entries, current, 1, seed="fixed")
    assert picked == [("new.py", "never audited")]


def test_a_changed_artifact_is_selected_once_the_new_ones_are_gone():
    current = {"old.py": _digest("y")}
    entries = {"old.py": {"sha256": _digest("stale"), "date": "2020-01-01"}}
    picked = rot.select(entries, current, 3, seed="fixed")
    assert picked == [("old.py", "changed since 2020-01-01")]


def test_a_verified_artifact_is_never_selected():
    """The operator's requirement in one assertion: not the ones already checked
    and fixed. Without it the rotation re-audits the same slice forever and the
    cycle never closes."""
    current = {"done.py": _digest("v1")}
    entries = {"done.py": {"sha256": _digest("v1"), "date": "2026-09-02"}}
    assert rot.select(entries, current, 5, seed="fixed") == []


def test_the_oldest_verdict_is_taken_first_among_changed_artifacts():
    current = {"a.py": _digest("new"), "b.py": _digest("new")}
    entries = {"a.py": {"sha256": _digest("old"), "date": "2026-01-01"},
               "b.py": {"sha256": _digest("old"), "date": "2020-01-01"}}
    picked = rot.select(entries, current, 1, seed="fixed")
    assert picked[0][0] == "b.py"


def test_the_same_seed_gives_the_same_slice():
    current = {f"f{i}.py": _digest(str(i)) for i in range(30)}
    first = rot.select({}, current, 5, seed="2026-09-02")
    second = rot.select({}, current, 5, seed="2026-09-02")
    assert first == second


def test_a_different_seed_gives_a_different_slice():
    """Otherwise every run picks the same artifacts and the rotation does not
    rotate."""
    current = {f"f{i}.py": _digest(str(i)) for i in range(30)}
    assert (rot.select({}, current, 5, seed="2026-09-02")
            != rot.select({}, current, 5, seed="2026-09-09"))


def test_selection_never_returns_more_than_asked():
    current = {f"f{i}.py": _digest(str(i)) for i in range(30)}
    assert len(rot.select({}, current, 4, seed="s")) == 4


# ============================================================
# Refusals
# ============================================================

def test_an_inventory_below_the_floor_refuses(tmp_path, monkeypatch, capsys):
    """A percentage over a handful of files reads as progress while measuring
    nothing. This gate's own defect shape, refused first."""
    monkeypatch.setattr(rot, "ROOT", tmp_path)
    monkeypatch.setattr(rot, "inventory", lambda _root: {"scripts/a.py": "d"})
    monkeypatch.setattr(rot, "load_ledger", lambda _path: {})
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--status"])

    assert rot.main() == 2
    assert "below the floor" in capsys.readouterr().err


def test_a_directory_that_is_not_a_repository_refuses(tmp_path):
    with pytest.raises(rot.Unreadable, match="git ls-files failed"):
        rot.inventory(tmp_path)


def test_a_repository_with_no_auditable_file_refuses(tmp_path):
    """The mirror of the case above. git ran and returned paths; none of them
    are in scope. Reading that as an empty inventory would print 0 of 0."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    with pytest.raises(rot.Unreadable, match="no auditable path"):
        rot.inventory(tmp_path)


def test_a_malformed_ledger_refuses_rather_than_reading_as_empty(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(rot.Unreadable, match="malformed"):
        rot.load_ledger(path)


def test_an_absent_ledger_is_empty_not_an_error(tmp_path):
    """The anchor for the case above: a first run has no ledger, and refusing
    there would mean the tool could never be used the first time."""
    assert rot.load_ledger(tmp_path / "nothing.json") == {}


# ============================================================
# Recording
# ============================================================

def _tree(tmp_path: Path, count: int) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for index in range(count):
        (scripts / f"f{index}.py").write_text(f"# {index}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_recording_writes_the_current_hash(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path, 3)
    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", ledger)
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f0.py", "--verdict", "clean"])

    assert rot.main() == 0
    entry = json.loads(ledger.read_text(encoding="utf-8"))["entries"]["scripts/f0.py"]
    assert entry["sha256"] == hashlib.sha256(b"# 0\n").hexdigest()
    assert entry["verdict"] == "clean"


def test_the_recorded_date_is_the_workspace_day_not_the_host_clock(tmp_path, monkeypatch):
    """A real gap, found by a surviving mutation on 2026-09-02.

    Replacing `_today()` with a hardcoded string survived: nothing asserted the
    recorded date at all. It matters twice. The date orders the changed-artifact
    queue, so a wrong one reorders the rotation; and it seeds the daily shuffle,
    so two machines in different zones would derive different slices for what
    they both call today.

    The expected value is computed here from the workspace timezone rather than
    read back from `_today()`, which would be true of any implementation
    including the constant.
    """
    from datetime import datetime

    from scripts.utils.workspace import get_default_tz

    root = _tree(tmp_path, 3)
    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", ledger)
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f1.py", "--verdict", "clean"])
    assert rot.main() == 0

    expected = datetime.now(get_default_tz()).date().isoformat()
    entry = json.loads(ledger.read_text(encoding="utf-8"))["entries"]["scripts/f1.py"]
    assert entry["date"] == expected


def test_the_day_follows_the_workspace_zone_and_not_the_host(monkeypatch):
    """The test above cannot see a dropped timezone on this machine.

    MEASURED 2026-09-02: mutating `datetime.now(get_default_tz())` to
    `datetime.now()` SURVIVED, because the host's zone and the workspace's agree
    here, so both spell the same day. Equivalent on this host at this hour, and
    wrong on any host where they differ or within the hours around midnight
    where they diverge.

    Forcing two zones a full day apart makes the difference observable without
    depending on where this runs or when.
    """
    from datetime import timedelta, timezone

    far_east = timezone(timedelta(hours=14))
    far_west = timezone(timedelta(hours=-11))

    monkeypatch.setattr(rot, "get_default_tz", lambda: far_east)
    east = rot._today()
    monkeypatch.setattr(rot, "get_default_tz", lambda: far_west)
    west = rot._today()

    assert east != west, (
        "the recorded day is identical 25 hours apart, so the zone is being "
        "ignored and the host clock is what gets written")


def test_recording_a_path_outside_the_inventory_is_refused(tmp_path, monkeypatch, capsys):
    """A verdict about a file the rotation does not cover would inflate nothing
    and confuse everything."""
    root = _tree(tmp_path, 3)
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "docs/README.md", "--verdict", "clean"])

    assert rot.main() == 2
    assert "not an auditable artifact" in capsys.readouterr().err


def test_recording_without_a_verdict_is_refused(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path, 3)
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)
    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f0.py"])

    assert rot.main() == 2
    assert "needs --verdict" in capsys.readouterr().err


def test_the_ledger_is_written_atomically(tmp_path, monkeypatch):
    """`.claude/rules/persistence.md`: a state file is written to a temporary
    path and moved into place, so a kill mid-write cannot leave a half-file that
    the next run reads as a malformed ledger."""
    import inspect

    source = inspect.getsource(rot.save_ledger)
    assert ".replace(" in source and "with_suffix" in source


def test_a_recorded_artifact_leaves_the_queue(tmp_path, monkeypatch):
    """End to end, through the real entry point: record, then select, and the
    recorded path is gone from what comes back."""
    root = _tree(tmp_path, 6)
    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(rot, "ROOT", root)
    monkeypatch.setattr(rot, "LEDGER_PATH", ledger)
    monkeypatch.setattr(rot, "MIN_INVENTORY", 1)

    monkeypatch.setattr(sys, "argv", ["audit-rotation.py", "--record",
                                      "scripts/f3.py", "--verdict", "clean"])
    assert rot.main() == 0

    entries = rot.load_ledger(ledger)
    current = rot.inventory(root)
    picked = [rel for rel, _ in rot.select(entries, current, 10, seed="s")]
    assert "scripts/f3.py" not in picked
    assert len(picked) == 5


# ============================================================
# This tool never starts an audit
# ============================================================

def test_nothing_here_calls_a_model_or_starts_an_audit():
    """The operator asked for a rotation, not a standing campaign. A ledger that
    could start work would start work, so the separation is asserted rather than
    assumed.

    Asked of the AST, never of the text. The first version of this test scanned
    the source for the string `engine-audit` and went red on the module
    docstring, which names that script precisely to explain the boundary. A rule
    that punishes a file for documenting its own trap teaches people to stop
    documenting it, and the campaign that ended 2026-09-02 replaced roughly 49
    such text scans with AST walks for exactly this reason.
    """
    import ast

    tree = ast.parse((ROOT / "scripts" / "audit-rotation.py")
                     .read_text(encoding="utf-8"))

    commands = []
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
            if node.func.attr == "run" and node.args:
                first = node.args[0]
                if isinstance(first, ast.List) and first.elts:
                    head = first.elts[0]
                    if isinstance(head, ast.Constant):
                        commands.append(head.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)

    # `git` was the only entry until 2026-09-02, when the index reader moved
    # into `scripts/utils/repo_files.git_index_paths` and this module stopped
    # spawning anything at all. A subset assertion holds both states: what
    # matters is that nothing else is ever spawned, not that git still is.
    assert set(commands) <= {"git"}, f"this module shells out to: {commands}"

    banned_calls = {"call_model", "complete", "generate"} & called
    assert banned_calls == set(), f"this module calls: {sorted(banned_calls)}"

    banned_imports = sorted(
        name for name in imported
        if any(token in name for token in
               ("proxy_transport", "anthropic", "openai", "model")))
    assert banned_imports == [], f"this module imports: {banned_imports}"


# ============================================================
# The live tree
# ============================================================

def test_the_live_inventory_is_large_enough_to_be_this_repository():
    current = rot.inventory(ROOT)
    assert len(current) >= rot.MIN_INVENTORY, (
        f"inventory holds {len(current)}, below the floor of {rot.MIN_INVENTORY}")


def test_every_ledger_entry_names_a_path_that_is_auditable():
    """A ledger accumulating entries for paths outside the rotation is a ledger
    whose coverage number stops meaning what it says."""
    entries = rot.load_ledger(rot.LEDGER_PATH)
    stray = sorted(rel for rel in entries if not rot.is_auditable(rel))
    assert stray == [], f"ledger entries outside the rotation: {stray}"


def test_every_ledger_entry_carries_a_hash_and_a_known_verdict():
    entries = rot.load_ledger(rot.LEDGER_PATH)
    bad = sorted(rel for rel, entry in entries.items()
                 if not entry.get("sha256") or entry.get("verdict") not in rot.VERDICTS)
    assert bad == [], f"ledger entries with no hash or an unknown verdict: {bad}"
