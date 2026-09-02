#!/usr/bin/env python3
"""Advisory audit: skill bash blocks passing bare data-class paths to scripts.

Engine/data separation (CLAUDE.md): every data artifact must resolve under the DATA
root via the get_*_dir() seam. The PreToolUse data-path-redirect hook rewrites
`@outputs/...` for Read/Write/Edit/Grep/Glob tool ops, but NOT for Bash. So a SKILL
that hands a bare `outputs/...` path to a Bash-invoked script (cwd = engine root) can
misroute a write into the engine clone -- the class flagged in auto-memory
`skill-data-paths-need-explicit-resolution`.

This is ADVISORY, not the guarantee. The authoritative, how-agnostic guarantee is
`tests/test_engine_tree_clean.py` (any data artifact landing in the engine clone fails,
regardless of how the write happened). This scanner is the earlier, narrower signal:
it surfaces SKILL bash lines that *could* misroute, so they can be reviewed.

The gate is a BASELINE RATCHET: it fails only when a SKILL gains a NEW bare-data-path
bash line beyond the frozen baseline, catching regressions without forcing churn on
examples that were reviewed and accepted.

A hit carrying a placeholder (YYYY-MM-DD, {sender-slug}) is NOT thereby illustrative.
The baseline was first frozen on that reasoning and it was wrong: nine of its ten
entries were live commands the skill instructs the agent to run, placeholder and all.
Triage a new hit by asking whether the line is executed, never by what it spells.

The corpus is every markdown file a skill ships, SKILL.md and `references/` alike,
because a reference file carries runnable bash the same way. It read only SKILL.md
until 2026-09-02; see `skill_files`.

Usage:
  python scripts/audit-skill-bash-paths.py            # list all candidates
  python scripts/audit-skill-bash-paths.py --check    # exit 1 if any skill exceeds baseline
  python scripts/audit-skill-bash-paths.py --json      # machine-readable

Exit codes: 0 clean, 1 a skill is over baseline (--check only), 2 the audit did not
read the corpus it covers and refuses to report a verdict at all.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET  # noqa: E402
from scripts.utils.repo_files import read_sources  # noqa: E402

# Data-class top-level dirs (mirror test_engine_tree_clean.DATA_DIRS).
_DATA = re.compile(r"\b(outputs|crm|knowledge|threads|plans|datastore|auto-memory)/")
# Tokens that prove the path was resolved through the seam (not a bare literal).
_RESOLVED = re.compile(r"get_\w+_dir|get_data_root|\$OUTPUTS_DIR|\$DATA_ROOT|\$\(.*get_")

# Options that designate a destination path, so a command carrying one is writing
# somewhere even when it invokes no script this scanner recognises.
#
# Enumerated from the corpus, not guessed. Counting the flags that appear inside
# bash-labelled fences across all 94 skills on 2026-08-30 gives: -o (10),
# --output (8), --out (5), --file (4), --out-dir (2), -f (2), --output-dir (2),
# --path (1). All eight are covered, and the list holds nothing else -- a flag
# with no use in the corpus would make the sentence above false, so `--dest` was
# dropped after the count came back zero for it.
#
# Deliberately NOT covered, and why: -c (29 uses, every one of them `python3 -c`,
# already matched by the `python \S` alternative), and -m, -C, -u, -v, -A, -p,
# which designate no destination. Adding a short flag costs precision, because a
# single letter collides across tools; each of these was read in context first.
# -f is in because both of its uses take a path, though `-f` means "force" in
# other tools -- the conjunction with `_DATA` and `not _RESOLVED` is what keeps
# that affordable.
#
# Longest-first, because Python alternation is leftmost-FIRST rather than
# leftmost-longest. The trailing lookahead happens to save the ordering today,
# but relying on that couples the tuple's order to a regex detail two lines
# below it.
_DEST_OPTS = ("--output-dir", "--out-dir", "--output", "--file", "--path",
              "--out", "-o", "-f")
# A bash command only counts if it invokes a script, redirects, or names a
# destination. `(?<!\S)` stops `-o` matching inside `--output`; `(?=[=\s]|$)`
# stops `--out` matching the front of `--output`.
_COMMAND = re.compile(
    r"python \S|scripts/|>\s|(?<!\S)(?:"
    + "|".join(re.escape(o) for o in _DEST_OPTS)
    + r")(?=[=\s]|$)"
)

# Frozen baseline of known illustrative candidates (skill -> count), captured
# 2026-06-16. A skill exceeding its baseline (or a new skill appearing) is a
# regression and fails --check. Lowering a baseline (cleaning a SKILL) is welcome;
# update the number here in the same change.
# `calibrate` was here at 1. Its only hit was `-> threads/{layer}/{slug}.md`,
# an arrow in a diagram inside an UNLABELLED fence, which the scanner used to
# treat as bash. It is not a command, so the entry went with the widening.
#
# Nine entries left on 2026-08-30, and the reason is a correction to the
# paragraph above. `ceo-intel`, `corporate-letter`, `dashboard`, `official-doc`,
# `osint`, `osint-advanced`, `partnership-doc`, `proposal` and `xpager` were
# frozen here as "illustrative template paths", on the strength of the
# `YYYY-MM-DD` and `{sender-slug}` placeholders in them. That read the
# placeholder as the whole claim. Every one of those lines was a live
# instruction the skill tells the agent to run, with the placeholder filled in
# at runtime; the five doctype skills documented a `render-doctype.py` call that
# could not execute at all, because the Write tool put `_work/data.json` in the
# DATA overlay and the Bash-resolved `--data` looked for it under the engine
# root. A placeholder in a path says nothing about whether the line is executed.
# All nine now resolve through `get_outputs_dir()` and are gone from the scan.
# EMPTY since 2026-08-31, and that is the point of a ratchet: the last two
# entries (`workspace-deep-audit`, both a live command the skill tells the agent
# to run) were fixed rather than kept, so ANY bare data path in ANY skill's bash
# now fails `--check` instead of being absorbed. Never add a key back to buy
# quiet. A frozen entry is a defect with a note on it.
BASELINE: dict[str, int] = {}


# The empty string used to be in here, so all 78 UNLABELLED fences across the
# skills were scanned as bash too -- output samples, diagrams, pasted diffs. The
# scanner said "bash blocks" and read anything. One diagram arrow was already
# counted as a misroute candidate and frozen into the baseline, and any new one
# would fail --check on content that is not a command at all. The authoritative
# guarantee is tests/test_engine_tree_clean.py, so the narrower scan loses
# nothing that matters.
_BASH_FENCES = ("bash", "sh", "shell")


def fence_language(stripped: str) -> str:
    """The lowered LANGUAGE of a fence line, which is the first token only.

    A fence is an opening delimiter plus an info string, and everything after
    the first token is metadata for whatever renders the block. This read
    ``stripped.strip("`").lower()`` and compared the WHOLE info string against
    `_BASH_FENCES`, so ```` ```bash linenos ```` produced `"bash linenos"`,
    matched nothing, and the scanner walked past every command in the block.

    MEASURED 2026-09-02 on one line, `python scripts/x.py -o outputs/a.png`:
    one hit inside ```` ```bash ````, zero inside ```` ```bash linenos ````.
    The command is identical; only the fence metadata differed. That is the one
    input that can silence this ratchet without tripping its coverage floor,
    since `Coverage.refusals` counts files OPENED and a file whose fences are
    all mislabelled is opened, read, and scored zero.

    The narrowing that dropped the empty string from `_BASH_FENCES` is
    untouched: an unlabelled fence still yields `""`, which is in no tuple.
    Taking the first token is deliberately not a prefix test, so `bashful` and
    `pythonbash` stay out.

    Shared with `tests/test_skill_bash_paths.py`, which reimplements fence
    selection to parse commands out of the corpus and carried a copy of the
    defective expression. The two are meant to read the same blocks; a mirror
    maintained by hand is one edit from being a copy that lags.
    """
    info = stripped.strip("`").split()
    return info[0].lower() if info else ""


