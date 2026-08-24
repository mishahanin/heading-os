#!/usr/bin/env python3
"""chronicle.py - the Conversation Chronicle (Хроника) builder.

Reads past Claude Code session transcripts for THIS workspace and writes one
short, dated "what this was about" entry per non-trivial conversation. Entries
are a distinct, low-priority RECORD CLASS - never a belief, never the brain.
A business/personal flag routes each entry:

  chronicle/business/  -> indexed, recallable via /recall, ranked BELOW the brain
  chronicle/personal/  -> tagged "Личное", air-gapped by the `personal` segment

The local model (gemma3:4b) only summarizes and flags; it never judges "is this
a valuable decision." Personal tagging FAILS TOWARD PERSONAL: on any doubt the
entry is walled, so an unattended run can only over-wall (a minor recall miss),
never over-expose. Nothing leaves the machine; the brain is never touched.

Usage:
  python scripts/chronicle.py build                 # incremental (since marker)
  python scripts/chronicle.py build --since 2026-07-01 --limit 25
  python scripts/chronicle.py build --backfill      # full one-time history pass
  python scripts/chronicle.py build --dry-run       # print, write nothing
  python scripts/chronicle.py stats                 # counts on disk
  python scripts/chronicle.py query "<text>"        # thin passthrough to the index

Reuses scripts/calibrate.py for transcript parsing (top-level sessions only -
never the nested subagents/ transcripts). Follows the memory-index frontmatter
contract (created/updated/confidence) so the `chronicle` collection ranks it.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.calibrate import (  # noqa: E402
    DEFAULT_SESSIONS_DIR,
    apply_truncation,
    build_envelope,
    parse_jsonl,
)
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.ollama_host import (  # noqa: E402
    OllamaHostUnavailable,
    generation_host,
)
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.workspace import get_data_root, get_default_tz  # noqa: E402

# ============================================================
# Configuration
# ============================================================

MODEL = "gemma3:4b"


@lru_cache(maxsize=1)
def ollama_url() -> str:
    """The /api/generate endpoint this run summarizes against.

    Where that is comes from `generation_host()`: the machine file
    `config/ollama-hosts.yaml` under `generate:`, overridden by
    `HEADING_OS_OLLAMA_HOST`, falling back to the local daemon when neither
    answers. On this laptop that reaches the Windows-side ollama and its iGPU,
    which prefills at 198.7 tok/s against 74.6 on the WSL CPU daemon (measured
    2026-08-22). The whole 120,000-character budget below is sized for the fast
    number.

    It was a module-level constant until 2026-08-23, computed at import from an
    environment variable nobody had set - so every chronicle build summarized on
    the CPU and no code said otherwise. A constant also probed a host on import,
    which `chronicle stats` has no use for. Resolved on first use instead, and
    cached, so one build probes once.
    """
    return f"{generation_host()}/api/generate"

# Prefill is the bottleneck, and this is how much conversation the model is
# allowed to read before it summarizes.
#
# It was 9000 characters -- roughly 2,250 tokens of a session whose transcript
# runs from hundreds of kilobytes to tens of megabytes. The entries read as bare
# facts because that is all the model was ever shown: the decision survives the
# first paragraph, the reasoning behind it does not.
#
# The comment that used to sit here named its own exit: the budget was tuned
# against the CPU daemon at ~65-70 tok/s, and said a GPU-backed
# HEADING_OS_OLLAMA_HOST "lifts prefill to ~220 tok/s, which is what this budget
# would have to be re-tuned against before raising it." Measured 2026-08-22 on
# the Windows-side instance: 198.7 tok/s against 74.6 on the WSL CPU daemon, so
# the claim held and the host now points there (see OLLAMA_HOST below).
#
# At 198.7 tok/s a full 120,000-character envelope costs ~150 s of prefill. The
# build runs unattended at 03:00 over the one to three sessions a day produces,
# so that is minutes of machine time nobody waits for, spent on the only record
# of how a decision was reached.
BODY_CHAR_BUDGET = 120_000
ENVELOPE_MAX_BYTES = 120_000

# The context window is STATED, never inherited. Measured 2026-08-22: the same
# gemma3:4b, which declares `gemma3.context_length: 131072`, was loaded by the
# WSL daemon with a window of 4096 and by the Windows daemon with 131072. Ollama
# truncates a longer prompt silently -- a 120,000-character probe came back
# reporting `prompt_eval_count: 2051`. With the old 9000-character budget nothing
# overflowed and the difference was invisible; at 120,000 it would have thrown
# away seven eighths of every session and said nothing.
# 120,000 chars of mixed RU/EN is roughly 30,000 tokens; 32768 covers it with the
# prompt scaffolding and the reply.
NUM_CTX = 32_768

# A session with less than this much user+assistant text is mechanical
# (a bare /clear, an aborted boot) and produces no entry (CAP-1).
TRIVIAL_TEXT_CHARS = 200

DEFAULT_WINDOW_DAYS = 14  # default lookback when no marker exists

# Fail-toward-personal keyword pre-filter. A hit forces "personal" regardless of
# what the model returns - the model can only ADD personal flags, never remove
# one the keywords caught. STRONG markers only: proper nouns and terms from the
# operator's private life that do NOT double as engineering subject matter.
# Weak/ambiguous words ("personal", "личное", "medical", "family") are left to
# the model's primary-subject judgment - they fire constantly in meta-work ABOUT
# the personal-tagging system and would over-wall genuine engineering sessions.
#
# This public engine repo ships only GENERIC defaults - no operator-private
# proper nouns. The operator's real private markers (family names, their bank,
# their property) load at runtime from a private file in the data overlay
# (`<data_root>/config/chronicle-personal-keywords.txt`, one keyword per line),
# so a private entity never lives in this shareable engine tree. On a public
# clone that file is absent and only these generic defaults apply.
_DEFAULT_PERSONAL_KEYWORDS = (
    "mortgage", "ипотек", "house purchase", "home purchase", "personal home",
)

PROMPT = """You classify and summarize a past AI-assistant work session.

