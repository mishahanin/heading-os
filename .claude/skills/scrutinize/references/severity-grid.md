# Severity Grid - /scrutinize Findings

**Consumed by:** `.claude/skills/scrutinize/SKILL.md`
**Last Updated:** 2026-05-27

Five-level severity scale plus a 0-100 confidence score for findings produced by a `/scrutinize` VIIA pass. Severity determines required action and approval urgency; confidence determines what gets surfaced in the approval block.

Trajectory-specific severity examples are listed at the end of the Examples section. Trajectory findings inherit the same severity scale but the example patterns differ from file/dir/workspace findings.

## Severity Levels

| Level | Required action | Threshold |
|---|---|---|
| **BLOCKER** | Must be fixed or user must explicitly accept the risk. Halts forward progress. | Security violation, data loss risk, forbidden-pattern use, rule violation causing harm, fundamental architecture error |
| **HIGH** | Should be fixed before approval. Proposed fix required. | Wrong implementation vs. intent, missing critical error handling, broken dependency, missing success criteria on a plan |
| **MEDIUM** | Notable issue; proposed fix required. | Incomplete edge case coverage, hidden assumption, poor naming that will confuse later readers, unclear interface |
| **LOW** | Minor quality issue. Proposed fix optional (only if cheap). | Readability, minor inconsistency, small inefficiency, redundant import |
| **NIT** | Preference or style note. No fix expected. | Pure style preference, no functional impact |

## Examples

**BLOCKER:**
- Secret hardcoded in source file
- `eval()` or `exec()` with user input
- Plan step contradicts `.claude/rules/secure-projects.md`
- Write to `_secure/` from outside the vault
- `subprocess` call with `shell=True` and user-controlled input

**HIGH:**
- Function claims to "handle timeout" but raises unhandled `ConnectionError`
- Plan promises rollback path but no task implements it
- New script does not use `from scripts.utils.workspace import get_workspace_root`
- SKILL.md frontmatter is missing required fields (`name`, `description`, `parallel_safe`, `shared_state`, `triggers`)
- Post-apply check would fail (hidden chars, py_compile)

**MEDIUM:**
- Magic number with no constant, no comment explaining why
- Missing error path for a plausible production edge case
- Inconsistent logging: some paths use `print()`, others use `colors.RED`
- Naming drift between files (e.g., `clear_layers` vs `clearFullLayers` for the same concept)
- Hidden assumption: code assumes file exists without checking

**LOW:**
- Single-quote vs double-quote inconsistency
- Unused import
- Minor argument ordering nit
- Redundant check after an `isinstance` already narrowed the type

**NIT:**
- Comment wording could be clearer
- Alphabetical ordering in a list
- Blank-line consistency

### Trajectory-specific examples (v2.1)

For `trajectory:<run_id>` target type, findings inherit the same severity scale but the patterns differ. The trajectory lens (`references/trajectory-evaluation.md`) prioritises deterministic tool-call records over rationale prose - when they disagree, the tool call wins.

**BLOCKER trajectory:**
- Agent skipped a plan step that the final report claims was completed (missing `step_end`, present in `run_end` summary)
- Trajectory writes to `_secure/` from a non-vault session

**HIGH trajectory:**
- Agent executed steps in wrong declared order without recording a `deviation` event
- `step_end` reports `status=ok` but the affected file fails post-apply hidden-character scan in the same step

**MEDIUM trajectory:**
- Agent applied a fix without the plan's required post-apply check (`validation_check` event absent for an edited file)
- Wave-mode emitted `wave_start` but no matching `wave_end` (incomplete trajectory)

**LOW trajectory:**
- Rationale prose contradicts the deterministic tool-call record (tool call wins per the lens priority order; prose flagged for cleanup)
- Timestamp ordering across events is non-monotonic (clock skew or out-of-order writes)

**NIT trajectory:**
- Timestamp formatting inconsistency in JSONL events (mixing UTC and local time)
- Step-number labelling drift (plan step 4 referenced as step "4a" in one event, "4" in another)

## Grade Mapping

| Overall Grade | Conditions |
|---|---|
| **PASS** | Zero `BLOCKER`, zero `HIGH` findings |
| **PASS-WITH-NOTES** | Zero `BLOCKER`, zero `HIGH`; some `MEDIUM` / `LOW` / `NIT` |
| **NEEDS-REWORK** | Any `HIGH` findings |
| **BLOCKED** | Any `BLOCKER` findings |

The skill outputs the grade in the approval block and in the saved report.

## Finding ID Convention

Each finding gets a short ID for approval commands:

- `B1`, `B2`, ... for `BLOCKER`
- `H1`, `H2`, ... for `HIGH`
- `M1`, `M2`, ... for `MEDIUM`
- `L1`, `L2`, ... for `LOW`
- `N1`, `N2`, ... for `NIT`

IDs are numbered per severity in the order findings appear. `approve B1, H1, H3` is a valid approval command.

## Confidence Score (0-100)

Every finding includes a confidence score alongside its severity. The score is the reviewer's estimate of the probability that the finding is correct (not a false positive). Modelled on the Anthropic Code Review plugin (March 2026) scale.

| Score | Meaning |
|---|---|
| **0** | Not confident, likely false positive |
| **25** | Somewhat confident, might be real |
| **50** | Moderately confident, real but caveats apply |
| **75** | Highly confident, real and important |
| **100** | Certain, definitely real |

**Default threshold: 75.** Findings below threshold log to the saved report but do NOT appear in the approval block unless `--include-low-confidence` is passed.

**Approval block format with confidence:**

```text
[B1] (conf: 92) <statement>
[H1] (conf: 78) <statement>
[M1] (conf: 65) <statement>    <- below threshold, hidden by default
```

**How the score is produced:**

1. Phase 2 identification emits an initial confidence at finding time, derived from the strength of the evidence link (direct quote of a forbidden pattern -> 95+; inferred-style finding from heuristic -> 50-65; aesthetic preference -> 25-40).
2. Phase 2.5 refutation (see `references/refutation-protocol.md`) adjusts the score: a survived refutation adds +5 to +15 depending on the refutation strength; a refuted finding gets dropped, not lowered.
3. Phase 2.5b debate (BLOCKER + HIGH only, see `references/refutation-protocol.md`) replaces the score with the Meta-Judge's verdict score: certain -> 100, lean-correct -> 80, ambiguous -> 60 (the finding is then dropped from the approval block per the threshold rule).

**What confidence is NOT:**

- Not severity. A BLOCKER can be conf=55 (severe-if-true but uncertain); a NIT can be conf=99 (definitely a style nit, just unimportant). The two axes are independent.
- Not priority. Confidence-weighted severity is computed downstream; the CEO sees both axes raw in the approval block.

**Calibration:** the confidence score is calibrated against CEO `flag-as-fp` decisions over time (see `references/refutation-protocol.md` Calibration section and the FP aggregator). A scorer is well-calibrated if findings emitted at conf=80 are actually wrong about 20% of the time, findings at conf=95 wrong about 5% of the time, etc.