def scan_skill(path: Path) -> list[tuple[int, str]] | None:
    """Return (lineno, command) candidates inside bash-LABELLED fences.

    `None` rather than `[]` when the file could not be read, so a caller can tell
    "opened it, found nothing" from "never opened it". The two used to be the same
    empty list, which is the shape that lets a corpus quietly shrink to nothing and
    still report a clean tree.

    Backslash continuations are joined into one logical command before matching,
    and the line number reported is the line the command STARTS on.

    That join is the whole point. Until 2026-08-30 this matched physical lines,
    so a command whose destination sat on a continuation line was split across
    two matches that each failed: the first line carried `python scripts/...`
    and no data path, the second carried the data path and nothing that looked
    like a command. Every multi-line invocation in the corpus has that shape, so
    the scanner reported a clean zero for skills that were demonstrably naming
    an engine-tree path. `--out` was in `_COMMAND` and `-o` was not, which is the
    only reason any of the five doctype skills scored above zero at all -- they
    were caught by the long form on one continuation line while the `--data`
    line beside it stayed invisible.

    The harm this detects is misrouting, not leaking. `.gitignore` carries
    `/outputs/`, so an engine-tree `outputs/` file cannot be pushed -- and for
    the same reason `tests/test_engine_tree_clean.py` and the push wall both
    walk past it. The artifact lands in the wrong tree and nothing else notices.
    """
    hits: list[tuple[int, str]] = []
    in_block = False
    cur_bash = False
    pending: str | None = None
    pending_line = 0
    # `errors="replace"`: this runs over every SKILL.md in the tree and drives a
    # `--check` gate, so one stray non-UTF-8 byte in any one of them used to
    # raise UnicodeDecodeError - a ValueError, caught nowhere on the path - and
    # take the whole audit down naming no file. A replaced byte cannot invent or
    # conceal a `scripts/...` path, which is all this scanner matches on.
    #
    # Through `read_sources`, and SKIPPING is the right answer here rather than
    # failing. `skill_files` walks the LIVE tree (see its docstring), several
    # agents share one checkout, and a SKILL.md created and deleted inside the
    # window between that glob and this read raised FileNotFoundError from
    # inside a gate that had found nothing wrong. A skill that is not on disk
    # cannot be over its baseline, so dropping it narrows the corpus without
    # changing any verdict this scanner reaches -- and `read_sources` WARNS
    # naming the file, so the narrowing is stated rather than hidden. `list()`
    # rather than a `for`-with-`return`: the warning fires when the generator is
    # exhausted, and an abandoned generator never warns.
    sources = list(read_sources([path], errors="replace"))
    if not sources:
        return None
    text = sources[0][1]
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_block:
                cur_bash = fence_language(stripped) in _BASH_FENCES
            in_block = not in_block
            pending = None  # a fence never continues across its own boundary
            continue
        if not (in_block and cur_bash):
            continue
        if pending is None:
            command, start = stripped, i
        else:
            command, start = f"{pending} {stripped}", pending_line
        if stripped.endswith("\\"):
            pending, pending_line = command.rstrip("\\").rstrip(), start
            continue
        pending = None
        if _COMMAND.search(command) and _DATA.search(command):
            if not _RESOLVED.search(command):
                hits.append((start, command))
    return hits


