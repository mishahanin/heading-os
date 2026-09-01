"""A tool may not tell the operator more than its method established.

Two defects of one shape surfaced on 2026-08-12, hours apart:

`scripts/harness-audit.py` printed every `hooks.json` under the plugin cache
beneath the words "running in this session". The cache keeps superseded
versions, so it named `superpowers` 6.1.1 and 6.2.0 as two live SessionStart
hooks when the loader reads one. The method walked a directory; the sentence
claimed a session.

`scripts/turn-check.py` called `git diff` "the edits made in this turn" and the
Stop hook blocked a turn over a parallel session's deliberately-red TDD test.
The method read a working tree; the sentence claimed an author.

Neither was a logic bug. Both were a sentence wider than its evidence, and both
survived review because the sentence read as obviously true. Prose cannot guard
that, so this file does: a user-facing string that asserts session membership or
live execution has to come from a file that resolves it, and a NEW such string
has to be declared here on purpose. The registry is the point. Adding a claim
means answering "what establishes this?" while writing it, rather than after an
operator is misled by it.

## Granularity, and the defect this file itself carried until 2026-08-31

Until 2026-08-31 the registries were keyed by FILE PATH. That is the same
over-claim the file exists to stop, aimed at itself: the prose promised "each
match is classified exactly once", and the code computed
`set(_claimants()) - classified`, which asks only whether the FILE was ever
classified. Measured on 2026-08-29's tree: appending a brand-new
`"...running in this session..."` literal to `scripts/harness-audit.py` or to
`scripts/fireside-bot.py` left the suite at 19 passed, exit 0. The same literal
in an unregistered file failed. So every claim after a file's first one was
waved through, and 28 of the tree's 43 claims had never been looked at
individually. `NON_SCOPE_CLAIMS` was the sharper edge: `scripts/fireside-bot.py`
was exempt over one string in which "session" means a fireside meeting, and the
exemption covered the whole file.

The registries are now keyed per claim.

## The key, and what rots it

A claim's key is `(path, fingerprint)`, where the fingerprint is the first 12
hex digits of the sha256 of the claim's NORMALISED text: whitespace collapsed to
single spaces, then case-folded. The alternatives were weighed and rejected:

- A line number rots on the first insertion above it, which is every edit.
- The verbatim claim text rots on a re-wrap, and three of these claims run past
  a thousand characters, so the registry would be unreadable.
- A substring excerpt cannot key `scripts/checkpoint-paths.py`, where the bare
  literal `"this session"` is a substring of fourteen of its neighbours.

Normalising before hashing is the re-wrap defence, and it is deliberate about
what survives. The key does NOT rot on: re-wrapping a long message, re-indenting
it, switching quote style, splitting one literal into implicitly-concatenated
pieces on adjacent lines, or changing case. The key DOES rot on: adding or
removing a word, editing punctuation, and renaming a `{format}` placeholder.
That direction is the point. A reworded claim is a new claim, and the author is
asked "what establishes this?" again rather than inheriting an answer given for
different words.

Two limits worth naming rather than hiding. A claim assembled at RUNTIME from
two literals is seen as two claims, because the walk reads the AST and not the
concatenation. And `head` beside each fingerprint is a human label only, not the
key; `test_a_registered_head_still_matches_its_claim` keeps it from drifting
into a lie, but it is never what the gate matches on.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Phrases that assert something a directory listing or a diff cannot show:
# that a thing is loaded RIGHT NOW, or that this session/turn is its author.
CLAIM_PHRASES = (
    "in this session",
    "in this turn",
    "this session",
    "running in",
)

SEARCHED = ("scripts", ".claude/hooks")

# Floors for the walk. A detector that quietly stops detecting looks exactly
# like a clean tree, and a per-claim registry makes that failure cheaper to hit:
# one broken `_claim_strings` and every entry below goes stale at once, which
# reads as "nothing claims anything any more". Set below the 2026-08-31 census
# (43 claims across 15 files) with room for ordinary deletions, and far above
# zero. Raise them when the tree grows; never lower them to make a run pass.
MIN_CLAIMS_FOUND = 30
MIN_FILES_FOUND = 10

# ...and PER SOURCE, because a floor over the union is satisfied while one of
# the two trees contributes nothing at all. Measured 2026-09-01: `scripts/`
# holds 28 claims in 9 files and `.claude/hooks/` holds 16 in 7, so
# `.claude/hooks/` dropping out of `SEARCHED` entirely left 28 claims in 9
# files against union floors of 30 and 10 - caught by a margin of two claims
# and one file, and not caught at all after two more `scripts/` claims land.
# The reference-case assertion below pins `scripts/` and nothing pinned the
# hooks tree. Floors here are per source, set well under each source's census.
MIN_PER_SOURCE = {"scripts": (18, 6), ".claude/hooks": (10, 4)}

# (path, fingerprint) -> (head, the identifier that must appear in the file,
# naming what resolves THIS claim). A claim with no resolver is the defect this
# file exists to stop, so the value is never allowed to be empty.
#
# Note what per-claim keying exposed the moment it was applied: six of these
# claims are backed by a DIFFERENT identifier than the file-level entry used to
# name. `.claude/hooks/checkpoint-offer.py` was declared under `session_slug`,
# but its three HERDR sentences are resolved by `resolve_pane`.
# `.claude/hooks/_dispatch.py` was declared under `actor_id`, but its two
# release-gate sentences are resolved by `_last_operator_prompt`.
# `.claude/hooks/checkpoint-precompact.py` was declared under `files_written`,
# which backs "Files this session wrote" and not the handoff pointer beside it.
# `scripts/compact-now.py` was declared under `resolve_pane`, which does not
# back its `--session` help text. Each of those was true of the file and false
# of the claim, which is the whole shape of the original defect.
DECLARED_CLAIMANTS: dict[str, dict[str, tuple[str, str]]] = {
    # Both walls in the dispatcher speak about "this session": the graph wall
    # says no codegraph query has run in it yet, and the fan-out wall says how
    # many files it has investigated by hand. Neither sentence was entitled to
    # the subject until 2026-08-29. A dispatched agent's payload carries the
    # DISPATCHING session's `session_id` and its `transcript_path`, so both
    # walls were reading a set of calls that was mostly not the session's:
    # measured, 36 hook calls in 25 seconds with 2 of them the session's own.
    # `actor_id` is what resolves it, by reading the `agent_id` field the main
    # session does not carry, and every piece of per-session state is keyed on
    # its answer.
    #
    # The release gate's two sentences are a different claim with a different
    # resolver. "the operator did not ask for a {action} in this turn" is a
    # claim about what the human typed, and `_last_operator_prompt` establishes
    # it by reading `type: "last-prompt"` records out of the transcript. The
    # second sentence is the fail-closed branch of the same read.
    ".claude/hooks/_dispatch.py": {
        "3cc5474f06f1": (  # pragma: allowlist secret
            "ask the graph first. this call reaches source code and no codegraph quer",
            "actor_id"),
        "99b8b7154d10": (  # pragma: allowlist secret
            "consider fanning out. this session has investigated",
            "actor_id"),
        "5aac44670b3e": (  # pragma: allowlist secret
            "in this turn. their most recent typed words, echoed from the session tra",
            "_last_operator_prompt"),
        "c7a2cc1b8ca4": (
            "is refused. this wall reads `last-prompt` from the session transcript to",
            "_last_operator_prompt"),
    },
    # The checkpoint system says "this session" a great deal, and until
    # 2026-08-16 it was not entitled to: one shared pointer and one shared state
    # file for the whole workspace meant the handoff it injected could belong to
    # a sibling session. Every path it reads is now keyed by the session id from
    # the hook payload (or CLAUDE_CODE_SESSION_ID for the model-driven skill), so
    # the sentence and the lookup finally describe the same session. The second
    # entry is the id-less branch, which widens rather than narrows: it says the
    # handoff MAY belong to a different session instead of implying it does not.
    ".claude/hooks/checkpoint-inject.py": {
        "de24ca4bc8d4": (
            "a handoff saved by this session (",
            "session_slug"),
        "acb0c1546d2d": (  # pragma: allowlist secret
            "'. this session reported no id, so that bucket holds every id-less sessi",
            "session_slug"),
    },
    # One `session_slug` claim and three `resolve_pane` claims, and the split
    # matters. The AUTO MODE prompt promises the model "this session's stamp and
    # paths"; this hook keys its own state by `session_slug`, and the command it
    # tells the model to run keys the paths the same way. The other three are
    # membership claims about a TERMINAL, resolved by `HA.resolve_pane` matching
    # the session id against `agent_session.value` from `herdr agent list`. The
    # distinction they preserve is that "not hosted" is a definite answer while
    # an unreachable HERDR is not one, and there is a separate sentence for each
    # rather than one sentence covering both.
    ".claude/hooks/checkpoint-offer.py": {
        "6ea81c1a029e": (  # pragma: allowlist secret
            "context is about {used:.0f}% used (~{remaining:.0f}% remaining), which c",
            "session_slug"),
        "6e846c78d146": (
            "this hook could not determine whether herdr is hosting this session, so",
            "resolve_pane"),
        "1d380d6c78ce": (  # pragma: allowlist secret
            "claude code has no internal way to compact on demand, so this hook does",
            "resolve_pane"),
        "eaa4c16fcff7": (
            "herdr is not hosting this session, so this hook cannot compact it - clau",
            "resolve_pane"),
    },
    # "Files this session wrote" is an authorship claim, and it is the one this
    # guard was written over: `git status` reports that a file changed and never
    # who changed it, so on a tree with two sessions open it would put a
    # sibling's edits into this session's compaction brief. The label is backed
    # by `files_written`, which reads this session's own transcript, and the hook
    # reports an empty set rather than narrowing anything by it. The handoff
    # pointer beside it is a different claim: it is a PATH, keyed by
    # `session_slug`, and `files_written` says nothing about it.
    ".claude/hooks/checkpoint-precompact.py": {
        "e6f49c2f87e0": (  # pragma: allowlist secret
            "files this session wrote",
            "files_written"),
        "2dc11449bcee": (
            "this session's handoff pointer",
            "session_slug"),
    },
    ".claude/hooks/turn-check.py": {
        "1193c66ab33d": (
            "`scripts/turn-check.py` failed on the uncommitted python edits in this t",
            "transcript_path"),
        "495f714c2234": (  # pragma: allowlist secret
            "`scripts/turn-check.py` did not finish the {lane} lane on the uncommitte",
            "transcript_path"),
    },
    # Fifteen claims, one resolver. Every one of them either prints the slug
    # from `CP.safe_slug(CP.session_id())` in the same sentence, or is argparse
    # help for a flag whose state file is keyed by it. The sixteenth claim in
    # this file is not a coverage claim and lives in NON_SCOPE_CLAIMS.
    "scripts/checkpoint-paths.py": {
        "028cdc72b35c": (  # pragma: allowlist secret
            "this session",
            "session_id"),
        "46e9502e8401": (  # pragma: allowlist secret
            "% for this session (",
            "session_id"),
        "2fc2153a809d": (  # pragma: allowlist secret
            "this session has not reported a usable context reading, so the value was",
            "session_id"),
        "dd56e95b8e34": (  # pragma: allowlist secret
            "print this session's checkpoint paths.",
            "session_id"),
        "f96090cffbfd": (
            "hands-off mode for this session only (overrides claude_handoff_auto)",
            "session_id"),
        "4c84abda1d2f": (  # pragma: allowlist secret
            "continue at a pause after a silent grace period, this session only (over",
            "session_id"),
        "5bad150a41cc": (
            "hard threshold where this session offers, and compacts when auto or unat",
            "session_id"),
        "7adf84056866": (  # pragma: allowlist secret
            "auto=on for this session (",
            "session_id"),
        "d43220436cf1": (  # pragma: allowlist secret
            "auto=off for this session (",
            "session_id"),
        "fbf56fe0021a": (  # pragma: allowlist secret
            "unattended=on for this session (",
            "session_id"),
        "3fe5996e7687": (
            "unattended=off for this session (",
            "session_id"),
        "65a50c04f31f": (  # pragma: allowlist secret
            "note: unattended is off for this session (",
            "session_id"),
        "ef54f6d3ebfc": (  # pragma: allowlist secret
            "this session has not reported its context usage yet",
            "session_id"),
        "7683b2d9b5dc": (  # pragma: allowlist secret
            "compact-at cleared for this session (",
            "session_id"),
        "017d19cb0c55": (
            "checkpoint-paths: refused. this session read",
            "session_id"),
    },
    # "does not host this session" is a membership claim about a terminal, and it
    # decides what the operator is told. The lookup behind it is `resolve_pane`.
    # The `--session` help text is a different claim with a different resolver:
    # it describes a DEFAULT, and `CP.session_id()` is what supplies it.
    "scripts/compact-now.py": {
        "49572abec60d": (  # pragma: allowlist secret
            "submit /compact to this session's own terminal via herdr.",
            "resolve_pane"),
        "54165e1fddde": (
            "session id to act on; defaults to this session, then to the newest trans",
            "session_id"),
        "96c20db8f3fb": (  # pragma: allowlist secret
            "nothing was submitted; the native auto-compact remains the only path for",
            "resolve_pane"),
    },
    # The reference case, and the complement sentence added with the fix. Both
    # are computed from the loader's own record via `active_install_paths`, not
    # from the directory walk that produced the 2026-08-12 over-count.
    "scripts/harness-audit.py": {
        "dc08fda1b72a": (  # pragma: allowlist secret
            "running in this session and not owned by this repository",
            "active_install_paths"),
        "be77b593ed5a": (
            "further hook(s) are on the installed surface but not in this session - e",
            "active_install_paths"),
    },
    "scripts/turn-check.py": {
        "91be02008c31": (  # pragma: allowlist secret
            "session transcript; narrows the check to files this session wrote. omitt",
            "session_scope"),
        "ecac1c4155c9": (
            "no uncommitted python edits by this session (",
            "session_scope"),
    },
    # The failure message `resolve_pane` raises when the record it matched
    # carries a pane_id no caller can use. "matching this session" is a
    # membership claim about one entry in `herdr agent list`, and it is entitled
    # to the subject only inside the branch guarded by
    # `session.get("value") == session_id`, which is the comparison that
    # established the match one line above the raise. `session_id` is therefore
    # the resolver, and it is the caller-supplied id rather than anything this
    # module infers about itself.
    #
    # `resolve_pane` would have been the wrong answer here even though it is the
    # right one in scripts/compact-now.py: there the claim is made by a CALLER
    # about the result of the lookup, here it is made inside the lookup, and
    # naming the enclosing function would say only that the sentence is where it
    # is.
    "scripts/utils/herdr_agent.py": {
        "79bf204f150e": (  # pragma: allowlist secret
            "the agent record matching this session carries pane_id",
            "session_id"),
    },
}

# The detector is deliberately wide, because a defect of this shape is written
# in whatever words the author reached for, not in a fixed phrase. Width costs
# false positives, and a false positive left unclassified rots the guard into
# noise people learn to override. So every match is classified exactly once:
# either it makes a coverage claim and names its resolver above, or it says why
# it is not a coverage claim here. Both answers are cheap; neither is silence.
#
# An exemption covers ONE claim, never a file. `scripts/fireside-bot.py` is why:
# it was exempt over a single string in which "session" means a fireside
# meeting, and that exemption used to cover every future string in the file.
NON_SCOPE_CLAIMS: dict[str, dict[str, tuple[str, str]]] = {
    ".claude/hooks/checkpoint-save.py": {
        "50a04396b0c6": (  # pragma: allowlist secret
            "## objective resume the work this session was doing when it compacted. t",
            "'the work this session was doing' appears in a handoff the hook writes "
            "FOR the session whose transcript it was handed; the subject is its own "
            "caller"),
    },
    ".claude/hooks/recall-inject.py": {
        "0a50758805f8": (
            "recall answered nothing for this message, and that is an outage, not an",
            "'in this session' instructs the model about what NOT to conclude while "
            "the pinned embedder is down; it claims no coverage over a set of files, "
            "and the fact it reports - that this hook's own recall call returned "
            "nothing - the hook observed directly in its own subprocess result"),
    },
    "scripts/checkpoint-paths.py": {
        "8dc8fc1e2b94": (  # pragma: allowlist secret
            "hook also submits /compact to this session's terminal through herdr.",
            "this line describes what the --auto switch turns ON, and it is the one "
            "claim in this file that `session_id` does not back: whether HERDR hosts "
            "this session is resolved by `resolve_pane`, which this script never "
            "calls. It is not left over-claiming, because the very next line printed "
            "names the drop - 'Without HERDR hosting it, Claude Code's own "
            "auto-compact frees the context instead' - which is obligation 2 of the "
            "rule, stating the exclusion rather than letting silence read as "
            "coverage. Per-claim keying is what surfaced this; the file-level entry "
            "had it inheriting `session_id`"),
    },
    "scripts/fireside-bot.py": {
        "968a643a1134": (
            "hi {name}, you're the helmsman for the week starting {week_starting}. yo",
            "'one thing you're taking from this session' is the closing go-around of "
            "a fireside invitation; session there means the meeting, not a Claude "
            "Code session. Scoped to this one string as of 2026-08-31: the exemption "
            "used to cover the whole file, so any future claim written into "
            "fireside-bot.py inherited it silently"),
    },
    "scripts/router-accuracy-nightly.py": {
        "69d69547522e": (
            "sensitivity was declared for this session, which outranks any payload pr",
            "'declared for this session' reports the SENSITIVE_MODE flag, resolved by "
            "sensitivity_is_declared(); it describes a mode, not a set of files "
            "covered"),
    },
    "scripts/scrutinize-dispatch.py": {
        "e56027f6c208": (  # pragma: allowlist secret
            "--verdict is required for --family claude: the claude judge is this sess",
            "'the Claude judge IS this session' is the REASON --verdict is required "
            "for --family claude, printed when it is missing. It is an architectural "
            "statement about where that judge runs, not a claim about what this run "
            "checked: the dispatcher covers no set of files with it, and the sentence "
            "is true by construction because there is no Claude endpoint for this "
            "process to call"),
    },
    "scripts/utils/observability.py": {
        "94a3251e269f": (  # pragma: allowlist secret
            "langfuse observability is enabled but degraded (%s); traces will not be",
            "'will NOT be recorded this session' reports this process's own degraded "
            "Langfuse state, which the process observed directly"),
    },
}

# Claims that the file-keyed registry waved through and that nobody has ever
# looked at ONE AT A TIME. This bucket exists so that migrating to per-claim
# keys never has to be paid for in laundering: the honest move when a claim's
# resolver cannot be established is to name it here, in full, where the operator
# can count them, rather than to file it under `DECLARED_CLAIMANTS` with a
# borrowed identifier so the suite goes green.
#
# It is EMPTY, and that is a measurement rather than a default. The 2026-08-31
# migration walked all 43 claims and traced each to a call site; 28 of them had
# been covered only by inheritance, six turned out to name the wrong resolver
# (see the note on DECLARED_CLAIMANTS) and one turned out not to be a coverage
# claim at all (`scripts/checkpoint-paths.py`, the HERDR line). None was left
# unresolvable. An entry here carries the same shape as the others, with a note
# saying what could not be established.
INHERITED_UNREVIEWED: dict[str, dict[str, tuple[str, str]]] = {}

REGISTRIES = (
    ("DECLARED_CLAIMANTS", DECLARED_CLAIMANTS),
    ("NON_SCOPE_CLAIMS", NON_SCOPE_CLAIMS),
    ("INHERITED_UNREVIEWED", INHERITED_UNREVIEWED),
)


def _normalise(text: str) -> str:
    """Whitespace-collapsed, case-folded. See the module docstring: this is the
    re-wrap defence, and it is what the fingerprint is taken over."""
    return " ".join(text.split()).casefold()


def _fingerprint(normalised: str) -> str:
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


def _docstrings(tree: ast.AST) -> set[int]:
    """Ids of the string nodes that are docstrings, which explain rather than
    assert: this file's own module docstring quotes both defects verbatim."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            found.add(id(first.value))
    return found


