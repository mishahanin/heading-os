# VIIA Framework - /scrutinize

**Consumed by:** `.claude/skills/scrutinize/SKILL.md`
**Last Updated:** 2026-05-27

The four-phase pass applied to every `/scrutinize` invocation. Invariant across all targets. Only the lens changes per target.

> 2026-05-27 updates:
>
> - Subcheck list expanded from 9 to 14 (R4 of the meta-review). The new 5 (workspace-specific compliance gates) only fire on relevant target types - they are guarded, not universal.
> - Added `trajectory:<run_id>` target type to the target-to-subcheck map (R12 of the meta-review). The universal subchecks (1-9) are re-interpreted for the sequential-decision shape per `references/trajectory-evaluation.md`; subchecks 10-14 mostly default to N/A.

## Ultrathink Mandate

Before emitting any finding, engage maximum reasoning effort. Think like a principal engineer reviewing for production. Surface what is wrong, not what works. Prioritize correctness and robustness over speed.

No shortcut exits: every required subcheck in Phase 1 runs, even after finding an early issue. Every finding requires evidence (a quote or reference). If nothing is wrong, grade `PASS` rather than fabricating concerns.

## Phase 1 - Validate (end-to-end walk-through)

Trace the target from start to finish.

**Per-target lens:**

- **Plan** - Does the sequence of steps actually produce the claimed outcome? Are success criteria defined and testable? Does any step depend on a prior step's output that is not explicitly produced?
- **Execution** - Does the code do what the plan promised? Do changed files fulfill their modified responsibilities? Are tests present and actually testing the right thing?
- **File / dir** - Does the artifact fulfill its stated purpose? Does a skill file meet skill standards? Does a reference file have the required headers?
- **Workspace** - Do the rules, skills, and scripts compose into a coherent system? Does `CLAUDE.md` accurately reflect the current workspace? Do declared triggers match the actual skill-router registry?

**Required subchecks (ALL must run - no shortcut exits). 9 are universal; 5 are workspace-specific compliance gates that fire only when applicable to the target.**

### Universal subchecks (apply to every target)

1. **Architecture coherence** - Do the pieces fit together without contradiction?
2. **Logic correctness** - Does the stated logic actually work for the intended cases?
3. **Dependency correctness** - Are imports, calls, file references valid and pointing at real things?
4. **Edge cases** - What happens with empty input, missing file, malformed data, unicode, zero, negative, very large?
5. **Failure modes** - What fails first under pressure? Is the failure graceful or catastrophic?
6. **Hidden assumptions** - What is the author assuming that is not stated? Are any assumptions wrong?
7. **Rule compliance** - Does the target comply with every `.claude/rules/*.md` rule that applies to it?
8. **Security** - Per `~/.claude/CLAUDE.md` forbidden patterns: `eval`, `exec`, `compile` with user input; `pickle.loads` on untrusted data; `subprocess` with `shell=True`; `yaml.load` without `SafeLoader`; `os.system` with user-controlled input; bare `except:` that swallows errors; SSL `verify=False`.
9. **Hidden-character cleanliness** - Per `.claude/rules/hidden-chars.md`: zero invisible Unicode in any workspace text file.

### Workspace-specific compliance subchecks (fire only on applicable target types)

These five are not universal because they have no signal on, say, a Python utility script. They fire when the target type matches the gate. Each subcheck explicitly declares its applicable target types - if the target does not match, the subcheck is logged as `N/A (out of scope)` rather than `PASS`. Subcheck IDs continue the universal numbering (10-14) so the target-to-subcheck map below can reference them by ID.

- **Subcheck 10 - Sanctions language compliance** - Per `feedback_sanctions_compliance` memory: never imply 31C targets sanctioned countries; every named-country mention is verified against the current sanctions list. **Applies to:** any content artefact (LinkedIn post, proposal, partnership doc, letter, official doc, xpager, presentation), any rule or skill that produces such artefacts, any reference file that lists country-specific market data.

- **Subcheck 11 - Five Core Principles alignment** - Per `.claude/rules/terminology.md`: the artefact must align with (not contradict) the five operational states - Proof of Value over PoC, Partnership for Life, Operate with Integrity, Deliver Under Pressure, Data Sovereignty Always. **Applies to:** any external-facing artefact (corporate documents, investor materials, proposals, Tribe messages), any rule that establishes operational standards.

- **Subcheck 12 - Operational vocabulary (Tribe / ODUN.ONE / DPI+)** - Per `.claude/rules/terminology.md` and `.claude/rules/voice.md`: required terms used correctly, forbidden terms ("team", "family", "crew" in Tribe context) absent. **Applies to:** any content artefact, any skill or rule that defines workspace voice or vocabulary.

- **Subcheck 13 - Voss negotiation tone** - Per `.claude/rules/voss.md`: tactical empathy applied to outgoing communications - labels before logic, calibrated questions over demands, precise numbers (no round-number pricing), accusation audits for sensitive topics. **Applies to:** any outgoing communication artefact (emails, proposals, partnership docs, investor pitches, meeting prep, negotiation prep, CEO-to-CEO letters).

