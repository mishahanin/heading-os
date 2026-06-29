---
name: next
description: >
  State-aware "what's next" recommender. Reads what just happened (the session handoff
  pointer, the newest outputs/ files, recent git commits, active business threads) plus the
  skill-relationship catalog, then names the logical next step(s) and the exact slash-command
  to run -- "you finished X, the next step is Y, run /Y." Read-only: it recommends commands,
  it never runs them. Use when the user says "what's next", "what should I do now", "where
  were we", "logical next step", "recommend next", or hits a mid-session lull. Do NOT use for:
  full context load at session start (that is /prime), function-health diagnosis (/state-check),
  the daily morning briefing (/dashboard), or the end-of-week review (/weekly-review).
argument-hint: "[optional: area, e.g. 'deals' or 'content']"
allowed-tools: "Read, Bash(python3:*), Bash(python:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - "what next"
    - "what should I do now"
    - "logical next step"
    - "where were we"
    - "recommend next"
x-31c-capability:
  what: >
    State-aware "what's next" recommender - reads the session handoff, newest outputs, recent commits,
    and active business threads, then names 2-4 ranked next steps and the exact slash-command to run.
  how: >
    Run /next (optionally /next <area>). Read-only - it names the command but never runs it; the CEO
    decides and invokes it. Backed by scripts/next-signal.py and scripts/skill_graph.py.
  when: >
    Use at a mid-session lull or "where were we". For a full context load at session start use /prime;
    for function-health diagnosis use /state-check; for the daily briefing use /dashboard.
---
# Next (state-aware recommender)

Tell the CEO the logical next move and the exact command — grounded in what just happened, not a generic menu. Read-only: name the command, never run it.

---

## Phase 0: Gather the signal

1. Run the "what just happened" reader:
   ```bash
   python scripts/next-signal.py --json
   ```
   It returns a ranked recent-actions list from four sources, newest first and handoff-weighted: the session handoff pointer (`outputs/operations/handoff-archive/.latest/summary.md`), the newest `outputs/` files (noise dirs excluded), recent `git log` subjects, and active `threads/business/` files. If it exits non-zero ("outputs unreadable" etc.), say so plainly and fall back to the handoff pointer alone.
2. Load the relationship catalog accessor (do not read the whole CSV into context):
   ```bash
   python scripts/skill_graph.py followers <skill>      # next steps after a skill
   python scripts/skill_graph.py by-output-dir <path>   # which skill produced a recent output
   ```

## Phase 1: Match

1. For each recent action, map it to its producing skill — via `skill_graph.py by-output-dir` on the output's directory, or directly when the handoff/commit names the work.
2. Look up that skill's `followed_by` edges. The handoff pointer is the strongest signal (explicit human intent); outputs/git/threads are supporting inference.

## Phase 2: Recommend

1. Emit **2–4 ranked recommendations**, each one line: `{what just happened} → run /{skill} ({one-line why}).` Order optional steps first, then any genuinely-gated next step, and say which is which.
2. **Honesty floor:** if no edge is strong enough, emit nothing rather than a weak guess. "Nothing obvious is queued — the last clear action (`{X}`) has no strong next step. Tell me the area and I'll point you." beats a fabricated recommendation.
3. **Read-only.** Name the slash-command; never invoke it. The CEO decides and runs it (CEO sovereignty; `.claude/rules/prompt-refinement.md` Phase 3). Recommend running the chosen step in a fresh context where it is heavy.

---

## Console-first

Both backing scripts run standalone from the terminal or chat with no daemon and no browser:
- `python scripts/next-signal.py` — the recent-actions signal (text or `--json`).
- `python scripts/skill_graph.py followers <skill>` — the relationship lookup.
`/next` is a thin reasoning layer over these; the state is the files, not a UI.

## Voice rules

- Hyphens, not em-dashes; ODUN.ONE, DPI+, Tribe per `.claude/rules/terminology.md`.
- Bilingual: answer in the language the CEO used.
- Terse. A recommendation is one line, not a paragraph.

## NEVER

- NEVER execute, send, or invoke the recommended skill — name the command only.
- NEVER fabricate a next step to fill silence. Below the confidence floor, recommend nothing and ask.
- NEVER duplicate `/prime` (full context load) or `/state-check` (function health) — point at them if they are the right move, do not reimplement them.
- NEVER read or echo personal-thread content — `next-signal.py` reads business threads only; personal threads stay out of the signal.
