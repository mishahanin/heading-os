"""`scripts/context7.py` parsed a 200 body with a bare `resp.json()`.

`handle_response` only runs when the status is NOT 200, so it never saw this:
a 200 carrying a non-JSON body - a proxy error page, a captive portal, a
truncated upstream - reached `resp.json()` unguarded and left the CLI as an
uncaught `requests.exceptions.JSONDecodeError` traceback. MEASURED 2026-09-02
against a stub returning `200 OK` with the body `<html>Bad Gateway</html>`:
BOTH `fetch_docs_json` (the `--json` path the 2026-08-24 audit named) and
`search_libraries` (the same line one function away, which the audit did not
name) raised.

The file already anticipates exactly this shape one branch over: the non-200
path wraps its own `resp.json()` in `except ValueError` with a debug line. The
success path had no such guard.

No network call is made here. `requests` is replaced on the module object with
a stub, so the only bytes that move are the ones this file writes.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_context7(body: str, status: int = 200):
    """A fresh `context7` module whose `requests` never leaves the machine."""
    spec = importlib.util.spec_from_file_location(
        "context7_under_test", ROOT / "scripts" / "context7.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Resp:
        status_code = status
        text = body
        headers: dict = {}

        def json(self):
            import json
            return json.loads(self.text)

    def _refuse(*_a, **_k):  # pragma: no cover - a real call is the failure
        raise AssertionError("the test reached the network")

    module.requests = types.SimpleNamespace(
        get=lambda *a, **k: _Resp(),
        post=_refuse,
        exceptions=types.SimpleNamespace(
            ConnectionError=ConnectionError, Timeout=TimeoutError),
    )
    # No `.env` read either: the key loader touches the operator's real file.
    module.load_api_key = lambda *_a, **_k: None
    return module


NON_JSON_BODIES = [
    "<html>Bad Gateway</html>",
    "",
    "upstream timed out",
    '{"results": [',          # truncated mid-object
]


@pytest.mark.parametrize("body", NON_JSON_BODIES)
@pytest.mark.parametrize("call", ["fetch_docs_json", "search_libraries"])
def test_a_200_with_a_non_json_body_exits_1_instead_of_raising(call, body):
    """The defect: an unhandled JSONDecodeError instead of a diagnostic exit."""
    module = _load_context7(body)
    fn = getattr(module, call)
    args = ("/org/lib", "query") if call == "fetch_docs_json" else ("react",)

    with pytest.raises(SystemExit) as exc:
        fn(*args)
    assert exc.value.code == 1, (
        f"{call} exited {exc.value.code!r}; the file's other error paths all "
        f"use sys.exit(1)")


@pytest.mark.parametrize("call", ["fetch_docs_json", "search_libraries"])
def test_the_refusal_names_the_upstream_rather_than_the_python_exception(call, capsys):
    """A traceback tells the operator nothing actionable. The message must."""
    module = _load_context7("<html>Bad Gateway</html>")
    fn = getattr(module, call)
    args = ("/org/lib", "query") if call == "fetch_docs_json" else ("react",)

    with pytest.raises(SystemExit):
        fn(*args)
    err = capsys.readouterr().err
    assert "not JSON" in err, f"the refusal does not say what went wrong: {err!r}"
    assert "200" in err, f"the refusal does not name the status it got: {err!r}"


def test_a_well_formed_json_body_is_still_returned_verbatim():
    """ANCHOR for fetch_docs_json. A guard that refused every body would pass
    the tests above and break the tool."""
    module = _load_context7('{"snippets": [{"code": "print(1)"}], "tokens": 12}')
    assert module.fetch_docs_json("/org/lib", "q") == {
        "snippets": [{"code": "print(1)"}], "tokens": 12}


def test_a_well_formed_search_body_still_yields_its_results():
    """ANCHOR for search_libraries, whose contract is the `results` list."""
    module = _load_context7(
        '{"results": [{"id": "/org/lib", "title": "Lib", "trustScore": 9}]}')
    results = module.search_libraries("lib")
    assert [r["id"] for r in results] == ["/org/lib"]


def test_a_json_body_that_is_not_an_object_yields_no_results_rather_than_raising():
    """A 200 whose body parses but is a list has no `results` to read.

    `.get` on a list is the same AttributeError class the JSON guard was added
    for, one layer up, so the isinstance check beside it is asserted here.
    """
    module = _load_context7('["not", "an", "object"]')
    assert module.search_libraries("lib") == []
