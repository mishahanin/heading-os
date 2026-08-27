#!/usr/bin/env python3
"""slugs.py - the one transliteration pass the filename builders share.

Three builders in this workspace clean a title down to `[a-z0-9-]` and use the
result as a filename stem: `scripts/render-doctype.py`, `scripts/marp_render.py`
and `scripts/utils/threads_lib.py`. A Cyrillic title has no character in that
set, so each of them produced the empty string and named a file with a hole in
it. Measured 2026-08-27: `slugify('Партнёрское предложение') == ''`, so two
letters to two Russian-named recipients on one day both rendered to
`2026-08-27_letter__.pdf` and the second overwrote the first.

This module is a PRE-PASS, not a replacement. Each builder keeps its own ASCII
rules and calls `transliterate()` first, so every slug that worked before is
byte-identical afterwards. Unifying the three rule sets would rename existing
outputs and existing thread ids, which is a different change with a different
risk, and it is not taken here.

No new dependency. A transliteration table is forty lines and a pinned package
is a supply-chain decision the operator owns.

Consumed by:
  - scripts/render-doctype.py (slugify)
  - scripts/marp_render.py (generate_slug)
  - scripts/utils/threads_lib.py (slugify)
"""
from __future__ import annotations

import hashlib
import unicodedata

# Cyrillic to Latin, lowercase keys only; the caller lowercases or the mapping
# below handles case by looking the lowered character up and restoring the case
# of a single-character result.
#
# The scheme is the readable BGN/PCGN-style one, not a reversible standard: a
# filename is read by a person, so "Щука" should look like "shchuka" and not
# like "ŝuka". `ъ` and `ь` map to nothing, which is what makes "объезд" read as
# "obezd".
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Ukrainian and Belarusian letters that are not in the Russian alphabet.
    # The operator's counterparties write in both.
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
}


def transliterate(text: str) -> str:
    """Cyrillic to Latin, everything else returned unchanged.

    Unchanged is the important half. ASCII passes through byte for byte, so a
    caller's existing slugs do not move, and a script with no entry here (Arabic,
    Hebrew, CJK) is left alone rather than mangled into a guess. Those callers
    reach for `stable_suffix` when their own cleaning leaves nothing.

    Case is preserved for a single-character result, because the callers all
    lowercase afterwards and a reader of an intermediate value should not see
    "Dogovor" turn into "dOGOVOR".
    """
    out = []
    for char in unicodedata.normalize("NFC", text):
        lowered = char.lower()
        if lowered not in _CYRILLIC:
            out.append(char)
            continue
        latin = _CYRILLIC[lowered]
        if latin and char != lowered:
            latin = latin[0].upper() + latin[1:]
        out.append(latin)
    return "".join(out)


def stable_suffix(text: str, length: int = 8) -> str:
    """A short, deterministic, filename-safe stand-in for an unslugifiable title.

    Used only when transliteration plus the caller's own cleaning leaves nothing
    at all: a title written entirely in a script this module does not cover, or
    one made of punctuation. Two different titles get two different suffixes, so
    the files stop overwriting each other; the same title gets the same suffix,
    so re-rendering a document does not scatter copies.

    It is deliberately NOT offered to `threads_lib`. A thread id is read by a
    person and `2026-08-27-a3f19c02` names nothing to them, so that caller keeps
    refusing the title instead.
    """
    digest = hashlib.sha256(unicodedata.normalize("NFC", text).encode("utf-8"))
    return digest.hexdigest()[:length]
