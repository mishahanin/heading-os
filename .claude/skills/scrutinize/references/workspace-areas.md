# Workspace Areas - /scrutinize Parallel Dispatch

**Consumed by:** `.claude/skills/scrutinize/SKILL.md`
**Last Updated:** 2026-04-17

For `target = workspace`, `/scrutinize` dispatches exactly 5 parallel specialist agents (respecting the concurrency cap in `.claude/rules/skill-orchestrator.md`). Each agent does a full VIIA pass on its slice. A synthesis phase then runs a cross-area check.

## The 5 Areas

| # | Area | Paths (review scope) | Lens |
|---|---|---|---|
| 1 | **Code surface** | `.claude/skills/`, `scripts/`, `.claude/hooks/` | Development standards compliance, forbidden patterns, hidden-chars, error handling realism, security, frontmatter integrity, Python syntax |
| 2 | **Governance** | `.claude/rules/`, `templates/`, `config/`, `CLAUDE.md` | Rule conflicts, internal coherence, drift between CLAUDE.md and actual workspace state, classification correctness |
| 3 | **Documentation** | `reference/`, `docs/`, `context/` | Freshness (Last Updated dates), broken internal links, coverage gaps, stale claims (pointer to a file that was renamed or deleted) |
| 4 | **Knowledge & data** | `knowledge/`, `datastore/` | Organization, orphan notes, broken wiki-links, Zettelkasten conventions, datastore schema correctness |
| 5 | **Operations state** | `crm/contacts/`, state files in `.claude/`, `context/pipeline.md`, `context/current-data.md`, git cleanliness | CRM schema compliance, stale contacts, health-engine parity, untracked artifacts that should or should not be committed |

## Agent Brief Template

Dispatch each specialist agent with this brief (substitute `<AREA_N>` fields):

```
You are a `/scrutinize` specialist agent for Area <N>: <AREA NAME>.

Scope (paths to review): <PATHS from table>

Task: Run a full VIIA pass on every applicable file in scope. Use the framework from `.claude/skills/scrutinize/references/viia-framework.md` and the severity grid from `.claude/skills/scrutinize/references/severity-grid.md`. Both files are committed to the repo; read them.

Your lens: <LENS from table>

For any Python script, SKILL.md, rule, or reference file in scope: call `python3 scripts/artifact-evaluator.py --path <file-or-dir> --json` and parse the result for deterministic findings, then add your qualitative VIIA pass.

Engage maximum reasoning effort. Ultrathink. Principal-engineer posture.

Return your findings inline in this format:

## Area <N> - <NAME> Findings

Grade: <PASS | PASS-WITH-NOTES | NEEDS-REWORK | BLOCKED>

[B1] <statement>
  Location: <file:line>
  Evidence: <quote>
  Proposed fix: <concrete fix>

[H1] ... [M1] ... [L1] ... [N1] ...

If a required subcheck could not run (e.g., an expected file was missing), state so explicitly under a "Gaps" section.

NEVER:
- Write to any workspace file (including CRM, state files, plans).
- Touch `_secure/` (the vault).
- Modify CRM contact files.
- Auto-apply any fix. Your job is findings + proposals only.
- Modify git state (no `git add`, no commits).

Return findings when complete. Do not wait for further instruction.
```

## Dispatch Mechanics

Main session uses the Agent tool with `run_in_background: true` for all 5 agents in a single message (parallel execution). Each agent's prompt is the template above with its area-specific substitutions.

After dispatching, wait for all 5 to complete. Then run the synthesis phase.

### Fallback when Agent tool is unavailable

If the Agent tool is not exposed in the current thread (inline conversational execution, restricted tool mode, nested invocation), skip parallel dispatch and run the 5 area passes sequentially in the main session. For each area:

1. Load the area brief above (same lens, same scope, same exclusions).
2. Execute Validate + Identify phases in-session using Read/Grep/Bash.
3. Collect findings inline, tagged with the area name.
4. Move to the next area.

When all 5 areas are complete, proceed to the synthesis phase as normal. The approval-block header MUST note the serialized execution: `"Note: Agent tool was not available in this thread, so Phase 2 ran 5 area passes sequentially in the main session."` This transparency lets the CEO judge whether results are materially weaker than a parallel run.

## Synthesis Phase (sequential, after all 5 return)

Main session aggregates the 5 area reports and runs an explicit cross-area pass:

1. **Rule-vs-skill conflicts** - Does any rule in `.claude/rules/` require behavior that no skill implements, or prohibit behavior that a skill performs?
2. **CLAUDE.md drift** - Does any pointer in `CLAUDE.md` reference a file that was moved, deleted, or renamed?
3. **Documentation drift** - Does `reference/workspace-overview.md` claim a script or skill that does not exist? Does any skill exist that is not indexed?
4. **Classification coherence** - Is any file classified `corporate` located in a ceo-only directory per `.claude/rules/classification.md`, or vice versa?
5. **Skill-router completeness** - Does every skill in `.claude/skills/` have a corresponding entry in `.claude/rules/skill-router.md` (or explicit exemption)?

Cross-area findings are added to the consolidated output under a `Cross-Area` group.

## Degradation

If any specialist agent fails (returns error, times out, or returns malformed output), the others complete and the synthesis runs with partial input. The failing area is flagged:

```
[Area N - <name>: review incomplete, agent failed - reason: <reason>]
```

The synthesis still runs cross-area checks that do not depend on the failed area's output. The final report notes which area is partial.

## Concurrency Safety

All 5 specialist agents are read-only (`parallel_safe: true`). No shared-state conflicts during the review phase. Writes happen only during the post-approval apply phase, sequentially, area by area.
