"""One constant in `design-engine.py` answered two incompatible questions.

`POLL_TIMEOUT` was passed to `urlopen` as the per-request socket timeout and
compared against total elapsed as the polling budget. Those are different
questions: how long may ONE call block, versus how long may ALL of them
together. With one number answering both, a request that hung for its socket
timeout had, by definition, spent the entire budget, and the loop's first check
only runs after that call returns. The poll performed exactly one attempt
and printed a timeout, with one socket hung and the service healthy.

The split is `CREATE_TIMEOUT` (the POST, which carries `Prefer: wait`),
`POLL_REQUEST_TIMEOUT` (a status GET, derived as a quarter of the budget), and
`POLL_TIMEOUT` (the budget, now only the budget) with `_validated_budget`
holding the relationship between them.

No test here reaches the network: `socket.connect` is blocked for every test in
the file and the blocker is proved to be armed.

Tests: scripts/design-engine.py
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


de = _load("design-engine", "design_engine_hung_request")


# ==========================================================================
# Harness: a hand-driven clock and a network that is not there
# ==========================================================================

class _Clock:
    """A monotonic clock the test advances by hand. Nothing sleeps for real."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(de.time, "monotonic", clock)
    monkeypatch.setattr(de.time, "sleep", lambda s: clock.advance(s))
    return clock


