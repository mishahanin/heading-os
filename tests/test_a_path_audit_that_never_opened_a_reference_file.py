"""The skill bash data-path audit read 94 of the tree's 201 markdown files, and 0
of the 83 under `.claude/skills/*/references/`.

One line did it. `skill_files` globbed `.claude/skills/*/SKILL.md`, one level deep,
so a reference file was never opened -- and a reference file carries runnable bash
exactly as its SKILL.md does. MEASURED 2026-09-02 by wrapping `scan_skill` in a
counter: 94 files opened, `references/*.md` opened 0. Widening the same scanner to
the whole tree surfaced six live commands it had never seen, in two skills:
`notebooklm/references/mode-catalog.md` told the agent to run three `nlm download
... -o "outputs/content/notebooklm/..."` calls, and `docparse/references/
integration.md` told it to run two `--files "datastore/..."` parses plus a report
with `--output-dir outputs/...`. Bash is not covered by the data-path-redirect
hook, so each of those reads or writes the engine clone instead of the DATA
overlay. The gate printed OK over all six for as long as it existed.

Two properties are held here, because closing only the first leaves the same
failure available to the next edit.

1. **Coverage.** The corpus is every markdown file under `.claude/skills/`, and
   the references are in it. Asked of the audit's own corpus function and of the
   files `scan_corpus` actually opens, never of the glob string.

2. **The floor.** A run that opens nothing, or that opens fewer files than the
   skills tree holds, REFUSES with a non-zero exit naming both numbers. Without it
   the widening is one typo from being undone in silence: `BASELINE` is empty by
   design, so `counts == BASELINE` is true over a corpus of zero, and every question
   this gate asks is an ABSENCE question that a corpus of nothing answers perfectly.
   Precedent: `scripts/ste-check.py` and `scripts/validate-crm-schema.py` both exit
   2 rather than report a pass over an empty corpus.

The floor is stated against the tree in front of it (an `os.walk` written separately
from the corpus glob, so a narrowing glob cannot shrink its own yardstick), never as
an absolute count. Absolute floors were the first attempt and they refused two
legitimate callers: the one-skill scratch workspace
`tests/test_a_gate_that_passed_the_plan_it_had_just_failed.py` uses to drive the FAIL
branch, and any downstream fork carrying fewer skills than this one.

The shortfall is reported PER CLASS as well as over the union, because a union figure
understates a whole surface going dark: 94 of 201 reads as 53 percent overall and 0
percent for references, and it is the second number that names the defect.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_skill_bash_paths_corpus", str(ROOT / "scripts" / "audit-skill-bash-paths.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

SKILLS = ROOT / ".claude" / "skills"


def _tree_counts() -> tuple[int, int, int]:
    """(SKILL.md, references/*.md, all *.md) walked independently of the audit."""
    all_md = list(SKILLS.rglob("*.md"))
    skill_md = [p for p in all_md if p.name == "SKILL.md"]
    refs = [p for p in all_md if p.parent.name == "references"]
    return len(skill_md), len(refs), len(all_md)


# ============================================================
# 1. Coverage
# ============================================================

def test_the_corpus_holds_every_reference_file_the_tree_ships():
    """The defect, asked directly: are the references in the corpus at all?"""
    skill_md, refs, all_md = _tree_counts()
    assert refs >= 50, f"only {refs} references/*.md on disk; this test lost its subject"

    corpus = _mod.skill_files(ROOT)
    in_corpus = {p.resolve() for p in corpus}
    missing = [p for p in SKILLS.rglob("*.md") if p.resolve() not in in_corpus]
    assert not missing, (
        f"{len(missing)} markdown file(s) a skill ships are outside the audit corpus, "
        f"e.g. {[str(p.relative_to(ROOT)) for p in missing[:5]]}")
    assert len(corpus) == all_md, f"corpus {len(corpus)} != tree {all_md}"


def test_the_scan_opens_every_reference_file_not_merely_lists_it():
    """A corpus that contains a file proves nothing until the scan reads it.

    Bound on the files `scan_corpus` records as OPENED, which it can only record
    after `scan_skill` returned something other than None -- so a `references`
    filter reintroduced anywhere between the glob and the read fails here.
    """
    _, refs_on_disk, _ = _tree_counts()
    _, coverage = _mod.scan_corpus(ROOT)
    opened_refs = [p for p in coverage.opened if p.parent.name == "references"]
    assert len(opened_refs) == refs_on_disk, (
        f"opened {len(opened_refs)} of {refs_on_disk} references/*.md files")
    assert not coverage.unreadable, f"unreadable: {coverage.unreadable}"


def test_a_reference_files_bash_is_actually_matched(tmp_path):
    """Coverage without matching is a corpus that reads and never reports.

    A synthetic reference file at the real nesting depth, so this fails if the
    scanner is pointed at reference files but declines to score them.
    """
    ref = tmp_path / ".claude" / "skills" / "example" / "references" / "cookbook.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(
        "# Example\n\n```bash\n"
        'tool render deck -o "outputs/content/example.png"\n'
        "```\n",
        encoding="utf-8",
    )
    hits = _mod.scan_skill(ref)
    assert hits, "a bare data path inside a reference file's bash fence was not flagged"
    assert any("outputs/content/example.png" in cmd for _, cmd in hits), hits


def test_an_unreadable_file_is_distinguishable_from_a_clean_one(tmp_path):
    """`scan_skill` returned `[]` for both, which is what let the coverage count
    treat a file it never opened as a file it read and found nothing in."""
    clean = tmp_path / "SKILL.md"
    clean.write_text("# nothing\n", encoding="utf-8")
    assert _mod.scan_skill(clean) == []
    assert _mod.scan_skill(tmp_path / "gone.md") is None


def test_a_hit_names_the_file_it_came_from(tmp_path):
    """One skill now has many scannable files, so the skill name no longer locates
    a hit. The report has to say which file and which line.

    Held on a synthetic workspace root: the live tree is clean by design, so an
    assertion about hit shape has nothing to bind to there.
    """
    ref = tmp_path / ".claude" / "skills" / "example" / "references" / "cookbook.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(
        "# Example\n\nprose\n\n```bash\n"
        'tool render deck -o "outputs/content/example.png"\n'
        "```\n",
        encoding="utf-8",
    )
    found, _ = _mod.scan_corpus(tmp_path)
    assert list(found) == ["example"], found
    (rel, line, command) = found["example"][0]
    assert rel == ".claude/skills/example/references/cookbook.md", rel
    assert line == 6, line
    assert "outputs/content/example.png" in command, command


def test_hits_are_keyed_by_the_skill_not_by_the_directory_holding_them():
    """`path.parent.name` was the key while the corpus was one level deep.

    Over the widened corpus that keys every reference-file hit as `references`,
    collapsing unrelated skills into one bucket and reporting a skill name that
    does not exist. `BASELINE` is keyed by skill, so the ratchet would be tracking
    a fiction.
    """
    ref = ROOT / ".claude" / "skills" / "docparse" / "references" / "integration.md"
    assert ref.is_file(), "fixture path moved; re-point this test"
    assert _mod.skill_name(ref, ROOT) == "docparse"
    assert _mod.skill_name(ROOT / ".claude" / "skills" / "docparse" / "SKILL.md",
                           ROOT) == "docparse"


# ============================================================
# 2. The floor
# ============================================================

def _build_tree(base: Path, skills: int, refs: int) -> tuple[list[Path], list[Path]]:
    """A real on-disk skills tree; `missed()` asks whether a file still exists."""
    skill_mds, ref_mds = [], []
    for i in range(skills):
        p = base / ".claude" / "skills" / f"s{i}" / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# s\n", encoding="utf-8")
        skill_mds.append(p)
    for i in range(refs):
        p = base / ".claude" / "skills" / f"s{i}" / "references" / "page.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# r\n", encoding="utf-8")
        ref_mds.append(p)
    return skill_mds, ref_mds


def _coverage(tree, opened, unreadable=()):
    return _mod.Coverage(tree=set(tree), corpus=list(tree), opened=list(opened),
                         unreadable=list(unreadable))


def test_a_run_that_opens_nothing_refuses_and_names_both_numbers(tmp_path):
    skills, refs = _build_tree(tmp_path, 94, 107)
    reasons = _coverage(skills + refs, []).refusals()
    assert reasons, "an empty scan reported no reason to refuse"
    joined = " ".join(reasons)
    assert "0" in joined and "201" in joined, joined
    # The wording and the SINGLE reason are both load-bearing, and neither was
    # asserted until a mutation proved it. Deleting the zero-open branch left the
    # test above green, because the shortfall reasons fire on the same input and
    # also carry both numbers. What is lost is the sentence that names the failure
    # ("a pass over an empty corpus is not a pass") and the early return that stops
    # three redundant reasons burying it.
    assert len(reasons) == 1, reasons
    assert "empty corpus" in reasons[0], reasons[0]


def test_the_empty_corpus_reason_survives_an_empty_tree_too():
    """Tree zero AND opened zero: a walk pointed at nothing at all.

    The shortfall comparison cannot fire here, so this is the input where the
    zero-open branch is the only thing between a broken walk and a clean exit.
    """
    reasons = _coverage([], []).refusals()
    assert len(reasons) == 1 and "empty corpus" in reasons[0], reasons


def test_opening_fewer_files_than_the_tree_holds_refuses(tmp_path):
    skills, refs = _build_tree(tmp_path, 94, 107)
    tree = skills + refs
    reasons = _coverage(tree, tree[:180], tree[180:]).refusals()
    assert reasons, "a short read reported no reason to refuse"
    assert any("180" in r and "201" in r for r in reasons), reasons


def test_the_union_shortfall_is_reported_per_class_as_well(tmp_path):
    """94 SKILL.md read out of 201 is a 53-percent union figure and a 0-percent
    references figure, and it is the second number that names the defect.

    A union-only report understates a whole surface going dark, so the per-class
    line has to be there and has to be specific to the class that dropped.
    """
    skills, refs = _build_tree(tmp_path, 94, 107)
    reasons = _coverage(skills + refs, skills).refusals()
    assert any("0 of the 107 references" in r for r in reasons), reasons
    assert not any("SKILL.md file(s)" in r for r in reasons), (
        "the SKILL.md class is fully read here and must not be reported", reasons)


def test_a_file_that_vanished_mid_run_is_not_counted_as_missed(tmp_path):
    """The documented mid-walk race: several agents share one checkout.

    A file created and deleted inside the run window is not a surface this audit
    failed to cover, and turning it into a hard refusal is the regression the
    scanner's own docstring warns against.
    """
    skills, refs = _build_tree(tmp_path, 94, 107)
    ghost = tmp_path / ".claude" / "skills" / "s0" / "gone.md"
    cov = _coverage(skills + refs + [ghost], skills + refs, unreadable=[ghost])
    assert cov.missed() == [], cov.missed()
    assert cov.refusals() == [], cov.refusals()


def test_a_healthy_run_refuses_nothing():
    """Negative case. A floor that fires on the live tree is a broken gate, and a
    refusal list that is never empty proves nothing about the cases above."""
    _, coverage = _mod.scan_corpus(ROOT)
    assert coverage.refusals() == [], coverage.refusals()
    assert len(coverage.opened) >= 150


def test_the_independent_walk_reaches_nested_reference_files(tmp_path):
    """The yardstick is only a yardstick if it descends.

    A walk that stops at the top of `.claude/skills/` returns nothing at all, since
    that level holds directories. `tree` then goes empty, `missed()` goes empty, and
    every shortfall reason silently cannot fire while `opened` looks healthy. The
    floor would be measuring against zero and passing everything.
    """
    skill = tmp_path / ".claude" / "skills" / "example" / "SKILL.md"
    ref = tmp_path / ".claude" / "skills" / "example" / "references" / "deep.md"
    ref.parent.mkdir(parents=True)
    skill.write_text("# s\n", encoding="utf-8")
    ref.write_text("# r\n", encoding="utf-8")
    (tmp_path / ".claude" / "skills" / "example" / "notes.txt").write_text("x", encoding="utf-8")

    walked = _mod.walk_markdown(tmp_path)
    assert walked == {skill, ref}, walked


def test_a_narrowed_corpus_glob_is_caught_by_the_walk_and_refuses():
    """The defect itself, driven end to end through `scan_corpus`.

    `skill_files` is pushed back to the exact pre-fix glob while the independent
    walk is left alone, which is what a future edit narrowing the corpus looks
    like. If the yardstick were derived from `skill_files` it would shrink in step
    and this run would report a clean tree, which is precisely how the audit passed
    over 83 unread reference files for its whole life.
    """
    narrowed = lambda root: sorted(root.glob(".claude/skills/*/SKILL.md"))  # noqa: E731
    real = _mod.skill_files
    _mod.skill_files = narrowed
    try:
        _, coverage = _mod.scan_corpus(ROOT)
    finally:
        _mod.skill_files = real
    reasons = coverage.refusals()
    assert reasons, "a corpus narrowed back to SKILL.md reported nothing to refuse"
    assert any("references" in r for r in reasons), reasons


def test_a_small_but_complete_tree_is_not_refused(tmp_path):
    """The floor is stated against the tree in front of it, never an absolute count.

    An absolute floor was the first attempt and it broke two legitimate callers:
    the one-skill scratch workspace that
    `tests/test_a_gate_that_passed_the_plan_it_had_just_failed.py` uses to drive the
    FAIL branch, and any downstream fork carrying fewer skills than this one.
    """
    skill = tmp_path / ".claude" / "skills" / "only-one" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# one\n", encoding="utf-8")
    _, coverage = _mod.scan_corpus(tmp_path)
    assert coverage.refusals() == [], coverage.refusals()
    assert len(coverage.opened) == 1


def test_the_cli_exits_non_zero_when_the_corpus_collapses(tmp_path, monkeypatch):
    """End to end through `main`, because the refusal has to reach the exit code.

    A pointed-elsewhere workspace root is the mechanical stand-in for the typo'd
    glob: the audit finds no skills tree, and must exit 2 rather than print an
    empty findings table and exit 0.
    """
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("stub\n", encoding="utf-8")
    env = {**dict(__import__("os").environ), "WORKSPACE_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit-skill-bash-paths.py"), "--check"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSING" in proc.stderr, proc.stderr
    assert "0" in proc.stderr, proc.stderr
    assert "OK" not in proc.stdout, proc.stdout


# ============================================================
# 3. Working directory independence
# ============================================================

def test_the_audit_resolves_its_corpus_from_the_source_file_not_the_cwd(tmp_path):
    """Run from an unrelated directory with an absolute path; same corpus size.

    `cwd` is the pytest `tmp_path`, not a hardcoded `/tmp`. Any directory that
    is not the repository proves the point, and a shared, world-writable one
    does not (ruff S108). The directory only has to be somewhere the audit
    cannot mistake for its own tree.
    """
    expected = len(_mod.skill_files(ROOT))
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit-skill-bash-paths.py"), "--json"],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    import json
    assert json.loads(proc.stdout)["scanned"] == expected, proc.stdout
