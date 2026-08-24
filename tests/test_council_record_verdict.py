import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "council-record-verdict.py"
_spec = importlib.util.spec_from_file_location("crv", SCRIPT)
crv = importlib.util.module_from_spec(_spec)
sys.modules["crv"] = crv
_spec.loader.exec_module(crv)


def test_glm_not_a_valid_choice():
    # A tuple since 2026-08-24, not a set: `render_tally` derives the per-choice
    # breakdown from this one name, so the order is now part of what it means.
    assert "glm" not in crv.VALID_CHOICES
    assert crv.VALID_CHOICES == ("claude", "gemini", "grok", "kimi", "mix", "reject")


def test_tally_line_has_no_glm():
    assert "glm" not in crv.render_tally({"a": {"choice": "kimi"}})
