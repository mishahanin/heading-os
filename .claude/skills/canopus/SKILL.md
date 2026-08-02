---
name: canopus
description: >
  The engineering standard for building anything non-trivial in this workspace: thirteen
  numbered moments in four acts, with the operator's two approvals as steps 6 and 12. Bare
  `/canopus` prints where the current slice is and the whole agenda. Subcommands drive the
  lifecycle: `plan` runs the planning gate (success criteria, contrarian critique, harness
  audit, the real test contract), `lock` freezes that contract so it cannot move under the
  code, `check` reports the machine's verdict and the state of the lock, `release` closes a
  shipped slice, `back` opens a release window mid-build and names the way back. Use for any
  change worth a plan. Skip for typo fixes, config-only edits, and one-line corrections.
argument-hint: "[plan | lock | check | release | back]  (bare = where am I)"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(git:*), Skill"
disable-model-invocation: true
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: [".canopus/"]
  triggers: []
x-heading-routing:
  category: Operations
  triggers: ["NEVER auto-trigger. Explicit `/canopus [plan | lock | check | release | back]` only. Bare `/canopus` prints the current position and the full agenda."]
  exclusions: ["N/A"]
  compound: "No"
  router: manual
  label: "/canopus [plan \\| lock \\| check \\| release \\| back]"
x-heading-capability:
  what: >
    The workspace's engineering standard as a command. Prints where a slice is in its
    thirteen moments, and drives the lifecycle that freezes a test contract before the
    code exists so the target cannot move under the builder.
  how: >
    Bare `/canopus` for the orientation page. `/canopus plan` to run the planning gate,
    `lock` to freeze the contract, `check` for the machine's verdict, `release` to close a
    shipped slice, `back` to open a release window mid-build. State lives in `.canopus/`;
    gate artifacts land in the plans directory of the data overlay.
  when: >
    Use for any change worth a plan. Do NOT use for typo fixes or config-only edits, and
    do NOT use it to review finished work: that is `/scrutinize`.
---

# Canopus

The standard for building anything here. Thirteen moments, four acts, and two of
those moments are the operator's own.

**The one thing this exists to prevent:** a test contract that moves under the
code. Everything else is scaffolding around that.

## Bare `/canopus` — where am I

Run this first, always, and show the operator its output verbatim:

```
python scripts/canopus.py where
```

It prints the current step out of thirteen, which act it belongs to, what was
just finished, what comes next, HOW that position was worked out, and the full
agenda. Never summarise it into a status line. Only six of the thirteen leave a
durable trace, so the command says plainly when it is inferring, and that
admission is the point.

`--json` for the same thing as data.

## The thirteen moments

The agenda has exactly one definition, `scripts/utils/canopus_steps.py`. This
file may summarise it and may never renumber it.

**Act 1 — Decide (1 to 6, nothing is built yet).** Say what we want; decide what
to build; write the plan; write the test that decides; try to break the plan;
**Approval 1 (step 6)**.

**Act 2 — Build (7 to 9, no human inside).** Lock the test; write the code; the
machine checks it.

**Act 3 — Check (10 to 12, green is not the same as right).** Check it is what we
wanted; try to break it; **Approval 2 (step 12)**.

**Act 4 — Release (13).** Release it, with the undo named in advance.

Acts 1 and 3 end on the operator's step. Act 2 is the only act with no human in
it, which is exactly why the contract is locked before it starts.

## NEVER

- **NEVER take Approval 1 (step 6) or Approval 2 (step 12).** They are the
  operator's, and nothing in this skill may approve on his behalf. A standard
  with two approvals that the assistant can grant itself is a standard with
  zero. Present the evidence and stop.
- NEVER present a summary in place of evidence at step 12. Name what the test
  does NOT cover.
- NEVER edit a frozen contract to make it pass. The way out is `back`.
- NEVER pass `--no-verify`, and never weaken a guard to get a commit through.
- NEVER delete anything without the operator's explicit word.

## `/canopus plan` — steps 1 to 6

Read `references/planning-gate.md` in full and follow it. It is the former
`/pre-impl` gate, moved rather than rewritten, and it carries the six phases:
context, success criteria, the inline contrarian critique, the optional
architecture council, the harness audit, and the test contract as REAL FILES.

Four things in it decide whether the slice is worth anything:

1. **The contract is real test files**, written to
   `tests/contract/{YYYY-MM-DD}-{slug}/`, importing the code under test INSIDE
   each test body. A prose contract and the tests that later decide whether the
   work is done are two artifacts joined by good intentions.
2. **Vacuity is measured, not assumed.** `python scripts/canopus.py probe
   tests/contract/{date}-{slug}/` null-stubs the missing modules and classifies
   every test. A test that ERRORS against the stub is vacuous: an outcome
   invariant to the stub proves nothing. Report the table. A contract whose every
   red test is vacuous is refused at approve and freeze time, and `vacuity was
   NOT measured` is not a clean bill.

3. **A fixture must produce the shape the real source produces.** Check every
   fabricated input against a real sample before freezing. Measured 2026-08-02:
   a 28-test contract missed that a report could not parse its own log's
   timestamps, because every fixture in it stamped a format no real record has
   ever carried. Green, and proving nothing about the real shape.
