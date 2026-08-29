"""Stage-aware CRM cadence read the engine clone and found nothing.

`scan_contacts` loads two files to make cadence depend on where a deal stands:
`context/pipeline.md` for the stage and `crm/aliases.md` to map a contact's
company onto a pipeline row. Both are operator DATA and live in the private
overlay. The code resolved them against `_WORKSPACE_ROOT`, a module constant
computed from `__file__` at import, which is the ENGINE clone root. The comment
beside it called that "the canonical workspace root when called in production".

On the split engine/data topology it is not, and the paths simply do not exist.
Measured 2026-08-29 against the operator's real tree:

    module _WORKSPACE_ROOT: <engine clone>
      engine context/pipeline.md exists: False    data: True
      engine crm/aliases.md      exists: False    data: True
      stages parsed from the ENGINE path: 0       from the DATA path: 29
      aliases from the ENGINE path: 0             from the DATA path: 61
      stage histogram: Qualified 10, Demo/POC 9, Lead 6, Negotiation 3, Parked 1
      pipeline rows whose stage maps to a STAGE_CADENCE entry: 28

So `parse_pipeline_stages` and `parse_aliases` both returned `{}`, every contact
fell back to its relationship-type default, and stage-aware cadence had never
once applied in production. Nine Demo/POC accounts that should be contacted
every 7 days and three in Negotiation that should be every 3 all sat on the
14-day default, so they turned yellow and red days late. `STAGE_CADENCE["Won"]`
is 0, the stop-tracking signal, and it was unreachable from the pipeline side,
so closed accounts kept accruing red debt and feeding `/cold-sweep`'s outreach
drafting. It fires on every `/crm`, `/dashboard` and `/cold-sweep` run, with no
triggering file needed.

`census_oracles.py` reads the same two files through its corpus paths and was
right all along. This was the second copy.

Why the existing tests did not catch it, which is the more useful half:

- `tests/test_crm_pipeline_stage.py` calls `parse_pipeline_stages`,
  `parse_aliases` and `compute_stage_aware_cadence` directly with fixture paths.
  The parser is proven; its WIRING is not.
- `tests/test_crm_entity_helpers.py` does drive `scan_contacts`, and passes
  `workspace_root=fake_workspace`, which is the exact argument that bypasses the
  frozen constant.
- `tests/test_a_handle_the_leak_gate_could_not_spell.py` makes eight calls that
  DO hit the broken default and pass anyway: their fixtures carry no pipeline
  file, and their expected health values were written against the empty-map
  behaviour. The baseline they pin is the defect.

Every test here therefore calls `scan_contacts` with NO `workspace_root`, which
is how production calls it (`crm-health.py:286`, `generate-dashboard.py:170`,
`email-intelligence.py:769`).

The guard that should have caught this statically, and its own gap, is in
`tests/test_data_root_no_bypass.py`.
"""
from pathlib import Path

import pytest

from scripts.utils.crm import parse_config, scan_contacts

TODAY = __import__("datetime").date(2026, 8, 29)

# 10 days since the last touch. Under the 14-day type default that is still
# green; under Demo/POC's 7 it is overdue. One number separates the two worlds.
LAST_TOUCH = "2026-08-19"


def _overlay(root: Path, *, with_pipeline: bool = True,
             with_aliases: bool = True) -> Path:
    """A data overlay shaped like the operator's, holding one Demo/POC contact."""
    (root / "context").mkdir(parents=True, exist_ok=True)
    (root / "crm" / "contacts").mkdir(parents=True, exist_ok=True)
    (root / "crm" / "address-book").mkdir(parents=True, exist_ok=True)

    if with_pipeline:
        (root / "context" / "pipeline.md").write_text(
            "# Pipeline\n\n"
            "| Company | Stage | Owner |\n"
            "|---|---|---|\n"
            "| Northwind Telecom | Demo/POC | Alex |\n"
            "| Harbour Systems | Won | Alex |\n",
            encoding="utf-8")
    if with_aliases:
        (root / "crm" / "aliases.md").write_text(
            "## Aliases\n\n"
            "### Northwind Telecom\n"
            "- Northwind T.A.S.\n",
            encoding="utf-8")

    (root / "crm" / "config.md").write_text(
        "| Type | Expected Cadence | Yellow Threshold | Red Threshold |\n"
        "|------|-----------------|-----------------|---------------|\n"
        "| prospect | 14 | 20 | 30 |\n",
        encoding="utf-8")

    for slug, name, company in (("dana-reed", "Dana Reed", "Northwind Telecom"),
                                ("lee-park", "Lee Park", "Harbour Systems")):
        (root / "crm" / "contacts" / f"{slug}.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"company: {company}\n"
            "relationship_type: prospect\n"
            f"last_touch: {LAST_TOUCH}\n"
            "created: 2026-03-15\n"
            "---\n\n"
            f"# {name}\n",
            encoding="utf-8")
    return root


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """Point the data-root seam at a fixture overlay, as production is pointed
    at the real one. `HEADING_OS_DATA` is read at CALL time by the seam, which
    is the whole property under test."""
    root = _overlay(tmp_path / "data")
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    return root


