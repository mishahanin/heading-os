#!/usr/bin/env python3
"""Canopus: the three thin commands the standard's steps 4 and 7 need.

    python scripts/canopus.py probe tests/contract/     # step 3, is it vacuous
    python scripts/canopus.py note <slug> --value ...   # step 7, the record
    python scripts/canopus.py check --range A..B        # the four clauses

`note` and `check` hold no logic of their own. `note` validates and writes
through `scripts/utils/canopus_note.write_note`, and `check` calls
`scripts/canopus_check.main` — the same entry point the `sovereignty guards` CI
job runs — so there is exactly one implementation of each and this file is the
one place an operator has to remember. They exist because the skill promises
`/canopus note` and `/canopus check`, and a document claiming a capability the
tool does not have is the defect this whole standard was built to remove.

`probe` is the command that does the work here. It runs the test files that are
meant to define done for a slice, at a moment when the implementation they
describe has not been written, and reports which of them assert something about
it.

Three questions are answered, and each of them is a way a contract can be born
too weak to be worth committing:

  * Which tests are ALREADY GREEN. A test that passes before a line of the
    implementation exists is not measuring that implementation.
  * Which tests are VACUOUS. The contract is run twice more with its own
    imports null-stubbed, each stub carrying a different set of values; a test
    whose outcome does not move when the stubbed value moves cannot be reading
    it.
  * Which tests a WRONG implementation satisfies. Several deliberately wrong
    stand-ins are put in front of the contract, and every red test one of them
    turns green is a test the contract cannot use to tell right from wrong.

It writes nothing, anywhere. The exit code is 1 when the contract would be
refused on any of those grounds and 0 otherwise, so it is usable as a gate by a
caller that wants one, and the reasons are printed either way.

What replaced the rest. Until 2026-08-07 this file also held a freeze
lifecycle - approve, freeze, verify, status, release, repin, pack, where - that
locked the contract's bytes and re-checked them from a manifest under
`.canopus/`. Git already records what that machinery recorded: the approval is
a commit carrying the plan and the red contract, and `git diff` against its sha
answers whether the contract moved. `scripts/canopus_check.py` asks that
question directly, from the repository, and CI runs it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))
from scripts.utils.canopus_contract import (  # noqa: E402
    RED_OUTCOMES,
    ContractError,
    contract_files,
    parse_failure_modes,
    parse_junit,
    pass_candidate_refusal,
    refusal_reasons,
    run_null_stub,
    run_pass_candidates,
    run_pytest_report,
    vacuity_refusal,
)
# The candidate NAMES only, so the summary line names every candidate that was
# run rather than only the ones that took something. Importing the plugin module
# registers no pytest hook: hooks come from `-p`, not from import.
from scripts.utils.canopus_nullstub import CANDIDATES  # noqa: E402
from scripts.canopus_check import main as check_main  # noqa: E402
from scripts.utils.canopus_note import (  # noqa: E402
    BODY_FIELD, NoteError, OPTIONAL_FIELDS, REQUIRED_FIELDS, read_note, write_note,
)
from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW  # noqa: E402

# Every note field except `slug`, which is the subcommand's positional. Derived
# from the schema rather than restated: a field added there — the retirement pair
# `retired_sha`/`promoted_to` above all — gets its flag here without anyone
# remembering to add one, and a flag can never name a field the schema refuses.
_NOTE_FLAGS = tuple(name for name in REQUIRED_FIELDS + OPTIONAL_FIELDS + (BODY_FIELD,)
                    if name != "slug")


def _under_root(raw: str, root: Path) -> Path:
    """Resolve a CLI path argument against --root, not against the shell's cwd.

    --root exists so the tree being probed need not be the cwd. Resolving
    relative arguments against the cwd would make `--root ../other probe
    tests/x.py` fail for a legal invocation.
    """
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate


def cmd_probe(args) -> int:
    """Read a contract's shape before its implementation exists. Writes nothing.

    A wholly vacuous contract is easy to notice. A partly vacuous one is not,
    and that is what this table is for: the operator reading it sees which
    contract tests are ALREADY GREEN before a line of implementation exists, and
    which are red for no better reason than the code being absent. Those are the
    ones to question.

    This command runs pytest over the contract THREE times: once for real, and
    then twice more with the contract's OWN imports stubbed, each stub carrying a
    DIFFERENT set of values. Two stub runs rather than one is what buys the
    vacuity proof: a test whose outcome does not move when the stubbed value
    moves cannot be reading that value. Nothing can be shared across the
    processes. The contract is a handful of files, so the cost is seconds, but an
    operator wondering why this takes longer than it used to should find the
    answer written down.

    The exit code is 1 when any of those readings would refuse the contract, so
    a caller that wants a gate has one; every reason is printed either way.
    """
    root = Path(args.root).resolve() if args.root else ENGINE_ROOT
    paths = [_under_root(p, root) for p in args.paths]
    expected = contract_files(paths, root)
    if not expected:
        print("canopus: no test modules found under those paths", file=sys.stderr)
        return 1
    xml_text = run_pytest_report(paths, root)
    counts, outcomes = parse_junit(xml_text)
    # BOUND before the call, on every path, because the table twenty lines down
    # reads `(case_rel, name) in vacuous` and `vacuity_refusal` reads it again at
    # the end. An except branch that only printed left both reading an unbound
    # name, so `probe` died with an UnboundLocalError on exactly the failure the
    # branch was added to report.
    vacuous: set[tuple[str, str]] = set()
    probe_failed = ""
    try:
        # The real run's triples, so the probe's lost-test guard and this
        # command's verdict weigh ONE population.
        vacuous = run_null_stub(paths, root, expected_population=outcomes)
    except ContractError as exc:
        probe_failed = f"the contract's vacuity could not be measured: {exc}"
    # BOUND before the call for the reason the line above carries: the summary
    # twenty lines down reads this on every path, and an except branch that only
    # printed left it unbound.
    taken: dict[str, set[tuple[str, str]]] = {}
    candidates_failed = ""
    try:
        taken = run_pass_candidates(paths, root, expected_population=outcomes)
    except ContractError as exc:
        candidates_failed = (
            f"the contract was not measured against wrong implementations: {exc}"
        )
    modes = parse_failure_modes(xml_text)
    for rel in expected:
        print(f"{BOLD}{rel}{RESET}  {counts.get(rel, 0)} collected")
        for case_rel, name, outcome in outcomes:
            if case_rel != rel:
                continue
            # One line per test, widened rather than a second table beneath the
            # first. A second loop reprints every case under another label, and
            # the operator has to work out that the two rows are one test.
            #
            # The red filter is the same one `vacuity_refusal` applies, and for
            # the same reason: `vacuous` holds everything that passed under the
            # stub, which includes every test that passed for REAL. Labelling
            # those "asserts nothing" is a false claim about a test that may
            # assert a great deal against code that already exists, and it would
            # also hide the "already green" reading the operator is told to
            # question. Measured: a contract of `assert True` plus a real green
            # case printed both as vacuous.
            # `outcome in RED_OUTCOMES` is the same membership test
            # `vacuity_refusal` applies, and the two must agree or the table
            # describes a contract the refusal is not judging. The near-miss
            # `outcome != "passed"` admitted `skipped`, and a skipped test can
            # be in `vacuous`: `pytest.importorskip` on the absent module skips
            # for real and PASSES once the stub supplies it. Measured: that test
            # printed `vacuous  <name>  asserts nothing` and the `continue`
            # below swallowed the only line that would have said it never ran.
            # Nothing refuses a skipped contract test, so the one surface the
            # operator is told to read was also the one hiding it.
            if outcome in RED_OUTCOMES and (case_rel, name) in vacuous:
                print(f"  {YELLOW}{'vacuous':8}{RESET} {name}  asserts nothing")
                continue
            colour = {"passed": GREEN, "skipped": YELLOW}.get(outcome, RED)
            # No failure-mode label on a skipped test: it has no failure child in
            # the report, so the heuristic would default it to `other` and invent
            # a way it failed. It says what a skip actually costs instead.
            mode = ("" if outcome in ("passed", "skipped")
                    else f"  {modes.get((case_rel, name), 'other')}")
            if probe_failed and outcome in RED_OUTCOMES:
                # Said on the ROW, not only in the refusal line at the bottom.
                # With the probe failed `vacuous` is empty, so every red row
                # printed its ordinary failure line and nothing on it
                # distinguished "measured, and this test asserts something" from
                # "never measured". The operator who reads to the end is told by
                # the refusal and the exit code; the one who skims the table is
                # the one this exists for, and a table that reads clean while
                # nothing was measured is the reading this slice removes.
                #
                # Red rows only, matching `vacuity_refusal`: a green test is
                # outside the verdict either way, so stamping it UNKNOWN would
                # invent a measurement that was never owed.
                note = "  vacuity UNKNOWN, it was never measured"
            elif outcome == "skipped":
                note = "  did not run, so it proves nothing"
            else:
                note = ""
            print(f"  {colour}{outcome:8}{RESET} {name}{mode}{note}")
    # One line per candidate, printed whatever the verdict, because a table that
    # named only the candidate that WON would leave the operator unable to tell a
    # contract three wrong implementations failed to satisfy from a run where
    # only one candidate was ever put in front of it. The count is per candidate
    # against the red set, which is the population the refusal weighs.
    red_set = {(rel, name) for rel, name, outcome in outcomes
               if outcome in RED_OUTCOMES}
    if candidates_failed:
        print(f"{YELLOW}candidates{RESET}  not measured")
    else:
        summary = "  ".join(
            f"{name} {len(taken.get(name, set()) & red_set)}"
            for name in CANDIDATES
        )
        print(f"{BOLD}candidates{RESET}  {summary}   "
              f"(of {len(red_set)} red; each is an implementation that EXISTS "
              f"and is wrong)")
    reasons = refusal_reasons(counts, outcomes, expected)
    if candidates_failed:
        reasons.append(candidates_failed)
    else:
        reasons.extend(pass_candidate_refusal(outcomes, taken))
    if probe_failed:
        reasons.append(probe_failed)
    else:
        # The `else` is load-bearing rather than tidy. `vacuous` is empty when the
        # probe did not run, so `vacuity_refusal` would weigh a real `cases` set
        # against an empty one, find no subset, and return [] — the silent
        # "measured, and nothing was vacuous" reading this refusal exists to
        # remove. It answers [] either way today, so the guard is what stops a
        # later change to that function from reading an empty set as a verdict.
        reasons.extend(vacuity_refusal(outcomes, vacuous))
    for reason in reasons:
        print(f"{YELLOW}would be refused:{RESET} {reason}")
    return 1 if reasons else 0


def cmd_note(args) -> int:
    """Write the slice's committed record, or print one back with --show.

    Every field is optional to argparse and required by the SCHEMA. That is the
    deliberate direction: `write_note` already refuses a note missing a required
    field, naming every one of them in a single sentence, and duplicating the
    required set into `add_argument(required=True)` would give the operator two
    different refusals for one mistake — and would drift the moment the schema
    gains a field. The one exception is the slug, which is the positional,
    because `--show` needs to name a note without supplying its contents.
    """
    root = Path(args.root).resolve() if args.root else ENGINE_ROOT
    try:
        if args.show:
            for name, value in read_note(root, args.slug).items():
                print(f"{name}: {value}")
            return 0
        fields = {name: getattr(args, name) for name in _NOTE_FLAGS
                  if getattr(args, name) is not None}
        fields["slug"] = args.slug
        print(write_note(root, args.slug, fields).relative_to(root))
    except (NoteError, OSError) as exc:
        # OSError alongside NoteError because the write can fail on the
        # filesystem (an unwritable records/ directory, a vanished root) and a
        # traceback over that reads as a bug in the tool rather than as a
        # refusal the operator can act on.
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_check(args) -> int:
    """Run the four clauses, from `scripts/canopus_check.py`, nothing reimplemented.

    That module IS the check: the `sovereignty guards` CI job invokes it
    directly, so a second copy of the clause logic behind this subcommand would
    be a second answer to the same question, and the two would diverge on the
    first fix that landed in only one of them. The flags are passed through, so
    the local reading and the CI reading are the same reading.
    """
    root = Path(args.root).resolve() if args.root else ENGINE_ROOT
    argv = ["--root", str(root)]
    if args.commit_range:
        argv += ["--range", args.commit_range]
    if args.json:
        argv.append("--json")
    return check_main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canopus",
        description="Write a slice's record, hold every record to the four "
                    "clauses, and read whether a contract asserts anything.",
    )
    parser.add_argument("--root", default=None,
                        help="working tree root (default: this script's own "
                             "repository root, NOT the shell's cwd)")
    # NOT required. argparse's own required-subparser error is a one-line usage
    # string; `main` prints the whole help instead, which names all three
    # subcommands and what each is for, and still exits non-zero.
    sub = parser.add_subparsers(dest="command")

    note = sub.add_parser(
        "note",
        help="write the slice's committed record under records/slices/",
    )
    note.add_argument("slug", help="the slice's slug: records/slices/<slug>.md")
    note.add_argument("--show", action="store_true",
                      help="print the named note's fields instead of writing one")
    for name in _NOTE_FLAGS:
        note.add_argument(
            f"--{name.replace('_', '-')}",
            help=f"the note's {name} field"
                 f"{' (required by the schema)' if name in REQUIRED_FIELDS else ''}",
        )
    note.set_defaults(func=cmd_note)

    check = sub.add_parser(
        "check",
        help="run the four clauses over every committed slice note",
    )
    check.add_argument("--range", dest="commit_range", metavar="A..B",
                       help="run the two expensive clauses only for notes whose "
                            "approval_sha or retired_sha falls inside this range")
    check.add_argument("--json", action="store_true",
                       help="one JSON row per clause on stdout, nothing else")
    check.set_defaults(func=cmd_check)

    probe = sub.add_parser(
        "probe",
        help="run a contract set before the code exists and report which of "
             "its tests assert something; writes nothing",
    )
    probe.add_argument("paths", nargs="+", help="contract files or directories")
    probe.set_defaults(func=cmd_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        # A bare `canopus.py` names no subcommand, so there is nothing to run
        # and nothing to report. Help on stderr and a non-zero exit, rather than
        # an AttributeError on a missing `func`.
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args)
    except ContractError as exc:
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # An unreadable member (permissions, a vanished mount) fails the command,
        # it does not traceback: the exit code already fails closed, and a raw
        # stack trace over a filesystem fault reads like a bug in the tool.
        print(f"canopus: the contract could not be read, so it cannot be "
              f"probed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
