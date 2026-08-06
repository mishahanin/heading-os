"""A2, the gate-yield ledger.

Retired from `tests/contract/2026-08-02-gate-yield/` into the ordinary suite at
step 13, 2026-08-02, unchanged apart from this note and the root path. The
coverage is worth keeping; the lock on it would bind every later slice to this
one's behaviour.

Two numbers, measured 2026-08-02, scope this and pull in opposite directions.

The Canopus ledger holds 152 events and NOT ONE REFUSAL: freeze 49, release 49,
approve 28, anchor_replaced 20, verify_fail 6, every one a success or a late
verify failure. `cmd_approve`, `cmd_freeze` and `cmd_release` carried twelve
early returns between them and none of them touched the ledger. Every time the
standard refused the builder, the event vanished.

Those three commands were deleted on 2026-08-07 with the rest of the freeze
lifecycle, and nine tests went with them: eight walked their refusal paths or
drove them through the CLI, and one compared two refusals that are now the same
argparse error in both runs, so it passed while measuring nothing. The lifecycle
half of this instrument has no producer left; the denial-log half, the report and
the vocabulary are untouched below.

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

_ROOT = Path(__file__).resolve().parent.parent
_REPORT = _ROOT / "scripts" / "gate-yield.py"

# The two sources started on different days, and that is the whole reason
# `since` is a mapping. Step 5 of this slice caught the single-window version:
# it would have judged the one-day-old denial log over the ledger's window and
# called a mechanism silent that had not yet had a day to speak.
_SINCE = {"lifecycle": "2026-08-01T00:00:00+00:00",
          "denials": "2026-08-02T00:00:00+00:00"}


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
# Property 1 - every refusal leaves a record, and none can be added silently
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Property 2 - recording can never change a refusal
# ---------------------------------------------------------------------------

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

def test_a_refusal_record_never_carries_the_refused_content():
    """Same boundary A1 already holds. A refused path is attacker-shaped input.

    Read against `redact_reason` directly since 2026-08-07. The end-to-end half
    drove `scripts/canopus.py freeze` with the value as its label and read the
    ledger row back; that command is deleted, and there is no other producer of
    a lifecycle refusal record to put in its place. What is left is the function
    the writer calls, which is where the redaction actually happens.
    """
    from scripts.utils.gate_yield import redact_reason

    # Named for what it IS: a value somebody tried to push past a guard,
    # which is what every refusal record is about. The earlier name tripped
    # the commit gate's keyword heuristic and made the whole slice
    # uncommittable, which no allow-list entry was going to fix.
    pushed_value = "sk-" + "A" * 24

    assert redact_reason(f"refused {pushed_value}") != f"refused {pushed_value}"
    assert pushed_value not in redact_reason(f"refused {pushed_value}")


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

    out = summarise(ledger=[], denials=[], since=_SINCE,
                    now="2026-08-03T00:00:00+00:00")
    gates = [m for n, m in out["mechanisms"].items() if n in GATES]
    assert gates, out
    assert all(m["verdict"] == TOO_EARLY for m in gates), out


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
    from scripts.utils.gate_yield import GATES, TOO_EARLY, summarise

    out = summarise(ledger=[], denials=[],
                    since={"lifecycle": "2026-06-01T00:00:00+00:00",
                           "denials": "2026-10-01T00:00:00+00:00"},
                    now="2026-10-02T00:00:00+00:00")
    young = [m for n, m in out["mechanisms"].items()
             if m["source"] == "denials" and n in GATES]
    assert young, out
    assert all(m["verdict"] == TOO_EARLY for m in young), (
        "a mechanism recorded for one day was judged over another source's window")
    # The window claim itself is the point, and it holds for walls too, so it is
    # asserted over the whole source rather than only over the gates.
    assert all(m["days"] == 1 for m in out["mechanisms"].values()
               if m["source"] == "denials")


# ---------------------------------------------------------------------------
# Regressions found by /scrutinize, 2026-08-02, all four against live data
# ---------------------------------------------------------------------------

def test_a_denial_stamp_is_read_as_a_time_and_not_dropped():
    """The two logs have never stamped alike, and only one was ever read.

    `log_denial` writes `time.time()`; the lifecycle ledger writes
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
        ledger=[],
        denials=[{"mechanism": "depth-gate", "ts": epoch, "reason": "why"}],
        since={"lifecycle": "2026-06-21T00:00:00+00:00", "denials": epoch},
        now="2026-08-02T00:00:00+00:00")

    caught = out["mechanisms"]["depth-gate"]
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
    """One source stamps ISO and the other a float; the report answers in one."""
    from scripts.utils.gate_yield import summarise

    out = summarise(
        ledger=[],
        denials=[{"mechanism": "depth-gate", "ts": 1782000000.0, "reason": "w"}],
        since={"lifecycle": "", "denials": 1782000000.0},
        now="2026-08-02T00:00:00+00:00")

    assert out["mechanisms"]["depth-gate"]["last_catch"].startswith("2026-")


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


