#!/usr/bin/env python3
"""Five `Bash(...)` grants nothing in this workspace ever used, one of them a shell.

A skill's `allowed-tools` is a GRANT and never a limit: it pre-authorises a
command so the operator is not asked, and it forbids nothing. So a grant for a
command the skill never runs buys no capability at all and costs the whole
pre-authorisation. It is attack surface with no upside.

`.claude/skills/ast-grep/SKILL.md` carried `Bash(sg:*)`, vendored from upstream,
where `sg` is ast-grep's own short alias. On Ubuntu `/usr/bin/sg` is a DIFFERENT
program, from the `login` package:

    NAME
           sg - execute command as different group ID
    DESCRIPTION
           The sg command works similar to newgrp but accepts a command. The
           command will be executed with the /bin/sh shell.

MEASURED 2026-09-02 on this machine: `ast-grep` was not installed, `command -v
sg` resolved to `/usr/bin/sg`, and `sg <group> -c "<command>"` ran. The grant
was a general command runner. Nothing in that skill or its two reference files
ever invoked `sg`, so removing it cost nothing.

Sweeping the same property across all 94 skills found four more grants no skill
text ever invokes, three of which reach the network or run commands:

    playwright             Bash(npx:*)     fetches and executes a package
    workspace-deep-audit   Bash(find:*)    `-exec` runs commands
    workspace-deep-audit   Bash(pip:*)     installs from the network
    workspace-deep-audit   Bash(ls:*)      harmless, and equally unused

All five are removed. This test keeps them gone and catches the next one.

## What this test does NOT claim

It does not judge whether a granted command is dangerous. `Bash(python3:*)` is a
general command runner too, and 79 skills grant it deliberately, because every
tool in this workspace is a Python script. Ranking commands by danger would mean
a hand-maintained danger list, and a hand-maintained security list falls behind
silently. The checkable property is narrower and needs no such list: **a grant
must be for a command the skill actually invokes.** That property is derived
from the tree on every run, it caught `sg` on its own, and it cannot rot.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

SKILLS = ROOT / ".claude" / "skills"

#: The interpreter family. Every script here is Python, and a skill routinely
#: grants `python3` while its body writes `python scripts/x.py` or
#: `.venv/bin/python scripts/x.py`. That naming mismatch is a real inconsistency
#: and a separate question; it is not an unused grant, so treating it as one
#: would bury the signal under 29 false positives. Measured 2026-09-02: 29 of
#: the 32 raw hits were exactly this.
INTERPRETERS = frozenset({"python", "python3"})

#: A grant that IS unused and stays anyway. Each entry must carry the reason AND
#: what it costs, so the register can be argued with rather than trusted. Empty
#: today, and an empty register is the honest state: all five were removable.
ACCEPTED: dict[tuple[str, str], str] = {}


def _skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _frontmatter(text: str) -> str:
    """The YAML block between the first two `---` lines, or empty."""
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _grants(skill: Path) -> list[tuple[str, str]]:
    """[(full grant text, the command it names)] from `allowed-tools`."""
    line = re.search(r"^allowed-tools:\s*(.+)$", _frontmatter(
        skill.read_text(encoding="utf-8")), re.M)
    if not line:
        return []
    out = []
    for grant in re.findall(r"Bash\(([^)]*)\)", line.group(1)):
        cleaned = grant.strip().strip("\"'")
        command = cleaned.split(":")[0].strip().split()[0] if cleaned else ""
        if command:
            out.append((cleaned, command))
    return out


def _corpus(skill: Path) -> str:
    """Everything the skill ships: its body plus every reference file.

    A reference file that disappears between the glob and the read is a hard
    failure here, not a quiet skip, and the direction of the error is why. This
    corpus is the EVIDENCE that a grant is used. Reading one file fewer cannot
    clear a skill; it can only make a grant that IS invoked look unused, so the
    sweep would accuse a skill of a defect it does not have. `read_sources`
    survives the race and names what it dropped; this raises on anything it
    dropped rather than narrowing the evidence in silence.
    """
    vanished: list[Path] = []
    text = skill.read_text(encoding="utf-8")
    refs = sorted((skill.parent / "references").glob("*.md"))
    for _, body in read_sources(refs, vanished):
        text += "\n" + body
    if vanished:
        raise AssertionError(
            f"{skill.relative_to(ROOT)}: reference file(s) vanished between the "
            f"walk and the read, so this skill's evidence is incomplete and a "
            f"grant it does invoke could be reported unused: "
            f"{[str(p) for p in vanished]}")
    return text


def _invokes(corpus: str, command: str) -> bool:
    """Does the skill's own text ever run `command`?

    Bounded on both sides so `sg` does not match inside `using`, and so a path
    like `/usr/bin/sg` still counts. The `allowed-tools` line itself is stripped
    by the caller, because a grant citing itself is the circular evidence this
    whole test exists to refuse.
    """
    return re.search(rf"(?<![\w./-]){re.escape(command)}(?=[\s'\"`;|)]|$)",
                     corpus, re.M) is not None


def _without_the_grant_line(corpus: str) -> str:
    return re.sub(r"^allowed-tools:.*$", "", corpus, flags=re.M)


# ============================================================
# The floor
# ============================================================

def test_the_walk_finds_a_real_corpus_of_skills_and_grants():
    """A pass over an empty corpus is not a pass.

    Both floors are set well below the live counts, so adding a skill never
    turns this red; they exist so a glob that stops matching fails loudly
    instead of certifying everything.
    """
    skills = _skill_files()
    assert len(skills) >= 60, (
        f"only {len(skills)} SKILL.md found under {SKILLS}; the walk is broken, "
        "not the tree")
    granted = sum(len(_grants(s)) for s in skills)
    assert granted >= 50, (
        f"only {granted} Bash grant(s) parsed across {len(skills)} skills; the "
        "frontmatter parser has stopped reading them")


# ============================================================
# The property
# ============================================================

def test_no_skill_grants_a_command_its_own_text_never_invokes():
    unused = []
    for skill in _skill_files():
        corpus = _without_the_grant_line(_corpus(skill))
        for grant, command in _grants(skill):
            if command in INTERPRETERS:
                continue
            if _invokes(corpus, command):
                continue
            key = (skill.parent.name, command)
            if key in ACCEPTED:
                continue
            unused.append(f"{skill.parent.name}: Bash({grant}) is granted and "
                          f"`{command}` appears nowhere in the skill")
    assert not unused, (
        "a grant pre-authorises a command and forbids nothing, so an unused "
        "one is attack surface with no upside. Remove it, or add it to "
        "ACCEPTED with the reason AND what it costs:\n  "
        + "\n  ".join(sorted(unused)))


def test_the_check_can_actually_see_an_unused_grant(tmp_path):
    """The positive case, so the test above cannot pass over a broken reader.

    Without this, an `_invokes` that always returned True would leave the sweep
    green and look identical to a clean tree.
    """
    skill = tmp_path / "invented" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        '---\nname: invented\nallowed-tools: "Bash(nevercalled:*)"\n---\n'
        "# Body that runs something else entirely\n\n```bash\nast-grep run\n```\n",
        encoding="utf-8")

    grants = _grants(skill)
    assert grants == [("nevercalled:*", "nevercalled")]
    assert not _invokes(_without_the_grant_line(_corpus(skill)), "nevercalled")
    assert _invokes(_without_the_grant_line(_corpus(skill)), "ast-grep")


def test_a_grant_cannot_vouch_for_itself(tmp_path):
    """`Bash(sg:*)` on the allowed-tools line is not evidence that sg is used.

    This is exactly how the founding case would have hidden: the ONLY occurrence
    of `sg` in the whole ast-grep skill was the grant naming it.
    """
    skill = tmp_path / "selfvouching" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        '---\nname: selfvouching\nallowed-tools: "Bash(sg:*)"\n---\n'
        "# Nothing here runs it\n", encoding="utf-8")

    assert not _invokes(_without_the_grant_line(_corpus(skill)), "sg"), (
        "the grant line was counted as evidence for itself")


# ============================================================
# The five, pinned by name
# ============================================================

@pytest.mark.parametrize("skill_name,command,why", [
    ("ast-grep", "sg",
     "/usr/bin/sg on Ubuntu runs a command under another group through /bin/sh"),
    ("playwright", "npx",
     "npx fetches and executes a package from the network at call time"),
    ("workspace-deep-audit", "find", "find -exec runs arbitrary commands"),
    ("workspace-deep-audit", "pip", "pip installs from the network"),
    ("workspace-deep-audit", "ls", "unused, and removed with the other three"),
])
def test_the_five_removed_grants_stay_removed(skill_name, command, why):
    skill = SKILLS / skill_name / "SKILL.md"
    assert skill.is_file(), f"{skill} is gone; update this test with it"
    granted = {c for _, c in _grants(skill)}
    assert command not in granted, (
        f"Bash({command}:*) is back in {skill_name}. It was removed on "
        f"2026-09-02 because {why}, and because the skill never invoked it.")


def test_the_ast_grep_skill_checks_for_its_binary_before_using_it():
    """Router `auto` plus an absent binary is a skill that routes and then fails.

    `command -v sg` resolving to the wrong program is why the preflight has to
    name `ast-grep` and nothing else.
    """
    text = (SKILLS / "ast-grep" / "SKILL.md").read_text(encoding="utf-8")
    assert "command -v ast-grep" in text, (
        "the ast-grep skill must check for its own binary before drafting a "
        "rule; every command it prints fails without it")
    assert re.search(r"command -v sg", text) is None or "Never accept" in text, (
        "if the skill mentions `command -v sg` it must be to REFUSE it as "
        "evidence, never to use it as the check")
