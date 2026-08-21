<!-- version: 5.0.0 | last-updated: 2026-08-22 -->
# HEADING OS v0.13

**The reasoning behind every change became searchable, and the thing that does the searching now says which one it was.**

A repository keeps two records. The tree says what the code is. The commit log says why it became that. This engine had good retrieval over the first and none at all over the second: 1,093 commit messages carrying the reasoning behind every decision here, findable only by exact substring. Ask `git log --grep` for "почему мы отказались от серверной базы" and it returns nothing, because those words appear in no commit. That was the largest body of written thinking in the project and it was invisible to the tool built to remember things.

It is now indexed by meaning and it answers, measured at **85%** on questions that `grep` provably cannot answer at all.

The second half of the release is what that work exposed. Everything retrieved by meaning depends on one model computing one set of numbers. The store recorded the model's NAME and nothing else — and a name is not an identity. Two hosts can serve the same tag and different weights the moment one of them updates, at which point the stored vectors and the new ones are not comparable, cosine returns a plausible number either way, and the memory answers confidently and wrongly with nothing on disk to reveal it. Provenance now lives in the store: model, host, and the **digest of the weights**, which is the only field that moves when a model is silently replaced.

The third part is a capability that was built, measured, and **withdrawn**. Symbol search — "find the function that does X without knowing a word in it" — answered 46% against a bar of 70% agreed before the build. It is documented here in as much detail as the features that shipped, because a negative result nobody records gets rebuilt.

| | |
|---|---|
| **4** | commits since v0.12 |
| **6,128** | automated tests across 412 test files |
| **563** | of those in `tests/security/`, run on every commit |
| **1,093** | commit messages now searchable by meaning |
| **85%** | recall on grep-blind questions, against an 80% bar |
| **46%** | recall of the symbol layer, against a 70% bar — withdrawn |
| **0.99997** | cosine agreement between the two embedding hosts |
| **0** | messages sent without a human click |

Released 22 August 2026. Every figure on this page comes from the engine's own records, measurements, and test suite. None are estimates. The full commit-level record is in [CHANGELOG.md](https://github.com/mishahanin/heading-os/blob/main/CHANGELOG.md).

---

## 1. Commit messages are searchable by meaning

*The headline change, described in full*

### What was missing

`git log --grep` is exact-substring matching. It finds the word you typed and nothing else — not a synonym, not a paraphrase, and not the same idea in another language. The operator of this engine works in Russian and English and the commit log is written in English, so the failure was total rather than partial: a Russian question about a decision found zero of the commits that recorded it.

Everything else here was already retrievable. Skills, rules, notes, plans, outputs and threads all have vectors. The commit log had no index of any kind, and it is the only place where *why* is written down at the moment the decision was made, by the person making it, before the reasoning was smoothed over by hindsight.

### How it works

A commit is not a file, so `layers:` in `config/memory-index.yaml` gained a `source:` kind. The default kind walks a glob; the new `git-log` kind reads messages. The two branches share the pending, claimed and prune bookkeeping and nothing else. No schema change was needed — the `notes` table was already generic enough to take a commit row unaltered.

Two layers ship, one per side of the engine/data seam:

- **`commit-engine`** — 607 rows, built from the engine clone into `.memory-index-code/index.db`, reachable through the `code` collection.
- **`commit-data`** — 486 rows, built from the private overlay into `.memory-index/index.db`, reachable through a new `history` collection that the default `/recall` deliberately does not search. Commit prose is a different kind of answer from a note, and mixing it into every recall would dilute both.

Query it directly:

```bash
python scripts/memory-index.py query "почему коммиты стали искаться по смыслу" --layer commit-engine
```

### How it was measured, and why the method matters more than the score

A 25-question set was written and **frozen before the index was built**, then split by a mechanical rule rather than by judgement. Every word of four characters or more in a question, plus every adjacent word pair, was run as `git log -i --grep`. A question is **Set A** when no probe from it puts the target commit inside a result of five or fewer; **Set B** when at least one probe does.

That split is the whole design. Set A is the justification — questions the existing tool provably cannot answer. Set B is the guard — questions it already answers, which the new index must not bury. A single blended score would hide a system that is impressive on paper and worse in practice.

