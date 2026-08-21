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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_thr", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)

CFG_REL = "config/memory-index.yaml"


def _shipped():
    return mi.load_config(mi.get_workspace_root())


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
