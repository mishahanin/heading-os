"""Three documentation claims, each derived from the thing it describes.

All three found by the 2026-08-23 engine audit, all three the same shape: a
sentence in a document restating a fact that lives in code or config, with
nothing comparing the two. The durable fix is never to correct the sentence --
it is to derive one side from the other, so the next change moves both.

A fourth claim of the same shape has been retired rather than fixed.
`docs/HOOKS-REFERENCE.md` listed `memory-inject.py` in the SessionStart table as
a wired hook while the `recall-inject.py` row three lines down said it "defaults
off", and the guard here derived the caveat from `inject.enabled` in
`config/memory-index.yaml`. On 2026-09-01 the operator deleted the hook, the
config block, and the table row, so there is no longer a claim on either side to
compare. The guard went with them: a doc-versus-config check over two things
that no longer exist measures nothing.

2. `docs/PLUGINS.md` referred to "the `heading-crm` skills" in code
   formatting, next to a list of four real bundles. `heading-crm` is declared
   in `config/plugin-bundles.yaml` with `skills: []`, and
   `scripts/dev/build-plugins.py --all` skips placeholders, so nothing by that
   name is installable. A reader searches the marketplace and finds nothing.

3. `docs/DESIGN-CHECK.md` closed an `audit-skip` region with live prose on the
   same line as `-->`. Under CommonMark an HTML comment BLOCK runs to the end
   of the line carrying `-->`, so that sentence was emitted as raw text
   OUTSIDE the paragraph and the paragraph split in two. Verified with
   markdown-it (CommonMark) on 2026-08-23; Python-Markdown, which builds this
   site, happens to survive it, so the two published renderings of the same
   source disagreed. An inline `<!-- x --> text <!-- /x -->` pair inside a
   paragraph is a different construct and stays allowed.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from tests.repo_files import read_sources, tracked_paths

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

_NUMBER_WORD = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


# --- 1. the generator's mode count -------------------------------------------

def _documented_modes() -> tuple[int, list[str]]:
    text = (DOCS / "DOCS-PIPELINE.md").read_text(encoding="utf-8")
    m = re.search(r"single docs generator\. It has (\w+) modes:", text)
    assert m, "the 'It has N modes' sentence moved; this guard is unanchored"
    stated = _NUMBER_WORD[m.group(1).lower()]
    rows = []
    for line in text[m.end():].splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if rows:
                break
            continue
        if set(s) <= set("| -"):
            continue
        cell = s.strip("|").split("|")[0].strip()
        rows.append(cell)
    return stated, rows[1:]  # drop the header row


def test_the_mode_count_matches_its_own_table():
    stated, rows = _documented_modes()
    assert stated == len(rows), (
        f"DOCS-PIPELINE says {stated} modes over a {len(rows)}-row table: {rows}"
    )


def test_the_mode_table_matches_the_generator_cli():
    """The table is only right if it names the flags argparse defines."""
    _, rows = _documented_modes()
    documented = {f for row in rows for f in re.findall(r"--[a-z-]+", row)}
    src = (ROOT / "scripts" / "regenerate-docs-html.py").read_text(encoding="utf-8")
    defined = set(re.findall(r'add_argument\("(--[a-z-]+)"', src))
    defined.discard("--quiet")          # a modifier, not a mode
    assert documented == defined, (
        f"the modes table and the CLI disagree.\n"
        f"  documented but not defined: {sorted(documented - defined)}\n"
        f"  defined but not documented: {sorted(defined - documented)}"
    )


# --- 2. no docs page names a plugin bundle that is not built -----------------

def _shipped_bundles() -> set[str]:
    cfg = yaml.safe_load((ROOT / "config" / "plugin-bundles.yaml").read_text(encoding="utf-8"))
    return {name for name, b in (cfg.get("bundles") or {}).items()
            if (b or {}).get("skills") or (b or {}).get("hooks")}


def _declared_bundles() -> set[str]:
    cfg = yaml.safe_load((ROOT / "config" / "plugin-bundles.yaml").read_text(encoding="utf-8"))
    return set((cfg.get("bundles") or {}).keys())


def test_the_bundle_registry_has_both_kinds():
    """Otherwise the check below is vacuous."""
    shipped, declared = _shipped_bundles(), _declared_bundles()
    assert shipped, "no bundle declares skills or hooks"
    assert declared - shipped, "no placeholder bundle exists; this guard proves nothing"


def test_plugins_page_does_not_present_a_placeholder_as_installable():
    placeholders = _declared_bundles() - _shipped_bundles()
    text = (DOCS / "PLUGINS.md").read_text(encoding="utf-8")
    bad = []
    inspected = 0
    for name in sorted(placeholders):
        for n, line in enumerate(text.splitlines(), 1):
            if f"`{name}`" not in line:
                continue
            inspected += 1
            # Naming it is fine; naming it without saying it is not shipped is not.
            if not re.search(r"no (?:crm )?bundle|reserved|not built|skips it|placeholder",
                             line, re.I):
                bad.append(f"PLUGINS.md:{n}: names `{name}` as if it shipped")
    # Measured 1 on 2026-08-26 (one PLUGINS.md line naming a placeholder bundle),
    # so the floor is 1. If the backtick containment test at the top of the loop
    # stopped matching (a rename of the bundle, a change in how PLUGINS.md quotes
    # it, or the page dropping the mention), every line would be skipped, `bad`
    # would be empty, and this guard would pass while reading nothing.
    assert inspected >= 1, f"no PLUGINS.md line named a placeholder bundle: {inspected}"
    assert not bad, (
        "the plugins page names a bundle the build skips:\n" + "\n".join(bad)
        + f"\n(placeholders in config/plugin-bundles.yaml: {sorted(placeholders)})"
    )


# --- 3. a block-level audit-skip marker owns its line ------------------------

_MARKER = re.compile(r"<!--\s*audit-skip-(?:start|end)\s*-->")


def _markdown_files() -> list[Path]:
    return tracked_paths(
        ("docs/*.md", "reference/*.md", ".claude/rules/*.md", "*.md"))


def _read_marker_file_or_fail(path: Path) -> str:
    """Read one markdown file, or fail naming it. Not a skip.

    The two functions below share a corpus and part company here on purpose.
    This one produces a TOTAL, and a total taken over a corpus that shrank is a
    smaller number reported as the real one - the reader is then told the marker
    pattern rotted when what happened is that a file went missing. The sweep in
    `test_a_block_level_audit_skip_marker_is_alone_on_its_line` hunts offenders
    instead, and a file that is gone holds no offending line, so that one skips
    through `read_sources` and says so.

    Retried once, in case the miss landed in a rewrite window.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        pass
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        raise AssertionError(
            f"{path} vanished between the walk and the read, so this count is "
            f"not the corpus total it is about to be compared against"
        ) from None


