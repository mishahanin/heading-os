"""Baseline-ratchet guard: no NEW SKILL.md bash line passes a bare data-class path to a script.

Advisory layer of the engine/data separation (the authoritative guarantee is
test_engine_tree_clean.py). The PreToolUse data-path-redirect hook does NOT cover Bash,
so a SKILL handing a bare `outputs/...` path to a Bash-invoked script can misroute a
write into the engine clone (auto-memory `skill-data-paths-need-explicit-resolution`).

Accepted hits are frozen as a BASELINE in scripts/audit-skill-bash-paths.py. This test
fails only on a REGRESSION: a skill gaining a new bare-data-path bash line, or a new
skill appearing. That catches creep without forcing churn on reviewed examples.

Two corrections landed on 2026-08-30, both about what this gate does NOT establish.

First, the baseline is a record of what was ACCEPTED, never of what is harmless. Nine
of its ten entries were frozen as "illustrative template paths" because they contained
`YYYY-MM-DD` or `{sender-slug}`, and all nine were live commands the skill tells the
agent to run. The five doctype skills among them documented a `render-doctype.py` call
that could not execute at all. A gate that finds a defect and files it under the wrong
heading is worse than one that misses it, because the entry then reads as review.

Second, the scanner was line-oriented and matched only long destination flags, so a
data path on a backslash continuation, or behind a short `-o`, was invisible to it.
`scan_skill` now joins continuations and `_DEST_OPTS` enumerates the destination flags
the corpus actually uses. That is what the four `test_scanner_*` cases below hold.

Neither correction was cosmetic, and the two compound. The old scanner's hit for a
doctype skill was the text `--out outputs/documents/{sender-slug}/letter/` -- the
`--out` continuation line ALONE, with the `--data` argument on the line above it never
shown. A reviewer freezing the baseline saw a bare directory path full of placeholders,
which is exactly what an illustrative example looks like, instead of the whole
`render-doctype.py` invocation that could not run. The truncation is what made the
mis-triage plausible.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_skill_bash_paths", str(ROOT / "scripts" / "audit-skill-bash-paths.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

from scripts.utils.workspace import get_workspace_root  # noqa: E402


def _bash_commands(path: Path) -> list[str]:
    """Every command in a bash-labelled fence, with backslash continuations joined.

    Deliberately NOT a scan of the file's source text. A skill may name the wrong form
    in prose to explain why it is wrong, and a grep would punish it for documenting its
    own trap. Fence selection CALLS `_mod.fence_language`, so both read the same blocks
    by construction. It was a hand-copied `stripped.strip("`").lower()` until
    2026-09-02, and the copy carried the same defect as the original: an info
    string after the language (```` ```bash linenos ````) hid the whole block from
    both. See `tests/test_a_fence_whose_info_string_hid_the_whole_block.py`.
    """
    commands: list[str] = []
    in_block = False
    is_bash = False
    pending: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_block:
                is_bash = _mod.fence_language(stripped) in _mod._BASH_FENCES
            in_block = not in_block
            pending = None
            continue
        if not (in_block and is_bash) or not stripped:
            continue
        joined = stripped if pending is None else f"{pending} {stripped}"
        if stripped.endswith("\\"):
            pending = joined.rstrip("\\").rstrip()
            continue
        pending = None
        commands.append(joined)
    return commands


def _flag_value(command: str, flag: str) -> str | None:
    """The token after `flag`, quotes stripped. None when the flag is absent."""
    tokens = command.split()
    for i, token in enumerate(tokens):
        if token == flag and i + 1 < len(tokens):
            return tokens[i + 1].strip("\"'")
        if token.startswith(f"{flag}="):
            return token[len(flag) + 1:].strip("\"'")
    return None


def test_no_new_skill_bash_data_path_misroutes():
    found = _mod.scan_all(get_workspace_root())
    counts = {name: len(hits) for name, hits in found.items()}
    regressions = []
    for name, n in counts.items():
        base = _mod.BASELINE.get(name)
        if base is None:
            regressions.append(f"{name}: NEW skill with {n} bare-data-path bash line(s)")
        elif n > base:
            regressions.append(f"{name}: {n} > baseline {base}")
    assert not regressions, (
        "New SKILL bash data-path misroute candidate(s) -- resolve via get_*_dir()/"
        "$OUTPUTS_DIR, or update BASELINE in scripts/audit-skill-bash-paths.py if "
        "intentional:\n  " + "\n  ".join(regressions)
    )


def test_the_ratchet_is_not_green_over_an_empty_corpus():
    """The two ratchet tests above ask only ABSENCE questions, so they pass over
    a corpus of nothing.

    MEASURED 2026-09-01: changing `scan_all`'s glob to `.claude/skillz/*/SKILL.md`
    left this whole file green, and left the neighbouring
    `test_the_real_skill_tree_still_has_no_unlabelled_fence_hits` in
    `tests/test_a_gate_that_passed_the_plan_it_had_just_failed.py` green as well.
    `BASELINE` is empty by design since 2026-08-31, and `{} == {}` is true whether
    the scanner read 94 files or none. The four `test_scanner_*` cases below prove
    the MATCHER works, on synthetic files in tmp_path; nothing proved the scanner
    was still pointed at the skills tree.

    The floor is per source in the only sense that applies here: this gate has one
    source, and it is pinned below its live size rather than at it, so adding or
    retiring a skill does not force a churn commit.
    """
    files = _mod.skill_files(get_workspace_root())
    assert len(files) >= 60, (
        f"the audit corpus fell to {len(files)} markdown files; the ratchet above "
        "reports a clean tree over an empty one")
    # Every markdown file a skill ships since 2026-09-02, not only its SKILL.md.
    # The coverage floor that pins the widening in place lives in
    # tests/test_a_path_audit_that_never_opened_a_reference_file.py; this one keeps
    # asking the union question it always asked.
    assert all(p.suffix == ".md" and p.is_file() for p in files), files[:5]


def test_scan_all_reads_every_file_in_that_corpus():
    """The corpus and the scan must be the same set.

    A non-empty `skill_files` does not by itself prove `scan_all` iterates it: the
    two could drift apart in a later edit and the assertion above would still hold.
    Bound with a recording stub over `scan_skill` rather than by reading the
    source, so a rewrite that keeps the behaviour keeps the test.
    """
    root = get_workspace_root()
    expected = _mod.skill_files(root)
    seen: list[Path] = []
    real = _mod.scan_skill
    _mod.scan_skill = lambda path: seen.append(path) or []
    try:
        assert _mod.scan_all(root) == {}
    finally:
        _mod.scan_skill = real
    assert seen == expected


def test_baseline_matches_current_corpus():
    """The frozen baseline must equal the live scan -- so a CLEANED skill (count drops
    below baseline) forces a baseline update, keeping the ratchet honest."""
    found = _mod.scan_all(get_workspace_root())
    counts = {name: len(hits) for name, hits in found.items()}
    assert counts == _mod.BASELINE, (
        "Baseline drift: scripts/audit-skill-bash-paths.py BASELINE must equal the live "
        f"scan.\n  live:     {counts}\n  baseline: {_mod.BASELINE}"
    )


def test_the_five_doctype_skills_resolve_their_render_paths(tmp_path):
    """The `render-doctype.py` call in each locked doctype skill must resolve both
    `--data` and `--out` through the seam.

    Not a source grep: the scanner's own fence parser selects the bash blocks, and the
    assertion runs over the parsed command text. A doctype skill is free to mention the
    bare form in prose while explaining the trap.

    Why this exists beside the ratchet: the `--data` argument sits on a backslash
    continuation line, which `scan_skill` cannot see at all (see the test below). The
    ratchet therefore scores these files on their `--out` line alone, and would stay
    green if `--data` regressed to the bare form. That is the half that actually kills
    the run -- `render-doctype.py` checks `args.data.exists()` and returns 1 before it
    ever reaches `args.out.mkdir()`.
    """
    root = get_workspace_root()
    offenders = []
    checked = set()
    for name in ("corporate-letter", "official-doc", "partnership-doc",
                 "proposal", "xpager"):
        path = root / ".claude" / "skills" / name / "SKILL.md"
        for command in _bash_commands(path):
            if "render-doctype.py" not in command:
                continue
            checked.add(name)
            for flag in ("--data", "--out"):
                value = _flag_value(command, flag)
                if value is None:
                    offenders.append(f"{name}: render-doctype call has no {flag}")
                elif not _mod._RESOLVED.search(value):
                    offenders.append(f"{name}: {flag} is unresolved -> {value}")
    assert not offenders, (
        "A locked doctype skill documents a render command Bash cannot execute. The "
        "Write tool is redirected to the DATA overlay and Bash is not, so a bare "
        "engine-relative path is read from the wrong root. Resolve it with "
        "get_outputs_dir(), as .claude/skills/flux-image/SKILL.md does:\n  "
        + "\n  ".join(offenders)
    )
    # Non-vacuity. Every assertion above is inside the loop, so a renamed script or a
    # fence that stops being labelled `bash` would empty the corpus and let this test
    # pass having examined nothing.
    assert checked == {"corporate-letter", "official-doc", "partnership-doc",
                       "proposal", "xpager"}, (
        "Found no render-doctype.py command in: "
        f"{sorted({'corporate-letter', 'official-doc', 'partnership-doc', 'proposal', 'xpager'} - checked)}. "
        "The skill stopped documenting the render call, the fence lost its `bash` "
        "label, or the script was renamed. Update this test deliberately."
    )


def test_scanner_joins_backslash_continuations(tmp_path):
    """A data path on a continuation line must be seen.

    The gap this closes: the scanner matched PHYSICAL lines, so a multi-line
    invocation split into a first line carrying the command with no path and a
    second carrying the path with nothing command-shaped. Neither half matched.
    Every multi-line command in the corpus has that shape, so the gate returned a
    clean zero over real misroutes -- measured on `design` before it was fixed.
    """
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "```bash\n"
        "python scripts/render-doctype.py --type letter \\\n"
        "  --data outputs/documents/example-sender/letter/_work/data.json \\\n"
        "  --check-only\n"
        "```\n",
        encoding="utf-8",
    )
    hits = _mod.scan_skill(skill)
    assert len(hits) == 1, f"continuation not joined: {hits}"
    line, command = hits[0]
    assert line == 2, f"should report the line the command STARTS on, got {line}"
    assert "--data outputs/documents/" in command, command
    assert "--check-only" in command, "the join stopped early: " + command


def test_scanner_covers_short_destination_options(tmp_path):
    """`-o` must count as a destination, not only `--out` / `--output`.

    A command that invokes no recognised script still writes when it names an
    output path. `-o` is the most used destination flag in the corpus (10 uses)
    and was invisible.
    """
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "```bash\n"
        "imagemagick-convert cover.png \\\n"
        "  -o outputs/content/images/example-cover.png\n"
        "```\n",
        encoding="utf-8",
    )
    assert _mod.scan_skill(skill), "short -o destination slipped through"


def test_short_option_matching_does_not_misfire():
    """The `-o` alternative must not fire inside a longer flag, and a resolved
    destination must still be excluded. Both directions, or the widened regex
    would trade one blind spot for a wall of false positives."""
    # `-o` inside `--output` must not be what matches; `--output` must.
    assert _mod._COMMAND.search("tool --output outputs/x.png")
    # A flag that merely starts with a covered prefix is not a match by itself.
    # These are held by the trailing `(?=[=\s]|$)`.
    assert not _mod._COMMAND.search("tool --outrageous value")
    assert not _mod._COMMAND.search("tool -optimize value")
    # A short flag GLUED to the end of another token is not a flag. These are
    # held by the leading `(?<!\S)`, and nothing else in this file reached it --
    # the two guards look interchangeable and are not. Brute-forcing both
    # regexes over short strings on 2026-08-30 found `--o`, `--f` and any token
    # ending `-o` / `-f` as the inputs where they disagree.
    assert not _mod._COMMAND.search("render deck-o value")
    assert not _mod._COMMAND.search("render deck-f value")
    assert not _mod._COMMAND.search("tool --o value")
    # Resolution still wins over any destination flag.
    assert _mod._RESOLVED.search('tool -o "$OUTPUTS_DIR/x.png"')


def test_dest_opts_are_covered_by_the_command_regex():
    """Every flag enumerated in `_DEST_OPTS` must actually match. A list that
    grows without the regex following it is a silent hole of the same shape as
    the one this suite exists to close."""
    unmatched = [o for o in _mod._DEST_OPTS
                 if not _mod._COMMAND.search(f"tool {o} outputs/x.png")]
    assert not unmatched, f"_DEST_OPTS entries the regex never matches: {unmatched}"


def test_scanner_excludes_resolved_paths():
    """A bash line that resolves via the seam must NOT be flagged."""
    # scan_skill works on a real file; assert the regex pair behaves on representative lines.
    assert _mod._DATA.search("python scripts/x.py outputs/foo.md")
    assert _mod._RESOLVED.search('python scripts/x.py "$(... get_outputs_dir)/foo.md"')
    assert _mod._RESOLVED.search("OUT=$OUTPUTS_DIR/foo.md")
