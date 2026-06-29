---
name: workspace-deep-audit
description: >
  EXPLICIT INVOCATION ONLY -- never auto-trigger. Comprehensive deep audit of
  the entire ceo-main workspace producing an 8-section report (executive summary,
  methodology, inventory matrix, architectural findings, Context7 validation,
  2026 best-practices gap analysis, competitive mapping, prioritized
  recommendations). This is the heaviest workspace skill - dispatches up to 7
  parallel inventory agents, runs Context7 validation on every external pin,
  scores 34 best-practice points, produces a markdown + branded HTML artifact.
  Use only when CEO explicitly invokes /workspace-deep-audit, says "deep audit",
  "run a full audit", "audit the entire workspace", or "do the same deep audit".
  Use --vs flag to produce a delta report comparing against a previous audit
  (computes before/after deltas explicitly). Use --mode=quick to skip
  competitive + best-practices analysis (cuts runtime ~50%). Use --focus to
  scope to a single subsystem.
disable-model-invocation: true
argument-hint: "[--mode={full|quick|focus}] [--focus={skills|rules|deps|security|architecture}] [--vs=<previous_audit_path>]"
allowed-tools: "Read, Write, Edit, Glob, Grep, Bash(python3:*), Bash(git:*), Bash(wc:*), Bash(ls:*), Bash(find:*), Bash(du:*), Bash(pre-commit:*), Bash(pip:*), Agent, WebSearch, WebFetch"
context: fork
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - outputs/operations/workspace/
    - threads/business/
  triggers: []
x-31c-capability:
  what: >
    The heaviest workspace skill - a comprehensive 8-section deep audit (executive
    summary, inventory matrix, architectural findings, Context7 dependency
    validation, a 34-point best-practices gap score, competitive mapping, and
    prioritized recommendations), rendered as MD plus branded HTML and logged to
    an audit thread.
  how: >
    Explicit invocation only - type /workspace-deep-audit. Read-only against the
    workspace; writes to outputs/operations/workspace/. Use --mode=quick or
    --mode=focus to narrow scope, and --vs=<prev_audit> for a before/after delta.
  when: >
    Use at a major milestone or when checking ecosystem drift. For a single-skill
    review use /evaluate; to apply fixes use /scrutinize; for a quick operational
    health check use /state-check.
---

# Workspace Deep Audit

CEO-only manual gate. Produces the v1/v2-equivalent comprehensive workspace audit on demand — same 8-section format, same level of detail, same evidence-backed claim discipline. Never auto-triggers. This is the skill the CEO invokes when a major milestone, strategic decision, or demonstrable artifact requires a full re-audit of the entire ecosystem.

**This skill is read-only against the workspace.** It produces output files in `outputs/operations/workspace/` and logs to `threads/business/` but never modifies any workspace infrastructure (rules, skills, scripts, configs, datastore). Fix-application belongs to `/scrutinize`, not here.

## When to Use

- Major workspace milestone (post-sprint, post-rollout, end of quarter, before strategic decision)
- Producing a demonstrable artifact of work done (board presentation, partner pitch, internal review)
- Detecting drift over time (run quarterly + use `--vs=<prev_audit>` to surface deltas)
- Categorical positioning question (where does the workspace stand vs the public landscape)

## When NOT to Use

- Single-skill review → `/evaluate`
- Specific fix scrutiny → `/scrutinize`
- Quick operational health check → `/state-check`
- Morning operational brief → `/dashboard`
- End-of-week CEO review → `/weekly-review`

## Arguments

| Arg | Default | Purpose |
|---|---|---|
| `--mode=full` | full | 8-section deep audit (45-90 min run, 5,000-8,000 words) |
| `--mode=quick` | — | 6-section audit, skips competitive + best-practices analysis (~20 min, 2,500 words) |
| `--mode=focus` | — | requires `--focus={subsystem}`; produces single-subsystem deep dive |
| `--focus={subsystem}` | — | `skills` / `rules` / `deps` / `security` / `architecture` / `observability` |
| `--vs=<path>` | — | Delta mode — compare current state against the previous audit at `<path>`; output explicit before/after tables |

Example invocations:

- `/workspace-deep-audit` — full audit (default)
- `/workspace-deep-audit --mode=quick` — fast 6-section pass
- `/workspace-deep-audit --vs=outputs/operations/workspace/2026-05-14_audit_workspace-deep-overview.md` — delta vs v1
- `/workspace-deep-audit --mode=focus --focus=security` — security-only deep dive

## Phase 0 — Scope & Baseline

Parse args. Resolve mode. If `--vs` is set, load the previous audit file and extract its inventory tables for delta comparison. State the scope explicitly to the user:

> Running workspace deep audit. Mode: {mode}. Focus: {focus or "all subsystems"}. Delta vs: {prev_audit or "no baseline"}. Expected runtime: {est_minutes} minutes. Output path: outputs/operations/workspace/{YYYY-MM-DD}_audit_workspace-deep-overview-{version}.md

