#!/usr/bin/env python3
"""The exact strings the router-accuracy judge is sent, in one place.

A checker that rebuilds an outbound payload from its own idea of the sources is
green forever while the sender quietly starts sending something else. That is not
a hypothetical: the first draft of the egress-proof slice modelled the payload as
one concatenation of the source files, and the council pointed out that the wire
carries something different in shape. `judge_query` sends a SYSTEM prompt built
from a judge preamble, the target skill's description and the routing rules, and
then ONE user message per trigger case. A scan of the concatenation would have
missed the preamble and every user message while reporting the payload clean.

So the sender and the checker read from the same functions here. `skill-trigger-
test.py` cannot be imported (a hyphen is not a Python identifier), which is why
the shared half lives in a module and not in the script.

Everything below resolves from the ENGINE root. That is the claim the egress
proof rests on and `tests/test_egress_proof.py` asserts it against resolved
paths rather than against this sentence.

Consumed by: scripts/skill-trigger-test.py, scripts/router-accuracy-nightly.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator

from scripts.utils.markdown import FM_OK, split_frontmatter
from scripts.utils.workspace import get_workspace_root

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_RULE = ROOT / ".claude" / "rules" / "skill-router.md"
CATEGORY_DETAIL_DIR = ROOT / "reference" / "skill-router"

JUDGE_INSTRUCTION = (
    "You are a routing oracle for a Claude Code workspace. Below are the workspace's "
    "skill-routing rules. Given a user message and a TARGET skill, decide whether the "
    "rules would route that message to the TARGET skill (as its primary skill or compound "
    "entrypoint). Judge strictly by the rules and the target skill's own trigger description "
    "— not by what you personally think is reasonable.\n\n"
    "Reply with ONLY a compact JSON object, no prose:\n"
    '{"routes_to_target": true|false, "skill": "<skill you think fires, or none>", '
    '"reason": "<one short clause>"}'
)


# ============================================================
# Sources
# ============================================================


def router_rules_text() -> str:
    """The router rule text PLUS the per-category detail files.

    F-5.2 moved the exclusions/compound columns out of the always-on router rule
    into reference/skill-router/<category>.md. The judge builds its negative test
    cases from the documented exclusions, so it must see them: concatenate every
    detail file under a clear delimiter. Degrades to the rule alone if the detail
    dir is absent.
    """
    parts = [ROUTER_RULE.read_text(encoding="utf-8")]
    if CATEGORY_DETAIL_DIR.exists():
        for detail in sorted(CATEGORY_DETAIL_DIR.glob("*.md")):
            # Retried once, then REFUSED, and not the skip-and-warn of
            # `scripts.utils.repo_files.read_sources`.
            #
            # The absent-directory branch one line up is not a precedent for
            # skipping a file here. That one is a DECLARED degradation, decided
            # once, on a condition a reader can see: a public clone has no
            # detail directory and the judge is told so. A file that disappears
            # mid-glob produces an UNDECLARED partial payload -- one category's
            # exclusions silently missing from the system prompt -- and this
            # function's whole reason for existing is that "a checker that
            # rebuilds an outbound payload from its own idea of the sources is
            # green forever while the sender quietly starts sending something
            # else." Two calls straddling the race return different strings, so
            # `tests/test_egress_proof.py` would certify a payload that was
            # never sent, and the router-accuracy percentage would move for a
            # reason no reader could reconstruct.
            #
            # The retry recovers a writer's unlink-and-rewrite window and
            # nothing else; a file that is genuinely gone is still gone on the
            # second look.
            try:
                body = detail.read_text(encoding="utf-8")
            except FileNotFoundError:
                try:
                    body = detail.read_text(encoding="utf-8")
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        f"{detail} vanished between the walk and the read. The "
                        f"judge payload would silently lose that category's "
                        f"exclusions, so neither the accuracy figure nor the "
                        f"egress proof would be over the payload actually sent. "
                        f"Re-run once the tree is quiet."
                    ) from exc
            parts.append(
                f"\n\n=== {detail.stem.upper()} DETAIL (exclusions + compound) ===\n"
                f"{body}"
            )
    return "".join(parts)


def load_triggers(skill_dir: Path) -> list[dict]:
    """Return the list of trigger cases for a skill, or [] if it has none."""
    path = skill_dir / "triggers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array of cases")
    return data


def load_skill_description(skill_dir: Path) -> str:
    """Return the `description` frontmatter field of a skill's SKILL.md (best-effort).

    The fences come from the shared splitter. `text.find("\\n---", 3)` sat here
    and took any line merely BEGINNING with three dashes as the close, and
    `startswith("---")` took any line beginning with them as the open. MEASURED
    2026-08-28 against the shared splitter over six documents: a SKILL.md opening
    `---extra` was read here and REFUSED there, so the judge payload was built
    from a file the rest of the engine treats as having no frontmatter.

    The line scan below stays: it deliberately reads the RAW folded scalar rather
    than a YAML-parsed one, so the judge sees the author's own wording.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    fm, _body, kind = split_frontmatter(text)
    if fm is None or kind != FM_OK:
        return ""
    # Capture `description:` possibly spanning until the next top-level key.
    lines = fm.splitlines()
    desc_lines: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("description:"):
            capturing = True
            desc_lines.append(line.split(":", 1)[1].strip())
            continue
        if capturing:
            # Continuation lines are indented; a new top-level key ends the field.
            if line and not line[0].isspace():
                break
            desc_lines.append(line.strip())
    return " ".join(d for d in desc_lines if d).strip()