def _bound_names(path: Path) -> set[str]:
    """Every name the CODE of one file actually binds, calls, or reads.

    The resolver check used to be `resolver in path.read_text()`, and a raw
    substring over the whole file is the same shape of over-claim this suite
    exists to stop: it matches a comment, a docstring, and the longer name a
    rename produced. Measured on 2026-08-31 - renaming `HA.resolve_pane(` to
    `HA.resolve_paneX(` in `.claude/hooks/checkpoint-offer.py` left all 57 tests
    green, because `resolve_pane` is a prefix of the new name AND appears twice
    in prose. Asking the AST instead means the answer comes from what the file
    executes.

    String constants count, and deliberately: `transcript_path` is read as
    `payload.get("transcript_path")`, a dict key rather than an identifier, and
    that IS the code reading the field. Docstrings do not count - they are the
    prose this function exists to stop trusting - and comments never reach the
    AST at all.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    skip = _docstrings(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):  # noqa: SIM114 - see below
            # Deliberately NOT merged with the FunctionDef branch above. The two
            # read different attributes (`node.name` vs `node.arg`), so `or`-ing
            # the isinstance checks would need a getattr dance that reads worse
            # than the repetition and hides which shape contributes which name.
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update(node.name.split("."))
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.update(node.module.split("."))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip):
            names.add(node.value)
    return names


def _claim_strings(path: Path) -> list[str]:
    """User-facing literals in one file that make a scope claim."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        low = node.value.lower()
        if any(phrase in low for phrase in CLAIM_PHRASES):
            out.append(node.value)
    return out


