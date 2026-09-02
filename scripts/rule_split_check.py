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
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# This module is imported as `scripts.rule_split_check` by its tests (repo root
# already on the path) AND run as `python scripts/rule_split_check.py` by CI,
# where sys.path[0] is `scripts/` and the package is not importable. The insert
# is the same one every sibling CLI script under `scripts/` carries.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.repo_files import read_sources  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()

# Anchored on the repository, never on the process cwd. Both were bare relative
# paths until 2026-09-02, and a relative path in a gate is not a smaller bug than
# a wrong one: MEASURED that day, `--check` from the repo root handed 6 files to
# `read_sources` (2 snapshots, 3 rule files, 1 declared destination) and printed
# "inventory check: OK"; the identical command from `/tmp` handed it 0 files and
# printed the identical line at the identical exit 0. The glob that found nothing
# and the glob that found everything intact are indistinguishable in the output,
# which is the whole reason `corpus_floor()` below exists.
INVENTORY_DIR = ROOT / "config/rule-split-inventory"
RULES_DIR = ROOT / ".claude/rules"

# Floors for `--check`. A gate whose corpus can silently shrink to nothing is
# green over nothing, so refuse rather than pass. These are FLOORS, not
# expectations: they are set below the live counts on purpose so that adding a
# rule or a snapshot never has to touch them, and they only ever move up when
# the operator decides the coverage baseline has risen.
MIN_SNAPSHOTS = 2            # committed snapshots under INVENTORY_DIR
MIN_RULE_FILES = 10          # *.md under RULES_DIR (26 live on 2026-09-02)
MIN_FROZEN_DIRECTIVES = 5    # non-blank lines in each snapshot

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
    """Files a rule declared as an offload target, wherever they live.

    One repo-relative path per line in `<inventory_dir>/<stem>.destinations`;
    blank lines and `#` comments ignored. Absent file means no destinations,
    which is the common case and stays free.

    This said "Files OUTSIDE `.claude/rules/`" until 2026-09-02, describing what
    it was first written for rather than what it does. The code never restricted
    the location, and since `_rule_union_paths` stopped guessing sibling rule
    files by name shape, a split INSIDE `.claude/rules/` is declared here too.

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


def _rule_union_paths(stem: str, rules_dir=None,
                      inventory_dir: Path | None = None) -> list[Path]:
    """Every file whose sentences may retain `stem`'s frozen directives.

    Split out of `_rule_union_sentences` so `corpus_floor()` can report WHICH
    rule files the snapshots actually reach without reading them twice.
    """
    inventory_dir = INVENTORY_DIR if inventory_dir is None else inventory_dir
    rules_dir = RULES_DIR if rules_dir is None else rules_dir
    # The rule file itself, by EXACT name, plus whatever the snapshot declared as
    # a destination. Nothing is guessed.
    #
    # This was `glob(f"{rules_dir}/{base}*.md")`, meant to pick up a `-detail.md`
    # sibling after a split. A prefix glob cannot tell a successor from an
    # unrelated rule that happens to share an opening word, and MEASURED
    # 2026-09-02 it did not: the union for `documentation.md` pulled in
    # `documentation-style.md`, a separate always-on rule about writing style.
    #
    # The direction of that error is what makes it worth removing rather than
    # tolerating. Every other degradation in this module fails SAFE: a file
    # missing from the union can only make a frozen directive read as LOST, which
    # is a false positive routed to a human. A file wrongly ADDED to the union
    # fails the other way. It can certify a directive as retained because some
    # unrelated rule happens to contain the same sentence, and this gate exists to
    # notice exactly that kind of loss.
    #
    # It masks nothing today. Measured the same day over `documentation.md`'s 17
    # frozen directives: 0 were retained only via `documentation-style.md`, and 0
    # read as lost either way. So this is closing a hole, not fixing an outage.
    #
    # The cost is that a future split has to DECLARE its successor in
    # `<stem>.destinations` instead of being found by name shape. That is the
    # trade `_declared_destinations` already argues for in its own docstring: a
    # named file is a claim someone made and can be held to, and an undeclared
    # split now goes red rather than quietly widening the search.
    base = stem[:-3] if stem.endswith(".md") else stem
    core = Path(rules_dir) / f"{base}.md"
    paths = [core] if core.is_file() else []
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
    return paths


def _rule_union_sentences(stem: str, rules_dir=None,
                          inventory_dir: Path | None = None) -> set[str]:
    paths = _rule_union_paths(stem, rules_dir, inventory_dir)
    # Through `read_sources`: `paths` came from an `is_file()` test above,
    # and a rule file rewritten between that walk and this read raised
    # FileNotFoundError out of a gate that had found nothing wrong. SKIPPING is
    # the correct degradation here, and the module docstring above is the reason:
    # this gate fails SAFE, never dangerous. A successor file missing from the
    # union can only make a frozen directive read as LOST, which is the false
    # POSITIVE this design already accepts and routes to a human. It can never
    # certify a dropped directive as retained. The skip is announced --
    # `read_sources` warns naming the file -- so the human dismissing the
    # finding can see the corpus shrank.
    text = "\n".join(t for _, t in read_sources(paths))
    return {_norm(s) for s in _units(text) if _norm(s)}


def _anchor(p: str) -> Path:
    # A relative argument names a repo path, not a cwd path. `--snapshot
    # .claude/rules/voice.md` from anywhere but the root used to raise
    # FileNotFoundError; the same relative string in `--dump` did too.
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def snapshot_inventory(rule_path: str) -> Path:
    # Freeze the current extracted imperatives of one rule file. Written at compress/split
    # time so --check has a baseline to guard against a FUTURE edit that drops a directive.
    src = _anchor(rule_path)
    inv = INVENTORY_DIR / (src.name + ".txt")
    inv.parent.mkdir(parents=True, exist_ok=True)
    imps = extract_imperatives(src.read_text(encoding="utf-8"))
    inv.write_text("\n".join(imps) + "\n", encoding="utf-8")
    return inv


def corpus_floor(inventory_dir: Path | None = None,
                 rules_dir=None) -> tuple[list[str], dict]:
    """Refuse a `--check` that would pass by having read (almost) nothing.

    `check_inventories` globs `<dir>/*.txt` and returns `[]` when the glob is
    empty, which is byte-for-byte the answer it gives over a clean tree. A
    renamed directory, a snapshot deleted with the rule it guarded, a `.txt`
    convention that moves, or -- until 2026-09-02 -- simply running from a
    different working directory, each report "no directive was dropped" while
    reading nothing at all.

    So the corpus is derived from the tree and then held against a floor before
    any verdict is printed. Returns `(problems, counts)`; `problems` empty means
    the corpus is big enough for the verdict below it to mean something.
    `counts` is reported on every run, pass or fail, because `.claude/rules/
    scope-claims.md` asks a tool to state the coverage its method established:
    two snapshots reaching three of twenty six rule files is a true and narrow
    result, and printing only "OK" hides the narrowness rather than the result.
    """
    inventory_dir = INVENTORY_DIR if inventory_dir is None else Path(inventory_dir)
    rules_dir = RULES_DIR if rules_dir is None else Path(rules_dir)

    rule_files = sorted(rules_dir.glob("*.md"))
    snapshots = sorted(inventory_dir.glob("*.txt"))
    covered: set[Path] = set()
    thin: list[str] = []
    for snap, body in read_sources(snapshots):
        frozen = [ln for ln in body.splitlines() if ln.strip()]
        if len(frozen) < MIN_FROZEN_DIRECTIVES:
            thin.append(f"{snap.name} froze {len(frozen)} directive(s), "
                        f"floor is {MIN_FROZEN_DIRECTIVES}")
        for p in _rule_union_paths(snap.name[:-4], rules_dir, inventory_dir):
            if p.parent == rules_dir:
                covered.add(p)

    counts = {"rules_dir": rules_dir, "inventory_dir": inventory_dir,
              "rule_files": len(rule_files), "snapshots": len(snapshots),
              "covered_rule_files": len(covered)}

    problems = []
    if len(rule_files) < MIN_RULE_FILES:
        problems.append(f"read {len(rule_files)} rule file(s) under {rules_dir}, "
                        f"expected at least {MIN_RULE_FILES}")
    if len(snapshots) < MIN_SNAPSHOTS:
        problems.append(f"read {len(snapshots)} snapshot(s) under {inventory_dir}, "
                        f"expected at least {MIN_SNAPSHOTS}")
    problems.extend(thin)
    return problems, counts


def check_inventories(inventory_dir: Path | None = None,
                      rules_dir=None) -> list[tuple[str, str]]:
    inventory_dir = INVENTORY_DIR if inventory_dir is None else inventory_dir
    # For every snapshot, assert each frozen imperative is still an exact sentence of the
    # current core+detail union. Returns [(stem, dropped_line), ...]; empty = clean.
    bad = []
    # The SECOND walk-then-read in this gate, fixed in the same change as the one
    # in `_rule_union_sentences`: a snapshot listed by the glob and gone by the
    # read raised the identical FileNotFoundError out of the identical `--check`
    # run, and a fix that lands in one of two copies is the copy that stops being
    # fixed. Skipping loses that snapshot's coverage, which `read_sources`
    # announces by name; it cannot turn a dropped directive into a retained one.
    for inv, body in read_sources(sorted(Path(inventory_dir).glob("*.txt"))):
        stem = inv.name[:-4]  # strip ".txt"
        union = _rule_union_sentences(stem, rules_dir, Path(inventory_dir))
        for line in body.splitlines():
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
        problems, counts = corpus_floor()
        # Printed before the verdict, and on the failing path too, so the reader
        # never has to infer the corpus from a bare "OK".
        print(f"corpus: {counts['snapshots']} snapshot(s) under "
              f"{counts['inventory_dir']}; {counts['covered_rule_files']} of "
              f"{counts['rule_files']} rule file(s) under {counts['rules_dir']} "
              f"are covered by one")
        if problems:
            for p in problems:
                print(f"CORPUS FAIL: {p}")
            print("inventory check: REFUSED (corpus below floor; a pass here "
                  "would mean nothing)")
            return 2
        bad = check_inventories()
        for stem, line in bad:
            print(f"CHECK FAIL {stem}: dropped {line!r}")
        print("inventory check: OK" if not bad else f"inventory check: {len(bad)} dropped")
        return 1 if bad else 0
    if a.dump:
        imps = extract_imperatives(_anchor(a.dump).read_text(encoding="utf-8"))
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
