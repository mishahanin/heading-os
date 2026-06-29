# Skill Graph — relationship catalog for `/next`

The machine-readable map of how skills sequence into one another. Makes explicit the "X feeds Y" relationships that today live only as prose in `.claude/rules/skill-router.md` (the Compound column and Exclusions cross-references). The `/next` recommender reasons over this catalog plus a "what just happened" signal to suggest the logical next step and its exact command.

Last Updated: 2026-06-04
Consumed by: `/next` (via `scripts/skill_graph.py`).
Classification: corporate.

## File

`reference/skill-graph.csv` — one row per routable skill.

## Columns

| Column | Meaning |
|---|---|
| `skill` | The skill id (the slash-command name without the leading `/`). |
| `phase` | Coarse lifecycle bucket, reusing the router's own section taxonomy: `intel`, `comms`, `content`, `crm`, `design`, `strategy`, `operations`. Most ops skills are effectively "anytime". |
| `preceded_by` | Skills that typically run *before* this one. `\|`-delimited. Soft suggestion, not a gate. |
| `followed_by` | The load-bearing column — skills that typically run *after* this one. `\|`-delimited. This is what `/next` recommends. Seeded from the router's Compound column + Exclusions cross-references. |
| `produces_in` | The `outputs/` (or other) subdirectory where this skill lands its artifact. The recency-signal join key: `scripts/next-signal.py` maps a recently-touched file back to its producing skill via this column. Empty when the skill produces no durable file (e.g. comms that send, utilities). |
| `consumes_from` | Where the skill typically reads its inputs (e.g. `datastore`, `crm`, a prior skill's `produces_in`). Context only. |

## Editing rules

- One row per routable skill. When a skill is added, re-scoped, or retired in `skill-router.md`, update its row here too (the relationship and the routing metadata are kept deliberately separate — triggers/exclusions stay in `triggers.json` and the router, never here).
- `followed_by` carries the recommendation logic; keep it honest — only real, common sequences, never an exhaustive "could conceivably follow" list. A weak edge produces a weak recommendation.
- Empty `followed_by` means "no strong next step" — `/next` will fall back to the recency signal alone and may recommend nothing rather than guess (honesty floor).
- `\|` is the multi-value separator inside a cell (not a comma — commas are the CSV delimiter).

## How `/next` uses it

1. `scripts/next-signal.py` reports the most recent action(s) — from the handoff `.latest` pointer, newest `outputs/` files, `git log`, and active business threads.
2. Each recent output is mapped to its producing skill via `produces_in` (reverse index in `scripts/skill_graph.py`).
3. `/next` looks up that skill's `followed_by` edges and emits the next step(s), each with the exact slash-command, optional first then required, never executing anything.
