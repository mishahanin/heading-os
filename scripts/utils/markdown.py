#!/usr/bin/env python3
"""
Shared markdown parsing utilities for 31C workspace scripts.

Library module (snake_case per workspace naming convention) - importable
by any script that needs to parse YAML frontmatter or simple key:value
config blocks from markdown files.

Public surface:

- ``parse_frontmatter(text)`` -> ``(Dict, str)``
    Split ``---\\n{yaml}\\n---\\n{body}`` and return
    ``(parsed_yaml_dict, body_text)``. Uses ``yaml.safe_load`` when PyYAML
    is available (handles inline lists, block lists, quoted strings,
    booleans, numbers). Falls back to a regex parser otherwise. Returns
    ``({}, text)`` if no frontmatter is present.

- ``parse_frontmatter_str(text)`` -> ``(Dict[str, str], str)``
    String-coerced variant for legacy callers (crm-health.py, aggregate-crm.py,
    skill-metadata-check.py loose variant). All values become strings.

- ``split_frontmatter(text)`` -> ``(Optional[str], str, str)``
    The fences only: ``(yaml_block, body, kind)``, no YAML parsed. For callers
    with their own YAML policy.

- ``split_frontmatter_raw(text)`` -> ``(Optional[str], str, str)``
    The same split, byte-preserving: ``front + rest == text``. For a caller that
    rewrites one half and emits the file again.

- ``parse_frontmatter_strict(text)`` -> ``(Optional[Dict], str, str)``
    ``(data, kind, detail)``, keeping the REASON a document failed. The two
    variants above collapse every failure into ``({}, text)``, which is why
    three callers kept private copies until 2026-08-28.

- ``frontmatter_date(value)`` -> ``datetime.date``
    One date from a frontmatter value of any shape (``date``, ``datetime``,
    ``str``). Raises ``ValueError`` on anything unreadable. Two health engines
    had private copies of this and disagreed; see the function's docstring for
    the measurement.

- ``parse_config(text, key)`` -> ``Optional[str]``
    Extract a single ``key: value`` pair from a ``## Config:`` (or similarly
    named) markdown block. No existing callers in the workspace use this
    today - it is provided for future scripts that adopt the convention.
    Returns ``None`` if not found.

Extracted in Phase 6.2 of the 2026-05-12 workspace performance tune-up.
Phase 6.2 mop-up (2026-05-12) migrated ``odin-brain-health.py`` and
``marp_render.py`` to thin wrappers around the shared util.

``scripts/skill-metadata-check.py``, ``scripts/generate-skill-router.py`` and
``scripts/artifact-evaluator.py`` were listed here until 2026-08-28, each
because it needed the failure REASON that ``parse_frontmatter`` discards. All
three are wrappers now: ``parse_frontmatter_strict`` and ``split_frontmatter``
return the classification and each caller keeps its own wording. Their copies
had drifted apart in the meantime, and two of the three cut the block at a
``---`` inside a scalar while the third did not, so two CI gates reading the
same SKILL.md corpus disagreed about the same file.

Intentionally NOT migrated (each script's local ``parse_frontmatter`` carries a
comment block at the call site explaining why):

- ``scripts/merge-contacts.py`` - paired with a naive ``serialize_frontmatter``
  that round-trips through ``f"{key}: {value}"``. Switching to ``yaml.safe_load``
  would surface native ``datetime.date``/``int``/``bool`` types that the
  serializer cannot stringify safely - the parser and serializer must migrate
  together or the merged CRM file would corrupt.

- ``scripts/promote-knowledge.py`` - returns the raw YAML block as a string
  (not a parsed dict) so ``inject_frontmatter_fields`` can do line-level edits
  that preserve the author's original quoting, comments, ordering, and
  whitespace byte-for-byte. The "promote without rewriting" contract is
  incompatible with round-tripping through PyYAML.
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _regex_parse_yaml(raw_yaml: str) -> Dict[str, Any]:
    """Minimal regex YAML parser used when PyYAML is not available.

    Handles ``key: value``, simple inline lists ``[a, b]``, booleans,
    and quoted strings. Does not handle block lists or nested mappings -
    callers that need those should ensure PyYAML is installed.
    """
    data: Dict[str, Any] = {}
    for line in raw_yaml.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value: Any = line[colon_idx + 1:].strip()
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        elif value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        data[key] = value
    return data


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown document.

    Returns ``(metadata_dict, body)``. Returns ``({}, text)`` if the
    document has no frontmatter.

    Uses ``yaml.safe_load`` when PyYAML is installed (preserves native
    types: lists, ints, bools, nested mappings). Falls back to a regex
    parser otherwise.
    """
    if not text:
        return {}, text

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_yaml = match.group(1)
    body = text[match.end():]

    if HAS_YAML:
        try:
            data = yaml.safe_load(raw_yaml)
            if isinstance(data, dict):
                return data, body
            return {}, body
        except yaml.YAMLError:
            pass  # Fall through to regex parser

    return _regex_parse_yaml(raw_yaml), body


