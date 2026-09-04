---
paths:
  - "reference/corporate-style-guide.md"
  - "scripts/render-doctype.py"
  - "datastore/brand/templates/doctypes/**"
  - "outputs/documents/**"
  - ".claude/skills/corporate-letter/**"
  - ".claude/skills/proposal/**"
  - ".claude/skills/partnership-doc/**"
  - ".claude/skills/official-doc/**"
  - ".claude/skills/xpager/**"
---

# Corporate Documents — Authoring, Rendering, Classification

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Path-scoped sibling of the always-on `.claude/rules/corporate-docs.md`. That rule
keeps the part that must be resident: WHICH message routes to which of the five
locked doctypes, and the non-negotiable announcement. Everything here fires only
once a doctype has already been chosen, so it loads on demand.

**The load is mechanical, not remembered.** The resident guardrail orders every
one of the five skills to read `reference/corporate-style-guide.md` before
drafting; that path is the first glob above, so opening the style guide loads this
rule. The rendering, output and template paths cover the rest of the run.

## Brand Enforcement (applies to all five types)

This section listed six files as one obligation until 2026-09-02, and four of the
six could never be violated. They are two different things and are now written as
two.

**Already in context; nothing to load.** `.claude/rules/terminology.md` (Tribe,
ODUN.ONE, DPI+, Five Principles), `.claude/rules/voice.md` (writing rules),
`.claude/rules/voss.md` (negotiation overlay) and `.claude/rules/hidden-chars.md`
(zero invisible characters) carry no `paths:` frontmatter, which in this
workspace means always-on: they load every session whatever any skill does. A
skill cannot fail to load them, so listing them as a per-skill duty was
unfalsifiable, and it made a six-item list read as enforced when two items were.

**Read before drafting.** These are on-demand, so a skill that does not open them
has not read them:

1. `reference/corporate-style-guide.md` - locked colors, typography, letterhead,
   signature, footer, file naming, authoring checklist. Engine content, present
   in every clone, and all five skills name it. Required, no exceptions.
2. `reference/misha-voice.md`, or the sender's own executive voice file. It
   routes `private`, so it ships in the operator's data overlay and an engine
   clone without an overlay does not have it. When it is absent, continue on
   `.claude/rules/voice.md`, which is always-on and carries the writing rules,
   and say in the delivery that the voice file was unavailable. Never hold a
   document for it, and never reconstruct its content from memory.

After drafting, every skill runs:

- `python scripts/sanitize-text.py {path} --scan` on the generated HTML/MD
- Authoring checklist from `reference/corporate-style-guide.md` for that doctype

Before declaring complete, the skill must carry the confirmation line defined in
`.claude/rules/hidden-chars.md`, with both numbers copied from
`scripts/sanitize-text.py --scan` — including when the scan was not clean.

## Rendering Pipeline

All five types render through one script:

```
python scripts/render-doctype.py --type {letter|proposal|partnership|official|xpager} \
  --data path/to/data.json --out outputs/documents/{sender}/ \
  --formats {pdf,docx|pdf,html}
```

It loads the locked template from `datastore/brand/templates/doctypes/{type}.html`,
substitutes the JSON data, inlines logos/fonts/brand CSS, renders PDF via
Playwright and DOCX via python-docx (HTML instead of DOCX for xpager), and returns
the paths. Step-by-step: `python scripts/render-doctype.py --help`.

## Authoring Outputs Location

Rendered documents land in `outputs/documents/{sender-slug}/{doctype}/` as
`YYYY-MM-DD_{doctype}_{recipient-slug}_{short-subject-slug}.{ext}`. Senders
without an explicit slug default to `misha-hanin`.

## Classification

Resolved by `get_routing_destination()` over `config/routing-map.yaml`, the single
classification input (`.claude/rules/classification.md`). Called on 2026-08-31:

- `reference/corporate-style-guide.md` - engine.
- `.claude/rules/corporate-docs.md` - engine.
- `scripts/render-doctype.py` - engine.
- `.claude/skills/corporate-letter/` - engine.
- `.claude/skills/proposal/` - engine.
- `.claude/skills/partnership-doc/` - engine.
- `.claude/skills/official-doc/` - engine.
- `.claude/skills/xpager/` - engine.
- `datastore/brand/templates/doctypes/` - corporate.
- `outputs/documents/` - private.

Five of those lines read `corporate` until 2026-08-31, one of them crediting
`reference/` with a `corporate` directory default that has never existed (the
directory resolves `engine`; the CEO files in it are per-file `private` carve-outs).
The map's header records why: code directories that were `corporate` (shared down to
executives) became `engine` (shared to everyone), because code is not data. Only the
brand templates, which are content, stayed `corporate`.

`engine` is the WIDER destination. The engine repo is public, so the stale text was
wrong in the dangerous direction: it said a `scripts/` file and four skill
directories were held back from the public when they already ship in the public
clone. `tests/test_a_rule_that_classified_its_own_files_by_hand.py` now resolves
every claim above, so this section cannot drift from the map again unnoticed.

## Change Control

Changes to any locked template require CEO approval. After edit, run
`/push-updates` to propagate to all execs.