Return ONLY a compact JSON object, no prose, no markdown fence:
{{"gist": "<2-4 sentences: what this conversation was about and what was decided.
      A session usually holds SEVERAL unrelated subjects. Name each one, in the
      order they first appear, starting with the subject the conversation opened
      on. Do NOT summarize only the longest stretch: the subject that took the
      most turns is often not the one the operator came for>",
  "reasoning": "<2-5 sentences: HOW the decision was reached - what was weighed,
      which measurement or fact settled it, what changed someone's mind. Write
      what a reader would need to re-open this decision in four months and
      understand the thinking, not just the outcome. "" if nothing was decided>",
  "considered": ["<each option, approach or claim that was raised and then NOT
      taken, with the reason it was dropped, e.g. 'NPU for embedding - INT8
      vectors would not match the F16 index'. [] if there were no alternatives>"],
  "open": ["<questions left unanswered or work deliberately deferred. [] if none>"],
  "topics": ["<3-6 short topic/entity tags>"],
  "class": "business" | "personal"}}

Rules for the three new fields. Report only what the conversation actually shows;
never invent a rationale that was not stated. Prefer the concrete over the
abstract - a number, a filename, a measured result beats "performance concerns".
When a decision was reversed mid-conversation, say what reversed it: that is the
most valuable line in the record.

LANGUAGE - NEVER TRANSLATE ANYTHING.

Write the record in the language the conversation was actually held in. Russian
conversation -> Russian entry. English conversation -> English entry. A
conversation that mixed the two -> keep the mix, each part in the language it
happened in. Do not normalise, do not pick one language, do not translate a
single sentence in either direction.

Quotations are absolute: reproduce what was said word for word, in its own
alphabet and its own wording. A translated instruction stops being evidence of
what was asked, and the exact phrasing is frequently the whole reason the line
is worth keeping.

This applies to every field: gist, reasoning, considered, open. Topic tags too -
a topic the conversation named in Russian stays Russian.

Report only what the conversation shows. If you cannot tell why something was
decided, leave "reasoning" short or empty. An invented connection is worse than
a missing one: a reader four months from now cannot tell the two apart.

If the conversation has NO substantive content - only mechanical commands like
/clear or /exit, an aborted or empty boot, or nothing was actually discussed -
return EXACTLY this instead, nothing else:
{{"skip": true}}

Decide the class by the PRIMARY SUBJECT of the whole conversation, not by a
single word that happens to appear.

- "personal" = the main subject is the operator's PRIVATE LIFE: buying a
  home/villa/property, a mortgage, family, health or medical insurance, personal
  friends, private money matters, leisure travel.
- "business" = the main subject is WORK: 31C, ODUN.ONE, deals, partners,
  investors, investor decks, the workspace engine, skills, scripts, code,
  memory/index tooling, content, or CRM.
- A passing mention of a word like "personal", "PII", or "privacy" while doing
  ENGINEERING work does NOT make it personal. Judge the dominant subject.
- Tie-breaker ONLY: if the conversation is genuinely half-and-half, or you truly
  cannot tell the primary subject, choose "personal". Do not use the tie-breaker
  when the primary subject is clearly work.

CONVERSATION (user + assistant turns, trimmed):
---
{body}
---
Return the JSON object now."""


# ============================================================
# Paths
# ============================================================

def chronicle_root() -> Path:
    """chronicle/ lives in the DATA overlay, never the engine tree."""
    return get_data_root() / "chronicle"


def marker_path() -> Path:
    return chronicle_root() / ".last-chronicle"


def entry_path(session_date: str, session_id: str, personal: bool) -> Path:
    sub = "personal" if personal else "business"
    return chronicle_root() / sub / f"session-{session_date}-{session_id}.md"


# ============================================================
# Session selection
# ============================================================

def _iso_day(ts: str) -> str | None:
    """The YYYY-MM-DD prefix of an ISO timestamp, or None if it is not one."""
    if len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
        return ts[:10]
    return None


def _session_date(envelope: dict, path: Path) -> str:
    """ISO date (YYYY-MM-DD) of the session - the EARLIEST real timestamp among
    the turns. `started_at_utc` alone is unreliable: a transcript's first event is
    often a meta line (`last-prompt`, `mode`) with a null timestamp, so a naive
    events[0] read yields nothing and would wrongly fall back to today. We scan all
    turns for the earliest valid ISO timestamp; failing that, the file mtime date
    (a real historical date), NEVER today()."""
    stamps = [envelope.get("started_at_utc") or ""]
    for key in ("user_turns", "assistant_turns", "system_reminders"):
        stamps.extend(t.get("ts") or "" for t in envelope.get(key, []))
    days = [d for d in (_iso_day(s) for s in stamps) if d]
    if days:
        return min(days)  # ISO days sort lexicographically -> earliest = start
    # UTC, like the ISO stamps above it and like the marker filter in
    # `select_sessions`. This fallback feeds `sdate`, which becomes the marker,
    # so a local-clock day here reintroduces the same two-clock comparison one
    # level up. Still a real historical date, never today().
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()


def read_marker() -> str | None:
    p = marker_path()
    if not p.is_file():
        return None
    val = p.read_text(encoding="utf-8").strip()
    return val or None


def already_chronicled(session_id: str) -> bool:
    """True if either a business or personal entry already exists for this id.

    Existence-check makes the build idempotent and resumable independent of the
    date-granular marker (CAP-4: a second run processes only new sessions).
    """
    root = chronicle_root()
    for sub in ("business", "personal"):
        hits = list((root / sub).glob(f"session-*-{session_id}.md")) if (root / sub).is_dir() else []
        if hits:
            return True
    return False


def select_sessions(sessions_dir: Path, since: str | None, backfill: bool, limit: int) -> list[Path]:
    """Top-level *.jsonl only (never subagents/), newest first, capped by limit.

    Cutoff: --backfill => all; else --since; else marker; else today-window.
    """
    if backfill:
        cutoff = None
    elif since:
        cutoff = since
    else:
        # The operator's today, not libc's. This was `date.today()` under a
        # DTZ011 waiver, which reads the HOST's zone -- and the two hosts in the
        # fleet disagree by four hours, so the same nightly fire computed a
        # catch-up window starting a day apart on each. `get_default_tz()` reads
        # HEADING_OS_TZ, which `main` loads from .env before anything gets here.
        today = datetime.now(get_default_tz()).date()
        cutoff = read_marker() or (today - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()

    files = sorted(
        sessions_dir.glob("*.jsonl"),  # non-recursive: excludes <id>/subagents/*.jsonl
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    selected = []
    for f in files:
        if already_chronicled(f.stem):
            continue
        if cutoff is not None:
            # UTC, because the MARKER is UTC. `capped_marker` writes a date that
            # `_session_date` read out of the transcript's own ISO stamps, which
            # are UTC days, and this compared it against `date.fromtimestamp`,
            # which is libc's LOCAL day. On a host behind UTC, a session started
            # just after midnight UTC has a local mtime date one day earlier; if
            # its summarization then failed, the marker was capped at its UTC
            # date and this filter read `mday < cutoff` as true on every later
            # run. Permanently invisible, never reported, `cmd_build` exiting 0 —
            # the exact orphan `capped_marker`'s docstring says `<` prevents. It
            # only prevents it when both dates come off the same clock.
            #
            # mtime is the session's LAST write, so its UTC day is never earlier
            # than the UTC day of its first turn: a cutoff equal to a session's
            # own date can no longer exclude that session, in any zone.
            mday = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).date().isoformat()
            if mday < cutoff:
                continue
        selected.append(f)
        if limit and len(selected) >= limit:
            break
    return selected


# ============================================================
# Summarization (local model)
# ============================================================

# Per-turn caps, sized against BODY_CHAR_BUDGET rather than against the old
# 9000-character one. They were 1200 chars per user turn and 800 per assistant
# turn over the first 40 turns, which under a 9000-character budget never bound
# anything — the budget cut first. With the budget at 120,000 they became the
# real limit, and an 800-character cut lands in the middle of exactly the
# passage that explains a decision. The budget is the single place that decides
# how much conversation is read; these only keep one long turn from eating it.
TURN_CHARS = 3000
MAX_ASSISTANT_TURNS = 150


# ============================================================
# Which language the record is written in
# ============================================================
#
# Measured, not asked. The prompt above states the rule in prose, and prose in
# the middle of a prompt is not what a 4B model obeys: tested 2026-08-22 against
# this workspace's own session -- 23,773 characters of body, 56% of the letters
# Cyrillic -- gemma3:4b returned an entry written entirely in English.
#
# So the share is computed here and handed to the model as a fact, in one short
# line at the very END of the prompt, written IN the target language. Position
# and brevity are what a small model follows; a directive written in Russian is
# also an example of Russian, which is worth more than a sentence about it.

# Below this share of Cyrillic among the letters, the session is English; above
# the upper bound it is Russian; between them it genuinely mixed both. The band
# is wide because every session here quotes Latin filenames, commands and
# identifiers, so a Russian conversation still carries a lot of Latin letters --
# a naive majority test would call it English.
_RU_LOWER = 0.15
_RU_UPPER = 0.50


def dominant_language(body: str) -> str:
    """"ru", "en" or "mixed", from the share of Cyrillic among the letters."""
    cyrillic = latin = 0
    for char in body:
        lowered = char.lower()
        if "а" <= lowered <= "я" or lowered == "ё":
            cyrillic += 1
        elif "a" <= lowered <= "z":
            latin += 1
    letters = cyrillic + latin
    if not letters:
        return "en"
    share = cyrillic / letters
    if share < _RU_LOWER:
        return "en"
    if share >= _RU_UPPER:
        return "ru"
    return "mixed"


_DIRECTIVES = {
    "ru": "ВАЖНО: разговор шёл по-русски. Пиши все поля JSON по-русски. "
          "Ничего не переводи на английский.",
    "en": "IMPORTANT: this conversation was in English. Write every JSON field "
          "in English. Do not translate anything.",
    "mixed": "IMPORTANT / ВАЖНО: this conversation mixed Russian and English. "
             "Keep the mix - write each point in the language it was said in, "
             "Russian points in Russian and English points in English. "
             "Ничего не переводи ни в ту, ни в другую сторону.",
}


def language_directive(language: str) -> str:
    return _DIRECTIVES.get(language, _DIRECTIVES["mixed"])


# How much of the opening turn to quote back. Long enough to carry a proper noun
# and the ask around it, short enough that it cannot crowd out the directive.
OPENING_CHARS = 300


def opening_subject(body: str) -> str:
    """The first USER turn of the body, trimmed. "" when there is none.

    Measured, not judged - the same shape as dominant_language(). A session here
    routinely holds several unrelated subjects, and the model reliably reports
    whichever ran longest; the opening turn is usually what the operator came
    for. On session c9bbd8dc both gemma3:4b and gemma3:12b opened their gist on
    the second subject and never named the first at all.
    """
    for line in body.splitlines():
        if line.startswith("USER:"):
            text = line[len("USER:"):].strip()
            if text:
                return text[:OPENING_CHARS]
    return ""


_OPENING_DIRECTIVES = {
    "ru": "ВАЖНО: разговор НАЧАЛСЯ вот с этого. Назови эту тему в gist, даже если "
          "позже дольше говорили о другом: «{opening}»",
    "en": "IMPORTANT: the conversation OPENED on this. Name this subject in the "
          "gist, even if a later subject took more turns: \"{opening}\"",
    "mixed": "IMPORTANT / ВАЖНО: the conversation OPENED on this. Name this "
             "subject in the gist, even if a later subject took more turns. "
             "Назови эту тему, даже если позже дольше говорили о другом: "
             "\"{opening}\"",
}


def opening_directive(opening: str, language: str) -> str:
    """One line naming the opening subject, written in the target language."""
    if not opening:
        return ""
    template = _OPENING_DIRECTIVES.get(language, _OPENING_DIRECTIVES["mixed"])
    return template.format(opening=opening)


def build_prompt(body: str) -> str:
    """The full prompt, with the language directive as its LAST line.

    Last on purpose. The rule is also stated inside PROMPT, where a capable model
    reads it in context; this repetition at the end is what the 4B model actually
    obeys, and costs one line.

    The opening-subject directive sits just above it, for the same reason and by
    the same method. Order matters: the language fix was measured at the very
    end, so nothing displaces it from there.
    """
    language = dominant_language(body)
    tail = [opening_directive(opening_subject(body), language),
            language_directive(language)]
    return PROMPT.format(body=body) + "\n\n" + "\n\n".join(t for t in tail if t)


def envelope_body(envelope: dict) -> str:
    """Interleave the turns in the order they were spoken, oldest first.

    Order matters now that both sides are present: the old shape listed every
    user turn and then every assistant turn, which reads as two monologues and
    hides which answer followed which question. Reasoning is a conversation.
    """
    turns = [("USER", t) for t in envelope.get("user_turns", [])]
    turns += [("ASSISTANT", t)
              for t in envelope.get("assistant_turns", [])[:MAX_ASSISTANT_TURNS]]
    turns.sort(key=lambda pair: pair[1].get("ts") or "")
    lines = [f"{who}: {t['text'][:TURN_CHARS]}" for who, t in turns]
    return "\n".join(lines)[:BODY_CHAR_BUDGET]


_PERSONAL_KEYWORDS_CACHE: tuple[str, ...] | None = None


def _personal_keywords() -> tuple[str, ...]:
    """Generic engine defaults merged with the operator's private keyword file.

    The private file (`<data_root>/config/chronicle-personal-keywords.txt`) lives
    only in the private data overlay; on a public clone it is absent and just the
    generic defaults apply. One keyword per line; blank lines and `#` comments are
    ignored; matched case-insensitively. Cached after first load.
    """
    global _PERSONAL_KEYWORDS_CACHE
    if _PERSONAL_KEYWORDS_CACHE is not None:
        return _PERSONAL_KEYWORDS_CACHE
    keywords = list(_DEFAULT_PERSONAL_KEYWORDS)
    private_file = get_data_root() / "config" / "chronicle-personal-keywords.txt"
    try:
        text = private_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except OSError as exc:
        print(f"chronicle: could not read {private_file}: {exc}", file=sys.stderr)
        text = ""
    for line in text.splitlines():
        kw = line.strip().lower()
        if kw and not kw.startswith("#"):
            keywords.append(kw)
    _PERSONAL_KEYWORDS_CACHE = tuple(dict.fromkeys(keywords))
    return _PERSONAL_KEYWORDS_CACHE


def _keyword_personal(body: str) -> bool:
    low = body.lower()
    return any(k in low for k in _personal_keywords())


def _extract_json(text: str) -> dict | None:
    """Pull the JSON object out of the model reply (tolerates ```json fences)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def summarize(body: str, timeout: int = 300) -> dict | None:
    """One of three shapes, never two.

    * ``{gist, topics, personal, reasoning, considered, open}`` on success. The
      last three are optional by construction and may be empty.
    * ``{"skip": True}`` when the model judged the session substance-free.
      `cmd_build` already branches on this; the docstring simply did not name
      it, and it said the success shape was the three keys it had when it was
      written, so every documented caller contract here was wrong in both
      directions at once.
    * ``None`` on a hard failure.

    Fail-toward-personal: a keyword hit forces personal, and any parse/transport
    failure that still yielded a gist defaults personal. A total failure (no gist)
    returns None and the session is skipped rather than mis-filed.
    """
    payload = {
        "model": MODEL,
        "prompt": build_prompt(body),
        "stream": False,
        "think": False,
        # num_predict was 400, which fit a gist and topics. The reply now also
        # carries the reasoning, the rejected options and the open questions, and
        # a reply cut mid-JSON parses as nothing at all -- the session would be
        # dropped rather than thinned.
        "options": {"temperature": 0.2, "num_predict": 900, "num_ctx": NUM_CTX},
    }
    try:
        endpoint = ollama_url()
    except OllamaHostUnavailable as exc:
        # The host is a pin (2026-08-23) and there is no local daemon behind it
        # any more. Stop the whole run instead of returning None per session:
        # None means "this session had nothing to summarize", and a build that
        # quietly recorded that for every session would be indistinguishable
        # from a real day of empty sessions.
        raise SystemExit(
            f"{RED}chronicle: {exc}.{RESET}\n"
            f"{RED}Start Ollama on the Windows side, then run this again.{RESET}"
        ) from exc

    req = urllib.request.Request(  # noqa: S310 - ollama endpoint, scheme checked in ollama_host
        endpoint, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - hardcoded localhost ollama URL
            reply = json.loads(r.read().decode()).get("response", "")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"{RED}  model call failed: {exc}{RESET}", file=sys.stderr)
        return None

    obj = _extract_json(reply)
    if not obj:
        return None
    if obj.get("skip") is True:
        return {"skip": True}   # model judged the session substance-free
    if not str(obj.get("gist", "")).strip():
        return None

    gist = str(obj["gist"]).strip()
    topics = [str(t).strip() for t in obj.get("topics", []) if str(t).strip()][:6]
    model_class = str(obj.get("class", "")).strip().lower()
    # personal wins on: keyword hit, explicit personal, or an unknown/blank class.
    personal = (
        _keyword_personal(body)
        or model_class == "personal"
        or model_class not in ("business", "personal")
    )
    # The reasoning fields are optional by construction: a model that returns
    # only the old three keys still produces a valid entry, just a thinner one.
    # An entry is never withheld for missing reasoning -- a bare gist beats no
    # record of the session at all.
    reasoning = str(obj.get("reasoning", "") or "").strip()
    considered = [str(x).strip() for x in obj.get("considered", []) or [] if str(x).strip()][:8]
    still_open = [str(x).strip() for x in obj.get("open", []) or [] if str(x).strip()][:6]

    return {"gist": gist, "topics": topics, "personal": personal,
            "reasoning": reasoning, "considered": considered, "open": still_open}


# ============================================================
# Entry writing
# ============================================================

# Typographic -> ASCII. Model output sometimes carries curly quotes/apostrophes,
# an ellipsis, or fancy dashes; chronicle entries are normalized to straight ASCII.
_TYPO_MAP = {
    "‘": "'", "’": "'",              # ' '  single quotes / apostrophe
    "“": '"', "”": '"',              # " "  double quotes
    "‚": "'", "„": '"',              # low quotes
    "′": "'", "″": '"',              # primes
    "…": "...",                            # ellipsis
    "–": "-", "—": "-",              # en / em dash
}


def _normalize(text: str) -> str:
    for k, v in _TYPO_MAP.items():
        text = text.replace(k, v)
    return text


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(_normalize(i), ensure_ascii=False) for i in items) + "]"


def _title(session_date: str, topics: list[str], gist: str) -> str:
    """Title from the top topics (informative, no mid-word cuts); word-boundary
    gist truncation only when there are no topics."""
    if topics:
        return f"Session {session_date} - {', '.join(topics[:3])}"
    words, acc = gist.split(), ""
    for w in words:
        if len(acc) + len(w) + 1 > 60:
            break
        acc = (acc + " " + w).strip()
    return f"Session {session_date} - {acc or gist[:60].rstrip()}"


def render_entry(session_id: str, session_date: str, session_path: str, summary: dict) -> str:
    personal = summary["personal"]
    gist = _normalize(summary["gist"])
    title = _normalize(_title(session_date, summary["topics"], summary["gist"]))
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {session_date}",
        f"created: {session_date}",   # the field the index reads for recency
        f"updated: {session_date}",
        f"session_id: {session_id}",
        f"topics: {_yaml_list(summary['topics'])}",
        "source: chronicle",
        "class: chronicle",
        "confidence: low",
    ]
    if personal:
        lines.append("personal: true")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    if personal:
        lines.append("> Личное - historical record, not a current fact.")
        lines.append("")
    lines.append(gist)

    # The reasoning sections. Rendered only when the model returned something,
    # so an entry for a session that decided nothing does not grow empty
    # headings. These exist because the entry used to carry the decision alone:
    # the record said WHAT was chosen and the "why" survived only in the raw
    # transcript, which the harness deletes on its own schedule.
    reasoning = _normalize(summary.get("reasoning") or "")
    if reasoning:
        lines.append("")
        lines.append("## How this was reached")
        lines.append("")
        lines.append(reasoning)

    considered = [_normalize(x) for x in (summary.get("considered") or []) if x]
    if considered:
        lines.append("")
        lines.append("## Considered and dropped")
        lines.append("")
        lines.extend(f"- {item}" for item in considered)

    still_open = [_normalize(x) for x in (summary.get("open") or []) if x]
    if still_open:
        lines.append("")
        lines.append("## Left open")
        lines.append("")
        lines.extend(f"- {item}" for item in still_open)

    lines.append("")
    lines.append(f"Full transcript: `{session_path}`")
    # Where the transcript survives after the harness deletes the live copy.
    # `scripts/archive-transcripts.py` files it by the session's START date, so
    # this pointer is stable even for a session resumed across midnight.
    lines.append(f"Archived transcript: `chronicle/transcripts/{session_date[:4]}/"
                 f"{session_date}-{session_id}.jsonl.gz`")
    lines.append("")
    return "\n".join(lines)


def write_entry(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # atomic


def capped_marker(newest_processed: str, failed_dates: list[str]) -> str:
    """The high-water mark, never raised past a session that failed.

    Sessions are walked newest-first, and only the FAILURE branch declined to
    advance the mark. A newer session that skipped — trivial, or no substantive
    content — still raised it above an older session whose summarization had
    failed, and `select_sessions` filters `mday < cutoff`, so that older session
    became invisible to every later run. It was chronicled never, reported
    never, and `cmd_build` exited 0 either way, so the nightly timer saw a clean
    build. Recovery needed a manual `--since` nobody was told to run.

    `<` is the filter, so a cutoff EQUAL to the failed date leaves that session
    selectable while everything genuinely older stays covered.
    """
    if not failed_dates:
        return newest_processed
    return min(newest_processed, min(failed_dates))


def write_marker(newest_date: str) -> None:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(newest_date + "\n", encoding="utf-8")
    tmp.replace(p)


# ============================================================
# Commands
# ============================================================

def cmd_build(args: argparse.Namespace) -> int:
    sessions_dir = args.sessions_dir or DEFAULT_SESSIONS_DIR
    if not sessions_dir.is_dir():
        print(f"{RED}sessions dir not found: {sessions_dir}{RESET}", file=sys.stderr)
        return 1

    selected = select_sessions(sessions_dir, args.since, args.backfill, args.limit)
    mode = "backfill" if args.backfill else ("since " + args.since if args.since else "incremental")
    print(f"{BOLD}chronicle build{RESET} ({mode}): {CYAN}{len(selected)}{RESET} session(s) to process"
          + (f" {GRAY}[dry-run]{RESET}" if args.dry_run else ""))

    written = skipped = failed = 0
    newest_processed: str | None = None
    failed_dates: list[str] = []
    for i, path in enumerate(selected, 1):
        events, _ = parse_jsonl(path)
        envelope = apply_truncation(build_envelope(path, events), ENVELOPE_MAX_BYTES)
        body = envelope_body(envelope)
        sdate = _session_date(envelope, path)
        label = f"[{i}/{len(selected)}] {path.stem[:8]} {sdate}"

        if len(body) < TRIVIAL_TEXT_CHARS:
            print(f"  {GRAY}{label}  skip (trivial/empty){RESET}")
            skipped += 1
            newest_processed = max(newest_processed or sdate, sdate)
            continue

        summary = summarize(body)
        if summary is None:
            print(f"  {YELLOW}{label}  skip (no summary){RESET}")
            failed += 1
            failed_dates.append(sdate)
            continue
        if summary.get("skip"):
            print(f"  {GRAY}{label}  skip (no substantive content){RESET}")
            skipped += 1
            newest_processed = max(newest_processed or sdate, sdate)
            continue

        tag = f"{RED}Личное{RESET}" if summary["personal"] else f"{GREEN}business{RESET}"
        dest = entry_path(sdate, path.stem, summary["personal"])
        if args.dry_run:
            print(f"  {label}  {tag}  -> {GRAY}{dest.name}{RESET}")
            print(f"      {summary['gist'][:110]}")
        else:
            write_entry(dest, render_entry(path.stem, sdate, str(path), summary))
            print(f"  {label}  {tag}  -> {dest.name}")
        written += 1
        newest_processed = max(newest_processed or sdate, sdate)

    if not args.dry_run and newest_processed:
        # High-water = newest session actually processed, NOT wall-clock now, so a
        # partial/limited run never orphans older, still-unprocessed sessions (H3).
        #
        # H3 only covered a run that STOPPED early. It did not cover a run that
        # kept going past a failure, and sessions are walked newest-first, so a
        # newer session that skipped (trivial, or no substantive content) still
        # raised the mark above an OLDER session whose summarization had failed.
        # `select_sessions` filters `mday < cutoff`, so that older session was
        # then invisible to every later run: chronicled never, reported never,
        # exit 0 either way, and the nightly timer saw a clean build. Recovery
        # needed a manual --since nobody was told to run.
        #
        # The mark is capped at the OLDEST failure instead. `<` is the filter, so
        # a cutoff EQUAL to that date keeps the failed session selectable, and
        # anything genuinely older than it stays covered.
        newest_processed = capped_marker(newest_processed, failed_dates)
        prev = read_marker()
        if prev is None or newest_processed > prev:
            write_marker(newest_processed)

    print(f"{BOLD}done:{RESET} {GREEN}{written} written{RESET}, "
          f"{skipped} trivial, {failed} failed"
          + (" (dry-run, nothing saved)" if args.dry_run else ""))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    root = chronicle_root()
    biz = len(list((root / "business").glob("session-*.md"))) if (root / "business").is_dir() else 0
    per = len(list((root / "personal").glob("session-*.md"))) if (root / "personal").is_dir() else 0
    print(f"{BOLD}chronicle{RESET}: {GREEN}{biz} business{RESET}, {RED}{per} personal{RESET} "
          f"(marker: {read_marker() or 'none'})")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Thin passthrough to the memory index, scoped to the chronicle collection."""
    idx = Path(__file__).resolve().parent / "memory-index.py"
    cmd = [sys.executable, str(idx), "query", args.text, "--collection", "chronicle"]
    return subprocess.run(cmd, check=False).returncode


# ============================================================
# Personal recall (OPT-IN, on-the-fly, nothing persisted)
# ============================================================
#
# The `personal` path segment is a HARD-CODED air-gap deny (scripts/utils/air_gap.py),
# so personal chronicle NEVER enters any persistent index. To still make it
# recallable on explicit demand, `personal-recall` reads chronicle/personal/*.md
# AT QUERY TIME, scores them locally (bge-m3 on the fly, lexical fallback), and
# persists NOTHING. Personal life cannot surface unless the CEO summons it here.

# The embedder is NOT this module's to choose. It comes from
# `scripts.utils.embeddings.index_embed_target()`, the one place that reads
# `config/memory-index.yaml`, so personal recall scores with the same host and
# the same model as everything else in the workspace.
#
# What used to be here: `EMBED_MODEL = "bge-m3"` and a host resolved from
# `HEADING_OS_OLLAMA_EMBED_HOST` ALONE. That variable is unset on the CEO laptop,
# so the resolver returned the local daemon without probing and personal recall
# ran on the WSL CPU while the index ran on the Windows iGPU - a split nothing
# reported, because the fallback IS the documented behaviour. Reading the config
# is what closes it.
#
# Still separate from `ollama_url()` above, and for the reason that separation
# was written for: chronicle SUMMARIZES on a schedule and can wait for a host,
# while embedding answers a CEO who is standing there. Different risk, different
# preference, and moving one must not silently move the other. That argument was
# never about which file the embedding preference is read from.
#
# Resolved per call, never at import: an `auto:` preference probes a host, and
# `chronicle build` and `chronicle stats` never embed at all.
_FRONT_RE = __import__("re").compile(r"^(\w[\w-]*):\s*(.*)$")


def _personal_gist(body: str) -> str:
    """The summary paragraph of a rendered personal entry.

    Structural, not positional. This used to be
    `body.strip().split("\\n\\n")[-2]`, which was correct while an entry was
    exactly title, gist and transcript pointer. `render_entry` later grew three
    optional sections AFTER the gist - "How this was reached", "Considered and
    dropped", "Left open" - and `[-2]` silently became the last bullet of
    whichever section came last. Personal recall would then embed and display a
    reasoning fragment as the summary, on the one record class that is
    air-gapped and opt-in, with no error anywhere.

    So: walk the blocks in order and return the first one that is not the `# `
    title, not the `> Личное` banner, not a `## ` section heading, and not the
    transcript pointer. Adding a fourth section cannot move it again.
    """
    for block in body.strip().split("\n\n"):
        block = block.strip()
        if not block or block.startswith(("#", ">")):
            continue
        if block.startswith(("Full transcript:", "Archived transcript:")):
            continue
        return block
    return body.strip()


def _load_personal_entries() -> list[dict]:
    """Read chronicle/personal/*.md into {path, date, topics, gist, text} dicts."""
    d = chronicle_root() / "personal"
    if not d.is_dir():
        return []
    entries = []
    for f in sorted(d.glob("session-*.md")):
        raw = f.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        meta, body = {}, raw
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                m = _FRONT_RE.match(line.strip())
                if m:
                    meta[m.group(1)] = m.group(2).strip()
            body = parts[2]
        gist = _personal_gist(body)
        topics = meta.get("topics", "").strip("[]")
        text = f"{meta.get('title', '')} {topics} {gist}".strip()
        entries.append({
            "path": str(f), "date": meta.get("date", "?"),
            "topics": topics, "gist": gist[:300], "text": text,
        })
    return entries


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (na * nb)


def _lexical_score(query: str, text: str) -> float:
    """Term-overlap fraction of query terms present in the entry text."""
    q = {w for w in query.lower().split() if len(w) > 2}
    if not q:
        return 0.0
    low = text.lower()
    return sum(1 for w in q if w in low) / len(q)


def cmd_personal_recall(args: argparse.Namespace) -> int:
    entries = _load_personal_entries()
    if not entries:
        print(f"{GRAY}no personal chronicle entries yet{RESET}")
        return 0

    query = args.text
    scored: list[tuple[float, dict]] = []
    mode = "semantic"
    # The fallback is sound design; the catch-all around it was not. `except
    # Exception` also swallowed the `strict=True` zip's ValueError, a TypeError,
    # and any regression inside scripts.utils.embeddings — flipping the operator
    # to lexical scoring with nothing but the word "(lexical)" in the header to
    # say so. EmbeddingError was even imported here and never referenced, which
    # is what the author meant to catch. Narrowed and made audible 2026-08-24.
    try:
        from scripts.utils.embeddings import EmbeddingError, embed, index_embed_target
    except ImportError as exc:
        EmbeddingError = ()  # noqa: N806 - nothing to catch if the module is absent
        print(f"{GRAY}chronicle: embeddings module unavailable ({exc}); "
              f"scoring lexically.{RESET}", file=sys.stderr)
        embed = index_embed_target = None
    if embed is not None:
        try:
            embed_host, embed_model = index_embed_target()
            vecs = embed([query] + [e["text"] for e in entries],
                         model=embed_model, host=embed_host)
            qv, evs = vecs[0], vecs[1:]
            scored = [(_cosine(qv, ev), e) for ev, e in zip(evs, entries, strict=True)]
            floor = 0.5
        except (EmbeddingError, OSError) as exc:
            print(f"{GRAY}chronicle: semantic scoring unavailable ({exc}); "
                  f"scoring lexically.{RESET}", file=sys.stderr)
            embed = None
    if embed is None:
        mode = "lexical"
        scored = [(_lexical_score(query, e["text"]), e) for e in entries]
        floor = 0.34

    scored.sort(key=lambda t: t[0], reverse=True)
    hits = [(s, e) for s, e in scored if s >= floor][: args.limit]
    print(f"{BOLD}personal chronicle{RESET} ({mode}, on-the-fly, nothing indexed) "
          f"- {len(hits)} hit(s) for {CYAN}{query!r}{RESET}:")
    if not hits:
        best = scored[0][0] if scored else 0.0
        print(f"  {GRAY}no match (best {best:.3f} < {floor}){RESET}")
        return 0
    for s, e in hits:
        print(f"  {RED}[Личное {e['date']}]{RESET} ({s:.3f})  {e['gist'][:120]}")
        print(f"       {GRAY}{e['path']}{RESET}")
    return 0


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    # First, before anything reads the clock. `select_sessions` derives the
    # catch-up window from `get_default_tz()`, which reads HEADING_OS_TZ from
    # os.environ ONLY -- and that variable lives in the gitignored .env, which
    # nothing exports. Without this the window is computed in UTC while the
    # timer fires on local time.
    load_env()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="summarize in-scope sessions into chronicle entries")
    b.add_argument("--since", type=str, help="only sessions on/after this ISO date")
    b.add_argument("--limit", type=int, default=0, help="max sessions this run (0 = no cap)")
    b.add_argument("--backfill", action="store_true", help="process the full in-scope history")
    b.add_argument("--dry-run", action="store_true", help="print planned entries; write nothing")
    b.add_argument("--sessions-dir", type=Path, default=None, help="override the sessions dir (tests)")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("stats", help="show chronicle counts on disk")
    s.set_defaults(func=cmd_stats)

    q = sub.add_parser("query", help="recall over the chronicle collection")
    q.add_argument("text", help="query text")
    q.set_defaults(func=cmd_query)

    pr = sub.add_parser("personal-recall",
                        help="OPT-IN on-the-fly search over personal chronicle (nothing indexed)")
    pr.add_argument("text", help="query text")
    pr.add_argument("--limit", type=int, default=5, help="max hits (default 5)")
    pr.set_defaults(func=cmd_personal_recall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
