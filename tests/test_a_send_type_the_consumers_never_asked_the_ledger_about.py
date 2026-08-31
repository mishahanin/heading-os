"""A send type the consumers never asked the ledger about.

`.claude/rules/tiered-risk.md` makes one promise: an `action_type` in
`config/tool-risk.json`'s `send_capable` set resolves `gated` no matter what.
`tests/test_tool_risk_coverage.py` binds that promise on the LEDGER side - that
the file lists the senders and that the resolver floors them. This file binds
the CONSUMER side, which is where it was broken.

Both consumers carried a hand-written `("email_send", "telegram_send")` tuple
and put the tier check INSIDE the branch that tuple keyed:

    scripts/action-queue.py            if atype in ("email_send", "telegram_send"):
                                           if tier_for(atype) != GATED: refused
    scripts/action-queue-execute.py    if action_type != "email_send": skipped
                                       if tier_for(action_type) != GATED: refused

So a type registered in `send_capable` but absent from the tuple never reached
the check. The guard was SKIPPED, not failed - and `approve_and_send` then fell
through to `approve_card`, which marks the card `approved`, the exact status the
batch executor selects for sending. Measured 2026-08-31 with a placeholder
`example_send`: `tier_for` was consulted zero times on the executor path, and a
resolver forced to answer `autonomous` produced `skipped` / `approved` from the
two consumers instead of `refused`.

Nothing could actually send then (no executor exists for any second type), so
this was latent defence-in-depth, not a live hole. It would have opened the day
someone added an executor, because a new type branch goes where the others are:
above the old check.

Everything here DERIVES the send-type corpus from `tool_risk.send_capable_types()`
plus one invented placeholder. A hand-maintained list in a test guarding against
hand-maintained lists is the same defect one layer up. Every derivation carries a
minimum-member assertion, because a guard over an empty corpus is green.

Nothing here can send: `subprocess.run` is replaced by a raiser in BOTH consumer
modules before any test runs, the queue always lives under `tmp_path`, and the
real `config/tool-risk.json` is never written - only copies.

Run: python3 -m pytest tests/test_a_send_type_the_consumers_never_asked_the_ledger_about.py
"""
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import tool_risk  # noqa: E402

EXECUTE_PATH = ROOT / "scripts" / "action-queue-execute.py"
CLI_PATH = ROOT / "scripts" / "action-queue.py"
LEDGER_PATH = ROOT / "config" / "tool-risk.json"

# An invented name, never a real integration. It stands for "the send type
# somebody registers next", which is the whole point: the consumers must gate it
# without anyone editing them.
PLACEHOLDER = "example_send"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aqx = _load("aqx_sendtypes", EXECUTE_PATH)
aqcli = _load("aqcli_sendtypes", CLI_PATH)


class _NoTransport:
    """Stands in for the `subprocess` module inside the two consumer copies.

    It REPLACES the module reference rather than assigning to
    `subprocess.run`. Writing `aqx.subprocess.run = ...` reaches through to the
    real stdlib module and rebinds it for the whole interpreter: measured
    2026-08-31, that version of this file turned 18 tests in
    `test_two_controls_that_measured_themselves.py` and its neighbours red
    whenever pytest-randomly scheduled them after it. A test that can only send
    nothing must also leave every other test able to spawn.
    """

    TimeoutExpired = subprocess.TimeoutExpired

    @staticmethod
    def run(*a, **k):
        raise AssertionError(
            "the transport ran on a path that must never send: argv=%r"
            % (a[0] if a else None,))


# Both module objects. `aqcli._AQX` is a SECOND load of
# action-queue-execute.py (the CLI imports it by file path), so it has its own
# `subprocess` name; replacing only `aqx`'s would leave the CLI path live.
aqx.subprocess = _NoTransport
aqcli._AQX.subprocess = _NoTransport


