# Evaluator Rubric - Detailed Grading Criteria

Consumed by: evaluate
Last Updated: 2026-03-26

Detailed scoring dimensions per artifact type, skepticism calibration, and common antipatterns.

---

## Skepticism Calibration

The evaluator's default stance is skeptical, not hostile. The goal is to catch real problems, not generate noise.

**Triggers NEEDS REWORK (blocking):**
- Any deterministic check failure (frontmatter, syntax, missing required sections)
- Instructions that are ambiguous enough to produce different outcomes depending on interpretation
- Missing error handling for common failure modes (file not found, network errors, empty input)
- Hardcoded paths that should use workspace utilities
- Skill descriptions that would trigger on the wrong prompts

**Triggers PASS WITH NOTES (advisory):**
- Minor style inconsistencies (not blocking but worth noting)
- Missing optional metadata fields
- Slightly verbose sections that could be tightened
- Valid but non-standard patterns (e.g., os.path used alongside pathlib)

**Does NOT trigger issues:**
- Personal style preferences that don't affect functionality
- Theoretical edge cases that are extremely unlikely in practice
- Convention differences that are internally consistent within the artifact

---

## Skill Scoring Dimensions

### Instruction Clarity (High Weight)
- **Good:** "Read the file at `{path}`. Extract all lines matching `^##\\s`. Count them. If fewer than 3, append check result with status WARN."
- **Bad:** "Analyze the file structure and report on quality."
- **Test:** Could two different Claude sessions produce the same output from these instructions?

### Trigger Accuracy (High Weight)
- **Good:** Description lists explicit trigger phrases AND explicit non-triggers. Mentions adjacent skills that handle similar but different requests.
- **Bad:** Generic description like "helps with code quality" that could match dozens of intents.

### Phase Completeness (Medium Weight)
- **Good:** Each phase has numbered steps with defined inputs and outputs. Phase N's output feeds Phase N+1.
- **Bad:** Phase 2 says "process the results" without specifying what "process" means or what the output looks like.

### Edge Cases (Medium Weight)
- **Good:** Handles missing arguments with a clear error message. Handles empty files. Has fallback for missing dependencies.
- **Bad:** Assumes all inputs are well-formed. Crashes on empty string arguments.

---

## Script Scoring Dimensions

### Error Handling (High Weight)
- **Good:** try/except around file I/O, network calls, subprocess. Specific exception types caught. Meaningful error messages printed to stderr.
- **Bad:** Bare except. Silent pass. Generic "something went wrong" message.

### CLI Interface (Medium Weight)
- **Good:** `argparse` with help text on every argument. Sensible defaults. Example usage in module docstring.
- **Bad:** Positional-only arguments with no help. No docstring.

### Workspace Integration (Medium Weight)
- **Good:** Uses `get_workspace_root()`, `colors`, `load_api_key()`. Paths constructed with `pathlib.Path`.
- **Bad:** Hardcoded `/home/user/workspace`. Uses `os.path.join`. Implements own color codes.

---

## Reference File Scoring Dimensions

### Scannability (High Weight)
- **Good:** H1 title, one-line summary, table of contents for long docs, logical section hierarchy, tables for structured data.
- **Bad:** Wall of prose. No section headers. Critical info buried in paragraph 7.

### Completeness (High Weight)
- **Good:** Every topic promised in the title is covered. No "TODO" or "TBD" sections.
- **Bad:** Title says "Complete Guide to X" but only covers half the features.

### Freshness (Low Weight)
- **Good:** Last Updated date within 90 days. Content references current workspace state.
- **Bad:** References deprecated scripts or removed features.

---

## Rule Scoring Dimensions

### Enforceability (High Weight)
- **Good:** "Always use `html.escape()` before inserting user input into HTML templates."
- **Bad:** "Be careful with user input in templates."

### Brevity (Medium Weight)
- **Good:** Under 40 lines. Every line is load-bearing.
- **Bad:** 100+ lines of context that dilutes the actual rules.

---

## Common Antipatterns to Flag

1. **The Checkbox Skill** - Has all required sections but the content is minimal/templated. Passes deterministic checks but has no substance.
2. **The Kitchen Sink Script** - Handles every conceivable edge case but the happy path is buried. Over-engineered for its purpose.
3. **The Stale Reference** - Last Updated says 2025 but references current features. Someone updated content without updating the date.
4. **The Aspirational Rule** - Reads well but is too vague to enforce. "Write clean code" is not a rule.
5. **The Orphan Artifact** - Created but never referenced from CLAUDE.md, skill tables, or development checklists. Exists but is undiscoverable.
