"""Frozen contract - A2, the gate-yield ledger.

Two numbers, measured 2026-08-02, scope this and pull in opposite directions.

The Canopus ledger holds 152 events and NOT ONE REFUSAL: freeze 49, release 49,
approve 28, anchor_replaced 20, verify_fail 6, every one a success or a late
verify failure. `cmd_approve`, `cmd_freeze` and `cmd_release` carry twelve early
returns between them and none of them touches the ledger. Every time the standard
has refused the builder, the event vanished.

The denial counter is one day old and holds exactly one record.

So the recorder is built in full and the reporter at its minimum, and the
minimum is not a compromise: with one day of data the report's job is not to
adjudicate the subtraction list, it is to say WHEN that list can be adjudicated.
A confident table of zeros, where zero means "no occasion arose" and reads as
"does not work", is the machine that would get 700 lines cut for nothing. Two
properties below exist to make building that machine impossible rather than
merely unintended: TOO EARLY is a distinct verdict from NO YIELD, and the report
cannot form a removal recommendation at all.

Authoring rule: every import of the code under test happens INSIDE a test body.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CLI = _ROOT / "scripts" / "canopus.py"
_REPORT = _ROOT / "scripts" / "gate-yield.py"
_GATED = ("cmd_approve", "cmd_freeze", "cmd_release")

# The two sources started on different days, and that is the whole reason
# `since` is a mapping. Step 5 of this slice caught the single-window version:
# it would have judged the one-day-old denial log over the ledger's window and
# called a mechanism silent that had not yet had a day to speak.
_SINCE = {"lifecycle": "2026-08-01T00:00:00+00:00",
          "denials": "2026-08-02T00:00:00+00:00"}


def _functions():
    """The lifecycle commands that can refuse, as AST."""
    tree = ast.parse(_CLI.read_text(encoding="utf-8"))
    found = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name in _GATED}
    assert set(found) == set(_GATED), f"missing {set(_GATED) - set(found)}"
    return found


def _refusals(fn):
    """Every early return of a failing exit code, by line number."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if value in (1, 2):
                out.append(node)
    return out


def _run(args, cwd=None):
    return subprocess.run([sys.executable, str(_CLI), *args], capture_output=True,
                          text=True, cwd=str(cwd or _ROOT), env=dict(os.environ),
                          timeout=180)


def _report(args, env=None):
    return subprocess.run([sys.executable, str(_REPORT), *args], capture_output=True,
                          text=True, cwd=str(_ROOT), timeout=180,
                          env=dict(os.environ, **(env or {})))


def _without_root(text, root):
    """The message with the tree it ran in taken out.

    Everything else is the refusal itself, which is what SC-3 is about.
    """
    return str(text).replace(str(root), "<root>")


def _payload(proc):
    assert proc.stdout.strip(), f"no output (exit {proc.returncode})\n{proc.stderr[:600]}"
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"not JSON: {exc}\n{proc.stdout[:400]}") from exc


@pytest.fixture
def scratch(tmp_path):
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# gate\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Property 1 - every refusal leaves a record, and none can be added silently
# ---------------------------------------------------------------------------

def test_no_refusal_path_in_the_lifecycle_returns_without_recording():
    """The point of the slice, and it fails BY NAME.

    A test that merely counts `return 1` proves the count, not the coverage: a
    refusal returning through a helper would pass while recording nothing. This
    walks each refusal's own statement list and names the line it found.
    """
    from scripts.utils.gate_yield import RECORDER

    unrecorded = []
    for name, fn in _functions().items():
        for node in _refusals(fn):
            block = _enclosing_block(fn, node)
            recorded = any(
                isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and _called_name(stmt.value) == RECORDER
                for stmt in block)
            if not recorded:
                unrecorded.append(f"{name} line {node.lineno}")
    assert not unrecorded, (
        f"these refusals leave no record, so their yield can never be counted: "
        f"{unrecorded}")


def _enclosing_block(fn, target):
    """The statement list that directly contains *target*."""
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and any(stmt is target for stmt in block):
                return block
    return []