# ============================================================
# Fixtures: a copy of the real ledger, and a queue under tmp_path
# ============================================================

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point tool_risk at a MODIFIED COPY of the shipped ledger.

    Returns a callable taking a mutator over the parsed real ledger. The real
    file is read, never written.
    """
    def _make(mutate=None):
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(data)
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "tool-risk.json").write_text(
            json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(tool_risk, "get_workspace_root", lambda: tmp_path)
        tool_risk.load(force=True)
        return data
    yield _make
    # Clear rather than reload: get_workspace_root is still patched here, so a
    # force-load would leak the temp ledger into later suites.
    tool_risk._CACHE = None


@pytest.fixture
def queue(tmp_path):
    """Write a one-card queue under a temp DATA root. Never the live queue."""
    def _make(card):
        data_root = tmp_path / "data"
        qdir = data_root / "outputs" / "operations" / "action-queue"
        qdir.mkdir(parents=True, exist_ok=True)
        path = qdir / "queue.json"
        path.write_text(json.dumps({"version": 1, "generated_at": None,
                                    "actions": [card]}), encoding="utf-8")
        return data_root, path
    return _make


def _card(action_type, **over):
    c = {"id": "card0001", "action_type": action_type, "status": "pending",
         "to": "recipient@example.invalid", "subject": "s", "draft_body": "b",
         "draft_status": "ready_for_review"}
    c.update(over)
    return c


def _status(path):
    return [c["status"] for c in
            json.loads(path.read_text(encoding="utf-8"))["actions"]]


def _lower(monkeypatch, *types):
    """Force the resolver to answer `autonomous` for `types` - a tampered tier.

    The LEDGER is never edited to produce this. `tier_for` floors a
    `send_capable` name at gated by design, so the only way to observe the
    consumers' own refusal is to make the resolver lie to them.
    """
    real = tool_risk.tier_for
    monkeypatch.setattr(
        tool_risk, "tier_for",
        lambda t: tool_risk.AUTONOMOUS if t in types else real(t))


# ============================================================
# The corpus, and the accessor the consumers now ask
# ============================================================

def _ledger_on_disk():
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def test_the_ledger_names_at_least_two_send_types():
    """Empty-corpus guard for every derivation below.

    Each behavioural test loops over `send_capable_types()`. A renamed key
    yields an empty set and every one of those loops passes vacuously, reporting
    a gate proven over nothing at all.
    """
    names = tool_risk.send_capable_types()
    assert len(names) >= 2, f"send_capable must name at least two senders; got {sorted(names)}"


def test_send_capable_types_reports_exactly_what_the_ledger_lists():
    assert tool_risk.send_capable_types() == frozenset(_ledger_on_disk()["send_capable"])


@pytest.mark.parametrize("raw", [None, "email_send", {"email_send": True}, 7])
def test_send_capable_types_is_empty_for_a_malformed_send_capable(ledger, raw, monkeypatch):
    """The accessor never raises and never invents members on a broken ledger.

    Empty is the honest answer, and it is safe only because no consumer uses
    membership as its sole gate - which is what the tier tests below bind.
    """
    ledger(lambda d: d.__setitem__("send_capable", raw))
    assert tool_risk.send_capable_types() == frozenset()


def test_send_capable_types_drops_non_string_members(ledger):
    ledger(lambda d: d["send_capable"].append(17))
    assert 17 not in tool_risk.send_capable_types()
    assert "email_send" in tool_risk.send_capable_types()


# ============================================================
# Structural: the gate is not keyed on a list, and it dominates the dispatch
# ============================================================

# The names each consumer binds the card's action_type to. Not a security list:
# getting one wrong makes the detectors below find nothing, which the decay
# guards turn red.
TYPE_VARS = frozenset({"action_type", "atype"})


def _func(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} no longer defines {name}()")


def _tier_for_linenos(fn):
    return sorted(n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == "tier_for")


def _type_literal_linenos(node):
    """Every comparison of the action-type variable against a string literal.

    `action_type == "telegram_send"`, `atype in ("email_send", ...)`. These are
    the type-dispatch sites the gate must dominate.
    """
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Compare):
            continue
        names = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
        if not (names & TYPE_VARS):
            continue
        if any(isinstance(c, ast.Constant) and isinstance(c.value, str)
               for c in ast.walk(n)):
            out.append(n.lineno)
    return sorted(out)


def _send_type_collections(tree, senders):
    """Tuple/list/set literals naming two or more of the ledger's senders.

    That shape IS the defect: a second copy of `send_capable` living in a
    consumer, free to fall out of step with the ledger.
    """
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            vals = {e.value for e in n.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if len(vals & senders) >= 2:
                out.append(n.lineno)
    return sorted(out)


def test_the_two_detectors_still_detect():
    """A detector that matches nothing passes everything.

    Both are proven against synthetic source carrying exactly the shape they
    hunt, so a rename or an AST change cannot silently turn the two tests below
    into clean passes over unchecked files.
    """
    senders = frozenset(_ledger_on_disk()["send_capable"])
    assert len(senders) >= 2

    hit = ast.parse('def f(action_type):\n'
                    '    if action_type == "email_send": pass\n')
    assert _type_literal_linenos(hit), "the dispatch detector matches nothing"
    miss = ast.parse('def f(action_type):\n'
                     '    if action_type in send_capable_types(): pass\n')
    assert not _type_literal_linenos(miss), "the dispatch detector over-matches"

    listy = ast.parse('X = ("email_send", "telegram_send")')
    assert _send_type_collections(listy, senders), "the list detector matches nothing"
    tuply = ast.parse('X = ("skipped", "refused")')
    assert not _send_type_collections(tuply, senders), "the list detector over-matches"


@pytest.mark.parametrize("path", [EXECUTE_PATH, CLI_PATH])
def test_neither_consumer_carries_its_own_copy_of_the_send_type_set(path):
    """No hand-written send-type collection in either consumer.

    `scripts/action-queue.py` held `("email_send", "telegram_send")` and
    `scripts/action-queue-execute.py` held the same set as two comparisons. The
    ledger is the single source; a consumer asks `tool_risk.send_capable_types()`.
    """
    senders = frozenset(_ledger_on_disk()["send_capable"])
    assert len(senders) >= 2
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = _send_type_collections(tree, senders)
    assert not found, (
        f"{path.name} names two or more of the ledger's send types in a literal "
        f"collection at line(s) {found}. That is a second copy of "
        f"`send_capable`, and the copy is the one that stops being updated. "
        f"Call tool_risk.send_capable_types() instead."
    )


@pytest.mark.parametrize("path,func", [(EXECUTE_PATH, "send_card"),
                                       (CLI_PATH, "approve_and_send")])
def test_the_tier_check_dominates_every_type_dispatch(path, func):
    """The gate runs BEFORE the function branches on the type - the whole defect.

    The check used to sit below the type dispatch, so a type the dispatch did
    not name returned before reaching it. Position is the invariant: a new
    branch is written where the existing ones are, and every one of those is
    now downstream of the gate.
    """
    fn = _func(path, func)
    gates = _tier_for_linenos(fn)
    dispatch = _type_literal_linenos(fn)
    assert gates, f"{func}() no longer calls tier_for at all"
    assert dispatch, (
        f"the dispatch detector found no type comparison in {func}(); it has "
        f"decayed, or the variable is no longer one of {sorted(TYPE_VARS)}"
    )
    assert min(gates) < min(dispatch), (
        f"{path.name}:{func}() branches on the action_type at line "
        f"{min(dispatch)} before resolving its tier at line {min(gates)}. A "
        f"send type that branch does not name would skip the gate entirely."
    )


# ============================================================
# Behavioural: every send-capable type, on every path that can send it
# ============================================================

# Derived at import from the shipped ledger, never typed out. Parametrisation
# over an empty list generates ZERO cases and reports a clean pass, so
# `test_the_parametrised_corpus_is_not_empty` guards it below.
SEND_TYPES = sorted(tool_risk.send_capable_types())


def test_the_parametrised_corpus_is_not_empty():
    assert len(SEND_TYPES) >= 2, (
        f"the send-type corpus the tests below iterate is {SEND_TYPES}; an "
        f"empty or one-member corpus makes them pass over nothing"
    )


@pytest.mark.parametrize("atype", SEND_TYPES)
def test_the_executor_refuses_every_send_capable_type_whose_tier_is_lowered(
        atype, monkeypatch):
    """The gate, driven to refusal, for each sender the ledger names."""
    _lower(monkeypatch, atype)
    res = aqx.send_card(ROOT, _card(atype))
    assert res["result"] == "refused", (
        f"{atype} resolved below gated and the executor did not refuse: {res}")
    assert "does not resolve gated" in res["error"]


def _no_delegation(monkeypatch):
    """Make any call to `send_card` a failure.

    The two consumers each carry the gate, and the executor's copy runs
    downstream of the CLI's. That masks the CLI's: deleting the CLI's refusal
    and letting the card fall through to `send_card` still produces `refused`,
    so a test reading only the return value cannot tell which layer refused.
    Measured 2026-08-31 - the mutation `if tier != GATED:` -> `if False:` in
    `approve_and_send` SURVIVED the whole suite until this spy existed. Each
    layer has to be bound where it stands.
    """
    def _boom(*a, **k):
        raise AssertionError(
            "approve_and_send delegated to send_card on a card its own gate "
            "must have refused first")
    monkeypatch.setattr(aqcli, "send_card", _boom)


@pytest.mark.parametrize("atype", SEND_TYPES)
def test_the_terminal_approve_refuses_every_send_capable_type_whose_tier_is_lowered(
        atype, monkeypatch, queue):
    """The CLI's OWN gate, bound where it stands - it refuses before delegating."""
    _lower(monkeypatch, atype)
    _no_delegation(monkeypatch)
    data_root, path = queue(_card(atype))
    res = aqcli.approve_and_send(ROOT, data_root, "card0001")
    assert res["result"] == "refused", (
        f"{atype} resolved below gated and approve did not refuse: {res}")
    assert _status(path) == ["pending"], "a refused card must not be moved"


