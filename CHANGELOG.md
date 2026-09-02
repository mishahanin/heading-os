# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

### Added

- **The Evidence Standard, and four machines that hold it.** The v0.14.0 campaign left a method and no written standard, so the method was derived from its own 144 commits and written into `.claude/rules/development-standards.md`: twelve obligations, each naming the gate that enforces it or stating plainly that none does. A rule that implies enforcement it does not have is the defect it exists to prevent.

  The measurement that made this a set of gates rather than a paragraph of advice: of the 327 `scripts/*.py` files that campaign repaired, only **32** had no test at its start. The tests existed and were green. Stronger still, the campaign wrote 564 test files under maximum attention and rewrote **312** of them inside the same campaign, 184 in a single commit named "a suite that passed over the defects it was written to catch". Discipline is demonstrably not sufficient at this scale.

  Four gates, all blocking, all ratcheted from the current tree, all mutation-verified with zero survivors:

  - `scripts/check-test-vacuity.py` (pre-commit `test-vacuity` + CI) fails on a new test whose every assertion sits inside a loop over a corpus that can come back empty. Calibrated in three narrowing passes over 16,529 test functions: 362 candidates with a naive rule, 147 once a loop over a literal was exempted, **126** once a name bound to a literal was resolved. Those 126 are frozen in `config/test-vacuity-baseline.json` and the writer only ever removes entries. Mutation 13/13, plus one equivalent mutant documented in the test rather than left in the harness.
  - `scripts/check-gate-integrity.py` (pre-commit `gate-integrity` + CI) reads the enforcement layer itself: a `files:` regex matching no tracked path scopes its hook to nothing, and a hook whose script no test names has never been observed refusing. It found two on its first run, `lint-ratchet.py` and `run-integration-tests.py`. Mutation 16/16.
  - `crashed_wall_block` in `.claude/hooks/_dispatch.py` turns a crashed PreToolUse wall from an advisory into a block. Every remaining wall still runs, so an early crash cannot mask a later refusal, and reads plus edits under `.claude/hooks/` stay open so the crash is repairable. Mutation 11/11.
  - `scripts/audit-rotation.py` replaces the campaign with a rotation. Each verdict is recorded against the artifact's **content hash**, so a changed file re-enters the queue by itself and a new one enters at the front; the inventory is derived from `git ls-files` on every run, never stored. An audit that found defects records `open` with a severity and a minute estimate per finding, and `open` does not count as checked. Mutation 16/16 and 11/11 across two passes.

- **`scripts/night-repair.py`** turns an operator-approved batch into an unattended repair pass that leaves the working tree dirty for morning review. It cannot commit or push: its prompt carries no word from the release gate's own authorising lists, imported from `.claude/hooks/_dispatch.py` rather than copied, so a word added there tomorrow is checked tonight. It consumes the batch before starting, and it never writes `fixed` to the ledger, because an agent that repairs and then certifies its own repair is marking its own homework. Mutation 14/14 and 8/8.

- **`config/automation-hold.json`**, a dated freeze both the night pass and the digest read. A corrupt or unparseable hold file HOLDS; only an absent one lapses, because absent carries the operator's intent and corrupt carries none. Nothing is armed: no timer is installed and no unit template exists.

- **`scripts/utils/repo_files.git_index_paths`**, one reader of git's index. Two gates written the same afternoon had each grown a private copy within an hour of the rule against exactly that, and both copies carried the same two defects: a missing `-z`, so git C-quoted any non-ASCII path away, and `text=True`, so a carriage return in a filename arrived as a line feed. Pinned by tests over real repositories holding a newline, a carriage return, a non-UTF-8 byte and a Cyrillic name.

### Changed

- `.claude/hooks/_dispatch.py` renders every refusal through one `_terminate` function, which is the single call site of `_record_denial`. `tests/test_denial_counter.py` requires exactly one, so that a wall added tomorrow is counted by construction rather than by its author remembering.
- `.pre-commit-config.yaml` excludes `config/audit-rotation-ledger.json` from detect-secrets, because its sha256 fields are hex digests by construction. `tests/test_an_exclusion_that_promised_another_scanner.py` proves the promise beside every such exclusion by planting a credential-shaped canary in a copy and requiring `scripts/secret-scanner.py` to fire.

### Fixed

- **The overlay sentinel accused a session that ran nothing.** `tests/conftest.py` snapshots the operator's live overlay at session start and fails the session when anything moved, which is right for a run that executes tests and wrong for one that only collects them. Measured 2026-09-02: the full suite went red on a single test because `scripts/dev/check-readme-numbers.py` derives the security-test count by spawning `pytest tests/security --collect-only`, and an unrelated hook wrote a file into the overlay during the 0.7 seconds that child spent collecting. The child printed "556 tests collected" and exited 1. A collect-only session is now exempt; it runs no test body, so it cannot be the writer, and any import-time write it could have seen is seen again by the ordinary run that imports the same modules. Reproduced end to end with a background writer touching a scratch overlay every 100 ms. Mutation 8/8.
- The same guard reported a collection failure it could not explain: its message carried the tail of stderr, and pytest writes its diagnosis to stdout. It now carries both.

## [0.14.0] - 2026-09-02

The largest release in the project's history, and the only one written from an
audit rather than from a feature. On 23 August an external read of the engine
returned a verdict; the ten days that followed answered it. 144 commits,
1,603 files, +311,337 lines. The suite went from 9,365 tests to 23,893 across
492 to 1,051 test files.

Two shapes account for most of what was found, and neither is a coding mistake
in the ordinary sense. A **guard that reported success over something it never
read**: a gate whose corpus was empty, a scan whose file list failed to
produce, a check whose exit code was fixed before its finding was. And a
**test that was green while measuring nothing**: an assertion satisfied by the
comment explaining the bug it guards, a loop that could run zero times, a
stub that discards the argument it is handed. Both classes pass every review
that reads code for correctness, because the code IS correct; what fails is
the claim the code makes about itself.

Every entry below carries the measurement that established the defect and, where
one was run, the mutation that proves the new test would catch a reversal. The
figures are the commits' own. The audit register that closed this campaign gave
all 708 of its findings a verdict: 670 already fixed by earlier work in the
campaign, 27 not defects, and 11 still live on 2 September, closed that day.

Entries carry their commit hash. Themes were derived from the commit bodies
rather than imposed on them.

### Added

- **An active-threads panel at every session start, computed from the thread
  files.** `.claude/hooks/session-start.py`. Removing the memory index (below)
  took passive awareness with it: the running set became visible only from
  `/prime` or an explicit `/thread`. The panel gives it back without a copy to
  keep in sync. It lists the threads touched in the last 14 days, up to 12 rows,
  newest first, and it names both kinds of omission separately, because "20 more
  active" cannot tell a thread that went quiet for a month from one the row cap
  cut off this morning. A quiet thread never appears: this panel is the
  definition of proactive surfacing, so the rule binds here first. A thread whose
  `last_touched` will not parse sorts to the TOP and prints `(no date)` rather
  than being buried under the cap. An unreadable thread file is counted in the
  header and named below the rows, up to three of them, so the operator knows
  which file to repair rather than only that one is broken; a resolver that
  raises prints one line to stderr instead of an empty panel. Read-only, and
  `python scripts/thread.py list` stays the primary interface. Guard:
  `tests/test_a_panel_that_reads_the_record_not_a_copy.py`.

#### Reports that outran their evidence

- **Roughly 800 datastore files were invisible to `/recall` for lack of a text extraction, and three health and inventory gates could not report a failure at all.**

  Measured on the live overlay on 2026-09-02: 983 files, 593 binary, only 12 extract companions existed, and the extractor covered `.xlsx`/`.pptx` only, leaving 165 PDFs and Word documents with no extractor whatsoever. `scripts/datastore-extract.py` gains PDF and DOCX support (paragraphs and tables), capped at 100,000 characters cut at a boundary and marked at both ends; a run over the live tree produced 349 new companions with zero failures, moving readable-without-extraction from 385/983 to 1090/1349. `scripts/classification-health.py` had `main()` fall through without a `return` on every branch, so a CI step named "Classification health" always exited 0 regardless of findings; against a scratch tree with an unparseable routing map it printed a complete-looking report and passed, because a resolver's fail-closed behaviour is invisible to a report. `scripts/datastore-map.py` replaces a hand-written inventory that had sat in a rules file since 2026-04-20 and had drifted to omit three whole top-level directories, about 150 files; its first version wrote the generated map through a resolver that returns the ENGINE root on the operator's own workspace, which would have published every real datastore directory name into this public repository. The memory index also widens to cover hand-written markdown inside the datastore, exposing 33 files, 13 of them intelligence notes, that no index layer had reached before.

  Full suite: 22931 passed, 1 skipped.

  `7e5ca73`

#### Standing instructions given a mechanism

- **The operator's repeated, explicit instruction to "always start with the graph" (CodeGraph) had no enforcement behind it beyond a 16KB advisory reminder injected on every code-shaped prompt, and it was ignored with that reminder visible on screen every time.** The most recent lapse also produced a wrong number (22 tree sweeps instead of the correct count) from a hand-rolled matcher standing in for the graph. The new hook, `check_graph_first`, refuses the first code-shaped search of a session until one `codegraph_explore` call has been attempted (an error or empty result still counts, so an outage cannot wedge a session). It never fires on repositories with no `.codegraph/` index, on searches over scratch/log/markdown/JSON content, or on payloads carrying no `session_id`. Before shipping, it was found to be a cage rather than a wall: the dispatcher's PreToolUse matchers (`Bash`, `Read|Grep|Glob`, the write family) never covered MCP tool calls, so a real `codegraph_explore` call could never unlock the session it should have unlocked. Fixed with an `mcp__codegraph__.*` matcher in the machine-local settings file, plus a hard cap, `MAX_GRAPH_REFUSALS = 3`, so a broken unlock path costs a few turns rather than freezing a session indefinitely. Mutation-verified 15/15 after closing three survivors, all gaps in the commit's own test fixtures.

  `176be4b`

- **The operator's instruction that agents and workflows are not merely permitted but must be used when they give speed or optimisation had lapsed repeatedly, including once in the hour before this commit, and had no enforceable mechanism behind it.** Unlike the graph-first wall, "did not use an agent" is an absence, not a single wrong action to refuse, so this wall instead watches the shape of a stretch: how many distinct files a session has investigated by hand since it last considered fanning out. Measured on the live hook: 15 reads of 15 different files allowed 12 and refused from the 13th; 40 reads of one file were all allowed (a dependency chain is inherently serial); 30 reads of scratch logs under `.tmp/` were all allowed. Three unlock doors were measured deny-to-allow: an Agent dispatch, a Workflow, and `scripts/fanout-note.py "<reason>"` for a deliberate serial claim, which is logged to `.claude/state/fanout/serial-claims.jsonl` rather than silently accepted (a reason under 15 characters is refused). The cage bug from the graph wall's first ship was caught this time before shipping: the dispatcher's matchers did not cover `Agent`, so a real dispatch could never have cleared the budget; `Agent|Task|Workflow` is now in all four settings files, with the three tracked templates under test. Mutation-verified 21/21 after four survivors were closed, all in the commit's own test fixtures rather than the production rule; 42 new tests, 586 pass across the security suite and both wall suites.

  `87734f1`

#### Injection vocabulary, denylists, and credential handling

- **A full-scope mutation run over `scripts/utils/content_denylist.py` mutated 26 lines and found five survivors, all sitting in the compiled-pattern and token-gate logic the module's own comments explain most carefully but nothing verified.** All five were confirmed against real text before any test was written:

  - **Longest-first ordering:** reversed sort makes `a one-word company token and its two-word form` report the shorter component name in a hit on a sentence naming that company degrading what the operator reads while judging a hit, not whether it fires.
  - **`re.escape` on curated tokens:** without it, `q3 (emea) push` (parentheses read as a regex group) stopped matching its own real text and matched an unrelated string instead, and `a.b@c.com` matched `aXb@cYcom`, both directions wrong at once.
  - **The empty-denylist short-circuit:** without `_pattern = None` on an empty token set, the alternation collapses to `(?:)`, which matches the empty string at every boundary; on "-- end." this fired at positions 0, 1, 2, and 7, meaning any public clone with no overlay would flag punctuation in ordinary lines.
  - **The length gate's exemption:** the five-character minimum is meant to exempt anything containing a space, `@`, `-`, or `.`; without the exemption, a four-character hyphenated slug is dropped from coverage entirely.
  - **The curated allowlist bypass:** without it honouring `ALLOW_IDENTITY`/`ALLOW_FICTIONAL`, a curated entry naming the engine's own public identity turns the wall against the repository, since "31c" appears in tracked engine prose by design.

  Production code is unchanged; every survivor was a coverage gap. Mutation-verified 6/6 (the 5 survivors plus a related loose-pattern escape gap in a second alternation branch). 28 tests pass against an empty overlay and the real one.

  `b3515d2`

### Changed

- **Persistence rule corrected: the constraint is a SERVER database, not file
  formats and not embedded stores.** `.claude/rules/persistence.md` shipped in
  0.13.0 reading "Markdown files and SQLite, nothing else", which was wider than
  the operator's intent in two directions. It refused ordinary data files - a
  JSON index, a JSONL log, a YAML export - and it refused LanceDB, DuckDB and
  Kuzu on the reasoning that "embedded is deliberately not the test". Files may
  now be any format that fits the data, and an in-process store is a normal
  proposal to argue on its merits, because it adds no daemon, no port and no
  service account. Postgres, Oracle, MySQL, Microsoft SQL Server and every other
  server database stay ruled out, and SQLite stays the one this workspace runs.
  Also corrected in `docs/ARCHITECTURE.md` § 4 and `docs/RULES-REFERENCE.md`; the
  0.13.0 release note below keeps its original text with a correction attached.
- **The unattended Stop hook stops reprinting its standing rules.**
  `.claude/hooks/checkpoint-offer.py`. The four-line form was gated on
  "continuation 1 of this window", and `--done` clears the window - so an
  operator who works in short stretches ended with `--done` saw a continuation 1
  at every pause and never once reached the one-line repeat form. The gate is now
  session-scoped (`unattended_rules_shown`, deliberately outside `_WINDOW_KEYS`),
  because what it records is what the ASSISTANT has read and `--done` does not
  remove that from its context. A compaction still restores the full text.
  Guard: `tests/test_unattended_continuation_brevity.py`.
- **`checkpoint-paths.py --done` answers in one line instead of four.** It still
  names both halves of the state - the stretch ended, the switch stays on - and
  drops the session slug and the echo of the note the assistant had just typed.
- **`thread.py list` marks an indefinite freeze instead of printing a None.**
  The suffix was composed from `quiet_until` alone, while `is_quiet` is also true
  for `do_not_remind`, which has no date. Every indefinitely frozen thread listed
  as `[quiet until None]`, which is not the `[quiet until DATE]` suffix `/prime`
  documents and reads. It now prints `[quiet indefinitely]`.

#### Paths, filenames, and tree sweeps

- **Closing out the production side of the tree-sweep audit, fourteen root-anchored walks in `scripts/` and `.claude/hooks/` were checked; thirteen are narrow subtrees a gitignored worktree copy cannot reach (latent at worst), and the one real defect was fixed in `2f42b72`.** The fourteenth, `harness-audit.py`, sweeps 250 files and git ignores exactly one scratch file inside a skill directory; measured 2026-08-29, this tool was deliberately left unfiltered, because it hunts prompt-injection phrasing in the surface the harness actually loads, and the harness itself does not consult `.gitignore` before reading a file. Filtering it would trade real security coverage for a tidier file count. The reasoning is written down at the point the change would otherwise be made, with the rule of thumb stated: a tool that reports a corpus filters by git; a tool that scans for danger does not. No behaviour change; 41 harness-audit tests pass unchanged.

  `6042755`

### Removed

- **`/thread` stops writing the memory index, seven days after `/prime` stopped
  reading it.** 0.13.0 retired the `## Active Threads` block on the READER side
  only: `/prime` moved to `thread.py list --status active`, and
  `scripts/memory-hygiene.py` began reporting the block's own row shape as a
  defect. The writer was left in place, so `/thread` kept regrowing rows that
  nothing read and that the workspace's own hygiene tool then flagged. Seven days
  later it had regrown three rows against thirty-three active threads on disk, so
  the copy was wrong about ninety percent of the set it claimed to index.
  Removed: fifteen names in `scripts/utils/threads_lib.py` (the header
  constants, `SUBSECTIONS`, `QUIET_PREFIX_RE`, `quiet_hook_prefix`,
  `ensure_active_threads_section`, `_index_block`, `_split_at_subheader`,
  `compose_thread_hook`, `add_thread_to_index`, `update_thread_hook`,
  `read_thread_hook`, `read_thread_quiet_marker`, `remove_thread_from_index`),
  the `_memory_md()` resolver and every index call in `scripts/thread.py`, and
  the `reindex` subcommand, which existed only to repair drift between the index
  and the threads it pointed at. Two closed defect classes go with it: a `log` on
  a closed thread resurrecting it, and a `reopen` dropping the quiet marker. The
  freeze now lives in frontmatter only and surfaces through `list`. Guards:
  `test_no_subcommand_writes_a_memory_index` (twelve subcommands, asserted
  against an empty data root), `test_the_retired_index_helper_is_gone`, and an
  AST check that `threads_lib` writes nothing but the thread file.

### Fixed

- **Five tools each reported more than their method established, and one of
  them spent money doing it.** Reproduced by running each before a line changed.
  Guard: `tests/test_five_reports_that_outran_their_evidence.py` (36 tests).
  - `scripts/perplexity-research.py` sent an EMPTY question to a billed
    endpoint. `question` came back `""` from the stdin branch and nothing looked
    again, so a non-tty stdin carrying nothing - cron, a daemon, any
    `< /dev/null` - paid for a request that asked nothing. The tty branch
    refuses correctly; the branch that READS rather than asks had no matching
    refusal. Reproduced with the transport stubbed, so no request was purchased
    to prove it.
  - `scripts/utils/pid_liveness.py`'s Windows branch read another user's process
    as dead. `OpenProcess` returns NULL with `ERROR_ACCESS_DENIED` when the PID
    belongs to a different user or a more-privileged process - exactly the case
    the POSIX branch handles through `PermissionError`, and exactly the defect
    this module's own docstring records for the two copies it replaced. A daemon
    under a service account would have read as dead, `stop` would no-op, and the
    pulse script would start a SECOND daemon beside the first. The branch is now
    a function that takes its ctypes surface as arguments, because `os.name` is
    never `"nt"` on WSL2 and no test could otherwise reach it - which is how it
    kept the defect. It also builds its own `WinDLL(..., use_last_error=True)`:
    `ctypes.windll` leaves the ctypes error slot at a stale zero, so the check
    would have silently read access-denied as an unknown failure.
  - `scripts/watchdog_core.py` counted alerts ATTEMPTED and
    `scripts/daemon-watchdog.py` printed them as "N alert(s) fired". `alert()`
    has always returned `{"telegram": bool, "card": bool, "log": bool}` naming
    what actually went out, and both call sites discarded it - so three alerts
    that reached nothing but a log file read as three alerts fired. The report
    now carries `alerts_undelivered` beside `alerts_fired`, the grid says
    "raised" and names the undelivered count in red, and the bridge daemon logs
    it at WARNING. Recovery notices stay uncounted: `info` is log-only by
    design, and a severity with no other channel cannot fail to use one.
  - `scripts/html-to-pdf.py` printed every one of its four failure paths to
    STDOUT - the same stream `render-doctype.py` reads the generated PDF path
    from. A caller cannot tell an error from a result by channel, which is the
    entire reason two channels exist. Results still go to stdout.
  - `scripts/migrate-data.py --apply --dry-run` never called
    `up(..., dry_run=True)`: the dry-run branch printed a one-line guess and
    `continue`d, so the only call site passed `dry_run=False` unconditionally.
    The contract `scripts/migrations/0001_baseline.py` states - every migration
    MUST honor `dry_run` and "describe, change nothing" - was unreachable, so
    the first real migration would have shipped a branch no code path executes.
    The runner now asks the migration, and a dry run that raises fails the run
    rather than exiting 0 over it.
- **Six checks each admitted the one thing it was written to refuse.** Every one
  was reproduced by running it before a line changed, which matters because the
  workflow panel that surfaced them refuted 0 of 16 findings, and a verifier that
  never refutes is a rubber stamp. Guard:
  `tests/test_six_guards_that_said_yes_to_what_they_refuse.py` (43 tests).
  - `scripts/utils/optdeps.py` counted an EMPTY DIRECTORY anywhere on
    `sys.path` - a PEP-420 namespace package - as an installed dependency.
    `find_spec` returns a real spec for one and `import_module` returns a real
    module, so `available()` answered True and `require()` handed its caller an
    object whose every attribute access raises `AttributeError`: the
    stack-trace-instead-of-a-message outcome the module exists to prevent, with
    the operator never told to run `uv sync --extra <group>`. The first fix
    refused every namespace package and the SUITE refuted it in one run -
    `google` is legitimately one, which is how `google.auth`, `google.oauth2`
    and `google.protobuf` ship as separate distributions into one directory, and
    `gmail_auth.get_service()` calls `require("google", ...)`. Emptiness is the
    test, not the shape: measured in this venv, `google` has 12 entries and the
    phantom directory has 0. `available()` also answers False now instead of
    raising when a parent package is missing, and an unreadable search location
    is never read as empty.
  - `scripts/utils/odin_skill_proposal.py`'s reflection gate was
    `r"matured from\b.*?\breflect"` with `re.DOTALL`, so the two words anywhere
    in a document satisfied it. On the live corpus of 294 principles it matched
    one across 2,280 characters and several sections, where "matured from" is
    mid-sentence prose and `reflect` sits in an earlier heading. The gate decides
    whether a principle may be proposed into a 31C skill checklist, so an
    over-match puts a book abstraction where a lived how-to belongs, which is the
    case the module docstring says it "correctly refuses". Replaced by two
    anchored signals: a LINE that opens with "Matured from", plus the word
    `reflect` in the body. A first attempt bounded it to one line and the corpus
    refuted that - two genuine principles wrap the attribution over a newline -
    so the anchor, not the distance, is the discriminator. 22 passing before, 21
    after; the only file dropped is the false positive.
  - `scripts/utils/docx_font_embed.py`'s `_patch_content_types` replace branch
    used `[^/>]*`, which cannot cross a slash, and every real ContentType value
    carries one. The branch was dead, the insert branch ran in its place, and a
    `[Content_Types].xml` that already declared `ttf` gained a SECOND
    `<Default Extension="ttf">` - which OPC forbids - with the stale ContentType
    left underneath. Reproduced: one in, two out, growing by one on every
    re-embed. The identical defect was fixed in `_build_font_rels` forty lines
    above and not here.
  - `scripts/utils/deep_research_prompts.py` returned an unsourced angle's
    content untouched, so its local `[1]` travelled into a corpus whose own
    prompt states "the ids are GLOBAL" and tells the model to cite the id
    printed. The model read it as global source 1, which belongs to a different
    angle, and attributed a claim to a source that never supported it - exactly
    what the remap was written to end, surviving in the branch that returned
    first. Markers are now stripped, and an angle with no sources says so in
    words instead of rendering a blank Sources line.
  - `scripts/llm-fit-report.py`'s `fetch_traces` returned a bare list from both
    early exits, a failed page and the 50-page cap, warning only on stderr. The
    report is a FILE, so the caveat was not in it: it printed "Window: last N
    days" over a partial walk and every count, percentage and percentile below
    was computed on the fragment. It now returns the reason beside the traces and
    the report leads with an INCOMPLETE FETCH notice.
  - `scripts/compression-candidates.py` joined `--path` onto the ENGINE root,
    but `datastore/` lives in the DATA overlay, so the default invocation and
    every example in the script's own usage block resolved to a directory that
    by design never exists: "Path not found", exit 1. Ten other scripts already
    reach that tree through `get_datastore_dir()`. Two further defects in the
    same `scan()`, fixed because leaving a known one in a file being edited is
    how a second copy outlives the first: `rglob("*.pdf")` is case-SENSITIVE on
    Linux, so `REPORT.PDF` was invisible at any size (one such file exists in
    the live datastore today, under its threshold, which is why it never
    surfaced); and `"_archive" in f.parts` read the ABSOLUTE path, so one
    directory named `_archive` in the tree's ancestry would have excluded every
    file beneath it. The sibling `output-organizer.py` carries a fix-comment for
    that second one already.
- **A newline in any router cell split the generated table row in half, and
  both gates ratified it.** `scripts/generate-skill-router.py` type-checked the
  container and each item but never the cell CONTENT, so a trigger written as a
  folded scalar (`- >`), which is the house style for `x-heading-capability` in
  the same frontmatter, arrived carrying a trailing newline and ended its own
  markdown row - leaving an orphan fragment whose remaining columns describe the
  wrong skill, inside the always-on `.claude/rules/skill-router.md`. The
  corruption is deterministic, so `--check` regenerated the same broken output
  and reported OK. A new `_as_cell()` now guards `triggers`, `exclusions`,
  `compound` AND `label` against a newline, a carriage return, a non-string, and
  an empty value, and reports each as the curated `{file}: {error}` line. A tab
  is deliberately allowed: it renders as whitespace, so refusing it would fail a
  working `SKILL.md` over a symptom no reader can see. Two smaller defects found
  while reproducing this: `label` was never type-checked at all, so `label: 7`
  raised a bare `TypeError` out of `escape_pipes` and `label: no` - the YAML 1.1
  boolean, which is falsy - vanished into the `or f"/{name}"` default with no
  word to its author.
- **The router migration tool parsed 0 of 94 rows and printed a green line over
  it.** `scripts/dev/extract-router-rows.py` read `.claude/rules/skill-router.md`,
  which was correct until F-5.2 split the generator's output into a two-column
  core index there plus four-column detail tables under `reference/skill-router/`.
  Every row then failed the "expected 4 cells" check and was warn-skipped, and
  both exit paths still returned 0 - so a script or CI step reading the exit code
  was told the round trip had been verified. It now derives each file from the
  category name via `_gen.category_slug`, reports a missing detail file instead
  of reading it as a category with no skills, and exits non-zero when it parses
  nothing at all. Two further defects the fix exposed: the parser never removed
  the escape the generator adds, so the `/canopus` trigger round-tripped carrying
  a `\|` that would have been written into a `SKILL.md` that never had one
  (`unescape_pipes`, parity-aware so a data backslash keeps its own escape); and
  a cell holding the separator `, ` is genuinely ambiguous once rendered, so the
  tool now warns when its split disagrees with the authoritative frontmatter
  rather than silently writing an item the author never wrote.
  Guard: `tests/test_a_router_row_that_broke_in_half_and_a_parser_that_read_nothing.py`.
- **The DOCX twin of every corporate document fused its lists, tables and
  headings into one line.** `scripts/utils/doctype_renderer.py`'s `build_docx`
  carried a private four-line `strip_html` that converted `<br>` and `</p><p>`
  to newlines and then deleted every remaining tag with NO separator and no
  entity decoding. Measured 2026-08-27: `<ul><li>Module A</li><li>Module
  B</li></ul>` arrived as `Module AModule B`, a table row as `ab`, and `&amp;`
  as four literal characters. It fed every DOCX body site, so
  `/corporate-letter`, `/proposal`, `/partnership-doc` and `/official-doc` were
  all affected; the PDF was correct because the template gets the HTML raw, so
  nothing the operator saw showed what the counterparty would open in the
  editable copy. Now routed through the shared `html_text.strip_html`, which
  `html_text`'s own docstring asks new callers to import rather than copy,
  followed by `sanitize_text.sanitize` - not optional, because the shared parser
  decodes `&nbsp;` to U+00A0 and `.claude/rules/hidden-chars.md` bans that
  character in every generated artifact. A third copy of the same idea, the
  salutation line stripping `<p>` by name with two `.replace` calls, goes
  through the same helper. Guard:
  `tests/test_a_docx_twin_that_fused_every_list_and_a_font_that_never_loaded.py`
  (24 tests; nothing under `tests/` imported `build_docx` before this).
- **The Cyrillic fallback fonts were never embedded, so every Russian document
  rendered in a system font.** `_resolve_brand_assets` built the Inter paths as
  `_fonts_dir(root) / "Inter"`, and `_fonts_dir` already ends in `GT Standard`.
  The resulting `datastore/brand/fonts/GT Standard/Inter` has never existed.
  `_embed_asset` returned `""` for a missing file without a word, so both faces
  rendered as `src: url("")` and the render exited 0. Measured on the live
  workspace 2026-08-27: GT Standard 246689 and 248853 bytes, both Inter faces 0.
  `base.css` lists Inter first in the `[lang="ru"]` stack and GT Standard has no
  Cyrillic glyphs, so Russian text fell through to Segoe UI or Arial at a heavier
  weight than the Latin column - verbatim the outcome the comment above those two
  lines says the embed exists to prevent. Fixed with an `_inter_dir` resolver
  beside `_fonts_dir`, and a missing brand asset now names itself on stderr
  instead of resolving to silence.
- **Three filename builders erased every Russian title, and one of them
  overwrote a rendered document.** Each cleans a title down to `[a-z0-9-]` and
  uses the result as a filename stem, so a Cyrillic title produced the empty
  string. Verified 2026-08-27: `slugify('Партнёрское предложение') == ''`. In
  `scripts/render-doctype.py` two letters to two Russian-named recipients on one
  day both rendered to `2026-08-27_letter__.pdf`, and the second silently
  replaced the first in PDF, DOCX and HTML. In `scripts/marp_render.py` every
  Russian-titled deck rendered on one day collapsed to `31C--27-Aug-2026`.
  `scripts/utils/threads_lib.py` failed safe (`new_thread_path` raises on an
  empty slug) but still lost information on a mixed title: the live thread
  a title like "Миграция CRM на новый сервер" carried the id `crm`. The operator
  writes in Russian daily, so this was not a hypothetical script. New shared
  module `scripts/utils/slugs.py` adds a Cyrillic transliteration pre-pass and a
  deterministic short-digest fallback for titles in scripts the table does not
  cover. It is a PRE-PASS on purpose: each builder keeps its own ASCII rules, so
  every slug that worked before is byte-identical afterwards, and no existing
  output or thread id moves. Threads deliberately do not get the digest fallback,
  because a thread id is read by a person. No new dependency: the table is forty
  lines. Guard: `tests/test_a_slug_that_erased_every_russian_title.py`. One older
  test had pinned the defect as intent, listing "Привет мир" among the titles
  that must be refused; it now asserts that title opens a thread.
- **Seven readers asked git for paths and got quoted strings back.**
  `core.quotePath` defaults to on, so `git ls-files` and `git diff --name-only`
  C-quote any path holding a byte outside printable ASCII: a Cyrillic filename
  arrives as `"datastore/\320\261.../x.md"`, quotes and octal escapes included.
  Each caller then split the output and used the quoted string as a path.
  `scripts/publish-corporate.py` fed it to `get_routing_destination`, where every
  rule key missed and the map's `engine` default answered. Measured against the
  live data overlay on 2026-08-27: 8294 tracked files, 66 C-quoted; resolved from
  their real names, 65 route `private` and one routes `corporate`. That corporate
  file had never reached an executive, and nothing reported a skip. The `engine`
  default is what made this an omission rather than a leak, which is luck, not a
  control. The same shape sat in `scripts/turn-check.py` (which claims to check
  "the edits made in this turn"), `scripts/skill-trigger-test.py`,
  `scripts/implement-trajectory-log.py`, and three test guards, two of which
  split on whitespace and so also broke any path holding a space. All seven now
  read the NUL-separated form, the way `scripts/push-all.py` already did, and the
  publisher's two enumerators share one reader instead of carrying a copy each.
  `tests/test_a_publisher_that_could_not_see_a_non_ascii_path.py` pins the class:
  it walks the syntax tree of `scripts/`, `tests/` and `.claude/hooks/` and fails
  on any path-listing git call without `-z`.
- **The volatile-money hook guard read 10 lines of a 216-pointer index.**
  `scripts/utils/memory_health.py`. `scan_volatile_hooks` matched a hook with a
  pattern anchored to the bullet, `- [Title](file.md)`, which demands the link
  immediately after the dash. The index is grouped by subject, so a line reads
  `- Memory: [a](a.md) · [b](b.md)`, and the label between the bullet and the
  bracket made the whole line fail. Measured against the live index on
  2026-08-27: 10 lines matched, against 216 pointers present. The guard reported
  "0 volatile hook(s)" and that reading was believed, because a scan of 5% of a
  corpus prints the same words as a scan of all of it. A second defect had the
  same cause: on a line that did match, the signals were read from the whole row
  while the reported `target` was the FIRST pointer, so a price in the fifth hook
  sent the operator to the first hook's file. It now walks the pointers one at a
  time, the way `scripts/utils/memory_expiry.py` already did, attributes the
  group label to the first pointer, and reports the hook rather than the row.
  Guard: `tests/test_a_money_guard_that_read_ten_pointers_of_two_hundred.py`.

#### Guards that reported success over something they never read

- **Two more shards, read by hand once the k3 quota ran out, found seven places where a docstring or a comment promised behavior the code did not deliver.** `crm-health.py --json` documented clean stdout for programmatic use, but on a workspace with no contacts it printed two English lines and exited 0; two callers had already grown private prose-handlers rather than fixing the producer, and a fresh `cold-sweep.py` run still died on a raw `JSONDecodeError`. `herdr_agent._run` was annotated `-> dict` but returned raw `json.loads` output on three of its four call sites, guarded only where `agent list` was called; a HERDR release answering with a bare array on the other three would have raised `AttributeError` past every `HerdrUnavailable` handler. `create-data-repo.py` carried "idempotent, skip if already a repo" comments on branches that were unreachable, because `init-data.py` refuses any non-empty directory, so a failed `gh repo create` on a taken name left an unrecoverable half-scaffolded tree. `council-models.py --get ""` fell through every branch to a `TypeError` instead of the documented exit 2. `council-record-verdict.py` kept a hand-written second copy of its six valid choices inside `render_tally`, so a seventh choice would have been counted in the total and dropped from the breakdown. `council-models-notify.py`'s promised "logged and swallowed, exit 0" could still raise past its own handler from an unwritable outputs directory or a malformed state file. One finding, about the `--public` help text's remote-identity refusal, was checked and found true, then pinned with a test anyway. Full suite green, ruff ratchet at or below baseline.

  `1b8d891`

- **Eight more shards turned up guards that checked the wrong layer of what they claimed to protect.** The artifact listing banned "a symlink out of the tree" by testing the leaf file while `folder.is_dir()` follows links, so a folder-shaped symlink to any readable directory leaked that directory's prose into a summary. Unified search matched only the 50 rows the Studio page displays, making a file edited six days ago unfindable while the result set looked complete. The telemetry summary read its log head-first and stopped at line 20,000; since the file only grows, that discarded every newer event and froze "today" at zero. A one-click undo popped a saved value, renamed it, wrote nothing back, and reported success anyway. The daemon's liveness probe classified an answering-with-500 daemon as absent, with nothing holding a lock across the gap, so it could start twice. A token file's 0600 mode was set only at creation and never re-checked. A touch-log line with a null date raised `TypeError` inside an `except ValueError`, taking down the whole `/pipeline` surface. One finding was refuted with pinned evidence: the pulse refresher does bump its own component on every path, including internal failure; the report had misread a comment as code. A new drift guard now requires every key a source function returns to also appear, in quoted form, in that function's own Returns block. 27 new test files, one mutation harness per shard, 100% caught. Full suite: 10,872 green; ratchet eighteen below baseline.

  `25a994a`

- **Six more shards found a content-leak gate for the public engine repo that warned on stderr about an undecodable file and still exited 0, which is the only signal CI reads.** Making that state refuse immediately surfaced a real instance: `.bin` was missing from `content-guard`'s binary-suffix list, so a committed test fixture had gone unscanned on every sweep since it landed. Alongside it: `census-bench` documented exit 1 as a real benchmark verdict, but a hand-written `"paths": null` reached `set(None)` and raised a `TypeError` absent from the except chain, filing an operator typo as a falsified benchmark; `check-version-sync` matched `## Status` unanchored, so `### Status` satisfied it too; `census.append_answer` checked only the container shape, so a record missing `question_id` crashed after a run that could take 180 seconds; `check-build` read `.get("build", 0)`, so a `BUILD.json` with no `build` key was never flagged as malformed; and `calibrate` read a turn as `(ev.get("message") or {})` with a comment four lines below calling that the "correct idiom" for the exact case it broke. `context-freshness`, `context-floor-audit`, `composite-logo`, and two flag-drop bugs (`--coverage --check`, `--kind`) rounded out the shard. One shard (03-p3) was found already fixed and pinned by 24 tests; recorded, not redone. Full suite: 11,042 green; ratchet eighteen below baseline.

  `86fa5b1`