# A fence LINE: three dashes alone on their own line. Not the three characters
# wherever they land.
_FENCE_LINE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)

# Classification returned by the two functions below. The parser owns the
# CLASSIFICATION; each caller owns the WORDING, because the wording is that
# caller's user-facing output and three callers word it differently today.
FM_OK = ""
FM_NO_OPENING = "no-opening-fence"
FM_NO_CLOSING = "no-closing-fence"
FM_INVALID_YAML = "invalid-yaml"
FM_EMPTY = "empty"
FM_NOT_MAPPING = "not-a-mapping"


def split_frontmatter(text: str) -> Tuple[Optional[str], str, str]:
    """Find the frontmatter fences. Returns ``(yaml_block, body, kind)``.

    ``yaml_block`` is None when there is no usable block, and ``kind`` is then
    ``FM_NO_OPENING`` or ``FM_NO_CLOSING``. No YAML is parsed here: callers that
    need their own YAML policy (a PyYAML-optional fallback, a custom error
    string) take this and stop.

    Splitting is the part that was wrong in three places, all of them looking
    for the CHARACTERS rather than the LINE.

    MEASURED 2026-08-28 on
    ``description: drift --- check`` inside an otherwise ordinary SKILL.md:
    ``skill-metadata-check.py`` (`text.split("---", 2)`) and
    ``artifact-evaluator.py`` (`re.match(r"^---\\r?\\n(.*?)\\r?\\n---")`) both cut
    the block at the embedded dashes and returned a TRUNCATED mapping, while
    ``generate-skill-router.py``, ``utils.markdown.parse_frontmatter``,
    ``marp_render.py``, ``inbox_pulse.rules`` and three more read it whole. The
    same defect was fixed in ``scripts/dev/extract-router-rows.py`` on
    2026-08-24 and in ``generate-skill-router.py`` on 2026-08-20; these were the
    copies it never reached.

    The block KEEPS the newline before the closing fence, so a folded scalar
    ending the block keeps its trailing "\\n". ``parse_frontmatter`` above drops
    it (its regex puts the newline outside group 1), which is the measured
    difference on 2 of the 94 SKILL.md files (canopus, census) and the reason
    the two CI gates would not migrate to it.

    The offset is computed from the first line, not assumed to be 4 characters.
    MEASURED: ``generate-skill-router.py`` used ``text[4:]``, so an opening
    fence written ``---\\t\\t`` left a tab at the start of the block and PyYAML
    refused it with "found character '\\t' that cannot start any token" on a
    file whose YAML was perfectly good.
    """
    if not text:
        return None, text, FM_NO_OPENING
    first, sep, rest = text.partition("\n")
    if not sep or not _FENCE_LINE.match(first):
        return None, text, FM_NO_OPENING
    closing = _FENCE_LINE.search(rest)
    if closing is None:
        return None, text, FM_NO_CLOSING
    return rest[:closing.start()], rest[closing.end():].lstrip("\r\n"), FM_OK


