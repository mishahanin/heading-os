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
  - "docs/EXTENDING.md"
  - "docs/TELEGRAM-AND-ALERTS.md"
  - ".claude/skills/**/SKILL.md"
---

# Documentation Style — ASD-STE100 Subset

Last Updated: 2026-08-22
Last Verified: 2026-08-22

Path-scoped rule. Governs the pages a reader executes and the instruction bodies skills execute. Those pages are read by people whose first language is not English, on a bad day, while something is broken.

## What this is, honestly

A subset of **Part 1 (Writing Rules)** of ASD-STE100 Simplified Technical English, the controlled-language specification maintained by ASD and its STEMG working group for aerospace maintenance documentation. It is NOT conformance and must not be described as such: **Part 2, the Dictionary, is not adopted.** Its roughly 900 approved words were chosen for aircraft maintenance and hold no `repository`, no `daemon`, no `idempotent`; adopting it would mean declaring most of this engine's vocabulary as Technical Names, which is compliance in name only. Noun-cluster limits and the approved-part-of-speech constraint are out of scope for the same reason. What is adopted is the part that survives the move from aircraft to software.

## The rules

1. **Imperative in procedures.** `Run uv sync`, not `The dependencies should then be installed`.
2. **Active voice.** Passive only where the actor is genuinely unknown or irrelevant.
3. **Twenty words per procedural sentence, twenty-five per descriptive sentence.** Over the limit, split it. **An inline code span counts as ONE word**, never zero and never the words inside it. Until 2026-08-22 the checker deleted spans before counting, which discounted each sentence in proportion to the code it carried — the densest sentences drew the largest pass. One QUICKSTART line read 21 words to the checker and 27 to a person, and reported clean. Adopting the one-word count cost 37 rewrites, 15 on the gated pages and 22 in the skill bodies. Counting the interior instead would score 32 on the pages, and it would penalise naming the exact flag or path, which is pressure in the wrong direction for reference documentation.
4. **One action per numbered step.** Two actions joined by `and then` are two steps.
5. **Keep the articles and the linking verbs.** Telegraphic compression (`Open file, set value, restart`) is banned; it reads as a cable, not an instruction.
6. **One term, one meaning, corpus-wide.** Pick `engine clone` or `engine repo` and use only that one. A synonym introduced for variety is a defect here, not style.
7. **No `-ing` openers** on a sentence or a step, unless the word is part of a technical name.
8. **The warning comes before the step it guards** — never after it, never as the procedure's last line. The one rule with a physical cost when broken.
9. **No `and/or`.** Write `A`, `B`, or both.
10. **No minimising or padding words:** `simply`, `just`, `easily`, `obviously`, `in order to`, `prior to`, `in the event that`. The reader for whom the step is not simple is the reader who needs the step.

## Where it applies

The fourteen pages in this file's `paths:` frontmatter, plus the instruction bodies of `.claude/skills/**/SKILL.md`. By convention but deliberately not path-scoped, so the rule is not loaded on every script edit: CLI `--help` text, argparse descriptions, and operator-facing error strings in `scripts/`.

It does NOT apply to explanatory documentation, where the reasoning is the value and flattening it destroys the point: `ARCHITECTURE.md`, `THREAT-MODEL.md`, `SECURITY-MODEL.md`, `CANOPUS.md`, `DESIGN-CHECK.md`, `RELEASE-NOTES.md`, `RULES-REFERENCE.md`, `memory-lifecycle.md`, `engine-data-segregation-contract.md`, README positioning prose, every file in `.claude/rules/` including this one, and commit messages. It NEVER applies to outbound prose of any kind — email, LinkedIn, Tribe messages, corporate documents — or to any non-English text.

**Every page under `docs/` sits in one of those two lists, and a test holds it there.** Until 2026-08-22 three did not: `EXTENDING.md`, `TELEGRAM-AND-ALERTS.md` and `RULES-REFERENCE.md` were absent from the frontmatter and absent from the exclusion sentence, so no one had ever decided about them. Two turned out to be pages a reader executes and joined the gate at a cost of 28 sentences; one is a catalogue and joined the exclusion list. The failure is worth naming because of its shape: a page fell through by being in NEITHER list, which no gate on the gated set can ever see. `test_every_docs_page_is_classified` reads this paragraph and the frontmatter together, so a new page under `docs/` fails the suite until someone decides which list it belongs to.

