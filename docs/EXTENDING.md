<!-- version: 1.1.0 | last-updated: 2026-07-26 -->
# Extending the engine

How to build on HEADING OS: add a skill, a rule, or a script, and clear the gates
before declaring it done. This is the developer how-to. The contribution policy (open
an issue first; pull requests are by invitation) lives in
[CONTRIBUTING.md](https://github.com/mishahanin/heading-os/blob/main/CONTRIBUTING.md);
read it before sending code.

---

## 0. Dev setup

```bash
uv sync --all-extras --group dev   # core + all integration extras + dev tools (pytest, ruff, pre-commit)
pre-commit install                 # arm the commit-time gates (once per clone)
uv run python scripts/run-tests.py
```

A green test run on a fresh clone means your environment is sound.

---

## 1. The shape of the engine

Four kinds of artifact, each with its own home and conventions:

| Artifact | Lives in | Is |
|---|---|---|
| Skill | `.claude/skills/{name}/SKILL.md` | a slash-command workflow, routed from natural language |
| Rule | `.claude/rules/*.md` | always-on or path-scoped behavior the agent follows |
| Script | `scripts/*.py` | a CLI tool or daemon; shared code in `scripts/utils/` |
| Hook | wired in `.claude/settings*.json` | a `PreToolUse` / `PostToolUse` / `SessionStart` guard |

Before building anything: search for an existing pattern and reuse it. The standards
below are summarized from the engine's own development rules.

Editing the documentation site itself (`docs/`) has its own contract: every page is
either Markdown-sourced (regenerated) or hand-authored HTML, and a drift guard fails
the build if the two fall out of sync. See [DOCS-PIPELINE.md](DOCS-PIPELINE.html)
before editing anything under `docs/`.

---

## 2. Writing a skill

A skill is a folder with a `SKILL.md`. The frontmatter is a contract:

```yaml
---
name: example-skill                    # kebab-case
description: >                          # what it does, when to use, AND when NOT to
  One paragraph. Name the alternative skill for the cases this one should not handle.
argument-hint: "[target]"
allowed-tools: "Read, Bash(python3:*)"  # least privilege
metadata:
  author: Your Name
  email: you@example.com
  version: "1.0"
x-heading-orchestration:                    # how the orchestrator may dispatch it
  parallel_safe: false                  # true | partial | false
  shared_state: []                      # paths it writes to
  triggers: ["example phrase"]          # natural-language triggers, or []
---
```

Rules of the road:

- **Body under 500 lines.** Overflow goes in a `references/` subdirectory.
- **Phased structure.** Phase 0 loads context, Phase 1 executes, Phase 2 synthesizes,
  Phase 3 outputs. Include a `NEVER` section listing prohibitions.
- **Routing-sensitive skills ship `triggers.json`** (6 to 10 positive and negative
  cases) and an entry in the skill router, so a new skill cannot silently hijack
  another's queries.
- **Invocation control.** Add `disable-model-invocation: true` for high-blast-radius
  skills that should fire only on an explicit slash command.

The `/skill-creator` skill scaffolds and evaluates a new skill against these
standards.

---

## 3. Writing a rule

Rules in `.claude/rules/` load automatically. A rule with no frontmatter is always
active; a rule with a `paths:` list loads only when work touches those paths:

```yaml
---
paths:
  - "scripts/**"
---
```

Keep rules concise and single-purpose. Several existing rules encode security controls
(the send-gate, the engine/data separation, the secret guards); adapt brand and voice
rules freely, but leave the security ones in place.

---

## 4. Writing a script

```python
#!/usr/bin/env python3
"""One-line purpose. Usage examples in the docstring."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root   # reuse, don't reinvent
# ... argparse CLI ...
if __name__ == "__main__":
    main()
```

- **Naming.** `kebab-case.py` for CLI scripts you invoke directly; `snake_case.py` for
  anything in `scripts/utils/` or imported as a module (hyphens are illegal in Python
  module names).
- **Reuse `scripts/utils/`** for workspace paths, colors, API-key loading, and `.env`
  reads. Never hardcode paths; use `pathlib.Path` and the workspace helpers.
- **Data goes through the seam.** Write artifacts via the data-root helpers
  (`get_data_root()` / `get_*_dir()`), never into the engine tree.
- **Catch `HTTPError` before `URLError`** (the former is a subclass).

---

## 5. The gates before "done"

Run these on anything you add or change:

```bash
uv run python scripts/sanitize-text.py <file> --scan   # zero hidden characters
uv run python -m py_compile <script>                    # Python syntax
uv run python scripts/run-tests.py                      # the suite
```

`ruff` (the linter) and the secret scan run automatically through `pre-commit`. Then:

- **The pre-push gate.** `push-all.py` runs the full regression suite (parallelized
  with `pytest-xdist`) before a push is allowed, plus the unbypassable secret content
  scan. Budget time for it; do not work around it.
- **CodeQL** runs on the repository for static security analysis; address what it
  flags on a pull request.

### Freezing the test contract

`scripts/canopus.py` locks a set of paths so the tests that prove a change cannot be
edited by whoever is making the change.

```bash
python scripts/canopus.py approve tests/test_thing.py --label my-slice \
    --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
# read the candidate, then COMMIT that artifact: the commit is the approval
python scripts/canopus.py freeze tests/test_thing.py --label my-slice \
    --anchor ../my-notes-repo/plans/2026-07-25-pre-impl-my-slice.md
python scripts/canopus.py verify
python scripts/canopus.py status
python scripts/canopus.py release --ship --reason "slice shipped"
```

`approve` measures the contract, prints a COUNT of how many of its tests are
already green before any code exists (`contract  1 of 3 already green before this
approval`), computes the **root hash**, and writes it into the gate artifact. It
freezes nothing. The per-test table, naming which tests are green and which
assert nothing, is `probe`'s output, not `approve`'s; run `probe` when you want
to read it. You then read the artifact and COMMIT it: that commit is the
approval, carrying an author and a timestamp.

`freeze` recomputes the same hash and refuses a root that the artifact's
COMMITTED copy CONTRADICTS. Be exact about the scope of that refusal, because
the loose reading flatters it: where the committed copy records no hash at all,
which is the state of a first freeze, an untracked artifact, or a folder outside
any repository, there is no approval to disagree with, so the freeze is TAKEN
and reads amber. Either copy satisfies the check, the committed one or a freshly
approved working copy, and the committed copy is what governs the lock and the
approval axis afterwards. A freeze taken over an approval nobody has committed yet is
permitted, says on the way out that it is uncommitted, and reads amber; `verify`
reports `LOSS OF LOCK` until the commit lands. Refusing there instead would leave
the operator with no manifest at all and a silently green suite, which is the one
outcome worse than a red lock.

`freeze` writes a per-file digest manifest to a gitignored `.canopus/` and records
the event in an append-only ledger. It no longer writes the anchor line, because
a tool that writes the hash and then verifies the hash it wrote has verified
nothing. From then on `verify` reads the expected hash from that artifact by
itself. Nobody types a digest and nobody compares one by eye.

Three ways to name a path, and they mean different things:

| Form | Effect |
|---|---|
| positional | a file freezes by content plus a composition guard on every ancestor; a directory freezes recursively |
| `--contract DIR` | recursive, plus a per-file item count recorded at freeze time, and a refusal unless the contract is red for a reason that means something |
| `--content FILE` | the bytes only, with no composition guard on any ancestor |

`--content` is how the enforcer files are frozen. Frozen as ordinary files they
would guard `scripts/` and `scripts/utils/`, and a build that cannot create a
file there cannot build anything.

`canopus probe DIR` runs a contract set and prints its per-test outcomes without
freezing anything. `canopus pack` prints the Fix 2 evidence page: both
indicators, collected against baseline, commits made while no freeze was held,
whether the attestation has gone stale, and what is not covered.

**Red is not the same as meaningful.** A contract can be red only because the
module it imports does not exist yet, which says nothing about whether its tests
assert anything. So `probe` always, and `approve` and `freeze` whenever
`--contract` names the set, run the contract a second time with every absent
module resolved to a mock. A test that was RED for real and PASSES that run is
proved to assert nothing: its only failure was the absent import, and a mock
satisfies any shape it was asked for. Only the red ones are weighed, because a
test that was already green passes the mocked run too and proves nothing by it.
Those tests print as `vacuous  <name>  asserts nothing`; every OTHER red test is
labelled `import`, `assertion`, or `other` beside its outcome, and a vacuous one
carries no mode label. A SKIPPED test is printed as `skipped`, never as vacuous:
the refusal weighs red outcomes only, and the display has to agree with it or the
table describes a contract the refusal is not judging. A skipped test is the
quiet case to read, because `pytest.importorskip` skips for real and then passes
once the stub supplies the module. Nothing refuses it, so it carries the plain
warning `did not run, so it proves nothing` and the operator decides. A contract whose every red test is vacuous is REFUSED,
and a skipped or `xfail` test in the set does not buy it a freeze. Partial
vacuity is printed and not refused, because "these three assert nothing" is a
judgement for a human, and a test that legitimately asserts absence lands on
that list. The cost is stated rather than hidden: those runs are two pytest
sessions over the contract, and the second is what buys the proof (`approve` and
`freeze` skip it once the first run has already earned a refusal).

**A retake needs a named waiver, not a workaround.** The redness rule is right
about a first freeze and wrong about the last one: once a slice has implemented
its contract, every row is green and the same rule refuses the retake. Passing
the contract directory positionally gets past it and silently gives up
everything `--contract` buys, so `approve` and `freeze` take
`--contract-satisfied REASON` instead. The reason is the flag's VALUE, so it
cannot be passed without one, and a reason of pure whitespace takes NO waiver and
says so rather than degrading in silence. It waives EXACTLY the redness refusal:
a contract file that collected nothing is still refused, and the baseline, the
ledger note, and the pack's contract section all survive. On a contract that is
actually red the flag changes nothing and says so out loud, so it cannot quietly
become a habit that hides redness.

Be exact about the vacuity proof on that path, because the loose reading is the
flattering one. The null stub weighs RED tests only, so on the wholly green
contract the waiver exists for it has nothing to weigh and returns no verdict:
`approve` and `freeze` therefore SKIP that second pytest session instead of
spending a minute on an answer that cannot matter. A retake is not re-proved
non-vacuous; it inherits the proof the first freeze earned while the contract was
red. `probe` still runs the stub unconditionally, because there the verdict is
the output rather than an input to a refusal.

The reason prints at the surface, is recorded in the `.canopus/history.jsonl`
entry for that `approve` or `freeze`, AND is written onto the anchor artifact by
`approve` as a `canopus-contract-satisfied:` line above the approval it belongs
to. That last one is the durable half: `.canopus/` is gitignored and one
`rm -rf` removes it, while the anchor is the artifact a human commits in another
repository. `canopus pack` reads it back, bound to this freeze's root hash, and
prints `CONTRACT WAIVED` on the evidence page, so a freeze that passed the
redness rule on a stated reason never reads like one that earned it.

Say what the instrument cannot do. It learns which modules were absent by
reading the failure text the contract's own tests produced, so a test that
suppresses its exception chain (`raise AssertionError(...) from None` around the
import) hides the absent module, nothing is stubbed, and no test can be proved
vacuous. That direction is fail-open and the contract author controls it. What
the commands do about it is refuse to stay quiet: when a contract is red but its
report names no absent module at all, `probe`, `approve`, and `freeze` print
`vacuity was NOT measured`, which is a different claim from measuring vacuity
and finding none. It is not a refusal, because tests failing on assertions
against code that already exists produce the same reading and are a perfectly
ordinary contract.

**Blinding the gate no longer pays, and what that does and does not cover.**
One environment variable used to defeat the lock at no cost: `resolve_anchor`
fell back to the artifact's WORKING copy whenever git answered `no_repo` or
`no_git`, and both answers came from a child `git` process whose environment the
builder controls, so `GIT_DIR=/tmp/nothing.git` in front of any command that
reached the gate turned a genuine `LOSS OF LOCK` into `LOCK HELD` with gate exit
0. Wire 2.2 closed it. The freeze now RECORDS which repository the anchor
belonged to, as a digest over that repository's root commits, and a manifest that
recorded a repository resolves RED whenever git answers anything else — a
different repository, no repository, or no git at all. The child environment is
scrubbed of every `GIT_*` variable by prefix rather than by a denylist, which
also fixed a real bug: git exports `GIT_DIR` and `GIT_INDEX_FILE` to its own
hooks, so the gate running inside a pre-commit hook was asking the wrong
repository about the anchor. Blinding is now strictly worse for a builder than
doing nothing at all.

Say what remains, because the flattering reading is that this makes the lock
adversary-proof. It does not defend against a builder who replaces the `git`
binary on PATH: every answer the gate binds to comes from a child process. The
identity binds a repository's LINEAGE, not an instance, so a `git clone` of the
anchor's repository carries the same root commits and reads as the same
repository; that is deliberate, because a relocated or cloned repository IS the
same repository, but it means the binding proves which history the anchor belongs
to and never which copy of it was read. And the PreToolUse deny is still only a
convenience: a shell `sed -i` walks past it, and `verify` plus the test gate are
the guarantee. The operator's countermeasure has not changed and is still worth
the ten seconds: run `canopus verify` YOURSELF, in your own shell, at the
approval gate, rather than reading a status the builder printed.

**The instrument has no committed human artifact behind it.**
`scripts/utils/canopus_contract.py` and `scripts/utils/canopus_nullstub.py` sit
OUTSIDE the frozen enforcer set, deliberately: they do their work BEFORE any
freeze exists, and there is nothing yet to freeze them against. So at the moment
the vacuity refusal fires, the builder can still edit both the refusal and the
tests that prove it, and the operator's only evidence that a contract was
measured is a table the builder printed. That is the same shape as the hole this
layer closed one level up, where `freeze` used to write the anchor line and then
verify the line it had written. Here it is unavoidable rather than overlooked,
which is exactly why it belongs in writing: the vacuity verdict is a courtesy
from the build to the operator, not a guarantee held against it.

An anchor artifact that already records a hash is refused, because an approved
contract's anchor is never silently overwritten. When the frozen SET legitimately
changes mid-build, `approve --replace --reason "<why>"` appends a second line and
writes an `anchor_replaced` entry to the ledger; the artifact keeps the whole
trail. The reason lands in the artifact too, on its own
`canopus-approval-reason:` line directly above the hash it explains, because
`.canopus/history.jsonl` is gitignored and one command removes it: without that
line, the durable record a human commits carries two indistinguishable hash lines
and no account of either. It is a separate line by necessity rather than by
taste. `read_anchor` takes everything after `canopus-anchor:` as the hash value,
so a reason appended to that line would be parsed as part of the digest; for the
same reason the text is collapsed to a single line before it is written.

**After the slice ships, the contract is retired.** A contract is a POINT IN
TIME: it exists to pin what "done" means for one slice, and its job ends at the
Fix 2 evidence pack. At ship time, promote its still-valid coverage into the
ordinary test suite and REMOVE the contract directory. Regression coverage then
lives in the ordinary tests, where a later slice may argue with it like any other
test. Leaving the directory in place instead keeps it running as an ordinary test
set, which quietly imposes a rule nobody adopted: that every later slice must
preserve every earlier slice's behaviour verbatim. Measured on this very tool, a
slice that deliberately reversed three pinned behaviours turned three earlier
contract tests red for doing exactly what it was approved to do.

While a contract is live it is never edited to match an implementation. A
contract that turns out to be genuinely wrong reopens the approval gate, where it
is re-approved deliberately and the superseded anchor is retired on the record.

`--anchor` is required. An anchorless freeze still catches a later edit, but it is the one
route to a *passing* gate that never leaves this clone: release, edit the contract,
re-freeze, amber, exit 0. With an anchor the same sequence fails, because the artifact
still holds the previously approved hash. Making re-baselining visible is what the anchor
is for.

`--root` defaults to this script's own repository root, never the shell's cwd. A freeze
taken from a subdirectory would otherwise write its state where neither the deny nor the
gate ever looks, and report success. A `--root` with no `scripts/run-tests.py` is refused
outright: a tree with no gate cannot enforce anything.

Three layers, and the differences matter:

- The PreToolUse deny is a **convenience**. It sees `Write`, `Edit`, `MultiEdit`, and
  `NotebookEdit` tool calls only, so a shell `sed -i` or an agent with its own toolset
  walks past it.
- `verify` is the **guarantee**. It recomputes digests from disk and catches a change made
  by any route.
- The test gate is what makes the guarantee **fire**. `tests/conftest.py` runs the check at
  pytest session start and `scripts/run-tests.py` runs it before the suite, so the ordinary
  routes into the suite do not reach green while the contract is moved — not `python
  scripts/run-tests.py`, and not the bare `pytest tests/test_thing.py` inner loop. A
  verification that is never invoked is worth nothing, however well its expected value is
  protected.

  One route is not covered, and it is named here rather than left to be discovered:
  `pytest --noconftest` skips conftest loading altogether, so the session-start check never
  runs. Measured: with a moved contract, `pytest tests/contract/s -q` aborts with exit 4,
  and the same command with `--noconftest` prints `2 passed` and exits 0. That is deliberate
  evasion rather than tampering by helpfulness, and the threat model this tool states is the
  latter, so the gap is accepted — but `scripts/run-tests.py` is then the only gate of
  record, and a slice signed off from a `--noconftest` run has not been gated at all.

`verify` reports one of three states:

| State | Meaning |
|---|---|
| `LOCK HELD` | the manifest is intact and matches the hash recorded in the anchor artifact |
| `LOSS OF LOCK` | something changed, or the anchor disagrees or vanished. Exit code 1. |
| `LOCK UNCONFIRMED` | no anchor hash is recorded yet: nothing changed since the last check, which is not the same as "this is the approved contract" |

`verify`, `status`, and `pack` print a third axis beneath the lock and the
attestation, answering whether this exact freeze is the one a human approved:

| State | Meaning |
|---|---|
| `APPROVED` | the frozen root hash is recorded in a COMMIT of the gate artifact |
| `APPROVAL UNVERIFIED` | the committed artifact records a DIFFERENT hash (the re-baseline case, and the commonest of the four), or the artifact is uncommitted or untracked, or it is outside a repository, or git is unavailable; the reason is printed beside it |

Amber rather than red on the second row, deliberately: an operator whose gate
artifact is a file in a folder has no repository to attribute an approval to, and
that is a supported way to use the tool rather than a failure of it.

The anchor must live outside the working tree. An anchor the build can write to is not an
anchor. Point it at a sibling repository with its own history, so a build reaching for the
anchor dirties a repository it had no reason to touch.

Be precise about what that trace is worth. `verify` reads the **committed copy**, through
`git show HEAD:<rel>`, whenever the artifact sits in a repository, and falls back to the
working copy only where there is nothing to consult: outside a repository, or with no git
available. `approve` writes the line and a human's COMMIT of it is the approval. So an
approval reachable only through git still holds the lock, and a line appended to the working
copy alone is an uncommitted diff in the sibling repository: visible in its `git status`,
erasable with `git checkout --`, and never enough on its own to read `APPROVED`. It is
evidence for a human who looks, not containment.

Freezing a directory is recursive. Freezing a path also guards the membership of every
ancestor directory up to and including the tree root, and each guard watches a stated set
of basenames rather than everything:

| Guarded directory | Watches | Why |
|---|---|---|
| the frozen directory itself | everything | a directory freeze asks whether anything at all moved inside it |
| each ancestor | `conftest.py` | the one file pytest imports without being told to, and the place a stub reaches `sys.path` |
| the tree root | `*.py` | `pyproject` declares `pythonpath = ["."]`, so the root is the first entry the contract's own imports resolve against |

The narrowing is what makes the guard usable. A first version watched the full membership
of every ancestor; that put 201 of this repository's 296 test files under a write deny and
made the builder's next ordinary unit test report `LOSS OF LOCK`. A guard that fires on the
builder doing its job gets routed around, and a routed-around guard protects nothing. After
the narrowing the same freeze denies 2 files, both of them frozen for a stated reason.

`status` prints each guard's filter beside it, because a line reading `dir tests/` on its own
invites the reading that everything under `tests/` is watched.

Composition lists **files**, so two things are outside it by construction: a package
directory appearing at the tree root, and a module dropped into another in-tree `sys.path`
entry such as `tests/`. Both are closed by practice rather than by the primitive: the
contract lives in its own directory under `tests/contract/`, which freezes recursively, so
anything appearing beside it is caught by content and by composition alike.

Every `conftest.py` from a frozen path up to the tree root is additionally frozen by
**content**. A composition guard records member paths only, so a conftest sitting beside a
frozen test used to be listed and never hashed — and that file is exactly where a good-faith
edit changes what the contract measures without moving anything the guard watches, because
filtering inside `pytest_collection_modifyitems` fires no deselection hook. The cost is
deliberate: a slice that legitimately edits a `conftest.py` mid-build gets `LOSS OF LOCK`,
which under this standard is the correct answer.

If the manifest is ever damaged, every write is denied fail-closed. Clear it with
`release --force --window --reason "<why>"`, which is logged. A release names its
kind, so `--force` alone exits 2. Deleting `freeze.json` by hand also works and
deliberately leaves a gap in an append-only ledger.

### Did the frozen tests actually run?

The lock states answer "did the contract move". They cannot answer "did the contract run":
`pytest -k`, `--deselect`, `--ignore`, and `--lf` all reach green with every frozen byte
intact. A builder that cannot edit a frozen test can decline to run it. A bare path or a
node id no longer does, for any file carrying a freeze-time baseline: the collected count
is compared against that baseline, so a subset reports 1 of 7 and does not attest.

So the root `tests/conftest.py` writes an attestation to `.canopus/attest.json` at session
finish, and `verify` and `status` print a second line beneath the lock state:

| State | Meaning |
|---|---|
| `ATTESTED` | a run of THIS exact contract collected every frozen test file in full, deselected none of them, and passed |
| `NOT ATTESTED` | absent, recorded against a different root hash, incompletely collected, or carrying failures; the recorded reasons print beneath it |

The record is **last-write-wins**, and the canonical gate must therefore be the LAST thing
you run before reading `status` or `pack`. Measured during the wire 2 sign-off: the gate
attested at 20:07:53, and a pre-commit hook's partial pytest run overwrote it two minutes
later with "collected nothing". That is the correct side to fail on, because the record
describes the LAST run and a stale-but-good record would lie about now, but it means a
green record is not durable across a later partial run.

The record measures **collection, never invocation**. Deselection is observed through
pytest's own deselection hook, which `-k`, `-m`, `--lf` and `--deselect` all route through,
and completeness is `passed + skipped == collected` per frozen file. Nothing inspects a
command-line flag, which is what lets `-m "not acceptance"` — the marker expression
`run-tests.py` always passes — attest normally: it deselects nothing inside a frozen test
file. Under `pytest-xdist` the controller runs no collection of its own, so it seeds its
tally from the workers' node ids and folds in the deselection counts they ship home; only
the controller writes, because a worker holds a partial tally and its own exit status.

**What attestation claims, precisely.** It covers the frozen test CONTRACT, not the
artifact: it says a run of this exact contract was green, not that the implementation now
in the tree is the one that produced it. Binding the record to the working tree was
considered and deferred — as specified it makes `NOT ATTESTED` the ambient state, and an
amber line that is amber all day stops being read. A module-level
`pytest.skip(allow_module_level=True)` prevents collection, so such a file collects nothing
and cannot attest; that is what "measure collection" means, and nothing works around it.

**Attestation blocks nothing and changes no exit code.** It cannot: the gate that would act
on it runs at session start, before the run it would attest has finished. It is a passive
record, read by a human at sign-off, where an evidence pack without `ATTESTED` cannot be
assembled. The record is bound to the recomputed root hash, so editing any frozen file
after a green run makes it stop applying without anyone remembering to delete it. It is a
true statement about *a* run of this exact contract, not necessarily the most recent one.

**Freeze the enforcers, all eight of them.** A freeze that omits them protects the
contract while leaving the thing that checks the contract editable.

| File | Why it is in the set |
|---|---|
| `scripts/utils/canopus_freeze.py` | hashes the manifest and answers "did it move" |
| `scripts/utils/canopus_gate.py` | the check pytest and the runner call |
| `scripts/utils/canopus_git.py` | resolves the anchor, so it decides `LOCK HELD` against `LOSS OF LOCK`; a decider outside the freeze is the same hole closed for the write path |
| `scripts/run-tests.py` | one of the two places the gate fires |
| `tests/conftest.py` | the other, at pytest session start |
| `scripts/utils/atomic.py` | writes the manifest, so it is the write path of the guarantee |
| `scripts/utils/venv.py` | re-execs the interpreter, so it chooses which Python runs the gate |
| `scripts/utils/colors.py` | imported by both of the above |

The last three are the transitive import tail: leaving them out put the write
path of the guarantee outside the guarantee. A closure test in
`tests/test_canopus_freeze.py` recomputes the tail and fails when a new import
escapes the set.

`.canopus/` is gitignored, so CI carries no manifest and neither the gate nor attestation
fires there. The whole mechanism is scoped to the local build loop, and an evidence pack
should say so.

Be precise about what that ledger proves. It lives inside the same gitignored `.canopus/`
directory as the manifest, so it is evidence against an *edit to* `freeze.json`, and not
against deletion of the directory: `rm -rf .canopus` takes the ledger with it, after which
the gate returns 0 in silence because it cannot tell "no freeze was ever taken" from "the
freeze was removed", and git never saw either. Nor does the ledger record the gate: a
passing gate writes nothing, so the absence of a `verify_fail` line does not mean the
contract was verified. The gate's evidence is its exit code in the test output; the durable
evidence that a contract was approved is the anchor artifact, committed in the other
repository.

A contract that turns out to be wrong is not edited in place. Release it, fix it,
re-freeze it, and get the new root hash re-approved.

### Known defects, in files the active freeze holds

Named here rather than fixed, because each lives in a file the current freeze
covers and a frozen file is not edited in place:

- `canopus_gate.py:43` claims "a build cannot reach green while its contract is
  moved". The environment defeat above falsifies that sentence as written.
- The PreToolUse deny message in `canopus_freeze.py` reads "The contract is not
  editable in place. Fix the code instead", which is wrong in the one case where
  the frozen file IS the code being fixed, and that case is this tool's own
  maintenance.

---

## 6. Testing discipline

The suite lives in `tests/` (security tests in `tests/security/`). Every behavior you
change needs a test that exercises the real pattern through the public interface, not
an implementation detail. Write one test, make it pass, then the next.

When debugging, build a fast reproduction first: a failing test or a deterministic
harness that makes the bug appear and disappear on demand. Do not hypothesize about a
cause you cannot reproduce. Write the regression test before the fix, watch it fail,
apply the fix, watch it pass.

---

## 7. Restraint

- **Simplicity.** The minimum artifact that solves the problem. No speculative
  features, flags, or abstractions for single-use code.
- **Surgical changes.** Touch only what the task requires. Do not refactor adjacent
  code, and match the style of the file you are editing.
- **No new dependency** without raising it in the issue first; pin exact versions.
- **Security and review findings override restraint.** Fix an open finding in a file
  you touch before the requested change, and say so if it widens the diff.

---

## 8. Reference

| File | Role |
|---|---|
| [`CONTRIBUTING.md`](https://github.com/mishahanin/heading-os/blob/main/CONTRIBUTING.md) | Contribution policy (issues, PR by invitation) |
| `scripts/run-tests.py` | The test runner |
| `tests/`, `tests/security/` | The regression suite |
| `.claude/skills/skill-creator/` | Scaffolds and evaluates a new skill |
| `scripts/utils/` | Shared modules to reuse |
| `pyproject.toml` | Pinned dependencies, ruff / pytest config |

---

*HEADING OS · Extending the engine · maintained by Misha Hanin · see also
[Architecture](ARCHITECTURE.html) for how the pieces compose and
[Security model](SECURITY-MODEL.html) for the controls your code inherits.*
