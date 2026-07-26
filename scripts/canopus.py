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
    python scripts/canopus.py release --reason "slice shipped"
    python scripts/canopus.py release --force --reason "manifest damaged"

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
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))
from scripts.utils.canopus_contract import (  # noqa: E402
    RED_OUTCOMES,
    ContractError,
    contract_files,
    missing_modules,
    parse_failure_modes,
    parse_junit,
    refusal_reasons,
    run_null_stub,
    run_pytest_report,
    vacuity_refusal,
    vacuity_unmeasured,
)
from scripts.utils.canopus_freeze import (  # noqa: E402
    ANCHOR_MISSING,
    ANCHOR_NONE,
    ANCHOR_PREFIX,
    ANCHOR_RECORDED,
    ANCHOR_UNBOUND,
    APPROVED,
    ATTESTED,
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    NOT_ATTESTED,
    REPO_PRESENT,
    FreezeCorrupt,
    FreezeError,
    append_history,
    attestation_state,
    build_manifest,
    clear_freeze,
    lock_state,
    read_anchor,
    read_attestation,
    read_freeze,
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
)
from scripts.utils.canopus_pack import (  # noqa: E402
    commits_outside,
    diff_stat,
    freeze_windows,
    git_commits,
    is_dirty,
    merge_base,
    parse_ts,
    read_ledger,
)
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
            reason: str = "") -> str:
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
        append_history(root, event, digest=digest, label=label, reason=reason)
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