- **Subcheck 14 - Corporate-docs guardrail compliance** - Per `.claude/rules/corporate-docs.md`: any artefact that qualifies as one of the five locked doctypes (external letter, proposal, partnership doc, official doc, xpager) routes through the correct skill, uses the locked template, embeds GT Standard fonts and the 31C letterhead. **Applies to:** any content artefact that could be classified as one of the five locked doctypes, any skill that produces such artefacts.

### Target-to-subcheck map

| Target type | Universal (1-9) | Sanctions (10) | Five Principles (11) | Vocabulary (12) | Voss (13) | Corp-docs (14) |
|---|---|---|---|---|---|---|
| plan | yes | if plan touches content | yes | yes | if plan touches comms | if plan produces corp doc |
| execution (Python script) | yes | if script generates content | no | no | no | no |
| execution (content artefact) | yes | yes | yes | yes | yes | yes |
| execution (skill or rule) | yes | yes | yes | yes | conditional | yes |
| file (Python) | yes | no | no | no | no | no |
| file (content / markdown) | yes | yes | yes | yes | conditional | yes |
| file (skill or rule) | yes | yes | yes | yes | conditional | yes |
| dir | per-file resolution | per-file | per-file | per-file | per-file | per-file |
| workspace | yes (cross-area synthesis) | yes (governance area) | yes | yes | yes (governance) | yes (governance) |
| trajectory:<run_id> | yes (re-interpreted for sequential-decision shape per `trajectory-evaluation.md`) | if step_end events list content-artefact files | if step_end events list external-facing artefacts | if step_end events list any content | if step_end events list outgoing comms | if step_end events list one of the five locked doctypes |

A subcheck logged `N/A (out of scope)` is treated as passed for grade purposes - it never blocks PASS. A subcheck logged `FAIL` produces a finding at appropriate severity per `severity-grid.md`.

**Trajectory target note:** for `trajectory:<run_id>`, the universal subchecks (1-9) are re-interpreted as sequential-decision questions (e.g. subcheck 1 "Architecture coherence" becomes "Did the agent execute steps in declared order?"). Full re-interpretation table lives in `references/trajectory-evaluation.md` § "Universal subchecks (1-9) re-interpreted for trajectories". Deterministic tool-call records win over rationale prose when they disagree (gaming-the-judge mitigation).

## Phase 2 - Identify (adversarial pass)

Principal-engineer lens: assume the author took shortcuts. Find what is wrong, missing, or weak.

**Each finding MUST include:**

- **ID** - `B1`, `H1`, `M1`, `L1`, `N1` (per `severity-grid.md` convention)
- **Severity** - `BLOCKER` / `HIGH` / `MEDIUM` / `LOW` / `NIT` (per `severity-grid.md`)
- **Location** - file + line number, or plan step number, or workspace area name
- **What is wrong** - one-sentence declarative statement, not a suggestion ("Line 47 uses `os.path` instead of `pathlib`" - NOT "Consider using `pathlib` on line 47")
- **Evidence** - quoted snippet or file reference proving the finding. No handwaves.

**Anti-patterns to detect (non-exhaustive):**

- Declarations that do not match implementations
- Functions that handle the happy path but crash on the first unhappy input
- Rules that contradict other rules
- Pointers to files that do not exist
- Workspace index claims that drift from reality
- Plan steps that cannot be executed in the declared order
- Success criteria that are not testable
- Security forbidden patterns

## Phase 3 - Improve (concrete fixes)

For every `BLOCKER`, `HIGH`, and `MEDIUM` finding: produce a concrete proposed fix.

**Fix format:**

- For code: a diff or a replacement snippet showing the new code exactly
- For plans: a rewritten step block
- For rules: the corrected rule wording
- For files: the replacement content or a precise edit instruction
- For workspace-level issues (rule conflict, CLAUDE.md drift): the specific file(s) to edit and the exact change

Every fix must be applyable by a mechanical edit - no "handle this somehow" vagueness. If a fix needs user judgment that the skill cannot make, flag it as a `QUESTION` and defer (not a finding, a gate).

`LOW` and `NIT` findings get fixes only when they are cheap one-liners. Otherwise report the finding without a fix.

## Phase 4 - Adjust (apply approved set)

Produces the approval block (see SKILL.md Phase 3 output format). Writes nothing to disk until the user explicitly approves. On approval, applies fixes sequentially, runs post-apply checks per file, reports pass/fail, and halts further applies on any check failure.

## Per-Target Grade Criteria

Use `severity-grid.md` mapping. Additionally:

- For **plan** target, `PASS` also requires: all success criteria testable, all dependencies between steps explicit, rollback/recovery path defined for any destructive step.
- For **execution** target, `PASS` also requires: changed files match the plan's declared scope, post-apply checks would pass on every edited file, no regressions introduced to unrelated files.
- For **workspace** target, `PASS` also requires: no cross-area contradictions (e.g., a rule that no skill follows), `CLAUDE.md` accurate to current state, `reference/workspace-overview.md` in sync with actual files.

## Output

Hand results to SKILL.md Phase 3 (approval block construction) and Phase 5 (report persistence).
