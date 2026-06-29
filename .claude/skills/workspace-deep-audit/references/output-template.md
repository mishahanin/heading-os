# Audit Output Template

Consumed by: `.claude/skills/workspace-deep-audit/SKILL.md` Phase 7.
Last Updated: 2026-05-20

Produces the markdown artifact at `outputs/operations/workspace/{YYYY-MM-DD}_audit_workspace-deep-overview{-version}.md`.

Substitute `{placeholders}` with computed values from Phases 1-6. Sections marked `[QUICK MODE: SKIP]` are omitted when `--mode=quick`. Sections marked `[FOCUS MODE: ONLY IF MATCHING]` are included only when `--focus` matches.

---

## Skeleton

```markdown
# ceo-main Workspace — Глубокий архитектурный и стратегический аудит {version_suffix}

**Дата:** {YYYY-MM-DD}
**Версия:** {v1 / v2 / vN — auto-incremented if same-date audit exists}
{if --vs: **Companion:** [previous audit]({prev_audit_path}) · explicit delta tables in each section}
**Автор:** Claude Opus 4.7 (1M context)
**Объём:** {Полная инвентаризация / quick / focus on X}, Context7-валидация всех зависимостей, gap-анализ против {N}-best-practice пунктов 2026 года, конкурентный анализ {M} платформ, приоритизированные рекомендации.
**Классификация:** CEO-only.

---

## 0. Executive Summary

{1-2 paragraphs setting the headline}

**Три ключевых вывода:**

1. {finding 1 - architectural state}
2. {finding 2 - cost/observability state}
3. {finding 3 - strategic positioning state}

**Топ-{N} приоритетных действий:**

| # | Action | Effort | Impact |
|---|---|---|---|
| 1 | {P0 item 1} | {hrs/days} | {outcome} |
| 2 | {P0 item 2} | ... | ... |
{... up to 5 items}

Полная детализация в Section 7.

---

## 1. Methodology

{1 paragraph summarizing the 6 audit streams (or fewer for quick/focus modes)}

1. **Инвентаризация ({N} streams):** {list}
2. **Архитектурный разбор:** {files inspected}
3. **Context7 валидация:** {N} packages
4. **Web research:** {N} sources surveyed
5. **Конкурентный анализ:** {M} platforms (skip if quick)
6. **Синтез:** gap analysis, recommendations prioritized by effort × impact

{If --vs: All numbers explicitly compared to v(prev). Delta columns added throughout.}

Никакие файлы не модифицированы. `_secure/` не читался. Corporate-классифицированные файлы не изменялись.

---

## 2. Inventory Matrix

### 2.1 Skills ({count} active + {archived} archived)

{insert table from Stream 1}

### 2.2 Rules ({count} files)

{insert table from Stream 2}

### 2.3 Hooks ({hook_count} scripts + {check_count} PreToolUse checks)

{insert table from Stream 3}

### 2.4 Scripts ({main} main + {utils} utility)

{insert table from Stream 4}

### 2.5 Plugins ({count})

{insert plugin table}

### 2.6 MCP Servers ({count})

{insert MCP table}

### 2.7 Dependencies

{insert dep summary, full Context7 table in Section 4}

### 2.8 Knowledge / Memory / CRM / DataStore

{insert table from Stream 6}

---

## 3. Architectural Findings

{written narrative based on Phase 2 reads}

### 3.1 Layer Map

{ASCII diagram of rules → skills → scripts → filesystem → hooks}

### 3.2 Coupling / Cohesion

{verdict}

### 3.3 Multi-User Architecture (if applicable)

{verdict on hub-and-spoke at current exec count}

### 3.4 Hook Topology

{verdict on _dispatch.py state, fail-open posture}

### 3.5 Skill Orchestration Maturity

{verdict based on pattern table + parallel_safe metadata coverage}

### 3.6 Data Flow Verdict

{verdict on CRM aggregation, classification flow}

### 3.7 Defense-in-Depth (NEW in v2+)

{7-layer audit summary from Stream 7}

### 3.8 Three Biggest Architectural Risks

| # | Risk | Severity | Mitigation status |
|---|---|---|---|
| 1 | {risk 1} | {HIGH/MED/LOW} | {state} |
| 2 | {risk 2} | ... | ... |
| 3 | {risk 3} | ... | ... |

---

## 4. Context7 Validation Results

### 4.1 CRITICAL

{table from Stream 5}

### 4.2 HIGH

{table}

### 4.3 MEDIUM

{table}

### 4.4 NEW / Strategic Flags

{any new dependencies added since last audit; strategic flags like EWS deprecation}

### 4.5 Version Mismatches (Internal)

{fireside vs main alignment}

---

## 5. 2026 Best Practices Gap Analysis [QUICK MODE: SKIP]

{6 category tables from Phase 4 per references/best-practices-rubric.md}

### 5.1 Anthropic Official Guidance (7 points)

{table with v(prev)→v(current) delta if --vs}

### 5.2 Skill & Agent Design (7 points)

{table}

### 5.3 Cost Optimization (5 points)

{table}

### 5.4 Observability & Eval (5 points)

{table}

### 5.5 Security & Compliance (6 points)

{table}

### 5.6 Multi-User / Enterprise (4 points)

{table}

### 5.7 Gap Summary

| Category | LEAD | MATCH | GAP | N/A | Total |
|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... |
| **TOTAL** | **{X}** | **{Y}** | **{Z}** | **{N}** | **34** |

**Percentages (excluding N/A):** {LEAD%} LEAD / {MATCH%} MATCH / {GAP%} GAP

{If --vs: explicit delta line — was {prev}% LEAD / {prev}% GAP, now {curr}% / {curr}%}

---

## 6. Competitive Mapping & Positioning [QUICK MODE: SKIP]

### 6.1 What no one else does

{list of unique workspace strengths from Phase 5}

### 6.2 What everyone has that workspace lacks

{list of category gaps; flag CEO-excluded items separately}

### 6.3 Framing Analysis

| Frame | Market echo | Fit |
|---|---|---|
| AI chief of staff | ... | ... |
| Executive copilot | ... | ... |
| CEO operating system | ... | ... |

### 6.4 Strategic Positioning Status

{report CEO-decided exclusions explicitly; do NOT re-propose excluded options}

### 6.5 Anthropic Validation Signal

{cite cookbook / blog if pattern is validated upstream}

---

## 7. Prioritised Recommendations

### P0 — Critical Immediate ({N} items)

{for each P0 item:}

#### P0.X — {title}

**Effort:** {hrs/days}.
**Impact:** {specific outcome}.
**Acceptance criteria:**

- [ ] {criterion 1}
- [ ] {criterion 2}
- [ ] {criterion 3+}

### P1 — Quick Wins ({N} items)

{same structure}

### P2 — Structural Improvements ({N} items)

{same structure}

### P3 — Strategic Shifts ({N} items)

{same structure}

### Summary by Priority

| Priority | Items | Total effort | Top impact |
|---|---|---|---|
| **P0** | {n} | {sum} | {item 1} |
| **P1** | {n} | {sum} | {item 1} |
| **P2** | {n} | {sum} | {item 1} |
| **P3** | {n} | {sum} | {item 1} |

---

## 8. Conclusion

{narrative wrap-up}

**Next steps:** {3-5 sentences on what the CEO should do with this report}

---

## Appendix A: Sources

### Anthropic Primary
{list}

### Observability
{list}

### Security
{list}

### Workspace Internal
{list previous audit, threads, memory files referenced}

---

## Appendix B: Quantified Workspace Footprint ({YYYY-MM-DD})

{full count table from Section 2 condensed into one Appendix table}

---

**Word count:** {N}
**Hidden characters:** clean (sanitizer-verified)
**Classification:** CEO-only
**Companion file (HTML):** `{file}.html`
**Status:** Final {version} — {one-line summary}
```

---

## Version Suffix Convention

| Scenario | Suffix |
|---|---|
| First audit on a date | none (e.g., `2026-08-15_audit_workspace-deep-overview.md`) |
| Same-date follow-up | `-v2`, `-v3` etc. |
| Post-sprint delta audit | `-post-rollout` |
| Versus-baseline delta audit | `-vs-{baseline-date}` |
| Focus mode | `-focus-{subsystem}` |
| Quick mode | `-quick` |

---

## Voice Discipline

Match the v1+v2 audit precedent:

- Russian/English mix acceptable when v1 register works
- Engineering audit tone, not marketing
- Every claim backed by count / hash / pin / run
- Explicit before/after deltas when `--vs` is active
- Honesty floor: report zero P0s if zero exist; never inflate
