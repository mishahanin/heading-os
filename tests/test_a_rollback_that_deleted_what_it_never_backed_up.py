"""Shard 04-p2: a one-shot migration, and two reporters that crashed on input.

`cmd_apply`'s backup loop iterates contact FILES, so the pre-existing
`crm/address-book/` was never copied. `cmd_rollback` ran `shutil.rmtree` over
that whole directory anyway, destroying entries that predated the migration with
no copy anywhere -- while the manifest's `created_address_book` field, written by
apply for exactly this purpose, was never read.

`group_records` dropped any record with no email AND no usable name. Every count
in the review map derives from `groups`, so the drop was invisible; `--apply`
backed the file up, never migrated it, and never removed it.

`render_relationship_record` interpolated `relationship_type`, `source` and
`pipeline_company` raw, in a file that defines `_yaml_quote` as "a defensive
guard" and applies it to every comparable field elsewhere. `_yaml_quote` itself
tested only CHARACTERS, so a region of `NO` came back as a boolean.

The module docstring promised to "rewrite each contact file"; the code writes
only the CEO's, and has no write path into an exec's repository at all.

In `daemon-fleet-health.py`, `_classify` was widened to `(TypeError, ValueError)`
because a heartbeat can hold valid JSON with a non-string timestamp -- and
`_print_grid`'s own parse of the same field was not, so that input classified as
`error` and then killed the table. `_read_corporate_config_version` promised None
on an unparseable file and raised AttributeError on a valid YAML list.

`datastore-extract.py` globbed `*.xlsx` case-sensitively, so `Q3.XLSX` was
neither extracted nor mentioned on Linux.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import crm_migrate_to_entity_model as mig  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fleet = _load("daemon-fleet-health", "daemon_fleet_health_04p2")
extract = _load("datastore-extract", "datastore_extract_04p2")


# ==========================================================================
# 1 - the record that vanished between the scan and every count
# ==========================================================================

# A path that is never opened: these records exercise GROUPING, which reads
# `file_path` only as an identity. Rendering, which does open it, uses the
# `legacy_file` fixture instead.
_FAKE_DIR = "/nonexistent/crm-contacts"


def _stub_apply_environment(module, monkeypatch, tmp_path):
    """Everything cmd_apply reaches outside the CRM tree.

    The engine root must EXIST because the staged-validation step runs a
    subprocess with it as cwd, and the validator itself is stubbed: this file
    is about backup and rollback bookkeeping, not about schema validation.
    """
    import subprocess

    engine = tmp_path / "engine"
    engine.mkdir(exist_ok=True)
    monkeypatch.setattr(module, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(module, "get_outputs_dir", lambda: tmp_path / "out")
    # `(records, unreadable_slugs)`. It was a bare `list` while the scan
    # returned one value; the second half is the exec directories it could not
    # read, which the scan line now has to name.
    monkeypatch.setattr(module, "scan_all_contacts", lambda: ([], []))

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: _Ok())
    del subprocess


def _rec(**kw):
    base = {"owner": "owner-exec-a", "name": "", "email": "", "company": "",
            "type": "", "file_path": _FAKE_DIR + "/x.md", "source": ""}
    base.update(kw)
    return base


def test_a_record_with_no_email_and_no_name_still_reaches_a_group():
    records = [_rec(company="Acme", file_path=_FAKE_DIR + "/nameless.md")]
    groups = mig.group_records(records)
    assert len(groups) == 1, "the record was dropped before any count could see it"
    assert groups[0]["records"] == records
    assert groups[0]["confidence"] == "singleton"


def test_every_scanned_record_lands_in_exactly_one_group():
    records = [
        _rec(name="Alice", email="a@x.test"),
        _rec(name="Bob"),
        _rec(company="Acme"),
        _rec(name="   ", company="Beta"),
    ]
    groups = mig.group_records(records)
    grouped = [r for g in groups for r in g["records"]]
    assert len(grouped) == len(records), \
        "the group counts do not add up to the records scanned"


def test_two_nameless_records_at_one_company_stay_separate():
    """Sharing an employer is not evidence of being the same person.

    The mutation that removed the nameless branch survived a suite that only
    counted groups: without it, a nameless record joins `name_groups` under the
    key `("", company)`, so TWO of them merge into one low-confidence group.
    That group is a claim the migration would carry into a single address-book
    entity -- on the strength of a shared employer and no name at all.
    """
    records = [_rec(company="Acme", file_path=_FAKE_DIR + "/a.md"),
               _rec(company="Acme", file_path=_FAKE_DIR + "/b.md")]
    groups = mig.group_records(records)
    assert len(groups) == 2, \
        "two people with no name were merged because they share an employer"
    assert all(g["confidence"] == "singleton" for g in groups)


def test_a_whitespace_name_is_treated_as_no_name():
    groups = mig.group_records([_rec(name="   ", company="Acme")])
    assert len(groups) == 1


def test_named_records_still_group_by_name_and_company():
    records = [_rec(name="Alice", company="Acme"), _rec(name="Alice", company="Acme")]
    groups = mig.group_records(records)
    assert len(groups) == 1
    assert groups[0]["confidence"] == "low"


def test_email_grouping_is_unchanged():
    records = [_rec(name="Alice", email="a@x.test"), _rec(name="A.", email="A@X.test")]
    groups = mig.group_records(records)
    assert len(groups) == 1
    assert groups[0]["confidence"] == "high"


# ==========================================================================
# 2 - the guard that read characters and not words
# ==========================================================================

@pytest.mark.parametrize("value", ["NO", "no", "yes", "Y", "true", "off",
                                   "null", "~", "None"])
def test_a_yaml_one_one_keyword_is_quoted(value):
    """`region: NO` is Norway, and an unquoted NO is False."""
    out = mig._yaml_quote(value)
    assert out.startswith('"') and out.endswith('"'), \
        f"{value!r} would parse as a boolean or null, not a string"


@pytest.mark.parametrize("value", ["007", "3", "1.5", "-2", "1e3"])
def test_a_numeric_looking_value_is_quoted(value):
    assert mig._yaml_quote(value).startswith('"'), \
        f"{value!r} would parse as a number and lose its leading zeros or type"


@pytest.mark.parametrize("value", ["Holdings: Europe", "A # B", "[bracket]", "a*b"])
def test_a_special_character_is_still_quoted(value):
    assert mig._yaml_quote(value).startswith('"')


@pytest.mark.parametrize("value", ["Alice Smith", "Acme Holdings", "Norway", "EMEA"])
def test_an_ordinary_value_is_left_alone(value):
    assert mig._yaml_quote(value) == value


def test_empty_and_none_are_empty():
    assert mig._yaml_quote("") == ""
    assert mig._yaml_quote(None) == ""


# ==========================================================================
# 3 - three fields that bypassed the guard
# ==========================================================================

@pytest.fixture()
def legacy_file(tmp_path):
    path = tmp_path / "legacy.md"
    path.write_text("---\nname: Alice\n---\n\n## Interaction Log\n\n- met\n",
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("field,key", [
    ("pipeline_company", "company"),
    ("relationship_type", "type"),
    ("source", "source"),
])
def test_a_colon_in_a_field_does_not_break_the_frontmatter(field, key, legacy_file):
    import yaml

    rec = _rec(name="Alice", file_path=str(legacy_file), **{key: "Holdings: Europe"})
    text = mig.render_relationship_record(rec, "alice")
    fm = text.split("---", 2)[1]
    parsed = yaml.safe_load(fm)
    assert parsed[field] == "Holdings: Europe", \
        f"{field} was written raw and reparsed as {parsed.get(field)!r}"


def test_a_yaml_keyword_company_survives_the_round_trip(legacy_file):
    import yaml

    rec = _rec(name="Alice", file_path=str(legacy_file), company="NO")
    fm = mig.render_relationship_record(rec, "alice").split("---", 2)[1]
    assert yaml.safe_load(fm)["pipeline_company"] == "NO", \
        "an unquoted NO came back as the boolean False"


def test_the_record_still_renders_normally(legacy_file):
    import yaml

    rec = _rec(name="Alice", file_path=str(legacy_file), company="Acme", type="lead")
    text = mig.render_relationship_record(rec, "alice")
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["entity_ref"] == "alice"
    assert fm["pipeline_company"] == "Acme"
    assert "## Interaction Log" in text


# ==========================================================================
# 4 - the address book the rollback deleted and never had
# ==========================================================================

def test_the_pre_existing_address_book_is_backed_up(tmp_path, monkeypatch):
    crm_root = tmp_path / "data" / "crm"
    contacts = crm_root / "contacts"
    contacts.mkdir(parents=True)
    (crm_root / "address-book").mkdir()
    (crm_root / "address-book" / "preexisting.md").write_text("old\n", encoding="utf-8")

    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    _stub_apply_environment(mig, monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_a: "no")

    (tmp_path / "out" / "operations" / "crm").mkdir(parents=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    (tmp_path / "out" / "operations" / "crm" / f"{today}_migration-map.md").write_text(
        "map\n", encoding="utf-8")

    mig.cmd_apply()
    backups = list((crm_root / ".migration-backup").rglob("preexisting.md"))
    assert backups, "the address book that predated the migration was never copied"


def test_rollback_removes_only_what_apply_created(tmp_path, monkeypatch, capsys):
    crm_root = tmp_path / "data" / "crm"
    contacts = crm_root / "contacts"
    contacts.mkdir(parents=True)
    ab = crm_root / "address-book"
    ab.mkdir()
    (ab / "preexisting.md").write_text("old\n", encoding="utf-8")
    (ab / "created.md").write_text("new\n", encoding="utf-8")

    backup = crm_root / ".migration-backup" / "2026-08-25"
    backup.mkdir(parents=True)
    (backup / "applied-manifest.json").write_text(json.dumps({
        "created_contacts": [], "created_address_book": ["created.md"],
        "removed_legacy": [],
    }), encoding="utf-8")

    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(mig, "get_workspace_root", lambda: tmp_path / "engine")
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")

    mig.cmd_rollback()
    assert (ab / "preexisting.md").is_file(), \
        "rollback destroyed an entry it never created and never backed up"
    assert not (ab / "created.md").exists(), "rollback left its own output behind"


def test_rollback_without_a_manifest_leaves_the_address_book_alone(
        tmp_path, monkeypatch, capsys):
    crm_root = tmp_path / "data" / "crm"
    contacts = crm_root / "contacts"
    contacts.mkdir(parents=True)
    ab = crm_root / "address-book"
    ab.mkdir()
    (ab / "preexisting.md").write_text("old\n", encoding="utf-8")
    (crm_root / ".migration-backup" / "2026-08-25").mkdir(parents=True)

    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(mig, "get_workspace_root", lambda: tmp_path / "engine")
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")

    mig.cmd_rollback()
    assert (ab / "preexisting.md").is_file(), \
        "a backup too old to say what apply created still lost data"
    assert "No applied-manifest" in capsys.readouterr().out


# ==========================================================================
# 5 - the map that had to be from today
# ==========================================================================

def test_a_map_from_yesterday_is_accepted(tmp_path, monkeypatch, capsys):
    crm_root = tmp_path / "data" / "crm"
    (crm_root / "contacts").mkdir(parents=True)
    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: crm_root / "contacts")
    _stub_apply_environment(mig, monkeypatch, tmp_path)

    yesterday = (datetime.now(timezone.utc).astimezone()
                 - timedelta(days=1)).strftime("%Y-%m-%d")
    map_dir = tmp_path / "out" / "operations" / "crm"
    map_dir.mkdir(parents=True)
    (map_dir / f"{yesterday}_migration-map.md").write_text("map\n", encoding="utf-8")

    assert mig.cmd_apply() == 0, \
        "a map reviewed overnight was refused as though it did not exist"
    assert "not from today" in capsys.readouterr().out


def test_no_map_at_all_still_refuses(tmp_path, monkeypatch, capsys):
    crm_root = tmp_path / "data" / "crm"
    (crm_root / "contacts").mkdir(parents=True)
    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: crm_root / "contacts")
    monkeypatch.setattr(mig, "get_workspace_root", lambda: tmp_path / "engine")
    monkeypatch.setattr(mig, "get_outputs_dir", lambda: tmp_path / "out")
    assert mig.cmd_apply() == 1
    assert "Run --propose first" in capsys.readouterr().out


# ==========================================================================
# 6 - the reporter that crashed on the input it classified fine
# ==========================================================================

def _record(**kw):
    base = {"slug": "exec-a", "path": _FAKE_DIR + "/exec-a", "last_heartbeat": None,
            "recent_error_count": 0}
    base.update(kw)
    return base


def test_a_non_string_timestamp_does_not_kill_the_grid(capsys):
    """`_classify` tolerates it; the grid's own parse must too."""
    fleet._print_grid([_record(last_heartbeat=123, status="error")], 30, None, {})
    assert "?" in capsys.readouterr().out