4. **Every criterion is bound to a test, and the machine checks it.** Each
   success criterion from Phase 1 is named in the DOCSTRING of at least one
   contract test; `python scripts/sc-trace.py --anchor {artifact} --contract
   {dir}` prints the binding, and `approve` and `freeze` refuse a criterion
   claimed by nothing or a claim on a criterion that was never stated. Measured
   2026-08-02: an artifact stating seven criteria against a 28-test contract had
   five of the seven bound to nothing. It proves a test CLAIMS to decide a
   criterion, never that it does.

Step 6 ends with the gate artifact written to the plans directory and the
operator asked. His COMMIT of that artifact IS the approval.

## `/canopus lock` — step 7

```
python scripts/canopus.py approve --label "{slug}" --anchor {artifact path} \
    --contract tests/contract/{date}-{slug}/ \
    --content scripts/run-tests.py \
    --content scripts/utils/atomic.py \
    --content scripts/utils/canopus_freeze.py \
    --content scripts/utils/canopus_gate.py \
    --content scripts/utils/canopus_git.py \
    --content scripts/utils/canopus_tree.py \
    --content scripts/utils/colors.py \
    --content scripts/utils/production_shape.py \
    --content scripts/utils/venv.py \
    --content tests/conftest.py
# read the already-green count, COMMIT the artifact, then:
python scripts/canopus.py freeze  ...identical flags...
python scripts/canopus.py verify
```

`verify` must report LOCK HELD and APPROVED. Ten files, not four: the gate's own
import TAIL is inside the guarantee, because a lock whose enforcer can be edited
is not one. `canopus_freeze` reaches `atomic`, which WRITES the manifest;
`run-tests` reaches `venv`, which chooses which interpreter runs the gate;
`canopus_gate` reaches `production_shape`, which can WITHHOLD an attestation. The
list is not decoration and it is checked —
`tests/test_canopus_freeze.py::test_the_documented_enforcer_set_covers_its_import_closure`
recomputes the closure and fails if this command has fallen behind it.

`freeze` refuses a contract that is not red for a reason that means something,
and refuses a root the COMMITTED artifact contradicts. Both refusals and their
exceptions are in `references/canopus-gate.md`. Read it before the first retake.

## `/canopus check` — steps 9 to 11

```
python scripts/canopus.py verify      # did the contract move
python scripts/canopus.py status      # the lock and the attestation, together
python scripts/canopus.py pack        # the evidence page for step 12
```

The verdict at step 9 is mechanical and nothing else counts: the locked tests
pass, none deselected, bound to a commit. Then steps 10 and 11 are the human
half — measure against the sentence from step 1, and attack the built thing with
`/scrutinize` until it returns nothing new. Green is not the same as right, and
step 10 is the only place "passed but wrong" is visible.

`pack` is not optional and it is the LAST thing before Approval 2, not the
first. It records the render, and `release --ship` refuses without one. Run it
after the last change, because the attestation dies when the tree moves and a
render older than the attestation describes an earlier state.

## `/canopus release` — step 13

```
python scripts/canopus.py release --ship --reason "<why>"
```

It refuses on two grounds, and the attestation is checked first because clearing
it invalidates the page anyway: a record that no longer stands for the tree sends
you to `run-tests`, and a missing or stale render sends you to `pack`. Neither
fires on a root that is not a git working copy, where the tree cannot be
described at all. It does NOT refuse when the ledger has lost the freeze
itself: a ledger that cannot answer prints an `unverifiable` warning and lets
the ship through, because a gate that pushes an honest operator toward `--force`
is worse than no gate. `--window` is never gated: a window is the way back into
the build, not the way out of it.

Then finish the step, because the command is not the step:

- **Retire the contract.** Promote the coverage that is still worth having into
  the ordinary suite and remove `tests/contract/{date}-{slug}/`. Left in place it
  binds every later slice to this one's behaviour.
- **Name the undo, in advance.** Write into the gate artifact which commit to
  revert, which baseline to restore, and what to re-run. The moment you need an
  undo is the worst moment to invent one.

## `/canopus back` — the way back, mid-build

For when the recipe itself must change while the slice is running: the frozen
set was wrong, or the fix belongs inside a frozen file.

```
python scripts/canopus.py release --window --reason "<why>"
```

An open window makes every pytest session start print an amber line saying no
lock is held, so a green suite proves nothing while it is open. Close it fast.

**Coming back is six commands, not one.** The enforcer bytes moved, so the root
moved with them, and the committed approval still records the previous root —
precisely what `freeze` refuses. `approve --replace --reason "<why>"`, a fresh
COMMIT of the artifact, then the identical `freeze`, then `verify`. Releasing a
freeze clears the attestation with it, so step 9 is run again.

A retake of a contract the slice has legitimately turned green needs
`--contract-satisfied "<why>"` on BOTH `approve` and `freeze`. It waives only
the redness refusal, the reason is mandatory, and it lands in the committed
artifact.

## Depth

Not every slice earns all thirteen. `python scripts/slice-depth.py <paths>`
classifies the change and prints the depth. Calibration may only ever REMOVE
ceremony from a shallow slice; the floor cannot be diluted, and the enforcement
surface is fixed. A slice touching the guards, the lifecycle, or anything in
`docs/security/` is `full` and is not negotiable.

## Voice

Internal engineering prose: terse and concrete. No `--` as punctuation. 31C
terms exactly (**ODUN.ONE**, **DPI+**, **Tribe**, **TrustONE**). No hidden
Unicode. Success criteria and test contracts are factual claims — never
fabricate a metric, a threshold, or a behaviour. Derive it from the plan, or ask.
