# LinkedIn-post skill evals

Detects regression when a model update or skill edit silently degrades LinkedIn-post output. Pattern established 2026-05-15 (audit P1.1).

## Run

```bash
python scripts/run-skill-eval.py --skill linkedin-post           # all cases
python scripts/run-skill-eval.py --skill linkedin-post --case case-1-sovereign-dpi  # one case
python scripts/run-skill-eval.py --skill linkedin-post --dry-run # validate cases without API calls
python scripts/run-skill-eval.py --skill linkedin-post --no-write  # skip benchmark.json update
```

## Case format

Each `cases/case-N-{slug}.json` is a self-contained test:

```json
{
  "id": "case-N-short-slug",
  "description": "One-sentence purpose",
  "input": "User prompt that triggers the post draft",
  "checks": {
    "must_mention": ["term-a", "term-b"],
    "must_not_mention": ["banned-opener", "thrilled to"],
    "min_words": 80,
    "max_words": 250,
    "hidden_chars_clean": true
  }
}
```

Available check types (deterministic, no LLM judge):

- `must_mention` — case-insensitive substring search; each missing term fails one check
- `must_not_mention` — fails if any banned term appears
- `min_words` / `max_words` — word-count bounds on the model output
- `hidden_chars_clean` — fails if the output contains any zero-width / soft-hyphen / NBSP characters

## benchmark.json

Auto-written after every non-dry-run. Schema:

```json
{
  "baseline": { "...same shape as last_run, never overwritten after first write..." },
  "last_run": {
    "timestamp": "2026-05-15T...",
    "model": "claude-haiku-4-5-20251001",
    "passed_total": 12,
    "check_total": 12,
    "cases": [{ "id": "...", "passed": 4, "total": 4, "failures": [], "usage": {...}, "elapsed_seconds": 2.3 }]
  }
}
```

`baseline` is captured on the first run and frozen. Compare `last_run` to `baseline` to detect drift after a model update.

## Adding cases

1. Drop a new `case-N-{slug}.json` in `cases/`.
2. Run the eval to confirm the case passes against the current skill.
3. If the case represents a regression mode, document why in the case's `description`.

Target coverage per audit P1.1: **3-7 cases per critical skill**. linkedin-post is the pattern-establishing skill; the same structure rolls out to osint, email-intel, meeting-prep, push-updates, scrutinize, crm, odin, proposal, prime over the rest of the P1 cycle.
