---
name: recall
description: >
  Workspace-wide semantic recall. Searches the local associative-memory index
  (scripts/memory-index.py: bge-m3 hybrid dense+BM25, on-machine, zero API cost)
  across everything meaningful in the workspace -- Odin brain, business threads,
  CRM, LinkedIn, context, reference, plans, deliverable outputs, knowledge,
  datastore extracts, and the auto-memory files -- then answers ONLY from the
  retrieved sources with file-path citations, or says "not in memory" when the
  index reports a gap. Use when the user asks "what do we know about X", "where
  did we decide Y", "have we touched Z before", "recall ...", "search my memory
  for ...", "find what we said about ...", or wants to surface a past decision /
  brief / contact / note by meaning rather than exact words. Do NOT use for:
  Odin-brain-only advice or episode dedup (use /odin recall, which is
  brain-scoped); external/world intelligence on a company or person (use /osint);
  capturing a NEW note (use /zk); plain exact-string file search (use Grep). This
  skill never fabricates beyond returned sources and never sends anything. CEO-only.
argument-hint: "<what to recall> [--collection content|code|all] [--layer NAME]"
allowed-tools: "Read, Bash(python3:*), Bash(python:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "recall"
    - "what do we know about"
    - "where did we decide"
    - "search my memory for"
    - "have we touched"
    - "find what we said about"
x-31c-capability:
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
   build prints an embedding error; do not fail — note "index not refreshed
   (ollama down), recalling from the existing index" in one line and continue to
   the query against whatever is already indexed.

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
  scored {best} vs {threshold})." Do NOT pad, do NOT speculate, do NOT answer
  from your own prior knowledge. Optionally name the nearest layer or suggest a
  rephrase or `--collection all`. Stop here.

- **Hits** (`{"hits": [ {path,title,layer,ntype,classification,collection,score,channels}, ... ], "gap": false}`):
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
