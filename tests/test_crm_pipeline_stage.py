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
        "| Acme Corp | Won | Misha |\n",
        encoding="utf-8",
    )
    (tmp_path / "crm").mkdir()
    (tmp_path / "crm" / "aliases.md").write_text(
        "## Aliases\n\n"
        "### ExampleTelco\n"
        "- Türk Telekom A.Ş.\n"
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
        pipeline_company="Türk Telekom A.Ş.",
        stages=stages,
        aliases=aliases,
        type_default=14,
    )
    assert cad == 3  # Negotiation resolved via alias


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
