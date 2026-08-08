<!-- version: 1.0.0 | last-updated: 2026-08-09 -->
# Canopus, the build standard

How anything non-trivial gets built into HEADING OS. Canopus answers one
governance question: **how do you let an AI build real capability into a system
you depend on, without ever having to take its word for it?**

It is a named, numbered, repeatable procedure. Every non-trivial change to the
engine goes through it. Typo fixes and configuration edits do not, deliberately,
because a process everybody skips is worse than no process.

Two of its seven steps belong to the operator. The assistant is forbidden from
taking either on their behalf, because a standard whose approvals the assistant
can grant itself is a standard with zero approvals.

The steps are defined as data in `scripts/utils/canopus_steps.py`, which is their
only definition. This page and the `/canopus` skill both summarise that module;
neither may renumber it.

!!! warning "The one thing Canopus exists to prevent"
    A test too weak to decide anything, passing as a test that decided
    something. Across 62 recorded slices, a test was never once weakened after
    the fact to make a failure go away. It was born too weak thirty times. The
    green light to distrust is the one that no wrong version of the code was
    ever made to fail.

## The four acts

| Act | Steps | The point of it |
|---|---|---|
| **1. Decide** | 1 to 4 | Nothing is built yet |
| **2. Build** | 5 | The implementer is not the planner |
| **3. Check** | 6 | Green is not the same as right |
| **4. Production** | 7 | The undo is named before it is needed |

## The seven steps

**1. Define the Value** *(Act 1)*

One sentence saying what would be worth having, in business terms. Not "add a
module", but "a weekly digest that tells me which suppliers slipped this week, so
I stop finding out at the quarterly review". It goes into the slice record
verbatim, and step 6 measures the built thing against it.

*What it buys:* a stated purpose you can hold the finished thing against. Half
the failures later in this list turn out to be a good build of the wrong idea.

**2. Brainstorm the scope** *(Act 1)*

`superpowers:brainstorming`, until a scope document says what should be built
rather than how to build what was asked. What is in, what is deliberately out,
what the alternatives were, and why they were rejected. The rejected options are
written down, not discarded, so nobody re-litigates them in three months.

*What it buys:* the argument happens while changing your mind is still free.

**3. Write the plan** *(Act 1)*

`superpowers:writing-plans`. The plan states three to five success criteria, each
written so that a single test can decide it, in the form *when this happens, the
system shall do that*. The criteria are not brainstormed from what came to mind:
they come from a partition of the input domain, one row per value class with the
awkward edges included, and the name of the test that decides each.

The criteria then become **real test files** rather than a description of them,
written before a single line of the implementation exists. Those files are the
contract. They fail, correctly, because there is nothing yet to make them pass.

The plan carries a byte budget: 16 KiB warn, 24 KiB hard, because a plan nobody
finishes reading is a plan whose decisive paragraph nobody read. Both numbers are
a proposal with operator override, never a gate; nothing refuses a commit over
them. They were set from 99 real plans (median 23,704 bytes), and 51 of the 99
would pass the hard number unchanged.

*What it buys:* the definition of "done" is fixed in writing, in executable form,
before anybody is invested in the work.

