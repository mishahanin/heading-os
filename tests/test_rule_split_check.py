import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scripts.rule_split_check import extract_imperatives, check_split, SEED
from scripts.rule_split_check import _norm, check_inventories
from tests.repo_files import read_sources


def _n(t):
    return re.sub(r"\s+", " ", t).strip()


def test_extractor_recall_on_markdown_fixture():
    got = [_n(g) for g in extract_imperatives(SEED)]
    for needle in ["Use `pathlib.Path` objects, not string paths.", "You MUST run the scan.",
                   "NEVER pass --no-verify.", "**Do not** delete pre-existing dead code.",
                   "Every skill must have: name, description, version.",
                   "DO NOT execute any CRM writes.", "Always pin exact versions.",
                   "Avoid bare except."]:
        assert any(_n(needle) in g for g in got), f"extractor missed: {needle!r}"


def test_extractor_catches_real_flagship_directives():
    # H1: exercise the extractor against REAL rule grammar. GLOB the union of
    # development-standards*.md so the test survives the Wave 1 split (k3 1.2): pre-split
    # it matches one file, post-split it matches core+detail, and the moved directives
    # stay in the union either way. Reading only the live core file would self-destruct
    # the moment Step 13 moves "must have"/"Use pathlib.Path" into the detail file.
    import glob
    text = "\n".join(pathlib.Path(p).read_text(encoding="utf-8")
                     for p in sorted(glob.glob(".claude/rules/development-standards*.md")))
    imps = [_n(i) for i in extract_imperatives(text)]
    for probe in ["must have", "must follow", "must include", "Use `pathlib.Path`"]:
        assert any(_n(probe) in i for i in imps), f"flagship directive missed: {probe!r}"


def test_check_split_detects_loss_and_pass():
    original = "You MUST scan. NEVER skip the gate."
    assert check_split(original, ["You MUST scan.", "NEVER skip the gate."]) == []
    assert check_split(original, ["You MUST scan."]) == ["NEVER skip the gate."]


def test_check_split_tolerates_newline_span():
    # H2: a directive reassembled as its own sentence across a hard wrap is NOT lost.
    # Surrounding prose is sentence-terminated (realistic rule grammar), so the wrapped
    # directive reassembles as a standalone sentence and matches exactly.
    original = "You MUST scan the file before commit."
    wrapped_successor = "Preamble.\nYou MUST scan the\nfile before commit.\nMore prose."
    assert check_split(original, [wrapped_successor]) == []


def test_check_split_rejects_inversion():
    # k3 #1: a dropped directive that is a substring of an OPPOSITE successor sentence
    # must be reported lost, not silently accepted (the old substring check passed this).
    assert check_split("Use caching.", ["Do not use caching."]) == ["Use caching."]


def test_check_split_rejects_leading_context_absorption():
    # k3 #1/#2: a directive absorbed as a non-leading fragment of a longer successor
    # sentence is reported lost (old substring-in-blob wrongly passed this).
    assert check_split("Run the suite.", ["Please Run the suite. Deploy after."]) == ["Run the suite."]


def test_check_split_rejects_cross_sentence_fabrication():
    # k3 #2: a directive must not be stitched together from fragments of two different
    # successor sentences.
    lost = check_split("Run the suite. Always.", ["We Run the suite here.", "Always deploy on green."])
    assert "Run the suite." in lost


def test_check_split_count_aware_catches_dropped_duplicate():
    # k3 #2/#4: a directive stated twice, with one copy dropped, is reported lost even
    # though an identical copy survives (count-aware membership, not bare set membership).
    assert check_split("Skip. Skip.", ["Skip."]) == ["Skip."]


def test_norm_is_negation_invariant():
    # k3 #4: _norm is the linchpin. It collapses whitespace ONLY — it must preserve case
    # and punctuation, so a directive never normalizes equal to its own negation.
    assert _norm("Use caching.") != _norm("Do not use caching.")
    assert _norm("  USE  the\n scan.  ") == "USE the scan."
    assert _norm("Never send.") != _norm("Always send.")


def test_check_inventories_guards_against_drop(tmp_path):
    # Task 1e / D4: a snapshot frozen at split time must fail --check if a later edit
    # drops one of its directives. The snapshot survives across the core+detail union.
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "widget.md").write_text("You MUST scan the file.\n", encoding="utf-8")
    (rules / "widget-detail.md").write_text("NEVER skip the gate.\n", encoding="utf-8")
    inv = tmp_path / "inv"
    inv.mkdir()
    (inv / "widget.md.txt").write_text("You MUST scan the file.\nNEVER skip the gate.\n", encoding="utf-8")

    # Both directives present across the union -> clean.
    assert check_inventories(inventory_dir=inv, rules_dir=str(rules)) == []

    # Drop the detail directive -> --check must flag it.
    (rules / "widget-detail.md").write_text("some unrelated prose.\n", encoding="utf-8")
    bad = check_inventories(inventory_dir=inv, rules_dir=str(rules))
    assert bad == [("widget.md", "NEVER skip the gate.")]


