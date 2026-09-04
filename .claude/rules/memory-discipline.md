<!-- version: 2.0.0 | last-updated: 2026-09-04 -->

# Memory Discipline — Source of Truth, Fetched on Demand

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Always-active rule. The workspace's files are its memory; the context window is a
cache of pointers into that memory, never the memory itself.

**A pointer is not a fact.** A `MEMORY.md` index hook, a recalled snippet, a
skill's cached summary, a `CLAUDE.md` bullet — each is an ENTRY POINT to a
record, not the record. Pointers lag the file they point at, and the file can lag
the primary source. Acting on the stale layer produces an answer that is
confident and wrong. This has happened; it must not recur silently.

## Fidelity — open the record before acting

Before any CONSEQUENTIAL action, open and read the authoritative record, not the
pointer that surfaced it. Consequential means the action touches any of:

- a commitment, promise or agreement (what was owed, to whom, by when);
- a decision or its rationale;
- a fact, number, price, quantity or measurement;
- a deadline, date-as-state or schedule;
- a relationship, or a person's stated position;
- the live state of an ongoing matter (a negotiation, deal, plan, task).

Freshness order when layers disagree: **primary source > full record file > index
hook / recalled snippet.** When a pointer contradicts its record, flag the drift;
never paper over it. When a task touches a domain you hold records for, FETCH
those records first — do not answer a consequential question from the hook alone.

Bound, against defensive over-fetching: this is not "re-read everything every
turn". Trivial, no-stakes conversation is exempt. The trigger is consequence, per
the list above, not volume.

## Economy — the pointer layer stays lean

A `MEMORY.md` hook carries a topic and a pointer, never a live value: no price,
no ceiling, no offer, no current count, no live deadline or status. Those live in
the body, read on demand. Stable descriptors are fine (a version pin, a fixed
threshold, a capacity, a historical date, a hardware spec). Sync the hook in the
same change as the body. Auto-memory is never pruned; deletion happens only on an
explicit instruction from the operator.

The convention written out, what `scripts/memory-hygiene.py` checks, what
consolidation does and does not mean, and how this composes with `/recall` and
auto-memory: `reference/memory-discipline-detail.md`. Read it before writing or
consolidating the index; `/memory-hygiene` and `/dream` open it as a matter of
course. Only the Economy half moved on 2026-09-04, because it fires at the moment
a hook is written and those skills are what write them. The Fidelity half above
stayed resident: it fires on a judgement about consequence, which no path and no
skill invocation can signal.
