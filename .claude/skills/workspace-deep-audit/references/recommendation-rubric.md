# Recommendation Priority Rubric

Consumed by: `.claude/skills/workspace-deep-audit/SKILL.md` Phase 6.
Last Updated: 2026-05-20

Every gap surfaced in Phases 1-5 must be categorized as P0 / P1 / P2 / P3 using the rules below. Each recommendation needs 4 required fields: priority, effort, impact, acceptance criteria.

---

## Priority Definitions

### P0 — Critical Immediate (this sprint, 1-3 days)

A recommendation is P0 if it meets ALL of:

- **Active risk:** the gap is causing harm right now (production breakage, security exposure, compliance hole) OR will cause harm within 7 days if not fixed
- **High-ROI ratio:** effort ≤3 days AND impact is one of: cost reduction ≥30%, security control restoration, blocker for downstream work
- **No dependencies:** can be shipped in current sprint without waiting on external decisions

Examples:
- Critical security CVE in a pinned package
- API key leaked in tracked file
- Production-blocking dependency missing
- Single highest-ROI capability bump (e.g., prompt caching when SDK supports it)

### P1 — Quick Wins (1-2 weeks)

A recommendation is P1 if it meets ALL of:

- **Measurable improvement:** delivers an observable workspace property change (test pass rate, eval coverage, compliance %, etc.)
- **Effort ≤2 weeks** including testing
- **Bounded scope:** doesn't require architectural decisions or CEO judgment calls

Examples:
- Add evals/ to N critical skills
- Pre-commit hook for sync enforcement
- Bump package within same major (no breaking changes)
- Add Last Verified dates to stale rules

### P2 — Structural Improvements (quarter)

A recommendation is P2 if it meets ANY of:

- **Architectural change** requiring design decisions
- **Effort >2 weeks** for full implementation
- **Multiple subsystems affected** (rule + skill + script + hook)
- **Workflow change** for the CEO (new gate, new approval step)

Examples:
- Add observability stack (Langfuse / LangSmith)
- Implement staging branch + canary exec
- Refactor 10+ skills against new pattern
- Add per-task budget caps with hook integration

### P3 — Strategic Shifts (6+ months)

A recommendation is P3 if it meets ANY of:

- **Pivots positioning** (e.g., expose workspace as MCP server, add web UI)
- **Migrates a load-bearing dependency** (e.g., EWS → Graph)
- **Adds commercial / external surface**
- **Long-term technology bet** (e.g., persistent memory layer)

Examples:
- Microsoft Graph migration off EWS (when triggered)
- Add web UI / mobile companion
- Expose workspace skills as MCP server
- Commercial offering / pricing model

---

## Effort Estimation Bands

| Band | Hours | Examples |
|---|---|---|
| Tiny | <1 hour | Bump one package version |
| Small | 1-4 hours | Add one hook, write one rule |
| Medium | 1-2 days | Add evals to one skill, refactor one script |
| Large | 3-7 days | New skill from scratch, add observability layer |
| XL | 1-2 weeks | Refactor 10+ skills, add staging branch system |
| XXL | 1-2 months | Migrate dependency stack, add web UI |

---

## Impact Categories

Every recommendation must state impact in concrete terms:

| Impact type | How to phrase |
|---|---|
| Cost reduction | "X% input-token reduction on cache hit" / "$Y/month savings" |
| Security control | "Closes lethal trifecta exfiltration vector" / "Blocks Bash-write to corporate/" |
| Compliance | "Restores 100% Last Verified coverage on always-active rules" |
| Capability unlock | "Enables [specific feature] not previously available" |
| Risk mitigation | "Reduces architectural risk #N from HIGH to MEDIUM" |
| Regression detection | "Catches model-update regression on N hot-path skills" |
| Drift prevention | "Eliminates manual doc propagation step" |

**Vague impact statements are unacceptable.** Reject "improves quality" / "better security" / "more robust" — these aren't measurable. Force concrete.

---

## Acceptance Criteria Format

Every recommendation includes 3-7 checkbox-style criteria. Each criterion must be:

- **Verifiable** — can be observed as true/false after implementation
- **Specific** — names file paths, command outputs, or measurable thresholds
- **Independent** — doesn't depend on other criteria in the same item

Example bad criteria:
- [ ] Improve security posture
- [ ] Make the code more maintainable

Example good criteria:
- [ ] `_dispatch.py` has new function `check_rate_limit` registered in CHECKS block
- [ ] Hook blocks Write call when daily count >500
- [ ] State persists across sessions in `.claude/state/` (gitignored)
- [ ] Pre-commit hook `runtime-state-guard` updated to ignore new state file

---

## Output Format

Each recommendation in the audit output:

```markdown
#### {PriorityCode}.{Index} — {Title}

**Effort:** {band} ({hours/days}).
**Impact:** {concrete impact statement}.

**Acceptance criteria:**

- [ ] {criterion 1}
- [ ] {criterion 2}
- [ ] {criterion 3}
- [ ] {criterion 4-7 as needed}

**Dependencies (if any):** {list other recommendations that must ship first}
**Excludable by CEO:** {yes/no — flag P3 items that the CEO may decide to skip entirely}
```

---

## Sort Order

Within the output's Section 7:

1. **By priority** — P0 first, then P1, P2, P3
2. **By impact within priority** — highest impact first
3. **By effort within impact band** — lowest effort first (high-ROI ordering)

Cap output at **25 total recommendations across all priorities.** Beyond 25, the audit produces a "Carry-forward backlog" subsection listing additional items without full breakdown.

---

## Anti-Patterns to Reject

The audit should NOT produce these as recommendations:

1. **Already-shipped work** — don't recommend something currently in production
2. **CEO-excluded items** — don't re-propose P3.2-P3.5 if CEO has explicitly excluded them
3. **Vague aspirations** — "improve testing" with no specific test cases or threshold
4. **Tooling preferences** — "switch from X to Y" without concrete capability gain
5. **Cosmetic refactors** — renaming files / reorganizing folders for aesthetics
6. **Future-feature bets** — "add support for Claude 5 when released" — wait until needed

If a candidate recommendation fits any anti-pattern, drop it rather than recommend it.

---

## Honesty Floor

If the audit finds zero P0 items, the report says so explicitly. Do NOT manufacture P0 items to fill a perceived "P0 must have entries" expectation. The honest verdict that the workspace has no critical immediate issues is more valuable than a fabricated P0 list.

Similarly: if the audit's recommendations all converge into one priority band (e.g., all P2 structural items), report that as the actual finding, not as a defect requiring spreading across all 4 priorities.
