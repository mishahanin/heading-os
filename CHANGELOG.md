# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

### Added

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
  installed files baselined, 453 loaded files scanned, zero injected patterns.
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

[Unreleased]: https://github.com/mishahanin/heading-os/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/mishahanin/heading-os/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mishahanin/heading-os/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mishahanin/heading-os/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/mishahanin/heading-os/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/mishahanin/heading-os/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mishahanin/heading-os/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mishahanin/heading-os/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mishahanin/heading-os/releases/tag/v0.1.0
