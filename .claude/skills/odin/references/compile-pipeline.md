# Compile Pipeline - Unified Brain Linting

Consumed by: `/odin` compile mode
Last Updated: 2026-04-08

---

## Pipeline

**Phase 1: Structural analysis (script)**

Run: `python scripts/odin-brain-health.py --compile`

This outputs a JSON report. Read and parse it. The JSON contains:
- `orphan_principles` - principles not referenced by any position
- `domain_clusters` - keyword domains with 3+ principles from 2+ authors (position candidates)
- `stale_seeds` - brain files with `status: seed` older than 7 days
- `stale_positions` - positions with revisit_when conditions to evaluate
- `keyword_frequency` - keyword frequency across the entire brain
- `orphan_sources` - sources that never had principles extracted

**Phase 2: Semantic analysis (LLM)**

Using the JSON data:

**Check 1: Internal contradictions**
- Read principles that share 2+ keywords. Compare claims.
- If contradiction found: create a conflict file in `knowledge/odin-brain/conflicts/`
- Report contradictions for CEO resolution

**Check 2: Orphan detection**
- Present `orphan_principles` with suggested actions (connect to position, archive, delete)
- Present `orphan_sources` - sources that may need principle extraction

**Check 3: Position formation candidates**
- For each entry in `domain_clusters`, evaluate whether the principle cluster warrants a formal position
- Present: "Position opportunity: [keyword]. Supported by [N] principles from [M] sources by [K] authors: [list]. Want me to draft it?"

**Check 4: Stale knowledge**
- Present `stale_seeds` with age and suggest: enrich, archive, or delete
- For each `stale_positions` entry, grep `context/` for the `revisit_when` terms. If matches found, flag: "Position [title] may need revision - revisit condition appears active."

**Check 5: Gap analysis**
- From `keyword_frequency`, find keywords appearing in 3+ files but with no dedicated principle title matching that keyword
- Present: "Gap: [keyword] appears in N files but has no focused principle."

**Check 5.5: Temporal validity (superseded_by) -- R11**
- The `--compile` report carries a `temporal_validity` block (computed by `scripts/odin_brain_lint.py`); a standalone run is `python scripts/odin_brain_lint.py --json`.
- Surfaces three issue classes: `dangling_reference` (superseded_by points to a non-existent note -- error), `circular_chain` (A -> B -> ... -> A -- error), `orphan_superseded` (a superseded principle neither referenced by a position nor cited by its successor -- warn, archival candidate).
- Treat errors as blocking, warnings as advisory. Convention: `.claude/skills/odin/references/temporal-validity.md`.

**Check 6: INDEX regeneration**
- Run `python scripts/odin-brain-health.py --update-index`

## Output Format

Present the compile report in Odin's voice:

```
## Odin Compile Report

Scanned: X brain files (S sources, P principles, Pos positions, C conflicts, R reference)

### Contradictions Found: N
[list with file pairs and conflict summary]

### Temporal Validity: N issues
**Errors:** [dangling references, circular chains - must fix]
**Warnings:** [orphan-superseded candidates - advisory]
[per item: file, superseded_by target, severity]

### Orphans: N
[list with suggested actions per file]

### Position Candidates: N
[list with keyword domain, principle count, author count]

### Stale Knowledge: N
[list by category - seeds, positions]

### Gaps: N
[list with keyword and file count]

### INDEX Regenerated
Brain: S sources, P principles, Pos positions, C conflicts, R reference

Actions required: N items above need your decision.
```

Wait for CEO direction on each actionable item. No auto-fixes.
