#!/usr/bin/env python3
"""Canopus freeze CLI: lock the test contract before a build, verify it after.

The Canopus standard freezes the definition of done before any code exists, so
the builder cannot move the target it is measured against.

    python scripts/canopus.py approve tests/test_thing.py --label my-slice \\
        --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
    # read the candidate, then COMMIT that artifact: the commit is the approval
    python scripts/canopus.py freeze tests/test_thing.py --label my-slice \\
        --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
    python scripts/canopus.py verify
    python scripts/canopus.py status
    python scripts/canopus.py release --ship --reason "slice shipped"
    python scripts/canopus.py release --window --reason "mid-build recipe change"
    python scripts/canopus.py release --force --window --reason "manifest damaged"

Three layers. The PreToolUse deny is a CONVENIENCE: it sees Write, Edit,
MultiEdit, and NotebookEdit tool calls only, so a shell `sed -i` walks past it.
`verify` is the GUARANTEE, because it recomputes digests from disk. The test gate
is what makes the guarantee FIRE: tests/conftest.py runs it at pytest session
start and scripts/run-tests.py runs it before the suite, and an unrun verify is
worth nothing.

The expected root hash lives in a committed artifact OUTSIDE the working tree,
and `verify` reads it from there. Nobody types it and nobody compares it by eye.
Point --anchor at a sibling repository with its own history, so a build reaching
for the anchor dirties a repository it had no reason to touch.

Say exactly what that buys, and no more. `read_anchor` reads the artifact's
WORKING COPY, so a line appended there is only an uncommitted diff in the other
repository: visible in its `git status`, and erasable with `git checkout --`.
That is evidence for a human who looks, not containment and not a permanent
record. The durable artifact is the `canopus-anchor:` line once someone COMMITS
it, which is why `approve` writes the candidate and `freeze` refuses a root the
COMMITTED copy CONTRADICTS. Be exact about the scope of that refusal, because
the loose reading is the flattering one: where the commit records no hash at all
(a first freeze, an untracked artifact, a folder outside any repository) there is
no approval to disagree with, so the freeze is TAKEN and reads amber rather than
being refused. The COMMITTED copy governs both the lock and the approval axis,
so an approval reachable only through git still holds the lock. A freshly
approved candidate that is on the artifact and not yet committed is permitted,
and every surface reads amber until someone commits it.
`freeze` writes nothing to the anchor: an instrument that writes the hash and
then checks the hash it wrote has verified nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))
from scripts.utils.canopus_contract import (  # noqa: E402
    RED_OUTCOMES,
    ContractError,
    contract_files,
    parse_failure_modes,
    parse_junit,
    read_plugin_dump,
    refusal_reasons,
    run_null_stub,
    run_pytest_report,
    vacuity_refusal,
)
from scripts.utils.canopus_friction import count_friction, render_friction
from scripts.utils.canopus_freeze import (  # noqa: E402
    ANCHOR_MISSING,
    ANCHOR_NONE,
    ANCHOR_PREFIX,
    ANCHOR_RECORDED,
    ANCHOR_UNBOUND,
    APPROVED,
    ATTEST_FILENAME,
    ATTESTED,
    FREEZE_DIRNAME,
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    NOT_ATTESTED,
    REASON_DIFFERENT_RECIPE,
    REASON_DIFFERENT_ROOT,
    REPO_PRESENT,
    REPO_UNKNOWN,
    SATISFIED_PREFIX,
    FreezeCorrupt,
    FreezeError,
    append_history,
    attestation_state,
    build_manifest,
    clear_freeze,
    guard_watches_directories,
    lock_state,
    open_release_window,
    read_anchor,
    read_anchor_waiver,
    read_attestation,
    read_freeze,
    read_ledger,
    tree_drift,
    unreleased_freeze,
    validate_anchor_path,
    verify_manifest,
    write_freeze,
)
from scripts.utils.canopus_git import (  # noqa: E402
    COMMITTED,
    AnchorResolution,
    head_sha,
    read_committed_anchor,
    repo_identity,
    resolve_anchor,
    resolve_anchor_waiver,
)
from scripts.utils.canopus_pack import (  # noqa: E402
    commits_outside,
    diff_stat,
    freeze_windows,
    git_commits,
    is_dirty,
    merge_base,
    parse_ts,
    render_process,
)
from scripts.utils.canopus_evidence import (  # noqa: E402
    EVIDENCE_FRESH,
    EVIDENCE_MISSING,
    EVIDENCE_UNVERIFIABLE,
    attestation_refusal,
    evidence_state,
)
from scripts.utils.canopus_gate import loss_of_lock_sentences  # noqa: E402
from scripts.utils.gate_yield import (  # noqa: E402
    record_refusal,
    retake_cause_or_error,
)
from scripts.utils.production_shape import shape_refusal  # noqa: E402
from scripts.utils.sc_trace import gate_refusal  # noqa: E402
from scripts.utils.canopus_steps import (  # noqa: E402
    ACTS,
    NO_SLICE,
    STEPS,
    act,
    act_of,
    position,
    step,
)
from scripts.utils.canopus_tree import tree_state  # noqa: E402
from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW  # noqa: E402

# The gate script every root must carry. A tree without it has no place where the
# freeze is ever checked, so a freeze taken against it is inert by construction.
GATE_SCRIPT = Path("scripts") / "run-tests.py"

# The line `approve` writes above the anchor line when it carries a reason.
# Deliberately NOT a prefix of, and not prefixed by, ANCHOR_PREFIX: `read_anchor`
# matches with `startswith(ANCHOR_PREFIX)`, so this line is prose to the parser
# and an explanation to the human reading the artifact diff.
REASON_PREFIX = "canopus-approval-reason:"


def _record(root: Path, event: str, *, digest: str, label: str,
            reason: str = "", kind: str = "") -> str:
    """Append one ledger entry, and RETURN the OSError text instead of raising.

    Every ledger write in this file records an act that has already landed, or
    one that is about to land a line later. An unguarded append therefore lets an
    OSError fall through to `main`, which prints "the frozen contract could not be
    read, so it cannot be verified" over a state that sentence is false of.
    Measured on `cmd_freeze` by injecting OSError into `append_history`:
    `freeze.json` existed, the freeze was live and enforced, and the operator was
    told the command had failed outright.

    A helper rather than four copies of a try block, because this is the seventh
    time on this branch that a guard was applied to one function and not its
    sibling. Each caller asks this once and then says, in its own words, which
    half landed; that sentence is the part that cannot be shared.

    `cmd_approve` keeps its own handler: it makes TWO appends and has to name
    which of the two failed, which is a different message rather than a different
    mechanism.
    """
    try:
        append_history(root, event, digest=digest, label=label, reason=reason,
                       kind=kind)
    except OSError as exc:
        return str(exc)
    return ""


def _unlogged_release(failed: str) -> int:
    """Both release paths log BEFORE clearing, so an unlogged release is refused.

    The order is what makes this branch simple to state: nothing has been
    cleared, so the freeze is still held and the operator can retry once the
    ledger is writable. Clearing anyway would end a freeze with no line saying it
    ended, which is the same gap `rm .canopus/freeze.json` leaves and the one the
    ledger exists to make visible.
    """
    print(f"canopus: NOTHING was released and the freeze is still held; the "
          f"ledger entry failed: {failed}. A release that leaves no line behind "
          f"is indistinguishable from a deleted manifest, so it is refused: make "
          f"`.canopus/` writable and run the same command again.",
          file=sys.stderr)
    return 1


def _satisfied_reason(args) -> str:
    """The `--contract-satisfied` reason, whitespace-collapsed, or "".

    Collapsed for the same reason `cmd_approve` collapses its approval reason:
    the value reaches a single-line ledger record, and a newline inside it would
    start a line the writer did not write.
    """
    return " ".join((getattr(args, "contract_satisfied", "") or "").split())


def _ledger_reason(*parts: str) -> str:
    """Join the reasons one ledger entry carries, dropping the empty ones.

    This ledger's `reason` is already a free-form "why this entry looks like
    this" string rather than a typed cause, so a retake carries both halves:
    what the contract measured, and why a green contract was accepted.
    """
    return "; ".join(part for part in parts if part)


def _print_root(digest: str, manifest: dict) -> None:
    count = len(manifest["files"])
    noun = "file" if count == 1 else "files"
    print(f"{BOLD}CANOPUS  root {digest}{RESET}  "
          f"(label: {manifest['label']}, {count} {noun})")


def _resolve_root(args) -> Path:
    """Resolve --root and refuse a tree that has no test gate.

    --root defaults to the engine root rather than the shell's cwd. Defaulting
    to the cwd meant `cd tests && python ../scripts/canopus.py freeze ...`
    printed a root hash, exited 0, and wrote the state to tests/.canopus/ —
    where neither the PreToolUse dispatcher nor the gate ever looks. The
    operator was told the lock was on while it was inert, which is the worst
    thing a discipline tool can do.
    """
    root = Path(args.root).resolve()
    if not (root / GATE_SCRIPT).is_file():
        raise FreezeError(
            f"{root} has no {GATE_SCRIPT.as_posix()}; a tree with no test gate "
            f"cannot enforce a freeze, so freezing it would be inert"
        )
    return root


def _under_root(raw: str, root: Path) -> Path:
    """Resolve a CLI path argument against --root, not against the shell's cwd.

    --root exists so the tree being frozen need not be the cwd. Resolving
    relative arguments against the cwd would make `--root ../other freeze
    tests/x.py` fail with "outside the working tree" for a legal invocation.
    """
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else root / candidate


def _record_refusal(root: Path, mechanism: str, cause: str, *, reason: str = "",
                    label: str = "") -> None:
    """Append one refusal to the ledger, and never let that change the refusal.

    Until 2026-08-02 the ledger held 152 events and not one refusal: the twelve
    early returns across `approve`, `freeze` and `release` all exited without
    touching it, so every time the standard refused the builder the event
    vanished and its yield could never be counted.

    Reports its own failure and swallows nothing else. `record_refusal` does not
    raise, so this cannot convert a refusal into a crash -- the one outcome
    worse than an unrecorded refusal is a refusal that stops refusing.

    One name, spelled exactly once, because the AST guard in
    `tests/test_gate_yield.py` walks each refusal's own statement list looking
    for a call to it. A refusal added later without this line fails that test BY
    NAME rather than passing silently.
    """
    # Two refusals are deliberately NOT recorded, and both were found by the
    # ordinary suite rather than by me.
    #
    # A root this tool REFUSED to accept has no ledger to belong to. Recording
    # there created `.canopus/` inside a directory with no test gate, which is
    # the exact litter `_resolve_root` exists to prevent: state written where
    # neither the dispatcher nor the gate will ever look.
    if not (root / FREEZE_DIRNAME).exists() and not (root / GATE_SCRIPT).exists():
        return
    # And a refusal CAUSED by the ledger failing cannot be recorded in the
    # ledger. Writing it there would either fail again or, worse, succeed by a
    # different path and leave a record implying the ledger was working.
    if cause == "ledger_write_failed":
        return
    failed = record_refusal(root, mechanism=mechanism, cause=cause, label=label,
                            reason=reason)
    if failed:
        print(f"canopus: {failed}", file=sys.stderr)


def _print_attestation(root: Path, recomputed_root: str, current_tree=None) -> None:
    """The second axis: did the frozen tests actually RUN, and pass?

    The lock line answers "did the contract move". It cannot answer "did the
    contract run": -k, --deselect, --ignore and a bare path argument all reach
    green with every frozen byte intact. From wire 2, a bare path or node id is
    caught too whenever the file carries a freeze-time baseline, because the
    record compares what was collected against the whole-file item count.

    Reports only, and deliberately: it can never change an exit code, because
    the gate that would act on it runs at pytest session start, before the run
    it would attest has finished. Making it fatal would fail every run on its
    own missing record.

    *current_tree* lets a caller that already sampled the tree hand its own
    sample down instead of this function taking a second one. `cmd_pack` is
    the one caller that needs it: it reads the tree once for its own
    `staleness` section and, before this parameter existed, read it again
    here -- two separate git walks behind one verdict, and a window between
    them for the tree to move and the two readings to disagree. None (every
    other caller) means "sample it here", unchanged from before this
    parameter was added.
    """
    record = read_attestation(root)
    if current_tree is None:
        current_tree = tree_state(root)
    state, reason = attestation_state(record, recomputed_root, current_tree)
    if state == ATTESTED:
        counts = [
            entry for entry in (record.get("frozen_tests") or {}).values()
            if isinstance(entry, dict)
        ]
        passed = sum(entry.get("passed", 0) for entry in counts)
        skipped = sum(entry.get("skipped", 0) for entry in counts)
        tail = f", {skipped} skipped" if skipped else ""
        # What the record is BOUND to, named on the one line an operator reads
        # to sign off: a local ATTESTED record is evidence about a specific
        # tree, not a timeless fact about the code.
        tree = record.get("tree") or {}
        # `+0 dirty` said "this record was taken over an unclean tree" on the one
        # line an operator signs off from, for the CLEANEST possible case. The
        # count was right and the word was a lie, which is the harder kind to
        # notice. Found at step 11 of the yield-axes slice, on that slice's own
        # evidence page, and the same shape as the unconditional "Mutation
        # testing has not run" the friction-counters slice removed a day earlier.
        soiled = len(tree.get("dirty") or {})
        bound = (f", bound to {tree['head'][:12]}"
                 + (f"+{soiled} dirty" if soiled else ", clean tree")
                 if tree.get("head") else "")
        print(f"{GREEN}{BOLD}{ATTESTED}{RESET}  {passed} frozen tests passed, none "
              f"deselected, at "
              f"{record.get('attested_at') or 'an unrecorded time'}{tail}{bound}")
        return
    print(f"{YELLOW}{BOLD}{NOT_ATTESTED}{RESET}  {reason}")
    listed = (record or {}).get("reasons")
    if isinstance(listed, list):
        for line in listed[:5]:
            print(f"  reason   {line}")
        if len(listed) > 5:
            # Five is a display bound, not a claim about how many there were. A
            # plugin delta arrives one reason per name, so the bound stops being
            # harmless exactly when it starts mattering, and a truncated list
            # that does not say so reads as the whole story.
            print(f"  reason   ... and {len(listed) - 5} more, in "
                  f"{FREEZE_DIRNAME}/{ATTEST_FILENAME}")
    if record and reason in (REASON_DIFFERENT_ROOT, REASON_DIFFERENT_RECIPE):
        # `attestation_state` answers with ONE reason, and the root/recipe axis
        # it names here short-circuits before it ever compares the tree -- so a
        # root that moved AND a tree that moved (the ordinary case: a new file
        # anywhere in the tree moves both) would otherwise report only the
        # root's verdict and never name the path. Both are real and both
        # perish the record; this names the tree's half too, from the record's
        # own stored tree rather than re-deriving what already qualified it.
        moved = tree_drift(record.get("tree"), current_tree)
        for line in moved[:5]:
            print(f"  reason   {line}")
        if len(moved) > 5:
            print(f"  reason   ... and {len(moved) - 5} more, not named here")


def _print_approval(resolution: AnchorResolution) -> None:
    """The third axis: was this exact freeze the one a human approved?

    The lock says the contract has not moved since the freeze. The attestation
    says it ran. Neither says the freeze was ever approved, and every surface
    that reports state prints this line. An operator who finds one command that
    omits it learns to read that command instead.

    Amber rather than red when unverified, deliberately: an operator whose gate
    artifact is a file in a folder has no repository to attribute an approval
    to, and that is a supported way to use the tool, not a failure of it.
    """
    colour = GREEN if resolution.approval == APPROVED else YELLOW
    detail = f"  {resolution.approval_reason}" if resolution.approval_reason else ""
    print(f"{colour}{BOLD}{resolution.approval}{RESET}{detail}")


def _print_contract(manifest: dict, record: dict) -> None:
    """The freeze-time baseline against what the last run actually collected.

    Silent when the manifest carries no baseline, which is every freeze taken
    without `--contract`. That silence is the reading an operator needs: a
    contract frozen positionally has no per-file item count behind it, so the
    attestation's subset check has nothing to compare against and this section
    would be inventing a row it cannot fill.

    Shared by `pack` and `status` rather than written twice. `status` is the
    command an operator types after a retake to confirm the baseline came back,
    and it printed every other manifest axis while silently dropping this one.
    """
    baseline = manifest.get("baseline") or {}
    if not baseline:
        return
    print(f"\n{BOLD}contract{RESET}")
    counts = (record or {}).get("frozen_tests") or {}
    for rel, expected in sorted(baseline.items()):
        entry = counts.get(rel) if isinstance(counts, dict) else None
        got = entry.get("collected", 0) if isinstance(entry, dict) else 0
        mark = GREEN if got == expected else YELLOW
        print(f"  {mark}{got} of {expected}{RESET}  {rel}")


def _print_waiver(anchor: str, root_digest: str) -> None:
    """Say, on every reporting surface, that this freeze's contract was waived.

    A freeze whose contract was wholly green passed the redness rule on a stated
    reason rather than on the contract's own redness, and a surface that omits
    that is reporting a stronger claim than the freeze earned. `pack` said it and
    `verify` and `status` did not, which is the wrong way round: the documented
    countermeasure to every limit in this tool is "run `canopus verify`
    yourself", and that command was the one hiding the weaker claim.

    Read COMMITTED-copy-first, through `resolve_anchor_waiver`. The lock and the
    approval are read from HEAD, so a waiver read only from the working file was
    erasable from the evidence page by one `sed -i` while both of those stood.
    Measured: deleting the line took CONTRACT WAIVED from one occurrence to zero
    with LOCK HELD and APPROVED unchanged and HEAD still carrying the waiver.
    """
    waiver = resolve_anchor_waiver(Path(anchor), root_digest) if anchor else ""
    if waiver:
        print(f"\n{YELLOW}{BOLD}CONTRACT WAIVED{RESET}  the approved freeze was "
              f"accepted with a wholly green contract, under "
              f"--contract-satisfied: {waiver}")


def _print_dormant_lock(root: Path) -> None:
    """What `status` says when no manifest is on disk, which is THREE states.

    The gate speaks for all three at every pytest session start; `status` is the
    command an operator actually types, and it said "no active freeze" over an
    open release window and over a freeze whose manifest had been deleted alike.
    A reporting surface that is quieter than the automatic one teaches an
    operator to read the automatic one instead.

    Reporting only, so the exit code stays 0 here as it does everywhere else in
    `status`: this command describes state and `verify` is the one that fails.
    """
    entries = read_ledger(root)
    vanished = unreleased_freeze(entries)
    if vanished is not None:
        print(f"{RED}{BOLD}MANIFEST GONE{RESET}  the ledger records a freeze "
              f"taken {vanished.get('ts') or 'at an unrecorded time'} "
              f"(label: {vanished.get('label') or 'unrecorded'}) that no release "
              f"closed, and .canopus/freeze.json is not there. Re-freeze it, or "
              f"end the lock the way the ledger can see: `release --force "
              f"--window --reason \"<why>\"`.")
        return
    window = open_release_window(entries)
    if window is not None:
        print(f"{YELLOW}release window open{RESET}  since "
              f"{window.get('ts') or 'an unrecorded time'}: "
              f"{window.get('reason') or 'no reason recorded'}. No lock is held, "
              f"so a green suite proves nothing about the contract.")


def _candidate_manifest(args, root: Path, anchor_path: Path):
    """(manifest, contract_note, waived) for approve and freeze; manifest None on refusal.

    `waived` says whether `--contract-satisfied` ACTUALLY fired: a wholly green
    contract accepted on the stated reason. It is False on a red contract, where
    the flag changes nothing and says so, and False when no contract ran at all.
    Only `cmd_freeze` reads it, and only to refuse a waiver the approval on the
    artifact does not carry — a distinction the flag's mere presence cannot make,
    because refusing on presence would refuse the no-op runs too.

    One builder for both commands. Two copies of this construction is how the
    approved hash and the frozen hash come to differ over a default nobody
    noticed, and every freeze after that is refused for a reason that looks like
    tampering.

    The note carries the already-green count forward. That measurement landed two
    commits before this slice for a reason that has not changed: the redness gate
    needs ONE red in the SET, so a mid-build retake of an 11-of-14-green contract
    passes the same check a fully red contract passes at the start. resolve_anchor
    does not supersede it; they answer different questions, one about approval and
    one about how much the contract was still proving.
    """
    if not args.paths and not args.content and not args.contract:
        print("canopus: at least one path is required, positionally or via "
              "--content or --contract", file=sys.stderr)
        return (None, "", False)
    # The repository questions come FIRST, ahead of the contract run, because
    # both of their answers are refusals and neither one can be changed by
    # anything the contract does. Behind the contract block they cost the
    # operator a full pytest session over the contract, plus the null-stub
    # session behind it, before being told to go and commit something: measured
    # on `freeze --contract` against an anchor in a freshly initialised
    # repository. A refusal that is knowable up front is said up front.
    repo_status, repo_identity_digest = repo_identity(anchor_path.parent)
    if repo_status == REPO_UNKNOWN:
        # Recording a freeze here would record a LIE. `in_repo` below is
        # `repo_status == REPO_PRESENT`, so a git that cannot be reached wrote
        # `in_repo: false` into the manifest — the claim that the anchor was
        # OUTSIDE any repository, which the tool has no evidence for and which
        # was probably untrue. Every later `verify` then reads BINDING_BROKEN,
        # "the freeze was taken blind", and blames the blinding rather than
        # naming the real cause. Refusing is the honest answer: an unbound freeze
        # is a positive claim about the world, not a shrug.
        print("canopus: git could not be consulted, so the anchor's repository "
              "cannot be identified and a freeze taken now would RECORD that the "
              "anchor is outside any repository. That claim has no evidence "
              "behind it. Put a working `git` on PATH (and check the anchor's "
              "directory still exists) and run the same command again.",
              file=sys.stderr)
        return (None, "", False)
    if repo_status == REPO_PRESENT and not repo_identity_digest:
        print("canopus: the anchor's repository has no commits, so an approval "
              "cannot be attributed to it and the identity recorded now would "
              "change the moment you commit. Commit something in that "
              "repository first.", file=sys.stderr)
        return (None, "", False)
    binding = {"in_repo": repo_status == REPO_PRESENT,
               "identity": repo_identity_digest}
    contracts = [_under_root(p, root) for p in args.contract]
    baseline: dict[str, int] = {}
    plugins: list[str] = []
    contract_note = ""
    waived = False
    if contracts:
        expected = contract_files(contracts, root)
        if not expected:
            print("canopus: --contract names no test modules; a contract with no "
                  "tests can never be attested", file=sys.stderr)
            return (None, "", False)
        # A12, and the position is argued rather than convenient. AFTER the
        # names-no-modules refusal, because that one is the more fundamental
        # finding and its message is the more useful: over an empty contract
        # directory this check would otherwise report every criterion as
        # unclaimed, which is true and useless. BEFORE the contract RUN, because
        # the answer is static, costs one file read, and cannot be changed by
        # anything the run does -- behind it the operator pays a full pytest
        # session plus the null-stub session behind that before being told about
        # a docstring. The same ordering argument the repository checks make.
        #
        # `gate_refusal` is TOTAL and fails open on anything short of a definite
        # finding, because this builder is shared by approve and freeze and a
        # raise here would refuse every slice in the workspace including the
        # `/canopus back` that repairs it.
        criteria_refusal = gate_refusal(anchor_path, contracts)
        if criteria_refusal:
            print(f"canopus: {criteria_refusal}", file=sys.stderr)
            return (None, "", False)
        # The SOFT half of the production-shape check. Here the closure can only
        # reach what already exists, so a slice EXTENDING existing code that
        # reads a record store is refused now, while a slice building a brand
        # new module is not: at this moment its module is absent by
        # construction and the walk stops at the hole. That second case is the
        # gate-yield case exactly, which is why the hard half runs at
        # attestation, once the code is on disk. Total, like `gate_refusal`.
        shape = shape_refusal(contracts, root)
        if shape:
            print(f"canopus: {shape}", file=sys.stderr)
            return (None, "", False)
        # One real run, read twice. Running the contract for the outcomes and
        # again for the report would double the wall time and compare outcomes
        # from one run against failure modes from another.
        with tempfile.TemporaryDirectory() as scratch:
            dump = Path(scratch) / "plugins.json"
            xml_text = run_pytest_report(contracts, root, plugin_dump=dump)
            # Read INSIDE the scratch context, which is the whole reason the
            # path is owned here rather than inside run_pytest_report: its own
            # temporary directory is gone by the time it returns.
            plugins = read_plugin_dump(dump)
        counts, outcomes = parse_junit(xml_text)
        satisfied = _satisfied_reason(args)
        red = any(outcome in RED_OUTCOMES for _rel, _name, outcome in outcomes)
        if getattr(args, "contract_satisfied", "") and not satisfied:
            # A reason of pure whitespace collapses to "", no waiver is taken,
            # and the refusal below then says only "no contract test failed".
            # Fail-closed is the right direction and silence about WHY is not:
            # an operator who passed the flag and reads a refusal that never
            # mentions it concludes the flag does not work.
            print(f"{YELLOW}contract{RESET}  --contract-satisfied was passed "
                  f"with a blank reason, so NO waiver was taken. The reason is "
                  f"the flag's value, and a waiver with nothing behind it is "
                  f"not a waiver.")
        if satisfied and red:
            # Said out loud rather than passed over in silence. The waiver is a
            # no-op here, because the redness condition it suppresses never
            # fires on a red contract; an operator who is never told that learns
            # to paste the flag into every command, and the one run where it
            # DOES change the answer looks identical to the ones where it did
            # not.
            print(f"{YELLOW}contract{RESET}  --contract-satisfied changed "
                  f"nothing: this contract is RED, so the redness it waives was "
                  f"never in question")
        reasons = refusal_reasons(counts, outcomes, expected,
                                  green_ok=bool(satisfied))
        # The null stub is TWO whole pytest sessions, one per stub value set, and
        # they are skipped whenever their answer cannot change this one. Two
        # states qualify, and the second is the state `--contract-satisfied`
        # exists for. With a refusal already earned, a vacuity verdict can only
        # add to it. With no RED test in the set, `vacuity_refusal` weighs an
        # empty `cases` and returns [] by construction, so the sessions are spent
        # to be discarded a line later. `probe` still runs them unconditionally:
        # there the verdict is the output rather than an input to a refusal.
        if not reasons and red:
            # `expected_population` is the REAL run's triples, read off the very
            # report this function already parsed, and passing it is not an
            # optimisation. Omitted, `run_null_stub` runs its OWN unstubbed
            # baseline and checks its lost-test guard against THAT population
            # while the verdict is applied to the outcomes above: two pytest
            # sessions, with nothing holding them equal. Measured on review, a
            # module-scope counter in a contract file made the two disagree and
            # froze a wholly vacuous contract through the gap. The parameter
            # carries a default, so a caller that forgets fails silently.
            try:
                vacuous = run_null_stub(
                    contracts, root, expected_population=outcomes
                )
            except ContractError as exc:
                # A measurement that could not happen is a refusal, not a pass.
                # The alternative reading — say so and freeze anyway — is what
                # this slice removes: the operator cannot act on a sentence
                # printed beside an exit 0.
                reasons.append(
                    f"the contract's vacuity could not be measured: {exc}"
                )
            else:
                reasons.extend(vacuity_refusal(outcomes, vacuous))
        if reasons:
            print("canopus: the contract was refused:", file=sys.stderr)
            for reason in reasons:
                print(f"  {reason}", file=sys.stderr)
            return (None, "", False)
        if satisfied and not red:
            # The waiver actually fired here, so the reason is on the surface as
            # well as in the ledger. A refusal that was overridden silently is
            # the same defect as a refusal that never fired.
            waived = True
            print(f"{YELLOW}contract{RESET}  the contract is wholly GREEN and was "
                  f"accepted by --contract-satisfied: {satisfied}")
        baseline = {rel: counts[rel] for rel in expected}
        already_green = sum(
            1 for _rel, _name, outcome in outcomes if outcome == "passed"
        )
        contract_note = f"{already_green} of {sum(counts.values())} already green"
    if not plugins:
        # OUTSIDE the contract block, which is where it was first written and
        # where it covered one of the two ways to get here. A freeze over plain
        # paths runs no pytest child at all, so it captured nothing, said
        # nothing, and every later run then refused for a baseline the operator
        # never knew was expected — days later, and worded as though a capture
        # had failed. Fail-closed is the right DIRECTION (SC-7), and silence
        # about it is the defect.
        detail = (
            "The contract run recorded no plugin set; re-run the freeze, and if "
            "it persists the contract child is not reaching session finish."
            if contracts else
            "A freeze taken without --contract runs no pytest child, so there is "
            "nothing to capture the set from. Freeze the contract directory with "
            "--contract to record one."
        )
        print(f"{YELLOW}plugins{RESET}  this freeze carries NO plugin baseline, "
              f"so no later run can attest against it: every gate run will report "
              f"NOT ATTESTED naming the missing baseline. {detail}")
    manifest = build_manifest(
        [_under_root(p, root) for p in args.paths] + contracts,
        root,
        label=args.label,
        frozen_at=datetime.now(timezone.utc).isoformat(),
        anchor=anchor_path,
        content_only=[_under_root(p, root) for p in args.content],
        baseline=baseline,
        anchor_repo=binding,
        plugins=plugins,
    )
    return (manifest, contract_note, waived)


def cmd_approve(args) -> int:
    """Record the candidate root hash for a human to commit. Freezes nothing.

    The approval act is the human's COMMIT of the artifact this writes into: it
    carries an author, a timestamp, and a position in a history that cannot be
    altered afterwards without rewriting it. `freeze` then refuses anything that
    disagrees with the committed value.

    freeze does not write the anchor any more, and that is forced rather than
    chosen: an instrument that writes the hash and then checks the hash it wrote
    has verified nothing.
    """
    root = _resolve_root(args)
    # read_freeze also raises FreezeCorrupt on a damaged manifest, so approve
    # inherits the corrupt-manifest refusal along with the active-freeze one.
    # That is deliberate and matches cmd_freeze: the artifact is left untouched
    # and `release --force --window --reason` is the logged escape. Named here because a
    # behaviour that arrives as a side effect of one guard is the kind nobody
    # remembers is there.
    if read_freeze(root) is not None:
        # Measured, not imagined: approve set A, commit, freeze set A, verify
        # reads LOCK HELD; then approve set B while that freeze is still active
        # and commit it, exactly as this command's own closing line instructs,
        # and verify reads LOSS OF LOCK with nothing on the tree having moved.
        # `cmd_freeze` already refuses for the same reason, and the docstring of
        # _candidate_manifest names this failure as the one to avoid.
        print("canopus: a freeze is already active; run "
              "`release --window --reason \"<why>\"` first. "
              "Approving a different set now turns the held lock red the moment "
              "you commit it, and nothing on the tree will have moved.",
              file=sys.stderr)
        _record_refusal(root, "approve", "freeze_already_active",
                        reason="a freeze is already active")
        return 1
    anchor_path = validate_anchor_path(_under_root(args.anchor, root), root)
    status, recorded = read_anchor(anchor_path)
    # The status is deliberately not bound: the union below needs the committed
    # HASH and nothing else, and every non-COMMITTED status already yields None.
    # A name bound and never read invites a maintainer to build a branch on it
    # that duplicates a decision `resolve_anchor` already owns.
    _, committed_recorded = read_committed_anchor(anchor_path)
    # The union of both readers, deliberately. Refusing on the working file alone
    # would let `git checkout --` erase an uncommitted approve line, so a second
    # approve over a different set never trips --replace. Refusing on the
    # committed one alone would miss the line this command wrote a minute ago.
    # The committed half is the one the rule actually protects, and the one
    # `git checkout --` cannot erase.
    already = recorded if status == ANCHOR_RECORDED else committed_recorded
    if already and not args.replace:
        print(f"canopus: {anchor_path} already records {already}. An approval is "
              f"never silently overwritten. If the SET being approved is "
              f"legitimately changing, re-run with --replace and --reason.",
              file=sys.stderr)
        _record_refusal(root, "approve", "anchor_already_recorded",
                        reason="the anchor already records an approval and --replace was not given")
        return 1
    if args.replace and not args.reason:
        print("canopus: --replace requires --reason; an unexplained replacement "
              "is indistinguishable from a re-baseline", file=sys.stderr)
        _record_refusal(root, "approve", "replace_without_reason",
                        reason="--replace without --reason")
        return 1
    # The prose reason and the declared cause are not redundant. The reason says
    # what happened this once; the cause is the only thing that makes retakes
    # countable, and a retake is the standard's largest single output. Measured
    # 2026-08-03: 39 of them in the ledger, and the yield report saw none.
    if args.replace:
        cause_error = retake_cause_or_error(getattr(args, "cause", ""))
        if cause_error:
            print(f"canopus: {cause_error}", file=sys.stderr)
            _record_refusal(root, "approve", "retake_cause_missing",
                            reason="--replace without a declared --cause")
            return 1
    elif getattr(args, "cause", ""):
        # A first approval is not a retake, so there is nothing for a cause to
        # classify and nowhere for it to be written. Accepting it silently is the
        # worse outcome: the operator typed a flag, saw exit 0, and recorded
        # nothing. Same fail-loud posture as `--replace` without `--reason`.
        print("canopus: --cause classifies a RETAKE and means nothing without "
              "--replace; a first approval records no anchor_replaced entry for "
              "it to land on.", file=sys.stderr)
        _record_refusal(root, "approve", "retake_cause_missing",
                        reason="--cause given without --replace")
        return 1
    manifest, contract_note, waived = _candidate_manifest(args, root, anchor_path)
    if manifest is None:
        _record_refusal(root, "approve", "candidate_refused",
                        reason="the candidate manifest was refused")
        return 1
    # Bound to the ACT, never to the flag's presence, exactly as `cmd_freeze`
    # binds its refusal. This command discarded the third return value and wrote
    # the waiver line on `args.contract_satisfied` alone, so `approve --contract
    # <a wholly RED contract> --contract-satisfied "<why>"` printed
    # "--contract-satisfied changed nothing: this contract is RED" and then wrote
    # `canopus-contract-satisfied: <why>` into the artifact a human commits.
    # `canopus pack` afterwards printed CONTRACT WAIVED under a red-earned
    # contract row. The same happened with no `--contract` at all: a waiver line
    # for a contract that never ran. A falsehood in the committed record, on the
    # surface an operator approves from.
    #
    # The ledger takes the same value for the same reason. A waiver recorded
    # there that never fired is the identical claim, made more quietly.
    satisfied = _satisfied_reason(args) if waived else ""
    # The artifact write comes before the ledger, and the order is the same one
    # cmd_freeze reasons about: an OSError on THIS line leaves no approval
    # anywhere, while the reverse would leave a ledger claiming an approval the
    # artifact never received.
    #
    # The ledger write below opens the opposite window, and it is named here
    # rather than left to be discovered: by the time it runs the artifact
    # ALREADY carries the candidate, so an OSError there is a partial approval,
    # not a failed one. Reporting it as a plain failure is what sends an
    # operator into a retry that demands --replace --reason for an approval they
    # were told had not happened, so that path says which half landed.
    # The reason goes to the DURABLE record, not only to the ledger. `--replace`
    # demands one, and writing it to `.canopus/history.jsonl` alone put it in a
    # gitignored directory one `rm -rf` removes, while the artifact a human
    # commits carried a bare second hash line: two indistinguishable hashes and
    # no account of either.
    #
    # It sits on its OWN LINE, above the anchor line and never on it. `read_anchor`
    # takes everything after `canopus-anchor:` as the hash value, so a reason
    # appended there would be parsed as part of the digest. The reason's own
    # whitespace is collapsed for the same reason in reverse: a newline inside it
    # would start a line this file did not write.
    #
    # The `--contract-satisfied` reason rides in on the same mechanism, and for
    # the same reason: a waiver of the redness rule is precisely the kind of act
    # that must survive in the record a human commits. The ledger alone put it
    # in `.canopus/`, which is gitignored and which `rm -rf .canopus` removes,
    # so the claim "this retake was accepted for a stated reason" had no durable
    # artifact behind it. Its own line and its own prefix, never the anchor
    # line, and `canopus pack` reads it back beside the freeze it belongs to.
    reason_line = " ".join((args.reason or "").split())
    lines = [""]
    if reason_line:
        lines.append(f"{REASON_PREFIX} {reason_line}")
    if satisfied:
        lines.append(f"{SATISFIED_PREFIX} {satisfied}")
    lines.append(f"{ANCHOR_PREFIX} {manifest['root']}")
    try:
        with anchor_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        # Named here rather than left to `main`'s handler, which says "the frozen
        # contract could not be read, so it cannot be verified" — a sentence about
        # a different subsystem in both halves. Measured on a `chmod 444` gate
        # artifact: the contract read fine and ran fine, and the only thing that
        # failed was this write. `_record`'s docstring names that class for the
        # ledger and the artifact write beside it was left generic.
        print(f"canopus: the candidate {manifest['root']} could NOT be written to "
              f"{anchor_path}: {exc}. Nothing was approved and nothing was "
              f"logged, so make the artifact writable and run the same command "
              f"again.", file=sys.stderr)
        _record_refusal(root, "approve", "artifact_write_failed",
                        reason="the candidate could not be written to the anchor")
        return 1
    logged = ""
    try:
        append_history(root, "approve", digest=manifest["root"],
                       label=args.label,
                       reason=_ledger_reason(args.reason or "", satisfied))
        logged = "approve"
        if already:
            # `kind` carries the declared cause, and it is the whole reason this
            # slice exists: the prose in `reason` is unreadable by any counter,
            # and a counter built on its substrings lies the first time somebody
            # rewords their sentence.
            append_history(root, "anchor_replaced", digest=manifest["root"],
                           label=args.label, kind=getattr(args, "cause", ""),
                           reason=args.reason)
    except OSError as exc:
        # Which ledger entry failed is the whole point of this message, so it
        # names the state rather than the call: saying "the ledger entry failed"
        # when the approve entry landed and only anchor_replaced did not is the
        # same imprecision this branch exists to remove one layer up.
        missing = "anchor_replaced" if logged else "approve"
        landed = f" The `{logged}` entry did land." if logged else ""
        print(f"canopus: the candidate {manifest['root']} WAS written to "
              f"{anchor_path}; the `{missing}` ledger entry failed: {exc}."
              f"{landed} The approval is on the artifact, so read it and commit "
              f"it if it is the set you meant, and expect a re-run to ask for "
              f"--replace --reason.",
              file=sys.stderr)
        _record_refusal(root, "approve", "ledger_write_failed",
                        reason="the candidate was written but a ledger entry failed")
        return 1
    _print_root(manifest["root"], manifest)
    if contract_note:
        print(f"{YELLOW}contract{RESET}  {contract_note} before this approval")
    print(f"\nCandidate recorded in {anchor_path}, uncommitted. Read it, then "
          f"COMMIT that repository: the commit is the approval, and freeze "
          f"refuses anything that disagrees with what you committed.")
    return 0


def cmd_freeze(args) -> int:
    """Take the freeze, but only over what a human committed an approval for.

    This command no longer writes the anchor line. That is the point of the
    split: an instrument that writes the hash and then checks the hash it wrote
    has verified nothing, and every wire before this one shipped exactly that.
    `approve` writes the candidate, a human COMMITS it, and this command refuses
    anything the commit disagrees with.

    The refusal is scoped to COMMITTED, deliberately. Under uncommitted, no_repo
    and no_git there is no approval to bind to, and an operator whose gate
    artifact is a file in a folder is a supported way to use the tool rather
    than a failure of it. Those freezes are taken and say so in amber, which is
    the same posture the approval axis already takes on every reporting surface.

    One exception, and it is the SAFER branch rather than a softening. When the
    artifact already records exactly what this freeze would take, and only the
    COMMIT is outstanding, the freeze proceeds amber. Refusing there leaves NO
    manifest, and `freeze_gate` returns 0 in silence when no freeze is active:
    the operator who edited a contract and was refused walks away with no lock
    at all and a green suite. Taking the freeze instead leaves an ACTIVE lock
    whose committed approval disagrees, so `verify` reports LOSS OF LOCK and
    every pytest session fails until a human commits the new approval. A
    re-baseline that ends red is worth more than one that ends unlocked.

    One refusal this command owns alone: `--contract-satisfied` is refused unless
    the approval on the artifact carries the matching waiver. `canopus pack`
    renders CONTRACT WAIVED from the artifact, so a freeze waived here over an
    approval that was not produces an evidence page showing no waiver at all.
    """
    root = _resolve_root(args)
    if read_freeze(root) is not None:
        print("canopus: a freeze is already active; run "
              "`release --window --reason \"<why>\"` first "
              "(changing a contract reopens the approval gate)", file=sys.stderr)
        _record_refusal(root, "freeze", "freeze_already_active",
                        reason="a freeze is already active")
        return 1
    anchor_path = validate_anchor_path(_under_root(args.anchor, root), root)
    manifest, contract_note, waived = _candidate_manifest(args, root, anchor_path)
    if manifest is None:
        # The cause is the candidate's, not the lock's. This line read
        # `freeze_already_active` until 2026-08-02 -- a copy of the branch
        # above, and unreachable prose there: control flow has already proved
        # no freeze is active. It cost the yield report twice over, inflating
        # one cause with refusals it never made and leaving `candidate_refused`
        # looking like it never fires on this path. Both guard tests passed
        # through it, because one checks that a recorder is CALLED and the
        # other that a cause is EMITTED SOMEWHERE; neither reads the argument.
        _record_refusal(root, "freeze", "candidate_refused",
                        reason="the candidate manifest was refused")
        return 1
    committed_status, committed_hash = read_committed_anchor(anchor_path)
    _working_status, working_hash = read_anchor(anchor_path)
    # Full digests, compared whole, against both copies. A prefix or truncation
    # comparison here would look rigorous and hand a builder with a shell a
    # short value to brute-force by appending whitespace to a frozen file.
    recorded_anywhere = manifest["root"] in (committed_hash, working_hash)
    if committed_status == COMMITTED and not recorded_anywhere:
        print(f"canopus: the committed approval does not match what this freeze "
              f"would take, and no freeze was taken.\n"
              f"  approved  {committed_hash}\n"
              f"  computed  {manifest['root']}\n"
              f"A contract edited after approval reopens the gate: re-run "
              f"`approve --replace --reason \"<why>\"` and commit it.",
              file=sys.stderr)
        _record_refusal(root, "freeze", "approval_disagrees",
                        reason="the committed approval does not match what this freeze would take")
        return 1
    # Bound to the act, like the refusal below and like `cmd_approve`'s write: a
    # ledger line saying a green contract was accepted for a reason, written on a
    # run where the contract was red and the flag changed nothing, is a claim the
    # freeze did not make.
    satisfied = _satisfied_reason(args) if waived else ""
    recorded_waiver = (read_anchor_waiver(anchor_path, manifest["root"])
                       or resolve_anchor_waiver(anchor_path, manifest["root"]))
    if waived and not recorded_waiver:
        # The pairing nothing refused before: `approve` WITHOUT the flag, then
        # `freeze --contract-satisfied`. The freeze is waived, and the evidence
        # page renders no waiver at all — `canopus pack` reads its CONTRACT
        # WAIVED marker from the anchor artifact, which only `approve` writes. So
        # the one surface an operator approves from reported a stronger claim
        # than the freeze earned, and the waiver survived only in gitignored
        # `.canopus/history.jsonl`.
        #
        # Bound to the waiver having FIRED, never to the flag being present. On a
        # red contract the flag changes nothing and the command already says so;
        # refusing there would refuse a run that made no claim at all, which is
        # the failure mode of every guard written against a flag rather than
        # against the act the flag performs.
        #
        # BOTH copies are consulted, and the union is the same shape the hash
        # comparison above takes. The working copy alone would refuse a waiver
        # that is COMMITTED and has since been scrubbed from the working file,
        # which is the direction a builder edits in. The committed copy alone
        # would refuse the ordinary sequence of `approve`, then `freeze`, then
        # commit, which the docstring above deliberately permits amber. Whether
        # the approval is COMMITTED is a separate axis and is already reported,
        # loudly, a few lines below.
        print(f"canopus: --contract-satisfied was passed, and {anchor_path} "
              f"records no waiver for {manifest['root']} in its working copy or "
              f"in HEAD. A waiver no approval carries is one `canopus pack` "
              f"cannot show: the evidence page reads the "
              f"`{SATISFIED_PREFIX}` line off that artifact, and there is none "
              f"on it. Re-run `approve --contract-satisfied \"<why>\"` (with "
              f"--replace --reason if an approval is already recorded), commit "
              f"the artifact, then freeze.",
              file=sys.stderr)
        _record_refusal(root, "freeze", "waiver_unapproved",
                        reason="a waiver no committed approval carries")
        return 1
    manifest["git_sha"] = head_sha(root)
    write_freeze(root, manifest)
    # `reason` carries the git status when no contract note exists, and that
    # conflation is deliberate rather than overlooked. This ledger's `reason` is
    # a free-form "why this entry looks like this" string, not a typed cause:
    # `verify_fail` a few lines down already writes a lock-state token into the
    # same field. Splitting status out would need a new key in `append_history`,
    # which lives in the module the PreToolUse dispatcher loads on every write.
    failed = _record(root, "freeze", digest=manifest["root"],
                     label=manifest["label"],
                     reason=_ledger_reason(contract_note or committed_status,
                                           satisfied))
    if failed:
        # The freeze IS active by the time this can fire, so reporting a plain
        # failure is false of the state twice over. It tells the operator to
        # re-run a command that will now refuse with "a freeze is already
        # active", and it hides the second consequence: `freeze_windows` opens a
        # window on this ledger entry, so with the entry missing `canopus pack`
        # reports every commit made under this lock as made outside it. That is
        # a false red on the page the operator approves from.
        print(f"canopus: the freeze IS ACTIVE over {manifest['root']}; the "
              f"`freeze` ledger entry failed: {failed}. The manifest was "
              f"written, so `verify` and the test gate enforce this contract "
              f"now, and `release --window --reason \"<why>\"` is how you clear "
              f"it. With "
              f"no `freeze` entry in the ledger, `canopus pack` opens no window "
              f"here and will report every commit made under this lock as made "
              f"outside it.",
              file=sys.stderr)
        _record_refusal(root, "freeze", "ledger_write_failed",
                        reason="the freeze was written but its ledger entry failed")
        return 1
    _print_root(manifest["root"], manifest)
    if contract_note:
        print(f"{YELLOW}contract{RESET}  {contract_note} before this freeze")
    if committed_status != COMMITTED:
        print(f"{YELLOW}approval unverified{RESET}  {committed_status}: this "
              f"freeze was taken without a committed approval behind it")
    elif committed_hash != manifest["root"]:
        # The uncommitted-candidate branch above. Loud, because the committed
        # status alone would print nothing here and the operator would be given
        # a lock that `verify` is about to call red with no warning attached.
        print(f"{YELLOW}approval uncommitted{RESET}  the committed approval "
              f"records {committed_hash}; this freeze takes {manifest['root']}, "
              f"which is on the artifact and not committed. `verify` reports "
              f"{LOSS_OF_LOCK} until you commit it.")
    return 0


def cmd_probe(args) -> int:
    """Show what a contract would look like at freeze time. Writes nothing.

    The freeze gate catches an entirely vacuous contract. It cannot catch a
    partly vacuous one, and that is what this table is for: the operator reading
    the Fix 1 gate sees which contract tests are ALREADY GREEN before a line of
    implementation exists, and which are red for no better reason than the code
    being absent. Those are the ones to question.

    This command runs pytest over the contract THREE times: once for real, and
    then twice more with the contract's OWN imports stubbed, each stub carrying a
    DIFFERENT set of values. Two stub runs rather than one is what buys the
    vacuity proof: a test whose outcome does not move when the stubbed value
    moves cannot be reading that value. Nothing can be shared across the
    processes. The contract is a handful of files, so the cost is seconds, but an
    operator wondering why this takes longer than it used to should find the
    answer written down.

    `approve` and `freeze` run the same set when `--contract` names it, with one
    difference: they skip the stub runs once the first has already earned a
    refusal, because a contract that collected nothing cannot be saved by a
    vacuity verdict and should not pay for one.
    """
    root = _resolve_root(args)
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
        # See `_probe_contract`: the real run's triples, so the probe's lost-test
        # guard and this command's verdict weigh ONE population.
        vacuous = run_null_stub(paths, root, expected_population=outcomes)
    except ContractError as exc:
        probe_failed = f"the contract's vacuity could not be measured: {exc}"
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
    reasons = refusal_reasons(counts, outcomes, expected)
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


def cmd_pack(args) -> int:
    """The Fix 2 evidence page. Reports and never blocks; writes ONE ledger line.

    The write is the whole of the ship-evidence change and it is the only one:
    the page itself is still pure reporting, the exit code is still 0 whatever
    the page says, and the single `pack` event exists so `release --ship` can
    require that this page was rendered. It lands at the END of the function,
    past every read, so a fault in the reporting cannot leave a record claiming
    a render that never printed.
    """
    root = _resolve_root(args)
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze; there is no contract to build a pack "
              "around", file=sys.stderr)
        return 1

    report = verify_manifest(manifest, root)
    resolution = resolve_anchor(manifest)
    anchor, status, value = resolution.anchor, resolution.status, resolution.value
    state = lock_state(report, status, value)
    record = read_attestation(root)
    # Sampled ONCE, here, and handed to both readers below. This used to be two
    # separate `tree_state(root)` calls -- one here, feeding the staleness
    # section's fallback reason, and a second inside `_print_attestation` for
    # the banner -- two git walks behind one verdict, and a window between
    # them in which the tree could move and the two readings disagree. See
    # `_print_attestation`'s `current_tree` parameter.
    current_tree = tree_state(root)
    # _print_attestation below already renders the state; only the reason string
    # is needed again, for the staleness section.
    _state, reason = attestation_state(record, report["recomputed_root"], current_tree)

    _print_root(report["recomputed_root"], manifest)
    colour = {LOCK_HELD: GREEN, LOCK_UNCONFIRMED: YELLOW}.get(state, RED)
    print(f"{colour}{BOLD}{state}{RESET}   anchor {anchor or '(none)'} [{status}]")
    _print_attestation(root, report["recomputed_root"], current_tree=current_tree)
    _print_approval(resolution)

    _print_contract(manifest, record)

    # The waiver, said out loud on the page the operator approves from, bound to
    # this freeze's root and read from the COMMITTED artifact where there is one.
    # A freeze taken with the flag whose `approve` did not carry it renders
    # nothing here: the artifact is the record, and there is nothing on it.
    _print_waiver(anchor, manifest["root"])

    # Which interpreter the attestation above speaks for. Beneath the states it
    # qualifies, because it is the evidence behind them rather than a fourth
    # verdict.
    print(render_process((record or {}).get("process"), manifest.get("files") or {}))

    base = args.base or merge_base(root, "main") or "HEAD"
    commits = git_commits(root, base)
    # Read ONCE and handed to both readers below, for the reason `current_tree`
    # above is sampled once: two reads of one append-only file behind one page
    # leave a window in which the second sees a line the first did not, and the
    # continuity section and the evidence record would then describe different
    # ledgers on the same render.
    entries = read_ledger(root)
    outside = commits_outside(commits, freeze_windows(entries))
    print(f"\n{BOLD}continuity{RESET}  {len(commits)} commits since {base}")
    if outside:
        for sha, when, subject in outside:
            print(f"  {RED}outside the lock{RESET}  {sha} {when.isoformat()} {subject}")
    else:
        print(f"  {GREEN}every commit was made while a freeze was held{RESET}")

    attested_at = parse_ts((record or {}).get("attested_at"))
    newer = [c for c in commits if attested_at and c[1] > attested_at]
    dirty = is_dirty(root)
    print(f"\n{BOLD}staleness{RESET}")
    if attested_at is None:
        print(f"  {YELLOW}no attestation to age{RESET}  {reason}")
    elif dirty or newer:
        for sha, _when, subject in newer:
            print(f"  {YELLOW}newer than the attestation{RESET}  {sha} {subject}")
        if dirty:
            print(f"  {YELLOW}the working tree has uncommitted changes{RESET}")
        print("  Re-run the gate: the attestation describes a tree that no "
              "longer exists.")
    else:
        print(f"  {GREEN}the attestation is the freshest artifact here{RESET}")

    # Reuses the `entries` already read for the continuity window above, so the
    # two sections cannot describe different ledgers on the same render -- the
    # same argument the continuity read itself carries.
    #
    # ONE expression on purpose. The first version assigned the rendered text to
    # a local and printed a slice of it, and a mutation that kept the call but
    # dropped the print survived the contract: the section left the page while
    # every test stayed green. With the call inside the `print`, "is it wired"
    # and "does it reach the page" are the same question, and one AST assertion
    # answers both.
    print("\n" + render_friction(count_friction(entries, manifest["label"]),
                                 heading_wrap=(BOLD, RESET)))

    stat = diff_stat(root, base)
    if stat:
        print(f"\n{BOLD}diff{RESET}\n{stat}")

    # Every gap the slice MEASURED belongs here, not only in docs/EXTENDING.md.
    # This is the page an operator signs off from, and render_process argues the
    # same standard three sections above: a page that omits the residual reads as
    # a clean bill of health. Short lines, because a list nobody finishes reading
    # covers nothing either.
    print(f"\n{BOLD}not covered{RESET}")
    # Not "mutation testing has not run". Nothing here records whether it did,
    # and on 2026-08-03 that sentence was printed on the sign-off page of a slice
    # whose mutations had run twice under scripts/utils/mutation_probe.py. A page
    # that states as fact something it cannot observe is worse than one that
    # admits the blind spot: the false version is the one an operator acts on.
    print("  Whether mutation testing ran is not recorded here. This page cannot "
          "tell a contract proven red before the code existed from one also "
          "proven strong; read the slice's gate artifact for any mutation run it "
          "reports.")
    print("  A local record is evidence rather than proof: it lives on the "
          "machine that produced it, and anyone with write access to that "
          "disk can hand-edit it.")
    print("  .canopus/ is itself gitignored, so an edit inside it -- including "
          "hand-writing a tree block into attest.json that already matches the "
          "live git state -- is invisible to the tree check the same way any "
          "other gitignored edit is; only the append-only ledger's own "
          "reasoning catches a doctored freeze.json, and nothing here catches "
          "a doctored attest.json.")
    print("  Gitignored files anywhere in the tree are outside the state this "
          "check examines -- including .git/info/exclude and "
          "core.excludesFile, which exclude the same way a committed "
          "gitignore does but are themselves untracked -- tree_state is "
          "defined relative to git, not to the filesystem, so an edit to "
          "one does not perish the record.")
    print("  Git's index bits -- assume-unchanged and skip-worktree -- are "
          "read directly (git ls-files -v), bypassing git status, so an "
          "edit to a flagged path still perishes the record. Flipping "
          "either bit with no content change is itself read as drift: the "
          "path enters or leaves the state on the FLAG alone, not the "
          "bytes -- always a false positive, never a false negative.")
    print("  A submodule's content is outside the tree state: it hashes to "
          "None, being a directory rather than a file, so the state sees "
          "only that the submodule is dirty, never what it now contains. A "
          "second, different edit inside it reads as no further drift at "
          "all.")
    print("  A root that is not a git working copy cannot attest at all: "
          "tree_state answers None for it, and every run over it refuses on "
          "that ground. Not FIRST, though: build_attestation checks the "
          "frozen test files and the process description before it ever "
          "looks at the tree, and attestation_state (what verify/status/pack "
          "read here) checks the recipe, the root hash, and whether the "
          "attesting run qualified at all before it compares the tree. The "
          "tree comparison is the LAST thing either function looks at.")
    print("  The tree is sampled twice, at collection and at finish. The "
          "sampling window opens at collection, not at process start, so "
          "whatever ran before pytest began collecting is unobserved, and a "
          "file edited and reverted inside the window leaves no trace either.")
    print("  The plugin comparison is by top-level module NAME. A same-named "
          "plugin from another distribution passes: measured, a hostile anyio "
          "earlier on PYTHONPATH takes a red contract to this page's greenest "
          "reading at exit 0.")
    print("  The recorder runs inside the interpreter it describes, so a plugin "
          "already loaded there can rewrite what the interpreter section says.")
    print("  Whatever configured the interpreter BEFORE the recorder existed is "
          "unobserved: PYTHONPATH, a .pth file, sitecustomize. Only PYTEST_ "
          "names are recorded.")
    print("  CANOPUS_NO_ATTEST and CANOPUS_PLUGIN_DUMP each suppress WRITING a "
          "new record; they do not preserve an earlier one. An earlier ATTESTED "
          "record is re-checked against the CURRENT tree on every read, so it "
          "perishes the moment the tree the probe run needed to dirty moves, "
          "with or without a new write.")

    # The render is now recorded, which is what lets `release --ship` require
    # it. This ends `pack` as a pure-report command, deliberately and with the
    # litter risk closed structurally rather than by a flag: the write is
    # reachable only past the `read_freeze` above, so it can land only where a
    # freeze already created `.canopus/`. A `--record` flag was considered and
    # rejected -- a flag nobody passes is the disuse THE LAW describes, and the
    # whole point is that the record is not optional.
    #
    # Idempotent on the state, not on the command. A second render of a state
    # that already has a qualifying record adds a line carrying no new fact, and
    # `pack` is re-runnable by design, so a debugging session would otherwise
    # inflate the ledger the gate reads.
    state, _reason = evidence_state(entries, manifest["root"],
                                    (record or {}).get("attested_at"))
    if state != EVIDENCE_FRESH:
        failed = _record(root, "pack", digest=manifest["root"],
                         label=manifest["label"], reason="evidence page rendered")
        if failed:
            # Loud, because the page above is the operator's evidence and this
            # is only telemetry about it: losing the record must not cost the
            # render, and a silent loss would leave `release --ship` refusing
            # later for a reason nobody was ever told.
            print(f"canopus: this render was not recorded ({failed}); "
                  f"`release --ship` will refuse until `canopus.py pack` "
                  f"records one", file=sys.stderr)
    return 0


def cmd_verify(args) -> int:
    root = _resolve_root(args)
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze; nothing to verify", file=sys.stderr)
        return 1

    report = verify_manifest(manifest, root)
    override = (
        str(validate_anchor_path(_under_root(args.anchor, root), root))
        if args.anchor else None
    )
    resolution = resolve_anchor(manifest, override)
    anchor, status, value = resolution.anchor, resolution.status, resolution.value
    state = lock_state(report, status, value)

    _print_root(report["recomputed_root"], manifest)

    if state == LOCK_HELD:
        print(f"{GREEN}{BOLD}{LOCK_HELD}{RESET}  matches the hash recorded in {anchor}")
        _print_attestation(root, report["recomputed_root"])
        _print_approval(resolution)
        # On every branch, including the green one, and especially the green one:
        # LOCK HELD plus APPROVED over a waived contract is the strongest-looking
        # output this tool produces, and it is the one reading that most needs the
        # qualifier.
        _print_waiver(anchor, manifest["root"])
        return 0
    if state == LOCK_UNCONFIRMED:
        # The reason comes from the one producer of the precedence decision. An
        # earlier revision re-derived its own sentence here ("carries no
        # canopus-anchor line yet"), which is plainly false under the committed
        # mapping for an artifact whose working copy carries one.
        detail = ("no anchor was recorded at freeze time" if status == ANCHOR_NONE
                  else resolution.approval_reason)
        print(f"{YELLOW}{BOLD}{LOCK_UNCONFIRMED}{RESET}  {detail}. Nothing changed "
              f"since the last check, which is NOT the same as 'this is the "
              f"approved contract'.")
        _print_attestation(root, report["recomputed_root"])
        _print_approval(resolution)
        _print_waiver(anchor, manifest["root"])
        return 0

    print(f"{RED}{BOLD}{LOSS_OF_LOCK}{RESET}")
    for rel in report["changed"]:
        print(f"  changed  {rel}\n           expected {manifest['files'][rel]}")
    for rel in report["added"]:
        print(f"  added    {rel}")
    for rel in report["removed"]:
        print(f"  removed  {rel}")
    if status == ANCHOR_UNBOUND:
        # The path and the STATE, not the reason. `_print_approval` a few lines
        # below already prints the reason on the approval axis, because the
        # binding sets approval_reason as well, and the same sentence twice in
        # one report teaches an operator to skim the second one.
        print(f"  anchor   {anchor} [{ANCHOR_UNBOUND}]")
    if status == ANCHOR_MISSING:
        print(f"  anchor   {anchor} is gone")
    elif status == ANCHOR_RECORDED and value != report["recomputed_root"]:
        # The path is a working-tree path and the hash may have come from HEAD,
        # so say which copy was read. Without it an operator opens that file,
        # finds a different hash, and has no explanation for the difference.
        origin = f" ({resolution.approval})" if resolution.source == COMMITTED else ""
        print(f"  anchor   {anchor} records {value}{origin}")
    _print_attestation(root, report["recomputed_root"])
    _print_approval(resolution)
    _print_waiver(anchor, manifest["root"])
    print("A contract that is genuinely wrong reopens the approval gate. "
          "It is never edited in place.")
    failed = _record(root, "verify_fail", digest=report["recomputed_root"],
                     label=manifest["label"], reason=state)
    if failed:
        # The exit code is already 1 and the per-file report is already printed,
        # so the only thing at stake is the sentence. Unguarded, the last word an
        # operator reads on a genuinely broken lock is "the frozen contract could
        # not be read", which invites the reading that the lock state above was
        # never established.
        print(f"canopus: the {state} above stands and was recomputed from disk; "
              f"only the `verify_fail` ledger entry failed: {failed}.",
              file=sys.stderr)
    return 1


def cmd_release(args) -> int:
    root = _resolve_root(args)
    if args.force:
        # Never parses the manifest: this is the escape FROM an unparseable one.
        failed = _record(root, "force_release", digest="", label="",
                         reason=args.reason, kind=args.kind)
        if failed:
            return _unlogged_release(failed)
        clear_freeze(root)
        print("Force-released and logged. A forced release is a normal, recorded "
              "event; deleting the file by hand is not.")
        return 0
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze to release", file=sys.stderr)
        _record_refusal(root, "release", "no_active_freeze",
                        reason="no active freeze to release")
        return 1

    # Step 13 is the machine-observable successor of the operator's second
    # approval, so it is the only place the evidence page can be required. A
    # window is not an approval -- it is the way BACK into the build -- and
    # gating it would demand an evidence page for work that is not finished.
    #
    # This runs BEFORE the ledger append below, so a refused ship leaves the
    # freeze standing: a refusal that also ended the lock would leave the slice
    # neither shipped nor protected.
    if args.kind == "ship":
        record = read_attestation(root)

        # The FIRST of the two, because it is the more fundamental failure and
        # because clearing it invalidates the render anyway: re-running the gate
        # moves `attested_at` forward, which makes any earlier page stale. Asking
        # about the page first would send the operator to `pack`, then to the
        # gate, then back to `pack`.
        #
        # Judged from the tree sample rather than from the reason text: a root
        # that cannot be described is a fault, not haste, and refusing there
        # would be a wall no slice outside a git working copy could pass.
        current_tree = tree_state(root)
        att_state, att_reason = attestation_state(
            record, verify_manifest(manifest, root)["recomputed_root"], current_tree
        )
        stale = attestation_refusal(att_state, att_reason,
                                    judgeable=current_tree is not None)
        if stale:
            print(f"canopus: NOT SHIPPED - {stale}", file=sys.stderr)
            _record_refusal(root, "release", "attestation_perished",
                            reason=stale, label=manifest["label"])
            return 1

        state, reason = evidence_state(
            read_ledger(root), manifest["root"],
            (record or {}).get("attested_at"),
        )
        if state == EVIDENCE_MISSING:
            print(f"canopus: NOT SHIPPED - {reason}", file=sys.stderr)
            _record_refusal(root, "release", "evidence_missing", reason=reason,
                            label=manifest["label"])
            return 1
        if state == EVIDENCE_UNVERIFIABLE:
            print(f"canopus: {reason}", file=sys.stderr)

    failed = _record(root, "release", digest=manifest["root"],
                     label=manifest["label"], reason=args.reason,
                     kind=args.kind)
    if failed:
        return _unlogged_release(failed)
    clear_freeze(root)
    print(f"Released {manifest['root']} (label: {manifest['label']})")
    return 0


def cmd_status(args) -> int:
    root = _resolve_root(args)
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze")
        _print_dormant_lock(root)
        return 0
    report = verify_manifest(manifest, root)
    resolution = resolve_anchor(manifest)
    anchor, status, value = resolution.anchor, resolution.status, resolution.value
    state = lock_state(report, status, value)
    _print_root(manifest["root"], manifest)
    # The lock line, not just the attestation line. An earlier revision paid for
    # the full verify here and then printed only the manifest's STORED root, so
    # `status` on a moved contract was byte-identical to `status` on an intact
    # one -- the operator was told the lock was on while it was broken, which is
    # the one failure this whole tool exists to prevent. Reporting only: the
    # exit code stays 0 so `status` remains a description of state, and `verify`
    # remains the command that fails.
    colour = {LOCK_HELD: GREEN, LOCK_UNCONFIRMED: YELLOW}.get(state, RED)
    # The CAUSES, from the one function that enumerates them, not a fixed
    # sentence. `lock_state` reaches red from four causes and three of them leave
    # the contract exactly where it was, so "run `canopus verify` for the
    # per-file report" sent an operator whose anchor repository had gone missing
    # to a report that lists nothing. Measured: with the anchor's `.git` renamed,
    # `status` printed that line while nothing on the tree had moved.
    # `loss_of_lock_sentences` was written for this and was applied to the gate
    # alone, so this surface kept the fixed sentence: a guard repaired on one
    # sibling and not the other.
    tail = ("  " + " ".join(loss_of_lock_sentences(report, resolution))
            if state == LOSS_OF_LOCK else "")
    print(f"{colour}{BOLD}{state}{RESET}{tail}")
    _print_attestation(root, report["recomputed_root"])
    _print_approval(resolution)
    _print_waiver(anchor, manifest["root"])
    print(f"frozen at {manifest['frozen_at']}")
    print(f"git sha   {manifest.get('git_sha') or '(not a git working tree)'}")
    print(f"anchor    {anchor or '(none)'}  [{status}]")
    for rel in manifest["files"]:
        print(f"  file  {rel}")
    for rel, entry in manifest["dirs"].items():
        # The filter is printed because it is the guard's actual scope. A line
        # reading "dir tests/" without it invites the reading that everything
        # under tests/ is watched, which is the opposite of true. The directory
        # half is printed for the same reason in the other direction: from wire
        # 2.3 the tree-root guard also measures importable subdirectories, and a
        # line reading "watching *.py" alone UNDER-states it.
        watching = " ".join(entry["names"])
        if guard_watches_directories(entry["names"]):
            watching += " + importable directories"
        print(f"  dir   {rel or '.'}/  ({entry['mode']}, watching {watching})")
    _print_contract(manifest, read_attestation(root))
    return 0


# ============================================================
# where -- the orientation page
# ============================================================

def _position(root: Path) -> dict:
    """Read the disk, then hand the ladder to `canopus_steps.position`.

    Split deliberately: this half touches the filesystem and is awkward to test,
    the ladder itself is pure and is tested directly. A damaged manifest is NOT
    handled here -- `read_freeze` raises FreezeCorrupt and `main` reports it.
    Reporting a position over state that cannot be parsed would be the same lie
    by a different route.
    """
    manifest = read_freeze(root)
    if manifest is None:
        return dict(position(label=None, attested=False), lock=None)

    report = verify_manifest(manifest, root)
    resolution = resolve_anchor(manifest)
    lock = lock_state(report, resolution.status, resolution.value)
    attested, _reason = attestation_state(
        read_attestation(root), report["recomputed_root"], tree_state(root))
    return dict(position(label=manifest["label"], attested=attested == ATTESTED),
                lock=lock)


def _agenda() -> list:
    return [
        {"number": s["number"], "act": s["act"], "name": s["name"],
         "what": s["what"], "approval": s["approval"],
         "machine_visible": s["machine_visible"]}
        for s in STEPS
    ]


def _where_payload(root: Path) -> dict:
    place = _position(root)
    number = place["number"]
    current = step(number)
    following = step(number + 1)
    here = act_of(number) if current else act(1)
    return {
        "slice": place["slice"],
        "step": number,
        "step_name": current["name"] if current else None,
        "act": {"number": here["number"], "name": here["name"],
                "steps": list(here["steps"]), "note": here["note"]},
        "next": ({"number": following["number"], "name": following["name"],
                  "what": following["what"]} if following else None),
        "derived": place["derived"],
        "basis": place["basis"],
        "lock": place["lock"],
        "agenda": _agenda(),
    }


def _wrap(text: str, indent: str) -> str:
    return textwrap.fill(text, width=88, initial_indent=indent,
                         subsequent_indent=indent)


def _print_agenda(current: int) -> None:
    print(f"\n{BOLD}The thirteen moments{RESET}")
    for entry in ACTS:
        first, last = entry["steps"]
        span = f"step {first}" if first == last else f"steps {first}-{last}"
        print(f"\n  {BOLD}Act {entry['number']} - {entry['name']}{RESET}  "
              f"({span}: {entry['note']})")
        for moment in STEPS:
            if not first <= moment["number"] <= last:
                continue
            here = moment["number"] == current
            mark = f"{GREEN}>{RESET}" if here else " "
            # The approvals are marked in the agenda itself, not only in a
            # legend. They are the two moments the operator must recognise as
            # his own while reading past them.
            owner = f"  {YELLOW}[yours]{RESET}" if moment["approval"] else ""
            label = f"{BOLD}{moment['name']}{RESET}" if here else moment["name"]
            print(f"  {mark} {moment['number']:>2}. {label}{owner}")
            print(_wrap(moment["what"], " " * 9))


def cmd_where(args) -> int:
    """The bare orientation page: where you are, and the whole agenda.

    Deliberately not a status line. `status` answers "is the lock intact"; this
    answers "what is this process and where am I in it", which is the question
    an operator has when he has been away from it for a week.
    """
    root = _resolve_root(args)
    payload = _where_payload(root)
    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2))
        return 0

    # Keyed off the STEP, never off the truthiness of the label. A freeze
    # carrying an empty label made the header read "no slice open" three lines
    # above "Step 8 of 13", and a position display that contradicts itself is
    # the exact defect this command exists to prevent. Found at step 11.
    where = "no slice open" if payload["step"] == NO_SLICE else (
        payload["slice"] or "(unnamed slice)")
    print(f"{BOLD}CANOPUS{RESET}  {where}")
    if payload["step"] == NO_SLICE:
        print(f"\n  Nothing is open. The process has {len(STEPS)} moments and "
              f"the first one is where it starts.")
    else:
        act_line = f"act {payload['act']['number']}, {payload['act']['name']}"
        print(f"\n  Step {payload['step']} of {len(STEPS)} - "
              f"{BOLD}{payload['step_name']}{RESET}   ({act_line})")
        done = step(payload["step"] - 1)
        if done:
            print(f"  Just finished: {done['number']}. {done['name']}")
    if payload["next"]:
        print(f"  Next: {payload['next']['number']}. {payload['next']['name']}")
    if payload["lock"] and payload["lock"] != LOCK_HELD:
        colour = YELLOW if payload["lock"] == LOCK_UNCONFIRMED else RED
        print(f"  {colour}{payload['lock']}{RESET}  "
              f"run `canopus verify` for the per-file report")

    tag = "inferred" if payload["derived"] else "observed"
    print(f"\n  How this was worked out ({tag}):")
    print(_wrap(payload["basis"], "  "))
    _print_agenda(payload["step"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canopus",
        description="Freeze the test contract so the builder cannot move the target.",
    )
    parser.add_argument("--root", default=str(ENGINE_ROOT),
                        help="working tree root (default: this script's own repository "
                             "root, NOT the shell's cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="lock a set of paths")
    freeze.add_argument("paths", nargs="*", default=[],
                        help="files or directories to freeze recursively")
    freeze.add_argument("--content", action="append", default=[], metavar="FILE",
                        dest="content",
                        help="freeze this file's BYTES only, with no composition guard "
                             "on its parent directory (use for the enforcer files)")
    freeze.add_argument("--contract", action="append", default=[], metavar="DIR",
                        dest="contract",
                        help="freeze this contract directory recursively, record a "
                             "per-file collected-item baseline, and refuse the "
                             "freeze unless the contract is red")
    freeze.add_argument("--contract-satisfied", default="", metavar="REASON",
                        dest="contract_satisfied",
                        help="accept a contract that is wholly green, for the "
                             "stated REASON. Waives ONLY the redness refusal; a "
                             "file that collected nothing and a vacuous contract "
                             "are still refused. Use on a RETAKE whose contract "
                             "the slice has already implemented.")
    freeze.add_argument("--label", required=True, help="short name for this build")
    # Required, because an anchorless freeze can only ever report LOCK
    # UNCONFIRMED. It still catches a later edit, but it is the one route to a
    # PASSING gate that never leaves the engine clone: release, edit the
    # contract, re-freeze, amber, exit 0. With an anchor that same sequence
    # fails, because the artifact still holds the previously approved hash.
    # Re-baselining is exactly what the anchor exists to make visible.
    freeze.add_argument("--anchor", required=True,
                        help="committed artifact OUTSIDE this tree that records the root hash")
    freeze.set_defaults(func=cmd_freeze)

    approve = sub.add_parser(
        "approve",
        help="record the candidate root hash for a human to commit; freezes nothing",
    )
    approve.add_argument("paths", nargs="*", help="paths to be frozen positionally")
    approve.add_argument("--label", required=True, help="what this contract is for")
    approve.add_argument("--anchor", required=True,
                         help="the gate artifact, outside the working tree")
    approve.add_argument("--content", action="append", default=[],
                         help="freeze this file's bytes only")
    approve.add_argument("--contract", action="append", default=[],
                         help="a contract directory: recursive, with a baseline")
    approve.add_argument("--contract-satisfied", default="", metavar="REASON",
                         dest="contract_satisfied",
                         help="accept a contract that is wholly green, for the "
                              "stated REASON. Waives ONLY the redness refusal; "
                              "a file that collected nothing and a vacuous "
                              "contract are still refused.")
    approve.add_argument("--replace", action="store_true",
                         help="append a new approval over a recorded one")
    approve.add_argument("--reason", default="",
                         help="why the approval is being replaced")
    approve.add_argument("--cause", default="", metavar="CAUSE",
                         help="the CLASS of the retake, from the closed set in "
                              "scripts/utils/gate_yield.RETAKE_CAUSES. Required "
                              "with --replace: the prose reason says what "
                              "happened this once, the cause is what makes the "
                              "standard's largest output countable.")
    approve.set_defaults(func=cmd_approve)

    probe = sub.add_parser("probe", help="run a contract set and show what a "
                                         "freeze would record; writes nothing")
    probe.add_argument("paths", nargs="+", help="contract files or directories")
    probe.set_defaults(func=cmd_probe)

    pack = sub.add_parser("pack", help="the Fix 2 evidence page")
    pack.add_argument("--base", default=None,
                      help="branch base for the commit range (default: the merge "
                           "base with main)")
    pack.set_defaults(func=cmd_pack)

    verify = sub.add_parser("verify", help="recompute and compare against the anchor")
    verify.add_argument("--anchor", default=None,
                        help="override the anchor path recorded in the manifest")
    verify.set_defaults(func=cmd_verify)

    release = sub.add_parser("release", help="clear the active freeze")
    release.add_argument("--reason", default="", help="why the freeze is being released")
    release.add_argument("--force", action="store_true",
                         help="clear a damaged manifest without parsing it; the "
                              "forced release is LOGGED to the ledger, which is "
                              "what tells it apart from deleting freeze.json by "
                              "hand")
    # Required, and mutually exclusive. Two releases that look identical in the
    # ledger are two different events: one you will close, and the end of a
    # slice. The tool used to record the difference only in free-form prose, so
    # nothing could act on it.
    kind = release.add_mutually_exclusive_group(required=True)
    kind.add_argument("--window", dest="kind", action="store_const", const="window",
                      help="the lock will be taken again; the slice is still in progress")
    kind.add_argument("--ship", dest="kind", action="store_const", const="ship",
                      help="the slice is over")
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="show the active freeze")
    status.set_defaults(func=cmd_status)

    where = sub.add_parser("where", help="where you are in the process, and the "
                                         "whole agenda")
    where.add_argument("--json", dest="as_json", action="store_true",
                       help="the position and agenda as JSON")
    where.set_defaults(func=cmd_where)
    return parser


def _record_raised(args, cause: str, exc: Exception) -> None:
    """Record a refusal that RAISED rather than returned.

    Half the lifecycle's refusals never reach a `return 1`: an anchor that is not
    a file, a contract that is not red, a damaged manifest all raise and land in
    `main`'s handlers. Counting only the returns would have measured half the
    yield and called it the yield, which is the failure this slice exists to end.

    `main` is the one funnel every raising refusal passes through, so this is one
    call site rather than a guard per raise, and a raise added later is counted
    without its author doing anything.
    """
    command = getattr(args, "command", "") or ""
    if command not in ("approve", "freeze", "release"):
        return
    _record_refusal(Path(getattr(args, "root", ".")), command, cause,
                    reason=str(exc))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FreezeCorrupt as exc:
        _record_raised(args, "freeze_corrupt", exc)
        print(f"canopus: {exc}\n"
              f"         Every write is denied while the manifest is damaged. "
              f"Clear it with: python scripts/canopus.py release --force "
              f"--window --reason \"<why>\"",
              file=sys.stderr)
        return 1
    except FreezeError as exc:
        _record_raised(args, "freeze_error", exc)
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    except ContractError as exc:
        _record_raised(args, "contract_error", exc)
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        _record_raised(args, "unreadable", exc)
        # Same posture as the test gate: an unreadable member (permissions, a
        # vanished mount) fails the command, it does not traceback. The exit
        # code already failed closed; the layer billed as the guarantee should
        # not present a raw stack trace while doing so.
        print(f"canopus: the frozen contract could not be read, so it cannot be "
              f"verified: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
