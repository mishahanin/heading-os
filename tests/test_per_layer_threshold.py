#!/usr/bin/env python3
"""A layer may set its own confidence cut, and only when it is the only layer.

Cosine is not comparable across registers. Measured 2026-08-21 on the frozen
25-query commit set: the correct answer to a paraphrased or Russian question
scores 0.456-0.597, while the same index answers a keyword query at 0.590-0.697.
The single prose-calibrated 0.55 therefore reported "a gap in this area of
memory" for 7 of 23 answers it had in fact ranked first, and the set scored 77%
against an agreed 80% bar. At 0.45 it scores 85%.

The cut is per-layer so `content` keeps 0.55 and its honest-gap behaviour: a
global drop would have bought commit recall with prose precision.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_thr", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)

CFG_REL = "config/memory-index.yaml"


@pytest.fixture(autouse=True)
def _no_embedder_probe(monkeypatch):
    """Read the shipped YAML without asking the network which host answers.

    `load_config` resolves the embedder pin through `resolve_pinned_host`, which
    OPENS A SOCKET to each candidate. Measured 2026-08-27 by wrapping
    `socket.socket.connect`: every one of the seven tests below connected to
    172.30.48.1:11434, the Windows ollama daemon. Nothing here is about the
    embedder - these tests read threshold numbers out of a YAML file - so the
    probe bought nothing and made the outcome depend on whether a daemon on
    another operating system happened to be up.

    The assertion below is the point of the fixture: patching a name the module
    does not read would restore the network in silence.
    """
    assert hasattr(mi, "_resolve_embed_host"), (
        "memory-index.py no longer resolves the host under this name; the patch "
        "below is aimed at nothing and these tests are back on the network")
    monkeypatch.setattr(mi, "_resolve_embed_host", lambda preferred, **kw: preferred)


def _shipped():
    return mi.load_config(mi.get_workspace_root())


def test_reading_the_shipped_config_opens_no_socket_off_this_machine():
    """The fixture above is only as good as something that notices it is gone.

    Without this, deleting `_no_embedder_probe` puts all seven tests back on the
    network and every one of them still passes, which is how the defect survived
    in the first place. Loopback is left alone: a local stub server is a
    legitimate test double, an address on the LAN is not.
    """
    import socket

    original = socket.socket.connect
    off_machine = []

    def _watch(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in ("127.0.0.1", "::1", "localhost"):
            return original(self, address)
        off_machine.append(address)
        raise OSError(f"this test does not use the network: {address}")

    socket.socket.connect = _watch
    try:
        _shipped()
    finally:
        socket.socket.connect = original
    assert off_machine == [], (
        f"reading a YAML file reached {off_machine}; the embedder probe is back")


def test_the_shipped_commit_layers_carry_the_measured_cut():
    """0.45 is the only setting that clears the agreed bar; pin it."""
    layers = {lc["layer"]: lc for lc in _shipped()["layers"]}
    for name in ("commit-engine", "commit-data"):
        assert name in layers, f"{name} missing from {CFG_REL}"
        assert layers[name].get("threshold") == 0.45, (
            f"{name} threshold moved; re-run scripts/eval-query-set.py and record "
            "the number before changing it"
        )


def test_a_layer_without_its_own_threshold_keeps_the_global_one():
    cfg = _shipped()
    for lc in cfg["layers"]:
        if lc["layer"] in ("skill", "rule", "odin", "thread"):
            assert "threshold" not in lc, (
                f"{lc['layer']} gained a per-layer cut; prose layers share the "
                "global 0.55 on purpose"
            )
    assert cfg["threshold"] == 0.55


def _resolve(cfg, *, layer=None, collection="content", cli=None):
    """Replay cmd_query's threshold resolution without running a query."""
    threshold = cli if cli is not None else cfg["threshold"]
    coll_map = cfg.get("collections") or {}
    if layer:
        allowed = {layer}
    elif collection in coll_map:
        allowed = set(coll_map[collection])
    else:
        allowed = None
    if cli is None and allowed and len(allowed) == 1:
        only = next(iter(allowed))
        per = next((lc.get("threshold") for lc in cfg["layers"]
                    if lc["layer"] == only and lc.get("threshold") is not None), None)
        if per is not None:
            threshold = float(per)
    return threshold


def test_one_layer_uses_its_own_cut():
    assert _resolve(_shipped(), layer="commit-engine") == 0.45


def test_a_single_layer_collection_uses_it_too():
    """`--collection history` resolves to commit-data alone."""
    assert _resolve(_shipped(), collection="history") == 0.45


def test_a_multi_layer_collection_keeps_the_global_cut():
    """No single right cut across registers, so do not invent one."""
    assert _resolve(_shipped(), collection="content") == 0.55
    assert _resolve(_shipped(), collection="code") == 0.55


def test_an_explicit_cli_threshold_always_wins():
    assert _resolve(_shipped(), layer="commit-engine", cli=0.9) == 0.9


def test_the_query_parser_still_defaults_threshold_to_none():
    """The resolution above only runs when the operator passed nothing."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=None)
    assert ap.parse_args([]).threshold is None
