# Code-Review Voices - /scrutinize external code specialists

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 1, code targets)
**Last Updated:** 2026-07-10

Extra, non-Claude finding sources for `/scrutinize` when the target is code. Two
independent code specialists run in parallel with Claude's Identify pass, and
their candidate findings survive the same Phase 2.5 refutation as every other
finding (false positives filtered, not trusted blindly):

- **Kimi-Code** (`kimi-k2.7-code:cloud`) - the default focused-diff code reviewer;
  the same voice added to `/council`.
- **GLM** (`glm-5.2:cloud`) - a second, large-context reviewer. Its edge is a 1M
  context: it reads the whole file/dir under review without the truncation
  Kimi-Code needs on big targets, and it is a different training pedigree, so it
  catches defects the other two miss.

Both are code-scoped: they never fire on `plan`, `workspace`, or `trajectory`
targets.

## When they fire

Auto-on for a **code target**, each suppressible by its own flag
(`--no-code-review` for Kimi-Code, `--no-glm-review` for GLM):

- `file:<path>` whose extension is code (`.py .js .ts .tsx .jsx .go .rs .java .kt
  .c .h .cpp .cc .rb .php .sh .bash .sql .swift .scala .lua .pl .r`), OR
- `dir:<path>` that contains such files, OR
- `execution` whose git diff touches such files.

Do NOT fire either for `plan` (no code), `workspace` (the code-surface specialist
agent already covers code), or `trajectory` targets.

Token discipline: on a small focused diff the two voices are largely redundant -
drop GLM with `--no-glm-review` when the target is small and Kimi-Code suffices.
On a large `dir:`/whole-file target GLM is the higher-value voice (full context).

Availability: each voice needs its pin present in local ollama
(`python scripts/council-models.py --get kimi-code` / `--get glm`, then
`ollama list`). If ollama is unreachable or a tag is missing, skip THAT voice,
continue the pass, and note `code-review: unavailable` / `glm-review: unavailable`
in the report header. Never fail the scrutiny because a code voice is down.

## Dispatch

One `kimi-consult.py` call per active voice, in **critique** mode, pointed at that
voice's model (both are ollama-served, so the same wrapper serves both - do NOT
add a new script). The `draft` is the code under review (file contents, dir's
concatenated code, or the diff for an execution target); the `context` is the
review brief:

```bash
# Kimi-Code (default):
python scripts/kimi-consult.py --mode critique \
  --draft '<code under review>' \
  --context '<review brief, see below>' \
  --model "$(python scripts/council-models.py --get kimi-code)"

# GLM (large-context, unless --no-glm-review):
python scripts/kimi-consult.py --mode critique \
  --draft '<code under review, full - no truncation>' \
  --context '<same review brief>' \
  --model "$(python scripts/council-models.py --get glm)"
```

Review brief (identical for both voices):

> Code-correctness review. Target: <path/diff>. Find real defects only: logic
> bugs, off-by-one and boundary errors, incorrect error handling, resource leaks,
> race conditions, injection/security issues, API misuse, and violations of the
> stated intent. For each: SEVERITY (BLOCKER|HIGH|MEDIUM|LOW) | file:line |
> one-sentence defect | why it fails. No style nits, no praise.

Run both in parallel with the Claude VIIA Identify pass (single assistant message,
alongside the other Phase 1 work) so they add no serial latency. Escape single
quotes in the code as `'\''`. Kimi-Code caps the draft to the highest-risk files
when the code exceeds its context (note the truncation in the report); GLM's 1M
context takes the full target, which is its whole point.

## How their findings are used

Neither voice is trusted as-is. Parse each line into a candidate finding
(Severity, Location, Statement, Evidence = "<voice> code review") and MERGE into
the Phase 1 Identify set, de-duplicating against Claude's findings AND against
each other (same file:line + same defect = one finding, keep the clearest
statement). The merged set then flows through Phase 2.5 refutation exactly like
any other finding - a candidate that cannot survive refutation is dropped.

Attribution: each surviving finding carries its origin in the saved report -
`source: kimi-code`, `source: glm`, or a combo (`claude+glm`, `kimi-code+glm`,
`claude+kimi-code+glm`) when more than one found it. Corroboration across voices
is signal; note it.

## Logging

Add one line per active voice to the saved report's `## Judge layer` section (or a
`## Code review` section when Phase 2.5 was skipped):

```
- Code voice (Kimi-Code): kimi-k2.7-code:cloud - N candidates, M survived refutation, K corroborated Claude
- Code voice (GLM): glm-5.2:cloud - N candidates, M survived refutation, K corroborated Claude
```

When a voice is skipped, log the reason, e.g. `- Code voice (GLM): skipped
(--no-glm-review)` or `- Code voice (GLM): unavailable (ollama tag glm-5.2:cloud
not found)`.