def test_a_non_string_timestamp_classifies_as_error():
    assert fleet._classify(_record(last_heartbeat=123), 30, None, None) == "error"


@pytest.mark.parametrize("value,expected", [
    (0, 0), (2, 2), ("2", 2), ("0", 0),
    ("not a number", 1), (None, 1), ([], 1), (True, 1),
])
def test_an_unreadable_error_count_is_treated_as_an_error(value, expected):
    assert fleet._error_count(value) == expected


def test_a_stringly_typed_error_count_does_not_crash_the_report():
    fresh = datetime.now(timezone.utc).isoformat()
    status = fleet._classify(
        _record(last_heartbeat=fresh, recent_error_count="2"), 30, None, None)
    assert status == "error", "a string count crashed the report instead of raising the alarm"


def test_a_healthy_record_is_still_healthy():
    fresh = datetime.now(timezone.utc).isoformat()
    status = fleet._classify(
        _record(last_heartbeat=fresh, recent_error_count=0), 30, None, None)
    assert status != "error"


@pytest.mark.parametrize("body", ["- just-a-list\n", "3\n", "a scalar\n"])
def test_a_non_mapping_config_returns_none(tmp_path, body):
    cfg = tmp_path / "corporate" / "daemon"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text(body, encoding="utf-8")
    assert fleet._read_corporate_config_version(tmp_path) is None, \
        "one malformed config file took the whole fleet report down"