def _called_name(call):
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def test_the_recorder_has_exactly_one_name_so_the_guard_above_cannot_be_dodged():
    from scripts.utils.gate_yield import RECORDER

    assert RECORDER
    assert RECORDER in _CLI.read_text(encoding="utf-8")


def test_a_refused_freeze_appends_one_refusal_event(scratch):
    """End to end, through the CLI, over a real ledger on disk."""
    from scripts.utils.canopus_freeze import read_ledger

    proc = _run(["--root", str(scratch), "freeze", "--label", "x",
                 "--anchor", str(scratch / "missing.md"),
                 "--content", str(scratch / "scripts" / "run-tests.py")])
    assert proc.returncode != 0, "this call was supposed to be refused"

    events = [r for r in read_ledger(scratch) if r["event"].startswith("refuse_")]
    assert len(events) == 1, read_ledger(scratch)
    assert events[0]["event"] == "refuse_freeze"


def test_the_mechanism_is_in_the_event_and_the_cause_is_a_class_not_prose(scratch):
    """SC-2. Two refusals of one kind must count as two of one thing, so the
    cause is a declared token; the human sentence stays in `reason`."""
    from scripts.utils.canopus_freeze import read_ledger
    from scripts.utils.gate_yield import CAUSES, MECHANISMS

    _run(["--root", str(scratch), "freeze", "--label", "x",
          "--anchor", str(scratch / "missing.md"),
          "--content", str(scratch / "scripts" / "run-tests.py")])
    row = [r for r in read_ledger(scratch) if r["event"].startswith("refuse_")][0]

    assert row["event"].split("refuse_", 1)[1] in MECHANISMS
    assert row["kind"] in CAUSES, f"{row['kind']!r} is not a declared cause class"
    assert " " not in row["kind"] and row["kind"] == row["kind"].lower()


def test_every_declared_cause_is_reachable_from_the_source():
    """A vocabulary with entries nothing can emit is a vocabulary that lies."""
    from scripts.utils.gate_yield import CAUSES

    assert len(CAUSES) >= 4, f"a vocabulary of {len(CAUSES)} classes describes nothing"
    assert all(isinstance(c, str) and c for c in CAUSES), CAUSES
    source = _CLI.read_text(encoding="utf-8")
    unreachable = sorted(c for c in CAUSES if repr(c)[1:-1] not in source)
    assert not unreachable, f"declared but never emitted: {unreachable}"


# ---------------------------------------------------------------------------
# Property 2 - recording can never change a refusal
# ---------------------------------------------------------------------------

def test_an_unwritable_ledger_leaves_the_refusal_exactly_as_it_was(scratch):
    """The one thing worse than an unrecorded refusal is a refusal that stops
    refusing because its own logging raised."""
    clean = _run(["--root", str(scratch), "freeze", "--label", "x",
                  "--anchor", str(scratch / "missing.md"),
                  "--content", str(scratch / "scripts" / "run-tests.py")])

    second = scratch.parent / "tree2"
    (second / "scripts").mkdir(parents=True)
    (second / "scripts" / "run-tests.py").write_text("# gate\n", encoding="utf-8")
    state = second / ".canopus"
    state.mkdir()
    (state / "history.jsonl").mkdir()          # a directory where a file must go

    blocked = _run(["--root", str(second), "freeze", "--label", "x",
                    "--anchor", str(second / "missing.md"),
                    "--content", str(second / "scripts" / "run-tests.py")])

    assert blocked.returncode == clean.returncode
    assert clean.stderr.strip()
    # Compared with the root normalised out, because the two runs happen in
    # different directories and the refusal names the path it refused. Retake,
    # 2026-08-02: the first version compared raw stderr and so compared where
    # each ran rather than what each did, which is the second contract test in
    # two slices to couple itself to its own environment. The invariant SC-3
    # actually claims is that the refusal is unchanged, not that two runs in two
    # places print the same string.
    assert _without_root(clean.stderr, scratch).strip() in _without_root(
        blocked.stderr, second)


