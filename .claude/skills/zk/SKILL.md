---
name: zk
description: >
  Zettelkasten knowledge base - add, enrich, find, connect, distill, garden,
  stats, and brief across the knowledge/ second brain shared with Zettlr.
  Use when the user says "zk", "add a note", "knowledge base", "distill this",
  "garden", "what do we know about", "connect this to", or asks to capture
  an idea, insight, signal, or decision. Also use when processing outputs
  from other skills into durable knowledge.
argument-hint: "[add|enrich|find|connect|distill|garden|stats|brief] [target]"
allowed-tools: "Read, Write, Edit, Glob, Grep, Bash(python3:*), WebSearch, WebFetch"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-heading-orchestration:
  parallel_safe: partial
  shared_state:
    - knowledge/
    - knowledge/INDEX.md
  triggers:
    - zk
    - add a note
    - knowledge base
    - distill
    - garden
    - what do we know about
x-heading-capability:
  what: >
    Zettelkasten second-brain manager over knowledge/ - add atomic notes, enrich, find, connect, distill skill outputs into durable notes, garden for orphans/stale seeds, stats, and topic briefs.
  how: >
    Run /zk [add|enrich|find|connect|distill|garden|stats|brief] [target]. On the CEO workspace /zk is dormant - durable CEO capture instead flows to /odin log, /thread, and the auto-memory system.
  when: >
    Primary capture tool for the executive fleet. On the CEO workspace prefer /odin for learned knowledge, /thread for situations, auto-memory for cross-session facts.
x-heading-routing:
  category: Operations
  triggers:
    - zk
    - add a note
    - knowledge base
    - distill
    - garden
    - what do we know about
  exclusions:
    - Primary capture tool for the executive fleet. On the CEO workspace `/zk` is dormant -- durable CEO capture flows to `/odin log`, `/thread`, and auto-memory.
  compound: 'No'
  router: auto
---
# Zettelkasten Knowledge Base

Manage the `knowledge/` second brain shared between Claude Code and Zettlr. Atomic notes, wiki-links, status lifecycle, and anti-dumping discipline.

## Variables

- `$ARGUMENTS` - Subcommand and parameters. Format: `[action] [target]`
- Actions: `add`, `enrich`, `find`, `connect`, `distill`, `garden`, `stats`, `brief`

## Context Loading

First, read `.workspace-identity.json` to determine workspace type. Paths depend on workspace type:

**CEO workspace** (flat paths):
- `knowledge/odin-brain/INDEX.md` - current brain stats and note inventory
- `context/strategy.md` - for strategic relevance assessment
- `context/pipeline.md` - for deal/prospect context
- `context/current-data.md` - for timeline and milestone context

**Exec workspace** (tiered paths):
- `personal/knowledge/INDEX.md` - personal note inventory
- `corporate/knowledge/shared/INDEX.md` - corporate shared knowledge (if exists)
- `corporate/context/strategy.md` - for strategic relevance assessment
- `corporate/context/pipeline.md` - for deal/prospect context
- `corporate/context/current-data.md` - for timeline and milestone context

## Subcommand dispatch

Parse the first word of `$ARGUMENTS` to determine the action. If no subcommand
is given, default to `stats`.

**Read `references/subcommands.md` before you run any subcommand below.** It
holds the full step sequence, the note file template, and the per-type
enrichment rules. The table names only the command lines and the approval gates.

| Subcommand | Commands and gates | What it does |
|---|---|---|
| `add [type]` | Ends with `python3 scripts/sanitize-text.py {path}`. Type `people` is a redirect: "People intel belongs in CRM. Use `/crm add`." Ask "CEO-only or Corporate-wide?" before writing. | Create one atomic note under the workspace's knowledge root, status `seed`. |
| `enrich [note]` | Ends with `python3 scripts/sanitize-text.py {path}`. Ask which note when several match. | Research a seed or growing note, add connections, raise its status. |
| `find [query]` | Grep only. No writes. | Search personal AND corporate shared knowledge by title, keyword, type, or body. |
| `connect [note]` | Present suggested links as a numbered list, then ask which to add. Never add a link unasked. | Suggest links to other notes and to workspace files, then write the approved ones both ways. |
| `distill [source]` | Present the proposed notes, then WAIT for Misha's approval. Ends with `python3 scripts/sanitize-text.py` on each created file. | Extract 3-7 atomic insights from a skill output file into durable notes. |
| `garden` | `python3 scripts/odin-brain-health.py`, then ask which actions to take, then `python3 scripts/odin-brain-health.py --update-index`. | Maintenance pass over stale seeds, orphans, broken links, and connection opportunities. |
| `stats` | `python3 scripts/odin-brain-health.py --update-index` | Regenerate INDEX.md and display it with any health concerns. |
| `brief [topic]` | Read-only. Present inline; create no file unless asked. | Synthesize every note on a topic into a narrative summary. |

> For full brain linting (contradictions, position candidates, gap analysis),
> use `/odin compile` rather than `garden`.

> **Shared knowledge:** Execs can propose notes for corporate shared knowledge by
> tagging with `#propose-shared` in keywords. These are reviewed during
> `/publish-corporate`.

## Output Conventions

- **File naming**: `{YYYYMMDDHHMMSS}-{slug}.md` - timestamp ID + human-readable slug
- **Subdirectory mapping**: type determines subdirectory (see `add` in `references/subcommands.md`)
- **Wiki-links**: `[[ID|Label]]` format for Zettlr graph compatibility
- **Cross-references**: backtick paths for workspace files (e.g., `crm/contacts/victor-stein.md`)
- **Validation**: Run `python3 scripts/sanitize-text.py <note> --scan` on every generated note and report what it found, per `.claude/rules/hidden-chars.md`. Never pre-write the word "clean".
- **Hyphens only** (-) never em-dashes

## Rules

1. **Atomic discipline**: One idea per note. If a note grows beyond ~4 paragraphs, split it into multiple notes and link them.
2. **Status lifecycle**: seed -> growing -> evergreen -> archived. Never skip stages. Gardening surfaces stuck notes.
3. **Anti-dumping**: The knowledge base is not a filing cabinet. Every note must have a clear reason to exist and be worth revisiting. If it's purely operational, it belongs in `context/` or `memory/`.
4. **Keyword consistency**: Before adding a new keyword, check existing keywords in the knowledge base. Prefer existing terms over synonyms to maintain a coherent tag cloud.
5. **Bidirectional linking**: after a link goes from note A to note B, add the reciprocal link from B to A.
6. **Source attribution**: Always fill the `source` field. Use URLs, meeting names, "observation", or skill names (e.g., "/osint", "/ceo-intel").
7. **Confidence honesty**: Set confidence based on source quality, not conviction. Unverified signals stay `unverified` until corroborated.
8. **Zettlr compatibility**: Use `keywords` (not `tags`) in frontmatter. Use `[[ID|Label]]` wiki-links. These are Zettlr conventions.
