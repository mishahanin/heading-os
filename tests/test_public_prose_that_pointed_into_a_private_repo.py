#!/usr/bin/env python3
"""Public engine prose must not send its reader into the private overlay.

Two guards, both found by the 2026-08-27 audit shard.

**One: no `plans/` citation.** Every Markdown file tracked in this repository is
public. A plan is not: `plans/` is gitignored here and routes `private`, and the
operator's plans are archived in a repository that no reader of this one will
ever have. Eight citations were live when this file was written, and the reason
none of them had ever been caught is worth stating, because it is a hole in two
guards at once:

  - `scripts/check-path-references.py` extracts paths with a regex whose
    top-level whitelist is `scripts|config|docs|reference|tests|examples|
    templates|.claude|.github`. `plans/` is not in it, so no citation to a plan
    is ever EXTRACTED.
  - Even if it were, that tool skips any path routing non-`engine` ("lives in an
    overlay this tool cannot see") and then drops anything gitignored. `plans/`
    is both.

Three of the eight pointed at `plans/2026-06-26-retire-workspace-sync-disk-import.md`
and three more at `plans/2026-04-19-sentinel-integration-tests.md`; both files
had moved to `plans/archive/2026/` in the overlay, so they were broken for the
operator as well, not only for the public.

**Two: no orphan corpus under `tests/integration/fixtures/`.** Three JSON files
there were reachable only through conftest fixtures that no test requested. An
unread corpus contributes no assertion while reading as coverage, and the
directory's README listed all three under "Test Coverage".

**What this guard does NOT cover, stated rather than left to be inferred.** The
corpus is tracked MARKDOWN only. Every tracked file in this repository is public,
not only the `.md` ones, and the same citations are live outside the scan.
MEASURED 2026-09-01 over `git ls-files`: 39 tracked non-Markdown files carry a
`plans/` citation, and 23 of those carry one in PROSE (a docstring or a comment)
rather than as fixture data. Three of those 23 cite the very two plan files named
above as offenders this guard closed (two the retire-workspace-sync one, one the
sentinel-integration-tests one), so the fix landed in one file type of two.

It was not widened here, and the reason is scope rather than judgement: the fix
is 23 files across territory other work is holding, and separating a pointer
("see `plans/x.md`") from invented fixture data (`plans/2026-06-28-foo.md`,
`plans/no-such-plan-exists.md`) needs a per-site reading, not a regex. The
prose-versus-data split above is the discriminator that worked when measured;
whoever widens this should start there. Until then, read the green result below
as "no Markdown page points at a plan", never as "no public page does".
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PLAN_CITATION = re.compile(
    r"(?<![\w/.-])(plans/[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.[a-z]{2,9})"
)

# Frozen, with the reason each one is prose rather than a pointer. An entry here
# is a claim that the text does NOT ask the reader to open the file.
ILLUSTRATIVE = {
    (".claude/skills/implement/SKILL.md",
     "plans/2026-01-28-add-guest-research-command.md"):
        "an `e.g.` showing the shape of the argument; no such plan has ever existed",
    (".claude/skills/scrutinize/references/target-detection.md",
     "plans/2026-05-27-r12-trajectory-evaluation.md"):
        "inside a fenced transcript demonstrating a /implement then /scrutinize run",
}


def _tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "*.md"],
                         cwd=str(ROOT), capture_output=True, text=True)
    assert out.returncode == 0, f"git ls-files failed: {out.stderr[:400]}"
    return [f for f in out.stdout.split("\0") if f]


def test_the_citation_pattern_still_recognises_a_plan_citation():
    """A guard whose detector matches nothing is green and blind.

    Every other assertion in this file is "no offenders found", which stays true
    when the pattern is broken. This one shows the pattern on known input, in
    both the shapes the eight real citations came in, and on a line that must
    NOT match.
    """
    backticked = "gone (see `plans/2026-06-26-retire-workspace-sync-disk-import.md`)."
    bare = "User: /implement plans/2026-05-27-r12-trajectory-evaluation.md"
    assert _PLAN_CITATION.findall(backticked) == [
        "plans/2026-06-26-retire-workspace-sync-disk-import.md"]
    assert _PLAN_CITATION.findall(bare) == [
        "plans/2026-05-27-r12-trajectory-evaluation.md"]
    # The archive lives in the overlay too, but it is a different path and the
    # guard must still see it rather than treat it as an escape hatch.
    assert _PLAN_CITATION.findall("see `plans/archive/2026/x.md`") == [
        "plans/archive/2026/x.md"]
    # A word ending in "plans/" is not a path.
    assert _PLAN_CITATION.findall("the buildplans/thing.md folder") == []


def test_no_public_markdown_points_a_reader_at_a_plan():
    files = _tracked_markdown()
    # Floor. A scan over an empty file list is green and means nothing.
    assert len(files) >= 200, (
        f"only {len(files)} tracked markdown files; the guard measured nothing"
    )

    offenders = []
    for rel in files:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # unreadable is a finding, not a skip
            offenders.append(f"{rel}: unreadable ({exc})")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for path in _PLAN_CITATION.findall(line):
                if (rel, path) in ILLUSTRATIVE:
                    continue
                offenders.append(f"{rel}:{lineno}: cites {path}")

    assert not offenders, (
        "public engine prose points its reader at the private plans tree. "
        "State the date and the decision inline instead, or add the site to "
        "ILLUSTRATIVE with the reason it is prose:\n  "
        + "\n  ".join(offenders)
    )


def test_every_illustrative_exemption_still_matches_something():
    """An exemption that matches nothing is a claim nobody re-reads.

    Without this, a renamed file or a rewritten line leaves a frozen entry that
    quietly widens the guard forever.
    """
    files = set(_tracked_markdown())
    stale = []
    for (rel, path), reason in ILLUSTRATIVE.items():
        if rel not in files:
            stale.append(f"{rel} is no longer tracked ({reason})")
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if path not in text:
            stale.append(f"{rel} no longer cites {path} ({reason})")
    assert not stale, "ILLUSTRATIVE has entries that match nothing:\n  " + \
        "\n  ".join(stale)


def test_no_integration_fixture_goes_unread():
    """Every corpus file under tests/integration/fixtures/ must be named by code."""
    fixtures_dir = ROOT / "tests" / "integration" / "fixtures"
    corpus = sorted(p for p in fixtures_dir.iterdir() if p.is_file())
    assert len(corpus) >= 4, (
        f"only {len(corpus)} fixture file(s) found under {fixtures_dir}; the "
        "guard measured almost nothing"
    )

    sources = list((ROOT / "tests" / "integration").rglob("*.py"))
    assert len(sources) >= 5, (
        f"only {len(sources)} python file(s) under tests/integration/; the "
        "guard measured almost nothing"
    )

    # Code only. A docstring or a comment that MENTIONS a corpus file is not a
    # test that reads it, and searching raw text let exactly that hide an
    # orphan: `conftest.py` names `fixtures/sample_emails.json` in its module
    # docstring, so a mutation splitting the load call's literal into
    # `"sample_emails" + ".json"` left the file unread and the guard green.
    literals = set()
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef,
                                        ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value not in docstrings):
                literals.add(node.value)
    blob = "\n".join(sorted(literals))

    orphans = [p.name for p in corpus if p.name not in blob]
    assert not orphans, (
        "these fixture files are read by no test and contribute no assertion. "
        "Wire them into a test or delete them; an unread corpus reads as "
        f"coverage and is not: {orphans}"
    )
