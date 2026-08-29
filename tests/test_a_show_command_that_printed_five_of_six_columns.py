"""`skill_graph show` promised a full catalog row and printed five of six fields.

`fmt_show` built its lean-text block from skill, phase, followed_by,
preceded_by and produces_in, and never touched consumes_from -- while the
`--json` branch one line above emitted all six FIELDS. The module docstring
says `show SKILL` is "the full catalog row" and that the lean text is what an
LLM reads, so the two output modes of one command disagreed about how wide a
row is, and the text consumer was the one shown the narrower answer.

Measured before the fix, on the real reference/skill-graph.csv:
  `skill_graph.py show osint` printed followed_by and produces_in only;
  `--json` on the same row carried "consumes_from": "datastore".
35 of the catalog's 94 rows populate consumes_from.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.skill_graph import FIELDS, default_file, fmt_show, load  # noqa: E402


@pytest.fixture()
def catalog(tmp_path: Path) -> Path:
    """A two-row catalog: one row populates consumes_from, one leaves it empty."""
    path = tmp_path / "skill-graph.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerow({
            "skill": "moneypenny-brief",
            "phase": "intel",
            "preceded_by": "",
            "followed_by": "bond-dossier",
            "produces_in": "outputs/intel",
            "consumes_from": "datastore",
        })
        writer.writerow({
            "skill": "bond-dossier",
            "phase": "intel",
            "preceded_by": "moneypenny-brief",
            "followed_by": "",
            "produces_in": "outputs/intel",
            "consumes_from": "",
        })
    return path


def test_lean_text_show_carries_consumes_from(catalog: Path) -> None:
    rows = load(catalog)
    populated = [r for r in rows if r["consumes_from"]]
    assert populated, "fixture must contain a row that populates consumes_from"

    text = fmt_show(populated, as_json=False)

    assert "consumes_from:" in text
    assert "datastore" in text


def test_lean_text_show_omits_the_field_when_empty(catalog: Path) -> None:
    """Empty cells stay suppressed, like every other optional field."""
    rows = load(catalog)
    empty = [r for r in rows if not r["consumes_from"]]
    assert empty, "fixture must contain a row with an empty consumes_from"

    text = fmt_show(empty, as_json=False)

    assert "consumes_from:" not in text


def test_text_and_json_show_agree_on_every_populated_field(catalog: Path) -> None:
    """The two modes of one command must not disagree about how wide a row is."""
    rows = load(catalog)
    assert rows, "fixture must be non-empty"

    text = fmt_show(rows, as_json=False)
    parsed = json.loads(fmt_show(rows, as_json=True))
    assert parsed, "JSON mode must emit the rows it was handed"

    for row in parsed:
        for field in FIELDS:
            value = row[field]
            if field in ("skill", "phase") or not value:
                continue
            assert value in text, (
                f"{field}={value!r} is in the JSON row but missing from the "
                f"lean text the docstring calls the full catalog row"
            )


def test_the_real_catalog_still_populates_consumes_from() -> None:
    """Pins the fixture to reality: a corpus that stopped using the field would
    make the three tests above vacuous without failing."""
    real = default_file()
    if not real.is_file():
        pytest.skip(f"reference catalog absent at {real} (bare clone)")
    rows = load(real)
    assert rows, "the real catalog must be non-empty"
    populated = [r for r in rows if r["consumes_from"]]
    assert populated, "no row populates consumes_from; this regression is unreachable"

    text = fmt_show(populated[:1], as_json=False)
    assert "consumes_from:" in text
