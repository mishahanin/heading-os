---
name: canopus
description: >
  The engineering standard for building anything non-trivial in this workspace: seven
  numbered steps, with the operator's two moments as steps 4 and 7. Bare `/canopus`
  prints the seven steps and where the operator's two moments sit in them. Subcommands
  are thin: `note` writes the slice's committed record, `check` runs the four clauses a
  CI step runs identically, `probe` measures whether a contract's redness means
  anything. Use for any change worth a plan. Skip typo fixes and config-only edits.
argument-hint: "[note | check | probe]  (bare = the seven steps)"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(git:*), Skill"
disable-model-invocation: true
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "3.1"
x-heading-orchestration:
  parallel_safe: false
  shared_state: ["records/slices/"]
  triggers: []
x-heading-routing:
  category: Operations
  triggers: ["NEVER auto-trigger. Explicit `/canopus [note | check | probe]` only. Bare `/canopus` prints the seven steps and the two the operator owns."]
  exclusions: ["N/A"]
  compound: "No"
  router: manual
  label: "/canopus [note | check | probe]"
x-heading-capability:
  what: >
    The workspace's engineering standard as a command. Names the seven steps and the two
    the operator owns, records what a slice approved and shipped, and runs the four
    clauses that decide whether the contract held between the approval commit and the code.
  how: >
    Bare `/canopus` for the seven steps. `/canopus note` to write the slice record,
    `check` for the four clauses, `probe` to measure whether a contract is vacuous, and
    `probe --after-build` to ask that question again over code that already shipped.
    Records live in `records/slices/`; plans and scope documents live in the operator's
    private overlay and are referenced by digest, never by path.
  when: >
    Use for any change worth a plan. Do NOT use for typo fixes or config-only edits, and
    do NOT use it to review finished work: that is `/scrutinize`.
---

# Canopus

The standard for building anything non-trivial here. Seven steps, two of them the
operator's own. Every mechanical part is built out of git, GitHub and pytest
rather than re-implemented in Python. One shell command defeated 93 percent of
the retired version's hand-built prevention surface.

**The one thing this exists to prevent:** a contract too weak to decide anything,
passing as one that decided. Across all 62 prior records a contract was never once
weakened after the fact, and was born too weak thirty times. The green to distrust
is the one no wrong implementation was ever made to fail.

## Bare `/canopus` — the seven steps

Nothing is run. Show the agenda below and name the step the work in hand is at.
Read that position from the conversation, never from the disk. Only steps 4 and 7
leave a trace in this repository, so a position read off it is a guess dressed as
a measurement.

## The seven steps

The agenda has exactly one definition, `scripts/utils/canopus_steps.py`. This
file may summarise it and may never renumber it.

```
1. Define the Value
2. superpowers:brainstorming                 -> scope document
3. superpowers:writing-plans                 -> plan
4. /scrutinize the plan, apply every finding -> the operator's commit
5. superpowers:subagent-driven-development   -> implementation
6. /scrutinize --relentless, apply every finding
7. Production
```

Steps 4 and 7 are the operator's. The seven steps fall into four acts:

- Act 1, steps 1 to 4. Nothing is built yet.
- Act 2, step 5. The implementer is not the planner.
- Act 3, step 6. Green is not the same as right.
- Act 4, step 7. The undo is named before anyone needs it.

## Step 3 — what makes a plan worth committing

Four properties, and a plan missing any of them buys nothing at step 4. The
authoring detail behind properties 1 and 4 is `references/planning-gate.md`. It
carries the five contract-writing rules, each earned by a measured failure, and
how to read `probe`'s table. Read it before writing a contract.

1. **Criteria derived from a partition of the input domain.** One row per value
   class, edges included, and the contract is real test files at
   `tests/contract/{YYYY-MM-DD}-{slug}/` importing the code under test INSIDE each
   test body. It is the only generator of a strong contract we have, and a contract
   born too weak is the dominant measured defect.
2. **A byte budget on the plan itself: warn at 16,384 bytes, hard at 24,576.**
   Measured across 99 real plans, median 23,704. The warn mirrors the SKILL.md
   warn that `skill-metadata-check.py` already enforces, so the workspace carries
   one number rather than two. **You measure the plan yourself and you decide.**
   No code anywhere reads these numbers. `PLAN_BYTE_WARN` and `PLAN_BYTE_HARD`
   in `canopus_steps.py` exist for one purpose: to stop this paragraph and the
   agenda module drifting apart. A test holds them in lockstep. Nothing counts a plan's bytes,
   nothing warns, and nothing refuses a commit. It is a proposal with operator
   override. It earns its keep by forcing the writer to discard what does not
   matter. Without one, the plan grows until nobody reads the part that decides.
