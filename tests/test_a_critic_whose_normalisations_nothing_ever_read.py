"""Six normalisations in `critique_draft` that no test in the repo ever bound.

`tests/test_draft_critique.py` covers the advisory-only contract well: the result
carries no status field, `annotate_card` cannot move a card, a critiqued
`email_send` still resolves `gated`. What it does not cover is the layer between
the model's answer and that result, which exists because the answer comes from a
Haiku-class model and is therefore not to be trusted in shape.

MEASURED 2026-09-01 by mutating `scripts/utils/draft_critique.py` and running
every test file in the repo that names it (`tests/test_draft_critique.py`,
`test_a_scan_that_never_ran_reported_nothing_to_do.py`,
`test_a_stall_after_the_headers_arrived.py`, `test_no_claude_model_pins.py`,
479 tests). Each of the six below survived the whole set:

    deleting the ```json fence stripper                 479 passed
    deleting the flags[:10] cap                         479 passed
    deleting the non-list flags coercion                479 passed
    deleting the summary[:300] truncation               479 passed
    deleting the stderr line on a failed critique       479 passed
    deleting timeout=30 from the model call             479 passed

The last two are the ones that matter most and the fifth is the sharpest. The
module's own comment says at length why the failure line exists: "The operator
reads an absent critique as 'no concerns', so the failure has to say it
happened." A broken proxy, a retired model pin and a clean run in which nothing
was worth flagging all produce the same card. Nothing measured that the sentence
is still printed.

The third is not cosmetic either. `flags` is iterated with a comprehension, so a
model answering `"flags": "leaks pricing"` (a bare string, which is a valid JSON
value and a plausible generation) would be exploded one CHARACTER per flag
without the coercion. VERIFIED 2026-09-01: with the guard, `["leaks pricing"]`.

No network and no real send: the anthropic SDK is faked, `_resolve_model` is
pinned so the model catalogue is never fetched, and nothing here constructs a
transport.

Run: .venv/bin/python -m pytest tests/test_a_critic_whose_normalisations_nothing_ever_read.py -q
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import draft_critique  # noqa: E402

# Invented. The engine repo is public; a fixture may carry no real address.
RECIPIENT = "dana.quill@example.invalid"
PINNED_MODEL = "claude-haiku-test-pin"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Key present, catalogue pinned, `.env` untouched.

    `_resolve_model` asks `claude_models.resolve`, which reaches the Models API
    on a cache miss. A unit test that reaches the network is not a unit test, and
    the resolution is not what any test here is about.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(draft_critique, "_resolve_model", lambda model: PINNED_MODEL)
    import scripts.utils.workspace as ws
    monkeypatch.setattr(ws, "load_env", lambda *a, **k: None)


def _answer(monkeypatch, text: str) -> dict:
    """Install a fake anthropic SDK returning `text`; return the captured kwargs.

    The dict is filled when `messages.create` is called, so a test that asserts
    on it is asserting about the call that was actually made.
    """
    captured: dict = {}
    block = types.SimpleNamespace(type="text", text=text)
    response = types.SimpleNamespace(content=[block])

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response

    class _Client:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return captured


def _explode(monkeypatch, exc: BaseException) -> None:
    """A fake SDK whose client construction raises, to drive the failure arm."""
    class _Client:
        def __init__(self, **kwargs):
            raise exc

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)


# ============================================================
# 1. the shape the model answers in, which is not promised
# ============================================================

def test_a_fenced_answer_is_parsed_rather_than_discarded(monkeypatch):
    """`_parse` documents "Tolerates surrounding markdown fences" and a cheap
    model wrapping JSON in ```json is the ordinary case, not the odd one.

    Without the stripper this returned None, which the caller reads as a
    graceful skip: every draft would arrive uncritiqued and the layer would look
    like it was working.
    """
    _answer(monkeypatch,
            '```json\n{"risk": "high", "flags": ["leaks pricing"], '
            '"summary": "Discloses a price to an external party."}\n```')

    result = draft_critique.critique_draft("Re: pricing", "a body",
                                           recipient=RECIPIENT)

    assert result is not None, "a fenced answer was thrown away"
    assert result["risk"] == "high"
    assert result["flags"] == ["leaks pricing"]


def test_a_bare_fence_without_a_language_tag_is_parsed_too(monkeypatch):
    """The other spelling of the same wrapper, so the stripper cannot pass by
    handling only the tagged form."""
    _answer(monkeypatch,
            '```\n{"risk": "low", "flags": [], "summary": "nothing to flag"}\n```')

    result = draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert result is not None and result["risk"] == "low"