| | Result | Bar | |
|---|---|---|---|
| **Set A** (grep-blind) | **11/13 = 85%** top-5, mean rank 1.2 | ≥ 80% | **PASS** |
| **Set B** (grep answers) | **12/12 = 100%** top-5, mean rank 1.0 | no regression | **PASS** |

Measured on the operator's real query path, with no forced threshold.

### Three things this build got wrong first

**The scoring script flattered the tool.** Its first version passed `--threshold 0`, which measures raw ranking rather than what the command actually answers, and reported 85% while the real CLI answered 77%. A score taken on a path the operator never uses is worse than no score at all. It now runs the default path and accepts `--threshold` only for explicitly measuring raw ranking.

**The prose-tuned confidence cut does not transfer to commits.** A paraphrased or Russian question finds its commit at cosine 0.456–0.597. A keyword query hits the same index at 0.590–0.697. Those bands barely overlap, and the global 0.55 sat between them: seven of 23 correct answers were ranked FIRST and then reported as "a gap in this area of memory".

| Threshold | Set A | Set B | False-confident on 6 nonsense queries |
|---|---|---|---|
| **0.45** (shipped) | **85%** | 100% | 1 |
| 0.50 | 77% | 100% | 0 |
| 0.55 (prose default) | 77% | 100% | 0 |

`threshold` is now a per-layer key, set to 0.45 on the commit layers only. `content` keeps 0.55 untouched, because a global drop would have bought commit recall at the price of prose precision. The cost is stated rather than waved away: one nonsense query in six now gets a confident-looking hit on this layer.

**Both numbers are real, and neither may be quoted without its threshold.** During this release one of them was "corrected" to the other in a reference file. That edit was wrong, was made by trusting a summary line instead of opening the record, and was reverted after re-running the measurement.

**Rebuilding one layer deleted 122 rows of another.** The build's prune removes every stored path the pass did not claim, and a single-layer pass claims nothing else. It is now scoped to layers the pass actually walked, plus layers that no longer exist in the configuration at all, so a dropped layer is still cleaned up. Two tests hold both halves.

### What is deliberately excluded

**The air gap refuses a WHOLE commit, never merely the denied file inside it.** A commit that touches any `personal` path is skipped entirely. The subject line is prose: "closed the villa purchase" describes the change as completely as the diff does, so indexing the message of a private change leaks the change even with the path dropped. It refused 14 data commits.

**153 `chore: workspace backup` subjects are excluded as noise** — a fifth of the data repository's history, answering nothing, with near-identical vectors that would crowd real hits out of every result.

### The A/B that was run rather than assumed

The design called for measuring whether the changed-path list belongs in the embedded text. Both variants were built and both numbers are recorded: identical hit rates, mean rank 1.2 with the list against 1.4 without. The list ships. The loser's number is in the design record so the question is not reopened from memory.

---

## 2. The embedder now says which one it was

*The control that makes silent memory corruption impossible*

### The question that had no answer

The operator asked whether the same embedding model is always used. Nothing on disk could answer it. The store's `meta` table held the model NAME, and a name is not an identity — `bge-m3` on two hosts is one tag and can be two different sets of weights.

The consequence is specific and nasty. Vectors from two models are not comparable, and cosine gives no hint: it returns a plausible number either way. A half-and-half store answers confidently and wrongly, forever, and the only way to discover it is to re-run both hosts by hand and compare.

### What was measured before anything was built

This engine runs two Ollama daemons: one on the Windows side with the GPU, one inside WSL on the CPU. Both were checked.

| | Windows GPU | WSL CPU |
|---|---|---|
| Digest | `7907646426070047…` | `7907646426070047…` |
| Quantisation | F16 | F16 |
| Parameters | 566.70M | 566.70M |

Cosine agreement on the same text: **0.99997**. That is float noise from different compute kernels, four orders of magnitude below the 0.12 near-miss margin. The risk had not been realised. The controls ship anyway, because "we checked once" is not a control.

### The three controls

**Provenance is recorded.** `meta` now carries `model`, `embed_host` and **`model_digest`** — the sha256 of the weights. A build compares all three against the store and names any drift.

**The digest is the one that matters, and it answers the follow-up question directly: how do the two installations stay in sync? They do not.** Nothing synchronises them. They are independent services that happen to hold the same weights today, and an auto-update on either swaps those weights under an unchanged tag. Model name unchanged, host string unchanged, every vector written afterwards incomparable with every vector written before. The digest is the only field that moves, and drift prints `WEIGHTS CHANGED`.

