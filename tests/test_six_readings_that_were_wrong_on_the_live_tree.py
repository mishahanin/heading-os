"""Six tools that answered wrongly about the tree they were pointed at.

Each was measured wrong on this machine on 2026-08-29, not reasoned about.

1. `install-git-hooks._hooks_dir` spelled `<repo>/.git/hooks` by hand. `.git` is
   a directory only in an ordinary clone; in a linked worktree it is a FILE, and
   this workspace keeps six worktrees, one inside the engine tree. From
   `.claude/worktrees/hdr` the function named a path that does not exist while
   git named the shared one that does and is armed. Install died with
   NotADirectoryError, and `--check` reported armed SECURITY gates as MISSING.

2. `output-organizer.report` scanned only the top level of a tree its own
   `organize --execute` had emptied into subdirectories. It printed
   "Total files: 1" over 6814.

3. `pipeline-summary.normalize_stage` tested `"closed" in s` before `"lost"`, so
   "Closed Lost" returned "won": a dead deal counted as won revenue at full
   weight and dropped out of the pipeline total.

4. `pipeline-summary.parse_table_rows` closed a table only on a blank line or a
   `---` rule, so a `##` heading written directly under the last row left it
   open and the NEXT table's header and separator became data rows. A two-table
   file yielded 4 rows for the first table, 3 of them phantom.

5. `send-email` accepted `--batch` beside `--reply`: argparse allowed it, the
   batch branch ran first and returned, and the requested reply silently never
   happened, exit 0, on the one script allowed to put mail on the wire.

6. `send-email._normalize_addrs` turned `[123]` into `['123']`, which
   exchangelib accepts client-side, so a bogus address travelled to the server
   instead of becoming the `malformed` per-message result the batch contract
   promises.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# 1. `.git` is a file in a linked worktree
# ============================================================

@pytest.fixture(scope="module")
def hooks_mod():
    return _load("install_git_hooks_probe", "scripts/install-git-hooks.py")


@pytest.fixture()
def clone_with_worktree(tmp_path):
    main = tmp_path / "main"
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "t@example.invalid"],
                   check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "Test"], check=True)
    (main / "a.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "first"], check=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--detach",
                    str(linked)], check=True, capture_output=True)
    return main, linked


def test_a_linked_worktree_keeps_git_as_a_file(clone_with_worktree):
    """The fixture's control: without this the test below proves nothing."""
    _main, linked = clone_with_worktree
    assert (linked / ".git").is_file()


def test_the_hooks_dir_of_a_worktree_is_the_shared_one(hooks_mod, clone_with_worktree):
    main, linked = clone_with_worktree
    assert hooks_mod._hooks_dir(linked) == hooks_mod._hooks_dir(main)
    assert hooks_mod._hooks_dir(linked).is_dir()


def test_an_ordinary_clone_still_answers_its_own_hooks(hooks_mod, clone_with_worktree):
    """The other direction: a fix that always returned the worktree's parent
    would break every plain clone."""
    main, _linked = clone_with_worktree
    assert hooks_mod._hooks_dir(main) == main / ".git" / "hooks"


