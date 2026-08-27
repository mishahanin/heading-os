#!/usr/bin/env python3
"""SEC-013: analyze_batch handles a parse error apart from everything else.

Until 2026-08-27 the whole control was one negative substring scan over the
whole file:

    assert "(json.JSONDecodeError, Exception)" not in content

Three things were wrong with it at once.

* **Byte-fragile.** It forbids one exact spelling. `except (Exception,
  json.JSONDecodeError)`, an extra space, a trailing comma, or `from json import
  JSONDecodeError` followed by `except (JSONDecodeError, Exception)` all pass it
  while being the very thing it names.
* **Unscoped.** The test is named for `analyze_batch` and read the entire
  1900-line module, so the literal appearing in an unrelated function - or in a
  comment, as it did here - decided the verdict.
* **No positive half.** Nothing asserted the differentiated handling EXISTS.
  Deleting the `except json.JSONDecodeError` clause outright, leaving only
  `except Exception`, was green. The control certified the absence of a mistake
  nobody was making instead of the presence of the thing it was created for.

Both halves are now asserted where they live: the structure from the AST of
`analyze_batch` alone, and the behaviour by driving the function with a response
the model cannot have meant and reading which handler logged it.
"""

import ast
import json
import logging

import pytest

from tests.security.conftest import read_file_content