def _by_name(contacts):
    return {c["name"]: c for c in contacts}


def test_a_production_call_reads_the_pipeline_from_the_data_overlay(overlay):
    """No `workspace_root`, exactly as `crm-health.py` calls it."""
    config = parse_config(overlay / "crm" / "config.md")
    contacts, _warn, _dangling, stages, aliases = scan_contacts(config, today=TODAY)

    assert stages, "the pipeline was read as empty, which is the whole defect"
    assert stages["northwind telecom"] == "Demo/POC"
    assert aliases, "aliases were read as empty"


def test_a_demo_poc_contact_gets_the_stage_cadence_not_the_type_default(overlay):
    """The consequence, on the number the operator actually sees."""
    config = parse_config(overlay / "crm" / "config.md")
    contacts, _w, _d, _s, _a = scan_contacts(config, today=TODAY)

    dana = _by_name(contacts)["Dana Reed"]
    assert dana["stage"] == "Demo/POC", (
        "the contact resolved no pipeline stage, so /crm and /crm-next rank it "
        "flat and print 'no pipeline link'")
    assert dana["cadence"] == 7, (
        f"cadence is {dana['cadence']}; Demo/POC is 7 days and the prospect "
        f"default is 14, so this contact goes yellow a week late")


def test_a_won_account_stops_being_tracked(overlay):
    """`STAGE_CADENCE["Won"] = 0` was unreachable from the pipeline side, so
    closed accounts kept accruing red debt and feeding /cold-sweep drafts."""
    config = parse_config(overlay / "crm" / "config.md")
    contacts, _w, _d, _s, _a = scan_contacts(config, today=TODAY)

    lee = _by_name(contacts)["Lee Park"]
    assert lee["stage"] == "Won"
    assert lee["cadence"] == 0
    assert lee["health"] == "gray", (
        f"a closed account reported {lee['health']}; it should have stopped "
        f"being tracked")


def test_the_same_contact_without_a_pipeline_falls_back_to_the_type_default(tmp_path,
                                                                           monkeypatch):
    """Anti-vacuity. Every assertion above would also hold if the code hardcoded
    7 days for everything. The fallback must still be reachable and still be 14.
    """
    root = _overlay(tmp_path / "data", with_pipeline=False, with_aliases=False)
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    config = parse_config(root / "crm" / "config.md")
    contacts, _w, _d, stages, aliases = scan_contacts(config, today=TODAY)

    assert stages == {} and aliases == {}
    dana = _by_name(contacts)["Dana Reed"]
    assert dana["cadence"] == 14
    assert dana["stage"] == ""


def test_an_explicit_workspace_root_still_wins(tmp_path, monkeypatch):
    """`workspace_root` keeps its exact meaning: a fixture and exec-repo
    override. Only the FALLBACK moved onto the seam, so the callers that pass it
    must be unaffected. Two trees are built with DIFFERENT stages and the
    argument has to decide.
    """
    seam_root = _overlay(tmp_path / "seam")
    monkeypatch.setenv("HEADING_OS_DATA", str(seam_root))

    explicit = tmp_path / "explicit"
    _overlay(explicit)
    (explicit / "context" / "pipeline.md").write_text(
        "| Company | Stage | Owner |\n"
        "|---|---|---|\n"
        "| Northwind Telecom | Negotiation | Alex |\n",
        encoding="utf-8")

    config = parse_config(seam_root / "crm" / "config.md")
    contacts, _w, _d, stages, _a = scan_contacts(
        config, today=TODAY,
        contacts_dir=explicit / "crm" / "contacts",
        workspace_root=explicit)

    assert stages["northwind telecom"] == "Negotiation", (
        "the explicit root was ignored in favour of the seam")
    assert _by_name(contacts)["Dana Reed"]["cadence"] == 3


def test_the_engine_clone_is_not_where_these_files_live(overlay):
    """The premise, asserted rather than assumed.

    If `context/pipeline.md` ever appears in the engine clone, the old code
    would start working by accident and this whole file would stop measuring
    anything. It must not: the engine repository is public and that file is
    operator data.
    """
    from scripts.utils import crm
    engine_root = crm._WORKSPACE_ROOT
    assert not (engine_root / "context" / "pipeline.md").exists(), (
        "operator data has appeared in the engine clone")
    assert not (engine_root / "crm" / "aliases.md").exists(), (
        "operator data has appeared in the engine clone")
