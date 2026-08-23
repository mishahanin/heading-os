"""Every routable skill has a row in the skill graph, and the check is mechanical.

Found by the 2026-08-23 audit. `reference/skill-graph.md` states the rule in its
own editing section — "One row per routable skill. When a skill is added,
re-scoped, or retired in `skill-router.md`, update its row here too" — and
nothing enforced it. Measured: the router listed 96 skills, the CSV carried 85,
and 11 routable skills had no row at all:

    canopus, census, deep-research-advance, memory-hygiene, pencil-export,
    promote-corporate, queue, queue-draft, radar, recall, rollback-corporate
    (the last two skills were removed on 2026-08-23; the count is historical)

`/next` reasons entirely over that CSV. A skill with no row cannot be
recommended, and cannot be reached by the recency signal either — the
`produces_in` reverse index is the only path from a recently-touched file back
to the skill that wrote it. So `/next` was silently blind to the action queue,
the ops radar, recall, and the census, while `skill-graph.md` carried a
`Last Updated` date and an editing rule that read like a guarantee.

A rule a document states about itself is a wish. This is the check.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ROUTER = ROOT / ".claude" / "rules" / "skill-router.md"
GRAPH = ROOT / "reference" / "skill-graph.csv"

# The router's generated registry rows open with the skill's slash command. An
# arg-hint may follow inside the same backtick span (`/scrutinize [target] ...`),
# so the name stops at the first character that is not part of a slug.
_ROUTER_ROW = re.compile(r"^\| `/([a-z0-9-]+)", re.M)

PHASES = {"intel", "comms", "content", "crm", "design", "strategy", "operations"}


def _router_skills() -> set[str]:
    return set(_ROUTER_ROW.findall(ROUTER.read_text(encoding="utf-8")))


def _graph_rows() -> list[dict]:
    return list(csv.DictReader(GRAPH.read_text(encoding="utf-8").splitlines()))


def test_every_router_skill_has_a_graph_row():
    missing = sorted(_router_skills() - {r["skill"].strip() for r in _graph_rows()})
    assert missing == [], (
        "routable skills with no row in reference/skill-graph.csv, so /next can "
        f"neither recommend them nor map their outputs back to them: {missing}"
    )


def test_the_graph_has_no_rows_for_skills_the_router_dropped():
    """The other direction. A retired skill left behind is a dead recommendation."""
    extra = sorted({r["skill"].strip() for r in _graph_rows()} - _router_skills())
    assert extra == [], (
        f"skill-graph.csv rows with no matching router entry: {extra}"
    )


def test_the_detector_is_not_vacuous():
    """Both sides must actually be parsed; a regex that matches nothing passes."""
    skills = _router_skills()
    rows = _graph_rows()
    assert len(skills) > 50, f"the router parse found only {len(skills)} skills"
    assert len(rows) > 50, f"the graph parse found only {len(rows)} rows"
    assert "osint" in skills and "osint" in {r["skill"] for r in rows}


def test_every_phase_is_one_the_document_defines():
    """`skill-graph.md` lists seven buckets; a typo would silently orphan a row."""
    bad = sorted({r["skill"]: r["phase"] for r in _graph_rows()}.items())
    offenders = [(s, p) for s, p in bad if p.strip() not in PHASES]
    assert offenders == [], f"rows with a phase outside {sorted(PHASES)}: {offenders}"


def test_every_edge_points_at_a_skill_that_exists():
    """A `followed_by` naming a skill with no row is a recommendation into nothing."""
    rows = _graph_rows()
    known = {r["skill"].strip() for r in rows}
    dangling = set()
    for row in rows:
        for column in ("preceded_by", "followed_by"):
            for edge in (row[column] or "").split("|"):
                edge = edge.strip()
                if edge and edge not in known:
                    dangling.add(f"{row['skill']}.{column} -> {edge}")
    assert dangling == set(), f"edges pointing at unknown skills: {sorted(dangling)}"