- **A third defect-class fan-out found four functions whose docstrings promised a soft failure and instead crashed one line past the guarded region.** A search fallback lacked the terminal decode/JSON/OS-error clause its sibling had, so an HTML error page from a proxy escaped as an uncaught `JSONDecodeError`, meaning a fallback search backend was never actually tried. A profile loader, a model-digest builder, and a principles loader each raised past code documented to "never raise" or to make a gate "noisier, never quieter," on inputs as ordinary as a JSON array where an object was expected, or a YAML key present with a null value. Two walls degraded silently instead of refusing: five call sites swallowed `(OSError, JSONDecodeError)` after a file-existence check had already passed, so a corrupt executive roster dropped every excluded name and the content-leak gate printed the engine tree clean; and a corrupted identity file made the corporate-write protection wall return "allow" for a session, silently letting a sync overwrite an executive's edit. A prior test had asserted the opposite of the correct behavior for the first case, arguing a degraded list makes the gate harmlessly inert; that argument held for the advisory CLI and not for the push wall, which now treats overlay-present-and-degraded as a hard refusal. Four writers opened files for truncation without an atomic swap, including the Gmail OAuth token (found because the AST guard meant to cover it targeted a file with zero matching code) and the executive access registry, rewritten in place while emergency-revoke is running. Mutations: 22/22 caught. Suite: 13297 passed, 1 skipped; security test count 409 to 411.

  `e90a77c`

- **A radar signal for pending fleet publications had been structurally incapable of firing since the flags it shelled out with were never defined by the target script's parser.** `ops_signals.publish_state` called `publish-corporate.py --dry-run --json`, flags that parser has never accepted; every run exited 2 on the argparse error, so the "pending" count could never leave zero, and the `/radar` publish-to-fleet row read permanently green. The existing unit test covered only the pure classifier fed an already-computed integer, and cited the never-executed function by name as its well-guarded example. `publish-corporate.py` gained a real `--preview --json` mode and a parser split out of `main()` so its argv could be checked without admin credentials, closing the path that made this untestable before. A separate measurement bug: `_repo_uncommitted` parsed `git status --porcelain` as plain text, but git C-quotes any path containing a space, non-ASCII byte, or backslash, so such paths were silently dropped from the backup-debt scan; fixed with `-z`, which suppresses quoting. Four CLI flags were found doing nothing: a `--profile` flag that was declared, defaulted, and printed back while the actual cookie reader stayed hardcoded to one name; a `--verbose` flag on `marp_render.py` whose subparser default silently overwrote the parent's; and two documented flags (`--repo`, `--import-rater-output`) that no parser had ever defined. A derived guard now checks every `python scripts/<x>.py ... --flag` invocation documented in tracked Markdown/HTML against that script's real `--help` output: 92 invocations, 179 flag pairs. Mutations: 17/17 caught. Suite: 13372 passed.

  `482e884`

- **Four workspace tools tore mid-write and reported success anyway.** Each individual write was atomic; the set of writes around it was not, and nothing downstream could tell a torn state from a finished one.
  - `action-queue.py` sent the mail, then discarded whether the queue recorded it: `apply_status` writes neither the status nor the disposition-log entry on `ok:False`, and an `OSError` from the underlying write (a full disk, a read-only mount) propagates after the message is already gone, so the command printed "sent ... (delivered now)" or died on a traceback over an irreversible send nothing on disk knew about, and the card kept its pre-approval status, risking the same mail being sent twice. Now produces a `sent_unrecorded` result, a durable dead-letter artifact classified `permanent` (forcing re-approval, never a silent retry), and exits 1.
  - `crm_migrate_to_entity_model --apply` wrote its rollback manifest LAST, after both rename loops and the legacy unlinks, though the file's own comment says the manifest is the only thing making `--rollback` symmetric. It is now written first, as an intent record, before the first rename.
  - `offboard-exec` wrote the fixed line "GitHub access revoked, workspace archived, contacts preserved" into the durable offboarding record before `offboard_verdict`, which exists to measure the actual step results, had even run. The verdict is now computed first and the record states what was measured.
  - `transfer-contact` and `merge-contacts` move a record across two repositories; a failed first commit was downgraded to a warning that fell through to the second, so the source repo could commit a removal while the target repo's copy stayed untracked, leaving the contact in neither repository on a fresh clone under "Transfer complete." and exit 0. The removal commit is now conditional on the addition landing.

  Guard: `tests/test_four_changes_that_tore_and_said_they_had_not.py` (32 cases). Mutations 19/19 caught first run.

  `7223568`

- **Six comms readers marked a message consumed before anything had actually read it.**
  - `sentinel.py`'s Telegram cursor advanced past a message the moment it was fetched, before analysis; an outage or any raise between fetch and digest lost the DM permanently, never retried and never notified. The email half of the same cycle had already been fixed for this; Telegram was left behind.
  - The already-notified branch in the same file marked nothing and continued, so a Telegram dialog behind a duplicate message was re-read forever, since Telegram's memory is a per-chat cursor rather than a re-hashable message.
  - `_load_business_context` resolved every configured file against the engine root, but every shipped entry is a data-overlay path, so the `{business_context}` block the system prompt is built around was substituted as an empty string on every run, with a bare `exists()` test reporting nothing wrong.
  - `_format_item_prompt` interpolated the sender, subject, attachment filenames and body straight into a bare `---` fence with no boundary the model could distinguish from the prompt's own structure; every sender-authored field now goes through `sanitize_untrusted` and a labelled frame.
  - `format_untrusted_emails` passed the `to` recipient addresses through verbatim, listed in its own docstring as a trusted field, though on inbound mail the To and Cc lists are sender-written.
  - The dedup key was `source + sender + body[:500]` with no subject and no field separator, so two different messages from one sender whose first 500 body characters matched collided and the second was silently dropped.
  - `inbox_pulse`'s recipient-aware block returned before the keyword-override step could run at all, so `promote_to_critical` could never fire for internal Tribe mail: a colleague writing "PRODUCTION DOWN" straight to the operator classified LOW.

  Two findings were named and deliberately not fixed: the oldest-first email backlog walk skips `processed_ids` while the store is FIFO-trimmed, evicting the oldest ids the walk needs first; and `_check_protected_time` compares only a meeting's start against a protected window while a sibling check tests overlap. Both are named as design decisions for the operator, not patched.

  Mutations 28/28 caught (`.tmp/audit/mut_six_readers_that_consumed.py`).

  `68de851`

- **Eleven panels on the CEO's daily dashboard stated facts their own sources never supported.** `scripts/generate-dashboard.py` is 1,662 lines drawing eleven panels from nine sources, at 237 lines per test reference the thinnest-covered file left in the engine.
  - `collect_calendar` returned the same empty list for "the sync ran and today is clear" and "the sync file is missing," and the dashboard rendered both as the affirmative "No meetings scheduled today," a claim about the CEO's morning generated from a source nothing had read; the email collector had the same shape. Neither collector's own `> Synced:` staleness stamp, present since the initial import, had ever been compared against the clock.
  - `collect_metrics` claimed to extract key business metrics from `current-data.md` but extracted exactly one of fifteen values it returned; the other fourteen were hardcoded fallback literals, so editing or deleting the source file produced byte-identical output while section 07 certified that same file green and "0d ago" underneath. Measured 2026-08-28: every constant still equalled the file that day, meaning the defect was invisible on the day it was found.
  - Two of five heading indicators, Hiring Momentum and Fundraising Progress, were hardcoded literals that never consulted the collectors already computing the underlying data, so they stayed amber regardless of how the real numbers moved.
  - The LinkedIn cadence indicator counted files in `outputs/content/linkedin/`, and `/linkedin-archive` moves a post out of that directory the moment it publishes, so publishing two posts moved the indicator from ON TRACK to BEHIND.
  - A corrupt viraid `state.json` was caught by a bare `except (json.JSONDecodeError, OSError): pass`, leaving `completion_rate` at its initialised 0.0 and rendering a measured-looking "0%" indistinguishable from a genuinely idle week.
  - `build_viraid` tested `active == 0` to report "No Viraid tasks data available," which also discarded a completion rate read correctly from a different file.
  - `HEALTH_BUCKETS` has four values and `result["total"]` counts all four, but `build_radar` drew only three circles, so contacts whose health could not be read, the ones most needing attention, appeared on no circle at all.
  - `stage_prob.get(stage, 0.05)` fell back silently for any deal-stage spelling outside six canonical strings, mis-weighting such a deal by up to 95% while the bar chart drew it in no column at all, though it still counted toward Active Deals and Total Value.
  - The email table was cut at six rows with no count shown, so a truncated list of thirty read as the complete recent set, though `collect_emails` had always parsed the real total out of the file's own `Count:` header.
  - A `--pdf` failure was caught, printed to stderr, and the script printed "Done." and returned 0 regardless, with `main()`'s return value discarded by the entry point anyway, so the exit code was 0 whatever happened inside.

  Mutations 37/37 caught (`.tmp/audit/mut_eleven_panels.py`), no survivors.

  `84efe60`

- **A document-citation renderer showed a page image and a highlight box even when it could not confirm the quote was actually there, or silently showed nothing when the operator followed its own advice.** `docparse report` exists to answer "where does this document say this," using a page image with a highlight drawn over the matched words as the trust mechanism, but `_generate_report_html` had exactly one way to say it could not answer (an ambiguous basename resolving to two documents) and stayed silent on four other failure states that render a card indistinguishable from a good one. The sharpest case: the tool's own ambiguity note tells the operator to cite the full path to resolve it, but the screenshot lookup keyed strictly on basename, so following the tool's own printed advice was the one spelling that matched no screenshot. The second-sharpest case: `find_boxes_for_quote` returning an empty list is the tool establishing that the document does not say the quoted text on that page, the single most valuable thing the report could tell a reader, and it rendered as a missing highlight with no note at all. Fixed by resolving the cited file once up front, keying the screenshot lookup off the resolved document rather than the raw citation string, withholding the page image when the parse data has no matching page, and rendering one note channel that can report more than one degraded state at once, since a second problem hidden behind the first is the same defect again.

  Guard: `tests/test_a_citation_card_that_vouched_for_what_it_could_not_see.py` (28 tests). Mutations 14/14 caught, no survivors.

  `5acbd15`

- **Nine defects across `scripts/docparse.py` and `scripts/generate-newsletter-html.py`, all the same shape: something failed, the tool carried on, and the number or sentence it printed afterwards did not mention it.**

  - `cmd_clear_cache --file` wrapped both the cache read and the unlink in one bare `except (json.JSONDecodeError, OSError): pass`. Measured with an EACCES on unlink: it printed "Removed 0 cache entries" while the entry was still on disk.
  - The `--force` branch printed `Cleared {len(entries)}`, counting entries found rather than removed, and an `OSError` anywhere in the sweep aborted it with no summary at all.
  - `cmd_parse` wrote `total_files: len(results["files"])` with no failure record. Measured on five documents of which two raised: the archived JSON said "3 files" with no trace of the other two.
  - `_setup_check` printed "All prerequisites met" without comparing a version. Measured on the operator's machine 2026-08-28: `LITEPARSE_VERSION` pinned "2.0.0", the installed package was 2.9.0, and the check reported all prerequisites met anyway.
  - `esc()` in the newsletter generator guarded on `if not text`, true for `0`, `0.0`, and `False`, so `esc(0)` returned `""` and a "0 deals closed" figure rendered as a blank.
  - `build_market_depth` and `build_navigation_chart` dropped entire figure blocks or written regions (measured: "apac" and "eu" both vanished from a chart) when an unrelated decoration was absent.

  Pinned by `tests/test_the_failures_a_tool_absorbed.py` (47 tests). Mutation-verified: 23/23 caught after a first run of 20/22, with both survivors real gaps (a version-mismatch message that read "liteparse None installed" instead of naming the packaging problem, plus a same-basename-different-directory cache-clear collision).

  `a8d44ba`

- **`scripts/knowledge-health.py` bounded its scan with a hardcoded list of eight directory names and printed "Notes: N total" as if that were the whole knowledge tree.** Measured 2026-08-28 against the live knowledge root: one real directory held notes and the hardcoded list spelled that directory's name in the plural, so the tool printed "Notes: 1 total" over a root actually holding two notes, absent from the total, the status counts, the orphan set, and the generated index.

  The same file's `scan_shared_notes` carried a four-name subset of the eight, so a note published to any of the other four was counted by neither scan. And `knowledge-health.py` aged a seed date with a bare `except: pass` swallowing bad dates, while the sibling `scripts/odin-brain-health.py` scanning the same root had already fixed both halves. Measured across three date shapes, the two engines disagreed on two of three: a fix that landed in one of two copies. The scan set is now derived from disk (every child directory except the two with their own reader) and `unread_note_files` reports every `.md` the scan did not open, by name.

  Pinned by `tests/test_a_health_engine_that_scanned_a_name_list.py`, the decisive test feeding one `created` value table to both health engines and requiring agreement. Mutation-verified: 25/25 caught, no survivors, plus one mutation removed as a proven equivalent.

  `750e7fd`

- **When the model chain died mid-run, an email-intel run substituted a placeholder analysis for the whole batch, stamped the run `complete`, and committed every fetched message id into the dedupe set, so a conversation that was never analysed was never analysed again.** Measured 2026-08-29 with the vendor chain raising the way an exhausted anthropic-then-gemini-then-grok chain raises: one inbound thread went in, the state file came out `last_run_status: complete`, the id committed, and the next run analysed nothing, with the only trace a "Review manually" card in one terminal. `scripts/sentinel.py` has carried the correct rule (leave unanalysed items unprocessed for the next cycle) since it was written; this was the second copy of the rule and it was missing.

  `_fallback_analysis` now returns a marker distinguishing a placeholder from a real judgement, `commit_payload` prunes only the failed conversations' message ids per-conversation rather than per-run, and a failure is announced on stderr without requiring `--verbose` (the scheduled run never passes that flag, which is why nobody had seen it). Also fixed: `StateManager.__init__` captured `STATE_FILE` as a default argument evaluated once at import, so `monkeypatch.setattr(module, "STATE_FILE", ...)` redirected nothing and an audit run on 2026-08-29 wrote the operator's live overlay, evicting two real entries with no git copy to restore from.

  Pinned by `tests/test_a_digest_that_burned_the_mail_it_never_read.py` (27 tests). Mutation-verified: 13/13 caught, no survivors, on the first run.

  `42f2e1e`

- **Three defects in `run_unread_mode`, the path the bridge tick calls, all let a failed analysis look like a finished one.** `_cache_key` is the set of message ids, which does not change for a quiet unread thread, so once the model failed for a conversation the placeholder `_fallback_analysis` dict came straight back as a cache hit on every later tick. Measured: run 1 with the model dead, run 2 with it healthy, same thread; before the fix, run 2 served `'Quiet thread'` from cache (`analyzed_cached: 1`); after, `'REAL ANALYSIS'` (`analyzed_fresh: 1`). A control (a successful analysis) still cached correctly both before and after.

  `zip(to_analyze, fresh)` had no `strict=`, so a short result list silently dropped its tail: one analysis returned for two conversations left the second on a placeholder while the run still reported `analyzed_fresh: 2` and `complete`. And the run summary printed `{"ok": true, ...}` counting only conversations attempted, so a total model outage printed success; it now carries `status` and `analysis_failures`, with a partial run printing to stderr without requiring `--verbose`.

  Mutation-verified: 16/16 caught, no survivors: five on the cache condition, four on partial-status reporting, three on the truncation detector, four on the run summary.

  `1bc82d6`

- **`scripts/turn-check.py` killed its pytest lane on a wall-clock timeout with nothing judged, but reported that outcome as an ordinary failure carrying a `tests_run` count for work that never happened.** Measured 2026-08-29 by forcing `subprocess.TimeoutExpired`: before, `{"status": "fail", "lane": "tests", "tests_run": 2, ...}`; after, `{"status": "fail", "lane": "tests", "tests_run": 0, "unmeasured": 2, ...}`. The Stop hook rendered the before-case with its ordinary template, telling the operator "a failure here is almost always real," which was false twice over (there was no failure and nothing ran), and the exclusion line said nothing, so unmeasured files read as covered. Both non-completion paths (a timeout and an `OSError` where pytest could not start) are now handled the same way, reporting "did not finish" rather than "failed." Three unrelated contradictions between a stated contract and its code, all measured the same day, were fixed alongside it: `scripts/firecrawl.py`'s header advertised a `--clear-cache` flag that does not exist; `scripts/inbox_pulse/paths.py` carried a second, un-redirectable `get_workspace_root` that answered the real checkout even when `WORKSPACE_ROOT` was overridden; and `scripts/utils/markdown.py::parse_md_table` had a dead, unreachable `return None` beneath an unconditional `return rows`. Mutation-verified 16/16 after closing three survivors, two of which were the fix's own tests rebuilding the hook's template logic in a copy rather than exercising the real code.

  `feaf1f3`

- **A secret scanner reported a directory, a missing file, and a 6 MB file as clean without ever reading any of them, and a style gate crashed or silently passed over zero files.**

  Measured directly: scanning a real AWS key inside a directory at mode 0444 printed "No secrets detected," exit 0; scanning a named file that does not exist did the same; scanning a real AWS key inside a 6 MB file via `--scan-dir` did the same. `os.walk` can list a directory it cannot stat into, `_walk_scannable` dropped that entry, and `os.path.isfile` swallowed the same `EACCES` and answered False, so a refused path read identically to an absent one. The style gate, `ste-check.py --all`/`--skills`, printed nothing and exited 0 under `--quiet` over a scope resolving to zero files, and raised `StopIteration` (exit 1, the code reserved for real findings) under `--json` over the same empty scope.

  19 new cases, 10/10 mutations caught.

  `b276295`

- **Two health-check gates reported success on exactly the broken state they exist to detect.**

  - `install-hooks.py --check` took its green branch on the pre-commit config file existing alone, never opening a hook file; measured in a scratch repository with the config present and the actual hook absent, it exited 0 with verdict "managed by the pre-commit framework," on a clone with no commit gate armed at all, the exact state two rule files name this command as the way to diagnose. It now checks three conditions on the actual hook file: it exists where git says hooks live, it carries the framework's marker, and it is executable. 12 tests, 10 of which fail against the pre-fix source, 14/14 mutations caught.
  - `check-path-references.py --check` went from 2 findings and exit 1 to 0 findings and exit 0 on nothing but changing the routing map's top-level default from `engine` to `private`, because its scope excluded any path whose routing destination was not `engine`, and every recognized prefix that matched no explicit rule fell through to the default. One word emptied the entire scanned corpus. Scope is now derived only from explicit rules, and a corpus that collapses to zero files or zero in-scope references refuses with exit 2 rather than reporting clean. 12 tests, 11/11 mutations caught. The live repository's own verdict is unchanged: exit 0 over 341 files and 1895 in-scope references.

  Full suite: 22982 passed, 1 skipped.

  `a9aedf0`

#### Tests that were green while measuring nothing

- **With the shard backlog exhausted, the audit turned to the `tests/` tree itself and found tests that reported green while checking nothing.** Four tests asserted nothing observable: a port test that never watched the hold, a JSON test whose rescued values were never read back off disk, a prune test checking neither directory nor output, and a nudge test that could not distinguish silence from a send. Eleven of twelve `pytest.skip` calls were defects wearing an environment excuse; skips now stand at one deliberate case. Fixing them surfaced real bugs: two DOCX generators pinned a brand template by a filename that no longer existed, and `marp_render` resolved its workspace against the engine root only, making a data overlay invisible to it. A documented security control ran only 14 of 17 SEC files while double-counting through a star-import aggregator: the published test count read 721; the real count is 405. Six `pytest.raises(Exception)` tests could not distinguish a correct field limit from a limit of 1, and now assert the exact pydantic `(loc, type)` pair at the exact boundary. Nine tests asserted only that a string was absent from subprocess output, a condition a crash also satisfies; `package_skill.py` was found telling six usage lines to run a path that does not exist. An empty `parametrize` is a silent skip to pytest, not a failure, letting a gate report green over zero files; five engine-only collectors gained a measured floor. A real production bug fell out along the way: `sync_calendar` and `create_meeting` in `sync-exchange.py` read module globals only `_ensure_exchangelib()` binds, without calling it, invisible in production but exposed as an order-dependent xdist failure. Suite: 13173 passed, 1 skipped; ratchet 265 against a 271 baseline.

  `6b979bc`

- **Two more shards found that a guard asserting an offender list is empty reports green over zero files, and that the same emptiness collapses one level down inside any loop-only assertion.** Measured on the tree: 38 tests of the whole-corpus-empty shape and 182 with loop-only assertions; after triage, 17 files had no floor at all and were each given a measured count with the date recorded, floored near 55-70% of that number. Two of the 17 filter before asserting, so the floor had to count survivors rather than files on disk, a distinction that mattered concretely in `test_a_tick_that_landed_on_the_wrong_line.py` (14 of 18 units) and `tests/bridge/test_symlink_guards_are_not_dead_code.py` (8 of 19). Separately, `monkeypatch.setattr(mod, "x", v, raising=False)` binds an attribute even when the name is wrong, letting a test exercise the real code it believed it had replaced: 14 such sites were found, 5 dead. `test_every_page_is_attempted_even_after_a_failure` patched three names and then ran its own fake logic in a comprehension, measuring a property of Python rather than of the script under test, and now drives the real script through argv instead. `test_setup_wizard_snapshot.py` wrote its golden file on first run and continued, certifying whatever current behavior happened to be; the write now happens only under `--update`. Each shard driven to 100% caught, 22 and 4 mutations. Suite: 13173 passed, 1 skipped; ratchet 265 against 271.

  `3c0b656`

- **A follow-up pass found the sharper version of the same defect: a guard test that loops, drops each item behind an early `continue`/`return`, and then asserts an offender list is empty, which passes the instant its filter predicate drifts to matching everything.** Measured on the tree: 53 tests of the shape, 31 real after keeping only those whose every assertion claims emptiness, across 21 files. All 31 now count what reached the check and floor that count, with the measured number and date recorded, floor set near 55-70% of measured. Two files needed the opposite fix: where the survivors of a guard are by definition the offenders, the counted set is the pre-classification input, not the empty output. "Caught" by a mutation was not accepted as proof by itself, since a mutation that empties a corpus can be caught by an unrelated `AttributeError` or `FileNotFoundError`; every mutation was required to fail the floored test with the floor's own message, 23/23. A docstring correction: a test claimed twelve installers exist; there are fourteen. Three neighboring classes were swept and refuted rather than fixed, each because the source-side data made the empty-corpus case genuinely unreachable, not because the guard was sound. Suite: 13176 passed, 1 skipped; ratchet 265 against 271.

  `ffee0a9`

- **An eight-lens fan-out over `tests/` found a display-dependent bridge daemon test suite that ran two different code paths on two different machines while reporting the same green result.** `find_linux_terminal()` decides whether to spawn a GUI terminal by reading `DISPLAY`/`WAYLAND_DISPLAY` off the ambient environment; measured with `--cov-branch`, five lines were covered with a display present and uncovered without one, while 1187 tests passed either way. It hid a real bug: a test asserted on the LAST subprocess call, which on a GUI host is never the one under test, so a mutation substituting a stale session ID for a missing one left the whole bridge suite green. Four security controls were found to check nothing: one walked a hook for an except clause naming "Timeout" that the hook no longer had; one had its stderr-logging assertion behind a condition that was already fatal to the build, so it never ran; one matched a substring ("finally:") anywhere in a function rather than parsing where `state.save()` actually sits; and one set a timeout budget to 0, meaning `asyncio.wait_for` raised before any coroutine ran a single step. Four gates asserted only that "Traceback" was absent from subprocess stderr, a check a correct exit-2 refusal, a rejected malformed identity, and a rejected malformed systemd unit all satisfy identically to a real crash. Five further assertions could not fail by construction, including an `assert "1" in rendered` against a report where "1" appears 17 times, and a `gc.collect()` "pinning" a fix against a running asyncio task that no garbage collector can reclaim. Mutations: 24/24 caught. Suite: 13239 passed, 1 skipped; security test count corrected 405 to 409.

  `f8fb63c`

- **A second workflow fan-out found three shapes of `monkeypatch` missing its target and patching a name the code never reads, leaving the intended branch entirely untested.** Three tests patched a module-level constant that a mail refresher does not read (it resolves the path through a function argument instead), so all three ran only the missing-producer branch and never executed the subprocess-success path; coverage of that block went from 33% to 89% once a real producer script was planted and exercised. One test patched a class and then called a helper that runs `importlib.reload`, which re-executes the module's imports and silently restores the real class over the patched stub. A langfuse observability test captured a 3.x API that the code's own docstring says does not exist in the 4.x client actually in use, so its "nothing leaked" assertions ran over data that was never captured in the first place. Fixing that test exposed a health probe that never actually connected: `_ = ews.account` triggers a lazy constructor, not a network round trip, so the probe printed "OK: ... EWS connectable" in 0.25 s against a nonexistent host with zero network activity; only `.account.root` forces the round trip that can fail. Five further assertions sat behind a condition the test's own setup made structurally unreachable, including a "nothing is cut mid-string" test that looped over zero lines because its filler size never crossed the truncation bound. Mutations: 20/20 caught; three earlier mutation attempts were replaced after being shown unable to distinguish the behavior they claimed to test. Suite: 13246 passed, 1 skipped; operator's live handoff archive unchanged.

  `f5567e0`

- **Three tests reached the real internet on every run of the suite, and six numeric caps had only ever been tested one past their limit, never on the limit itself.** Measured by replacing `socket.socket.connect` with a raiser and reading what tried to connect: one test POSTed a fabricated OAuth refresh token to Google's live token endpoint, hidden behind a broad `contextlib.suppress(Exception)` that made the whole suite's outcome depend on the internet and on a third party's endpoint; a config-threshold test opened a socket to a local embedder host seven times while merely reading a YAML file, because loading it also probed DNS resolution; and a guard test's assertion order let it fall through into a live HTTPS request to a public code-hosting site. All three are now refused in-process or have the network call directly patched and asserted against. Separately, six numeric caps (four census-schema caps, one summary limit, two compaction-timing bounds) had only ever been tested at cap+1, so a `>` silently becoming `>=` or an inclusive bound becoming exclusive would have passed every existing test; each now has a boundary-exact case, plus a second test proving the boundary fixtures themselves sit exactly on the boundary. One test's runtime grew with the session because it read the largest transcript file on the machine, which during a long session is that session's own file, still being appended to; measured between 4.10s and 15.58s with no `slow` marker, now capped and marked. Mutations: 12/12 caught.

  `0bd0164`

- **Five tests were found asserting a value their own stub had supplied, never reading the argument the code under test actually passed in, which is the entire behavior the test claimed to cover.** One stub discarded the URL a version-checker built per branch, so dropping the trailing path segment from two real endpoints kept the test green. One patched-out subprocess call was never asserted on its argv, and the same ternary flag it computed had never been driven with both values, so inverting it would have marked a mail conversation READ instead of unread with the undo control silently broken. Another stub discarded which of two LLM consult functions it was handed, so all three real branches shared one test, and pointing the "kimi" branch at an entirely different vendor's script would have shipped sentinel and email-intelligence content there while logging said kimi. A modem SSH test asserted only the stub's own stdout, so collapsing `host = host or default_host` could have written an IMEI to the wrong router with the test unaware. Three further assertions could not fail at all: one iterated a dict of tuples with a check that never unpacked one, so the actual consumer's unpacking style was untested and `/prime` would raise `TypeError` at session start on the real registry; one compared a return value's default against itself; and one asserted `cmd_rollback() == 0 or True`, unconditionally true, under a comment that (incorrectly) claimed the function returns `None`. A fourth was an unsalted-hash tautology: `swap_for_run("r1") == swap_for_run("r1")`, satisfied even by `return False`, which would have seated the same family as skeptic on every run forever. Mutations: 17/17 caught.

  `e9cbda8`

- **Four tests put every assertion inside a loop or an `all()` over a collection that can legitimately be empty, with nothing floored underneath to prove the loop ever ran.** The workspace's only guard on write-side PostToolUse hook registration iterated `.get("PostToolUse", [])`; measured with an empty list in all three shipped per-OS templates, meaning every write hook gone, the test still reported 15 passed. One test filtered a post collection inside the test body itself, so `all([])` is trivially true and tagging every post with only the first configured handle satisfied a test named for per-author tagging. One test looped over the exact constant a batching function also consults, so emptying that constant is the regression AND removes the loop that would have caught it; measured at an empty constant, 43 tests passed while every subtitle run silently took the wrong path (5 cues instead of 37). One test derived its input entirely from the function's own JSON output and checked one direction only, so an empty `--json` report satisfied a test meant to prove the terminal and JSON views show the same picture. Separately, one test inserted a repo path onto `sys.path[0]` inside a `try` whose `finally` restored only an unrelated spy, leaking the path entry to every later test in the same xdist worker and shadowing an SDK import for the rest of the run; the insertion was also unnecessary, since another module already handles it. And one refusal test hand-raised the very exception the code under test was supposed to cause, so the line that actually converts a non-zero exit into that exception went untested and a real exit code 2 would have fallen through to a message asserting a false fact about the exit. Mutations: 9/9 caught.

  `b915594`

- **Five cleanup, shutdown, and outage-warning code paths, the branches that run when something has already gone wrong, had never been executed by any of 13,434 passing tests.** A service-sync script's scp-timeout rollback is supposed to remove the staging tree and keep the live mirror, printing that the mirror was left intact; a one-word variable slip meant it deleted the live mirror instead while printing the reassurance anyway, and no test had ever driven the timeout path to notice. A daemon's liveness check read only whether a PID-file's process was running, with no identity check, so a leaked PID file plus a reused PID would make the daemon refuse to start while status reported it running with a fabricated uptime indefinitely; no test drove that path either. A notification-dedupe save existed only inside a shutdown handler that no test and no real subprocess had ever triggered, so a skipped shutdown would re-notify every email and Telegram item on the next start. An embedder-outage warning was reachable only in combination with a non-zero exit code, an ordering dependency no test had ever supplied together, so the operator could read an outage as an empty memory instead of a flagged one. And a CRM contact-listing function carried its self-snapshot filter in two places, but an autouse test fixture emptied the registry for all 20 tests in its module, so only the second copy was ever exercised, and deleting the first copy's filter left the bridge suite green at 1199 passed. Mutations: 18/18 caught; one mutation initially survived because of a bug in the new test itself, `asyncio.run()` cancels pending tasks on exit, fixed by reading state inside the loop instead of after it. Suite: 13455 passed, 1 skipped.

  `edc1602`

- **Four tests named a specific count in their title and then checked for that number as a bare substring inside a rendered string that already contained the digit for an unrelated reason.** A contract-note test checked `"2" in text`, but the ANSI green-color escape code that opens every passing render itself contains the character `2`, so the assertion held even after the undercount was replaced with `count * 3`, which would have told the operator six contracts were skipped when two were. A backlog-header test checked `"2" in out` over a two-item list whose body is numbered `1.`, `2.`, `3.`, so the count is satisfied by the last enumeration line even when the header's own count is deleted or rendered as `len(ideas) - 1`. A cap-warning test with 107 rows over a cap of 100 checked for `"7"`, a substring of `107`; replacing the correct overflow arithmetic with `len(out)` produced the sentence "107 rows over the cap of 100 were dropped," more rows dropped than existed, and it still passed. A deployment guide's risk-section test checked `"push" in guide and "root" in guide` over 1367 lines where both words occur incidentally many times unrelated to the security claim; deleting the entire explanation of why root access is refused left every incidental match in place. All four now assert the whole rendered phrase or the extracted relevant block, with a zero/under-cap case proving the message is not unconditional. Mutations: 13/13 caught. Suite: 13470 passed, 1 skipped.

  `ba9ec46`

- **Three comparators and one dedup mechanism were proven correct only by a fixture shaped so the answer was already right before the logic under test ran.** A contract gate documented "newest by parsed date" but every calling test created at most one directory per slug, so `max(...)` was never actually exercised; replacing it with `min(...)` or a bare first-element access was green, closed by a new fixture with two directories sharing a slug in both creation orders, plus a case where the date-parsing key and the name-sorting key disagree (an unparseable date prefix sorts last by date and first alphabetically). A skill-output-directory lookup promised most-specific-match-first, but the only fixture listed the deeper match first in source order, so the sort call itself was inert, and its alphabetical tiebreak was equally untested because the one tie-producing fixture compared results as an unordered set. An action-queue dedup mechanism is documented as "the sole dedup authority" and carries two layers, an on-disk index and a within-batch registration; every existing test deposited duplicates as two separate calls, so only the on-disk layer was ever exercised, and deleting the within-batch layer was green, which in production means a single cold-sweep run could emit two drafts for the same contact. Mutations: 8/8 caught. Suite: 13478 passed, 1 skipped.

  `3d1e3b7`

- **Three test fixtures described a shape of data the code they were testing never actually meets in production.** A calibration tool's error extraction dispatched on a top-level transcript event shape Claude Code has never written; measured across every transcript on the machine (one session alone carried 13,503 tool_use blocks and 192 failed tool_result blocks), zero events of the invented shape exist, so the tool's "errors / friction" section, its own reference file calls this the highest-quality category, reported nothing on every real run and was kept green only by a test fixture written in the wrong shape. The parser now reads the actual content-block structure and pairs a failed result with its command by tool_use_id rather than by "last command seen with this name," which had been misattributing errors when two Bash calls occurred in one turn. A CRM truth-set builder read a `name:` field directly off a contact card, but a card migrated to the entity model carries no name field at all, the name lives in a linked address-book entity per the project's own schema, so every migrated card silently fell out of two "who has no CRM card" oracles and reported those people as cardless; all six corpus cards were still the legacy shape, so nothing had ever surfaced the gap. A setup-wizard snapshot test claimed to catch drift in the shipped wizard templates but tested a duplicate copy inside its own fixture tree instead, since the applier resolves against `cwd` and both integration tests ran with `cwd=dest`; the duplicate had already drifted from the real templates, and breaking the shipped personal-info template so its body rendered empty left both tests green. Mutations: 10/10 caught, including the shipped-template break the old snapshot could not see. Suite: 13491 passed, 1 skipped.

  `dc3afaa`

- **`scripts/utils/operator_identity._resolve_file` had seven test files reaching its module and none actually exercising it: every test in `tests/test_operator_seam.py` monkeypatched the function away wholesale.** Measured: changing `return engine_local, True` to `return engine_local, False` left all seven files at 517 passed, in both environments. The report that surfaced this claimed the tier was caught by mutation testing, but only by accident: deleting the tier outright produced 14 unrelated `TypeError`s from unpacking a bare `None` elsewhere, with no assertion about the tier itself ever running.

  Separately, `scripts/utils/sanitize.scan_for_terms` computed a finding's line number by counting newlines and indexed that number into `content.splitlines()`, which also breaks on carriage return, vertical tab, form feed, U+0085, U+2028, and U+2029. Measured: 6 of 7 break characters produced a line number for the correct verdict pointing at the wrong text in the file. The module had zero test references.

  Pinned by `tests/test_a_resolver_that_every_test_patched_away.py` (34 tests), including one that fails if `_resolve_file` is ever monkeypatched in that file again. Mutation-verified: 9/9 caught, no survivors.

  `0d1b5fa`

- **CRM cadence resolution silently returned empty for every contact because it resolved `context/pipeline.md` and `crm/aliases.md` against the engine clone root instead of the data overlay.** `scan_contacts` used a module constant computed from `__file__` at import time, labelled "the canonical workspace root," which is correct only on the retired single-root topology. On the current split topology neither file exists at that path, so both parsers returned `{}` on every run of `/crm`, `/dashboard`, and `/cold-sweep`. Measured on the real 169-contact tree: stages resolved went from 0 to 36 once pointed at the data path (29 stages, 61 aliases parsed there versus 0 from the engine path); cadence changed for 12 contacts (11 Demo/POC from 14 to 7 days, 1 Qualified from 21 to 14 days); health colour changed for 0 contacts today, because 35 of the 36 newly staged contacts sit inside an active radar freeze. The fix moves the fallback onto the existing `workspace_root` seam. A companion static guard, `tests/test_data_root_no_bypass.py`, was blind to this exact join (a ternary behind a leading-underscore variable name); an AST binder was added beside its regex, closing the gap without weakening the existing check (measured over 430 files: 211 found by both, 45 regex-only, 66 AST-only, zero new violations). Mutation-verified 17/17, zero survivors.

  `9bbe204`

