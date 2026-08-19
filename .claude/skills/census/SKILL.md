---
name: census
description: "Answer an aggregating question by traversing every file in a named corpus inside a sandbox, so the corpus never enters the context window. Use when the answer exists in no single file: \"how many threads have not moved in 30 days\", \"which pipeline rows have no CRM card\". Do NOT use to find something already recorded somewhere - that is /recall, which retrieves rather than counts."
argument-hint: "<aggregating question> [--corpus threads|crm|context|auto_memory|knowledge|outputs]"
allowed-tools: "Read, Write, Bash(python3:*), Bash(python:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "how many"
    - "count across the workspace"
    - "which threads have"
    - "which contacts have"
    - "aggregate over"
x-heading-routing:
  category: Operations
  triggers:
    - "how many X have Y"
    - "count across the workspace"
    - "which pipeline rows have no card"
    - "which threads have not moved in N days"
    - "aggregate over the whole corpus"
    - "intersection of people and pipeline"
  exclusions:
    - "\"what do we know about X\" / \"where did we decide Y\" -> /recall"
    - "\"add a note\" / \"distill this\" -> /zk"
    - "\"what would Odin say\" -> /odin"
    - "background on a company or person -> /osint"
    - "an exact string in a known file -> Grep"
  compound: "No"
  router: auto
x-heading-capability:
  what: >
    Answers a counting or set question over the workspace by writing and running a
    traversal program over the files, returning a count or a path list with the
    sources that produced it.
  how: >
    Run /census "<question>" [--corpus SCOPE]. The traversal program is printed
    before it runs; read it, then approve. Execution is always sandboxed with no
    network and a read-only corpus.
  when: >
    Use when the answer needs many files visited and sits in none of them. For
    "what do we know about X" use /recall; for a new note /zk; for external intel
    /osint; for an exact string Grep.
---

# /census — aggregation over the corpus

Answer an aggregating question by traversing the corpus, not by retrieving from it.

## Why this exists

The step-1 benchmark (2026-08-13) measured the incumbent retrieval path against
15 code-computed truths. On the aggregating class the retrieval ceiling was **0.000**.
All seven questions scored exactly 0.00. That is not a low score but an
unreachable one. The answer sits in no single chunk, so top-K cannot return it at
any depth.

`/census` measured **6 of 7** on the same questions and the same corpus state.
Median 0.05 s per answer, against 0.92 s for retrieval.

The one miss is a known wording debt, not a capability gap. agg-09 names a
reference list of countries that the traversal never receives. It therefore
measures whether the list was guessed. It stays wrong, because the alternative
was a program rewritten after the answer was known.

Both numbers live in `outputs/operations/census-bench/` in the data overlay.

## When NOT to apply

| Signal | Route to |
| --- | --- |
| "what do we know about X", "where did we decide Y" | `/recall` |
| The corpus scope fits the context window | `/recall` — the engine REFUSES with exit `4` |
| An exact string in a known file | `Grep` |
| A new note to capture | `/zk` |
| Advice from the Odin brain | `/odin` |
| Anything about a person or company outside the workspace | `/osint` |

The window refusal is not advice. SRLM (arXiv:2603.15653) reports that a traversal
primitive on a corpus that already fits actively hurts, so the engine refuses
rather than degrade quietly.

## Corpus scopes

| Scope | What it mounts |
| --- | --- |
| `threads` | the business thread registry |
| `crm` | the CRM contact cards |
| `context` | the context documents, including the pipeline |
| `auto_memory` | the auto-memory files and their index |
| `knowledge` | the knowledge base |
| `outputs` | produced deliverables |

Default scope set: `threads`, `crm`, `context`, `auto_memory`. Name scopes with
repeated `--corpus` flags; a scope you do not name is not mounted, so the
traversal cannot read it at all.

Air-gapped paths are never mounted, whatever is asked for: the CEO-private thread
branch (the `personal` path segment) and any `_secure/` prefix. The engine checks the refusal before it tests
the path for existence. A denied directory underneath a mounted one receives a
tmpfs overlay. It is therefore ABSENT inside the box, not merely forbidden.

## Protocol

1. **State the question as a count or a set.** "How many", "which files", "which
   pairs". A question that wants prose is a `/recall` question.
2. **Write the traversal program** against the mount paths, which mirror the
   data-root-relative layout: `/data/threads/business/*.md`,
   `/data/crm/contacts/*.md`. Write results to `/out/answer.json`.
3. **Read the printed program before you run it.** The engine prints the program
   by default, then runs it. It does NOT wait for you, and `--no-print-program`
   removes the print. So reading is your discipline, not a gate the engine
   enforces. What makes the eval/exec carve-out legitimate is the four sandbox
   controls in `.claude/rules/generated-code-execution.md`, not this step.
4. **Run it.** `python scripts/census.py "<question>" --program <file> --corpus <scope>`.
5. **Report the count AND the sources.** An answer whose paths cannot be opened
   and checked by hand is not an answer.

## The return shape

Counts, paths and pairs only:

```json
{"kind": "count", "value": 13,  "sources": ["<scope>/a.md"]}
{"kind": "paths", "paths": [...], "sources": [...]}
{"kind": "pairs", "pairs": [["a.md", "b.md"]], "sources": [...]}
```

Free prose needs `--free-text` and comes back tagged
`"provenance": "untrusted"`. That tag is the fourth control. The other three
protect the child; this one protects the parent. The RESULT travels to a session
that HAS the network and the credentials. An injected instruction never needs to
run inside the box. It only needs a quote in the return.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | answered |
| 2 | bad arguments, or an unknown corpus scope |
| 3 | the traversal failed, or its return did not satisfy the schema |
| 4 | the corpus fits the context window — use `/recall` |
| 5 | the sandbox refused: no bubblewrap, an air-gapped path, or a timeout |
| 6 | the return exceeded the return budget |

The air-gap refusal is checked BEFORE the window refusal. A small air-gapped
scope must not be answered with "it fits, use `/recall`", which is advice to
read the same branch by another route.

## NEVER

- Never run a traversal outside the sandbox, and never fall back to running one
  when bubblewrap is missing. No sandbox, no run.
- Never mount a corpus scope read-write, and never mount an air-gapped path.
- Never return free prose without `--free-text`, and never let a traversal vouch
  for text it read out of the corpus.
- Never give the sandbox a network channel of any kind, including a broker that
  would relay a model call on its behalf.
- Never approve a traversal program without reading it.
- Never quote a traversal's answer without the sources that produced it.
- Never send anything outbound. This primitive reads and counts.
