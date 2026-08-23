#!/usr/bin/env python3
"""Two fleet registries, one join, no overlapping facts.

The workspace keeps two records of "the executives", and they answer different
questions:

  `<data-root>/config/exec-registry.json`  -- the 31C ORG CHART. Who is an
      executive in the business: title, email, business role. Hand-maintained.
  `<data-root>/admin/executives.json`      -- the HEADING OS FLEET ROSTER. Who
      is provisioned as a user: github handle, data repo, provisioning status.
      Single writer, `admin/provision/registry.py`.

Merging them into one file was considered and rejected on 2026-08-23. A real
fleet already contains people who are one and not the other: an executive with
no HEADING OS install at all, and an admin who is not a row of the fleet. One
row cannot carry two independent lifecycles, and the attempt to make it produced
the defect below.

**What went wrong before the split.** `exec-registry.json` carried
`aios: "removed"` on two executives, with the note "works in a different
environment, not a HEADING OS user", stamped 2026-06-12. `executives.json`
listed both as `active`. A system fact had been bolted onto a business record
because there was nowhere else to put it, and then it went stale: on 2026-08-23
both `heading-os-data-{slug}` repos existed and had been pushed within twelve
days. The business file was wrong about a system fact
it had no business holding.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A data root carrying both registries."""
    d = tmp_path / ".heading-os-data"
    (d / "config").mkdir(parents=True)
    (d / "admin").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    from scripts.utils import workspace
    workspace._reset_identity_cache()

    (d / "config" / "exec-registry.json").write_text(json.dumps({"executives": [
        {"slug": "ceo", "name": "The Operator", "title": "Founder & CEO",
         "email": "ceo@example.com", "role": "admin", "status": "active"},
        {"slug": "both", "name": "Both Ways", "title": "CSO",
         "email": "both@example.com", "role": "cso", "status": "active"},
        {"slug": "business-only", "name": "Business Only", "title": "COO",
         "email": "bo@example.com", "role": "coo", "status": "active"},
    ]}), encoding="utf-8")

    (d / "admin" / "executives.json").write_text(json.dumps({"version": 1, "executives": [
        {"slug": "both", "name": "Both Ways", "role": "cso",
         "github_user": "bothways", "data_repo": "heading-os-data-both",
         "status": "active"},
        {"slug": "system-only", "name": "System Only", "role": "exec",
         "github_user": "sysonly", "data_repo": "heading-os-data-system-only",
         "status": "provisioning"},
    ]}), encoding="utf-8")
    return d


def _fleet():
    from scripts.utils.workspace import load_fleet
    return {r["slug"]: r for r in load_fleet()}


# ------------------------------------------------------------------- the join

def test_the_join_covers_the_union_of_both_files(fleet):
    assert set(_fleet()) == {"ceo", "both", "business-only", "system-only"}


def test_a_person_in_both_files_is_marked_as_both(fleet):
    r = _fleet()["both"]
    assert r["is_business_exec"] is True
    assert r["is_heading_os_user"] is True


def test_a_business_exec_with_no_install_is_not_a_user(fleet):
    """Maxim's real shape: on the org chart, no HEADING OS install."""
    r = _fleet()["business-only"]
    assert r["is_business_exec"] is True
    assert r["is_heading_os_user"] is False
    assert r["github_user"] is None
    assert r["data_repo"] is None


def test_a_user_with_no_org_chart_row_is_surfaced_not_dropped(fleet):
    """A contractor or assistant could be a user without being an executive.
    Dropping them would hide an install from every fleet-wide command."""
    r = _fleet()["system-only"]
    assert r["is_heading_os_user"] is True
    assert r["is_business_exec"] is False
    assert r["title"] is None


def test_each_file_supplies_its_own_side_of_the_record(fleet):
    r = _fleet()["both"]
    # business side
    assert (r["title"], r["email"], r["business_role"]) == ("CSO", "both@example.com", "cso")
    # system side
    assert (r["github_user"], r["data_repo"]) == ("bothways", "heading-os-data-both")


def test_the_two_status_fields_stay_distinguishable(fleet):
    """Both files call their field `status` and they mean different things:
    employment versus provisioning. The join must not collapse them."""
    r = _fleet()["both"]
    assert r["employment_status"] == "active"
    assert r["provisioning_status"] == "active"
    r2 = _fleet()["system-only"]
    assert r2["provisioning_status"] == "provisioning"
    assert r2["employment_status"] is None


