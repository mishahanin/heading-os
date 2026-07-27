<!-- version: 1.3.0 | last-updated: 2026-07-27 -->
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
permitted, and it says on the way out WHICH of the two uncommitted states it is
in, because they end differently. Where the commit records no hash at all it
prints `approval unverified`, and `verify` reads amber, `LOCK UNCONFIRMED`, until
someone commits. Where the commit records an older hash while the candidate this
freeze took sits on the working copy alone, it prints `approval uncommitted`, and
`verify` reports `LOSS OF LOCK` until the commit lands. Refusing there instead would leave
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
freezing anything. `canopus pack` prints the Fix 2 evidence page: all three axes
(lock, attestation, approval), collected against baseline, the `interpreter`
block describing what configured the attesting run (which `pack` alone prints),
commits made while no freeze was held, whether the attestation has gone stale,
and what is not covered.

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
repository. `canopus pack`, `verify` and `status` all read it back, bound to this
freeze's root hash, and print `CONTRACT WAIVED`, so a freeze that passed the
redness rule on a stated reason never reads like one that earned it — including
on the command an operator is told to run for themselves.

Every one of those surfaces prefers the COMMITTED copy of the artifact and falls
back to the working file only where no committed copy exists, which is the
precedence the anchor hash itself already used. Reading the waiver from the
working file while the lock and the approval came from HEAD meant one `sed -i`
took `CONTRACT WAIVED` off the evidence page while `LOCK HELD` and `APPROVED`
stood and HEAD still carried the waiver.

Both writes are bound to the ACT rather than to the flag. A run where the
contract is red, or where no contract ran at all, takes no waiver, so nothing is
written to the artifact or the ledger however the flag was spelled; the command
says the flag changed nothing, and the record says the same. The two halves are
then held together rather than trusted to arrive together:
`freeze --contract-satisfied` over an approval that records no matching waiver in
either copy is REFUSED, because the evidence page reads its `CONTRACT WAIVED`
marker off the artifact and only `approve` writes it there.

Be honest about what the reason is worth. It is EVIDENCE, not a tamper-proof
record. It rides on the anchor artifact beside the approval, exactly like
`canopus-approval-reason:`, and neither line is inside the root hash: a human who
reads the artifact sees the waiver and its stated reason, and nothing recomputes
it or fails when it is edited. What the hash protects is the SET that was frozen,
not the prose explaining why it was accepted.

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

**Blinding the gate, measured before and after.**
Wire 2.1 shipped one Critical open, and the account below is what a fixture
measured rather than what the repair intended. `resolve_anchor` fell back to the
artifact's WORKING copy whenever git answered `no_repo` or `no_git`, and both
answers came out of a child `git` process whose environment the builder controls.

Measured at commit `7bd1744`, the last state before any of this landed, over a
freeze whose approval line sat on the working copy and in no commit:

| Run | Reported | Exit |
|---|---|---|
| control | `LOCK UNCONFIRMED` | 0 |
| `GIT_DIR=/tmp/nothing.git` | `LOCK HELD` | 0 |
| the anchor repository's `.git` renamed | `LOCK HELD` | 0 |
| `git` removed from `PATH` | `LOCK HELD` | 0 |

Three ways to spend nothing and buy a green gate over an approval no human had
committed, and none of them leaves a trace on the tree.

Measured again on the same fixture with wire 2.2 in place:

| Run | Reported | Exit |
|---|---|---|
| control | `LOCK UNCONFIRMED` | 0 |
| `GIT_DIR=/tmp/nothing.git` | `LOCK UNCONFIRMED` | 0 |
| the anchor repository's `.git` renamed | `LOSS OF LOCK`, `git now answers no_repo` | 1 |
| `git` removed from `PATH` | `LOSS OF LOCK`, `git now answers no_git` | 1 |

The second row is the shape of the environment fix: the variable no longer
CHANGES the answer, because the child environment is stripped of every variable
whose name begins `GIT_`, by prefix rather than by a denylist naming the ones
somebody thought of. That prefix also repairs a plain correctness bug rather than
only a security one. Git exports `GIT_DIR` and `GIT_INDEX_FILE` to its own hooks,
the engine's pre-commit and pre-push hooks run the suite, the suite starts the
gate — so the gate was asking the HOOK's repository about an anchor that lives in
another one.

**The anchor's repository, recorded and re-checked.**
The manifest now carries `anchor_repo`: whether the anchor was inside a
repository at freeze time, and if it was, which one. The identity is a sha256
over that repository's sorted ROOT COMMITS, newline-joined. A toplevel path would
have been cheaper and is wrong, because a relocated repository is the same
repository and this workspace has been relocated once already; a merged history
can carry more than one root commit, so the whole sorted set is hashed rather
than one line of output.

