# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

## [0.8.0] - 2026-08-09

The release the engine grew a memory, a method, and ears. Recall stopped being a list somebody had to remember to read and became relevance computed against what was just typed, with "I do not know" as a first-class answer and no path that turns a quiet memory into a deletion. Canopus, the build standard every non-trivial change now passes through, shrank from thirteen enforced moments to seven and moved its enforcement onto tools that already existed, after measurement found 93% of the hand-built prevention surface defeatable by one shell command. Local speech-to-text and layout-aware document reading landed, so a recorded call becomes the thread log, the CRM entries, and the drafted follow-ups it should have produced, on the operator's own machine. And four failures that had been invisible precisely because every health surface reported green were found and fixed.

The reader-facing release note is [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md) ([published page](https://mishahanin.github.io/heading-os/RELEASE-NOTES.html)). What follows is the commit-level record.

### Added

- **`DISCLAIMER.md` at the repository root**, version 1.0, effective 8 August 2026: the project's notice on names, examples, and data. It declares every example name, company, contact detail, figure, and scenario in the engine and in anything generated from it to be invented, holds that declaration even where a placeholder coincides with something real, names the one deliberate exception (the author's own identity and the 31 Concept ownership disclaimer), and states in advance the position on content that reaches the published tree in error. It also carries a no-questions-asked correction route: a request to `misha.hanin@odinix.com` with the subject `HEADING OS naming` is enough, with acknowledgement inside five business days and no requirement to demonstrate harm or assert a right. Short forms for reuse in generated documents and page footers ship with it. Referenced from `NOTICE`, `README.md`, and section 8 of the release note.
- **`docs/RELEASE-NOTES.md`** (and its generated page), the first reader-facing release note the project has published: what changed, why it changed, what it buys, and an honest-limits section naming what the release does not do. Linked from the site nav under Reference.

### Changed

- **Auto-memory is never pruned; a memory that goes unused sinks in ranking and
  stays retrievable forever.** `access_count` was a salience field nothing had
  ever written, so every consumer of it read a corpus-wide zero and the ranking
  signal it was meant to carry did not exist. It is now written on the retrieval
  path: `memory-index.py query --touch` bumps the auto-memory files a CONFIDENT
  result surfaced, debounced to one write per file per day, and the recall hook
  passes that flag on every prompt. The reinforcement bonus is log-scaled
  (`scripts/utils/salience.py`), matching the old linear curve at zero and at ten
  and continuing to separate above ten where the old one was flat.

  Every path that turned a low count into a deletion proposal is gone with it.
  `dream-shadow.py` reports DORMANCY — what the retriever has not surfaced
  lately, informational, oldest first — where it used to emit prune candidates,
  and `/dream` no longer proposes retiring anything: a superseded fact is
  rewritten in place so the record survives. Removal happens only when the
  operator explicitly asks, through `scripts/retire-memory.py`.

- **Clock-driven auto-retire is retired and disabled.** `/dream` no longer
  stamps `expires:`, which was the pass's only trigger, so nothing new accrues
  for it to act on. `scripts/install-memory-auto-retire-timer.sh` now refuses to
  install the timer unless the no-prune directive is deliberately overridden
  (`--i-am-reversing-the-no-prune-directive`), because a fresh clone that runs
  the old installer re-arms deletion. The script and its unit templates stay on
  disk: switched off, not removed.

### Added

- **`memory-index.py stats --top-access [N]`** lists the N most-accessed
  memories (default 20) with their `access_count` and `last_accessed`. It exists
  to read the reinforcement loop from the outside: if the same names hold the
  top of that list month over month, retrieval is reinforcing what retrieval
  already surfaces and `REINFORCE_K` is weighted too heavily. A low count is a
  ranking position and nothing else, so the never-surfaced entries stay visible
  rather than being filtered out into a shortlist.

### Fixed

- **The freeze's plugin baseline described whichever interpreter typed the
  command, not the one that runs the suite.** `freeze --contract` captures the
  set from a pytest CHILD, and `run_pytest_report` launched that child with
  `sys.executable`. Measured 2026-08-04: invoked as bare `python` rather than
  `.venv/bin/python`, on a machine where those are different interpreters, the
  freeze recorded `['dist:_pytest', 'dist:anyio', 'dist:pytest_asyncio']` while
  every run of the suite loads `['dist:pytest_cov', 'dist:xdist']` — two DISJOINT
  sets, so no run could ever attest that freeze. Nothing refused at capture time;
  the symptom arrived after a full suite run as seventeen lines of `a plugin the
  freeze did not record was loaded`, wording that points at plugin injection
  rather than at the interpreter, and `plugins` sits inside `root_hash_payload`
  so correcting it cost a whole retake.

  The child is now launched by `contract_interpreter()`, which prefers the
  project venv WHEN IT EXISTS and falls back to the invoking interpreter
  otherwise, so a public clone that has not run `uv sync` keeps working. One
  named function rather than a `sys.executable` per launch site: a second
  spelling would disagree silently, both returning a path that runs pytest.
  `freeze` also prints one line when the capturing and invoking interpreters
  differ, and stays silent when they agree — a notice that fires every time is
  one nobody reads. `scripts/run-tests.py` solved the same problem for itself
  with `ensure_venv()`; `scripts/canopus.py` cannot copy that, because it is
  imported by `tests/test_canopus_cli.py` and a re-exec at import time would take
  the suite down, so the choice moved to the child.

- **Two interpreter comparisons collapsed a venv onto the system interpreter it
  symlinks to.** Both compared `Path(...).resolve()` on each side, and a venv
  built by the stdlib `python -m venv` — a layout `CLAUDE.md` documents as
  supported — links `.venv/bin/python` straight at the interpreter an operator
  types. Measured 2026-08-05 on real symlinks: `ensure_venv` read "already
  there" and skipped its re-exec, so `python3 scripts/run-tests.py` ran the whole
  suite under the system interpreter with none of the pinned dependencies, which
  is the precise outcome its own docstring promised to prevent; and the new
  `interpreter_notice` stayed silent in exactly the case it was written for. Both
  now ask `venv.interpreter_identity`, which compares the resolved containing
  DIRECTORY beside the resolved file — `pyvenv.cfg` sits next to `bin/` and is
  what puts the venv's `site-packages` on the path, so the directory is what
  decides the environment. Keeping the real file in the comparison makes it
  strictly narrower than the old one, so `/usr/bin/python3.11` and
  `/usr/bin/python3.12` are still told apart. One spelling in the lower module,
  read by both callers.

- **The two-command enforcer cure deadlocked, so the manifest split's whole
  saving was unreachable on this repository.** Editing an enforcer reddens the
  lock; `tests/conftest.py` then refused to run ANY pytest session; the
  `always_run` `data-root-bypass-guard` pre-commit hook runs one; so the commit
  `repin` demands could not be made, and `repin` refuses without it. Measured
  twice on 2026-08-04, both times escaped by a release window plus a
  six-command retake — the exact ceremony the split was taken to avoid, and 21
  of the ledger's 39 recorded retakes were an enforcer edit and nothing else.
  `freeze_gate` now asks the new `enforcer_is_sole_cause` and PERMITS the
  session when a moved enforcer is the only red cause, printing the file and the
  cure in amber instead of failing silently. The question is answered by asking
  `lock_state` what the state would be with the enforcer axis emptied, never by
  a second copy of the redness rule, and the CONTENT axis's definition of green
  now has one spelling (`content_held`) read by both. Every other cause still
  blocks, including a moved enforcer standing beside anything else, and the
  whole anchor axis is asked rather than the content report alone, so a freeze
  whose anchor disagrees or has vanished stays refused. The test is "would this
  session have been PERMITTED without the enforcer", not "would it have been
  green": the gate refuses on one state of three, so asking for LOCK HELD left
  the original deadlock alive inside LOCK UNCONFIRMED — the documented window
  between freezing and writing the anchor hash down, where the gate already
  exits 0. An amber freeze the gate was already letting through keeps going
  through; the relaxation never turns a refused session into a permitted one.

  Permitting the RUN is not permitting a VERDICT, and that is what pays for it.
  `build_attestation` takes a REQUIRED `enforcer_moved` argument and refuses the
  record outright while one has moved: the enforcer set holds the test runner,
  the interpreter chooser and `conftest.py`, so a run taken under edited bytes
  was produced by a different checker. The root hash cannot carry that refusal —
  the split took the enforcer digests out of the payload on purpose, so a moved
  enforcer leaves the recomputed root exactly where it was. The argument is
  required rather than defaulted for the reason `lock_state`'s optional anchor
  pair is a standing defect: a default is the greener reading, and every
  un-updated caller would inherit it silently.

- **The frozen enforcer SET was not bound by the approved root, so an enforcer
  could be dropped out of the guarantee without any indicator moving.** The
  manifest-split slice took the enforcer bytes out of the root-hash payload so
  that editing one costs a `repin` instead of a six-command retake. It took the
  enforcer NAMES out with them. Measured 2026-08-04 on a synthetic tree: a freeze
  over ten enforcers and a freeze over nine compute the SAME root, so `release
  --window` followed by a `freeze` with a shorter `--content` list drops a file
  out of the frozen set, leaves the COMMITTED approval matching, and reads `LOCK
  HELD` and `APPROVED`. From that moment the dropped file was editable under a
  green lock, and editing it changed nothing anywhere. `enforcer_moved` could not
  see it either: it diffs the RECORDED map against disk, and a name that was
  never recorded is in neither.

  Recipe **`canopus-freeze-v7`** puts the names back inside the payload and
  leaves the digests outside it, so a change to the SET costs a re-approval and a
  change to the BYTES still costs only a `repin`. A recorded enforcer that is
  gone from disk is recomputed as a named absent sentinel rather than dropped, so
  the recomputed name set is always the RECORDED one: deleting an enforcer stays
  on its own `enforcer_moved` axis instead of masquerading as a moved contract.
  One exception, and it is not a leak: an enforcer the manifest also carries in
  `files` moves the root when deleted, because it is a frozen contract file as
  well. `tests/conftest.py` is one on the live engine freeze.
  The guarantee is a comparison, not a rule — it binds a freeze to the set its
  committed approval recorded, which is why the CLI still refuses an anchorless
  freeze.

  **A v6 manifest is refused BY NAME**, at every write and at every pytest
  session start. A clone still holding one clears it with `python
  scripts/canopus.py release --force --window --reason "<why>"`, the logged
  escape. That manifest is obsolete rather than damaged and `read_freeze` cannot
  tell the two apart, which is left open and written down in `docs/EXTENDING.md`
  rather than fixed here: telling them apart changes what a corrupt manifest IS,
  and this slice's approval did not cover that.

- **`repin` cleared the attestation even when no enforcer byte had moved**, and
  said so on the terminal either way. A re-pin is accepted over an unchanged set
  on purpose, because it is what an operator reaches for when they BELIEVE the
  enforcer moved; charging a full suite re-run for having checked taxed the one
  behaviour the command exists to make cheap. The recorded run was produced by
  exactly those bytes, so it now stands, and the closing line says which of the
  two happened.

- **`loss_of_lock_sentences` decided whether the CONTRACT had moved from the
  file lists rather than from the root comparison.** Measured: with a stored root
  the tree does not compute and one enforcer byte edited, the only sentence said
  was "The ENFORCER moved, not the contract", which was false of that tree and
  named the cheap cure, so the operator re-pinned and the lock stayed red. That
  is the exact failure the branch's own comment forbids. `verify_manifest` now
  reports `root_moved` as a named field and the sentences read it, defaulting to
  the red direction when a report cannot answer.

### Known issues

- **An OBSOLETE freeze manifest is indistinguishable from a DAMAGED one.**
  `read_freeze` raises the same `FreezeCorrupt` for a valid manifest of a known
  older recipe as for an unreadable one, so a deliberate recipe bump costs the
  `--force` escape and records the same `force_release` ledger event as genuine
  corruption. Met on 2026-08-04 when the `canopus-freeze-v7` bump invalidated
  the running slice's own freeze one edit after it was taken.
- **Nothing binds the root-hash payload's SHAPE to `RECIPE`.** `RECIPE` and
  `root_hash_payload` are two independent edits, so a payload change shipped
  without a bump reads as LOSS OF LOCK on a tree where nothing moved. No
  instance has shipped; the exposure is real and untested.
- **`lock_state`'s `anchor_status` and `anchor_value` are still optional**, with
  the greener content-only reading as the default, so a caller that forgets them
  is told LOCK HELD over a freeze nobody approved.

### Added

- **A contract could be red for a perfectly real reason and still be satisfied
  by an implementation nobody would accept, and nothing measured that until the
  code was already written.** The existing null-stub probe asks whether a
  contract test passes while the code under test is ABSENT. `canopus probe`,
  `approve` and `freeze` now also ask the other question: whether it passes while
  the code is PRESENT and WRONG. Three pass-candidates are synthesized and run,
  one pytest session each, through the same finder over the same claim set:
  `none` returns nothing from every call, `echo` hands back its first argument
  unchanged, and `greedy` answers with every string the contract itself wrote.
  A contract whose every red test passes against one of them is refused, and the
  refusal names that one, because the cure differs per candidate.

  The design named four candidates; two of them already ran. A constant-return
  module IS the null stub, which is two of them carrying deliberately
  disagreeing constants, and an import-only module IS the null stub at import
  time, where a test satisfied by it is already labelled vacuous. `echo` is not
  in the design and is here because a pass-through satisfies the "something was
  done to the input" assertion neither of the others touches.

  The greedy payload is built from the contract's OWN literals behind a
  `canopus-pass-candidate` marker, and the marker is the whole property: the
  joined string can never EQUAL a single literal, so `assert "refused" in
  render()` is satisfied and `assert render() == "refused"` is not. A candidate
  carrying an alphabet would satisfy greps the contract never wrote and
  manufacture refusals against honest tests.

  **What it does not reach, measured rather than assumed.** It stands in only
  for modules the contract imports and that do not resolve, so a test driving a
  real entry point whose internals are wrong is outside it; on a reconstructed
  CLI-wiring test of exactly that shape, all three candidates took nothing. The
  discipline that closes that inside a contract is writing wiring tests as
  PAIRS, a refusal beside a non-refusal.

  The price is stated rather than hidden: `probe` goes from three pytest sessions
  to six, measured 1.20s to 2.28s on a two-test contract, and `approve` and
  `freeze` still skip every probe session once a refusal is already earned.

  The instrument found four defects in its own contract before that contract
  could be frozen. Three at `probe` (one vacuous, two already green against
  refusals the assertion could not tell apart) and one at step 8, where a fixture
  imported its absent subject at module scope, killed collection for the whole
  file, and asserted about a test that appeared in no population any run could
  report. Three step-11 mutations then survived the frozen contract, all of them
  the same shape the slice exists to refuse: two assertions written against a
  word rather than a value, and one guard covered only at the door the contract
  happened to use. All three are closed by regression tests in the ordinary
  suite.

### Changed

- **One manifest hash covered two claims that have nothing to do with each
  other, and it charged a full re-approval for the wrong one.** The CONTRACT
  (what a builder is measured against, and what a human commits an approval
  over) and the ENFORCER bytes (the code that does the measuring) both lived in
  the freeze manifest's `files` map and both fed `root_hash`, so touching a
  single enforcer byte moved the approved root, the committed approval stopped
  matching, and `freeze` refused until the whole approve/commit/freeze cycle was
  repeated. Measured over the 39 `anchor_replaced` records in the lifecycle
  ledger on 2026-08-03: **21 of them were exactly that**, the largest class of
  retake in the standard's history, and not one was a contract that had changed.
  Eleven consecutive records in July read, verbatim, "the enforcer bytes changed,
  so the approved root changed."

  The enforcer now hashes on its own. `scripts/utils/canopus_freeze.py` records
  those bytes under a `content` map with its own digest, the ENFORCER PIN, and
  `root_hash_payload` covers everything else it always did — recipe, anchor,
  anchor binding, contract files and dirs, the per-file baseline, the plugin set.
  Recipe `canopus-freeze-v6`; a v5 manifest is refused BY NAME rather than
  reading as a silent loss of lock on a tree where nothing moved.

  **Cheaper, never quieter.** `verify_manifest` reports `enforcer_moved` as its
  own list beside `changed` and `removed`, folded into `held` and deliberately
  not into the recomputed root, so an un-repinned enforcer edit does NOT read
  `LOCK HELD`; `verify` prints it as `enforcer <path> … cure: repin` and the test
  gate says "The ENFORCER moved, not the contract", because the two have
  different cures and one undifferentiated red told an operator neither.
  `canopus repin --reason "<why>"` re-records the pin under the freeze already
  held and clears the attestation — the enforcer set holds the test runner, the
  interpreter chooser and `conftest.py`, so a run taken before those bytes
  changed was produced by a different checker. It never touches `files`, `dirs`,
  `baseline` or `root`, which is what stops the cheap path becoming a way around
  the expensive one: a contract edit stays red through any number of re-pins.

  **The commit requirement is why this is not a security trade.** The first draft
  of the gate accepted it as one, until the operator asked what the trade was and
  the premise broke: "an enforcer change must pass through a committed approval"
  was never operator-gated — all 39 retakes were run by the assistant, `git
  commit` included. What the old design really bought was that the new enforcer
  state landed in git AT ALL, at a cost of six commands. So `repin` REFUSES while
  any changed enforcer file is uncommitted, naming the files, and the ledger
  event carries the sha. A readable diff with an author, in the public engine
  repository, is strictly better evidence than a hash line in a private artifact.
  The PreToolUse deny no longer refuses a write to an enforcer path: detection at
  `verify` replaced prevention, the same asymmetry already recorded for watched
  directories. Ceremony falls from six commands to two, and `canopus pack` now
  counts `repins` per label, because an act nobody counts is how a weakened
  enforcer stops being noticeable.

### Added

- **The gate-yield report judged a wall and a gate with one instrument, and the
  instrument was right for only one of them.** Every guard on the push path of a
  PUBLIC repository reached `NO YIELD` and the FLAGGED list the moment its window
  passed 31 days. For the secret scanner that verdict is true and useless: zero
  catches is its success condition and one miss publishes a live credential
  irreversibly, so a catch count reads its own success signal as evidence against
  it. `scripts/utils/gate_yield.py` now splits the two axes by LOSS FUNCTION and
  deliberately not by which log a mechanism writes to. A WALL (asymmetric,
  unbounded) reads `HOLDING` at any window length and can never be FLAGGED; a GATE
  (symmetric, bounded) is judged as before. `depth-gate` is the case that proves
  the split is not a log filter: it shares the walls' writer and stays judgeable,
  because under-ceremony costs rework and over-ceremony costs time. An UNDECLARED
  mechanism is treated as a wall, since flagging something nobody classified is
  the expensive direction and a missing verdict is the cheap one; a second test
  keeps that fail-safe a net rather than the plan.

  The same report also could not see the standard's largest single output.
  Measured 2026-08-03: 39 frozen-contract retakes in the ledger, against a whole
  reported lifecycle yield of five. `approve --replace` now requires `--cause`
  from a closed vocabulary and writes it to the ledger STRUCTURALLY; a cause is
  never inferred from the prose reason, for the reason `canopus_friction.py`
  already refused the same shape ("a counter built on a substring lies quietly
  the first time somebody rewords"). Retakes predating the field are bridged by
  the committed `config/canopus-retake-history.json`, which carries the RAW PROSE
  REASON beside every class so each line is checkable, and a structural cause
  always beats the file for the same record.

  **The report names how many records came from that file, because that part is
  judgement and the rest is measurement.** Two hand passes over the same 39
  records, by the same classifier on the same day, disagreed on three of them
  (17/18/3, then 14/21/4). The direction is robust across both passes and every
  denominator; no single figure is. So the frozen criterion asserts coverage
  computed from the live ledger rather than any total, which is the stronger
  claim anyway: it cannot go stale, and it fails the moment a pre-field retake
  exists with no class.

- **The page an operator signs off from now says how hard the green was to
  get.** `pack` rendered `25 of 25`, `LOCK HELD`, `APPROVED` identically for a
  slice that went green first time and one that went green on the sixth attempt.
  The ledger had held the difference line by line the whole time and nothing read
  it. Measured across the whole ledger on 2026-08-03: 254 records, 19 shipped
  slices, **23 windows and 37 retakes**, almost all of it concentrated in six
  slices. `timer-timezone` shipped that morning with five windows and six retakes
  behind it, and the operator approving it saw none of that on the page.

  `scripts/utils/canopus_friction.py` counts one label's friction out of the
  ledger's STRUCTURE (`release` + `kind`, `anchor_replaced`, `refuse_*`,
  `verify_fail`) and renders it into `pack`. Three refusals are pinned by test
  rather than promised in prose: it never grades (a window is the sanctioned way
  to correct a wrong contract, and a page that scolds the count teaches the
  builder to suppress windows, which means editing a frozen contract in place);
  it never counts what is not structural (waivers live in a free-text `reason`,
  and a counter built on a substring lies quietly the first time somebody
  rewords); and it never presents a floor as a total, because `.canopus/` is
  gitignored and one `rm -rf` takes the ledger with it. A row of zeroes is
  therefore ambiguous, and `recorded` resolves exactly that one ambiguity: a held
  freeze always wrote a `freeze` line.

  Shipped 2026-08-03 after a window of its own. Three mutations survived in
  sequence and each changed something: SC-4's `"render_friction" in source` was
  satisfied by the `import` line alone, its AST replacement was satisfied by a
  call whose value was discarded, and the render could drop its heading entirely.
  The fix for the second changed the CODE rather than the test -- `render_friction`
  gained an optional `heading_wrap` so the whole section is ONE expression inside
  the `print`, making "is it wired" and "does it reach the page" a single claim.
  Eleven mutations, eleven killed, none invalid. The frozen contract retired into
  `tests/test_canopus_friction.py` (all 12 IDs, none dropped) plus one end-to-end
  assertion in `tests/test_canopus_cli.py` that the contract could not make: a
  `pack` test written before the code existed ended in a skip, and nothing
  refuses a skipped contract test.

- **A scheduled job can now EARN the right to send, instead of guessing from a
  flag that answers the same thing every night.** `scripts/utils/egress_proof.py`
  classifies an assembled outbound payload into `egress_clear`,
  `egress_blocked`, or `egress_unverifiable` by scanning it with the same
  real-entity detector the content-leak wall uses to decide whether a file may
  become PUBLIC on GitHub. Clear requires all of: no source with uncommitted
  changes, a denylist that built and holds tokens, no `content-guard: ok`
  suppression marker in the payload, and no match. `is_sensitive()` is UNCHANGED
  and its seven consumers see nothing; the additive `sensitivity_is_declared()`
  distinguishes "a human typed the variable" from "nobody ever set it", and a
  declaration outranks any proof. `scripts/utils/router_payload.py` holds the
  exact wire strings so the checker cannot drift from what is sent.
  `scripts/router-accuracy-nightly.py` consults the proof, and a refusal now
  appends a typed record to the trend rather than printing into a journal nobody
  reads. `scripts/utils/sensitive.py` and the new proof join
  `ENFORCEMENT_SURFACE`; they were missing, so a change to the workspace's
  egress control classified as `standard` depth.

  Shipped 2026-08-03. The capability's first night produced the first
  router-accuracy measurement ever taken: 701/710 = 98.73% across 69 skills,
  surfacing genuine weak spots that had never been visible (`competitor-intel`
  78.6%, `meeting-prep` 84.6%, `email-respond` 87.5%, `zk` 87.5%). The frozen
  contract retired into `tests/test_egress_proof.py` (18 IDs) and
  `tests/test_sensitive_mode.py` (11 new IDs for `sensitivity_is_declared`); the
  remaining 12 contract IDs were duplicates already held by
  `tests/test_sensitive_mode.py` and `tests/test_router_accuracy_nightly.py`, and
  the one assertion those lacked (a refusal record must carry its REASON) was
  added there rather than dropped.

### Removed

- **The eval-drift daemon, 1015 lines that could not produce anything.** It
  replayed Langfuse traces of skill runs against the current `SKILL.md` and
  compared today's pass rate to a rolling baseline. It could not do that here for
  two independent reasons, either one fatal: its input is Langfuse traces and
  `observability.is_enabled()` is False, so the input set is empty by
  construction; and it refused to run at all under the fail-closed
  `SENSITIVE_MODE` default. Measured on the service host 2026-08-03: the unit
  loaded, enabled and **active**, its heartbeat 60 seconds old, its newest report
  dated 2026-05-23 -- **72 days** -- and a nightly journal WARNING nobody reads.
  Every health surface called it healthy for all 72 days. The four reports it did
  produce, back when it ran, recorded 0 traces and a 100% pass rate for all ten
  skills, which is what dividing zero by zero looks like once rendered as a
  percentage.

  Removed with it: its `.service` template, its supervision arms in
  `install-/restart-/uninstall-daemon-service.sh`, its `EXPECTED_DAEMONS` entry
  (left behind it would resolve `missing` -- a genuine down state -- every ten
  minutes forever), its Healthchecks.io deadman entry, its `state_dirs` row in
  `service-host.example.json`, its `.lint-baseline.json` allowance, and its
  mentions in an always-on rule and a skill reference. New in
  `tests/test_watchdog_expected_set.py`: every name in `EXPECTED_DAEMONS` must
  have an installable unit template, which catches this drift in both directions.
  The frozen contract's other keeper landed in `tests/test_routing_map.py`: the
  output of a retired daemon must stay private after its code is gone, because
  the routing default is `engine`, which is PUBLIC.

  **Kept, deliberately:** the eval CASES (ten skills, 35 files) and
  `run-skill-eval.py`, the harness a human drives from `/scrutinize` -- the
  daemon was one consumer of that harness, never its owner. And the private
  routing rule plus both gitignore entries for `datastore/operations/eval-drift/`,
  because deleting a producer must not un-protect 72 days of what it produced.

  **Fixed on the way out, unasked:** `setup-daemon-healthchecks.py` read the zone
  at module scope with `.env` unloaded, and the eval-drift entry was the only
  consumer of the result -- so that deadman was registered in UTC while the daemon
  fired at local 02:00. The same defect class the timer slice closed, in a script
  no timer-entrypoint walk covers because it is not one. The call site died with
  the entry.

  **Not fixed, and named:** deleting this daemon removes the instance of the
  disease, not the class. A supervised process that lives but produces nothing is
  still indistinguishable, from every surface, from a healthy one. Measured on
  the same host: `steward-email-triage.service` is the only daemon producing
  daily output, and it is neither in `EXPECTED_DAEMONS` nor writing a heartbeat --
  so the watchdog watches three daemons and misses the one that works.

### Fixed

- **A scheduled job fired four hours off its own configured schedule, on the host
  that runs the Tribe bot.** `HEADING_OS_TZ` reaches `os.environ` only through
  `load_env()` reading a gitignored `.env`, so it is per-machine, never in git,
  and nothing checks that a host has it. The service host had no such line from
  the 2026-05-23 daemon migration onward, and every caller silently got UTC.
  Consequence: the fireside day-of reminder, configured for 15:30 and documented
  in its own docstring as "3h before the 18:30 session", DM'd speakers their Zoom
  link at 19:30 local -- an hour after the session ended. The Healthchecks.io
  deadman checks, registered from a laptop where the variable IS set, expected
  Asia/Dubai and flapped DOWN/UP for about three and a half hours every single
  day. Fixed on the host, not in the engine; the engine's contribution is the
  resolver above, which now runs on 3.12 and announces the fallback instead of
  dying into it. `setup-daemon-healthchecks.py` gained the operational rule that
  bit this: retiring a daemon means DELETING its check through the API, because
  that script only ever creates and updates -- an orphaned check keeps alerting
  forever, and one did.

- **The timezone resolver shipped this morning could not run on Python 3.12, and
  reported UTC.** Twelve timer installers invoked it as a FILE
  (`"$PYTHON" ".../scripts/utils/paths.py" tz`), which puts `scripts/utils/` at
  `sys.path[0]` -- where `operator.py` shadows the stdlib `operator` that
  `collections` imports during `functools`. Fatal on 3.12 (the service host),
  silent on 3.11 (a laptop, where `operator` is already cached by the time it
  matters). The `|| echo UTC` fallback then rendered that crash as "no timezone
  is configured", which is precisely the condition the resolver exists to detect
  and announce: a guard that fails into its own error case reports the wrong
  answer with confidence. All twelve now use `-m scripts.utils.paths` from the
  workspace root, so nothing under `scripts/utils/` can shadow a stdlib name.
  New `tests/test_tz_resolver_invocation.py` drives it exactly as an installer
  does, keeps the shadow itself executable so the reason cannot rot into
  folklore, and holds both jaws: no installer may reach a `scripts/utils` file by
  path, and every installer must still resolve the zone.

- **The Canopus sign-off page asserted, as fact, that mutation testing had not
  run.** `canopus.py pack` printed that line unconditionally, in the one section
  of the page whose purpose is to name what the evidence does NOT cover. Nothing
  on the machine records whether mutations ran, so the sentence was never an
  observation; on 2026-08-03 it was printed on the sign-off page of a slice whose
  mutations had run twice, including one that found a real leak path and one the
  harness refused as invalid. An operator reads that section to learn what is
  missing, so a false entry there argues for work already done. The page now
  states the blind spot it actually has, and `tests/test_canopus_cli.py` forbids
  the old claim by text rather than only asserting the new one. Recording a
  mutation run into `.canopus/` remains unbuilt and is named as such on the page.

- **The fleet's scheduled jobs had no single source of truth for the operator's
  timezone, and fell back to UTC in silence.** Three defect classes, measured
  2026-08-03 after the router-accuracy nightly stamped its first record under the
  previous day. (1) Five `.timer` templates declared `OnCalendar` with no timezone
  suffix, so they fired on the HOST's zone — and the two hosts disagree: one
  resolves `/etc/localtime` to a `+04` zone, the other to `Etc/UTC`, so the same
  unit fired four hours apart. (2) `HEADING_OS_TZ` lives only in the gitignored
  `.env` and is exported by nothing, so the shell installers rendered `UTC` and
  `get_default_tz_name()` answered `UTC` for every caller that had not called
  `load_env()` first; three timer entrypoints had not. (3) Two templates and ten
  installers named the operator's actual timezone in the PUBLIC engine, which is
  exactly what `get_default_tz_name()` exists to externalize.
  `scripts/utils/paths.py` gains a `tz` argument on its documented shell-callable
  resolver, so bash reads the same `.env` the runtime layer does; every timer
  installer resolves through it and validates the rendered calendar expression;
  `chronicle.py` swaps a naive `date.today()` (under a DTZ011 waiver) for the
  configured zone. `Environment=HEADING_OS_TZ=` is deliberately NOT used and is
  now forbidden by test: `load_env` uses `setdefault`, so a unit-pinned value
  could never be corrected by `.env`. A new frozen guard fails any `OnCalendar`
  without the token, any installer that does not substitute it, any timer
  entrypoint that reads local time without loading `.env` first (resolving
  intra-module callees, so an indirect read is caught), and any geographic
  literal on the template surface.

  That guard, extended to answer ORDER rather than reachability, then found a
  defect in the slice before it: `router-accuracy-nightly.py` loaded `.env`
  inside `_run_harness`, which a REFUSAL never reaches, so every refusal record
  was dated UTC while the unit fires on local time. The load moved to the top of
  `run`. A runtime probe now asks the same question of the running process --
  wrapping `load_env`, replacing `get_default_tz_name` with a reporter, and
  exiting at the first zone read -- which catches what no static walk can (a
  method on an object, a dynamic dispatch, a callable in a variable). It runs
  with scratch roots and every outbound transport replaced by a raise, and its
  plan is asserted complete against the timer set in both directions.

  Shipped 2026-08-03. The frozen contract retired verbatim into
  `tests/test_timer_timezone.py` (39 tests): every one of them pins a standing
  invariant that a new timer, installer or entrypoint can break at any time, so
  none of it was slice-specific and none was dropped.

- **`scripts/utils/mutation_probe.py`** (with `tests/test_mutation_probe.py`):
  mutation runs now carry a mandatory positive CONTROL, so a mutation that is
  not what its label claims reports `invalid` rather than a confident, wrong
  `survived`. Two such mutations occurred in one slice on 2026-08-03 -- one
  anchored on an indent and landed in the wrong function, one inserted an import
  with no call -- and both were caught by re-reading them afterwards, which is
  luck rather than method. Files are snapshotted once per mutation, not once per
  edit; the per-edit version left real residue in the tree while reporting a
  successful restore.

- **A Tier-B alert reported "ok" for every day its producer was dead.**
  `classify_router_accuracy(None, None)` answered `due=False, severity="ok"`,
  summary `"router-accuracy: no trend data"`. The nightly runner it watches had
  never executed once on any host, so the signal described as waiting on that
  output was reporting healthy the entire time. No measurement is now `due` at
  `warn`; a present record with no baseline still reads as a trend legitimately
  forming. `router_accuracy_state` also filters refusal records, so a trend of
  pure refusals reads as no data rather than as stable. Its sibling
  `steward-eval-drift.service` shows the same class at 74 days: live, enabled,
  firing at 02:00 with no misfire, and skipping every night since 2026-05-20
  while its heartbeat stayed fresh and every health surface called it healthy.

- **The yield report could not read half of its own input, and said nothing.**
  The A1 denial log stamps `time.time()` floats; the Canopus lifecycle ledger
  stamps `isoformat()` strings; the reader knew only the second. An unparsed
  stamp answered `None`, and `None` reads out as a 0-day observation window and
  a blank last-catch rather than as an error, so all nine denial-sourced guards
  were pinned to a permanently 0-day window no matter how long the log ran.
  `NO YIELD` -- the one verdict the report exists to reach -- was therefore
  unreachable for half the mechanisms BY CONSTRUCTION, which is precisely the
  state the report was built to end. It survived a 28-test frozen contract
  because every denial fixture in that contract stamps ISO strings, a format no
  real denial record has ever carried: the mismatch was untestable by
  construction too. Found by running the shipped tool against the live logs, not
  by reading it.
- **`cmd_freeze` recorded a refused candidate as `freeze_already_active`**, a
  copy of the branch above it whose prose the control flow disproves one line
  up. It inflated one cause with refusals it never made and left
  `candidate_refused` looking as though it never fired on that path. Both
  existing guards passed it: one checks that a recorder is CALLED, the other
  that a cause is emitted SOMEWHERE in the file, and neither reads the argument.
- **The declared mechanism list omitted all eight PreToolUse dispatcher
  guards**, so the guards least likely to fire -- each waits on a model mistake
  -- were invisible in the report rather than `TOO EARLY`, which is the exact
  confusion the list's own comment says it exists to end. The new test reads the
  registry out of `.claude/hooks/_dispatch.py` by AST, so a ninth check fails a
  test instead of vanishing.
- `record_refusal` claimed `log_denial`'s total posture while catching only
  `OSError`, so any other failure converted a clean refusal into a traceback.
  `sc-trace` tracebacked on an unreadable `--contract` while `--anchor` already
  exited cleanly, on the command an author runs while still guessing at a path.
  The yield report's `--root` moved only the lifecycle ledger, leaving the
  denial half reading the real workspace.

### Added

- **The evidence page for the operator's second approval existed, and nothing
  required it.** `canopus.py pack` renders the page a slice is signed off from,
  wrote nothing, and could therefore not be shown to have run at all. Measured
  2026-08-02: the slice shipped that day was signed off on a PROSE SUMMARY, the
  exact thing the standard's own NEVER list forbids, and no artifact records
  that it happened. `pack` now appends one idempotent `pack` event to the
  lifecycle ledger and `release --ship` refuses without one, naming the command
  that clears the refusal. The claim is deliberately narrow and the wide one is
  retired: this does NOT make the second approval real, because no machine
  witnesses a human reading. What it does buy is that a render exists, that it
  is no older than the attestation it reports on, and that the attestation still
  stands for the tree being shipped. The middle claim alone was wider than the
  code and `/scrutinize` said so at step 11: the freshness test compares against
  the attestation's STORED stamp, so an edit made after the render and never
  re-attested moved neither stamp and the stale render shipped. Rather than ship
  eight met criteria and name the ninth as a residual, the contract grew: a
  `--ship` against a judgeable tree whose record does not attest it is now
  refused, naming the test gate. "Judgeable" is the tree sample's own answer --
  a root that is not a git working copy cannot be described, and refusing there
  would be a refusal on a fault rather than on haste. The ledger is believed about the render exactly when it remembers the freeze
  it is being asked about: one that has lost it warns `unverifiable` and lets
  the ship through rather than refusing, because a gate that pushes an honest
  operator toward `--force` is worse than no gate. Fail closed against haste,
  open against a broken disk. `--window` is never gated. Stress-tested by
  architecture council (Kimi k3 as devil's advocate, Gemini, Grok): the scope is
  Grok's, the degraded mode is Kimi's, and Gemini's verdict to drop the gate was
  rejected on the measured ground that `rm -rf .canopus` was already terminal
  for `--ship` before this change.

- **A frozen contract can hand-author every fixture for a store it never calls,
  and nothing notices.** Measured at `a2cb7d1^`: the gate-yield contract held 23
  tests, its code read the denial store, it called the real writer zero times,
  and it stamped `"ts": "2026-08-02T00:00:00+00:00"` while the writer emits a
  `time.time()` float. The tool shipped useless for half its mechanisms and the
  23-test contract said nothing, because the mismatch was untestable by
  construction. `scripts/utils/production_shape.py` now refuses a contract whose
  code under test reads a registered record store when no test in that contract
  builds its fixtures by CALLING that store's writer. Soft at `approve` and
  `freeze` over what exists, hard at attestation over the full closure, because
  the module under test does not exist yet at freeze time. The witness is the
  writer, not the live file: a fixture minted by the real writer carries the real
  shape by construction and stays hermetic. The check is total -- an internal
  fault refuses nothing -- and it REPORTS that fault on stderr rather than
  leaving the gate quietly toothless. The registry is one enumerated table, not a
  heuristic: a gate that accuses falsely is a gate people learn to disable.

- **A success criterion and the test that decides it were written twice, by
  hand, with nothing detecting a divergence.** Measured 2026-08-02 across the two
  slices shipped that day: one gate artifact states seven success criteria, its
  contract carries 28 test functions, and the string `SC-` appears in those tests
  three times, all three in prose. Five of the seven criteria were traceable to
  nothing at all. A test now CLAIMS the criterion it decides in its docstring,
  and `approve` and `freeze` refuse a contract leaving a stated criterion
  unclaimed, or claiming one the artifact never stated. `scripts/sc-trace.py`
  prints the binding without running the gate. Two false-positive classes are
  closed by position rather than by heuristic, and both were measured on the real
  corpus rather than imagined: on the artifact side a criterion is DEFINED by the
  line it opens, so `| H1 | HIGH | SC-13 rewritten: ... |` in a critique table and
  `SC-1 to SC-7, from the spec` in a later phase are mentions and not definitions;
  on the test side a claim OPENS the docstring, which this module earned by
  refusing its own contract at step 8, where three tests describing those very
  false positives were read as claiming SC-13, SC-7 and SC-9. Read the guarantee
  narrowly, and it says so in its own clean output: it proves a test claims to
  decide a criterion, never that it does. The check lives inside the one builder
  `approve` and `freeze` share, so it is total and fails OPEN on anything short of
  a definite finding -- a parser defect must not refuse every slice in the
  workspace including the `/canopus back` that would repair it.

- **Every refusal the engineering standard ever made had vanished.** Measured
  2026-08-02 before this existed: the Canopus lifecycle ledger held 152 events
  and not one refusal, because the twelve early returns across `approve`,
  `freeze` and `release` all exited without touching it. So "successful
  deterrent" and "pointless ceremony" were the same observation for the
  lifecycle gates, which is exactly the state the denied-attempt counter ended
  for the write guards a day earlier. Twelve returning sites are now recorded
  with their mechanism and a stable cause class, plus FOUR RAISING CLASSES the
  plan did not predict: an anchor that is not a file, a contract that is not
  red, a damaged manifest all raise and land in `main`'s handlers, and counting
  only the returns would have measured half the yield and called it the yield.
  Two refusals are deliberately not recorded, both found by the ordinary suite:
  a root the tool refused to accept has no ledger to belong to, and a refusal
  CAUSED by the ledger failing cannot be recorded in the ledger. The reporter
  (`scripts/gate-yield.py`) is minimal on purpose: with a zero-day denial window
  its job is not to adjudicate the subtraction list but to say when that list
  can be adjudicated, so `TOO EARLY` is a verdict distinct from `NO YIELD` and
  each mechanism is judged over its own source's window. It cannot form a
  removal recommendation at all -- a forbidden-verb list plus its test make the
  operator's no-deletion rule a property of the code rather than a promise in
  prose. Step 11 found the report could be made to LIE about its own numbers: a
  crafted denial reason forged a row indistinguishable from a real one, the
  third time on this branch a guard was applied to one function and not its
  sibling, so `printable` now lives in `scripts/utils/denial_log.py` and both
  readers import one implementation. The slice cost two release windows and
  earned two rules for the planning gate, both from measurement: a contract test
  takes its own scratch root and compares invariants rather than raw text, and
  the contract file is run through the commit gates before it is frozen.
  `scripts/utils/gate_yield.py`, `scripts/gate-yield.py`,
  `tests/test_gate_yield.py`, `tests/test_gate_yield_render.py`.

- **The engineering standard had no way in that did not start with a file path.**
  `/canopus` is the operator's surface onto the lifecycle. Bare, it orients
  rather than reports: which step of thirteen, which act, what was just
  finished, what comes next, and the whole agenda every time. Only six of the
  thirteen moments leave a durable trace, so the payload carries `derived` and
  `basis` and says plainly when it is inferring, because a confident "step 10 of
  13" where nothing is knowable is a lie the operator would reasonably act on.
  The count is thirteen because the operator numbered his own two approvals into
  the sequence on 2026-08-02; the consequence turned out better than the reason,
  since acts 1 and 3 now END on his step and act 2 is the only act with no human
  in it. `/pre-impl` is folded in as `/canopus plan`, its body moved rather than
  rewritten, and gate artifacts keep the `plans/YYYY-MM-DD-pre-impl-{slug}.md`
  filename so the nine existing ones stay findable. The slice was re-taken
  mid-build through its own `/canopus back`: two of the 28 frozen tests
  described the between-slices state and could never be green while the lock
  they were frozen by was held, and mutation showed the position ladder was
  pinned by nothing else. The ladder moved into a pure function, the contract
  went 28 to 33, and the same three mutations went from killing 0, 0, 0 to
  killing 2, 1, 1. Step 11 then found the display contradicting itself: a freeze
  with an empty label printed "no slice open" three lines above "Step 8 of 13".
  `scripts/utils/canopus_steps.py`, `scripts/canopus.py where`,
  `.claude/skills/canopus/`, `tests/test_canopus_steps.py`,
  `tests/test_canopus_where.py`.

- **We scanned everything this workspace writes and nothing it installs.**
  `scripts/harness-audit.py` audits the code and text that arrives from outside
  and then loads into, or executes inside, every session. Measured on one
  machine on 2026-08-02 before the tool existed: 10 plugins on disk (4 at version
  `unknown`), 116 markdown files, 75 scripts, 28 hook files, and 6 PostToolUse
  hooks from a single plugin each running a bash script out of the cache. Files
  of that surface scanned by any existing layer: zero. `prompt-guard.py` covers
  four data ingest paths and none of this, and `superpowers` moved 5.1.0 to
  6.1.1 on 2026-07-14 with nobody reading the diff. The audit does three things:
  enumerates every hook command running in the session that this repository does
  not own (12 on the first run, from three sources including user-level
  settings); hashes the installed surface against a reviewed baseline committed
  in the PRIVATE data overlay, so the next upgrade is a named list of changed
  files rather than nothing. The baseline started in the public engine's
  `config/` and the commit gate refused it: 236 sha256 digests are high-entropy
  strings, and `detect-secrets` was right. Adding a pragma or an allow-list entry
  to push our own file is the move this workspace forbids, so the file moved
  instead, which also removes the noise it would have been on every other clone; and scans all loaded content for injected
  instructions. It is a REPORTER, not a gate: it refuses nothing, blocks no tool
  call, and is wired into no hook, because THE LAW says the honest order is to
  measure the first run's yield and let that number decide whether it earns a
  hook, a timer, or removal. First run: 12 third-party hooks inventoried, 236
  installed files baselined, 453 loaded files scanned, zero injected patterns,
  and one symlink named (`superpowers/6.1.1/AGENTS.md` to its own `CLAUDE.md`,
  benign, and previously invisible to everything).
  The detection vocabulary moved to `scripts/utils/injection_patterns.py` and is
  now imported by both consumers rather than duplicated; unlike the credential
  patterns, neither consumer blocks, so neither needs an embedded copy and no
  lockstep test is required. Idea taken from `affaan-m/ECC`'s AgentShield;
  the question was taken, not the design.

- **How much process a change carries is now computed from the change.**
  `scripts/utils/slice_depth.py` classifies a set of paths as `full`, `standard`
  or `light`; `scripts/depth-gate.py` is the pre-commit hook that makes the answer
  bind; `scripts/slice-depth.py` answers "how deep is what I am about to commit"
  from the terminal (`--files`, `--range`, `--json`). Canopus previously ran the
  same eleven steps through a CHANGELOG typo and through a change to the
  credential patterns, and the obvious repair (collapse the lifecycle for
  everybody) is a uniform trade of rigour for speed, including where rigour is the
  point. Two rules keep this from being decorative: the floor cannot be diluted
  (one enforcement-surface path among fifty prose paths is still `full`), and
  calibration may only ever REMOVE ceremony, never lower the depth of work that
  touches the surface however small the diff. The gate refuses a `full` change
  while no Canopus freeze is held, and the refusal names the file that raised the
  depth and the command that proceeds properly. The escape is
  `HEADING_OS_DEPTH_OVERRIDE="<reason>"`: an empty reason still refuses, and both
  the refusal and the override are counted through the denial log, so "we
  overrode it every time" is readable rather than folklore. Deliberately
  bypassable and deliberately not promoted to the push wall, because depth is a
  process discipline rather than a leak wall, and locking the operator out of an
  emergency fix to his own hooks would trade a real risk for a procedural one.
  Measured with the shipped classifier on 2026-08-01, over the 60 engine commits
  then current: 53% land on `full`, 42% `standard`, 5% `light`. The window slides
  as commits land, so the reading is anchored to its date rather than quoted as a
  standing property. Calibration is therefore not primarily a
  speed win; what it buys is the right to keep full depth on that half without the
  standard becoming too heavy to use on the rest.
- **The false-positive rate was measured against a denominator thirty times too
  small.** `scripts/scrutinize-fp-aggregate.py` counted finding lines with
  `^\s*\[([BHMLN]\d+)\]`, which tolerates leading whitespace and nothing else,
  while the report format drifted across 2026-04 to 2026-08 and finding lines
  gained heading prefixes (`### [L1]`) and bold-list wrapping (`- **[M1]**`).
  Measured over the real 63-report corpus: the old pattern matched **7 findings in
  3 reports**, the corrected one matches **213 in 41**. Every false-positive rate
  this instrument has ever reported was computed against the wrong denominator.
  The fix enumerates the five prefix conventions actually observed rather than
  loosening the anchor, tolerates iteration-suffixed ids (`[L1-i2]`), and reads
  the confidence annotation from the rest of the finding's own line so the three
  spellings that exist all parse. Stated rather than hidden: a handful of
  pre-standardization reports use a bracket-less convention and still count zero,
  because matching a bare `H1` token safely needs its own vetting against prose.

- **The cost side of every subtraction argument is now a number.**
  `scripts/slice-cycle-time.py` reads the ledger Canopus already writes and
  reports, per slice, approve-to-release duration beside the friction that
  happened inside it: release windows opened, approvals retaken, verify failures.
  First reading over the real history: **9 shipped slices, median 5.76h, mean
  7.0h, with 6 windows, 19 retakes and 6 verify failures across them.** Two
  deliberate choices. Duration starts at the FIRST approval, so a retake cannot
  shorten the slice it lengthened. And an unshipped slice reports `open` rather
  than zero, because folding a running slice in as zero flatters every average.
  The limit is stated in the output itself rather than left to be discovered: the
  earliest machine-recorded moment is the approval, so deciding what to build,
  planning and writing the test are real work this number does not contain.

- **A slice that fails mid-flight now has a way back.** `scripts/slice-rollback.py`
  returns the frozen paths to the commit the freeze recorded, keeping a copy of
  everything it replaces under `.logs/rollback/<timestamp>-<label>/` and printing
  where.
  Dry by default; `--apply` executes; `--json` for a runner. Recovery was manual
  git surgery, which is tolerable while someone is watching and becomes a hazard
  the moment the unattended loop runs, because a build that fails at 03:00 leaves
  a half-written tree and the next thing to touch it is another automated step.
  Two deliberate properties: nothing is ever deleted, and an untracked file is
  named but never moved, because whether it belongs to the failed slice is not
  knowable from here and guessing is the one way this tool could destroy the work
  it exists to protect. It also reads a freeze manifest that fails strict
  validation, on purpose: the slice that failed badly enough to need this is
  exactly the slice whose manifest may be what broke, and a recovery tool that
  refuses on a schema mismatch is useless in the only situation it exists for.

- **Every guard refusal is now counted.** `scripts/utils/denial_log.py` appends one
  redacted line per refused path, and `scripts/denials.py` reports the counts from
  the terminal (`--days`, `--detail`, `--json`). Instrumented: the PreToolUse
  dispatcher (one call site in its main loop, so all eight checks and any ninth
  added later are counted by construction rather than by an author remembering),
  the secret scanner, the leak guard, the content guard, and the push-time routing,
  content and tracked-secret walls. Why it exists: the workspace enforced at a dozen
  points and counted none of them, so "this guard is a successful deterrent" and
  "this guard is pointless ceremony" produced the same observation and no guard
  could honestly be judged or removed. Two properties are asserted rather than
  assumed: a record never carries the refused content (reason and path both pass
  through `redact()`, bounded at 512 characters), and a logging failure never turns
  a deny into an allow (the writer returns a bool and raises nothing; the contract
  drives the live hook with an unwritable log destination and requires the block to
  survive). Two scope statements rather than implications. Refusals by the Canopus
  LIFECYCLE gate — `freeze_gate()` and the approve/verify gates in
  `scripts/canopus.py` — are NOT counted here; they measure gate friction, not
  attempted policy violations, and belong to the per-slice ledger. That exclusion
  is narrow: the PreToolUse `check_canopus_freeze` is one of the eight dispatcher
  checks and IS counted. And the unit is a refused PATH, not a refused action, so a
  commit the content guard refuses over six lines contributes six records; read the
  per-mechanism totals as caught-something versus never-fired, which is the
  discrimination the counter exists for, and not as a frequency ranking across
  mechanisms. The log lives under the gitignored `.logs/` because records name real
  paths and this repository is public.
- **Remote-identity wall on every supervised push.** No repository other than the
  engine may be pushed to the engine's push remote, or to a remote GitHub reports
  as public. Two checks in `scripts/utils/git_push.py:remote_objection`: an
  offline comparison of normalized push URLs that carries the hard guarantee, and
  a GitHub visibility lookup that raises the ceiling when the network can answer
  and warns without blocking when it cannot. Wired at `supervised_push` and as a
  `push-all` precondition, the latter so `--dry-run` reports the refusal too.
  Reach, stated honestly: six callers route through `supervised_push`; ten other
  call sites push directly and are not covered, none of which pushes the data
  overlay. Neither check validates that a repository points at the RIGHT private
  remote, only that it does not point at the engine's or at a public one, and the
  offline check itself fails open with a printed warning when the workspace roots
  cannot be resolved. One of those ten, `scripts/dev/publish-marketplace.py`, is
  uncovered on purpose rather than as debt: it deliberately pushes a repository
  meant to be public, and routing it through `supervised_push` would make the
  wall refuse a push that is correct. Check B's visibility answer can also be
  `internal`, and only `public` is refused; on a GitHub Enterprise organisation
  an `internal` repository is readable by every member of that enterprise, and
  that reading is currently permitted, which is the limit stated here rather
  than left to be discovered later.

### Changed

- **`create-data-repo.py --public` now creates a repository it cannot push to.**
  The flag has always been documented as almost never what you want for a data
  overlay; it is now refused rather than discouraged, because `first_push` routes
  through `supervised_push` and a public remote is exactly what the new wall
  exists to stop. The repository is still created and origin still wired, so the
  failure is visible and recoverable rather than silent. Separately, the success
  message printed "created private GitHub repo" unconditionally, so `--public`
  produced a false assurance about the very property this release walls; it now
  names the visibility it actually created.

- **`push-all.py`: a refusal about one repository no longer cancels the other.** The command had ten `sys.exit` sites making two different statements in one uniform, and the branch check was one of the wrong kind: whenever the engine clone sat on a feature branch the process died at the engine and the DATA overlay was never pushed at all. Since the engine sits on a feature branch during every slice of work by construction, the backup had been quietly declining to back up the only irreplaceable half of the workspace for the duration of every slice. A new `RepoNotPushable` separates "this repository cannot be pushed right now" (a branch that is not `main`, an unarmed engine test gate) from "stop the world" (a secret in content, a data artifact in the engine clone, a real-entity token in an engine-routed file, an absent push token, a misconfigured data root). The eight stop-the-world refusals are untouched, at their original exit codes, and `SystemExit` is not an `Exception` subclass so no security refusal can be absorbed as a skip even by accident. Three operator-visible consequences: the **DATA overlay is now pushed first** (the engine's pre-push hook runs the full suite inside the push, and data is the only half that cannot be reconstructed), a skipped repository is **committed locally and named with its reason** in a closing summary, and **exit `3` now means a partial backup** rather than a failure. Exit `0`, `1` and `2` keep their meanings exactly; nothing in the workspace branched on this exit code, so `3` breaks no caller. `--dry-run` reports the same skips and still writes nothing: the precondition check moved above the dry-run return, because a dry run that hides the one thing the change surfaces would be a dry run that lies. Documented for operators in `docs/DEPLOYMENT.md` and in the `/backup`, `/sync` and `/push-updates` skills. Exit `3` distinguishes its two shapes in the headline rather than in prose: `Partial: N of M repo(s) not pushed` means the rest pushed and verified, while `NOTHING PUSHED: all M repo(s) skipped` means no new off-machine copy exists at all. The managed-workspace and pre-cutover modes push a single repository, so exit `3` there can only ever be the second shape, and calling that "partial" would have been a false success claim about the only irreplaceable half of the workspace.

### Fixed
- **The depth floor depended on how a path was spelled.** `scripts/depth-gate.py
  .claude/hooks/_dispatch.py` refused, and the same file passed as an absolute
  path exited 0; `classify(["scripts/./push-all.py"])` answered `standard`.
  `_normalise()` collapsed backslashes and a single leading `./` and nothing
  else, in a module whose own docstring names the floor as the thing that cannot
  be diluted. The wired gate was never bypassed, because pre-commit feeds
  git-relative names, but the advisory CLI the operator reads BEFORE starting
  work was, which is the reading that decides how much process a change carries.
  `classify()` now takes the workspace root, collapses dot segments and
  re-expresses an absolute path relative to the root, once per call rather than
  once per path. Regression coverage lives in
  `tests/test_slice_depth_path_shapes.py`, outside the frozen depth-calibration
  contract, so a later regression does not bind every future slice to this
  slice's behaviour.
- **The rollback tool printed a save location that did not hold the files.**
  With a freeze label carrying `../`, `scripts/slice-rollback.py --apply`
  reported one directory and wrote the replaced bytes to another. The label is
  untrusted input by construction: `_read_freeze()` deliberately reads the
  manifest raw when strict validation fails, because the slice that fails badly
  enough to need a rollback is the slice whose manifest may be what broke. For a
  tool whose single promise is that nothing is deleted and the operator is told
  where it went, printing the wrong path is the whole failure. The label is now
  slugged, and the destination is resolved and prefix-checked against the log
  root before anything is written.
- **The leak guard wrote the refused content into the denial record.**
  `_LITERAL_RE` captures the data-path token plus everything to the closing
  quote, so a real path landed in `.logs/denials/denials.jsonl` whole, and
  `redact()` does not strip it because a path is not credential-shaped. That
  contradicts the property `scripts/utils/denial_log.py` states and its sibling
  guards honour. The record now carries the token class; the operator's terminal
  still shows the literal, because that is a reading, not a record. Beside it,
  `scripts/denials.py` renders every record field through a printable filter: a
  record's `path` is a denied tool call's `file_path`, which a prompt injection
  can shape, so replaying an escape sequence into the operator's terminal when he
  reads the log would make the instrument a delivery mechanism. Mechanism names
  that render to the same safe string are summed rather than overwritten, so the
  per-mechanism totals still add up to the record count printed above them.
- **Smaller corrections from the same pass.** `push-all.py` now records the repo
  name in the denial context, because the scanner logs a repo-relative path and
  the wall runs over both clones, so the same relative path produced two records
  nothing could tell apart. `slice-cycle-time.py` reads a naive ledger timestamp
  as UTC instead of raising `TypeError` out of the sort and losing the whole
  report over one hand-edited line, and reports "no completed slice to average
  yet" instead of "median Noneh". `.claude/hooks/_dispatch.py` no longer grows
  `sys.path` on every counted refusal.
- **The PostCompact hook wrote the compact summary into the tracked handoff
  archive unmodified, so a session discussing the secret scanner produced files
  that refused `push-all.py`.** Measured on 2026-07-31: four findings in two
  files, both generated by the workspace itself. The summary is now redacted
  before it is written, and every payload field that reaches a tracked file goes
  through the same redactor, not only the summary: the trigger, the session id
  and the transcript path each refused the wall on their own when tested. If
  redaction fails for any reason, including the import failing or the redactor
  returning a non-string, the handoff is QUARANTINED to a gitignored directory
  rather than lost or written raw, a pointer carrying none of the text lands at
  the normal location, and the systemMessage, the continuation prompt and the
  state entry all say so. Losing it is unacceptable because the hook runs after
  the session's context is discarded; writing it raw resurrects the incident
  silently, because the hook's stderr is read by nobody.
- **A large enough `Write` walked past the blocking secret gate without
  defeating a single pattern.** The connection-string pattern opened with an
  unbounded run before the scheme separator, giving the regex engine no anchor,
  so it retried at every start position and cost grew quadratically: 5.8s at
  100 KB, and an unbroken 400 KB run did not finish inside two minutes. The
  PreToolUse hook passes whole file content as one string, its timeout is 30
  seconds, and a timed-out hook returns no decision, which the harness reads as
  no objection. The run is bounded now (400 KB in 0.035s), differenced across
  every tracked file in both repositories, 5,067 files and 1,194,834 lines, with
  zero verdict changes. One contrived class is genuinely lost and is named in
  the source rather than left to be discovered.
- **Every guard on the credential vocabulary compared data, never the
  consumer.** An environment-conditional rebind of the pattern list shipped with
  all of them green and the gate off for every description, and a rebind inside
  the module's `__main__` block was invisible by construction, because
  production runs the hook through `runpy` under that name while the tests
  loaded it under another. `tests/security/test_SEC_018_gate_behaviour.py` drives
  one positive sample per description through the gate exactly as production
  invokes it and compares the verdict against the scanner's. Its environment
  sweep is symmetric: setting a name and removing it are different experiments,
  and only the second reaches a construct keyed on the absence of a name pytest
  defines.
- **Three files were exempt from the write-time scan by BASENAME, anywhere in
  either repository.** A planted key was written successfully to a decoy
  `secret-scanner.py`, `prevent-secrets.py` and `.env.example` outside their real
  locations. All allowances are path-scoped now, and `.env.example` loses its
  exemption entirely: a template holds placeholders by definition, so a real
  credential in one is a finding rather than a false positive.
- **`/next` rendered its strongest-signal header over nothing after every
  successful compact,** for the whole life of the handoff archive, because the
  pointer carried none of the headings its parser reads. It carries them now,
  and the parser stops at the summary heading so the model's own prose cannot
  append its steps to the pointer's.
- **The placeholder exclusion tested how a value BEGAN, not what it was.** All
  seven word alternatives were defeated identically: a value that merely started
  with a marker was excluded whole, however long and however random its tail, so
  a real password prefixed `xxx` passed the commit hook, the blocking PreToolUse
  gate AND the push-time content scan, then landed in a public repository. The
  exclusion now fires only when the value TAKEN WHOLE has placeholder shape.
  Measured across both repositories before shipping: 5089 files, 1327584 lines,
  0 new positives, 0 regressions. The residual false positive is recorded and
  pinned by a test rather than left for an operator to meet as a stopped push: a
  placeholder that breaks word shape (`changeme123!`, `your-P4ssw0rd`) is
  flagged, because such a value cannot be told apart from a real password.
- **The same decoy hole the basename allowances carried was still open one level
  up, for directory segments.** A planted key was written successfully to
  `outputs/scratch/tests/security/`, `knowledge/tests/security/`,
  `outputs/scratch/.sessions/` and `crm/contacts/.sessions/`; in the data
  overlay such a path is not gitignored, so the decoy would have been tracked,
  and the scanner's `SKIP_PATHS` carries no counterpart for either directory.
  Both directory allowances are anchored to this workspace's own root now.
- **The credential patterns existed in two hand-maintained copies that had
  already drifted:** the environment-password entry carried a placeholder
  exclusion in `scripts/secret-scanner.py` and not in
  `.claude/hooks/_dispatch.py`. The vocabulary moved to
  `scripts/utils/secret_patterns.py`, the copies were reconciled, and a test
  now fails on any future divergence.
- **APScheduler jobs were being discarded for being late, and every health surface said the daemons were fine.** `misfire_grace_time` defaults to 1 second, so a job whose due moment slipped past that was DISCARDED rather than run late, leaving only a journal warning. Measured over the 24 hours to 2026-07-30 on one machine: the `sync-exchange` 1-minute heartbeat lost **1059 of 1440** runs, and its 2-hour Exchange mail and calendar sync ran **twice instead of twelve times**. Observed lateness ran from 24 seconds to 27 minutes, and the cause is tick latency rather than load, so this was the steady state and not an incident: a freshly restarted process had already dropped 12 runs inside 14 minutes. The silence was structural, because the heartbeat that would have accused the daemon was itself 74% dropped, so `systemctl` reported `active running` throughout. The fix is one shared `JOB_DEFAULTS` (`scripts/utils/scheduler_defaults.py`) passed to the four scheduler CONSTRUCTIONS rather than to the eleven `add_job` calls, because APScheduler fills a job's unset options from its scheduler's defaults, so a job registered later inherits the safe value without its author knowing the constant exists. That inheritance is the whole point: the correct value already existed in `scripts/bridge_daemon/scheduler.py` and did not travel to the five jobs `scripts/bridge-daemon.py` adds to that same scheduler object, two lines below a comment that diagnosed this exact bug. Only `misfire_grace_time` changes behaviour; `coalesce` and `max_instances` already match the library defaults and are stated so the pairing that makes `grace=None` safe reads in one place. A call site may still override any option, in both directions. `tests/test_scheduler_misfire_guard.py` walks the AST of `scripts/` and refuses any scheduler built without `job_defaults`, or with a `job_defaults` that omits `misfire_grace_time`; it was observed failing on all four sites before the edits landed. It checks construction, and `scripts/` only, and says so. **Operator-visible consequence:** the 2-hour Exchange sync now completes twelve times a day rather than twice, a sixfold increase in real work on that path that is the configuration finally being honoured.

## [0.7.0] - 2026-07-19

The memory-reliability release. One stale one-line index hook once produced a confidently wrong answer on a live decision; this release makes that class of failure structural rather than incidental (a fetch-the-record discipline, a mechanical guard on the pointer layer, a leaner always-on footprint), and hardens operator privacy in the public engine.

### Added
- **Memory-discipline rule (`.claude/rules/memory-discipline.md`), always-on:** before any consequential action (a commitment, a decision, a fact or figure, a deadline, a live state), open the authoritative record, not the pointer that surfaced it (a `MEMORY.md` index hook, a recalled snippet, a summary). Freshness order is primary source, then the full record file, then the index hook; a contradiction between layers is flagged, never papered over. The rule's second half keeps the always-loaded pointer layer lean: hooks carry a topic and a pointer, never a volatile value (a price, a ceiling, a live count, a live date), and a record's one-line hook is synced whenever its body changes. Engine-classified, fleet-wide.
- **Volatile-pointer guard (`scan_volatile_hooks` in `scripts/utils/memory_health.py`, wired advisory into `/memory-hygiene`):** a high-precision, advisory scanner that flags any `MEMORY.md` index hook or memory-file `description:` frontmatter quoting live money state (a currency figure, or a magnitude token beside a money-context word), the exact class that can go stale into a wrong number. Spec magnitudes with no money context (`128k context`, `i9-13900K`, `1M-context`, a local model ceiling) are deliberately not flagged, and `threads/` pointers are out of scope. It never gates. Both directions are pinned by `tests/test_memory_volatile_hooks.py`.

### Changed
- **Always-on context economy, `humanization.md` compressed 40% (22,022 to 13,215 bytes):** the empirical-datapoint prose and the full banned-vocabulary catalog moved to `reference/humanization-empirical-basis.md` and `reference/humanization-banned-vocabulary.md`, loaded on demand; every operative directive plus the Step-0 calibration gate and the sub-15% byte-immutability directive stay resident verbatim. A behavioral A/B check (two agents, identical prose samples, only the rule version differing) confirmed the compressed rule drives the same humanization decisions as the full one, including reaching the moved banned-vocabulary catalog through the on-demand read path.
- **`/scrutinize` Kimi code voice gains an opt-in 1M-context "wide" mode:** `references/code-review-voice.md` now documents that on our plan `k3` carries a 1M-token context, so for a deep audit (`--relentless` or an explicit whole-subsystem review) the Kimi voice can be handed the full target instead of trimming to the highest-risk files — a second independent full-context read alongside the Claude-native voice. The routine focused-diff default is unchanged (wide mode is opt-in because `k3` is slow and verbose). Paired with a proxy-side addition of the `kimi-for-coding-highspeed` model (K2.7 Code HighSpeed, ~2s vs `k3`'s ~18-20s in a live probe) for latency-sensitive Kimi paths; the transport seam already reaches it by id, no engine code change.
- **Deep-research degrades a missing `k3` to `kimi-for-coding`, not to corpus-only:** `deep-research-advance.py` now probes the proxy catalog once per run (`_reason_model_for(probe_proxy())`) and picks `k3`/`max` when `k3` is present, else `kimi-for-coding` with no `reasoning_effort` — so if `k3` ever drops off the proxy, the Phase 0/2 reasoning still runs (shallower) instead of falling all the way through to a corpus-without-analysis run. This mirrors `/council`'s `k3`->`kimi-for-coding` degrade and closes the single-point-of-failure the v0.6.0 migration introduced by hard-pinning research to `k3`. An unreachable catalog stays optimistic on `k3` (the existing phase-level degrade still catches a real failure). New tests in `tests/test_deep_research_advance.py` (`_reason_model_for` selection matrix + a run-level assertion that both reasoning calls use the fallback model when `k3` is absent).

### Fixed
- **Operator-private entities removed from the public engine and scrubbed from git history:** `scripts/chronicle.py` no longer hardcodes personal proper nouns; its keyword pre-filter became generic engine defaults merged at runtime with a private keyword file from the data overlay (`_personal_keywords()` reading `<data_root>/config/chronicle-personal-keywords.txt`), so a real private entity never lives in the shareable engine tree while detection behavior is preserved on the operator machine. The `md-to-docx-letter.py` demo path and the `test_chronicle.py` plus new `test_memory_volatile_hooks.py` fixtures were made fully fictional. The public history was then rewritten with `git-filter-repo` (141 commits, private strings replaced by `REDACTED`) and force-pushed, with branch protection recorded, toggled, and restored around the push; a fresh clone confirms zero occurrences across every ref and tag.

## [0.6.0] - 2026-07-18

### Added
- **One shared proxy-transport seam for every external-model call (`scripts/utils/proxy_transport.py`):** `/council`, `/scrutinize`, `deep-research-advance`, and the Anthropic `llm_fallback` cascade now reach Kimi/Grok/Gemini through a single prompt-agnostic function, `call_model(model, prompt, *, temperature, max_tokens, timeout, reasoning_effort) -> str`, pointed at the local CLIProxyAPI proxy (`http://127.0.0.1:8317/v1`, OpenAI-compatible) and authenticated with `CLIPROXY_API_KEY` from the gitignored engine `.env`. The seam sends the prompt verbatim and injects no system block, so each caller keeps ownership of its own prompt coupling (council injects the 31C block via `council_prompts`; deep-research sends raw prompts and never leaks business context into a third-party cloud). It reproduces the thinking-model truncation retry once — empty content plus `finish_reason=length` retries at `max(max_tokens*2, 16384)` and then raises an accurate `length` truncation error, never a safety-block claim — routes `content_filter` to a distinct safety error, and classifies every OpenAI SDK exception (auth, rate-limit, 404, bad-request, timeout, connection, 5xx) into an actionable `RuntimeError`. New `tests/test_proxy_transport.py` (10 cases: base-URL, missing-key, length-retry success and exhaustion, content-filter vs empty-stop, timeout forwarding, and `reasoning_effort` presence/omission).
- **Kimi K3 wired as a per-voice reasoning upgrade (thinking `low`/`high`/`max` via request field, not config):** the seam threads an optional `reasoning_effort` that rides `extra_body={"reasoning_effort": ...}` and is sent ONLY when set. Deep-research reasons through `k3` at `max`, the `/council` Kimi voice dispatches `k3` at `high` (with graceful degrade to `kimi-for-coding` when `k3` is absent from the catalog), and the `/scrutinize` Kimi code voice reviews at `k3` `high`. `kimi-consult.py` gained a `--reasoning-effort {low,high,max}` flag. The `llm_fallback` cascade and the default Kimi council pin stay on `kimi-for-coding` (fast, quota-light) and pass no effort field.

### Changed
- **Council trimmed to three proxy voices; pins live in `config/council-models.json`:** `gemini-3-flash` / `grok-4.5` / `kimi-for-coding`. The three consult adapters (`gemini`/`grok`/`kimi-consult.py`) are now thin delegates over `call_model`; their exit-code contract keys on the seam's `"is missing from .env"` sentinel (missing key -> exit 2, any other proxy failure -> exit 3). `/scrutinize` code voices repoint to proxy Kimi (`k3` `high`) plus Claude-native review inside Claude Code. `/council` SKILL.md 1.3 -> 1.4.
- **Council freshness checks the proxy catalog, not vendor APIs or Ollama:** `scripts/utils/council_freshness.py` was rewritten to `probe_proxy()` + `classify_proxy_model(provider, pin, catalog)` (absent pin -> broken, unknown -> unknown, present -> ok) + `assess()` over gemini/grok/kimi; ten now-orphaned vendor/Ollama probe helpers were deleted. The daily freshness nudge and `/prime`'s health line are unchanged externally.
- **`llm_fallback.yaml` chains refreshed; Kimi leads Sonnet/Opus:** haiku = [`gemini-3.1-flash-lite`, `grok-3-mini-fast`], sonnet = [`kimi-for-coding`, `gemini-3-flash`, `grok-4.5`], opus = [`kimi-for-coding`, `gemini-3.1-pro-low`, `grok-4.5`]. `_invoke_vendor` gained a `kimi` branch (calls `consult_kimi`, no `reasoning_effort`).
- **Deep-research reasons through the proxy:** `deep-research-advance.py` now calls a local `kimi_reason()` shim over `proxy_transport` (`k3`, `reasoning_effort="max"`) in place of the deleted Ollama-cloud transport.
- **Single-transport posture is intentional (no paid-key fallback):** a proxy outage now degrades `/council` and deep-research rather than silently falling back to metered vendor keys. The fix is to restore or replace CLIProxyAPI, not to re-add vendor keys. Ollama keeps ONLY `bge-m3` embeddings and the `gemma3` chronicle model.
- **Lint baseline rebaselined honestly:** `.lint-baseline.json` was regenerated after the migration, and the four pre-existing findings surfaced by the whole-tree ratchet were fixed properly rather than baselined away — `B904` (`raise ... from None`) in `memory-touch.py`, `DTZ011` (`date.today()` -> `datetime.now(get_default_tz()).date()`) in `prime-health-parallel.py` and `reminders-notify.py`, and `S105` (`# noqa` on a fake test token) in `test_telegram_bot.py`.

### Removed
- **Legacy metered transports and dead voices:** `scripts/utils/kimi_transport.py` and `tests/test_kimi_transport.py` are deleted (deep-research no longer reaches Kimi via Ollama-cloud); the GLM voice and the Kimi-2.6 / Kimi-Code voices are dropped from `/council` and `/scrutinize`; the `glm` choice is removed from `council-record-verdict.py`'s valid verdicts and tally.
- **Unused `google-genai` dependency:** removed from the `ai-extra` optional group in `pyproject.toml`, `uv.lock`, and the regenerated `requirements.txt` — the direct Google SDK path is gone now that Gemini routes through the proxy.

> Executed via `superpowers:subagent-driven-development` (Tasks 0-10 plus a K3-wiring amendment) with a two-stage review per task — a task-reviewer subagent plus a `k3`-`high` proxy review — and a final Opus whole-branch review that returned zero Critical/Important findings. Full suite green (2762 passed); live smokes SMOKE-OK for gemini/grok/kimi and K3-OK for `k3`-`high`; freshness green. Design and plan live in the gitignored `docs/superpowers/{specs,plans}/2026-07-18-*proxy-transport*` (CEO design archive, not shipped).

## [0.5.0] - 2026-07-18

### Removed
- **Legacy `x-31c-*` frontmatter namespace (F-4.2 close-out):** both frontmatter parsers (`scripts/skill-metadata-check.py`, `scripts/bridge_daemon/sources/capabilities.py`) now accept ONLY `x-heading-*`; a `x-31c-*`-only SKILL.md is rejected as missing its required block. All 94 skills were migrated to `x-heading-*` in v0.4.0, so nothing regresses. The completed one-shot migration tool `scripts/dev/rename-x31c-namespace.py` is deleted — its provenance lives in the git history and the v0.4.0 F-4.2 changelog entry.
- **Operator-identity compatibility shim:** `operator_identity_default()` and its `_is_established_instance()` / `_SHIM_WARNED` helpers are removed from `scripts/utils/workspace.py`; all ~13 call sites (workspace, aggregate-crm, merge/transfer-contact, publish-corporate, and the bridge daemon) now resolve identity through the real operator seam (`operator_slug()` / `operator_org()` / `get_operator()`), so no personal identity literal remains in engine code. An operator sets identity in `config/operator.yaml` (env / data overlay), per `scripts/operator.example.yaml`; a fresh clone still resolves to a neutral "operator". The `tests/test_operator_seam.py` regression guard now forbids any personal literal in the engine sites with no shim exception, and a new `tests/test_skill_metadata_check.py` case proves a legacy-only SKILL.md is rejected.

## [0.4.1] - 2026-07-18

### Added
- **Odin propose-tier weekly delivery surface (C+):** the headless `odin reflect --propose` tier (shipped in v0.4.0) now has a live automatic trigger — a dedicated weekly systemd-user timer `odin-propose.timer` (Monday 05:31, `{{TZ}}`-pinned) running `scripts/odin-cadence-notify.py --propose-only`. The new `--propose-only` mode skips the counts nudge entirely (ops-radar already surfaces the counts daily) and delivers to the CEO's Telegram DM ONLY a real outcome: a standalone, phone-readable (DATA-relative) proposal path when a proposal is produced, or the CRITICAL brain-integrity alert; otherwise it is silent. `odin-cadence-notify.py` was refactored to extract `_run_headless_propose(root) -> Optional[Path]`, with `_maybe_headless_propose` kept as a behavior-preserving wrapper for the normal path. Installer `scripts/install-odin-propose-timer.sh` (with `--uninstall`) mirrors the sibling timer installers and bakes in all three reboot-survival mechanisms (`Persistent=true`, `systemctl --user enable`, `loginctl enable-linger`). Closes the Step-16 loop-back from the notification-bot plan (`odin-cadence.timer` disabled since 2026-06-26; `ops-radar.py` reads `odin-cadence.py` directly). Options A (re-enable `odin-cadence.timer`) and B (fold into `ops-radar.py`) were rejected. The just-produced proposal is located by a same-run mtime fallback when the harness session-date that names the file and the Python `get_default_tz()` reconstruction diverge (a `HEADING_OS_TZ` edit without re-running the installer, or the up-to-600s headless call crossing local midnight), so a produced proposal is never silently withheld. New `--propose-only` cases in `tests/test_odin_cadence_notify.py`. Design: `docs/design/F-10.3-headless-skill-runner.md`.

## [0.4.0] - 2026-07-17

### Added
- **Dedicated Telegram notification bot (Bot API):** all five system notifications (Odin cadence, ops-radar, council model-freshness, reminders, and critical daemon alerts) now send through a dedicated BotFather Bot API bot instead of the userbot client's self-send, which did not reliably push-notify. A new `scripts/utils/telegram_bot.py` holds the shared `TelegramBot`/`TelegramAPIError` (extracted from `scripts/fireside-bot.py`, generalized with an injectable `on_error` callback), and a new `scripts/utils/telegram_notify.py` exposes `notify(target, message) -> bool` — a single entry point that never raises, sends plain text, and refuses the unresolvable `me`/`self`/`saved` (Saved Messages) sentinels by system invariant. The five notification scripts (`odin-cadence-notify.py`, `ops-radar-notify.py`, `council-models-notify.py`, `reminders-notify.py`, `scripts/utils/alert.py`) import it directly; the userbot client stays in place for everything that still needs a real user session (`/telegram`, `/viraid`, Sentinel reads). The target is a channel id/`@username` OR a numeric user id for a DM (press Start on the bot first); the token lives in the gitignored engine `.env` as `TELEGRAM_NOTIFY_BOT_TOKEN`. A `/council` pass (5/5) confirmed a pure-outbound bot needs no persistent server, so there is no new daemon and no webhook. One-time BotFather setup is documented in `docs/TELEGRAM-AND-ALERTS.md`. New `tests/test_telegram_bot.py`, `tests/test_telegram_notify.py`.
- **Nightly memory-consolidation worklist — dream-shadow (Gap #1):** `scripts/dream-shadow.py` is a read-only detector over `auto-memory/*.md` that computes stale + low-salience prune candidates and salience-ranked near-duplicate merge candidates into a dated report, and NEVER mutates, merges, or deletes a memory file — resolution stays with `/dream` (a human reviews, then applies). A host-local systemd-user timer (`install-dream-shadow-timer.sh`, `dream-shadow.{service,timer}`, 03:10, before the nightly memory-index refresh) runs it, and `/prime`'s health block surfaces the latest report. New `tests/test_dream_shadow.py`.
- **Recall citation-reinforcement (Gap #2):** `scripts/utils/salience.py` centralizes the "how load-bearing is this fact" signal (type weight plus a capped access-count reinforcement multiplier) so recall ranking and dream-shadow never compute it differently. `/recall` now reinforces each cited memory-layer hit via `scripts/memory-touch.py`, an atomic, frontmatter-only bump of `access_count`/`last_accessed` (byte-preserving everywhere else), and `scripts/memory-index.py` folds the reinforcement multiplier into its importance score. New `tests/test_salience.py`, `tests/test_memory_touch.py`, `tests/test_memory_index_ranking.py`.
- **Odin headless propose tier (F-10.3 extension):** the headless skill runner (`scripts/heading_cli.py`) gains a narrow `propose` tier that allowlists exactly one invocation shape — `odin reflect --propose` — to run non-interactively, drafting reflect-cluster proposals to a dated file under `outputs/operations/odin-reflect-proposals/` and NEVER writing `knowledge/odin-brain/`. The brain is protected by a path-scoped `Edit(...)` allow, a brain-directory deny, and — independently of the vendor permission layer — a before/after brain snapshot whose any-change branch is a CRITICAL integrity failure that withholds the proposal and escalates to the CEO. Every other Odin mode, and `reflect` without `--propose`, is refused before any vendor call. Design doc: `docs/design/F-10.3-headless-skill-runner.md` (Decision 9). `tests/test_heading_skill.py`, `tests/test_odin_cadence_notify.py`.
- **Bare `heading` command (marketplace follow-up, item 4):** the engine is now an installed uv package (it was a uv *virtual* project), so `heading` is a real console command: `uv run heading list | run <script> | skill <name> | health`. This adds `[build-system]` (hatchling), `[tool.uv] package = true`, `[tool.hatch.build.targets.wheel] packages = ["scripts"]`, `[project.scripts] heading = "scripts.heading_cli:main"`, and a `scripts/__init__.py` package marker; `uv.lock` flips the project source `virtual -> editable`. The conversion makes `uv export` emit the editable self-reference `-e .`, so the two export call sites - the `ci.yml` requirements drift guard and `scripts/audit-deps.py` - now pass `--no-emit-project`, keeping `requirements.txt` a clean dependency list and keeping pip-audit happy (`requirements.txt` regenerated: header-only change). The whole-suite collect (2629 tests) and the guard/deps/repro subsets stay green under the new build config; in-tree `sys.path` imports and the installed `heading` resolve to the same source (editable). `tests/test_heading_cli.py` gains a real entry-point gate (the installed `heading list` console script, not the in-tree import, which passes regardless) plus a build-config assertion.
- **Live draft-tier skill + send-boundary test (marketplace follow-up, item 3):** a new reference skill `/queue-draft` composes a short message and deposits it into the Action Queue as a GATED `email_send` draft card (tier stamped `gated` by `append_cards`, status `pending`), then stops - it never approves, sends, or calls a send transport. It is the first skill allowlisted at the headless runner's `draft` tier (`scripts/heading_cli.py` `SKILL_ALLOWLIST["queue-draft"] = "draft"`), so `heading skill queue-draft` now exercises the draft tier end to end. The skill is `disable-model-invocation: true` + `router: manual` (never auto-fires; explicit `/queue-draft` or explicit headless run only). A new `tests/test_heading_skill.py::test_queue_draft_live_draft_boundary` proves the lethal-trifecta boundary on the real skill: its built `claude -p` command grants `action-queue.py deposit` but excludes `approve` and every `send-email.py` transport from `--allowedTools`, and names all `SEND_DENY` transports under `--disallowedTools`. The `send_capable -> gated` invariant is untouched: the skill cannot lower a card's tier, and `email_send` floors to `gated` regardless.
- **Auto-republish marketplace workflow (marketplace follow-up, item 2):** a new `.github/workflows/publish-marketplace.yml` republishes the plugin marketplace whenever a push to `main` touches a bundle input (`config/plugin-bundles.yaml`, the build/publish scripts, or any `.claude/skills/**` or `.claude/hooks/**`), plus manual `workflow_dispatch`. It checks out the engine and the marketplace repo, sets the commit identity on the marketplace checkout (so `publish-marketplace.py`'s `ensure_identity()` early-returns correctly), and runs the publisher, which is a no-op when nothing changed. Because it pushes to a sibling repo the default `GITHUB_TOKEN` cannot authorize, it authenticates with a fine-grained PAT added as a repo secret named `MARKETPLACE_PUBLISH_TOKEN` (write-only to `heading-os-marketplace`), and fails on its first step with a clear message when that token is absent, never publishing a broken state. Actions are pinned by commit SHA; `permissions: contents: read`; publishes are serialized by a non-cancelling concurrency group. Egress note: this push bypasses `push-all.py`'s content scan, but is safe because the bundles derive solely from already-content-scanned public engine files. `docs/PLUGINS.md` documents the one-time secret setup.
- **Curated capability bundles (marketplace follow-up, item 1):** the four reserved bundles are now populated with a curated, credential-free set, so the marketplace installs more than just `heading-core`. `heading-intel` ships docparse + market-brief; `heading-comms` ships translate; `heading-content` ships linkedin-post + linkedin-series + image-prompt; `heading-ops` ships create-plan + deep-think + editorial-review. The curation criterion (recorded in `config/plugin-bundles.yaml`): a skill ships only if it runs for a stranger with NO CEO credentials, NO running daemon, and NO private data overlay. Skills that fail it stay reserved and are named in the bundle description and `docs/PLUGINS.md`: `email-draft` (references the Exchange send transport), `next` (reads private operational state), and the whole `heading-crm` set (private overlay / Google OAuth). Each bundle's enumerated scripts were verified free of non-utils sibling imports and credentials; `build-plugins.py --all` builds all five green and `claude plugin validate` passes on each. `tests/test_build_plugins.py` gains a curated-bundle composition test and an `--all` marketplace test (asserting the fully-reserved `heading-crm` is skipped, never published). `docs/PLUGINS.md` documents the shipped bundles and what stays engine-only.
- **Plugin marketplace published (F-10.1 follow-through):** the engine is now installable as a Claude Code plugin. The `heading-core` bundle (the `prime`/`state-check`/`checkpoint` skills plus the sovereignty guard hooks) is published to a dedicated public repo, **[mishahanin/heading-os-marketplace](https://github.com/mishahanin/heading-os-marketplace)**, so `/plugin marketplace add mishahanin/heading-os-marketplace` then `/plugin install heading-core@heading-os-marketplace` gets you the sovereignty core with no clone. The marketplace is a generated distribution artifact: a new `scripts/dev/publish-marketplace.py` builds the bundles fresh, syncs them into a checkout of the marketplace repo, refreshes its README/LICENSE/.gitignore, and pushes (the engine monorepo stays the source of truth; the repo is never hand-edited). `build-plugins.py` now excludes `__pycache__`/`.pyc` from bundles so only source ships. A new docs page **[docs/PLUGINS.md](docs/PLUGINS.md)** documents the marketplace, install, updates (bundles omit `version`, so each commit auto-updates), and plugin-vs-clone; QUICKSTART and the README carry install callouts. No private data is bundled: with nothing configured a plugin resolves to the read-only demo tree, and the push-time content scan plus the `send_capable -> gated` invariant remain the backstops.
- **Headless skill runner (F-10.3):** a new `heading skill <name>` verb (in `scripts/heading_cli.py`, next to F-10.1's `heading run <script>`) runs an allowlisted Claude Code skill non-interactively via `claude -p "/<name>"`, built so an unattended run structurally cannot send. The send boundary is allowlist-first: a pure `build_skill_command` grants each tier only the tools it needs (`read-only -> Read`; `draft -> Read, Write, action-queue deposit`, never `approve`), so the outbound transports are unreachable by construction, with a `SEND_DENY` `--disallowedTools` list (send-email, action-queue approve) as defense-in-depth. The run passes `--permission-mode dontAsk`, `--output-format json` (parsed best-effort; the process exit code is authoritative), a `--max-budget-usd 0.50` cap, and `--add-dir <data_root>` so a skill can read the data overlay that the `data-path-redirect` hook rewrites outside cwd. Default-deny allowlist (`state-check` only this slice, read-only); a non-allowlisted name is refused (exit 2) before any vendor call, and an absent `claude` binary degrades clearly (exit 3). Tests (`tests/test_heading_skill.py`) assert the send boundary for both tiers with no real `claude` invocation. Design doc: `docs/design/F-10.3-headless-skill-runner.md`. Scope: no cron wiring and no live draft-capable skill (the draft tier is defined and tested at construction level); both are follow-ups.
- **Plugin packaging walking skeleton (F-10.1):** a `scripts/dev/build-plugins.py` generator assembles installable Claude Code plugin bundles from the monorepo, driven by a new `config/plugin-bundles.yaml` manifest, into a gitignored `dist/marketplace/`. The first bundle, `heading-core` (the `prime`/`state-check`/`checkpoint` skills plus the self-contained sovereignty guard hooks), builds with a generated `plugin.json`, a generated `hooks/hooks.json` that registers the guards and a SessionStart env hook, and a `heading-os-marketplace` marketplace file; `claude plugin validate` accepts it. The monorepo stays the source of truth: the build rewrites `python scripts/...` invocations to the `${CLAUDE_PLUGIN_ROOT}` form in the BUILT `SKILL.md` only (in-repo files are byte-unchanged), a completeness gate fails the build on any unbundled script or hook reference, and bundled scripts resolve their root from the plugin cache via `WORKSPACE_ROOT` (exported by the SessionStart hook) with `paths.py`'s structural fallback as backstop. A thin `heading` CLI (`scripts/heading_cli.py`) dispatches over the same scripts as the vendor-independent Option-C hedge and the invocation surface F-10.3 will reuse. Tests: `tests/test_build_plugins.py`, `tests/test_heading_cli.py` (incl. the both-ways parity and cache-simulation checks). Design doc: `docs/design/F-10.1-plugins-marketplace.md`. Scope of this slice: no publish, no separate marketplace repo, and rules-shipping in plugin form remains a follow-up.
- **Zero-setup devcontainer over demo mode (F-10.2):** a new `.devcontainer/` lets a newcomer open the engine in VS Code "Reopen in Container" or a GitHub Codespace and reach a running read-only skill with no local toolchain, no `.env`, no API key, and no private data repository. `devcontainer.json` defaults `HEADING_OS_DATA` to the bundled `examples/` tree, installs `uv` via its official installer, runs a core `uv sync`, and prints demo output from `scripts/crm-health.py` on first build; demo mode refuses writes by design, so nothing can mutate state. This packages the already-shipped demo path (the `paths.py` data-root fallback, `examples/`, `docs/assets/demo.sh`) without changing any Python. `docs/QUICKSTART.md` gains a "Try it in 60 seconds" section and `docs/DEPLOYMENT.md` a section-4 devcontainer subsection; `.devcontainer/README.md` documents the demo-default and the switch to a real workspace. Design doc: `docs/design/F-10.2-demo-mode-devcontainer.md`.
- **Community front door (F-10.5, part 2-3):** GitHub Discussions is enabled with a welcome post (#52) that maps each category to its use and routes setup questions to Q&A. The issue-template chooser (`.github/ISSUE_TEMPLATE/config.yml`) is repaired: the dead `docs/SETUP-GUIDE.md` link now points at `docs/DEPLOYMENT.md`, and a new "Questions and how-it-works" link routes to Discussions Q&A. `CONTRIBUTING.md` sends questions to Q&A instead of a nonexistent template. The design-doc-first habit is now a stated convention: `docs/design/README.md` records what a design doc is, when to write one, the dual naming (playbook `F-XX-slug.md`, standalone `ADR-NNNN-slug.md`), and an index; `docs/design/adr-template.md` is the fill-in skeleton. Design docs stay markdown-only and out of the site nav by design, so the F-8.1 docs-drift guard does not touch them.
- **Memory lifecycle map + unified memory CLI (F-10.4):** a new `docs/memory-lifecycle.html` page draws the six memory stores (auto-memory, the native harness store, the semantic recall index, the ODIN brain, the knowledge base, threads) as nodes and the exact script or hook that moves data between them as labeled edges with trigger and cadence (`memory-reconcile.py` at SessionStart newest-wins with no deletion propagation, `memory-auto-retire.py` daily on `expires:` records, `retire-memory.py` manual all-store, `memory-index.py build` daily, `promote-knowledge.py`, `/dream`, `memory-hygiene.py` weekly detect-only). To render it on the light-theme site the generator now emits a `mermaid` fence as a `<pre class="mermaid">` block and injects a vendored `docs/assets/mermaid.min.js` per-page only where a fence is present, so every other page stays byte-identical and zero-JS. A new console-first `scripts/memory.py` gives the six operations one namespace (`status`, `recall`, `promote`, `retire`, `reconcile`, `hygiene`) as a pure shell-out facade over the existing scripts with zero behavior change; `reconcile` calls the hook in CLI mode with a resolved native dir, never the bare no-op hook call. Proven by `tests/test_memory_cli.py`; the page is listed in `docs/DOCS-PIPELINE.md` and drift-guarded by F-8.1. Design doc: `docs/design/F-10.4-memory-lifecycle.md`.
- **Docs front door + drift guards (F-8.x):** the committed docs site now mechanically proves `html == render(md)` on every commit. A new CI `guards` step regenerates `docs/` (`scripts/regenerate-docs-html.py --all`: md pairs, nav and search injection, the search index) and fails on `git diff --exit-code docs/`; a matching `docs-html-drift` pre-commit hook catches it before push. A second guard, `scripts/dev/check-readme-numbers.py`, re-derives the security-test count (collecting `tests/security`) and the guard-layer constant and fails if the README or `docs/index.html` "By the numbers" block disagrees (a `readme-numbers` pre-commit hook mirrors it). The 191 KB single-page skills catalog (`docs/skills-mcp-plugins.html`) was mechanically split by `scripts/dev/split-skills-catalog.py` into 8 per-category pages (each under 60 KB, the rich hand-authored cards preserved verbatim, each card's anchor id moved onto its heading so client-side search deep-links to `skills-intel.html#s-osint`), with the old URL rebuilt as the catalog index carrying the preserved MCP servers and Plugins sections; cross-page search is proven by `tests/test_docs_search_index.py`. New `docs/DOCS-PIPELINE.md` documents, per page, md-sourced vs hand-authored, and is linked from CONTRIBUTING and EXTENDING. The README first paragraph now leads with the enforceable guarantee (your data cannot ship with the code) and carries a CI-sourced "By the numbers" block; `docs/index.html` mirrors both.
- **Trigger-coverage gate + backfill (F-6.1):** `scripts/skill-metadata-check.py` now classifies every skill's `triggers.json` router corpus (COVERED / GRANDFATHERED / EXEMPT / MISSING) and exits 1 UNCONDITIONALLY (so the flagless CI "Skill metadata contract" step and the widened `skill-size-budget` pre-commit hook both enforce it) on any MISSING corpus (an auto-routable skill with no valid corpus that is not grandfathered), any thin/malformed present corpus (< 6 cases, < 4 positive, or < 2 negative), or any stale baseline entry (a baselined skill that now has a corpus). "Auto-routable" means `x-heading-routing.router: auto` AND NOT `disable-model-invocation: true`; a `router: manual` or `disable-model-invocation` skill is EXEMPT. Grandfathering is the committed, only-shrinks `config/triggers-coverage-baseline.json`, regenerated shrink-only via `--write-baseline` (it removes now-covered skills, never adds a newly-shipped uncovered one, so a NEW auto-routable skill must ship a corpus). `/skill-creator` refuses to finish a new auto-routable skill without one. Backfilled 42 of the 43 previously-uncovered auto-routable skills with 8-case corpora (5 positive from each skill's triggers, 3 hard negatives naming the neighbor from its `x-heading-routing.exclusions`, illustrative placeholders only), taking coverage from 28 to 70 of 94 skills; the grandfather baseline shrank to a single entry, the vendored, hash-locked `ast-grep` skill (its `skills-lock.json` tree hash forbids adding a workspace-authored `triggers.json`, so it stays grandfathered rather than backfilled). New `tests/test_skill_trigger_coverage.py`; `development-standards.md` corpus contract mechanized.
- **Nightly router-accuracy trend (F-6.2):** `scripts/router-accuracy-nightly.py` runs `skill-trigger-test.py --all --json` once a night and persists a dated raw artifact plus an append-only `trend.jsonl` (`{date, overall_rate, total_passed, total_cases, per_skill}`) under the DATA overlay (`get_datastore_dir()/operations/router-accuracy/`, the F-6.2-designed data-overlay exception), guarded by `require_writable_data_root()` and skipped under `SENSITIVE_MODE` (judge traffic traverses Anthropic). A systemd-user timer installer `scripts/install-router-accuracy-timer.sh` (+ `scripts/templates/systemd/router-accuracy.{service,timer}`, `OnCalendar` 03:00, after eval-drift's 02:00) mirrors the ops-radar installer and carries a `--uninstall` path. A new `router_accuracy` ops-radar signal producer (`classify_router_accuracy` + `router_accuracy_state` in `scripts/utils/ops_signals.py`, registered in `ops-radar.py`) reads the trend against a rolling 7-record baseline and raises a **Tier-B** `warn`/`high` flag when any skill drops > 10 points (point-scaled like eval-drift), so the alert rides the existing ops-radar -> Telegram path with no new channel. `datastore/operations/router-accuracy/` routed private. New `tests/test_router_accuracy_nightly.py`.
- **/implement typed trajectory flags + self-check:** `scripts/implement-trajectory-log.py --event` now accepts typed flags (`--step`, `--title`, `--file`, `--status`, `--wave`, `--successes`, `--check`, `--passed`/`--failed`, and the rest), so each event emits in one `Bash` call with no temp file. A new `--verify --run-id` mode self-checks a trajectory's structural invariants (step and wave pairing, bracketed `successes`, literal `files_affected`, `run_start` first and `run_end` last) and exits non-zero on a defect. New `tests/test_implement_trajectory_log.py`.
- **Operator identity seam (F-4.1):** one place names who runs an instance, so the engine ships operator-agnostic (a fresh clone resolves to a neutral "operator"). New `scripts/utils/operator.py` (`get_operator()` / `operator_slug()` / `operator_org()`) resolves identity with precedence env `HEADING_OS_OPERATOR_*` → data-overlay `operator.yaml` → engine-local `config/operator.yaml` → shipped `scripts/operator.example.yaml`. Every load-bearing identity default (bridge user slug, GitHub org, corporate publisher, the email-reply voice clause, admin-slug fallbacks) routes through the seam via `workspace.operator_identity_default()`. `config/operator.yaml` is routed private and gitignored. Regression guard in `tests/test_operator_seam.py`.

### Changed
- **Dependency modularization: core + optional-dependency extras (F-7.1):** `pyproject.toml`'s ~36-package hard runtime `dependencies` split into a light **core** (13: anthropic, requests, httpx, pyyaml, python-dotenv, markdown, pymdown-extensions, lxml, Pillow, liteparse, watchdog, apscheduler, **numpy**) plus `[project.optional-dependencies]` extras keyed to the names already hard-coded in `optdeps.require(..., extra=)` and the pytest markers (`email`, `telegram`, `browser`, `documents`, `media`, `dashboard`, `ai-extra`) with two new ones (`observability` = langfuse, `research` = firecrawl-py + apify-client) and a self-referencing meta `all`. An adopter runs `uv sync` (small core) and arms a capability on demand with `uv sync --extra <name>` (the exact command `optdeps` already prints on absence); the operator/CI/provisioning path uses `uv sync --all-extras` and is byte-for-byte unchanged for the runtime-imported package set. **numpy** was promoted from an undeclared transitive to an explicit core dep because `scripts/memory-index.py` (recall) imports it directly. Three genuinely-unused declared deps were dropped (`weasyprint`, `replicate`, `xlsxwriter`; `xlsxwriter` survives as a python-pptx transitive). No enforcement scope shrinks: `scripts/audit-deps.py`, the generated `requirements.txt`, and every CI/launcher `uv sync` (`ci.yml` ×4, `dependency-audit.yml`, `setup.py`, `CONTRIBUTING.md`, `docs/EXTENDING.md`) gained `--all-extras`, and a new CI `guards` drift step diffs `requirements.txt` against the byte-identical canonical `uv export --no-hashes --no-dev --all-extras --format requirements-txt`. `scripts/workspace-health.py --section extras` prints an **Armed / Dormant** readiness ladder (distribution-presence via `importlib.metadata`, shadow-immune to `scripts/firecrawl.py`); 13 heavy-dep skills carry `x-heading-requires` frontmatter; 14 capability test files gained `pytest.importorskip` guards so a core-only `uv sync --group dev` collects and runs the suite green with capability tests SKIPPED. `tests/test_import_purity.py` BLOCKED set realigned (weasyprint/replicate leave; xlsxwriter stays as a documents transitive). Core `uv sync --group dev` → 2315 passed / 21 skipped / 0 failed; `--all-extras` → 2541 passed / 0 failed.
- **Router/frontmatter reconcile + trajectory verify guardrail (F-6.1 follow-up):** 12 skills that were `disable-model-invocation: true` yet `router: auto` (backup, calibrate, dream, pre-impl, publish-corporate, push-updates, request-skill, scrutinize, sentinel, setup-wizard, skill-creator, sync) are now `router: manual` with `NEVER auto-trigger`-convention triggers, so the generated skill-router registry no longer presents a harness-blocked skill as model-invocable (per the `development-standards.md` contract that `disable-model-invocation` implies `router: manual`). `pre-impl`'s now-vestigial routing corpus was removed (it is EXEMPT as a manual skill), taking coverage 70 -> 69. `scripts/implement-trajectory-log.py --verify` gained an advisory check: a completed run (`run_end` present) with zero `validation_check` events is flagged, so `/implement` Phase 3 gate outcomes are logged as structured, machine-auditable events rather than only prose notes. Router registry regenerated; `tests/test_implement_trajectory_log.py` extended.
- **SKILL.md size budget (F-5.3):** `scripts/skill-metadata-check.py` now enforces a mechanical size budget on every `SKILL.md` — a hard cap of 500 lines AND 18432 bytes (18 KB) with a 16384-byte (16 KB) warn threshold. The size gate is UNCONDITIONAL: any hard violation exits 1 regardless of `--fail-on-missing`, so the existing flagless CI invocation enforces it with no workflow-file change; a new `skill-size-budget` pre-commit hook runs the same audit on `SKILL.md` edits. The two byte outliers were slimmed under the cap by moving overflow into `references/`: `implement` (20725 → 16467 bytes) relocated its wave-execution mechanics and version history to `references/implement-details.md`; `scrutinize` (21452 → 17915 bytes) relocated its approval-block format + strict semantics to `references/approval-block.md` and tightened several inline sections. Behavior preserved (procedures reachable via pointers; scrutinize Phase 0 eager-loads all references). `development-standards.md` prose mechanized. New `tests/test_skill_metadata_check.py`.
- **Skill router progressive disclosure (F-5.2):** the generated router is split into two layers — a compact always-on core index (Skill + Triggers only) between the sentinel markers in `.claude/rules/skill-router.md`, and full per-category detail tables (Skill \| Triggers \| Exclusions \| Compound) in new `reference/skill-router/<category>.md` files read on demand for disambiguation. `scripts/generate-skill-router.py --split-by-category` (formerly a stub) is now the default write and generates both layers; `--check` verifies both (drift / missing / orphan); `--flat` prints the legacy flat monolith to stdout for the semantics-preservation proof (the union of category rows byte-equals it). The always-on marked region shrinks ~36% with every skill's triggers still in-core. `scripts/skill-trigger-test.py` now concatenates the category files onto its judge context so the routing regression harness still sees the relocated exclusions; the pre-commit `skill-router-sync` filter widens to `reference/skill-router/`. No routing-content change; no schema change; CI command unchanged.
- **Skill router generated from SKILL.md frontmatter (F-5.1):** each skill now owns its router row in its own `SKILL.md` under a new `x-heading-routing` block (category, triggers[], exclusions[], compound, router, optional label), and the seven registry tables in `.claude/rules/skill-router.md` are generated from those blocks between sentinel markers by `scripts/generate-skill-router.py` (`--write` / `--check` / `--flat`). The presence-only `check-skill-router-sync.py` is replaced by `generate-skill-router.py --check` (content idempotency, wired into CI, pre-commit, and the canary smoke set); a skill missing the block fails with the file path and a fix-it snippet. One-shot migration `scripts/dev/extract-router-rows.py` populated the 94 blocks; the initial regeneration is a semantics-neutral reorder (deterministic category, then name). New `tests/test_generate_skill_router.py`. (F-5.0: the rules-loading mechanism is the native Claude Code `.claude/rules/` auto-load, not a hook or import chain — documented so Phase 5 rests on fact.)
- **/implement trajectory emission (v1.6):** the skill drives emission through the typed flags, runs `--verify` after `run_end` (advisory), and consolidates the v1.3-v1.5 wave contract into one statement. `--data-file`/`--data-stdin`/`--data-json` stay as the arbitrary-payload escape hatch; the event schema and the `/scrutinize` lens are unchanged.
- **/implement emission discipline (v1.7):** `scripts/implement-trajectory-log.py` now enforces the sequencing invariant at emit time — a `step_start` opened while another step is open (outside a parallel wave), or a `step_end` for an unopened step, is rejected with a new exit code `5` instead of landing silently. `--verify` gains a run-level files reconciliation: any engine file changed since `run_start.git_head` but recorded in no step's `files_affected` is flagged as an advisory defect (git-degrades gracefully; meaningful only immediately after the run). The `/implement` SKILL (v1.7) now mandates verbatim surfacing of a non-zero `--verify` in the Report Deviations while still never hard-failing a completed run. Event schema and `/scrutinize` lens unchanged.
- **Frontmatter namespace `x-31c-*` → `x-heading-*` (F-4.2):** all 94 skills renamed from the brand-specific namespace to the neutral `x-heading-orchestration` / `x-heading-capability`. Both parsers are dual-key (prefer `x-heading-*`, accept the legacy key with a deprecation notice). New one-shot dev tool `scripts/dev/rename-x31c-namespace.py`.

### Deprecated
- The legacy `x-31c-*` frontmatter key and the operator-identity compatibility shim (`operator_identity_default()` legacy fallbacks) are accepted through a transition window and **removed in v0.5.0**. Write `config/operator.yaml` and re-stamp any skill still on `x-31c-*` before then.

### Fixed
- **`/recall` reinforcement path double-join:** `scripts/memory-touch.py`'s `touch_file()` now accepts both a bare filename and the data-root-relative `auto-memory/<name>.md` form that `/recall` actually passes (from `memory-index.py`'s JSON `path` field), instead of doubling the prefix into a nonexistent `auto-memory/auto-memory/<name>.md` and raising `TouchError`. The whole citation-reinforcement feature was dead through its only documented entry point until this fix; a regression test now covers both forms.
- **Odin brain-tamper integrity failure now reaches the CEO:** a detected `knowledge/odin-brain/` change during a headless `odin reflect --propose` run now calls `telegram_notify.notify(...CRITICAL...)` in addition to logging, so the integrity failure escalates off-machine instead of sitting in a journald line nothing watches. `main()` still returns 0 and `notify()` never raises, so the escalation itself cannot crash the run.
- **Notifications send as plain text:** `telegram_notify` no longer sends system notifications with `parse_mode=Markdown`. A notification line routinely contains an unbalanced Markdown token — a `_` in a file path (`2026-07-17_odin-reflect-proposal.md`), in `access_count`, or in `@headingos_bot` — which made Telegram reject the whole message with HTTP 400. `TelegramBot.send_message` now omits `parse_mode` when it is falsy.
- **Critical daemon alerts no longer default to Saved Messages:** `scripts/utils/alert.py`'s `_telegram_target()` no longer falls back to `"me"` (Telegram Saved Messages, which has no reliable phone push and no Bot API equivalent). It resolves the configured target (or empty) and routes through the notification bot. This was a confirmed live defect — critical alerts were silently landing in Saved Messages because no `daemon.alert.telegram_target` config key was ever set anywhere.

### Security
- **Leak-path matrix (F-6.3):** `tests/security/test_leak_path_matrix.py` attacks every headless-testable engine/data segregation layer on purpose (write-vectors by data-class targets), asserting each leak is blocked by the expected layer with its distinctive message, all inside a sandboxed throwaway repo that never touches the real tree. 31 executable blocker cells; each of the tree-clean guard, leak-guard, content-guard, push wall, and data-root seam is the blocker in `>= 2` cells. The hook-mediated `data-path-redirect` vector is a documented manual drill in the security model. Closes the untested `engine_content_scan` real-entity wall and consolidates coverage that was previously per-layer.
- **Dashboard Host/Origin guard (F-9.2):** a FastAPI middleware on the bridge daemon rejects non-loopback `Host` (421) and cross-origin `Origin` (403), a belt-and-suspenders defense against DNS-rebinding and localhost-CSRF on the unauthenticated surface. `workspace-health.py` gains a `daemon-token` check asserting the bearer-token file is 0600. Proven by `tests/bridge/test_host_origin_guard.py`.
- **Threat model published (F-9.1):** `docs/THREAT-MODEL.md` maps every threat to its control and the exact test or CI guard that proves it, with an honest gap list. Linked from the Security model reference.
- **Vendored-skill hash verifier (F-9.5):** `scripts/verify-skills-lock.py` recomputes the `skills-lock.json` hashes (recipe `sha256-tree-v1`, LF-normalized) and fails on drift; wired into CI guards and pre-commit. `frontend-design` is marked plugin-managed (not vendored in-repo).
- **CI hardening (F-9.3, F-9.6):** the `audit-skill-bash-paths` and `classification-health` audits now run in the guards job; a CycloneDX SBOM is generated on push and tags; an OpenSSF Scorecard workflow runs weekly. detect-secrets baseline drift (F-9.4) confirmed in place.
- **Data-overlay migration framework (F-9.7):** `scripts/migrations/` + `scripts/migrate-data.py` (`--status` / `--apply` / `--stamp` / `--dry-run`); `require_writable_data_root()` refuses writes to an overlay behind the engine schema. Proven by `tests/test_data_migrations.py`.

## [0.3.0] - 2026-07-06

### Added
- **Memory metamemory (Phase 1):** an advisory near-duplicate detector over the auto-memory store, surfaced in the weekly memory-hygiene report as merge candidates. It never enters the hygiene exit-code gate and never auto-applies — a human resolves each candidate via `/dream`. New `scan_redundancy()` in `scripts/utils/memory_health.py`, wired through `scripts/memory-hygiene.py`, tunable via `audit.near_dup_threshold` in `config/memory-index.yaml`. Degrades gracefully when the local embedder is unavailable.
- **Recall-op log:** `scripts/utils/memory_ops_log.py` appends one local-only JSONL record per recall query (from `scripts/memory-index.py query`), redacting the query text under `SENSITIVE_MODE` while keeping the numeric metrics. It accumulates a baseline for deferred recall-quality metrics and never leaves the machine.
- **Both-store memory retirement:** `scripts/retire-memory.py` and `retire_memory()` in `scripts/utils/memory_stores.py`. A memory removed on one store alone is resurrected by the SessionStart reconcile (which never propagates deletions); `retire_memory` clears the canonical store and every native harness store so a delete sticks.

### Changed
- **`/dream` now operates on the canonical data-overlay `auto-memory/`** instead of the per-launch native harness store, and retires superseded or merged files via `scripts/retire-memory.py`. It also applies human-approved merge proposals from the hygiene report and appends a consolidation trace. This fixes a latent issue where `/dream` deletes were silently resurrected at the next session.
- `scripts/memory-index.py`: `cmd_query` now records each recall to the recall-op log (file-only; the stdout JSON contract is unchanged).

### Fixed
- `.gitignore`: ephemeral per-machine runtime directories (`.logs/`, `.state/`, `.data/`) are now ignored, so the local recall-op log can never reach the repo.

## [0.2.0] - 2026-07-05

### Added
- `/pencil-export`: **automatic brand-font embedding** in the editable PPTX. The typefaces used on the slides are embedded into the `.pptx` itself (the PowerPoint "Embed fonts in the file" structures, written directly at the OpenXML package layer — a `fntdata` content-type, one font part and relationship per typeface, and a schema-ordered `<p:embeddedFontLst>` with `embedTrueTypeFonts`), so the deck opens identically on a machine without the fonts installed. Only TTF/OTF embed (PowerPoint cannot use woff/woff2); the layout is never round-tripped through LibreOffice, which would drift it. See `.claude/skills/pencil-export/SKILL.md`.
- `/pencil-export`: a **portable "ready to be shared" flat PPTX**, opt-in via `--formats pptx-flat` (alias `pptx-image`). It is an image-per-slide deck (like the PDF, not editable) that needs no fonts installed and renders identically anywhere, written as `<name> (ready to be shared with the world).pptx`.
- Documentation: a **Rules reference** cataloguing all always-on and path-scoped behavioural rules (`docs/RULES-REFERENCE.md`).
- Documentation: a **Hooks reference** inventorying every `PreToolUse` / `PostToolUse` / `SessionStart` / lifecycle hook (`docs/HOOKS-REFERENCE.md`).
- Documentation: a **Configuration** reference for `config/` (`routing-map.yaml`, `tool-risk.json`, `memory-index.yaml`, `llm_fallback.yaml`, wizard files, schemas) (`docs/CONFIGURATION.md`).
- Documentation: a **Troubleshooting** guide and a **Glossary** (`docs/TROUBLESHOOTING.md`, `docs/GLOSSARY.md`).
- This changelog.

### Changed
- `/pencil-export`: **PPTX now defaults to the editable twin** (`--formats pptx`) — native, editable text boxes laid over a text-less background render, with the brand fonts embedded — instead of an image-per-slide deck. Editability is the point of a PPTX; the frozen image deck is still available via the new `pptx-flat` format, and `editable` is kept as an alias of `pptx`. `pdf` remains image-per-slide.
- Docs site: the sidebar navigation is now generated from a single source of truth for every page, including the hand-authored HTML pages, so nav stays consistent across the site.

### Fixed
- Docs site: `EMERGENCY-PROCEDURES` is now linked in the site navigation (it was generated but orphaned).
- Docs site: the engine/data segregation contract now has a rendered page in the navigation, fixing two broken inline links to it from the architecture and security pages.
- Docs site: the `ROADMAP` is now linked from the README and the site navigation.

## [0.1.0] (2026-07-01)

Initial public release.

### Added
- The **engine / data separation**: a shareable engine repository and a separate private data repository, wired at runtime through a single seam (`get_data_root()`), with the guarantee proven by multiple enforcement layers rather than policy alone.
- The **security model**: the lethal-trifecta control (outbound send is always human-gated), the engine/data enforcement layers, the secret gates, and the progress-watchdog on must-complete steps. See `docs/SECURITY-MODEL.md` and `docs/engine-data-segregation-contract.md`.
- **Skills**: slash-command workflows for research, communications, content, CRM, strategy, and operations, routed from natural language by a single router rule.
- **Rules**: an always-on behavioural layer governing voice, humanization, classification, and the safety controls.
- **Hooks**: `PreToolUse` / `PostToolUse` / `SessionStart` guards that enforce the rules before a write lands.
- **Daemons**: optional always-on background services (a loopback dashboard, mail and calendar sync), driven from the CLI, never required through a browser.
- **Memory and ODIN**: a local associative-memory index behind `/recall` and a persistent knowledge brain.
- The published documentation site at [mishahanin.github.io/heading-os](https://mishahanin.github.io/heading-os/), the deployment guide, and the focused setup guides for models, integrations, and personalization.

[Unreleased]: https://github.com/mishahanin/heading-os/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/mishahanin/heading-os/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/mishahanin/heading-os/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mishahanin/heading-os/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mishahanin/heading-os/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/mishahanin/heading-os/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/mishahanin/heading-os/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mishahanin/heading-os/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mishahanin/heading-os/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mishahanin/heading-os/releases/tag/v0.1.0
