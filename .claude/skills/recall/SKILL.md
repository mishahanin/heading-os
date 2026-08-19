---
name: recall
description: "Semantic recall across the whole workspace - Odin brain, threads, CRM, context, plans, outputs, knowledge and auto-memory - answering only from retrieved sources with file citations, or saying \"not in memory\". Use for \"what do we know about X\", \"where did we decide Y\", \"have we touched Z before\". Do NOT use for counting or aggregating across many files, where the answer sits in no single chunk - that is /census. Do NOT use for Odin-brain-only advice - that is /odin."
argument-hint: "<what to recall> [--collection content|code|all] [--layer NAME] [--personal]"
allowed-tools: "Read, Bash(python3:*), Bash(python:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: partial
  shared_state: ["auto-memory/"]
  triggers:
    - "recall"
    - "what do we know about"
    - "where did we decide"
    - "search my memory for"
    - "have we touched"
    - "find what we said about"
x-heading-capability:
  what: >
    Workspace-wide semantic recall - finds past decisions, briefs, contacts, and
    notes by MEANING (cross-lingual RU/EN) across the whole workspace, and answers
    only from retrieved sources with path citations, or says "not in memory".
  how: >
    Run /recall <query>. It refreshes the index (incremental), queries
    scripts/memory-index.py with --json, reads the top cited files, and composes a
    cited answer. --collection code searches skills/rules; default is content.
  when: >
    Use to surface something the workspace already knows. For Odin-brain advice use
    /odin recall; for external intel use /osint; to capture a new note use /zk; for
    exact-string search use Grep.
x-heading-routing:
  category: Strategy
  triggers:
    - recall
    - what do we know about
    - where did we decide
    - search my memory for
    - have we touched [X] before
    - find what we said about
    - surface past notes on [X]
  exclusions:
    - Odin-brain-only advice / episode dedup -> /odin recall (brain-scoped)
    - external/world intel on a company or person -> /osint
    - capture a NEW note -> /zk
    - exact-string file search -> Grep. CEO-only, not synced to execs.
  compound: 'No'
  router: auto
---
# Recall (workspace-wide semantic memory)

Surface what the workspace already knows, by meaning. The engine
(`scripts/memory-index.py`) does hybrid dense+lexical retrieval entirely on the
local machine (ollama `bge-m3`, zero API cost) across every meaningful layer.
This skill turns its ranked hits into a **cited answer** — or relays its
**honest "gap"** when the answer is not in memory. It never guesses past the
sources, and it never sends anything.

CEO-only. Not synced to executives.

## Phase 0 — Refresh, then query

1. **Refresh the index first** (mirrors `/odin recall`): run

   ```bash
   python3 scripts/memory-index.py build
   ```

   This is incremental — it embeds only changed files and updates the gitignored
   `.memory-index/` cache. It is NOT a workspace write. **If ollama is down**, the
   build prints an embedding error. Do not fail. Note "index not refreshed
   (ollama down), recalling from the existing index" in one line, then query
   whatever is already indexed.

2. **Query with JSON output:**

   ```bash
   python3 scripts/memory-index.py query "<the user's question, RU or EN>" --json
   ```

   - Default collection is `content` (what we know / decided). To search the
     machinery (skills, rules), pass `--collection code`. To search everything,
     `--collection all`. To pin one layer, `--layer NAME`
     (e.g. `odin`, `thread`, `crm`, `outputs`, `context`, `skill`).
   - Pass the user's phrasing as the query text; the engine is cross-lingual, so
     a Russian question recalls English notes and vice-versa.

## Phase 1 — Answer from sources, or admit the gap

Parse the JSON. It is one object:

- **Gap** (`{"hits": [], "gap": true, "best": <float>, "threshold": <float>}`):
  there is no match above the salience threshold. **Say so plainly** — e.g.
  "Not in memory: nothing above the recall threshold for that (closest match
  scored {best} vs {threshold})." Do NOT pad and do NOT speculate. Do NOT answer
  from your own prior knowledge. Optionally name the nearest layer or suggest a
  rephrase or `--collection all`. Stop here.

- **Near-miss** (`"gap": false` but `"near_miss": true` and `"confident": false`,
  each hit carrying `"below_threshold": true`): **not the same as Hits below.**
  Nothing cleared the salience threshold; the engine surfaced the nearest
  material anyway because it sat within the near-miss margin, not because it
  answers the question. Say so plainly — "No confident match in memory for
  that; nearest by similarity: <titles>, relevance not established." List the
  `title`/`path` pairs as **possible leads only**. Do NOT read the files, and do
  NOT compose an answer from them. Read them only when the user explicitly asks
  to go deeper ("check that anyway", "open it", "read it"). Stop here.

