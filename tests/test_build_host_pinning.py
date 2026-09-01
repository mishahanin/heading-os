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


# --- the shape the preference actually arrives in -----------------------------
#
# Every case above hands `host_fell_back` a single string, and the first line of
# the function is `preferred if isinstance(preferred, (list, tuple)) else
# [preferred]`. That branch was never taken by a test, and it is the LIVE one on
# this operator's machine: `cfg["host_preferred"]` comes from
# `index_embed_preference()`, whose third and normal source is
# `machine_hosts("embed")`, declared `-> list[str]`. MEASURED 2026-09-01:
# replacing the whole line with `entries = [preferred]` left all 10 tests in this
# file green, while a two-entry pin whose FIRST host answered would have been
# announced as a fallback - a red "GPU EMBEDDER NOT AVAILABLE" banner on every
# run, and, since `cmd_build` refuses on the same signal, a build that stops
# because it succeeded.

def test_a_list_preference_is_satisfied_by_any_entry_that_answered():
    hosts = ["http://172.30.48.1:11436", "http://gpu.box:11436"]
    assert mi.host_fell_back(hosts, "http://172.30.48.1:11436") is False
    assert mi.host_fell_back(hosts, "http://gpu.box:11436") is False


def test_a_list_preference_none_of_which_answered_is_a_fallback():
    """The negative case: a list must still be able to report a real drop."""
    hosts = ["http://172.30.48.1:11436", "http://gpu.box:11436"]
    assert mi.host_fell_back(hosts, "http://localhost:11434") is True


def test_a_list_preference_of_auto_entries_resolves_by_port():
    assert mi.host_fell_back(["auto:11436"], "http://172.30.48.1:11436") is False
    assert mi.host_fell_back(["auto:11436"], "http://localhost:11434") is True


def test_an_empty_list_preference_is_never_a_fallback():
    """`machine_hosts` returns [] on a fresh clone with no accelerator."""
    assert mi.host_fell_back([], "http://localhost:11434") is False
    assert mi.host_fell_back(["", "   "], "http://localhost:11434") is False


# --- the two normalisations, each of which turns a success into an alarm -------

def test_a_trailing_slash_on_either_side_is_not_a_different_host():
    """MEASURED 2026-09-01: dropping the `rstrip("/")` on the RESOLVED value left
    all 10 tests green, and made the preferred host answering read as a fallback
    the moment the resolver returned a URL with a trailing slash."""
    assert mi.host_fell_back("http://gpu.box:11436", "http://gpu.box:11436/") is False
    assert mi.host_fell_back("http://gpu.box:11436/", "http://gpu.box:11436") is False


def test_a_padded_config_entry_is_stripped_before_it_is_compared():
    """A YAML value with stray whitespace is an ordinary typo, not a fallback."""
    assert mi.host_fell_back("  http://gpu.box:11436  ", "http://gpu.box:11436") is False
    assert mi.host_fell_back(["  auto:11436 "], "http://172.30.48.1:11436") is False


def test_a_bare_auto_with_no_port_never_matches_by_the_empty_suffix():
    """`if port and ...` is what keeps `landed.endswith(":")` out of the decision.

    A bare `auto` names no port, so nothing can satisfy it and the honest answer
    is that the preference was not met."""
    assert mi.host_fell_back("auto", "http://172.30.48.1:11436") is True
    assert mi.host_fell_back("auto", "http://localhost:11434") is True


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
    """The announcement sits in load_config so a NEW subcommand cannot omit it.

    Reachable only through the explicit `--allow-host-fallback` since
    2026-08-23; without the flag a down host is a refusal, not a fallback.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory-index.yaml").write_text(
        'host: "http://gpu.box:11436"\nmodel: bge-m3\n', encoding="utf-8"
    )
    monkeypatch.setattr(mi, "resolve_ollama_host", lambda h, **kw: "http://localhost:11434")
    cfg = mi.load_config(tmp_path, allow_fallback=True)
    assert cfg["host_preferred"] == "http://gpu.box:11436"
    assert "GPU EMBEDDER NOT AVAILABLE" in capsys.readouterr().err


def test_without_the_flag_a_down_host_leaves_no_host_at_all(tmp_path, monkeypatch, capsys):
    """The default path refuses rather than degrades. `stats` and `meta` still
    load their config, which is why this reports through `host` being None
    instead of exiting inside `load_config`."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "memory-index.yaml").write_text(
        'host: "http://gpu.box:11436"\nmodel: bge-m3\n', encoding="utf-8"
    )

    def _down(pref, **kw):
        raise mi.OllamaHostUnavailable("no pinned ollama host answered: http://gpu.box:11436")

    monkeypatch.setattr(mi, "_resolve_embed_host", _down)
    cfg = mi.load_config(tmp_path)
    assert cfg["host"] is None
    assert "gpu.box:11436" in cfg["host_error"]
    err = capsys.readouterr().err
    assert "EMBEDDER NOT AVAILABLE" in err and mi.RED in err


# The shipped config's pin is asserted once, in tests/test_embed_host_pinned.py
# (`test_the_shipped_config_pins_the_windows_side_and_never_localhost`). A second
# copy here drifted the moment the pin grew a second port.
