#!/usr/bin/env python3
"""rule_split_check.py - L1 verbatim-inventory gate for rule core/detail splits.

Extract every imperative sentence from a pre-split rule file and assert each
survives verbatim in the union of its successor files. READS ONLY.

Safety direction (k3 review, 2026-07-20): the gate fails SAFE, never DANGEROUS.
A directive counts as retained only when it appears as an EXACT normalized SENTENCE
of a successor file, not as a substring of the whole successor blob. The old
substring-in-blob test was a false-negative hazard: a dropped `Use caching.` tested
as present when a successor said `Do not use caching.` (an inverted rule certified
safe), and a directive could be fabricated from fragments of two unrelated
sentences. Exact-sentence membership kills both. The cost is false POSITIVES (a
wrapped or mid-sentence-embedded directive may be over-reported as lost); those are
reviewed by a human and dismissed, which is the safe direction.

Known limitations (necessary-not-sufficient, backstopped by the human PLACEMENT gate
and the Task-1b recall hand-review; see plan CAP-1 / design H1):
 - Grammar blind spot: the lexicon sees modal + lead-verb directives only. Directives
   in other grammar (table/colon-encoded specs, prose like "No new dependency without
   justification") are never extracted, so never checked. The lexicon is broadened
   here past MUST/NEVER, but it is not exhaustive.
 - Context negation (k3 review): retention is SENTENCE-level. A directive sentence that
   survives verbatim but is semantically negated by an ADJACENT sentence ("Hold all
   deployments. Deploy on green.") reads as retained. A syntactic gate cannot see this;
   the human placement gate's read of the actual partition is the backstop.
 - Locality: matching is count-aware but not section-aware. A directive dropped from
   section A while an identical-text directive survives in section B at the SAME total
   count is not flagged. The placement gate owns locality.

snake_case because tests import it (`from scripts.rule_split_check import ...`);
a hyphen would be an illegal module name (development-standards script-naming rule).
"""
from __future__ import annotations
import argparse
import glob
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

INVENTORY_DIR = Path("config/rule-split-inventory")

# Recall self-test fixture lives HERE (source of truth); the test imports it. Fixture
# uses REAL rule-file grammar (markdown bullets, **bold**, lowercase "must have:",
# "DO NOT") because a clean-prose SEED hides the exact misses scrutinize H1 caught.
SEED = (
    "- Use `pathlib.Path` objects, not string paths.\n"
    "You MUST run the scan. NEVER pass --no-verify.\n"
    "**Do not** delete pre-existing dead code.\n"
    "Every skill must have: name, description, version.\n"
    "- DO NOT execute any CRM writes.\n"
    "Always pin exact versions. Avoid bare except.\n"
)

# Imperative lexicon: modal directives + bare leading imperatives. Broadened past
# MUST/NEVER (k3 finding 4a + 2026-07-20 review #3) to cut the grammar blind spot.
# CASE-INSENSITIVE modal anywhere (rule files use "MUST" AND lowercase "must have:"),
# plus colon-introducers and DO NOT / DON'T (scrutinize H1). _LEAD is ^-anchored after
# markdown is stripped. Over-extraction is the safe direction (a human reviews the dump).
_MODAL_RE = re.compile(
    r"\b(?:must(?: have| follow| include| not)?|should|shall(?: not)?|needs? to|"
    r"required|make sure|never|always|do not|don't|cannot)\b",
    re.IGNORECASE,
)
_LEAD_RE = re.compile(
    r"^(?:Do not|Don't|Avoid|Never|Always|Run|Verify|Read|Use|Write|Confirm|Ensure|"
    r"Prefer|Catch|Pin|Keep|Delete|Stop|Check|Log|Restart|Ship|Escalate|Update|Add|"
    r"Remove|Commit|Move|Skip|Follow|Match|Reuse|Search|Treat|Load|Store|Wrap)\b",
    re.IGNORECASE,
)

_MARKER_RE = re.compile(r"^(?:[-*>]\s+|\d+\.\s+|#{1,6}\s+|\|)")


def _strip_md(s: str) -> str:
    # Strip leading markdown (bullet, numbering, bold, code) so a `- Use ...` bullet's
    # lead verb reaches the ^ anchor (scrutinize H1: the `- ` prefix defeated `^Use`).
    return re.sub(r"^\s*(?:[-*>]\s+|\d+\.\s+|\*\*|`)+", "", s).strip()


def _norm(t: str) -> str:
    # Collapse all whitespace so a directive that moved across a hard wrap still matches
    # byte-for-byte at sentence granularity.
    return re.sub(r"\s+", " ", t).strip()


