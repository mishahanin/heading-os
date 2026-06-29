# Odin Temporal-Validity Convention (R11)

Consumed by: `/odin` (compile mode), `scripts/odin_brain_lint.py`, `scripts/odin-brain-health.py` (`--compile`).

Last Updated: 2026-06-08

When a teaching overrides a principle or position, mark the old note as superseded
and keep it -- never delete it. The brain stays able to answer "what did we believe
about X before date Y", and the evolution of Odin's thinking is auditable. This
generalises the prose-only "Prior Lean (superseded)" sections already used in the
conflict notes into a machine-checkable frontmatter convention.

## Frontmatter fields (all optional)

| Field | Meaning |
|---|---|
| `superseded_by` | Slug (file stem) of the note that supersedes this one. |
| `superseded_date` | ISO date (`YYYY-MM-DD`) the supersession occurred. Set only with `superseded_by`. |
| `valid_until` | ISO date a time-bound stance expires -- an alternative to `superseded_by` for scheduled phase transitions. |

```yaml
---
id: "20260401100000"
title: "Old framing (superseded)"
type: principle
sources: ["source-id"]
confidence: high
keywords: [domain]
created: 2026-04-01
updated: 2026-04-01
superseded_by: "new-framing-principle"
superseded_date: "2026-04-15"
---
```

## When to apply

1. **Direct override.** Misha teaches a principle that contradicts an existing one -> mint the new principle, add `superseded_by` to the old one. Cite the old note's `id` in the new note's `sources` so the lineage is explicit (and the orphan check stays quiet).
2. **Position refinement.** A position is replaced by a substantively different stance -> `superseded_by` on the old position. Mere enrichment uses `updated:` alone, no supersession.
3. **Conflict resolution.** A conflict moving open -> resolved records the teaching in its `resolution` field; the conflict file stays, no `superseded_by` needed.
4. **Episode graduation.** An episode maturing into a principle via `/odin reflect` is a NEW principle, not a supersession (the episode's `status: graduated` marks the maturation).

Never delete a superseded note. Archival is a separate, deliberate decision.

## Lint checks (Check 5.5 of compile)

`scripts/odin_brain_lint.py` (run standalone `--json`, or read the `temporal_validity`
block of `odin-brain-health.py --compile`):

- **dangling_reference** (error): `superseded_by` points to a non-existent note.
- **circular_chain** (error): `A -> B -> ... -> A` -- a superseded note cannot eventually supersede itself.
- **orphan_superseded** (warn): a superseded principle that no position references and that its successor does not cite -- a candidate for archival.

Errors block; warnings are advisory. Read-only: the lint never edits the brain.

## Classification

HEADING OS step 7 (2026-06-14, CEO decision): the Odin **code** ships as `engine`
(public + every clone) — the lint script, its test, and the `.claude/skills/odin/`
tree all route `engine` in `config/routing-map.yaml`. Only the Odin **content**
(`knowledge/odin-brain/`) is `private`; everyone (CEO, execs, public installers) fills
their own brain in their own data overlay. `odin-brain-health.py` imports the lint
defensively so its `--compile` never breaks on a workspace whose brain is empty.