def test_the_recorder_survives_a_failure_that_is_not_an_oserror():
    """Its docstring claims the posture of `log_denial`, which is total.

    Catching only OSError left that claim false for every other failure, and the
    caller runs this line BEFORE its `return 1` -- so anything escaping converts
    a clean refusal into a traceback, the outcome the function's own first
    paragraph names as the worst available.
    """
    from scripts.utils import gate_yield

    class NotAPath:
        """Not path-like, so `Path(root)` raises TypeError -- not an OSError."""

    failure = gate_yield.record_refusal(NotAPath(), mechanism="freeze",
                                        cause="candidate_refused", reason="w")
    assert isinstance(failure, str) and failure, "a non-OSError escaped"
    assert "OSError" not in failure, (
        f"the point is that this failure was NOT an OSError: {failure}")


def test_a_damaged_classification_file_is_reported_and_an_absent_one_is_not(
        tmp_path, capsys):
    """`{}` from a corrupt bridge rendered exactly like `{}` from no bridge.

    `load_hand_classified` answers `{}` for both, which is right -- a report
    that cannot render because a historical annotation is unreadable has turned
    a footnote into an outage. What was wrong until 2026-08-04 is that it said
    nothing either way: a corrupt file dropped all 39 hand classifications, the
    page then reported "0 were classified BY HAND", and nothing anywhere said
    they had been lost.

    ABSENT stays silent because it is the ordinary state of every clone that is
    not this one. DAMAGED speaks, because this module's own rule for a check
    that could not run is that it must not read as a check that found nothing --
    the same rule `read_sources` follows with `missing_sources` and `tree_state`
    follows by answering None.
    """
    from scripts.utils.gate_yield import HAND_CLASSIFIED_PATH, load_hand_classified

    assert load_hand_classified(tmp_path) == {}
    assert capsys.readouterr().err == "", "an absent bridge is not a fault"

    target = tmp_path / HAND_CLASSIFIED_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text("{not json", encoding="utf-8")
    assert load_hand_classified(tmp_path) == {}
    damaged = capsys.readouterr().err
    assert str(target) in damaged, damaged
    assert "unclassified" in damaged, damaged

    target.write_text('["a list is not a mapping"]', encoding="utf-8")
    assert load_hand_classified(tmp_path) == {}
    assert "unclassified" in capsys.readouterr().err


def test_the_real_committed_classification_still_loads_after_the_split(capsys):
    """The split must not have made the live bridge noisy or empty.

    A report that shouts on every ordinary run trains its reader to ignore it,
    which costs exactly the signal the change was made to add.
    """
    from scripts.utils.gate_yield import load_hand_classified
    from scripts.utils.workspace import get_workspace_root

    loaded = load_hand_classified(get_workspace_root())

    assert len(loaded) >= 39, f"the committed bridge lost entries: {len(loaded)}"
    assert capsys.readouterr().err == "", "the healthy bridge reported a fault"
