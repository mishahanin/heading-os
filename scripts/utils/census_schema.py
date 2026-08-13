#!/usr/bin/env python3
"""The shape a traversal is allowed to hand back, and why the shape IS a control.

Three of `/census`'s four controls protect the child: no network, no secrets, a
read-only corpus. This module is the fourth, and it protects the PARENT. The
council pass of 2026-08-12 found the hole the other three leave open: the box
holds an agent that cannot phone home, but its RESULT travels to a parent that
has the network, the credentials and the tools. An injected instruction does not
need to execute inside the sandbox. It only needs to be quoted in the return and
read by the orchestrator afterwards.

The cure is cheap because the questions are cheap. A traversal answers "how
many", "which files", "which pairs" - counts, paths and pairs. If the return is
required to be exactly that, free prose has nowhere to sit:

    {"kind": "count",  "value": 13,              "sources": ["<scope>/a.md", ...]}
    {"kind": "paths",  "paths": [...],           "sources": [...]}
    {"kind": "pairs",  "pairs": [["a.md","b.md"]], "sources": [...]}

Free text is not forbidden outright - some questions genuinely need it - but it
is opt-in per run (`--free-text`) and it arrives labelled:

    {"kind": "text", "text": "...", "provenance": "untrusted", "sources": [...]}

`provenance: untrusted` is mandatory on that kind and is the only accepted
value. A traversal that could claim its own text is trusted would be deciding
the question the label exists to answer.

The validator returns the REASON a return was rejected, never a boolean. A
caller that only knows "invalid" prints "invalid" to the operator, who then has
to re-run the traversal to learn what was wrong with it.

Imported by both `scripts/census.py` and `scripts/census-bench.py` on purpose:
one definition, so the engine cannot emit a shape the scorer will not read.
"""
from __future__ import annotations

import json
from pathlib import Path

KINDS = ("count", "paths", "pairs", "text")
STRUCTURED_KINDS = ("count", "paths", "pairs")

UNTRUSTED = "untrusted"

# A source is a corpus-relative path. Anything longer is prose wearing a path's
# clothes, which is the smuggling route this schema exists to close.
MAX_SOURCE_LEN = 512
MAX_TEXT_LEN = 20000
MAX_PAIR_MEMBER_LEN = MAX_SOURCE_LEN

# Exactly the keys each kind may carry. An ALLOWLIST, not a blocklist.
#
# Until 2026-08-13 this module blocked one extra key, the literal `text`, and
# accepted every other. Measured on the shipped code: a `count` return carrying
# `{"note": <16.8 KB of prose>}` validated clean, fitted inside the 20,000-char
# return budget, and printed into the caller's context. The docstring above
# claims free prose "has nowhere to sit"; it had a spare room. The failure does
# not need an adversary either - a traversal adding a helpful `"detail"` field
# opens the same channel by accident.
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "count": frozenset({"kind", "value", "sources"}),
    "paths": frozenset({"kind", "paths", "sources"}),
    "pairs": frozenset({"kind", "pairs", "sources"}),
    "text": frozenset({"kind", "text", "provenance", "sources"}),
}


