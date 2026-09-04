# Memory discipline — the Economy half, written out

Last Updated: 2026-09-04

Companion to the always-on `.claude/rules/memory-discipline.md`, which keeps the
Fidelity half resident. This file carries the Economy half: the convention that
keeps the pointer layer lean, what the mechanical check enforces, and how the
rule composes with the rest of the memory ecosystem.

**Consumed by:** `/memory-hygiene`, `/dream`, `/recall`, and anything else that
writes or consolidates `MEMORY.md`. Open it before touching the index.

It moved out of the resident rule on 2026-09-04 because it fires at one moment,
writing or consolidating a memory hook, and the skills that do that are named
above. The Fidelity half stayed resident because it fires on a judgement about
consequence that no path and no skill can signal.

## Economy — the pointer layer stays lean and trustworthy

- **Hooks carry topic + pointer, never volatile state.** A `MEMORY.md` index line
  names WHAT a memory is about and points at the file; it does NOT quote a live
  value — a price, a ceiling, an offer, a live count, a live deadline, a current
  status. Those live in the body, read on demand. A hook that never quotes a live
  number cannot go stale into a wrong number. Stable descriptors ARE fine: a
  version pin, a fixed threshold, a capacity, a historical date, a hardware spec.
- **Sync the hook when the body changes**, in the same change, so pointers never
  silently diverge.

`scripts/memory-hygiene.py` enforces the volatile-hook convention mechanically
(advisory). Consolidate the index actively (`/memory-hygiene`, `/dream`) so
"remember everything" stays a lean index plus disciplined retrieval rather than a
warehouse held resident. Consolidation means merging near-duplicates into one
survivor carrying both facts, and keeping hooks short. It never means removing a
fact: auto-memory is never pruned, and deletion happens only on an explicit
instruction from the operator.

## How this composes

`/recall` and the memory index are the ENTRY to a record; the resident rule says
read the record before acting on it. Auto-memory recalled inside a
`<system-reminder>` is background context and point-in-time — verify it against
the current record before asserting it as fact. Applies to auto-memory, business
threads, CRM, outputs, and any pointer-to-record structure, not only the memory
system.
