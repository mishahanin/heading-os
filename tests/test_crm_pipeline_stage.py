"""Tests for pipeline.md parsing and stage-aware cadence."""

from pathlib import Path

import pytest


@pytest.fixture
def pipeline_workspace(tmp_path):
    """Create a fake context/pipeline.md and crm/aliases.md."""
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "pipeline.md").write_text(
        "# Pipeline\n\n"
        "| Company | Stage | Owner |\n"
        "|---|---|---|\n"
        "| ExampleTelco | Demo/POC | Alex |\n"
        "| ExampleTelco UAE | Qualified | Misha |\n"
        "| PartnerCo | Negotiation | Alex |\n"
        "| Acme Corp | Won | Misha |\n"
        # The parenthetical form the parser's docstring is written about, and
        # which no row carried until 2026-09-01. See
        # test_a_contact_parenthetical_is_stripped_from_the_company_key.
        "| Meridian Freight (Alba Karimova) | Proposal | Misha |\n"
        "\n"
        # Prose after the table, then a stray pipe-prefixed line. The parser is
        # supposed to leave the table at the first non-`|` line; without that it
        # keeps ingesting and the line below becomes a pipeline row. See
        # test_a_pipe_line_after_the_table_ends_is_not_a_pipeline_row.
        "Notes on the quarter follow.\n"
        "\n"
        "| NotAPipelineRow | Negotiation | nobody |\n",
        encoding="utf-8",
    )
    (tmp_path / "crm").mkdir()
    (tmp_path / "crm" / "aliases.md").write_text(
        "## Aliases\n\n"
        "### ExampleTelco\n"
        "- ExampleTelco A.Ş.\n"
        "- TT\n\n"
        "### AllianceCo\n"
        "- AllianceCo\n",
        encoding="utf-8",
    )
    return tmp_path


def test_parse_pipeline_stages_returns_company_to_stage_map(pipeline_workspace):
    from scripts.utils.crm import parse_pipeline_stages
    stages = parse_pipeline_stages(pipeline_workspace / "context" / "pipeline.md")
    assert stages["exampletelco"] == "Demo/POC"
    assert stages["exampletelco uae"] == "Qualified"
    assert stages["partnerco"] == "Negotiation"
    assert stages["acme corp"] == "Won"


def test_a_contact_parenthetical_is_stripped_from_the_company_key(pipeline_workspace):
    """`re.sub(r"\\s*\\([^)]*\\)\\s*$", "", company)` -- documented, unmeasured.

    `parse_pipeline_stages`' docstring names this behaviour and gives the worked
    example, and until 2026-09-01 no fixture row contained a parenthesis, so
    deleting the substitution left this file and all 32 other files naming
    `scripts.utils.crm` green (measured). The key it produces is what
    `crm/aliases.md` and `compute_stage_aware_cadence` both match against, so an
    unstripped key silently drops that company's stage-aware cadence: the
    contact keeps its type default and nobody is told the pipeline was consulted
    and missed.
    """
    from scripts.utils.crm import parse_pipeline_stages
    stages = parse_pipeline_stages(pipeline_workspace / "context" / "pipeline.md")
    assert stages["meridian freight"] == "Proposal"
    assert "meridian freight (alba karimova)" not in stages


def test_a_pipe_line_after_the_table_ends_is_not_a_pipeline_row(pipeline_workspace):
    """The `in_table = False` arm, which nothing had ever driven.

    Every fixture ran the table to end-of-file, so the parser never had to
    NOTICE the table ending. Measured 2026-09-01: replacing `in_table = False`
    with `pass` left the whole neighbourhood green while every later
    pipe-prefixed line in the file -- a second unrelated table, a quoted example,
    a formatting sketch -- was ingested as a live pipeline row. A phantom row
    carrying a Won or Lost stage sets that company's cadence to 0, which stops
    tracking it entirely; carrying Negotiation it sets 3 days and floods the
    radar. Both are silent.
    """
    from scripts.utils.crm import parse_pipeline_stages
    stages = parse_pipeline_stages(pipeline_workspace / "context" / "pipeline.md")
    assert "notapipelinerow" not in stages, (
        f"a `|`-prefixed line below the table's end was parsed as a pipeline "
        f"row; parsed keys were {sorted(stages)}"
    )
    # Floor: the rows BEFORE the break must still be there, so this cannot pass
    # by the parser having stopped too early or read nothing at all.
    assert len(stages) == 5, sorted(stages)


