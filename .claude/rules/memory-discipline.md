<!-- version: 1.0.0 | last-updated: 2026-07-19 -->

# Memory Discipline — Source of Truth, Fetched on Demand

Last Updated: 2026-07-19
Last Verified: 2026-07-19

Always-active rule. The workspace's files are its memory; the context window is a
cache of pointers into that memory, never the memory itself. This rule governs how
Claude reads that memory before acting, so a stale or partial pointer never becomes
a confident wrong answer.

## The failure this prevents

A pointer is not a fact. A one-line `MEMORY.md` index hook, a recalled snippet, a
skill's cached summary, a `CLAUDE.md` bullet — each is an ENTRY POINT to a record,
not the record. Pointers lag the file they point at, and the file can lag the
primary source (the deal file, thread, output, or document). Acting on the stale
layer produces an answer that is confident and wrong. This has happened; it must
not recur silently.

## Fidelity — open the record before acting

Before any CONSEQUENTIAL action, open and read the authoritative record, not the
pointer that surfaced it. Consequential means the action touches any of:

- a commitment, promise, or agreement (what was owed, to whom, by when);
- a decision or its rationale (what was chosen, and why);
- a fact, number, price, quantity, or measurement;
- a deadline, date-as-state, or schedule;
- a relationship, or a person's stated position;
- any live/current state of an ongoing matter (a negotiation, deal, plan, task).

Freshness order when layers disagree: **primary source > full record file > index
hook / recalled snippet.** The most authoritative and most recent wins. When a
pointer contradicts its record, say so — flag the drift, do not paper over it.

When a task touches a domain you hold records for, FETCH those records first. Do
not answer a consequential question from the hook alone.

Bound (to avoid defensive over-fetching): this does not mean re-reading everything
on every turn. Trivial, no-stakes conversation is exempt. The trigger is
consequence, per the list above — not volume.

## Economy — the pointer layer stays lean and trustworthy

The discipline above only works if the always-loaded pointer layer is small and
honest. Two conventions keep it so:

- **Hooks carry topic + pointer, never volatile state.** A `MEMORY.md` index line
  names WHAT a memory is about and points to the file; it does NOT quote a live
  value — a price, a ceiling, an offer, a live count, a live deadline, a current
  status. Volatile values live in the body, read on demand. A hook that never
  quotes a live number cannot go stale into a wrong number.
  - Stable descriptors ARE fine in a hook: a version pin, a fixed threshold, a
    capacity, a historical date, a hardware spec — things that identify and do not
    drift.
- **Sync the hook when the body changes.** Editing a record's body means updating
  its one-line hook in the same change, so pointers never silently diverge.

`scripts/memory-hygiene.py` enforces the volatile-hook convention mechanically
(advisory). Consolidate the index actively (`/memory-hygiene`, `/dream`) so
"remember everything" stays a lean index plus disciplined retrieval, not a
warehouse held resident. Consolidation means merging near-duplicates into one
survivor that carries both facts, and keeping hooks short. It never means
removing a fact: auto-memory is never pruned, and deletion happens only on an
explicit instruction from the operator.

## How this composes

- `/recall` and the memory index are the ENTRY to a record; this rule says read the
  record before acting on it.
- Auto-memory recalled inside a `<system-reminder>` is background context and
  point-in-time — verify against the current record before asserting it as fact.
- Applies to auto-memory, business threads, CRM, outputs, and any
  pointer-to-record structure — not only the memory system.
