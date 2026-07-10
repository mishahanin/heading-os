# Code-Review Voice - /scrutinize Kimi-Code specialist

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 1, code targets)
**Last Updated:** 2026-07-10

An extra, coding-specialised finding source for `/scrutinize` when the target is
code. It is the same Kimi-Code voice added to `/council` as a 4th player, pointed
at `/scrutinize`'s review job: a second, non-Claude, code-specialist read of the
exact code under review, whose findings then survive the same Phase 2.5
refutation as every other finding (so its false positives are filtered, not
trusted blindly).

## When it fires

Auto-on for a **code target**, unless `--no-code-review` is set:

- `file:<path>` whose extension is code (`.py .js .ts .tsx .jsx .go .rs .java .kt
  .c .h .cpp .cc .rb .php .sh .bash .sql .swift .scala .lua .pl .r`), OR
- `dir:<path>` that contains such files, OR
- `execution` whose git diff touches such files.

Do NOT fire for `plan` (no code), `workspace` (the code-surface specialist agent
already covers code), or `trajectory` targets. Skip silently when
`--no-code-review` is present, and note the skip in the approval-block header.

Availability: requires the `kimi-code` pin to resolve to a tag present in local
ollama (`python scripts/council-models.py --get kimi-code`, then `ollama list`).
If ollama is unreachable or the tag is missing, skip the code voice, continue the
normal VIIA pass, and note `code-review: unavailable` in the report header. Never
fail the scrutiny because the code voice is down.

## Dispatch

One `kimi-consult.py` call in **critique** mode, pointed at the code model. The
`draft` is the code under review (the file contents, the dir's concatenated code,
or the diff for an execution target); the `context` is the review brief:

```bash
python scripts/kimi-consult.py --mode critique \
  --draft '<code under review>' \
  --context 'Code-correctness review. Target: <path/diff>. Find real defects only: logic bugs, off-by-one and boundary errors, incorrect error handling, resource leaks, race conditions, injection/security issues, API misuse, and violations of the stated intent. For each: SEVERITY (BLOCKER|HIGH|MEDIUM|LOW) | file:line | one-sentence defect | why it fails. No style nits, no praise.' \
  --model "$(python scripts/council-models.py --get kimi-code)"
```

Run it in parallel with the Claude VIIA Identify pass (single assistant message,
alongside the other Phase 1 work) so it adds no serial latency. Escape single
quotes in the code as `'\''`. For a large `dir:` target, cap the draft at the
files actually in scope; if the code exceeds the model's context, review the
highest-risk files first and note the truncation in the report.

## How its findings are used

Kimi-Code's raw output is NOT trusted as-is. Parse each line into a candidate
finding (Severity, Location, Statement, Evidence = "Kimi-Code code review") and
MERGE it into the Phase 1 Identify finding set, de-duplicating against Claude's
own findings (same file:line + same defect = one finding, keep the clearer
statement). The merged set then flows through Phase 2.5 refutation exactly like
any other finding - a Kimi-Code finding that cannot survive refutation is dropped
like any other false positive.

Attribution: a finding that originated from (or was corroborated by) the code
voice carries `source: kimi-code` (or `source: claude+kimi-code` when both found
it) in the saved report, so the CEO can see where each finding came from.

## Logging

Add one line to the saved report's `## Judge layer` section (or a `## Code
review` section when Phase 2.5 was skipped):

```
- Code voice: kimi-k2.7-code:cloud - N candidate findings, M survived refutation, K corroborated Claude
```

When skipped, log the reason: `- Code voice: skipped (--no-code-review)` or
`- Code voice: unavailable (ollama tag kimi-k2.7-code:cloud not found)`.