# ---- the placeholder: a send type registered AFTER these consumers shipped ----

def test_a_newly_registered_send_type_reaches_the_executors_gate(ledger, monkeypatch):
    """The defect, bound. Registering `example_send` in the ledger is the ONLY
    step; neither consumer is edited, and both must gate it.
    """
    ledger(lambda d: d["send_capable"].append(PLACEHOLDER))
    assert tool_risk.tier_for(PLACEHOLDER) == tool_risk.GATED

    seen = []
    real = tool_risk.tier_for
    monkeypatch.setattr(tool_risk, "tier_for",
                        lambda t: (seen.append(t), real(t))[1])
    res = aqx.send_card(ROOT, _card(PLACEHOLDER))
    assert PLACEHOLDER in seen, (
        "the executor never asked the ledger about a registered send type - "
        "the gate was SKIPPED, which is the defect this file exists for")
    assert res["result"] != "sent"


def test_a_newly_registered_send_type_reaches_the_terminal_approves_gate(
        ledger, monkeypatch, queue):
    ledger(lambda d: d["send_capable"].append(PLACEHOLDER))
    data_root, path = queue(_card(PLACEHOLDER))
    res = aqcli.approve_and_send(ROOT, data_root, "card0001")
    # It must NOT be recorded as a non-send disposition: `approve_card` writes
    # `approved`, the exact status the batch executor selects for sending.
    assert res["result"] != "approved", (
        "a registered send type was disposed as a non-send action, which marks "
        "it `approved` and hands it to the batch send path ungated")
    assert _status(path) == ["pending"], "the claim must be released"