def _blocks(text: str) -> list[str]:
    # Group physical lines into logical units. A new unit starts at a blank line, a
    # list/quote/heading/table marker, or right after the previous line ended a sentence
    # (.!?:). Continuation lines (hard-wrapped prose) join the current unit. This keeps
    # one-per-line bullets SEPARATE while reassembling a wrapped prose directive into a
    # single unit, so wraps are not torn into fragments (k3 review).
    blocks: list[str] = []
    cur = ""
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if cur:
                blocks.append(cur)
                cur = ""
            continue
        if not cur:
            cur = line
        elif _MARKER_RE.match(line) or re.search(r"[.!?:]$", cur):
            blocks.append(cur)
            cur = line
        else:
            cur = cur + " " + line
    if cur:
        blocks.append(cur)
    return blocks


def _units(text: str) -> list[str]:
    units = []
    for block in _blocks(text):
        for s in re.split(r"(?<=[.!?])\s+", block):
            s = s.strip()
            if s:
                units.append(s)
    return units


def _is_imperative(s: str) -> bool:
    return bool(_MODAL_RE.search(s) or _LEAD_RE.match(_strip_md(s)))


def _extract(text: str, dedup: bool) -> list[str]:
    out, seen = [], set()
    for s in _units(text):
        if not _is_imperative(s):
            continue
        if dedup and s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def extract_imperatives(text: str) -> list[str]:
    # Deduped, order-preserving. Used by --dump for the recall hand-review display.
    return _extract(text, dedup=True)


def check_split(original_text: str, successor_texts: list[str]) -> list[str]:
    # Count-aware (multiset) membership: a directive is retained only if the successor
    # sentences carry it at least as many times as the original did. Exact normalized
    # SENTENCE match (not substring of a blob) kills inversion + cross-boundary
    # fabrication; the count check kills the "dropped one of two identical copies"
    # hole and the collision path the broadened lexicon would otherwise open (k3 review).
    orig = Counter(_norm(i) for i in _extract(original_text, dedup=False))
    succ = Counter(_norm(s) for s in _units("\n".join(successor_texts)) if _norm(s))
    return [imp for imp, cnt in orig.items() if succ[imp] < cnt]


def _declared_destinations(stem: str, inventory_dir: Path, repo_root: Path) -> list[Path]:
    """Files OUTSIDE `.claude/rules/` that a rule declared as an offload target.

    One path per line in `<inventory_dir>/<stem>.destinations`; blank lines and
    `#` comments ignored. Absent file means no destinations, which is the common
    case and stays free.

    Why this exists: the union was `rules_dir/<base>*.md` only, so it could see a
    rule SPLIT into a sibling but not a rule OFFLOADED into `docs/` or
    `reference/`. On 2026-08-20 `documentation.md` moved its propagation chain to
    `docs/DOCS-PIPELINE.md` and four frozen directives read as dropped although
    every one of them was alive in the destination — a false positive that
    pressures the next person to re-freeze the snapshot, which is exactly how a
    guard like this decays into a rubber stamp.

    The destination list is committed beside the snapshot ON PURPOSE. Widening
    the search to "anywhere in the repo" would make the check unfalsifiable: a
    directive could vanish from every loaded surface and still match some
    unrelated prose. A named file is a claim someone made and can be held to.
    """
    manifest = Path(inventory_dir) / f"{stem}.destinations"
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        rel = line.split("#", 1)[0].strip()
        if rel:
            out.append(repo_root / rel)
    return out


def _rule_union_sentences(stem: str, rules_dir: str = ".claude/rules",
                          inventory_dir: Path = INVENTORY_DIR) -> set[str]:
    # stem is a basename like "development-standards.md"; glob its core+detail siblings
    # so the snapshot survives a later split (development-standards.md + -detail.md),
    # plus any file the rule declared as an offload destination.
    base = stem[:-3] if stem.endswith(".md") else stem
    pattern = f"{rules_dir}/{base}*.md"
    paths = [Path(p) for p in sorted(glob.glob(pattern))]
    # A destination path is repo-relative, so it needs the repo root. Derive it
    # from rules_dir by stripping the `.claude/rules` tail when it is there, and
    # otherwise take the parent — which is what a test fixture's `<tmp>/rules`
    # wants. The first cut hard-coded `.parent.parent` and happened to be right
    # for the real layout and wrong for every other, which a synthetic case
    # caught immediately: the destination resolved one directory above the
    # fixture and the check reported a live directive as dropped.
    rp = Path(rules_dir)
    repo_root = rp.parent.parent if rp.parts[-2:] == (".claude", "rules") else rp.parent
    for extra in _declared_destinations(stem, inventory_dir, repo_root):
        if extra.is_file():
            paths.append(extra)
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    return {_norm(s) for s in _units(text) if _norm(s)}