def test_a_directory_that_is_not_a_repo_falls_back(hooks_mod, tmp_path):
    """git answers nothing here, and the caller still needs a path to report
    as missing rather than a crash."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert hooks_mod._hooks_dir(plain) == plain / ".git" / "hooks"


# ============================================================
# 2. a report over a tree it had itself emptied
# ============================================================

def test_the_outputs_report_counts_the_whole_tree(tmp_path, monkeypatch, capsys):
    mod = _load("output_organizer_probe", "scripts/output-organizer.py")
    root = tmp_path / "outputs"
    (root / "images").mkdir(parents=True)
    (root / "documents").mkdir()
    (root / "top.md").write_text("x\n", encoding="utf-8")
    (root / "images" / "a.png").write_bytes(b"x")
    (root / "documents" / "b.pdf").write_bytes(b"x")
    monkeypatch.setattr(mod, "OUTPUTS_DIR", root)

    mod.report()
    out = capsys.readouterr().out
    assert "Total files: 3" in out
    assert "is empty" not in out


def test_an_organised_tree_is_not_called_empty(tmp_path, monkeypatch, capsys):
    """The exact shape after `organize --execute`: nothing at the top level,
    everything one level down. This printed "outputs/ is empty"."""
    mod = _load("output_organizer_probe2", "scripts/output-organizer.py")
    root = tmp_path / "outputs"
    (root / "images").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"x")
    monkeypatch.setattr(mod, "OUTPUTS_DIR", root)

    mod.report()
    out = capsys.readouterr().out
    assert "is empty" not in out
    assert "Total files: 1" in out


def test_a_genuinely_empty_tree_is_still_called_empty(tmp_path, monkeypatch, capsys):
    mod = _load("output_organizer_probe3", "scripts/output-organizer.py")
    root = tmp_path / "outputs"
    root.mkdir()
    monkeypatch.setattr(mod, "OUTPUTS_DIR", root)
    mod.report()
    assert "is empty" in capsys.readouterr().out


def test_organize_still_scans_only_the_top_level():
    """`organize` MOVES top-level files into subdirectories, so recursing would
    re-move what it already organised. The two functions differ on purpose, and
    a sweep that changed both would look like a tidier fix and be wrong."""
    source = (ROOT / "scripts" / "output-organizer.py").read_text(encoding="utf-8")
    body = source.split("def organize(")[1].split("\ndef ")[0]
    assert "OUTPUTS_DIR.iterdir()" in body
    assert "OUTPUTS_DIR.rglob(" not in body


# ============================================================
# 3 + 4. a lost deal booked as won, and a table that never closed
# ============================================================

@pytest.fixture(scope="module")
def pipeline():
    return _load("pipeline_summary_probe", "scripts/pipeline-summary.py")


STAGES = [
    ("Closed Lost", "lost"),
    ("closed-lost", "lost"),
    ("CLOSED LOST", "lost"),
    ("Lost", "lost"),
    ("Closed Won", "won"),
    ("Won", "won"),
    ("closed", "won"),
]


@pytest.mark.parametrize("raw, expected", STAGES, ids=[s for s, _ in STAGES])
def test_a_lost_deal_is_never_a_won_deal(pipeline, raw, expected):
    assert pipeline.normalize_stage(raw) == expected


def test_the_two_verdicts_are_both_reachable(pipeline):
    """A reorder that returned "lost" for everything would satisfy the lost
    cases one at a time."""
    got = {pipeline.normalize_stage(s) for s, _ in STAGES}
    assert got == {"lost", "won"}


HEADER = "| Client | Stage | Value |\n|---|---|---|\n"
ROW = "| Acme | Proposal | $500K |\n"


def test_a_heading_under_the_last_row_closes_the_table(pipeline):
    doc = ("## Active Deals\n" + HEADER + ROW
           + "## Won / Closed\n" + HEADER + "| Globex | Won | $1M |\n")
    rows = pipeline.parse_table_rows(doc, "Active Deals")
    assert rows == [{"Client": "Acme", "Stage": "Proposal", "Value": "$500K"}]


def test_a_note_under_the_last_row_closes_the_table(pipeline):
    doc = ("## Active Deals\n" + HEADER + ROW
           + "See the archive for older deals.\n" + HEADER)
    assert len(pipeline.parse_table_rows(doc, "Active Deals")) == 1


def test_an_ordinary_table_still_parses(pipeline):
    """The other direction. A close-on-everything change would empty every
    table, and every assertion above would still pass."""
    doc = ("## Active Deals\n\n" + HEADER + ROW + "| Beta | Lead | $2K |\n\n")
    assert len(pipeline.parse_table_rows(doc, "Active Deals")) == 2


def test_a_blank_line_still_closes_the_table(pipeline):
    doc = ("## Active Deals\n\n" + HEADER + ROW + "\n" + HEADER
           + "| Ghost | Won | $9 |\n")
    assert len(pipeline.parse_table_rows(doc, "Active Deals")) == 1


# ============================================================
# 5 + 6. the one script allowed to put mail on the wire
# ============================================================

@pytest.fixture(scope="module")
def sender():
    return _load("send_email_probe", "scripts/send-email.py")


BAD_LISTS = [[123], [None], ["ok@example.invalid", 5], [["nested"]]]


@pytest.mark.parametrize("value", BAD_LISTS, ids=[str(v) for v in BAD_LISTS])
def test_a_non_string_address_is_refused_not_stringified(sender, value):
    with pytest.raises(ValueError, match="not strings"):
        sender._normalize_addrs(value)


GOOD = [
    ("a@example.invalid", ["a@example.invalid"]),
    (["a@example.invalid"], ["a@example.invalid"]),
    (["a@example.invalid", "b@example.invalid"],
     ["a@example.invalid", "b@example.invalid"]),
    # `None` in, `None` out: callers test `if not to` and the existing contract
    # keeps the two distinguishable. Not part of this fix, pinned so the fix
    # cannot quietly change it.
    (None, None),
    ([], []),
]


@pytest.mark.parametrize("value, expected", GOOD, ids=[str(v) for v, _ in GOOD])
def test_a_real_address_list_still_passes(sender, value, expected):
    assert sender._normalize_addrs(value) == expected


CONFLICTS = ["--reply", "--reply-all", "--forward"]


@pytest.mark.parametrize("flag", CONFLICTS)
def test_batch_cannot_be_combined_with_a_threaded_flag(flag, tmp_path):
    batch = tmp_path / "batch.json"
    batch.write_text("[]", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "send-email.py"),
         "--batch", str(batch), flag, "--match-subject", "x",
         "--to", "a@example.invalid", "--body", "<p>x</p>", "--dry-run"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 2
    assert "cannot be combined" in proc.stderr


def test_batch_alone_is_still_accepted(tmp_path):
    """The other direction: a guard that refused every --batch would satisfy
    all three cases above and break the batch path entirely."""
    batch = tmp_path / "batch.json"
    batch.write_text("[]", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "send-email.py"),
         "--batch", str(batch), "--dry-run"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "cannot be combined" not in proc.stderr
