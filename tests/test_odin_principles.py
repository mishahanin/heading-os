"""Tests for the R9 deal-side principle retrieval core (CEO-only).

Covers the (relationship_type, stage) -> domains mapping, internal-type [],
unknown-type fallback, brain-absent [] (exec-shape guard, no raise), the
fabrication floor (every returned slug maps to a real fixture with an actual
keyword intersection), the --keywords passthrough, and deterministic ranking
(intersection size, then confidence, then slug).
"""
from __future__ import annotations

from pathlib import Path

from scripts.utils.odin_principles import (
    INTERNAL_TYPES,
    principles_for_domains,
    relevant_principles_for,
)


def _write_principle(pdir: Path, slug: str, keywords: list[str], confidence: str) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    kw = ", ".join(keywords)
    (pdir / f"{slug}.md").write_text(
        f"---\nid: \"20260101000000\"\ntitle: {slug} title\ntype: principle\n"
        f"sources: [\"s1\"]\nconfidence: {confidence}\nkeywords: [{kw}]\ncreated: 2026-01-01\n---\n"
        f"## Principle\nbody\n",
        encoding="utf-8",
    )


def _brain(tmp_path: Path) -> Path:
    pdir = tmp_path / "odin-brain" / "principles"
    _write_principle(pdir, "p-channel", ["channel", "partnerships"], "medium")
    _write_principle(pdir, "p-partnerships-high", ["partnerships"], "high")
    _write_principle(pdir, "p-negotiation", ["negotiation"], "high")
    _write_principle(pdir, "p-sales", ["sales"], "low")
    _write_principle(pdir, "p-multi", ["negotiation", "persuasion", "sales"], "medium")
    _write_principle(pdir, "p-comm", ["communication"], "medium")
    return tmp_path / "odin-brain"


def test_partner_returns_partnership_channel(tmp_path):
    brain = _brain(tmp_path)
    r = relevant_principles_for("partner", brain_root=brain)
    slugs = [x["slug"] for x in r]
    assert "p-channel" in slugs and "p-partnerships-high" in slugs
    # p-channel matches both partnerships AND channel (intersection 2) -> ranks first
    assert r[0]["slug"] == "p-channel"


def test_prospect_negotiation_surfaces_negotiation(tmp_path):
    brain = _brain(tmp_path)
    r = relevant_principles_for("prospect", "Negotiation", brain_root=brain)
    slugs = [x["slug"] for x in r]
    assert "p-multi" in slugs and "p-negotiation" in slugs
    # p-multi matches negotiation+persuasion+sales (intersection 3) -> ranks first
    assert r[0]["slug"] == "p-multi"


def test_internal_type_returns_empty(tmp_path):
    brain = _brain(tmp_path)
    assert relevant_principles_for("tribe", brain_root=brain) == []
    assert relevant_principles_for("inactive", brain_root=brain) == []


def test_internal_type_returns_empty_whatever_stage_is_passed(tmp_path):
    """The documented fix, which had no witness until 2026-09-01.

    `relevant_principles_for`'s docstring says internal types resolve to []
    "WHATEVER stage is passed", and names the defect: the suppression used to be
    a side effect of the type's domain list being empty, and STAGE_DOMAINS was
    then unioned onto that empty list, so `("tribe", "Negotiation")` returned
    deal-side citations for a Tribe member. The test above passes no stage at
    all, so deleting the `if relationship_type in INTERNAL_TYPES: return []`
    clause left it green: with no stage, an empty domain list produces [] by
    either route. MEASURED: with the clause replaced by `if False:`, this file
    and its neighbours all stayed green.
    """
    brain = _brain(tmp_path)
    for internal in sorted(INTERNAL_TYPES):
        for stage in ("Negotiation", "Proposal", "Demo/POC", "Qualified", "Lead"):
            assert relevant_principles_for(internal, stage, brain_root=brain) == [], (
                f"{internal!r} at stage {stage!r} produced deal-side citations")
    assert INTERNAL_TYPES, "the internal-type set is empty; this test guards nothing"


def test_the_stage_union_is_load_bearing(tmp_path):
    """A stage must be able to ADD a domain the relationship type does not carry.

    Every stage in the file until now was paired with a relationship type whose
    own domains already contained the stage's domain, so `domains +=
    STAGE_DOMAINS.get(stage, [])` could be replaced by `domains += []` with the
    whole file green. A `partner` carries {partnerships, channel} and no
    negotiation domain, so at stage Negotiation the union is the only thing that
    can surface p-negotiation.
    """
    brain = _brain(tmp_path)
    without = {x["slug"] for x in relevant_principles_for("partner", brain_root=brain)}
    with_stage = {x["slug"] for x in relevant_principles_for("partner", "Negotiation",
                                                            brain_root=brain)}
    assert "p-negotiation" not in without
    assert "p-negotiation" in with_stage, (
        "the pipeline stage added no domain; STAGE_DOMAINS is not reaching the query")


def test_unknown_type_falls_to_default(tmp_path):
    brain = _brain(tmp_path)
    r = relevant_principles_for("totally-made-up-type", brain_root=brain)
    # default domains are communication+persuasion -> p-comm matches, never errors
    assert any(x["slug"] == "p-comm" for x in r)


def test_brain_absent_returns_empty_no_raise(tmp_path):
    missing = tmp_path / "no-such-brain"
    assert relevant_principles_for("partner", brain_root=missing) == []
    assert principles_for_domains(["partnerships"], brain_root=missing) == []


def test_fabrication_floor(tmp_path):
    brain = _brain(tmp_path)
    r = relevant_principles_for("partner", brain_root=brain)
    for item in r:
        assert (brain / "principles" / f"{item['slug']}.md").exists()
        assert set(item["keywords"]) & {"partnerships", "channel"}, "returned a non-intersecting principle"
        assert item["matched_domains"]  # non-empty, real intersection


def test_keywords_passthrough(tmp_path):
    brain = _brain(tmp_path)
    r = principles_for_domains(["negotiation"], brain_root=brain)
    slugs = [x["slug"] for x in r]
    assert "p-multi" in slugs and "p-negotiation" in slugs
    assert "p-comm" not in slugs


def test_ranking_confidence_tiebreak(tmp_path):
    brain = _brain(tmp_path)
    # domain {partnerships}: p-partnerships-high (high) and p-channel (medium) both
    # match with intersection size 1 -> high outranks medium.
    r = principles_for_domains(["partnerships"], brain_root=brain)
    assert r[0]["slug"] == "p-partnerships-high"
    assert r[1]["slug"] == "p-channel"


def test_empty_domain_set_returns_empty(tmp_path):
    brain = _brain(tmp_path)
    assert principles_for_domains([], brain_root=brain) == []