**A build refuses the fallback host; a query does not.** The asymmetry is deliberate and the reason is cost, not caution:

| | Behaviour | Why |
|---|---|---|
| **Build** | Exits 1. `--allow-host-fallback` is the named override. | Writes vectors that live in the store for months. An unintended embedder here IS the split brain, and nobody would find out. |
| **Query** | Proceeds, loudly. | Embeds one throwaway vector against 0.99997 agreement. Refusing recall over float noise trades a live capability for an imaginary risk. |

**The announcement is loud, and it actually arrives.** The red banner is emitted from `load_config`, through which every subcommand passes, so a command added later cannot forget it. That alone was not enough: the recall hook that runs on every operator prompt captures the backend's stderr and discards it on a zero exit, so the banner would never have reached the one surface the operator reads all day. The query JSON now carries an `embed_fallback` field and the hook renders it into the session.

### Both stores were rebuilt on the pinned host

| Store | Rows | Host | Digest | Integrity |
|---|---|---|---|---|
| `.memory-index/index.db` (data) | 15,372 | `…:11436` | `7907646426070047…` | ok |
| `.memory-index-code/index.db` (engine) | 10,337 | `…:11436` | `7907646426070047…` | ok |

### A speed measurement that changed the reason for the pin

The accelerated host was adopted in August on a figure of "~1.9× faster embeddings". Re-measured for this release with both daemons warm, three runs per point:

| Mode | Windows GPU | WSL CPU | Ratio |
|---|---|---|---|
| **Batched at 32** (what a build does) | ~30 texts/s | 21–26 texts/s | **1.2–1.5×** |
| **One text per request** | 9.8 texts/s | 12.7 texts/s | **0.8× — the GPU is SLOWER** |

`bge-m3` occupies 0.66 GB of video memory. On a model that small, per-request overhead and the WSL-to-Windows network hop consume most of the acceleration, and on a single request they exceed it. Raising the batch does not rescue it: 32 and 128 differ by 3%, and 256 is worse.

Both re-measurements sit above the original figure on **both** hosts, which points at a difference in method rather than a change in hardware. The August method was not recorded, so the two sets are not comparable and neither is retracted. What changed is the reason to keep the pin: **single provenance, not speed.** Four documents that carried the bare ratio now carry the batch size that makes it mean something.

---

## 3. Symbol search: built, measured at 46%, withdrawn

*A negative result documented at the same length as a shipped feature, because one that is not written down gets rebuilt*

### What was attempted

`.codegraph/codegraph.db` holds every function, method, class and route in the engine with exact line ranges and 46,558 edges. Its only text search is FTS5 — exact tokens. So "the check that refuses an ungated send" finds nothing unless you already know a word in the file. The plan was to embed the symbols themselves, with every row carrying its CodeGraph node id so a vector hit feeds straight back into the graph for callers and blast radius.

It was built. All **9,608** symbols were indexed.

### The bar, and the kill line, agreed before the build

Set A ≥ 70% top-5. Set B no regression. **Kill criterion: Set A below 50% stops the phase — ship commits only, do not tune.**

The two sets aim at the *same* 13 symbols, varying only the register of the question. That is the control: holding the target fixed isolates the effect being measured.

| | Result | Bar | |
|---|---|---|---|
| **Set A** (grep-blind intent) | **6/13 = 46%** | ≥ 70% | **FAIL, below the 50% kill line** |
| **Set B** (query names the symbol) | 11/12 = 92% | no regression | pass |

### Two excuses were raised and both were refuted by measurement

This matters more than the score. A killed feature with an unexamined excuse attached gets reopened in a month as "it probably just needed tuning".

**"The vectors do not separate."** Refuted. Mean top-1-minus-top-10 cosine spread is 0.0338 for the symbol layer against 0.0388 for `commit-engine` — statistically the same, and `commit-engine` passes at 85%. Separation is not the differentiator.

**"The recency × importance re-ranker is wrong for code and pushed the targets out."** Refuted, and this is decisive. Scoring all 9,608 rows by **raw cosine alone** — no RRF, no re-rank, no threshold — gives **6/13 = 46%**, identical to the shipped pipeline. The ranker changes nothing. The embedding never had the answer.

