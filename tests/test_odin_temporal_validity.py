"""Tests for R11 -- Odin brain temporal-validity lint (superseded_by convention).

Synthetic temp brains, no real brain files touched. Covers: valid supersession
(clean), dangling reference (error), circular chain (error), orphan-superseded
(warn), position-referenced superseded note (no warn), valid_until-only (clean),
and the exec-workspace no-brain case.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.odin_brain_lint import (  # noqa: E402
    collect_brain_files,
    check_dangling_references,
    check_circular_chains,
    check_orphan_superseded,
    lint,
    run_all_checks,
)


def _write(brain: Path, subdir: str, slug: str, fm: dict, body: str = "Body.") -> None:
    d = brain / subdir
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f'{k}: "{v}"')
    lines.append("---")
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n\n" + body + "\n", encoding="utf-8")


def _base(slug_title, **extra):
    fm = {
        "id": extra.pop("id", "20260401100000"),
        "title": slug_title,
        "type": "principle",
        "sources": extra.pop("sources", ["s1"]),
        "confidence": "high",
        "keywords": ["domain"],
        "created": "2026-04-01",
        "updated": "2026-04-01",
    }
    fm.update(extra)
    return fm


def test_valid_supersession_with_citation(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "old-principle",
           _base("Old", id="20260401100000", superseded_by="new-principle", superseded_date="2026-04-15"))
    _write(brain, "principles", "new-principle",
           _base("New", id="20260415100000", sources=["s1", "20260401100000"]))
    assert lint(brain) == []


def test_dangling_superseded_by(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "orphaned",
           _base("Orphaned", superseded_by="does-not-exist", superseded_date="2026-04-15"))
    fb, _id, slug = collect_brain_files(brain)
    issues = check_dangling_references(fb, slug)
    assert len(issues) == 1
    assert issues[0]["check"] == "dangling_reference"
    assert "does-not-exist" in issues[0]["message"]


def test_circular_chain(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "p-a", _base("A", id="1", superseded_by="p-b"))
    _write(brain, "principles", "p-b", _base("B", id="2", superseded_by="p-c"))
    _write(brain, "principles", "p-c", _base("C", id="3", superseded_by="p-a"))
    fb, _id, _slug = collect_brain_files(brain)
    issues = check_circular_chains(fb)
    assert any(i["check"] == "circular_chain" for i in issues)
    # the cycle is reported once, not once per start node
    assert len([i for i in issues if i["check"] == "circular_chain"]) == 1


def test_orphan_superseded_warns(tmp_path):
    brain = tmp_path / "odin-brain"
    # superseded, successor exists but does NOT cite it, no position references it
    _write(brain, "principles", "lonely-old",
           _base("LonelyOld", id="20260401100000", superseded_by="fresh", superseded_date="2026-04-15"))
    _write(brain, "principles", "fresh", _base("Fresh", id="20260415100000", sources=["s1"]))
    issues = lint(brain)
    assert [i for i in issues if i["check"] == "orphan_superseded"], "expected an orphan-superseded warning"
    assert not [i for i in issues if i["severity"] == "error"], "no errors expected (successor exists)"


def test_superseded_but_in_position_no_warn(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "old-but-cited",
           _base("OldButCited", id="20260401100000", superseded_by="fresh", superseded_date="2026-04-15"))
    _write(brain, "principles", "fresh", _base("Fresh", id="20260415100000", sources=["s1"]))
    # a position references the old principle by id -> not an orphan
    _write(brain, "positions", "stance",
           {"id": "20260410100000", "title": "Stance", "type": "position",
            "principles": ["20260401100000"], "sources": ["s1"], "confidence": "high",
            "keywords": ["domain"], "created": "2026-04-10", "updated": "2026-04-10",
            "revisit_when": "conditions change"})
    fb, _id, _slug = collect_brain_files(brain)
    assert check_orphan_superseded(fb) == []


def test_valid_until_only_is_clean(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "positions", "time-bound",
           {"id": "20260401100000", "title": "Time-bound", "type": "position",
            "principles": ["p1"], "sources": ["s1"], "confidence": "high",
            "keywords": ["domain"], "created": "2026-04-01", "updated": "2026-04-01",
            "revisit_when": "Q3 review", "valid_until": "2026-09-30"})
    assert lint(brain) == []


def test_no_brain_is_not_an_error(tmp_path):
    # exec workspace: brain dir does not exist -> run_all_checks returns 0
    assert run_all_checks(brain_root=tmp_path / "nonexistent", json_output=True) == 0


def test_wikilink_to_existing_note_is_clean(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "alpha", _base("Alpha", id="20260401100000"),
           body="See [[beta]] and [[20260402100000|Beta by id]] for the related idea.")
    _write(brain, "principles", "beta", _base("Beta", id="20260402100000"))
    assert [i for i in lint(brain) if i["check"] == "dangling_wikilink"] == []


def test_dangling_wikilink_warns_not_errors(tmp_path):
    brain = tmp_path / "odin-brain"
    _write(brain, "principles", "alpha", _base("Alpha"),
           body="Links to [[nonexistent-note]] which has no target yet.")
    issues = lint(brain)
    dangling = [i for i in issues if i["check"] == "dangling_wikilink"]
    assert len(dangling) == 1
    assert dangling[0]["severity"] == "warn"
    assert dangling[0]["target"] == "nonexistent-note"
    # markers are warnings, never errors -- they must not fail the gate
    assert [i for i in issues if i["severity"] == "error"] == []