`anchor_repo` is inside the root hash, beside the recipe, the anchor path, the
file and directory maps, the per-file baseline, and (from wire 2.3) the plugin
set the freeze captured. That placement is the load-bearing
half: outside the hash, a builder edits the binding to `in_repo: false`, wins the
working-copy fallback permanently, and the committed approval still matches.
Inside it, the same edit moves the root and the human's committed approval stops
agreeing.

Five outcomes, judged on every `verify`, `status`, `pack`, and test-gate run:

| Recorded at freeze time | What git answers now | Result |
|---|---|---|
| a repository, identity X | the same identity X | intact; the approval is read from the COMMITTED copy |
| a repository, identity X | a different identity | RED, `unbound`: the anchor is inside a different repository than the freeze recorded |
| a repository | no repository, or no git at all | RED, `unbound`: this is the blinding case |
| no repository (a file in a folder) | still no repository | intact; the working copy governs, and the approval axis stays amber |
| no repository | a repository | RED, `unbound`: the freeze was taken blind, so release and re-freeze |

Two states are refused at freeze time rather than recorded. When git cannot be
consulted at all, `approve` and `freeze` refuse: writing `in_repo: false` there
would record the positive claim that the anchor is outside any repository, which
the tool has no evidence for and which is probably untrue, and every later
`verify` would then blame the blinding rather than name the real cause. When the
anchor's repository has no commits, they refuse too: the first commit into it is
the approval act itself, so an identity recorded now would change at the exact
moment a human approved. Both refusals are checked BEFORE the contract runs, so a
`freeze --contract` does not spend two pytest sessions on its way to being told
to go and commit something.

A broken binding is RED with exit 1, never amber. Amber exits 0, and a builder
who blinded the gate would keep the entire payoff of the bypass while paying only
a colour change. The bypass has to cost more than doing nothing, and red with
exit 1 is what makes it cost more.

**What the binding does not buy.**
Nothing here defends against a builder who replaces the `git` binary on PATH, or
who forges commits in a repository they control. Every answer the gate binds to
comes from a child process, and a process the builder owns cannot prove itself.
Name the PRICE of that, because the argument for this whole layer turns on it: it
is one command. Measured, a five-line shell script named `git`, placed earlier on
PATH, passing every invocation through except `git -C <dir> show HEAD:<rel>`,
which it answers with the working file instead, converts a re-baselined contract
whose approval was never committed from `LOSS OF LOCK` at exit 1 into `LOCK HELD`
at exit 0. `verify` prints `LOCK HELD` and then `APPROVED`, and the anchor
repository shows nothing but an unstaged modification.
The operator's countermeasure is unchanged and still worth the ten seconds: run
`canopus verify` YOURSELF, in your own shell, at the approval gate, rather than
reading a status the builder printed.

The identity binds a repository's LINEAGE, not an instance. A `git clone` of the
anchor's repository carries the same root commits and reads as the same
repository, so an anchor repointed at a clone that carries a forged approval
passes the binding check. That is deliberate — a relocated or cloned repository
IS the same repository — and it means the binding proves which HISTORY the anchor
belongs to and never which copy of it was read. Three legitimate events move the
identity and cost one release-and-re-freeze each: checking out an orphan branch
with its own root, merging in a history carrying another root commit, and a
shallow clone, whose grafted boundary commit reads as a root until
`git fetch --unshallow`.

**That identity DEFINITION is now pinned, and the fixture that once loosened it
is repaired.** The wire 2.2 contract claimed a wrong definition of repository
identity would be caught, listing "a toplevel path, an unsorted join, a different
separator, a trailing newline". Measured by mutation on 2026-07-27, while the
contract was still in place: the toplevel-path and trailing-newline variants were
killed, and the other two were not. Removing `sorted(...)` from `repo_identity`,
and changing the newline separator to a comma, each left all 449 cases green (the
438 in `tests/test_canopus_*.py` plus the eleven contract cases). The cause was
the fixture rather than the argument: no fixture built a repository with more
than one root commit, so sorted, unsorted and any separator collapsed onto the
same one-element digest.

