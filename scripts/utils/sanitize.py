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
    inside unrelated words (e.g., "odin" not matching "decoding", "maxim" not
    matching "maximum").

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
        if not find or find not in result:
            continue
        if find.lower() in boundary_set:
            result = re.sub(r"\b" + re.escape(find) + r"\b", replace, result)
        else:
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
    lines = content.splitlines()

    for term in substring_terms:
        if not term:
            continue
        t_lower = term.lower()
        if t_lower not in content_lower:
            continue
        for match in re.finditer(re.escape(t_lower), content_lower):
            line_num = content_lower[: match.start()].count("\n") + 1
            key = (t_lower, line_num)
            if key in seen:
                continue
            seen.add(key)
            line_text = lines[line_num - 1].strip() if line_num <= len(lines) else ""
            findings.append((term, line_num, line_text[:200], "substring"))

    for term in (word_boundary_terms or set()):
        if not term:
            continue
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        for match in pattern.finditer(content):
            line_num = content[: match.start()].count("\n") + 1
            key = (term.lower(), line_num)
            if key in seen:
                continue
            seen.add(key)
            line_text = lines[line_num - 1].strip() if line_num <= len(lines) else ""
            findings.append((term, line_num, line_text[:200], "word-boundary"))

    return findings
