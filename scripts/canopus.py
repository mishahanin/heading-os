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

`probe --after-build <paths>` asks the third of those questions at the other end
of a slice's life. The standard asks "if this code were wrong, would any gate
notice" exactly once, before the code exists, and then never again: a shipped
slice's contract is retired into the ordinary suite and nothing re-asks. Armed
with `--after-build`, the same wrong implementations are put in front of tests
covering code that ALREADY EXISTS, and every test that stays green under all
three is named. It reports and exits 0; it never refuses. A reading nobody has
calibrated must not become a gate.

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
    skip_markers_without_reason,
    tests_that_never_ran,
    tests_the_candidates_never_ran,
    vacuity_refusal,
    verification_gaps,
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


# The whole value of `--after-build` is in these two paragraphs, so they live on
# the PAGE the operator reads rather than in a docstring nobody opens. Both
# halves are load-bearing and neither may be dropped for brevity.
#
# The first half bounds the claim. A test named by this reading is not a bad
# test and the page must never let it be read as one: the claim is exactly that
# the test did not distinguish right from wrong under three specific
# wrongnesses, and a test of a file on disk or of a document's prose is a
# perfectly good test of something these three candidates cannot express.
#
# The second half bounds the reader's expectation. Under replacement MOST tests
# go red, and that is the instrument working. A reader who arrives expecting a
# mostly-green run reads a correct measurement as a broken tool, and the first
# thing they do about it is stop running the tool.
_AFTER_BUILD_MEANING = (
    "What a name below means. That test stayed GREEN while the modules on the "
    "`replaced` line above were replaced by three implementations that are "
    "wrong in three different ways: one that returns nothing from every call, "
    "one that hands back its own first argument, and one that answers with "
    "every string the test file itself wrote. The claim is exactly that and no "
    "more, so read it exactly: the test did not distinguish right from wrong "
    "under those three wrongnesses. It is NOT a bad test. It may be a perfectly "
    "good test of something these candidates cannot express, such as a file on "
    "disk, a document's prose, or a value the subject never returns."
)
# Printed ONLY when the narrowing actually dropped something, because a caveat
# on every page is a caveat nobody reads and would itself be false on a run
# where every module the contract named was stood in for.
#
# It exists because the sentence above used to say "the tree's own code beneath
# it was replaced" unconditionally, and that is false whenever the subject
# reaches the claim set only through a package prefix: the ordinary idiom
# `from scripts.utils import subject` claims the PACKAGE, the sweep rule drops
# it, and the subject is never stood in for. Measured 2026-08-07 on
# `tests/test_update_common.py`: `survived 3 of 6`, exit 0, and the module the
# whole file is about was replaced zero times.
#
# The third reason in it is newer and arrives from the other end: a module the
# claim set names and no import ever reaches is replaced by nothing, and the
# `replaced` line used to name it anyway, because it was filled from the claim
# set. The children now say what they actually replaced.
_AFTER_BUILD_UNREPLACED = (
    "What was NOT replaced. The modules on the `not replaced` line above were "
    "left standing: each either resolves outside the tree being probed, or has "
    "this probe's own plugin under it, or was never imported by the run at all, "
    "so nothing stood in for it. The reason for each is on "
    "stderr above. Nothing below is evidence about them. A test that exercises "
    "only those modules was put in front of no wrong implementation at all, so "
    "its name below records that it was never measured against one, not that "
    "it failed to tell right from wrong. Check the subject you care about "
    "against the `replaced` line before reading any name below as being about "
    "it, and if it is missing, name that module in the contract's own imports "
    "rather than its parent package."
)
# Printed ONLY when something was skipped, for the reason the paragraph above
# is conditional: a caveat on every page is a caveat nobody reads.
#
# It exists because a skipped test is the THIRD thing a test on this page can
# be, and the page could say only two of them. Absent from the taken map, it was
# folded into the "went red under at least one candidate" bucket that the
# paragraph below describes, in words, as the measurement working. Measured at
# HEAD on 2026-08-07: a module whose two tests both carried a module-level skip
# printed `survived 0 of 2`, the green `none` line, and exit 0, over a run in
# which nothing ran at all. That whole-module case now refuses; this paragraph
# is for the ordinary mixed one, where a reading exists and some of the suite
# sat it out.
_AFTER_BUILD_NEVER_RAN = (
    "What the tests on the `never ran` and `sat out` lines below did. Nothing. "
    "A `never ran` test was skipped in the real run, so no wrong implementation "
    "was ever put in front of them at all. A `sat out` test ran for real and "
    "was then skipped under a candidate: the wrong implementation was "
    "installed, but the test never executed against it, so that candidate "
    "returned no verdict and no candidate can be said to have run it. Either "
    "way this reading says nothing whatever about them, in either "
    "direction: they neither survived a wrong implementation nor went red "
    "under one. They are not counted in the total above, because that total is "
    "the population this reading is about. A skipped test in the suite that "
    "guards shipped code is worth its own look, and this page is not the "
    "instrument that gives it one."
)
_AFTER_BUILD_EXPECTATION = (
    "What the tests named on NEITHER list below did. They went red under "
    "replacement, and that is the measurement working rather than a broken "
    "tool: the code "
    "beneath them was wrong on purpose, so failing is the correct answer. A "
    "reader arriving here expecting a mostly-green run is reading the page "
    "backwards. This reading refuses nothing and gates nothing; nobody has "
    "calibrated it yet, so it reports and exits 0."
)


