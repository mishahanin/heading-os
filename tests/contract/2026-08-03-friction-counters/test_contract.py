"""The frozen contract for the friction-counters slice.

The evidence page an operator approves from says `25 of 25` and `LOCK HELD`. It
does not say that the contract was rewritten five times to reach that green, or
that the lock was released and retaken five times on the way. Both facts are
already recorded, line by line, in `.canopus/history.jsonl`; nothing reads them.

Measured over the whole ledger on 2026-08-03 (254 records, 19 shipped slices):

    slice                              windows   retakes
    2026-07-26-canopus-repository-bin        3        11
    production-shape                         5         5
    timer-timezone                           5         6
    gate-yield                               2         2
    egress-proof                             2         2
    every other shipped slice                0         0

23 windows and 37 retakes in total, and almost all of it in five slices. So the
number is not decoration: it separates a slice that went green first time from a
slice that went green on the sixth attempt, and those are different claims about
the same page.

WHAT THIS DOES NOT DO, pinned by test rather than left to prose:

- It does not judge. A high count is not a failure; `production-shape` earned its
  five windows. The page reports, and the operator reads.
- It counts only what the ledger records STRUCTURALLY: a window is
  `release` with `kind == "window"`, a retake is `anchor_replaced`, a refusal is
  `refuse_approve` / `refuse_release`, a failed verify is `verify_fail`. Waivers
  are NOT counted, because `--contract-satisfied` lands in a free-text `reason`
  and a counter built on a substring is a counter that lies quietly. The waiver
  state of the CURRENT freeze already reaches the page from the committed
  artifact, which is the honest source.
- The count is a FLOOR, never a total. `.canopus/` is gitignored and one `rm -rf`
  takes the ledger with it, so a zero can mean "no friction" or "no ledger". The
  page must say which it can distinguish.
- Counts are scoped by LABEL. Two slices sharing a label merge into one row, and
  the page may not pretend otherwise.

Every test imports the code under test INSIDE its body.
"""

import json
from pathlib import Path


def _ledger(tmp_path: Path, rows: list) -> Path:
    """Write a scratch ledger and return the root that holds it."""
    root = tmp_path / "ws"
    (root / ".canopus").mkdir(parents=True, exist_ok=True)
    path = root / ".canopus" / "history.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")
    return root


def _row(event, label="s", kind="", reason="", ts="2026-08-03T00:00:00+00:00"):
    return {"event": event, "label": label, "kind": kind, "reason": reason,
            "ts": ts, "root": "d" * 64}


# ============================================================
# SC-1 -- the four structural counts
# ============================================================

def test_a_window_is_a_release_of_kind_window_and_nothing_else():
    """SC-1. `release` carries both meanings in one event name; only `kind`
    separates the mid-slice window from the end-of-slice ship. Counting `release`
    would report every shipped slice as having opened a window."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("release", kind="window"),
        _row("release", kind="ship"),
        _row("release", kind="window"),
    ], label="s")
    assert counts.windows == 2
    assert counts.ships == 1


def test_a_retake_is_an_anchor_replaced_entry():
    """SC-1b. `approve --replace` writes BOTH an `approve` and an
    `anchor_replaced`, so counting `approve` double-counts a retake as an
    approval and hides that it was a second one."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("approve"), _row("approve"), _row("anchor_replaced"),
    ], label="s")
    assert counts.retakes == 1
    assert counts.approvals == 2


def test_refusals_and_failed_verifies_are_counted_apart():
    """SC-1c. A refusal is the gate declining an action; a failed verify is the
    contract having moved. Merging them would let a slice that was refused twice
    read the same as one whose tree drifted twice."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("refuse_approve"), _row("refuse_release"), _row("verify_fail"),
    ], label="s")
    assert counts.refusals == 2
    assert counts.verify_failures == 1


def test_entries_for_other_labels_are_not_counted():
    """SC-1d. The page describes ONE slice. A ledger holding 254 records across
    35 labels must not report the fleet's friction as this slice's."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([
        _row("release", label="mine", kind="window"),
        _row("release", label="other", kind="window"),
        _row("anchor_replaced", label="other"),
    ], label="mine")
    assert counts.windows == 1
    assert counts.retakes == 0


