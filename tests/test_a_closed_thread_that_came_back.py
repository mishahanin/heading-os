#!/usr/bin/env python3
"""Shard scripts-13-p3: four commands that did something they had not said.

THE UPDATE MANAGER WAS DEAD FOR A DAY. Commit 76c63fd (2026-08-24) added
`if os.name == "posix":` to `scripts/update-manager.py` and no `import os`. The
linter passed, the whole suite passed, and the systemd timer failed at 07:00 the
next morning with `NameError: name 'os' is not defined`. One auto-apply cycle
lost. The test that "covered" that line was a STRING MATCH on the source, which
cannot see a missing import, so it stayed green throughout. Nothing in the repo
answered "does this name exist?" either: `select` in pyproject.toml REPLACES
ruff's default `E4,E7,E9,F`, so pyflakes was off. F821 is now enabled, at a cost
of zero findings. Those fixes and their tests live in
`tests/test_a_tick_that_landed_on_the_wrong_line.py`, beside the string match
they replace.

A CLOSED THREAD CAME BACK. `close` and `hold` removed a thread's line from
`## Active Threads` in MEMORY.md. `thread.py log` then called
`update_thread_hook`, which raises when the line is absent - and the handler for
that raise read it as damage and put the line back. One `log` on a closed thread
silently resurrected it in the index that every session loads, with no `reopen`.
`scripts/memory-hygiene.py` already reported the regrowth and named this writer
as the cause, advisorily, because a gate there would have fired on the next
legitimate write instead of on the mistake.

  SUPERSEDED 2026-08-27. The index itself is gone: the block was retired on
  2026-08-20 on the reader side, and the writer was removed from
  `scripts/thread.py` and `scripts/utils/threads_lib.py` seven days later. The
  status-gated handler this shard added went with it, so the six tests that
  pinned it are replaced below by what still holds - a `log` on a closed thread
  records the event and changes nothing else. The standing guard against the
  index coming back lives in `tests/test_thread_cli.py`
  (`test_no_subcommand_writes_a_memory_index`) and `tests/test_threads_lib.py`
  (`test_the_retired_index_helper_is_gone`).

A DRY RUN CHANGED THE DISK. `thread.py archive-scan` without `--apply` prints
"would archive:" and is a preview. Its `dest_dir.mkdir(parents=True)` sat ABOVE
the `if args.apply:`, so the preview created `threads/archive/<year>/<type>/`.

A TYPO MOVED A CONTACT NOWHERE. `transfer-contact.py --to` was never checked
against the roster. `get_per_exec_repo_path` rejects only path SHAPES, so any
other string became a directory name: a typo created
`../.heading-os-data-<typo>/crm/contacts/`, wrote the contact there, turned the
failed git commit into a yellow warning because a phantom tree is not a repo,
then SUCCEEDED at committing the source deletion in the real repo, printed
"Transfer complete:" and exited 0. `get_all_active_exec_slugs` was imported in
that file and never called.

NOT A DEFECT, checked and dismissed: the shard report claimed `cmd_archive_scan`
could leave a half-archived batch when MEMORY.md is missing. It could not. The
index removal ran BEFORE `shutil.move` in the same iteration, and a missing
MEMORY.md raised FileNotFoundError, which the `except ValueError` there did not
catch, so the first candidate aborted the command before any file moved. Moot
since 2026-08-27: `cmd_archive_scan` no longer opens MEMORY.md at all.

NOTE ON METHOD: nothing here writes outside tmp_path, spawns git, or touches a
real workspace. `transfer-contact` is driven only far enough to reach its own
refusal.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

THREAD_SRC = ROOT / "scripts" / "thread.py"
TRANSFER_SRC = ROOT / "scripts" / "transfer-contact.py"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Finding 3 - log resurrected a closed thread in the index
# ============================================================

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A threads root with the real CLI pointed at it.

    It used to hand back a MEMORY.md as well, and patch `_memory_md` to reach
    it. Both went with the index on 2026-08-27; the thread file is the record.
    """
    th = _load("thread_p13c", "scripts/thread.py")
    threads = tmp_path / "threads"
    (threads / "business").mkdir(parents=True)
    monkeypatch.setattr(th, "_threads_root", lambda: threads)
    # No path here reaches the data root today. Pinned anyway: a mutation
    # that puts the MEMORY.md writer back must land in tmp, not in the
    # operator's live overlay. It did, on 2026-08-27.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return th, threads


