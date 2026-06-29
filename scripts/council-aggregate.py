#!/usr/bin/env python3
"""Council aggregate - Track C frontier-evidence reader.

Walks every transcript under outputs/operations/council/, extracts the
question + each model's answer, and writes a single aggregate file with
side-by-side summaries and the recorded CEO verdict (if any).

VERDICT WORKFLOW (2026-05-24 redesign): the CEO does NOT edit this file.
After every /council run, the model asks the CEO which answer landed best
and calls scripts/council-record-verdict.py to append the choice to
_verdicts.jsonl. This script then reads that JSONL and renders the
verdicts inline. The aggregate is a READ-ONLY view onto the verdict log.

Once >=20 verdicts accumulate, the data is enough to calibrate a
Gemini-as-judge mode (Phase 3b per the plan). Until then, every entry is
either Recorded (with choice + notes) or Pending CEO verdict.

Usage:
  python scripts/council-aggregate.py              # rebuild aggregate
  python scripts/council-aggregate.py --json       # JSON output

Output: outputs/operations/council/_aggregate.md (CEO-only by directory
default classification).

Cross-platform: pure Python, runs anywhere a workspace clone runs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.utils.colors import CYAN, GRAY, GREEN, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import display_path, get_outputs_dir  # noqa: E402

COUNCIL_DIR = get_outputs_dir() / "operations" / "council"
AGGREGATE_PATH = COUNCIL_DIR / "_aggregate.md"
VERDICTS_PATH = COUNCIL_DIR / "_verdicts.jsonl"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_H1_TOPIC_RE = re.compile(r"^# (?:Council Consultation\s*-\s*)?(.*?)$", re.MULTILINE)
_SECTION_RES = {
    "question": re.compile(r"^## (?:Question|Question\s*/\s*Draft|Draft).*?\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE),
    "gemini":   re.compile(r"^## Gemini'?s full response.*?\n(.*?)(?=^## (?!Side-by-side)|\Z)", re.DOTALL | re.MULTILINE),
    "grok":     re.compile(r"^## Grok'?s full response.*?\n(.*?)(?=^## (?!Side-by-side)|\Z)", re.DOTALL | re.MULTILINE),
    "kimi":     re.compile(r"^## Kimi'?s full response.*?\n(.*?)(?=^## (?!Side-by-side)|\Z)", re.DOTALL | re.MULTILINE),
    "claude":   re.compile(r"^## Claude'?s (?:view|response|answer|critique).*?\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE),
}


@dataclass
class Transcript:
    """One /council run extracted to comparison form."""
    path: Path
    timestamp: str
    mode: str
    topic: str
    question_snippet: str
    claude_snippet: str
    gemini_snippet: str
    grok_snippet: str
    kimi_snippet: str

    def models_present(self) -> list[str]:
        out = []
        if self.claude_snippet: out.append("claude")
        if self.gemini_snippet: out.append("gemini")
        if self.grok_snippet:   out.append("grok")
        if self.kimi_snippet:   out.append("kimi")
        return out


def _snippet(text: str, max_chars: int = 320) -> str:
    """Squash whitespace + truncate cleanly. None / empty -> empty string."""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) <= max_chars:
        return t
    # Trim to last word boundary before max_chars
    truncated = t[:max_chars].rsplit(" ", 1)[0]
    return truncated + "..."


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def parse_transcript(path: Path) -> Transcript | None:
    """Parse one transcript file. Returns None on shape mismatch."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _parse_frontmatter(text)
    topic_match = _H1_TOPIC_RE.search(text)
    topic = topic_match.group(1).strip() if topic_match else path.stem

    def _section(key: str) -> str:
        m = _SECTION_RES[key].search(text)
        return m.group(1).strip() if m else ""

    return Transcript(
        path=path,
        timestamp=fm.get("timestamp", path.stem.split("_")[0]),
        mode=fm.get("mode", "?"),
        topic=topic[:120],
        question_snippet=_snippet(_section("question")),
        claude_snippet=_snippet(_section("claude")),
        gemini_snippet=_snippet(_section("gemini")),
        grok_snippet=_snippet(_section("grok")),
        kimi_snippet=_snippet(_section("kimi")),
    )


def collect_transcripts() -> list[Transcript]:
    if not COUNCIL_DIR.exists():
        return []
    files = sorted(
        [p for p in COUNCIL_DIR.glob("*.md")
         if not p.name.startswith("_") and not p.name.startswith(".")],
        reverse=True,  # newest first
    )
    parsed = [t for t in (parse_transcript(p) for p in files) if t is not None]
    return parsed


# ============================================================
# Verdict loading (source of truth = _verdicts.jsonl, last-write-wins)
# ============================================================
def load_verdicts() -> dict[str, dict]:
    """Returns {verdict_id: latest verdict record} from _verdicts.jsonl.

    The JSONL is append-only - last record per id wins. CEO can revise a
    verdict by recording again via scripts/council-record-verdict.py.
    """
    if not VERDICTS_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    for line in VERDICTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = rec.get("verdict_id")
        if vid:
            out[vid] = rec
    return out