def _after_build(paths, root, expected) -> int:
    """The gap reading over code that already exists. Writes nothing, gates nothing.

    `probe` asks "if this code were wrong, would any gate notice" exactly once,
    at a moment when the code does not exist, and then never again: once a slice
    ships, its contract is retired into the ordinary suite and nothing re-asks.
    This is the surface that asks it afterwards, against whatever tests cover the
    shipped code.

    It arms the replacement switch, which is what makes the three candidates
    reach code that EXISTS at all. Without it they install a PEP 562
    `__getattr__`, which Python consults only for a name a module lacks, so
    against shipped code they touch nothing and the page reports a clean run over
    a suite that was never measured.

    It does NOT run the null-stub vacuity probe. That probe asks whether a test
    passes while the code is ABSENT, and against shipped code nothing is absent,
    so its two runs would agree for a reason that has nothing to do with the
    tests and the word `vacuous` would appear beside tests it never judged. Two
    fewer pytest sessions is the incidental benefit, not the reason.

    The exit is 0 whenever a reading was produced, and 1 only when one could not
    be. That is not a gate on what the reading SAYS: a page naming twenty
    survivors exits 0 exactly like a page naming none. Rule 3 of this standard is
    that a reading nobody has calibrated must not become a gate, and this one has
    never been pointed at real code before today.
    """
    xml_text = run_pytest_report(paths, root)
    _counts, outcomes = parse_junit(xml_text)
    candidate_outcomes: dict[str, list[tuple[str, str, str]]] = {}
    claims: dict[str, list[str]] = {}
    try:
        taken = run_pass_candidates(
            paths, root, expected_population=outcomes,
            replace_existing=True, outcomes_out=candidate_outcomes,
            claims_out=claims,
        )
        gaps = verification_gaps(outcomes, taken, candidate_outcomes)
    except ContractError as exc:
        # The one failure this command reports as a failure: no reading exists.
        # Printed on stderr and exited non-zero, because a page that said
        # nothing survived would be indistinguishable from a run in which
        # nothing was ever measured, which is the reading this whole instrument
        # was built to refuse.
        print(f"canopus: the after-build gap reading could not be made: {exc}",
              file=sys.stderr)
        return 1
    # The DEDUPLICATED population, which is the set `gaps` was drawn from
    # (`verification_gaps` collapses the triples to pairs before it counts).
    # `len(outcomes)` counted raw report rows, so a report carrying one pair
    # twice printed a denominator larger than the set the numerator came out of.
    #
    # The tests that never ran come OUT of it, through the same reader
    # `verification_gaps` drops them with, so the page and the answer it prints
    # weigh one population. Counted in, the denominator says a suite of two was
    # measured when one test was measured, and the missing name reads as a test
    # that went red.
    never_ran = tests_that_never_ran(outcomes)
    # The same third bucket, entered through the candidates rather than the real
    # run, and read with the reader `verification_gaps` drops them with. A test
    # that passed for real and skipped under a candidate is on neither of the
    # two lists the page could previously print, so it fell into the sentence
    # that says the tests named on neither went red under replacement.
    # Subtracted from the real-run set so a test skipped on BOTH sides is named
    # once, under the line that describes it first.
    sat_out = [
        pair for pair in tests_the_candidates_never_ran(candidate_outcomes)
        if pair not in set(never_ran)
    ]
    population = (
        {(rel, name) for rel, name, _outcome in outcomes}
        - set(never_ran) - set(sat_out)
    )
    total = len(population)
    dropped = claims.get("dropped", [])
    print(f"{BOLD}after-build gap reading{RESET}")
    for rel in expected:
        # The count of THIS reading's population, not the raw collection count.
        # The two differ whenever anything was skipped, and the page printed one
        # beside the other: `(2 collected)` above `survived 0 of 1`, reconciled
        # only by the `never ran` row further down. Two numbers a skim can take
        # for each other is one number too many, so the page carries the
        # denominator's own, decomposed per target.
        print(f"  target      {rel}  "
              f"({sum(1 for case_rel, _n in population if case_rel == rel)} "
              f"in this reading)")
    print(f"  candidates  {', '.join(CANDIDATES)}   "
          f"(three implementations that EXIST and are wrong)")
    # On the PAGE, not only on stderr. The reading below is evidence about these
    # modules and no others, and a reader who cannot see the list cannot tell
    # whether the subject they came here about is in it.
    # No empty-case fallback: `run_pass_candidates` raises before it returns
    # when the claim set is empty, so this line cannot be reached with nothing
    # to name, and a fallback for a state that cannot occur is a claim about
    # the code that is not true.
    print(f"  replaced    {', '.join(claims.get('claimed', []))}")
    if dropped:
        print(f"  not replaced  {', '.join(dropped)}")
    print(f"  survived    {len(gaps)} of {total}")
    if never_ran:
        print(f"  never ran   {len(never_ran)}  (skipped in the real run, so no "
              f"candidate was put in front of them)")
    if sat_out:
        print(f"  sat out     {len(sat_out)}  (skipped under a candidate, so "
              f"that candidate returned no verdict)")
    print()
    print(_AFTER_BUILD_MEANING)
    print()
    if dropped:
        print(_AFTER_BUILD_UNREPLACED)
        print()
    if never_ran or sat_out:
        print(_AFTER_BUILD_NEVER_RAN)
        print()
    print(_AFTER_BUILD_EXPECTATION)
    print()
    if not gaps:
        print(f"  {GREEN}none{RESET}  every test that RAN went red under at "
              f"least one candidate")
    for rel, name in gaps:
        print(f"  {YELLOW}survived{RESET}  {rel}::{name}")
    # LAST, under the survivors, because the two lists answer different
    # questions and the survivors are what the reader came for. Named at all
    # because a skipped test that is only absent reads as a test that went red.
    for rel, name in never_ran:
        print(f"  {YELLOW}never ran{RESET}  {rel}::{name}")
    for rel, name in sat_out:
        print(f"  {YELLOW}sat out{RESET}  {rel}::{name}")
    return 0


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
    if getattr(args, "after_build", False):
        # Branched HERE rather than woven into the table below, because the two
        # readings answer different questions over different subjects and a
        # single page carrying both would invite each to be read as the other.
        # Everything above this line is shared on purpose: the path resolution
        # and the collected-nothing refusal are properties of `probe`, not of
        # either reading.
        return _after_build(paths, root, expected)
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
    # Read from the contract's SOURCE, not from `outcomes`: a skip never fails,
    # so nothing in the JUnit report can name it. Unwrapped, like the
    # `contract_files`/`run_pytest_report` calls above it in this function: a
    # `ContractError` here (an unparseable contract file) propagates to
    # `main`'s own `except ContractError`, the same path an unparseable
    # contract already takes on those two calls.
    skipped = skip_markers_without_reason(paths, root)
    reasons = refusal_reasons(
        counts, outcomes, expected, skipped_without_reason=skipped
    )
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
    # A FLAG on `probe`, never a fourth subcommand. It is the same question this
    # command already asks ("if this code were wrong, would any gate notice"),
    # asked at the other end of a slice's life, and a separate subcommand would
    # present it as a separate capability an operator has to learn.
    probe.add_argument(
        "--after-build", action="store_true",
        help="read the gap over code that ALREADY EXISTS: run the wrong "
             "implementations against shipped code and name every test that "
             "stayed green under all of them. Reports, never refuses; exits 0 "
             "unless no reading could be made at all",
    )
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
