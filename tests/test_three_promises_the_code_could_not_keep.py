"""Three places where the code and the thing it said were different code paths.

* ``scripts/utils/schedule.py`` installs a 15-minute scheduled task for every
  provisioned exec, on Windows, macOS and Linux, running
  ``scripts/sentinel.py --check``. ``sentinel.py`` never defined ``--check``, so
  argparse exited 2 with "unrecognized arguments" every fifteen minutes and no
  cycle ever ran. The failure is invisible: a systemd timer's non-zero exit only
  reaches the journal. ``--test`` could not stand in - it is a true dry run, so
  it neither sends a notification nor writes state back. Both live callers
  (``scripts/setup.py`` and ``scripts/provision-exec.py``) install it.

* ``scripts/bridge_daemon/sources/action_queue.py`` serialised every write to
  ``queue.json`` with a ``threading.Lock`` under a docstring stating "the daemon
  process is the single writer". That was true until 2026-06-27, when the queue
  went terminal-native: ``action-queue.py``, ``cold-sweep.py`` and
  ``dead-letter.py`` now each import these helpers in their own short-lived
  PROCESS, where a thread lock orders nothing. Two overlapping runs are a lost
  update, and the dangerous direction erases a terminal status - a card stamped
  ``sent`` reverting to ``approved`` is a card the CEO can send twice.

* ``scripts/crm_migrate_to_entity_model.scan_all_contacts`` skipped any exec
  whose contacts directory is absent on this machine, and both callers then
  printed "Scanned N records across all execs". A migration map built from three
  of five execs and described as covering five merges the wrong records.

Run: python3 -m pytest tests/test_three_promises_the_code_could_not_keep.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import multiprocessing
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import action_queue as aq  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# The flag the scheduler installed and the script rejected
# ============================================================

def _sentinel_flags() -> set[str]:
    """Every long option `sentinel.py::main` registers, read from the AST.

    Importing the module runs its top-level workspace bootstrap and building a
    Sentinel touches config and logging, so the parser is read rather than
    constructed. `--help` would also work but shells out for one fact.
    """
    tree = ast.parse((ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                    flags.add(arg.value)
    return flags


def _scheduled_sentinel_args() -> set[str]:
    """Every `script_args` value `install_sentinel_schedule` passes."""
    tree = ast.parse((ROOT / "scripts" / "utils" / "schedule.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "install_sentinel_schedule")
    args: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg == "script_args":
            for elt in getattr(node.value, "elts", []):
                if isinstance(elt, ast.Constant):
                    args.add(elt.value)
    return args


def test_every_flag_the_scheduler_installs_exists_in_sentinel():
    """The defect, as a contract between two files.

    Written as "whatever schedule.py passes must be a flag sentinel.py accepts"
    rather than "--check exists", so the next flag added on one side and not the
    other fails here too.
    """
    installed = _scheduled_sentinel_args()

    assert installed, "the extractor found no scheduled args; it is not reading the call"
    assert installed <= _sentinel_flags(), (
        f"the scheduled task passes {sorted(installed - _sentinel_flags())}, "
        f"which sentinel.py does not accept"
    )


def test_the_extractors_actually_see_both_sides():
    """Pins the contract test. Two empty sets satisfy a subset assertion."""
    assert "--check" in _scheduled_sentinel_args()
    assert {"--check", "--test", "--status", "--stop"} <= _sentinel_flags()


def test_sentinel_accepts_the_flag_when_actually_run(main_clone_only):
    """The AST says the flag is registered; this proves argparse takes it.

    `--check` with no config still parses; it fails later on connection setup,
    which is not what is under test. The old behaviour was exit 2 with
    "unrecognized arguments" BEFORE any of that.

    Spawned rather than called, because the claim is about the script as
    invoked. A child re-imports the real `require_main_clone`, which exits 2
    from a worktree before argparse runs, and no in-process patch reaches it --
    so this is gated on the main clone by `main_clone_only`.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sentinel.py"), "--check", "--help"],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--check" in proc.stdout
    assert "unrecognized arguments" not in proc.stderr