### Where the correct answers actually sat

| Rank | Target |
|---|---|
| 1 | `_pid_is_browser`, `_domain_of`, `resolve_entity_ref` |
| 2–4 | `kimi_model`, `census_schema.validate`, `count_crm` |
| 12–29 | `splice_region`, `auto_slide_breaks`, `is_sensitive_key` |
| 75–124 | `answer_callback_query`, `atomic_write_text`, `select_sessions` |
| **1016** | `load_env_key` |

Rank 117, 124 and 1016 are not near-misses a threshold or a weight could recover. `atomic_write_text` documents itself as "Write *text* to *path* atomically via a same-directory tempfile" and the question was "как записать файл так, чтобы обрыв не оставил половину". The concepts match exactly. The vectors do not.

### What this establishes, and what it does not

**Established:** embedding a code slice does not retrieve that slice from a description of its *intent*, at this corpus size, with this model. Set B's 92% proves the plumbing is correct and that name-search is all the layer does — which `git grep` and CodeGraph's FTS5 already do, for free, in under a millisecond. The layer adds nothing over the tools already present, which is exactly what Set A was designed to detect.

**Not established:** that symbol search is impossible. This measured ONE design — signature plus source slice plus docstring, capped at 2,000 characters, embedded whole by `bge-m3`. Embedding the docstring alone, an LLM-written summary per symbol, or a code-specialised embedder are untested. Restarting is a separate decision with its own bar, not a continuation of this phase. **No tuning was attempted, per the frozen rule.**

### One finding that outlives the result

**CodeGraph's `docstring` column is not usable and the reason is a parser defect.** It reports 12.4% docstring coverage. Parsing the same tree with Python's own `ast` reports **52.0%**. The gap is that CodeGraph attributes the section banner *above* a symbol — a `# =====` comment line — instead of the string inside it. **582 of its 1,180 "docstrings" are banners.**

So the boundary is: CodeGraph supplies identity, location and edges, which is what it is good at; the source file on disk supplies every character of text. That is not a complaint about the tool. It is a boundary, and it outlives this phase.

### How it was withdrawn, including the wrong turn

The layer definition in `config/memory-index.yaml` is **commented out**, not deleted, with the numbers in the comment beside it. Four lines plus one collection entry restore it.

The first attempt removed the layer from its collection and left the definition in place. That is an *orphan*, and an orphan is neither built nor queried while printing `layers in no collection` on **every** invocation — including the recall hook that fires on each operator prompt. It also made a documented fallback false: `--layer symbol` answered "index is empty". Three documents had already stated that fallback as fact before it was tested.

`scripts/utils/symbol_source.py` and its 12 tests stay in the tree, so a future attempt starts from a measured negative rather than from nothing.

---

## 4. A coverage report instead of an edge table

*A planned feature measured before it was built, and reduced on the evidence*

The plan called for a persisted `(prose_file, line, code_path)` table answering "which prose describes this code". The extraction already existed inside the path-reference auditor, so the table was measured before a line of it was written.

| | |
|---|---|
| Edges the table would hold | **28,067** |
| From `outputs/` and `plans/` — a handoff *mentioning* a path | **16,530 — 59%** |
| Distinct prose files naming code | 1,271 |
| Engine Python files named in no prose at all | 19 |
| Named in no NON-ARCHIVE prose | **57** |

Three readings, each removing a reason to persist:

1. **The point lookup does not need a store.** "Who mentions `scripts/memory-index.py`?" is `grep -rn` across both roots in **0.33 s**, returning 240 sites. A table saves a third of a second and costs a schema, a migration path, and a second thing to keep in sync.
2. **The aggregate answer is 57 lines.** "What is undocumented?" computes in **0.6 s** from the same extraction.
3. **The table would be majority noise.** A handoff summary quoting a filename in passing is not documentation of that file, and 59% of the edges are exactly that. The persisted artifact would make the signal harder to find — the opposite of the point.

What ships instead:

```bash
python scripts/check-path-references.py --coverage        # human
python scripts/check-path-references.py --coverage --json # machine
```

Three honesty properties are tested rather than assumed:

- **Archive prose does not count as documentation.** `outputs/`, `plans/archive/`, `chronicle/`, `docs/superpowers/` and `threads/` are excluded from the verdict. Without the exclusion the report reads "everything is documented", because the archives quote nearly every path in the tree at some point.
- **The overlay's absence narrows the claim and says so.** On a clone with no private overlay, a file documented only there reads as undocumented, and the report states which prose sources it actually read.
- **The `__init__.py` drop is printed, not swallowed.** A package marker is not a documentable unit, but a narrowed check that prints like a complete one is the exact defect this project's scope-claims rule exists to stop.

It is advisory and gates nothing. The existing `--check` gate is untouched, so CI still gates only on the one claim it can verify without the overlay.

---

## 5. A second Ollama daemon that nothing was watching

On 20 August the Windows-side Ollama self-updated, the restarted tray application inherited its parent's environment instead of the user-level `OLLAMA_HOST`, tried to bind the default port that `wslrelay.exe` holds, and crash-looped roughly once a second for **16 hours** — 6.9 MB of one repeated line in its log.

The existing health signal reported green throughout. It probes one address, and that address was the healthy CPU daemon. Every caller degraded silently to the slower host and nothing said so.

A second signal, `ollama_accel`, now watches the accelerated host specifically. It reads the same preference the memory index reads, in the same order, resolves it with a new `candidate_url()` and probes it. "Not configured" and "local-only" both resolve to *not configured*, never to *due*, so a workspace with one daemon never sees a false alarm.

It is Tier B on purpose: the daemon lives outside this operating system, nothing here can restart it, and a Tier-A signal stays invisible until two auto-heals have failed.

The same self-update also recreated three broad inbound firewall rules that had been replaced with one narrow WSL-only rule two days earlier.

---

## 6. Markdown and SQLite, named literally

A rule now states what may hold state in this workspace: **Markdown files and SQLite. Nothing else.** Postgres, MySQL, Redis, Qdrant, Neo4j, Elasticsearch and every other server-process database are ruled out — and so are LanceDB, Kuzu, DuckDB, usearch, LMDB and every other embedded store that is not SQLite.

"Embedded" is deliberately not the test. A rule that asks "does this add a process?" needs a judgement every time it is applied. A rule that names SQLite literally is answered by reading a file header.

The reason is a question nobody can answer: fsync pass-through on ext4-over-VHDX under WSL2 is undocumented, so a database daemon's write-ahead promise is unverifiable in this environment. SQLite inherits exactly the same uncertainty and adds nothing else — no daemon, no port, no service account, no separate backup path.

It governs **data and state**, not configuration. `config/routing-map.yaml`, `config/tool-risk.json`, `pyproject.toml` and `.pre-commit-config.yaml` stay whatever format their consumers expect. And it governs only what runs *in* this workspace; it says nothing about what any product may use.

---

## 7. Eight paths the prose had outlived

A sweep found eight places where documentation named a file that does not exist — the rot you get when a script is renamed and the text describing it is not.

Among them: an `/odin` ingest command that could not run, because it named the browser helper at the top level of `scripts/` while the file actually lives at `.claude/skills/playwright/scripts/pw.py`. A security page describing two hook files deleted months earlier. A models page listing a file in a table of files that exist. An architecture page calling two shipped capabilities pending. Three files still citing a rule and a vault directory that a later mechanism replaced.

All eight are fixed, and the gate is the other half or the same eight return. `check-path-references.py --check` runs in pre-commit and in CI, and fails when tracked Markdown gains a **new** reference to an engine path that does not exist.

It is deliberately narrow about what it establishes. Only **engine-routed** paths are checked, resolved through the routing map and never through the disk — the private overlay is absent on a public clone, so its absence is not evidence. The answer is therefore identical on the operator's machine and on a CI runner. One class is filtered rather than listed: a path `.gitignore` covers, such as runtime state that exists locally and in no clone. That distinction was itself learned from a CI break during this release.

Placeholders, regex fragments and correct prose about deleted things live in a frozen baseline, each carrying the reason it should not exist — an entry without a reason is indistinguishable from rot someone gave up on. The ratchet only shrinks, and a test fails on a stale entry.

---

## 8. Six defects found in this release's own work

Written down because a release that lists only what worked is a release that teaches nothing.