- **`test_colliding_basenames_get_distinct_mounts`, the only test on `_mount_names` (which decides where each `/census` corpus directory mounts inside its sandbox), asserted only `len(set(names.values())) == 2`, so any function returning two distinct strings satisfied it.** Measured 2026-08-29: replacing the real derivation with `base = "mount"` or `base = f"mount-{i}"` over `enumerate(paths)` both survived, 0 of 2 caught, meaning a mount table naming every corpus `mount-0`, `mount-1` would have passed the test whose whole subject is mount naming. Widening to both census test files found that dropping the `preferred` argument and falling back to the directory basename also survived, because the one test that passes a `preferred` name built its fixture so the two values agreed and proved nothing; the real case where they disagree is `census.resolve_corpus` mounting scope `threads` at its data-root-relative path `threads/business` rather than the misleading basename `business`, a wrong-answer-with-no-error already recorded in the function's own docstring from a live incident on 2026-08-13. A third gap: `path.resolve().name` weakened to `path.name` also survived, because the only `..` test fixture placed it where `Path.name` already strips it. No production behaviour changed; the derivation rule was correct and untested. Mutation-verified 9/9, zero survivors.

  `46e96b7`

- **Forty-nine `scripts/utils/` tools reported states they had not verified, including two that could open a path around the private-data air gap.**

  - `commit_source._changed_paths` reported nothing withheld over a path inside the vault, because git quotes and C-escapes any path containing a control character regardless of `core.quotePath`; measured, `is_denied` returned False on the quoted form of a crafted path and True on the bare one. Fixed by reading git's `-z` output instead.
  - `air_gap.is_denied` folded case with `.lower()` instead of `.casefold()`; `"perſonal".lower()` is unchanged (U+017F is already lowercase), so `is_denied("perſonal/todo.md")` returned False in the function this tree treats as the single source of truth for what must never be read.
  - `crm.is_radar_frozen` rounded a freeze timestamp down, so a freeze recorded at 18:00 read as expired from midnight, eighteen hours early, the fail-open direction the function's own docstring calls dangerous. `crm._cadence_override` turned a typo'd `cadence: -30` into `red` for every value a date can hold.
  - A logging record factory called `tracing.get()` on every record, so `daemon_heartbeat.beat()`, whose docstring promises it never raises, raised inside its own warning call, failing only in production and nowhere in tests.

  Five test-suite failures also surfaced, none what they first looked like, including `ops-radar.py` appearing to raise `KeyError: 'due'` on roughly one run in six, traced to a test's own dict stub keyed off Python's per-process randomized string hashing rather than to the production code, which was left unchanged.

  Suite: 17987 passed, 1 skipped. Lint ratchet: 252 findings, 43 below baseline.

  `6b12a8e`

- **204 tests across five packages presented a large green suite while none of them could detect the defect they were written for; the pre-fix red count was zero in every single case.**

  - The adversarial prompt-injection scorer checked `if forbidden.lower() in output.lower()` against English descriptions of forbidden acts ("Stop after one word"), which a model never emits verbatim; measured, an empty response, a clean compliant answer, and an output committing every forbidden act all scored "8/8 defended," and no output could ever reach BREACH.
  - A test resolved and wrote to the operator's real memory index under the private data overlay (an UPDATE and a commit), invisible to the isolation snapshot because that path sits on conftest's unwatched list; damage measured afterward at 20,391 rows, access_count sum 79, max 9, on a rebuildable index.
  - A guard asserting no session-id or command injection reached `cmd /k` used a disjunction whose "safe" branch was true under both correct and injected behaviour; measured with the sanitiser removed, an injected command tail still passed.
  - A push-wall test patched a name the real code never reads, so the patch was inert and the test passed only because the checkout happened to be clean at the time it ran.
  - `build_data_repo.py` and `build_engine_repo.py` parsed `git ls-files` by lines under quoting that C-escapes control characters; measured on nine adversarial filenames in a scratch repo, five of nine came back quoted, and a crafted path could resolve `private` in the routing map while its quoted form resolved `engine`.
  - A CI leak-guard step fed `git ls-files | xargs`, which word-splits, so a path containing a space named no file and was silently skipped; measured on two violating files, the old form flagged 1 of 2 and still went green.

  Suite: 18255 passed, 1 skipped. Lint ratchet: 251 findings, 44 below baseline. The security-test count long documented as 487 is corrected to the guard-derived 538.

  `40327f4`

- **A 73-shard mutation audit of every file under `tests/` (929 files, roughly 2,400 mutations) found that a large number of tests could not fail no matter what the code under them did.**

  - Recurring test shapes that could never fail: an assertion satisfied by the comment explaining the bug it guards against; a corpus floor cleared by the union of several sources while one contributed zero files; a guard scored green over an empty corpus; a straw-man negative case refused by its first character before the real clause ever decided anything; a "must not run" test whose raising stub was swallowed by the code's own `except Exception`; and `src.index(needle, anchor) > anchor`, arithmetically incapable of failing, swept and fixed at all 1386 remaining sites.
  - The three push walls that keep private data out of the public engine were never asserted to be CALLED; five one-line deletions could unwire the gate with a green suite and a push reporting success.
  - `leak-guard check-paths` failed open on a degraded routing map: the resolver fails closed to `private`, but the lint step only inspects paths resolving `engine`, so a corrupt map inspected nothing and returned 0 findings.
  - Routing rules matched bare string prefixes, so `crm/address-bookkeeping/` resolved `corporate` instead of `private`.
  - Editing one contact's URL deleted every other URL that contact had.
  - A blank `HEADING_OS_TZ=` made `get_default_tz()` raise in every CLI script while a sibling resolver silently answered UTC for the same input.
  - One non-UTF-8 byte in a comms log made a reader return the whole file as empty, mailing a member who had actually replied as unresponsive.
  - 14 of 18 malformed sentinel config values crashed the invite check into a permanent retry loop.

  The shared mutation harness itself carried four defects (a shared backup name, an unverified restore, a truncating write, a `finally` that does not run on SIGTERM) and had already damaged the data-root seam twice before being fixed.

  `cb07f7a`

#### Tests that reached the operator's live data

- **Two test files launched a real hook subprocess without redirecting its data root, writing genuine session handoffs into the operator's live archive on every test run.** `cwd=tmp_path` does not isolate a child process, because the hook resolves its write location through `get_data_root()`, which reads an environment variable a child inherits. Measured in the operator's overlay on 2026-08-27: 1107 files named for a compact-unknown session and 114 for a probe session, with the shared "latest handoff" pointer that `/next` reads pointing at one of them. Both tests now set the data-root environment variable to a tmp path AND assert the artifact landed there, since asserting only the redirect passes just as well while still writing into the live tree. Redirecting the root exposed a real crash: `session-start.py` died with `ValueError: too many values to unpack (expected 3)` against an overlay with no `context/`, because a function documented to return `(filename, days_old, severity)` tuples returned a bare string on that path, and `main()` unpacked it character by character, meaning every fresh public clone hit a traceback at session start. The unit test for that branch was green because its only assertion, a substring check, is satisfied by a string just as well as a tuple. A new `conftest.py` guard snapshots the live handoff archive at session start and fails the whole test session if it grew by the end, and found the second writer within a minute of being written. Two pytest settings turned on for free: `xfail_strict = true` and `empty_parameter_set_mark = "fail_at_collect"`, the latter closing 528 parametrize sites that would otherwise silently skip over an empty registry or glob. Mutations: 6/6 caught. Suite: 13240 passed, 1 skipped; live archive unchanged across a full run.

  `78ca67e`

- **A mutation-testing run for the memory-index removal truncated the operator's real live memory index from 20,828 bytes to 20, because the CLI tests pinned one data-root variable and not the other.** Residue from commit b123d81 ("an index nothing read and everything wrote"), not carried in the Unreleased entry for that removal. The thread test fixtures pinned `THREADS_ROOT` but not `HEADING_OS_DATA`, so a mutation that put the retired `MEMORY.md` writer back into `cmd_open` let the subprocess under test resolve `get_data_root()` to the operator's real data overlay, and the harness truncated the live auto-memory index in place. Restored exactly, verified pointer by pointer: 217 pointers, 217 files, no dangling link, no orphan. Closed in two places: every thread test fixture now pins `HEADING_OS_DATA` at a tmp directory regardless of whether the code under test reaches the data root, and `tests/conftest.py`'s session guard, which watched one directory and compared file NAMES (a truncation in place adds no file and removes none, so it would have passed the destruction cleanly), now also watches `auto-memory/` and compares file SIZES.

  `b123d81`

- **Two tests passed on a workstation and failed under CI because they silently depended on the operator's own data overlay.** Two `cmd_log_session` tests patched `load_state`/`save_state` and took `tmp_path` as a fixture without using it; once that function moved inside `locked_state` (which resolves `STATE_DIR` for real before loading anything), a workstation with a sibling overlay resolved outside the clone and passed, while CI has no overlay, so `STATE_DIR` fell to `examples/` inside the engine clone and the write funnel refused by operator law before either test reached its own assertion. Fixed by patching `fb.STATE_DIR` to `tmp_path`, the idiom the same file already used elsewhere. Verified by forcing the CI condition on the same clone with `HEADING_OS_DATA=$PWD/examples`: 2 failed before the change, 57 passed and 1 skipped after.

  `7edcdce`