**4. Scrutinize the plan, apply every finding** *(Act 1, the operator's)*

An adversarial review runs against the plan, and every finding is applied rather
than triaged. No code exists yet, so this is the cheapest moment to change
anything. Then the operator reads it and commits.

That commit **is** the approval. It captures the plan and the RED contract
together at one point in time, and that sha is the freeze.

*What it buys:* a fixed reference point. Anyone can later ask, mechanically,
whether the delivered work matches what was approved.

**5. Build it, under separation** *(Act 2)*

`superpowers:subagent-driven-development` dispatches a fresh implementer per task
plus a reviewer who did not write the code. The session that wrote the plan does
not implement it. This is not ceremony: an author reviewing their own work
reproduces their own assumptions with the same weights and reaches the same
conclusions. Every commit descends from the approval sha.

*What it buys:* the entity that decides what "done" means is never the entity
that declares it done.

**6. Scrutinize the built thing, apply every finding** *(Act 3)*

Relentless, bounded, and every finding is applied. Anything still open at the end
goes to the operator rather than being quietly closed. Each finding carries an
origin, and the origin decides where it goes:

- **Code origin.** The build is wrong. Fix it against the existing contract.
- **Contract origin.** The test was too weak. Return to step 3 and produce a NEW
  contract with a new approval, never a patch that leaves the old one green.
- **Value origin.** The finished thing reveals that step 1 was wrong. That is the
  operator's call, because step 1 is where they said what would be worth having.

*What it buys:* a weak test cannot survive its own slice. Naming the origin is
what forces it back to step 3 instead of being smoothed over.

**7. Production** *(Act 4, the operator's)*

The operator ships, on the evidence rather than on a summary, including what the
contract does NOT cover. Then the slice writes one committed record under
`records/slices/`: the value, the approval it descends from, what the reviews
found, what was fixed, and, in plain words, how to undo it. Which commit to
revert, which baseline to restore, what to re-run.

*What it buys:* an audit trail per change, and a rollback path that exists before
the emergency.

## What the machine can see, and what it cannot

Two of the seven steps leave a durable trace in this repository: **step 4** (the
approval commit and the contract files it carries) and **step 7** (the record
under `records/slices/`). The other five are human and agent work that no file
here records. The scope document and the plan live in the operator's private
overlay and are referenced by digest, never by path, because this repository is
public.

`canopus_steps.py` carries a `machine_visible` flag per step so a reader of the
agenda can tell the two steps this repository can evidence from the five it
cannot.

## The approval is a commit, not a lock file

`git show <sha>:<path>` reads the frozen bytes. `git diff` answers whether the
contract moved. `git merge-base --is-ancestor` answers whether the implementation
descends from the approval. Nothing on the machine holds those bytes down, and
this page does not claim otherwise.

## Three instruments

### `note` — what actually happened here

```bash
python scripts/canopus.py note <slug> --value "<one sentence>" \
    --approval-sha <sha> --contract tests/contract/<date>-<slug>/ \
    --plan-digest sha256:<...> --scrutinize-plan "<step 4 findings, all applied>" \
    --scrutinize-built "<step 6 findings, all applied>" \
    --undo "revert <sha>, restore <baseline>, re-run <cmd>"
```

Every flag is required by the record's schema, which refuses an incomplete note
rather than writing a half-formed one, so the archive cannot fill with entries
that look complete and say nothing. A slice whose contract has been retired into
the ordinary suite adds `--retired-sha` and `--promoted-to`, and the schema
refuses the first without the second: a retirement pointing nowhere cannot be
told apart from a contract that was simply dropped.

### `check` — did the work honour the approval

```bash
python scripts/canopus.py check --range origin/main..HEAD
```

`scripts/canopus_check.py` reads the records back over the repository they are
committed to, in four clauses:

| Clause | The question |
|---|---|
| **C1** | Did the contract move between its approval and the end of its life? |
| **C2** | Does HEAD descend from the approval commit? |
| **C3** | Was the contract genuinely RED at the approval sha, run in a worktree checked out there? |
| **C4** | Is the target green at HEAD **and does the junit report show it RAN**? |

C4 is written that way because collected is not run, and an all-skipped file
exits 0.

**No clause reads a timestamp.** `GIT_COMMITTER_DATE` and `GIT_AUTHOR_DATE` are
environment variables, and on 2026-08-06 two of them put an implementation commit
nine hours before the approval it descends from. Order comes from lineage.

`check` is a passthrough to `canopus_check.py`, which is the module CI runs
directly, so the local reading and the CI reading are the same reading.

### `probe` — is this test worth anything

```bash
python scripts/canopus.py probe tests/contract/<date>-<slug>/
python scripts/canopus.py probe --after-build tests/test_<subject>.py
```

`probe` measures whether a contract's redness means anything. It null-stubs the
missing modules and runs the contract twice, each stub carrying different values,
so a test that never FAILS under either run is vacuous. Passing, skipping and
erroring all leave a test unproved; only a failure shows it read the value. It
also runs three deliberately wrong implementations and prints what each one got
past. The result goes in front of the operator before they approve.

A skip-family marker (`skip`, `skipif`, `xfail`) that states no reason refuses
the contract, whether it sits on a test, on a class, or on `pytestmark`. A
`pytest.skip()` inside a test body and a module-scope `pytest.importorskip` are
not read, so they remain the reviewer's to catch.

`probe --after-build` asks the same question at the other end of a slice's life.
Once a contract is retired into the ordinary suite, nothing re-asks whether the
tests guarding that code would notice if it were wrong; this flag puts the same
three wrong implementations in front of shipped code and names every test that
stayed green under all of them. The page names which modules were actually
replaced and which were not, because a name that survived a module nothing stood
in for is not evidence about it. Skipped tests are named separately: a test that
never ran neither survived a wrong implementation nor went red under one.

## Honest limits

**`check` reports; it does not block.** A CI step in the `sovereignty guards` job
runs `canopus_check.py` on every push. It reports a broken clause. It does not
refuse one, because `enforce_admins` is false on the only push path in use.
Nothing on the machine prevents a test contract from being edited by whoever is
implementing against it. Do not describe this as prevention.

**`probe --after-build` is uncalibrated.** It reports and never refuses: exit 0
whether it names twenty survivors or none, exit 1 only when no reading could be
made at all. Nobody has calibrated that reading, so it is not wired into a gate
and must not be.

## Why seven, and not thirteen

An earlier Canopus had thirteen numbered moments and a large amount of custom
machinery to enforce them: file locks, freeze manifests, approval gates written
in code. Measurement retired most of it. **93 percent of that hand-built
prevention surface could be defeated by a single shell command**, which meant it
stopped the careless hand and not the determined one, while charging every honest
change a heavy toll.

The thirteen were four acts of ceremony around two things git already provides.
The freeze is a commit. The separation is a dispatch. Everything the retired
agenda numbered around those two facts was removed on 2026-08-06.

Thirteen became seven. Enforcement now rests on tools that already exist and are
already trusted: version control for the freeze, a separate reviewer for the
separation, and the ordinary test suite for the verdict. Less code to maintain,
less ceremony per change, and the guarantees that were real are still there.

!!! note "Four rules the measurements paid for"
    **1. Order comes from lineage, never from clocks.** A timestamp is an
    environment variable anyone can set.

    **2. The verdict is the suite actually run, not a shortcut.** A deletion
    experiment produced 586 failing tests across 28 files while all three fast
    shortcuts reported success.

    **3. Every check is broken on purpose before it is trusted.** An uncalibrated
    check is worse than no check, because it manufactures confidence.

    **4. Prove a safeguard is alive, never infer it from silence.** 24 broad
    error handlers were found wrapped around calls into this system, 10 of them
    invisibly. Silence is not evidence.

## A worked example, end to end

**The ask:** "I want a Monday morning digest of which suppliers slipped last
week."

**Step 1.** Value: *a weekly supplier slippage digest, delivered before the
Monday leadership call, so a slip is discussed in the week it happens rather than
at quarter end.*

**Step 2.** Scope: reads the supplier records and the delivery log. Sends to one
channel. Explicitly out of scope: chasing the supplier, changing any record,
forecasting.

**Step 3.** Criteria, one test each. When a delivery date passes with no receipt
logged, the digest shall name that supplier. When nothing slipped, the digest
shall say so in one line rather than staying silent. When the delivery log cannot
be read, the digest shall report the failure and shall not send an empty all
clear. The input partition covers the awkward cases: a supplier with no
deliveries at all, a delivery logged late but on time, a date exactly on the
boundary.

**Step 4.** The operator reads the plan, sees that the third criterion is the one
that saves them from a false all clear, and commits.

**Step 5.** A separate builder implements it. A separate reviewer checks it.

**Step 6.** The review finds that an unreadable log produced an empty digest that
read as good news. That is a code-origin finding against a criterion that already
existed, so it is fixed, and the test that always covered it now genuinely bites.

**Step 7.** Ships. The record names the undo in one line: turn off the schedule,
restore the previous version, run the test suite.

## Why an executive should care

| Canopus property | What it means for the business |
|---|---|
| Two human approvals, never delegated | Nothing enters the system you depend on without you agreeing twice, once to the intent and once to the result. |
| Definition of done fixed in advance | No moving goalposts, in either direction. You cannot be sold a smaller success than the one you approved. |
| Builder separated from planner | The self-assessment problem is removed by construction rather than by good intentions. |
| Test strength measured, not assumed | A green light means something. That is the entire point of the standard. |
| Undo written before it is needed | Recovery is a one-line instruction on file, not an improvisation under pressure. |
| One record per change | A complete, readable audit trail of every meaningful change, with the reasoning attached. |

## Where to go next

- **[Extending the engine](EXTENDING.html)** — the gates every change runs before
  "done", and where Canopus sits among them.
- **[Release notes](RELEASE-NOTES.html)** — section 2 tells the same story for a
  reader who is not going to run any of these commands.
- `scripts/utils/canopus_steps.py` — the steps as data, the only definition.
- `records/slices/` — the committed record of every slice that has shipped.
