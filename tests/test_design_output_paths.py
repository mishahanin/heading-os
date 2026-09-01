#!/usr/bin/env python3
"""The /design surface must never document a write into the engine clone.

`/design` produces DATA artifacts: renders, AI imagery, PDFs. They belong under
the DATA root, reached through `get_outputs_dir()`. Both of its scripts resolve
`--output` / `--file` LITERALLY relative to the current directory, and the
current directory is the engine root when a SKILL runs a Bash command. So a
documented `-o outputs/design/{name}.png` writes a private artifact inside the
public engine clone.

Reproduced 2026-08-30 against the then-current SKILL.md, at 400x200:

    design-studio.py render --file ... -o outputs/design/probe.png
    [OK] Screenshot saved: <engine-root>/outputs/design/probe.png

and the documented `--file outputs/design/source/{name}.html` failed
"File not found: <engine-root>/outputs/design/source/probe.html", because the
PreToolUse `data-path-redirect` hook covers Read/Write/Edit/Grep/Glob and does
NOT cover Bash.

**Nothing else was going to catch this, which is why the check lives here.**
`tests/test_engine_tree_clean.py` is described as the authoritative, how-agnostic
guarantee, and the unbypassable push wall shares its detector. Both enumerate
git-CARRIED paths, and `.gitignore` line 320 ignores `/outputs/` in the engine.
Measured with the probe render still on disk: `scan_engine_repo()` returned `[]`
and the 21 tests of `test_engine_tree_clean.py` all passed. A `/design` misroute
is invisible to the belt AND to the braces.

`scripts/audit-skill-bash-paths.py` did not see it either, and its blind spot is
worth naming for whoever widens it: its `_COMMAND` regex must match the SAME
physical line as the data path, and it accepts `--out` but not `-o`. Every
offending line in this SKILL was a backslash continuation carrying `-o`, so the
scanner reported `design` with zero hits and `--check` said OK. This test parses
instead: it joins continuations, `shlex`-splits the logical command, and asks the
workspace's own router about each option VALUE.

Asking the router is deliberate. A text pattern for "looks like a data path"
would be a second classifier to drift out of step with
`config/routing-map.yaml`, and it would punish the prose that documents the trap.
`find_data_artifacts()` is the same function the push wall uses.

Covers: .claude/skills/design/SKILL.md, scripts/design-studio.py,
scripts/design-engine.py
"""
from __future__ import annotations

import ast
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.engine_guard import find_data_artifacts  # noqa: E402
from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402

SKILL = ROOT / ".claude" / "skills" / "design" / "SKILL.md"
STUDIO = ROOT / "scripts" / "design-studio.py"
ENGINE = ROOT / "scripts" / "design-engine.py"

BASH_FENCES = {"bash", "sh", "shell"}

# Options whose value is a filesystem path the tool then writes to or reads.
PATH_OPTS = {"-o", "--output", "--out", "--output-dir", "--file", "--image"}


# --------------------------------------------------------------------------
# Structural extraction
# --------------------------------------------------------------------------


def _join_continuations(lines: list[str]) -> list[str]:
    """Fold `foo \\` + `  --bar baz` into one logical command line."""
    logical: list[str] = []
    buf = ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        if buf.strip():
            logical.append(buf.strip())
        buf = ""
    if buf.strip():
        logical.append(buf.strip())
    return logical


def _fenced_regions(md: Path) -> list[tuple[str, list[str]]]:
    """(info-string, body-lines) for every fenced block, in order."""
    out: list[tuple[str, list[str]]] = []
    info: str | None = None
    body: list[str] = []
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            if info is None:
                info = line.strip().strip("`").strip().lower()
                body = []
            else:
                out.append((info, body))
                info = None
            continue
        if info is not None:
            body.append(line)
    return out


