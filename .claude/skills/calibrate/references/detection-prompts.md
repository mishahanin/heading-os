# Detection prompts - /calibrate

Consumed by: `.claude/skills/calibrate/SKILL.md` Phase 2.
Last Updated: 2026-05-13

Six categories of session-end signals the /calibrate skill detects from the
parser envelope (full mode) or in-context conversation (light mode).

Each category section carries: the detection prompt Claude follows, the
disambiguation rule, the default patch-target heuristic, and one worked example.

## 1. Corrections

**Detection prompt:**
> Scan the `user_turns` array (full mode) or recent user messages (light mode)
> for explicit behavioural reversals or stop-orders directed at the assistant.
> Match patterns: "no", "stop", "don't", "I told you", "that's wrong", "do it
> like X instead", "never", "stop doing Y". Each match is one candidate.

**Disambiguation rule:** A correction targets the assistant's *behaviour*, not
a fact. "ExampleTelco is in UAE, not Saudi" is a fact correction - skip. "Stop
writing in title case" is a behaviour correction - include.

**Patch-target heuristic:**

1. If the correction names a specific tool or skill -> that skill's `SKILL.md`.
2. If it names a recurring style choice -> Memory or the relevant rule file.
3. Default fallback: Memory.

**Worked example:**

- Source quote: "stop using em-dashes in my drafts"
- Category: correction
- Proposed target: `~/.claude/projects/.../memory/feedback_em_dash_rolling_clause.md`
- Proposed diff: append `- 2026-05-13: confirmed again - session-end calibration capture` under the main "Why" block.
- Blast radius: low (memory only).

## 2. Preferences

**Detection prompt:**
> Scan user turns for explicit declared preferences. Patterns: "I prefer X
> over Y", "always use", "from now on", "whenever you X, do Y",
> "I want you to". Excludes one-shot instructions ("do this for this one email")
> - only sticky preferences with durability signal.

**Disambiguation rule:** A preference is durable. "Use shorter sentences in
this email" is one-shot - skip. "Always use shorter sentences in outbound
prose" is a preference - include.

**Patch-target heuristic:**

1. Almost always Memory.
2. If the preference is about a specific skill's mechanics -> that skill's `SKILL.md` (e.g., "I prefer /osint to skip Phase 0.5 for known targets").

**Worked example:**

- Source quote: "always present clarifying questions as a numbered list with
  lettered options and a Моя рекомендация footer"
- Category: preference
- Proposed target: `~/.claude/projects/.../memory/feedback_question_format.md` (existing - append reinforcement bullet)
- Blast radius: low.

## 3. Repeated patterns

**Detection prompt:**
> Identify cases where the same correction or instruction recurs across the
> session - same user intent appearing 2+ times in different topical contexts.
> Compute pairs/triples of user turns whose semantic content matches.

**Why this matters:** A correction that repeats is signal that the underlying
behaviour is hard-coded somewhere and needs a *structural* fix, not just a
memory note. If "stop using em-dashes" appeared three times despite an existing
rule, the rule is failing - that's a calibration target.

**Light mode:** SKIPPED. Reliable repeat detection requires the full event
count from the parser envelope.

**Patch-target heuristic:**

1. Look at the file that *should* have prevented the repeat.
2. Often a rule file.
3. Sometimes a skill prompt that's overriding the rule.

**Worked example:**

- Source quotes: "CRM is at crm/contacts/ not crm/" (turn 3, 7, 12)
- Category: repeated pattern (3 occurrences)
- Proposed target: `.claude/rules/skill-router.md` (corporate - routes to review queue)
- Blast radius: high (rule layer, propagates to execs).

## 4. Errors / friction

**Detection prompt:**
> Walk the `tool_errors` array (full mode only). Surface candidates for:
> (a) same tool invocation failing 2+ times before succeeding;
> (b) wrong file paths (file-not-found errors);
> (c) hallucinated tool/command names;
> (d) malformed Bash/Python syntax that was retried.

**Why this is the highest-quality category:** The signal is structured, not
natural language. No LLM disambiguation needed - the parser already extracted
the array.

**Light mode:** SKIPPED. Requires the structured `tool_errors` array from the
parser envelope.

**Patch-target heuristic:**