@pytest.mark.parametrize("consumer", ["executor", "approve"])
def test_a_newly_registered_send_type_is_refused_when_its_tier_is_lowered(
        consumer, ledger, monkeypatch, queue):
    ledger(lambda d: d["send_capable"].append(PLACEHOLDER))
    _lower(monkeypatch, PLACEHOLDER)
    if consumer == "executor":
        res = aqx.send_card(ROOT, _card(PLACEHOLDER))
    else:
        _no_delegation(monkeypatch)   # the CLI must refuse on its own
        data_root, _ = queue(_card(PLACEHOLDER))
        res = aqcli.approve_and_send(ROOT, data_root, "card0001")
    assert res["result"] == "refused", res


# ============================================================
# The unclassified fail-safe, and the emptied ledger
# ============================================================

UNKNOWN = "no_such_action_type_zz"


def test_an_unknown_type_still_resolves_gated():
    assert tool_risk.tier_for(UNKNOWN) == tool_risk.GATED
    assert UNKNOWN not in tool_risk.send_capable_types()


def test_an_unknown_type_never_sends_through_the_executor():
    res = aqx.send_card(ROOT, _card(UNKNOWN))
    assert res["result"] == "skipped"
    assert "no executor" in res["error"]


def test_an_unknown_type_never_sends_through_the_terminal_approve(queue):
    """Gated by the fail-safe, so it takes the send path and finds no executor.

    It must NOT be quietly recorded `approved`, which is what the old
    hand-written tuple did with anything it did not name.
    """
    data_root, path = queue(_card(UNKNOWN))
    res = aqcli.approve_and_send(ROOT, data_root, "card0001")
    assert res["result"] != "approved", res
    assert res["result"] != "sent", res
    assert _status(path) == ["pending"]


