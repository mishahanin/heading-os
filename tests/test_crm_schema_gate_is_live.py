#!/usr/bin/env python3
"""The CRM schema gate must actually validate, not take its skip branch.

`scripts/validate-crm-schema.py` falls back to "skipped, exit 0" when
`jsonschema` cannot be imported. That branch is correct - a hard failure there
would block every commit on a fresh clone - but it made the gate invisible when
it fired. Until 2026-08-20 `jsonschema` was installed nowhere and declared in no
manifest, so the branch fired ALWAYS, and four callers had been reporting a
green CRM schema gate that ran zero validations:

  - the `validate-crm-schema` pre-commit hook
  - `scripts/aggregate-crm.py`, before it aggregates into crm-central
  - `scripts/crm_migrate_to_entity_model.py`, verifying a staged migration

`--quiet` also suppressed the skip line, so the hook printed nothing at all.
Both halves are repaired: the dependency is pinned in `pyproject.toml`, and the
skip now prints on every path including `--quiet` and `--json`.

This test holds the first half. A gate whose dependency quietly leaves the
manifest is a gate that goes back to sleep, and the only signal would be its
silence.
"""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_jsonschema_is_declared_in_the_manifest():
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = manifest["project"]["dependencies"]
    names = [d.split("==")[0].split(">")[0].split("[")[0].strip() for d in declared]
    assert "jsonschema" in names, (
        "jsonschema left the core dependency list; validate-crm-schema.py will "
        "silently skip every validation again"
    )
    pin = next(d for d in declared if d.startswith("jsonschema"))
    assert "==" in pin, f"jsonschema must be pinned exactly, got {pin!r}"


def test_jsonschema_is_actually_importable():
    """The manifest is a claim; this is the fact the script branches on."""
    assert importlib.util.find_spec("jsonschema") is not None, (
        "jsonschema is declared but not installed - run `uv sync --all-extras "
        "--group dev`. The CRM schema gate is skipping every record right now."
    )


VALID_RELATIONSHIP = """---
entity_ref: example-corp-alba-karimova
relationship_type: prospect
last_touch: 2026-08-01
created: 2026-01-15
cadence: 30
---

# Alba Karimova

Body.
"""

INVALID_RELATIONSHIP = """---
entity_ref: example-corp-ilya-vetrov
relationship_type: prospect
created: 2026-01-15
---

# Ilya Vetrov

No `last_touch`, which the relationship schema requires.
"""


def _run(args, tmp_path):
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-crm-schema.py"),
         "--json", "--dir", str(tmp_path), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    return result.returncode, json.loads(result.stdout)


def test_the_validator_reaches_its_validating_path(tmp_path):
    """Drive the real entry point and assert it did NOT take the skip branch.

    Against a FIXTURE tree, not the operator's live CRM. The original ran with
    no `--dir` and asserted `total > 0`, so it measured how many contacts this
    machine happens to hold: red on a fresh clone, red on CI, red for anyone
    who runs the suite before importing their records — and red in a way that
    reads as "the schema gate is broken" when the gate is fine and the data is
    simply absent. Found by the 2026-08-23 audit.
    """
    contacts = tmp_path / "contacts"
    contacts.mkdir()
    (contacts / "alba-karimova.md").write_text(VALID_RELATIONSHIP, encoding="utf-8")

    code, payload = _run([], tmp_path)
    assert payload.get("status") != "skipped", (
        f"the gate skipped instead of validating: {payload.get('reason')}"
    )
    # A non-skipped run reports a count. Zero records checked is the same
    # silence in a different shape, so assert the count, not just the branch.
    assert payload.get("total", 0) == 1, f"the gate validated nothing: {payload}"
    assert code == 0, payload


def test_the_validator_actually_rejects_a_bad_record(tmp_path):
    """The mutation guard. A gate that passes everything is the same silence.

    The original test could not tell "validated one record successfully" from
    "validated one record and did not notice it was wrong", because it never
    showed the validator anything invalid.
    """
    contacts = tmp_path / "contacts"
    contacts.mkdir()
    (contacts / "ilya-vetrov.md").write_text(INVALID_RELATIONSHIP, encoding="utf-8")

    code, payload = _run([], tmp_path)
    assert payload.get("status") != "skipped"
    assert code != 0, f"an invalid record passed the gate: {payload}"