Retiring that contract carried the test into
`tests/test_canopus_gate.py::test_a_committed_approval_in_the_bound_repository_reaches_lock_held`
with the fixture repaired. The anchor repository now merges in an orphan root, and
the orphan is chosen so `rev-list` emits the two roots in an order that is NOT the
sorted one, which is what makes an unsorted implementation differ from a sorted
one. Both mutations now fail that test, one case each, reproduced over three runs.
The test still computes the identity by hand rather than calling `repo_identity`,
because every other binding test in the module builds its manifest through the
implementation and so moves both sides of the comparison together.

The contract's SC-1 test, the parametrised `GIT_*` poisoning one, retired to
`tests/test_canopus_git.py::test_no_single_git_variable_hides_the_repository`
rather than being kept as written. Its gate-level assertion was directional
("never green"), and a directional assertion cannot fail on this seam: removing
the `GIT_*` scrub entirely left all eleven contract cases passing, because a
blinded gate answers RED, which the direction permits. The port asserts instead
that `repo_identity` returns the SAME answer under each variable one at a time,
which is the form that bites: narrowing the scrub to a two-name denylist
(`GIT_DIR`, `GIT_WORK_TREE`) failed nothing anywhere in the suite before the port
and fails two cases after it, `GIT_COMMON_DIR` and `GIT_OBJECT_DIRECTORY` being
the members that reached past the denylist.

`.canopus/` is gitignored, so the ledger recording release windows and forced
escapes can be deleted by the builder in one command, and git never saw it.

And the PreToolUse deny is still only a convenience: a shell `sed -i` walks past
it, and `verify` plus the test gate are the guarantee.

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

  Two routes are not covered, and they are named here rather than left to be discovered.
  `pytest --noconftest` skips conftest loading altogether, and `--confcutdir` pointed at the
  contract's own directory stops the walk below the root conftest, so in both the
  session-start check never runs. Measured on a scratch tree with a moved contract: bare
  `pytest tests/contract/s -q` aborts on the gate, while `--noconftest` and
  `--confcutdir=tests/contract/s` each print `2 passed` with no canopus line at all. Wire
  2.3 did not touch either. That is deliberate evasion rather than tampering by
  helpfulness, and the threat model this tool states is the latter, so the gap is accepted
  — but `scripts/run-tests.py` is then the only gate of record, and a slice signed off from
  such a run has not been gated at all.

`verify` reports one of three states:

| State | Meaning |
|---|---|
| `LOCK HELD` | the manifest is intact and matches the hash recorded in the anchor artifact |
| `LOSS OF LOCK` | something changed, or the anchor disagrees, or it vanished, or its repository binding broke. Exit code 1. |
| `LOCK UNCONFIRMED` | no anchor hash is recorded yet: nothing changed since the last check, which is not the same as "this is the approved contract" |

`verify` and `status` also print the anchor's own status in brackets beside its
path, and `pack` prints it beside the lock line. It is the finer reading behind
the three states above:

| Anchor status | Meaning |
|---|---|
| `recorded` | the artifact carries a `canopus-anchor:` hash; whether the lock is held then turns on whether it matches |
| `unrecorded` | the artifact exists and no approval is written in it yet. Amber. |
| `missing` | the artifact is gone. Red, even when a commit still records the hash: a vanished anchor is evidence. |
| `unbound` | the anchor's repository binding broke, so no copy of the artifact is consulted at all. Red, and the reason prints on the approval axis. |
| `none` | the manifest carries no anchor. The CLI refuses an anchorless freeze, so this reaches you only from a manifest an older CLI or the library wrote. |

`verify`, `status`, and `pack` print a third axis beneath the lock and the
attestation, answering whether this exact freeze is the one a human approved:

| State | Meaning |
|---|---|
| `APPROVED` | the frozen root hash is recorded in a COMMIT of the gate artifact |
| `APPROVAL UNVERIFIED` | the committed artifact records a DIFFERENT hash (the re-baseline case, and the commonest of them), or the artifact is uncommitted or untracked, or it is outside a repository, or git is unavailable, or the repository binding broke; the reason is printed beside it |

Amber rather than red on the second row, deliberately: an operator whose gate
artifact is a file in a folder has no repository to attribute an approval to, and
that is a supported way to use the tool rather than a failure of it. One entry in
that row is the exception and it is amber only on this axis: a BROKEN binding
sets `APPROVAL UNVERIFIED` here while the lock axis beside it goes red with exit
1, so nothing about a blinded gate ends in a 0.

The anchor must live outside the working tree. An anchor the build can write to is not an
anchor. Point it at a sibling repository with its own history, so a build reaching for the
anchor dirties a repository it had no reason to touch.