def test_an_unknown_event_is_ignored_rather_than_miscounted():
    """SC-1e. The ledger's vocabulary grows. An event this counter has never
    seen must not land in the nearest bucket."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([_row("something_new"), _row("release", kind="")],
                            label="s")
    assert counts.windows == 0 and counts.ships == 0
    assert counts.retakes == 0 and counts.refusals == 0


# ============================================================
# SC-2 -- a zero must never read as a clean bill on its own
# ============================================================

def test_a_slice_with_no_freeze_entry_is_reported_as_unrecorded():
    """SC-2. The distinction the whole section turns on. A held freeze always
    wrote a `freeze` line, so no `freeze` line means the ledger lost it -- and a
    row of zeroes then describes a missing ledger, not a frictionless slice.
    """
    from scripts.utils.canopus_friction import count_friction

    absent = count_friction([], label="s")
    assert absent.recorded is False

    present = count_friction([_row("freeze")], label="s")
    assert present.recorded is True
    assert present.windows == 0


def test_the_rendered_section_says_which_zero_it_is():
    """SC-2b. Same claim at the render layer, because the operator reads the
    render and never calls the function."""
    from scripts.utils.canopus_friction import count_friction, render_friction

    unrecorded = render_friction(count_friction([], label="s"))
    recorded = render_friction(count_friction([_row("freeze")], label="s"))

    assert "no ledger entries" in unrecorded.lower()
    assert "no ledger entries" not in recorded.lower()

    # Both branches open with the heading. A mutation dropped it and stayed
    # green: the numbers reached the page under no title, which on a report
    # whose every other section is titled reads as part of the section above.
    from scripts.utils.canopus_friction import FRICTION_HEADING
    for text in (unrecorded, recorded):
        assert text.splitlines()[0].strip().endswith(FRICTION_HEADING), (
            f"the section does not open with {FRICTION_HEADING!r}: "
            f"{text.splitlines()[0]!r}"
        )


# ============================================================
# SC-3 -- the page states the boundary, every time
# ============================================================

def test_the_render_always_states_that_the_count_is_a_floor():
    """SC-3. `.canopus/` is gitignored and deletable, so every count here is a
    floor. A page that prints `windows 0` without that sentence is making a
    stronger claim than the data supports -- which is the defect this whole
    slice exists to stop making elsewhere."""
    from scripts.utils.canopus_friction import count_friction, render_friction

    for rows in ([], [_row("freeze")], [_row("freeze"), _row("release", kind="window")]):
        text = render_friction(count_friction(rows, label="s")).lower()
        assert "floor" in text or "at least" in text, (
            "the render omits the floor caveat for rows=%r" % rows)


def test_the_render_never_grades_the_slice():
    """SC-3b. Report, do not judge. `production-shape` earned its five windows by
    finding five real problems; a page that calls that "poor" teaches the builder
    to avoid windows, which is exactly backwards."""
    from scripts.utils.canopus_friction import count_friction, render_friction

    text = render_friction(count_friction(
        [_row("freeze")] + [_row("release", kind="window")] * 9, label="s")).lower()
    for verdict in ("poor", "bad", "excessive", "too many", "warning", "concerning"):
        assert verdict not in text, f"the render grades the slice: {verdict!r}"


# ============================================================
# SC-4 -- wired into the page an operator actually approves from
# ============================================================

def test_scripts_canopus_actually_CALLS_the_renderer():
    """SC-4b. A module whose own tests pass while the one wiring line is absent
    is the failure this criterion exists for.

    Asserted on the AST, not on a substring. The first version of this test read
    `"render_friction" in text`, and a mutation that deleted the CALL while
    leaving the `import` line survived it: the section vanished from the page and
    the contract stayed green. That is the same substring trap
    `scripts/utils/mutation_probe.py` documents in its own docstring, reproduced
    here by the harness that module exists to be.

    An import is not a use. This walks for an `ast.Call` whose target resolves to
    the name, so only a real call site satisfies it.
    """
    import ast

    from scripts.utils.canopus_friction import FRICTION_HEADING

    assert FRICTION_HEADING, "the page needs a stable heading to render under"

    src = Path(__file__).resolve().parents[3] / "scripts" / "canopus.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)

    for name in ("render_friction", "count_friction"):
        assert name in called, (
            f"scripts/canopus.py never CALLS {name}, so the friction section "
            f"cannot reach the page regardless of how well the module behaves "
            f"(an import alone is not a use)"
        )

    # And CALLED IS NOT PRINTED. A second mutation kept the call and dropped the
    # print: the section left the page, every test stayed green. So the call must
    # sit inside a `print`, which makes "wired" and "reaches the page" one claim.
    printed = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        for arg in node.args:
            for inner in ast.walk(arg):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id == "render_friction"):
                    printed = True
    assert printed, (
        "render_friction is called but its value never reaches a print; the "
        "section is computed and discarded"
    )


# ============================================================
# SC-5 -- the reader it depends on is not weakened
# ============================================================

def test_a_corrupt_ledger_line_costs_only_that_line(tmp_path):
    """SC-5. `read_ledger` already skips damaged lines rather than raising,
    because the ledger is evidence and nine readable entries beat a traceback.
    The counter must inherit that, not undo it."""
    from scripts.utils.canopus_freeze import read_ledger
    from scripts.utils.canopus_friction import count_friction

    root = tmp_path / "ws"
    (root / ".canopus").mkdir(parents=True)
    (root / ".canopus" / "history.jsonl").write_text(
        json.dumps(_row("release", kind="window")) + "\n"
        + "{ not json\n"
        + json.dumps(_row("anchor_replaced")) + "\n",
        encoding="utf-8")

    counts = count_friction(read_ledger(root), label="s")
    assert counts.windows == 1
    assert counts.retakes == 1


def test_the_counter_never_raises_on_a_malformed_entry():
    """SC-5b. A dict missing `event`, `kind` or `label` is readable JSON and so
    survives `read_ledger`. It must not take the page down: this renders on the
    approval path, where a traceback costs the operator the whole page."""
    from scripts.utils.canopus_friction import count_friction

    counts = count_friction([{}, {"event": None}, {"label": "s"},
                             {"event": "release", "label": "s"}], label="s")
    assert counts.windows == 0
    assert counts.recorded is False
