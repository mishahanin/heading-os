"""Four ways the ops radar crashed, or lied, about what it measures.

`scripts/utils/ops_signals` runs inside one radar pass with no per-signal
exception handling, so an uncaught exception in any wrapper takes every other
signal down with it. The file has already fixed that exact class four times
(`probe`, `queue_state`, `odin_cadence_state`, `_read_trend_records`) and each
fix landed in one function while its siblings kept the hole. Measured
2026-08-30, on the file as it stands:

1. `_embed_model_present` -- `/api/tags` answering `null`, `[]` or `"x"` parses
   and then raises AttributeError on `body.get`, which no except clause here
   catches. It escapes through `ollama_accel_state` into the radar run.
2. `_index_source_globs` -- `config/memory-index.yaml` is operator-editable. A
   config that parses to a LIST raised AttributeError on `cfg.get`; a `layers:`
   holding strings, or a mapping (whose iteration yields keys), raised the same
   on `layer.get`. The docstring promised a fall back to the hand-written dirs.
3. `publish_state` -- `len(data.get("files", []))` on `{"pending": 0, "files": 3}`
   raised TypeError out of a function documented to degrade to 0 and be "never
   an emergency".
4. `_repo_uncommitted` -- `record[3:].strip()` undid what `-z` was chosen for. A
   filename with a leading or trailing space was mangled, `stat()` raised, the
   path was dropped by `except OSError: continue`, and the oldest sitting change
   was UNDERSTATED. Backup debt then reads younger than it is, which is the
   direction that keeps the signal quiet.

Plus one signal that reported green on a misconfiguration the embedder refuses
to start on: `ollama_accel_state` folded "nothing is pinned" together with "a
host IS pinned and the pin names no usable address".

No test here reaches the network: the tags endpoint is stubbed at
`urllib.request.urlopen`, and git runs against scratch repositories.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from scripts.utils import ops_signals as ops

# Every one of these is well-formed and none is the shape the reader assumed.
NON_OBJECT_JSON = ["null", "[]", '"x"', "3"]
WRONG_SHAPED_INDEX_CONFIGS = [
    "- just\n- a\n- list\n",              # the whole document is a list
    "layers:\n  - just-a-string\n",       # layers holds scalars
    "layers:\n  a: 1\n",                  # layers is a mapping; iteration yields keys
    "layers: 7\n",                        # layers is not iterable as records at all
]


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_malformed_corpora_are_not_empty():
    """A guard over an empty corpus is green for free."""
    assert len(NON_OBJECT_JSON) >= 4
    assert len(WRONG_SHAPED_INDEX_CONFIGS) >= 4
    for body in NON_OBJECT_JSON:
        assert not isinstance(json.loads(body), dict)


# ---------------------------------------------------------------- _embed_model_present

@pytest.mark.parametrize("payload", NON_OBJECT_JSON)
def test_a_tags_endpoint_answering_a_non_object_is_unknown_not_a_crash(
    payload, monkeypatch
):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(payload))
    assert ops._embed_model_present("http://stub:11434", timeout=1) is None


def test_a_well_shaped_tags_reply_still_answers_yes_and_no(monkeypatch):
    """The negative case: the guard must not turn every answer into unknown."""
    monkeypatch.setattr("scripts.utils.embeddings.index_embed_model",
                        lambda **_kw: "bge-m3")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(
        json.dumps({"models": [{"name": "bge-m3:latest"}]})))
    assert ops._embed_model_present("http://stub:11434", timeout=1) is True

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(
        json.dumps({"models": [{"name": "gemma3:4b"}]})))
    assert ops._embed_model_present("http://stub:11434", timeout=1) is False


def test_a_non_object_tags_reply_does_not_escape_the_accel_signal(monkeypatch, tmp_path):
    """The blast radius, not just the function: this used to leave
    `ollama_accel_state` and end the whole radar pass."""
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        'model: bge-m3\nhost: "http://127.0.0.1:11499"\n', encoding="utf-8")
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    monkeypatch.setattr("scripts.utils.ollama_host.probe", lambda *a, **k: True)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse("null"))

    signal = ops.ollama_accel_state(engine, timeout=1)
    assert signal["key"] == "ollama_accel"
    assert signal["value"]["model_present"] is None


# ---------------------------------------------------------------- _index_source_globs

@pytest.mark.parametrize("content", WRONG_SHAPED_INDEX_CONFIGS)
def test_a_wrong_shaped_index_config_falls_back_instead_of_crashing(content, tmp_path):
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(content, encoding="utf-8")
    assert ops._index_source_globs(engine) is None


def test_a_well_shaped_index_config_still_yields_its_globs(tmp_path):
    """The negative case, including the brace expansion and the git-log layer
    that legitimately carries no glob."""
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        "layers:\n"
        "  - name: notes\n"
        "    glob: 'knowledge/**/*.{md,txt}'\n"
        "  - name: commits\n"
        "    source: git-log\n",
        encoding="utf-8")
    assert ops._index_source_globs(engine) == [
        "knowledge/**/*.md", "knowledge/**/*.txt",
    ]


def test_a_wrong_shaped_index_config_leaves_the_freshness_signal_standing(tmp_path):
    """The wrapper the crash actually killed. It must still classify, and say on
    stderr that it narrowed the sweep."""
    engine = tmp_path / "engine"
    data = tmp_path / "data"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8")
    (data / ".memory-index").mkdir(parents=True)
    (data / ".memory-index" / "index.db").write_bytes(b"x")

    signal = ops.index_freshness_state(engine, data, now=time.time())
    assert signal["key"] == "memory_index"
    assert signal["value"] != "absent"


# ---------------------------------------------------------------- publish_state

@pytest.mark.parametrize("payload,expected", [
    ('{"pending": 0, "files": 3}', 0),          # the TypeError case
    ('{"files": "three"}', 0),
    ('{"pending": [1, 2]}', 0),                 # int() of a list
    ('{"pending": 4}', 4),
    ('{"changed": 2}', 2),
    ('{"files": ["a", "b", "c"]}', 3),
])
def test_the_publish_preview_degrades_on_every_shape_it_can_be_handed(
    payload, expected, tmp_path
):
    engine = tmp_path / "engine"
    (engine / "scripts").mkdir(parents=True)
    (engine / "scripts" / "publish-corporate.py").write_text(
        f"print({payload!r})\n", encoding="utf-8")
    signal = ops.publish_state(engine)
    assert signal["value"] == expected
    assert signal["due"] is (expected >= ops.PUBLISH_PENDING)


# ---------------------------------------------------------------- _repo_uncommitted

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True)


NOW = 1_800_000_000.0        # a pinned instant; no test here reads the host clock


class _PinnedClock:
    """Stands in for the `time` module inside ops_signals, so ages are exact."""

    def time(self) -> float:
        return NOW

    def __getattr__(self, name):              # sleep, monotonic, anything else
        return getattr(time, name)


@pytest.mark.skipif(os.name != "posix", reason="filenames with spaces need POSIX")
def test_the_oldest_dirty_file_is_found_even_when_its_name_is_padded(
    tmp_path, monkeypatch
):
    """A leading or trailing space is a legal filename, and `.strip()` renamed it
    into one that does not exist. The two padded files here are the OLDEST, so
    losing them understated the debt by 199 hours and the signal fell silent:
    `classify_backup` needs an age past BACKUP_UNCOMMITTED_HOURS to fire.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", ".")

    for name, age_hours in {" lead.md": 200.0, "trail .md": 150.0,
                            "fresh.md": 1.0}.items():
        path = repo / name
        path.write_text("x", encoding="utf-8")
        stamp = NOW - age_hours * 3600.0
        os.utime(path, (stamp, stamp))

    monkeypatch.setattr(ops, "time", _PinnedClock())
    count, oldest = ops._repo_uncommitted(repo)
    assert count == 3
    assert oldest == pytest.approx(200.0, abs=0.01)

    # The consequence, not just the number: 200 hours of uncommitted work is
    # past the crunch-piercing floor, and the pre-fix 1.0 was not even due.
    signal = ops.classify_backup(count, oldest, 0)
    assert signal["due"] is True
    assert signal["severity"] == "critical"


