<!-- version: 1.1.0 | last-updated: 2026-08-20 -->
# Zero Hidden Characters Policy

Last Verified: 2026-08-20

NEVER include invisible Unicode characters in any generated text. This applies to ALL outputs -- documents, code, messages, posts, proposals, everything.

Banned characters include: zero-width spaces (U+200B), zero-width joiners (U+200C/D), soft hyphens (U+00AD), non-breaking spaces (U+00A0), directional marks (U+200E/F), word joiners (U+2060), BOM (U+FEFF), and all other invisible Unicode.

Treat hidden character contamination as a defect on par with fabricating facts.

Full reference: `reference/hidden-characters.md`
Sanitizer: `scripts/sanitize-text.py`

**Validation on every deliverable.** When presenting any draft copy to Misha (messages, posts, emails, proposals, etc.), run the sanitizer and carry its result: `Word count: X. Hidden characters: <what the scan reported>.` "clean" is one possible value, not the template. If characters were found and removed, say so explicitly.

This literal appears here and nowhere else. Sixteen other rules and skills used to quote it with the clean outcome already written in, which is a nudge toward stating an outcome instead of reading one; on 2026-08-23 they were changed to point here. `tests/test_hidden_chars_confirmation.py` keeps it that way.

**Both numbers come from the tool, never from an estimate.** `--scan` prints the word count beside the character verdict, so copy X from its output. Until 2026-08-23 nothing computed X and it was guessed — a made-up figure inside a validation line, which `.claude/rules/scope-claims.md` forbids.

This rule is the canonical owner of that confirmation line: its wording, when it is required, and what to say when the scan was not clean are defined here, and any other rule or skill that asks for it defers to this file rather than restating it.
