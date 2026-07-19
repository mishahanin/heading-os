"""Tests for scripts/chronicle.py pure functions (no ollama, no workspace writes).

Locks the session-date derivation (the meta-first-line bug that dated every
backfilled entry to today), typography normalization, the skip signal, the
topic-based title, and the fail-toward-personal keyword pre-filter.
"""

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.chronicle import (  # noqa: E402
    _cosine,
    _extract_json,
    _keyword_personal,
    _lexical_score,
    _normalize,
    _session_date,
    _title,
)
from scripts.utils.air_gap import is_denied  # noqa: E402


# --- _session_date: the date bug regression -------------------------------

def test_session_date_earliest_turn_timestamp():
    env = {
        "started_at_utc": "",  # meta-first line had a null timestamp
        "user_turns": [{"ts": "2026-06-11T09:00:00.000Z", "text": "hi"}],
        "assistant_turns": [{"ts": "2026-06-11T08:59:00.000Z", "text": "hey"}],
        "system_reminders": [],
    }
    # earliest of all valid stamps, not events[0]
    assert _session_date(env, Path("/nonexistent.jsonl")) == "2026-06-11"


def test_session_date_prefers_started_when_valid():
    env = {"started_at_utc": "2026-05-01T12:00:00Z", "user_turns": [], "assistant_turns": []}
    assert _session_date(env, Path("/nonexistent.jsonl")) == "2026-05-01"


def test_session_date_mtime_fallback_never_today(tmp_path):
    # No timestamps anywhere -> fall back to the file mtime date, NOT today().
    f = tmp_path / "s.jsonl"
    f.write_text("{}", encoding="utf-8")
    past = 1_700_000_000  # 2023-11-14 UTC-ish; a real historical mtime
    os.utime(f, (past, past))
    env = {"started_at_utc": "", "user_turns": [], "assistant_turns": [], "system_reminders": []}
    got = _session_date(env, f)
    assert got == date.fromtimestamp(past).isoformat()  # noqa: DTZ012 - local mtime date, mirrors code under test
    assert got != date.today().isoformat()  # noqa: DTZ011 - asserting NOT today


# --- _normalize: typography -> ASCII --------------------------------------

def test_normalize_curly_to_straight():
    assert _normalize("it’s a “quote”…") == "it's a \"quote\"..."
    assert _normalize("en–dash em—dash") == "en-dash em-dash"


def test_normalize_leaves_plain_ascii():
    assert _normalize("plain 'text' and \"quotes\"") == "plain 'text' and \"quotes\""


# --- _extract_json: model reply parsing + skip signal ---------------------

def test_extract_json_from_fence():
    obj = _extract_json('```json\n{"gist": "x", "class": "business"}\n```')
    assert obj["gist"] == "x" and obj["class"] == "business"


def test_extract_json_skip_signal():
    assert _extract_json('{"skip": true}') == {"skip": True}


def test_extract_json_garbage_returns_none():
    assert _extract_json("no json here") is None


# --- _title: from topics, word-boundary fallback --------------------------

def test_title_from_topics():
    t = _title("2026-06-01", ["memory", "indexing", "recall", "extra"], "long gist text")
    assert t == "Session 2026-06-01 - memory, indexing, recall"  # top 3 only


def test_title_fallback_word_boundary():
    gist = "The user asked about a very specific and moderately long topic today"
    t = _title("2026-06-01", [], gist)
    assert t.startswith("Session 2026-06-01 - ")
    assert "  " not in t and not t.endswith("-")  # no mid-word cut / dangling


# --- _keyword_personal: strong personal-life nouns only -------------------

def test_keyword_personal_strong_nouns():
    # Generic engine defaults fire without any private keyword file present.
    assert _keyword_personal("we went over the mortgage refinance last week")
    assert _keyword_personal("notes on the house purchase and the personal home move")


def test_keyword_personal_ignores_engineering_meta():
    # Talk ABOUT the personal-tagging system is engineering, not personal.
    assert not _keyword_personal(
        "we discuss personal tagging, Личное, PII redaction, and medical examples"
    )


# --- personal-recall scoring (on-the-fly, nothing indexed) ----------------

def test_cosine_identity_and_orthogonal():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_lexical_score_overlap_fraction():
    assert _lexical_score("cabin loan lakeside", "the cabin at lakeside") == 2 / 3
    assert _lexical_score("nothing here", "unrelated text") == 0.0


# --- air-gap invariant: personal chronicle is NEVER indexable --------------

def test_personal_chronicle_is_air_gapped_from_the_index():
    # The `personal` segment is a hard-coded deny, so personal chronicle can
    # never enter a persistent store even if config tried to allow it.
    assert is_denied("chronicle/personal/session-2026-05-01-abc.md", (), [])
    # business chronicle is NOT denied (it is indexed, ranked below the brain).
    assert not is_denied("chronicle/business/session-2026-05-01-abc.md", (), [])