def test_the_status_vocabulary_runs_to_provisioned(fleet):
    """`provisioned` sits between `provisioning` and `active`, added 2026-08-23.

    Without it a finished install had to sit at "provisioning", which reads as
    unfinished work, or be flipped to "active" and start being aggregated
    before its operator had touched it. The live case that prompted it was an
    install finished end to end and then deliberately held at "provisioning"
    until its operator ran /backup for the first time.
    """
    import json
    from scripts.utils.workspace import get_data_root, load_fleet
    (fleet / "admin" / "executives.json").write_text(json.dumps({"executives": [
        {"slug": "done-not-used", "status": "provisioned"},
    ]}), encoding="utf-8")
    rec = {r["slug"]: r for r in load_fleet()}["done-not-used"]
    assert rec["provisioning_status"] == "provisioned"
    assert rec["is_heading_os_user"] is True


def test_only_active_counts_as_fleet_membership(fleet):
    """A provisioned-but-unused install must not be aggregated or synced."""
    import json
    from scripts.utils.workspace import get_all_active_exec_slugs
    (fleet / "admin" / "executives.json").write_text(json.dumps({"executives": [
        {"slug": "in-use", "status": "active"},
        {"slug": "done-not-used", "status": "provisioned"},
        {"slug": "still-building", "status": "provisioning"},
    ]}), encoding="utf-8")
    assert get_all_active_exec_slugs() == ["in-use"]


def test_the_canary_concept_is_gone_from_both_registries():
    """The staging/canary rollout was removed on 2026-08-23 as unwired: no
    entry point (publish never wrote to `staging`), no scheduled smoke run to
    open its gate, no live canary install, against a repo published three times
    in three months. A stray flag left behind would be a fact nothing reads."""
    import json
    from scripts.utils.workspace import get_data_config_dir, get_data_root
    for path in (get_data_config_dir() / "exec-registry.json",
                 get_data_root() / "admin" / "executives.json"):
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8")).get("executives", [])
        assert not [r["slug"] for r in rows if "canary" in r], path


def test_the_join_is_sorted_and_stable(fleet):
    from scripts.utils.workspace import load_fleet
    slugs = [r["slug"] for r in load_fleet()]
    assert slugs == sorted(slugs)
    assert slugs == [r["slug"] for r in load_fleet()]


def test_an_empty_data_root_yields_an_empty_fleet(tmp_path, monkeypatch):
    """A data-less engine clone has no fleet, and that is not an error."""
    d = tmp_path / "bare"
    d.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    assert workspace.load_fleet() == []


# ------------------------------------------ the fields that must not come back

REMOVED_FROM_BUSINESS = ("aios", "aios_removed_at", "aios_note", "crm_path",
                         "workspace_repo", "canary")


def test_the_live_business_registry_carries_no_system_facts():
    """Runs against the REAL file, not a fixture: this is the cleanup landing.

    - `aios*` said whether someone was a HEADING OS user. That is exactly what
      presence in `executives.json` says, and the copy went stale by two months.
    - `workspace_repo` held `31c-workspace-{slug}`, the retired topology's name.
    - `canary` is a deployment property.
    - `crm_path` had no reader at all.
    """
    from scripts.utils.workspace import get_data_config_dir
    path = get_data_config_dir() / "exec-registry.json"
    if not path.exists():
        pytest.skip("no data overlay on this clone")
    rows = json.loads(path.read_text(encoding="utf-8")).get("executives", [])
    offenders = {
        f"{r.get('slug')}.{k}" for r in rows for k in REMOVED_FROM_BUSINESS if k in r
    }
    assert offenders == set(), sorted(offenders)


def test_the_live_registries_do_not_contradict_each_other():
    """The concrete defect: a business row claiming someone is not a user while
    the roster lists them as one. Asymmetry is fine and expected; a same-slug
    contradiction is not."""
    from scripts.utils.workspace import load_fleet
    bad = [
        r["slug"] for r in load_fleet()
        if r["is_heading_os_user"] and r["employment_status"] in {"offboarded", "revoked"}
    ]
    assert bad == [], (
        f"still provisioned but no longer employed: {bad}. Offboard the install "
        f"or correct the org chart; do not leave the two disagreeing."
    )


def test_no_script_reads_a_removed_business_field():
    """A reader left behind would silently see None forever."""
    offenders = []
    for script in sorted((ROOT / "scripts").rglob("*.py")):
        text = script.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            for field in ("workspace_repo", "crm_path", "aios_removed_at", "aios_note"):
                if f'"{field}"' in line or f"'{field}'" in line:
                    offenders.append(f"{script.relative_to(ROOT)}:{n}: {stripped[:90]}")
    assert offenders == [], "\n  ".join([""] + offenders)
