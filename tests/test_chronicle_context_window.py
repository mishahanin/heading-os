"""The Chronicle must read the whole transcript it was handed, not 13% of it.

Measured 2026-08-22. `chronicle.py` trims a session to `ENVELOPE_MAX_BYTES`
(120,000 characters, roughly 30,000 tokens) and posts it to ollama's
`/api/generate`. The payload set `temperature` and `num_predict` and said nothing
about `num_ctx`, so the DAEMON decided the window — and the two daemons on this
machine disagreed:

    WSL      ollama loaded gemma3:4b with context_length = 4096
    Windows  ollama loaded gemma3:4b with context_length = 131072

gemma3:4b itself declares `gemma3.context_length: 131072`, so nothing about the
model forced this. `HEADING_OS_OLLAMA_HOST` was unset, which resolves to the WSL
daemon, so the nightly run posted ~30,000 tokens into a 4,096-token window.
Ollama truncates silently: a probe with a 120,000-character body came back
reporting `prompt_eval_count: 2051`. The model was summarizing about a
thirteenth of each session and nothing anywhere said so.

That is the mechanical reason Chronicle entries read as bare facts. A summary
cannot carry reasoning it was never shown.

The window is therefore stated in the request, not inherited from whichever
daemon answers. A daemon that is reconfigured, replaced, or reached over a
different host cannot silently shrink it back.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def chronicle():
    """Load chronicle.py by path — its filename is not an importable module name."""
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location(
        "chronicle_mod", WORKSPACE / "scripts" / "chronicle.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["chronicle_mod"] = module
    spec.loader.exec_module(module)
    return module


def test_the_request_states_its_own_context_window(chronicle, monkeypatch):
    sent = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"response": '{"gist":"x","topics":["a"],'
                                           '"class":"business"}'}).encode()

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return FakeResponse()

    # The endpoint is resolved lazily since 2026-08-23 and its probe would go
    # through the very `urlopen` this test replaces, so pin it instead of
    # letting a fake summarizer response answer a version probe.
    monkeypatch.setattr(chronicle, "ollama_url", lambda: "http://pinned.test:11434/api/generate")
    monkeypatch.setattr(chronicle.urllib.request, "urlopen", fake_urlopen)
    chronicle.summarize("a transcript body")

    assert "num_ctx" in sent["options"], (
        "no num_ctx in the payload: the daemon decides the window, and one of "
        "the two daemons on this machine defaults to 4096"
    )


def test_the_window_is_large_enough_for_the_envelope_actually_sent(chronicle):
    """A window smaller than the body is a silent truncation, not an error.

    Roughly 4 characters per token for mixed RU/EN prose. The window must cover
    the trimmed body plus the prompt scaffolding and the reply.
    """
    needed = chronicle.ENVELOPE_MAX_BYTES // 4
    assert needed <= chronicle.NUM_CTX, (
        f"num_ctx {chronicle.NUM_CTX} cannot hold "
        f"{chronicle.ENVELOPE_MAX_BYTES} characters (~{needed} tokens)"
    )


def test_the_window_is_within_what_the_model_supports(chronicle):
    """gemma3:4b declares 131072. Asking for more wastes memory or fails to load."""
    assert chronicle.NUM_CTX <= 131_072
