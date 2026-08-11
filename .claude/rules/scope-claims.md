---
paths:
  - "scripts/**"
  - ".claude/hooks/**"
---

# A Tool Says Only What Its Method Established

Last Updated: 2026-08-12
Last Verified: 2026-08-12

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
`scripts/` and `.claude/hooks/` for phrases that assert session membership or
live execution, and requires each match to be classified exactly once: either it
names the identifier that resolves it (`DECLARED_CLAIMANTS`) or it states why it
is not a coverage claim (`NON_SCOPE_CLAIMS`). A new claim fails the suite until
its author answers "what establishes this?".

The detector is deliberately wide and its false positives are a feature: a
defect of this shape is written in whatever words the author reached for, not in
a fixed phrase. The registry also pins the detector against decay, since a
phrase list that matches nothing passes everything.

## Classification

Engine (public, fleet-shared via the `.claude/rules/` default).

## Change control

Changes to this rule require Misha's explicit approval.