def test_an_unknown_flag_is_still_rejected(main_clone_only):
    """The negative case. A parser that accepts everything would pass the test
    above while accepting the typo that caused this.

    Same child-process gate as the positive case above, and for the same
    reason: exit 2 from the clone guard is indistinguishable from exit 2 from
    argparse, so this must run where the guard passes."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sentinel.py"), "--chekc"],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr


def test_check_is_a_live_cycle_and_test_is_not():
    """The two flags are different requests and must not collapse.

    `--test` was the only "run one cycle" flag and it is a TRUE dry run: state
    is read but never written and notifications are logged, not sent. Using it
    for the scheduled task would have produced a monitor that never notified.
    """
    src = (ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    init = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "__init__"
                and any(a.arg == "once" for a in n.args.args))

    assert {a.arg for a in init.args.args} >= {"dry_run", "once"}
    assert "if self.dry_run or self.once:" in src
    assert "dry_run=args.test" in src
    assert "once=args.check" in src


# ============================================================
# The queue lock that stopped at the process boundary
# ============================================================

def _card(title: str) -> dict:
    return {"action_type": "note", "title": title, "reasoning": "r"}


def _queue_path(root: Path) -> Path:
    return root / aq.QUEUE_FILE


def _titles(root: Path) -> set[str]:
    data = json.loads(_queue_path(root).read_text(encoding="utf-8"))
    return {c.get("title") for c in data["actions"]}


def test_the_lock_is_held_across_processes_not_only_threads(tmp_path):
    """The assertion the old `threading.Lock` could not satisfy.

    Two real processes each append a card. Without a file lock this is a lost
    update whenever their read-modify-writes overlap; the barrier below makes
    them overlap on purpose rather than hoping.
    """
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)

    ctx = multiprocessing.get_context("spawn")
    start = ctx.Barrier(2)
    procs = [ctx.Process(target=_append_in_child, args=(str(root), f"card-{i}", start))
             for i in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    assert _titles(root) == {"card-0", "card-1"}


def _append_in_child(root: str, title: str, start) -> None:
    """Runs in a spawned process. Module-level so it can be pickled."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.bridge_daemon.sources import action_queue as child_aq

    start.wait(timeout=30)
    child_aq.append_cards(Path(root), [{"action_type": "note", "title": title,
                                        "reasoning": "r"}])


def test_a_second_holder_of_the_file_lock_makes_the_write_wait(tmp_path, monkeypatch):
    """Proves the flock is real rather than a no-op wrapper.

    A child process holds the lock file for a beat; the parent's mutation must
    not complete before the child releases it. Without the file lock the parent
    would return immediately.
    """
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)
    lock_path = root / (aq.QUEUE_FILE + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, 1.5))
    holder.start()
    assert ready.wait(timeout=30), "the holder never took the lock"

    began = time.monotonic()
    aq.append_cards(root, [_card("waited")])
    waited = time.monotonic() - began
    holder.join(timeout=30)

    assert waited >= 1.0, f"the write did not wait for the lock ({waited:.2f}s)"
    assert _titles(root) == {"waited"}


def _hold_lock(lock_path: str, ready, seconds: float) -> None:
    """Runs in a spawned process: take the flock, signal, hold, release."""
    import fcntl

    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        ready.set()
        time.sleep(seconds)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def test_a_busy_lock_degrades_loudly_rather_than_hanging(tmp_path, monkeypatch, caplog):
    """`file_lock` is bounded: it gives up and writes UNLOCKED. That is the right
    trade for a Stop hook and the wrong silence for this store, so the degraded
    path must say what it risks."""
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)
    monkeypatch.setattr(aq, "QUEUE_LOCK_WAIT_SECONDS", 0.05)

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    lock_path = root / (aq.QUEUE_FILE + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = ctx.Process(target=_hold_lock, args=(str(lock_path), ready, 1.0))
    holder.start()
    assert ready.wait(timeout=30)

    with caplog.at_level("WARNING", logger=aq.logger.name):
        aq.append_cards(root, [_card("degraded")])
    holder.join(timeout=30)

    assert "writing UNLOCKED" in caplog.text
    assert _titles(root) == {"degraded"}, "a degraded write must still write"


def test_a_quiet_mutation_logs_no_lock_warning(tmp_path, caplog):
    """The negative case for the warning. A line printed every time is noise
    nobody reads by the time it matters."""
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)

    with caplog.at_level("WARNING", logger=aq.logger.name):
        aq.append_cards(root, [_card("quiet")])

    assert "writing UNLOCKED" not in caplog.text


# `annotate_card` needs a field: with none it returns "no fields to annotate"
# BEFORE reaching the lock, so an argument-free call would prove nothing here.
@pytest.mark.parametrize("fn,args,kwargs", [
    ("append_cards", ([{"action_type": "note", "title": "t", "reasoning": "r"}],), {}),
    ("apply_status", ("missing-id", "approved"), {}),
    ("annotate_card", ("missing-id",), {"critique": "x"}),
    ("edit_card", ("missing-id",), {}),
    ("undo_card", ("missing-id",), {}),
])
def test_every_mutator_takes_the_shared_lock(tmp_path, monkeypatch, fn, args, kwargs):
    """Each read-modify-write, not just the one the finding named. A mutator
    left on the bare thread lock is the same defect with a smaller blast
    radius."""
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)
    taken: list[Path] = []

    real = aq._queue_lock

    def _spy(workspace_root):
        taken.append(workspace_root)
        return real(workspace_root)

    monkeypatch.setattr(aq, "_queue_lock", _spy)
    getattr(aq, fn)(root, *args, **kwargs)

    assert taken == [root]