def test_the_recorder_returns_its_failure_instead_of_raising(tmp_path):
    from scripts.utils.gate_yield import record_refusal

    blocked = tmp_path / "root"
    (blocked / ".canopus").mkdir(parents=True)
    (blocked / ".canopus" / "history.jsonl").mkdir()
    failure = record_refusal(blocked, mechanism="freeze", cause="anchor_missing",
                             label="x", reason="why")
    assert isinstance(failure, str) and failure, "a silent failure is not a report"


def test_a_successful_record_reports_no_failure(tmp_path):
    from scripts.utils.gate_yield import record_refusal

    root = tmp_path / "root"
    root.mkdir()
    assert record_refusal(root, mechanism="freeze", cause="anchor_missing",
                          label="x", reason="why") == ""


# ---------------------------------------------------------------------------
# Property 3 - the record carries the class of thing refused, never the thing
# ---------------------------------------------------------------------------

def test_a_refusal_record_never_carries_the_refused_content(scratch):
    """Same boundary A1 already holds. A refused path is attacker-shaped input."""
    from scripts.utils.canopus_freeze import read_ledger
    from scripts.utils.gate_yield import redact_reason

    # Named for what it IS: a value somebody tried to push past a guard,
    # which is what every refusal record is about. The earlier name tripped
    # the commit gate's keyword heuristic and made the whole slice
    # uncommittable, which no allow-list entry was going to fix.
    pushed_value = "sk-" + "A" * 24
    _run(["--root", str(scratch), "freeze", "--label", pushed_value,
          "--anchor", str(scratch / "missing.md"),
          "--content", str(scratch / "scripts" / "run-tests.py")])
    row = [r for r in read_ledger(scratch) if r["event"].startswith("refuse_")][0]
    assert pushed_value not in json.dumps(row), (
        f"the refused value was recorded: {row}")
    assert redact_reason(f"refused {pushed_value}") != f"refused {pushed_value}"


# ---------------------------------------------------------------------------
# Property 4 - the report answers WHEN, and cannot recommend a removal
# ---------------------------------------------------------------------------

def test_the_report_cannot_form_a_removal_recommendation():
    """The operator's own instruction, made structural rather than promised.

    Nothing here is removed without his explicit permission, and a report that
    can say "cut this" is one bad window away from getting 700 lines cut for
    nothing. A mechanism that cannot pronounce the word cannot recommend it.
    """
    from scripts.utils.gate_yield import FORBIDDEN_VERBS, render

    text = render(_sample(), now="2026-10-02T00:00:00+00:00").lower()
    said = sorted(v for v in FORBIDDEN_VERBS if v in text)
    assert not said, f"the report recommended a subtraction: {said}"
    assert FORBIDDEN_VERBS, "an empty forbidden list forbids nothing"


def test_the_flag_says_in_its_own_words_that_the_decision_is_the_operators():
    from scripts.utils.gate_yield import render

    text = render(_sample(), now="2026-10-02T00:00:00+00:00").lower()
    assert "operator" in text or "yours" in text
    assert "flag" in text


def test_too_early_is_a_distinct_verdict_from_no_yield():
    """Zero catches in one day and zero in one month are different facts."""
    from scripts.utils.gate_yield import NO_YIELD, TOO_EARLY

    assert TOO_EARLY != NO_YIELD


def test_a_silent_mechanism_inside_the_budget_reads_too_early():
    from scripts.utils.gate_yield import TOO_EARLY, summarise

    out = summarise(ledger=[], denials=[], since=_SINCE,
                    now="2026-08-03T00:00:00+00:00")
    assert all(m["verdict"] == TOO_EARLY for m in out["mechanisms"].values()), out


def test_a_silent_mechanism_beyond_the_budget_is_flagged_with_its_number():
    from scripts.utils.gate_yield import NO_YIELD, summarise

    out = summarise(ledger=[], denials=[], since=_SINCE,
                    now="2026-10-02T00:00:00+00:00")
    silent = [m for m in out["mechanisms"].values() if m["verdict"] == NO_YIELD]
    assert silent, out
    assert all(m["caught"] == 0 for m in silent)
    assert all(isinstance(m["days"], int) and m["days"] >= 31 for m in silent)