def test_a_send_capable_type_with_no_tiers_entry_still_gates(ledger):
    """`send_capable` membership alone carries the floor.

    The placeholder is registered as a sender and deliberately given no `tiers`
    row, so nothing but `send_capable` can produce `gated` here.
    """
    data = ledger(lambda d: d["send_capable"].append(PLACEHOLDER))
    assert PLACEHOLDER not in data["tiers"], "the tiers row must be absent for this to bind"
    assert tool_risk.tier_for(PLACEHOLDER) == tool_risk.GATED
    assert aqx.send_card(ROOT, _card(PLACEHOLDER))["result"] != "sent"


def _empty_send_capable(d):
    d["send_capable"] = []


def test_an_emptied_send_capable_does_not_lower_email_send(ledger):
    """The `tiers` row still says gated, so the resolver still says gated."""
    ledger(_empty_send_capable)
    assert tool_risk.send_capable_types() == frozenset()
    assert tool_risk.tier_for("email_send") == tool_risk.GATED


def test_an_emptied_send_capable_cannot_switch_the_executors_check_off(
        ledger, monkeypatch):
    """The fail-open shape, closed.

    Deriving the send-type set from the ledger creates one risk: empty the set
    and the branch never fires, so the check never runs. It does not happen
    here, because the executor's gate is keyed on `tier_for` - total over all
    action_types - and not on membership. Emptying BOTH halves of the ledger
    (no `send_capable`, `tiers.email_send` lowered to autonomous) still gets the
    resolver asked, and still gets no send.
    """
    ledger(lambda d: (_empty_send_capable(d),
                      d["tiers"].__setitem__(
                          "email_send", {"tier": "autonomous", "reason": "tampered"})))
    assert tool_risk.tier_for("email_send") == tool_risk.AUTONOMOUS

    seen = []
    real = tool_risk.tier_for
    monkeypatch.setattr(tool_risk, "tier_for",
                        lambda t: (seen.append(t), real(t))[1])
    res = aqx.send_card(ROOT, _card("email_send"))
    assert "email_send" in seen, (
        "the gate did not run at all - an emptied ledger switched it off, "
        "which is the fail-open shape this test exists to refuse")
    assert res["result"] != "sent"
    # No transport: `_never_send` would have raised. Asserted explicitly so the
    # guarantee is stated, not implied.
    assert res["result"] in ("skipped", "refused"), res


def test_an_emptied_send_capable_cannot_make_the_terminal_approve_send(
        ledger, queue):
    ledger(lambda d: (_empty_send_capable(d),
                      d["tiers"].__setitem__(
                          "email_send", {"tier": "autonomous", "reason": "tampered"})))
    data_root, path = queue(_card("email_send"))
    res = aqcli.approve_and_send(ROOT, data_root, "card0001")
    # The transport raises if reached, so reaching here at all is the proof.
    assert res["result"] != "sent", res
    assert _status(path) != ["sent"]
