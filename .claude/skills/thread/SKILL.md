---
name: thread
description: "Open, log, close, find threads in the operational threads registry. Triggers on 'open a thread', 'log to the [name] thread', 'close the [name] thread', 'thread list', 'thread find'. NOT for: single emails (use /email-draft), notes (use /zk), CRM logs (use /crm)."
argument-hint: "<command> [args]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Glob, Grep"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state:
    - "threads/"
    - "memory/MEMORY.md"
  triggers:
    - "open a thread"
    - "log to the"
    - "log this to the"
    - "close the"
    - "hold the"
    - "reopen"
    - "what threads are active"
    - "thread list"
    - "thread find"
    - "show me the thread"
    - "thread for"
x-heading-capability:
  what: >
    Maintains the operational threads registry - the running state of business
    and personal situations across sessions, with decisions, follow-ups, and
    linked artifacts. Distinct from CRM (people), pipeline (deals), ZK (ideas).
  how: >
    Run /thread <command> [args] - open, log, close, hold, reopen, list, find,
    show. Drives scripts/thread.py writing to threads/; always asks before
    opening or logging.
  when: >
    Use for a multi-step situation that follows through across sessions. For a
    single email use /email-draft; for a note use /zk; for a contact log use
    /crm.
x-heading-routing:
  category: Operations
  triggers:
    - open a thread
    - log to thread
    - close thread
    - hold thread
    - reopen
    - thread list
    - thread find
    - what threads are active
  exclusions:
    - Single email -> /email-draft
    - knowledge note -> /zk
    - CRM log -> /crm
  compound: 'No'
  router: auto
---

# /thread - Operational Threads Registry

Manages running state of business and personal life situations across sessions. Distinct from CRM (people), pipeline (deals), ZK (ideas), outputs (artifacts).

**Spec:** `docs/superpowers/specs/2026-04-29-threads-registry-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-04-29-threads-registry-design.md`)

## When to use

| Trigger | Action |
|---|---|
| Multi-step external situation that has follow-through across sessions | `open` |
| Recurring project (HEADING book, ODUN.ONE x TrustONE, SPL) | `open` |
| Personal life thread (medical, family, travel) | `open` with type `personal` |
| Outbound communication on an existing thread | `log` with `--artifact` |
| Decision made on an active thread | `log --decision "..."` |
| Pending action surfaced | `log --follow-up "..."` |
| Follow-up completed | `log --done <index>` |
| Thread done | `close --reason "<why>"` |
| Thread paused for weeks | `hold --reason "<why>"` |
| Thread reactivates | `reopen` |
| Operator says "do not remind me about this until <date>" | `quiet --until <date>` |
| Operator says "freeze this, I will raise it myself" | `quiet --indefinite` |
| Quiet period is over | `quiet --clear` |
| Survey active threads | `list` |
| Lookup by keyword | `find <query>` |
| Read full thread | `show <id>` |
| /prime archive scan | `archive-scan --apply` |
| MEMORY.md index lines look wrong or hand-edited | `reindex` |

## When NOT to use

- Code work, debugging, technical fixes - explicitly out of scope.
- Single emails to new contacts - use `/email-draft`.
- Knowledge notes / insights - use `/zk`.
- CRM contact logs - use `/crm`.
- Anything inside `_secure/` - the vault has its own audit log via `_secure/.audit-log.md`.

## Approval gate (always)

I never open or log silently. Before invoking the CLI, I ask:

> "This looks like a thread / part of the [Porkbun] thread. Open / log it?"

The user approves, modifies, or skips. After several months of trusted use, this gate may be relaxed - deferred to v2.

## CLI

```bash
python3 scripts/thread.py open <business|personal> "<title>"
python3 scripts/thread.py log <thread-id> "<event>" [--artifact PATH ...] [--decision TEXT ...] [--follow-up TEXT ...] [--done INDEX]
python3 scripts/thread.py close <thread-id> --reason "<why it leaves the index>"
python3 scripts/thread.py hold <thread-id> --reason "<why it leaves the index>"
python3 scripts/thread.py reopen <thread-id>
python3 scripts/thread.py list [--type business|personal] [--status active|on-hold|closed]
python3 scripts/thread.py find "<query>"
python3 scripts/thread.py show <thread-id>
python3 scripts/thread.py quiet <thread-id> [--until YYYY-MM-DD | --indefinite | --clear]
python3 scripts/thread.py archive-scan [--apply]
python3 scripts/thread.py reindex [--dry-run]
```

## Quiet periods

A quiet thread is one the operator has asked me not to raise. I never surface it
proactively. It stays out of session-opener rollups, `/next`, `/dashboard`,
`/weekly-review`, ad-hoc "what is open" answers, and any nudge. I answer about it
only when the operator raises it themselves.

Two forms. `--until <date>` is a dated pause and it expires on its own.
`--indefinite` has no end date and lifts only when the operator raises the
subject. Both forms write the state into the thread's frontmatter. Both also stamp a
`[quiet until <date>]` marker on the thread's line in the MEMORY.md index. That
index loads every session, so it tells me the thread is quiet.

## What the index line says

A thread's line in MEMORY.md carries the title, the path, and a hook. The hook is
DERIVED state — `active, last 2026-08-15` — plus the `[quiet until …]` marker when
one applies. It does not retell the newest event.

The index loads every session. A hook that quoted live prose therefore went stale
into a wrong answer, which `.claude/rules/memory-discipline.md` forbids. Thirty
such hooks at 120 characters also made the block 8 KB. What happened lives in the
thread file instead, one `show` away.

`reindex` rebuilds every hook from frontmatter, so a drifted or hand-edited index
is repairable. It rewrites hooks ONLY. It never adds or drops a line, because
membership is a status decision that belongs to `close`, `hold`, and `reopen`.

`archive-scan` reports a dated quiet period once it expires, and it never
proposes on-hold for a thread that is still quiet.

## Personal-thread rule

I never reference content from `threads/personal/` in any output destined outside the workspace - emails, LinkedIn posts, proposals, Tribe messages, anything. Personal-thread context informs my work for Misha only.

The `protect-personal-threads.py` PreToolUse hook plus `.gitignore` plus classification + path-filter belts make four independent enforcement layers; behavioural compliance from me is the fifth.

## Auto-trigger heuristic

### Propose OPENING when:
- A second-or-later round of an external email exchange likely to span sessions.
- An external situation gets named with no current home (registrar, vendor dispute, legal, medical, family).
- A recurring-project decision is being made.
- The user says "remember this", "track this", "log this".

### Propose LOGGING when:
- Outbound communication to a counterparty already in an active thread's `counterparties:`.
- A decision is made matching an active thread's title or tags.
- An artifact is saved to a path linked by an active thread.

### Skip:
- Code work, debugging, technical fixes.
- One-shot tasks.
- Anything inside `_secure/`.
