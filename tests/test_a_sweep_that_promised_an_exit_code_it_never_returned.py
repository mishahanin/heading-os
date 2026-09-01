"""Two contracts scripts/email-sweep.py stated and did not keep.

1. The docstring promised exit 2 whenever "the sweep file for that date is
   missing or unreadable". For a MISSING file that is true of the four mutating
   commands only: `cmd_list` and `cmd_pending` print "no sweep for <date>" and
   return 0, which is the right answer to a query about a day nobody has swept.
   The doc overpromised; the code was correct and is unchanged. The scope test
   below DERIVES the command list from the exit codes those commands actually
   return, so a future edit to either side has to move both.

2. `bool` is a subclass of `int`, so every `isinstance(a.get("id"), int)` shape
   guard in the file accepted a hand-edited `"id": true`, and `hash(True) == 1`
   then collided it with action #1 in `_mutate_ids`'s `by_id` map. Being later
   in the list it WON the key, so `approve 1` moved the boolean-keyed entry and
   reported "#1" over an action the operator never read.

Run: .venv/bin/python -m pytest tests/test_a_sweep_that_promised_an_exit_code_it_never_returned.py -q
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("email_sweep", ROOT / "scripts" / "email-sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

DATE = "2026-08-31"
_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _args(**kw):
    ns = argparse.Namespace()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# ============================================================
# 1. The exit-code contract
# ============================================================

def _run_against_a_missing_sweep(root: Path, cmd: str) -> int:
    """Run one command against a root that has no sweep file for DATE."""
    root.mkdir(parents=True, exist_ok=True)
    if cmd == "propose":
        payload = root / "proposed.json"
        payload.write_text(json.dumps([{"type": "task", "title": "seed"}]), encoding="utf-8")
        return sweep.cmd_propose(root, _args(file=str(payload), date=DATE))
    if cmd == "list":
        return sweep.cmd_list(root, _args(date=DATE, json=False))
    if cmd == "pending":
        return sweep.cmd_pending(root, _args(date=DATE, json=False))
    if cmd == "approve":
        return sweep.cmd_approve(root, _args(date=DATE, ids=[1]))
    if cmd == "skip":
        return sweep.cmd_skip(root, _args(date=DATE, ids=[1], note=None))
    if cmd == "edit":
        return sweep.cmd_edit(root, _args(date=DATE, id=1, note="tighten it"))
    if cmd == "set":
        return sweep.cmd_set(root, _args(date=DATE, id=1, status="done", note=None))
    raise AssertionError(f"unlisted command {cmd!r}")


COMMANDS = ("propose", "list", "pending", "approve", "skip", "edit", "set")


def _documented_entry(code: int) -> str:
    """The docstring's own text for one exit code, joined onto one line."""
    lines = sweep.__doc__.splitlines()
    start = lines.index("Exit codes:")
    entry, collecting = [], False
    for line in lines[start + 1:]:
        if not line.strip():
            break
        head = re.match(r"^  (\d)  (.*)$", line)
        if head:
            if collecting:
                break
            if head.group(1) == str(code):
                collecting = True
                entry.append(head.group(2))
        elif collecting:
            entry.append(line.strip())
    return " ".join(entry)


def test_the_docstring_scopes_exit_two_to_the_commands_that_return_it(tmp_path, capsys):
    """Which commands the doc names must equal which commands actually exit 2.

    Nothing here is typed twice. The right-hand side is measured by running
    every command against a date with no sweep file; the left-hand side is
    parsed out of the docstring. The old text named no command at all, so the
    two sets could not match however the code behaved.
    """
    observed = {c: _run_against_a_missing_sweep(tmp_path / c, c) for c in COMMANDS}
    capsys.readouterr()

    entry = _documented_entry(2)
    assert entry, "the docstring has no exit-code 2 entry to check"
    missing_file_clause = entry.split(";")[0]
    assert missing_file_clause.strip(), "the exit-2 entry has no missing-file clause"

    named = {c for c in COMMANDS if f"`{c}`" in missing_file_clause}
    returns_two = {c for c, rc in observed.items() if rc == 2}

    assert returns_two, "no command exits 2 on a missing sweep; the fixture is wrong"
    assert named == returns_two, (
        f"docstring names {sorted(named)} for a missing sweep file, "
        f"but {sorted(returns_two)} actually exit 2 (observed: {observed})"
    )


