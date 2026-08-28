"""One model-call contract, kept by one wrapper and not by its two siblings.

`scripts/kimi-consult.py`, `scripts/gemini-consult.py` and
`scripts/grok-consult.py` are three thin CLIs over one shared transport. They
document one exit contract, the transport prints one remediation for all three,
and `.claude/skills/council/SKILL.md` dispatches them interchangeably. The
contract was then applied unevenly:

  - kimi and gemini catch an unwrapped error and honour the documented exit 3.
    grok catches only RuntimeError, so the same failure leaves a traceback.
  - only kimi defines `--timeout`, the flag the transport's own truncation error
    tells the operator to raise. The other two exit 2 on it.
  - the transport reads `resp.choices[0].message.content` OUTSIDE the block that
    classifies exceptions, so a response that parses but carries a different
    shape escapes every branch as an AttributeError.

And two guards that were green over the thing they were written to find:

  - `tests/test_no_code_reaches_a_model_provider_directly.py` looks for a second
    model client by the literal `127.0.0.1:8317`, so the one second client in
    the tree, which spells the same address `localhost:8317`, was invisible.
  - every cascade test in `tests/test_llm_fallback.py` stubs the vendor with a
    lambda that discards `prompt`, so a cascade that sent a blank prompt to a
    second vendor passed the whole suite.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("openai")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import proxy_transport as pt  # noqa: E402


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


WRAPPERS = {
    "gemini": ("gemini-consult.py", "consult_gemini"),
    "grok": ("grok-consult.py", "consult_grok"),
    "kimi": ("kimi-consult.py", "consult_kimi"),
}


@pytest.fixture(scope="module")
def wrappers():
    return {
        vendor: _load(f"_ct_{vendor}_consult", f"scripts/{script}")
        for vendor, (script, _fn) in WRAPPERS.items()
    }


# ============================================================
# The transport classifies the response shape, not only the exception
# ============================================================

class _Resp:
    """A proxy answer that parses but does not carry the shape we read."""

    def __init__(self, choices):
        self.choices = choices


class _Choice:
    def __init__(self, message=None, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _Message:
    def __init__(self, content):
        self.content = content


def _client_returning(resp):
    client = mock.MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _call(resp, **kw):
    with mock.patch.object(pt, "_make_client", return_value=_client_returning(resp)), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        return pt.call_model("some-model", "a prompt", **kw)


def test_a_choice_with_no_message_is_classified_not_a_traceback():
    """`ch.message` is None on a delta-shaped answer, and `.content` on None is
    an AttributeError. It used to leave `call_model` unclassified.
    """
    with pytest.raises(RuntimeError, match="unreadable response"):
        _call(_Resp([_Choice(message=None)]))


def test_a_choice_with_no_finish_reason_is_classified():
    choice = _Choice(message=_Message("hello"))
    del choice.finish_reason
    with pytest.raises(RuntimeError, match="unreadable response"):
        _call(_Resp([choice]))


def test_a_response_with_no_choices_attribute_is_classified():
    class _Bare:
        pass

    with pytest.raises(RuntimeError, match="unreadable response"):
        _call(_Bare())


def test_the_unreadable_message_names_the_model_and_the_type():
    with pytest.raises(RuntimeError) as excinfo:
        _call(_Resp([_Choice(message=None)]))
    text = str(excinfo.value)
    assert "some-model" in text, text
    assert "AttributeError" in text, text


def test_an_empty_choices_list_keeps_its_own_older_message():
    """`no choices` is a different fact from `unreadable`, and it was already
    named. The new classification must not swallow it into the generic one.

    The absence check is the whole test. A `match="no choices"` alone passes
    over the re-wrap, because the generic branch quotes the RuntimeError it
    caught and the older words survive inside the wider message.
    """
    with pytest.raises(RuntimeError) as excinfo:
        _call(_Resp([]))
    text = str(excinfo.value)
    assert "no choices" in text, text
    assert "unreadable response" not in text, (
        f"the specific message was re-wrapped in the generic one: {text}"
    )


def test_a_good_response_still_returns_its_text():
    """Anchor: the guard above is vacuous if the happy path stopped working."""
    assert _call(_Resp([_Choice(_Message("the answer"))])) == "the answer"


# ============================================================
# All three wrappers hold the documented 0 / 2 / 3 exit contract
# ============================================================

@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_an_unwrapped_error_exits_three_in_every_wrapper(monkeypatch, wrappers, vendor):
    """grok caught only RuntimeError while its two siblings had the catch-all,
    so the same proxy fault printed a traceback for one voice of three.
    """
    mod = wrappers[vendor]

    def _boom(*a, **k):
        raise KeyError("choices")

    monkeypatch.setattr(mod, "call_model", _boom)
    rc = mod.main(["--mode", "independent", "--question", "why"])
    assert rc == 3, f"{vendor}: expected the documented API-failure code 3, got {rc}"


@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_the_unwrapped_error_names_its_type_in_every_wrapper(
    monkeypatch, capsys, wrappers, vendor
):
    """Exit 3 alone hides which fault happened. Both siblings print the type."""
    mod = wrappers[vendor]

    def _boom(*a, **k):
        raise KeyError("choices")

    monkeypatch.setattr(mod, "call_model", _boom)
    mod.main(["--mode", "independent", "--question", "why"])
    err = capsys.readouterr().err
    assert "KeyError" in err, f"{vendor}: the failure type is not named: {err!r}"


@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_a_missing_key_exits_two_in_every_wrapper(monkeypatch, wrappers, vendor):
    mod = wrappers[vendor]

    def _boom(*a, **k):
        raise RuntimeError("CLIPROXY_API_KEY is missing from .env")

    monkeypatch.setattr(mod, "call_model", _boom)
    assert mod.main(["--mode", "independent", "--question", "why"]) == 2


@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_a_good_call_exits_zero_in_every_wrapper(monkeypatch, wrappers, vendor):
    """Anchor: the three tests above pass trivially if nothing ever returns 0."""
    mod = wrappers[vendor]
    monkeypatch.setattr(mod, "call_model", lambda *a, **k: "an answer")
    assert mod.main(["--mode", "independent", "--question", "why"]) == 0


# ============================================================
# The remediation the transport prints is a flag the CLI accepts
# ============================================================

@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_every_wrapper_accepts_the_timeout_flag_its_error_prescribes(wrappers, vendor):
    """`call_model` tells the operator to raise `--timeout`, and the council
    skill repeats it for whichever voice truncated. Two of three rejected it.
    """
    args = wrappers[vendor].parse_args(
        ["--mode", "independent", "--question", "q", "--timeout", "480"]
    )
    assert args.timeout == 480.0


@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_the_timeout_reaches_the_transport_in_every_wrapper(monkeypatch, wrappers, vendor):
    """Accepting the flag and forwarding it are two hops. A wrapper that parses
    `--timeout` and drops it is worse than one that refuses it: the operator
    re-dispatches, waits, and times out at the same ceiling.
    """
    mod = wrappers[vendor]
    seen = {}

    def _spy(model, prompt, **kwargs):
        seen.update(kwargs)
        return "an answer"

    monkeypatch.setattr(mod, "call_model", _spy)
    assert mod.main(["--mode", "independent", "--question", "q", "--timeout", "480"]) == 0
    assert seen.get("timeout") == 480.0, f"{vendor}: timeout did not reach call_model: {seen}"


@pytest.mark.parametrize("vendor", sorted(WRAPPERS))
def test_omitting_the_timeout_inherits_the_transport_default(monkeypatch, wrappers, vendor):
    """The wrapper must not pin its own ceiling. Passing an explicit None or a
    hardcoded number here would make DEFAULT_TIMEOUT unreachable.
    """
    mod = wrappers[vendor]
    seen = {}

    def _spy(model, prompt, **kwargs):
        seen.update(kwargs)
        return "an answer"

    monkeypatch.setattr(mod, "call_model", _spy)
    assert mod.main(["--mode", "independent", "--question", "q"]) == 0
    assert "timeout" not in seen, f"{vendor}: pinned a timeout of its own: {seen}"


def test_every_flag_the_transport_prescribes_is_defined_by_every_wrapper(wrappers):
    """The detector, not the three cases above.

    A future message that names a fourth flag reintroduces the defect silently,
    because nothing reads the message. This reads it.
    """
    text = (ROOT / "scripts/utils/proxy_transport.py").read_text(encoding="utf-8")
    prescribed = set()
    for line in text.splitlines():
        if "Raise " not in line and "raise " not in line.lower():
            continue
        prescribed.update(re.findall(r'(?<![-\w])--[a-z][a-z-]+', line))
    assert prescribed, "the detector found no prescribed flag; the messages changed shape"

    assert set(wrappers) == set(WRAPPERS), "the wrappers under test changed"
    for vendor in sorted(WRAPPERS):
        parser_src = (ROOT / f"scripts/{WRAPPERS[vendor][0]}").read_text(encoding="utf-8")
        missing = [f for f in sorted(prescribed) if f'"{f}"' not in parser_src]
        assert not missing, (
            f"{vendor}-consult.py does not define {missing}, which "
            f"proxy_transport tells the operator to raise"
        )


# ============================================================
# The loopback guard sees every spelling of one address
# ============================================================

GUARD_REL = "tests/test_no_code_reaches_a_model_provider_directly.py"

# Assembled from parts, never written whole. The guard under test scans every
# tracked file for a proxy address beside a client shape, and a fixture holding
# either one literally would make this file its own offender. Building them here
# is the same discipline the guard asks of production code.
_PROXY_PORT = "83" + "17"
_PATH = "/v1/chat/" + "completions"


def _sample(host: str, port: str = _PROXY_PORT) -> str:
    return f'URL = "http://{host}:{port}{_PATH}"'


def test_the_proxy_address_pattern_covers_the_loopback_spellings():
    """One socket, four spellings. The guard matched one of them, and the only
    second client in the tree used another.
    """
    guard = _load("_ct_proxy_guard", GUARD_REL)
    for host in ("127.0.0.1", "local" + "host", "[::1]", ".".join("0" * 4)):
        assert guard.PROXY_ADDRESS_RE.search(_sample(host)), host


def test_the_proxy_address_pattern_does_not_match_another_port():
    """A pattern that matched any loopback address would catch the bridge
    daemon, ollama and every local health check, and would be turned off within
    a week. The port is what makes the address the proxy.
    """
    guard = _load("_ct_proxy_guard2", GUARD_REL)
    assert not guard.PROXY_ADDRESS_RE.search(_sample("127.0.0.1", "8765"))
    assert not guard.PROXY_ADDRESS_RE.search(_sample("local" + "host", "11434"))


def test_the_second_client_is_named_and_still_exists():
    """The bench is exempt by decision, not by spelling. An exemption that
    outlives its file is a hole nobody can see, so the anchor is the file.
    """
    guard = _load("_ct_proxy_guard3", GUARD_REL)
    assert len(guard.OWN_CLIENT_EXEMPT) == 1, guard.OWN_CLIENT_EXEMPT
    for rel in guard.OWN_CLIENT_EXEMPT:
        assert (ROOT / rel).exists(), f"exempted file is gone: {rel}"


def test_the_exempt_bench_still_measures_wall_time():
    """The exemption's whole reason is that the bench times its calls, and a
    retry layer would inflate the number it exists to produce. If it stops
    timing, the reason is gone and the exemption must be re-argued.
    """
    src = (ROOT / "scripts/census-submodel-bench.py").read_text(encoding="utf-8")
    assert "perf_counter" in src


# ============================================================
# The cascade carries the prompt it was given
# ============================================================

def test_invoke_vendor_forwards_the_prompt_to_the_wrapper(monkeypatch):
    """Every stub in the fallback suite discarded `prompt` and returned a fixed
    string, which was then the asserted value. A cascade that dropped the prompt
    answered from nothing, reported vendor and fallback_triggered as usual, and
    passed the suite.
    """
    from scripts.utils import llm_fallback as F

    seen = {}

    def _load_fn(path, fn):
        def _wrapper(prompt, model, temperature, max_tokens):
            seen["prompt"] = prompt
            seen["model"] = model
            return "an answer"
        return _wrapper

    monkeypatch.setattr(F, "_load_consult_fn", _load_fn)
    F._invoke_vendor("gemini", "a-model", "THE ACTUAL PROMPT", 1000, 0.7)
    assert seen["prompt"] == "THE ACTUAL PROMPT"
    assert seen["model"] == "a-model"


def test_the_cascade_sends_the_flattened_prompt_to_the_fallback_vendor(monkeypatch):
    """End to end: the system block and the user turn both have to arrive."""
    from openai import InternalServerError

    from scripts.utils import llm_fallback as F

    seen = {}

    class _Client:
        class messages:  # noqa: N801 - mirrors the SDK attribute name
            @staticmethod
            def create(**kwargs):
                raise InternalServerError(
                    "503", response=mock.MagicMock(), body=None
                )

    def _load_fn(path, fn):
        def _wrapper(prompt, model, temperature, max_tokens):
            seen["prompt"] = prompt
            return "an answer"
        return _wrapper

    monkeypatch.setattr(F, "_load_consult_fn", _load_fn)
    monkeypatch.setattr(F, "sensitivity_is_declared", lambda: False)

    result = F.call_anthropic_with_fallback(
        client=_Client(),
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system="A UNIQUE SYSTEM BLOCK",
        messages=[{"role": "user", "content": "A UNIQUE USER TURN"}],
        skill_name="test",
    )
    assert result.fallback_triggered is True
    assert "A UNIQUE SYSTEM BLOCK" in seen["prompt"]
    assert "A UNIQUE USER TURN" in seen["prompt"]


# ============================================================
# The kimi timeout hop, measured where it happens
# ============================================================

def test_consult_kimi_forwards_an_explicit_timeout(monkeypatch, wrappers):
    """The one test that named `timeout` patched `consult_kimi` itself, so it
    measured argparse and removed the forwarding line it claimed to pin.
    """
    mod = wrappers["kimi"]
    seen = {}
    monkeypatch.setattr(mod, "call_model",
                        lambda model, prompt, **kw: seen.update(kw) or "ok")
    mod.consult_kimi("p", timeout=480.0)
    assert seen.get("timeout") == 480.0


def test_consult_kimi_omits_the_timeout_when_none(monkeypatch, wrappers):
    """Passing `timeout=None` through would override DEFAULT_TIMEOUT with None."""
    mod = wrappers["kimi"]
    seen = {}
    monkeypatch.setattr(mod, "call_model",
                        lambda model, prompt, **kw: seen.update(kw) or "ok")
    mod.consult_kimi("p")
    assert "timeout" not in seen