**A test that could not fail.** The air-gap security test was written to assert "with no deny list the commit is indexed; with one it is not", so the fixture could not pass vacuously. **It failed.** The commit stayed refused with empty deny arguments, because the air gap carries a hardcoded floor that a caller's arguments *add* to and can never subtract from. That is a stronger guarantee than the one being tested, so it is what the test now asserts: a future caller who forgets the deny arguments still cannot index a private commit.

**`--json` that emitted prose.** The empty-index path printed a human message regardless of the flag. The recall hook's parser raised, it logged "unparseable JSON" and went silent — safe, but *blind*: an empty index and a broken backend became the same observation. The scoring script died on a raw traceback. Fixed at the source, with the contract asserted from the hook's side.

**A diagnostic that stopped the work.** The new digest lookup caught network failure but not a malformed URL, so a host with no scheme — a configuration typo, or the test suite's stub value — aborted a build with `unknown url type`. It broke 11 tests, which is how it was found. A diagnostic must never be the thing that halts the job it was added to observe.

**A URL opened without checking its scheme.** The same lookup took a host from configuration and opened it. `file:///etc/passwd` would have been opened and read. The project's full-ruleset lint ratchet blocked the commit and the guard that already existed elsewhere was applied.

**A correction that was itself wrong.** A reference file was edited to say the commit layer scores 77% and that an earlier 85% was mistaken. Both numbers are real and belong to different thresholds. The edit was made by trusting a summary instead of opening the record, and was reverted after re-running the measurement.

**Dynamic SQL where none was needed.** An f-string built the placeholder list of a query. The values were bound and there was no injection hole, but the shape is what the next author copies before interpolating a value into it. It is now a `json_each` lookup with no dynamic SQL at all.

Two of these six were caught by gates that blocked the commit rather than by review. That is the intended order.

---

## 9. What this means for you

- **Ask the history why.** `python scripts/memory-index.py query "<question>" --layer commit-engine` answers in either language, from paraphrase, at 85% on questions `grep` cannot answer at all.
- **Commit prose stays out of your normal recall.** The data-side commit layer sits in its own `history` collection, so `/recall` is unchanged.
- **If you run more than one Ollama, pin one and let the engine police it.** Set `host:` in `config/memory-index.yaml`; a build on any other host now refuses, and a query on any other host announces itself in red both in the terminal and inside the session.
- **Check what built your index:** read `model`, `embed_host` and `model_digest` from the `meta` table of the store. If your store predates this release it has no digest, and the comparison stays silent rather than claiming a match it cannot support.
- **`host:` accepts `auto:<port>`**, which resolves the current default gateway at call time. Use it to reach an Ollama on the Windows side of WSL2, where a literal address stops working at the next restart.
- **Find undocumented code with `check-path-references.py --coverage`.** Advisory; it gates nothing.
- **Do not add a database.** State is Markdown or SQLite. A tool whose default store is neither is a rejection to raise, not a dependency to add.
- **Nothing about what the engine publishes has loosened.** No private data in the engine, and the push-time content wall still has no override flag.

---

## 10. Honest limits

*What this release does not do, stated here rather than discovered later*

- **85% is 11 of 13.** Two questions in the frozen set are answered wrongly, and a 13-question set has wide error bars. The number is a floor established by a fixed method, not a precision claim.
- **The commit layer's threshold buys recall with precision, and the price is measured.** At 0.45 one nonsense query in six gets a confident-looking hit on this layer. At 0.55 none do, and recall falls to 77%. That trade is a choice, not a free win.
- **A commit message is a claim, not a fact.** The index retrieves what the author wrote at the time. Where a message is wrong, incomplete, or optimistic, the index will retrieve it faithfully and confidently.
- **Only the engine's own history is indexed on the public side.** 607 commits. A fork with different history gets different answers, and none of the numbers here transfer to it.
- **Symbol search does not work at this corpus size with this model.** That is a measurement of one design, not a proof about the problem. It is stated at length in § 3 precisely so nobody has to guess which.
- **The two embedding hosts were measured on one day.** They agreed to 0.99997 then. The controls exist because that measurement expires; they do not make the two hosts identical.
- **The digest check runs at build time, not at query time.** A query on a drifted host announces the host change, which is enough to notice, but it does not compare weights.
- **The speed figures are from one laptop with one integrated GPU.** They are not a claim about GPUs generally, and the batch-size sensitivity is likely to differ elsewhere.
- **The coverage report reads Python under `scripts/` and nothing else.** It says nothing about skills, rules, hooks, or the private overlay's code.
- **`docs/EXTENDING.md` carries 11 pre-existing style errors and is not in the twelve pages the style gate checks.** Surfaced here rather than fixed, because it is unrelated to this release's scope.
- **This page is not inside the style gate.** That gate covers the twelve pages a reader executes plus the skill instruction bodies, and a release narrative is neither. The notice below is approved legal wording and stays verbatim, long sentences included.

