---
paths:
  - "scripts/**"
  - ".claude/hooks/**"
---

# A Tool Says Only What Its Method Established

Last Updated: 2026-08-31
Last Verified: 2026-08-31

Path-scoped rule. Loads when work touches a script or a hook. Governs the
sentence a tool prints about its own coverage.

## The defect

On 2026-08-12 two tools of this workspace misled their operator within hours of
each other, in the same way and neither through a logic bug.

`scripts/harness-audit.py` walked the plugin cache and printed what it found
under the words "running in this session". The cache keeps every version it ever
fetched and the loader reads one, so a superseded `superpowers` 6.1.1 was
reported as a live SessionStart hook beside the 6.2.0 that actually runs. The
method enumerated a directory; the sentence asserted a session.

`scripts/turn-check.py` called `git diff` "the edits made in this turn", and the
Stop hook blocked a turn over a deliberately-red TDD test a PARALLEL session had
written a minute earlier. `git` reports that a file changed and never who
changed it. The method read a working tree; the sentence asserted an author.

Both sentences were written by someone who believed them, both read as obviously
true, and both survived review for exactly that reason. The cost is worse than a
wrong number: a measurement that over-claims is trusted, acted on, and quoted
back later as established fact.

## The rule

**State the coverage your method establishes, and no more.** Before printing a
sentence about what a tool checked, saw, or attributes, name what would make it
false. If nothing in the code answers that, either resolve it or narrow the
sentence.

Three obligations, in order of preference:

1. **Resolve the claim.** Authorship comes from
   `scripts/utils/session_scope.py`, which reads the session's own transcript.
   Live-versus-installed comes from the loader's record
   (`installed_plugins.json`), never from a directory walk. A shared resolver is
   preferred over a local reimplementation, because the second copy is the one
   that stops being fixed.
2. **Name what you left out.** A narrowed check that prints like a complete one
   is the same defect wearing a different hat, so report the drop count:
   `turn-check` says "3 changed file(s) written by another session, not
   checked". Silence about an exclusion reads as coverage.
3. **Fail toward over-reporting, never toward silence.** When the evidence is
   unavailable -- an unreadable transcript, an unparseable plugin record --
   widen back to everything and say the state is unknown. `files_written()`
   returns None rather than an empty set for exactly this: a caller that reads
   "I could not tell" as "nothing was touched" checks nothing and reports a
   clean pass. Hiding a hook that executes is worse than listing one that does
   not.

## Enforcement

`tests/test_scope_claims.py` scans every user-facing string literal under
`scripts/` and `.claude/hooks/` (docstrings excluded, because they explain
rather than assert) for phrases that assert session membership or live
execution. Each MATCH is classified once, on its own:

- `DECLARED_CLAIMANTS` names the identifier that resolves that claim. The test
  then requires that identifier to still be a name the file's code binds, calls
  or reads, asked of the AST. A mention in a comment or a docstring is not
  evidence, and neither is a longer name that merely starts with it.
- `NON_SCOPE_CLAIMS` states why that string is not a coverage claim, in more
  than a fragment.
- `INHERITED_UNREVIEWED` is the honest parking space for a claim whose resolver
  nobody has established. It is currently empty, and when it is not, the failure
  message prints every entry so its real size is visible rather than inferred.

The key is the claim, never the file holding it: `(path, sha256 of the claim
text with whitespace collapsed and case folded)`. Three consequences, all
intended. A new sentence in a file that already declared one fails until its
author answers "what establishes this?". A re-wrap, a re-indent, or a change of
quote style leaves the classification alone. A REWORD retires it, because
different words are a different claim. A registered key with no live claim under
it fails too, so the registry cannot accumulate entries guarding nothing.

Until 2026-08-31 the registries were keyed by PATH while this section claimed
the per-match behaviour above, which is this rule over-claiming its own
coverage. Measured before the fix: a fresh "running in this session" literal
appended to `scripts/harness-audit.py` left the suite green at 19 passed, and
the same literal in an unregistered file failed. Every claim after a file's
first one inherited a classification in silence, and 28 of the tree's 43 claims
had never been looked at one at a time. A whole-file entry in `NON_SCOPE_CLAIMS`
was the sharper edge: `scripts/fireside-bot.py` was exempt over one string in
which "session" means a fireside meeting.

The detector is deliberately wide and its false positives are a feature: a
defect of this shape is written in whatever words the author reached for, not in
a fixed phrase. Floors on the walk pin it against decay, since a phrase list
that matches nothing passes everything. The floors are PER SEARCHED TREE as well
as over the union: measured 2026-09-01, `scripts/` holds 28 claims in 9 files and
`.claude/hooks/` holds 16 in 7, so the hooks tree dropping out of the walk
entirely cleared the union floors by two claims and one file, and would have
cleared them outright once two more `scripts/` claims landed. A floor over the
union is satisfied while one source contributes zero.

**What the gate does not establish**, stated here rather than dropped. It checks
that a resolver is named and still bound; it cannot check that the named
resolver actually resolves THAT sentence, which stays the author's judgement and
is why every registry entry carries prose. It sees only the phrases on its list,
so a claim worded outside them is invisible. And it reads literals from the AST,
so a sentence assembled at runtime from two strings arrives as two claims.