Be precise about what that trace is worth. `verify` reads the **committed copy**, through
`git show HEAD:<rel>`, whenever the artifact sits in a repository, and falls back to the
working copy in one case only: the freeze recorded NO repository, and git still answers
`no_repo` or `no_git`, so there is nothing to consult. A freeze that recorded a repository
and now cannot see it does not fall back at all —
it reddens, which is the whole of the wire 2.2 repair above.
`approve` writes the line and a human's COMMIT of it is the approval. So an
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
| the tree root | `*.py`, plus every importable subdirectory | `pyproject` declares `pythonpath = ["."]`, so the root is the first entry the contract's own imports resolve against, and a package directory shadows an import as readily as a module file |

The narrowing is what makes the guard usable. A first version watched the full membership
of every ancestor; that put 201 of this repository's 296 test files under a write deny and
made the builder's next ordinary unit test report `LOSS OF LOCK`. Those two figures are the
count on the day the narrowing was measured, not a running total: on 2026-07-27 the tree
carries 288 `test_*.py` files and 298 `.py` files under `tests/`. A guard that fires on the
builder doing its job gets routed around, and a routed-around guard protects nothing. After
the narrowing the same freeze denies 2 files, both of them frozen for a stated reason.

`status` prints each guard's filter beside it, because a line reading `dir tests/` on its own
invites the reading that everything under `tests/` is watched.

The tree-root guard lists **importable subdirectories** beside `*.py` files, each rendered
with a trailing `/` so a directory `plug` and a file `plug` can never produce the same line.
Importable is `str.isidentifier()` minus the cache names, deliberately not a denylist of
suspicious ones: `.git`, `.venv` and `.canopus` fall out because a leading dot is not an
identifier, while a directory named for a Python keyword passes and is watched, which is the
safe direction. Until wire 2.3 the composition listed files only, so `plug/__init__.py`
dropped at the root shadowed an installed distribution while every guard read green.

**The deny does NOT refuse what this guard watches, and that gap was tried, measured and
withdrawn inside this slice.** The PreToolUse hook matches a new file's BASENAME against
the guard's patterns, so a file written INSIDE a newly created root directory has that
directory as its parent, matches no guard entry, and is not denied: verification reddens
and the write still lands. Wire 2.3 briefly closed that by refusing any Write whose path
would add a watched directory to a guard's recorded members, then reopened it deliberately.
The reason is recorded in full on the open list below; the short form is that the wider deny
refused ordinary writes to data-routed top-level names absent from a fresh engine clone, and
a guard that reddens on ordinary work is one an operator learns to release around.

**Pytest adds no second in-tree `sys.path` entry, and the reason previously recorded for
that was false.** An earlier revision of this page said a module dropped into another
in-tree entry such as `tests/` was outside the composition by construction, closed "by
practice" because the contract freezes recursively. The practice half is true. The route it
excused is not live at all: `pyproject.toml` sets `--import-mode=importlib`, under which
pytest inserts no basedir for a collected test file. Measured on a scratch tree carrying
that setting, `sys.path[:2]` inside `tests/contract/<slice>/test_contract.py` was the tree
root twice and nothing else, and a package at `tests/plug/` and a package beside the
contract both raised `ImportError`. Say only what that establishes: pytest contributes no
tree-owned entry beyond the root, which is what the `pythonpath = ["."]` guard has to cover.
It is not the claim that nothing importable lives under the tree at all, which would be
plainly untrue of `.venv/…/site-packages`, and is why `_library_dirs` exists to tell an
interpreter library from an in-tree file in the first place. A first attempt at the same
measurement was taken on a
scratch tree missing this repository's own `addopts`, read pytest's PREPEND mode instead,
and produced a trigger that can never fire here; it is corrected rather than quietly
dropped, because a false reason left standing is what the next slice reasons from. What
replaces it is narrower and open: the import mode is one line of `pyproject.toml`, and that
file is neither frozen by content nor watched by the root guard.

Every `conftest.py` from a frozen path up to the tree root is additionally frozen by
**content**. A composition guard records member paths only, so a conftest sitting beside a
frozen test used to be listed and never hashed — and that file is exactly where a good-faith
edit changes what the contract measures without moving anything the guard watches, because
filtering inside `pytest_collection_modifyitems` fires no deselection hook. The cost is
deliberate: a slice that legitimately edits a `conftest.py` mid-build gets `LOSS OF LOCK`,
which under this standard is the correct answer.

If the manifest is ever damaged, every write is denied fail-closed. Clear it with
`release --force --window --reason "<why>"`, which is logged.