def skill_files(root: Path) -> list[Path]:
    """Every markdown file a skill ships, as its own assertable value.

    Split out of `scan_all` on 2026-09-01 because the corpus was unmeasurable
    from outside. `scan_all` returns only the skills that produced a hit, and
    `BASELINE` has been legitimately empty since 2026-08-31, so
    ``counts == BASELINE`` holds just as well over a corpus of ZERO FILES.
    MEASURED by pointing this glob at `.claude/skillz/`: both
    `test_no_new_skill_bash_data_path_misroutes` and
    `test_baseline_matches_current_corpus` stayed green, and so did the
    neighbouring `test_the_real_skill_tree_still_has_no_unlabelled_fence_hits`,
    which asks the same absence question. A ratchet that has stopped looking
    reports exactly what a clean tree reports.

    RECURSIVE since 2026-09-02, and that widening is a defect fix rather than a
    tidy-up. The glob read `.claude/skills/*/SKILL.md`, one level deep, so the 83
    files under `.claude/skills/*/references/` were never opened at all: MEASURED
    by wrapping `scan_skill` in a counter, the audit read 94 of the tree's 201
    markdown files and 0 of the references. A reference file carries runnable
    bash exactly as its SKILL.md does -- `notebooklm` puts three live `-o
    outputs/...` download commands in `references/mode-catalog.md`, and `docparse`
    puts three `datastore/...` invocations in `references/integration.md`. All six
    were invisible, and the gate printed OK over them for as long as it existed.

    The LIVE tree on purpose, not `git ls-files`: an unstaged new skill is the
    file most likely to carry a misroute, and "a new skill appearing" is one of
    the two regressions this gate exists to catch. Rooting the walk at
    `.claude/skills/` rather than `.claude/` keeps an agent worktree under
    `.claude/worktrees/` out of it, which is what the one-level glob used to buy.
    """
    return sorted((root / ".claude" / "skills").rglob("*.md"))


