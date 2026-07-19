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
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.calibrate import (  # noqa: E402
    DEFAULT_SESSIONS_DIR,
    apply_truncation,
    build_envelope,
    parse_jsonl,
)
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_data_root  # noqa: E402

# ============================================================
# Configuration
# ============================================================

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:4b"

# Prefill is the CPU bottleneck (~65 tok/s). Trim each transcript to this many
# characters of conversation before the model reads it - enough to summarize,
# cheap enough to keep a session near ~50s on this hardware.
BODY_CHAR_BUDGET = 9000
ENVELOPE_MAX_BYTES = 120_000

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
{{"gist": "<2-4 sentences: what this conversation was about and what was decided>",
  "topics": ["<3-6 short topic/entity tags>"],
  "class": "business" | "personal"}}

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
    return date.fromtimestamp(path.stat().st_mtime).isoformat()  # noqa: DTZ012 - local mtime date, historical


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
        cutoff = read_marker() or (date.today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()  # noqa: DTZ011 - local today-window

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
            mday = date.fromtimestamp(f.stat().st_mtime).isoformat()  # noqa: DTZ012 - local mtime date
            if mday < cutoff:
                continue
        selected.append(f)
        if limit and len(selected) >= limit:
            break
    return selected


# ============================================================
# Summarization (local model)
# ============================================================

def envelope_body(envelope: dict) -> str:
    turns = []
    for t in envelope.get("user_turns", []):
        turns.append("USER: " + t["text"][:1200])
    for t in envelope.get("assistant_turns", [])[:40]:
        turns.append("ASSISTANT: " + t["text"][:800])
    return "\n".join(turns)[:BODY_CHAR_BUDGET]


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
    """Return {gist, topics, personal} or None on a hard failure.

    Fail-toward-personal: a keyword hit forces personal, and any parse/transport
    failure that still yielded a gist defaults personal. A total failure (no gist)
    returns None and the session is skipped rather than mis-filed.
    """
    payload = {
        "model": MODEL,
        "prompt": PROMPT.format(body=body),
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 400},
    }
    req = urllib.request.Request(  # noqa: S310 - hardcoded localhost ollama URL
        OLLAMA_URL, data=json.dumps(payload).encode(),
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
    return {"gist": gist, "topics": topics, "personal": personal}


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
    lines.append("")
    lines.append(f"Full transcript: `{session_path}`")
    lines.append("")
    return "\n".join(lines)


def write_entry(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # atomic


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

EMBED_MODEL = "bge-m3"
EMBED_HOST = "http://localhost:11434"
_FRONT_RE = __import__("re").compile(r"^(\w[\w-]*):\s*(.*)$")


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
        gist = body.strip().split("\n\n")[-2].strip() if "Full transcript" in body else body.strip()
        gist = gist.replace("> Личное - historical record, not a current fact.", "").strip()
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
    try:
        from scripts.utils.embeddings import EmbeddingError, embed
        vecs = embed([query] + [e["text"] for e in entries], model=EMBED_MODEL, host=EMBED_HOST)
        qv, evs = vecs[0], vecs[1:]
        scored = [(_cosine(qv, ev), e) for ev, e in zip(evs, entries, strict=True)]
        floor = 0.5
    except Exception:  # noqa: BLE001 - ollama down or embed failure -> lexical fallback
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