def _thread_file(threads, slug, status, title="A thread", last="2026-01-01"):
    """Dates are QUOTED. Unquoted, YAML parses them into `date` objects and
    `date.fromisoformat` then raises TypeError, which `scan_for_archive`
    swallows - so the fixture would silently produce no candidates at all."""
    path = threads / "business" / f"{slug}.md"
    path.write_text(
        f'---\nid: {slug}\ntitle: {title}\ntype: business\nstatus: {status}\n'
        f'classification: private\n'
        f'opened: "2026-01-01"\nlast_touched: "{last}"\n'
        f'links:\n  outputs: []\ntags: []\n---\n\n'
        f"## Log\n\n## Open follow-ups\n",
        encoding="utf-8")
    return path


def _log(th, path, note="something happened"):
    args = type("Args", (), {
        "thread_id": path.stem, "event": note, "artifact": None,
        "decision": None, "follow_up": None, "done": None})()
    return th.cmd_log(args)


@pytest.mark.parametrize("status", ["closed", "on-hold"])
def test_the_log_entry_itself_is_still_written(workspace, status):
    """Logging to a closed thread is legitimate - the record belongs in the
    file. Only the INDEX membership was ever wrong."""
    th, threads = workspace
    path = _thread_file(threads, "quiet-deal", status)
    assert _log(th, path, note="the buyer called back") == 0
    assert "the buyer called back" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["closed", "on-hold", "active"])
def test_a_log_does_not_change_the_status(workspace, status):
    """What the resurrection actually did, restated on the surviving surface.

    Membership of the active set is now decided by one field, `status`, in one
    file. `log` records an event; only `close`, `hold` and `reopen` may move a
    thread in or out. A `log` that touched the status would be the same defect
    in the one place it can still happen.
    """
    th, threads = workspace
    path = _thread_file(threads, "quiet-deal", status)
    assert _log(th, path) == 0
    assert f"status: {status}" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["closed", "on-hold", "active"])
def test_a_log_writes_the_thread_file_and_nothing_else(workspace, tmp_path, status):
    """The handler wrote a SECOND file. There is no second file to write now."""
    th, threads = workspace
    path = _thread_file(threads, "quiet-deal", status)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert _log(th, path) == 0
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before


def test_the_log_handler_no_longer_reaches_a_second_file(workspace):
    """Structural, and asked of the parse tree rather than of the text.

    `cmd_log`'s `except ValueError:` handler is what re-added the closed thread.
    A source grep for the old helper names would also match the comment above
    that explains them, so this walks the function's AST and requires it to call
    exactly the writers that belong there.
    """
    import ast

    tree = ast.parse(THREAD_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_log")
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "write_thread_file" in called
    assert not (called & {"add_thread_to_index", "update_thread_hook",
                          "ensure_active_threads_section", "compose_thread_hook",
                          "_memory_md"}), f"cmd_log reaches the retired index: {called}"
    assert not [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)], (
        "cmd_log grew an exception handler again; the last one turned a missing "
        "index line into a resurrection"
    )


# ============================================================
# Finding 4 - a preview that created a directory
# ============================================================

def _archive_scan(th, apply):
    args = type("Args", (), {"apply": apply})()
    return th.cmd_archive_scan(args)


def _closed_long_ago(threads, slug):
    """Closed and older than the 90-day archive threshold."""
    return _thread_file(threads, slug, "closed", last="2020-01-01")


