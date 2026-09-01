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

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

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


def _keys_the_renderer_reads() -> set[str]:
    """Every literal key `render_relationship_record` reads off `record`.

    Asked of the AST, not of a regex over a text slice. The regex version was
    `record\\.get\\(['\"]([a-z_]+)['\"]` over
    `text.split("def render_relationship_record", 1)[1]`, and `[a-z_]+` is not
    the set of things a frontmatter key can be. MEASURED 2026-09-01 by adding
    one line to the renderer and running this file:

        record.get("stage_note")  -> guard RED   (seen)
        record.get("tier2_note")  -> guard GREEN (a digit; invisible)
        record.get("stageNote")   -> guard GREEN (a capital; invisible)

    Both survivors are the exact defect this guard is named for: a renderer key
    nobody supplies, which is a line that can never render and never errors.
    `record["tier2_note"]` did turn the file red, but through the three render
    tests raising KeyError, but the guard itself still saw nothing, so the file
    would have reported a crash rather than the missing field.

    The AST also drops the `.split("\\ndef ", 1)` slice, which was reading "the
    text between two markers" rather than "this function".
    """
    src = ROOT / "scripts" / "crm_migrate_to_entity_model.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "render_relationship_record"), None)
    assert fn is not None, "render_relationship_record is gone or was renamed"

    keys: set[str] = set()
    for node in ast.walk(fn):
        # record.get("x") / record.get("x", default)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "record"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
        # record["x"]
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "record"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def test_every_key_the_renderer_reads_is_supplied_by_the_scan(tmp_path):
    """The structural guard. A new `record.get("x")` in the renderer without a
    matching key in _record_from is the same silent no-op all over again."""
    read_keys = _keys_the_renderer_reads()

    # A floor: a matcher that has quietly stopped matching reports no missing
    # keys and passes, which is the shape this guard is supposed to refuse.
    assert {"cadence", "owner", "file_path", "last_touch", "name",
            "radar_freeze_until"} <= read_keys, sorted(read_keys)

    supplied = mig._record_from(_contact(tmp_path), "operator",
                                {"name": "x", "type": "partner"}).keys()
    missing = sorted(read_keys - set(supplied))
    assert not missing, (
        f"render_relationship_record reads {missing} but _record_from never "
        f"supplies them, so those lines can never render")


@pytest.mark.parametrize("cadence", [0, "", None])
def test_a_falsy_cadence_renders_no_cadence_line(tmp_path, cadence):
    """`0`, `""` and absent all mean "no cadence" across this workspace:
    `aggregate-crm.get_thresholds` ignores a `0` override and falls back to the
    type default, and `STAGE_CADENCE["Won"] = 0` is the stop-tracking signal
    rather than a zero-day cadence. Writing `cadence: 0` into a migrated record
    would hand the radar a number no reader treats as one. The renderer's
    `not in (None, "", 0)` says so; only the None case was measured, so
    loosening it to `is not None` left this file green (2026-09-01)."""
    rec = mig._record_from(_contact(tmp_path), "operator",
                           {"name": "x", "type": "partner"})
    rec["cadence"] = cadence
    assert "cadence" not in _fm(mig.render_relationship_record(rec, "zenon-makarios"))
