"""`rule_split_check.py --check` was green over a corpus it had not opened.

MEASURED 2026-09-02, before the fix, on a tree holding 26 `.md` files under
`.claude/rules/`:

    $ .venv/bin/python scripts/rule_split_check.py --check
    inventory check: OK
    EXIT=0                      # 6 paths opened: 2 snapshots, 3 rule files, 1 destination

    $ cd /tmp && <abs>/.venv/bin/python <abs>/scripts/rule_split_check.py --check
    inventory check: OK
    EXIT=0                      # 0 paths opened

Two separate defects, one indistinguishable output.

NARROWED CORPUS. `INVENTORY_DIR` and the `rules_dir` default were bare relative
paths, and `check_inventories` globs `<dir>/*.txt` and returns `[]` on an empty
glob -- byte-for-byte the answer it gives over a clean tree. A renamed
directory, a deleted snapshot, or a `.txt` convention that moves all read as "no
directive was dropped". There was no floor: a gate that reads nothing passes.

CWD DEPENDENCE. Those same relative paths resolved against the process cwd, so
the identical command one directory away opened nothing and still printed OK and
exited 0. CI happens to run from the repo root, which is why nobody saw it; a
pre-push hook, a systemd unit, or an agent shell with a different cwd would each
have been silently guarding nothing.

These tests probe WHICH FILES the checker opens and WHAT IT DOES over an empty
corpus, not the wording of its output, so a later reword of the report cannot
retire them.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import rule_split_check as R  # noqa: E402

SCRIPT = ROOT / "scripts" / "rule_split_check.py"
LIVE_RULES = ROOT / ".claude" / "rules"


def _spy(monkeypatch):
    """Record every path handed to `read_sources`, and pass it through."""
    opened: list[Path] = []
    real = R.read_sources

    def spy(paths, *a, **k):
        collected = list(paths)
        opened.extend(Path(p) for p in collected)
        return real(collected, *a, **k)

    monkeypatch.setattr(R, "read_sources", spy)
    return opened


# ============================================================
# Defect 1: the cwd decided how much of the tree the gate saw
# ============================================================

def test_the_check_opens_the_same_files_from_any_working_directory(monkeypatch, tmp_path):
    """The corpus is a property of the repository, never of the shell's cwd.

    Asserted on the set of files opened rather than on the printed verdict,
    because pre-fix BOTH runs printed the same verdict: the run that read
    everything and the run that read nothing were the same three characters.
    """
    monkeypatch.chdir(ROOT)
    from_root = _spy(monkeypatch)
    R.check_inventories()
    baseline = {p.resolve() for p in from_root}

    assert baseline, (
        "the checker opened no files at all from the repository root; either the "
        "inventory moved or this test is now measuring nothing")

    monkeypatch.chdir(tmp_path)
    from_elsewhere = _spy(monkeypatch)
    R.check_inventories()

    assert {p.resolve() for p in from_elsewhere} == baseline


def test_a_repo_relative_rule_path_still_resolves_from_another_directory(tmp_path):
    """`--dump .claude/rules/voice.md` names a repo path, not a cwd path.

    End to end through the CLI, which is how CI and any hook invoke it. Pre-fix
    this raised FileNotFoundError the moment the cwd was not the repo root.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dump", ".claude/rules/voice.md"],
        cwd=tmp_path, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert "regex-recognized imperatives" in proc.stdout


def test_the_cli_check_agrees_with_itself_from_two_directories(tmp_path):
    """Same command, two cwds, same stdout and same exit code."""
    def run(cwd):
        p = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                           cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout

    here = run(ROOT)
    there = run(tmp_path)
    assert here == there

    # And the agreement is not two runs agreeing on having read nothing: the
    # report must name a rule-file count at or above the floor the script
    # refuses below.
    rc, out = here
    assert rc == 0, out
    assert f"of {len(list(LIVE_RULES.glob('*.md')))} rule file(s)" in out


# ============================================================
# Defect 2: no floor, so an empty corpus was a pass
# ============================================================

def test_an_empty_corpus_is_refused_rather_than_passed(monkeypatch, tmp_path):
    """The whole gate pointed at nothing must exit non-zero.

    Driven through `main()` with the real `--check` flag, so this measures the
    exit code a caller sees. Pre-fix `main` printed "inventory check: OK" and
    returned 0 here.
    """
    empty_inv = tmp_path / "inventory"
    empty_rules = tmp_path / "rules"
    empty_inv.mkdir()
    empty_rules.mkdir()
    monkeypatch.setattr(R, "INVENTORY_DIR", empty_inv)
    monkeypatch.setattr(R, "RULES_DIR", empty_rules)
    monkeypatch.setattr(sys, "argv", ["rule_split_check.py", "--check"])

    assert R.main() != 0


