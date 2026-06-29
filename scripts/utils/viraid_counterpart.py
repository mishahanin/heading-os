#!/usr/bin/env python3
"""VIRAID counterpart resolver -- the content-class gate for /odin collect.

VIRAID is the only collect source with no path boundary (a single free-text
Telegram channel), so business-counterpart resolution is its ONLY air-gap. A
message is admitted into episode collection ONLY when it resolves to a real
EXTERNAL business counterpart: a named person or company in crm/contacts/ or
context/people.md whose relationship is not purely internal tribe.

Two failure modes from the first live run (2026-05-30) shaped this, neither of
which a name-match-alone gate can stop:

  1. Noise-token leak. Generic words (`channel`, `document`, `from`, `with`)
     harvested from CRM *bodies* resolved a personal apostille task. Fix: the
     vocabulary is built ONLY from structured name fields (CRM frontmatter
     name / entity_ref / pipeline_company, aliases.md company names, people.md
     section headers and top-contact lead names) -- never from free-text
     bodies -- plus a stoplist of generic and company-suffix words.

  2. Internal-personal content. "check with Alex re a colleague's case" names two
     real people, but both are tribe -- a personal matter, not a business
     episode. Fix: require at least one EXTERNAL (non-tribe) counterpart. A
     message resolving only to tribe members drops (and is counted).

Pure string transform over already-captured residue. No network, no filesystem
writes. Imported by the /odin collect mode; also runnable as a CLI to gate a
VIRAID state.json end-to-end.

Usage:
    from scripts.utils.viraid_counterpart import build_vocab, resolve, gate_message
    vocab = build_vocab(root)
    r = resolve(text, vocab)              # -> {"external": [...], "tribe": [...]}
    admit = bool(r["external"])           # gate: >=1 external counterpart

    python3 scripts/utils/viraid_counterpart.py --since 2026-05-19   # gate report
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_data_root

# ============================================================
# Configuration
# ============================================================

# Generic words that must never resolve a message to a counterpart. Two groups:
# company-suffix / role words that ride along inside structured name fields, and
# common English/Russian filler that surfaced as noise in the first live run.
STOPLIST = {
    # company suffixes and generic business words
    "group", "capital", "beyond", "holding", "holdings", "partners", "partner",
    "company", "communications", "technologies", "technology", "solutions",
    "systems", "consulting", "ventures", "labs", "inc", "ltd", "llc", "fz",
    "the", "and", "for", "dc", "one", "concept", "international", "global",
    "sales", "services", "service", "digital", "networks", "network",
    # tribe / role nouns
    "ceo", "cto", "cso", "coo", "svp", "vp", "founder", "principal", "tribe",
    # observed noise tokens (belt-and-braces; structured-only build already excludes them)
    "channel", "document", "from", "with", "per", "get", "ask", "check",
    "added", "task", "deleted", "regarding", "case", "advice", "paper",
    "status", "action", "needed", "request", "support", "them", "their",
    "advice's", "employer", "via", "today", "tomorrow", "meeting", "email",
}

_WORD = re.compile(r"[A-Za-zЀ-ӿ]+")


def _name_tokens(s: str):
    """Lowercase alphabetic tokens of length >= 3 from a person/company name."""
    if not s:
        return []
    return [t for t in (w.lower() for w in _WORD.findall(s)) if len(t) >= 3]


def _slug_tokens(slug: str):
    """Parts of a kebab-case slug, length >= 3."""
    return [p.lower() for p in (slug or "").split("-") if len(p) >= 3 and p.isalpha()]


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return fm


# ============================================================
# Vocabulary build (structured name fields ONLY)
# ============================================================

def _add(vocab: dict, token: str, cls: str):
    """Insert a token with its class. Conservative on collision: a token seen as
    tribe is never promoted to external, so a surname shared with a tribe member
    cannot silently auto-admit a personal message."""
    if token in STOPLIST or len(token) < 3:
        return
    if cls == "tribe":
        vocab[token] = "tribe"
    else:  # external
        vocab.setdefault(token, "external")


def build_vocab(root: Path) -> dict:
    """token -> 'external' | 'tribe', drawn only from structured name fields."""
    root = Path(root)
    vocab: dict = {}

    # 1. CRM contacts (primary, richest source)
    for p in sorted((root / "crm" / "contacts").glob("*.md")):
        fm = _frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        rel = (fm.get("relationship_type") or "").lower()
        company = fm.get("pipeline_company") or ""
        is_tribe = rel.startswith("tribe") or company.strip() == "31C"
        cls = "tribe" if is_tribe else "external"
        for t in _name_tokens(fm.get("name", "")):
            _add(vocab, t, cls)
        for t in _slug_tokens(fm.get("entity_ref") or p.stem):
            _add(vocab, t, cls)
        # company tokens: only meaningful for external partners/deals
        if not is_tribe:
            for t in _name_tokens(company):
                _add(vocab, t, "external")

    # 2. aliases.md -- canonical + variant company names (all external)
    aliases = root / "crm" / "aliases.md"
    if aliases.exists():
        for line in aliases.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^###\s+(.+)$", line) or re.match(r"^-\s+(.+)$", line)
            if m:
                for t in _name_tokens(m.group(1)):
                    _add(vocab, t, "external")

    # 3. people.md -- structured lines only (never free-text bodies)
    people = root / "context" / "people.md"
    if people.exists():
        for line in people.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            # section headers -> company names (external)
            h = re.match(r"^###\s+(.+)$", stripped)
            if h:
                for t in _name_tokens(h.group(1)):
                    _add(vocab, t, "external")
                continue
            # top-contact bullets: "- Name (role, Company) - ..." ; class by 31C mention
            b = re.match(r"^-\s+([A-Z][^()\-]+?)\s*[(\-]", stripped)
            if b:
                cls = "tribe" if "31C" in stripped else "external"
                for t in _name_tokens(b.group(1)):
                    _add(vocab, t, cls)
                continue
            # "**Principals:** A and B" -> external person names
            pr = re.match(r"^-?\s*\*\*Principals?:\*\*\s*(.+)$", stripped)
            if pr:
                for t in _name_tokens(pr.group(1)):
                    _add(vocab, t, "external")

    return vocab


# ============================================================
# Resolution + gate
# ============================================================

def resolve(text: str, vocab: dict) -> dict:
    """Return {'external': [...], 'tribe': [...]} of counterpart tokens named in
    text. Whole-word, case-insensitive; possessives ('Victor'') collapse to the
    bare token. Only structured-name vocab can match -- generic words cannot."""
    ext, trb = set(), set()
    for w in _WORD.findall((text or "").lower()):
        cls = vocab.get(w)
        if cls == "external":
            ext.add(w)
        elif cls == "tribe":
            trb.add(w)
    return {"external": sorted(ext), "tribe": sorted(trb)}


def gate_message(msg: dict, vocab: dict, since: str):
    """(admit, reason, resolved) for one VIRAID message dict.

    Admit iff disposition in {task, crm} AND date >= since AND >=1 external
    counterpart resolves. Otherwise drop with a reason string.
    """
    disp = (msg.get("disposition") or "").lower()
    if disp not in ("task", "crm"):
        return False, f"disposition={disp or 'none'}", {"external": [], "tribe": []}
    date = (msg.get("date") or "")[:10]
    if date and date < since:
        return False, f"date<{since}", {"external": [], "tribe": []}
    text = (msg.get("text") or "") + " " + (msg.get("action_summary") or "")
    r = resolve(text, vocab)
    if not r["external"]:
        why = "no business counterpart" if not r["tribe"] else "tribe-only (internal)"
        return False, why, r
    return True, "external counterpart", r


# ============================================================
# CLI -- gate a VIRAID state.json end-to-end
# ============================================================

def _run(since: str, root: Path):
    state_path = root / "outputs" / "operations" / "viraid" / "state.json"
    if not state_path.exists():
        print(f"VIRAID state not found: {state_path}")
        return 1
    vocab = build_vocab(root)
    msgs = json.loads(state_path.read_text(encoding="utf-8")).get("messages", {})
    kept, dropped = [], {}
    for mid, m in msgs.items():
        admit, reason, r = gate_message(m, vocab, since)
        if admit:
            kept.append((m.get("date", "")[:10], r["external"], m.get("action_summary", "")[:70]))
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    print(f"VIRAID gate  (--since {since})   vocab tokens: {len(vocab)}")
    print(f"  messages: {len(msgs)}  admitted: {len(kept)}  dropped: {sum(dropped.values())}")
    for reason, n in sorted(dropped.items(), key=lambda x: -x[1]):
        print(f"    drop[{reason}]: {n}")
    print("  admitted:")
    for d, ext, summ in sorted(kept):
        print(f"    [{d}] external={ext} :: {summ}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Gate a VIRAID state.json for /odin collect.")
    ap.add_argument("--since", default="1970-01-01", help="ISO date floor (YYYY-MM-DD)")
    ap.add_argument("--root", default=None, help="workspace root (default: auto-detect)")
    args = ap.parse_args()
    root = Path(args.root) if args.root else get_data_root()
    return _run(args.since, root)


if __name__ == "__main__":
    raise SystemExit(main())