# ============================================================
# Rendering
# ============================================================
def render(transcripts: list[Transcript], verdicts: dict[str, dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append("# /council comparative log")
    lines.append("")
    lines.append(f"Last rebuilt: {today}. Transcripts: {len(transcripts)}.")
    lines.append("")
    lines.append("**What this is.** Side-by-side comparison of every `/council` consultation. "
                 "Verdicts come from `scripts/council-record-verdict.py` writing to "
                 "`_verdicts.jsonl`; this file is a READ-ONLY view onto that log. Once >=20 verdicts "
                 "accumulate, the dataset is large enough to calibrate a Gemini-as-judge mode "
                 "(Phase 3b - waiting per CEO decision 2026-05-24).")
    lines.append("")
    lines.append("**How verdicts get recorded.** The `/council` skill asks the CEO at the end of "
                 "every run which answer landed best (claude / gemini / grok / kimi / mix / reject). The "
                 "model writes the answer + any free-text note to `_verdicts.jsonl` and rebuilds "
                 "this aggregate. CEO never edits this file or the JSONL directly.")
    lines.append("")

    counts = {"claude": 0, "gemini": 0, "grok": 0, "kimi": 0, "mix": 0, "reject": 0}
    pending = 0
    for t in transcripts:
        rec = verdicts.get(t.path.stem)
        if rec and rec.get("choice") in counts:
            counts[rec["choice"]] += 1
        else:
            pending += 1

    lines.append("**Tally so far:** "
                 f"Claude={counts['claude']}, "
                 f"Gemini={counts['gemini']}, "
                 f"Grok={counts['grok']}, "
                 f"Kimi={counts['kimi']}, "
                 f"Mix={counts['mix']}, "
                 f"Reject={counts['reject']}, "
                 f"Pending={pending} of {len(transcripts)}.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for t in transcripts:
        rec = verdicts.get(t.path.stem)
        models = t.models_present()
        lines.append(f"## {t.timestamp} - {t.topic}")
        lines.append("")
        lines.append(f"- File: [{t.path.name}]({t.path.name})")
        lines.append(f"- Mode: {t.mode}. Models: {', '.join(models) if models else 'none parsed'}.")
        lines.append("")
        lines.append(f"**Question:** {t.question_snippet or '_(no question section parsed)_'}")
        lines.append("")
        if t.claude_snippet:
            lines.append(f"**Claude:** {t.claude_snippet}")
            lines.append("")
        if t.gemini_snippet:
            lines.append(f"**Gemini:** {t.gemini_snippet}")
            lines.append("")
        if t.grok_snippet:
            lines.append(f"**Grok:** {t.grok_snippet}")
            lines.append("")
        if t.kimi_snippet:
            lines.append(f"**Kimi:** {t.kimi_snippet}")
            lines.append("")
        if rec:
            choice = rec.get("choice", "?").upper()
            notes = rec.get("notes", "").strip()
            recorded_at = rec.get("recorded_at", "")
            verdict_line = f"**CEO verdict:** {choice}"
            if notes:
                verdict_line += f" - _{notes}_"
            if recorded_at:
                verdict_line += f"  ({recorded_at})"
            lines.append(verdict_line)
        else:
            lines.append("_(pending CEO verdict)_")
        lines.append("")
        lines.append("---")
        lines.append("")

    if not transcripts:
        lines.append("_No `/council` transcripts found yet under `outputs/operations/council/`. "
                     "Run `/council` to start populating this aggregate._")
        lines.append("")

    lines.append("_Generated by `scripts/council-aggregate.py` from `_verdicts.jsonl`. "
                 "To record or revise a verdict, use `scripts/council-record-verdict.py` "
                 "(the /council skill calls it automatically at end of each run)._")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate /council transcripts.")
    parser.add_argument("--json", action="store_true", help="Print parsed transcripts as JSON.")
    args = parser.parse_args(argv)

    transcripts = collect_transcripts()
    verdicts = load_verdicts()
    print(f"{CYAN}Parsed {len(transcripts)} transcripts, {len(verdicts)} verdicts from "
          f"{COUNCIL_DIR}{RESET}", file=sys.stderr)

    if args.json:
        out = []
        for t in transcripts:
            rec = verdicts.get(t.path.stem)
            out.append({
                "path": display_path(t.path),
                "timestamp": t.timestamp,
                "mode": t.mode,
                "topic": t.topic,
                "models_present": t.models_present(),
                "question_snippet": t.question_snippet,
                "claude_snippet": t.claude_snippet,
                "gemini_snippet": t.gemini_snippet,
                "grok_snippet": t.grok_snippet,
                "kimi_snippet": t.kimi_snippet,
                "verdict": rec,
            })
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    md = render(transcripts, verdicts)
    AGGREGATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGGREGATE_PATH.write_text(md, encoding="utf-8")
    print(f"{GREEN}Aggregate written: {AGGREGATE_PATH}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