def test_a_corpus_missing_only_its_snapshots_is_refused(monkeypatch, tmp_path):
    """The rules tree alone is not a corpus.

    Separated from the case above because the two failures are independent: an
    inventory directory that was renamed leaves the rules tree untouched, and a
    floor that only counted rule files would wave that through.
    """
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    inv.mkdir()
    rules.mkdir()
    for i in range(R.MIN_RULE_FILES + 4):
        (rules / f"rule-{i}.md").write_text("Always set the heading.\n", encoding="utf-8")

    problems, counts = R.corpus_floor(inv, rules)
    assert counts["snapshots"] == 0
    assert counts["rule_files"] == R.MIN_RULE_FILES + 4
    assert any("snapshot" in p for p in problems)


def test_a_corpus_missing_only_its_rules_is_refused(monkeypatch, tmp_path):
    """And the inventory alone is not a corpus either."""
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    inv.mkdir()
    rules.mkdir()
    frozen = "\n".join(f"Always run step {i}." for i in range(R.MIN_FROZEN_DIRECTIVES))
    for name in ("alpha.md", "beta.md"):
        (inv / f"{name}.txt").write_text(frozen + "\n", encoding="utf-8")

    problems, counts = R.corpus_floor(inv, rules)
    assert counts["rule_files"] == 0
    assert any("rule file" in p for p in problems)


def test_an_emptied_snapshot_cannot_hide_behind_a_full_sibling(tmp_path):
    """A snapshot truncated to nothing is a near-empty corpus of its own.

    Per snapshot, not in total: a gutted file inside an otherwise healthy
    inventory is exactly the shape a total-only floor cannot see.
    """
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    inv.mkdir()
    rules.mkdir()
    for i in range(R.MIN_RULE_FILES + 1):
        (rules / f"rule-{i}.md").write_text("Always set the heading.\n", encoding="utf-8")
    (inv / "full.md.txt").write_text(
        "\n".join(f"Always run step {i}." for i in range(R.MIN_FROZEN_DIRECTIVES)) + "\n",
        encoding="utf-8")
    (inv / "gutted.md.txt").write_text("\n\n", encoding="utf-8")

    problems, _ = R.corpus_floor(inv, rules)
    assert any("gutted.md.txt" in p for p in problems)
    assert not any("full.md.txt" in p for p in problems)


# ============================================================
# The floor must not be so high that the live tree fails it,
# nor so low that it is decorative
# ============================================================

def test_the_live_tree_clears_the_floor_and_the_rule_floor_has_headroom(monkeypatch):
    """The live corpus clears both floors, and the rule floor leaves room.

    Only the rule floor. The snapshot floor has no headroom today and this says
    so rather than asserting it away, per the last paragraph below.
    """
    monkeypatch.chdir(ROOT)
    problems, counts = R.corpus_floor()

    assert problems == [], problems
    assert counts["rule_files"] >= R.MIN_RULE_FILES
    assert counts["snapshots"] >= R.MIN_SNAPSHOTS

    # A floor sitting exactly ON the live count reddens on the next legitimate
    # removal, and a gate that reddens for a legitimate change is the gate that
    # gets deleted rather than lowered.
    #
    # Two lines here used to read `assert R.MIN_RULE_FILES <= counts[...]`,
    # which is the comparison two lines above spelled backwards. They asserted
    # nothing the earlier pair had not already asserted, so the headroom this
    # test's name promised was never measured. Found by ruff (SIM300) on
    # 2026-09-02, not by reading.
    assert counts["rule_files"] > R.MIN_RULE_FILES, (
        f"the rule-file floor ({R.MIN_RULE_FILES}) sits on the live count "
        f"({counts['rule_files']}), so removing one rule reddens this gate")

    # The snapshot floor is NOT asserted to have headroom, because it does not:
    # MEASURED 2026-09-02, 2 snapshots against `MIN_SNAPSHOTS = 2`. The honest
    # move is to record that, not to lower the floor so an assertion passes.
    # Only 3 of 26 rules carry a snapshot at all, so the way out is more
    # snapshots and then a raised floor, not a smaller number here.


def test_the_report_states_how_narrow_the_coverage_actually_is(monkeypatch):
    """Coverage is reported, not implied.

    Two snapshots reaching three of twenty six rule files is a true result and a
    narrow one. `.claude/rules/scope-claims.md` asks a tool to state the coverage
    its method established; a bare "OK" states more than this method earns.
    """
    monkeypatch.chdir(ROOT)
    _, counts = R.corpus_floor()
    assert 0 < counts["covered_rule_files"] <= counts["rule_files"]


