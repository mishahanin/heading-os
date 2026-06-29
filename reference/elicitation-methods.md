# Elicitation & Critique Method Catalog

A data-driven catalog of reasoning, critique, and elicitation methods that reasoning skills can pull from on demand, so depth comes from a named instrument rather than whichever angle the model happens to pick.

Last Updated: 2026-06-04
Consumed by: `/deep-think`, `/devil`, `/scrutinize`, `/council`

## What this is

`reference/elicitation-methods.csv` holds 53 methods across 11 categories (advanced, collaboration, competitive, core, creative, framing, learning, philosophical, research, retrospective, risk). Each row is `num, category, method_name, description, output_pattern`:

- **description** — a self-contained gist: enough to propose the method and run it inline.
- **output_pattern** — arrow notation for the method's flow, e.g. `assumptions -> truths -> new approach`.

The catalog is adapted from BMAD's `bmad-advanced-elicitation/methods.csv` (69 rows), trimmed to the CEO-relevant subset: code-generation and infrastructure-specific entries (the whole `technical` category, Chaos Monkey, Code Review Gauntlet, prompt-engineering primers) were dropped; the strategy, intel, negotiation, and risk methods were kept.

## How skills consume it

The catalog is reached through `scripts/elicit.py`, never by reading the CSV into context wholesale. The accessor is deliberately lean so a skill can offer a few matched methods without flooding the window:

```
python scripts/elicit.py categories                       # cheap map: category names + counts
python scripts/elicit.py list --category risk              # the index for one or more categories
python scripts/elicit.py show "Pre-mortem Analysis"        # full gist + output pattern for named methods
python scripts/elicit.py random --category creative -n 3   # draw blind
```

`list` refuses to run without `--category` or `--all` — dumping all 53 rows is a deliberate act, not an accident. Add `--json` to any command for structured output.

The intended pattern inside a reasoning skill: call `categories`, pick 2–5 methods matched to the problem, `show` them, then apply them in sequence. The hook is always optional — skip it when the default reasoning flow already suffices.

## Maintenance

To add a method, append a row to the CSV (next `num`, an existing or new `category`, a self-contained `description`, an arrow-notation `output_pattern`). `categories` and `list` pick it up with no code change. Keep descriptions self-contained — there is no detail-file layer.