def validate(answer: object, *, free_text_allowed: bool) -> str | None:
    """Return the reason `answer` is not a valid return, or None if it is."""
    if not isinstance(answer, dict):
        return f"return must be a JSON object, got {type(answer).__name__}"

    kind = answer.get("kind")
    if kind not in KINDS:
        return f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}"

    extra = sorted(set(answer) - ALLOWED_KEYS[kind])
    if extra:
        return (f"kind {kind!r} carries key(s) {extra} that its shape does not "
                "define; the structured return is an allowlist, because any "
                "spare key is a channel for untrusted corpus text to reach the "
                "orchestrator")

    reason = _validate_sources(answer.get("sources"))
    if reason:
        return reason

    if kind == "count":
        value = answer.get("value")
        if isinstance(value, bool) or not isinstance(value, int):
            return f"kind 'count' needs an integer value, got {value!r}"
        if value < 0:
            return f"kind 'count' cannot be negative, got {value}"

    elif kind == "paths":
        paths = answer.get("paths")
        if not isinstance(paths, list):
            return "kind 'paths' needs a list of paths"
        for item in paths:
            if not isinstance(item, str):
                return f"path entries must be strings, got {type(item).__name__}"
            if len(item) > MAX_SOURCE_LEN:
                return (f"path entry longer than {MAX_SOURCE_LEN} characters; "
                        "free text cannot ride back inside a path list")

    elif kind == "pairs":
        pairs = answer.get("pairs")
        if not isinstance(pairs, list):
            return "kind 'pairs' needs a list of pairs"
        for item in pairs:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                return f"each pair must be a 2-element list, got {item!r}"
            if not all(isinstance(x, str) for x in item):
                return f"pair members must be strings, got {item!r}"
            for member in item:
                if len(member) > MAX_PAIR_MEMBER_LEN:
                    return (f"pair member longer than {MAX_PAIR_MEMBER_LEN} "
                            "characters; a pair names two things, it does not "
                            "carry prose")

    elif kind == "text":
        if not free_text_allowed:
            return ("kind 'text' returned without --free-text; the structured "
                    "return is the control that stops untrusted prose reaching "
                    "the orchestrator, so it is opt-in per run")
        text = answer.get("text")
        if not isinstance(text, str):
            return f"kind 'text' needs a string text field, got {type(text).__name__}"
        if len(text) > MAX_TEXT_LEN:
            return f"text longer than {MAX_TEXT_LEN} characters"
        provenance = answer.get("provenance")
        if provenance != UNTRUSTED:
            return (f"kind 'text' must carry provenance {UNTRUSTED!r}, got "
                    f"{provenance!r}; a traversal does not get to vouch for text "
                    "it read out of the corpus")

    # A structured kind must not smuggle a text field past its own shape.
    if kind in STRUCTURED_KINDS and "text" in answer:
        return (f"kind {kind!r} carries a 'text' field; free prose belongs to "
                "kind 'text' under --free-text, not beside a count")

    return None


def _validate_sources(sources: object) -> str | None:
    if not isinstance(sources, list):
        return "every return needs a 'sources' list naming the files it read"
    if not sources:
        return ("'sources' is empty: an answer with no cited file cannot be "
                "checked, and an uncheckable answer is the failure mode this "
                "primitive exists to remove")
    for item in sources:
        reason = _validate_source_path(item)
        if reason:
            return reason
    return None


def _validate_source_path(item: object) -> str | None:
    """A source must LOOK like a corpus-relative path, not merely be a string.

    Checking only for string-ness and a length cap let `/home/operator/.env`,
    `../../../etc/shadow`, and 511 characters of prose with a newline in the
    middle all pass as "the file this answer read". A citation the operator
    cannot open is not a citation, and a citation that names a path outside the
    corpus is a claim the sandbox never allowed the traversal to make.
    """
    if not isinstance(item, str):
        return f"source entries must be strings, got {type(item).__name__}"
    if len(item) > MAX_SOURCE_LEN:
        return (f"source entry longer than {MAX_SOURCE_LEN} characters; "
                "free text cannot ride back inside a source list")
    if not item.strip():
        return "an empty source entry cites nothing"
    if any(ch in item for ch in ("\n", "\r", "\0")):
        return f"source entry contains a line break or NUL: {item[:60]!r}"
    if item.startswith(("/", "\\")) or (len(item) > 1 and item[1] == ":"):
        return (f"source {item[:60]!r} is absolute; sources are corpus-relative, "
                "and an absolute path names something the sandbox never mounted")
    if ".." in Path(item).parts:
        return (f"source {item[:60]!r} escapes the corpus with '..'; a citation "
                "must resolve inside the corpus it claims to have read")
    return None


def size_of(answer: dict) -> int:
    """Characters this return will add to the caller's context.

    Used by the engine's return budget. Measured on the serialized form because
    that is what actually travels, not on the value the traversal had in mind.
    """
    return len(json.dumps(answer, ensure_ascii=False))