def payload_sources() -> list[Path]:
    """Every file whose bytes reach the judge, resolved.

    Named rather than inferred, and asserted by the contract to live inside the
    engine and outside the private overlay. A source added to the sender without
    being added here would escape the scan, which is why `outbound_texts` below
    is built from the same accessors the sender calls rather than from this list.
    """
    sources = [ROUTER_RULE]
    if CATEGORY_DETAIL_DIR.exists():
        sources.extend(sorted(CATEGORY_DETAIL_DIR.glob("*.md")))
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        triggers = skill_dir / "triggers.json"
        if not triggers.is_file():
            continue
        sources.append(triggers)
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            sources.append(skill_md)
    return sources


# ============================================================
# The wire
# ============================================================


def system_text(skill_name: str, skill_desc: str | None = None,
                rules: str | None = None) -> str:
    """The system prompt sent for `skill_name`, byte for byte.

    `rules` exists so `outbound_texts` can read the rule set ONCE across a
    24-skill run without restating this format string. Restating it would put the
    wire format in two places inside the module built to keep it in one, which is
    the drift this whole file exists to prevent.
    """
    if skill_desc is None:
        skill_desc = load_skill_description(SKILLS_DIR / skill_name)
    desc = skill_desc or "(no description frontmatter found)"
    return (
        f"{JUDGE_INSTRUCTION}\n\n"
        f"=== TARGET SKILL ===\n/{skill_name}\nDescription: {desc}\n\n"
        f"=== WORKSPACE SKILL-ROUTING RULES ===\n"
        f"{router_rules_text() if rules is None else rules}"
    )


def user_text(query: str, target: str) -> str:
    """The user message sent for one trigger case, byte for byte."""
    return (
        f"User message: {query!r}\n"
        f"Does this route to /{target}? Answer with the JSON object only."
    )


def outbound_texts() -> Iterator[str]:
    """Every string a full `--all` run would send: one system prompt per skill
    with a corpus, and one user message per case in it.

    A generator so the caller decides whether to hold the whole run in memory.
    Skills without a corpus are skipped exactly as the runner skips them, so the
    set is what is SENT rather than what could be.
    """
    rules = router_rules_text()
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        cases = load_triggers(skill_dir)
        if not cases:
            continue
        name = skill_dir.name
        yield system_text(name, load_skill_description(skill_dir), rules)
        for case in cases:
            yield user_text(case["query"], name)


def dirty_sources() -> list[str]:
    """Payload sources git reports as modified, staged, or untracked.

    The egress argument is not "we scanned it" — a denylist knows only the
    entities the overlay happens to carry. It is "this content already passed the
    content-leak wall that decides whether a file may become public, and it has
    not changed since". A source with uncommitted edits breaks the second half,
    so it is named and the proof refuses.

    Returns [] when git cannot answer. That direction is deliberate: the caller
    treats an unbuildable denylist as unverifiable already, and a git failure
    must not become a second, silent way to refuse forever.
    """
    rel = sorted({str(p.relative_to(ROOT)) for p in payload_sources()
                  if p.is_relative_to(ROOT)})
    if not rel:
        return []
    # `-z` and `--no-renames`, because the refusal has to NAME a file the
    # operator can open.
    #
    # MEASURED 2026-08-30, both halves, because only one of them was real:
    #   * The C-quoting IS reachable. Without `-z`, `git status --porcelain`
    #     emits a path needing quoting with its escapes intact - a file named
    #     `od"un.md` arrives as `"ref/od\"un.md"` - and `.strip().strip('"')`
    #     peeled the outer quotes and left the backslash, naming a path that
    #     does not exist. `-z` emits the bytes verbatim and needs no unquoting.
    #   * The rename form is NOT reachable HERE. `git status --porcelain`
    #     does report `R  old -> new`, but only when the pathspec covers BOTH
    #     sides; this call passes the declared sources as explicit paths, and a
    #     pathspec matching one side collapses the record to a plain `D old` or
    #     `A new`. `--no-renames` is kept anyway, so the guarantee holds if the
    #     pathspec ever widens to a directory.
    # The `.strip()` goes with them: it would re-corrupt exactly the
    # leading/trailing-whitespace names `-z` exists to deliver intact.
    #
    # And the output is decoded from BYTES, not read through subprocess text
    # mode, because `-z` reaches only the quoting half. Text mode turns on
    # universal newlines, rewriting every CR byte to LF, and `subprocess` has no
    # `newline=` knob to switch it off. MEASURED 2026-08-30: two tracked files
    # differing only by that byte come back as two records in bytes mode and as
    # one under `text=True`. The refusal has to NAME a file the operator can
    # open, and a translated name is not that file.
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--no-renames", "--", *rel],
        cwd=str(ROOT), capture_output=True, check=False,
    )
    if proc.returncode != 0:
        return []
    decoded = proc.stdout.decode("utf-8", "surrogateescape")
    dirty: list[str] = []
    for record in decoded.split("\0"):
        if len(record) > 3:
            dirty.append(record[3:])
    return dirty
