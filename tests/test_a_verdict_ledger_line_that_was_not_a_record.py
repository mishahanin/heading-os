"""A `null` in the verdict ledger stopped the aggregate being written, forever.

`load_verdicts` caught `json.JSONDecodeError` and nothing else. `json.loads`
returns any JSON value, so a line of `null`, `[]`, `42` or `"text"` parsed fine
and then raised `AttributeError` on `rec.get("verdict_id")`. That escapes
`main`, so `_aggregate.md` is never written.

The permanence is the damage. `_verdicts.jsonl` is append-only and nothing
removes the bad line, so every later run crashed the same way until someone
hand-edited the file. The read side of the whole verdict workflow, and the
Phase-3b calibration gate that counts its rows, go down with it.

`scripts/council-record-verdict.py` already guarded the identical parse of this
identical file. The guard reached one of the ledger's two readers and not the
other, which is why this asserts the behaviour of the reader rather than the
presence of a line of code.

Measured 2026-08-29 before the fix: `AttributeError: 'NoneType' object has no
attribute 'get'`, from `scripts/council-aggregate.py:202`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SRC = ROOT / "scripts" / "council-aggregate.py"
_spec = importlib.util.spec_from_file_location("council_aggregate_ledger", _SRC)
ca = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec: `@dataclass` resolves its annotations through
# `sys.modules[cls.__module__]`.
sys.modules["council_aggregate_ledger"] = ca
_spec.loader.exec_module(ca)

# Every JSON value that is not an object. Each one parses, and each one used to
# reach `.get` on something that has no `.get`.
NOT_A_RECORD = ["null", "[]", "42", '"text"', "true", '["verdict_id"]']

GOOD_BEFORE = '{"verdict_id": "2026-08-29_council_alpha", "choice": "kimi", "notes": "clearest"}'
GOOD_AFTER = '{"verdict_id": "2026-08-29_council_beta", "choice": "claude"}'


def _ledger(tmp_path, monkeypatch, lines):
    path = tmp_path / "_verdicts.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ca, "VERDICTS_PATH", path)
    return path


@pytest.mark.parametrize("scalar", NOT_A_RECORD)
def test_a_scalar_ledger_line_is_skipped_not_dereferenced(tmp_path, monkeypatch, scalar):
    """One bad line costs that line, and only that line."""
    _ledger(tmp_path, monkeypatch, [GOOD_BEFORE, scalar, GOOD_AFTER])

    verdicts = ca.load_verdicts()

    assert set(verdicts) == {
        "2026-08-29_council_alpha",
        "2026-08-29_council_beta",
    }, f"a ledger line of {scalar} must not cost the records around it"
    assert verdicts["2026-08-29_council_alpha"]["choice"] == "kimi"
    assert verdicts["2026-08-29_council_beta"]["choice"] == "claude"


def test_the_aggregate_is_still_written_after_a_bad_ledger_line(tmp_path, monkeypatch):
    """The end the operator sees: `_aggregate.md` exists and carries the verdict.

    `load_verdicts` returning a dict is not the point on its own. The point is
    that `main` reaches the write. Before the fix this raised out of `main` and
    left no file at all.
    """
    council = tmp_path / "council"
    council.mkdir()
    transcript = council / "2026-08-29_council_alpha.md"
    transcript.write_text(
        "---\ntimestamp: 2026-08-29T09:00:00\nmode: independent\n---\n"
        "# Council Consultation - Acme Telecom renewal\n\n"
        "## Question\nDo we renew Acme Telecom at the same terms?\n\n"
        "## Kimi's full response\nRenew, but shorten the term.\n",
        encoding="utf-8",
    )
    _ledger(
        tmp_path,
        monkeypatch,
        ['{"verdict_id": "2026-08-29_council_alpha", "choice": "kimi"}', "null"],
    )
    monkeypatch.setattr(ca, "COUNCIL_DIR", council)
    monkeypatch.setattr(ca, "AGGREGATE_PATH", council / "_aggregate.md")

    assert ca.collect_transcripts(), "empty corpus: this test would pass proving nothing"

    assert ca.main([]) == 0

    written = (council / "_aggregate.md").read_text(encoding="utf-8")
    assert "Acme Telecom renewal" in written
    assert "**CEO verdict:** KIMI" in written
    assert "Kimi=1" in written
