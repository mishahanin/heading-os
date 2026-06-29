---
name: memory-hygiene
description: Objective-defect detector for the memory ecosystem (auto-memory + Odin brain). Runs scripts/memory-hygiene.py, which flags ONLY mechanically-verifiable rot - dangling/circular superseded_by refs, orphan memory files not linked from MEMORY.md, and an over-budget MEMORY.md - into a dated report, and exits non-zero when any are present. It never mutates memory. Use when the user says "memory hygiene", "check memory health", "memory rot", "scan memory for defects", or wants to know whether the memory store has accumulated objective defects. Do NOT use to consolidate/merge/delete/reword memory (that is judgement - use /dream); to recall a past fact (use /recall); or for operational function health (use /state-check).
argument-hint: ""
allowed-tools: "Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state:
    - "outputs/operations/memory-hygiene/"
  triggers:
    - memory hygiene
    - check memory health
    - memory rot
    - scan memory for defects
x-31c-capability:
  what: >
    Surfaces only the objective, deterministically-verifiable defects across
    auto-memory and the Odin brain (dangling/circular superseded_by, orphan
    memory files, over-budget MEMORY.md) into a dated report; never mutates
    memory.
  how: >
    Run /memory-hygiene; it executes scripts/memory-hygiene.py, writes
    outputs/operations/memory-hygiene/YYYY-MM-DD_memory-hygiene_report.md, and
    presents the gate defects plus advisory signals. Resolution stays with /dream.
  when: >
    Use to check whether memory has accumulated objective rot, on demand or on a
    weekly cadence. For consolidation/merge/delete use /dream; to recall a fact
    use /recall; for function health use /state-check.
---
# Memory Hygiene

Detect objective memory defects. This skill is a detector, not a fixer: it surfaces deterministically-verifiable rot and hands resolution to `/dream`. It NEVER merges, deletes, or rewrites memory.

## Phase 0 - Run the detector

```bash
python scripts/memory-hygiene.py
```

The script:
- Reads the canonical auto-memory dir (`<data-root>/auto-memory/`) and computes orphans, line-budget breach, and stale files.
- Runs `odin-brain-health.py --compile` and reads its temporal-validity errors.
- Writes `outputs/operations/memory-hygiene/YYYY-MM-DD_memory-hygiene_report.md`.
- Prints a one-line summary and exits `0` (clean) or `1` (objective defects present).

If the Odin brain is unavailable, the script degrades clearly and still reports the auto-memory half.

## Phase 1 - Present the result

Read the report it wrote and present, grouped:

1. **Objective defects (the gate)** - dangling/circular `superseded_by`, orphan memory files, over-budget `MEMORY.md`. These are the items that need action.
2. **Advisory (non-gating)** - stale files (>45 days), Odin stale seeds/positions, orphan principles. Mention counts; do not treat as failures.

State the exit posture plainly: "clean" or "N objective defect(s)".

## Phase 2 - Hand resolution to /dream

If objective defects exist, recommend `/dream` to resolve them (consolidation is judgement). Do NOT fix them here. Name the specific defects so `/dream` has a worklist.

## NEVER

- NEVER merge, delete, reword, or otherwise mutate any memory file - resolution is `/dream`, human-gated.
- NEVER include judgement defects (semantic contradictions, near-duplicate facts, stale-but-maybe-valid positions) in the gate; those are advisory only.
- NEVER claim the brain half ran when the script reported it unavailable - surface the degradation.
