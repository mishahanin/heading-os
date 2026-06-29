---
name: marp
description: "Markdown-driven presentation pipeline producing PDF and HTML from MARP source with 31C branded theme. Four subcommands: new (prompt-to-deck), render (existing source), from (workspace markdown transform), watch (live preview). Internal decks, runbooks, state checks, knowledge renders. Do NOT use for editable PPTX client decks (use /pptx-generator) or LinkedIn carousels (use /pptx-generator)."
argument-hint: "[new <topic> | render <path> | from <path> | watch <path>]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(marp:*), Glob, Grep"
context: fork
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "marp"
    - "render as slides"
    - "turn this into slides"
    - "slides from this doc"
    - "render this as a deck"
    - "internal deck about"
    - "runbook deck"
    - "quick slides"
    - "md to slides"
x-31c-capability:
  what: >
    Renders 31C-branded presentations (PDF + HTML, not editable PPTX) from
    Markdown source using the dual-mode 31c theme.
  how: >
    Type /marp with one of four subcommands - new <topic> (draft a deck from a
    prompt), render <path> (render existing source), from <workspace-path>
    (transform any workspace markdown into slides), or watch <path> (live
    preview). Output lands beside the source or in
    outputs/deliverables/presentations/marp-source/.
  when: >
    Use for internal decks, runbooks, state checks, and knowledge renders. For an
    editable brand-heavy client deck or a LinkedIn carousel use /pptx-generator.
---
# /marp - Markdown Presentation Pipeline

Render 31C-branded presentations from Markdown source. Outputs PDF and HTML. Not editable PPTX.

## Subcommands

### `/marp new <topic>`

Draft a full deck from a topic prompt.

1. Parse topic into purpose and audience. Ask one clarifying question only if truly ambiguous.
2. Generate outline:
   - Cover slide (title layout, dark)
   - One context or "why" slide
   - Three to five main-point slides (one idea per slide, under 50 words each)
   - One key-takeaway slide
   - Closing slide
3. Default mode: `mixed` (dark cover, dark section-breaks, dark closing; light content slides).
4. Write source to `outputs/deliverables/presentations/marp-source/YYYY-MM-DD-<slug>.md`.
5. Run `python3 scripts/marp_render.py render <source>` to produce PDF and HTML.
6. Report: file paths, slide count, sanitizer status.

**Flags:** `--slides N`, `--mode dark|light|mixed`, `--no-render`, `--title "override"`, `--images png`

If target filename exists, refuse to overwrite. Prompt for rename or `--force`.

### `/marp render <path>`

Pure render of an existing .md source. No authoring.

```bash
python3 scripts/marp_render.py render <path> [--pdf-only] [--html-only] [--images png] [--output <dir>] [--verbose]
```

Renders PDF and HTML to the same directory as source, or to `--output <dir>`.

### `/marp from <workspace-path>`

Transform any workspace markdown into slides without modifying the source.

```bash
python3 scripts/marp_render.py from <path> [--break-at h2|h3|manual] [--mode dark|light|mixed] [--title "..."] [--subtitle "..."] [--no-auto-cover] [--no-auto-closing] [--output <dir>] [--paginate-heavy] [--verbose]
```

Pre-processing (in-memory, source never mutated):
- Strip existing YAML frontmatter from rendered body
- Strip wiki-links: `[[id]]` becomes `id`, `[[id|Display]]` becomes `Display`
- Insert slide breaks at configured heading level (default h2)
- Auto-generate cover slide with title, date, source path
- Auto-generate closing slide
- Inject MARP frontmatter

**Workspace-aware defaults:**

| Source location | Default mode | Cover subtitle |
|---|---|---|
| `context/` | light | Operating Context - 31 Concept |
| `knowledge/` | light | From the brain - <date> |
| `reference/` | light | Reference - 31 Concept |
| `outputs/intel/` | dark | Intelligence - Classified |
| `outputs/operations/` | dark | Operations - 31 Concept |
| `outputs/proposals/` | mixed | <filename> - Proposal |
| anywhere else | mixed | <filename> |

### `/marp watch <path>`

Live preview with hot reload.

- **Start:** `python3 scripts/marp_render.py watch <path>` - spawns detached marp-cli server, opens browser
- **Stop:** `python3 scripts/marp_render.py watch stop` - kills process, removes state
- **Status:** `python3 scripts/marp_render.py watch status` - reports running/stopped, URL, source

One watch session at a time (v1).

## Subcommand Dispatch

When invoked via natural language (not a direct subcommand):

| Signal | Subcommand |
|---|---|
| Message contains a workspace path outside `marp-source/` | `from` |
| Message contains a path inside `marp-source/` or any `.md` with MARP frontmatter | `render` |
| Message has a topic but no file path | `new` |
| "watch", "preview", "live" plus a path | `watch` |
| Ambiguous "this doc" | Resolve from conversation context (last-read file). If none, ask. |

## Theme

Single-file dual-mode 31C theme with 7 layouts. Template: `.claude/skills/marp/themes/31c.css.tmpl`.

**Layouts:** title, content, two-column, quote, stats, section-break, closing

**Per-slide directives:**
```markdown
<!-- _class: title | content | two-column | quote | stats | section-break | closing -->
<!-- _class: light -->
<!-- _class: dark -->
<!-- _class: "content no-corner" -->
<!-- _footer: "" -->
<!-- _paginate: false -->
```

## Frontmatter Convention

```yaml
---
marp: true
theme: 31c
paginate: true
size: 16:9
class: dark
title: "Deck Title"
author: "Misha Hanin"
date: "2026-04-16"
classification: "ceo-only"
footer: "(C) 2025-2026 - 31 Concept - 31C.io - Proprietary & Confidential"
---
```

## Self-Test

```bash
python3 scripts/marp_render.py --self-test
```

Renders the sample deck, validates output sizes, checks hidden characters, reports pass/fail.

## NEVER

- Never modify the source file during render. All transformations happen in-memory on a copy.
- Never render externally sourced .md files without review (--allow-local-files security risk).
- Never skip hidden-character sanitization. Every render reports sanitizer status.
- Never output editable PPTX. That is `/pptx-generator` territory.
