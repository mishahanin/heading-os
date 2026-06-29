"""council-aggregate parses a three-voice transcript including Kimi."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "council-aggregate.py"
_spec = importlib.util.spec_from_file_location("council_aggregate", SCRIPT)
ca = importlib.util.module_from_spec(_spec)
sys.modules["council_aggregate"] = ca
_spec.loader.exec_module(ca)

TRANSCRIPT = """---
timestamp: 2026-06-18T10:00:00
mode: independent
---
# Council Consultation - Partner with X?

## Question
Should we partner with X?

## Gemini's full response
Gemini says yes because alpha.

## Grok's full response
Grok says no because beta.

## Kimi's full response (verbatim)
Kimi says wait because gamma.

## Claude's view
Claude leans yes.
"""


def test_kimi_section_parsed(tmp_path):
    f = tmp_path / "2026-06-18_council.md"
    f.write_text(TRANSCRIPT, encoding="utf-8")
    t = ca.parse_transcript(f)
    assert t is not None
    assert "gamma" in t.kimi_snippet
    assert "kimi" in t.models_present()


def test_kimi_rendered_and_tallied(tmp_path):
    f = tmp_path / "2026-06-18_council.md"
    f.write_text(TRANSCRIPT, encoding="utf-8")
    t = ca.parse_transcript(f)
    out = ca.render([t], {t.path.stem: {"choice": "kimi", "notes": "best call"}})
    # the parsed Kimi snippet is rendered as its own labelled line
    assert "**Kimi:** Kimi says wait because gamma." in out
    # a kimi verdict increments the Kimi tally
    assert "Kimi=1" in out
