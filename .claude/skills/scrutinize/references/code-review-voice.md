# Code-Review Voices - /scrutinize external code specialists

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 1, code targets)
**Last Updated:** 2026-07-18

Extra, non-Claude finding source for `/scrutinize` when the target is code. One
independent code specialist runs in parallel with Claude's Identify pass, and
its candidate findings survive the same Phase 2.5 refutation as every other
finding (false positives filtered, not trusted blindly):

- **Kimi** (k3 at high reasoning effort, via the proxy) - the default
  focused-diff code reviewer; the same voice added to `/council`.

It is code-scoped: it never fires on `plan`, `workspace`, or `trajectory`
targets.

## When it fires

Auto-on for a **code target**, suppressible by `--no-code-review`:

- `file:<path>` whose extension is code (`.py .js .ts .tsx .jsx .go .rs .java .kt
  .c .h .cpp .cc .rb .php .sh .bash .sql .swift .scala .lua .pl .r`), OR
- `dir:<path>` that contains such files, OR
- `execution` whose git diff touches such files.

Do NOT fire for `plan` (no code), `workspace` (the code-surface specialist
agent already covers code), or `trajectory` targets.

Availability: the voice needs its pin present in the proxy catalog (`cliproxy
models`). If the proxy is down, skip the Kimi voice, continue the pass, and
note `code-review: unavailable` in the report header. Never fail the scrutiny
because the code voice is down. On our plan k3 carries a 1M-token context, so
for a deep audit the Kimi voice can read a large target whole too (see Dispatch,
wide mode); for routine large targets the Claude-native voice remains the
full-context reader.

## Dispatch

One `kimi-consult.py` call, in **critique** mode, pointed at k3 via the proxy
(do NOT add a new script). The `draft` is the code under review (file
contents, dir's concatenated code, or the diff for an execution target); the
`context` is the review brief:

```bash
python scripts/kimi-consult.py --mode critique \
  --draft '<code under review>' \
  --context '<review brief, see below>' \
  --model k3 --reasoning-effort high --max-tokens 12000
```

Explicit `--model k3` (not `--get kimi` - the fast pin used elsewhere stays
`kimi-for-coding`; code review benefits from k3's depth). k3 always thinks and
ignores `--temperature`; give it head-room via `--max-tokens 12000` rather than
trying to tune its effort with sampling flags.

Review brief:

> Code-correctness review. Target: <path/diff>. Find real defects only: logic
> bugs, off-by-one and boundary errors, incorrect error handling, resource leaks,
> race conditions, injection/security issues, API misuse, and violations of the
> stated intent. For each: SEVERITY (BLOCKER|HIGH|MEDIUM|LOW) | file:line |
> one-sentence defect | why it fails. No style nits, no praise.

Run in parallel with the Claude VIIA Identify pass (single assistant message,
alongside the other Phase 1 work) so it adds no serial latency. Escape single
quotes in the code as `'\''`. Default (focused-diff) review: if the
draft exceeds k3's context the voice caps it to the highest-risk files and notes
the truncation. **Wide mode** for a deep audit (`--relentless`, or an explicit
whole-subsystem review): pass the full target to k3 rather than trimming - its
1M-token context covers whole subsystems, giving a second independent
full-context read alongside the Claude-native voice, and raise `--max-tokens` to
`16000` for the larger findings set. Wide mode is opt-in because k3 is slow and
verbose (deep-batch, not latency-sensitive), so the routine focused-diff default
is unchanged.

## How the findings are used

The voice is not trusted as-is. Parse each line into a candidate finding
(Severity, Location, Statement, Evidence = "kimi code review") and MERGE into
the Phase 1 Identify set, de-duplicating against Claude's findings (same
file:line + same defect = one finding, keep the clearest statement). The
merged set then flows through Phase 2.5 refutation exactly like any other
finding - a candidate that cannot survive refutation is dropped.

Attribution: each surviving finding carries its origin in the saved report -
`source: kimi`, `source: claude`, or `source: claude+kimi` when both found it.
Corroboration across voices is signal; note it.

## Logging

Add one line to the saved report's `## Judge layer` section (or a `## Code
review` section when Phase 2.5 was skipped):

```
- Code voice (Kimi): k3 (proxy, reasoning-effort high) - N candidates, M survived refutation, K corroborated Claude
```

When the voice is skipped, log the reason, e.g. `- Code voice (Kimi): skipped
(--no-code-review)` or `- Code voice (Kimi): unavailable (proxy unreachable)`.