Deleting `freeze.json` by hand still removes the manifest, and it is no longer
the quiet way out. The ledger already held the evidence — the last lock event is a
`freeze`, no release closed it, and no manifest is on disk — and from wire 2.2 the
gate reads exactly that pair: it prints a RED line naming the freeze the ledger
records and exits 1, one step louder than the amber an honest `release --window`
prints, rather than the silence it used to print. `status` says the same thing
(`MANIFEST GONE`) instead of "no active freeze". Deleting the whole `.canopus/`
directory takes the ledger with it and is still silent; see the honest-limits
section below.

**A release names its kind, and an open window is no longer silent.** `release`
requires `--window` or `--ship`, and passing neither exits 2 with argparse's own
usage line. `--force` obeys the same rule: a forced release that names no kind is
refused like any other. The two kinds are different events. A `--window` release says the
lock will be taken again and the slice is still in progress, which is the state
you are in whenever a frozen enforcer is the thing being fixed. A `--ship`
release says the slice is over.

The distinction exists because something reads it. While a window stands open and
no freeze is active, the test gate prints an amber line at every pytest session
start — `a release window is open`, with the timestamp and the reason recorded in
the ledger, and the sentence "No lock is held, so a green suite proves nothing
about the contract." It reports and never blocks, so the run still exits 0. A
later `freeze` closes the window, which is what keeps the line self-clearing
rather than something an operator learns to dismiss. Before this, "no freeze is
active" and "the lock was taken off mid-slice and never put back" were the same
silence.

A ledger entry with no kind reads as a `ship`. Every entry written before wire
2.2 has none, and reading those as windows would have turned a quiet past amber
retroactively on the first pytest run after the update.

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
| `ATTESTED` | a run of THIS exact contract collected every frozen test file in full, deselected none of them, passed, described its own process, and loaded the plugin set the freeze recorded |
| `NOT ATTESTED` | absent, recorded against a different root hash, incompletely collected, carrying failures, describing no process, loading a plugin set the freeze did not record, or taken over a freeze that captured no plugin baseline at all (any freeze without `--contract`); the recorded reasons print beneath it |

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
record, read by a human at sign-off, where a pack that does not say `ATTESTED` is the
operator's cue to refuse the slice. `pack` itself prints the page either way and returns 0:
it reports and never blocks, so the refusal is the operator's act and not the tool's.
The record is bound to the recomputed root hash, so editing any frozen file
after a green run makes it stop applying without anyone remembering to delete it. It is a
true statement about *a* run of this exact contract, not necessarily the most recent one.

### What configured the interpreter, and what that is worth

The lock refuses a repository the freeze did not record; the attestation answers
whether the contract ran. Between the two sits the pytest process itself, and
before wire 2.3 nothing described it. `PYTEST_ADDOPTS` was not scrubbed the way
every `GIT_`-prefixed variable is, and the tree-root composition guard watched
`*.py` FILES only, so a package DIRECTORY dropped at the root was invisible to
it. Five things changed, and none of them closes the bypass:

1. **The record describes the process.** Every attestation carries a `process`
   block: the launcher, the compared plugin identities, the in-tree plugins kept
   as provenance, the `anon:`/`name:` entries kept and never compared, the
   `PYTEST_`-prefixed variable NAMES present in the environment, the parsed `-p`
   option, and one plugin list per xdist worker. Names only, never values: a
   value can carry a token and this record is pasted into a committed artifact.
2. **The freeze captures a plugin baseline, and it is inside the root hash.**
   `freeze --contract` reads the set off the contract's own run. A later run
   whose compared set differs in either direction cannot attest, and the reason
   names the plugin. Both directions, because a plugin that VANISHED changed what
   the run measured as surely as one that appeared. The corollary is a real cost
   and not a footnote: a freeze taken over plain paths runs no pytest child, so
   it captures no baseline, so NO later run of it can ever read `ATTESTED`. That
   is fail-closed by design, and `freeze` says so at the surface the moment it
   happens rather than leaving the operator to discover it days later.
3. **The identity compared is derived from ORIGIN, never from pytest's
   registration name.** A registration name is an absolute path for a conftest
   and a memory address for an anonymous plugin, so comparing those refuses every
   honest run and carries an operator's home directory into a public hash.
   Measured: sixty-six raw names collapse to seven identities, and the
   distribution subset is identical in the freeze probe, the gate controller and
   every gate worker.
4. **`scripts/run-tests.py` chooses its child's environment** instead of
   inheriting it, dropping every variable whose name begins `PYTEST_` by prefix
   rather than by a denylist. A bare `pytest` still inherits whatever the shell
   holds; the canonical gate does not.