def split_frontmatter_raw(text: str) -> Tuple[Optional[str], str, str]:
    """The same split, byte-preserving: ``(front, rest, kind)`` with
    ``front + rest == text`` whenever ``kind`` is ``FM_OK``.

    ``split_frontmatter`` above drops the fence lines and strips the newlines
    that follow the closing one, which is right for a caller that wants the YAML
    and the prose. It is wrong for a caller that must REWRITE one half and emit
    the file again: the dropped bytes would be silently normalised, so a fence
    written ``---\\t`` or a body opening on a blank line would come back changed.

    ``front`` is the opening fence line, the block, and the closing fence line,
    each with its own line terminator. ``rest`` is everything after, verbatim,
    including any leading blank lines. When there is no usable block, ``front``
    is None and ``rest`` is *text* unchanged, so a caller can treat the whole
    file as body without a second read.

    Written for ``scripts/dev/build-plugins.py``, which applies a YAML-escaped
    substitution to the frontmatter and a plain one to the body, then
    concatenates. Its own regex ``\\A(---\\r?\\n.*?\\r?\\n---\\r?\\n)(.*)\\Z`` did
    not accept a fence with trailing whitespace, so on such a file the whole
    document took the BODY substitution: the plain form closes the
    ``allowed-tools`` double-quoted scalar early and the bundle's frontmatter
    stops being YAML. That is the defect ``_REWRITE_SUB_YAML`` exists to prevent,
    reintroduced through the splitter instead of the substitution.
    """
    if not text:
        return None, text, FM_NO_OPENING
    first, sep, rest = text.partition("\n")
    if not sep or not _FENCE_LINE.match(first):
        return None, text, FM_NO_OPENING
    closing = _FENCE_LINE.search(rest)
    if closing is None:
        return None, text, FM_NO_CLOSING
    # The closing fence line ends where its own terminator ends, not where the
    # match ends: `_FENCE_LINE` is anchored with `$`, so it stops BEFORE the
    # newline. Consuming that newline here is what keeps `front + rest == text`
    # without handing the body a leading blank line that was never in the file.
    after = closing.end()
    if rest.startswith("\r\n", after):
        after += 2
    elif rest.startswith("\n", after):
        after += 1
    cut = len(first) + len(sep) + after
    return text[:cut], text[cut:], FM_OK


def set_frontmatter_field(text: str, key: str, value: str) -> str:
    """Set ``key: value`` inside the FRONTMATTER BLOCK, and nowhere else.

    Returns *text* unchanged when the document has no usable block, so a caller
    that wants to create one keeps that policy at its own call site: the two
    callers today disagree about it, and hiding the disagreement in here would
    give one of them the other's behaviour.

    Everything outside the block is byte-for-byte identical, including the
    fences, the line endings and any blank line opening the body. The reading
    side already learned this lesson twice (see :func:`split_frontmatter` and
    :func:`split_frontmatter_raw`); the WRITING side had not.

    THE DEFECT THIS ENDS. Three functions in this workspace rewrite a field in
    frontmatter and each spelled the scope itself. MEASURED 2026-08-29:

      `crm_autolog.bump_last_touch_in_text` ran `^last_touch:` MULTILINE over the
        WHOLE DOCUMENT before deciding to insert. Given a card with no
        `last_touch` in frontmatter and any body line starting `last_touch:` --
        a quoted email in the interaction log will do -- it rewrote the BODY
        line, returned, and left frontmatter without the field. The write
        succeeded, the audit log recorded `matched: true`, and `calculate_health`
        went on reading the contact as never touched. Its docstring said the
        insert "lands inside the frontmatter block or nowhere".
      `transfer-contact.update_owner_in_frontmatter` scoped the edit correctly
        and spelled its own fences, `^---\\s*\\n(.*?)\\n---\\s*\\n`. The trailing
        `\\n` is required, so a card whose file ENDS at the closing fence matched
        nothing and took the "no frontmatter" branch: it PREPENDED a second
        block, and the card's real fields became body text.
      `crm-health.frontmatter_end` had both defects, was fixed on its own, and
        wrote the reason down. The fix never reached the other two.

    The value is substituted through a callable, so a backslash or a ``\\1`` in
    it cannot be read as a regex group reference.
    """
    front, rest, kind = split_frontmatter_raw(text)
    if kind != FM_OK or front is None:
        return text
    open_line, sep, after_open = front.partition("\n")
    closing = _FENCE_LINE.search(after_open)
    if closing is None:          # unreachable while kind is FM_OK; not assumed
        return text
    block, tail = after_open[:closing.start()], after_open[closing.start():]

    field = re.compile(rf"^{re.escape(key)}[ \t]*:.*$", re.MULTILINE)
    if field.search(block):
        block = field.sub(lambda _m: f"{key}: {value}", block, count=1)
    else:
        # `block` carries the newline before the closing fence, so an append
        # needs no separator of its own. An EMPTY block (`---\n---\n`) is the
        # one case that does not, and it needs none either.
        newline = "\r\n" if block.endswith("\r\n") else "\n"
        block = f"{block}{key}: {value}{newline}"
    return open_line + sep + block + tail + rest