def test_no_mutator_was_left_on_the_bare_thread_lock():
    """Read from the AST, so a mutator added later cannot quietly skip it.

    `_queue_lock` itself is the one `with _LOCK:` that must remain: it is the
    thread half, and `file_lock` is a per-file-description flock that does not
    order two threads of one process.
    """
    tree = ast.parse((ROOT / "scripts" / "bridge_daemon" / "sources"
                      / "action_queue.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "_queue_lock":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.With):
                for item in inner.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Name) and ctx.id == "_LOCK":
                        offenders.append(f"{node.name}:{inner.lineno}")

    assert offenders == [], f"still on the bare thread lock: {offenders}"


def test_the_lock_file_sits_beside_the_queue(tmp_path):
    """A lock in a different directory locks nothing another process will find."""
    root = tmp_path
    _queue_path(root).parent.mkdir(parents=True)

    aq.append_cards(root, [_card("x")])

    assert (root / (aq.QUEUE_FILE + ".lock")).exists()


# ============================================================
# The scan that said "all execs" over the ones it skipped
# ============================================================

mig = _load("crm_migrate_summary", "scripts/crm_migrate_to_entity_model.py")


def test_a_complete_scan_still_says_all_execs():
    """The negative case. A line that always warns is a line nobody reads."""
    assert mig._scan_summary([{}, {}], []) == "Scanned 2 records across all execs."


def test_a_skipped_exec_is_named_and_the_claim_is_withdrawn():
    # Invented slugs. The engine repo is public and carries no real entity, so
    # a test fixture never borrows a colleague's name - the content gate caught
    # exactly that in the first draft of this file.
    out = mig._scan_summary([{}], ["quillon", "brannox"])

    assert "NOT across all execs" in out
    assert "quillon" in out and "brannox" in out
    assert "2 contacts directory(ies)" in out


def test_the_skipped_execs_are_listed_in_a_stable_order():
    """Two runs over the same fleet must produce the same line, or a diff of two
    reports shows a change that did not happen."""
    a = mig._scan_summary([], ["zeta", "alpha", "mu"])
    b = mig._scan_summary([], ["mu", "zeta", "alpha"])

    assert a == b
    assert "alpha, mu, zeta" in a


def test_the_scan_returns_the_slugs_it_could_not_read(tmp_path, monkeypatch):
    """The summary is only honest if the scan reports the skip. Drive the real
    function over a fleet whose overlays are absent."""
    contacts = tmp_path / "ceo" / "crm" / "contacts"
    contacts.mkdir(parents=True)
    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(mig, "get_all_active_exec_slugs", lambda: ["ghost", "absent"])
    monkeypatch.setattr(mig, "get_per_exec_contacts_dir",
                        lambda slug: tmp_path / f"no-such-{slug}" / "crm" / "contacts")

    records, unreadable = mig.scan_all_contacts()

    assert records == []
    assert sorted(unreadable) == ["absent", "ghost"]


def test_an_exec_whose_directory_exists_is_not_reported_as_skipped(tmp_path, monkeypatch):
    """The other direction: a present-but-empty overlay is read, not skipped.

    Without this, `unreadable` could be "every exec" and the two tests above
    would still pass.
    """
    contacts = tmp_path / "ceo" / "crm" / "contacts"
    contacts.mkdir(parents=True)
    present = tmp_path / "exec" / "crm" / "contacts"
    present.mkdir(parents=True)
    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(mig, "get_all_active_exec_slugs", lambda: ["here"])
    monkeypatch.setattr(mig, "get_per_exec_contacts_dir", lambda slug: present)

    _records, unreadable = mig.scan_all_contacts()

    assert unreadable == []


def test_both_callers_print_the_shared_summary():
    """One line so the two commands cannot say different things about the same
    scan, which is how one gets fixed and the other left."""
    src = (ROOT / "scripts" / "crm_migrate_to_entity_model.py").read_text(encoding="utf-8")

    assert src.count("print(_scan_summary(records, unreadable))") == 2
    assert "records across all execs.\")" not in src.replace(
        'return f"Scanned {len(records)} records across all execs."', "")