def test_committed_inventories_match_the_live_rules():
    # The same assertion CI's `sovereignty guards` job makes, run here so the drift is
    # caught by the pre-push suite instead of by a red CI email after the push. It was
    # CI-only until 2026-08-10, and an edit to documentation.md's migration-cruft table
    # sat red on main until someone read the notification.
    repo = pathlib.Path(__file__).resolve().parents[1]
    inventory = repo / "config/rule-split-inventory"

    # `check_inventories` globs `<dir>/*.txt` and returns [] when the glob is
    # empty, which is byte-for-byte the answer it gives over a clean tree. A
    # renamed directory, a snapshot deleted with the rule it guarded, or a `.txt`
    # convention that moves, all report "no directive was dropped" while reading
    # nothing at all. Floors first, per snapshot rather than in total, so one
    # emptied file cannot hide behind a full sibling.
    snapshots = sorted(inventory.glob("*.txt"))
    assert len(snapshots) >= 2, (
        f"only {len(snapshots)} rule-split snapshots under {inventory}; the "
        f"drift check below would pass by having nothing to check")
    # The floor above is per-snapshot on purpose, so a snapshot silently dropped
    # because it vanished between the glob and the read would be exactly the
    # emptied file hiding behind a full sibling that this loop exists to catch.
    # COUNT, not scan: read through `read_sources` for the walk/read race, retry
    # once, then FAIL naming the snapshot rather than skip past it.
    lost = []
    read = list(read_sources(snapshots, lost))
    if lost:
        still_gone = []
        read += list(read_sources(lost, still_gone))
        assert not still_gone, (
            "rule-split snapshot(s) disappeared between the glob and the read "
            "and are still gone on retry; the per-snapshot floor cannot be "
            "asserted over a file nobody read: "
            + ", ".join(str(p) for p in still_gone))
    for snap, text in read:
        frozen = [ln for ln in text.splitlines() if ln.strip()]
        assert len(frozen) >= 5, f"{snap.name} froze only {len(frozen)} directives"

    bad = check_inventories(inventory_dir=inventory,
                            rules_dir=str(repo / ".claude/rules"))
    assert bad == [], (
        "rule-split inventory drift: a snapshotted directive is no longer a sentence of "
        "its rule file. Review the edit; if no directive was actually lost, re-freeze "
        "with `python scripts/rule_split_check.py --snapshot .claude/rules/<file>.md`. "
        f"Dropped: {bad}"
    )


def test_a_directive_offloaded_to_a_declared_destination_is_not_a_loss(tmp_path):
    """A rule may offload a directive OUT of .claude/rules/ without losing it.

    The union was `rules_dir/<base>*.md` only, so it saw a rule SPLIT into a
    sibling but not a rule OFFLOADED into docs/ or reference/. On 2026-08-20
    documentation.md moved its propagation chain into docs/DOCS-PIPELINE.md and
    four frozen directives read as dropped while every one was alive at the
    destination — the false positive that pressures the next person to re-freeze
    the snapshot, which is how this guard would decay into a rubber stamp.
    """
    rules = tmp_path / "rules"
    docs = tmp_path / "docs"
    inv = tmp_path / "inv"
    for d in (rules, docs, inv):
        d.mkdir()
    (rules / "widget.md").write_text("You MUST scan the file.\n", encoding="utf-8")
    (docs / "detail.md").write_text("NEVER skip the gate.\n", encoding="utf-8")
    (inv / "widget.md.txt").write_text(
        "You MUST scan the file.\nNEVER skip the gate.\n", encoding="utf-8")

    # Without the declaration the offloaded directive reads as dropped.
    assert check_inventories(inventory_dir=inv, rules_dir=str(rules)) == [
        ("widget.md", "NEVER skip the gate.")]

    # Declaring the destination resolves it.
    (inv / "widget.md.destinations").write_text(
        "# comment ignored\n\ndocs/detail.md\n", encoding="utf-8")
    assert check_inventories(inventory_dir=inv, rules_dir=str(rules)) == []

    # And the guard still bites: delete it from the destination and it is a loss
    # again. A destination declaration must not become a blanket exemption.
    (docs / "detail.md").write_text("unrelated prose.\n", encoding="utf-8")
    assert check_inventories(inventory_dir=inv, rules_dir=str(rules)) == [
        ("widget.md", "NEVER skip the gate.")]


def test_a_declared_destination_that_does_not_exist_is_not_a_free_pass(tmp_path):
    """Naming a file that is not there must not silence the check.

    Otherwise the cheapest way past this guard is a typo.
    """
    rules = tmp_path / "rules"
    inv = tmp_path / "inv"
    for d in (rules, inv):
        d.mkdir()
    (rules / "widget.md").write_text("You MUST scan the file.\n", encoding="utf-8")
    (inv / "widget.md.txt").write_text(
        "You MUST scan the file.\nNEVER skip the gate.\n", encoding="utf-8")
    (inv / "widget.md.destinations").write_text("docs/nope.md\n", encoding="utf-8")

    assert check_inventories(inventory_dir=inv, rules_dir=str(rules)) == [
        ("widget.md", "NEVER skip the gate.")]