def test_a_real_config_still_reads(tmp_path):
    cfg = tmp_path / "corporate" / "daemon"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    assert fleet._read_corporate_config_version(tmp_path) == "2"


# ==========================================================================
# 7 - the file the extractor never saw
# ==========================================================================

def test_an_uppercase_extension_is_found(tmp_path, monkeypatch, capsys):
    (tmp_path / "Q3.XLSX").write_bytes(b"not really a workbook")
    monkeypatch.setattr(extract, "get_datastore_dir", lambda: tmp_path)
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# extracted\n")
    extract.scan_and_extract(tmp_path)
    assert (tmp_path / "Q3-extract.md").is_file(), \
        "an uppercase extension was silently skipped and the run reported success"


def test_a_lowercase_extension_still_works(tmp_path, monkeypatch):
    (tmp_path / "q3.xlsx").write_bytes(b"x")
    monkeypatch.setattr(extract, "get_datastore_dir", lambda: tmp_path)
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# extracted\n")
    extract.scan_and_extract(tmp_path)
    assert (tmp_path / "q3-extract.md").is_file()


def test_an_unrelated_extension_is_not_picked_up(tmp_path, monkeypatch, capsys):
    (tmp_path / "notes.TXT").write_text("x", encoding="utf-8")
    monkeypatch.setattr(extract, "get_datastore_dir", lambda: tmp_path)
    extract.scan_and_extract(tmp_path)
    assert "No XLSX or PPTX files" in capsys.readouterr().out


