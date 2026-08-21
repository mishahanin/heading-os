#!/usr/bin/env python3
"""The store records WHICH embedder built it, so split brain is detectable.

Vectors from two different models are not comparable, and cosine gives no hint --
it returns a plausible number either way. Until 2026-08-21 the `meta` table
recorded the model name and nothing else, so "was this store built by one
embedder?" had no answer on disk. Misha asked the question directly and it could
only be answered by re-running both hosts and comparing, which is not an answer a
future session can reach.

Measured that day, for the record: the Windows GPU host and the WSL CPU host run
the SAME `bge-m3` digest (`7907646426070047`, F16) and agree to cosine 0.99997 on
the same text -- float noise from different kernels, four orders of magnitude
below the 0.12 near-miss margin. So the risk was not realised. The provenance is
recorded anyway, because "we checked once" is not a control.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib.util  # noqa: E402

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_prov", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


def _meta(conn) -> dict:
    return dict(conn.execute("SELECT key, val FROM meta"))


def test_a_build_records_the_model_and_the_host(tmp_path):
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://172.30.48.1:11436")
    assert _meta(conn)["model"] == "bge-m3"
    assert _meta(conn)["embed_host"] == "http://172.30.48.1:11436"


def test_the_same_host_twice_reports_no_mismatch(tmp_path):
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://172.30.48.1:11436")
    assert mi.provenance_mismatch(conn, model="bge-m3",
                                  host="http://172.30.48.1:11436") is None


def test_a_different_host_is_reported(tmp_path):
    """Same model, different machine: worth saying, not worth refusing."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://172.30.48.1:11436")
    msg = mi.provenance_mismatch(conn, model="bge-m3", host="http://localhost:11434")
    assert msg and "localhost:11434" in msg and "172.30.48.1:11436" in msg


def test_a_different_model_is_reported_too(tmp_path):
    """This is the one that actually breaks cosine. It must never be silent."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1")
    msg = mi.provenance_mismatch(conn, model="nomic-embed-text", host="http://x:1")
    assert msg and "nomic-embed-text" in msg


def test_a_store_with_no_provenance_yet_reports_nothing(tmp_path):
    """An old store predates this field. Absence is not a mismatch."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://x:1") is None


# --- the digest: what a tag cannot tell you --------------------------------
#
# Misha, 2026-08-21: *"а как будет работать sync между инстанс (ембедингс) между
# Windows и WSL?"* Nothing syncs the two Ollama installs. They are independent
# services that happen to hold the same weights today, and an auto-update on one
# of them swaps those weights under an unchanged tag. The name stays `bge-m3`,
# the host string stays whatever it was, and every vector written afterwards is
# incomparable with every vector written before. The digest is the only field
# that moves, so it is the only field that can raise the alarm.

def test_the_digest_is_stored_when_the_host_reports_one(tmp_path):
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1", digest="7907646426070047aa")
    assert _meta(conn)["model_digest"] == "7907646426070047aa"


def test_the_same_digest_is_not_a_mismatch(tmp_path):
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1", digest="deadbeefcafe00")
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://x:1",
                                  digest="deadbeefcafe00") is None


def test_a_changed_digest_under_the_same_tag_is_loud(tmp_path):
    """The silent split brain. Same name, same host, different weights."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1", digest="aaaaaaaaaaaa11")
    msg = mi.provenance_mismatch(conn, model="bge-m3", host="http://x:1",
                                 digest="bbbbbbbbbbbb22")
    assert msg and "WEIGHTS CHANGED" in msg
    assert "aaaaaaaaaaaa" in msg and "bbbbbbbbbbbb" in msg


def test_an_unknown_digest_never_claims_a_match(tmp_path):
    """The tags endpoint can hiccup. "I could not tell" must not read as "same"
    -- and must not overwrite the evidence already on disk either."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1", digest="aaaaaaaaaaaa11")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1", digest=None)
    assert _meta(conn)["model_digest"] == "aaaaaaaaaaaa11"
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://x:1",
                                  digest=None) is None


def test_a_store_predating_the_digest_field_is_judged_silently(tmp_path):
    """No stored digest means no evidence, so no claim in either direction."""
    conn = mi.open_store(tmp_path, ".idx/index.db")
    mi.record_provenance(conn, model="bge-m3", host="http://x:1")
    assert mi.provenance_mismatch(conn, model="bge-m3", host="http://x:1",
                                  digest="cccccccccccc33") is None


# --- reading the digest off a live host ------------------------------------

def test_model_digest_matches_the_tag_ignoring_the_latest_suffix(monkeypatch):
    """`bge-m3` and `bge-m3:latest` are the same model; the config writes one
    and ollama reports the other."""
    import json as _json
    from scripts.utils import embeddings as emb

    body = _json.dumps({"models": [
        {"name": "qwen3:8b", "digest": "ffff"},
        {"name": "bge-m3:latest", "digest": "7907646426070047ab"},
    ]}).encode()
    monkeypatch.setattr(emb.urllib.request, "urlopen", lambda *a, **k: _ctx(body))
    assert emb.model_digest(model="bge-m3", host="http://x:1") == "7907646426070047ab"


def test_model_digest_returns_none_when_the_host_is_down(monkeypatch):
    """Unknown, never a guess. A build must not fail over a diagnostic."""
    import urllib.error
    from scripts.utils import embeddings as emb

    def _boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(emb.urllib.request, "urlopen", _boom)
    assert emb.model_digest(model="bge-m3", host="http://x:1") is None


def test_model_digest_returns_none_when_the_model_is_absent(monkeypatch):
    import json as _json
    from scripts.utils import embeddings as emb

    body = _json.dumps({"models": [{"name": "qwen3:8b", "digest": "ffff"}]}).encode()
    monkeypatch.setattr(emb.urllib.request, "urlopen", lambda *a, **k: _ctx(body))
    assert emb.model_digest(model="bge-m3", host="http://x:1") is None


class _ctx:
    """Minimal context manager standing in for urlopen's response."""
    def __init__(self, body): self._body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._body


def test_a_host_with_no_scheme_yields_none_instead_of_crashing_the_build():
    """`urlopen("stub/api/tags")` raises ValueError before any socket opens.

    Found 2026-08-21 by the suite: wiring the digest into `_build_store` broke
    11 tests that pass `host: "stub"`. The same shape hits production on a config
    typo -- `172.30.48.1:11436` without `http://` -- and would abort a build with
    `unknown url type` instead of the embedder's own clear message. A diagnostic
    must never be the thing that stops the work.
    """
    from scripts.utils import embeddings as emb
    assert emb.model_digest(model="bge-m3", host="stub") is None
    assert emb.model_digest(model="bge-m3", host="172.30.48.1:11436") is None