3. **The plan does not decide what the implementer can decide from its own context**,
   and it names the files to read first. Re-specifying what the code already says
   wastes the implementer's attention on agreement.
4. **Vacuity is measured, not assumed.** `.venv/bin/python scripts/canopus.py
   probe tests/contract/{date}-{slug}/` null-stubs the missing modules and runs
   the contract twice, each stub carrying different values. A test that never
   FAILS under either run is vacuous: an outcome invariant to the stub proves
   nothing. Passing, skipping and erroring all leave a test unproved. Only a
   failure shows that it read the value. The probe also runs three wrong
   implementations that EXIST: `none`, `echo`, `greedy`. It prints what each one
   took of the red set. `greedy 2 of 3` says two checks are greps for a word
   rather than assertions about a value.

## Step 4 — the freeze is a commit

The operator's COMMIT of the plan and the RED contract IS the approval, and its
sha is the freeze. Nothing else is needed:

- `git show <sha>:<path>` reads the frozen bytes.
- `git diff` against it answers whether the contract moved.
- `git merge-base --is-ancestor` answers whether the implementation descends from the approval.

Put that sha in the note as `approval_sha`.

## Step 5 — the separation is a dispatch

`superpowers:subagent-driven-development` sends a fresh implementer per task plus
a reviewer who did not write the code. The entity that decides what "done" means
is then not the entity that decides it is done. Executing the plan inline in the
session that wrote it IS the gap. The note is a RECORD and never an agent input.
Feeding it back hands a reviewer the previous author's framing, which destroys
what the separation buys.

## Step 6 — every finding carries an origin

`/scrutinize --relentless`, and apply all, for THREE fix rounds; then stop and
hand the operator what is still open. Say why: an unbounded loop already ran to
five rounds here without a line of code being written.

Three origins. A contract-origin finding returns to step 3 and produces a NEW
contract; it never becomes a patch that leaves the old contract green. A
code-origin finding is fixed against the contract that already exists. A
value-origin finding returns to step 1. That is the built thing revealing that
step 1's value statement was wrong. The call is the OPERATOR's and not the
assistant's, because step 1 is where he said what would be worth having. File it
as contract-origin instead, and step 3 faithfully writes a strong contract
against a value that is still wrong.

Add one blinded pass: a reviewer seeing only the diff and the tree, blind to the
plan and the value statement. ONE, not a panel. The more of the original
session's context a reviewer gets, the more it reproduces that session's
reasoning with the same weights. It then reaches the same conclusions. It cannot see a
gap between intent and result, so it sits beside the ordinary pass and never in
place of it.

Naming the origin is what keeps a weak contract from surviving its slice.

## Step 7 — production, the note, and the undo

Ship it, then write the record:

```
.venv/bin/python scripts/canopus.py note <slug> --value "<one sentence>" \
    --approval-sha <sha> --contract tests/contract/<date>-<slug>/ \
    --plan-digest sha256:<...> --scrutinize-plan "<step 4 findings, all applied>" \
    --scrutinize-built "<step 6 findings, all applied>" \
    --undo "revert <sha>, restore <baseline>, re-run <cmd>"
```

Every flag above is required by the schema, and the schema refuses the note
rather than writing a half-formed one. `--show` prints a written note back.

One committed markdown file per slice under `records/slices/`, engine-relative
paths only. This repository is PUBLIC, so a note carries no absolute path and no
overlay path. That is why the plan and the scope document go in by sha256 digest.

Retire the contract into the ordinary suite as part of the step. RECORD the
retirement with two flags. `--retired-sha` is the commit that removed the
contract. `--promoted-to` is the file carrying the coverage now. Without both,
the clauses below read a shipped slice as a broken one.

## `probe --after-build` — the same question, over code that already exists

```
.venv/bin/python scripts/canopus.py probe --after-build <test paths>
```

All three subcommands take a global `--root <path>` ahead of the subcommand
name, which sets the working tree they read. It defaults to this script's own
repository root rather than the shell's cwd. An invocation from anywhere then
answers about the same tree, unless you say otherwise.

