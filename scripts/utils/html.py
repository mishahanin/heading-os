"""Shared HTML-to-plaintext utilities.

Used by scripts that process email/calendar bodies (sync-exchange, sentinel,
email-intelligence). Consolidates three previously-duplicated copies of the
same `_HTMLStripper` class and `strip_html` function.

If any new caller needs HTML-to-text conversion, import from here rather than
copying the logic.

Public API:
    strip_html(html_str) -> str
"""

import re
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    """Accumulate text from HTML while dropping tags."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(html_str) -> str:
    """Convert an HTML fragment to plain text.

    Strips `<style>`, `<script>`, and HTML comments before parsing so their
    contents don't appear in the output. Collapses runs of 3+ newlines to 2.
    Returns "" for empty/None input.
    """
    if not html_str:
        return ""
    raw = str(html_str)
    raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