def test_the_docstring_still_names_the_date_condition():
    """The earlier wave's finding, kept: the date check is exit 2, not exit 1."""
    entry = _documented_entry(2)
    assert "--date is not an exact YYYY-MM-DD" in entry


@pytest.mark.parametrize("cmd", ["list", "pending"])
def test_a_read_only_command_answers_a_missing_sweep_with_zero_and_a_message(
        tmp_path, capsys, cmd):
    rc = _run_against_a_missing_sweep(tmp_path / cmd, cmd)
    out = _plain(capsys.readouterr().out)
    assert rc == 0
    assert f"no sweep for {DATE}" in out


@pytest.mark.parametrize("cmd", ["approve", "skip", "edit", "set"])
def test_a_mutate_refuses_a_missing_sweep_with_two_and_a_message(tmp_path, capsys, cmd):
    rc = _run_against_a_missing_sweep(tmp_path / cmd, cmd)
    err = _plain(capsys.readouterr().err)
    assert rc == 2
    assert f"no sweep file for {DATE}" in err
    assert "run propose first" in err


# ============================================================
# 2. True is an int
# ============================================================

def _seed(root: Path, actions: list[dict]) -> Path:
    path = sweep._state_path(root, DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": DATE, "actions": actions}), encoding="utf-8")
    return path


def _statuses(path: Path) -> dict:
    """status by the REPR of the id, so 1 and True stay distinguishable."""
    return {repr(a["id"]): a["status"] for a in json.loads(path.read_text())["actions"]}


REAL = {"id": 1, "type": "crm_log", "tier": "local", "title": "log the mNDA",
        "status": "proposed", "note": ""}
BOOLEAN = {"id": True, "type": "send_reply", "tier": "gated",
           "title": "reply to the vendor", "status": "proposed", "note": ""}


def test_approve_one_cannot_reach_a_boolean_keyed_entry(tmp_path, capsys):
    path = _seed(tmp_path, [REAL, BOOLEAN])
    loaded = json.loads(path.read_text())["actions"]
    # The collision is the premise, so measure it rather than assert it in prose.
    assert len({a["id"] for a in loaded}) == 1, "ids no longer collide; premise gone"

    rc = sweep.cmd_approve(tmp_path, _args(date=DATE, ids=[1]))
    err = _plain(capsys.readouterr().err)

    assert rc == 1
    assert "malformed action entr" in err
    assert _statuses(path) == {"1": "proposed", "True": "proposed"}


def test_the_boolean_entry_is_reported_by_list_and_by_pending(tmp_path, capsys):
    _seed(tmp_path, [REAL, BOOLEAN])
    sweep.cmd_list(tmp_path, _args(date=DATE, json=False))
    listed = _plain(capsys.readouterr().out)
    sweep.cmd_pending(tmp_path, _args(date=DATE, json=False))
    pended = _plain(capsys.readouterr().out)

    assert "malformed entry" in listed
    assert "log the mNDA" in listed        # the sound entry still renders
    assert "[malformed]" in pended         # unresolved work, never dropped


def test_propose_refuses_a_sweep_carrying_a_boolean_id(tmp_path, capsys):
    payload = tmp_path / "proposed.json"
    payload.write_text(json.dumps([{"type": "task", "title": "new one"}]), encoding="utf-8")
    path = _seed(tmp_path, [BOOLEAN])

    rc = sweep.cmd_propose(tmp_path, _args(file=str(payload), date=DATE))
    err = _plain(capsys.readouterr().err)

    assert rc == 1
    assert "non-numeric action id" in err
    assert len(json.loads(path.read_text())["actions"]) == 1  # nothing appended


def test_a_sound_sweep_still_approves(tmp_path, capsys):
    """The guard refuses corruption, not every sweep."""
    path = _seed(tmp_path, [REAL, dict(BOOLEAN, id=2)])
    rc = sweep.cmd_approve(tmp_path, _args(date=DATE, ids=[1, 2]))
    capsys.readouterr()
    assert rc == 0
    assert _statuses(path) == {"1": "approved", "2": "approved"}


# ============================================================
# 3. An entry that is not an object at all
# ============================================================
#
# `_has_int_id` opens with `if not isinstance(action, dict): return False`, and
# nothing measured it. MEASURED 2026-09-01 by removing that line and driving the
# real commands against a state file holding `[<a sound action>, null, "a
# string", 7]`: `list`, `pending`, `approve` and `set` each raised
# `AttributeError: 'NoneType' object has no attribute 'get'`, and the whole
# suite stayed green - 31 passed across this file, `test_email_sweep.py` and
# `test_a_record_that_lost_a_row_and_refused_with_the_wrong_error.py`.
#
# The path is reachable, not hypothetical. `_load_state` checks only that
# `actions` IS A LIST (`isinstance(data.get("actions"), list)`); it never checks
# what is in it, and the four sites at lines 366, 421 and 440 iterate that list
# straight into `_has_int_id`. The one site that pre-filters with
# `isinstance(a, dict)` is `cmd_propose`, so it is the only command the outer
# check protects.
#
# Same threat model as the boolean id above and the same file: a hand-edited
# sweep. `"id": true` needed a plausible edit to produce; `null` in a JSON list
# needs a stray comma.

NOT_OBJECTS = [None, "a string", 7, []]


def test_list_and_pending_report_a_non_object_entry_instead_of_crashing(tmp_path, capsys):
    _seed(tmp_path, [REAL, *NOT_OBJECTS])

    assert sweep.cmd_list(tmp_path, _args(date=DATE, json=False)) == 0
    listed = _plain(capsys.readouterr().out)
    assert "malformed entry" in listed
    assert "log the mNDA" in listed, "the sound entry stopped rendering"

    assert sweep.cmd_pending(tmp_path, _args(date=DATE, json=False)) == 0
    assert "[malformed]" in _plain(capsys.readouterr().out)


@pytest.mark.parametrize("cmd", ["approve", "skip", "edit", "set"])
def test_a_mutate_refuses_a_non_object_entry_instead_of_crashing(tmp_path, capsys, cmd):
    """Refused, and the sound entry is left exactly as it was.

    A traceback halfway through a mutate is worse than a refusal: it leaves the
    operator unable to say which entries were written before it stopped.
    """
    path = _seed(tmp_path, [REAL, *NOT_OBJECTS])
    call = {
        "approve": lambda: sweep.cmd_approve(tmp_path, _args(date=DATE, ids=[1])),
        "skip": lambda: sweep.cmd_skip(tmp_path, _args(date=DATE, ids=[1], note=None)),
        "edit": lambda: sweep.cmd_edit(tmp_path, _args(date=DATE, id=1, note="x")),
        "set": lambda: sweep.cmd_set(tmp_path, _args(date=DATE, id=1,
                                                    status="done", note=None)),
    }[cmd]

    rc = call()
    err = _plain(capsys.readouterr().err)
    assert rc == 1, f"{cmd} did not refuse a state file holding non-object entries"
    assert "malformed action entr" in err
    # `_statuses` indexes every entry, which the non-object ones do not support,
    # so read the sound one on its own. The state file must be untouched.
    sound = [a for a in json.loads(path.read_text())["actions"]
             if isinstance(a, dict) and a.get("id") == 1]
    assert [a["status"] for a in sound] == ["proposed"], (
        f"{cmd} mutated past the refusal")