def test_a_stale_companion_is_named_when_a_sibling_arrives(tmp_path):
    (tmp_path / "pitch-extract.md").write_text("describes the deck\n", encoding="utf-8")
    orphans = extract.orphaned_companions({(tmp_path, "pitch")})
    assert orphans == [tmp_path / "pitch-extract.md"], \
        "the stale companion was never named, only its stem"


def test_a_stale_companion_is_never_deleted(tmp_path):
    stale = tmp_path / "pitch-extract.md"
    stale.write_text("describes the deck\n", encoding="utf-8")
    extract.orphaned_companions({(tmp_path, "pitch")})
    assert stale.is_file(), "extracted content the operator may have edited was deleted"


def test_no_orphan_means_no_report(tmp_path):
    assert extract.orphaned_companions({(tmp_path, "pitch")}) == []


def test_the_orphan_is_reported_by_the_scan(tmp_path, monkeypatch, capsys):
    (tmp_path / "pitch.xlsx").write_bytes(b"x")
    (tmp_path / "pitch.pptx").write_bytes(b"x")
    (tmp_path / "pitch-extract.md").write_text("describes the deck\n", encoding="utf-8")
    monkeypatch.setattr(extract, "get_datastore_dir", lambda: tmp_path)
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# x\n")
    monkeypatch.setattr(extract, "extract_pptx", lambda p: "# p\n")
    extract.scan_and_extract(tmp_path)
    assert "pitch-extract.md" in capsys.readouterr().out, \
        "the stale file itself was never surfaced"