5. **The tree-root guard sees importable directories, and the deny refuses the
   write that creates one.** Both halves are above, under the guard table.

`pack` prints an `interpreter` block beneath the three axes: the launcher, the
compared identities, the environment names, the parsed plugin option, each
in-tree plugin marked `frozen` or `NOT FROZEN`, the recorded-and-never-compared
entries, and the worker count with the number of distinct plugin sets among
them. `verify` and `status` print the attestation's recorded reasons, which name
the offending plugin, but not the block. Origins stay in the record and off
every page, because a distribution plugin's origin is a path inside the
operator's virtualenv.

**The recorded reproduction, re-run against the new code.** It now has two
answers rather than one, and only the first is closed.

```bash
# A. the plugin at the tree root, exactly as this page recorded it
mkdir -p plug && printf 'def pytest_pyfunc_call(pyfuncitem):\n    return True\n' > plug/skipper.py
PYTEST_ADDOPTS="-p plug.skipper" python -m pytest -q
#   ERROR: canopus: the freeze gate is red
#   canopus: LOSS OF LOCK ... this tree computes <a root the anchor does not record>
python scripts/canopus.py verify
#   LOSS OF LOCK    added  plug/                                        exit 1
```

Nothing is collected: `plug/` moved the root composition, and the gate reddens at
session start. The same file written through the Write tool is refused before it
lands.

```bash
# B. the same plugin from OUTSIDE the tree, where no composition guard can see it
mkdir -p ../outside/plug
printf 'def pytest_pyfunc_call(pyfuncitem):\n    return True\n' > ../outside/plug/skipper.py
PYTHONPATH=../outside PYTEST_ADDOPTS="-p plug.skipper" python -m pytest -q
#   2 passed          (over a contract that is RED by construction)
python scripts/canopus.py verify
#   LOCK HELD ...
#   NOT ATTESTED    reason  a plugin the freeze did not record was loaded: dist:plug
#   APPROVED                                                             exit 0
```

Say what that is and is not. The lie to pytest still succeeds: two frozen tests
that cannot pass report `2 passed`, and `verify` still exits 0, because
attestation blocks nothing by construction. What closed is the SIGN-OFF claim:
the record now names the plugin that made the suite green, on the page the
operator reads before approving, instead of leaving the environment variable and
the untracked directory invisible to everyone. Route A is closed at the gate;
route B is reported and not prevented, and reporting is worth what an operator
who reads the page makes it worth.

### What wire 2.3 leaves open

Every item below was measured or reproduced during the slice rather than
assumed, and they run from the ceiling of the design downward. None of them is
softened by a later sentence.

**A plugin in the same process can lie about the process block.** This is the
ceiling of the whole design, and it is cost and visibility rather than
impossibility. The recorder runs inside the interpreter it describes, so anything
already loaded there can rewrite what it reports. Reproduced on the same scratch
tree, with a seventeen-line plugin that wraps `canopus_gate.process_facts`,
deletes its own identity from the returned map, and returns `True` from
`pytest_pyfunc_call`:

```bash
PYTHONPATH=../outside PYTEST_ADDOPTS="-p plug.liar" python -m pytest -q   # 2 passed
python scripts/canopus.py verify
#   LOCK HELD ... ATTESTED  2 frozen tests passed, none deselected ... APPROVED   exit 0
```

The contract was red. Every axis reads green at exit 0, and the record carries no
trace of the plugin. Closing this needs a describer the described process cannot
reach, which is a different instrument. What the slice buys against it is the
same thing the anchor buys against a forged approval: the attack now costs a
deliberate, written artifact instead of one environment variable.

**Whatever configures the interpreter BEFORE the recorder exists is unobserved.**
A `.pth` file in site-packages, a `sitecustomize` module, `PYTHONPATH` and
`PYTHONHOME` all take effect at interpreter startup, before `tests/conftest.py`
is imported, and nothing here records them: `env_configured` collects
`PYTEST_`-prefixed names only. Measured on the scratch tree, a `sitecustomize.py`
reached through `PYTHONPATH` printed its own marker to stderr ahead of the gate
line, the run read `ATTESTED`, and the record's `env_configured` held one entry,
`PYTEST_VERSION`. Route B above is the same gap in its other form: `PYTHONPATH`
is what put the hostile plugin where pytest could find it, and no field says so.
Closing it means launching the interpreter isolated, which is a different slice.

