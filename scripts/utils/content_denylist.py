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

* ``crm/contacts/*.md``       -- person slugs (filenames) + split name-words,
                                 AND, from the frontmatter, the organisation
                                 field (``pipeline_company`` / ``company`` /
                                 ...) and every e-mail address
* ``admin/executives.json``   -- exec slugs, full names, github users, data-repo
                                 names, AND each bare given/family name. The
                                 bare form is in the DEFAULT gate: the roster is
                                 a handful of real colleagues, so it costs six
                                 tokens, unlike the CRM slug decomposition below
* ``config/*.json|*.yaml``    -- e-mails (regex), Telegram-ID-shaped ints, and
                                 fireside roster handles (member-dict keys)
* ``config/content-denylist.yaml`` -- CURATED non-person tokens (companies,
                                 events, codenames, competitors); CEO-maintained

Why the organisation field, and why not slug decomposition
----------------------------------------------------------
Until 2026-08-08 the only company coverage was the CURATED list, so an
organisation nobody had thought to curate was invisible: a contact slug pairs a
person with their employer (``<given>-<org>``), the whole slug was a token, but
the BARE organisation name matched nothing -- and the bare name is what prose
actually contains. Harvesting the structured organisation field closes that,
because it is the one place the name appears on its own.

The rejected alternative was decomposing every slug into its component words.
Measured on the live overlay it turns ordinary English into tokens (the pieces of
real slugs include ``heading``, ``security``, ``policy``, ``world``, ``traffic``)
and the gate would refuse nearly every push. A gate that cries wolf gets
disabled, which is worse than the hole it closes. Thread titles were considered
as a second source and rejected for the same reason: they are free prose, so
harvesting them reintroduces the ordinary-word problem the structured field
avoids.
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
    # Published company mailboxes: shared, printed on public collateral, no
    # person behind them. A CRM record may carry one, so name them here.
    "info@31c.io", "sales@31c.io", "support@31c.io",
    # Named public contributors, credited on purpose in CHANGELOG.md and
    # docs/PLUGINS.md. Each is also a CRM contact, so the display-name harvest
    # would read published attribution as a leak. Add a person here only when
    # their name is ALREADY deliberately public in this repo.
    #
    # This is the ONLY real person named anywhere in the engine, and it is a
    # deliberate exception the operator confirmed on 2026-08-24. The standing
    # rule that day: real names live in the DATA repo, the engine carries
    # invented ones for examples. A published contributor credit is the one
    # thing that cannot follow it, because removing someone's public thank-you
    # is an outward-facing act and they linked their own GitHub profile beside
    # it. Everything else in the tree was moved to Bond-universe placeholders
    # the same day. Do not add a second name here without asking.
    "mahmoud maatuq",  # content-guard: ok the allowlist entry naming itself
}

# Fictional / illustrative names that legitimately appear in rule, skill, and test
# scaffolding -- NEVER flagged. Keep in sync with the placeholders the docs use.
ALLOW_FICTIONAL = {
    "alice", "bob", "carol", "dave", "erin", "tamsin", "dana", "jane", "jane-doe",
    "jane doe", "john", "doe", "pat", "nolan", "pat nolan", "exampletelco",
    "examplecorp", "example", "acme", "globex", "rivex", "contoso", "okonkwo",
    "tamsin okonkwo", "someoutsider", "outsider", "randomperson",
    # 2026-08-08: the two placeholder personas carried first names that also
    # belong to real CRM contacts, so a reader could wrongly connect a fixture to
    # a person. Renamed suite-wide to Marlow / Tamsin, which match no contact.
    "marlow", "carter", "marlow carter", "rivera", "marlow rivera",
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
    # Added with the organisation harvest (2026-08-08). A widely-known public
    # technology that is also the head word of an organisation, so the bare word
    # appears in engine prose that has nothing to do with anybody. The
    # organisation's multi-word form stays a token, so this narrows the match
    # rather than removing it. Measured: this is the ONLY such collision in the
    # tree -- the placeholder set and the one-surviving-word rule handle the rest,
    # so nothing else needs listing here.
    "solana",
    # Added 2026-08-24, and the reason is the deep-audit mode's usability rather
    # than the gate's. Both words are pieces of real contact slugs, so ``--strict``
    # emitted them and then fired on ordinary prose: measured over the engine
    # surface, ``security`` produced 967 findings and ``likely`` 56, out of 1,052
    # in total. Ninety-two percent noise is why nobody ran the flag, and while
    # nobody ran it two real given names sat in tracked engine files. Silencing
    # the two words makes the remaining ~29 findings readable, which is what
    # turns a deep-audit mode from a thing that exists into a thing that is used.
    # Neither word can hide a person: the full slug and the space-separated name
    # stay tokens in the DEFAULT gate.
    "security", "likely",
}

# Frontmatter keys on a CRM contact that name an organisation.
_ORG_FIELDS = ("pipeline_company", "company", "organization", "organisation", "employer")

# Placeholder organisation values that name nobody. Rejected before tokenizing.
_ORG_PLACEHOLDERS = {
    "", "-", "n/a", "na", "none", "tbd", "unknown", "independent", "self",
    "self-employed", "freelance", "private", "individual", "retired",
}

# Legal-form and generic industry words. Stripped when deriving the "core" form
# of an organisation name, and never emitted as a bare head-word token.
_ORG_GENERIC = {
    "ab", "ag", "bv", "capital", "co", "communications", "company", "corp",
    "corporation", "cyber", "digital", "dmcc", "fzc", "fzco", "fze", "global",
    "gmbh", "group", "holding", "holdings", "inc", "international", "labs",
    "limited", "llc", "llp", "lp", "ltd", "media", "networks", "nv", "oy",
    "partners", "plc", "pty", "pvt", "sarl", "security", "services", "solutions",
    "spa", "srl", "systems", "technologies", "technology", "telecom", "telecoms",
    "ventures",
}

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.S)

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
        # The space-separated form is what prose contains. Emitting only the slug
        # is the 2026-08-23 hole: a live counterparty's name sat in a tracked
        # engine test and every layer read the tree as clean. A multi-word phrase
        # carries the same no-collision guarantee as the organisation phrase form
        # harvested below, so it is safe outside strict mode; a one-word slug is
        # not, and stays behind it.
        if "-" in slug:
            _add(tokens, slug.replace("-", " "), "crm-name")
        if strict:  # bare name-words are noisy (collide with English) -> opt-in only
            for word in slug.split("-"):
                _add(tokens, word, "crm-name")


def _org_token_forms(value: str) -> list[str]:
    """Return the denylist forms of one organisation name, most specific first.

    ``"Krellide Technologies"`` -> ``["krellide technologies", "krellide"]``;
    ``"Krellide (Somewhere)"`` -> ``["krellide"]``.

    A BARE word is emitted only when stripping legal forms and generic industry
    words leaves exactly one word AND that word opened the name. Everything else
    contributes its multi-word phrase only. That single rule is what keeps
    ordinary English out of the gate, and it is strict on purpose. Measured over
    the engine tree, the looser "first word unless generic" rule produced 762
    findings, every one of them noise: real organisations whose names open with
    an ordinary English word each donated that word as a token, and the gate then
    fired on routing tests, policy prose and market-data fixtures that had
    nothing to do with anybody. Under this rule such a name keeps only its phrase
    form and the tree is clean, while a one-word organisation, or a compound with
    a generic tail, still yields the bare name that prose actually contains --
    which is the whole gap this harvest closes.
    """
    forms: list[str] = []
    for alt in re.sub(r"\([^)]*\)", " ", value).replace('"', " ").split("/"):
        words = [w.strip(".,&'’“”").lower() for w in alt.split()]
        words = [w for w in words if w]
        if not words:
            continue
        core = [w for w in words if w not in _ORG_GENERIC]
        for phrase in (" ".join(words), " ".join(core)):
            if phrase and len(phrase.split()) > 1:
                forms.append(phrase)
        if core == words[:1]:
            forms.append(core[0])
    return forms


def _harvest_contact_frontmatter(data_root: Path, tokens: dict[str, str]) -> None:
    """Harvest organisation names and e-mail addresses from CRM contact frontmatter.

    High precision by construction: both are structured fields, not prose, and
    every emitted form passes the same allowlist/stopword/length gates as every
    other token. See the module docstring for why slug decomposition and thread
    titles were rejected as sources.

    Contact addresses are harvested here because the config-file e-mail regex saw
    only ``config/*.json|yaml``, so a person's own address -- the highest-value
    thing a contact record holds -- was never a token at all.
    """
    contacts = data_root / "crm" / "contacts"
    if not contacts.is_dir():
        return
    for md in contacts.glob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        block = m.group(1)
        for email in _EMAIL_RE.findall(block):
            _add(tokens, email, "crm-email")
        for line in block.splitlines():
            key, sep, raw = line.partition(":")
            if not sep or key.strip() not in _ORG_FIELDS:
                continue
            value = raw.strip().strip("\"'")
            # Placeholder check runs on the parenthetical-free form, so
            # "Independent (Freelance)" is recognised as "no employer".
            if re.sub(r"\([^)]*\)", " ", value).strip().lower() in _ORG_PLACEHOLDERS:
                continue
            for form in _org_token_forms(value):
                _add(tokens, form, "crm-org")


def _harvest_executives(data_root: Path, tokens: dict[str, str]) -> None:
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
        # Bare given/family names, in the DEFAULT gate -- not behind ``strict``.
        #
        # They used to sit beside the CRM slug decomposition, and that pairing is
        # what made them invisible. Slug decomposition IS noisy: it turns 300+
        # contact slugs into ordinary English (``security`` alone accounts for 967
        # of the 1,052 findings a strict sweep prints), so ``strict`` is labelled
        # "deep-audit only" and no gate runs it. The exec roster is nothing like
        # that source. Measured on the live overlay 2026-08-24: promoting it adds
        # SIX tokens, against the 263 that all of ``strict`` adds, and the engine
        # tree stays clean.
        #
        # What the pairing cost: a real executive's given name sat in a tracked
        # engine test (``tests/test_sentinel_telegram_cursor.py``) and another in
        # a ``scripts/utils/workspace.py`` docstring, and the gate that exists to
        # stop exactly that reported the surface clean, because the full name was
        # a token and the given name alone was not. One of the two reached the
        # public repo. Colleagues are few, curated, and real; ordinary English is
        # neither. Only the noisy source stays opt-in.
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
        return          # a public clone has none; absence is normal
    # No local `except` here on purpose. Swallowing a parse error and returning
    # left `degraded` False, so the gate ran WITHOUT the operator's curated
    # companies, events and codenames and still reported the tree clean. A
    # partial wall that looks whole is worse than one that says it is blind, so
    # the failure propagates to build_denylist, which sets degraded.
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
    tokens only -- full slugs, full names, organisation names from CRM
    frontmatter, handles, e-mails, IDs, curated tokens.
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
        _harvest_contact_frontmatter(data_root, dl.tokens)
        _harvest_executives(data_root, dl.tokens)
        _harvest_config(data_root, dl.tokens, strict)
        _harvest_curated(data_root, dl.tokens, curated_path)
    except Exception:
        # Fail-open on a harvest error (the gate degrades, never wedges the push);
        # the structural layers still hold. Surfaced via degraded=True.
        dl.degraded = True
    dl._compile()
    return dl
