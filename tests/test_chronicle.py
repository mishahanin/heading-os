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


# --- ollama host override: generation moves, embeddings do not ------------

def _reload_chronicle(monkeypatch, *, reachable=True, **env):
    """Re-import chronicle with a patched environment.

    The GENERATION endpoint is a module-level constant resolved at import time,
    so the only way to observe an override is a reload under the wanted
    environment. `reachable` stands in for the probe, so no test touches the
    network.

    The EMBEDDING endpoint is no longer a constant here - see `_embed_target`
    below and the comment above `_FRONT_RE` in `scripts/chronicle.py`.
    """
    import importlib

    import scripts.chronicle as mod
    from scripts.utils import ollama_host

    monkeypatch.setattr(ollama_host, "probe", lambda host, **kw: reachable)
    for key in ("HEADING_OS_OLLAMA_HOST", "HEADING_OS_OLLAMA_EMBED_HOST"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(mod)


def _embed_target(monkeypatch, tmp_path, *, config=None, reachable=True, **env):
    """Chronicle's embedder, resolved under a controlled config and environment.

    It comes from `scripts.utils.embeddings.index_embed_target()` since
    2026-08-22, so this helper controls what that function reads instead of
    reloading a module constant. `config=None` writes no file at all, which is
    the "nothing configured" case.
    """
    import yaml

    from scripts.utils import ollama_host, workspace
    from scripts.utils.embeddings import index_embed_target

    monkeypatch.setattr(ollama_host, "probe", lambda host, **kw: reachable)
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    if config is not None:
        (tmp_path / "config" / "memory-index.yaml").write_text(
            yaml.safe_dump(config), encoding="utf-8")
    for key in ("HEADING_OS_OLLAMA_HOST", "HEADING_OS_OLLAMA_EMBED_HOST"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return index_embed_target()


def test_ollama_endpoints_default_to_the_local_daemon(monkeypatch, tmp_path):
    mod = _reload_chronicle(monkeypatch)
    assert mod.OLLAMA_URL == "http://localhost:11434/api/generate"
    host, _model = _embed_target(monkeypatch, tmp_path)
    assert host == "http://localhost:11434"


def test_personal_recall_asks_the_shared_target_for_its_embedder(monkeypatch):
    """The point of the 2026-08-22 change: chronicle no longer picks an embedder.

    Before it, `EMBED_MODEL = "bge-m3"` sat here and the host came from
    `HEADING_OS_OLLAMA_EMBED_HOST` ALONE - a variable unset on the CEO laptop, so
    personal recall ran on the WSL CPU while the index ran on the Windows iGPU,
    and the fallback being documented behaviour is exactly why nothing reported
    it.

    The other half - that no model literal is left behind - belongs to the AST
    detector in `tests/test_embed_model_single_source.py`, not to a substring
    scan here. A substring scan would flag the comment above `_FRONT_RE` that
    RECORDS the old line, which is the same trap the `Tests:` declaration hit on
    2026-08-22: prose about a pattern is not the pattern.
    """
    root = Path(__file__).resolve().parent.parent
    source = (root / "scripts" / "chronicle.py").read_text(encoding="utf-8")
    assert "index_embed_target" in source


def test_generation_host_is_overridable(monkeypatch):
    mod = _reload_chronicle(monkeypatch, HEADING_OS_OLLAMA_HOST="http://172.30.48.1:11436")
    assert mod.OLLAMA_URL == "http://172.30.48.1:11436/api/generate"


def test_generation_override_does_not_move_embeddings(monkeypatch, tmp_path):
    # Embeddings must stay put unless their own setting says otherwise. A shared
    # variable would silently ship every indexed text to whatever host generation
    # points at, and would couple the always-on indexing path to a host chosen
    # for a nightly summarizer. Unchanged by the 2026-08-22 move to a shared
    # target: that move was about WHERE the embedding preference is read, never
    # about merging it with the generation one.
    host, _model = _embed_target(
        monkeypatch, tmp_path, HEADING_OS_OLLAMA_HOST="http://172.30.48.1:11436")
    assert host == "http://localhost:11434"


def test_embedding_host_has_its_own_override(monkeypatch, tmp_path):
    """`HEADING_OS_OLLAMA_EMBED_HOST` still moves embeddings, and only them."""
    host, _model = _embed_target(
        monkeypatch, tmp_path, config={"model": "bge-m3"},
        HEADING_OS_OLLAMA_EMBED_HOST="http://10.0.0.5:11434/")
    assert host == "http://10.0.0.5:11434"      # trailing slash trimmed
    mod = _reload_chronicle(monkeypatch, HEADING_OS_OLLAMA_EMBED_HOST="http://10.0.0.5:11434/")
    assert mod.OLLAMA_URL == "http://localhost:11434/api/generate"


def test_the_config_outranks_the_embed_variable(monkeypatch, tmp_path):
    """A precedence CHANGE, recorded rather than discovered later.

    Before 2026-08-22 `HEADING_OS_OLLAMA_EMBED_HOST` was chronicle's only input,
    so it always won. Now chronicle reads `config/memory-index.yaml` first, the
    same order `scripts/memory-index.py` uses, and the variable is what you reach
    for when the config names no host. That is the price of one source of truth,
    and it is the right side of the trade: an operator who edits the config
    expects the whole workspace to follow.
    """
    host, model = _embed_target(
        monkeypatch, tmp_path,
        config={"host": "http://from-config:11436", "model": "from-config"},
        HEADING_OS_OLLAMA_EMBED_HOST="http://from-env:11434")
    assert host == "http://from-config:11436"
    assert model == "from-config"


def test_unreachable_override_degrades_to_the_local_daemon(monkeypatch):
    # The nightly run must still happen when the GPU-side host is down, which
    # on this laptop means whenever Windows rebooted and the tray did not start.
    mod = _reload_chronicle(
        monkeypatch, reachable=False, HEADING_OS_OLLAMA_HOST="http://172.30.48.1:11436"
    )
    assert mod.OLLAMA_URL == "http://localhost:11434/api/generate"


def test_reload_leaves_the_module_on_defaults(monkeypatch):
    # Guard for the other tests in this file: a stale override would leak into
    # every later import of scripts.chronicle in the same session.
    mod = _reload_chronicle(monkeypatch)
    assert mod.OLLAMA_URL.startswith("http://localhost:11434")