def test_compute_stage_aware_cadence_stage_match(pipeline_workspace):
    from scripts.utils.crm import compute_stage_aware_cadence
    stages = {"exampletelco": "Demo/POC", "acme corp": "Negotiation"}
    aliases = {}
    cad = compute_stage_aware_cadence(
        relationship_type="prospect",
        pipeline_company="ExampleTelco",
        stages=stages,
        aliases=aliases,
        type_default=14,
    )
    assert cad == 7  # Demo/POC


def test_compute_stage_aware_cadence_falls_back_to_type_default(pipeline_workspace):
    from scripts.utils.crm import compute_stage_aware_cadence
    cad = compute_stage_aware_cadence(
        relationship_type="prospect",
        pipeline_company="Unknown Company",
        stages={},
        aliases={},
        type_default=14,
    )
    assert cad == 14


def test_compute_stage_aware_cadence_resolves_alias(pipeline_workspace):
    from scripts.utils.crm import compute_stage_aware_cadence, parse_aliases
    aliases = parse_aliases(pipeline_workspace / "crm" / "aliases.md")
    stages = {"exampletelco": "Negotiation"}
    cad = compute_stage_aware_cadence(
        relationship_type="prospect",
        pipeline_company="ExampleTelco A.Ş.",
        stages=stages,
        aliases=aliases,
        type_default=14,
    )
    assert cad == 3  # Negotiation resolved via alias


def test_the_raw_company_name_is_tried_when_the_alias_hop_lands_nowhere(pipeline_workspace):
    """The SECOND lookup in the chain, which no fixture could distinguish.

    `compute_stage_aware_cadence` looks the stage up twice: once through the
    alias table, then once on the unaliased name. Every case in this file had
    the two lookups agree -- either `aliases` was empty (so canonical IS the raw
    name) or the alias hop hit -- so the fallback could be deleted outright and
    stay green, measured 2026-09-01 across 32 files.

    The case that separates them is the ordinary one: `crm/aliases.md` maps a
    variant to a canonical name, and `context/pipeline.md` happens to list the
    VARIANT. The alias hop then resolves to a key the pipeline does not carry,
    and only the fallback finds the stage that is plainly there. Without it the
    contact silently drops to its type default -- 14 days for a prospect sitting
    in Negotiation, which the table prices at 3.
    """
    from scripts.utils.crm import compute_stage_aware_cadence
    cad = compute_stage_aware_cadence(
        relationship_type="prospect",
        pipeline_company="ExampleTelco A.Ş.",
        stages={"exampletelco a.ş.": "Negotiation"},   # the pipeline lists the variant
        aliases={"exampletelco a.ş.": "exampletelco"},  # the alias points elsewhere
        type_default=14,
    )
    assert cad == 3, (
        "the alias hop missed and the exact-name fallback did not run, so a "
        "Negotiation-stage contact was handed its 14-day type default"
    )


def test_compute_stage_aware_cadence_won_lost_returns_zero(pipeline_workspace):
    from scripts.utils.crm import compute_stage_aware_cadence
    stages = {"acme corp": "Won"}
    cad = compute_stage_aware_cadence(
        relationship_type="customer",
        pipeline_company="Acme Corp",
        stages=stages,
        aliases={},
        type_default=14,
    )
    assert cad == 0  # no tracking


def test_compute_stage_aware_cadence_unknown_stage_falls_back(pipeline_workspace):
    """When the pipeline contains a stage string not in STAGE_CADENCE, fall back
    to type_default rather than crashing."""
    from scripts.utils.crm import compute_stage_aware_cadence
    stages = {"acme corp": "Declined"}  # not in STAGE_CADENCE
    cad = compute_stage_aware_cadence(
        relationship_type="investor-active",
        pipeline_company="Acme Corp",
        stages=stages,
        aliases={},
        type_default=30,
    )
    assert cad == 30  # falls back to type_default
