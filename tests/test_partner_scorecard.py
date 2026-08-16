"""Partner scorecard generated from the pipeline, never hand-maintained.

The scorecard in `context/partners.md` was hand-written and drifted: on
2026-08-17 it carried six partners against twenty-three partnership rows in
`context/pipeline.md`, and recorded an executed worldwide OEM agreement as
"In Discussion" eighty days after signature. Hand-maintaining a second copy of
a list is how that happens, so the summary table is generated from the pipeline
the way `pipeline-summary.py` already generates the stage counts.

Run: python3 -m pytest tests/test_partner_scorecard.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "partner-scorecard.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("partner_scorecard_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PIPELINE = """\
## Partnership Discussions

| Partner | Topic | Stage | Priority | Stage Date | Next Action | Notes |
|---------|-------|-------|----------|------------|-------------|-------|
| Acme Group | CIS expansion | Active | High | 2026-05-20 | Enable sales | Anchor |
| Globex (Jane Roe) | OEM partnership | Strategic asset - agreement executed | High | 2026-05-29 | Confirm owner | Executed |
| Initech | Reseller channel | Post-MWC | Medium | 2026-03-05 | Follow-up | Cold |

---

## Won / Closed
"""

PARTNERS = """\
> Last verified: 2026-05-27
# Partner Ecosystem

## Partner Scorecard Summary

<!-- BEGIN GENERATED SCORECARD -->
stale content nobody updated
<!-- END GENERATED SCORECARD -->

---

## Detailed Partner Profiles
"""


def test_every_pipeline_partnership_reaches_the_scorecard(mod):
    """Coverage is the whole point: six rows against twenty-three was the defect."""
    rows = mod.parse_partnerships(PIPELINE)
    assert len(rows) == 3
    table = mod.render_scorecard(rows)
    for name in ("Acme Group", "Globex", "Initech"):
        assert name in table, f"{name} missing from the generated scorecard"


def test_the_partner_name_drops_its_parenthetical_contact(mod):
    """`Globex (Jane Roe)` is one partner, not a partner called "(Jane Roe)"."""
    rows = mod.parse_partnerships(PIPELINE)
    names = [r["partner"] for r in rows]
    assert "Globex" in names
    assert not any("(" in n for n in names)


def test_an_executed_agreement_is_not_rendered_as_a_discussion(mod):
    """The HPE defect in one assertion.

    `partners.md` carried a signed worldwide OEM agreement as "In Discussion"
    for eighty days. The generator reads the pipeline's Stage cell, so a stage
    that says the agreement is executed can never render as a discussion.
    """
    rows = mod.parse_partnerships(PIPELINE)
    globex = next(r for r in rows if r["partner"] == "Globex")
    assert "executed" in globex["stage"].lower()
    assert "discussion" not in globex["stage"].lower()


def test_generation_replaces_only_the_marked_block(mod):
    """Everything a human wrote outside the markers must survive verbatim."""
    out = mod.splice(PARTNERS, mod.render_scorecard(mod.parse_partnerships(PIPELINE)))
    assert "stale content nobody updated" not in out
    assert "# Partner Ecosystem" in out
    assert "## Detailed Partner Profiles" in out
    assert "> Last verified: 2026-05-27" in out
    assert out.count("<!-- BEGIN GENERATED SCORECARD -->") == 1
    assert out.count("<!-- END GENERATED SCORECARD -->") == 1


def test_generation_is_idempotent(mod):
    """A second run over its own output must change nothing.

    Otherwise `--check` reports drift against itself and the gate is useless.
    """
    once = mod.splice(PARTNERS, mod.render_scorecard(mod.parse_partnerships(PIPELINE)))
    twice = mod.splice(once, mod.render_scorecard(mod.parse_partnerships(PIPELINE)))
    assert once == twice


def test_missing_markers_are_an_error_not_a_silent_no_op(mod):
    """A file without the markers must fail loudly.

    A generator that silently writes nothing is indistinguishable from one that
    ran and found no changes, which is how a stale table survives a green run.
    """
    with pytest.raises(ValueError, match="marker"):
        mod.splice("# Partner Ecosystem\n\nno markers here\n", "| x |\n")