def test_a_dry_run_creates_no_archive_directory(workspace, capsys):
    """The whole finding: `archive-scan` with no --apply is a preview."""
    th, threads = workspace
    _closed_long_ago(threads, "ancient-deal")
    assert _archive_scan(th, apply=False) == 0
    assert "would archive" in capsys.readouterr().out
    assert not (threads / "archive").exists(), (
        "a preview created a directory on disk")


def test_a_dry_run_moves_nothing(workspace):
    th, threads = workspace
    path = _closed_long_ago(threads, "ancient-deal")
    _archive_scan(th, apply=False)
    assert path.exists()


def test_apply_still_creates_the_directory_and_moves_the_file(workspace):
    """The mkdir moved INSIDE the branch; it must not have been lost."""
    th, threads = workspace
    path = _closed_long_ago(threads, "ancient-deal")
    assert _archive_scan(th, apply=True) == 0
    assert not path.exists()
    moved = list((threads / "archive").rglob("ancient-deal.md"))
    assert len(moved) == 1


def test_the_mkdir_sits_inside_the_apply_branch():
    src = THREAD_SRC.read_text(encoding="utf-8")
    body = src.split("def cmd_archive_scan", 1)[1].split("\ndef ", 1)[0]
    assert body.index("if args.apply:") < body.index("dest_dir.mkdir(")


def test_the_scan_archives_every_candidate_in_one_pass(workspace):
    """Replaces `test_a_missing_memory_file_aborts_before_any_move`.

    That test pinned the ordering inside the loop: the index removal ran before
    `shutil.move`, so a missing MEMORY.md aborted on the first candidate rather
    than leaving a half-archived batch. `cmd_archive_scan` no longer opens
    MEMORY.md, so there is nothing left to abort on and no partial state to
    guard. What is worth keeping is the other half of that claim, never asserted
    at the time: a batch of candidates is archived whole.
    """
    th, threads = workspace
    first = _closed_long_ago(threads, "aaa-deal")
    second = _closed_long_ago(threads, "zzz-deal")
    assert _archive_scan(th, apply=True) == 0
    assert not first.exists() and not second.exists()
    assert len(list((threads / "archive").rglob("*.md"))) == 2


# ============================================================
# Finding 5 - a typo that moved a contact nowhere
# ============================================================

def _transfer(args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(TRANSFER_SRC), *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=120)


def test_an_unknown_target_slug_is_refused():
    """The finding. Any string that is not a path shape became a directory
    name, so a typo wrote the contact into a phantom tree."""
    proc = _transfer(["--contact", "someone", "--from", "misha-hanin",
                      "--to", "marlow-cartre"])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not an active exec or admin slug" in proc.stdout


def test_an_unknown_source_slug_is_refused_too():
    """Both ends. A bad `--from` resolves a phantom source, so the run dies on
    "source contact not found" and names a path that never existed."""
    proc = _transfer(["--contact", "someone", "--from", "nobody-here",
                      "--to", "misha-hanin"])
    assert proc.returncode == 1
    assert "not an active exec or admin slug" in proc.stdout


def test_the_refusal_lists_the_slugs_that_would_work():
    proc = _transfer(["--contact", "someone", "--from", "misha-hanin",
                      "--to", "marlow-cartre"])
    assert "Known slugs:" in proc.stdout


def test_the_refusal_lands_before_anything_is_written():
    """Order is the point: the check has to precede `target_dir.mkdir`, or the
    phantom directory exists by the time the refusal prints."""
    src = TRANSFER_SRC.read_text(encoding="utf-8")
    # The refusal text is split across two f-strings, so match the first half.
    refusal = src.index("is not an active exec ")
    assert refusal < src.index("target_dir.mkdir(")
    assert refusal < src.index("source_path.rename(")


