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


# Tags that end a line of text. Anything not listed is inline and its text runs
# on, which is what `<b>` and `<a>` should do.
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})


class _HTMLStripper(HTMLParser):
    """Accumulate text from HTML while dropping tags.

    A newline is emitted at every block boundary. Without it the parser simply
    concatenated the data between tags, so `<div>Hello</div><div>World</div>`
    became `HelloWorld` and `Line one<br>Line two` became `Line oneLine two`.
    Exchange bodies routinely carry no source newline between block tags, so the
    plaintext handed to sync-exchange, sentinel and email-intelligence was a
    run-on with FUSED words - and `strip_html`'s own promise to collapse runs of
    newlines presumed a line structure this parser never produced.
    """

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def _break(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._break()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._break()

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
