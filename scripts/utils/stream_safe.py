#!/usr/bin/env python3
"""Render a string so that writing it to a text stream cannot raise.

WHY THIS EXISTS. A POSIX path is bytes and need not be valid UTF-8, so every
reader in this workspace that takes a path from git decodes it with
``surrogateescape`` (``scripts/utils/repo_files.py``, ``scripts/push-all.py``,
``scripts/utils/git_push.py`` and a dozen more). That is correct: a directory
named ``b"re\\xffpo"`` arrives as the string ``"re\\udcffpo"`` and every
comparison downstream is exact.

Then something PRINTS it. ``print`` encodes to the stream, and a lone surrogate
cannot be encoded by any codec, so the diagnostic raises ``UnicodeEncodeError``
and kills the operation it was added to narrate.

MEASURED 2026-09-05, on ``main`` at 26d84ca, before this module existed::

    .venv/bin/python -m pytest --capture=sys \\
      "tests/test_a_push_wall_that_refused_the_root_it_was_given.py\\
::test_the_chokepoint_does_not_refuse_a_root_for_being_itself[not-utf8]" -q

      UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcff' in
      position 54: surrogates not allowed
      scripts/utils/git_push.py:443

    The push wall is documented as failing OPEN and LOUDLY. The loud part was a
    hard crash of the push.

WHY NOT THE OBVIOUS FIXES, each of which was considered and rejected:

* ``sys.stdout.reconfigure(errors="surrogateescape")`` — the shape
  ``scripts/datastore-log.py`` uses at ITS entry point. Wrong here for two
  reasons. ``git_push`` is a library six CLIs and several daemons import, and a
  library must not mutate the process's global streams. And the handler writes
  the raw undecodable byte back out, which is not text the operator can read,
  while still raising for ordinary Cyrillic on an ASCII stream.
* ``errors="replace"`` — silently drops the odd byte, so ``re\\xffpo`` prints as
  ``re?po``: a path that does not exist, offered to the operator as the path to
  go and look at. Losing the identifying value is the failure, not the fix.
* Removing the print, or the ``surrogateescape`` decode. Both are load-bearing;
  ``test_the_root_reader_does_not_run_in_subprocess_text_mode`` guards the
  second.

WHAT THIS DOES. ``safe_for_stream`` asks the destination stream which codec it
encodes with and escapes ONLY the characters that codec cannot take, using
``backslashreplace``. Everything else is returned byte-identical:

* ``"re\\udcffpo"`` on a UTF-8 stream  -> ``"re\\\\udcffpo"`` (visible, and the
  surrogate range U+DC80..U+DCFF is ``os.fsdecode``'s escape of a raw byte, so
  ``\\udcff`` names byte 0xff exactly);
* ``"\\u0440\\u0435\\u043f\\u043e"`` on a UTF-8 stream -> unchanged;
* the same Cyrillic on an ASCII stream (C locale, a redirected pipe, a Windows
  console) -> ``"\\u0440\\u0435\\u043f\\u043e"`` rather than a crash.

It cannot raise. The final escape is computed against ``ascii``, which
``backslashreplace`` satisfies for every code point including lone surrogates,
and an unusable ``encoding`` attribute (absent, empty, unknown, or a non-text
codec such as ``base64``) falls back to that same ASCII rendering rather than
propagating the ``LookupError``.
"""
from __future__ import annotations

import sys
from typing import Optional, TextIO

# The one codec every escape falls back to. `backslashreplace` against `ascii`
# is total: it has a rendering for every code point, surrogates included.
_FALLBACK = "ascii"

# `UnicodeEncodeError` is a `UnicodeError` is a `ValueError`. `LookupError` is
# what a codec name that is unknown, or known but not a TEXT codec, raises.
_CODEC_ERRORS = (LookupError, ValueError)


def _usable_encoding(stream) -> str:
    """The text codec ``stream`` encodes with, or a codec that always works.

    A stream with no ``encoding`` (``io.StringIO``, a mock) tells us nothing, so
    UTF-8 is assumed: it is this workspace's stream encoding everywhere, and the
    assumption still escapes the lone surrogate that is the actual defect while
    leaving ordinary non-ASCII alone.
    """
    enc = getattr(stream, "encoding", None)
    if not isinstance(enc, str) or not enc:
        return "utf-8"
    try:
        "x".encode(enc)
    except _CODEC_ERRORS:
        return _FALLBACK
    return enc


def _escaped(ch: str) -> str:
    """``ch`` as a backslash escape. Total, and cannot raise."""
    return ch.encode(_FALLBACK, "backslashreplace").decode(_FALLBACK)


def safe_for_stream(text: str, stream: Optional[TextIO] = None) -> str:
    """``text``, with anything ``stream`` cannot encode replaced by an escape.

    ``stream`` defaults to ``sys.stdout``, which is where the callers that
    matter print. Pass the real destination when it is something else.

    The fast path is a single whole-string encode, so the ordinary case (a
    message a stream can carry) returns the SAME string object and costs one
    encode. Only a message that would have raised is walked character by
    character, and only its unencodable characters change.
    """
    if stream is None:
        stream = sys.stdout
    enc = _usable_encoding(stream)
    try:
        text.encode(enc)
    except _CODEC_ERRORS:
        pass
    else:
        return text

    out = []
    for ch in text:
        try:
            ch.encode(enc)
        except _CODEC_ERRORS:
            out.append(_escaped(ch))
        else:
            out.append(ch)
    return "".join(out)