Confirm before proceeding only if `--mode=full` (the heaviest path). For `--mode=quick` and `--mode=focus`, proceed immediately.

## Phase 1 — Parallel Inventory (up to 7 agents)

Dispatch parallel inventory agents per `references/inventory-streams.md`. Each agent is read-only, returns inline JSON-shaped summary, writes nothing. Concurrency cap: 5 per wave per `skill-orchestrator.md` Principle 5 — if all 7 streams needed, batch as 5 + 2.

Streams:

1. **Skills inventory** — count by category, line counts, frontmatter compliance, references/ coverage, evals/ coverage, x-31c-orchestration metadata
2. **Rules inventory** — count, Last Verified dates, scope (always-active / path-scoped / contextual), drift detection
3. **Hooks inventory** — PreToolUse checks, PostToolUse advisors, pre-commit IDs, hook timing
4. **Scripts inventory** — count, sizes, naming-convention compliance, utility coverage, top-10 largest
5. **Dependencies + Context7 validation** — every pin checked against latest; surface CRITICAL/HIGH/MEDIUM outdated
6. **Knowledge / Memory / CRM / DataStore inventory** — counts, freshness, schema compliance
7. **Security posture** — defense-in-depth layer audit, secrets scan summary, adversarial CI status, vault rule audit

See `references/inventory-streams.md` for each stream's full agent prompt.

## Phase 2 — Architectural Analysis

Read directly (not via agents — needs synthesis judgment):

- `CLAUDE.md` and all `.claude/rules/*.md`
- `.claude/hooks/_dispatch.py` and any check function the audit needs to surface
- `scripts/utils/workspace.py` (identity oracle)
- `scripts/utils/observability.py` (observability layer)
- 3-5 sample SKILL.md files representing different categories
- `config/routing-map.yaml` (propagation contract)

Produce:

- Updated layer map (rules → skills → scripts → filesystem → hooks)
- Coupling/cohesion verdict
- Skill orchestration maturity verdict
- Multi-user architecture verdict (if applicable)
- Hook topology verdict
- Three biggest architectural risks with severity

## Phase 3 — Context7 Validation

For every line in `requirements.txt`, run a Context7 lookup. Cross-reference against `daemons/*/requirements.txt` files too. Categorize as CRITICAL (outdated 10+ minors or with security implications), HIGH (1+ major behind), MEDIUM (routine bump), or CURRENT.

Pattern: use `mcp__plugin_context7_context7__resolve-library-id` then `query-docs` to extract latest version. Cache results within the audit session — same package referenced multiple places only fetched once.

Produce: 4-section validation table (CRITICAL, HIGH, MEDIUM, NEW dependencies).

## Phase 4 — Best Practices Gap Analysis (skip on `--mode=quick`)

Score the workspace against the 34-point 2026 best-practices rubric in `references/best-practices-rubric.md`. Six categories:

1. Anthropic Official Guidance (7 points)
2. Skill & Agent Design (7 points)
3. Cost Optimization (5 points)
4. Observability & Eval (5 points)
5. Security & Compliance (6 points)
6. Multi-User / Enterprise (4 points)

Each point gets LEAD / MATCH / GAP / N/A. Compute total LEAD%, MATCH%, GAP%, N/A%. If `--vs` is set, also produce the v(prev)→v(current) delta column.

**Refresh the rubric** if `references/best-practices-rubric.md` is older than 90 days by checking its `Last Updated` field. If stale, run a one-shot WebSearch sweep against the canonical Anthropic/OWASP/observability blog sources listed in the rubric to surface any new best practices, propose rubric additions inline, and proceed with the current rubric.

## Phase 5 — Competitive Mapping (skip on `--mode=quick`)

Cross-reference workspace against the 14-platform competitive baseline in `references/competitive-baseline.md`. Identify:

