"""Two silent degradations in the fleet CRM aggregator.

Both found by the 2026-08-23 engine audit, both the shape the same audit found
twice more the same day in `scripts/admin-health.py`: a failure that yields a
plausible number rather than a signal, in a tool whose whole output is a number
somebody acts on.

1. **A failed `git pull` on an exec overlay was invisible.** The clone branch
   inspects `result.returncode` and records a `slug_errors` entry. The pull
   branch called the same helper with `check=False` and looked at nothing, so a
   nonzero exit -- expired auth, a merge conflict, a detached HEAD -- proceeded
   straight to aggregating whatever the stale clone held. Only a raised
   exception was reported. The two branches sit four lines apart and disagreed.

2. **A corrupt fleet registry became an empty fleet.** `load_fleet_registry`
   caught `json.JSONDecodeError` and `OSError` and returned
   `{"version": 1, "executives": []}`. Absent and unreadable are different
   facts: absent legitimately means "no fleet yet", unreadable means "the file
   that says who the fleet is cannot be parsed". Both produced a CEO-only run
   that exits 0 and reports success, so one stray comma in `executives.json`
   silently drops every executive from the aggregate.

The registry fix keeps the empty-on-absent behaviour, because that one is
correct, and separates it from the parse failure. That distinction is the whole
finding: a guard that treats both the same is what made the typo invisible.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _agg():
    path = ROOT / "scripts" / "aggregate-crm.py"
    spec = importlib.util.spec_from_file_location("aggregate_crm_failures", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AGG = _agg()


# --- 1. a failed pull is recorded --------------------------------------------

def _exec_repo(tmp_path: Path, slug: str) -> Path:
    repo = tmp_path / f".heading-os-data-{slug}"
    (repo / "crm" / "contacts").mkdir(parents=True)
    return repo


def _scan(monkeypatch, tmp_path, slug, runner, *, skip_clone=False):
    """Drive the pull branch through its only public entry, `scan_all_contacts`.

    The branch lives in a closure, so there is nothing narrower to call. The CEO
    half is pointed at an empty directory so only the exec half runs.
    """
    repo = _exec_repo(tmp_path, slug)
    monkeypatch.setattr(AGG, "get_per_exec_repo_path_for_workspace",
                        lambda _root, _slug: repo)
    ceo_dir = tmp_path / "ceo-crm"
    ceo_dir.mkdir()
    monkeypatch.setattr(AGG, "get_crm_contacts_dir", lambda: ceo_dir)
    monkeypatch.setattr(AGG.subprocess, "run", runner)
    return AGG.scan_all_contacts(tmp_path, [slug], {}, ceo_only=False,
                                 skip_clone=skip_clone)


def test_a_failed_pull_lands_in_the_error_list(monkeypatch, tmp_path):
    def fake_run(cmd, **kw):
        if list(cmd[:2]) == ["git", "pull"]:
            return subprocess.CompletedProcess(cmd, 1, "", "fatal: Authentication failed")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    _contacts, errors = _scan(monkeypatch, tmp_path, "someone", fake_run)
    assert any("someone" in e and "pull" in e.lower() for e in errors), (
        f"a failed pull produced no error entry; the aggregate would be built "
        f"from a stale clone and reported as current. Errors were {errors!r}"
    )


def test_a_successful_pull_records_nothing(monkeypatch, tmp_path):
    _contacts, errors = _scan(
        monkeypatch, tmp_path, "someone",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert not [e for e in errors if "pull" in e.lower()], errors


def test_skip_clone_does_not_pull_at_all(monkeypatch, tmp_path):
    calls = []

    def recording(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    _scan(monkeypatch, tmp_path, "someone", recording, skip_clone=True)
    assert not [c for c in calls if c[:2] == ["git", "pull"]], calls


# --- 2. absent and unreadable are different ----------------------------------

def test_an_absent_registry_is_an_empty_fleet(tmp_path):
    """The correct half of the old behaviour, kept."""
    reg = AGG.load_fleet_registry(tmp_path)
    assert reg == {"version": 1, "executives": []}


def test_a_valid_registry_loads(tmp_path):
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "executives.json").write_text(
        json.dumps({"version": 1, "executives": [{"slug": "a", "status": "active"}]}),
        encoding="utf-8")
    assert AGG.load_fleet_registry(tmp_path)["executives"][0]["slug"] == "a"


def test_a_corrupt_registry_is_not_silently_an_empty_fleet(tmp_path):
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "executives.json").write_text(
        '{"version": 1, "executives": [{"slug": "a",}]}', encoding="utf-8")
    with pytest.raises(AGG.FleetRegistryError) as exc:
        AGG.load_fleet_registry(tmp_path)
    assert "executives.json" in str(exc.value)


def test_a_registry_that_is_not_an_object_is_also_refused(tmp_path):
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "executives.json").write_text("[]", encoding="utf-8")
    with pytest.raises(AGG.FleetRegistryError):
        AGG.load_fleet_registry(tmp_path)


def test_the_error_names_the_file_so_the_operator_can_fix_it(tmp_path):
    (tmp_path / "admin").mkdir()
    path = tmp_path / "admin" / "executives.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(AGG.FleetRegistryError) as exc:
        AGG.load_fleet_registry(tmp_path)
    assert str(path) in str(exc.value)


# --- 3. a legacy bucket is merged into exactly one entity --------------------

def test_two_entities_sharing_a_name_do_not_both_claim_the_legacy_records():
    """Phase 3 bridges the migration window: a CEO record already carrying
    `entity_ref` and an exec record still keyed `legacy::name::...` are the same
    person and must end in one bucket.

    Two DISTINCT entity_refs can share a normalized name, which is what an
    inconsistent migration produces. Deletion of the legacy key happened after
    the loop, so both extended themselves from the same legacy bucket and the
    same records landed in two groups -- and `detect_shared_contacts` then
    emitted overlapping duplicates. Found by the 2026-08-23 audit.
    """
    records = [
        {"name": "Jordan Kim", "entity_ref": "ent-a", "owner_slug": "a"},
        {"name": "Jordan Kim", "entity_ref": "ent-b", "owner_slug": "b"},
        {"name": "Jordan Kim", "owner_slug": "c"},          # unmigrated
    ]
    grouped = AGG.group_by_entity(records)
    placements = [k for k, v in grouped.items()
                  if any(r.get("owner_slug") == "c" for r in v)]
    assert len(placements) == 1, (
        f"the unmigrated record landed in {len(placements)} buckets: {placements}"
    )


def test_the_ordinary_migration_bridge_still_works():
    """One entity, one legacy twin: they must still merge, or dual-owner
    detection goes blind during the whole migration window."""
    records = [
        {"name": "Dana Cole", "entity_ref": "ent-a", "owner_slug": "a"},
        {"name": "Dana Cole", "owner_slug": "b"},
    ]
    grouped = AGG.group_by_entity(records)
    assert len(grouped) == 1, grouped
    owners = {r["owner_slug"] for r in next(iter(grouped.values()))}
    assert owners == {"a", "b"}


def test_unrelated_names_stay_apart():
    records = [
        {"name": "Dana Cole", "entity_ref": "ent-a", "owner_slug": "a"},
        {"name": "Erik Grant", "owner_slug": "b"},
    ]
    assert len(AGG.group_by_entity(records)) == 2