def snapshot_inventory(rule_path: str) -> Path:
    # Freeze the current extracted imperatives of one rule file. Written at compress/split
    # time so --check has a baseline to guard against a FUTURE edit that drops a directive.
    inv = INVENTORY_DIR / (Path(rule_path).name + ".txt")
    inv.parent.mkdir(parents=True, exist_ok=True)
    imps = extract_imperatives(Path(rule_path).read_text(encoding="utf-8"))
    inv.write_text("\n".join(imps) + "\n", encoding="utf-8")
    return inv


def check_inventories(inventory_dir: Path = INVENTORY_DIR,
                      rules_dir: str = ".claude/rules") -> list[tuple[str, str]]:
    # For every snapshot, assert each frozen imperative is still an exact sentence of the
    # current core+detail union. Returns [(stem, dropped_line), ...]; empty = clean.
    bad = []
    for inv in sorted(glob.glob(str(Path(inventory_dir) / "*.txt"))):
        stem = Path(inv).name[:-4]  # strip ".txt"
        union = _rule_union_sentences(stem, rules_dir, Path(inventory_dir))
        for line in Path(inv).read_text(encoding="utf-8").splitlines():
            if line.strip() and _norm(line) not in union:
                bad.append((stem, line))
    return bad


def _read_gitref(ref_path: str) -> str:
    ref, _, path = ref_path.partition(":")
    try:
        return subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True,
                              text=True, check=True).stdout
    except subprocess.CalledProcessError as e:  # missing ref/path: fail loud, do not pass silently (L1)
        sys.stderr.write(f"rule_split_check: cannot read {ref_path!r}: {e.stderr.strip()}\n")
        raise SystemExit(2) from e


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recall-selftest", action="store_true")
    ap.add_argument("--dump", help="path of a live rule file; print every extracted "
                    "imperative, numbered, for a CEO recall hand-review (Task 1b)")
    ap.add_argument("--snapshot", help="freeze one rule file's imperatives to "
                    "config/rule-split-inventory/<basename>.txt")
    ap.add_argument("--check", action="store_true", help="CI drift guard: assert every "
                    "snapshotted imperative still survives in its core+detail union")
    ap.add_argument("--original", help="gitref:path of the pre-split file")
    ap.add_argument("--successors", nargs="*", default=[])
    a = ap.parse_args()
    if a.snapshot:
        print(f"snapshot: {snapshot_inventory(a.snapshot)}")
        return 0
    if a.check:
        bad = check_inventories()
        for stem, line in bad:
            print(f"CHECK FAIL {stem}: dropped {line!r}")
        print("inventory check: OK" if not bad else f"inventory check: {len(bad)} dropped")
        return 1 if bad else 0
    if a.dump:
        imps = extract_imperatives(Path(a.dump).read_text(encoding="utf-8"))
        for i, s in enumerate(imps, 1):
            print(f"{i:3d}. {s}")
        print(f"\n-- {len(imps)} regex-recognized imperatives. "
              "Hand-compare to a manual directive count; record recall = caught/total.")
        return 0
    if a.recall_selftest:
        got = [_norm(g) for g in extract_imperatives(SEED)]
        needles = ["Use `pathlib.Path` objects, not string paths.", "You MUST run the scan.",
                   "NEVER pass --no-verify.", "**Do not** delete pre-existing dead code.",
                   "Every skill must have: name, description, version.",
                   "DO NOT execute any CRM writes.", "Always pin exact versions.",
                   "Avoid bare except."]
        missing = [n for n in needles if not any(_norm(n) in g for g in got)]
        print("recall self-test: OK" if not missing else f"recall FAIL: {missing}")
        return 0 if not missing else 1
    if not a.original:
        # Every flag branch above is optional, so a bare invocation fell through
        # to _read_gitref(None) and died on `NoneType.partition` -- the loudest,
        # least informative failure a gate script can produce in CI.
        ap.error("--original is required (or use --snapshot / --check / --dump / "
                 "--recall-selftest)")
    original = _read_gitref(a.original)
    successors = [Path(p).read_text(encoding="utf-8") for p in a.successors]
    lost = check_split(original, successors)
    if lost:
        print(f"L1 FAIL: {len(lost)} imperative(s) lost:")
        for s in lost:
            print(f"  - {s}")
        return 1
    print("L1 OK: 0 imperatives lost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
