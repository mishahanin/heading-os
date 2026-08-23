# Writing a test contract that is not born too weak

Consumed by: `.claude/skills/canopus/SKILL.md` (step 3)
Last Updated: 2026-08-07

What survives of the former `/pre-impl` gate. Everything it said about approve,
freeze, lock, release and repin went with the freeze machinery on 2026-08-07.
What is kept is the part that machinery never provided: how to author a contract
that can decide something, and how to read `probe` when it says the contract
cannot.

The one defect this exists to prevent: **a contract too weak to decide anything,
passing as one that decided.** Across 62 prior records a contract was never once
weakened after the fact, and was born too weak thirty times.

## Criteria first, and one trigger per criterion

Write 3 to 5 criteria before any test file exists. Each is:

- **Testable** — a test can verify it. Not "the code is clean".
- **Binary** — yes or no, never "improved" or "faster".
- **Specific** — names the exact behaviour, output value, or system state.

Cover at least one happy path, one failure mode, and one integration point.

**Write each in the EARS shape:** `WHEN <trigger>, THE SYSTEM SHALL <response>`.
Not for its own sake. One trigger and one response is what makes a criterion
bindable to ONE test; a compound criterion holding two triggers cannot be.
Exceeding the 3-5 band is the cheaper error when the alternative is folding two
claims into one line.

**Derive the criteria from a partition of the input domain, and write it down as
a table**, one row per value class, edges included, each row naming the test
that decides it (`| Value class | Test |`). A criteria set assembled from the
cases that came to mind tests the author's imagination; a partition tests the
domain, but only if it is an artifact the plan carries rather than reasoning
that happened once in a chat window and left no trace. Name the classes first,
then one row per class, then check that no class is missing a row.

## The contract is real files

Write them to `tests/contract/{YYYY-MM-DD}-{slug}/test_*.py`, not as prose in a
plan. A prose contract and the tests that later decide whether the work is done
are two artifacts joined by good intentions.

Five authoring rules, each earned by a measured failure rather than by taste.

**1. Import the code under test INSIDE the test body, never at module scope.**
The implementation does not exist yet, so a module-scope import stops the file
collecting, and a file that collects nothing decides nothing.

```python
def test_the_report_counts_a_denial(self):
    from scripts.utils.gate_yield import summarise   # inside the body

    assert summarise(denials=[...], since=..., now=...)["mechanisms"]
```

**2. A contract test must not couple itself to the environment it runs in.**
Both failures were invisible until the build:

- 2026-08-02, `canopus-skill`: two tests described a between-slices state and ran
  against the ENGINE root, which carried their own slice's state. They could
  never be green.
- 2026-08-02, `gate-yield`: a test compared raw stderr from two runs in two
  different roots, so it compared where each ran rather than what each did.

The rule those give: **a test that reads working-tree state takes its own scratch
root, and a test that compares two runs compares the INVARIANT, never the raw
text.** Ask it of every test while changing it is still free.

**3. Run the commit gates against the contract file itself, before the approval
commit.**

    pre-commit run --files tests/contract/{YYYY-MM-DD}-{slug}/test_contract.py

Measured 2026-08-02, `gate-yield`: a test variable named `secret` tripped
detect-secrets' keyword heuristic. The value was assembled by concatenation
exactly as the workspace requires; the NAME was the problem. Nobody found out
until the slice was built and being committed, at which point the whole thing was
uncommittable — a baseline entry, a pragma and `--no-verify` are all forbidden
here, and correctly so. One command earlier would have cost nothing.

**4. Every criterion is claimed in the DOCSTRING of at least one test, and no
docstring claims a criterion nobody stated.** The claim OPENS the docstring —
`"""SC-2. What this decides."""` — and anything deeper in the prose claims
nothing, so a test can describe itself without binding to every identifier it
names. Check it:

    python scripts/sc-trace.py --anchor <plan> --contract tests/contract/{YYYY-MM-DD}-{slug}/

Measured 2026-08-02, which is why this exists: the `gate-yield` plan stated seven
criteria, its contract carried 28 tests, and five of the seven were traceable to
nothing at all. Read the guarantee narrowly — it proves a test CLAIMS to decide a
criterion, never that it does. A green trace does not excuse reading the tests.

**5. A fixture must be able to produce the shape the REAL source produces.**
Check every fabricated input against a real sample: a line from the live log, an
actual file on disk, a genuine API response. Not the shape the code expects, and
not the shape the docstring describes — the shape the source actually emits.

Measured 2026-08-02, `gate-yield`: a 28-test contract failed to catch that the
report could not parse the denial log's timestamps at all, because EVERY fixture
in it stamped an ISO string and no real denial record has ever carried one — the
log writes `time.time()` floats. The mismatch was untestable by construction, and
silent: an unparsed stamp answers `None`, and `None` renders as a 0-day window
rather than as an error, so the report's central verdict was unreachable for half
its inputs and nothing said so.

A test whose fixture cannot produce the real shape is green and proves nothing
about the real shape.

## Reading `probe`'s vacuity table

    .venv/bin/python scripts/canopus.py probe tests/contract/{YYYY-MM-DD}-{slug}/

Run it before the approval commit and put the table in front of the operator.
Three groups need naming, and each asserts nothing yet:

- **`passed`** — already green with no implementation. It decides nothing about
  the work being approved.
- **`vacuous`** — red only because the code is absent. The probe null-stubs the
  missing modules and runs the contract twice, each stub carrying different
  values; a test that never FAILS under either run is vacuous, because an
  outcome invariant to the stub proves nothing. Passing, skipping and erroring
  all leave a test unproved; only a failure shows it read the value. An error
  is often the probe's own stand-in reaching a caller that type-checks its
  argument, and `probe` names those tests on stderr so they can be told apart
  from a test that genuinely asserts nothing.
- **`skipped`** — never ran at all. `probe` refuses a skip-family MARKER
  (`skip`, `skipif`, `xfail`, on a test, on a class, or as `pytestmark`) that
  states no reason, so an undocumented parking now fails the gate rather than
  waiting for your eye. It does NOT read a `pytest.skip()` called inside a test
  body or a module-scope `pytest.importorskip`; both skip for real and both are
  still yours to catch here.

Strengthen or justify all three. A contract whose every red test is vacuous
decides nothing, and a skipped or `xfail` test does not buy it a pass.
`vacuity was NOT measured` means nothing was proved either way; say so, because
it is not a clean bill.

`probe` also runs three wrong implementations that EXIST — `none`, `echo`,
`greedy` — and prints what each took of the red set. `greedy 2 of 3` says two of
those checks are greps for a word, not assertions about a value.

## When the slice ships

Promote the still-valid coverage into the ordinary suite and remove
`tests/contract/{YYYY-MM-DD}-{slug}/`. Left in place it binds every later slice
to this one's behaviour. Record both moves in the slice note: `retired_sha` is
the commit that removed the contract, `promoted_to` the file carrying the
coverage now.

## Voice

Internal engineering prose: terse and concrete. Never use `--` (two ASCII
hyphens) as punctuation, and no em-dash either; restructure the sentence. Use 31C
terminology exactly: **ODUN.ONE**, **DPI+**, **Tribe**, **TrustONE**. No hidden
Unicode. Criteria and contracts are factual claims — never fabricate a metric, a
threshold, or a behaviour. Derive it from the plan, or ask.