# ==========================================================================
# The docstring correction this shard claimed and nothing pinned
# ==========================================================================

def test_the_migration_docstring_does_not_promise_to_write_exec_files():
    """Added 2026-08-30: every other defect this shard narrates has a test that
    goes red on a revert -- the grouping, `_yaml_quote`, the three unquoted
    fields, the backup, the rollback, the map date, the fleet reporter, the
    config version, the glob. This one had none. Nothing in the file read
    `mig.__doc__` or the module source, so restoring the false promise to
    `scripts/crm_migrate_to_entity_model.py` left the whole suite green.

    The claim is about a docstring, so the docstring is what is read; the
    behaviour behind it is pinned separately by
    `test_the_migration_has_no_write_path_into_an_exec_repository` below.
    """
    doc = mig.__doc__ or ""
    assert doc.strip(), "the migration module lost its docstring entirely"
    assert "rewrite\n     THE CEO'S contact files" in doc or \
           "THE CEO'S contact files" in doc, (
        "step 4 no longer says whose contact files are rewritten; the sentence "
        "it replaced promised 'each contact file', a behaviour the code has "
        "never had")
    assert "no write path into another" in doc, (
        "the docstring dropped the statement that this script cannot write "
        "into an exec's repository")


def test_the_migration_has_no_write_path_into_an_exec_repository():
    """The behaviour the docstring above describes, measured on the code.

    A docstring pin alone would let the sentence stay true while the code grew
    the write path it disclaims. `apply_migration` must not write anywhere it
    reached through the exec-record scan.
    """
    import ast
    import inspect

    source = inspect.getsource(mig)
    tree = ast.parse(source)

    writers = {"write_text", "write_bytes", "mkdir", "rename", "replace", "unlink"}
    exec_writes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in writers:
            continue
        target = ast.unparse(node.func.value).lower()
        if "exec" in target and "staging" not in target:
            exec_writes.append(ast.unparse(node)[:80])

    assert exec_writes == [], (
        f"a write reaches an exec-owned path, which the module docstring says "
        f"this script has no path to do: {exec_writes}")