def test_a_declared_destination_is_not_counted_as_a_covered_rule_file(tmp_path):
    """The coverage number counts rule files, and only rule files.

    A snapshot may name an offload destination anywhere in the repo. Counting
    `docs/DOCS-PIPELINE.md` as a covered rule file would inflate the very figure
    this report exists to keep honest, and it would inflate it in the reassuring
    direction. Caught by mutation: dropping the parent-directory test left every
    assertion in this file green while the live count went from 3 to 4.
    """
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    docs = tmp_path / "docs"
    for d in (inv, rules, docs):
        d.mkdir()
    for i in range(R.MIN_RULE_FILES + 1):
        (rules / f"rule-{i}.md").write_text("Always set the heading.\n", encoding="utf-8")
    (docs / "offloaded.md").write_text("Always run the scan.\n", encoding="utf-8")
    (inv / "rule-0.md.txt").write_text(
        "\n".join(f"Always run step {i}." for i in range(R.MIN_FROZEN_DIRECTIVES)) + "\n",
        encoding="utf-8")
    (inv / "rule-0.md.destinations").write_text("docs/offloaded.md\n", encoding="utf-8")

    problems, counts = R.corpus_floor(inv, rules)
    # The destination IS in the union the drift check reads...
    union = R._rule_union_paths("rule-0.md", rules, inv)
    assert docs / "offloaded.md" in union
    # ...and it is NOT a rule file, so it does not inflate the coverage count.
    assert counts["covered_rule_files"] == 1
    assert any("snapshot" in p for p in problems)  # one snapshot, floor is two


# ============================================================
# The fix must not have bought its greenness by disarming the gate
# ============================================================

def test_a_dropped_directive_is_still_caught_after_the_widening(tmp_path):
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    inv.mkdir()
    rules.mkdir()
    (rules / "widget.md").write_text("You MUST scan the file.\n", encoding="utf-8")
    (inv / "widget.md.txt").write_text(
        "You MUST scan the file.\nNEVER skip the gate.\n", encoding="utf-8")

    assert R.check_inventories(inventory_dir=inv, rules_dir=rules) == [
        ("widget.md", "NEVER skip the gate.")]


def test_check_inventories_still_takes_a_string_rules_dir(tmp_path):
    """Existing callers pass `rules_dir=str(path)`; the default moved, not the type."""
    inv = tmp_path / "inventory"
    rules = tmp_path / "rules"
    inv.mkdir()
    rules.mkdir()
    (rules / "widget.md").write_text("You MUST scan the file.\n", encoding="utf-8")
    (inv / "widget.md.txt").write_text("You MUST scan the file.\n", encoding="utf-8")

    assert R.check_inventories(inventory_dir=inv, rules_dir=str(rules)) == []


@pytest.mark.parametrize("name", ["INVENTORY_DIR", "RULES_DIR"])
def test_the_anchored_globals_are_absolute(name):
    """A relative default is the defect itself, not a stylistic preference."""
    assert getattr(R, name).is_absolute()


def test_the_module_globals_are_still_the_seam_the_signatures_read(monkeypatch, tmp_path):
    """`tests/test_defaults_that_froze_a_path_at_import.py` depends on this.

    The paths became absolute; they must not have become frozen into the
    signatures at import time, which is the sibling defect that file exists for.
    """
    rules = tmp_path / "rules"
    inv = tmp_path / "inventory"
    rules.mkdir()
    inv.mkdir()
    (rules / "navigation.md").write_text("Always set the heading.\n", encoding="utf-8")
    (inv / "navigation.md.txt").write_text(
        "This directive was dropped from the rule.\n", encoding="utf-8")

    monkeypatch.setattr(R, "INVENTORY_DIR", inv)
    monkeypatch.setattr(R, "RULES_DIR", rules)
    assert [stem for stem, _ in R.check_inventories()] == ["navigation.md"]


def test_no_path_is_captured_in_a_default_argument():
    for fn in (R.check_inventories, R._rule_union_sentences, R._rule_union_paths,
               R.corpus_floor):
        defaults = list(fn.__defaults__ or ()) + list((fn.__kwdefaults__ or {}).values())
        assert [d for d in defaults if isinstance(d, Path)] == [], fn.__name__


def test_the_environment_the_measurements_were_taken_in_still_holds():
    """The numbers in this module's docstring are dated measurements.

    If `.claude/rules/` stops being where rules live, the docstring is describing
    a tree that no longer exists and the reader should be told here rather than
    trust it.
    """
    assert LIVE_RULES.is_dir()
    assert os.path.samefile(R.RULES_DIR, LIVE_RULES)
