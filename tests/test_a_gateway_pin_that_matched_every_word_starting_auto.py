"""A misspelt ollama pin silently repointed at the WSL gateway.

`candidate_url` decided a preference was a gateway pin with
`wanted.startswith("auto")`, where the documented forms are exactly `auto` and
`auto:<port>`. A prefix, not a form. So `autobahn` became
`http://<gateway>:11434` and `automotive:9000` became `http://<gateway>:9000`:
a typo did not fail, it produced a DIFFERENT valid address, and whatever answers
on that port then decides where embedding runs.

The port was never validated either, so `auto:banana` built
`http://<gateway>:banana`. `resolve_ollama_host` then reported it as
"unreachable" and degraded, which sends the reader looking for a dead daemon
instead of at the pin they mistyped. `resolve_ollama_host`'s reason branch
carried the same `startswith("auto")` over-match, so both malformed forms were
blamed on an unreadable `/proc/net/route`.

Measured 2026-08-29 on this laptop, gateway 172.30.48.1:

    candidate_url("autobahn")        -> 'http://172.30.48.1:11434'
    candidate_url("automotive:9000") -> 'http://172.30.48.1:9000'
    candidate_url("auto:banana")     -> 'http://172.30.48.1:banana'

No network: the gateway is faked and no candidate survives to be probed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ollama_host as oh  # noqa: E402

GATEWAY = "10.77.0.1"

# Strings that begin with the letters `auto` and are not the documented pin.
NOT_A_GATEWAY_PIN = [
    "autobahn",
    "automotive:9000",
    "autoscaler",
    "auto-embed:11434",
    "autopilot.example.com",
]

# The documented forms, and what each must resolve to.
REAL_PINS = {
    "auto": f"http://{GATEWAY}:11434",
    "auto:11434": f"http://{GATEWAY}:11434",
    "auto:11436": f"http://{GATEWAY}:11436",
    "auto: 11436 ": f"http://{GATEWAY}:11436",
}

# An `auto:` pin whose port is not a port.
BAD_PORTS = ["auto:banana", "auto:11436x", "auto:0", "auto:70000", "auto:-1", "auto:11.4"]


@pytest.fixture(autouse=True)
def _fake_gateway(monkeypatch):
    """A readable gateway, so a None here can only mean the pin was rejected."""
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: GATEWAY)


@pytest.fixture(autouse=True)
def probed(monkeypatch):
    """Every address the resolvers tried to reach. No socket is ever opened.

    This is also the assertion for the misspelt pins: the defect was not that a
    bad pin failed slowly, it was that a bad pin produced a real address and
    something went and knocked on it.
    """
    attempts: list[str] = []

    def _recording_probe(host, **kwargs):
        attempts.append(host)
        return False

    monkeypatch.setattr(oh, "probe", _recording_probe)
    return attempts


@pytest.mark.parametrize("pin", NOT_A_GATEWAY_PIN)
def test_a_word_starting_auto_does_not_name_the_gateway(pin):
    assert oh.candidate_url(pin) is None, f"{pin!r} silently became a gateway address"
    assert oh.host_candidates(pin) == []


@pytest.mark.parametrize("pin,expected", sorted(REAL_PINS.items()))
def test_the_documented_auto_forms_still_resolve(pin, expected):
    assert oh.candidate_url(pin) == expected


@pytest.mark.parametrize("pin", BAD_PORTS)
def test_an_auto_pin_whose_port_is_not_a_port_is_refused(pin):
    assert oh.candidate_url(pin) is None, f"{pin!r} built an address with a bad port"


def test_a_pinned_resolver_refuses_a_misspelt_pin_instead_of_probing_it(probed):
    """The embed path names the bad pin rather than knocking on a wrong machine."""
    with pytest.raises(oh.OllamaHostUnavailable) as excinfo:
        oh.resolve_pinned_host("autobahn")
    assert "autobahn" in str(excinfo.value)
    assert probed == [], f"a misspelt pin was turned into an address and probed: {probed}"


def test_the_degrade_warning_blames_the_pin_not_the_route_table(capsys, probed):
    """`resolve_ollama_host` degrades, but says the true reason."""
    assert oh.resolve_ollama_host("autobahn") == oh.LOCAL_HOST
    warning = capsys.readouterr().err
    assert "'autobahn' is not an http(s) URL" in warning
    assert "cannot read default gateway" not in warning
    assert probed == []


def test_a_real_auto_pin_with_no_gateway_still_blames_the_gateway(monkeypatch, capsys):
    """The opposite direction: the original reason must survive for real pins."""
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: None)
    assert oh.resolve_ollama_host("auto:11436") == oh.LOCAL_HOST
    assert "cannot read default gateway" in capsys.readouterr().err


def test_a_real_auto_pin_is_still_probed(probed):
    """And the fix must not have made a good pin unreachable."""
    assert oh.resolve_ollama_host("auto:11436") == oh.LOCAL_HOST
    assert probed == [f"http://{GATEWAY}:11436"]