def parse_frontmatter_strict(text: str) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Frontmatter with the failure REASON kept: ``(data, kind, detail)``.

    The diagnostic counterpart to :func:`parse_frontmatter`, which collapses
    every failure mode into ``({}, text)``. Two CI gates kept private copies for
    exactly that reason, and the copies then drifted apart, so the gates
    disagreed about the same file. MEASURED 2026-08-28 on a SKILL.md whose
    description contained ` --- `: ``generate-skill-router.py`` read the whole
    mapping, while ``skill-metadata-check.py`` dropped every key after the
    dashes, reported three required fields as missing that were plainly in the
    file, and flipped that skill's triggers-corpus status from MISSING to
    EXEMPT, so the coverage gate stopped asking for a corpus it requires.

    ``data`` is None whenever ``kind`` is not ``FM_OK``. ``detail`` carries the
    PyYAML message or the offending type name, and is empty otherwise.

    Without PyYAML the block goes through the same regex fallback
    :func:`parse_frontmatter` uses, so ``FM_INVALID_YAML`` cannot be reported;
    a caller that needs its own fallback should use :func:`split_frontmatter`.
    """
    block, _body, kind = split_frontmatter(text)
    if block is None:
        return None, kind, ""
    if HAS_YAML:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            return None, FM_INVALID_YAML, str(exc)
    else:
        data = _regex_parse_yaml(block) or None
    if data is None:
        return None, FM_EMPTY, ""
    if not isinstance(data, dict):
        return None, FM_NOT_MAPPING, type(data).__name__
    return data, FM_OK, ""


def parse_frontmatter_str(text: str) -> Tuple[Dict[str, str], str]:
    """String-coerced variant of :func:`parse_frontmatter`.

    All values become strings (``None`` becomes ``""``). Used by callers
    that historically string-coerced everything (crm-health.py,
    aggregate-crm.py). New code should prefer :func:`parse_frontmatter`.
    """
    data, body = parse_frontmatter(text)
    coerced: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            coerced[k] = ""
        elif isinstance(v, (list, dict)):
            # Best-effort string form for compatibility; complex types are
            # uncommon in CRM-style frontmatter but should not crash.
            coerced[k] = str(v)
        else:
            coerced[k] = str(v)
    return coerced, body


def frontmatter_date(value: Any) -> _dt.date:
    """One ``datetime.date`` from a frontmatter date of any shape.

    ``parse_frontmatter`` returns NATIVE YAML types, so a ``created:`` field is
    a ``datetime.date`` when written bare, a ``datetime.datetime`` when it
    carries a time, and a ``str`` only when quoted. Every caller that ages a
    note therefore meets three types on one field.

    ``date.fromisoformat(str(value))`` is the shape that cannot read its own
    input: ``yaml.safe_load`` turns ``created: 2026-01-01 09:30:00`` into a
    ``datetime.datetime`` whose ``str()`` is ``"2026-01-01 09:30:00"``, which
    ``date.fromisoformat`` rejects on this repo's Python 3.11. MEASURED
    2026-08-28 against one fixture note per shape: ``odin-brain-health.py``
    aged both the bare date and the datetime, while ``knowledge-health.py``
    aged the bare date and silently dropped the datetime, over the same
    knowledge root and the same ``status: seed`` + ``created:`` rule. Two health
    engines, one corpus, two answers.

    A QUOTED datetime goes through ``datetime.fromisoformat``, because on Python
    3.11 ``date.fromisoformat`` accepts date forms only. Without that fallback
    the same instant was readable unquoted (YAML typed it) and unreadable quoted
    (``ValueError: Invalid isoformat string: '2026-01-02T09:30:00'``), which is
    the coercion failing to read its own domain. Found by a test written for the
    2026-08-28 consolidation, not by a report from the field.

    NOT ``str(value)[:10]``, the third spelling this repo carries (see
    ``scripts/utils/census_oracles._iso``). A blind ten-character slice reads
    ``"2026-01-02garbage"`` as 2026-01-02, so a mistyped field becomes a
    confident date. Trying the two ISO parsers in turn accepts exactly the ISO
    forms and rejects the rest.

    Raises ``ValueError`` and nothing else. ``str(value)`` guarantees a string
    argument, so ``TypeError`` is unreachable from here and a caller catching it
    is catching a case this function cannot produce.
    """
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return _dt.datetime.fromisoformat(text).date()


# The `## Config` heading, matched against ONE line at a time. It used to be one
# regex over the whole document ending at `(?:\n##|\Z)`, and that terminator
# needed a BLANK line before the next heading, because each block line had
# already consumed its own `\n`. MEASURED 2026-08-30:
#     parse_config("## Config\nother: x\n## Next\ncadence: 99\n", "cadence")
# returned "99" - a value read out of a DIFFERENT section, because the block
# could not end at the heading that immediately followed it and ran on to `\Z`.
# The docstring says the block ends at the next `##` heading; scanning lines
# says exactly that, with no terminator grammar to get wrong.
_CONFIG_HEADING_RE = re.compile(
    r"^##[ \t]*Config(?:uration)?[ \t]*:?[ \t]*$", re.IGNORECASE)


def _config_block_lines(text: str) -> Optional[List[str]]:
    """The lines of the first ``## Config`` block, or None when there is none."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not _CONFIG_HEADING_RE.match(line.rstrip()):
            continue
        block: List[str] = []
        for later in lines[i + 1:]:
            if later.startswith("##"):
                break
            block.append(later)
        return block
    return None


