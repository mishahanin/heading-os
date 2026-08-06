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
    `check` for the four clauses, `probe` to measure whether a contract is vacuous.
    Records live in `records/slices/`; plans and scope documents live in the operator's
    private overlay and are referenced by digest, never by path.
  when: >
    Use for any change worth a plan. Do NOT use for typo fixes or config-only edits, and
    do NOT use it to review finished work: that is `/scrutinize`.
---

# Canopus

The standard for building anything non-trivial here. Seven steps, two of them the
operator's own, and every mechanical part built out of git, GitHub and pytest
rather than re-implemented in Python: 93 percent of the retired version's
hand-built prevention surface was defeated by one shell command.

**The one thing this exists to prevent:** a contract too weak to decide anything,
passing as one that decided. Across all 62 prior records a contract was never once
weakened after the fact, and was born too weak thirty times. The green to distrust
is the one no wrong implementation was ever made to fail.

## Bare `/canopus` — the seven steps

Nothing is run. Show the agenda below and say which step the work in hand is at, from
the conversation rather than from the disk: only steps 4 and 7 leave a trace in this
repository, so a position read off it would be a guess dressed as a measurement.

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

Steps 4 and 7 are the operator's. Act 1 is steps 1 to 4, nothing built yet; act 2 is
step 5, where the implementer is not the planner; act 3 is step 6, where green is not
the same as right; act 4 is step 7, where the undo is named before it is needed.

## Step 3 — what makes a plan worth committing

Four properties, and a plan missing any of them buys nothing at step 4. The
authoring detail behind properties 1 and 4 — the five contract-writing rules,
each earned by a measured failure, and how to read `probe`'s table — is
`references/planning-gate.md`. Read it before writing a contract.

1. **Criteria derived from a partition of the input domain.** One row per value
   class, edges included, and the contract is real test files at
   `tests/contract/{YYYY-MM-DD}-{slug}/` importing the code under test INSIDE each
   test body. It is the only generator of a strong contract we have, and a contract
   born too weak is the dominant measured defect.
2. **A byte budget on the plan**, on the precedent of `skill-metadata-check.py`,
   which caps a `SKILL.md` at 500 lines and 18,432 bytes. The budget forces the
   writer to discard what does not matter; without one the plan grows until nobody
   reads the part that decides.
3. **The plan does not decide what the implementer can decide from its own context**,
   and it names the files to read first. Re-specifying what the code already says
   wastes the implementer's attention on agreement.
4. **Vacuity is measured, not assumed.** `.venv/bin/python scripts/canopus.py
   probe tests/contract/{date}-{slug}/` null-stubs the missing modules: a test
   that ERRORS against the stub is vacuous, because an outcome invariant to the
   stub proves nothing. It also runs three wrong implementations that EXIST
   (`none`, `echo`, `greedy`) and prints what each took of the red set. `greedy 2
   of 3` says two checks are greps for a word, not assertions about a value.

## Step 4 — the freeze is a commit

The operator's COMMIT of the plan and the RED contract IS the approval, and its sha
is the freeze. Nothing else is needed: `git show <sha>:<path>` reads the frozen
bytes, `git diff` against it answers whether the contract moved, and `git merge-base
--is-ancestor` answers whether the implementation descends from the approval. Put
that sha in the note as `approval_sha`.

## Step 5 — the separation is a dispatch

`superpowers:subagent-driven-development` sends a fresh implementer per task plus a
reviewer who did not write the code, so the entity that decides what "done" means is
not the entity that decides it is done. Executing the plan inline in the session
that wrote it IS the gap. The note is a RECORD and never an agent input: feeding it
back hands a reviewer the previous author's framing and destroys what separation buys.

## Step 6 — every finding carries an origin

`/scrutinize --relentless`, and apply all. A contract-origin finding returns to
step 3 and produces a NEW contract; it never becomes a patch that leaves the old
contract green. A code-origin finding is fixed against the contract that already
exists. Naming the origin is what keeps a weak contract from surviving its slice.

## Step 7 — production, the note, and the undo

Ship it, then write the record:

```
.venv/bin/python scripts/canopus.py note --slug <slug> --value "<one sentence>" \
    --approval-sha <sha> --contract tests/contract/<date>-<slug>/ \
    --plan-digest sha256:<...> --undo "revert <sha>, restore <baseline>, re-run <cmd>"
```

One committed markdown file per slice under `records/slices/`, engine-relative
paths only. This repository is PUBLIC: a note carries no absolute path and no
overlay path, which is why the plan and the scope document go in by sha256
digest. Retiring the contract into the ordinary suite is part of the step, and a
retirement is RECORDED: `retired_sha` is the commit that removed the contract and
`promoted_to` the file carrying the coverage now. Without both, the clauses below
read a shipped slice as a broken one.

## `check` — the four clauses

```
.venv/bin/python scripts/canopus_check.py --range <A>..<B>
```

C1 the contract did not move between the approval sha and the end state. C2 the
implementation descends from the approval. C3 the contract, checked out at the
approval sha and RUN there, was red. C4 the target is green at HEAD, with per-file
junit counts above zero, because collected is not run. A CI step in the `sovereignty
guards` job runs the same module on every push. It REPORTS. It does not block:
`enforce_admins` is false on the only push path in use, and a control that cannot
enforce must never be described as if it can.

## The four rules the measurements bought

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