def _path_arguments(command_lines: list[str]) -> list[tuple[str, str]]:
    """(option, value) for every path-carrying option in these command lines.

    `shlex` is what makes this a parse rather than a match: it strips quoting, so
    `-o "outputs/x.png"` and `-o outputs/x.png` produce the same value, and a
    bare mention of `outputs/` in a comment produces none.
    """
    found: list[tuple[str, str]] = []
    for cmd in _join_continuations(command_lines):
        if cmd.lstrip().startswith("#"):
            continue
        try:
            tokens = shlex.split(cmd, comments=True)
        except ValueError:
            continue  # unbalanced quotes in an illustrative snippet
        for i, tok in enumerate(tokens):
            if "=" in tok and tok.split("=", 1)[0] in PATH_OPTS:
                opt, val = tok.split("=", 1)
                found.append((opt, val))
            elif tok in PATH_OPTS and i + 1 < len(tokens):
                found.append((tok, tokens[i + 1]))
    return found


def _skill_path_arguments() -> list[tuple[str, str]]:
    args: list[tuple[str, str]] = []
    for info, body in _fenced_regions(SKILL):
        if info in BASH_FENCES:
            args.extend(_path_arguments(body))
    return args


def _docstring_path_arguments(script: Path) -> list[tuple[str, str]]:
    """Path options in a script's own `Usage:` examples, via the AST.

    The module docstring is read with `ast.get_docstring`, not sliced out of the
    text, so a docstring that moves or changes quoting style still measures.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    doc = ast.get_docstring(tree) or ""
    lines = [ln for ln in doc.splitlines() if "python" in ln and "scripts/" in ln]
    return _path_arguments(lines)


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,collect",
    [
        ("SKILL.md bash blocks", _skill_path_arguments),
        ("design-studio.py Usage", lambda: _docstring_path_arguments(STUDIO)),
        ("design-engine.py Usage", lambda: _docstring_path_arguments(ENGINE)),
    ],
)
def test_no_documented_path_argument_routes_private(label, collect):
    """No documented `-o`/`--file` value may be a cwd-relative DATA path.

    Relative, because that is the whole defect: the value is joined to the
    engine root. An absolute path, or one built from a shell variable the skill
    resolved through the seam, is fine and is not asked about here.
    """
    offenders = []
    for opt, val in collect():
        if val.startswith(("/", "$", "~")) or Path(val).is_absolute():
            continue
        if find_data_artifacts([val]):
            offenders.append(f"{opt} {val}")
    assert not offenders, (
        f"{label}: these documented path arguments route private/corporate and "
        f"are resolved against the ENGINE root, so following the documentation "
        f"writes a private artifact into the public clone. Resolve the data "
        f"outputs dir first (see .claude/skills/yt-pulse/SKILL.md Phase 0) and "
        f"pass an absolute path:\n  " + "\n  ".join(offenders)
    )


def test_the_skill_resolves_its_outputs_dir_by_running_the_snippet():
    """Execute the skill's own resolution line; the answer must be the data root.

    An assertion that the text `get_outputs_dir` appears somewhere would pass on
    a snippet that is subtly wrong. This one runs the command the reader is told
    to run and checks where it actually points.
    """
    assignments = []
    for info, body in _fenced_regions(SKILL):
        if info not in BASH_FENCES:
            continue
        for cmd in _join_continuations(body):
            if cmd.startswith("OUTPUTS_DIR="):
                assignments.append(cmd)

    assert assignments, (
        "The design SKILL runs Bash commands that write DATA artifacts but never "
        "resolves an OUTPUTS_DIR from the seam. Bash is not covered by the "
        "data-path-redirect PreToolUse hook, so every relative path it hands a "
        "script lands in the engine clone."
    )

    ws_root = get_workspace_root().resolve()
    expected = get_outputs_dir().resolve()
    for cmd in assignments:
        proc = subprocess.run(
            ["bash", "-c", f'{cmd}\nprintf "%s" "$OUTPUTS_DIR"'],
            cwd=ws_root, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, (
            f"the skill's own resolution line failed: {cmd!r}\n{proc.stderr}"
        )
        resolved = Path(proc.stdout.strip())
        assert resolved.is_absolute(), f"{cmd!r} produced a relative path: {resolved}"
        assert resolved == expected, (
            f"{cmd!r} resolved to {resolved}, not the data outputs dir {expected}"
        )
        assert ws_root not in resolved.parents and resolved != ws_root, (
            f"{cmd!r} resolved INSIDE the engine clone: {resolved}"
        )


# --------------------------------------------------------------------------
# Controls: the guard must be able to fail, over a non-empty corpus
# --------------------------------------------------------------------------


def test_the_extractor_actually_found_path_arguments():
    """A parser that silently yields nothing reports every corpus as clean.

    Floored per corpus, because the guard above is parametrized per corpus and
    each one can go empty on its own. It used to floor the SKILL and
    `design-studio.py` and say nothing about `design-engine.py`. MEASURED
    2026-09-01: rewriting that file's six `python scripts/design-engine.py ...`
    Usage lines to a spelling the extractor's `"scripts/" in ln` filter does not
    match dropped its parsed set from 3 to 0, and all 7 tests here stayed green.
    One third of this guard was reporting a clean sweep over nothing.

    Counts on 2026-09-01: 12 out of the SKILL, 5 out of design-studio.py, 3 out
    of design-engine.py. Floored below each so retiring one documented example
    does not fail this test.
    """
    skill_args = _skill_path_arguments()
    assert len(skill_args) >= 6, (
        f"only {len(skill_args)} path argument(s) parsed out of the design SKILL "
        f"bash blocks; the extractor has stopped seeing them: {skill_args}"
    )
    studio_args = _docstring_path_arguments(STUDIO)
    assert len(studio_args) >= 3, (
        f"only {len(studio_args)} path argument(s) parsed out of "
        f"design-studio.py's Usage examples: {studio_args}")
    engine_args = _docstring_path_arguments(ENGINE)
    assert len(engine_args) >= 2, (
        f"only {len(engine_args)} path argument(s) parsed out of "
        f"design-engine.py's Usage examples: {engine_args}")


def test_every_path_option_the_two_scripts_accept_is_one_the_guard_asks_about():
    """`PATH_OPTS` is a hand-written list, and the guard is only as wide as it.

    A new `--out-dir` on either script would be documented, followed, and
    invisible here, because the extractor only looks at options it was told
    about. This derives the option set from the scripts' own `add_argument`
    calls, keeps the ones whose help text says they name a file or a directory,
    and asks whether the hand list still covers them.

    Derived by AST rather than by grepping the help strings out of the file, so
    a re-wrapped `add_argument` call keeps measuring.
    """
    documented: dict[str, set[str]] = {}
    for script in (STUDIO, ENGINE):
        tree = ast.parse(script.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            flags = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and a.value.startswith("-")]
            help_text = ""
            for kw in node.keywords:
                if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                    help_text = str(kw.value.value).lower()
            if not any(word in help_text for word in
                       ("file path", "output file", "output directory",
                        "image path", "path to")):
                continue
            for flag in flags:
                documented.setdefault(flag, set()).add(script.name)

    # Floor: an AST question that finds no path options passes trivially.
    assert len(documented) >= 3, (
        f"only {sorted(documented)} path-carrying options found across "
        f"design-studio.py and design-engine.py; the AST question has stopped "
        f"reaching the parsers")

    missing = {flag: sorted(where) for flag, where in documented.items()
               if flag not in PATH_OPTS}
    assert not missing, (
        "these options name a filesystem path and are NOT in PATH_OPTS, so a "
        "documented example using one is invisible to the guard above:\n  "
        + "\n  ".join(f"{flag} ({', '.join(where)})"
                      for flag, where in sorted(missing.items())))


def test_the_guard_fires_on_the_shape_it_exists_to_catch():
    """The exact line that produced the reproduced misroute must be rejected."""
    bad = _path_arguments([
        "python3 scripts/design-studio.py render \\",
        "  --file outputs/design/source/probe.html \\",
        "  -o outputs/design/probe.png",
    ])
    assert ("--file", "outputs/design/source/probe.html") in bad
    assert ("-o", "outputs/design/probe.png") in bad
    assert [v for _, v in bad if find_data_artifacts([v])] == [
        "outputs/design/source/probe.html",
        "outputs/design/probe.png",
    ]


def test_the_guard_accepts_the_resolved_form():
    """The fixed shape must pass, or the guard is just banning the option."""
    good = _path_arguments([
        'python3 scripts/design-studio.py render \\',
        '  --file "$OUTPUTS_DIR/design/source/probe.html" \\',
        '  -o "$OUTPUTS_DIR/design/probe.png"',
    ])
    assert good, "the resolved form parsed to nothing; the extractor is broken"
    assert not [v for _, v in good if not v.startswith("$") and find_data_artifacts([v])]