def _analyze_batch(scripts_dir):
    tree = ast.parse(read_file_content(scripts_dir / "sentinel.py"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "analyze_batch":
            return node
    pytest.fail("sentinel.py has no analyze_batch; SEC-013 guards nothing")


def _handlers(func):
    return [n for n in ast.walk(func) if isinstance(n, ast.ExceptHandler)]


def _caught(handler) -> list[str]:
    """The exception names one `except` clause catches, however it is spelled."""
    if handler.type is None:
        return ["<bare>"]
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [ast.unparse(n) for n in nodes]


def test_analyze_batch_has_a_dedicated_json_parse_handler(scripts_dir):
    """The positive half the old scan never had."""
    handlers = _handlers(_analyze_batch(scripts_dir))
    caught = [_caught(h) for h in handlers]
    parse_only = [c for c in caught
                  if len(c) == 1 and c[0].split(".")[-1] == "JSONDecodeError"]
    assert parse_only, (
        f"analyze_batch has no except clause catching JSONDecodeError on its "
        f"own. A model that answers with prose instead of JSON is an ordinary, "
        f"expected event and must be logged as such. Handlers found: {caught}"
    )


def test_analyze_batch_still_has_a_catch_all_so_the_daemon_survives(scripts_dir):
    """Sentinel is a long-running daemon. An unhandled error in one batch ends
    the process, and nothing above analyze_batch catches it."""
    handlers = _handlers(_analyze_batch(scripts_dir))
    caught = [_caught(h) for h in handlers]
    assert any("Exception" in c for c in caught), (
        f"analyze_batch has no catch-all; one deviant response kills the "
        f"daemon. Handlers found: {caught}"
    )


def test_no_clause_catches_the_parse_error_together_with_everything_else(scripts_dir):
    """The original claim, now spelling-independent.

    JSONDecodeError is a subclass of Exception, so naming both in one clause is
    redundant AND destroys the distinction: an authentication failure and a
    prose answer produce the same log line, and the operator reading it cannot
    tell a model that is misbehaving from a proxy that is down.
    """
    offenders = []
    for handler in _handlers(_analyze_batch(scripts_dir)):
        names = _caught(handler)
        if len(names) < 2:
            continue
        leaves = {n.split(".")[-1] for n in names}
        if "JSONDecodeError" in leaves and "Exception" in leaves:
            offenders.append((handler.lineno, names))
    assert not offenders, (
        f"analyze_batch catches JSONDecodeError and Exception in one clause "
        f"{offenders}. Split them, so a parse error and an outage do not read "
        f"the same in the log."
    )


# --------------------------------------------------------------- behaviour
#
# The AST says the two clauses exist. These say they RUN, and that the one that
# runs is the one that matches. The two handlers differ only in their log line,
# so the log line is the discriminator - which is also exactly what an operator
# reading sentinel's output has to go on.


@pytest.fixture
def sentinel_module():
    return pytest.importorskip("scripts.sentinel")


def _stub(sen):
    """A real UrgencyAnalyzer with the network and the model removed.

    Subclassed rather than duck-typed because analyze_batch reads SYSTEM_PROMPT
    and _format_item_prompt off self BEFORE the try block; a hand-built double
    fails there and never reaches the handlers under test.
    """

    class _StubAnalyzer(sen.UrgencyAnalyzer):
        def __init__(self):
            self.model = "stub"
            self.max_tokens = 100
            self.logger = logging.getLogger("sec013-stub")
            self.client = None
            self.business_context = ""
            self.operator_name = "Test Operator"
            self.fallback_calls = 0

        def analyze(self, item):
            self.fallback_calls += 1
            return {"urgency_score": 5, "reason": "fallback", "summary": "",
                    "recommended_action": ""}

        def _get_client(self):
            return object()  # never used: the transport is stubbed out

    return _StubAnalyzer()


# Two items, always. `analyze_batch` short-circuits a single item straight to
# `analyze()` and never enters the try block, so a one-item case would measure
# the guard by never running it.
ITEMS = [{"subject": "x"}, {"subject": "y"}]


def test_a_prose_answer_is_logged_as_a_parse_error(sentinel_module, monkeypatch, caplog):
    """The model answered in English. That is the JSONDecodeError path."""
    sen = sentinel_module
    a = _stub(sen)

    class _Resp:
        text = "I'm sorry, I cannot rank these emails."

    monkeypatch.setattr(sen, "call_anthropic_with_fallback", lambda **kw: _Resp())
    with caplog.at_level(logging.WARNING, logger="sec013-stub"):
        out = a.analyze_batch(ITEMS)

    assert len(out) == 2 and a.fallback_calls == 2, "no fallback to individual calls"
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "JSON parse error" in messages, messages
    assert "unexpected error" not in messages, (
        f"a parse error was reported through the catch-all, so the operator "
        f"cannot tell it from an outage: {messages}"
    )


def test_a_transport_failure_is_not_logged_as_a_parse_error(sentinel_module, monkeypatch, caplog):
    """The other side of the same distinction. Without this, a handler that
    caught everything as a parse error would pass the test above."""
    sen = sentinel_module
    a = _stub(sen)

    def _boom(**kw):
        raise ConnectionError("proxy is down")

    monkeypatch.setattr(sen, "call_anthropic_with_fallback", _boom)
    with caplog.at_level(logging.WARNING, logger="sec013-stub"):
        out = a.analyze_batch(ITEMS)

    assert len(out) == 2 and a.fallback_calls == 2, "no fallback to individual calls"
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "unexpected error" in messages, messages
    assert "JSON parse error" not in messages, (
        f"an outage was reported as a parse error: {messages}"
    )


def test_the_two_paths_really_produce_different_log_lines(sentinel_module, monkeypatch, caplog):
    """Anchor. The two tests above compare against literals; if both handlers
    were edited to log the same sentence, each would still pass on its own half
    only by accident of wording. This states the property directly.
    """
    sen = sentinel_module

    def _run(responder):
        a = _stub(sen)
        monkeypatch.setattr(sen, "call_anthropic_with_fallback", responder)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sec013-stub"):
            a.analyze_batch(ITEMS)
        return " | ".join(r.getMessage() for r in caplog.records)

    class _Prose:
        text = "not json at all"

    def _boom(**kw):
        raise ConnectionError("proxy is down")

    parse_line = _run(lambda **kw: _Prose())
    error_line = _run(_boom)
    assert parse_line and error_line, (parse_line, error_line)
    assert parse_line != error_line, (
        f"both failure modes log the same sentence, so the split into two "
        f"handlers buys the operator nothing: {parse_line!r}"
    )


def test_json_is_still_the_module_the_handler_names(sentinel_module):
    """Guard against the AST test passing on a shadowed name: `json` must be the
    standard library module whose JSONDecodeError the handler actually catches."""
    assert sentinel_module.json is json
