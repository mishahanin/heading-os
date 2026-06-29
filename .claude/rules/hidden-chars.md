<!-- version: 1.0.0 | last-updated: 2026-04-28 -->
# Zero Hidden Characters Policy

Last Verified: 2026-05-15

NEVER include invisible Unicode characters in any generated text. This applies to ALL outputs -- documents, code, messages, posts, proposals, everything.

Banned characters include: zero-width spaces (U+200B), zero-width joiners (U+200C/D), soft hyphens (U+00AD), non-breaking spaces (U+00A0), directional marks (U+200E/F), word joiners (U+2060), BOM (U+FEFF), and all other invisible Unicode.

Treat hidden character contamination as a defect on par with fabricating facts.

Full reference: `reference/hidden-characters.md`
Sanitizer: `scripts/sanitize-text.py`

**Validation on every deliverable.** When presenting any draft copy to Misha (messages, posts, emails, proposals, etc.), run the sanitizer to validate and include confirmation: "Word count: X. Hidden characters: clean." If characters were found and removed, say so explicitly.