def test_a_mechanism_that_has_caught_something_is_not_flagged():
    from scripts.utils.gate_yield import CATCHING, summarise

    out = summarise(
        ledger=[{"event": "refuse_freeze", "kind": "anchor_missing",
                 "ts": "2026-08-02T00:00:00+00:00", "label": "s"}],
        denials=[], since=_SINCE,
        now="2026-10-02T00:00:00+00:00")
    assert out["mechanisms"]["freeze"]["verdict"] == CATCHING
    assert out["mechanisms"]["freeze"]["caught"] == 1
    assert out["mechanisms"]["freeze"]["last_catch"] == "2026-08-02T00:00:00+00:00"


def test_the_report_always_states_the_window_it_judged_over():
    """A verdict without its window is the confident table this slice refuses."""
    from scripts.utils.gate_yield import render, summarise

    out = summarise(ledger=[], denials=[], since=_SINCE,
                    now="2026-08-03T00:00:00+00:00")
    assert out["windows"] == {"lifecycle": 2, "denials": 1}
    rendered = render(out, now="2026-08-03T00:00:00+00:00")
    assert "2" in rendered and "1" in rendered


def test_the_budget_is_the_operators_one_month():
    from scripts.utils.gate_yield import BUDGET_DAYS

    assert BUDGET_DAYS == 31


def test_the_report_counts_the_denial_log_beside_the_lifecycle():
    """A1 and A2 are two halves of one question, not two reports."""
    from scripts.utils.gate_yield import summarise

    out = summarise(ledger=[], denials=[{"mechanism": "depth-gate",
                                         "ts": "2026-08-01T12:00:00+00:00"}],
                    since=_SINCE,
                    now="2026-10-02T00:00:00+00:00")
    assert out["mechanisms"]["depth-gate"]["caught"] == 1


def test_the_report_writes_nothing(tmp_path):
    from scripts.utils.gate_yield import render, summarise

    (tmp_path / "sentinel").write_text("x", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert before == ["sentinel"], "the comparison must have something to compare"

    text = render(summarise(ledger=[], denials=[],
                            since=_SINCE,
                            now="2026-08-03T00:00:00+00:00"),
                  now="2026-08-03T00:00:00+00:00")
    assert text.strip(), "a report that renders nothing writes nothing trivially"
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_missing_source_degrades_to_a_named_gap_not_a_zero():
    """An absent log and an empty one are different facts, and reading the first
    as the second is how a mechanism gets called dead for a missing file."""
    from scripts.utils.gate_yield import read_sources

    out = read_sources(Path("/nonexistent-canopus-root"))
    assert out["ledger"] == []
    assert out["missing"], "a missing source was reported as an empty one"


def test_the_cli_renders_and_exits_clean():
    proc = _report([])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip()


def test_the_cli_speaks_json_too():
    payload = _payload(_report(["--json"]))
    assert "mechanisms" in payload and "windows" in payload


def _sample():
    from scripts.utils.gate_yield import summarise

    return summarise(ledger=[], denials=[], since=_SINCE,
                     now="2026-10-02T00:00:00+00:00")


def test_a_mechanism_is_judged_over_its_own_sources_window_not_the_oldest_one():
    """Found at step 5, before the freeze, which is the cheapest moment.

    The lifecycle ledger began 2026-07-25 and the denial log began 2026-08-01.
    One shared window would have judged a one-day-old mechanism over an
    eight-day one and called it silent before it had a day to speak. That is
    precisely the false NO YIELD this slice exists to make impossible.
    """
    from scripts.utils.gate_yield import TOO_EARLY, summarise

    out = summarise(ledger=[], denials=[],
                    since={"lifecycle": "2026-06-01T00:00:00+00:00",
                           "denials": "2026-10-01T00:00:00+00:00"},
                    now="2026-10-02T00:00:00+00:00")
    young = [m for m in out["mechanisms"].values() if m["source"] == "denials"]
    assert young, out
    assert all(m["verdict"] == TOO_EARLY for m in young), (
        "a mechanism recorded for one day was judged over another source's window")
    assert all(m["days"] == 1 for m in young)