def _claimants() -> dict[str, dict[str, str]]:
    """path -> {fingerprint: normalised claim}.

    Two literals with identical normalised text collapse to one entry. They are
    the same sentence, so they have the same answer to "what establishes this?",
    and asking twice would only teach people to paste.
    """
    found: dict[str, dict[str, str]] = {}
    for tree_name in SEARCHED:
        base = ROOT / tree_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            claims = _claim_strings(path)
            if not claims:
                continue
            by_fp: dict[str, str] = {}
            for raw in claims:
                normalised = _normalise(raw)
                by_fp.setdefault(_fingerprint(normalised), normalised)
            found[path.relative_to(ROOT).as_posix()] = by_fp
    return found


def _classified() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for _, registry in REGISTRIES:
        for path, entries in registry.items():
            for fingerprint in entries:
                keys.add((path, fingerprint))
    return keys


def _registry_rows() -> list[tuple[str, str, str, str, str]]:
    """(registry name, path, fingerprint, head, value) for every entry."""
    rows = []
    for name, registry in REGISTRIES:
        for path, entries in sorted(registry.items()):
            for fingerprint, (head, value) in entries.items():
                rows.append((name, path, fingerprint, head, value))
    return rows


def unclassified(claims: dict[str, dict[str, str]],
                 classified: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """Claims present in the tree that no registry entry keys.

    A pure function of its two arguments on purpose: the negative cases below
    hand it synthetic input, so "a new claim in an already-registered file
    fails" is proved against the decision itself rather than against a mutation
    of the real tree.
    """
    return sorted((path, fp) for path, byfp in claims.items() for fp in byfp
                  if (path, fp) not in classified)


def stale(claims: dict[str, dict[str, str]],
          classified: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """Registry entries with no claim under them any more."""
    live = {(path, fp) for path, byfp in claims.items() for fp in byfp}
    return sorted(k for k in classified if k not in live)


def test_every_scope_claim_is_declared_with_what_resolves_it():
    """A new tool that says "running in this session" must say how it knows, and
    so must a new SENTENCE in a tool that already said it once.

    The fix when this fails is usually not to add a line here. It is to make the
    tool resolve the claim (`scripts/utils/session_scope.py` for authorship, the
    loader's own record for activation) and THEN declare it, or to reword the
    sentence down to what the method actually shows. Only a match that turns out
    not to be a coverage claim at all belongs in NON_SCOPE_CLAIMS, with the
    reason written out.
    """
    claims = _claimants()
    undeclared = [
        f'  "{path}": {{\n'
        f'      "{fingerprint}": ({claims[path][fingerprint][:72]!r},\n'
        f'          "<what establishes this?>"),\n'
        f'  }}   <- full claim: {claims[path][fingerprint]!r}'
        for path, fingerprint in unclassified(claims, _classified())
    ]
    assert not undeclared, (
        "these strings assert session membership or live execution in "
        "user-facing text without being classified. Classify each one on its "
        "own; a neighbour in the same file is not an answer:\n"
        + "\n".join(undeclared)
    )


def test_no_claim_is_classified_both_ways():
    """A claim cannot both back a resolver and disclaim making one; the overlap
    is how a real claimant hides behind an exemption written for a neighbour.

    File-level keying made this near-impossible to hit and near-useless when it
    did: two DIFFERENT claims in one file could legitimately land in different
    registries, so the old version of this test could only compare paths.
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for name, path, fingerprint, _head, _value in _registry_rows():
        seen.setdefault((path, fingerprint), []).append(name)
    both = sorted(k for k, names in seen.items() if len(names) > 1)
    assert not both, both


@pytest.mark.parametrize(
    "path,fingerprint,head,reason",
    sorted((p, f, h, v) for n, p, f, h, v in _registry_rows()
           if n == "NON_SCOPE_CLAIMS"),
    ids=lambda v: v if len(str(v)) < 40 else str(v)[:40],
)
def test_an_exemption_carries_a_real_reason(path, fingerprint, head, reason):
    """An exemption with a thin reason is an exemption nobody re-examines."""
    assert (ROOT / path).is_file(), f"{path} is exempted but is gone"
    assert len(reason) > 40, (
        f"{path}/{fingerprint} ({head!r}): the reason has to say why, not just "
        f"assert"
    )


@pytest.mark.parametrize(
    "path,fingerprint,head,resolver",
    sorted((p, f, h, v) for n, p, f, h, v in _registry_rows()
           if n == "DECLARED_CLAIMANTS"),
    ids=lambda v: v if len(str(v)) < 40 else str(v)[:40],
)
def test_a_declared_claimant_still_carries_its_resolver(path, fingerprint, head,
                                                        resolver):
    """The registry entry has to stay true. Deleting the narrowing while leaving
    the sentence in place is exactly how both defects were written."""
    assert resolver, f"{path}/{fingerprint}: a claim with no resolver is the defect"
    target = ROOT / path
    assert target.is_file(), f"{path} is declared as a claimant but is gone"
    assert resolver in _bound_names(target), (
        f"{path} still makes the claim {head!r}, but {resolver!r} is not a name "
        f"its code binds, calls or reads any more (a mention in a comment or a "
        f"docstring does not count), so the claim is unbacked again"
    )


def test_the_registry_has_no_stale_entries():
    """A claim that stopped being made should leave the registry, or the next
    reader trusts a guard over something it no longer guards.

    This is also what stops the migration to per-claim keys from being reversed
    by accretion: a reworded claim leaves its old fingerprint behind, and a dead
    fingerprint that nobody removes is a classification with nothing under it.
    """
    dead = stale(_claimants(), _classified())
    assert not dead, (
        "classified here but no claim in that file normalises to that "
        f"fingerprint any more: {dead}"
    )


def test_a_registered_head_still_matches_its_claim():
    """`head` is the human label beside an opaque fingerprint. If it is allowed
    to drift, the registry reads as a list of sentences nobody is checking."""
    claims = _claimants()
    drifted = []
    for name, path, fingerprint, head, _value in _registry_rows():
        normalised = claims.get(path, {}).get(fingerprint)
        if normalised is None:
            continue  # test_the_registry_has_no_stale_entries owns that failure
        if not normalised.startswith(head):
            drifted.append(f"{name} {path}/{fingerprint}: head {head!r} is not "
                           f"the start of {normalised[:90]!r}")
    assert not drifted, drifted


def test_every_head_is_stored_normalised():
    """A head with a newline or a capital in it can never be a prefix of a
    normalised claim, so it would silently be a label that matches nothing."""
    bad = [f"{name} {path}/{fingerprint}: {head!r}"
           for name, path, fingerprint, head, _v in _registry_rows()
           if _normalise(head) != head]
    assert not bad, bad


def test_the_inherited_bucket_is_listed_in_full():
    """The bucket for claims nobody reviewed individually is not allowed to be a
    quiet place. Every entry names a live claim and says what is unestablished,
    and the assertion message prints the whole bucket so its size is visible
    rather than inferred.
    """
    claims = _claimants()
    listing = []
    for path, entries in sorted(INHERITED_UNREVIEWED.items()):
        for fingerprint, (head, note) in entries.items():
            listing.append(f"{path}/{fingerprint}: {head!r} - {note}")
            assert fingerprint in claims.get(path, {}), (
                f"{path}/{fingerprint} is parked as unreviewed but no claim in "
                f"that file matches it"
            )
            assert len(note) > 40, (
                f"{path}/{fingerprint}: say what could not be established"
            )
    assert len(INHERITED_UNREVIEWED) == 0, (
        "claims covered by inheritance and never reviewed one at a time "
        f"({len(listing)}):\n" + "\n".join(listing)
    )


def test_the_detector_is_not_vacuous():
    """A phrase list that matches nothing passes everything.

    This is the failure the workspace has hit before: a guard whose detector
    quietly stopped detecting looks identical to a clean tree. Per-claim keying
    raises the stakes, because one broken walk retires every entry at once.
    """
    found = _claimants()
    total = sum(len(v) for v in found.values())
    assert len(found) >= MIN_FILES_FOUND, (
        f"the phrase scan found claims in only {len(found)} file(s), under the "
        f"floor of {MIN_FILES_FOUND}; the detector decayed rather than the code "
        f"improving"
    )
    assert total >= MIN_CLAIMS_FOUND, (
        f"the phrase scan found {total} distinct claim(s), under the floor of "
        f"{MIN_CLAIMS_FOUND}"
    )
    assert "dc08fda1b72a" in found.get("scripts/harness-audit.py", {}), (  # pragma: allowlist secret
        "the audit's own claim, 'running in this session and not owned by this "
        "repository', is the reference case; if it stopped matching, the "
        "detector decayed rather than the code improving"
    )
    assert len(_classified()) >= MIN_CLAIMS_FOUND, (
        "the registries hold fewer entries than the floor, so most of the tree's "
        "claims are no longer classified individually"
    )


@pytest.mark.parametrize("tree", sorted(MIN_PER_SOURCE))
def test_each_searched_tree_still_contributes(tree):
    """A floor over the union is satisfied while one source contributes zero.

    `SEARCHED` names two trees and they are not interchangeable: the hooks are
    where session membership is asserted most often, and the reference-case
    assertion above pins only `scripts/`. Measured 2026-09-01, dropping
    `.claude/hooks` from the walk left 28 claims in 9 files, two claims and one
    file under the union floors - a margin that disappears the moment two more
    `scripts/` claims are written. Each tree now carries its own floor, so the
    walk cannot go silent on one of them and be paid for by the other.
    """
    assert tree in SEARCHED, f"{tree} is floored here but is no longer searched"
    min_claims, min_files = MIN_PER_SOURCE[tree]
    found = {path: byfp for path, byfp in _claimants().items()
             if path == tree or path.startswith(tree + "/")}
    total = sum(len(v) for v in found.values())
    assert len(found) >= min_files, (
        f"{tree} contributed claims in only {len(found)} file(s), under its own "
        f"floor of {min_files}")
    assert total >= min_claims, (
        f"{tree} contributed {total} claim(s), under its own floor of "
        f"{min_claims}")


def test_every_searched_tree_carries_a_floor():
    """The table above must not fall behind `SEARCHED`. A third tree added to
    the walk with no floor beside it is the same blind spot in a new place."""
    assert set(MIN_PER_SOURCE) == set(SEARCHED), (
        f"SEARCHED is {SEARCHED} but MIN_PER_SOURCE floors "
        f"{sorted(MIN_PER_SOURCE)}")


# --------------------------------------------------------------------------
# Negative cases. The gate above is green on a clean tree, and a green gate
# proves nothing on its own: the version of this file that shipped until
# 2026-08-31 was also green while waving through every claim after a file's
# first one. Each test below drives the decision to REFUSE, and each has a
# positive twin so that "it refuses" is not just "it refuses everything".
# --------------------------------------------------------------------------

# A real registered path, so the synthetic cases below exercise the exact
# inheritance the old gate allowed rather than an invented one.
_ANCHOR_PATH = "scripts/harness-audit.py"
_ANCHOR_FP = "dc08fda1b72a"  # pragma: allowlist secret


def test_a_new_claim_in_an_already_registered_file_is_unclassified():
    """THE defect. A second claim in a file whose first claim is registered used
    to inherit that classification in silence; the author was never asked."""
    novel = _normalise("Audited every plugin running in this session, no exceptions")
    novel_fp = _fingerprint(novel)
    assert novel_fp != _ANCHOR_FP
    claims = {_ANCHOR_PATH: {_ANCHOR_FP: "already classified", novel_fp: novel}}

    assert unclassified(claims, _classified()) == [(_ANCHOR_PATH, novel_fp)]


def test_a_claim_that_is_registered_passes():
    """The positive twin. Without it, the test above is satisfied by a decision
    that refuses everything, which is a broken gate rather than a strict one."""
    claims = {_ANCHOR_PATH: {_ANCHOR_FP: "already classified"}}
    assert unclassified(claims, _classified()) == []


def test_a_claim_the_file_no_longer_makes_is_stale():
    """The registry may not accumulate dead entries. A reworded claim changes
    fingerprint, and the old key is then a classification over nothing."""
    retired = _fingerprint(_normalise("a sentence nobody prints any more"))
    classified = {(_ANCHOR_PATH, _ANCHOR_FP), (_ANCHOR_PATH, retired)}
    claims = {_ANCHOR_PATH: {_ANCHOR_FP: "still printed"}}

    assert stale(claims, classified) == [(_ANCHOR_PATH, retired)]


def test_a_live_claim_is_not_reported_stale():
    """Positive twin for the staleness check."""
    classified = {(_ANCHOR_PATH, _ANCHOR_FP)}
    claims = {_ANCHOR_PATH: {_ANCHOR_FP: "still printed"}}
    assert stale(claims, classified) == []


def test_a_claim_in_an_unregistered_file_is_unclassified():
    """The case the OLD gate did catch, kept so the migration cannot be read as
    having traded one blind spot for another."""
    text = _normalise("this is running in this session")
    claims = {"scripts/not-a-real-script.py": {_fingerprint(text): text}}
    assert unclassified(claims, _classified()) == [
        ("scripts/not-a-real-script.py", _fingerprint(text))
    ]


def test_the_detector_reads_a_new_literal_out_of_real_source(tmp_path):
    """End to end through the AST walk, not just the set arithmetic: a literal
    written into a file is seen, and a docstring saying the same words is not."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""A docstring that says running in this session and must be ignored."""\n'
        'MESSAGE = "audited every plugin\\n   running in this session"\n'
        'UNRELATED = "says nothing about coverage"\n',
        encoding="utf-8",
    )
    found = _claim_strings(probe)
    assert len(found) == 1, found
    # The newline and the run of spaces collapse, which is the re-wrap defence.
    assert _normalise(found[0]) == "audited every plugin running in this session"


def test_rewrapping_a_claim_keeps_its_key_and_rewording_it_does_not(tmp_path):
    """The key's stated rot profile, measured rather than asserted in prose."""
    original = "Files this session wrote, in full"
    rewrapped = "Files this session\n        wrote, in full"
    reworded = "Files this session touched, in full"

    assert _fingerprint(_normalise(original)) == _fingerprint(_normalise(rewrapped))
    assert _fingerprint(_normalise(original)) != _fingerprint(_normalise(reworded))
    # Case folding too, so a sentence-case pass does not retire a classification.
    assert (_fingerprint(_normalise(original))
            == _fingerprint(_normalise(original.upper())))


def test_the_resolver_check_does_not_accept_prose_or_a_longer_name(tmp_path):
    """Both directions on `_bound_names`, which replaced a raw substring search.

    A comment or a docstring naming the resolver is exactly the evidence this
    suite refuses to accept anywhere else, and a rename that leaves the old name
    as a PREFIX is how the substring version stayed green through M7.

    The docstrings below are the resolver names and NOTHING else, on purpose.
    `_bound_names` adds a string constant whole, so a docstring reading "backed
    by resolve_pane" could never have collided with the name and an assertion
    against it proved nothing. That straw man was caught by mutation M8 on
    2026-08-31: dropping the docstring exclusion left this test green.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""resolve_pane"""\n'
        '# resolve_pane is what establishes it\n'
        'import herdr_agent as HA\n'
        'def go(payload):\n'
        '    """session_scope"""\n'
        '    return HA.resolve_paneX(payload.get("transcript_path"))\n',
        encoding="utf-8",
    )
    names = _bound_names(probe)

    assert "resolve_pane" not in names, (
        "a module docstring naming the resolver, a comment naming it, and a "
        "rename that keeps it as a PREFIX are each prose or a different name"
    )
    assert "session_scope" not in names, "a function docstring is prose too"
    assert "resolve_paneX" in names, "the attribute the code calls is bound"
    assert "transcript_path" in names, "a dict key the code reads counts"


def test_the_resolver_check_accepts_every_shape_the_registry_relies_on(tmp_path):
    """Positive twin: the four ways these files name their resolver."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from scripts.utils.session_scope import files_written\n"
        "import checkpoint_paths as CP\n"
        "def active_install_paths(p):\n"
        "    return CP.session_slug(p), files_written(p)\n",
        encoding="utf-8",
    )
    names = _bound_names(probe)
    for expected in ("session_scope", "files_written", "session_slug",
                     "active_install_paths"):
        assert expected in names, expected