def test_a_string_where_a_flag_list_belongs_is_one_flag_not_thirteen(monkeypatch):
    """`flags` is iterated. A bare string is iterable, so without the coercion
    "leaks pricing" becomes thirteen single-character flags on the card the CEO
    reads before approving a send."""
    _answer(monkeypatch,
            '{"risk": "high", "flags": "leaks pricing", "summary": "s"}')

    result = draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert result["flags"] == ["leaks pricing"], result["flags"]


def test_a_json_array_answer_is_a_graceful_skip_and_not_a_reported_failure(
        monkeypatch, capsys):
    """`_parse` refuses anything that is not an object.

    The return value alone does not measure this guard: without it, `parsed.get`
    on a list raises `AttributeError`, the blanket handler catches it, and the
    caller still gets None. What changes is the REPORT. A model that answered in
    the wrong shape is a malformed answer, which this layer skips quietly by
    design; printing "critique skipped (AttributeError)" files it under the
    proxy-is-broken heading instead, which is the one line the operator is
    supposed to be able to trust.
    """
    _answer(monkeypatch, '[{"risk": "high"}]')

    assert draft_critique.critique_draft("s", "a body", recipient=RECIPIENT) is None
    err = capsys.readouterr().err
    assert err == "", (
        f"a wrong-shaped answer was reported as a critique failure: {err!r}")


# ============================================================
# 2. the bounds, which are the reason this is one cheap call
# ============================================================

def test_the_flag_list_is_capped_at_ten(monkeypatch):
    """A card is a UI surface. An unbounded list from a model that decided to
    enumerate is how a review card becomes unreadable."""
    flags = json.dumps([f"flag number {i}" for i in range(25)])
    _answer(monkeypatch, '{"risk": "medium", "flags": %s, "summary": "s"}' % flags)

    result = draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert len(result["flags"]) == 10, len(result["flags"])
    assert result["flags"][0] == "flag number 0", "the cap took the wrong end"


def test_the_summary_is_truncated_to_three_hundred_characters(monkeypatch):
    """The system prompt asks for "<=300 chars" and a prompt is a request, never
    a constraint. This truncation is the only thing that enforces it."""
    _answer(monkeypatch,
            json.dumps({"risk": "low", "flags": [], "summary": "x" * 900}))

    result = draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert len(result["summary"]) == 300, len(result["summary"])


def test_the_model_call_carries_a_timeout(monkeypatch):
    """"One bounded call", says the module docstring. Without the timeout this
    runs inside the daemon's critique sweep with no ceiling, and a hung proxy
    stalls the tick rather than skipping one card.
    """
    captured = _answer(monkeypatch,
                       '{"risk": "low", "flags": [], "summary": "s"}')

    draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert captured.get("timeout") == 30, captured.get("timeout")
    assert captured.get("max_tokens") == 600, captured.get("max_tokens")


# ============================================================
# 3. a skipped critique says it was skipped
# ============================================================

def test_a_failed_critique_prints_that_the_draft_is_uncritiqued(monkeypatch,
                                                                capsys):
    """The defect the module's own comment describes: an absent critique reads
    to the operator as "no concerns", so a failure that says nothing is a
    failure that clears the draft."""
    _explode(monkeypatch, RuntimeError("proxy refused the connection"))

    assert draft_critique.critique_draft("s", "a body",
                                         recipient=RECIPIENT) is None

    err = capsys.readouterr().err
    assert "UNCRITIQUED" in err, f"the failure was silent: {err!r}"
    assert "not cleared" in err
    assert "RuntimeError" in err, "the failure does not name its own cause"


def test_a_clean_run_says_nothing_on_stderr(monkeypatch, capsys):
    """The negative case. A reporter that prints on every call teaches the
    operator to ignore it, which is the same silence by another route."""
    _answer(monkeypatch, '{"risk": "low", "flags": [], "summary": "s"}')

    draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)

    assert capsys.readouterr().err == ""


def test_a_keyboard_interrupt_is_not_swallowed_as_a_skipped_critique(monkeypatch):
    """`except Exception` does not catch `KeyboardInterrupt`, and must not: an
    operator pressing Ctrl-C during a sweep is not a model that misbehaved.
    Pinned because the handler is blanket and the next widening is the one that
    would take BaseException with it."""
    _explode(monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        draft_critique.critique_draft("s", "a body", recipient=RECIPIENT)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
