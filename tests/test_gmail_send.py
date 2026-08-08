"""Draft selection for gmail-send.py.

The network path is not exercised here. What is exercised is the decision that
precedes a send: which draft, and whether the answer is unambiguous. Sending
the wrong draft is unrecoverable, so ambiguity must raise rather than resolve.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load():
    """Import the hyphenated CLI script by path."""
    path = PROJECT_ROOT / "scripts" / "gmail-send.py"
    spec = importlib.util.spec_from_file_location("gmail_send_cli", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gmail_send_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


gmail_send = _load()
select_draft = gmail_send.select_draft
DraftSelectionError = gmail_send.DraftSelectionError


DRAFTS = [
    {"id": "r1", "to": "support@github.com", "subject": "Private information removal: refs"},
    {"id": "r2", "to": "someone@example.com", "subject": "Lunch on Thursday"},
    {"id": "r3", "to": "", "subject": ""},
]


def test_exact_id_selects_that_draft():
    assert select_draft(DRAFTS, draft_id="r2") == "r2"


def test_unknown_id_raises():
    with pytest.raises(DraftSelectionError, match="no draft with id"):
        select_draft(DRAFTS, draft_id="nope")


def test_subject_substring_is_case_insensitive():
    assert select_draft(DRAFTS, match_subject="private INFORMATION") == "r1"


def test_subject_with_no_match_raises():
    with pytest.raises(DraftSelectionError, match="no draft whose subject"):
        select_draft(DRAFTS, match_subject="invoice")


def test_ambiguous_subject_raises_and_names_the_candidates():
    drafts = DRAFTS + [{"id": "r4", "to": "x@y.z", "subject": "Private information, part two"}]
    with pytest.raises(DraftSelectionError) as exc:
        select_draft(drafts, match_subject="private information")
    message = str(exc.value)
    assert "r1" in message and "r4" in message
    assert "--draft-id" in message


def test_no_selector_raises():
    with pytest.raises(DraftSelectionError, match="exactly one"):
        select_draft(DRAFTS)


def test_both_selectors_raise():
    with pytest.raises(DraftSelectionError, match="exactly one"):
        select_draft(DRAFTS, draft_id="r1", match_subject="private")


def test_draft_with_empty_subject_never_matches_a_substring():
    """An untitled draft must not be swept up by a subject search."""
    assert select_draft(DRAFTS, match_subject="removal") == "r1"