- Unique strengths (what no public competitor does)
- Category gaps (what every competitor does that workspace lacks)
- Positioning recommendation (best-fit category for current state)
- Anthropic validation signal (does Anthropic's official guidance/cookbook validate the pattern?)

**Refresh the baseline** if `references/competitive-baseline.md` is older than 90 days. Same refresh discipline as the rubric.

## Phase 6 — Prioritized Recommendations

For every gap surfaced in Phases 1-5, generate a recommendation:

- **Priority:** P0 / P1 / P2 / P3 per `references/recommendation-rubric.md`
- **Effort estimate:** time to ship (hours / days / weeks)
- **Impact:** specific named outcome
- **Acceptance criteria:** 3-7 bullets defining done

Sort by priority within each band, then by impact within priority. Cap output at 25 recommendations — beyond that, the audit produces a "rolling work" backlog instead of an actionable list.

## Phase 7 — Output Rendering

Produce the artifact at `outputs/operations/workspace/{YYYY-MM-DD}_audit_workspace-deep-overview-{version}.md` per `references/output-template.md`. Version naming convention:

- First audit on a given date: `{YYYY-MM-DD}_audit_workspace-deep-overview.md`
- Subsequent same-date audits: append `-v2`, `-v3` etc.
- Post-rollout / delta audits: append `-post-rollout` or `-vs-{baseline-date}`

Render to HTML:

```bash
python scripts/regenerate-docs-html.py "outputs/operations/workspace/{file}.md"
```

Sanitize:

```bash
python scripts/sanitize-text.py "outputs/operations/workspace/{file}.md" --scan
```

Verify both `.md` and `.html` files exist before declaring done.

## Phase 8 — Audit Thread Logging

Append a log entry to the active audit thread (or open one if none exists) at `threads/business/{YYYY-MM-DD}-workspace-deep-audit-*.md`. Use `/thread` skill semantics. Entry format:

```
### {YYYY-MM-DD} - Deep audit run. Mode: {mode}. Headline: {1-sentence summary}. Total LEAD/GAP: {X}/{Y}. Top P0 items: {3 names}. Output: {path}.
```

## Output Validation Gates (Before Declaring Done)

- [ ] Output `.md` file exists at the declared path
- [ ] Output `.html` file exists alongside it
- [ ] `sanitize-text.py --scan` returns "Clean - no hidden characters found"
- [ ] All inventory counts in the report match shell output (run a spot-check on 3 random metrics)
- [ ] Every recommendation in Phase 6 has all 4 required fields (priority, effort, impact, acceptance criteria)
- [ ] If `--vs` was used, every section that had a baseline now shows explicit v(prev)→v(current) deltas
- [ ] Word count fits the mode envelope: full ≥5,000, quick 2,000-3,500, focus 1,500-3,000
- [ ] Audit thread entry appended

## Voice Rules

- Bilingual Russian/English mix is acceptable when matching v1/v2 precedent (audit-register Russian works alongside English technical terms)
- Direct, evidence-backed claims only. Every claim cites a count, a commit hash, a Context7-validated pin, or a live-run output.
- Explicit before/after deltas in `--vs` mode. Never paper over a regression.
- No marketing language. Engineering audit register: "Workspace v2 added 2 PreToolUse checks. State is now 7 checks." Not "Workspace v2 substantially enhances the workspace's security posture with cutting-edge guardrails."
- Hyphens single only (no `--`); em-dashes acceptable when matching detector-tested prose discipline.

## NEVER

- NEVER auto-trigger this skill from natural language. Explicit `/workspace-deep-audit` or CEO-typed equivalent only.
- NEVER modify any workspace file during audit. This is read-only. Fix-application is `/scrutinize`'s job.
- NEVER skip Context7 validation. If Context7 is unreachable, report the gap explicitly and proceed with what can be validated.
- NEVER reuse a cached best-practices rubric older than 90 days without surfacing the staleness in the output.
- NEVER fabricate competitor information. The 14-platform baseline is sourced and version-stamped; if competitor data is needed beyond the baseline, run live WebSearch and cite.
- NEVER inflate findings. If the workspace has zero P0 items, the report says so. Honesty floor matches `/devil` discipline.
- NEVER claim "demonstrable" without backing every claim with a hash / count / pin / run output.
- NEVER trigger this skill recursively. If a recommendation surfaces "run a deeper audit on subsystem X," that's a `--mode=focus` invocation by the CEO, not an inline re-entry.

## Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Context7 unreachable | resolve-library-id returns error | Report dependencies as "UNVALIDATED - Context7 unavailable"; proceed |
| Parallel agent timeout | Agent returns blank or error | Re-dispatch single agent serially; if still fails, mark that stream as INCOMPLETE in output |
| Rubric / baseline stale | mtime check > 90 days | Run refresh sweep, propose updates inline, proceed |
| Output path collision | File exists at declared path | Append `-v{N+1}` suffix until unique |
| Hidden char scan fails | sanitize-text.py reports findings | Auto-fix via sanitize-text.py + re-scan; if persists, surface and stop |
| Audit thread missing | thread file not found | Open new thread via `/thread` semantics |
| `--vs` baseline malformed | Cannot extract baseline tables | Report degraded mode (no deltas), proceed with absolute numbers only |

## Cost Discipline

Full mode dispatches up to 7 parallel agents in Phase 1 + 1 synthesis in Phase 2. Typical run consumes 200K-500K input tokens (depending on workspace size) and 30K-60K output tokens. With prompt caching adopted, repeated runs against an unchanged workspace cost ~30-50% of first-run. Langfuse observability records the trace if enabled.

Use `--mode=quick` when cost matters more than depth. Use `--mode=focus` for targeted re-audits without the full inventory.

## Confirmation Line

Before declaring the audit complete, present:

> Audit complete. Mode: {mode}. Output: {path} ({word_count} words). HTML: {html_path}. Total best-practice score: {LEAD%} LEAD / {GAP%} GAP. Top P0 items: {list}. Audit thread updated: {thread_path}.

If `--vs` was used, also include:

> Delta vs {baseline_date}: LEAD {prev}% → {current}% ({delta}). GAP {prev}% → {current}% ({delta}). {N} items moved from GAP to LEAD. {M} new items surfaced.