## How this composes with the prose rules

`humanization.md` and `voice.md` govern prose written to be read by a person forming a judgement; this rule governs text written to be executed. The two never both apply to one file: inside this scope this rule wins, everywhere else humanisation wins and this rule is silent. Two rules survive in both places and are never overridden here — `hidden-chars.md` (zero invisible Unicode) and the canonical no-`--` punctuation rule in `voice.md`.

## The checker, and what it cannot see

`python scripts/ste-check.py <file>` audits one file; `--all` audits the fourteen gated pages; `--skills` audits the skill instruction bodies; `--strict` fails on warnings as well as errors; `--json` emits machine output; `--quiet` prints only the files that carry an error.

**`--all` is narrower than this rule's scope, and the two flags exist to say so.** Until 2026-08-16 `--all` described itself as "every in-scope file" while resolving twelve pages out of a hundred and eight, so a clean `--all` read as a clean corpus. The first `--skills` measurement, the same day: **74 of 96 skills, 300 errors, 443 warnings.**

**A quarter of that first number was the checker, not the corpus.** Rewriting the skill bodies on 2026-08-16 and 2026-08-17 surfaced three defects in the sentence splitter, each of the same shape: a markdown character sat between a sentence's terminator and the next sentence's first letter, the pattern did not accept it, and two clean sentences measured as one long one. Emphasis (`**You decide.** No code ...`) cost 51 errors, the blockquote continuation marker cost 21, and a closing quote or bracket (`... both work." If two ...`) cost 11. Prose was being rewritten to satisfy a broken measurement in every one of those 83 cases. The lesson is in the fix: the closer is now a character CLASS, because enumerating shapes is what produced rounds two and three.

The real debt was 217 errors, the corpus reached **zero** on 2026-08-17, and `--skills` is a gate from that day — a `documentation-style-skills` pre-commit hook and a CI step, errors only, exactly like its `--all` sibling.

**There is no vendored exemption, and the reasoning matters more than the outcome.** The last error sat in `.claude/skills/ast-grep/SKILL.md`, vendored from `ast-grep/agent-skill` and pinned in `skills-lock.json`, which read at first as untouchable. It is not: the lock hashes the copy that SHIPS here, not upstream's bytes, the in-repo copies are already lightly adapted, and `--relock` is a first-class supported operation. So the sentence was split and the tree re-locked, and the lock's `note` field now tells a future re-vendor to re-apply the adaptation. An exemption would have been the wrong instrument anyway: it hides one file's debt permanently, and unmeasured-therefore-clean is the precise failure this gate exists to end.

Bring a skill down when you edit it, run `--skills --quiet` to see where the number stands, and do not read a green `--all` as a green corpus.

**Errors gate; warnings stay advisory.** The first measurement, on 2026-08-11, reported 53 errors and 88 warnings across the twelve pages. The errors were 52 over-long sentences and one two-action step, all of them arithmetic on a word count with nothing to be wrong about, and the whole set was rewritten to zero the same day. So `--all --quiet` is now a pre-commit hook (`documentation-style`) and a step in the CI `sovereignty guards` job, and `tests/test_ste_check.py` holds the hook's file list to the checker's own scope so a page cannot fall out of the gate in silence.

The warnings did NOT earn a gate and are not in it. Seventy-nine of the eighty-eight are `passive_voice`, decided by a regex with no part-of-speech tagger behind it, and the rest are the `-ing` opener, the padding vocabulary, and the non-imperative-step heuristic. `--strict` would gate on guesses. Read them, act on the true ones, and leave the checker where it cannot fail a commit over a construction it cannot actually parse.

It verifies rules 3, 4, 7, 8, 9, and 10 mechanically, and flags rules 1 and 2 as heuristics with known false positives — there is no part-of-speech tagger behind the passive check. It **cannot** see rules 5 and 6: article omission and cross-corpus term drift need a parser and a glossary this engine does not have, so those two stay a human reading. That is why they are written down here rather than quietly dropped. Wrap any exempt block in `<!-- ste-skip-start -->` and `<!-- ste-skip-end -->`.