---

## 11. Notice on names and examples


Official. Applies to this document and to everything else the project publishes. The full notice is [DISCLAIMER.md](https://github.com/mishahanin/heading-os/blob/main/DISCLAIMER.md) in the repository root.

**Every name, company, contact detail, figure, and scenario used as an example in this document, in the HEADING OS engine repository, in its documentation, and in any artifact generated from it is invented.** Each exists for one purpose only: to show how the software behaves. No example is a record of a real person, a real organisation, or a real transaction, and no example is derived from, based on, or modelled after any real party.

Placeholder names are drawn deliberately from conventional stand-in vocabulary, so that it is plain on sight that no real party is being described: `Acme`, `ExampleTelco`, `ExampleCorp`, `Northwind`, `Contoso`, `Globex`, `Initech`, `Jane Doe`, `John Smith`, and similar.

**This declaration holds without exception, including where a name turns out to match something real.** A placeholder is chosen to be invented; the supply of words is finite, and a chosen word may coincide with the name of an actual party somewhere in the world. That coincidence changes nothing about what the example is. **Presence in this project is never evidence of a relationship, a dealing, an interest, or a fact concerning anyone.** And if real content ever appears here notwithstanding the controls below, it is present in error, it is not published as a statement about anyone, and it does not become a claim by virtue of having appeared.

**Any resemblance** between a name, detail, or figure used here and an actual person, living or deceased, or an actual company, organisation, product, or event, **is unintended and coincidental.** No example is a statement of fact about any real party, and nothing in an example should be read as an allegation, an assessment, an endorsement, or a disclosure concerning anyone.

!!! note "One deliberate exception, stated plainly, because precision protects better than a sweeping claim"
    **The author's own identity is real and published on purpose:** Misha Hanin, reachable at misha.hanin@odinix.com. Statements about his own role, work, and affiliations are his own.

    **Project ownership:** HEADING OS is a personal project by Misha Hanin. 31 Concept is not involved in it, does not maintain, sponsor, or endorse it, and bears no responsibility or liability for it. Vocabulary resembling 31C's appears as illustrative example vocabulary only; those marks belong to 31 Concept.

Real operator data is never present in the published repository. It lives in a separate private store that is never published, and the boundary is enforced by code on the publishing path rather than by convention: a routing map declares what may be shared, write-time guards refuse private material entering the shareable tree, and a content scan with no override flag refuses the push outright on a match. An example that resembles something real is therefore a coincidence of naming, not a disclosure of records.

!!! note "If you believe an example resembles you or your organisation"
    Write to **misha.hanin@odinix.com** with the subject line **"HEADING OS naming"**, naming the file or page and the detail concerned. You do not need to demonstrate harm, assert a legal right, instruct a lawyer, or explain why it matters to you. A request is enough.

    Acknowledgement follows within five business days. The material and its occurrences elsewhere in the project are reviewed, and where confirmed, the name or detail is changed or removed in the next release, with nothing left in place pending an argument about whether the resemblance was close enough to count. Change or removal is not an admission that any resemblance was intended or that anything was wrongly done; it is a courtesy extended on request. Reports involving real personal data, credentials, or confidential information are handled as a security matter at the highest priority.

Nothing in this project or its documentation is legal, financial, tax, security, or other professional advice. The software is licensed under the Apache License, Version 2.0, and is provided on an "AS IS" basis, without warranties or conditions of any kind; the disclaimer of warranty and limitation of liability in Sections 7 and 8 of that licence apply in full. Where anything published by this project could be read as describing a real party, this notice governs and that reading is disclaimed. Full notice, version 1.0, effective 8 August 2026: [DISCLAIMER.md](https://github.com/mishahanin/heading-os/blob/main/DISCLAIMER.md) in the repository root.
