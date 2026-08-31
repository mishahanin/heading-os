"""One Latin-1 note in the council directory killed the scan of every transcript.

`parse_transcript` promises to return None on a shape mismatch, and its
docstring spends a paragraph on the stray files that land in the council
directory: a scratch note, a README, anything not prefixed `_` or `.`. It caught
`OSError` to keep them out.

`UnicodeDecodeError` is a `ValueError`, not an `OSError`. So a stray `.md` an
editor saved as Latin-1 or Windows-1252 raised straight out of
`parse_transcript`, and `collect_transcripts` has no guard of its own: the whole
run died on one file it was never going to include, and no aggregate was
written.

Measured 2026-08-29 before the fix: `UnicodeDecodeError: 'utf-8' codec can't
decode byte 0xe9 in position 5`, raised from `collect_transcripts`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SRC = ROOT / "scripts" / "council-aggregate.py"
_spec = importlib.util.spec_from_file_location("council_aggregate_encoding", _SRC)
ca = importlib.util.module_from_spec(_spec)
sys.modules["council_aggregate_encoding"] = ca
_spec.loader.exec_module(ca)

VALID = (
    "---\ntimestamp: 2026-08-29T11:00:00\nmode: independent\n---\n"
    "# Council Consultation - Acme Telecom pricing\n\n"
    "## Question\nHold the price or discount for a three-year term?\n\n"
    "## Kimi's full response\nHold it. The term is the concession.\n"
)

# A note typed in an editor that saved cp1252: `Café` with a bare 0xe9, plus a
# heading that would otherwise make this parse AS a transcript, so the test
# cannot pass by the file being uninteresting.
LATIN1_NOTE = b"# Caf\xe9 debrief\n\n## Kimi's full response\nSome stray note.\n"


def test_an_undecodable_stray_file_is_skipped_not_raised(tmp_path):
    note = tmp_path / "note.md"
    note.write_bytes(LATIN1_NOTE)

    assert ca.parse_transcript(note) is None


def test_the_transcripts_beside_it_still_get_collected(tmp_path, monkeypatch):
    good = tmp_path / "2026-08-29_council_acme.md"
    good.write_text(VALID, encoding="utf-8")
    (tmp_path / "note.md").write_bytes(LATIN1_NOTE)
    monkeypatch.setattr(ca, "council_dir", lambda p=tmp_path: p)

    assert len(list(tmp_path.glob("*.md"))) == 2, "the corpus must hold both files"

    collected = ca.collect_transcripts()

    assert [t.path.name for t in collected] == ["2026-08-29_council_acme.md"]
    assert collected[0].topic == "Acme Telecom pricing"
    assert "three-year term" in collected[0].question_snippet


def test_the_aggregate_is_written_despite_the_undecodable_file(tmp_path, monkeypatch):
    good = tmp_path / "2026-08-29_council_acme.md"
    good.write_text(VALID, encoding="utf-8")
    (tmp_path / "note.md").write_bytes(LATIN1_NOTE)
    monkeypatch.setattr(ca, "council_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(ca, "verdicts_path", lambda p=tmp_path / "_verdicts.jsonl": p)
    monkeypatch.setattr(ca, "aggregate_path", lambda p=tmp_path / "_aggregate.md": p)

    assert ca.main([]) == 0

    written = (tmp_path / "_aggregate.md").read_text(encoding="utf-8")
    assert "Acme Telecom pricing" in written
    assert "Transcripts: 1." in written