def skill_name(path: Path, root: Path) -> str:
    """The skill a file belongs to: the first path component under `.claude/skills/`.

    `path.parent.name` was right only while the corpus was one level deep. Over the
    widened corpus it keys every reference file as `references`, collapsing hits
    from unrelated skills into one bucket and making the per-skill ratchet report a
    skill name that does not exist.
    """
    return path.relative_to(root / ".claude" / "skills").parts[0]


def walk_markdown(root: Path) -> set[Path]:
    """Every `*.md` under `.claude/skills/`, walked WITHOUT `skill_files`'s glob.

    Deliberately a second implementation of the same question, and that duplication
    is the whole value: it is the number `skill_files` is checked against, so a glob
    that narrows (the defect this file was fixed for) shows up as a shortfall
    instead of shrinking the yardstick along with the measurement.
    """
    found: set[Path] = set()
    for dirpath, _dirnames, filenames in os.walk(root / ".claude" / "skills"):
        for name in filenames:
            if name.endswith(".md"):
                found.add(Path(dirpath) / name)
    return found


@dataclass
class Coverage:
    """What the audit actually opened, so a caller can refuse a pass over nothing.

    `tree` is the independent walk; `corpus` is what `skill_files` enumerated;
    `opened` is what was really read. The floor compares the first against the
    last, so it is stated in terms of the tree in front of it rather than an
    absolute count. An absolute floor was the first attempt and it was wrong in
    both directions: it refused a legitimate one-skill scratch tree (which is how
    `tests/test_a_gate_that_passed_the_plan_it_had_just_failed.py` drives the FAIL
    branch), and on a downstream fork carrying twenty skills it would refuse every
    run forever.
    """

    tree: set[Path] = field(default_factory=set)
    corpus: list[Path] = field(default_factory=list)
    opened: list[Path] = field(default_factory=list)
    unreadable: list[Path] = field(default_factory=list)

    @staticmethod
    def _classify(path: Path) -> str | None:
        if path.name == "SKILL.md":
            return "SKILL.md"
        if path.parent.name == "references":
            return "references"
        return None

    def missed(self) -> list[Path]:
        """Tree files never opened, excluding anything no longer on disk.

        The exclusion is the documented mid-walk race, not a loophole: several
        agents share one checkout, and a file created and deleted inside the run
        window is not a surface this audit failed to cover. A file still sitting
        there unopened is.
        """
        opened = {p.resolve() for p in self.opened}
        return sorted(p for p in self.tree
                      if p.resolve() not in opened and p.exists())

    def refusals(self) -> list[str]:
        """Every reason this run must not report a pass, each naming both numbers."""
        out: list[str] = []
        if not self.opened:
            out.append(
                f"opened 0 files of the {len(self.tree)} markdown file(s) under "
                ".claude/skills/; a pass over an empty corpus is not a pass")
            return out
        missed = self.missed()
        if missed:
            shown = ", ".join(str(p) for p in missed[:5])
            out.append(
                f"opened {len(self.opened)} of the {len(self.tree)} markdown "
                f"file(s) the skills tree holds; {len(missed)} never read, "
                f"e.g. {shown}")
        # Per class as well as over the union, because a union shortfall can be
        # small while one whole surface is gone: 94 SKILL.md out of 201 is a
        # 53-percent union figure and a 0-percent references figure, and it is the
        # second number that names the defect.
        for label in ("SKILL.md", "references"):
            in_tree = sum(1 for p in self.tree if self._classify(p) == label)
            was_read = sum(1 for p in self.opened if self._classify(p) == label)
            if was_read < in_tree and any(self._classify(p) == label for p in missed):
                out.append(
                    f"opened {was_read} of the {in_tree} {label} file(s) the tree "
                    "holds; the corpus stopped covering that surface")
        return out