def test_the_marker_guard_has_markers_to_check():
    hits = sum(len(_MARKER.findall(_read_marker_file_or_fail(p)))
               for p in _markdown_files())
    assert hits >= 4, f"only {hits} audit-skip markers found; the pattern rotted"


def test_a_block_level_audit_skip_marker_is_alone_on_its_line():
    bad = []
    inspected = 0
    # A SCAN for offending lines, so a file that vanished between the walk and
    # the read is skipped with a warning naming it: it holds no line to offend.
    # The `inspected` floor below is over lines actually read, so a corpus that
    # shrank underneath the sweep still trips it.
    vanished = []
    for path, text in read_sources(_markdown_files(), vanished):
        for n, line in enumerate(text.splitlines(), 1):
            if not _MARKER.match(line.lstrip()):
                continue                      # inline pair mid-paragraph: allowed
            inspected += 1
            if _MARKER.sub("", line).strip():
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    # Measured 16 block-level marker lines on 2026-08-26, floored at 10 so
    # retiring an audit-skip region does not fail this test. If the
    # `_MARKER.match(line.lstrip())` predicate stopped matching (a rotted
    # pattern, a marker syntax change), every line would be skipped, `bad`
    # would stay empty, and the guard would pass having read nothing.
    assert inspected >= 10, (
        f"only {inspected} block-level marker lines inspected "
        f"({len(vanished)} file(s) vanished mid-walk)")
    assert not bad, (
        "prose shares a line with a block-level audit-skip marker. Under "
        "CommonMark the comment block runs to the end of that line, so the "
        "prose lands outside the paragraph and the paragraph splits:\n"
        + "\n".join(bad)
    )
