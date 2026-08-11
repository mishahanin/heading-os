---
paths:
  - "docs/QUICKSTART.md"
  - "docs/DEPLOYMENT.md"
  - "docs/INTEGRATIONS-SETUP.md"
  - "docs/MODELS-SETUP.md"
  - "docs/CONFIGURATION.md"
  - "docs/TROUBLESHOOTING.md"
  - "docs/EMERGENCY-PROCEDURES.md"
  - "docs/MAKE-IT-YOURS.md"
  - "docs/PLUGINS.md"
  - "docs/HOOKS-REFERENCE.md"
  - "docs/DOCS-PIPELINE.md"
  - "docs/GLOSSARY.md"
  - ".claude/skills/**/SKILL.md"
---

# Documentation Style — ASD-STE100 Subset

Last Updated: 2026-08-11
Last Verified: 2026-08-11

Path-scoped rule. Governs the pages a reader executes and the instruction bodies skills execute. Those pages are read by people whose first language is not English, on a bad day, while something is broken.

## What this is, honestly

A subset of **Part 1 (Writing Rules)** of ASD-STE100 Simplified Technical English, the controlled-language specification maintained by ASD and its STEMG working group for aerospace maintenance documentation. It is NOT conformance and must not be described as such: **Part 2, the Dictionary, is not adopted.** Its roughly 900 approved words were chosen for aircraft maintenance and hold no `repository`, no `daemon`, no `idempotent`; adopting it would mean declaring most of this engine's vocabulary as Technical Names, which is compliance in name only. Noun-cluster limits and the approved-part-of-speech constraint are out of scope for the same reason. What is adopted is the part that survives the move from aircraft to software.

## The rules

1. **Imperative in procedures.** `Run uv sync`, not `The dependencies should then be installed`.
2. **Active voice.** Passive only where the actor is genuinely unknown or irrelevant.
3. **Twenty words per procedural sentence, twenty-five per descriptive sentence.** Over the limit, split it.
4. **One action per numbered step.** Two actions joined by `and then` are two steps.
5. **Keep the articles and the linking verbs.** Telegraphic compression (`Open file, set value, restart`) is banned; it reads as a cable, not an instruction.
6. **One term, one meaning, corpus-wide.** Pick `engine clone` or `engine repo` and use only that one. A synonym introduced for variety is a defect here, not style.
7. **No `-ing` openers** on a sentence or a step, unless the word is part of a technical name.
8. **The warning comes before the step it guards** — never after it, never as the procedure's last line. The one rule with a physical cost when broken.
9. **No `and/or`.** Write `A`, `B`, or both.
10. **No minimising or padding words:** `simply`, `just`, `easily`, `obviously`, `in order to`, `prior to`, `in the event that`. The reader for whom the step is not simple is the reader who needs the step.

## Where it applies

The twelve pages in this file's `paths:` frontmatter, plus the instruction bodies of `.claude/skills/**/SKILL.md`. By convention but deliberately not path-scoped, so the rule is not loaded on every script edit: CLI `--help` text, argparse descriptions, and operator-facing error strings in `scripts/`.

It does NOT apply to explanatory documentation, where the reasoning is the value and flattening it destroys the point: `ARCHITECTURE.md`, `THREAT-MODEL.md`, `SECURITY-MODEL.md`, `CANOPUS.md`, `DESIGN-CHECK.md`, `RELEASE-NOTES.md`, `memory-lifecycle.md`, `engine-data-segregation-contract.md`, README positioning prose, every file in `.claude/rules/` including this one, and commit messages. It NEVER applies to outbound prose of any kind — email, LinkedIn, Tribe messages, corporate documents — or to any non-English text.

## How this composes with the prose rules

`humanization.md` and `voice.md` govern prose written to be read by a person forming a judgement; this rule governs text written to be executed. The two never both apply to one file: inside this scope this rule wins, everywhere else humanisation wins and this rule is silent. Two rules survive in both places and are never overridden here — `hidden-chars.md` (zero invisible Unicode) and the canonical no-`--` punctuation rule in `voice.md`.

## The checker, and what it cannot see

`python scripts/ste-check.py <file>` audits one file; `--all` audits the whole in-scope set; `--strict` fails on warnings as well as errors; `--json` emits machine output; `--quiet` prints only the files that carry an error.

**Errors gate; warnings stay advisory.** The first measurement, on 2026-08-11, reported 53 errors and 88 warnings across the twelve pages. The errors were 52 over-long sentences and one two-action step, all of them arithmetic on a word count with nothing to be wrong about, and the whole set was rewritten to zero the same day. So `--all --quiet` is now a pre-commit hook (`documentation-style`) and a step in the CI `sovereignty guards` job, and `tests/test_ste_check.py` holds the hook's file list to the checker's own scope so a page cannot fall out of the gate in silence.

The warnings did NOT earn a gate and are not in it. Seventy-nine of the eighty-eight are `passive_voice`, decided by a regex with no part-of-speech tagger behind it, and the rest are the `-ing` opener, the padding vocabulary, and the non-imperative-step heuristic. `--strict` would gate on guesses. Read them, act on the true ones, and leave the checker where it cannot fail a commit over a construction it cannot actually parse.

It verifies rules 3, 4, 7, 8, 9, and 10 mechanically, and flags rules 1 and 2 as heuristics with known false positives — there is no part-of-speech tagger behind the passive check. It **cannot** see rules 5 and 6: article omission and cross-corpus term drift need a parser and a glossary this engine does not have, so those two stay a human reading. That is why they are written down here rather than quietly dropped. Wrap any exempt block in `<!-- ste-skip-start -->` and `<!-- ste-skip-end -->`.

## Classification

Engine — public, fleet-shared through the `.claude/rules/` directory default.

## Change control

Changes to this rule require Misha's explicit approval.