def frontmatter_list(value: Any) -> List[str]:
    """A frontmatter field that should be a list, as a list of strings.

    `yaml.safe_load` returns native types, so a key written with no value at
    all -- `keywords:` on its own line -- parses to None, and a key written as
    one bare word parses to a string. Six readers spelled the coercion
    themselves and five of them handled the string but not the None.

    MEASURED 2026-08-29: one note with a bare `keywords:` made
    `knowledge-health.py` die with `TypeError: 'NoneType' object is not
    iterable` in all three of its output modes, and both health-check callers
    (`scripts/memory.py`, `scripts/prime-health-parallel.py`) reported the
    check as failed. `REQUIRED_FIELDS` tests key PRESENCE, so the note counted
    as valid on the way in.

    Beside `frontmatter_date` for the same reason: the coercion belongs once,
    where every reader can find it, not once per reader.
    """
    if value is None or value == "":
        return []
    # The container test names its three types and does NOT say `Iterable`. A
    # string is iterable, so a widened test would shatter `keywords: alpha`
    # into `['a', 'l', 'p', 'h', 'a']`. A separate `isinstance(value, str)`
    # branch used to sit above this and mutation showed it changed no answer
    # (the tail below returns `[str(value)]`, which is the same list), so the
    # dead branch is gone and the explicit tuple of types is the guard.
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def parse_config(text: str, key: str) -> Optional[str]:
    """Extract a ``key: value`` from a ``## Config:`` markdown block.

    Convention: a section like::

        ## Config

        cadence: 14
        timezone: the configured timezone

    Returns the value as a string, or ``None`` if the block or the key
    is absent. The block ends at the next ``##`` heading or end of file.

    No existing workspace scripts use this convention today; this primitive
    is provided so future scripts can adopt a consistent pattern instead of
    inventing yet another parser.
    """
    if not text or not key:
        return None

    block = _config_block_lines(text)
    if block is None:
        return None

    for line in block:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        k = line[:colon_idx].strip()
        if k != key:
            continue
        value = line[colon_idx + 1:].strip()
        if value.startswith('"') and value.endswith('"') or value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        return value
    return None


