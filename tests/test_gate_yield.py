"""A2, the gate-yield report over the denial log.

Retired from `tests/contract/2026-08-02-gate-yield/` into the ordinary suite at
step 13, 2026-08-02, unchanged apart from this note and the root path. The
coverage is worth keeping; the lock on it would bind every later slice to this
one's behaviour.

The lifecycle half of the instrument is gone. Its recorder wrote to the Canopus
freeze ledger, and that ledger's producers were deleted on 2026-08-07: a report
of permanent zeros is exactly the failure `gate_yield`'s own docstring names,
"how a mechanism gets removed for never having had the chance to fire". Ten
tests went with it here -- three whose subject was the recorder or the
redaction it applied on the way into that ledger, four whose subject was the
hand classification of ledger retakes, and three whose fixtures were lifecycle
rows or the two-source window discrimination. What is left measures something:
the denial log, the wall/gate split, and the report's inability to recommend a
subtraction.

The denial counter is one day old and holds exactly one record.

So the reporter runs at its minimum, and the minimum is not a compromise: with
one day of data the report's job is not to adjudicate the subtraction list, it is
to say WHEN that list can be adjudicated. A confident table of zeros, where zero
means "no occasion arose" and reads as "does not work", is the machine that would
get 700 lines cut for nothing. Two properties below exist to make building that
machine impossible rather than merely unintended: TOO EARLY is a distinct verdict
from NO YIELD, and the report cannot form a removal recommendation at all.

Authoring rule: every import of the code under test happens INSIDE a test body.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_REPORT = _ROOT / "scripts" / "gate-yield.py"

# `since` is a mapping per source, not one timestamp. Step 5 of this slice caught
# the single-window version: it would have judged the one-day-old denial log over
# another source's window and called a mechanism silent that had not yet had a day
# to speak.
_SINCE = {"denials": "2026-08-02T00:00:00+00:00"}


def _report(args, env=None):
    return subprocess.run([sys.executable, str(_REPORT), *args], capture_output=True,
                          text=True, cwd=str(_ROOT), timeout=180,
                          env=dict(os.environ, **(env or {})))


def _payload(proc):
    assert proc.stdout.strip(), f"no output (exit {proc.returncode})\n{proc.stderr[:600]}"
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"not JSON: {exc}\n{proc.stdout[:400]}") from exc


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
    # Scoped to the GATES since the yield-axes slice split the two axes. A wall
    # reads HOLDING at every window length, so `all(...)` over every mechanism
    # would now be asserting that the split does not exist.
    from scripts.utils.gate_yield import GATES, TOO_EARLY, summarise

    out = summarise(denials=[], since=_SINCE,
                    now="2026-08-03T00:00:00+00:00")
    gates = [m for n, m in out["mechanisms"].items() if n in GATES]
    assert gates, out
    assert all(m["verdict"] == TOO_EARLY for m in gates), out


def test_a_silent_mechanism_beyond_the_budget_is_flagged_with_its_number():
    from scripts.utils.gate_yield import NO_YIELD, summarise

    out = summarise(denials=[], since=_SINCE,
                    now="2026-10-02T00:00:00+00:00")
    silent = [m for m in out["mechanisms"].values() if m["verdict"] == NO_YIELD]
    assert silent, out
    assert all(m["caught"] == 0 for m in silent)
    assert all(isinstance(m["days"], int) and m["days"] >= 31 for m in silent)


def test_a_mechanism_that_has_caught_something_is_not_flagged():
    """Re-pointed at the denial log on 2026-08-07, assertions unchanged.

    Its fixture was a lifecycle refusal row, and the ledger that carried those
    has no producer left. The claim is about the counter, not about the source.
    """
    from scripts.utils.gate_yield import CATCHING, summarise

    out = summarise(
        denials=[{"mechanism": "check_rate_limit", "reason": "why",
                  "ts": "2026-08-02T00:00:00+00:00"}],
        since=_SINCE, now="2026-10-02T00:00:00+00:00")
    assert out["mechanisms"]["check_rate_limit"]["verdict"] == CATCHING
    assert out["mechanisms"]["check_rate_limit"]["caught"] == 1
    assert (out["mechanisms"]["check_rate_limit"]["last_catch"]
            == "2026-08-02T00:00:00+00:00")


def test_the_report_always_states_the_window_it_judged_over():
    """A verdict without its window is the confident table this slice refuses."""
    from scripts.utils.gate_yield import render, summarise

    out = summarise(denials=[], since=_SINCE,
                    now="2026-08-03T00:00:00+00:00")
    assert out["windows"] == {"denials": 1}
    rendered = render(out, now="2026-08-03T00:00:00+00:00")
    # The sentence, not the digit. `assert "1" in rendered` was the whole check
    # until 2026-08-27, and the character "1" appears seventeen times in this
    # report for reasons that have nothing to do with the window - dates, other
    # counts, the budget line. It could not have failed.
    assert "observed over 1 day(s) of denial log" in rendered, rendered


def test_the_budget_is_the_operators_one_month():
    from scripts.utils.gate_yield import BUDGET_DAYS

    assert BUDGET_DAYS == 31


def test_the_report_counts_the_denial_log():
    """A1 and A2 are two halves of one question, not two reports."""
    from scripts.utils.gate_yield import summarise

    out = summarise(denials=[{"mechanism": "check_tool_budget",
                              "ts": "2026-08-01T12:00:00+00:00"}],
                    since=_SINCE,
                    now="2026-10-02T00:00:00+00:00")
    assert out["mechanisms"]["check_tool_budget"]["caught"] == 1


def test_the_report_writes_nothing(tmp_path):
    from scripts.utils.gate_yield import render, summarise

    (tmp_path / "sentinel").write_text("x", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert before == ["sentinel"], "the comparison must have something to compare"

    text = render(summarise(denials=[],
                            since=_SINCE,
                            now="2026-08-03T00:00:00+00:00"),
                  now="2026-08-03T00:00:00+00:00")
    assert text.strip(), "a report that renders nothing writes nothing trivially"
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_missing_source_degrades_to_a_named_gap_not_a_zero(monkeypatch, tmp_path):
    """An absent log and an empty one are different facts, and reading the first
    as the second is how a mechanism gets called dead for a missing file.

    Driven by moving the denial log rather than by passing a root that has none:
    the log is workspace-global and was never resolved from the caller's root,
    and the lifecycle ledger that WAS root-relative is deleted.
    """
    from scripts.utils import denial_log
    from scripts.utils.gate_yield import read_sources

    monkeypatch.setattr(denial_log, "denial_log_path",
                        lambda: tmp_path / "absent" / "denials.jsonl")
    out = read_sources(tmp_path)
    assert out["denials"] == []
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

    return summarise(denials=[], since=_SINCE,
                     now="2026-10-02T00:00:00+00:00")


# ---------------------------------------------------------------------------
# Regressions found by /scrutinize, 2026-08-02, all four against live data
# ---------------------------------------------------------------------------

def test_a_denial_stamp_is_read_as_a_time_and_not_dropped():
    """The two logs never stamped alike, and only one was ever read.

    `log_denial` writes `time.time()`; the retired lifecycle ledger wrote
    `datetime.isoformat()`. A parser that knew only the second answered None for
    every denial row, and None is SILENT here: it reads out as a 0-day window
    and a blank last-catch rather than as an error. Measured against the live
    log, all nine A1 guards reported "0 catch(es) in 0 day(s)" and the one guard
    that HAD caught something reported it with no date. A window pinned at zero
    can never reach the budget, so NO YIELD was unreachable for half the
    mechanisms by construction -- the report could not deliver the one verdict
    it exists to deliver.
    """
    from scripts.utils.gate_yield import CATCHING, NO_YIELD, summarise

    epoch = 1782000000.0  # an ordinary time.time() value, as A1 writes it
    out = summarise(
        denials=[{"mechanism": "check_cwd_anchor", "ts": epoch, "reason": "why"}],
        since={"denials": epoch},
        now="2026-08-02T00:00:00+00:00")

    caught = out["mechanisms"]["check_cwd_anchor"]
    assert caught["days"] > 0, "an epoch stamp collapsed the window to zero"
    assert caught["last_catch"], "a catch was counted with no date"
    assert caught["verdict"] == CATCHING
    # The consequence, asserted directly rather than inferred from the window:
    # a guard silent past the budget must be able to REACH the flagged verdict.
    # Re-pointed at `check_tool_budget` by the yield-axes slice: `content-guard`
    # is a WALL, so it now reads HOLDING by design and can never be flagged. The
    # claim here is about the window arithmetic reaching a verdict, not about
    # which mechanism, and it needs a mechanism the verdict still applies to.
    assert out["mechanisms"]["check_tool_budget"]["verdict"] == NO_YIELD, (
        "a silent guard past a full budget window could not be flagged")


def test_the_last_catch_is_a_date_the_operator_can_read():
    """The log stamps a float; the report answers in a date a human reads."""
    from scripts.utils.gate_yield import summarise

    out = summarise(
        denials=[{"mechanism": "check_cwd_anchor", "ts": 1782000000.0,
                  "reason": "w"}],
        since={"denials": 1782000000.0},
        now="2026-08-02T00:00:00+00:00")

    assert out["mechanisms"]["check_cwd_anchor"]["last_catch"].startswith("2026-")


def test_every_pretooluse_guard_is_declared_so_a_silent_one_stays_visible():
    """The declared list exists so a guard that has NEVER fired still appears.

    It omitted the whole PreToolUse family -- seven guards, and the seven least
    likely to fire, since each waits on a model mistake. They were therefore
    invisible rather than TOO EARLY, which is the exact confusion the list's own
    comment says it exists to end.

    Read from the dispatcher's registry rather than retyped, so the next check
    added there fails this test instead of silently vanishing from the report.
    """
    from scripts.utils.gate_yield import DENIAL_MECHANISMS

    dispatch = _ROOT / ".claude" / "hooks" / "_dispatch.py"
    tree = ast.parse(dispatch.read_text(encoding="utf-8"))
    registry = [n for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "CHECKS" for t in n.targets)]
    assert registry, "no CHECKS registry found in the dispatcher"
    names = [e.id for e in registry[0].value.elts if isinstance(e, ast.Name)]
    assert len(names) >= 7, names

    # The dispatcher's deny path calls `_record_denial(check.__name__, ...)`,
    # so the mechanism name IS the function name.
    missing = sorted(set(names) - set(DENIAL_MECHANISMS))
    assert not missing, (
        f"these guards can never show as silent, only as absent: {missing}")
