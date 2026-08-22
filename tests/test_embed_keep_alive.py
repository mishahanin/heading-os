"""The embed call tells ollama how long to keep the model resident.

Measured 2026-08-22 on this machine, against the Windows-side ollama the index
embeds through:

    model unloaded (idle > 5 min)   7.00 s per query
    model resident                  0.87 s per query

The 6.1 s gap is the model being read back into video memory, nothing else.
Ollama's default `keep_alive` is 5 minutes, and a working session pauses for
longer than that constantly -- so almost every FIRST query after a pause paid
the full 7 s, including the `recall-inject` hook that fires on 80% of prompts.

`keep_alive` is per request: whatever the last call passed decides when the
model is dropped. So the value has to travel on the embed payload, or the next
default-carrying request silently resets the window back to five minutes.

Verified the endpoint honours it before the code was written: an embed with
`keep_alive: "30m"` sent at 15:38 came back with `expires_at` 16:08.
"""
import json

import pytest

from scripts.utils import embeddings


@pytest.fixture
def captured(monkeypatch):
    """Capture the decoded payloads `embed` posts, and answer with vectors."""
    seen = []

    def fake_post(url, payload, timeout, attempts=3):
        body = json.loads(payload.decode("utf-8"))
        seen.append(body)
        return [[0.0, 1.0] for _ in body["input"]]

    monkeypatch.setattr(embeddings, "_post_with_retry", fake_post)
    return seen


def test_payload_carries_the_configured_keep_alive(captured, monkeypatch):
    monkeypatch.setattr(embeddings, "_index_config", lambda: {"keep_alive": "45m"})
    embeddings.embed(["a"], model="bge-m3", host="http://h:1")
    assert captured[0]["keep_alive"] == "45m"


def test_keep_alive_defaults_when_the_config_does_not_say(captured, monkeypatch):
    """A config with no `keep_alive` key must not fall back to ollama's 5 minutes."""
    monkeypatch.setattr(embeddings, "_index_config", dict)
    embeddings.embed(["a"], model="bge-m3", host="http://h:1")
    assert captured[0]["keep_alive"] == embeddings.INDEX_EMBED_KEEP_ALIVE_DEFAULT
    assert embeddings.INDEX_EMBED_KEEP_ALIVE_DEFAULT == "30m"


def test_an_explicit_argument_beats_the_config(captured, monkeypatch):
    monkeypatch.setattr(embeddings, "_index_config", lambda: {"keep_alive": "45m"})
    embeddings.embed(["a"], model="bge-m3", host="http://h:1", keep_alive="2h")
    assert captured[0]["keep_alive"] == "2h"


def test_every_batch_carries_it_so_the_last_one_does_not_reset_the_window(
    captured, monkeypatch
):
    """The window is set by the MOST RECENT request, so one batch is not enough.

    A build embeds thousands of chunks in batches. If only the first carried the
    value, the final batch would post a default-carrying payload and hand the
    model back a five-minute lease -- the exact state this change removes.
    """
    monkeypatch.setattr(embeddings, "_index_config", lambda: {"keep_alive": "30m"})
    embeddings.embed(["a", "b", "c", "d", "e"], model="bge-m3", host="http://h:1",
                     batch=2)
    assert len(captured) == 3, "expected three batches"
    assert all(body["keep_alive"] == "30m" for body in captured)


def test_the_config_is_read_once_per_call_not_once_per_batch(monkeypatch):
    """A build makes many batches; the config file is not re-read for each."""
    reads = []

    def counting_config():
        reads.append(1)
        return {"keep_alive": "30m"}

    monkeypatch.setattr(embeddings, "_index_config", counting_config)
    monkeypatch.setattr(
        embeddings, "_post_with_retry",
        lambda url, payload, timeout, attempts=3: [
            [0.0] for _ in json.loads(payload.decode("utf-8"))["input"]
        ],
    )
    embeddings.embed(list("abcdefgh"), model="bge-m3", host="http://h:1", batch=2)
    assert len(reads) == 1, f"config read {len(reads)} times for 4 batches"


def test_no_texts_makes_no_request_and_reads_no_config(monkeypatch):
    def explode():
        raise AssertionError("config read for an empty embed")

    monkeypatch.setattr(embeddings, "_index_config", explode)
    assert embeddings.embed([], model="bge-m3", host="http://h:1") == []


def test_index_embed_keep_alive_reads_the_config(monkeypatch):
    monkeypatch.setattr(embeddings, "_index_config", lambda: {"keep_alive": "1h"})
    assert embeddings.index_embed_keep_alive() == "1h"
    monkeypatch.setattr(embeddings, "_index_config", dict)
    assert embeddings.index_embed_keep_alive() == "30m"


def test_the_shipped_config_declares_a_keep_alive():
    """The value is config, not a literal, so a change is a one-line config edit.

    Same reason the host lives there: the file is where this workspace decides
    how it embeds.
    """
    import yaml

    from scripts.utils.workspace import get_workspace_root

    path = get_workspace_root() / "config" / "memory-index.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg.get("keep_alive"), "config/memory-index.yaml declares no keep_alive"