**The plugin comparison is BY NAME.** A plugin replaced by a same-named one from
a different distribution passes, because comparing origins would redden every
fresh clone and every relocated workspace. Reproduced: a hostile `anyio` package
placed earlier on `PYTHONPATH`, with a `pytest_plugin` submodule returning `True`
from `pytest_pyfunc_call`, is loaded through the real entry point, resolves to the
identity `dist:anyio` that the freeze already recorded, and takes the red contract
to `2 passed`, `LOCK HELD`, `ATTESTED`, `APPROVED` at exit 0.

**The comparison covers distributions plus every in-tree plugin pytest did not
import BY COLLECTION.** A collected in-tree conftest is recorded as provenance
and never compared, because which conftests load depends on what is collected:
the freeze probe collects the contract directory and the gate run collects the
whole suite, so they legitimately differ. It cannot reach the frozen contract's
own tests without moving frozen bytes, which is why the exemption is acceptable
rather than an excuse for not trying. The exemption is decided by OBJECT IDENTITY
against pytest's own record of what collection imported, not by any string, and
that is a repair rather than a preference: two defeats were reproduced against
string rules. A hijacker containing `__file__ = __name__` satisfied "the
registration name equals the plugin's file", and a second one forging `__file__`
onto the honest `tests/conftest.py` folded into that identity and vanished from
the record entirely. Each turned `assert False` into `1 passed`. An object cannot
forge `is`.

**That exemption reads a PRIVATE pytest attribute,
`pluginmanager._dirpath2confmods`.** It is the only place pytest records which
modules collection actually imported, and it carries no compatibility promise. If
a future pytest renames it the predicate yields nothing, so NOTHING is exempt and
every in-tree plugin is compared: an honest run's own conftests start needing to
match the freeze. That is the fail-closed direction, noisy and safe rather than
quiet and wrong, and the dependency is a real cost stated here rather than left
in a comment.

**`GUARD_NAMES_ANCESTOR` still watches only `conftest.py`.** A new non-conftest
file under `tests/` does not redden `verify_manifest`. Under the identity rule it
can no longer smuggle a plugin past the comparison, but it is not the guard that
stops it.

**Two environment variables suppress the record entirely.**
`CANOPUS_PLUGIN_DUMP` joins `CANOPUS_NO_ATTEST`: with either set, session finish
writes no attestation and any earlier `ATTESTED` record stands untouched.
Measured with a green record in place, a poisoned run under each variable left
the record's own timestamp unchanged and `verify` still printing `ATTESTED`.
Neither is scrubbed by `child_env`, which strips `PYTEST_` and nothing else. Both
exist for the contract runner, which must not overwrite a real gate run's record
with a probe's partial tally, and both are one `export` away from an operator.

**The root hash now depends on a measured property of the machine.** `approve`
and `freeze` each run the contract and capture the `dist:` set that run loaded,
and that set is in the root-hash payload. So a dependency installed between the
two commands moves the hash, and the committed approval stops matching the
freeze. Intended and fail-closed, and it is the price of item 2 above: the
baseline has to be inside the hash or a builder appends the name of the plugin
that skips the contract and the approval still matches. It does mean the root
hash is no longer purely a function of file content.

**`pytest --noconftest` and `--confcutdir` skip the gate entirely.** Untouched by
this slice; measured and argued above, under the three layers.

**The ledger is deletable with the directory that holds it**, `.canopus/` being
gitignored, and a five-line shell script named `git` placed earlier on `PATH`
converts `LOSS OF LOCK` into `LOCK HELD`. Both are argued above; neither moved in
this slice.

**The import mode is one unwatched line.** `--import-mode=importlib` in
`pyproject.toml` is what makes the tree root the only in-tree `sys.path` entry.
The root guard watches `*.py` files and importable directories at the root, so it
does not watch `pyproject.toml`, and that file is not frozen by content either.

**The root composition guard WATCHES identifier-shaped top-level directories, and
the PreToolUse deny does NOT prevent their creation. This was closed during the
slice and then deliberately reopened.** `verify` reddens when such a directory
appears — `added` reports `plug/` — but a Write to `plug/__init__.py` while the
lock is held is allowed through, so detection is the whole of the protection and
the shadowing package lands before anyone runs `verify`.

It was not left this way for want of trying. The deny was implemented, and the
gate run under the retaken lock measured what it cost:
`tests/test_protect_personal_threads_hook.py::test_hook_allows_legitimate_write_inside_personal`
failed, because its payload writes a note under the private `threads/` tree and
`threads/` is an identifier-shaped top-level directory that is data-routed and
absent from a fresh engine clone. That is not a test artifact.
`.claude/hooks/_dispatch.py` has no data-path redirect in its `CHECKS` chain and
runs `check_canopus_freeze` BEFORE `check_protect_personal_threads` — a check
that exists precisely to police those writes — so the deny stopped them at that
first gate for the whole duration of every frozen slice. The same holds for
`crm/`, `knowledge/`, `context/`, `plans/`, `outputs/` and `datastore/`, each of
them a real gitignored, data-routed root name.

