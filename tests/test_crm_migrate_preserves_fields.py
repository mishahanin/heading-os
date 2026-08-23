"""The entity migration must not silently drop a field it claims to render.

The 2026-05-23 defect, found on 2026-08-23: `render_relationship_record` reads
`record.get("cadence")` and writes the line only when it is set -- but
`_record_from`, the function that builds every record, returned twelve keys and
`cadence` was not among them. So the conditional never fired. Measured against
`crm/.migration-backup/2026-05-15`, the live run stripped `cadence` from about a
hundred of the operator's contacts, and `crm-health.py` scores relationships on
exactly that field.

The general shape is what the guard pins: every field the renderer READS must be
a field the scan CARRIES. A renderer reading a key nobody supplies is a silent
no-op, not an error.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "crm_migrate", ROOT / "scripts" / "crm_migrate_to_entity_model.py")
mig = importlib.util.module_from_spec(_spec)
sys.modules["crm_migrate"] = mig
_spec.loader.exec_module(mig)


def _fm(text: str) -> dict:
    body = re.match(r"\A---\n(.*?)\n---", text, re.S)
    assert body, f"no frontmatter in:\n{text[:200]}"
    out = {}
    for line in body.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def _contact(tmp_path: Path, **extra) -> Path:
    lines = ["---", "name: Zenon Makarios", "email: z@vorlite.test",
             "type: partner", "last_touch: 2026-04-01"]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    lines += ["---", "", "# Zenon Makarios", "", "## Interaction Log", "", "- a note"]
    p = tmp_path / "zenon-makarios.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_cadence_survives_the_round_trip(tmp_path):
    src = _contact(tmp_path, cadence=21)
    rec = mig._record_from(src, "operator", _fm(src.read_text(encoding="utf-8")))
    assert rec.get("cadence") in (21, "21"), "the scan dropped cadence"
    out = _fm(mig.render_relationship_record(rec, "zenon-makarios"))
    assert out.get("cadence") == "21", "the render dropped cadence"


def test_a_contact_without_cadence_gets_no_empty_line(tmp_path):
    src = _contact(tmp_path)
    rec = mig._record_from(src, "operator", _fm(src.read_text(encoding="utf-8")))
    out = _fm(mig.render_relationship_record(rec, "zenon-makarios"))
    assert "cadence" not in out


def test_radar_freeze_survives(tmp_path):
    src = _contact(tmp_path, radar_freeze_until="2026-12-31")
    rec = mig._record_from(src, "operator", _fm(src.read_text(encoding="utf-8")))
    out = _fm(mig.render_relationship_record(rec, "zenon-makarios"))
    assert out.get("radar_freeze_until") == "2026-12-31", \
        "the render hardcoded an empty freeze over a real one"


def test_every_key_the_renderer_reads_is_supplied_by_the_scan(tmp_path):
    """The structural guard. A new `record.get("x")` in the renderer without a
    matching key in _record_from is the same silent no-op all over again."""
    src = ROOT / "scripts" / "crm_migrate_to_entity_model.py"
    text = src.read_text(encoding="utf-8")
    render = text.split("def render_relationship_record", 1)[1].split("\ndef ", 1)[0]
    read_keys = set(re.findall(r"record\.get\(['\"]([a-z_]+)['\"]", render))
    read_keys |= set(re.findall(r"record\[['\"]([a-z_]+)['\"]\]", render))

    supplied = mig._record_from(_contact(tmp_path), "operator",
                                {"name": "x", "type": "partner"}).keys()
    missing = sorted(read_keys - set(supplied))
    assert not missing, (
        f"render_relationship_record reads {missing} but _record_from never "
        f"supplies them, so those lines can never render")