Step 3 asks "if this code were wrong, would any gate notice" exactly once, at a
moment when the code does not exist. Retiring the contract into the ordinary
suite at step 7 ends the asking: nothing re-asks, ever. This flag asks it again
over code that ALREADY EXISTS. It puts the same three wrong implementations in
front of whatever tests now cover the shipped slice. It then names every test
that stayed green under all three.

Reach for it when a later slice is about to lean on a retired contract's
coverage. Reach for it when a suite looks greener than the work behind it.

It REPORTS and never refuses. Exit 0 whether it names twenty survivors or none.
Exit 1 only when it could make no reading at all, which is a different statement
from a clean one. **Nobody has calibrated this reading.** Rule 3 below forbids an
uncalibrated reading from becoming a gate, so do not wire it into one.

Three things decide whether you read the page correctly:

- **A name in the list is NOT a bad test.** The claim is only that the test did
  not tell right from wrong under those three wrongnesses. A test of a document
  or of a file on disk lands there while being a perfectly good test of what it
  is about.
- **The page names which modules it replaced and which it did not.** A test whose
  subject sits on the `not replaced` line was measured against nothing, and its
  name says only that. A module the run never imported is on that line too,
  because nothing stood in for it.
- **A SKIPPED test is neither a survivor nor a test that bit.** It is named on
  its own `never ran` line and it is out of the total. A run in which every test
  skipped produces no reading at all. A test the CANDIDATES skipped is that same
  third thing arriving by the other door. It passes for real, so the real run
  cannot see it. A fixture reading a value the candidates stand in for is the
  ordinary way that happens, and it is named on a `sat out` line.

## `check` — the four clauses

```
.venv/bin/python scripts/canopus.py check --range <A>..<B> [--json]
```

C1 the contract did not move between the approval sha and the end state. C2 the
implementation descends from the approval. C3 the contract, checked out at the
approval sha and RUN there, was red. C4 the target is green at HEAD, with per-file
junit counts above zero, because collected is not run. The subcommand is a
passthrough to `scripts/canopus_check.py`. A CI step in the `sovereignty guards`
job runs that same module on every push, so the local reading and the CI reading
are one reading. It REPORTS. It does not block, because `enforce_admins` is
false on the only push path in use. Never describe a control that cannot enforce
as if it can.

## The four rules the measurements bought

<!-- ste-skip-start -->
<!-- Explanatory: each rule IS the measurement that bought it, and the section
     opens by saying a reader who does not know why will delete them. Per
     .claude/rules/documentation-style.md, flattening this destroys the point. -->

Each was learned expensively. A reader who does not know why will delete them.

1. **Topology, never timestamps.** Any clause deciding an order uses git ancestry or
   an off-machine run record. `GIT_COMMITTER_DATE` is an environment variable:
   demonstrated 2026-08-06, two variables put an implementation commit nine hours
   before the approval it descends from, and ancestry got it right while the clock did not.
2. **The oracle is the executed suite at the target state.** Import assertions and
   `--collect-only` are lossy projections. Measured: a deletion program produced 586
   failing tests in 28 files while all three projections returned exit 0. Keep them as
   fast localisers, never as verdicts.
3. **Every check is demonstrated red on its own failure class before it is
   adopted.** An uncalibrated check is worse than none because it manufactures
   confidence. The four clauses were each broken on purpose first, six deliberate
   mutations of their own module.
4. **Prove a record is alive; do not infer it from silence.** 24 broad exception
   handlers wrap a call into this system, 10 of them invisibly, and 6 were a flagship's
   own pytest hooks where total failure changed the corpus by exactly zero tests.
<!-- ste-skip-end -->

## NEVER

- **NEVER take step 4 or step 7 on the operator's behalf.** They are his, and a
  standard whose two approvals the assistant can grant itself is a standard with
  zero. Present the evidence and stop.
- NEVER present a summary in place of evidence at step 7. Name what the contract
  does NOT cover.
- NEVER edit a frozen contract to make it pass. A contract-origin finding goes back
  to step 3 and produces a new one, with a new approval commit.
- NEVER write an absolute path, an overlay path, or a third-party entity name into
  a note. The engine repository is public.
- NEVER pass `--no-verify`, and never weaken a guard to get a commit through.
- NEVER delete anything without the operator's explicit word.

## Voice

Internal engineering prose: terse and concrete. No `--` as punctuation. 31C terms
exactly (**ODUN.ONE**, **DPI+**, **Tribe**, **TrustONE**). No hidden Unicode. Success
criteria and contracts are factual claims: never fabricate a metric, a threshold, or
a behaviour. Derive it from the plan, or ask.
