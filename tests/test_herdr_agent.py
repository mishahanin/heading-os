"""The HERDR seam: what it submits, and how it fails.

Two properties carry the weight here.

The argument vector is pinned literally, because `submit_compact` is the one
place in this workspace that can put text into a live agent session. The string
is a module constant rather than a parameter on purpose, and a test that accepts
"some call to herdr" would not notice the day someone makes it a parameter.

The failure taxonomy is pinned because a caller has to tell "HERDR does not host
this session" from "HERDR could not be reached". They lead to different
sentences in front of the operator, and reporting the second as the first is the
defect `.claude/rules/scope-claims.md` exists to prevent.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import herdr_agent as HA  # noqa: E402

SESSION = "0f1e2d3c-4b5a-4968-8776-655443322110"

AGENT_LIST = {
    "result": {
        "agents": [
            {
                "pane_id": "w9:p3",
                "agent_status": "idle",
                "agent_session": {"kind": "id", "value": "some-other-session"},
            },
            {
                "pane_id": "w37:p1",
                "agent_status": "working",
                "agent_session": {"kind": "id", "value": SESSION},
            },
        ]
    }
}


class _Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture()
def herdr_present(monkeypatch):
    monkeypatch.setattr(HA.shutil, "which", lambda _name: "/usr/bin/herdr")


def _stub(monkeypatch, payload, **kwargs):
    """Record the argv the seam builds and answer with `payload`."""
    seen = {}

    def fake_run(argv, **called):
        seen["argv"] = argv
        seen["kwargs"] = called
        return _Result(json.dumps(payload), **kwargs)

    monkeypatch.setattr(HA.subprocess, "run", fake_run)
    return seen


def test_resolve_pane_matches_this_session(herdr_present, monkeypatch):
    _stub(monkeypatch, AGENT_LIST)
    assert HA.resolve_pane(SESSION) == "w37:p1"


def test_resolve_pane_ignores_a_different_session(herdr_present, monkeypatch):
    _stub(monkeypatch, AGENT_LIST)
    assert HA.resolve_pane("nobody-here") is None


def test_no_match_is_not_an_error(herdr_present, monkeypatch):
    """None is an ordinary answer: this session simply is not hosted by HERDR."""
    _stub(monkeypatch, {"result": {"agents": []}})
    assert HA.resolve_pane(SESSION) is None


def test_submit_compact_builds_exactly_one_vector(herdr_present, monkeypatch):
    seen = _stub(monkeypatch, {"result": {"type": "agent_prompted"}})
    HA.submit_compact("w37:p1")
    assert seen["argv"] == ["herdr", "agent", "prompt", "w37:p1", "/compact"], (
        f"the submitted vector changed: {seen['argv']}"
    )


def test_submit_compact_never_uses_a_shell(herdr_present, monkeypatch):
    """The global security policy: list arguments, never shell=True."""
    seen = _stub(monkeypatch, {"result": {}})
    HA.submit_compact("w37:p1")
    assert "shell" not in seen["kwargs"], "the seam passed a shell argument"
    assert isinstance(seen["argv"], list), "the seam passed a command string"


def test_the_compact_literal_is_not_a_parameter():
    """`submit_compact` takes a pane and nothing else.

    A seam that accepted caller-supplied text would be a general
    "inject a prompt into a live agent" capability, which is a much larger thing
    than this workspace set out to build.
    """
    import inspect

    params = list(inspect.signature(HA.submit_compact).parameters)
    assert params == ["pane_id"], f"submit_compact grew a text parameter: {params}"
    assert HA.COMPACT_COMMAND == "/compact"


def test_a_missing_binary_raises_rather_than_reporting_not_hosted(monkeypatch):
    monkeypatch.setattr(HA.shutil, "which", lambda _name: None)
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_a_timeout_raises(herdr_present, monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(HA.subprocess, "run", fake_run)
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_a_non_zero_exit_raises(herdr_present, monkeypatch):
    _stub(monkeypatch, {"result": {}}, returncode=1, stderr="herdr: no server")
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_unparseable_output_raises(herdr_present, monkeypatch):
    monkeypatch.setattr(
        HA.subprocess, "run", lambda argv, **kw: _Result("not json at all")
    )
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_a_payload_without_agents_raises(herdr_present, monkeypatch):
    """A shape change upstream is a failure, not an empty fleet."""
    _stub(monkeypatch, {"result": {}})
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


# Parseable output of the WRONG SHAPE. `_run` already converts output that does
# not parse; these are the cases that parse fine and then fail an attribute
# lookup. Found on 2026-08-19 by a harness whose fake `herdr` printed a bare
# list: `payload.get` raised AttributeError, nothing caught it, and the Stop hook
# died with exit 1 and no output - so a HERDR format change would have cost the
# session its offer, its save and its countdown at once. Every one of these must
# arrive as HerdrUnavailable, the single exception the callers already degrade on.
@pytest.mark.parametrize("payload", [
    [{"pane_id": "w1:p1"}],                     # a bare list where an object goes
    "a string",
    42,
    None,
    {"result": [{"pane_id": "w1:p1"}]},         # result is a list, not an object
    {"result": "unexpected"},
    {"result": {"agents": "not a list"}},
    {"result": {"agents": {"pane_id": "w1:p1"}}},
    {"result": {"agents": ["a bare string"]}},  # a malformed RECORD
    {"result": {"agents": [None]}},
])
def test_a_wrong_shaped_payload_raises_rather_than_crashing(
    herdr_present, monkeypatch, payload
):
    _stub(monkeypatch, payload)
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_a_record_that_is_not_about_a_session_is_skipped(herdr_present, monkeypatch):
    """Distinct from the cases above on purpose.

    A malformed agents ARRAY means the lookup cannot be trusted, so it raises. A
    single record whose `agent_session` is not an object is simply not this
    session, so the search goes on and the real match is still found. Collapsing
    the two would report "HERDR does not host this session" for a neighbour's
    broken record, which is the one answer `resolve_pane` must never invent.
    """
    _stub(monkeypatch, {"result": {"agents": [
        {"pane_id": "w9:p9", "agent_session": ["not", "an", "object"]},
        {"pane_id": "w9:p8", "agent_session": None},
        {"pane_id": "w1:p1",
         "agent_session": {"kind": "id", "value": SESSION}},
    ]}})
    assert HA.resolve_pane(SESSION) == "w1:p1"


@pytest.mark.parametrize("pane", [None, "", 37, ["w1:p1"], {"id": "w1:p1"}])
def test_a_match_with_an_unusable_pane_id_raises_rather_than_reporting_not_hosted(
    herdr_present, monkeypatch, pane
):
    """The gap the shape guards left open one field further in.

    Every wrong-shaped payload above raises, and a record that is not about
    this session is skipped. A record that IS about this session and then
    carries a pane_id no caller can use fell between the two: `resolve_pane`
    returned None, which is the sentence "HERDR does not host this session".

    That answer is not merely wrong, it sticks. `_herdr_status` in
    .claude/hooks/checkpoint-offer.py writes `compact_host="not-hosted"` into
    the session state on a None and returns early on that cached value
    afterwards, so one malformed record would have switched driven compaction
    off for the rest of the session and reported it as never available.
    Measured 2026-09-01: with `return pane if isinstance(pane, str) and pane
    else None` in place, every case below returned None and no test in this
    suite or its neighbours went red.
    """
    _stub(monkeypatch, {"result": {"agents": [
        {"pane_id": pane, "agent_session": {"kind": "id", "value": SESSION}},
    ]}})
    with pytest.raises(HA.HerdrUnavailable):
        HA.resolve_pane(SESSION)


def test_an_unusable_pane_on_another_session_is_still_just_a_skip(
    herdr_present, monkeypatch
):
    """The other edge, so the raise above cannot widen into a blanket refusal.

    A neighbour's broken record says nothing about this session, and raising on
    it would report "could not tell" for a lookup that in fact succeeded.
    """
    _stub(monkeypatch, {"result": {"agents": [
        {"pane_id": None,
         "agent_session": {"kind": "id", "value": "some-other-session"}},
        {"pane_id": "w1:p1",
         "agent_session": {"kind": "id", "value": SESSION}},
    ]}})
    assert HA.resolve_pane(SESSION) == "w1:p1"


def test_label_calls_carry_the_tightest_timeout(herdr_present, monkeypatch):
    """The countdown sits inside a grace period and must never eat it."""
    seen = _stub(monkeypatch, {"result": {}})
    HA.set_label("w37:p1", "waiting for operator - 30s -> auto-continue")
    assert seen["argv"][:3] == ["herdr", "agent", "rename"]
    assert seen["kwargs"]["timeout"] == HA.LABEL_TIMEOUT
    assert HA.LABEL_TIMEOUT < HA.LIST_TIMEOUT


def test_clear_label_restores_rather_than_renames(herdr_present, monkeypatch):
    seen = _stub(monkeypatch, {"result": {}})
    HA.clear_label("w37:p1")
    assert seen["argv"] == ["herdr", "agent", "rename", "w37:p1", "--clear"]
