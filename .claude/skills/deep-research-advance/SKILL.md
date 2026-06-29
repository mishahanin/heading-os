---
name: deep-research-advance
description: >
  Advanced one-shot deep research. Runs token-heavy web acquisition (Perplexity)
  and reasoning/verification (Kimi) headless in a script, then Claude audits the
  findings, writes a cited report, and optionally distills to Odin. Use for a
  deep, multi-source, fact-checked report on a PUBLIC-web topic. Trigger on
  "deep-research-advance", "advanced deep research", "deep research on [topic]
  with verification". Do NOT use for private/internal questions (the topic and
  corpus go to third-party clouds), quick lookups (use /osint or WebSearch), or
  Odin-brain-only recall (use /odin). BEFORE running, if the question is
  underspecified, ask 2-3 clarifying questions to narrow scope.
argument-hint: "\"<question>\" [--critical] [--audio] [--depth N] [--domains a.com,b.com]"
allowed-tools: "Read, Write, Bash(python:*), Bash(python3:*), Skill"
context: fork
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: partial
  shared_state: ["outputs/research/"]
  triggers: ["deep-research-advance", "advanced deep research", "deep research with verification"]
x-31c-capability:
  what: >
    One-shot deep research that offloads acquisition and verification to
    Perplexity and Kimi, then has Claude audit and synthesize a cited report.
  how: >
    /deep-research-advance "<question>" [--critical] [--audio] [--depth N].
    Report lands in outputs/research/; Odin distillate is proposed for approval.
  when: >
    Use for deep, fact-checked reports on public-web topics. NOT for private
    questions (data leaves to third-party clouds), quick lookups (/osint), or
    Odin-brain recall (/odin).
---
# Deep research advance

Multi-source, verified deep research. The headless script handles token-heavy
acquisition (Perplexity) and reasoning/verification (Kimi). Claude takes the
intermediate output, audits it, writes a cited report, and proposes an Odin
distillate for human approval before anything reaches the brain.

## Guardrail (non-negotiable, state first)

This skill sends the user's question and the gathered web corpus to third-party
clouds: Perplexity (acquisition), Kimi (reasoning and verification). Both are
outside the 31C data boundary.

- **NEVER run on a private or internal topic.** CRM contacts, pipeline data, Odin
  brain content, Exchange mail, Telegram history, partner names, pricing -- none
  of these belong in the question text or in `--domains`.
- If the request references private data, refuse immediately. Point to `/recall`
  (workspace-wide semantic memory) or `/odin` (brain-scoped reasoning) instead.
- If in doubt whether a topic is public-web, ask before running.

## Phase 0 -- Scope

If the question is underspecified -- no clear domain, no time horizon, no
geography where those matter -- ask 2-3 focused clarifying questions before
running. Examples worth asking: "Which jurisdiction's regulation?", "What time
window?", "Vendor landscape or technical depth?" Do not pad; ask only what
materially changes the research angle.

Confirm the topic is public-web before proceeding.

## Phase 1 and 2 -- Run the headless script

Run the acquisition and reasoning phases:

```bash
python scripts/deep-research-advance.py "<question>" [--depth N] [--critical] [--domains a.com,b.com]
```

Notes on flags:
- `--depth N` sets the number of Perplexity angle queries (default 4, max 8).
- `--critical` forces the adversarial audit governor in Phase 3 regardless of
  source count.
- `--recency {hour|day|week|month|year}` sets the Perplexity time window.
  Default is none (full index) — correct for evergreen/footprint research. Use
  a window only for genuinely recent-events research; a narrow window starves a
  footprint search and returns off-topic recent noise.
- `--domains` accepts a comma-separated allow-list of domains to constrain
  Perplexity queries.
- `--audio` is a **skill-level flag** handled in Phase 5. Do NOT pass it to the
  script.

The script prints the run directory path as its last stdout line. Read the
intermediate file:

```bash
# <run_dir> is the last line of the script's stdout
Read <run_dir>/intermediate.json
```

`intermediate.json` shape (all fields Claude relies on):
- `question` -- original question string
- `generated_at` -- ISO timestamp
- `depth` -- number of angles requested
- `critical` -- boolean, whether --critical was passed
- `angles[]` -- list of research angle strings
- `sources[{id,url,angle}]` -- source list (id is the citation key)
- `corpus[{angle,content,source_ids[]}]` -- gathered text per angle
- `kimi_analysis{summary, claims[{claim,status,confidence,source_ids[]}], contradictions[]}` -- Kimi's reasoning pass
- `degraded` -- boolean; true when the script ran in degraded mode
- `degraded_reason` -- string explaining the degradation

**Degraded and error handling:**