The frozen contract's own
`test_a_directory_that_cannot_be_imported_does_not` states the standard this was
judged against: the fix's failure mode is over-reach, and a guard that reddens on
ordinary work is one an operator learns to release around, which is worse than no
guard. An operator who cannot write a private note while a freeze is held opens
the window to get work done, and then the guard protects nothing. So prevention
retreated and detection stayed. Two narrower cuts were considered and rejected:
reading the routing map inside `canopus_freeze.py` breaks its stdlib-plus-`atomic`
import floor, and keying on `.gitignore` adds a file read to a path that must
never raise and would still be guessing at intent. Closing this properly needs the
dispatcher to resolve data-routed paths before the freeze check sees them, which
is a different slice.

**Seventeen gitignored identifier-shaped root directories will redden the guard
by design.** `.gitignore` carries eighteen identifier-shaped root-level entries,
seventeen of them outside the cache names the guard already skips: `htmlcov`,
`dist`, `outputs`, `plans`, `threads`, `crm`, `knowledge`, `context`,
`datastore`, `corporate`, `personal`, `_archive`, `slash`, `_secure`, `Desktop`,
`LauncherFolder` and `MyDocuments`. Every one is importable, so every one is
watched on purpose. A `git clean -xfd`, a first `build-plugins.py` run, or a
single `--cov-report=html` moves the root composition with no source edited. The
real surface is that ignore list, not the three directories that happened to
exist on one machine when this was first written. Accepted rather than excluded,
because an exclusion set wide enough to cover seventeen names is exactly where a
real shadowing directory would hide, and the failure is loud and instantly
explicable.

**One test module loads `run-tests.py` without the skip guard its sibling
carries, and the gap is real but narrower than it first reads.**
`tests/test_run_tests_runner.py` and the frozen contract both import
`scripts/run-tests.py` by path; only `tests/test_run_tests_env.py` guards the
import with a module-level skip. `run-tests.py` calls `ensure_venv()` at import,
which `os.execv`s the whole pytest process when the interpreter is not
`.venv/bin/python`. What holds in practice is the root `tests/conftest.py`, which
sets the re-exec sentinel before any test module is imported, so under an
ordinary run the unguarded import is already a no-op. The exposed case is a run
where that conftest never loads, which is the `--noconftest` and `--confcutdir`
gap two items above, arriving here in its second form. Measured on 2026-07-27
with the system interpreter: `pytest -q --noconftest tests/test_run_tests_env.py`
prints `1 skipped` and exits 5, while the same command on
`tests/test_run_tests_runner.py` prints ZERO bytes and exits 0. A run that prints
nothing is indistinguishable from one that never happened, and inside the frozen
contract the pattern cannot be fixed at all until the contract is retired.

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
directory as the manifest, so it is evidence against an *edit to* `freeze.json` and against
deleting that one file, and not against deletion of the directory. Removing `freeze.json`
alone leaves the ledger behind, and the gate reddens on it. `rm -rf .canopus` takes the
ledger with it, after which the gate returns 0 in silence because it cannot tell "no freeze
was ever taken" from "the freeze was removed", and git never saw either. Nor does the ledger record the gate: a
passing gate writes nothing, so the absence of a `verify_fail` line does not mean the
contract was verified. The gate's evidence is its exit code in the test output; the durable
evidence that a contract was approved is the anchor artifact, committed in the other
repository.

Three measurements say what that is worth in practice. `rm .canopus/history.jsonl`
turns the gate's open-window amber line into total silence at exit 0, because that
line is read out of the ledger and nothing else records the window. `release
--ship` used mid-slice produces the same silence with no deletion at all, since a
ship reads as a closed slice rather than an open window. And ONE line appended to
the ledger flips `canopus pack`'s continuity section from `outside the lock` in
red to `every commit was made while a freeze was held` in green. So the ledger is
a convenience beside the durable record and never the record itself: it shows a
cooperating build's own account of what it did, and it cannot be used to prove
that a freeze was ever taken, that a window was opened and closed, or that a given
commit was made under the lock. Those three claims rest on the anchor artifact and
on an operator who ran `verify` in their own shell.

A contract that turns out to be wrong is not edited in place. Release it, fix it,
re-freeze it, and get the new root hash re-approved.

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
