"""A rule that claimed fifteen skills ran its audit, when none ever had.

`.claude/rules/visual-design-discipline.md` said, of fifteen named skills, that
"the producing skill is responsible for running the audit before declaring the
artifact done". `scripts/visual-discipline-check.py` said the same thing about
itself, calling them "the fifteen skills that already call it".

Both were false from the day they were written:

    $ grep -rl "visual-discipline-check" .claude/skills/
    $                                     # nothing, under every spelling

The only thing that fails on a visual finding is the CI ratchet on `docs/`. A
reader of the rule would have believed fifteen enforcement points existed where
there were zero, which is the exact defect `.claude/rules/scope-claims.md` is
for.

This binder does not compare prose to a list a human typed here; a typed list
rots the same way the claim did. It lifts the verification command OUT of the
rule, RUNS it, and requires the answer to match what the rule says next to it.
So it fails in both directions:

  * if someone re-asserts skill-side enforcement while no skill calls the
    checker, and
  * if someone wires a skill up while the rule still says none is wired.

Either way the rule and the tree are dragged back into agreement.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RULE = REPO / ".claude" / "rules" / "visual-design-discipline.md"
CHECKER = REPO / "scripts" / "visual-discipline-check.py"
SKILLS = REPO / ".claude" / "skills"

# The token every spelling of a call to the checker must contain. The script is
# named `visual-discipline-check.py`; a caller may write it with or without the
# `scripts/` prefix, with or without `.py`, or import it, but it cannot invoke
# it without naming it.
CHECKER_NAME = "visual-discipline-check"


def _skill_text_files() -> list[Path]:
    """Every text file under .claude/skills/, walked from disk."""
    out = []
    for path in SKILLS.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        out.append(path)
    return out


def count_skill_callers(skills_root: Path) -> tuple[int, list[str]]:
    """Return (caller_count, sorted skill names) that name the checker.

    Taken as an argument rather than read from a module global so the negative
    case below can hand it a tree that DOES contain a caller. A walk that has
    never once returned a non-zero answer is not a measurement.
    """
    callers = set()
    scanned = 0
    for path in skills_root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if CHECKER_NAME in text:
            rel = path.relative_to(skills_root)
            callers.add(rel.parts[0] if rel.parts else str(rel))
    return scanned, sorted(callers)


def test_the_skill_corpus_is_not_empty():
    """A guard is green over an empty corpus. Prove there is a corpus."""
    files = _skill_text_files()
    assert len(files) >= 200, (
        f"only {len(files)} files under {SKILLS}; the walk below would pass "
        f"vacuously. Check the path before trusting any result from it."
    )
    skill_dirs = [p for p in SKILLS.iterdir() if p.is_dir()]
    assert len(skill_dirs) >= 50, f"only {len(skill_dirs)} skill directories found"


def test_the_walk_can_actually_see_a_caller(tmp_path):
    """Negative case: the counter must report a caller when one exists.

    Without this, `count_skill_callers` returning 0 proves nothing - a function
    that always returns 0 would satisfy every other assertion in this file.
    """
    fake = tmp_path / "skills"
    (fake / "innocent").mkdir(parents=True)
    (fake / "innocent" / "SKILL.md").write_text("no audit here\n", encoding="utf-8")
    scanned, callers = count_skill_callers(fake)
    assert scanned == 1 and callers == [], "clean tree should report no callers"

    (fake / "guilty").mkdir()
    (fake / "guilty" / "SKILL.md").write_text(
        "Run `python scripts/visual-discipline-check.py --deep out.html` before done.\n",
        encoding="utf-8",
    )
    scanned, callers = count_skill_callers(fake)
    assert scanned == 2, f"expected to scan 2 files, scanned {scanned}"
    assert callers == ["guilty"], (
        f"the walk missed a skill that plainly names the checker: {callers}"
    )


def _verification_commands_in(text: str) -> list[str]:
    """Every shell line inside a fenced bash block that greps .claude/skills/."""
    blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
    lines = []
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("grep") and ".claude/skills/" in line:
                lines.append(line)
    return lines


def test_the_rule_ships_its_own_verification_command():
    """The rule must hand the reader a command, not an assertion to trust."""
    text = RULE.read_text(encoding="utf-8")
    commands = _verification_commands_in(text)
    assert commands, (
        "the rule states how many skills call the checker but ships no command "
        "to check it. A claim about the tree that the reader cannot re-run is "
        "the shape of claim that rotted here in the first place."
    )
    assert any(CHECKER_NAME in c for c in commands), (
        f"none of the rule's grep commands names the checker: {commands}"
    )


def test_the_rules_command_returns_what_the_rule_says_it_returns():
    """Run the rule's own command. Its answer must match the rule's annotation.

    This is the bidirectional half. `grep -rl` exits 1 with no output when
    nothing matches, so a non-empty stdout means a skill DOES call the checker.
    """
    text = RULE.read_text(encoding="utf-8")
    commands = _verification_commands_in(text)
    command = next(c for c in commands if CHECKER_NAME in c)

    # The annotation the rule writes beside the command, e.g. `# no matches`.
    claims_none = re.search(r"#\s*no matches", command) is not None

    # Split to argv and run WITHOUT a shell: `shell=True` on a string lifted out
    # of a markdown file would let whatever the rule contains execute, and the
    # workspace forbids it outright. The comment is stripped first, since shlex
    # keeps `#` as an ordinary token.
    argv = shlex.split(command.split("#")[0].strip())
    assert argv and argv[0] == "grep", f"unexpected command in the rule: {command!r}"
    proc = subprocess.run(  # noqa: S603 - argv list, no shell, fixed cwd
        argv, cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
    )
    matched = [line for line in proc.stdout.splitlines() if line.strip()]

    # Cross-check the shelled command against the in-process walk, so a broken
    # command string cannot quietly report "no matches" for the wrong reason.
    scanned, callers = count_skill_callers(SKILLS)
    assert scanned >= 200, f"in-process walk saw only {scanned} files"
    assert len(matched) == 0 or callers, (
        "the shell command found matches the in-process walk did not; one of "
        "the two is looking at the wrong tree"
    )

    if claims_none:
        assert not callers, (
            f"the rule still says no skill calls the checker, but these now do: "
            f"{callers}. Update the '### What enforces this rule' section - "
            f"skill-side enforcement is no longer an unenforced convention."
        )
        assert not matched, f"the rule's own command found matches: {matched}"
    else:
        assert callers, (
            "the rule no longer annotates its verification command with '# no "
            "matches', which reads as a claim that some skill calls the "
            "checker. None does."
        )


def test_the_rule_does_not_reassert_skill_side_enforcement():
    """The retired claim must not come back, in any re-wrapped form.

    Matched on whitespace-normalised text so a re-wrap does not slip it past:
    a substring match against the original line breaks would.
    """
    _, callers = count_skill_callers(SKILLS)
    if callers:
        pytest.skip(f"skills now call the checker ({callers}); the claim is true")

    flat = " ".join(RULE.read_text(encoding="utf-8").split())
    forbidden = [
        "the producing skill is responsible for running the audit",
        "Skills must invoke this rule's checklist",
    ]
    for phrase in forbidden:
        # The rule is allowed to QUOTE the retired claim while explaining that
        # it was false; it is not allowed to assert it. The quoted form in the
        # rule carries the word "previously" within the same sentence.
        for match in re.finditer(re.escape(phrase), flat):
            window = flat[max(0, match.start() - 220):match.end() + 60]
            assert "previously" in window or "used to" in window or "were false" in window, (
                f"the rule asserts {phrase!r} as current, but no skill calls "
                f"the checker. Context: ...{window}..."
            )


def test_the_checker_does_not_claim_skills_call_it():
    """`scripts/visual-discipline-check.py:709` asserted the same falsehood."""
    _, callers = count_skill_callers(SKILLS)
    flat = " ".join(CHECKER.read_text(encoding="utf-8").split())
    match = re.search(r"skills that already call it", flat)
    if callers:
        return  # a live claim would now be true
    if match is None:
        return
    window = flat[max(0, match.start() - 200):match.end() + 60]
    assert "used to say" in window or "no skill has ever called it" in window, (
        f"the checker still claims skills call it, and none does. "
        f"Context: ...{window}..."
    )


def test_ci_is_named_as_the_only_enforcement():
    """The rule must name what DOES enforce, not merely retract what does not.

    Softening a false claim into vagueness is the failure mode this replaces.
    """
    text = RULE.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert ".github/workflows/ci.yml" in flat, (
        "the rule retracts skill-side enforcement without naming the one "
        "mechanism that does fire"
    )
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert CHECKER_NAME in ci, (
        "the rule names CI as its only enforcement, but the workflow does not "
        "invoke the checker"
    )
    assert "baseline check --deep docs/" in ci, (
        "the CI step no longer runs the docs/ ratchet the rule points at"
    )