1. Almost always a skill or a reference file.
2. Wrong-path errors -> the skill that emitted the wrong path.
3. Hallucinated tool names -> the skill body that should have known the correct name (e.g., a hardcoded path that's now stale).

**Worked example:**

- Tool error: `python: can't open file 'scripts/linkedin_archive.py'` (snake_case fail)
- Tool result: success after retry with `linkedin-archive.py` (kebab-case)
- Category: error / friction
- Proposed target: any skill that references the wrong name (none in this case - operator typed it directly)
- Blast radius: depends on target.

## 5. Success patterns

**Detection prompt:**
> Scan user turns *immediately after* assistant turns for explicit endorsement
> of a non-obvious assistant choice. Patterns: "perfect", "yes exactly", "keep
> doing that", "this is good - note it". Also detect *implicit* acceptance:
> the user proceeds without pushback on a structural choice that was non-default
> (e.g., assistant chose a table format when a bullet list would have been
> typical and the user accepted it).

**Why this is the quietest category:** Corrections are loud; successes are
silent. The workspace's existing memory feedback rule explicitly says to save
from success too - this category enforces it.

**Light mode:** kept.

**Patch-target heuristic:** Always Memory. A success memory is "this approach
worked - lock it in."

**Worked example:**
- Assistant turn: presented options as a numbered list with `Моя рекомендация`
  footer plus a summary table.
- Next user turn: "C" (accepted option C without pushback on format).
- Category: success pattern
- Proposed target: `~/.claude/projects/.../memory/feedback_question_format.md` (reinforcement) OR new memory if no existing covers it.

## 6. Voice violations

**Detection prompt:**
> Cross-reference `assistant_turns` against three workspace rule files:
> `.claude/rules/humanization.md`, `.claude/rules/voice.md`,
> `.claude/rules/hidden-chars.md`. Flag specific violations:
> - Em-dashes (U+2014, the wide horizontal stroke) in Misha-voice drafts
> - Banned vocabulary (load `reference/humanization-banned-vocabulary.md` for the list)
> - Hidden Unicode characters (any in `reference/hidden-characters.md` banned set)
> - Title-case headings in prose outputs

For each candidate violation, check the *next* user turn for confirmation:
- If user flagged it: high-confidence calibration signal.
- If user did NOT flag it: log as low-confidence, do not propose a patch.

**Light mode:** kept.

**Patch-target heuristic:** Always corporate. The rule files are
corporate-classified - violations route to the corporate review queue, never
auto-applied.

**Exception:** If the violation type is already covered by an existing memory
file (e.g., em-dashes are already in `feedback_em_dash_rolling_clause.md`),
the patch becomes a memory reinforcement (ceo-only, applies in place) rather
than a rule edit (corporate, routes to review).

**Worked example:**
- Assistant turn contains: "It's important to note that this leverages..."
- User next turn: "stop using 'leverage' - banned"
- Category: voice violation
- Proposed target: either (a) memory reinforcement if existing memory covers it, or (b) `.claude/rules/humanization.md` banned-vocab list (corporate review).
- Blast radius: medium (if rule patch) or low (if memory).

## Candidate signal shape

Detection emits one or more candidates of this shape:

````json
{
  "id": 1,
  "category": "correction|preference|repeated_pattern|error|success_pattern|voice_violation",
  "source_quote": "<verbatim user turn or assistant turn>",
  "source_ts": "<ISO timestamp>",
  "proposed_target": "<absolute or workspace-relative path>",
  "proposed_target_classification": "ceo-only|corporate",
  "proposed_diff_body": "<full proposed text or unified diff>",
  "rationale_one_line": "<why this patch>",
  "blast_radius": "low|medium|high",
  "confidence": "low|medium|high"
}
````

## Sort order in Phase 4 display

Apply this tuple sort to all candidates:

1. `target_classification` ascending (ceo-only first, corporate last)
2. Target group (Memory < Settings < Skills < Rules)
3. `blast_radius` ascending (low first)
4. `confidence` descending (high first)

Numbering follows the sorted order so item 1 is always the lowest-risk
ceo-only memory patch.

## Idempotency check (mandatory before proposing)

For every candidate, before adding it to the proposal list:

1. If proposed target file exists: Grep for substring match of the proposed diff
   body. If matched, drop the candidate silently.
2. If proposed target file does not exist (new memory file): check that the
   `MEMORY.md` index does not already point to a file with the same slug.
