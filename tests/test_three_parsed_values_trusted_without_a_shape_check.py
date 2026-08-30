"""Three parsed values were spent on the shape their annotation promised.

None of the three was ever checked, and each one's consumer is written as
though it had been.

  eval-flag `_request`      annotated `-> dict`, returns `json.loads(...)`
                            unexamined. A JSON list, string or number decodes
                            cleanly, and `cmd_from_card`'s `d.get("items", [])`
                            then raised AttributeError -- a traceback out of the
                            one tool whose documented behaviour for a daemon it
                            cannot use is a one-line message and exit 2. A body
                            that is not JSON at all left through the same hole,
                            caught by neither urllib handler.

  eval-flag `_resolve_id`   annotated `list[dict]`, and every branch calls
                            `c.get(...)`. A list of bare id strings -- the
                            obvious shape some other server would answer with --
                            raised AttributeError from inside a comprehension.

  eval-outcomes `_assert_doctype_render`
                            `o.get("expect_missing", [])` goes straight into
                            `sorted()`. THE DANGEROUS ONE, because it does not
                            crash. A JSON string is a sequence, so
                            `expect_missing: "SUBJECT"` sorts into
                            ['B','C','E','J','S','T','U'] and is compared, one
                            character at a time, against a list of field names.
                            Nothing raises, so `run_one_case`'s `except
                            Exception` -- the place a malformed fixture is meant
                            to surface -- never fires. The case simply grades
                            wrong: `setup_error` stays False and the run exits 1
                            ("a check failed") instead of 2 ("malformed case"),
                            reporting a correct renderer as broken.

The trust boundary for the first two is the loopback daemon, not an
operator-edited file, which lowers the likelihood and not the cost. The
reachable path is a stale `.daemon-state/port`: the daemon dies, the file
survives, the OS hands that port to the next process that asks.

Every check added here is proved to REFUSE something (a case on the wrong side
of the line) and to ADMIT something (a well-formed value that must still pass),
so none of them is a guard with no negative case, and none is dead code.

No test here opens a socket or starts a subprocess; both are blocked for every
test in the file and both blockers are proved to be armed.

Tests: scripts/eval-flag.py, scripts/eval-outcomes.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ==========================================================================
# Isolation
# ==========================================================================

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """`_request` talks to a loopback daemon; nothing here may reach a real one."""
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
    with pytest.raises(RuntimeError, match="real socket"):
        socket.create_connection(("host.invalid", 8765))
    assert no_network == ["('host.invalid', 8765)"]
    no_network.clear()


def _load(filename: str, modname: str):
    """Both scripts are kebab-case, so neither is importable by name."""
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(modname, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ==========================================================================
# eval-flag: the loopback answer
# ==========================================================================

@pytest.fixture()
def flag(tmp_path, monkeypatch):
    m = _load("eval-flag.py", "eval_flag_shape")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    # Both state files present, so nothing short-circuits before the request.
    monkeypatch.setattr(m, "_read_state", lambda root, name: "stub")
    return m


def _answers(mod, monkeypatch, body: bytes):
    """Make the loopback reply with exactly these bytes."""
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Resp(body))


def _code(call) -> object:
    """The exit code however it arrived -- and NOTHING else.

    Any other exception propagates on purpose. Pre-fix these paths left through
    AttributeError, and swallowing it here would turn the defect into a pass.
    """
    try:
        call()
    except SystemExit as e:
        return e.code
    return None


@pytest.mark.parametrize("body,kind", [
    (b'["card-1", "card-2"]', "list"),
    (b'"just a string"', "str"),
    (b"17", "int"),
    (b"null", "NoneType"),
])
def test_a_non_object_response_is_refused_not_dereferenced(
        flag, monkeypatch, capsys, body, kind):
    """Pre-fix: AttributeError out of `d.get("items", [])`, no message, no code."""
    _answers(flag, monkeypatch, body)
    code = _code(lambda: flag.cmd_from_card("anyid", skill=None,
                                            case_type="outcome", as_json=False))
    assert code == 2
    err = capsys.readouterr().err
    assert "Action Queue contract" in err
    assert kind in err, f"the refusal must name what it got; err was {err!r}"


def test_a_body_that_is_not_json_at_all_is_refused(flag, monkeypatch, capsys):
    """`json.loads` raising inside the `with urlopen` block was caught by
    neither `HTTPError` nor `URLError`. An HTML error page is the ordinary way
    a wrong process on that port answers."""
    _answers(flag, monkeypatch, b"<html>502 Bad Gateway</html>")
    code = _code(lambda: flag.cmd_from_card("anyid", skill=None,
                                            case_type="outcome", as_json=False))
    assert code == 2
    assert "not JSON" in capsys.readouterr().err


def test_a_well_formed_object_still_comes_back_unchanged(flag, monkeypatch):
    """The admit case. A check that refuses everything is not a shape check."""
    payload = {"items": [{"id": "abc123", "title": "a draft"}]}
    _answers(flag, monkeypatch, json.dumps(payload).encode("utf-8"))
    got = flag._request("GET", "/action-queue", "tok", "9999")
    assert got == payload


def test_an_unreachable_daemon_still_takes_its_own_path(flag, monkeypatch, capsys):
    """The pre-existing refusal must not have been rerouted through the new one:
    a daemon that is DOWN is a different fact from one answering off-contract,
    and the operator is told which."""
    def _boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(flag.urllib.request, "urlopen", _boom)
    code = _code(lambda: flag.cmd_from_card("anyid", skill=None,
                                            case_type="outcome", as_json=False))
    assert code == 2
    err = capsys.readouterr().err
    assert "bridge daemon not reachable" in err
    assert "Action Queue contract" not in err


# ---- the elements, not the envelope ----------------------------------------

@pytest.mark.parametrize("items", [
    ["card-1", "card-2"],          # a list of bare ids
    [{"id": "ok"}, "card-2"],      # one good element and one not
    "card-1",                      # a string: iterable, yields characters
    {"id": "ok"},                  # an object where a list belongs
    [None],
])
def test_items_that_are_not_card_objects_are_refused(flag, items, capsys):
    """Pre-fix: AttributeError from inside the first comprehension.

    Not covered by the envelope check above -- `{"items": "card-1"}` is a
    perfectly well-formed JSON object, so `_request` passes it through and the
    element shape is still wrong.
    """
    code = _code(lambda: flag._resolve_id(items, "card"))
    assert code == 2
    assert "list of card objects" in capsys.readouterr().err


def test_a_well_formed_item_list_still_resolves_a_prefix(flag):
    """The admit case, and it proves the guard did not eat the feature."""
    items = [{"id": "abc123"}, {"id": "def456"}]
    assert flag._resolve_id(items, "abc") == "abc123"
    assert flag._resolve_id(items, "abc123") == "abc123"


@pytest.mark.parametrize("prefix,why", [("zzz", "no match"), ("", "ambiguous")])
def test_the_operators_own_mistakes_keep_exit_1(flag, prefix, why):
    """A prefix that matches nothing, or everything, is a USAGE error and stays
    exit 1. Folding it into the payload refusal's exit 2 would tell the operator
    their daemon is broken when their typing was."""
    items = [{"id": "abc123"}, {"id": "def456"}]
    assert _code(lambda: flag._resolve_id(items, prefix)) == 1, why


# ==========================================================================
# eval-outcomes: the string that sorted into characters
# ==========================================================================

@pytest.fixture()
def outcomes(monkeypatch):
    m = _load("eval-outcomes.py", "eval_outcomes_shape")

    def _no_subprocess(*a, **k):
        raise AssertionError("a test in this file started a subprocess")

    monkeypatch.setattr(m.subprocess, "run", _no_subprocess)
    return m


def test_the_subprocess_blocker_is_actually_armed(outcomes):
    with pytest.raises(AssertionError, match="started a subprocess"):
        outcomes.subprocess.run(["true"])


def _official_case(expect_missing, **drop) -> dict:
    """A real `official` doctype case. Invented issuer; no live fixture read."""
    data = {
        "CLASS": "Board Resolution", "REF_ID": "R-1", "DATE": "2026-06-06",
        "PLACE": "Port Aurelia, Sample Country", "ISSUER_NAME": "Avery Larkspur",
        "ISSUER_TITLE": "Chief Executive", "SUBJECT": "Sample subject",
    }
    for key in drop:
        data.pop(key, None)
    return {"id": "shape-case", "outcome": {
        "type": "doctype_render", "doctype": "official",
        "data": data, "expect_missing": expect_missing,
    }}


def test_the_list_form_still_grades_and_still_passes(outcomes):
    """The admit case, and the baseline the string form is measured against:
    one field genuinely missing, declared as a one-element LIST, grades PASS
    with no setup error."""
    case = _official_case(["SUBJECT"], SUBJECT=1)
    results, setup_error = outcomes.run_one_case(case, render=False)
    assert setup_error is False
    assert [r["passed"] for r in results] == [True]


def test_the_empty_default_still_grades(outcomes):
    """`expect_missing` absent entirely is the common positive fixture and must
    keep working: the default `[]` is a list and survives the check."""
    case = _official_case([])
    del case["outcome"]["expect_missing"]
    results, setup_error = outcomes.run_one_case(case, render=False)
    assert setup_error is False
    assert [r["passed"] for r in results] == [True]


def test_a_string_expect_missing_is_refused_not_sorted_into_characters(outcomes):
    """THE defect, in the direction that produces a wrong VERDICT.

    Same fixture as the passing list case above, with the one-element list
    written as a bare string. Pre-fix: `sorted("SUBJECT")` is
    ['B','C','E','J','S','T','U'], compared against ['SUBJECT'], unequal, so
    the case graded FAIL -- on a `validate_required_fields` that had answered
    correctly -- with `setup_error` False and the run exiting 1 rather than 2.
    """
    case = _official_case("SUBJECT", SUBJECT=1)
    results, setup_error = outcomes.run_one_case(case, render=False)

    # A malformed fixture is a SETUP error. This is the assertion the pre-fix
    # code failed while looking green-adjacent: it reported False here and
    # blamed the code under test.
    assert setup_error is True, (
        "a bare string must be refused as an unusable fixture, not graded; "
        f"got results={results}"
    )
    detail = " ".join(r["detail"] for r in results)
    assert "expect_missing" in detail
    assert "list of field-name strings" in detail
    # And the wrong answer specifically: nothing anywhere compared characters.
    assert "'B'" not in detail and "'J'" not in detail, (
        f"the string was still shredded into characters: {detail}"
    )
    assert not any(r["passed"] for r in results)


def test_a_string_expect_missing_can_no_longer_skip_the_render_assertion(outcomes):
    """The defect's SECOND direction, on the `--render` path.

    `if render and not expect_missing` reads a non-empty string as "this is a
    negative fixture, do not render it". So a COMPLETE fixture that merely
    misspelled `expect_missing` had its real-render assertion silently dropped
    and still returned a result list -- `--render` grading a case it never
    rendered. Post-fix the value is refused before that branch is reached,
    which is also why the subprocess blocker above never fires here.
    """
    case = _official_case("SUBJECT")  # data is COMPLETE; nothing is missing
    results, setup_error = outcomes.run_one_case(case, render=True)
    assert setup_error is True
    assert not any(r["passed"] for r in results)


@pytest.mark.parametrize("bad", [
    "SUBJECT",            # the measured one
    "",                   # falsy, so it slid through as "no missing fields"
    {"SUBJECT": True},    # a dict sorts to its KEYS, silently
    ["SUBJECT", 7],       # a list, but not of field names
    17,
    None,
])
def test_every_non_list_of_strings_is_refused(outcomes, bad):
    case = _official_case(bad, SUBJECT=1)
    _, setup_error = outcomes.run_one_case(case, render=False)
    assert setup_error is True, f"{bad!r} must be refused"