| Condition | Action |
|---|---|
| `degraded == true` | Note `degraded_reason` prominently in the report header |
| Script exits 3 (no corpus) | Fall back to a Claude-only `/deep-research`-style pass; note "FALLBACK: headless script returned no corpus" in the report header |
| Script exits 2 (bad args) | Fix the invocation or ask for clarification; do not proceed |
| `kimi_analysis` key absent or empty | Fall back to Claude-only synthesis; note "FALLBACK: Kimi analysis unavailable" in report header |

## Phase 3 -- Audit governor (conditional)

**Fire the adversarial audit when EITHER:**
- `len(sources) > 12` (read from `intermediate.json`), OR
- `critical == true` (read from `intermediate.json`)

**When the governor fires:**
For each claim in `kimi_analysis.claims` where `status == "supported"`, attempt
to refute it from the corpus alone -- cite the `source_ids` used. If you cannot
find corpus evidence that defends the claim, downgrade its status to
`"unsupported"` and record a one-line reason. Surface any claims that flip
status in a "Governor findings" block in the report.

**When the governor does not fire:**
Accept Kimi's analysis as-is. No re-examination of individual claims.

**Always state in the report** which path ran: "Governor: FIRED (N sources /
critical flag)" or "Governor: SKIPPED (N sources, non-critical)".

## Phase 4 -- Synthesize the report

Resolve the output path. The report lands under the data workspace:

```bash
python3 -c "from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir('research'))"
```

Write the report to `<outputs_research_dir>/<run_dir_basename>/report.md`,
following `.claude/rules/output-naming.md`.

Report structure (in order):

1. **Header block** -- one line each: question, depth, `critical` flag value,
   governor path, degraded state (or "not degraded").
2. **Executive summary** -- 3-5 sentences, committed stance, no hedging.
3. **Findings by angle** -- one section per angle from `angles[]`, grounded in
   `corpus` content, source citations inline as `[id]`.
4. **Claims table** -- Markdown table: Claim | Status | Confidence | Sources.
   Use post-governor statuses.
5. **Contradictions** -- list from `kimi_analysis.contradictions`; if empty,
   state "No contradictions flagged."
6. **Source list** -- one line per source: `[id] <url>` (angle tag optional).

Prose obeys `.claude/rules/humanization.md`: specificity density, committed
stance, no banned vocabulary, no empty structural patterns. Sentence-case
headings throughout.

Run the sanitizer and report the result:

```bash
python scripts/sanitize-text.py <report_path> --scan
```

State: "Word count: X. Hidden characters: clean." If the scan found and removed
characters, say so explicitly.

## Phase 5 -- Persist (gated)

Two optional persistence actions. Both are HARD STOP gates; never auto-execute.

**Odin distillate (always proposed):**

Extract the confirmed high-confidence claims from the claims table (status
`"supported"`, confidence >= 0.75) plus their source pointers. Present the
proposed `/odin learn` entries -- one per claim, formatted as the CEO would
approve them -- and STOP.

> "Proposed Odin distillate below. Reply 'approve' to write these to the brain,
> or name specific entries to skip."

Do not call `/odin learn` until the CEO replies with explicit approval.

**NotebookLM audio (only when `--audio` was passed):**

If `--audio` was in the original invocation, after the Odin gate (or if the CEO
skips it), surface a separate confirmation:

> "This will push the corpus to your Google NotebookLM account for an audio
> overview. Confirm to proceed."

Only after explicit confirmation, invoke `/notebooklm` with the corpus. This
sends data to the user's Google account -- separate confirmation required.

## Post-synthesis brain audit

Invoke `/brain-audit` after Phase 4 synthesis and append its footer to the
report:

```
Skill(brain-audit, args="--sources <comma-separated run_dir files> --entity <entity name if question is entity-scoped>")
```

If the question is not entity-scoped (e.g. a regulatory or market topic), omit
`--entity`. The audit gracefully degrades to a no-entity footer.

## Voice

Match `reference/misha-voice.md` and the humanisation rule. Committed stance,
specific, no hedging. Plain hyphens -- not double dashes. ODUN.ONE, DPI+, Tribe
per `.claude/rules/terminology.md`.

## NEVER

- **Never run on private/internal topics.** CRM, Odin brain, pipeline, partner
  data -- none of it goes near the question or the cloud prompts.
- **Never auto-write to Odin.** The distillate is proposed, never executed
  without explicit CEO approval.
- **Never auto-send the NotebookLM push.** Requires its own explicit confirmation
  after the Odin gate.
- **Never inject business or private context into cloud prompts.** The question
  and `--domains` must contain only public-web information.
- **Never claim a fact absent from `intermediate.json` sources.** If the corpus
  does not support a claim, say so; do not fill gaps from training knowledge.
- **Never pass `--no-verify` on git commits** made as part of any task in this
  workspace.
