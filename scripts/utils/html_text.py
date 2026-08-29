"""Shared HTML-to-plaintext utilities.

Used by scripts that process email/calendar bodies (sync-exchange, sentinel,
email-intelligence). Consolidates three previously-duplicated copies of the
same `_HTMLStripper` class and `strip_html` function.

If any new caller needs HTML-to-text conversion, import from here rather than
copying the logic.

Public API:
    strip_html(html_str) -> str
    email_body_text(item) -> str
"""

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.secret_patterns import redact  # noqa: E402


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


def email_body_text(item) -> str:
    """The plain-text body of one Exchange item, with credential spans removed.

    ONE extraction, because there were three, and they were the same three
    lines: prefer `text_body`, fall back to `strip_html(body)`, else empty.
    `sync-exchange`, `sentinel` (mail and invites) and `email-intelligence` each
    carried a copy, and each copy then wrote its result into the DATA overlay -
    markdown in one case, JSON in the other two. A fix applied to one of three
    is the defect shape this workspace keeps finding.

    The redaction is the reason the extraction was worth sharing. An archived
    email body is machine-generated text that lands in a git repository, and
    marketing mail routinely carries a signed image URL whose query value is a
    real JWT. MEASURED 2026-08-29 on the live overlay: a sign-in email's
    tracking image, `og-images.workos.com/api/logo-icon?t=<token>`, was written
    into `outputs/_sync/emails/inbox-latest.md` and `push-all.py` REFUSED the
    whole backup over it. The token is not ours and not a credential of this
    workspace, but the scanner cannot know that from the shape, and it is right
    not to guess.

    So the rule is not "strip image URLs" and not a second list of dangerous
    query parameters. It is `secret_patterns.redact`: exactly the vocabulary
    `secret-scanner.py` flags, span-merged, from the one table both read. An
    archived body therefore cannot carry a string this workspace calls a
    secret, and the guarantee holds for a REAL credential in a magic-link email
    as much as for a marketing pixel - the archive should not have been storing
    either. The URL survives with its host and path intact and the removed span
    named, so the record stays readable.

    No truncation here. The three callers use three different limits and three
    different markers, and unifying those was not asked for.
    """
    text = getattr(item, "text_body", None)
    if text and str(text).strip():
        return redact(str(text).strip())
    body = getattr(item, "body", None)
    if body and str(body).strip():
        return redact(strip_html(body))
    return ""
