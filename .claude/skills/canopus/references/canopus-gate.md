# Canopus at the Fix 1 gate

Consumed by: `.claude/skills/canopus/SKILL.md` (`/canopus lock` and `/canopus back`) and `.claude/skills/canopus/references/planning-gate.md` (Phase 6, "On approval: lock on Canopus").
Last Updated: 2026-07-27

The operator-facing detail behind the four commands in the skill. The full
rationale for every rule here lives in `docs/EXTENDING.md`; this file is the
recipe, not the argument.

## Why eight files, not four

Three enforcers (`canopus_freeze.py`, `canopus_gate.py`, `canopus_git.py`), the
two places the gate fires (`run-tests.py`, `tests/conftest.py`), and their
three-file import tail (`atomic.py`, `venv.py`, `colors.py`). A freeze that omits
the tail leaves the write path of the guarantee outside the guarantee. A closure
test in `tests/test_canopus_freeze.py` recomputes the tail and fails when a new
import escapes the set.

## What `freeze` refuses, exactly

- **A contract that is not red for a reason that means something.** A contract
  file collecting nothing is refused; so is a set whose every red test passes
  against a null stub, which proves it asserts nothing.
- **A contract any WRONG implementation satisfies.** Three pass-candidates are
  built and run: `none` returns nothing from every call, `echo` hands back its
  first argument unchanged, and `greedy` answers with every string the contract
  itself wrote. A contract whose every red test passes against ONE of them is
  refused, and the refusal names that one, because the cure differs — a contract
  taken by `none` wants a value assertion, one taken by `echo` wants a value the
  input alone cannot produce, and one taken by `greedy` wants its substring check
  replaced by an equality against the whole value.

  Whole-contract, like the vacuity refusal and for the same reason: one
  legitimate substring assertion beside one equality assertion is never refused,
  and "these three tests assert too little" is a judgement for a human.

  **What it does NOT reach, stated rather than implied.** It stands in only for
  modules the contract IMPORTS and that do not resolve. A contract test driving a
  real entry point whose internals are wrong is outside it, and so is anything
  reached through a child process. Measured on a reconstructed CLI-wiring test of
  exactly that shape: all three candidates took nothing. Inside a contract, the
  discipline that closes it is writing wiring tests as PAIRS — a refusal must
  stand beside a non-refusal, or a constant satisfies both.
- **A root the COMMITTED gate artifact contradicts.** Be exact: that refusal
  fires only when the commit records a hash AND neither the committed nor the
  working copy carries the root being frozen. Two states therefore pass it: a
  commit recording no hash at all (a first freeze, an untracked artifact, a
  folder outside any repository), and a committed hash that DOES contradict while
  the working copy carries a freshly approved candidate. The second is the safer
  branch rather than a softening, and both say so on the way out and read amber.
- **A `--contract-satisfied` waiver the approval on the artifact does not
  carry.** `canopus pack` renders `CONTRACT WAIVED` from the artifact, and only
  `approve` writes that line there.

`freeze` writes nothing to the anchor artifact: an instrument that writes the
hash and then checks the hash it wrote has verified nothing.

## The retake waiver

Once the slice has implemented its contract, every row is green and the redness
rule refuses the retake. Pass `--contract-satisfied "<why>"` to BOTH `approve`
and `freeze`. The reason is the flag's value, so it cannot be passed without one,
and a reason of pure whitespace takes no waiver and says so.

It waives EXACTLY the redness refusal. A contract file that collected nothing is
still refused, and the baseline, the ledger note and the pack's contract section
all survive. On a contract that is still red the flag changes nothing and says so
out loud, so it cannot become a habit that hides redness. The reason lands in the
committed anchor artifact on a `canopus-contract-satisfied:` line and renders as
`CONTRACT WAIVED` on the evidence page.

## Release kinds, and the open-window line

`release` requires `--window` or `--ship`; passing neither exits 2. `--window` says
the lock will be taken again and the slice is still in progress. `--ship` says
the slice is over.

While a window stands open and no freeze is held, every pytest session start
prints an amber line naming the window, its timestamp and its reason, with "No
lock is held, so a green suite proves nothing about the contract." It reports and
never blocks. A later `freeze` closes it.

## An enforcer edit is `repin`, two commands

Since the manifest-split slice, the enforcer bytes are hashed OUTSIDE the
contract root. Editing one moves the enforcer PIN and leaves the root alone, so
the committed approval still matches and no window is needed:

1. `git commit` the enforcer change
2. `python scripts/canopus.py repin --reason "<why>"`
3. `python scripts/run-tests.py` — the re-pin clears the attestation, because
   the enforcer set holds the test runner and `conftest.py`

The commit is REQUIRED and `repin` refuses without it, naming the files. That
refusal is the whole reason this is not a security trade: the change lands in
git as a readable diff with an author, which is strictly better evidence than
the hash line the old six-command ceremony wrote into a private artifact.

An edited enforcer that has not been re-pinned does NOT read `LOCK HELD`.
`verify` lists it as `enforcer  <path>` with `cure: repin`, and the gate says
"The ENFORCER moved, not the contract". Cheaper than a retake is the goal;
invisible is not.

## Re-freezing after a window: six commands, not one

A window is still the way back when the CONTRACT itself must change, and coming
back from one is not a single `freeze`. The contract bytes moved, so the root
hash moved with them, and the committed approval still records the previous root
— which is exactly what `freeze` refuses.

1. `python scripts/canopus.py release --window --reason "<why>"`
2. make the edit
3. `python scripts/canopus.py approve --replace --reason "<why>" --cause <class> [--contract-satisfied "<why>"] <the same --label / --anchor / --contract / --content flags>`
4. commit the anchor artifact in its own repository — that commit IS the approval
5. `python scripts/canopus.py freeze [--contract-satisfied "<why>"] <the same flags>`
6. `python scripts/run-tests.py`, then `python scripts/canopus.py verify`

Step 3 is not optional: `--replace` is what appends a second approval line over a
recorded one, and it demands BOTH `--reason` and `--cause`.

`--cause` takes one of `contract-strengthened`, `enforcer-moved`, `lint`,
`frozen-set-wrong` (the closed vocabulary in `scripts/utils/gate_yield.py`), and it
is not a duplicate of the reason. The reason says what happened this once, in
prose no counter can read; the cause is what makes retakes countable. Measured
2026-08-03: 39 retakes in the ledger and the yield report saw none of them, while
the whole lifecycle's reported yield was five. A cause is never inferred from the
prose, because a counter built on a substring lies the first time somebody
rewords their sentence. Step 4 is not optional either: without
the commit, `verify` reports `LOSS OF LOCK` because the committed approval still
holds the older hash. Step 6 is not optional because releasing a freeze clears
the attestation with it, so the contract has to be run again before `status` or
`pack` can read `ATTESTED`.

## What `verify` must report

Inside a repository the criterion is `LOCK HELD` and `APPROVED`. With the gate
artifact outside any repository, a supported mode, no commit exists to attribute
an approval to and `APPROVED` can never print: the criterion there is `LOCK HELD`
with `APPROVAL UNVERIFIED` naming `no_repo`.

A red anchor status of `unbound` means the anchor's repository binding broke —
the anchor is in a different repository than the freeze recorded, or git can no
longer see one at all. Release and re-freeze; it is never worked around.

## At ship time, retire the contract

Promote the still-valid coverage into the ordinary suite and REMOVE
`tests/contract/{YYYY-MM-DD}-{slug}/`. Left in place it binds every later slice
to this one's behaviour verbatim. `docs/EXTENDING.md` carries the measured case.