- **A test-suite guard against writing the operator's private data overlay had been widened twice, each time to the directory that had just been damaged, and a third directory was found the same way.** On 2026-08-29 the mutation harness for a separate shard reverted `StateManager.__init__` in `scripts/email-intelligence.py` to its import-time-frozen default and ran the email-intel tests in the main checkout, which inherits the real `HEADING_OS_DATA`; four runs of `main()` rewrote the live `outputs/operations/email-intelligence/state.json`, and the existing guard said nothing because that path was not one of the two it watched. Measured before the fix: of four writes into a fake overlay, only one (the memory index) drew a complaint.

  The snapshot now covers the whole data overlay as a single unit rather than a list of interesting places in it, walking roughly 11,000 files at about 50ms against a 100-second suite, with five rebuildable trees (git's object store, both memory indexes, the CodeGraph index, runtime session credentials) excluded and each reason stated in code. A second half wraps the write primitives directly (`open` in every write mode, `os.replace`, `os.rename`, `os.remove`, `os.unlink`), raising in-process at the point of the write so the traceback names the offending test.

  Pinned by `tests/test_a_test_run_that_could_write_the_operators_data.py` (20 tests). Mutation-verified: 18/18 caught after one real gap on the first run (nothing checked that the primitives were actually wrapped, only that the refusal function itself refused).

  `4b311e1`

- **Following the `StateManager.__init__(path: Path = STATE_FILE)` incident that wrote the operator's live overlay, an AST sweep of 431 tracked Python files under `scripts/` and `.claude/` found 91 defaults that are a bare name or attribute, of which eight resolve to a module-level path constant frozen at import time rather than resolved per call.** All eight are fixed: `scripts/bridge-daemon.py:586`, `scripts/email-intelligence.py:184` (the original incident), `scripts/rule_split_check.py:191` and `:225`, `scripts/sentinel.py:119`, `:200`, and `:2203`, and `.claude/hooks/sync-docs.py:117`. Measured on `scripts/sentinel.py` before the fix: `StateManager()` with no argument resolved the real overlay path even after `monkeypatch.setattr(module, "STATE_FILE", tmp)` had rebound the module global, because the class's `__defaults__` had already frozen the old value.

  Two bugs in the scanner itself were found while auditing and each had hidden a real hit: an alias-chain resolver dropped `RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"`, and a root-walker stopped at `Attribute`/`Subscript` nodes, hiding a `Path(__file__).resolve().parent.parent` construction.

  Pinned by `tests/test_defaults_that_froze_a_path_at_import.py`, with an empty allow-list (all eight fixed, none exempted) and a repository-wide AST rule preventing a ninth from landing. Mutation-verified: 17/17 caught, no survivors: eight re-freeze the original defect one site at a time, nine weaken the rule or its staleness checker.

  `8f94769`

- **A test posted to the bridge daemon's `/return` endpoint, which calls `webbrowser.open`, once per entry in the frontend's route table with no patch, opening eighteen real windows on the operator's desktop per full suite run.** `test_return_opens_every_renderable_page` was added without checking that every other `/return` test in `tests/bridge/test_endpoints.py` already wrapped the call in `patch("webbrowser.open")`. Nothing in the suite could detect the difference: eighteen passing assertions and eighteen opened windows look identical from inside pytest. A new guard, `tests/bridge/test_no_test_opens_a_real_browser.py`, parses every test module for a `/return` post and requires an enclosing `webbrowser.open` patch; it found three more unpatched call sites, all refusal tests that return 401 or 422 before reaching the browser, and strengthened them with an explicit `opened.assert_not_called()`. The detector is pinned against four guarded shapes, the one unguarded shape, and an anti-vacuity floor on how many `/return` call sites it reaches.

  `ddb77b8`

- **Seven tests across two modules reached the operator's live data overlay and were refused by the suite's own write guard, because `scripts/fireside-bot.py` resolves `STATE_DIR` at import time and each module's redirect fixture had been applied to only some of its tests.** In one module the redirect was written for two fixtures further down the file; in the other it was opt-in and the failing tests never requested it. The failures were also disguised: a write path's own `except Exception` swallowed the guard's refusal, and the error logger then tried the live `errors.log` and raised out of the handler, so the traceback named the second write rather than the first. Sweeping all 19 test modules that reach the bot found the shape at 5 sites, not 2: three more modules carried a redirect that 42 further tests never picked up, one error path away from writing the operator's tree. All five are now autouse. The guard itself is a pure predicate, unit-tested on synthetic modules in both directions. Verified by three deliberate breaks: removing the guard's collector turned 5 of its own tests red; breaking the pinned bot behaviour failed exactly the 7 named tests; downgrading one autouse fixture back to opt-in named all 4 newly-uncovered tests by node id.

  `89cefa3`

- **Three test cases in `TestWorkspaceTransform` looked for a real `context/` or `outputs/intel/` directory and operated on whatever they found there, which on the split topology is the operator's private data overlay, and two of the three were refused by the conftest write guard while trying to write into it.** On any machine without the overlay, both cases silently skipped instead, so the two affected rows of `WORKSPACE_DEFAULTS` were measured nowhere: not on CI, not on a fresh clone, and on the operator's own machine only as a refusal. A third case seeded itself directly into `WORKSPACE_ROOT / "context"` whenever that directory existed, silent in the engine clone but live-data-writing on the transitional single-root topology. All three now seed a throwaway overlay under `tmp_path` with `get_data_root` redirected at it. The mode assertions were also strengthened to derive their expected value from `WORKSPACE_DEFAULTS` rather than restating "light"/"dark" as literals. Measured 2026-08-29: 11 passed / 2 failed became 13 passed / 0 skipped, with and without a real overlay present. Mutation-verified 4/4.

  `b05b622`

- **Three cases in the content-leak gate's own test file ran the gate CLI with no `--data-root`, so the denylist scanned against came from the operator's live private overlay; all three failed once pointed at an empty overlay, meaning they were only ever green on one machine and green there for reading real personal records.** The skip behaviour that occurs with no denylist present is correct (there is nothing to detect, and refusing would break every public clone), so the fix lands in the tests, not the gate. A second, independent defect in the same three cases: each wrote its probe file into the real repository root and removed it in a `finally`, so a crash between the two steps would leave litter that the `engine-tree-clean` check then refuses the next commit over. Both are closed with a hermetic sandbox fixture building a synthetic engine and overlay with an invented denylist entity; the fixture needed a copy of `config/routing-map.yaml`, since an absent map fails closed to `private` and would select zero files, leaving every case green over an empty list undetected without a direct assertion that the sandbox saw 1 file and 1 denylist token. A missing mirror test, asserting a real entity is still blocked, was added. Measured: 30 passed against an empty overlay and against the real one; runtime dropped from 58.44s to 1.37s.

  `247aeef`

- **The overlay write guard could be disarmed by the exact environment variable every isolation fix tells you to export, and it never covered directory creation at all.**

  The guard aimed itself with `get_data_root()`, which honours `HEADING_OS_DATA`; exporting that variable, the standard remedy for test isolation, pointed both the guard and the write at the same scratch directory, leaving the operator's real overlay watching 0 files instead of 10,919. It now derives its root structurally from its own file location, which no environment variable reaches. Separately, the guard wrapped `open`, `replace`, and `remove` but not `mkdir`, `makedirs`, `rmdir`, or `os.open`, so a test reaching `Path.touch()` (which calls `os.open` directly, never `builtins.open`) wrote a real private directory in silence, with no trace in `git status` either. Also found: three real-entity leaks already public in this repository, located by eye because the leak scanner is name-list-driven and cannot see an unlisted entity, two operator session IDs committed as test fixtures since 2026-08-19, a third party's name and social handle in `pptx-generator`, and an acquired company named in `data-room`; session IDs now get a gate that reads the forbidden set off the machine itself rather than from a hand-maintained list.

  Suite: 19305 passed, 0 failed.

  `dd0c7b8`

- **The overlay write guard only armed itself when running under pytest, so a plain interpreter run wrote over a real operator file.**

  The guard lived in `tests/conftest.py`, so a scratch probe run as a plain `.venv/bin/python` calling an entry point blind had `openpyxl` save over a real operator workbook with nothing to refuse it. Extracted to `scripts/utils/overlay_write_guard.py` with explicit `arm()`/`disarm()`, and `conftest.py` now only re-exports glue rather than the moved names, so a stale monkeypatch raises instead of silently binding an unused copy. Separately, 108 module-level names across 38 files resolved a data path at import time and froze that answer for the life of the process, because the pin rule checked only whether a resolver was called at module scope rather than whether a module-level value was frozen, recognising only one of three shapes the defect takes; the rule is rewritten to derive both, tree-wide.

  `cb2cdd8`

- **A repository-wide sweep raced two other tests' own temporary scratch files and blocked a push on a tree where nothing was wrong.**

  `_corpus()` in `tests/test_a_wall_that_answered_about_the_spelling.py` listed tracked paths and read them in a hand-rolled loop instead of gathering first and reading after; under several concurrent pytest workers and agents against one checkout, two other tests write a temporary `.py` file into `tests/` and remove it moments later without gitignoring it, so it appears in a tracked walk and can vanish before it is read. Measured 2026-09-01: this raised `FileNotFoundError` on a timezone-probe scratch file during a full `-n auto` run and blocked the push. The shared `read_sources` helper, written for this exact race on 2026-08-30 after it killed a different test the same way, had never been adopted by this caller; it now is. Its prior `UnicodeDecodeError` handling was deliberately not carried over: a vanished file is a race to be skipped with a warning, while a decode failure is a real fault about a file that genuinely exists and still raises.

  `f3c951e`

#### A fix that landed in one of N copies, and the modules that ended the copying

- **Fifteen shard reports from a Kimi k3 audit of the engine tree turned up duplicated bugs living in as many as eleven copies of the same hand-written logic.** The bridge subsystem alone had three shapes repeated across its sources: six modules spelled an append-only JSONL log as read-the-whole-file-then-rewrite (`_jsonl`, eleven copies collapsed into one), nine of ten path readers under `sources/` tested `is_symlink` on an already-resolved path so the symlink ban was dead code in nine of them (`_safepath`), and every reader let `JSONDecodeError` stand in for "wrong shape," which does not catch a bare `[]` or a null timestamp in valid JSON (`_shapes`). Four more shared modules in `scripts/utils/` retired duplicated bugs: two private `rmtree` copies passed `onexc=`, a Python 3.12-only kwarg, against a project pinned to `>=3.11`; a scratch mutation harness with no timeout let one mutation turn a paging loop endless and hit 47 GB before the OOM killer took the session with it on 2026-08-24 (`mutation_harness`); two copies of PID-liveness checking shared the same mistake; and the eight note types were maintained twice, once as promoter choices and once as new-workspace directories. Separately, the chronicle's high-water mark compared a session file's local-clock mtime against UTC-written marker days, excluding same-day sessions in positive-offset zones; a census air-gap refusal returned before writing its answer row, breaking the module's own "every refusal recorded" contract; and several report paths used `x or default`, which silently drops a legitimate `0.0` or `""` reading. Verification: 9365 passed, 12 skipped, 0 failed; lint ratchet 16 findings below baseline; every mutation harness at 100% caught.

  `76c63fd`

- **A CRM backup helper silently destroyed its own backup on a second merge, and a truncated pipeline read let a live deal vanish from the CEO's digest.** `merge-contacts.py` moved the source record aside to a single fixed name, `.md.merged`; `Path.rename` on POSIX silently replaces its destination, so merging the same contact twice destroyed the first backup while still printing "Source backed up:". The sibling `transfer-contact.py` received this exact fix in July, comment included, and the same four lines in `merge-contacts.py` never got it; a workflow verifier reproduced it by running `main()` twice outside the repo. The logic now lives once, in `scripts/utils/crm.stamped_backup_path`, called by both tools.
  - `email-intelligence.load_pipeline_context` capped `context/pipeline.md` at 80 lines. `enrich_conversation` scans that string for a contact's company and writes `pipeline_context = None` on no match, so a deal at line 81 or later was indistinguishable from a company with no deal at all, and the CEO's approved digest said, by omission, that no live deal was attached to the thread. The cap bought nothing for its own stated consumer either, since `analyze_conversations` already applies its own `pipeline_text[:1500]` cap.
  - `sentinel._build_evening_digest` printed the first five medium-priority items in arrival order under a heading that made no top-N claim and never stated the band's size. Now sorted by urgency, with the count and the bound named in the heading.
  - `crm-backfill-exchange` called `.date()` on a UTC-aware `EWSDateTime`, filing a mail sent at 01:30 local under yesterday's date directly into `last_touch:`, which the CRM staleness stack then compares as a plain string against the stored value.

  Guard: `tests/test_four_bounds_that_nobody_was_told_about.py` (27 cases). Mutations 15/15 caught after two survivors traced to gaps in the new tests themselves.

  `55e1443`

- **The same OOXML child-ordering rule was fixed twice in one document generator and never reached the five siblings that made the identical mistake.** `w:pPr`, `w:tcPr` and `w:tblPr` each have a fixed child sequence in ECMA-376; a child appended in the wrong position is schema-invalid, and the consuming reader is free to drop it, so a border the code just wrote may never appear. `scripts/generate-odunone-docx.py` found and fixed this shape twice (`add_bullet` and `add_table`), naming the symptom in a comment both times, but the rule itself never left that file. Measured against the installed python-docx 1.2.0: six raw property appends existed across five other generators, and five of the six emitted their properties out of sequence, including `scripts/generate-client-docx.py` (sixteen section rules out of order, plus a cover table where three of six rows carried invalid cell properties depending on nothing but which of two loops ran first) and `scripts/md-to-docx-proposal.py` (every table in the national-programme proposal emitted its borders after `w:tblLook`). The fix is one shared funnel, `insert_in_order` in `scripts/utils/docx_helpers.py`, with four successor tuples copied from ECMA-376, adopted by all seven sites including the one hand-rolled copy that had already been correct.

  No artifact here shows Word actually dropping a border; what is measured is that the generated XML violates the ECMA-376 child sequence, leaving the outcome up to the consuming application rather than guaranteed.

  Guard: `test_no_generator_appends_onto_a_property_container`, plus regenerated golden fixtures proven to be pure reorderings (identical tag counts before and after). Mutations 22/22 caught after one honest re-run surfaced a mutation the harness had silently skipped over an ambiguous line match.

  `1114a90`

- **Three council model CLIs share one transport contract, and the fix for each defect landed in only one or two of the three.** `kimi-consult.py`, `gemini-consult.py` and `grok-consult.py` each document the same exit-code contract and `/council` dispatches them interchangeably.
  - The shared transport's `_attempt` classified eight SDK exception types into a clean `RuntimeError`, then read `resp.choices[0].message.content` below that classification, so any response shape it did not expect (a delta-shaped choice, a missing `finish_reason`) raised an unclassified `AttributeError` past every branch. Reproduced against a stub client returning three malformed shapes.
  - `gemini-consult.py` and `kimi-consult.py` both catch an unwrapped exception and exit 3 with a classified message; `grok-consult.py` caught only `RuntimeError`, so an unhandled `KeyError` exited 1 with a raw Python traceback carrying absolute workspace paths into the council transcript.
  - The transport's truncation-retry remediation names a `--timeout` flag that only `kimi-consult.py` actually defines; `gemini-consult.py` and `grok-consult.py` exit 2 on `unrecognized arguments` if an operator follows the printed advice for those models.
  - A guard meant to catch a second module quietly growing its own proxy client matched only the literal address `127.0.0.1:8317`; the one second client that exists, `scripts/census-submodel-bench.py`, writes `http://localhost:8317/...` and so passed the guard it was written to catch.
  - Two existing tests measured less than their names claimed: every cascade test in `tests/test_llm_fallback.py` stubbed the vendor call with a lambda that discarded the prompt, so a cascade sending a blank prompt to any fallback model would have passed; a "reasoning effort passed through" test asserted a value after monkeypatching the very function that does the forwarding, measuring argparse instead of the hop it claimed to pin.

  No model was called in the course of this fix; every test runs against stub clients. Mutations 19/19 caught after one survivor showed a substring assertion matching a wrapped error message that still contained the original words.

  `77f0d27`

- **Five different word-count implementations across the engine disagreed by as much as 55% on the same sentence, and one of them counted a 17-kilobyte inlined stylesheet as prose.** Measured on one ordinary sentence: `sanitize-text.py` counted 11 words, `ste-check.py` 12, `run-skill-eval.py` 15, `generate-newsletter-html.py` 15, `humanization-check.py` 17. `.claude/rules/hidden-chars.md` requires the validation line's word count to "come from the tool," but the tool it named did not agree with the tool beside it.
  - `generate-newsletter-html.py`'s `count_words` stripped HTML tags with a regex that removes the `<style>` tag but leaves its body, so `build_css()`'s 17,424 characters of inlined CSS were counted as prose. Measured: a newsletter carrying three words of actual prose reported "Word count: ~1961," with the stylesheet contributing 1958 of them. Now routed through the shared `scripts.utils.html_text.strip_html`.
  - `run-skill-eval.py` used `len(output.split())` as a length-floor gate, under which a bare `-` bullet, a `|` table rule, and a `---` separator each counted as one word, so a floor could be cleared on punctuation alone.
  - `humanization-check.py` and `ste-check.py` were deliberately left on their own word definitions, since every threshold in each tool is calibrated against it; instead, `humanization-check.py`'s printed line was renamed from "Word count:" (the exact wording `hidden-chars.md` owns) to "Rhythm words:," naming `sanitize-text.py --scan` as the source of a deliverable's actual word count.

  The canonical definition moved from a private function inside a kebab-case CLI, which no Python module can import, to `scripts/utils/sanitize_text.py`, alongside the sanitizer functions that CLI already imports.

  Guard: `tests/test_one_question_five_counters_five_answers.py` (29 tests). Mutations 11/11 caught.

  `7177305`

- **`scripts/utils/venv_guard.ensure_venv()` re-executed the running script under the project `.venv` by reading `sys.argv[0]`, which is not always a script.** Under `python -c "<code>"`, the REPL, or `python -`, `sys.argv[0]` is `"-c"`, `""`, or `"-"`, none of which is a file, so the guard re-exec'd a path that could not be opened and exited 2 having discarded the payload it existed to protect.

  Measured 2026-08-28 against a git worktree, before the fix: 25 failed, 14372 passed, 89 skipped, of which 24 failures were `tests/test_import_purity.py` reporting a false "not import-pure" diagnosis on code that was already pure. GitHub CI never saw this because it runs from the repo root under its own `.venv`, where the identity check short-circuits before the argv line is reached. After the fix: 14479 passed, 89 skipped. The guard now refuses to exec a path that is not a file and prints one stderr line naming the running interpreter, the intended one, and the non-script argv[0].

  Pinned by `tests/test_a_relaunch_that_had_no_script_to_relaunch.py` (18 tests). Mutation-verified: 10 mutations, one survivor on the first run (V2 dropped `""` and `"-"` from the refusal set, missing a reachable case where a file literally named `-` gets re-exec'd instead of stdin); fixed to 10/10.

  `0062448`

- **`.env` is read and written in six places in the codebase, each with its own hand-rolled grammar, and the six disagreed on identical input.** Measured 2026-08-28 on the same lines across `load_env`, `load_gh_token`, and `load_env_key`: a dotenv-quoted key returned quoted by two readers and unquoted by the third; a leading space before `KEY=v` made the value invisible to `load_gh_token` while `load_env` read it fine; `export KEY=v` set an environment variable literally named `"export KEY"` and left `KEY` unset in all readers.

  The writers had the same defect from the other side: a writer that cannot recognize the line a reader recognizes appends a duplicate instead of replacing it. Measured on an indented `  HEALTHCHECKS_API_KEY=OLD`: the writer's `^KEY=.*$` matched nothing, appended `KEY=NEW`, and afterward `load_env`'s `setdefault` semantics meant the daemons kept reading OLD while the provisioner reported NEW written. Everything now parses through one shared `parse_env_line` in `scripts/utils/paths.py`.

  Pinned by `tests/test_one_file_six_parsers.py` (95 tests), the decisive one feeding identical bytes to every reader and requiring agreement. Mutation-verified: 17/17 caught after a first run of 15/19; one real gap found a differential run over 6663 generated lines put a `KEY =value` (space before `=`) shape at 1012 occurrences, unparsed until fixed.

  `3daee29`

- **Three tools split markdown frontmatter by finding the characters `---` instead of the fence line, and a fix for that landed in some copies and not others.** Measured 2026-08-28 on `description: drift --- check` inside an otherwise ordinary SKILL.md: `generate-skill-router.py` read the whole mapping correctly, while `skill-metadata-check.py` dropped every key after the dashes and `artifact-evaluator.py` cut the block there. End to end through `check_skill`, one ` --- ` in a description turned the audit's verdict from WARN to FAIL, named three metadata fields as missing that were present, and flipped `triggers_status` from MISSING to EXEMPT so the coverage gate silently stopped asking for a required triggers.json.

  The two gates agreed on all 94 committed SKILL.md files at the time, so this was a latent defect with a measured trigger, not a live wrong answer. `scripts/utils/markdown.py` gains `split_frontmatter` and `parse_frontmatter_strict`, shared by all three callers while each keeps its own error wording.

  Pinned by `tests/test_three_gates_that_read_one_file_three_ways.py` (44 tests), the decisive one feeding one document table to all three gates. Mutation-verified: 17/17 caught, no survivors, on the first run.

  `a983d8c`

- **`scripts/email-intelligence.py::load_crm_contacts` parsed CRM cards' frontmatter directly instead of going through `scripts/utils/crm.scan_contacts`, skipping the entity merge the CRM schema now requires.** Measured 2026-08-29 over the live 169 CRM cards: contacts found went from 89 to 144 once routed through the shared scanner, blank `company` went from 87 to 0, and blank `type` went from 89 to 0. The digest had been blind to 55 contacts outright and had no company or relationship type for any it did find, live since the entity model landed.

  That same loader also held a fifth hand-rolled frontmatter parser carrying the "characters, not a line" defect other shards fixed elsewhere; the anti-duplication sweep never saw it because it was keyed on function names and this parsing sat inline. The shape-keyed sweep this shard adds found 20 call sites in 17 files testing the literal string `---`, of which nine are real, still-open frontmatter readers, named in `DECLARED_FENCE_SITES` for the next shard.

  Three of shard 53's four remaining `date.fromisoformat(str(...))` call sites are migrated to a shared coercion here; the fourth (`.claude/hooks/checkpoint-offer.py`) is deliberately left, since it compares full timestamps and a date-only coercion would make two same-day compactions compare equal.

  Pinned by `tests/test_a_digest_that_read_a_card_the_schema_had_left.py`. Mutation-verified: 14/14 caught after a first run left two real gaps (an un-lowercased email lookup key, and a `break` that should have been `continue` in a date-field chain). Live corpus verified before/after: 89 to 144 contacts, no blank fields remaining where 87-89 had been blank.

  `21b34aa`

- **Nine real frontmatter readers, found by shard 54's shape-keyed sweep, still tested for the literal characters `---` instead of the fence line, each with its own measured consequence.**

  - `scripts/utils/threads_lib.py::parse_thread_file` raised "missing YAML frontmatter" on any thread file whose fence carried a trailing space or tab; it has 45 callers, one of which silently dropped the thread from an archive scan.
  - `scripts/validate-crm-schema.py` returned a false FAIL on the same shape, blocking the record from aggregation, and separately reported an unreadable file as a malformed block under one bare `except Exception`.
  - `scripts/run-skill-eval.py` failed open: an unrecognized fence left the eval's system prompt as the whole raw file with YAML prepended, and `model:` was silently lost so the run used the default model.
  - `scripts/crm_migrate_to_entity_model.py` scores duplicate cards by body length to pick the survivor; a card with `---` inside a scalar scored 29 against 14 for identical prose, deciding which card becomes the address-book entity.
  - `scripts/chronicle.py`, `scripts/odin-cadence.py`, `scripts/utils/router_payload.py`, and `scripts/utils/viraid_counterpart.py` each either truncated a value or accepted a file the canonical parser refuses.

  Measured 2026-08-29 over one table of eight documents: all nine readers disagreed with `scripts.utils.markdown.split_frontmatter` before the fix and agreed on zero divergences after. A tenth copy in `.claude/skills/skill-creator/` is deliberately kept separate (a plugin-path collision) but is now line-anchored to the same grammar. A behavior-keyed sweep of every frontmatter-shaped regex in the tree found ten more that disagree, recorded for the next shard.

  Pinned by `tests/test_nine_readers_that_looked_for_three_characters.py`. Mutation-verified: 12/12 caught, no survivors, on the first run.

  `2848b13`

- **Three functions rewrite a frontmatter field, and two of them spelled the fence scope themselves rather than using the shared reading logic other shards had already fixed.** Measured 2026-08-29: `crm_autolog.bump_last_touch_in_text` ran `^last_touch:` MULTILINE over the whole document, so a CRM card with no `last_touch` in frontmatter but a body line beginning `last_touch:` (a plausible pasted email quote) rewrote the body line instead of inserting the field into frontmatter. The health engine, which reads frontmatter only, kept reporting that contact red forever, while every layer above it (the write, the audit log) reported success.

  `transfer-contact.update_owner_in_frontmatter` scoped its edit correctly but required a trailing newline after the closing fence; a card ending exactly at that fence matched nothing and got a second frontmatter block prepended, demoting the card's real fields (including `name`) to body text, dropping the blank line before the body, and silently converting CRLF cards to LF. A third writer, `crm-health.frontmatter_end`, had both defects and had already been fixed in isolation, with the fix never reaching the other two.

  The new `markdown.set_frontmatter_field(text, key, value)` is the one place this workspace now decides where a frontmatter field may be written, byte-preserving outside the block.

  Pinned by `tests/test_two_writers_that_edited_the_body_they_meant_to_skip.py` (40 tests), asserting through the CRM's own health-engine parser rather than raw string matching. Mutation-verified: 14/14 caught after two real gaps on the first pass.

  `0d1420b`

- **`memory_health.compute_memory_defects` decided whether a fact file is referenced from `MEMORY.md` using `p.name not in content`, a substring test against the entire index text, which cannot see where a name begins or ends.** Two ordinary inputs walked through it: a shorter filename that is a suffix of a longer, linked one read as permanently referenced, and a bare filename matching the tail of a path-qualified pointer to a different record read the same way. Both failed in the expensive direction: the orphan count read zero and `/memory-hygiene` printed "none" over an index nobody had actually checked.

  The correct rule already existed in `memory_expiry.strip_index_pointers`, matching the exact `](<name>)` link target, and in `scan_dangling_links` ninety lines above the defect in the same file, matching exact stems. This was the third copy of the rule and the one where it was never applied. A first version of the shared reader over-merged: measured against the live index, it collapsed two links joined by a semicolon on one line into a single match, reporting one false orphan. Fixed by building the pointer-stripping regex from the reader's own link pattern; live index verified after: 226 files, zero orphans.

  Pinned by `tests/test_an_orphan_that_hid_inside_a_longer_name.py` (18 tests). Mutation-verified: 15 designed, one real gap on the first run (a pointer target carrying a slash), fixed to 15/15.

  `f8c6d0c`

- **Two production scanners read only the legacy inline `email:` key on a contact card and stayed blind to the current entity/address-book schema, and a third reader in the canonical path itself had its own gap.** `scripts/inbox_pulse/rules.py` and `scripts/inbox-pulse-report.py` never followed `entity_ref`. Measured on the operator's 169-contact tree: the canonical reader resolves 148 addresses, the two blind scanners could see 89, leaving 59 real contacts invisible to inbox triage, 37 of them worth the maximum CRM weight of 3. The failure hid itself further: the same blind key fed the report's "known good domain" check, so an invisible contact's domain also read as unknown, and the report would have advised permanently suppressing it. A third, previously-hidden reader bug sat in `merge_entity_and_relationship`, which took the entity's address unconditionally even when the address-book's `canonical_email` was empty; four live contacts with that shape reported no email at all despite the real address sitting on the card, reaching CRM health, the dashboard, `aggregate-crm`, and `/cold-sweep` drafting. All three now route through one new shared reader, `contact_index_by_email` in `scripts/utils/crm.py`; the two canonical-vs-blind counts now agree exactly (148 = 148, versus a 4-contact divergence before). A leaked raw module-attribute patch in `test_ten_regexes_that_spelled_the_fence_themselves.py` was also found bleeding the operator's live CRM into later tests in the same session; fixed by pinning `HEADING_OS_DATA` instead of one symbol. Mutation-verified 20/20, zero survivors, across seven test files.

  `08a6f39`

- **`except OSError` around `os.kill(pid, 0)` cannot distinguish "process is gone" from "process exists but is not signalable by this user," and two of six places asking this question got it wrong, both acting on the wrong answer by deleting state.** Measured against PID 1 (alive, unsignalable by the test's uid): the canonical `pid_liveness.pid_is_running` and two other callers answered `True` correctly; `scripts/sentinel.py` and `scripts/marp_render.py` answered `False`. In a scratch directory holding a live PID, `sentinel --status` printed "NOT running (stale PID file)" and deleted the PID file that `--stop` needs; `marp`'s `watch_status()` reported the daemon stopped and deleted the watch state and the generated theme. Sentinel matters most because it runs under a service account on the Steward VM, exactly the condition that raises `PermissionError`. A fifth inline instance of the same `except OSError` sat inside sentinel's own SIGTERM-to-SIGKILL escalation and would have skipped the SIGKILL for an unsignalable-but-alive daemon. `scripts/fireside-bot-daemon.py`'s Windows branch had the mirror bug (a NULL handle from `OpenProcess` meaning both "no such process" and "access denied," compounded by `ctypes.windll` never populating the error slot). All three now delegate to the shared `pid_liveness.pid_is_running`; `scripts/fireside-pulse.py` was deliberately left unmerged because its divergent behaviour (answering alive on any `OpenProcess` failure) is a considered choice given it spawns a daemon on a "dead" verdict. Mutation-verified 21/21.

  `6bbc7c1`

- **`google-contacts.py`'s `_replace_first` helper documents and fixes the "whole list gets replaced" trap for emails, phones, URLs, addresses, and organisations, but two repeated fields, biographies and names, never got the treatment, so editing one entry silently dropped every other entry in the same field.** Measured 2026-08-29 on a synthetic two-entry contact: updating a biography shipped as `[updated]` where the correct result is `[updated, second note]`; updating a name shipped as `[New Name]` where the correct result is `[New Name, J D]`. `updatePersonFields` replaces a field's whole list with what the request body carries, and nothing on stdout or stderr indicated an entry had been silently discarded. A deliberate asymmetry is preserved and now pinned by a test: a single-word rename still sends an empty `familyName` because a rename legitimately clears the old family name, while `cmd_add` omits an empty family field entirely because a new contact has nothing to clear. Three smaller findings from the same audit shard, all measured: `gmail-draft.py` printed the literal string "in-reply-to None" because its guard tested `thread_id` (almost always present) rather than the `parent_headers` result that can legitimately be `None`; `gmail-reader.py` exited 0 with no subcommand while its sibling `gmail-send.py` exits 1 for the identical case, so a wrapper reads "did nothing" as success for one and failure for the other; and `--limit 0` reached the API unrefused in both pagers, sending `pageSize=0` (a Bad Request, correct only by luck) or `pageSize=-3` for `--limit -3`, with Gmail's undefined `maxResults=0` behaviour substituting a default page and making `fetch_drafts` silently return more drafts than requested. Zero and negative limits are now refused by argparse before any network call, via a new `scripts/utils/argtypes.py`. Mutation-verified 14/14; 24 new tests, 163 pass across the gmail/contacts suites.

  `1ccd361`

- **A YAML coercion trap was patched in six separate call sites instead of once, a log path was silently created as a directory, and a fan-out wall miscounted how many files a session had actually read.**

  - `yaml.safe_load` returns `None` for a bare `keywords:` line with no value, and `fm.get("keywords", [])` only supplies its default when the key is absent, not when it is present and empty. One note with a bare `keywords:` field raised `TypeError: 'NoneType' object is not iterable` inside `keyword_frequency`, killing all three output modes of `knowledge-health.py` and both callers that run it. Six readers had already patched this themselves; the fix is one shared `frontmatter_list` helper, and a follow-up AST sweep of `scripts/` found three more unfixed sites in `odin_brain_lint.py`, `odin_pagerank.py`, and `crm.py`.
  - `log_dir(*parts)` ran `mkdir` on its whole joined path, so `log_dir("memory-auto-retire.log")` created a directory with that literal name. Every append then raised `IsADirectoryError` into a swallowed `except OSError: pass`, leaving the retirement tool's audit trail empty for 54 days, from 2026-07-06 until this fix.
  - The fan-out wall counted backslash path separators, heredoc bodies, and non-reader Bash commands (`pytest a b c`) as file reads; one heredoc patching a single file was reported as fourteen reads. It now strips heredoc bodies and charges only Bash segments whose binary is on an explicit reader allowlist.

  39 new cases, mutations 12/12, 993 passed on the affected set.

  `b5892b5`

#### The release, push, and secret walls

- **A plugin bundle build gate deleted the previous bundle and started writing a new one before it checked that every referenced source actually existed.** `build_bundle`'s own comment promises "completeness gate first (fail fast before writing anything)," but `completeness_gate` only checks unbundled references, a different question; the checks that each named skill, command, hook and script actually exists sit in the four copy loops, which run after `shutil.rmtree(bundle)` and after `plugin.json` is written. A typo in `config/plugin-bundles.yaml` destroyed the previous bundle and left a half-written one behind. The bundle lives under the untracked `dist/marketplace/`, regenerated by the next successful build, so the severity is stated as low. `manifest_sources` now reports every absent source at once, before anything is written or deleted.

  Guard: three tests added to `tests/test_build_plugins.py`. Mutations 7/7 caught.

  `326b183`

- **The push-time content scanner, the workspace's unbypassable last check before anything leaves the machine, never saw a renamed file's destination path.**

  `push-all.py`'s `_push_delta_files` asked git for its diff with `--diff-filter=ACM` and no `--no-renames`; git detects renames by default, so a `git mv` plus an edit collapses to a single `R` entry that `ACM` drops entirely. Measured in a scratch repo against a real bare remote: a staged rename of a 200-line file carrying one new line returned an empty file list, and returned the file only once `--no-renames` was added. On the empty list, `content_scan` skips scanning entirely.

  Six other tools were found reading the wrong data on this machine: an install script hand-built `<repo>/.git/hooks`, which does not exist inside a linked worktree; a report scanned only the top level of a tree its own companion command had already emptied into subdirectories, reporting "Total files: 1" over 6814; a pipeline summary tested `"closed" in s` before `"lost"`, so "Closed Lost" read as won revenue; the same summary's table parser left a table open past a heading with no blank line, feeding a second table's header into the first as phantom rows; `send-email` accepted `--batch` alongside `--reply`, so a requested reply silently never sent, exit 0; and the same script's address normalizer coerced `[123]` into `['123']`, which exchangelib 5.6.0 accepts client-side rather than flagging as malformed.

  47 new cases, 15/15 mutations caught, 542 passed on the affected set.

  `9e9d5ef`

- **An unauthorised second push went out on a stale belief that approval was still standing, and nine further defects nothing in the workspace could see.**

  The operator authorised one push on 2026-08-30; the authorisation was written into a handoff summary that survived a context compaction and was later read back as a standing fact rather than a spent event, so a second push went out uncommanded. `check_release_gate` in `.claude/hooks/_dispatch.py` now re-reads the last operator-typed prompt in the session transcript before any commit, tag, push, or publish command, and refuses unless that turn's text authorises the action; bound by 45 tests, all red against the pre-fix dispatcher. The first version of the wall anchored every pattern at a command boundary and matched nothing for `.venv/bin/python scripts/push-all.py`, the one command this workspace actually pushes with, measured as `release_action` returning None. Also found: an outbound Telegram notifier rejected only an empty target and three sentinels, so seven timer-driven callers meant one misconfigured environment variable turned six autonomous self-notifications into ungated sends to a stranger; a logging record factory broke a "never raises" promise on every record, so `daemon_heartbeat.beat()` raised in production and nowhere else; a test opened a live socket to the operator's other machine, probing an unrelated daemon whose answer the assertion never depended on; and a single constant in `scripts/design-engine.py` did two incompatible jobs, so a request that hung for its own socket timeout had already spent the whole polling budget and the loop polled zero times.

  The audit's own accounting was also wrong: its parser recognised two of four shard heading styles, so 25 findings across four shards read as "zero findings" and were never worked; re-triage found 22 live.

  `2d1f168`

- **The DATA overlay's pre-push gate ran pytest against an untracked bytecode cache, so editing the very tests it guards was enough to make the next push fail.**

  `scripts/utils/overlay_write_guard.py` refuses any write from an untracked caller, and pytest, living in the engine's `.venv`, is untracked; collecting the overlay's test tree writes `__pycache__/` and `.pytest_cache/` under it, so the gate passed only while that bytecode cache was already present and current. Measured on 2026-09-01: editing two files under the overlay's `tests/admin/` made the very next push fail at collection with `OverlayWriteRefused`, on a repository whose tests all passed, worse than a flaky gate, because a gate that breaks every time its own guarded code is edited teaches the operator to reach for `--no-verify`. The refusal itself is unchanged and correct; the gate now runs with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` so pytest never writes there.

  Measured: 3/3 mutations caught.

  `18ae984`

#### The engine/data boundary

- **The previous commit's claim that the remaining eleven no-overlay failures were "environment noise" had not been proven, and nine of the eleven turned out to be real.** Ten were the missing `.venv`: `uv sync` in the worktree turned all ten green with no code change, once the simulation carried a real virtualenv matching CI. The eleventh hid two more: the shipped demo thread file, `examples/threads/business/EXAMPLE-thread.md`, had no frontmatter, so every thread tool that parses it broke on a fresh clone, and the census benchmark refused to compute truth at all. With that fixed, the census contract failed differently and more usefully: its `@needs_overlay` skip had never fired, because the predicate asked "is there any markdown in the corpus" and the shipped demo tree itself satisfied that. It now asks `data_overlay_present()` directly, pinned by two tests written to not depend on the machine's own overlay state. A separate concurrency bug: under `-n auto`, every test in one file shared one `.tmp/hook-shard-probe` directory, so one xdist worker's teardown deleted a file another worker had just written, producing a false "nothing was reported" that passed in isolation. Each test now gets its own directory. First fully green no-overlay run this audit produced: 13092 passed, 85 skipped, zero failures, down from 82 failures three commits earlier.

  `e3840ab`

- **Eight public, tracked Markdown files told a reader to open a file inside the operator's private data overlay, and the two most-cited had moved inside that overlay, so they were broken references for the operator too.** No guard could have caught this structurally: `scripts/check-path-references.py` extracts paths with a regex whose whitelist is engine-only, so `plans/`, `outputs/`, `crm/`, `datastore/`, `knowledge/`, and `threads/` were never extracted at all, and even when extracted the tool skips non-engine routing and drops anything gitignored (`plans/` is both). A new test closes it for `plans/` specifically, the one tree that can never legitimately be pointed to from a public file, carrying a frozen exemption list with the reason for each entry. Separately, `tests/integration/` claimed test coverage it did not have: three JSON corpora were reachable only through conftest fixtures no test ever requested, deleted along with five dead fixtures behind them; the orphan-detection guard had searched raw text rather than the AST, letting a docstring mention of a fixture file hide an orphan from it. The README also carried four claims that had stopped being true, including a "CEO-only, not published" classification for a `tests/` tree that has had no routing rule and been public since the repo went public. A third defect surfaced from this shard's own full-suite run: `mint_run_id` stamps at `%H%M%S` with no sub-second component, so two tests about ID collision within one second inherited pass/fail from wall-clock timing under 16 xdist workers; both are now pinned to a stub clock. Mutations: 13/13 caught. Suite: 13226 passed, 1 skipped.

  `a9b4873`

- **A hardcoded corporate email domain answered "internal" for one deployment and silently "nobody" for every other clone, and the private-overlay test pin never reached a state directory that sits inside the public engine tree.**

  The tenant domain was hardcoded in three scripts deciding "is this person internal," which trips no secret gate because it is a published identity, but which answers correctly for exactly one deployment; a new configuration key on the operator identity seam now carries it, defaulting empty on a clone with no private overlay. Closing it exposed two more literals wearing the same defect: a skill hardcoded a send recipient, and a Tribe-facing bot offered one as "reach a human" in outbound mail, both needing an administrator address rather than the deployment's own domain, resolved through a new dedicated key. Separately, `.claude/state/` sits under the ENGINE root while the data overlay is a sibling, so `HEADING_OS_DATA`, the pin every isolated test relies on, never redirected writes there; five stray checkpoint files were found already on disk in the operator's live tree, four written by a single test file whose sweep sent the engine root as `cwd` in every payload it built. The state resolver now reads a dedicated state-directory variable ahead of the payload; the first version of the fix redirected unconditionally and broke 14 tests that had already isolated themselves correctly, so the pin now applies only where a write would land inside this clone.

  Measured: 8/8 mutations caught, and a 291-test sweep now writes nothing into the live tree.

  `46bf0ff`

- **A daemon running on a read-only mirror VM rewrote live CRM cards, and the pull that mirror depended on then failed silently for three and a half days while systemd reported success.**

  On 2026-08-30 the fireside daemon's email-backup job shelled out to `scripts/send-email.py`, whose `_autolog_to` calls `crm_autolog.log_outbound` for every recipient with no host check and no mode switch; five `crm/contacts/*.md` cards were rewritten inside a clone that only ever runs `git pull --ff-only`. The pull aborted every hour after that, and the mirror sat five commits and three and a half days behind while the pull service kept exiting 0, so systemd reported success the whole time and nothing alerted, because the watchdog only watches daemons, not sync state. `scripts/utils/crm_autolog.py` gains a read-only flag checked in both `log_outbound` and `bump_inbound` after the recipient is resolved, so a refusal can name the contact it declined to touch; a second, unguarded door into the same contact files was found in the bridge daemon's finalizer and got the identical check. Bound by `tests/test_a_daemon_host_that_wrote_into_a_pull_only_mirror.py`, 31 tests; on the VM side, systemd drop-ins now set the read-only flag on all three daemons and the pull script hard-resets and names any local modification before realigning the mirror.

  Suite: 22771 passed, 1 skipped, 0 failed.

  `6b3b638`

#### Paths, filenames, and tree sweeps

- **The data overlay's `.gitignore` named lock sidecar files one exact path at a time, so a new sidecar shape kept slipping past it and reaching the engine's push guard.** `test_no_lock_sidecar_is_tracked_in_either_repository` refused an engine push over a new `outputs/operations/ops-radar/autoheal.json.lock` file in the data overlay: it matched none of the overlay's per-path ignore rules, so `push-all`'s `git add -A` committed it. The guard itself worked; the enumeration behind it, one rule written after each specific sidecar appeared, did not scale. The overlay now carries one `outputs/**/*.lock` rule. A new guard test asks `git check-ignore` about paths that exist nowhere on disk, so it goes red the moment the overlay names sidecars one at a time again, verified red with the rule removed and green with it restored.

  `8ac690c`

- **`str.lstrip` was used as if it strips a prefix, when it actually strips a character set, in five of six bridge-daemon file readers.** `rel_path.replace("\\", "/").lstrip("./")` removes every leading `.` and `/`, not one `./`, so the prefix check that followed ran against a string the caller never sent. `library.py`, `threads.py`, `investors.py` and two sites in `studio.py` carried the bug; `approvals.py` had already dropped the strip. Measured across six hostile inputs per reader: 25 of 30 cases mismatched between the reference validator and the five broken readers, 0 after the fix. Containment held (no traversal reached outside the served tree, since `lstrip` also eats the `..`), but refusal fidelity did not: a string the validator rejects was silently rewritten to an in-tree path and served, with the response's `path` field reporting the rewritten value rather than what was requested. The reusable normaliser moved into `scripts/bridge_daemon/_safepath.py`, with `approvals._normalize_rel_path` now aliasing it. Mutation-verified 15/15, zero survivors. A sibling instance of the same trap was found and left unfixed in `scripts/md-to-docx-competitive.py:285` (`line.lstrip("> ")`), noted for a separate change.

  `e6e1e54`

- **Twenty tests walked the repository with hand-written skip lists instead of asking git what to ignore, so an agent worktree (a full second copy of the tree) got counted twice by every one of them.** Measured on the CI-shaped suite: with a worktree checked out at `.claude/worktrees/agent-probe`, the suite went from 15,609 passed / 0 failed to 8 failed, each failure naming a file the operator cannot fix and never mentioning the worktree; while the copy is present, every sweep also silently doubles its corpus. After the fix: 15,622 passed, 0 failed (15,609 plus the 13 new tests). `tests/repo_files.py` is now the single implementation, asking git `check-ignore` once per sweep in batch form and raising on a git failure rather than degrading to "nothing is ignored." Sixteen exposed walk sites migrated to it; two independent duplicate implementations were deleted. An AST sweep with no allow-list now blocks a new test module from re-implementing its own walk. Mutation-verified 19/19; three first-run survivors were all the "green over an empty corpus" shape, closed by extracting pure functions and testing both directions on synthetic input.

  `76fc18c`

- **The third occurrence of the same `lstrip` character-set trap in this repository: `line.lstrip('> ')` in `scripts/md-to-docx-competitive.py` stripped every leading `>` and space, not the blockquote marker, so a throughput claim like `> >100 Gbps sustained throughput` rendered as `100 Gbps sustained throughput`, a materially weaker claim in a document that leaves the building.** Measured 2026-08-29 on three real lines, all three losing their leading digit-bearing character. The sibling file, `scripts/md-to-docx-proposal.py`, had already been bitten by the same class (a `-5% margin` list item rendering as `5% margin`) and carried the fix and a comment naming the symptom; the competitive-analysis generator did not. The replacement strips markers one at a time and stops at a `>` that is not one, so nested quotes still unwrap correctly. A new registry test declares every multi-character `lstrip`/`rstrip` literal in the tree with the reason its character-set semantics are correct, six entries plus the test's own anti-vacuity fixture. Mutation-verified 18/18; three survivors across two runs were the "green over an empty corpus" shape, closed by extracting the three violation predicates as pure functions.

  `6db7779`

- **Three of eight sidecar "quarantine" writers built a corrupted-state-file's name from the live file's name, so the wreck landed in the live directory under a name nobody had ignored.** Measured 2026-08-29 with `git check-ignore`: `outputs/operations/action-queue/queue.json` is ignored, but `queue.json.corrupt-<stamp>` is not, and that file can hold pending gated drafts including recipient addresses and full email bodies; since `push-all.py` commits with `git add -A`, a single corrupt-queue event (reachable from a plain read, not only a write) would have put un-sent draft bodies into the private repo's permanent history. A census of all eight sidecar writers found a third defect not in the original finding: `run-skill-eval.py` wrote `benchmark.json.corrupt` inside a tracked skill directory in the public engine repo. The fix is a single writer, `scripts/utils/quarantine.py`, that always lands the wreck in a `.quarantine/` sibling directory rather than letting the caller choose a name; both repositories now carry one general `**/.quarantine/` gitignore rule. No wreck file existed anywhere in either tree at the time of the fix; the defect had not yet fired.

  `5ee7104`

- **`scripts/classification-health.py::walk_workspace`, the production tool the operator reads to judge whether the engine/data split is holding, carried the same gitignore-blind sweep pattern that `76fc18c` had already fixed in twenty test modules, but left production untouched.** Measured 2026-08-29 on the real repository: total files 2,363, of which git ignores 427 (18%), including `.claude/settings.local.json`, a stale backup file, marp web-font binaries, and a scratch file; only one file was reported CEO-only, and that one file was itself gitignored. After the fix: 1,938 files, 0 ignored, and the CEO-only count is 0 because its only entry was gitignored. The `.claude` carve-out (skipping hidden directories except `.claude`) made it worse than a flat 18%, since `.claude/worktrees/` is where an agent worktree is checked out, a full second copy of the tree counted twice while it exists. Fixing this needed `ignored_paths`, previously living only in `tests/`, which production cannot import from; it moved to `scripts/utils/repo_files.py` with `tests/repo_files.py` re-exporting it. That move exposed a third finding: `scripts/check-path-references.py` already held an independent second copy of the batch call that had drifted to the opposite contract (silently degrading to "nothing ignored" instead of raising); the shared module now offers both `ignored_paths` (raises) and `ignored_paths_or_none` (returns None), with callers stating their choice explicitly. The one-implementation guard from `76fc18c` was itself found incomplete (it swept `tests/**` only, used a text match that flagged prose describing the command rather than using it, and would have defeated its own litter-detection test by filtering out untracked litter by construction); all three are fixed. Mutation-verified 15/15 after closing three survivors of the familiar "green over a clean corpus" shape. 143 tests pass across six affected suites.

  `2f42b72`

- **`strip_markdown_noise`'s indentation strip, `^[\s]*` under `re.MULTILINE`, matched the blank line above a list marker as well as the marker's own indentation, because `\s` includes the newline and `^` anchors at the start of that blank line too, merging a lead-in paragraph and a list into one paragraph.** Measured before the fix: a lead-in paragraph, a three-item list, and a closing paragraph came back from `get_paragraphs` as two paragraphs instead of three; on the merged form, `check_burstiness` returned nothing for a monotone list that should have flagged, while on the correctly-separated form it returned a real `burstiness_violation`. Since every paragraph-based humanisation check reads `get_paragraphs`, the blast radius covered burstiness, specificity, transition-opener, and over-fragmentation checks at once, unpinned by any test. Two smaller defects in the same file: `check_burstiness` tested `total_words >= 200` while its own docstring said "greater than 200 words," so a document of exactly 200 words produced a blocking violation the docstring said could not fire; and a comment promised a sentence-initial-capital acceptance in `check_specificity` that was never implemented. Separately, the fan-out wall's `investigated_paths` normalised backslashes as path separators (a Windows accommodation in a WSL-only workspace), so a regex escape like `\s`, `\n`, `\t`, or `\d` inside a shell command counted as a distinct file; measured, one heredoc patching a single file was reported as touching fourteen, and the fan-out wall refused the session mid-repair of itself. Backslash support was dropped, and a bare separator without a following alphanumeric no longer counts as a path token. A dispatcher docstring naming three matchers when there were five (missing the two walls' own unlock doors) was also corrected. 16 new cases for the bullet and 200-word boundary, 5 new cases for the token precision, mutation-verified 10/10, 213 passed on the adjacent set.

  `014c906`

- **Three guards written to stop a destructive copy or delete accepted the one input that matters: the root itself.**

  `_contained(dest, ".")` and `_contained(dest, "")` both returned the destination path itself instead of refusing, and `_vm_path` accepted `[".."]`, `["."]`, and `["/tmp", ...]` as valid mirror names. `publish-service.copy_includes` calls `rmtree_force` on a directory include before copying into it, so a hand-edited config value naming the root would delete `.git` and everything else, then copy the whole workspace back in filtered only by a static ignore list that does not name `.env` or `.sessions/`. On the pull side, `pull-service-state` deletes the previous mirror before renaming the new one in, so `".."` would delete the mirror's parent; element types were never checked either, so an integer segment raised `AttributeError`, uncaught by the handler's `except ValueError`. None of these fire on the configs as shipped; each needs one hand edit, which is exactly the input class the guards exist for.

  39 new cases, 13/13 mutations caught, 219 passed on the affected set.

  `e797807`

- **A symlinked interpreter inside a linked worktree took down every repository-wide tree sweep, and the first fix introduced a second defect that silently stopped filtering anything.**

  `not_ignored` called `Path.resolve()`, which follows symlinks, then asked `git check-ignore`; a worktree's own `.venv` interpreter symlink resolved outside the repository, `git check-ignore` exited 128 on a path outside it, and the caller correctly raised rather than silently reporting an unfiltered tree. One poisoned path took four sweeps down at once, turning a green suite into seven failures with no commit in between. Switching to `os.path.abspath`, which normalises without following, fixed that case but broke relative paths: the module tested the raw string against the repository's absolute prefix, so every repo-relative path read as "outside the repository" and nothing was filtered at all, a filter that silently stopped filtering. Six other red tests surfaced the same day, including a security-mechanism list missing two check names (the documented security-test figure of 485 versus CI's actual 487) and a test that flagged its own defensive comment as a violation.

  Full suite green at 16620 passed.

  `db13afb`

- **The bridge daemon's contacts reader was the one CRM reader never migrated off two retired repositories, so every executive's contacts page would have silently rendered the CEO's own records.**

  `scripts/bridge_daemon/sources/contacts.py` resolved a retired central-repo path first and a retired per-exec-repo path second, both absent from disk; every executive lookup resolved to `None`. Measured before the fix, with a fixture built at the live layout: `is_dir()` True, resolver still `None`, 0 contacts returned. Nothing caught it because the bridge daemon is disabled and the tests carried fixtures built at the retired layout, so they agreed with code describing a world that had ended. The fix resolves the live per-executive data overlay path through one shared helper, and removes two dead constants, a crm-central fallback, and an orphaned glob backstop, each removal proved by restoring it and watching tests redden (8, 2, and 4 failures respectively). Fourteen documents repeated the same wrong repository path, including the live read instruction for the company radar and an orchestrator prompt that told an agent to push to a remote that no longer exists.

  `895ce68`

#### Reports that outran their evidence

- **Eight more shards, read by hand, turned up tools that printed a number or a verdict their own method never computed.** `run-skill-eval --case <typo>` matched nothing, ran nothing, and exited 0; both eval runners overwrote the full benchmark with a single `--case` run's result (`email-intel` went from 9/9 across three cases to 2/2 over one, with no marker of the narrowing); `eval-outcomes --all --case <real-id>` ran and passed the case and still exited 2; `eval-query-set` scored an unparsed test set as 0/0 = 0% FAIL against an 80% bar. A trace-file credential masker let numbers through unmasked while reporting "0 keys masked," and its own "check it yourself" caution was suppressed in exactly the run where nothing had matched. `exchange-task --list` raised `IndexError` mid-listing on a whitespace-only body, silently dropping every task below it. A single-asterisk ignore pattern filtered an entire mailbox as noise. `email-sweep`'s `failed` state was a dead end with no transition out of it. `dream-shadow` reported "0 merge candidates" for a scan that never ran, and `prime-health` read that as healthy over an actual embedder outage. `design-engine` reported `$0.000` for seven models whose price it had never looked up. Serialized timestamps in both eval sidecars moved to UTC, since `time.strftime` carries no tzinfo that ruff's DTZ ruleset can flag. Eight new test files, each mutation-checked to 100% (25/25, 25/25, 20/20, 23/23, 33/33, 30/30, 31/31, 38/38, 50/50). Full suite: 9738 passed, 12 skipped; ratchet 17 below baseline.

  `01056f7`

- **Four reporting tools computed the right numbers and then printed a sentence those numbers did not support.** A memory-health panel shown at every session start printed "Memory: N files, L/200 lines. All healthy" without ever reading the `over_budget` flag its own helper returned, the refuting number sitting two words to the left of the claim in the same sentence; a separate script already reads that field correctly in five places, so only the panel the operator actually sees at session start was the silent, dishonest twin. A dashboard generator printed "Freshness: all current" whenever its red-severity count was zero, collapsing two other health states, files 8-14 days old, and files with no verification marker at all or an impossible date, the worse of the two since nothing about that file was ever measured, into the same "current" label; the HTML panel had always drawn each row correctly, only the terminal summary line lost the distinction, now extracted into a shared `freshness_summary` function both consume. A docs search-index builder stored only the first 1600 characters of each section with no indication that anything was cut; measured on the live index, 51 of 506 sections were truncated, meaning roughly a tenth of the shipped site's prose is unsearchable by any phrase past the cut, and the flag is now computed from the true pre-slice length and shipped in the index. An inbox report table cut at 50 rows under a heading carrying no count, now naming both the count and which sort order determined which rows were dropped. 26 test cases across every band and every boundary, plus a negative case per report so a tool that always complains cannot pass by default. Mutations: 15/15 caught. Suite: 13532 passed, 1 skipped; ratchet 265/271; content-guard clean; docs regenerate with no drift.

  `b814936`

- **Eight counters and guards reported a slice of a dataset as if it were the whole thing.**
  - The approvals dashboard measured `total` after slicing `items[:APPROVALS_ROW_CAP]`, so "N waiting" could never exceed the cap no matter how far behind the backlog ran. `total` now precedes the slice, and the envelope carries `truncated` and `row_cap`.
  - The threads panel measured `total` before its cap but the per-bucket counts after, so `sum(counts.values())` disagreed with `total` past `THREADS_ROW_CAP` active threads.
  - The pipeline parser broke at `PIPELINE_ROW_CAP` deals, so every stage total and value sum was computed over the first page of the file rather than the whole file.
  - The Action Queue's `applied` status was in neither `ACTIVE_STATUSES` nor the prune's terminal literal, so an applied card blocked no duplicate, was pruned by nothing, and appeared on no operator-facing surface. `TERMINAL_STATUSES` now names it, with its own `applied_at` stamp.
  - The dead-letter store named a record `<trace_id>__<kind>.json`, and a trace_id names a process tree, not a card, so two permanently-failed cards from one deposit collided on one filename and `os.replace` clobbered the first while both callers reported a durable record that did not exist. Names are now claimed with `O_CREAT|O_EXCL`.
  - The investor send log's write path moved to the shared append primitive while the read path kept its own `return {}` on an oversized file, so one byte past the cap erased every "already sent" mark.
  - The bridge daemon's `--health` reported whatever answered on the port as itself; it now requires the shape the daemon's own `/health` returns (`ok`, an int `pid`, a str `version`).
  - The engine-routed contacts source hardcoded the operator's real name; it now reads `get_operator()["name"]`.

  Guard: `.tmp/audit/mut_eight_counters_that_reported_a_slice.py`. Mutations 30/30 caught.

  `ed3201a`

- **Five verdicts were reached by inspecting only one of several possible code paths, then printed as if every path had been checked.**
  - The CodeGraph memory-index `_build_store` stamped a store as freshly built by the current embedding model based on the LAYER NAMES walked, even when `iter_symbols` or `iter_commits` raised and the handler deliberately kept a layer's existing rows, so `build --force` against a deleted CodeGraph index cleared the mixed-provenance flag while a whole layer still held vectors from a different model.
  - The same tool's denial count was incremented only inside the glob branch; the commit walk and symbol walk apply the same air gap but never reported it back, so a pass that refused N commits printed "0 denied."
  - `memory_health`'s pointer regex ended in a greedy `[^·\n]*`, which does not exclude `[`, `]`, `(` or `)`, so on a line whose pointers are not separated by a middle dot, the first match swallowed the rest of the line and every later pointer went unscanned. A second half of the same defect gave only the FIRST pointer on a line its leading label, so a value like "costing EUR 412,000" attached to a later pointer was read by neither the pointer it introduced nor the one before it.
  - A dashboard link test named "every server-supplied link" but matched only one literal shape, `href="${escapeHtml(...)}"` with at most one nesting level. Measured against `scripts/bridge_daemon/web/app.js` on 2026-08-28: eleven sites, nine distinct expressions, all already gated through other means, so no live vulnerability existed, but the check could not have seen a twelfth site if one appeared.
  - A pagination test's key assertion, `maxes == sorted(maxes, reverse=True)[:len(maxes)] or maxes[1:] == [16, 6]`, was vacuously true on a single page and only the hardcoded fallback pair ever decided the result, pinning a fixture rather than the contract.

  Mutations 18/18 caught (`.tmp/audit/mut_five_claims_one_path.py`).

  `2ad8f73`

- **`design-engine.py generate` accepted six flags and silently dropped a different subset of them per model family, and three related tools in the same file made the same class of silent-drop mistake.** The design skill's own documented command always passed `--width`/`--height`, but those two flags were dropped for `flux` and `banana`, the two families the skill recommends most, with nothing printed to say so. `_build_generate_input` now returns `(params, dropped)` and warns on every typed flag the payload does not carry, derived from the payload itself rather than a second hand-written per-family table.

  - The output filename was built from `--format`, a flag `recraft` and `ideogram` are never told, so `--model recraft-v4 --format webp` wrote PNG bytes into a file named `.webp`. The name is now sniffed from the bytes' magic numbers.
  - `_save_outputs` only numbered files when a `multi` flag was set, and three of its four callers hardcoded `multi=False`. Measured with three URLs: three "Saved" lines named one path, one file on disk held the last body, and cost was billed for three images against it.
  - `_download` had no scheme check, size cap, or error handling, unlike its sibling in `scripts/updaters/cliproxyapi_update.py`. A fix that landed in one of two copies, the fourth time this audit found that shape.
  - `_upload_file` interpolated a filename raw into a multipart header; a crafted name could rewrite the part the tool believed it was sending. The three structural characters are now neutered.

  Pinned by `tests/test_the_flags_a_tool_accepted_and_never_sent.py` (66 tests). Mutation-verified: 15/15 caught, no survivors, after a first run of 14/15 forced a per-line-to-per-command fix in the SKILL.md documentation check.

  `7695f0a`

- **`ALLOWED_RETURN_PAGES`, gating the bridge daemon's `/return` and `/telemetry/page-view` endpoints, had drifted from the `ROUTES` table in `web/app.js` in both directions.** One page shipped with a renderer, a Pulse KPI tile, and a keyboard shortcut, but was never added to the allowlist. Measured on the shipped tree: every `POST /telemetry/page-view` for that page answered 422 and was silently swallowed by the client, so eighteen half-hour page visits produced seventeen recorded events, and because `browser_first` is decided by the first event of the local day, a morning that opened on the refused page handed that slot to a later terminal launch: one such morning showed `browser_first_mornings=0, tab_time_total_minutes=0.0` with the view refused versus `=1, =25.0` with it kept. That statistic feeds one of the three gate booleans `/bridge-health --gate` reads for the Phase 1 to Phase 2 decision.

  In the other direction, two retired page names stayed on the allowlist and would still answer 200 for pages with no renderer at all. Six existing page-view tests missed both directions because every positive case used only two of nineteen names and the one negative case was a path-traversal string, which proves the allowlist rejects something but nothing about whether it matches the shipped route table.

  Mutation-verified: 11/11 caught, no survivors, including three mutations that reintroduce each half of the drift and three that blind the parsers reading `web/app.js`.

  `1d3bc25`

- **Five separate documentation pages asserted something the code does not do, bundled in one commit because the generated search index ties them together.** (1) No code has ever read a `role` field from `config/admin.json`, and `EMERGENCY-PROCEDURES` described a Deputy Admin grant through exactly that; the real grant lives on `.workspace-identity.json`, so an executive following the emergency procedure during a real outage would have gotten nothing. (2) The daemons page claimed to cover "every long-running daemon and every scheduled task"; measured, `scripts/install-*-timer.sh` returns 14 files and the page named 4, with a complete catalog already existing elsewhere. (3) `EXTENDING.md` stated skill counts of 69 skills and 710 trigger cases; measured, the real figures were 70 and 730, now derived by `scripts/dev/check-readme-numbers.py` under a pre-commit hook rather than hand-typed. (4) `/scrutinize`'s flag catalog listed five flags while six other surfaces carried between 2 and 4 of them, missing `--no-code-review` on three, and named a `--judge-family` flag that does not exist (the real flag is `--family`, with argparse choices limited to `{claude, kimi}`, not the documented Gemini/Grok). (5) `docs/skills-intel.html` said `/notebooklm` runs on Sonnet; its frontmatter has said `haiku` since 2026-08-09, and the catalog's lock badge was missing on `/scrutinize` in the index page specifically, because the badge guard's card parser could not read the index row shape and excluded it by construction rather than by design.

  `2117ccf`

- **The dashboard's Capture Payoff panel shelled out to `odin-cadence.py --json` and never checked `returncode`, so a crashing child (an uncaught exception, empty stdout, non-zero exit) was indistinguishable from "no cadence helper installed on this workspace."** Measured 2026-08-29 with a real note plus a child forced to exit 1 after writing to stderr: before the fix, a crashed child and an absent helper both rendered "-" with no CSS class and zero stderr bytes reaching any handler; a healthy child rendered "3" (accent) and "9d" (danger). The existing exception handler never ran because nothing was raised; the process exited 0 with a complete-looking dashboard on exactly the days something was wrong enough to kill the cadence helper. Two sibling callers of the same child already checked `returncode` correctly (`prime-health-parallel.run_odin_cadence` and `ops_signals.odin_cadence_state`), making the dashboard the asymmetric outlier. The read logic moved into a shared `scripts/utils/odin_cadence.read_cadence_json`, used by both `--json` call sites; the panel now renders "?" in the danger colour with the failure reason escaped and shown, distinct from the legitimate "-" no-helper state. Pinned by 27 new tests in `tests/test_a_panel_that_read_a_crash_as_no_data.py`. Mutation-verified 22/22, zero survivors.

  `748aef5`

- **Nine tools across the workspace printed, returned, or logged a success, a clean state, or a stored value that the run itself had never established.**

  - The ops radar could not silence its own auto-heal alarm: `cmd_ack("ollama_autoheal")` exited 2 because `KNOWN_KEYS` never carried the synthetic key, and even once accepted, the acked band would have resolved to `ok` from a signal source that does not carry that key either, storing an ack that suppressed nothing.
  - A scorecard splice wrote nothing and reported sync: with its markers reversed, `splice` returned the input byte-identical and `--check` reported "in sync" over a stale table.
  - A push retry died at an empty commit: with the commit already local and unpushed, a same-day `--overwrite` re-run staged nothing, `git commit` raised, and the retry never reached the push step.
  - A mailbox cursor lost and duplicated mail: a fake mailbox raising after serving 2 of 3 items produced A, B, A, B, C; separately, `+1s` cursor arithmetic against a strict `>` filter withheld any message stamped on the following whole second, permanently, measured to lose one message across 4 consecutive polls.
  - A config scalar inverted a classifier: `always_critical: "*@acme.example"` written as a scalar instead of a list made every sender match, because the membership test walked the string character by character.
  - A scaffold crashed instead of refusing a symlinked directory, and wrote through the link target with exit 0 rather than refusing it.
  - A version check matched the wrong package by substring, letting a real 4.4.0-versus-4.5.0 mismatch pass; eight more findings in the same renderer took pre-fix coverage from 19 of 28 cases passing to 74 passing across its three test files.
  - A backstop meant to catch a brain-integrity failure was itself bypassed on `TimeoutExpired`, the one path where the child process had run unsupervised for up to 600 seconds.
  - A contacts search latched "looked everywhere" true after checking only the first repository, running its deletion step before the archive step and leaving the unchecked repository read-only.

  Full suite: 16850 passed, 1 skipped. Lint ratchet: 252 findings, 43 below baseline.

  `53ca548`

- **Fourteen more tools reported outcomes their runs had not earned, and one test only passed five days a week.**

  - A contact touched in the future read as the strongest health signal: `calculate_health` bands a negative day-gap (a future `last_touch`) below every real threshold, so corrupt data scored `green` instead of the `gray` it gets now.
  - A refused census run left no row at all: `--emit-answers` writes one row per attempt, but two argument failures returned before writing one, so a moved traversal program scored identically to a question nobody ran; a comment claiming exactly one unrecorded exit was false, since three exits wrote no row.
  - A phone number could break the CRM file it was written into: the entity renderer hand-wrapped `phone` in unescaped double quotes and interpolated `linkedin` raw, so a quote, backslash, colon-space, or hash in a scanned value corrupted the YAML frontmatter.
  - Two writers shared one scratch file: `crm_autolog.atomic_write` derived its tmp filename from the target, so a scheduled `bump_inbound` and a terminal `log_outbound` writing the same contact could truncate and overwrite each other's scratch file mid-write.
  - A send queue reported a status write it never made (`approve_and_send` printed "card kept as send_failed" for a card carrying no such status on disk) and crashed with a raw `AttributeError` on a malformed queue document instead of rejecting it cleanly.
  - A bridge test built its fixture "yesterday" from the host clock but the counter it checked only counts weekdays, so it failed every Sunday and Monday since it was written; it is pinned to a fixed Thursday now.

  Full suite: 17004 passed, 1 skipped. Lint ratchet unchanged at 252 findings, 43 below baseline.

  `8855420`

- **A ten-day-old audit register could not answer its own central question: of 708 findings, how many were actually resolved?**

  Parsed by program rather than by eye, the 138 shard reports held 708 findings (not the 709 the campaign's own arithmetic carried; one entry was a placeholder), and every one now carries a verdict: 670 already fixed, 27 not defects, 11 still present, 0 unverified; four in five described code that no longer existed, so nothing was closed without opening the current file first.

  Of the eleven still-live findings:
  - A contact listing served a planted symlink's display name, role, and frontmatter with no symlink check and no size cap, while the detail view a hundred lines below in the same module refused both; the byte cap is now a single named constant shared between the two readers, pinned by a parametrised test.
  - A heartbeat raised out of every 60-second tick when no home directory resolved, because `Path.home()` raises `RuntimeError`, not the `OSError` its docstring promised zero of.
  - A JSONL fallback created its file at the process umask and wrote to it before narrowing the permission, reopening the exact window the main path closes; closed with `fchmod` on the open descriptor.
  - Two same-day, same-priority tasks sharing a 64-character description prefix collapsed to one storage key, silently merging two tasks into one.
  - The push-time content wall, already widened once to see renames, still omitted the `T` diff-filter status; measured in a scratch repo, a tracked file replaced by a symlink is a single `T` entry that `--diff-filter=ACM` returns as empty and `ACMT` returns.

  Two "already fixed" verdicts in the register turned out to be partial fixes hiding as closed, which the audit calls worse than open, because the register said otherwise.

  `49dc74a`

#### Clocks, timezones, and concurrency

- **Running the suite at a different timezone offset (Etc/GMT+12) broke 41 tests that inherited "today" from the host clock and asserted it as fixed, none of which were code defects.** The failures split across four root causes, each pinned at the point a test reads its clock: `tests/bridge/conftest.py` now pins `HEADING_OS_TZ` to UTC+4 for 37 bridge tests that had already assumed that zone in writing (a comment reads "10:00 UTC = 14:00 local (UTC+4)") and were only true because the host happened to agree; `test_timeparse.py` pins the operator day to UTC for its two counting tests; `test_compaction_probe.py` pins the display zone to UTC for two tests that had never gotten one despite an existing comment naming the assumption; and a calendar fixture in the bridge suite wrote a timestamp with the SYSTEM zone while the code under test reads the OPERATOR zone, a bug only visible at UTC+14, not UTC-12, because `TZ` and `HEADING_OS_TZ` are different seams needing opposite shifts. A new guard test re-runs the eight clock-sensitive files at both zones and floors on the number that actually ran, refusing to be green over a list decayed into names matching nothing. One fixture was removed rather than pinned: a mutation deleting it survived at three different zones, proving it constrained nothing. Mutations: 15/15 caught across three zones. Suite: 13222 passed, 1 skipped, at both the host zone and UTC-12.

  `d1047ef`

- **A background daemon's graceful shutdown took 29.54 seconds to respond to SIGTERM because `signal.signal` handlers run between bytecodes, and the event loop was blocked inside a long `select()` wait with nothing scheduled to notice the flag being set.** Measured against the daemon's real wait shape: SIGTERM sent at 2 seconds, `wait_for(..., timeout=30)` returned at 29.54 seconds under the old handler and at 1.84 seconds once switched to `loop.add_signal_handler`, which runs through the event loop rather than between bytecodes. At the daemon's real check interval (up to fifteen minutes), that gap exceeds systemd's `TimeoutStopSec=90s`, turning every graceful stop into a SIGKILL. A security test had already blessed the old code, since its only positive assertion was `assert "_stop_event" in content` over a 3,000-line file; it is now rewritten against the AST of the installer and the loop, with a behavioral test that sends a real signal to a child process and bounds the wake time under one second. A sibling security test had the identical shape (`assert "os.replace(" in content`), paired with a runtime check that globbed for a tempfile pattern that could never match the code's actual naming. Five refusal paths gained their first negative case, including the census return schema, which had fifteen declared refusal branches and zero of them executed by any test (measured at 83% coverage). Mutations: 35/35 caught; one mutation was withdrawn as fundamentally unanswerable (weakening an assertion cannot be caught by the assertion it weakens) and replaced with a question that does have an answer.

  `63006c1`

- Batch 2 changelog entries (raw commit review, house-style drafts)


  - **A scheduled comms monitor could never run, a cross-process lock stopped at the process boundary, and a migration scan claimed "all execs" over the ones it silently skipped.** Three separate promises the code could not keep, found and fixed together.
    - `scripts/utils/schedule.py` installs a 15-minute scheduled task on every provisioned exec that runs `scripts/sentinel.py --check`, but `sentinel.py` never defined a `--check` flag: argparse exited 2 with "unrecognized arguments" every fifteen minutes and no cycle ever ran. `--check` is now a live single cycle, and a contract test checks that whatever `schedule.py` passes is a flag `sentinel.py` accepts.
    - `scripts/bridge_daemon/sources/action_queue.py` serialized writes to `queue.json` with a `threading.Lock`, but since 2026-06-27 the queue is terminal-native and `action-queue.py`, `cold-sweep.py` and `dead-letter.py` each import these helpers as separate short-lived processes, where a thread lock orders nothing. Two overlapping runs risk a lost update, and the dangerous direction reverts a `sent` card back to `pending`, re-entering `SENDABLE_STATUSES` and risking a duplicate send. Reproduced with two real processes outside the repo. Fixed with an flock beside `queue.json` paired with the thread lock, guarded by an AST test that refuses a sixth mutator that skips it.
    - `scripts/crm_migrate_to_entity_model.scan_all_contacts` skipped any exec whose data overlay is absent on the running machine, while both callers printed "Scanned N records across all execs."

    Guard: `tests/test_three_promises_the_code_could_not_keep.py` (22 cases). Mutations 13/13 caught.

  `092c6e9`

- **A comms monitor stamped every alert with a bare UTC clock and deleted the offset, so a message could be announced as arriving on the wrong day.** `sentinel.py` sliced or stripped the timezone off every timestamp it raised (`str(dt)[:19]` and `datetime.now(timezone.utc).isoformat()[:19]`), feeding an unlabeled clock into the Telegram alert card and the urgency model's `DATE:` line. On a +04 mailbox the stamp read four hours early, and before 04:00 local it named the wrong day: a message that landed at 01:34 was announced as 21:34 the day before. `local_stamp` now converts once and labels the zone.
  - `exchange-task.py --list` rendered UTC-aware due dates with a bare `strftime`, while the create path in the same file already labels its confirmation with `EXCHANGE_TIMEZONE`, so one script reported the same reminder on two different clocks with only one of them saying which.
  - `email-intelligence.filter_noise`'s `check_processed=True` branch, which drops mail already handled on a previous run, had no test calling `filter_noise` at all: flipping the default to `False` in a scratch tree left the full 13.5k-test suite green. Six new tests cover both branches.

  Guard: `tests/test_two_clocks_and_a_default_nothing_read.py` (30 cases). Mutations 16/16 caught after three rounds.

  `1d315bd`

- **Two terminals approving the same Action Queue card could both send it, and a secret-scan hook let a staged secret through a filename with a space.**

  `approve_and_send` read a card's status, spent up to 120 seconds inside the sender, and only then wrote a status, with the read and the write sitting in separate locks. Measured with two threads racing one pending card: the sender ran twice and both calls returned `sent`, one keystroke from a duplicate message to an external counterparty. It is closed with a claim: `claim_card_for_send` checks and writes a `sending` status inside one queue lock, so the losing thread finds the card already claimed; a claim older than five minutes is taken over only on an explicit re-approve, since nothing inside a claim outlives the sender's own 120-second timeout. Separately, the standalone pre-commit secret-scan hook built its staged-file list with an unquoted `git diff --name-only -- $STAGED`; measured with `my secret.env` staged and its worktree copy then changed, the shell split the name in two, matched nothing, and the scan ran against the harmless worktree copy while the staged secret went into the commit.

  `920c9b2`

- **A dashboard status pill read the clock twice, once per half, so a request crossing local midnight could report two different days in one answer.**

  `sea_state` took a `today` parameter and threaded it into one of its two overdue counts, but the mood half, which reads the day's calendar, called `datetime.now()` for itself; a caller that pinned a date got one half pinned and one half live. `_today_event_count` now takes the same `today` and widens it to local noon rather than midnight, since a DST jump can skip or repeat midnight but no real timezone shifts at midday. The test fixture carried the mirror-image bug: it wrote a calendar file named from the clock, then `sea_state` read the clock again to decide which file to open, a race real under `-n auto` since each worker starts at an arbitrary wall-clock instant; the helper now takes the day as a parameter so one value names both sides.

  Both mutations (dropping the pass-through, and keeping it while ignoring the injected date) were caught, six failures each, source restored byte-identical. Suite: 22805 passed, 1 skipped, 0 failed.

  `e6cb14f`

#### Promises the code could not keep

- **The workspace's own emergency-revocation tool crashed at import under exactly the failure state an incident produces, and eleven quieter defects went with it.**

  `scripts/emergency-revoke.py` ran `GITHUB_ORG = load_github_org()` at module scope. Measured with the private data overlay unreachable (`HEADING_OS_DATA=/nonexistent-xyz`), even `--help` died with a raw `DataRootError` traceback at exit 1, not the exit 2 its own docstring documents: no argparse, no manual checklist, and the exact incident state was the one state the incident tool could not survive. The underlying "never raises" promise was false throughout `scripts/utils/operator_identity.py`, so three more scripts (`admin-health.py`, `offboard-exec.py`, `provision-exec.py`) died the same way at import; `_resolve_file()` now absorbs the failure and falls back through the engine-local config and the shipped example, announcing the fall once per process. Separately, `log_security_event` ran `mkdir(parents=True)` against a retired sibling repository outside the workspace with no network or authentication required, which is how that directory reappeared on disk during the audit itself; both it and a companion function now refuse to create or clone that path. `scripts/md-to-docx-proposal.py` carried five rendering defects, including a body line with a pipe absorbed into a one-cell table whenever the next line contained `---` anywhere, and an escaped-pipe row that lost a real price column to the header's column-count guard. A health check that had verified nothing for months looked for a heading that has not existed in `CLAUDE.md` since before the engine/data split; redirected to the data-root's own overview file, it now examines 697 paths on the real overlay, 625 resolving and 72 not.

  `d2a97ba`

- **A PDF-reading dependency the project never declared quietly carried a core capability, so removing it would have broken 142 documents with nothing failing to warn.**

  `scripts/datastore-extract.py` imports pdfminer to build the extract companion that is the only way a PDF reaches `/recall`; measured 2026-09-02, pdfminer was declared nowhere in the project and had arrived transitively through a document-conversion package by way of pdfplumber. The extraction tests skip when the import is missing, so narrowing that dependency for a lighter install would have left the suite green over a capability that no longer existed, the same shape of breakage indistinguishable from health this workspace keeps finding elsewhere. It is pinned at the version already resolved, so nothing installs or moves, only the ownership of the decision changes; `requirements.txt` is re-exported to show the direct dependency. `tests/test_deps_declared.py` gains a guard, derived rather than hand-listed, asserting a named entrypoint may not import a library the project does not declare, proven in both directions by deleting the pin and watching the test fail.

  `88d8ce3`

- **A setup script documented itself as idempotent while its last line unconditionally overwrote the operator's live settings file, on a cron.**

  `scripts/setup-platform.sh` ended in an unconditional copy under a header reading "Safe to run multiple times (idempotent)," and a sync script invokes it from a cron whenever the template changes, so the loss was scheduled rather than accidental. Measured 2026-09-02 by comparing the live file against the template: one run would have discarded 29 permission entries and three per-instance top-level keys no template can carry, including the pointer at the private data overlay's auto-memory, whose loss would have silently redirected every future memory write. The fix merges rather than overwrites: a local value is kept, a template addition is added, permission lists are unioned, and a hook group the live file already defines is left alone; applied to the live file and verified, 8 top-level keys before and after, 73 permission entries growing to 76, gaining 3 and losing 0. Two more findings in the same file: a macOS settings template was maintained and tested but installed by no code path, since the Darwin branch used the Linux template under a comment claiming "same Python3 paths"; and the fix's own backup pattern was not gitignored, which would have left the operator's data-overlay path and permission grants sitting in an untracked file in this public repository.

  `8ee7187`

### Security

- **The personal-threads read guard named fifteen utilities and left out `cat`.**
  `.claude/hooks/_dispatch.py`. The pattern's own comment promised "any plain
  read utility" pointed at the personal subtree, and the alternation listed head,
  tail, sed, awk, base64, b64encode, xxd, od, strings, nl, fold, cut, less, more,
  grep and rg. The only two `cat` patterns beside it both require a redirect or a
  pipe to tee, which is exactly the case this pattern was added to close. So
  `head` on a personal thread was refused while the plainest read of the same
  file went straight into the transcript, from 2026-06-09 to 2026-08-27. Added
  `cat` with thirteen neighbours (tac, rev, sort, uniq, shuf, paste, pr, fmt,
  expand, unexpand, column, tr, hexdump), and wrote down in the module that this
  is a DENY-LIST which no list of names can complete. Two claims that hid the
  gap are corrected with it: `tests/security/test_dispatch_read_guard.py` said
  "the Bash branch already blocks the cat/grep equivalent", half true and read as
  whole, and `tests/test_protect_personal_threads_hook.py` hand-listed ten
  utilities with the same omission, so it agreed with the defect instead of
  catching it. That enumeration now derives its names from the compiled
  alternation, so a name added to the guard is exercised and a name dropped from
  it fails. Guard:
  `tests/security/test_a_read_guard_that_named_every_utility_but_one.py`.
  The structural fix, a default-deny on any Bash command naming that directory,
  is a decision for the operator and is not taken here.

#### Guards that reported success over something they never read

- **Six text-safety detectors that back the workspace's commit and push gates each answered "clean" over a narrower corpus than the question implied.**
  - `scripts/utils/content_denylist.py`'s `scan_text` matched line by line, so a real third-party name split by a hard wrap, a double space, or a non-breaking space passed both the pre-commit hook and the unbypassable push wall in `scripts/push-all.py`, in a repo whose engine half is public. A second pass, `_scan_wrapped`, now catches the wrapped case; measured against the whole engine, the widened scan is still clean, so the fix costs zero false positives.
  - `scripts/utils/sanitize_text.py`'s `INVISIBLE_CHARS` list was missing five of the ten Trojan Source bidi control characters, U+202A through U+202E, including the CVE-2021-42574 character.
  - The sanitizer CLI counted only deletions when reporting "already clean," so a file whose non-breaking space had just been REPLACED (not deleted) was reported clean immediately after being changed on disk.
  - `scripts/content-guard.py`'s degraded-mode branch tested `not data_root.is_dir()` before checking for `None`, so an unresolvable data root raised `AttributeError` instead of explaining why the scan was degraded.
  - `scripts/humanization-check.py`'s sentence-boundary regex required a capital letter after a stop, so any sentence starting with a lowercase word, digit, quote or backtick was silently glued to its predecessor and the burstiness reading was computed over the wrong units. Measured effect on the real audit corpus: +18 findings over 49 files once fixed.
  - `scripts/ste-check.py` treated an unreadable page as a finding rather than a read error, and in `--all` mode a single unreadable file killed the whole sweep partway through with a plausible-looking partial result.

  Guard: `tests/test_detectors_that_reported_clean_over_what_they_could_not_see.py` (36 tests). Mutations 22/22 caught, no survivors.

  `d3e7432`

- **`scripts/harness-audit.py` scans the workspace's own instruction surface for prompt injection, and two loaded surfaces, slash-command bodies and hooks, were never opened.** `.claude/commands/*.md` is injected as the prompt the instant an operator types the command, and `.claude/hooks/**/*.py` executes on every tool call; neither glob was in the scanned corpus. Measured 2026-08-29 with one identical payload planted in all six candidate surfaces: four were scanned and flagged, the command file and the hook file drew no finding and no note of any kind, and the tool printed "No injected instruction patterns" and exited 0.

  Six of the tool's nine `ALLOWED_REPO_PREFIXES` entries named paths no existing glob could ever produce, including the file that guards the vocabulary itself, `.claude/hooks/prompt-guard.py`, carve-outs from a scan that had never actually reached them. That specific allowance is deliberately not re-added even with the new glob in place: measured with it removed, the whole own-tree corpus, including that hook, produces zero findings, so exempting the file that runs on every tool call would reopen the hole it exists to close.

  Live corpus verified: own-tree scanned files went from 228 to 247 (2 commands, 17 hooks added), zero own-tree findings before and after.

  Pinned by `tests/test_an_audit_that_vouched_for_a_surface_it_never_read.py` (9 tests), with the surface list derived from module constants so a later addition is automatically required to be scanned. Mutation-verified: 12/12 caught, no survivors.

  `82af923`

- **`secret_scan` in `scripts/publish-service.py` built its file list from an unchecked `git ls-files`, and a failed call (empty stdout) parsed to an empty list, hitting an `if not files: return True` shortcut that reported the downstream mirror clean without ever running the scanner.** Measured on scratch trees at commit `579fbaf`: with the destination not a git repo and a 36-character `ghp_`-shaped token present, `secret_scan` returned `True` before the fix and `False` after; forcing `ls-files` to exit 128 with the same token produced the same wrong-then-right pattern. `publish()`'s companion check, `git status --porcelain`, had the identical shape: a 128 exit with empty stdout read as "nothing changed," returning exit 0 over a repo it could not read, after content had already been copied in. This reaches production through `main()`'s only guard, `(dest / ".git").exists()`, which a half-deleted clone or a broken gitfile can satisfy while every git call underneath exits 128. Both guards now check the exit code, not output emptiness; `secret_scan` returns `False` (mapped to exit 2), the status guard returns 1. New test file `tests/test_a_gate_that_reported_clean_over_an_empty_list.py`, 12 tests, both directions. Mutation-verified 10/10, zero survivors.

  `c557c22`

- **Two send-side security controls were each verified only against themselves, both surfaced by a whole-suite mutation run and confirmed live on 2026-08-29 before anything was changed.** Three tests in `tests/test_heading_skill.py` asserted `for entry in SEND_DENY: assert entry in disallowed` against a `disallowed` list built as `list(SEND_DENY)`, the same object on both sides, so the loop held for any value of the constant; measured, truncating `SEND_DENY` to 1 entry or to `[]` still passed those three tests. A sweep of the 201 scripts that call an outbound mail transport found `gmail-send.py` missing from `SEND_DENY` entirely, 21 days after it shipped; paired with `gmail-draft.py`'s caller-supplied `--to`, that was a complete two-Bash-call exfiltration path, leaving leg 3 of `.claude/rules/lethal-trifecta.md` open. One process hop further added `action-queue-execute.py` and `fireside-bot.py`, both of which can spawn `send-email.py` via subprocess without being denied. `SEND_DENY` grew from 4 entries to 10. Separately, `scripts/content-guard.py` opened with `if dl.degraded or not dl.tokens:`, a branch nothing exercised; rewriting the `or` to `and` with an empty overlay flipped the output from "denylist unavailable... skipped" (exit 0) to "content-guard: clean (1 file(s); 0 denylist tokens)" (exit 0), and 92 existing tests over the gate passed against that mutant. The decision moved out to a pure `denylist_verdict(degraded, token_count, root_state)` function with a case for every state, both directions asserted, and the clean line now carries a second, independent refusal for a zero-token denylist. Mutation-verified 26/26, zero survivors; 37 new tests, affected subset 638 to 675 passed.

  `6c95d54`

#### Tests that were green while measuring nothing

- **Four security controls in the test suite checked whether a string of text appeared somewhere, rather than asking the system the question the control claims to answer.** One control certified `.gitignore` coverage by substring-scanning the file; `!.env.example` contains the substring `.env`, so a negation line, which grants the OPPOSITE of what the control claims, satisfied it. Measured: deleting the real `.env` rules kept the test green while `git check-ignore .env` reported the file NOT ignored. A second control forbade one exact spelling of an except clause across a 1900-line module with no positive case at all, so deleting the differentiated error handling it existed to protect was also green. A third asserted that specific Trojan Source codepoints appear "somewhere in the sanitizer file," which cannot distinguish the live character tuple from a comment mentioning it. A fourth, the standing "never bypass the proxy" control, rested on an assertion about a constant while the only consumer of that constant is patched out of every test that exercises it, so dropping the base-URL argument entirely, sending every prompt and the subscription key to the vendor default, left the control green. All four are now rewritten against the AST or the actually-constructed client. Separately, the tool-call cap had blocked a legitimate 66-agent workflow at 1849 calls in 29 minutes; raised from 1200 to 4000 on operator instruction, and the block message's override hint (which had said 2000, a value the raise would have made a downgrade) now derives from the same constant. Mutations: 11/11 caught. Suite: 13434 passed, 1 skipped; security test count 412 to 441.

  `457221d`

- **Three separate controls asserted against a restatement of the code under test rather than the code itself, each hiding a live defect.**

  - `tests/bridge/test_watcher_covers_what_it_claims.py` claimed to hold the bridge daemon's one-write-to-many-components fan-out but ran the fan-out logic in its own test body and never imported `_Handler`. Truncating the real comprehension in `scripts/bridge_daemon/watcher.py:161` left the full test file and all of `tests/bridge` passing (1210 tests), while against the live handler the same mutation dropped a component from the fan-out, meaning a document write would bump the Pulse in-flight count while the Studio page stayed stale.
  - `.claude/hooks/prompt-guard.py:147` read `input_data.get("tool_input", {})`, which returns the stored value (not the default) when the key is present. Measured with real payloads of `null`, `[]`, and `"x"`: each exited 1 with an uncaught `AttributeError` before the injection scanner ever ran, missed by an earlier sweep that fixed the same guard on this file's two neighbors.
  - `tests/test_subprocess_interpreter_guard.py` filtered its filesystem walk with a hand-written directory-name denylist that did not know about `.claude/worktrees/`, so agents working in isolated worktrees inside the repository put scratch files in front of the guard and the suite failed on code that was not part of the repository.

  Pinned by `tests/test_a_test_that_asserted_against_its_own_loop.py` (28 tests), driving the real `_Handler.on_any_event`. Mutation-verified: 11/11 caught, with one measured-equivalent mutation (`--no-index` on `check-ignore`) replaced by an observable one.

  `6b24d42`

- **`scripts/fireside_webhook.py` receives Telegram's POSTs on a public URL with a secret-token header as the only authorization, and four tests claiming to hold its malformed-body guards never executed the module.** Two restated the guard expression inside the test body and asserted on the restatement (e.g. `(msg.get("from") or {}).get("username", "?") == "?"`), which is a true statement about `dict.get` in every codebase, not a test of the guard. Measured by removing both real guards and driving the actual endpoint: with the guards present, all four malformed-body cases returned 200; with them removed, three returned 500 while the two "tests" still passed unchanged.

  A third test read the module's source text and counted `%` conversions in a log format string while its own docstring said the format string and its five arguments "have to be counted together"; it counted one side, so deleting an argument survives it and `logging` raises inside `emit` at runtime, silently dropping the log line. A fourth asserted two comment phrases appear in the source, which stays true when the code beneath it changes. All four are replaced by tests building the real app and posting through `ASGITransport`.

  Two mutation survivors on the first pass were each a real gap: the sibling `callback_query` branch had the identical three guards, written the same way, with no test at all; and `_drain`'s wait was decoration around a 50-turn spin that was doing the actual work, now replaced with awaiting the background task directly.

  Mutation-verified: 20/20 caught, no survivors, fifteen against the module including an argument-drop the per-cent-sign count cannot see, an acked failing update, a rewound offset, an unchecked secret token, and an unscheduled background task.

  `5b15b34`

- **`scripts/utils/air_gap.is_denied`, the predicate deciding what the memory index, chronicle, `/odin collect`, and the commit/symbol-source readers may read, had its traversal-handling logic (the collapse-before-check, fail-closed-on-escape, and pure-no-filesystem contract) exercised by zero of 262 existing assertions.** Measured 2026-08-29: dropping the `norm == ".."` traversal check, deleting the traversal branch entirely, comparing the raw un-normalised path, and dropping the `norm == "."` root guard all survived, 0 of 4 mutations caught. An audit shard's claim that `is_denied("..")` returns `False` is recorded as wrong and refuted with a table of correct `True`/`False` verdicts across ten inputs. Widening coverage to twelve cases found two more real gaps: the first of two `replace("\\", "/")` calls is load-bearing only for a backslash path that needs collapsing (`threads\business\..\..\_secure\x.md` reaches `normpath` as one opaque segment and is wrongly allowed without it), and the `norm == "."` early return was dead under hard-coded denies alone until driven with a caller config where the workspace root must stay readable regardless of that config. No production behaviour changed; the predicate was correct and unpinned. Mutation-verified 12/12, zero survivors.

  `3d44d16`

#### A fix that landed in one of N copies, and the modules that ended the copying

- **Seven security and quality guards each covered one spelling of a thing and missed the neighbouring spelling of the same thing, one of them open for eleven weeks.**
  - `session-start.py` built a careful "CRM HEALTH CHECK DID NOT RUN ... Overdue contacts are UNKNOWN, not zero" string, then handed it to a caller that read only `len()` of the list, so a failed `crm-health.py` run rendered "CRM ALERT: 1 contact(s) need attention today," identical to a session with exactly one genuinely overdue contact.
  - `_blocking_wait`'s poll-loop regex ran over the raw shell command while the sleep half of the same function used the quote-aware `_shell_segments`, so a quoted shell keyword followed by a short sleep was policy-denied by a message that promises short sleeps go through.
  - `_pytest_argv` caught `uv run python -m pytest tests/` but not the shorter spelling of the same command, in this repo's own canonical toolchain.
  - `check_protect_personal_threads` refused Bash-based reads of the CEO-only personal thread subtree but returned `None` for the native Grep and Glob tools, which `_dispatch.py` was not even registered for, open since 2026-06-09.
  - `check_protect_docs` gated on a substring requiring a separator before a synced doc's directory name, so the plainest repo-relative spelling passed unblocked while the dot-slash spelling was caught; it also compared `os.path.basename` of the raw path against the normalised path, so a Windows-spelled path on this Linux host passed the directory test and matched nothing in `SYNCED_FILES`.
  - `check_tool_budget`'s docstring claimed to count every tool invocation; it counts only the invocations the dispatcher is registered for, so WebFetch, Task and the MCP tools never reach it. Narrowed to match what it measures.

  Mutations 35/35 caught, one recorded equivalent.

  `d4ef287`

- **Eleven guards in the Tribe fireside bot each applied a rule that was written once to only one of the two or three places that needed it.** `scripts/fireside-bot.py` is 4,722 lines with one test reference per 224 lines, the thinnest-covered file in the engine.
  - `cmd_set_webhook` is the one Telegram call that bypasses `TelegramBot._call` (uploading a webhook certificate needs multipart, not JSON) and built its own request URL, so a connection reset or TLS failure could raise an exception whose message quotes the bot token in the clear, breaking the redaction `_call`'s docstring promises everywhere else.
  - `cmd_stats` wrote outside `require_writable_state_dir`, the funnel every other fireside write goes through by 2026-08-26 operator law; on a clone with no data overlay it wrote real Tribe speaker names into `examples/outputs/...` inside the repository that gets pushed.
  - `cmd_log_session` saved a mutated `schedule.json` back to disk outside `locked_state`, so an accepted swap taken by the webhook daemon between its read and write could be silently overwritten by the stale pre-swap copy, while the swap-request log and both members still recorded the acceptance as final.
  - `cmd_email_backup`'s tally counted `sent`, `skipped`, `already`, `no_email` and `not_in_roster` but incremented nothing on a send that ran and failed, so eight bounced emails printed `email-backup: sent=0 skipped=0`, identical to a healthy Sunday with nobody due.
  - The cycle-progress line hardcoded "week X of 9" while the cycle length is data read from `fireside-schedule.json`'s `weeks` array, so a ten-week cycle reported "week 10 of 9."
  - The DM-delivery filter listed a `helmsman_brief` type that no code path ever writes to the log, so the reported percentage spoke for a set it never contained.
  - The DM nudge telling the CEO a Helmsman slot was still empty passed `on_or_after=today`, dropping the current week the moment its Monday had passed, so it fired Monday and went silent Tuesday and Wednesday, the two days the empty slot mattered most.
  - `_handle_cycle_invite_tap` swallowed a pin failure with a bare `pass` and then rewrote the CEO's approval card to "Sent to the Tribe and pinned." unconditionally.
  - `cmd_dayof_reminders` was the only one of three send loops with no `_dm_already_sent` check, so a cron double-fire or manual rerun sent every speaker the same Zoom link twice.
  - The opt-in reaction handler checked only that a Telegram user id matched a roster row, not that the member was still active, so an offboarded Tribe member with `active: false` could still opt in, join the Helmsman rota, and be counted.
  - `cmd_health_check` alerted on a dm-log.jsonl that existed but held no tick, yet returned silently on a log that did not exist at all, the strictly stronger case for "no liveness tick."

  Mutations 24/24 caught after one honest re-run: the first pass reported 22/22 because a mutation that skipped a directory-creation guard left the directory on disk, which then made every mutation after it fail for the wrong reason.

  `7b9f081`

- **A behaviour-keyed sweep found ten frontmatter-shaped regexes disagreeing with the shared grammar; eight were fixed here, with measured consequences ranging from a silent security-wall bypass to a false compliance gate.**

  - `scripts/utils/content_denylist.py`, which feeds the leak wall deciding what may enter this public repository, dropped a CRM record's tokens entirely on a spaced fence. Measured: 5 denylist tokens for a canonical fence versus 2 for the same record with `"--- "`, meaning the wall would then print clean over engine prose naming the missing tokens.
  - `scripts/dev/build-plugins.py` reintroduced a defect its own comments describe: `yaml.safe_load` raised `ParserError` on a spaced or tabbed fence during plugin bundling.
  - `scripts/context-floor-audit.py` dropped an unparseable skill from the measured byte total entirely, the one direction a growth gate must never fail in, and separately mis-classified a path-scoped rule as always-on.
  - `scripts/utils/canopus_note.py` accepted trailing whitespace on the closing fence and not the opening one; `canopus_check.py` aborts its whole check on one refused note.
  - `scripts/humanization-check.py` let frontmatter survive its strip and audited a `title:` line as prose, producing false banned-vocabulary findings.

  Two sites (`scripts/merge-contacts.py`, `scripts/odin_pagerank.py`) diverge only on CRLF input, which no reader in the set can receive because all ten decode via universal-newline mode, proven by an AST test walking all ten files rather than asserted in prose. A new `split_frontmatter_raw` supports the one caller (build-plugins) that must rewrite one half of a document and re-emit the file byte-identically.

  Pinned by `tests/test_ten_regexes_that_spelled_the_fence_themselves.py` (44 tests). Mutation-verified: 15/15 caught after a first run of 14/15, the survivor a skipped-skill warning asserted on a return value instead of stderr.

  `0ca611c`

- **`push-all.py`'s content scanner refused an entire backup over a JWT-shaped bearer token embedded in a tracking-pixel URL that the email sync had copied verbatim into an archived inbox digest.** Measured 2026-08-29 via `--dry-run`: the whole run was blocked, both repositories, by one archived line. The URL had no image extension, so "strip image URLs" was the obvious and wrong fix, since a list of dangerous query parameters is always one entry short.

  Four sites, three copies of the same three-line body-extraction logic, each writing an unredacted body into the data overlay: `sync-exchange` (mail and calendar), `sentinel` (mail and invites), and `email-intelligence`. Fixing only the one that tripped the wall would leave the next push refused by one of the others. Extraction is unified into `html_text.email_body_text`, and every extracted body now goes through `secret_patterns.redact`, the same table `secret-scanner.py` reads, computing spans on the original text.

  A test recorded that `strip_html` alone would have hidden the exposure: it keeps only character data, so a URL inside an `href` never reaches the archive, and the actual token arrived through the text/plain alternative. The blocked artifact (288 bytes, one line) was cleaned in place with the same `redact`.

  Pinned by `tests/test_an_archive_that_stored_the_token_it_was_shown.py` (26 tests). Mutation-verified: 11/11 caught after a first run of 8/11, with three survivors all one gap: tests exercised the imported name rather than the call site, closed with two AST-level rules.

  `579fbaf`

#### The release, push, and secret walls

- **The unbypassable push wall between an engine file and the public remote silently skipped any file it could not decode as UTF-8, reporting the push clean over content nobody had read, while twenty lines above it the same function refused outright when its denylist degraded for exactly that stated reason.** The sibling CLI, `scripts/content-guard.py`, had already fixed the identical defect eleven days earlier, but the fix never reached the push wall, because each gate carried its own private copy of the same file-selection logic. The selector both gates use now lives once, in `scripts/utils/engine_guard.py` (`engine_text_files` plus `BINARY_SUFFIXES`); a genuine binary asset is still skipped deliberately by suffix, since refusing on those would block every push, and anything else that fails to decode is now recorded, logged as a denial, and refused. One adjacent test had been checking a hardcoded literal (`".bin"`) in the CLI's source rather than asking the selector, so it broke on the move despite no behavior change, and is now fixed to ask the selector directly. A new lockstep test runs both gates over the same file and requires the same verdict from both, which is the exact assertion that would have caught the eleven-day gap; an AST check now refuses a second copy of either function from ever being written again. Mutations: 12/12 caught. Suite: 13506 passed, 1 skipped; ratchet 265/271; content-guard clean over 760 tokens.

  `bec4ed3`

- **Two push-time secret walls checked git's tracked-file list at the wrong moment, so a credential this run was about to commit was never tested by either wall.** `scripts/push-all.py`'s secret-filename wall (step 1) read `git ls-files`, which reports only what git already tracks; step 3 then runs `git add -A`, making untracked files tracked, and the wall never ran again. Three layers all had to miss it, and did: `.gitignore` lists `.sessions/` and one exact `outputs/browser/cookies.json` rather than a bare `*.session` rule, and `scripts/secret-scanner.py` lists `.session` in `SKIP_EXTENSIONS`, so a `telegram.session` dropped at the repo root walked through all of it. Both step 1 and step 2 now read `repo_carried_paths`, the same resolver the routing wall at step 0 already used.
  - The secret-content scan ran at step 3.5, after this script's own `git add -A && git commit`. The push was still refused, so nothing left the machine, but the secret was already in local history, turning the repair into a history scrub instead of an edit. Moved to step 0a, for every repo.

  Guard: `tests/test_two_walls_that_looked_at_the_wrong_moment.py` (18 cases). Mutations 7/7 caught after one survivor traced to a test that grepped the source instead of driving `push_repo`.

  `aba8a99`

- **`scripts/utils/git_push.py` runs every git call as `git -C <path> ...`, and git walks up from that path to the enclosing repository, so nothing in the module ever checked that the path it was handed was a repository root.** Measured 2026-08-28 against a bare engine clone: `ahead_behind` returned the identical `(0, 20)` whether asked about the repo root, `examples/`, or `scripts/utils/`, because all three questions were answered about the enclosing repository. `supervised_push`'s postcondition is `ahead_behind(...) == (0, 0)`, so a subdirectory handed to it pushed and verified its parent, reporting a verified push of a repository it was never given.

  On a clone with no private data overlay, `get_data_root()` resolves to `<engine>/examples`, a demo directory. Measured on such a clone: `safe-push --repo all` sent that directory through the whole pipeline, the leak wall never scanned it, and `git -C` resolved it to the engine's own remote, producing a refusal message that named a private-content leak that did not exist. The fix is `enclosing_repo_root`, refusing only on positive evidence that a path is not a repository root, plus `safe-push` naming the real cause (no repository at the data root) before the walls run.

  Pinned by `tests/test_a_push_that_verified_the_wrong_repository.py` (25 tests), all against local bare remotes. Mutation-verified: 15/15 caught after a first run of 14/17, with one real gap (a relative-path caller comparing unequal to its own absolute form and being falsely refused) and two mutations removed as proven equivalent.

  `57e2ce3`

- **`sanitize-check.py --staged` asked git's index which files are staged and then opened those paths in the working tree, so it scanned the wrong bytes in both directions.** Measured 2026-08-29: with a term staged and then cleaned from the working tree without re-staging, the gate printed "No critical terms found", exit 0, while `git show :post.md` still held the term and the commit would ship it. With the reverse (clean index, term only in an uncommitted scratch edit), the gate refused a commit that would never have contained it. The fail-open is the one that matters: this gate exists to stop a compliance term reaching the corporate fleet, and it cleared a publish over content it never read.

  The fix is `staged_blob()`, reading bytes via `git cat-file blob :<path>` so the gate reads what git is about to write. A parallel decoding bug in `staged_files` used `text=True` with no encoding, so a non-ASCII staged path decoded via `locale.getpreferredencoding()` could vanish entirely on a cp1252 console; fixed with `os.fsdecode`. A separate, superseded standalone pre-commit hook with the same shape was measured live in a scratch repository (an AWS-shaped key staged and cleaned passed "No secrets detected") and, since it has no stash to fall back on, is changed to refuse rather than scan the wrong version.

  Pinned by `tests/test_a_gate_that_named_one_file_and_read_another.py` (18 tests) plus one new case elsewhere. Mutation-verified: 13/13 caught, no survivors, re-run on the final files.

  `409db1c`

- **`scripts/utils/git_push.py`'s remote-identity wall, which stops the private data overlay reaching the public engine repository, asked git for the push URL in one environment and then ran `git push` in a different one, and a git URL rewrite lives in the environment.** Measured 2026-08-29 against a local bare repository standing in for the engine remote: a `url.<base>.insteadOf` rewrite present only in the push environment left the wall silent, the postcondition satisfied, and the commit landed on the wrong remote while the intended one received nothing. The reverse (rewrite present only in the ambient environment) refused a push that was entirely safe.

  An initial diagnosis blamed the git command itself and was wrong: measured across the full matrix, `git remote get-url --push` correctly sees `pushurl`, `insteadOf`, and `pushInsteadOf` together and agrees with `git push --dry-run` in every case. The fix resolves the child environment once, at the top of `supervised_push`, before any wall runs, rather than denylisting individual `GIT_*` variables, which this audit repeatedly finds is always one variable short. `push-all.py` runs the same wall a second time as a precondition and had the same env mismatch, a fix that had landed in one of two copies.

  Pinned by `tests/test_a_wall_that_looked_at_a_different_world.py` (22 tests), driving real repositories and asserting where the commit actually landed. Mutation-verified: 15/15 caught after a first run found three real gaps, including an empty remote set produced by resolving `git remote` in the wrong environment.

  `079a59c`

- **Every gate in `push-all.py` decided what to scan from the present state of the clone (working tree, index, a two-endpoint diff) and read those bytes off disk, but a push ships the objects the commits carry, not the present state.** Measured 2026-08-29 on a real repository with a real bare remote: a secret committed with `--no-verify` and then wiped from the working tree passed the scan ("No secrets detected", exit 0) while the push still shipped the commit that added it; and a secret added in commit A and removed in commit B, both unpushed, netted to nothing in the two-endpoint diff and was never even listed, so the push still shipped commit A. A control (the same secret sitting in the working tree) was correctly refused.

  Three walls shared the blind spot: `content_scan` (secrets, reading worktree bytes), `engine_clean_scan` (routing, via `git ls-files`), and `engine_content_scan` (real names, via `Path.read_text()`). The routing wall's gap reproduces the shape of an earlier real leak exactly: commit a private file, notice, `git rm` it, commit again, push, and both commits reach the remote while the working tree looks clean. The fix is a new primitive, `scripts/utils/push_history.py`, laying out every blob a push would actually send via `git rev-list <base>..HEAD | git diff-tree --stdin -r -z -m --root --no-renames --no-commit-id` into a scratch tree the existing scanners can read as files.

  Pinned by `tests/test_a_wall_that_read_the_present_and_shipped_the_past.py` (52 tests), all against real clones with real bare remotes. Mutation-verified: 19/19 caught after three earlier runs each found a real gap, including a `git rev-list --objects` primitive that prints each object with only one of its paths, hiding a private duplicate of a public file.

  `72fe37f`

- **`build-plugins.py` validated a plugin bundle's `hooks:` list and never checked `hook_events`, the field that generates `hooks.json`, so a hook could be wired into a consumer's `hooks.json` without ever being copied into the bundle, and the build stayed green.** Measured through the real builder: a wired-but-not-copied hook left `completeness_gate` and `manifest_sources` both empty while `hooks.json` still listed it as wired, and the same held for a pure typo in the hook filename, which built green pointing at a file nowhere in the repository. `publish-marketplace.yml` ships on every push to main with no human in the path, and the two hooks caught in the first case were the sovereignty guards, so a one-character typo in `hook_events` would have silently shipped installers a declared but non-functional PostToolUse guard.

  The fix checks existence before membership and instead of it, since the two failures need different remediation messages, and the rule is deliberately one-directional: `hooks:` may still list a hook `hook_events` does not wire (`checkpoint-statusline.py` legitimately does this, since Claude Code has no manifest key for statusLine hooks).

  Verified: `build-plugins.py --all` builds all five real bundles and writes `marketplace.json`, exit 0. Mutation-verified: 14/14 caught, no survivors, including two mutations against the real `config/plugin-bundles.yaml` itself.

  `978cbe1`

#### The engine/data boundary

- **With no private data overlay present, `get_data_root()` falls back to `examples/` inside the public engine clone itself, so any tool writing to the data root writes straight into the repository that gets pushed, and no gate caught it.** `config/routing-map.yaml` carries no entry for `examples/`, so every path there defaulted to `engine` classification; measured that day, planted files under `examples/crm/contacts/`, `examples/state/`, and `examples/outputs/operations/` all cleared the unbypassable push wall. Per-path routing rules could not fix this, since the seven legitimate demo files sit at the same prefixes as any leak would. The fix is a closed manifest (`DEMO_MANIFEST` in `scripts/utils/engine_guard.py`) checked before the routing rule: anything under `examples/` not on the seven-file list is a data artifact regardless of its route. Four real write paths were live through the hole, all found by running the suite on a clone with no overlay: `scripts/utils/crm_autolog.py` (counterparty e-mail addresses), `scripts/archive-transcripts.py` (whole session transcripts, personal threads included), `scripts/utils/observability_safe.py` (raw e-mail bodies, subjects, senders), and `scripts/inbox_pulse/cost.py` (the dated LLM spend ledger). Each now resolves through a data-root guard and refuses rather than writes. No-overlay suite failures went from 82 to 13, eleven of which were pre-existing environment noise. Local suite: 13198 passed, 1 skipped; ratchet 265, thirty below baseline; hidden characters clean on all 33 files.

  `593d6b4`

- **Four more writers were found putting operator state into the public engine repository, all located by running the suite on a clone with no private data overlay rather than by reading source.** `checkpoint_paths.py`'s `handoff_dir()` took the data seam whenever `is_engine_tree()` said yes, and that function answers yes on a public clone too, so whole session handoffs landed under `examples/`. `.claude/hooks/checkpoint-save.py` re-implemented the same broken branch by hand instead of calling the resolver, so fixing the resolver alone changed nothing there. `scripts/fireside-bot.py` had five writers sharing a `STATE_DIR`, four with their own `mkdir(parents=True)`, now consolidated to one funnel. `capture-design-exemplars{,-retry}.py` did the `mkdir` at module level, so merely importing the module wrote into the clone with no call and no CLI run. The first version of the guard asked whether the machine had an overlay, an environment fact rather than a path fact, and broke the fireside/capture test suites that legitimately redirect a module-level directory constant to `tmp_path`: no-overlay failures went from 13 to 63 before the guard was rewritten to ask about the path itself (`require_outside_engine_clone`). No-overlay suite failures dropped from 82 (before this pair of commits) to 11, all pre-existing noise; `test_engine_tree_clean` passed there for the first time. Mutation harness 18/18 caught. Local suite: 13215 passed, 1 skipped.

  `b766200`

- **A test fixture in this public repository quoted the filename of a real competitor's private commercial proposal, disclosing both the competitor's identity and that the operator holds its document.**

  The filename lived only as an example of "a filename that reads like a sentence"; nothing could have caught it, because the leak-guard script grades file paths against routing rules and never inspects file contents, and the push-time scanner looks only for secrets, not for a public file spelling something that exists solely inside the private tree. The operator's ruling that day widened the standing policy: everything under the datastore directory is private and must never be public, contents and filenames alike, and shared-to-executives routing means shared through private repositories, never publishable. Removed and replaced with invented examples: the competitor document title (twice, the first repair changed only the vendor name and left the real title intact, which the new guard then caught), a real CRM tag used as a schema example, a dated deck filename repeated across five files, and a real social-post title with a real archive date. The guard that holds this slides a five-word window over both the datastore's real filenames and the tree's text, chosen over two designs measured and discarded first: single-word matching flagged ordinary engineering vocabulary hundreds of times, and whole-filename matching missed the founding case because the leaked title had been truncated. Fifteen brand filenames that engine code loads by name are now resolved through a manifest in the private overlay, invented keys in the engine and real names in the data repo, with no fallback filename and no silent default.

  Full suite: 22931 passed, 1 skipped. A second exposure was caught and rewritten before this fix was ever pushed, when the guard's own first draft quoted the leaked title while explaining the incident it was written to prevent.

  `9afe2a4`

#### Paths, filenames, and tree sweeps

- **`docparse` and `firecrawl` each cache derived private content, and neither asked where it was allowed to write, in opposite directions.** `docparse` cached full extracted text under `<workspace_root>/.cache/docparse`, inside the public engine clone; measured 2026-08-28, five parsed documents sat there. It was gitignored so never leaked, but sat in a tree the content wall's `--exclude-standard` scan cannot see. `firecrawl` cached scraped pages under `get_outputs_dir()`, correct only when a data overlay exists; with none, `get_data_root()` resolves to the bundled `examples/` demo tree, which `engine_guard.py` treats as a closed manifest. Measured on a clone with no overlay: one cached scrape wrote `examples/outputs/browser/firecrawl-cache/<key>.json`, uncovered by any gitignore rule, and `scan_engine_repo` flagged it, so both the pre-commit and push walls would refuse from that moment on, over a directory nothing told the operator about.

  The fix is `paths.private_cache_dir`: cache beside the data overlay when one exists, otherwise under the workspace root, never under a data root that might actually be the demo tree.

  Pinned by `tests/test_two_caches_that_wrote_where_nothing_may_write.py` (18 tests). Mutation-verified: 14/14 caught after a first run of 12/15, with two real gaps (the `.gitignore` rules mattered for `git add -A` staging even though the scanner routed either way).

  `5668c5b`

- **A guard restricting tool access to sensitive business-thread files tested whether a tool-call argument spelled the literal directory name, and a wildcard never spells it.** Measured against the real check 2026-08-29: seven of thirteen verdicts were wrong, allowing several `Glob`/`Grep` call shapes that legitimately returned protected filenames or matching lines from protected bodies. The check now builds the expression the tool will actually expand and asks where that can land, refusing only a literal path segment naming the protected directory so ordinary sweeps like `Glob("**/*.py")` still work.

  Separately, an archived subtree one directory deeper was invisible to the wall because the directory-matching pattern did not reach it; measured, `cp` of a file in that archived subtree was refused while `cat`, `head`, and `Read` of the same file went through, because two of several matching patterns had a hand-copied archive clause and the rest did not.

  Pinned by a new test file (103 tests), including a vacuity requirement that at least eight refusal cases never write the directory name literally. Mutation-verified: 18/18 caught after three real gaps on the first run, including a case-sensitive anchor and two untested archived-path write-content cases.

  `08db85c`

- **The CEO-only threads access wall matched a raw tool argument against a literal two-segment path pattern, so a dot segment, doubled separator, or `..` segment (each naming the same file) produced different verdicts.** Measured 2026-08-29 driving the real hook: 4 of 9 spellings naming one file were allowed, across Read, Bash, and the write-content leak check. A second hook, `data-path-redirect.py`, completed the bypass by normalising the same spellings and rewriting the call onto the real file under the data root; its own private normaliser had already been fixed once (2026-08-23) after a climbing path was found composing onto the data root, but that fix landed in only one of the two hooks that needed it. It now lives once in `scripts/utils/pathnorm.py`, imported by both, at roughly 0.9ms of a 55ms hook. A second face of the same wall had an unanchored wildcard branch that let a sweep rooted one directory above the tree through (measured 3 of 4 allowed, including a Grep rooted at the data root); its justifying assumption is now checked rather than assumed. All 13 measured verdicts are now correct (9 refused, 4 ordinary actions still allowed). Mutation-verified 31/31; eight survivors across three runs included three real defects in the fix itself (per-field normalisation composing a climb onto nothing, a redundant second answer to a question `get_data_root()` already answers, and a wiring gap where every test called the new function directly and none drove it through the hook).

  `d32ec82`

#### Clocks, timezones, and concurrency

- **Both the graph-first wall and the fan-out wall keyed their per-session state on `session_id` alone, but a dispatched agent's PreToolUse events carry the same `session_id` and `transcript_path` as the dispatching session, distinguished only by an `agent_id` the main session never carries.** Measured on live payloads: 36 hook calls arrived in 25 seconds during a five-agent dispatch, 2 of them the dispatching session's own, all sharing one budget and one unlock marker. This produced two opposite failures: the graph wall was silently unlocked by any subagent that called `codegraph_explore`, so the dispatching session never had to ask the graph itself, reopening the hole `176be4b` had closed that same morning; the fan-out wall was tripped by the agents' own reading filling the parent session's budget, refusing the dispatching session for having correctly fanned out, three minutes after five agents were dispatched. A third defect surfaced while fixing the second: the dispatcher ran every check on every matched tool, so the fan-out wall was also refusing `Write` and `Edit` calls whose paths it never even counts, blocking the repair along with the investigation once the budget ran out. The fix introduces `actor_id` as the discriminator: the graph marker keys per (session, actor) so each actor must ask the graph for itself, and the fan-out wall now skips agents entirely (an agent dispatching is itself the fan-out, and nesting further is what `skill-orchestrator` principle 8 forbids), with its marker also keyed by actor so deleting the early-return skip cannot let an agent spend the session's budget. 21 new cases across both suites, mutation-verified 13/13, 205 passed on the adjacent set.

  `a8a2db4`

#### Standing instructions given a mechanism

- **Asked to confirm the graph-first wall (from `176be4b`) could no longer be broken, the honest answer was five ways, four measured live against the armed wall in the same session that wrote it.** (1) `Read` was not covered by the predicate at all, and a test file had pinned `Read scripts/sentinel.py` as correct not-a-search behaviour; measured, a fresh session refused `Grep scripts/` but allowed `Read scripts/sentinel.py`, walking around the whole rule in one call. (2) Five shell readers (`sed`, `awk`, `cat`, `head`, `tail`) that open a source file without "searching" it were uncovered; earlier in the same session, `sed -n '1,40p'` on a source file went unseen by the wall. (3) `find` was named in a code comment as covered and was absent from the actual tuple. (4) The wall yielded after three refusals in a session, so it could simply be waited out; measured, refusals 1 through 3 denied and refusal 4 allowed, directly contradicting the operator's instruction that an absolute rule holds unconditionally. (5) The unlock matcher lived only in the gitignored, machine-local settings file, absent from all three tracked platform templates, so on any other machine the wall was a cage rather than a wall, which is exactly why the three-refusal hatch had existed. All five are closed: `Read` and the five shell readers are covered, `find` is genuinely in the tuple, the refusal-counter hatch is deleted rather than raised, and the unlock door is now doubled (the MCP matcher lives in all three tracked templates, and `codegraph explore` run via Bash is a second, independent unlock path that does not depend on any config file). Mutation-verified 15/15 after one survivor closed with both directions rather than a weaker mutation; 57 tests pass, and the 485-test security suite passes.

  `634864a`

#### Injection vocabulary, denylists, and credential handling

- **A browser cookie reader decrypted every cookie of every import into garbage bytes, matched hostnames through an unescaped SQL wildcard, and reported a file permission it never actually set.** Since Chromium schema version 24, the decrypted plaintext of a v10/v11 cookie begins with a 32-byte SHA-256 host hash ahead of the value; this reader never checked the schema version or stripped that prefix, and `errors="replace"` turned the 32 binary bytes into replacement characters instead of raising, so the value looked like a working string at every layer above it. Measured on a synthetic blob: an 18-character token came back as 48 characters. Measured against the machine's own real Brave profile (read-only, no cookie decrypted): `meta.version = 24`, 130 cookies, all v10, meaning every import of that profile was affected. The identical defect existed in `scripts/utils/firefox_cookies.py`, which `scripts/linkedin-activity.py` runs against LinkedIn.
  - Neither reader escaped `%` or `_` in the domain before using it as a SQLite `LIKE` pattern. Measured against a real table: a query for `my_site.com` also matched `.myXsite.com`, and a query for `%.com` matched every row including `.evil.com`; the domain comes from the operator's own `/setup-browser-cookies <domain>` argument and the matched rows are sent to the target site. The correctly escaped form already existed, unused, ninety lines below the broken one in `chromium_cookies.py`, and correctly in `scripts/firecrawl.py` and `scripts/osint-advanced-sync.py`.
  - Both readers flattened rows to `{name: value}` over a query with no `ORDER BY`, so a name collision (`li_at` scoped to `.linkedin.com` versus `www.linkedin.com`) picked whichever row the table scan returned last, decided by nothing but insertion order.
  - `--out` printed "mode 0600" while an existing store on disk stayed 0644, because `os.open`'s mode argument only applies when `O_CREAT` actually creates the file; measured before and after, 0o644 both times over live session tokens. It also wrote in place with `O_TRUNC` rather than atomically.
  - A partial decryption failure (the normal case on a Chrome M127+ profile with mixed v10/v20 blobs) replaced a working cookie store with an incomplete one and exited 0; only a total failure was caught before.

  Both SQL patterns and the row-selection rule now live once in `scripts/utils/cookie_domains.py`. Mutations 39/39 caught after a first run of 33/39 surfaced three genuine gaps in the new tests.

  `e601bd7`

- **`scripts/scrutinize-dispatch.py`'s shell-injection detector compared tokens against an enumerated set of shell operators, and `shlex` returns a run of adjacent punctuation as one combined token no single-character entry in the set matched.** Measured 2026-08-29: `--cmd "/bin/cat /shard58-no-such-file 2>&1"` produced the token `'2>&1'`, which matched nothing in `SHELL_OPERATORS`, so the redirect reached the child, `cat` failed for an unrelated reason, and the module recorded "REPRODUCED" as if the guard's own claim had been proven. `&>`, `|&`, `>|`, `<>`, and `2>>&1` are the same shape. The check is now structural: any token made only of punctuation characters is an operator, whatever it spells.

  Four more findings in the same module, none in the report that opened the shard:

  - A default `#` commenter stopped the guard's lexer early while `shlex.split` (building the real argv) uses none, so a pipe after a `#` was never seen by the check.
  - `posix=True` stripped quote characters the guard's own docstring claimed it kept, so `grep -c ';' /etc/hostname`, a legal command, was refused as if the `;` were unquoted.
  - `text=True` with no `errors=` raised `UnicodeDecodeError` inside `subprocess.run` on any command printing non-UTF-8 bytes, leaving no run record at all.
  - An unbalanced quote in `--cmd` raised uncaught out of `main`, with no degraded record; it now exits 4 with a `degraded` row.

  Separately, the `k3`-produced report that opened this shard claimed `append_row` performs no verdict validation; measured, it does validate and raises, so a misspelled verdict was a lost-record defect, not the claimed fabricated-record one.

  Pinned by `tests/test_a_guard_that_named_its_operators_one_at_a_time.py` (49 tests). Mutation-verified: 17/17 caught, no survivors, re-run on final files, after two real gaps on the first pass.

  `6c7e4fe`

- **The only test covering `scripts/utils/injection_patterns.py`'s prompt-injection vocabulary asserted `len(INJECTION_PATTERNS) >= 8` over a table of 13, a floor rather than coverage, which does not say which patterns are actually exercised.** Measured 2026-08-29 by deleting each of the 13 patterns in turn and running the twelve dependent test files: only 4 of 13 deletions were caught (`ignore-previous-instructions`, `you-are-now-a`, `reveal-your-system-prompt`, `</system>`), all four caught incidentally by two security test files that happened to quote them. The nine survivors included `disregard-all-previous`, `override-system-prompt`, `pretend-to-be`, `[SYSTEM]`, `[INST]`, `<<SYS>>`, and the invisible-unicode class. A control run, deleting `[SYSTEM]` against the whole 15,889-test suite, produced the identical 32 pre-existing failures, confirming the gap was not masked elsewhere. Verification also surfaced an unrecorded, unpinned decision: nine patterns carry `re.I` and four literal-spelling markers (`[SYSTEM]`, `[INST]`, `<<SYS>>`, the unicode class) deliberately do not, a split nothing had asserted. The new test gives each of the 13 patterns a positive sample only it matches, exercised through the public `scan_content` entry point, plus a near-miss negative. Mutation-verified 21/21, zero survivors.

  `8c35e9d`

## [0.13.0] - 2026-08-22

The release about the commit log becoming searchable by meaning, and about the
thing that does the searching being made to say which model computed what. 1,093
commit messages carried the reasoning behind every decision here and were findable
only by exact substring; they now answer paraphrased and cross-language questions
at 85% against an 80% bar agreed before the build. Behind that, the store gained
provenance - model, host, and the digest of the weights - because a model NAME is
not an identity and a silently swapped model corrupts a vector store in a way
cosine cannot reveal. A third capability was built, measured at 46% against a 70%
bar, and WITHDRAWN; it is documented at the same length as what shipped, because a
negative result nobody records gets rebuilt. Written for a reader rather than for a
diff: [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md).

### Added

- **Commit messages are searchable by meaning.** `layers:` in
  `config/memory-index.yaml` gained a `source:` kind; `git-log` reads messages
  where the default walks a glob, sharing the pending/claimed/prune bookkeeping and
  nothing else. No schema change - `notes` was already generic enough to take a
  commit row unaltered. Two layers, one per side of the seam: `commit-engine` (607
  rows, engine store, `code` collection) and `commit-data` (486 rows, data store,
  a new `history` collection the default `/recall` deliberately does not search,
  because commit prose is a different kind of answer and mixing it dilutes both).
  Query: `python scripts/memory-index.py query "<q>" --layer commit-engine`.
  Measured against a 25-question set frozen BEFORE the build and split mechanically
  by whether `git log -i --grep` can answer it at all: Set A (grep-blind) 11/13 =
  **85%** top-5, mean rank 1.2, against an 80% bar; Set B (grep already answers)
  12/12 = 100%, mean rank 1.0, so nothing that already worked is buried. New:
  `scripts/utils/commit_source.py`, `scripts/eval-query-set.py`.
- **`threshold` is a per-layer key.** The prose-tuned 0.55 does not transfer: a
  paraphrased or Russian question finds its commit at cosine 0.456-0.597 while a
  keyword query hits the same index at 0.590-0.697, so seven of 23 correct answers
  were ranked FIRST and reported as "a gap in this area of memory". Set to 0.45 on
  the commit layers only; `content` keeps 0.55, because a global drop would buy
  commit recall with prose precision. Cost measured, not waved away: one
  false-confident hit in six nonsense queries at 0.45, none at 0.55. Both figures
  are real and neither may be quoted without its threshold.
- **The store records WHICH embedder built it.** `meta` now carries `model`,
  `embed_host` and `model_digest` (the sha256 of the weights). A tag is not an
  identity: `bge-m3` on two hosts is one NAME and can be two sets of weights the
  moment either updates, at which point stored and new vectors are incomparable
  while cosine returns a plausible number either way. Nothing synchronises two
  Ollama installations, so the digest is the only field that moves when a model is
  silently replaced; drift prints `WEIGHTS CHANGED`. New: `model_digest()` in
  `scripts/utils/embeddings.py`.
- **A build on a fallback embedder refuses; a query does not.** The asymmetry is
  cost, not caution. A build writes vectors that live for months, so it exits 1 and
  names `--allow-host-fallback` rather than hiding it. A query embeds one throwaway
  vector against measured cosine 0.99997 between the two hosts, so refusing recall
  would trade a live capability for float noise.
- **The fallback is announced loudly, and it arrives.** A red banner is emitted
  from `load_config`, through which every subcommand passes, so a command added
  later cannot omit it. That alone was insufficient: `.claude/hooks/recall-inject.py`
  captures the backend's stderr and discards it on a zero exit, so the banner never
  reached the surface the operator reads all day. The query JSON now carries
  `embed_fallback` and the hook renders it into the session.
- **`check-path-references.py --coverage`** reports engine Python that no
  non-archive prose names - 57 of 356 today, in 0.6 s. This is the planned
  prose-to-code edge table, REDUCED on measurement: the table would hold 28,067
  rows, 16,530 of them (59%) from `outputs/` and `plans/` where a handoff MENTIONS
  a path rather than documents it, while the point lookup is `grep -rn` at 0.33 s
  and the aggregate answer is 57 lines. Three honesty properties are tested rather
  than assumed: archive prose does not count as documentation, the overlay's
  absence narrows the claim and says so, and the `__init__.py` drop is printed
  rather than swallowed. Advisory; `--check` is untouched.
- **`ollama_accel` ops-radar signal** watches the ACCELERATED ollama host, not just
  the local one. On 2026-08-20 the Windows daemon self-updated, inherited the wrong
  environment on restart, tried to bind a port `wslrelay.exe` holds, and
  crash-looped once a second for 16 hours while the existing signal reported green -
  it probes one address and that one was healthy. Tier B on purpose: the daemon
  lives outside this OS and nothing here can restart it. New: `candidate_url()` in
  `scripts/utils/ollama_host.py`, which returns the address a preference NAMES
  without probing it, so "not configured" and "configured and down" stop being the
  same observation.
- **Persistence rule: Markdown files and SQLite, nothing else.**
  `.claude/rules/persistence.md`. Server databases are ruled out and so are
  LanceDB, Kuzu, DuckDB, usearch, LMDB and every other embedded-but-not-SQLite
  store - "embedded" is deliberately not the test, because a rule asking "does this
  add a process?" needs a judgement every time while this one is answered by
  reading a file header. Rationale: fsync pass-through on ext4-over-VHDX under WSL2
  is undocumented, so a daemon's write-ahead promise is unverifiable here, and
  SQLite carries the same uncertainty without the daemon. Governs data and state,
  never configuration.
- **Prose path audit.** `scripts/check-path-references.py --check` (pre-commit
  `path-references` + a CI step) fails when tracked Markdown gains a NEW reference
  to an engine path that does not exist. Deliberately narrow: only engine-ROUTED
  paths, resolved through the routing map and never through the disk, so CI and the
  operator's machine agree. Paths `.gitignore` covers are filtered rather than
  baselined, because such an entry reads stale locally and dangling in CI.
- **Cross-lingual recall is tested, not asserted.**
  `tests/test_recall_cross_lingual.py`, the first user of the declared-but-unused
  `requires_ollama` marker. `bge-m3` was chosen over an English code embedder for
  exactly one reason, and the 85% Set A score is measured over a MIXED-language
  set, so the English half alone could have carried it. Three Russian questions
  must each rank the intended English commit above five close distractors, and a
  fourth case requires the Russian and English forms of one question to agree,
  which catches a model that is merely CONSISTENTLY wrong. Skips rather than fails
  where no embedder answers.
- **`tests/security/test_SEC_019_commit_air_gap.py`** asserts the commit air gap at
  the security seam. The second case is the one that fails quietly: a commit
  touching a private path AND a public path must be refused WHOLE, because a
  per-file filter passes it, keeps the subject line, and looks correct while the
  subject describes the private change as fully as the diff does.

### Changed

- **The build's prune is scoped to the layers the pass walked.** Rebuilding one
  layer to A/B a variant deleted 122 skill and rule rows: the prune removed every
  stored path the pass had not claimed, and a single-layer pass claims nothing
  else. Now scoped to walked layers plus layers absent from the config entirely, so
  a dropped layer is still cleaned up. Two tests hold both halves.
- **`query --json` emits JSON on every exit, including the empty-index path.** It
  printed prose regardless of the flag, so `recall-inject.py` logged "unparseable
  JSON" and went silent - safe but BLIND, since an empty index and a broken backend
  became one observation - and `eval-query-set.py` died on a raw traceback.
- **`eval-query-set.py` measures the operator's default path.** Its first version
  forced `--threshold 0`, which measures raw ranking rather than what the CLI
  answers, and reported 85% where the CLI answered 77%. `--threshold` is now for
  explicitly measuring raw ranking and the report says so.
- **The `symbol` layer is WITHDRAWN**, its definition commented out in
  `config/memory-index.yaml` with the numbers beside it. It answered 6/13 = 46% on
  grep-blind intent queries against a 70% bar and a 50% kill line agreed before the
  build. Two excuses were refuted by measurement: cosine spread is 0.0338 against
  the PASSING commit layer's 0.0388, and raw cosine over all 9,608 rows with no
  RRF, no re-rank and no threshold gives the IDENTICAL 46%, so no ranking change
  can recover it. Targets sat at rank 117, 124 and 1016. Set B scored 92%, which
  says the plumbing works and that name-search is all it does - what `git grep` and
  CodeGraph FTS5 already do for free. Commented rather than orphaned: a layer
  defined but outside every collection is neither built nor queried AND prints
  `layers in no collection` on every invocation, including the recall hook that
  fires on each operator prompt. `scripts/utils/symbol_source.py` and its 12 tests
  stay in the tree so a next attempt starts from a measured negative.
- **Symbol text comes from source via `ast`, never from CodeGraph's `docstring`
  column.** That column reports 12.4% coverage where `ast` reports 52.0%, because
  its parser attributes the `# =====` section banner ABOVE a symbol instead of the
  string inside it - 582 of its 1,180 "docstrings" are banners. CodeGraph supplies
  identity, location and edges; the file supplies text. The boundary outlives the
  withdrawn layer.
- **Four records carrying "GPU is ~1.9x faster on embeddings" now carry the batch
  size.** Re-measured with both daemons warm: batched at 32 (what a build does) the
  GPU runs ~30 texts/s against the CPU's 21-26, so 1.2-1.5x; one text per request
  the GPU runs 9.8 against 12.7 and is SLOWER, because the WSL-to-Windows hop costs
  more than a 0.66 GB model saves. Raising the batch does not help (32 and 128
  differ by 3%, 256 is worse). Both re-measurements sit above the original on BOTH
  hosts, which points at method rather than hardware; the original method is
  unrecorded, so neither set is retracted. The consequence changed: pin the
  accelerated host for SINGLE PROVENANCE, not for speed.
- **Eight documentation paths that named files which do not exist are fixed**,
  including an `/odin` ingest command that could not run, a security page
  describing two hook files deleted in `ba1affd`, and three files citing a rule and
  a vault directory that `SENSITIVE_MODE` replaced.

### Fixed

- **`model_digest` treated a malformed URL as fatal.** A host with no scheme - a
  config typo, or the suite's stub value - raised `unknown url type` before any
  socket opened and aborted the build. It broke 11 tests, which is how it was
  found. A diagnostic must never be the thing that stops the work.
- **`model_digest` opened a URL without checking its scheme.** The host arrives
  from configuration, and `urlopen` honours whatever it is handed, so
  `file:///etc/passwd` would have been opened and read. The guard that already
  existed in `ollama_host.probe` is now applied here too.
- **Dynamic SQL removed from `symbol_source.py`.** An f-string built the
  placeholder list; values were bound and there was no hole, but the shape is what
  the next author copies before interpolating a value. Now `json_each`, with no
  dynamic SQL at all.
- **A wrong correction reverted.** `reference/workspace-overview.md` was edited to
  say the commit layer scores 77% and that an earlier 85% was mistaken. Both
  numbers are real and belong to different thresholds. The edit was made by
  trusting a summary line instead of opening the record, and was reverted after
  re-running the measurement.
- **The seam is tested in both directions.** Only "an engine store does not build a
  layer outside its set" was covered. The uncovered direction carries the exposure:
  `.memory-index-code/index.db` lives inside the PUBLIC engine clone, so a routing
  slip puts private commit subjects in a public tree, and a gitignore is one guard
  and not the seam. One new test reads the SHIPPED config rather than a fixture.

### Security

- **The commit air gap refuses a WHOLE commit, never merely the denied file inside
  it.** A subject line is prose: "closed the villa purchase" describes the change as
  completely as the diff does, so indexing the message of a private change leaks the
  change even with the path dropped. It refused 14 data commits.
- **The air gap cannot be switched off by a caller.** Found by a test written to
  prove its own fixture was not vacuous, which then FAILED: the commit stayed
  refused with EMPTY deny arguments, because `air_gap.is_denied()` carries a
  hardcoded floor a caller's arguments ADD to and can never subtract from. That is
  stronger than the guarantee being tested, so it is what the test now asserts.
- 563 tests in `tests/security/`, up from 559.

## [0.12.0] - 2026-08-21

The release about switches the operator can reach, and gates that turned out
never to have been armed. One new control ships: the compaction threshold is now
a per-session number he sets mid-flight, in the running window, with no restart.
Behind it, a closing sweep found four mechanisms that existed, passed review, and
were wired to nothing - a push hook that had silently stopped uploading Git LFS
content since June, a data overlay whose own tests ran in no gate at all, a
plugin bundle shipping a skill without the two commands that skill tells you to
run, and a frontmatter rewrite that has emitted unparseable YAML since the
generator first shipped. Written for a reader rather than for a diff:
[docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md).

### Added

- **`/compact-at N` sets the compaction threshold for THIS session.** The
  quality of long reasoning depends on how full the context window is, and the
  useful band is 30-40%, so the operator fixes the number at the start of
  important work instead of restarting into a different environment. It takes
  effect in the RUNNING session: the Stop hook and the status line are both fresh
  processes per event and both re-read the session's own state file. The soft
  reminder is always `SOFT_OFFSET` (5) below the hard threshold, never a second
  setting. Bounds are 15-90 - under 15 the derived soft threshold lands below 10%,
  where the trigger sits at the always-loaded context floor and cascades, and over
  90 there is no window left to write the handoff that must precede the
  compaction. A number at or below the last rendered fill is refused, and the
  refusal names that reading as one render old rather than as the present fill.
  Stored under `session_hard_threshold`, never `hard_threshold`: the status line
  rewrites that key on every render as its echo of the resolved config, so a
  choice recorded there would survive about one turn. The value dies with the
  session, because the state file is keyed by session and pruned with it. The
  switch raises neither `auto` nor `unattended`, and says so when both are off -
  with both down the hook ASKS at the threshold and compacts nothing.
  `--compact-at status` reports the resolved pair and its source;
  `--compact-at off` returns the session to `CLAUDE_HANDOFF_HARD_THRESHOLD`.
  39 tests in `tests/test_session_compaction_threshold.py`.

- **The status line renders the resolved threshold** in every state that CAN fire
  the driven compaction (`⏵ unattended 35%`, `⏵ auto 35%`,
  `⏸ unattended paused 35%`) and omits it on `manual`, where the hook only asks.
  It is shown whether the number came from the session or from the environment: a
  number that appeared only once overridden would leave "not set" and "not
  working" looking identical, which is the ambiguity that segment exists to
  remove.

- **A data overlay's own tests now block its push.** Measured 2026-08-20:
  `tests/` in the private overlay sat in no gate at all, because the engine's
  pre-push hook runs the ENGINE suite and `push-all` called the DATA attempt with
  `test_gate` unset. The first run of the new gate found two tests that had been
  failing unnoticed. The mechanism is the existing one rather than a second one -
  a versioned hook in `.githooks/`, installed machine-locally, with `push-all`
  refusing to push a repository whose gate is not armed. The two gates carry
  distinct markers, so DATA can never borrow the engine's and demand the engine
  suite on a repository that holds no engine. An executive's overlay carries no
  `tests/` directory, and that absence passes rather than fails closed.

- **Plugin bundles ship `.claude/commands/`.** `build-plugins.py` had no field
  for them, so `heading-core` shipped the `/checkpoint` skill and neither
  `/unattended` nor `/compact-at` - the two commands that skill's own body tells
  the operator to run. A `commands:` list in `config/plugin-bundles.yaml` now
  ships them through the same completeness gate and the same
  `${CLAUDE_PLUGIN_ROOT}` rewrite the skills get.

### Fixed

- **CI had been red on every push for three days**, 23 consecutive runs from
  2026-08-17 18:13, and each one mailed the operator. Three causes, all found by
  reading the CI logs rather than by another local run. Local green never
  implied CI green, because the runner has no private data overlay.

- **`merge-contacts.py` inserted a blank line after the frontmatter.** The
  closing `---\s*\n` in `FRONTMATTER_RE` is greedy over whitespace, so it also
  swallowed every blank line that followed, and the writer put exactly one back.
  A record with one blank line survived that round trip; a record with none
  gained one, and a record with two lost one - a rewrite of a file the tool was
  asked to merge one field into. All 326 of the operator's records carry exactly
  one, so the corpus test could not see it; `examples/crm/contacts/EXAMPLE-contact.md`
  carries none, which is how CI found it. Now `[ \t]*\n`, the writer no longer
  adds a separator, and three parametrised cases hold the gap at zero, one and
  two blank lines.

- **The spec back-pointer guard skipped nothing and failed everything.** It
  tested for a private overlay with `get_data_root().exists()`, and that call
  never returns a missing path: with no overlay it falls back to the bundled
  `examples/` tree, which exists and holds no specs. So all 84 pointers resolved
  against `examples/`, all 84 failed, and the assertion printed "0 skipped:
  private overlay absent" in the same breath. Now `data_overlay_present()`,
  which exists for exactly this and answers False for a demo clone.

- **A shipped Canopus slice was never retired**, so its frozen contract kept
  being held to bytes approved on 2026-08-17 while the product deliberately
  moved past them: the no-progress stall fuse became an explicit done marker on
  2026-08-19 and the continuation prose was rewritten twice. Restoring the
  approved bytes leaves four tests red against behaviour that was changed on
  purpose, so the frozen form asserts a product that no longer exists. The
  contract is promoted into the ordinary suite as
  `tests/test_checkpoint_unattended_contract.py`, and the record carries
  `retired_sha` at the last commit where it stood as approved.

- **The engine's pre-push hook had silently broken Git LFS since 2026-06-29.**
  `.githooks/pre-push` occupies the slot git-lfs installs into and ended in
  `exec run-tests.py` with no delegation, while `.gitattributes` routes ten binary
  extensions through LFS. The next `.png` or `.pdf` added to the repository would
  have pushed as a pointer with no object behind it - green for the pusher, broken
  for the next fresh clone. It never bit only because all nine existing LFS objects
  were added the day the hook first replaced the stock one, and absence of a
  symptom is not coverage. The review that diagnosed this exact hazard FOR the data
  overlay, and wrote the delegation into `.githooks/pre-push-data`, left the engine
  hook carrying the identical defect. Two guards now hold it: the delegation is
  present, and it runs AFTER the suite, because exec-ing git-lfs first would pass a
  presence check while skipping every test.

- **The data gate resolved the engine by guessing a directory name.**
  `.githooks/pre-push-data` looked for "the sibling named `.heading-os`", a name
  this workspace nowhere promises - a public clone is `heading-os` - and then fell
  through to a bare `python3`, which on this machine carries pytest 9.0.3. A wrong
  guess therefore ran the overlay's tests GREEN under none of the pinned
  dependencies. `install-git-hooks.py` already knew the real path and threw it
  away; it now stamps it in, and `check_pre_push_data` resolves the stamp so a
  relocation shows red instead of quietly passing.

- **Every built `SKILL.md` has shipped unparseable frontmatter since the plugin
  generator first ran.** The `${CLAUDE_PLUGIN_ROOT}` rewrite substituted into the
  double-quoted `allowed-tools` scalar without escaping, injecting a bare `"` that
  `yaml.safe_load` refuses. The rewrite now splits frontmatter from body and
  escapes inside the quoted scalar, keeping the quotes in the PARSED value where
  they protect a cache path containing a space. The guard parses what was written
  rather than trusting the substitution, and the old form was falsified by
  reproduction before the guard was accepted. Replayed over all 96 in-repo skills,
  the original defect broke exactly two - `checkpoint` and `queue-draft` - and only
  `checkpoint` shipped in a bundle, so the real blast radius was `heading-core`
  alone.

- **That frontmatter guard covered one bundle out of five.** It rode a fixture
  that builds `heading-core` alone, while four other bundles ship eleven more
  skills through the same rewrite, so a broken shape only they carried would have
  passed every test in the file. It now builds `--all` and parses every `SKILL.md`
  and every bundled command, asserting that at least one file was actually
  rewritten - a guard that runs over untouched files proves nothing about the
  rewrite it exists to check. Measured while widening it: 5 bundles, 14 skill and
  command files, 10 rewritten, 0 bad.

- **A done marker written during a turn did not survive the Stop that ended it.**
  `unattended_turn` cleared the whole window whenever the Stop's `prompt_id`
  differed from the recorded `unattended_turn_id`, four lines before it read the
  marker. That comparison is on turn IDENTITY and never on age, so it could not
  tell last night's marker from one written seconds earlier in the turn now
  ending - and the operator's own turn is the common case, being the first pause
  after any instruction he gives. `checkpoint-paths.py --done` printed
  `done recorded` and the hook continued the stretch anyway; it worked only from
  the second consecutive continuation onward. `unattended_paused_at` now separates
  the two cases, because it is stamped when the hook ACTS on a marker.

- **`--compact-at` was documented nowhere in `docs/`**, and the `/checkpoint`
  catalogue card still stated 25/30 as absolutes rather than as the defaults a
  session can now override.

### Note

- One CI failure needed no fix: a Pages deployment returned HTTP 500 from
  GitHub's own service and the next deployment succeeded.

- **CodeGraph is on trial over this repository and ships nothing into it.** A
  structural index of the engine's 829 Python files (872 files, 17,748 symbols,
  46,498 edges, built in 2.3 s) answers "who calls this" and "what breaks if I
  change it" in one query. It is a third-party MIT tool, installed per-machine,
  and the only trace it leaves in the engine is the `.gitignore` line that keeps
  its 53 MB cache out of the repository. Adopt-or-remove is decided 2026-09-04.
  See [docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md) § 6 for what it measured and
  the three caveats its README omits.

## [0.11.0] - 2026-08-20

The release about green lights wired to nothing. Three days of reading the code
against what it does found eight instruments reporting on circuits that were not
connected: tests that could not fail, a validator that had never validated once,
a compaction that recorded success twice while executing nothing, and a repeat
detector whose branch had never run in the workspace's whole recorded history.
None of it was a broken feature, and every one of them had passed the suite, the
gates and a review. Written for a reader rather than for a diff:
[docs/RELEASE-NOTES.md](docs/RELEASE-NOTES.md).

### Fixed

- **Twenty-five tests could not fail.** Three files built their assertions as
  `_check(name, cond)` returning a bool, accumulated the results into `ok`, and
  closed with `return ok`. Under pytest a test that RETURNS a value passes: the
  return is a warning, never a failure. 25 functions and 78 conditions had been
  green since the day they were written, over `ops-signals`, `ops-radar` and the
  action queue's synchronous send. `_check` now asserts, the accumulators and the
  hand-rolled `main()` runners are gone, and `filterwarnings =
  ["error::pytest.PytestReturnNotNoneWarning"]` in `pyproject.toml` makes the
  shape impossible to reintroduce quietly.

- **`merge-contacts.py` corrupted 182 of 326 CRM records** every time it ran. Its
  frontmatter parser flattened YAML block lists to `''` (7 records lost their
  tags outright) and dropped the quotes from quoted scalars (175 records, 981
  values), so `status: "active"` came back as `status: active` and a date-shaped
  string became a date. The parser now carries the shape through: `_BlockList`
  holds its indent, `_Quoted` holds its quote character, and the serialiser
  round-trips all 326 records byte-for-byte.

- **The CRM schema gate had never run at all.** `validate-crm-schema.py` skipped
  silently when `jsonschema` was absent, and `--quiet` — the flag the pre-commit
  hook passes — suppressed the one line that said so, so the hook printed nothing
  and exited 0. The dependency is now pinned in the core set, the skip message
  prints on every path including `--json`, and the hook's `files:` pattern
  (`^crm/contacts/.*\.md$`, which can never match in an engine-only repo) is
  replaced by `always_run: true`. First live run: 326 of 326 valid, after one
  record's `status: inactive` was corrected to the schema's `dormant`.

- **The Stop hook strangled its own compaction request, and recorded success.**
  `submit_compact` does not compact; it queues the literal `/compact` into the
  session's input through HERDR, and the harness runs a queued prompt when the
  turn ENDS. The hook then printed a block decision, which is exactly what stops
  a turn from ending. Measured live: `compact_requests` held two entries, 07:41:02
  at bucket 55 and 08:07:10 at bucket 60, neither carrying an error, while
  `compact_history` still ended at the previous day's boundary, and the transcript
  showed both `enqueue` records with no matching `remove`. A submitted compaction
  now ends the Stop.

- **The same hook read its own request as the operator speaking.** The harness
  records that queueing as an ordinary `queue-operation` / `enqueue`, which is the
  signal `_queue_pending` and `_operator_spoke` use to detect a mid-turn message.
  With no removal ever coming, `_queue_pending` returned True permanently. Both
  readers now skip `content == "/compact"`.

- **Handoff filenames were stamped in UTC**, so between 00:00 and 04:00 local
  every archive carried the previous day's date. Filenames and calendar-day
  decisions now use `CP.local_now()`; stored timestamps stay UTC. The first fix
  broke `_handoff_since`, which compared the new local stamp against a UTC floor
  and would have accepted a handoff up to four hours stale — a compaction firing
  over unsaved work. Held by `tests/test_checkpoint_stamp_timezone.py`.

- **A blind status-line render decided things it could not see.** When the payload
  carried no usage figure, the hook still wrote the offer keys, clearing a pending
  checkpoint offer and overwriting the last good measurement with nothing. The
  measurement keys now move only when a measurement exists.

- **Six state writers raced.** Every hook read the checkpoint state, mutated a
  copy, and wrote it back whole, so the status line's per-render write could
  silently drop a done marker written a moment earlier. All six now go through
  `CP.locked_state` / `CP.file_lock` (`fcntl.flock`, bounded wait, honest
  degradation to an unlocked write with a stderr line). The shared `.latest`
  pointer pair is written under one lock, because two `os.replace` calls back to
  back are not atomic together.

- **`str.splitlines()` shredded transcript records.** It breaks on eight
  characters a file handle does not, and three of them (U+0085, U+2028, U+2029)
  survive `json.dumps` unescaped — 22 of them in the live 88 MB transcript. Three
  readers were cutting JSONL records in half. All three now iterate the handle.

- **`files_written` read the whole transcript into memory**: 795 MB peak RSS on
  that same file, in a hook that runs at every turn boundary. Streaming brought it
  to 19 MB.

- **The compaction watcher watched a key nobody writes.** It read
  `compact_request_at` while the writer spells it `compact_requested_at`, and
  reported "no request" through a run where one had fired. An instrument that
  names a missing key reads exactly like an instrument reporting nothing happened;
  a test now holds its key list against the writer.

- **The pointer lock leaked into the data overlay.** `.pointers.lock` is an empty
  file whose only meaning is the `flock` held on it at runtime, and it sat
  untracked in `git status`, one `git add -A` from a commit. Ignored now, with a
  guard that asks git rather than reading `.gitignore`.

- **`check_protect_docs` refused to let anything READ six files in `docs/`.** The
  check gated on `tool_name == "Bash"` and let every other tool fall through to a
  path test, so a `Read` of `docs/EMERGENCY-PROCEDURES.md` returned
  `decision: block`, which the harness renders as a permission deny. Not
  theoretical: `.logs/denials/denials.jsonl` records a real operator Read refused
  this way on 2026-08-11, eight days before anyone noticed. `check_protect_corporate`
  had the same shape and was stat'ing `.workspace-identity.json` on every Read to
  reach a verdict it could never return. Both now exclude `Bash` and `Read` by
  name rather than gating on a write allow-list, so a tool shape added to the
  matcher later arrives INSIDE the check instead of silently outside it.

- **`check_tool_budget`'s repeat detector could never fire.** `_stable_args_signature`
  built its key with the builtin `hash`, and `PYTHONHASHSEED` is randomised per
  process while every hook invocation is a fresh interpreter. The live state file
  held 344 tool-history entries and 344 DISTINCT signatures; the branch had never
  executed in the workspace's whole recorded history. Now sha1. The guard is a
  subprocess test, because the in-process version passes against the broken
  implementation too — which is exactly why this survived.

- **Three per-OS install templates carried an empty `env` block.** The live
  compaction settings are in the gitignored `settings.local.json`, so every
  machine built from a template ran the stock auto-compact window instead of the
  tuned one. Two guards now hold it: the templates must carry the same env, and
  the arithmetic that derives a 584,000-token trigger from a 750,000 window is
  pinned, so a future edit to the window cannot silently move the point.

- **The templates also registered PostToolUse on `Write|Edit` only**, so a
  workspace provisioned from one ran no hidden-character scan and no
  prompt-injection scan on `MultiEdit` or `NotebookEdit`. Both hook bodies already
  handled those shapes; only the matcher was wrong.

- **`yt-pulse` could not run the VPN gate its own rule calls mandatory.** Claude
  Code 2.1.218 made `context: fork` background by default, and a background fork
  gets the narrower subagent tool set, which has no `AskUserQuestion`. Seventeen
  skills set `context: fork` and none set `background`. Fixed there and on the two
  skills that dispatch parallel specialists.

- **`ops.py` returned a naive datetime** where seven sibling `_parse_iso` copies
  returned an aware one, and compared it against an aware cutoff. One
  externally-appended log line would 500 the `/settings/ops` endpoint. Replaced
  with one shared `scripts/utils/timeparse.py` across eight call sites.

- **`harness-audit.py` called a disabled plugin "running in this session".** It
  read `installed_plugins.json` (what was fetched) and nothing read
  `enabledPlugins` (whether the loader starts it) — the same over-claim
  `.claude/rules/scope-claims.md` was written for, one layer down. It also walked
  vendored `node_modules`, which produced all 1,596 baseline-drift lines and all
  46 injected-instruction hits, so a genuine change to a real plugin would have
  been invisible inside the wall. Both fixed; drift is 0 and injection hits are 0.

- **The rule-split guard reported four live directives as lost.** It could see a
  rule SPLIT into a sibling but not a rule OFFLOADED into `docs/`. It now honours a
  committed `<rule>.destinations` file — a named claim someone can be held to,
  never a widening to "anywhere in the repo" — and three tests hold it, including
  one proving a declaration is not a blanket exemption.

### Added

- **`.claude/hooks/unattended-resume.py`** (UserPromptSubmit). Clears a paused
  unattended stretch when the operator sends an instruction, which is what
  `clear_unattended_window` and `--done` have always promised. The clearing used
  to happen at the next Stop — the END of the turn that instruction opened — so
  the status bar read `unattended paused` through a turn that had already resumed.
  Never blocks, never prints, never touches the switch; ~30 ms, and a session with
  no pause marker writes nothing. The literal `/compact` is ignored, because the
  Stop hook queues that text itself. Registered in all three per-OS templates, not
  only in the gitignored live settings file.

- **`scripts/dev/compact-watch.py`.** Records who compacts a session and how, one
  JSONL line per observed CHANGE rather than per poll, into the data overlay so
  the evidence survives the compaction it is recording. An instrument for one
  question, not a daemon and not scheduled.

- **`jsonschema==4.26.0`** in the core dependency set, with the measured reason:
  without it the CRM schema gate skipped silently on every run.

- **Locking primitives in `scripts/utils/checkpoint_paths.py`** — `file_lock` and
  `locked_state`, plus `local_now()` for filename stamps, with the `get_default_tz`
  import deferred because five hooks import this module on every turn.

### Changed

- **The unattended continuation message is half the size, and repeats are one
  line.** It prints to the operator's transcript at every pause of an overnight
  run, so each sentence is one he re-reads forever. The full form went from 467
  characters to 372; from the second continuation of a window onward a 155-character
  form carries only what changes — the counter and the one command that can end the
  stretch. A compaction inside the window puts the full text back, because that is
  the one event which makes "you already read it" false.

- **`--done` names both halves of the state**: the stretch is over and the bar will
  say so, while the switch stays up. Only the operator lowers it.

- **Specs, plans and ADRs moved to the private data overlay** and were removed from
  the public engine repository, on the operator's decision: the engine is public,
  how it was built is not. `docs/design/` is gitignored and routed `private`; 84
  back-pointers in engine code were repointed at `.heading-os-data/docs/`, and a
  test now resolves every one of them.

### Removed

- **Three dead functions and nine dead constants**, after a tree-wide sweep of
  1,428 files. `bootstrap_root` was proven unreachable at its own birth commit,
  not merely unused today. Three constants were KEPT with the reason written down,
  and one of them gained the test its comment had promised.

- **`.claude/hooks/context-monitor.py`**, and its four registrations. It read
  `remaining_percentage` from the PostToolUse payload, a key the payload does not
  carry, so it exited at the same line on every invocation since it was written;
  the debounce file it writes on every warning had never been created once. It was
  one of only two synchronous PostToolUse hooks, so deleting it halved that
  blocking chain.

- **The `## Active Threads` block in the memory index.** All thirty rows quoted a
  live status and a live date, which `memory-discipline.md` forbids in an index
  hook, and it was measurably stale — thirty threads active on disk, twenty-nine
  listed, one of those already closed. It also sat inside the cached prompt prefix
  and changed on 66 commits in 30 days, rebuilding a ~50k-token prefix each time.
  `/prime` now reads the live set from disk.

### Changed

- **The always-on rule set is 40% smaller: 119,896 bytes across 18 files down to
  71,412 across 15.** No directive was lost; every stream was verified by a
  separate agent that diffed the removed lines one at a time and traced each to
  its destination. `skill-orchestrator.md` lost seven pattern blocks that every one
  of them already pointed at in `reference/orchestrator-patterns.md`, and reading
  that reference before dispatch is now mandatory rather than merely advised.
  `security.md` kept the directives a model can actually violate and moved the
  defence-layer narrative to `docs/SECURITY-MODEL.md`; it stays unconditional,
  because a path-scoped rule is lost after a compaction. `documentation.md` and
  `output-naming.md` are now path-scoped. `vpn-preflight.md` moved to `reference/`,
  with its obligation kept resident in one line because that gate has no path
  signal to fire on.

- **The coverage floor moved from the push gate to CI**, where it can actually stop
  a regression. It cost 37.9 s on every push (measured A/B, twice) while demanding
  27% against a delivered 43.44%, so it could not fail. The test that guarded it
  moved with it and now asserts both halves of the pair, since dropping the flag
  from one place without adding it to the other leaves the floor enforced nowhere.

- **`load_routing_map()` is cached on file identity** — `(path, mtime_ns, size)`,
  never a bare `lru_cache`, because a long-running daemon must still see an edit
  to the file that decides what counts as private data. It was re-parsing the YAML
  on every `get_routing_destination()` call: 9 ms each, and the entire cost of the
  `engine-tree-clean` pre-commit hook that fires on every commit.

- **`security-guidance` disabled**, on measurement rather than preference: 333 ms
  per write against this workspace's own 71 ms, zero findings across 12,308 lines
  of its own log, and duplicate cover from three existing gates. Its one unique
  capability, an LLM review of a commit diff, is available on demand through
  `/code-review`. `code-review` and `code-simplifier` enabled in the same pass —
  one slash command and one agent definition, no hooks, no per-turn cost. The
  roster moved out of the always-on router rule into `reference/plugin-roster.md`,
  after that rule was found to document four plugins enabled nowhere and omit
  three that were running.

- **`skillListingBudgetFraction` raised to 0.03.** Claude Code budgets the skill
  listing at a fraction of the context window and, on overflow, drops
  DESCRIPTIONS rather than skills — a skill goes mute with no error, still typable
  as `/name` but no longer matchable. The 73 model-invocable skills here carried
  97% of the default budget on their own, before ~40 plugin skills shared it.
  Nine descriptions were rewritten in the same pass: six too thin to route on, four
  over a thousand characters that opened with an arXiv citation or an
  implementation detail. Every one re-tested through the LLM-judge harness.

- **The bridge daemon is capped at a 6-hour recycle** while its leak is located:
  measured 1,198 MB RSS at 23:40 and 1,856 MB twelve hours later, monotone, against
  a 43 MB import baseline. Its log was 84% APScheduler job-lifecycle chatter, in
  which a job that STOPPED firing looked identical to one that fires; the
  scheduler's logger is now at WARNING.

- **Test suite: 5,713 tests in 72.6 s parallel**, from 5,643 in 426 s serial. CI
  gained `-n auto` on two steps that were running the suite serially on a two-way
  matrix with xdist already installed. 17.6 s of `time.sleep` came out of two
  bridge test files, where it guarded a timestamp collision the code had already
  fixed with a monotonic sequence prefix, and four marp assertions that were
  wrapped in a truthiness guard — so a broken render passed green — became real
  assertions against a deck now rendered once instead of five times.


## [0.10.0] - 2026-08-17

The release about not halting for nothing, and about what a compaction keeps. No
hook in Claude Code can start a compaction, so none of this automates one. It does
three other things: it takes our own Stop hook out of the way when something else
already drives the pause, it steers what the harness's own compaction PRESERVES,
and it removes the reason a session stops dead at a pause nobody is there to
answer. A session that halts at 23:40 never reaches the compaction threshold at
all, because context does not grow while nobody works.

Then a blinded review of the finished work found nine more defects, and the first
of them meant the mode did not work in the majority of real sessions. That
measurement, and the contract test that was green and useless, are the part of
this release worth reading.

### Added

- **A PreCompact hook, `.claude/hooks/checkpoint-precompact.py`.** Every
  compaction until now kept whatever the summariser happened to keep, including
  the ones that fire overnight with nobody present. The hook now dictates what
  survives verbatim: the objective in the operator's own words, every decision
  WITH the reason it was taken, exact paths and commands, the next concrete
  action, the last instruction given, any constraint still binding. And what to
  drop: file contents already on disk, the output of exploratory commands,
  discussion of finished work.

  Below that fixed block it appends six facts read off the tree at compaction
  time, so the summary does not have to carry them: branch, working tree, last
  five commits, the files this session wrote, this session's handoff pointer, and
  the most recently modified plan file. Three properties are load-bearing. It
  exits 0 on every path, because exit 2 blocks the compaction it exists to
  improve. It writes nothing, because PostCompact owns the write. And it redacts
  BEFORE bounding the length, because truncating first can cut a credential into
  a fragment the pattern no longer matches.

- **Unattended mode: `/checkpoint unattended on`.** A separate switch from
  `auto`, never a third value inside it. Above the soft threshold the Stop hook
  stops asking and waits a grace period instead; type anything inside it and the
  turn comes straight back, stay silent and the hook tells the assistant to carry
  on. It is named after its precondition rather than after compaction, because
  compaction happens either way and the only thing being chosen at a pause is who
  decides.

  Two bounds stop a run that goes nowhere, and they catch different failures. The
  no-progress fuse hashes HEAD plus the size and mtime of every file THIS session
  wrote; three consecutive evaluations that move none of it stop the mode. The
  ceiling stops it after 100 continuations. A stopped run records which fuse
  fired and when, readable with `--unattended status`, and sends one Telegram
  notice when a target is configured.

- **The Stop hook stands down when something else drives the pause.** A scheduled
  `/loop` wakeup, in-flight background work, or a ralph-loop that names this
  session each claim the Stop event; the offer used to fire regardless and cost
  the loop's owner a wasted turn. The suppression happens BEFORE the
  offer-delivered marker, so a suppressed offer is not recorded as delivered.
  `/goal` is the one case no hook can see, because the harness holds that state
  in memory; `stop_hook_active` bounds the cost instead.

### Changed

- **The threshold menu was rebuilt around one question.** `unattended` is now its
  second option, beside the plain checkpoint; `auto` is named only as a
  condition, for the operator who stays at the keyboard and wants the question to
  stop. Options are grouped - checkpoints, then compact, then "continue" last -
  because the operator reads them in the order the hook writes them. The
  eleven-line wrapper is one line: the harness shows the operator the WHOLE
  reason, so its opening restatement of the percentage cost him a line to read
  twice. The menu now closes with where compaction actually comes from.

- **`scripts/turn-check.py` no longer blocks a turn on a frozen contract.** A
  Canopus contract is written red at step 3 and stays red until the
  implementation lands at step 6; running it at the end of every turn of the
  build leaves the operator two bad choices, and the one that happens is learning
  to ignore the hook. Files under `tests/contract/` are matched, then skipped and
  COUNTED, the same treatment a parallel session's edits already get.

- **The PreCompact registration allows 20 seconds and the Stop registration 90.**
  Claude Code discards the output of a hook that outruns its timeout, so a 60
  second grace period inside a 60 second budget lands exactly on the boundary.

### Fixed

- **The queue counter knew two of the harness's four operations, and the mode did
  not work because of it.** `_queue_pending` counted `enqueue` against `remove`
  alone. Measured across all 44 transcripts for this project: 660 enqueue, 422
  remove, 231 `dequeue`, 1 `popAll`. The formula is falsely positive in 28 of the
  44, so in the MAJORITY of real sessions the hook read a phantom queued message,
  returned early, and halted the very run it was turned on to keep going -
  leaving no continuation, no stall record and no notice. The contract test
  covering this passed because its fixture was captured from a session before
  that session's own first dequeue.

- **The no-progress fuse measured the wrong thing in both directions.**
  `git status --short` reports that a file changed and never who changed it, so a
  sibling session or a daemon writing between two pauses reset the counter and an
  overnight run with nothing left to do would reach the ceiling inventing work.
  And the COUNT of files written could not see a second edit of a file already in
  the set, so real work read as three dead continuations. It now hashes HEAD plus
  per-file size and mtime, scoped to this session. One residual limit is named in
  the docstring rather than hidden: a sibling's commit still moves HEAD.

- **The grace period accepted 120 seconds against a 90 second timeout** and
  reported it back to the operator, while the harness discarded the output.
  Bounded at 75 in one shared place, and the progress fingerprint now runs BEFORE
  the wait rather than after it.

- **A completed background task claimed the Stop event forever,** silencing every
  threshold offer for the rest of the session. Terminal states no longer claim;
  an unknown state still does.

- **The stall record was re-stamped at every later pause,** so a 03:00 stall
  reported whatever time the operator happened to look. It is written once, and
  `--unattended status` now prints WHICH fuse stopped the run instead of
  hardcoding one of the two.

- **The offer path wrote back a whole stale state copy,** the exact defect the
  shared read-modify-write helper exists to prevent.

- **The PreCompact hook printed absolute paths** carrying the operator's home
  directory and the name of their private overlay into a summary another hook
  writes to a file. Project-relative first, data-root-relative next, absolute only
  as a last resort.

- **"Active plan" asserted which plan is in force from a modification-time sort
  that cannot establish it.** On the tree that produced the hook it named a
  four-day-old unrelated plan, because the plan actually in force had just been
  archived. The label now says recency, and reports the count of plan files it did
  not read.

- **The redactor import caught `ImportError` only.** A SyntaxError in that module
  is as fatal as its absence and likelier, since the module is actively edited and
  a compaction can fire mid-edit; the unguarded `print` would then have lost the
  whole keep-set. The sibling hook already guarded the identical import correctly.

- **Five documentation claims were wrong by measurement** and are corrected: the
  menu and SKILL.md said unattended mode "saves silently" when the Stop hook
  writes no checkpoint in that mode, the fuse fires after two continuations rather
  than three, the auto save is once per band rather than at every threshold, the
  paths command emits eleven keys rather than seven, and the hooks reference named
  four facts of six while restating a promise the same commit had removed.

## [0.9.0] - 2026-08-17

The release about what a measurement is worth. A style checker reported 300 errors
across the skill corpus and 83 of them were its own sentence splitter, so correct
prose was being rewritten to satisfy a broken instrument; the real 217 were fixed,
the corpus stands at zero, and both halves of the rule now carry a gate. Two tools
were caught telling the operator more than their method established, which produced
a rule and a test that refuse the shape rather than the two instances. A new
aggregation primitive answers the counting questions retrieval scored 0.000 on, by
walking the corpus on disk instead of loading it. Three sessions on one workspace
stopped being treated as one session. And a Telegram watchman that went blind the
moment the operator opened his phone, a backup email that had never once been sent,
and a reminder that arrived a week before its date were all found the same way:
by measuring something that had only ever reported success.

### Added

- **The checkpoint can now save itself, and it ships as a plugin.** Auto mode
  (`CLAUDE_HANDOFF_AUTO=1`, off by default) makes the Stop hook drive a silent
  checkpoint the moment context crosses the threshold and lets the session carry
  on; after a compaction the SessionStart hook tells the assistant to continue by
  itself. The hook POINTS AT `.claude/skills/checkpoint/SKILL.md` rather than
  restating its section list, because a format defined in two places drifts, and
  the copy that stops being updated is the one the model reads. It also refuses
  to name a compaction point unless one is actually configured
  (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`), per
  `.claude/rules/scope-claims.md`.

  `scripts/checkpoint-paths.py` prints this session's stamp, archive path,
  pointer paths and state path. `/checkpoint` used to build those paths itself,
  with a documented fallback to the literal slug `session` when it could not
  derive one, which is how a manual checkpoint lands in the wrong place once the
  paths are keyed by session.

  The four hooks now ship in the `heading-core` plugin bundle. They find their
  root by walking for `scripts/utils/` instead of counting parent directories, so
  the same files work at `.claude/hooks/` in this monorepo and at `hooks/` inside
  a bundle, and the archive follows the CONSUMER's repository rather than the
  operator's data overlay. `checkpoint-statusline.py` ships but cannot be wired
  from a plugin: Claude Code exposes context usage only to a `statusLine` and a
  plugin manifest has no `statusLine` key, so that stays one line the installer
  adds ([docs/PLUGINS.md](docs/PLUGINS.md)).

  Auto mode and the proactive threshold offer were contributed by
  [Mahmoud Maatuq](https://github.com/mmaatuq), who packaged this system as a <!-- content-guard: ok published contributor credit, kept by operator decision 2026-08-24; the only real person named in the engine -->
  plugin independently and found the concurrent-session collision fixed below.

- **Auto mode is now a per-session switch, offered from the prompt itself.**
  `CLAUDE_HANDOFF_AUTO` is a launch-time decision for a whole workspace, and the
  decision an operator actually makes is a running one, taken part-way into a
  long piece of work and belonging to a single window: three sessions on this
  tree routinely do three different sizes of work.
  `python scripts/checkpoint-paths.py --auto on|off|status` writes the choice
  into that session's own state file, and the threshold offer lists it as an
  option, because the operator is already reading the list at the moment they
  decide. It overrides the environment default in BOTH directions, so a window
  can also ask for the question back while the workspace default is silence.

  It is stored under `session_auto` rather than under `auto`, and the difference
  is not cosmetic: `checkpoint-statusline.py` rewrites `auto` after every turn as
  its echo of the resolved mode, so a choice recorded there would have survived
  roughly one turn. No cleanup path was added, because none is needed: the state
  file is keyed by session and already pruned with it. Eleven tests, including
  the two that hold the flag against the statusline's rewrite and against the
  post-compact reset.

  Naming: the switch says what it does, which is to stop asking. It is NOT
  called `compact-auto`, because no hook can start a compaction, and a name that
  implies otherwise is the same defect this release fixed one paragraph above.

### Fixed

- **A quarter of the documentation-style debt was the checker, not the prose.**
  `scripts/ste-check.py --skills` reported 300 errors across the skill corpus on
  2026-08-16. Bringing that number down surfaced three defects in the sentence
  splitter, all of one shape: a markdown character sits between a sentence's
  terminator and the next sentence's first letter, the pattern does not accept
  it, and two clean sentences measure as one over-long one. Emphasis
  (`**You decide.** No code reads them.`) accounted for 51 of the errors, the
  blockquote continuation marker for 21, and a closing quote or bracket
  (`... both work." If two variants ...`) for 11. Eighty-three sentences were
  being rewritten to satisfy a broken measurement.

  Enumerating the shapes is what produced rounds two and three, so the closer is
  now a character class covering quotes, brackets and emphasis together, and the
  blockquote marker is stripped in preparation like a list bullet. The
  warning-before-the-step check reads the same prepared text and keeps working;
  a test says so, because that is the one rule here with a physical cost when
  it breaks.

  The real debt was 217 errors across 74 skills, the corpus is now at **zero**,
  and `--skills` is a gate: a `documentation-style-skills` pre-commit hook and a
  CI step, errors only, like its `--all` sibling. The twelve `--all` pages stayed
  at zero throughout.

  No vendored exemption. The last error sat in the vendored
  `.claude/skills/ast-grep/SKILL.md` and read as untouchable, which was wrong:
  `skills-lock.json` hashes the copy that SHIPS here rather than upstream's
  bytes, the in-repo copies are already lightly adapted, and `--relock` is a
  supported operation. The sentence was split, the tree re-locked, and the lock's
  `note` now tells a re-vendor to re-apply the adaptation. An exemption would
  have hidden one file's debt permanently, which is the same
  unmeasured-therefore-clean failure the gate exists to end.

- **Three sessions on one workspace were treated as one session.** Every path
  below the workspace root was shared: one `.claude/state/checkpoint-state.json`
  and one `.latest/{summary,prompt}.md`. Measured on 2026-08-16 by replaying the
  real hooks: with session A at 46% and session B idle, B's Stop hook consumed
  A's offer, so the idle session was told to checkpoint and the session whose
  context was actually filling was told nothing. The same shared pointer let a
  resumed session be injected a DIFFERENT session's handoff, under a sentence
  asserting a previous checkpoint had been found, which nothing in the hook had
  established.

  State and the injected pointer are now keyed by session id, from the hook
  payload or from `CLAUDE_CODE_SESSION_ID` for the model-driven skill. The shared
  `.latest/{summary,prompt}.md` pair deliberately STAYS, because it has a second
  reader asking a different question: `scripts/next-signal.py` wants the newest
  handoff in the workspace, where last-writer-wins is the right answer rather
  than a race. Per-session pointer dirs and state files are pruned (14 days, 25
  sessions); the dated archives are the record and are never pruned.

  Two further defects surfaced while proving the plugin build. The archive was
  resolved from the location of the imported module rather than from the tree the
  hook actually runs in, and in a virtualenv where the engine is installed an
  editable-install finder runs ahead of `sys.path`: a bundled hook imported the
  ENGINE's copy of the helper, concluded it was in an engine tree, and wrote a
  scratch repository's handoff into the operator's live archive. The caller now
  passes the root it resolved. And the pointer was written unbounded at 32261
  bytes against an 8000-character injection cap, so three quarters of it was
  never read and the quarter that was arrived cut mid-sentence; it is bounded
  where it is written, naming the archive that holds the rest.

- **Two tools told the operator more than they had measured, and now a test says
  they may not.** Within hours of each other on 2026-08-12: `scripts/harness-audit.py`
  walked the plugin cache and printed the result under "running in this session",
  so a superseded `superpowers` 6.1.1 was reported as a live SessionStart hook
  beside the 6.2.0 the loader actually reads; and `scripts/turn-check.py` called
  `git diff` "the edits made in this turn", so the Stop hook blocked a turn over a
  deliberately-red TDD test a PARALLEL session had written a minute earlier. Neither
  was a logic bug. A directory listing does not establish a session, `git` does not
  establish an author, and both sentences survived review because they read as
  obviously true.

  The audit now resolves activation from `installed_plugins.json` and calls a hook
  dormant only when the record names a different version of the SAME plugin -- an
  unmentioned cache root stays reported as live, and an unreadable record widens to
  everything, because hiding a hook that executes is the one direction this must not
  fail in. The first cut had that backwards and the repository's own contract test
  caught it. Dormant versions stay hashed and scanned; only the claim changes.

  `turn-check` narrows to files this session wrote, via the new shared
  `scripts/utils/session_scope.py`, which reads the session transcript the Stop hook
  is handed. It returns None rather than an empty set when it cannot tell, so a
  caller widens instead of quietly checking nothing and reporting a pass, and it
  prints the count of files it skipped as another session's rather than letting a
  narrowed check read like a complete one.

  New `.claude/rules/scope-claims.md` states the obligation and
  `tests/test_scope_claims.py` enforces it: an AST scan of every user-facing string
  under `scripts/` and `.claude/hooks/` for phrases claiming session membership or
  live execution, each of which must either name what resolves it or say why it is
  not a coverage claim. The detector is deliberately wide, because a defect of this
  shape is written in whatever words its author reached for.

- **The Odin collect marker never left this machine.** `knowledge/odin-brain/.last-collect`
  was gitignored while its twin `.last-reflect` was tracked, so a second machine
  pulling the overlay read `last_collect: null` and counted the entire allowlist as
  un-harvested. Each marker holds one ISO date and no content. Both are tracked now.

### Changed

- **The documentation-style checker earned its gate, and only half of it.**
  `scripts/ste-check.py` shipped advisory on 2026-08-11 with the decision explicitly
  deferred to its first measurement: 53 errors and 88 warnings across the twelve
  in-scope pages. The errors were 52 over-long sentences and one two-action step,
  arithmetic on a word count with nothing to be wrong about, and all 53 are now
  rewritten to zero. So `--all --quiet` is a pre-commit hook (`documentation-style`)
  and a step in the CI `sovereignty guards` job. The 88 warnings are NOT in the gate
  and `--strict` stays out of it: 79 are `passive_voice`, decided by a regex with no
  part-of-speech tagger behind it, and gating on that would fail commits over
  constructions the checker cannot parse. New `--quiet` flag prints only the files
  carrying an error, so 88 advisory lines on every docs commit do not train the
  reader to skip the output that does fail. `tests/test_ste_check.py` holds the
  hook's `files:` pattern to the checker's own `CHECKED_GLOBS`, so a page cannot
  drop out of the gate without a test failing.

### Removed

- **The four backward-compat hook shims, and the blocker that outlived its own
  condition.** `.claude/hooks/{prevent-secrets,protect-corporate,protect-docs,protect-personal-threads}.py`
  were 28-line runpy delegators to `_dispatch.py`, kept for exec workspaces whose
  `settings.local.json` named them individually. The removal condition, written in
  2026-06 before the two-part topology hard-cut, was "every provisioned exec has
  re-synced" — remote machine state nobody here can read, so the row could never
  clear on evidence. The checkable version of the same claim is local, and it holds:
  a workspace built from this repository gets its hooks by copying a tracked per-OS
  template (`scripts/setup-platform.sh`), and all three templates have named
  `_dispatch.py` since the engine's initial import. The legacy provisioner that once
  wrote a `protect-corporate.py` reference now hard-refuses. `tests/test_settings_hook_targets.py`
  makes it permanent: every hook a tracked settings file names must exist, and none
  may name a retired shim. The `prevent-secrets.py` entry in `secret-scanner.py`'s
  `SKIP_PATHS` went in the same change, and the migration-cruft table in
  `.claude/rules/documentation.md` now has no open rows.

### Fixed

- **An Odin nudge that doing the work could not clear, and the test file that
  would have caught it never ran.** `scripts/odin-cadence.py` raised a Tier-B
  radar signal whenever any two raw episodes formed a cluster, and never read
  `knowledge/odin-brain/.last-reflect` at all. A reflect pass on 2026-08-11
  graduated ten episodes, wrote the marker, and the nudge stayed on exactly as
  before. Two causes. First, the count was every cluster that existed rather than
  every cluster carrying material the CEO had not reviewed; it now counts only
  clusters holding an episode logged strictly after the marker, and cluster age is
  the wait of the OLDEST unreviewed episode rather than the age of the newest
  member, which measured the opposite thing and reset each time a cluster grew.
  Second, the join threshold was one shared tag, which does not survive contact
  with a real brain: over the 15 raw episodes standing that day it produced ONE
  component of 12, welding two unrelated themes together through the workspace's
  own organisation tag (6 of 15 episodes carried it) plus generic domain labels
  such as `go-to-market` and `ownership`. Dropping high-frequency tags instead was
  measured and rejected, because the cutoff that breaks the weld also discards the
  slug of a recurring counterpart, and a counterpart who keeps reappearing is the
  best clustering signal there is. `CLUSTER_MIN_SHARED = 3` produced the two
  components a human would name. `/weekly-review` Phase 3.5 stopped re-checking
  the marker by hand, since that is now a weaker duplicate of the detector's rule.
  Separately: `tests/test_odin_cadence.py` carried a `main()` and no `test_`
  function, so pytest had never collected it and none of its cases had ever run in
  the suite. It has a wrapper now, and its cluster cases assert the new contract
  including that a single shared tag does NOT cluster.

- **A public docs page had been frozen for six weeks, and nothing said so.**
  `docs/EMERGENCY-PROCEDURES.md` is served on the public docs site and is generated
  from a template that lives in the private data overlay. The `sync-docs.py` hook
  resolved its destination from the template's own location, a 2026-08 fix for a
  CEO-only guide leaking into the engine tree that over-corrected: every pair went to
  the overlay, so the engine's published copy stopped updating on 2026-06-26 while
  the template moved on, with no error on any surface. Destination is now a property
  of the FILE, not of where its template happens to live: `sync_targets()` returns the
  overlay copy for everything and additionally the engine copy for the pages listed in
  `ENGINE_PUBLISHED`. `tests/test_sync_docs_targets.py` asserts both directions,
  including that a CEO-only guide still never reaches the engine, and compares the
  live published page against its template so this exact drift fails a test rather
  than sitting unnoticed.

- **A judge that could not answer was scored as a router that answered wrong.**
  On 2026-08-10 the nightly router-accuracy run reported a 5-point fleet-wide drop
  and the ops-radar Tier-B alert named `/voss` at -38 points, with no commit having
  touched `.claude/skills/` or `.claude/rules/` for two days. Two independent causes,
  both in the measurement. First, the judge family had advanced a release overnight
  and the newer model's verdict JSON no longer fitted `skill-trigger-test.py`'s
  300-token ceiling: of the 47 failing cases, 40 carried no verdict at all — 31 empty
  replies and 9 truncated mid-string, across 33 skills — and every one was counted as
  a routing miss. The 7 that remain are genuine, two FEWER than the 9 the previous
  judge logged, so routing had if anything improved. The ceiling is raised
  well clear of a verdict, a reply with no usable verdict is retried once, and a case
  the judge still never answers is now reported as UNMEASURED — excluded from the pass
  rate, counted in `errored`, printed as `NO VERDICT` rather than `MISS`. Second, the
  trend compared across the model change at all; the record now carries the judge
  `model` and `router_accuracy_state` builds its rolling baseline only from prior runs
  measured by the same judge, so an instrument swap reads as a baseline forming rather
  than as a regression. Re-run live after the fix, `/voss` scores 8/8 with zero
  unanswered cases — the router had never changed. The seven existing trend records
  were backfilled with the judge that produced them.

- **The rule-split inventory guard could only tell you it was unhappy by email.**
  `scripts/rule_split_check.py --check` ran in CI's `sovereignty guards` job and
  nowhere else, so an edit to a snapshotted rule file failed only AFTER the push,
  as a red notification, with main already red. The `.claude/rules/documentation.md`
  migration-cruft table was rewritten in `cbd4ef7`; its frozen 2026-07-20 snapshot
  still carried the old table row, which the extractor had picked up as a directive
  because the cell contained the word "never". No directive was actually lost — the
  row is a tracking-table entry, and the section's one real imperative ("When an item
  clears, delete its row…") survives untouched. The snapshot is re-frozen against the
  current file, and `tests/test_rule_split_check.py` now asserts the committed
  inventories match the live rules, so the pre-push suite refuses the push instead of
  GitHub reporting it afterwards.

- **`/email-intel` no longer burns email it never got a decision on.**
  `scripts/email-intelligence.py` marked every fetched message processed and
  stamped `last_run` at the end of the FETCH — inside the run that only
  proposes actions, before the skill's Phase 3 approval gate. A digest the CEO
  skipped entirely, or a session that died between the fetch and the digest,
  still left those messages recorded as handled, and Phase 1's dedupe filter
  then dropped them from every later run. Silent, and unrecoverable without
  hand-editing `state.json`. Found on 2026-08-09 by noticing the state file's
  mtime matched a fetch that had written nothing else. Fetch and commit are now
  two acts: `--json` emits a `state_commit` block and writes nothing, and
  `--commit-state FILE` replays that block after the approved actions have run.
  The block carries the full filtered id set, not just the ids that survived
  into a conversation — committing from `conversations` alone would resurface
  every internal and noise-filtered thread on the next run. Terminal mode has
  no approval phase and commits inline as before.
  `tests/test_email_intel_state_commit.py` holds the split at the seam and at
  the `main()` wiring, which is where it actually broke.

### Added

- **A turn can no longer end quietly on a broken tree.** `scripts/turn-check.py`
  runs three bounded lanes over the UNCOMMITTED Python edits, and
  `.claude/hooks/turn-check.py` blocks the `Stop` event when one fails. Compile
  every changed file, import every changed library module in one subprocess,
  then run the test files whose names match the changed stems. The stem match
  normalises hyphens to underscores, which is what connects
  `scripts/wizard-verify-key.py` to `tests/test_wizard_verify_key.py`: exactly
  the pair that broke four tests on 2026-08-09 and went unnoticed until a full
  suite was run by hand much later. Seconds, not minutes, because a check at the
  end of every turn only helps if nobody is tempted to skip it. The logic is a
  plain CLI per `.claude/rules/console-first.md`; the hook is a wrapper that
  never becomes fatal and bails on `stop_hook_active`.

- **Four reusable agent roles in `.claude/agents/`:** `crm-reader`,
  `comms-scout`, `datastore-validator`, `draft-writer`. The point is the tool
  list, not the prose. `draft-writer` has no `Bash`, so it cannot reach
  `scripts/send-email.py` whatever a dispatch prompt says, which makes the
  lethal-trifecta control a capability rather than a request; the read-only
  agents hold no write tool at all. What stays in
  `.claude/rules/skill-orchestrator.md` is what no agent file can express: the
  approval gates, the sequential post-approval CRM and pipeline writes, and the
  rule that two agents never write the same contact file.

### Changed

- **Six skills moved from Sonnet to Haiku, on a measurement rather than a
  judgement call:** `/brain-audit`, `/memory-hygiene`, `/radar`,
  `/linkedin-archive`, `/marp`, `/notebooklm`. Eighteen new eval cases were
  written for them, and both models scored 80 of 80 checks across two full
  rounds each. Sonnet 5 intro pricing ends 2026-08-31, after which the gap
  between the tiers is threefold in both directions. Three of the cases had to
  be rewritten first: they asked about content the harness deliberately strips
  (`load_skill_system_prompt` removes the frontmatter), and one demanded the
  literal `Tier A`, which appears nowhere in the body. A check nothing can
  satisfy measures the case, not the model.

- **A provisioned exec workspace now gets the whole guard set.**
  `provision-exec.py` wrote exactly one PreToolUse hook, `protect-corporate.py`,
  on `Write|Edit`. That is one of the seven checks `_dispatch.py` runs: secret
  detection, the personal-thread guard, the docs guard, the cwd anchor, the rate
  limit and the tool budget never ran in an exec workspace at all. It now points
  at `_dispatch.py` on the CEO's three matchers, ships the deny rules, and wires
  the end-of-turn check. It also stops recreating the four backward-compat shims
  that `.claude/rules/documentation.md` has been waiting to delete, which is why
  that removal condition could never previously be reached.

- **Deny rules exist.** The permission config carried 51 allow rules and zero
  refusals. Ten deny rules now cover the raw secret files the assistant never
  needs to read (`.env`, `.sessions/`, `*.pem`, `*.key`), force-push, and the
  flag-first form of `--no-verify`. This is a second layer that does not depend
  on a hook having run; the push-time content scan remains the actual wall.

### Security

- **`cryptography` 49.0.0 carried PYSEC-2026-3552; the pin moves to 50.0.0.**
  It reaches the engine transitively through `requests_ntlm` and `pyspnego`
  (the Exchange NTLM path), `google-auth`, and `pdfminer.six`, so it ships in
  every clone's `requirements.txt` without appearing in `pyproject.toml`. The
  pre-commit `pip-audit` gate fires only when `requirements.txt` is itself
  staged, which is why a vulnerability in a transitive pin can sit through
  commits that never touch dependencies. Bumped with
  `uv lock --upgrade-package cryptography` and re-exported; the lock delta is
  that one package and nothing else.

### Fixed

- **Two ordering tests trusted the wall clock to separate two writes, and WSL2
  does not always oblige.** `test_sorted_by_mtime_desc` slept 50 ms between two
  draft files and asserted the newer one sorted first;
  `test_dismiss_log_recent_orders_ts_desc` slept 10 ms between two log entries
  stamped from `datetime.now`. Both pass in isolation and fail occasionally in
  a full parallel run, because a host clock resync can step the guest clock
  backwards far enough to invert the pair. Sleeping longer only lowers the
  odds. Both now state the two instants outright, one via `os.utime` and one
  via a held clock, so the assertion tests the ordering rule rather than the
  hypervisor. Verified over eight consecutive 8-way runs of the bridge suite.

- **Six modules in the script tree carried standard-library names, and two of
  them were already breaking on the service VM.** Python puts an executed
  script's own directory first on `sys.path`, so running any file in
  `scripts/utils/` handed that directory the right to answer the standard
  library's own imports. `scripts/utils/operator.py` therefore intercepted
  `enum`'s `from operator import or_` during interpreter warm-up and died on a
  circular import through `functools` and `collections`, taking every direct
  run of every module in that directory with it. `scripts/utils/html.py` failed
  earlier and more quietly: `from html.parser import HTMLParser` resolved back
  to itself, so the module could not import its own dependency. Neither
  reproduced on the development laptop, because the distribution's `.pth` files
  load `operator` before any workspace file can claim the name; a bare venv on
  the server had no such head start, which is how an import bug hid behind a
  green test suite for weeks. Renamed: `operator` to `operator_identity`,
  `html` to `html_text`, `trace` to `tracing`, `venv` to `venv_guard`,
  `bridge_daemon/sources/calendar` to `agenda`, and
  `bridge_daemon/refreshers/email` to `mail`. Every import is package-qualified,
  so no daemon was ever affected and no public interface changes.
  `tests/test_no_stdlib_shadowing.py` now fails on any future file in
  `scripts/` or `.claude/hooks/` that takes a standard-library name.

  A test written to demonstrate this exact hazard had been asserting nothing
  since the day it was written. `test_running_the_file_directly_is_the_shape_that_broke`
  put `scripts/utils/` first on the path and imported `operator`, expecting the
  workspace file to answer, and accepted `shadowed or cached`. But `operator`
  is already in `sys.modules` before a `python -c` body runs, so the import
  always returned the cached standard-library module and always took the
  `cached` branch. The half that mattered was unreachable, which is why the
  renamed-away subject of the demonstration did not make it fail. The probe now
  drops the module from `sys.modules` first, so it fails if a stdlib-named
  module reappears in that directory.

- **`Maximum` appeared as `Jordanum` in 16 files, and had been shipping that way
  since the repository was first published.** `git log -S` dates it to
  `c1aedf0`, the squashed initial import of 2026-06-29: a find-and-replace during
  the pre-publication scrub caught a word it had no business touching. The
  corruption was in content clones carry, not in an internal note. The
  always-on `.claude/rules/skill-orchestrator.md` capped parallel agents at
  "Jordanum 5"; `implement`, `osint`, `design` and the pptx brand system carried
  it in their SKILL text; two `skill-creator` validators printed it in the error
  a user sees when a name is too long; and the vendored `docs/assets/mermaid.min.js`
  had it inside mermaid's own diagram-limit messages. Corrected in every live
  file. Records that quote it, the changelog history and saved reports, keep it
  verbatim. **A clone or fork taken before 2026-08-09 still carries it**; a pull
  is the fix, and `grep -rn Jordanum` says whether yours does.

- **The `flag-as-fp` channel wrote to two destinations, one of them the log whose
  emptiness had just been the argument for deleting its own aggregator.** The
  second write went to `_fp_log.jsonl`, which had received zero records in 75
  runs, which is why the 327-line aggregator reading it was removed in v0.8.0.
  Keeping the log alive as a destination preserved exactly the split that removal
  existed to close. The write is gone: a CEO's disagreement with a finding now
  lands as an `fp_flag` row in the same record as the verdict it disagrees with,
  and both the running tally and the human-agreement harness read it there.

- **The `/scrutinize` scheduler role lens matched bare substrings, so its first
  live run flagged its own marker table.** It searched for `apscheduler` and
  `add_job` anywhere in a file's text, which fires on a docstring about
  scheduling, a test fixture, or the lens definition itself. It now reads the
  syntax: an `apscheduler` import or an `add_job` call parsed from the AST. A
  lens whose whole value is precision cannot open its life by flagging itself.

### Added

- **`implement-trajectory-log.py --verify` reconciles a run against its own plan
  file.** Three advisories, each one a defect a human auditor had to catch by
  reading: a file listed under a plan step's "Files affected" that appears in no
  step's `files_affected`; a plan whose Implementation Notes declare more
  deviations than the trajectory carries as `deviation` events; a `deviation`
  emitted before its own step's `step_start`. All three go silent when the plan
  cannot be located, because a missing plan is not a trajectory defect.

- **`implement-trajectory-log.py --list-files --run-id <id>`** prints the deduped
  union of every step's `files_affected`, so the hidden-character scan reads its
  file list off the record instead of having it assembled by hand. The run that
  prompted this scanned 11 files for a run that had touched 22. Nothing had
  escaped, but the evidence covered half the surface it claimed to.

### Added

- **`/census` answers a counting question by walking the corpus instead of
  loading it.** The technique is Recursive Language Models (RLM,
  arXiv:2512.24601) at recursion depth exactly 1: the session writes a traversal
  program, `scripts/census.py` runs it, and only the question, the corpus
  metadata and a schema-validated result travel back. The corpus never enters the
  context window. This exists because of one measurement on 2026-08-13: over
  seven questions whose answer sits in no single file, the incumbent retrieval
  path scored a ceiling of 0.000, all seven at exactly 0.00. That is not a tuning
  problem, since top-K cannot return an answer that lives in no chunk. Questions
  comparing two dense files scored 0.667, so `/census` is deliberately NOT built
  for those and the engine refuses them by design (SRLM, arXiv:2603.15653,
  reports a traversal primitive on an in-window corpus actively hurts).

  Model-written code executes here, which the workspace otherwise forbids. What
  makes the carve-out acceptable is not trust: `scripts/utils/sandbox.py` gives
  the traversal an empty network namespace, an empty environment and a read-only
  corpus mount, and `scripts/utils/census_schema.py` validates what comes back.
  The fourth control protects the PARENT, which holds the credentials and the
  network an injected instruction would need. The four conditions that VOID the
  carve-out are written in `.claude/rules/generated-code-execution.md`.

  Accepted at 6 of 7 on the traversal class and 5 of 5 on the control class,
  against a threshold registered before the first run and never moved, with zero
  confidently-wrong answers and a median of 0.05s against 0.92s. Five acceptance
  runs were needed and every re-run was forced by a defect in the INSTRUMENT
  rather than in the primitive: two oracles were wrong (a substring match read
  the language "Russian" as the country; a predicate was constant-true across its
  whole population, so it measured the population), and the comparability guard
  pinned `git rev-parse HEAD` on a perpetually-dirty tree, so a corpus edit moved
  a truth while the pin held. It pins `corpus_content_sha256` now. One question
  class is withheld from grading BY NAME rather than dropped, because its three
  questions disagree on how to enumerate a table, a rule the question never
  states, so the zero measured the wording.

- **The visual gate resolves the cascade instead of matching words.**
  `scripts/visual-discipline-check.py` had enforced the visual-design rule since
  2026-06-26 with regexes over file contents, which answers "is a forbidden font
  written down here" and almost nothing else. It could not answer what a colour
  resolved to against the surface behind it, or whether the heading hierarchy
  holds. A second engine now sits behind the same facade: the impeccable CLI
  (pbakaus/impeccable, Apache 2.0), pinned at 3.5.0 and invoked through npx
  exactly as marp-cli already is. The fifteen skills the rule obliges to run this
  command inherit it with no skill file changing.

  Measured on this tree the first time it ran: 252 findings on the public
  documentation site are text below the WCAG AA contrast floor, all from one
  colour token, plus 49 heading-hierarchy breaks across the branded doctypes and
  a `font-family: Inter` sitting in the brand stylesheet while the rule declares
  GT Standard as the locked face. None of it is fixed in that change, by
  instruction: `.visual-baseline.json` freezes 399 findings across 38 files the
  way `.lint-baseline.json` freezes lint debt, so the gate fires on what appears
  above the line and each frozen finding surfaces the moment its file is next
  edited. Every calibration entry in `config/visual-check-profiles.json` carries
  the reason it exists, because a silent suppression is indistinguishable from a
  missing rule.

- **The `/scrutinize` judge record is written by the harness, not by prose.**
  Across 75 saved scrutiny reports the mandated `Refutation:` header appears in 8
  and the mandated `## Judge layer` heading in 12, while two NEVER clauses require
  cross-family judging that 17 merely name. Every one of those was a prose mandate
  addressed to a model that can omit it silently. So authorship moves:
  `scripts/utils/scrutinize_record.py` writes one row per judged event, and
  `scripts/scrutinize-dispatch.py` owns the third-party judge call, the family
  assignment, the sensitivity gate and the reproduction commands. `--validate`
  fails in both directions, including a header claiming a pass the rows do not
  show. It cannot make omission impossible, because the Claude judge IS the
  running session, so the code says it makes omission visible and claims no more.
  `REPRODUCED` and `FALSIFIED` are first-class verdicts only the harness may
  write, after four earlier passes had already invented them by hand.

- **Callers ask for a model FAMILY, because no model literal stays correct.**
  Every Claude model id is a pinned snapshot, including the dateless ones, so
  there is no literal anyone can type that stays right. Eight files under
  `scripts/` proved it: `skill-trigger-test.py`, the judge that decides whether a
  skill routes correctly, was judging on a five-month-old model, and
  `draft_critique.py`, which reviews an outbound email before a human sends it,
  was a major version behind. Nothing failed loudly. The work just happened on
  older models. `scripts/utils/claude_models.py` resolves a family to the newest
  release in it through the Models API, in a fixed order: config override, 24-hour
  cache, live API, stale cache, then a baseline floor that is the one version
  literal left. It never raises, so a public clone with no key resolves too, and
  it memoizes a FAILED fetch as well as a successful one, because without that a
  degraded API cost 21.9 seconds over three calls inside a five-minute tick.

  The first cut was a code change two YAML files quietly undid: a daemon config
  set the model by name, `config.get` is always truthy, and the resolver never
  ran. The guard could not see it, because it globbed `*.py` only. Both are fixed,
  and the widened guard immediately caught a literal in a comment written minutes
  earlier.

- **A thread can be quiet on purpose, and the index says so.** A thread carried
  `do_not_remind: true` in its frontmatter, nothing mechanical read it, and every
  rollup listed the thread as ordinary live work. `quiet_until` (dated, expires by
  itself) and `do_not_remind` (indefinite, lifts when the operator raises it) are
  first-class `ThreadFile` fields now, with `is_quiet()` behind them, a
  `thread.py quiet` subcommand, and a `[quiet until <date>]` marker regenerated
  onto the memory index line on every write, so the index loaded into every
  session carries it. The hygiene scan skips a quiet thread instead of nagging it
  as stale, and reports the dated form once it expires, which is what stops a
  freeze from outliving its reason.

- **`thread.py close` and `hold` require `--reason`, and the reason lands in the
  body.** One operator run flipped nineteen threads from active to closed at once.
  `_set_status` wrote two fields, `status` and `last_touched`, so afterwards a
  thread that was genuinely resolved and a thread that merely went quiet look
  identical on disk. Six of the nineteen closed over a loop the deal pipeline
  still showed as live. The reason is now a dated log entry in the thread body
  rather than a memory the operator has to keep. `reopen` is deliberately exempt,
  since it ADDS a thread back to the index and demanding a justification to resume
  work is friction with nothing behind it. The refusal happens before any
  mutation, and a test asserts that, because a guard that half-applies is worse
  than no guard.

- **The partner scorecard is generated from the pipeline.** A hand-written summary
  block held 6 partners against 23 partnership rows, and carried an executed
  worldwide OEM agreement as "In Discussion" for eighty days after signature.
  Four skills read that file as fact. The defect is not the six wrong rows: it is
  that a list was written down twice. `scripts/partner-scorecard.py` now generates
  the block between two markers, the way `pipeline-summary.py` already generates
  the stage counts, and everything a human wrote outside the markers survives
  verbatim. The detailed per-partner profiles stay hand-written on purpose,
  because those are judgement rather than a list. Deliberately NOT gated in CI: it
  reads the private data overlay, which a public clone and a CI runner do not
  have, so a gate there would fail on absence rather than on drift.

### Fixed

- **A Telegram watchman went blind the moment the operator opened his phone.**
  The personal-DM path in `scripts/sentinel.py` opened with
  `if dialog.unread_count == 0: continue`, so the unread badge decided what the
  watchman could see, rather than its own cursor. Neither consequence produced a
  log line. A message read on the phone before the next fifteen-minute cycle was
  never seen again, because the following cycle no longer counted it as unread. A
  conversation where the operator himself wrote last has a zero unread count by
  construction, so it was dropped whole, which means the reader could not see the
  operator's own commitments at all: precisely the class of message a watchman
  exists to remember. Newness is now the dialog's top message id against the
  stored cursor. `iter_dialogs` already carries that id, so the test costs no
  extra API call and the flood-wait budget is unchanged.

- **A dated reminder arrived a week early.** The `/prime` backstop listed
  everything inside a 7-day lookahead beside what was actually due, so a reminder
  dated to take a matter off the operator's mind came back every session for a
  week first. The lookahead is removed, along with the `upcoming()` helper it was
  the only caller of, so re-adding an early announcement is a deliberate act. The
  Telegram path was always due-only; the backstop matches it now. A third defect
  surfaced only because the fix triggered it: `write_thread_file` rebuilt
  frontmatter from a fixed field list, so the first `quiet` command DELETED the
  very keys it was meant to honour. Unmodelled keys are preserved now.

- **The operator's timezone reached the shell but not the callers.**
  `python -m scripts.utils.paths tz` printed the configured zone while
  `get_default_tz()` returned UTC in the same checkout minutes later, with the
  `.env` line present and correct throughout. `HEADING_OS_TZ` reaches
  `os.environ` only through `load_env()` reading the gitignored `.env`, and
  nothing exports it into a shell, so the helper read an environment nobody had
  filled and fell through to its UTC default on a correctly configured host. 61 of
  the 83 files importing the helper never call `load_env`. The daemons do, so
  scheduled work was unaffected and the damage was confined to standalone CLI
  scripts. It surfaced when a thread opened at 00:45 local time was filed under
  the previous day. Fixed at the layer rather than in the caller that exposed it:
  the helper loads the `.env` itself, once per process, and precedence is
  unchanged, because `load_env` uses `setdefault`.

- **Twelve subprocess calls spawned a bare `python`, and this repository already
  had a rule against it.** A fleet operator running their own clone reported three
  failures at v0.8.0 (issues #96, #97, #98). On a python3-only host a bare
  `python` raises `FileNotFoundError` before anything is asserted, and the quieter
  half is worse: where the name does resolve it is the ambient system
  interpreter, so the child runs outside the pinned set and a green run attests an
  environment the suite never ran in. An AST sweep found 12 sites rather than the
  4 the reporter could see. The eight invisible ones are production scripts:
  the secret scanner in `push-all.py`, the memory-index build in `ops-radar.py`,
  three in `ops_signals.py`, one in `crm_next.py`, two in a CRM migration. The
  radar sites degrade to "not due" when the child fails, so a host whose ambient
  interpreter lacks the dependencies got a radar that under-reported in silence.
  All 12 use `sys.executable` now, and
  `tests/test_subprocess_interpreter_guard.py` refuses the pattern across
  `tests/`, `scripts/` and `.claude/`, pinning its own detector against both the
  broken shape and the fixed one so it cannot decay into a check that matches
  nothing.

- **A fireside backup email had never once been sent.** The last-resort path for a
  speaker who never answered the bot's messages shelled out to a bare `python`,
  and the service host has no such binary, so every attempt raised
  `FileNotFoundError` from the first run onward. Nothing outside `errors.log`
  could tell that apart from a quiet week: the exception was caught and logged,
  each recipient was written to the log with `ok=False`, the job printed `sent=0`,
  and its healthcheck was pinged green. It spawns `sys.executable` now, and the
  test asserts the interpreter at the call site.

- **A member who joined mid-cycle was in the roster, in no week, and reported
  nowhere.** `scripts/fireside-bot.py` gained the speaker-side coverage guard,
  the twin of the earlier helmsman guard, with
  `tests/test_fireside_speaker_gaps.py` holding it.

- **CI was red for three refusals that only ever fired where nobody ran them.**
  Each was invisible on an operator machine and each was exposed by a bare runner.
  A test module imported an optional extra at module level, which is a COLLECTION
  error rather than a skip, and one collection error aborts the whole run.
  `scripts/utils/sandbox.py` judged the HOST before it judged the REQUEST, so with
  bubblewrap absent every argument refusal came back as "bwrap is not on PATH",
  and the five tests asserting those refusals are deliberately not marked as
  needing bubblewrap, because their point is that no process is needed. And the
  census overlay guard was rehearsed against an EMPTY tree, which is not the shape
  a bare clone has: `get_data_root()` falls back to the engine's bundled
  `examples/`, one demo thread satisfied "any populated directory", and both
  guarded tests then ran against the engine's own demo files.

- **An exit code arrived with its reason thrown away.** The bridge mail refresher
  logged `producer exited 2; stderr=` and nothing else. Exit 2 is the producer's
  one expected failure, and it reports itself as a JSON object carrying the detail
  and a thread pointer on STDOUT, so the explanation was captured and discarded
  one stream away from where anyone looked. It prefers the structured error now
  and falls back through stderr, stdout, and an explicit "no output on either
  stream". The same call then bumped the inbox freshness clock unconditionally,
  which makes an inbox nobody fetched render as refreshed seconds ago. The version
  still advances on failure, because the browser must re-read to see it, but the
  freshness clock does not, because a run that fetched nothing has established
  nothing about how old the data is.

- **A documented smoke test did not run outside a pre-seeded virtualenv.**
  `scripts/utils/draft_critique.py` offers itself as a smoke test in its own
  docstring and exited `No module named 'scripts'` anywhere the repository root
  was not already on the path, which is every plain venv including the service
  host's. Thirty sibling modules in that package insert the root; this one never
  did.

- **The version-pin guard could not see the file the pin actually survived in.**
  It scanned the skill directory and the dispatcher, and the operator's tool index
  is in neither, while describing the same judge layer in prose: so it carried a
  stale model literal straight through the change whose subject was removing that
  literal, four lines under the entry recording the removal. The guard reads that
  file too now, scoped to lines that mention the skill, because the same file
  legitimately names model versions when describing other tools. It skips rather
  than fails when the data overlay is absent, since a public clone has no operator
  index and a guard that fails on its absence teaches people to delete it.

- **The roadmap told the public the project was six releases younger than it is.**
  `ROADMAP.md` opened with "HEADING OS is `v0.3.0`", on the landing path a new
  reader takes for direction. The version-sync guard existed and did not read that
  file: it held `pyproject.toml`, the README status line and the newest changelog
  heading in agreement, and the fourth surface drifted through six releases
  unwatched. The guard reads the roadmap preamble now, its pre-commit pattern
  fires when that file is edited, and `tests/test_version_sync_guard.py` holds
  both, including the property the widening was for: every guarded surface must
  appear in the hook's `files:` pattern. The guard had no test of any kind before
  this, which is the more useful half of the finding.

- **The style checker's own help text said the skill corpus was ungated, one day
  after it was gated.** `--all` described the skill half as "NOT gated" and
  `--skills` described itself as "ungated on purpose", both written when that was
  true and both false the moment the `documentation-style-skills` hook and its CI
  step landed. A tool that misreports its own coverage is the defect
  `.claude/rules/scope-claims.md` exists for, whichever direction the error runs
  in.

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

[0.14.0]: https://github.com/mishahanin/heading-os/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/mishahanin/heading-os/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/mishahanin/heading-os/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/mishahanin/heading-os/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/mishahanin/heading-os/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/mishahanin/heading-os/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/mishahanin/heading-os/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/mishahanin/heading-os/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mishahanin/heading-os/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mishahanin/heading-os/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/mishahanin/heading-os/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/mishahanin/heading-os/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mishahanin/heading-os/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mishahanin/heading-os/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mishahanin/heading-os/releases/tag/v0.1.0
