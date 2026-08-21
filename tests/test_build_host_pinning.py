#!/usr/bin/env python3
"""A BUILD refuses the fallback host. A QUERY accepts it.

Misha's standing instruction, 2026-08-21: always embed on the Windows GPU host.
The risk he named is split brain -- part of a store built by one embedder and part
by another, with cosine comparing them and giving a plausible wrong answer either
way.

The two paths deserve opposite answers, and the reason is asymmetric cost:

A BUILD writes vectors that live in the store for months. Silently writing a few
thousand of them from a different embedder is exactly the split brain, and the
operator finds out never. So a build on the wrong host STOPS, with the command to
override.

A QUERY embeds one throwaway vector to compare against the store. Measured
2026-08-21, the Windows and WSL hosts run the same `bge-m3` digest and agree to
cosine 0.99997 -- four orders of magnitude below the 0.12 near-miss margin. So a
query keeps working when Windows sleeps, because refusing recall to avoid float
noise trades a real capability for an imaginary risk.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "memory-index.py"
_spec = importlib.util.spec_from_file_location("memory_index_host", _SRC)
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)


def test_a_resolved_auto_host_is_not_a_fallback():
    assert mi.host_fell_back("auto:11436", "http://172.30.48.1:11436") is False


def test_an_auto_host_that_landed_on_localhost_is_a_fallback():
    assert mi.host_fell_back("auto:11436", "http://localhost:11434") is True


def test_a_literal_host_that_survived_is_not_a_fallback():
    assert mi.host_fell_back("http://gpu.box:11436", "http://gpu.box:11436") is False


def test_a_literal_host_that_was_dropped_is_a_fallback():
    assert mi.host_fell_back("http://gpu.box:11436", "http://localhost:11434") is True


def test_no_preference_configured_is_never_a_fallback():
    """Nothing was asked for, so nothing was denied."""
    assert mi.host_fell_back(None, "http://localhost:11434") is False
    assert mi.host_fell_back("", "http://localhost:11434") is False


# --- the announcement ------------------------------------------------------
#
# Detecting the fallback is worth nothing if nobody is told. Operator directive,
# 2026-08-21: *"если система на Windows (там где GPU) не доступна, ты СРАЗУ
# говоришь об этом, громко, красным цветом"*. Three properties carry that, and a
# silent regression in any one of them restores the old failure mode exactly.

def test_the_banner_is_red_and_names_both_hosts():
    out = mi.host_fallback_banner("auto:11436", "http://localhost:11434")
    assert out is not None
    assert mi.RED in out, "the operator asked for red; a plain-text warning is not it"
    assert "auto:11436" in out and "http://localhost:11434" in out
    assert "GPU" in out


def test_no_banner_when_the_preferred_host_answered():
    """A banner on every run is a banner nobody reads."""
    assert mi.host_fallback_banner("auto:11436", "http://172.30.48.1:11436") is None
    assert mi.host_fallback_info("auto:11436", "http://172.30.48.1:11436") is None


def test_the_fallback_is_also_data_not_only_a_printed_line():
    """`recall-inject.py` throws stderr away on a zero exit; JSON is how the
    session -- the surface Misha actually reads -- ever learns about this."""
    info = mi.host_fallback_info("auto:11436", "http://localhost:11434")
    assert info == {"wanted": "auto:11436", "got": "http://localhost:11434"}


def test_loading_the_config_announces_a_fallback_by_itself(tmp_path, monkeypatch, capsys):
    """The announcement sits in load_config so a NEW subcommand cannot omit it."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory-index.yaml").write_text(
        'host: "http://gpu.box:11436"\nmodel: bge-m3\n', encoding="utf-8"
    )
    monkeypatch.setattr(mi, "resolve_ollama_host", lambda h, **kw: "http://localhost:11434")
    cfg = mi.load_config(tmp_path)
    assert cfg["host_preferred"] == "http://gpu.box:11436"
    assert "GPU EMBEDDER NOT AVAILABLE" in capsys.readouterr().err


def test_the_shipped_config_still_prefers_the_gpu_host():
    """The pin is the control. A silent edit to localhost would defeat all of it."""
    import yaml
    raw = yaml.safe_load((mi.get_workspace_root() / "config/memory-index.yaml").read_text())
    assert raw["host"] == "auto:11436", (
        "config/memory-index.yaml no longer prefers the Windows GPU host; "
        "builds would embed on the WSL CPU and split the store"
    )