def _print_attestation(root: Path, recomputed_root: str) -> None:
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
    """
    record = read_attestation(root)
    state, reason = attestation_state(record, recomputed_root)
    if state == ATTESTED:
        counts = [
            entry for entry in (record.get("frozen_tests") or {}).values()
            if isinstance(entry, dict)
        ]
        passed = sum(entry.get("passed", 0) for entry in counts)
        skipped = sum(entry.get("skipped", 0) for entry in counts)
        tail = f", {skipped} skipped" if skipped else ""
        print(f"{GREEN}{BOLD}{ATTESTED}{RESET}  {passed} frozen tests passed, none "
              f"deselected, at "
              f"{record.get('attested_at') or 'an unrecorded time'}{tail}")
        return
    print(f"{YELLOW}{BOLD}{NOT_ATTESTED}{RESET}  {reason}")
    listed = (record or {}).get("reasons")
    if isinstance(listed, list):
        for line in listed[:5]:
            print(f"  reason   {line}")


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


def _candidate_manifest(args, root: Path, anchor_path: Path):
    """(manifest, contract_note) for approve and freeze, or (None, "") on refusal.

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
        return (None, "")
    contracts = [_under_root(p, root) for p in args.contract]
    baseline: dict[str, int] = {}
    contract_note = ""
    if contracts:
        expected = contract_files(contracts, root)
        if not expected:
            print("canopus: --contract names no test modules; a contract with no "
                  "tests can never be attested", file=sys.stderr)
            return (None, "")
        # One real run, read twice. Running the contract for the outcomes and
        # again for the report would double the wall time and compare outcomes
        # from one run against failure modes from another.
        xml_text = run_pytest_report(contracts, root)
        counts, outcomes = parse_junit(xml_text)
        modules = missing_modules(xml_text)
        reasons = refusal_reasons(counts, outcomes, expected)
        # The null stub is a WHOLE second pytest session, and it can only ever
        # add to a refusal this contract has already earned. Running it anyway
        # made a contract that collected nothing pay for a run whose answer was
        # discarded a line later.
        if not reasons:
            reasons.extend(
                vacuity_refusal(outcomes, run_null_stub(contracts, root, modules))
            )
        if reasons:
            print("canopus: the contract was refused:", file=sys.stderr)
            for reason in reasons:
                print(f"  {reason}", file=sys.stderr)
            return (None, "")
        # Said out loud, on the way to a successful approve or freeze. The
        # refusal above cannot fire when nothing was stubbed, and an operator
        # who is not told so reads that silence as "measured, nothing vacuous".
        unmeasured = vacuity_unmeasured(outcomes, modules)
        if unmeasured:
            print(f"{YELLOW}vacuity{RESET}  {unmeasured}")
        baseline = {rel: counts[rel] for rel in expected}
        already_green = sum(
            1 for _rel, _name, outcome in outcomes if outcome == "passed"
        )
        contract_note = f"{already_green} of {sum(counts.values())} already green"
    repo_status, repo_identity_digest = repo_identity(anchor_path.parent)
    if repo_status == REPO_PRESENT and not repo_identity_digest:
        print("canopus: the anchor's repository has no commits, so an approval "
              "cannot be attributed to it and the identity recorded now would "
              "change the moment you commit. Commit something in that "
              "repository first.", file=sys.stderr)
        return (None, "")
    binding = {"in_repo": repo_status == REPO_PRESENT,
               "identity": repo_identity_digest}
    manifest = build_manifest(
        [_under_root(p, root) for p in args.paths] + contracts,
        root,
        label=args.label,
        frozen_at=datetime.now(timezone.utc).isoformat(),
        anchor=anchor_path,
        content_only=[_under_root(p, root) for p in args.content],
        baseline=baseline,
        anchor_repo=binding,
    )
    return (manifest, contract_note)


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
    # and `release --force --reason` is the logged escape. Named here because a
    # behaviour that arrives as a side effect of one guard is the kind nobody
    # remembers is there.
    if read_freeze(root) is not None:
        # Measured, not imagined: approve set A, commit, freeze set A, verify
        # reads LOCK HELD; then approve set B while that freeze is still active
        # and commit it, exactly as this command's own closing line instructs,
        # and verify reads LOSS OF LOCK with nothing on the tree having moved.
        # `cmd_freeze` already refuses for the same reason, and the docstring of
        # _candidate_manifest names this failure as the one to avoid.
        print("canopus: a freeze is already active; run `release` first. "
              "Approving a different set now turns the held lock red the moment "
              "you commit it, and nothing on the tree will have moved.",
              file=sys.stderr)
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
        return 1
    if args.replace and not args.reason:
        print("canopus: --replace requires --reason; an unexplained replacement "
              "is indistinguishable from a re-baseline", file=sys.stderr)
        return 1
    manifest, contract_note = _candidate_manifest(args, root, anchor_path)
    if manifest is None:
        return 1
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
    reason_line = " ".join((args.reason or "").split())
    with anchor_path.open("a", encoding="utf-8") as handle:
        if reason_line:
            handle.write(f"\n{REASON_PREFIX} {reason_line}\n"
                         f"{ANCHOR_PREFIX} {manifest['root']}\n")
        else:
            handle.write(f"\n{ANCHOR_PREFIX} {manifest['root']}\n")
    logged = ""
    try:
        append_history(root, "approve", digest=manifest["root"],
                       label=args.label, reason=args.reason or "")
        logged = "approve"
        if already:
            append_history(root, "anchor_replaced", digest=manifest["root"],
                           label=args.label, reason=args.reason)
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
    """
    root = _resolve_root(args)
    if read_freeze(root) is not None:
        print("canopus: a freeze is already active; run `release` first "
              "(changing a contract reopens the approval gate)", file=sys.stderr)
        return 1
    anchor_path = validate_anchor_path(_under_root(args.anchor, root), root)
    manifest, contract_note = _candidate_manifest(args, root, anchor_path)
    if manifest is None:
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
                     reason=contract_note or committed_status)
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
              f"now, and `release --reason \"<why>\"` is how you clear it. With "
              f"no `freeze` entry in the ledger, `canopus pack` opens no window "
              f"here and will report every commit made under this lock as made "
              f"outside it.",
              file=sys.stderr)
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

    This command runs pytest over the contract TWICE, once for real and once
    with every absent module mocked, and the second run is what buys the vacuity
    proof. Nothing can be shared across the two processes. The contract is a
    handful of files, so the cost is seconds, but an operator wondering why this
    takes longer than it used to should find the answer written down.

    `approve` and `freeze` run the same pair when `--contract` names the set,
    with one difference: they skip the second run once the first has already
    earned a refusal, because a contract that collected nothing cannot be saved
    by a vacuity verdict and should not pay for one.
    """
    root = _resolve_root(args)
    paths = [_under_root(p, root) for p in args.paths]
    expected = contract_files(paths, root)
    if not expected:
        print("canopus: no test modules found under those paths", file=sys.stderr)
        return 1
    xml_text = run_pytest_report(paths, root)
    counts, outcomes = parse_junit(xml_text)
    modules = missing_modules(xml_text)
    vacuous = run_null_stub(paths, root, modules)
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
            note = "  did not run, so it proves nothing" if outcome == "skipped" else ""
            print(f"  {colour}{outcome:8}{RESET} {name}{mode}{note}")
    unmeasured = vacuity_unmeasured(outcomes, modules)
    if unmeasured:
        print(f"{YELLOW}vacuity{RESET}  {unmeasured}")
    reasons = refusal_reasons(counts, outcomes, expected)
    reasons.extend(vacuity_refusal(outcomes, vacuous))
    for reason in reasons:
        print(f"{YELLOW}would be refused:{RESET} {reason}")
    return 1 if reasons else 0


