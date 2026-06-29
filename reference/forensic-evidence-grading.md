# Forensic Evidence Grading

A three-grade evidentiary classification plus an append-only case file, so intelligence work states how strongly each claim is supported and never quietly discards a hypothesis it later abandoned.

Last Updated: 2026-06-04
Consumed by: `/osint`, `/competitor-intel`, `/validate`, `/meeting-prep`

## Why this exists

Intel skills already tag claims with a confidence band, but they treat a finding as a verdict and throw away the paths they eliminated. That invites narrative lock-in: a later session re-derives a discarded theory, or trusts a single-source claim as if it were corroborated. Forensic grading fixes both — it grades the *evidence* behind each claim, and it keeps the eliminated hypotheses visible so the reasoning trail survives across sessions.

## The three grades

Every material claim in an intel deliverable carries exactly one grade:

| Grade | Bar | What to show |
|---|---|---|
| **Confirmed** | Two or more independent sources, or one authoritative official record (regulator filing, court record, primary corporate document). | Cite each source. Independence matters — two outlets repeating one wire story is one source, not two. |
| **Deduced** | A single credible source, or a defensible inference from confirmed facts. | Show the source, or the reasoning chain from the confirmed facts to the deduction. Name the assumption the deduction rests on. |
| **Hypothesized** | Plausible but unconfirmed — a lead, a pattern, an informed guess. | State what evidence *would* confirm or refute it, so a later pass knows what to look for. |

A claim that cannot even be hypothesized responsibly is omitted, not graded.

## Coexistence with the confidence tags

This grading does NOT replace the existing `[CONFIDENCE: HIGH/MEDIUM/LOW/UNVERIFIED]` tags that `/osint` and peers already attach. The two systems sit side by side and answer different questions — confidence is *how sure are we*, grade is *what kind of evidence backs it*. Map them so they never contradict:

| Forensic grade | Compatible confidence band |
|---|---|
| Confirmed | HIGH |
| Deduced | MEDIUM (LOW if the single source is weak) |
| Hypothesized | LOW or UNVERIFIED |

When a skill already emits a confidence tag, append the grade alongside it, e.g. `[CONFIDENCE: MEDIUM | Deduced — single source: 2026 annual report]`.

## The never-delete rule

A hypothesis is never removed from the case file. When evidence refutes it, flip its `Status` to `Refuted` and write a one-line `Resolution` explaining what killed it. When evidence confirms it, flip `Status` to `Confirmed` and upgrade its grade. The ledger is append-only: statuses change, entries are added, nothing is erased. This is the whole point — six months later the case file shows not just what we believe, but what we ruled out and why.

Statuses: `Open` (still being tested), `Confirmed`, `Refuted`, `Stale` (untested and no longer load-bearing).

## The case file

Each entity under active intelligence gets one persistent case file at `outputs/intel/cases/{target-slug}.md`, created from `reference/templates/intel-case-file.md`. It is **ceo-only** (it lives under `outputs/`, always ceo-only per classification policy) — confidential intel never reaches executives.

On a fresh investigation, a skill creates the case file and seeds the hypothesis ledger. On a re-run against the same target, the skill reads the existing case file FIRST, then updates statuses and appends new hypotheses rather than starting clean. The case file is the memory; the per-run brief is the snapshot derived from it.

## Composition with /brain-audit

Forensic grading composes *alongside* `/brain-audit`, not inside it. They are two separate post-synthesis annotation layers with distinct jobs:

- `/brain-audit` reports source **freshness**, modality coverage, and source **disagreement** across the source set.
- Forensic grading reports the **evidentiary strength** behind each individual claim.

A skill that already runs `/brain-audit` runs grading as a separate phase and emits both. Neither subsumes the other; keeping them apart preserves `/brain-audit`'s single responsibility.

## How a skill applies it

1. After research, before synthesis, classify each material finding Confirmed / Deduced / Hypothesized.
2. If a case file exists for the target, read it; reconcile new findings against the existing ledger (update statuses, append new hypotheses, never delete).
3. Write/append the case file from the template.
4. In the deliverable, surface the grade next to each claim (alongside the existing confidence tag).
5. Run `/brain-audit` as usual; emit its footer in addition to the grades.