@pytest.mark.skipif(os.name != "posix", reason="filenames with spaces need POSIX")
def test_a_padded_filename_is_still_counted_as_one_change(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    (repo / " lead.md").write_text("x", encoding="utf-8")
    count, _oldest = ops._repo_uncommitted(repo)
    assert count == 1


def test_a_clean_repo_still_reports_zero(tmp_path):
    """The negative case: the change must not invent debt out of an empty scan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    assert ops._repo_uncommitted(repo) == (0, 0.0)


# ---------------------------------------------------------------- ollama_accel_state

@pytest.mark.parametrize("pin", ["htp://typo", "not-a-url", "172.30.48.1:11436"])
def test_an_unresolvable_embed_pin_is_reported_and_not_called_unconfigured(
    pin, tmp_path, monkeypatch
):
    """`resolve_pinned_host` refuses these; the monitor called them ok.

    The embedder is PINNED, so a pin naming no usable address means nothing can
    embed at all -- the same outcome as the host being down, and the opposite of
    "most operators have one daemon and that is fine".
    """
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "ollama-hosts.yaml").write_text(
        f'embed: "{pin}"\n', encoding="utf-8")
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)

    # Establish the contradiction rather than assuming it: the resolver on the
    # embedding path refuses this very string.
    from scripts.utils.ollama_host import OllamaHostUnavailable, resolve_pinned_host
    with pytest.raises(OllamaHostUnavailable):
        resolve_pinned_host(pin)

    signal = ops.ollama_accel_state(engine, timeout=1)
    assert signal["due"] is True
    assert signal["severity"] == "high"
    assert signal["value"]["configured"] is True
    assert signal["value"]["pin_unresolvable"] is True
    assert "not configured" not in signal["summary"]
    # It was never probed, so the summary must not claim a host failed to answer.
    assert "down" not in signal["summary"]


def test_no_pin_at_all_is_still_the_quiet_normal_state(tmp_path, monkeypatch):
    """The negative case, and the one that matters most: a public clone with one
    daemon must not start paging."""
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)

    signal = ops.ollama_accel_state(engine, timeout=1)
    assert signal["due"] is False
    assert signal["severity"] == "ok"
    assert signal["value"]["configured"] is False
    assert signal["value"]["pin_unresolvable"] is False


def test_a_pin_naming_only_the_local_daemon_is_still_not_an_accelerated_host(
    tmp_path, monkeypatch
):
    """The discriminator is whether the pin RESOLVES, not whether it survived the
    local-host filter. `http://localhost:11434` is a perfectly usable address
    that is deliberately not an accelerated one."""
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        'model: bge-m3\nhost: "http://localhost:11434"\n', encoding="utf-8")
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)

    signal = ops.ollama_accel_state(engine, timeout=1)
    assert signal["due"] is False
    assert signal["value"]["configured"] is False
    assert signal["value"]["pin_unresolvable"] is False


def test_a_reachable_pinned_host_is_untouched_by_the_new_branch(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        'model: bge-m3\nhost: "http://127.0.0.1:11499"\n', encoding="utf-8")
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    monkeypatch.setattr("scripts.utils.ollama_host.probe", lambda *a, **k: True)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(
        json.dumps({"models": [{"name": "bge-m3:latest"}]})))

    signal = ops.ollama_accel_state(engine, timeout=1)
    assert signal["value"]["configured"] is True
    assert signal["value"]["reachable"] is True
    assert signal["value"]["pin_unresolvable"] is False
    assert signal["due"] is False


def _accel_against_tags(engine: Path, tags: list[str], monkeypatch) -> dict:
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    monkeypatch.setattr("scripts.utils.ollama_host.probe", lambda *a, **k: True)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(
        json.dumps({"models": [{"name": name} for name in tags]})))
    return ops.ollama_accel_state(engine, timeout=1)


@pytest.mark.parametrize("pulled,expected", [
    (["bge-m3:latest"], False),               # the host holds a DIFFERENT model
    (["nomic-embed-text:latest"], True),      # the host holds the named one
])
def test_the_accel_signal_asks_about_the_model_its_own_root_names(
    pulled, expected, tmp_path, monkeypatch
):
    """Reported by no audit. Found and measured 2026-08-30.

    `ollama_accel_state(engine_root)` read the pinned HOST out of the root it was
    handed and then asked `index_embed_model()` for the MODEL -- which took no
    root at all and fell to `get_workspace_root()`. On a machine with more than
    one clone the two halves of one answer came from two different configs:
    measured with an engine root naming `nomic-embed-text` against a host holding
    only `bge-m3`, the monitor answered "the embed model is pulled". It is the
    same defect the hardcoded `EMBED_MODEL_PREFIX` was removed for on 2026-08-22
    -- a monitor reporting on a model the workspace it names does not use -- one
    clone deeper. `index_embed_preference` has always taken a `root`; its sibling
    did not.
    """
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        'model: nomic-embed-text\nhost: "http://127.0.0.1:11499"\n',
        encoding="utf-8")

    signal = _accel_against_tags(engine, pulled, monkeypatch)
    assert signal["value"]["reachable"] is True
    assert signal["value"]["model_present"] is expected
    assert signal["due"] is (not expected)


def test_the_sending_status_still_counts_toward_the_queue_signal(tmp_path):
    """Not one of the five defects: a guard on the change the main session landed
    minutes before this file was written, so a later shape fix cannot quietly
    drop it again. A card claimed by a sender that then died never leaves
    `sending` on its own, and it must stay visible to the radar.
    """
    from scripts.bridge_daemon.sources.action_queue import SENDING

    queue = tmp_path / "outputs" / "operations" / "action-queue"
    queue.mkdir(parents=True)
    (queue / "queue.json").write_text(json.dumps({"actions": [
        {"status": SENDING, "draft_status": "ready_for_review"},
        {"status": "pending", "draft_status": "ready_for_review"},
        {"status": "send_failed"},
        {"status": "sent", "draft_status": "ready_for_review"},
    ]}), encoding="utf-8")

    signal = ops.queue_state(tmp_path)
    assert signal["value"] == {"ready": 2, "failed": 1}
    assert signal["severity"] == "high"


if __name__ == "__main__":            # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
