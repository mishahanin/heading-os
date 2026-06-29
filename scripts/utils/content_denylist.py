#!/usr/bin/env python3
"""Real-entity denylist for the engine CONTENT-leak gate.

The six structural segregation layers (leak-guard, engine_guard, the push wall,
the pre-commit/pre-push tests) all check WHERE a file routes -- never WHAT is
inside it. So a real name, handle, slug, e-mail, or Telegram ID embedded in a
file that legitimately routes ``engine`` sails through every one of them. The
2026-06-28 public-readiness audit found exactly this class of leak (real Tribe
handles + Telegram IDs in a test, a real exec slug across three tests, real
pricing in skill prose). This module is the missing CONTENT layer: it builds a
denylist of real entities and lets the gate scan engine-routed files for them.

Design constraints:

* The denylist IS PII. It is built in memory from the private DATA overlay at
  scan time and is NEVER persisted into the engine. On a public clone / CI where
  the overlay is absent, ``build_denylist`` returns an empty (degraded) list --
  the gate then no-ops rather than failing, because the only machine that both
  authors engine files and pushes them (the operator's) has the overlay present.

* High precision over high recall. A noisy gate that blocks legitimate pushes
  gets bypassed; a quiet, trustworthy one gets kept. Tokens are length-bounded,
  word-boundary matched, filtered against a stopword list, and exempted by the
  public-identity + fictional-example allowlist. Genuine edge cases are annotated
  inline with ``content-guard: ok <reason>`` (mirrors the ``leak-guard: ok``
  convention) rather than forcing ``--no-verify``.

Sources harvested from the DATA overlay:

* ``crm/contacts/*.md``       -- person slugs (filenames) + split name-words
* ``admin/executives.json``   -- exec slugs, names, github users, data-repo names
* ``config/*.json|*.yaml``    -- e-mails (regex), Telegram-ID-shaped ints, and
                                 fireside roster handles (member-dict keys)
* ``config/content-denylist.yaml`` -- CURATED non-person tokens (companies,
                                 events, codenames, competitors); CEO-maintained
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Public identity -- deliberately shareable, NEVER flagged.
ALLOW_IDENTITY = {
    "misha", "hanin", "misha hanin", "misha.hanin@odinix.com", "misha.hanin@31c.io",
    "31 concept", "31c", "odun.one", "odun", "trustone", "31c.io", "odinix.com",
    "heading os", "heading-os",
}

# Fictional / illustrative names that legitimately appear in rule, skill, and test
# scaffolding -- NEVER flagged. Keep in sync with the placeholders the docs use.
ALLOW_FICTIONAL = {
    "alice", "bob", "carol", "dave", "erin", "sara", "ahmed", "jane", "jane-doe",
    "jane doe", "john", "doe", "pat", "nolan", "pat nolan", "exampletelco",
    "examplecorp", "example", "acme", "globex", "rivex", "northgate", "okonkwo",
    "sara okonkwo", "someoutsider", "outsider", "randomperson",
}

# Common words that can surface as a CRM name-word (e.g. a surname) but are far
# more likely to be ordinary English in code/docs. Length>=5 already filters most;
# this catches the residue. Tuned empirically against the clean tree (--all).
STOPWORDS = {
    "about", "after", "agent", "alert", "always", "brief", "build", "check",
    "child", "class", "clean", "draft", "email", "event", "every", "field",
    "first", "great", "group", "guide", "hello", "media", "model", "north",
    "other", "owner", "phone", "place", "point", "press", "price", "queue",
    "radar", "reply", "round", "sales", "scope", "sheet", "south", "state",
    "store", "table", "thing", "title", "token", "track", "tribe", "under",
    "value", "voice", "world", "write",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_RE = re.compile(r"\b\d{7,}\b")
_MIN_WORD = 5  # minimum length for a bare name-word token


@dataclass
class Denylist:
    """Compiled real-entity denylist. ``token -> category`` for reporting."""

    tokens: dict[str, str] = field(default_factory=dict)
    _pattern: re.Pattern | None = None
    degraded: bool = False

    def _compile(self) -> None:
        if not self.tokens:
            self._pattern = None
            return
        # Longest-first so a full name wins over its component word in reporting.
        ordered = sorted(self.tokens, key=len, reverse=True)
        alts = "|".join(re.escape(t) for t in ordered)
        # Boundaries on alnum/underscore only, so a token next to '-', '.', '@',
        # quotes, or whitespace still matches (slugs/emails), but a token glued
        # inside a longer identifier does not.
        self._pattern = re.compile(
            rf"(?<![A-Za-z0-9_])(?:{alts})(?![A-Za-z0-9_])", re.IGNORECASE
        )

    def scan_text(self, text: str) -> list[tuple[int, str, str]]:
        """Return (lineno, matched_text, category) for every denylist hit.

        Lines carrying an inline ``content-guard: ok`` suppression are skipped.
        """
        if self._pattern is None:
            return []
        hits: list[tuple[int, str, str]] = []
        for n, line in enumerate(text.splitlines(), 1):
            if "content-guard: ok" in line:
                continue
            for m in self._pattern.finditer(line):
                cat = self.tokens.get(m.group(0).lower(), "entity")
                hits.append((n, m.group(0), cat))
        return hits


def _add(tokens: dict[str, str], value: str, category: str) -> None:
    """Add a normalized token unless it is allowed or too short/common."""
    if not value:
        return
    v = value.strip().lower()
    if not v or v in ALLOW_IDENTITY or v in ALLOW_FICTIONAL or v in STOPWORDS:
        return
    # bare single word: length + stopword gate (multi-word/email/slug exempt)
    if " " not in v and "@" not in v and "-" not in v and "." not in v:
        if len(v) < _MIN_WORD or not v.isalpha():
            return
    tokens[v] = category


def _harvest_person_slugs(data_root: Path, tokens: dict[str, str], strict: bool) -> None:
    contacts = data_root / "crm" / "contacts"
    if not contacts.is_dir():
        return
    for md in contacts.glob("*.md"):
        slug = md.stem  # e.g. "jane-doe"
        _add(tokens, slug, "crm-slug")
        if strict:  # bare name-words are noisy (collide with English) -> opt-in only
            for word in slug.split("-"):
                _add(tokens, word, "crm-name")


def _harvest_executives(data_root: Path, tokens: dict[str, str], strict: bool) -> None:
    p = data_root / "admin" / "executives.json"
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for ex in data.get("executives", []):
        for key in ("slug", "name", "github_user", "data_repo"):
            val = ex.get(key)
            if val:
                _add(tokens, str(val), "exec")
        if strict:
            for word in str(ex.get("name", "")).replace("-", " ").split():
                _add(tokens, word, "exec-name")


def _harvest_config(data_root: Path, tokens: dict[str, str], strict: bool) -> None:
    cfg = data_root / "config"
    if not cfg.is_dir():
        return
    # e-mails + Telegram-ID-shaped ints from the raw text of every data-config.
    for f in list(cfg.glob("*.json")) + list(cfg.glob("*.yaml")) + list(cfg.glob("*.yml")):
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for email in _EMAIL_RE.findall(raw):
            _add(tokens, email, "email")
        if "fireside" in f.name or "roster" in f.name:
            for num in _ID_RE.findall(raw):
                tokens[num] = "telegram-id"  # ids bypass _add length/alpha gate
    # fireside roster handles: member-dict keys (value is a dict with name/id).
    fs = cfg / "fireside-schedule.json"
    if fs.is_file():
        try:
            data = json.loads(fs.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        for handle, member in _iter_member_dicts(data):
            _add(tokens, handle, "handle")
            nm = member.get("name") if isinstance(member, dict) else None
            if nm:
                _add(tokens, str(nm), "handle-name")
                if strict:
                    for word in str(nm).replace("-", " ").split():
                        _add(tokens, word, "handle-name")


def _iter_member_dicts(data):
    """Yield (key, value) for dict entries whose value looks like a roster member."""
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(v, dict) and ("name" in v or "telegram_user_id" in v):
                    yield k, v
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


def _harvest_curated(data_root: Path, tokens: dict[str, str], curated_path: Path | None) -> None:
    """Load the CEO-maintained curated denylist (non-person entities)."""
    path = curated_path or (data_root / "config" / "content-denylist.yaml")
    if not path.is_file():
        return
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    for category in ("companies", "events", "codenames", "competitors", "tokens"):
        for val in (data.get(category) or []):
            if val:
                # curated tokens bypass the length/stopword gate (CEO chose them),
                # but still respect the allowlist.
                v = str(val).strip().lower()
                if v and v not in ALLOW_IDENTITY and v not in ALLOW_FICTIONAL:
                    tokens[v] = f"curated:{category}"


def build_denylist(data_root: Path | None, curated_path: Path | None = None,
                   strict: bool = False) -> Denylist:
    """Build the real-entity denylist from the private DATA overlay.

    Returns a degraded (empty) Denylist when the overlay is absent or unreadable,
    so the gate no-ops on a public clone instead of failing.

    strict=False (default, used by the hard push/commit gate): high-precision
    tokens only -- full slugs, full names, handles, e-mails, IDs, curated tokens.
    strict=True (opt-in deep audit): additionally harvests bare name-words split
    from person slugs/names. Those collide with ordinary English, so they are kept
    out of the default gate to preserve its trustworthiness.
    """
    dl = Denylist()
    if data_root is None or not Path(data_root).is_dir():
        dl.degraded = True
        dl._compile()
        return dl
    data_root = Path(data_root)
    try:
        _harvest_person_slugs(data_root, dl.tokens, strict)
        _harvest_executives(data_root, dl.tokens, strict)
        _harvest_config(data_root, dl.tokens, strict)
        _harvest_curated(data_root, dl.tokens, curated_path)
    except Exception:
        # Fail-open on a harvest error (the gate degrades, never wedges the push);
        # the structural layers still hold. Surfaced via degraded=True.
        dl.degraded = True
    dl._compile()
    return dl