# ============================================================
# Markdown tables
# ============================================================

HEADER_SCAN_LINES = 20


def _stderr_warn(message: str) -> None:
    print(f"parse_md_table: {message}", file=sys.stderr)


def split_table_row(line: str) -> List[str]:
    """Cells of one markdown table row, empty cells preserved positionally.

    The two dashboard generators each carried `[c for c in cells if c != ""]`,
    which DELETED an empty cell rather than keeping its position. A radar row
    `| Alice | | Smith | 14 | RED |` came back one cell short, so every value
    after the blank shifted one column left: Owner showed the company, health
    showed a number. Splitting on the pipes and dropping only the two outside
    the first and last pipe keeps the columns lined up.
    """
    cells = line.split("|")
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [c.strip() for c in cells]


def parse_md_table(text: str, header_pattern: Optional[str] = None, *,
                   source: str = "<text>",
                   warn: Optional[Callable[[str], None]] = None) -> List[Dict[str, str]]:
    """Rows of the first markdown table in `text`, as dicts keyed by header.

    `header_pattern` is a regex; the search for the table starts at the first
    line matching it. `source` names the file in warnings. `warn` overrides the
    stderr warning sink.

    Loud where the two script-local copies were silent. Both of those dropped
    any row with fewer cells than headers, so a pipeline deal with one empty
    Notes cell vanished from the deal count, the total value, the weighted
    value, the stage counts and the top-three, with nothing written anywhere.
    A short row is now padded and reported; the row survives.

    A blank line ends the table, and so does a line carrying no pipe at all
    (prose under the table). A line that DOES carry pipes but lost its leading
    one is read as a row and reported, never dropped. The old copies did
    `if not line: continue` inside the row loop, so two tables separated by one
    blank line merged and the second table's header row was parsed as data of
    the first.
    """
    emit = warn or _stderr_warn
    lines = text.split("\n")
    start = 0
    if header_pattern:
        for i, line in enumerate(lines):
            if re.search(header_pattern, line):
                start = i
                break
        else:
            return []

    headers: Optional[List[str]] = None
    data_start: Optional[int] = None
    for i in range(start, min(start + HEADER_SCAN_LINES, len(lines))):
        line = lines[i].strip()
        if "|" in line and "---" not in line and not headers:
            headers = split_table_row(line)
            continue
        if headers and "---" in line:
            data_start = i + 1
            break

    if not headers or data_start is None:
        if header_pattern:
            # Silence here read as "the table is empty". It also covers "the
            # table is more than HEADER_SCAN_LINES below its heading", which
            # renders as "No data available" and looks legitimate.
            emit(f"{source}: no table found within {HEADER_SCAN_LINES} lines of "
                 f"/{header_pattern}/")
        return []

    rows: List[Dict[str, str]] = []
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line:
            break
        if not line.startswith("|"):
            if "|" not in line:
                break  # prose after the table; the table really did end here
            # A row whose leading pipe is missing is what a hand edit or a
            # wrapped line produces, and `break` here dropped that row AND
            # every row after it with nothing written anywhere. MEASURED
            # 2026-08-30 on "| Deal | Value |\n|---|---|\n| A | 10 |\nB | 20 |\n
            # | C | 30 |": one row came back, B and C vanished silently. That is
            # the same silent-row-loss class the padding branch below was
            # written to end, one column over, so it is reported and the row
            # survives.
            emit(f"{source} line {i + 1}: row does not start with '|'; reading "
                 f"it as a row rather than ending the table. Row: {line}")
        cells = split_table_row(line)
        if len(cells) < len(headers):
            emit(f"{source} line {i + 1}: row has {len(cells)} cells, header has "
                 f"{len(headers)}; padding. Row: {line}")
        elif len(cells) > len(headers):
            emit(f"{source} line {i + 1}: row has {len(cells)} cells, header has "
                 f"{len(headers)}; extra cells dropped. Row: {line}")
        rows.append({h: cells[j] if j < len(cells) else ""
                     for j, h in enumerate(headers)})
    return rows