def test_no_phantom_directory_is_left_behind():
    """Behavioural backstop for the ordering test above."""
    parent = ROOT.parent
    before = {p.name for p in parent.iterdir() if p.is_dir()}
    _transfer(["--contact", "someone", "--from", "misha-hanin",
               "--to", "marlow-cartre"])
    after = {p.name for p in parent.iterdir() if p.is_dir()}
    assert after == before, f"the run created {after - before}"


def test_an_admin_slug_is_accepted_even_though_it_is_not_on_the_roster():
    """`get_all_active_exec_slugs()` returns EXECS. The operator running this is
    an admin and is not among them, so a roster-only check would refuse the CEO
    his own transfers. Proven by getting PAST the slug gate: the run reaches
    "Source contact not found", which is the next check.

    The admin slug is RESOLVED, exactly as the roster is read in the test below,
    and for a second reason on top of the leak one. `get_admin_slugs()` answers
    from `admin.json` in the DATA overlay, and falls back to the operator seam
    when no overlay is mounted, so the two workspaces disagree on the string. A
    slug typed in here therefore only resolved on the machine that wrote the
    test: on a clone with no overlay it is not an admin slug, the gate refused
    it, and this test failed for a reason that has nothing to do with the
    carve-out it exists to pin.
    """
    from scripts.utils.workspace import (
        get_admin_slugs, get_all_active_exec_slugs)
    admins = sorted(get_admin_slugs())
    assert admins, "no admin slug resolves in this workspace"
    admin = admins[0]
    assert admin not in get_all_active_exec_slugs(), (
        f"{admin!r} is on the exec roster, so accepting it proves nothing "
        f"about the admin carve-out")
    proc = _transfer(["--contact", "definitely-not-a-real-contact",
                      "--from", admin, "--to", admin])
    assert "not an active exec or admin slug" not in proc.stdout, proc.stdout
    assert "Source contact not found" in proc.stdout


def test_a_known_exec_slug_is_accepted():
    """The other half: a real roster entry must not be refused either.

    The slug is READ from the roster rather than written here. The engine repo
    is public and ships no real entity names, so hardcoding one would be a leak
    - the content guard caught exactly that on the first draft of this test.
    Reading it also keeps the test true when the roster changes.
    """
    from scripts.utils.workspace import get_all_active_exec_slugs
    roster = sorted(get_all_active_exec_slugs())
    if not roster:
        pytest.skip("no execs on the roster in this workspace")
    proc = _transfer(["--contact", "definitely-not-a-real-contact",
                      "--from", "misha-hanin", "--to", roster[0]])
    assert "not an active exec or admin slug" not in proc.stdout, proc.stdout


def test_the_refusal_stops_the_run_rather_than_warning():
    """Dropping the exit would leave a printed complaint and a run that carries
    on into the phantom path - the defect with a message attached."""
    proc = _transfer(["--contact", "someone", "--from", "misha-hanin",
                      "--to", "marlow-cartre"])
    assert proc.returncode == 1
    assert "Source contact not found" not in proc.stdout, (
        "the run continued past its own refusal")
    assert "Transfer complete" not in proc.stdout


def test_the_roster_resolver_is_actually_called():
    """It was imported and unused, which is why the gap survived review: the
    name being present made the file look as though it validated."""
    import ast
    tree = ast.parse(TRANSFER_SRC.read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "get_all_active_exec_slugs" in calls


def test_the_remaining_unused_imports_are_named_not_deleted():
    """`get_corporate_repo_path`, `get_per_exec_repo_path` and the
    `workspace_root` local are still unused. They predate this change and are
    not orphaned BY it, so per the restraint rule they stay and are named here
    rather than swept into an unrelated fix. If they are ever removed, remove
    this test with them."""
    src = TRANSFER_SRC.read_text(encoding="utf-8")
    assert "get_corporate_repo_path" in src
    assert "workspace_root = get_workspace_root()" in src