def scan_corpus(root: Path) -> tuple[dict[str, list[tuple[str, int, str]]], Coverage]:
    """Scan every file in the corpus; return the hits per skill and the coverage.

    The independent walk is taken twice, bracketing the scan, and only their
    INTERSECTION becomes the yardstick. A file another agent creates or deletes
    while this runs appears in one walk and not the other, so it can neither
    manufacture a shortfall nor hide one.
    """
    before = walk_markdown(root)
    out: dict[str, list[tuple[str, int, str]]] = {}
    coverage = Coverage(corpus=skill_files(root))
    for path in coverage.corpus:
        hits = scan_skill(path)
        if hits is None:
            coverage.unreadable.append(path)
            continue
        coverage.opened.append(path)
        if hits:
            rel = path.relative_to(root).as_posix()
            out.setdefault(skill_name(path, root), []).extend(
                (rel, ln, text) for ln, text in hits)
    coverage.tree = before & walk_markdown(root)
    return out, coverage


def scan_all(root: Path) -> dict[str, list[tuple[str, int, str]]]:
    return scan_corpus(root)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if a skill exceeds its baseline")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = get_workspace_root()
    found, coverage = scan_corpus(root)
    counts = {name: len(h) for name, h in found.items()}

    # Refuse BEFORE reporting. A findings table drawn over a corpus that was never
    # read is the misleading artifact, not the exit code: `--json` printed
    # `{"counts": {}}` and the console printed a clean list, both indistinguishable
    # from a tree with nothing wrong in it. Exit 2, as scripts/ste-check.py and
    # scripts/validate-crm-schema.py do, so a setup failure is never a findings
    # verdict.
    refusals = coverage.refusals()
    if refusals:
        print(f"{RED}{BOLD}REFUSING{RESET} -- the skill bash data-path audit did not "
              f"read the corpus it claims to cover:", file=sys.stderr)
        for r in refusals:
            print(f"  {RED}{r}{RESET}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"counts": counts, "baseline": BASELINE,
                          "scanned": len(coverage.opened)}, indent=2))
    else:
        print(f"{BOLD}{CYAN}Skill bash data-path audit (advisory){RESET} "
              f"{GRAY}{len(coverage.opened)} markdown files{RESET}")
        for name, hits in found.items():
            base = BASELINE.get(name, 0)
            tag = f"{GREEN}=baseline{RESET}" if len(hits) <= base else f"{RED}OVER baseline ({base}){RESET}"
            print(f"\n{BOLD}{name}{RESET} [{len(hits)}] {tag}")
            for rel, ln, text in hits:
                print(f"  {GRAY}{rel}:{ln}:{RESET} {text[:110]}")
        print(f"\n{GRAY}Authoritative guarantee: tests/test_engine_tree_clean.py. This is advisory.{RESET}")

    # Regression check: any skill over baseline, or a new skill not in baseline.
    regressions = []
    for name, n in counts.items():
        base = BASELINE.get(name)
        if base is None:
            regressions.append(f"{name}: NEW skill with {n} bare-data-path bash line(s) (not in baseline)")
        elif n > base:
            regressions.append(f"{name}: {n} > baseline {base}")

    if args.check:
        if regressions:
            print(f"\n{RED}{BOLD}FAIL{RESET} -- new SKILL bash data-path misroute candidate(s):", file=sys.stderr)
            for r in regressions:
                print(f"  {RED}{r}{RESET}", file=sys.stderr)
            print(f"{YELLOW}Resolve via get_*_dir()/$OUTPUTS_DIR, or update BASELINE if intentional.{RESET}", file=sys.stderr)
            return 1
        # stderr, like the FAIL path two lines up. On stdout it landed AFTER the
        # --json document, so `--json --check | json.tool` died on trailing text.
        print(f"\n{GREEN}OK{RESET} -- no SKILL bash data-path regressions vs baseline.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