def cmd_pack(args) -> int:
    """The Fix 2 evidence page. Reports; never blocks and never writes."""
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
    # _print_attestation below already renders the state; only the reason string
    # is needed again, for the staleness section.
    _state, reason = attestation_state(record, report["recomputed_root"])

    _print_root(report["recomputed_root"], manifest)
    colour = {LOCK_HELD: GREEN, LOCK_UNCONFIRMED: YELLOW}.get(state, RED)
    print(f"{colour}{BOLD}{state}{RESET}   anchor {anchor or '(none)'} [{status}]")
    _print_attestation(root, report["recomputed_root"])
    _print_approval(resolution)

    baseline = manifest.get("baseline") or {}
    if baseline:
        print(f"\n{BOLD}contract{RESET}")
        counts = (record or {}).get("frozen_tests") or {}
        for rel, expected in sorted(baseline.items()):
            entry = counts.get(rel) if isinstance(counts, dict) else None
            got = entry.get("collected", 0) if isinstance(entry, dict) else 0
            mark = GREEN if got == expected else YELLOW
            print(f"  {mark}{got} of {expected}{RESET}  {rel}")

    base = args.base or merge_base(root, "main") or "HEAD"
    commits = git_commits(root, base)
    outside = commits_outside(commits, freeze_windows(read_ledger(root)))
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

    stat = diff_stat(root, base)
    if stat:
        print(f"\n{BOLD}diff{RESET}\n{stat}")

    print(f"\n{BOLD}not covered{RESET}")
    print("  Mutation testing has not run; the contract is proven to be red "
          "before the code existed, not proven to be strong.")
    print("  .canopus/ is gitignored, so this ledger is evidence against an EDIT, "
          "not against deleting the directory.")
    print("  Staleness is a snapshot taken now, not a continuous property.")
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
                         reason=args.reason)
        if failed:
            return _unlogged_release(failed)
        clear_freeze(root)
        print("Force-released and logged. A forced release is a normal, recorded "
              "event; deleting the file by hand is not.")
        return 0
    manifest = read_freeze(root)
    if manifest is None:
        print("canopus: no active freeze to release", file=sys.stderr)
        return 1
    failed = _record(root, "release", digest=manifest["root"],
                     label=manifest["label"], reason=args.reason)
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
    tail = ("  run `canopus verify` for the per-file report"
            if state == LOSS_OF_LOCK else "")
    print(f"{colour}{BOLD}{state}{RESET}{tail}")
    _print_attestation(root, report["recomputed_root"])
    _print_approval(resolution)
    print(f"frozen at {manifest['frozen_at']}")
    print(f"git sha   {manifest.get('git_sha') or '(not a git working tree)'}")
    print(f"anchor    {anchor or '(none)'}  [{status}]")
    for rel in manifest["files"]:
        print(f"  file  {rel}")
    for rel, entry in manifest["dirs"].items():
        # The filter is printed because it is the guard's actual scope. A line
        # reading "dir tests/" without it invites the reading that everything
        # under tests/ is watched, which is the opposite of true.
        watching = " ".join(entry["names"])
        print(f"  dir   {rel or '.'}/  ({entry['mode']}, watching {watching})")
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
    approve.add_argument("--replace", action="store_true",
                         help="append a new approval over a recorded one")
    approve.add_argument("--reason", default="",
                         help="why the approval is being replaced")
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
                         help="clear a damaged manifest without parsing it; logged")
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="show the active freeze")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FreezeCorrupt as exc:
        print(f"canopus: {exc}\n"
              f"         Every write is denied while the manifest is damaged. "
              f"Clear it with: python scripts/canopus.py release --force "
              f"--reason \"<why>\"",
              file=sys.stderr)
        return 1
    except FreezeError as exc:
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    except ContractError as exc:
        print(f"canopus: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Same posture as the test gate: an unreadable member (permissions, a
        # vanished mount) fails the command, it does not traceback. The exit
        # code already failed closed; the layer billed as the guarantee should
        # not present a raw stack trace while doing so.
        print(f"canopus: the frozen contract could not be read, so it cannot be "
              f"verified: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