- **Hits** (`{"hits": [ {path,title,layer,ntype,classification,collection,score,channels}, ... ], "gap": false}`,
  no `near_miss` key — confident matches):
  1. **Read the top cited files** (`Read` each `path`, highest `score` first —
     usually the top 3–5 are enough). Read the actual files; the JSON carries
     only titles and scores, not full content.
  2. **Compose a concise answer grounded ONLY in what those files say.** Every
     claim traces to a source. Cite inline as `` `path` `` after the claim it
     supports. If two sources disagree, surface the disagreement rather than
     silently picking one.
  3. **If the read files do not actually contain the answer** (a near-miss
     retrieval), say that honestly — "the closest sources touch the topic but
     don't answer it directly" — and name what they do cover. Never invent the
     missing fact.
  4. **Reinforce cited auto-memory hits.** For each cited hit whose `layer` is
     `memory` (auto-memory facts only — every other layer: `odin`, `thread`,
     `crm`, `context`, `reference`, `plans`, `outputs`, `knowledge`,
     `datastore-extract`, `chronicle`, `skill`, `rule`, is left untouched), once
     per unique file path, run:

     ```bash
     python3 scripts/memory-touch.py <path>
     ```

     This bumps `access_count`/`last_accessed` in the file's frontmatter only —
     it never touches content. Run this regardless of whether Phase 0's index
     build was a no-op (it edits the source file directly, not the index).

## Phase 1.5 — Chronicle (historical class, always below the brain)

After the primary answer, run a SEPARATE secondary pass over the Conversation
Chronicle — a dated record of past sessions ("on date X we discussed Y"). It is
NOT a belief and NOT current fact; it only tells you a conversation happened.

```bash
python3 scripts/memory-index.py query "<the user's question>" --collection chronicle --json
```

- Append chronicle hits **below** the brain/content answer, never mixed into it.
  The separate collection is what makes this ordering structural: a brain hit
  always precedes a chronicle hit on the same topic. If the primary pass already
  answered, chronicle is supplementary "we talked about this on <date>" context.
- Render each hit **tagged with its date and class**, e.g.
  `[chronicle 2026-05-19] session about the Globex pricing debate — `path``.
  Never restate a chronicle summary as a current decision; if it matters now,
  say "we discussed this on <date>; confirm whether it still holds."
- The chronicle's `personal` entries are air-gapped (`personal` segment) and
  never appear here — that is intended, not a miss.
- Skip this pass entirely when the user pinned `--collection code`/`--layer`, or
  when the chronicle query returns a gap (say nothing rather than pad).

## Phase 1.6 — Personal chronicle (EXPLICIT opt-in ONLY)

Personal-life sessions are air-gapped. The `personal` path segment is a
hard-coded deny, so personal chronicle is in NO persistent index. It is NEVER
part of the default recall, Phase 1.5, `--collection all`, or auto-inject.

Run a personal-chronicle search **only when the user explicitly asks for it** —
`recall --personal ...`, "search my personal history", "when did I discuss the
villa", etc. Never on a normal recall.

```bash
python3 scripts/chronicle.py personal-recall "<the user's question>"
```

This reads `chronicle/personal/*.md` ON THE FLY, scores locally (bge-m3, lexical
fallback), and persists NOTHING. Present the dated hits tagged `[Личное <date>]`,
clearly historical, never as a current fact. If it returns no match, say so
plainly. Do NOT run this pass in the same breath as an outbound draft unless the
user asked for it. The whole point of the wall is to keep personal life out of
send-capable contexts unless summoned.

## Phase 2 — Source list

End with a one-line-per-source list of what you cited, each as a clickable
`path`, with its `layer` and `classification` tag, so the CEO can open the
originals. Example:

```
Sources:
- knowledge/odin-brain/positions/20260318140300-valuation-path-billion.md  (odin, ceo-only)
- threads/business/2026-05-19-globex-systems-engagement.md  (thread, ceo-only)
```

## Voice

- Match `reference/misha-voice.md` and the always-on humanisation rule. Plain,
  committed, specific. No "I cannot find information regarding..." filler —
  either answer with citations or state the gap in one clean line.
- Hyphens, not double dashes. ODUN.ONE, DPI+, Tribe per terminology.

## NEVER

- **Never fabricate beyond the returned sources.** No answer from your own
  training knowledge when the engine reports a gap — relay the gap.
- **Never send anything.** This is a read/recall skill; it drafts no outbound
  message and calls no send transport.
- **Never read the vault or the personal thread branch.** The engine air-gaps
  them structurally (`_secure/` prefix, `personal` segment); do not work around
  it by reading those paths directly.
- **Never present a hit's snippet as the answer without reading the file.** The
  index stores a 500-char embed snippet, not the full note.
- **Never claim freshness you don't have.** If ollama was down and the index
  wasn't refreshed, say so.
- **Never treat a chronicle hit as current fact or a decision.** It is a dated
  historical record of a past conversation; surface it tagged with its date,
  below the brain, and flag that it may be stale.
- **The reinforcement touch step never modifies content.** `scripts/memory-touch.py`
  only bumps `access_count`/`last_accessed` in frontmatter, scoped to `memory`-layer
  hits; it refuses any path outside the auto-memory directory.