class _Resp:
    """The slice of an `http.client.HTTPResponse` that `_api_request` uses."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test in this file runs with the network amputated.

    A test that reaches a real host is a test whose result depends on someone
    else's uptime, and `_api_request` builds a request against a real hostname.
    Blocking at `connect` is the narrowest place that cannot be routed around
    by a different client library.
    """
    reached = []

    def _blocked(self_or_addr, *args, **kwargs):
        reached.append(str(self_or_addr))
        raise RuntimeError("a test in this file tried to open a real socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield reached
    assert reached == [], f"a test reached the network: {reached}"


def test_the_network_blocker_is_actually_armed(no_network):
    """A guard nobody made refuse is not known to refuse anything.

    Without this the file could pass with the patch silently not applied, and
    "no test reached the network" would be a claim about an unarmed guard.
    """
    with pytest.raises(RuntimeError, match="real socket"):
        socket.create_connection(("host.invalid", 443))
    assert no_network == ["('host.invalid', 443)"]
    no_network.clear()   # this one attempt was made on purpose


# ==========================================================================
# 1 - the defect: one hung request spent the whole budget
# ==========================================================================

def _poll_with_a_hung_create(fake_clock, monkeypatch):
    """Run `_create_prediction` where the POST blocks for its FULL timeout.

    Only the POST hangs. A hung connection is one connection, not all of them,
    and the question is what the loop has left afterwards.
    """
    gets = {"n": 0}

    def _urlopen(req, timeout=None):
        if req.get_method() == "POST":
            fake_clock.advance(timeout)
            return _Resp({"id": "p1", "status": "starting"})
        gets["n"] += 1
        return _Resp({"id": "p1", "status": "processing"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    with pytest.raises(SystemExit) as exc:
        de._create_prediction("tok", "owner/name", {})
    assert exc.value.code == 1
    return gets["n"]


def test_a_request_that_hangs_for_its_whole_socket_timeout_leaves_the_loop_its_attempts(
        fake_clock, monkeypatch):
    """The discriminator: how many polls happen after one maximal request.

    With one constant serving both roles the answer was zero. The POST's own
    socket timeout WAS the budget, so the loop's first check found it already
    spent and gave up without ever asking the service how the prediction was
    doing.
    """
    attempts = _poll_with_a_hung_create(fake_clock, monkeypatch)
    assert attempts > 1, (
        "one hung request consumed the entire polling budget: the loop asked "
        f"for status {attempts} time(s) before declaring a timeout"
    )


def test_the_attempts_left_are_the_budget_the_hung_request_did_not_spend(
        fake_clock, monkeypatch):
    """Not merely "more than one" - exactly the remainder, at one per interval."""
    attempts = _poll_with_a_hung_create(fake_clock, monkeypatch)
    expected = (de.POLL_TIMEOUT - de.CREATE_TIMEOUT) // de.POLL_INTERVAL
    assert attempts == expected, (
        f"{attempts} polls ran on the {de.POLL_TIMEOUT - de.CREATE_TIMEOUT}s "
        f"the POST left behind; {expected} fit in it"
    )


def test_a_hung_status_poll_does_not_end_the_run_either(fake_clock, monkeypatch):
    """The GET has its own ceiling, so one bad poll is not the whole budget."""
    gets = {"n": 0}

    def _urlopen(req, timeout=None):
        if req.get_method() == "POST":
            return _Resp({"id": "p1", "status": "starting"})
        gets["n"] += 1
        if gets["n"] == 1:
            fake_clock.advance(timeout)   # the first poll hangs completely
        return _Resp({"id": "p1", "status": "processing"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    with pytest.raises(SystemExit):
        de._create_prediction("tok", "owner/name", {})
    assert gets["n"] > 1, "a single hung status poll ended the whole run"


# ==========================================================================
# 2 - the two numbers, and the relationship between them
# ==========================================================================

def test_the_socket_timeout_is_never_the_polling_budget(monkeypatch):
    """What `urlopen` is told, read off the call rather than off the source."""
    seen = {}

    def _urlopen(req, timeout=None):
        seen[req.get_method()] = timeout
        return _Resp({"id": "p1", "status": "starting"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    de._api_request("POST", "/models/owner/name/predictions", "tok", {"input": {}})
    de._api_request("GET", "/predictions/p1", "tok")

    assert seen["POST"] < de.POLL_TIMEOUT, \
        "the creating POST may still block for the entire budget"
    assert seen["GET"] < de.POLL_TIMEOUT, \
        "a status poll may still block for the entire budget"
    assert seen["GET"] < seen["POST"], \
        "a status GET was given the ceiling written for a `Prefer: wait` POST"


def test_an_explicit_timeout_argument_wins(monkeypatch):
    seen = {}

    def _urlopen(req, timeout=None):
        seen["t"] = timeout
        return _Resp({})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    de._api_request("GET", "/predictions/p1", "tok", timeout=7)
    assert seen["t"] == 7


def test_the_budget_outlasts_the_longest_single_request():
    assert de.POLL_TIMEOUT > de.CREATE_TIMEOUT + de.POLL_INTERVAL


def test_a_hung_poll_leaves_at_least_two_more_attempts():
    assert de.HUNG_POLLS_PER_BUDGET >= 2
    assert de.POLL_REQUEST_TIMEOUT * de.HUNG_POLLS_PER_BUDGET <= de.POLL_TIMEOUT


def test_the_relationship_refuses_a_pair_one_request_could_spend():
    """The guard is a function so it can be made to refuse, not just to pass."""
    with pytest.raises(RuntimeError, match="leaves the poll loop no attempts"):
        de._validated_budget(120, 120, 2)
    with pytest.raises(RuntimeError):
        de._validated_budget(120, 118, 2)   # exactly on the line
    assert de._validated_budget(120, 90, 2) == 120


# ==========================================================================
# 3 - anchors: the paths this split must not have disturbed
# ==========================================================================

def test_a_prediction_that_succeeds_is_still_returned(fake_clock, monkeypatch):
    statuses = iter(["starting", "succeeded"])

    def _urlopen(req, timeout=None):
        return _Resp({"id": "p1", "status": next(statuses), "output": "u"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    assert de._create_prediction("tok", "owner/name", {})["status"] == "succeeded"


def test_a_failed_prediction_still_exits_one(fake_clock, monkeypatch, capsys):
    def _urlopen(req, timeout=None):
        return _Resp({"id": "p1", "status": "failed", "error": "bad input"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    with pytest.raises(SystemExit) as exc:
        de._create_prediction("tok", "owner/name", {})
    assert exc.value.code == 1
    assert "bad input" in capsys.readouterr().err


def test_the_timeout_message_still_names_the_budget(fake_clock, monkeypatch, capsys):
    def _urlopen(req, timeout=None):
        fake_clock.advance(timeout)
        return _Resp({"id": "p1", "status": "processing"})

    monkeypatch.setattr(de.urllib.request, "urlopen", _urlopen)
    with pytest.raises(SystemExit):
        de._create_prediction("tok", "owner/name", {})
    err = capsys.readouterr().err
    assert "Timed out" in err
    assert f"budget {de.POLL_TIMEOUT}s" in err
