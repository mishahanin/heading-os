"""Shared sanitization primitives for workspace content.

Used by `scripts/sanitize-check.py` (the `/publish-corporate` pre-publish
critical-leak scanner). The AIOS-for-the-CEO export pipeline that previously
also imported these primitives now lives in a standalone OSS repo.

Public API:
    apply_sanitize_map(content, sanitize_map, word_boundary_terms=None) -> str
    apply_phone_scrubbing(content, phone_pattern, safe_phones) -> str
    scan_for_terms(content, substring_terms, word_boundary_terms=None) -> list[tuple]
"""

import re


def apply_sanitize_map(
    content: str,
    sanitize_map: list[tuple[str, str]],
    word_boundary_terms: set[str] | None = None,
) -> str:
    """Apply an ordered list of (find, replace) pairs to `content`.

    Terms listed in `word_boundary_terms` (case-insensitive) use \\b...\\b regex
    matching instead of plain str.replace, preventing them from matching substrings
    inside unrelated words: "odin" does not match "decoding", and the English
    word for a saying does not match "maximum".

    Args:
        content: The text to sanitize.
        sanitize_map: Ordered list of (find, replace) tuples. Longer strings first
            to avoid partial replacement (e.g., "John Smith" before "Smith").
        word_boundary_terms: Set of find-values (case-insensitive) that must match
            only at word boundaries.

    Returns:
        Sanitized content.
    """
    boundary_set = {t.lower() for t in (word_boundary_terms or set())}

    result = content
    for find, replace in sanitize_map:
        if not find:
            continue
        if find.lower() in boundary_set:
            # IGNORECASE, and no case-SENSITIVE pre-check in front of it.
            #
            # Both halves used to disagree with the docstring above and with
            # `scan_for_terms`, which has compiled its boundary pattern with
            # re.IGNORECASE the whole time. Measured 2026-08-30:
            # apply_sanitize_map("ODIN settled the ledger; odin approved.",
            # [("odin", "[redacted]")], {"odin"}) returned "ODIN settled the
            # ledger; [redacted] approved." and scan_for_terms then reported the
            # surviving "ODIN" as a leak - the sanitizer and the pre-publish
            # scanner disagreeing about what was sanitized. With only the
            # uppercase form present the `find not in result` pre-check skipped
            # the term outright and nothing was replaced at all.
            #
            # The replacement goes through a lambda because `re.sub` reads its
            # replacement as a TEMPLATE: r"R:\new" landed as "R:" + newline +
            # "ew" and r"\1x" raised `re.error: invalid group reference`, while
            # the plain-str.replace branch below inserted both literally. One
            # (find, replace) pair must not mean two things depending on which
            # branch it takes.
            pattern = re.compile(r"\b" + re.escape(find) + r"\b", re.IGNORECASE)
            result = pattern.sub(lambda _m, r=replace: r, result)
        elif find in result:
            result = result.replace(find, replace)
    return result


def apply_phone_scrubbing(
    content: str,
    phone_pattern: re.Pattern,
    safe_phones: list[str] | None = None,
) -> str:
    """Remove phone numbers matched by `phone_pattern` unless they are in `safe_phones`.

    Comparison ignores spaces, dashes, and parentheses so that "+1 555 010 0100",
    "+1-555-010-0100", and "+15550100100" are treated as the same number.

    Args:
        content: Text to scrub.
        phone_pattern: Compiled regex matching phone numbers.
        safe_phones: Phone numbers that should NOT be removed (e.g., public company lines).

    Returns:
        Content with non-safe phone numbers removed.
    """
    safe_normalized = {re.sub(r"[\s\-()]", "", p) for p in (safe_phones or [])}

    def replacer(match: re.Match) -> str:
        phone = match.group(0).strip()
        clean = re.sub(r"[\s\-()]", "", phone)
        if clean in safe_normalized:
            return match.group(0)
        return ""

    return phone_pattern.sub(replacer, content)


def _locate(lines: list[str], prefix: str) -> tuple[int, str]:
    """(1-based line number, that line's text) for a match sitting just past `prefix`.

    ONE notion of a line, shared by both halves of `scan_for_terms`. Until
    2026-08-29 the number was computed by counting "\\n" and then used to index
    into `content.splitlines()`, which are two different notions:
    `str.splitlines()` also breaks on \\r, \\x0b, \\x0c, \\x85, U+2028 and U+2029.
    On any content carrying one of those, the printed source line belonged to a
    different line than the number printed beside it - a correct verdict with
    wrong evidence under it, which sends the reader to the wrong place in the
    file. Callers pass `lines` from `content.split("\\n")`, so
    `len(lines) == content.count("\\n") + 1` and the index below is always in
    range; no bounds guard is needed, and one would be dead code.

    `prefix` may be a case-folded slice (the substring pass scans lowered text)
    while `lines` comes from the original. `str.lower()` can change a string's
    length but never adds or removes a "\\n", so the count is the same in both,
    and indexing the original list yields the line as the operator will see it.
    """
    line_num = prefix.count("\n") + 1
    return line_num, lines[line_num - 1].strip()


def scan_for_terms(
    content: str,
    substring_terms: set[str] | list[str],
    word_boundary_terms: set[str] | list[str] | None = None,
) -> list[tuple[str, int, str, str]]:
    """Scan content for banned terms. Returns list of findings.

    Two-tier matching:
    - substring_terms: plain `in` / finditer lookup, case-insensitive. Catches
      embedded forms like "janedoe" in URL slugs or camelCase.
    - word_boundary_terms: \\b...\\b regex, case-insensitive. For short/common
      terms where substring matching would false-positive.

    Args:
        content: Text to scan.
        substring_terms: Terms to find via substring match.
        word_boundary_terms: Terms to find via word-boundary match.

    Returns:
        List of (term, line_number, line_text, match_type) tuples. One entry per
        term+line pair (deduplicated).
    """
    findings: list[tuple[str, int, str, str]] = []
    seen: set[tuple[str, int]] = set()
    content_lower = content.lower()
    # split("\n"), never splitlines(): see _locate. The line number and the line
    # text must come from the same split or the evidence contradicts the number.
    lines = content.split("\n")

    for term in substring_terms:
        if not term:
            continue
        t_lower = term.lower()
        if t_lower not in content_lower:
            continue
        for match in re.finditer(re.escape(t_lower), content_lower):
            line_num, line_text = _locate(lines, content_lower[: match.start()])
            key = (t_lower, line_num)
            if key in seen:
                continue
            seen.add(key)
            findings.append((term, line_num, line_text[:200], "substring"))

    for term in (word_boundary_terms or set()):
        if not term:
            continue
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(content):
            line_num, line_text = _locate(lines, content[: match.start()])
            key = (term.lower(), line_num)
            if key in seen:
                continue
            seen.add(key)
            findings.append((term, line_num, line_text[:200], "word-boundary"))

    return findings
