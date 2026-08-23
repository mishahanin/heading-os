#!/usr/bin/env python3
"""Embedding runs on the pinned host or it does not run at all.

Operator directive, 2026-08-23: *"убери эмбединг из Ollama на WSL и ТОЛЬКО
(всегда) гоняй эмбединг на ollama, которая на Windows"*. This replaces the
2026-08-21 arrangement, where a QUERY was allowed to degrade to the WSL daemon
and only a BUILD refused.

What changed the answer. The degrade was justified by "both hosts run the same
`bge-m3` digest, so the difference is float noise" — true, and beside the point
the morning of 2026-08-23, when the Windows daemon had come back on port 11434
instead of the pinned 11436 and every recall had been quietly answering from the
CPU daemon instead. The fallback did not preserve a capability; it hid an
outage. A refusal is louder than a banner nobody reads.

Two properties carry the directive, and either one alone is defeatable:

1. A configured host is a PIN. Nothing resolves to `localhost` behind it.
2. The pin names the machine, not one port. The Windows daemon serves 11434 or
   11436 depending on how it was launched, and both are the Windows side across
   the WSL gateway, so both are candidates and neither is the local daemon.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import ollama_host as oh  # noqa: E402
from scripts.utils.embeddings import EmbeddingError, index_embed_target  # noqa: E402

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_pinned", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


# --- candidates: a preference may name more than one address ----------------

def test_a_single_preference_yields_one_candidate(monkeypatch):
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    assert oh.host_candidates("auto:11436") == ["http://10.0.0.1:11436"]


def test_a_list_preference_keeps_operator_order(monkeypatch):
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    assert oh.host_candidates(["auto:11434", "auto:11436"]) == [
        "http://10.0.0.1:11434",
        "http://10.0.0.1:11436",
    ]


def test_unusable_entries_drop_out_rather_than_poison_the_list(monkeypatch):
    """A typo in one entry must not disable the entries that are fine."""
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    assert oh.host_candidates(["172.30.48.1:11434", "", "auto:11436"]) == [
        "http://10.0.0.1:11436"
    ]


def test_duplicates_collapse(monkeypatch):
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    assert oh.host_candidates(["auto:11434", "http://10.0.0.1:11434"]) == [
        "http://10.0.0.1:11434"
    ]


# --- the pin: first that answers, never the local daemon --------------------

def test_the_first_reachable_candidate_wins(monkeypatch):
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    monkeypatch.setattr(oh, "probe", lambda host, **k: host.endswith(":11436"))
    assert oh.resolve_pinned_host(["auto:11434", "auto:11436"]) == "http://10.0.0.1:11436"


def test_no_candidate_answering_raises_instead_of_degrading(monkeypatch):
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    monkeypatch.setattr(oh, "probe", lambda host, **k: False)
    with pytest.raises(oh.OllamaHostUnavailable) as err:
        oh.resolve_pinned_host(["auto:11434", "auto:11436"])
    message = str(err.value)
    assert "10.0.0.1:11434" in message and "10.0.0.1:11436" in message, (
        "the operator has to be told which addresses were tried"
    )
    assert oh.LOCAL_HOST not in message.replace("localhost daemon", ""), (
        "naming the local daemon as a way out is how the fallback comes back"
    )


def test_an_unreadable_gateway_raises_too(monkeypatch):
    """`auto:` with no gateway used to mean 'use localhost'. Now it means stop."""
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: None)
    with pytest.raises(oh.OllamaHostUnavailable):
        oh.resolve_pinned_host("auto:11436")


# --- the seam every consumer reads ------------------------------------------

def test_index_embed_target_refuses_the_local_daemon_when_a_host_is_pinned(monkeypatch):
    monkeypatch.setattr(
        "scripts.utils.embeddings._index_config",
        lambda root=None: {"host": ["auto:11434"], "model": "bge-m3"},
    )
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    monkeypatch.setattr(oh, "probe", lambda host, **k: False)
    with pytest.raises(EmbeddingError) as err:
        index_embed_target()
    assert "10.0.0.1:11434" in str(err.value)


def test_the_named_override_still_allows_a_degrade(monkeypatch):
    """`--allow-host-fallback` stays the one documented way out, for a rebuild
    on a machine whose Windows side is genuinely gone."""
    monkeypatch.setattr(
        "scripts.utils.embeddings._index_config",
        lambda root=None: {"host": ["auto:11434"], "model": "bge-m3"},
    )
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "10.0.0.1")
    monkeypatch.setattr(oh, "probe", lambda host, **k: False)
    host, model = index_embed_target(allow_fallback=True)
    assert host == oh.LOCAL_HOST and model == "bge-m3"


def test_an_unpinned_workspace_still_uses_the_local_daemon(monkeypatch):
    """A public clone with no `host:` in config is not pinned to anything, and
    a hard failure there would break the engine for everyone but this laptop."""
    monkeypatch.setattr(
        "scripts.utils.embeddings._index_config", lambda root=None: {"model": "bge-m3"}
    )
    # And no machine file either. THIS machine has one; a clone does not, and
    # the clone is what this test is about.
    monkeypatch.setattr(oh, "machine_hosts", lambda role, **kw: [])
    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    assert index_embed_target() == (oh.LOCAL_HOST, "bge-m3")


# --- the shipped configuration ----------------------------------------------

def test_a_pin_wherever_it_is_written_is_a_gateway_preference_never_a_literal():
    """Whatever pins THIS machine must survive the next WSL restart.

    The pin moved out of the tracked config on 2026-08-23 (see
    tests/test_machine_host_pin.py for why), so this asserts the shape of
    whichever source supplies it here, and asserts nothing when nothing does -
    an unpinned clone is a valid clone.

    The shape matters because WSL2 picks a new gateway address on every restart.
    A literal that works today is a silent outage after the next reboot, and
    with a refusing pin it is a loud one - either way it is a defect written by
    hand into a file that looks correct.
    """
    from scripts.utils.embeddings import index_embed_preference

    preference = index_embed_preference()
    if not preference:
        return
    entries = preference if isinstance(preference, list) else [preference]
    for entry in entries:
        assert str(entry).startswith("auto"), (
            f"{entry!r} is a literal address; WSL2 renumbers its gateway on "
            "restart, so this breaks on the next reboot"
        )
    assert "11434" in " ".join(map(str, entries)), (
        "the Windows Ollama desktop app binds its default port unless launched "
        "with an explicit OLLAMA_HOST, and on 2026-08-23 it did exactly that"
    )


def test_a_query_that_cannot_embed_says_so_in_its_json():
    """The recall hook discards stderr, so a red banner never reaches the
    session. The refusal has to travel as data or it does not travel."""
    payload = mi.embed_unavailable_payload("no ollama at http://10.0.0.1:11434")
    assert payload["gap"] is True
    assert payload["hits"] == []
    assert "10.0.0.1:11434" in payload["embed_unavailable"]["reason"]
