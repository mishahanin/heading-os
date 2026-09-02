#!/usr/bin/env python3
"""Four defects in the one hook that opens every session, all measured 2026-08-31.

`.claude/hooks/session-start.py` is the workspace's alert surface. It exits 0 and
prints plain text, so when it goes wrong the operator sees a healthy-looking
session with nothing on it. Every defect below is invisible from the operator's
chair for exactly that reason.

**1. A non-object `.workspace-identity.json` killed the whole hook.**
`get_workspace_type` returned whatever `json.loads` produced. `json.loads`
succeeds on any well-formed JSON, so `[]`, `"x"`, `3` and `null` were handed to
four callers that all open with `identity.get(...)`. Measured by loading the file
and calling it against a `[]` identity:

    get_workspace_type([])   -> []
    check_sync_status          AttributeError 'list' object has no 'get'
    check_corporate_updates    AttributeError
    check_dep_update_marker    AttributeError
    _setup_wizard_banner       AttributeError

Driven as a real child with `{"cwd": <tree with []>}`: exit 1, traceback,
nothing delivered. Sync failure, corporate update, dependency marker, CRM red
debt, stale context and the thread panel all gone. This is the "fix that landed
in one of two copies" shape at its sixth copy:
`scripts/utils/workspace.get_workspace_identity` was given this same shape check
on 2026-08-30 (`tests/test_five_loaders_that_crashed_on_the_file_they_promised_to_survive.py`)
and the hook was missed. `_setup_wizard_banner` carried the same read with a
narrower handler still, catching only `json.JSONDecodeError`, so an unreadable or
undecodable file killed the hook at its first statement too.

**2. The internal timeout budget equalled the registered one.**
Claude Code DISCARDS the output of a hook that outruns its registered timeout.
All four settings files register this hook at 15 seconds, and the two subprocess
timeouts inside it were 5 (`apply-wizard-answers.py --status`) and 10
(`crm-health.py`), which is exactly 15. Everything the hook does around them was
therefore over budget. Measured against an exec-workspace scratch tree with both
children sleeping 600 s: 14.50 s wall, with a degenerate tail (no context
directory, no importable `scripts/utils`). On the operator's live tree the tail
measured 0.188 s and the registered `python3 -c` launcher 0.04 s, so the real
worst case was 15.23 s: past the wall, in silence. Cut to 3 + 8 with 4 for the
tail. The children measured 0.07 s and 0.29 s when healthy, so the cuts leave
43x and 27x their real cost.

**3. An unlocked read-modify-write of `.sync/last-update.json`.**
The write was atomic; the read and the write together were not, and no lock was
taken. Measured by firing 12 concurrent hooks at one fresh `notified: false`
marker on an exec-workspace scratch tree, five trials: 2, 2, 1, 2, 2 banners.
Nothing is lost, so this is milder than the statusline case that motivated
`locked_state`; a duplicated banner is still the operator being told twice. Six
trials after the fix: 1, 1, 1, 1, 1, 1.

**4. The thread panel asserted recency for rows whose date it could not read.**
A thread whose `last_touched` fails `date.fromisoformat` gets `age = None` from
an explicit `except (TypeError, ValueError)`, and was then counted into the head
sentence "Showing the N ... touched in the last 14 days". Measured with one good
thread and one carrying `last_touched: "sometime"`:

    Active threads: 2 active. Showing the 2 touched in the last 14 days.
    - business/b-broken - Broken (no date)
    - business/a-good - Good (2d)

The row says "(no date)", so a careful reader could catch it. The sentence still
claimed what the handler had just failed to establish, which is what
`.claude/rules/scope-claims.md` exists to stop. The head now says ", 1 with no
readable date", the way it already names `quiet` and `unreadable`.

Every guard here carries both directions. A shape check that refuses everything
is not a shape check, a timeout bound that no run reaches proves nothing, a lock
test that never sees contention is green over an empty corpus, and a head that
always mentions undated rows would be a different lie.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session-start.py"

sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_default_tz  # noqa: E402

TEMPLATES = (
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
)
LIVE = ".claude/settings.local.json"


@pytest.fixture(autouse=True)
def _sys_path_restored():
    """The hook puts trees it resolves onto `sys.path`. Correct in the hook,
    where the entry dies with the process; run in-process from here it would
    outlive the test and hold for the rest of the xdist worker."""
    saved = sys.path[:]
    try:
        yield
    finally:
        sys.path[:] = saved


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_identity", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules["session_start_identity"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# Scratch workspaces. Nothing here reaches the operator's tree.
# ============================================================

def _workspace(tmp_path, identity_body: str | bytes | None,
               *, wizard_pct: int | None = None,
               wizard_sleep: bool = False, crm_sleep: bool = False) -> Path:
    """An exec-shaped scratch tree, optionally with stub child scripts."""
    root = tmp_path / "ws"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    if identity_body is not None:
        path = root / ".workspace-identity.json"
        if isinstance(identity_body, bytes):
            path.write_bytes(identity_body)
        else:
            path.write_text(identity_body, encoding="utf-8")
    if wizard_pct is not None:
        (root / "scripts" / "apply-wizard-answers.py").write_text(
            f'import json\nprint(json.dumps({{"completion_pct": {wizard_pct}}}))\n',
            encoding="utf-8")
    if wizard_sleep:
        (root / "scripts" / "apply-wizard-answers.py").write_text(
            "import time\ntime.sleep(600)\n", encoding="utf-8")
    if crm_sleep:
        (root / "scripts" / "crm-health.py").write_text(
            "import time\ntime.sleep(600)\n", encoding="utf-8")
    return root


def _child_env(root: Path, **extra) -> dict:
    """Child env with the data root and the threads root pointed at scratch.

    A child resolves where it reads and writes through `get_data_root()`, which
    reads HEADING_OS_DATA, and the panel reads THREADS_ROOT. Without both, these
    subprocess runs would walk the operator's live overlay.
    """
    env = dict(os.environ,
               HEADING_OS_DATA=str(root / "data"),
               THREADS_ROOT=str(root / "no-threads-here"))
    # `_setup_wizard_banner` returns before doing anything when CI is "true", so
    # a CI runner would skip the very branch half of these tests measure.
    env.pop("CI", None)
    env.update(extra)
    return env


def _run(root: Path, *, timeout: int = 120, **env_extra):
    payload = json.dumps({"cwd": str(root)})
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True,
        text=True, timeout=timeout, env=_child_env(root, **env_extra), cwd=str(root))


# ============================================================
# 1. The identity file must not be able to kill the hook
# ============================================================

NON_OBJECTS = ("[]", '"a string"', "42", "null", '["role", "slug"]')


@pytest.mark.parametrize("body", NON_OBJECTS)
def test_a_non_object_identity_file_no_longer_crashes_the_hook(tmp_path, body):
    """The measured case, driven as a real child the way Claude Code drives it."""
    root = _workspace(tmp_path, body)
    proc = _run(root)
    assert proc.returncode == 0, (
        f"identity {body} exited {proc.returncode}:\n{proc.stderr}")
    assert "Traceback" not in proc.stderr, (
        f"identity {body} crashed the hook:\n{proc.stderr}")
    assert "AttributeError" not in proc.stderr


@pytest.mark.parametrize("body", NON_OBJECTS)
def test_a_non_object_identity_file_still_delivers_the_alerts(tmp_path, body):
    """Not crashing is half of it. The hook's whole job is the alert block, and
    an exit-0 hook that prints nothing is the failure this file exists to end."""
    root = _workspace(tmp_path, body)
    proc = _run(root)
    assert "Session alerts:" in proc.stdout, (
        f"identity {body} produced no alert block at all:\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr}")
    assert "CONTEXT STALENESS NOT CHECKED" in proc.stdout, (
        "the staleness alert did not survive the degraded identity: "
        f"{proc.stdout!r}")


@pytest.mark.parametrize("body", NON_OBJECTS)
def test_a_non_object_identity_file_says_so_on_stderr(tmp_path, body):
    """Degrading silently would leave a hand-edited identity file undiagnosable.
    Both readers announce, because both used to die instead."""
    root = _workspace(tmp_path, body)
    proc = _run(root)
    assert "not an object" in proc.stderr, proc.stderr


@pytest.mark.parametrize("body", NON_OBJECTS)
def test_get_workspace_type_returns_the_documented_default(hook, tmp_path, body):
    """In-process, at the reader itself: the documented legacy default, not the
    parsed nonsense."""
    root = _workspace(tmp_path, body)
    identity = hook.get_workspace_type(str(root))
    assert identity == {"role": "admin", "slug": "misha-hanin",
                        "type": "ceo-master"}


def test_a_well_formed_identity_is_still_returned_verbatim(hook, tmp_path):
    """The other direction. A shape check that refuses everything is not a shape
    check, and returning the default for a VALID exec file would masquerade an
    exec workspace as the CEO."""
    root = _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}')
    assert hook.get_workspace_type(str(root)) == {
        "role": "exec", "slug": "jamesbond", "type": "exec-workspace"}


def test_an_absent_identity_file_is_still_the_legacy_ceo_default(hook, tmp_path):
    """Absent is not corrupt. `.workspace-identity.json` is gitignored, so a
    fresh clone has none, and that means legacy ceo-master."""
    root = _workspace(tmp_path, None)
    assert hook.get_workspace_type(str(root))["type"] == "ceo-master"


@pytest.mark.parametrize("body", NON_OBJECTS)
def test_the_four_identity_consumers_survive_the_degraded_identity(
        hook, tmp_path, body):
    """The four `.get` calls that raised. Each is exercised against what the
    reader now hands them, so a future reader that stops guarding is caught
    here and not by an operator with a blank session."""
    root = _workspace(tmp_path, body)
    identity = hook.get_workspace_type(str(root))
    assert hook.check_sync_status(str(root), identity) is None
    assert hook.check_corporate_updates(str(root), identity) is None
    assert hook.check_dep_update_marker(str(root), identity) is None
    hook._setup_wizard_banner(root)   # must return, not raise


def test_the_consumers_still_fire_for_a_real_exec_workspace(hook, tmp_path):
    """The other direction for the three exec-gated checks: degrading must not
    become never reporting."""
    root = _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}')
    identity = hook.get_workspace_type(str(root))
    assert "No sync state found" in hook.check_sync_status(str(root), identity)

    (root / ".sync").mkdir()
    (root / ".sync" / "dep-update-pending.json").write_text("{}", encoding="utf-8")
    (root / "corporate").mkdir()
    (root / "corporate" / "requirements.txt").write_text("x==1\n", encoding="utf-8")
    assert "DEP UPDATE" in hook.check_dep_update_marker(str(root), identity)


@pytest.mark.parametrize("body", [b'{"type": "exec-\xff"}', b'\xff\xfe\x00'])
def test_an_undecodable_identity_file_degrades_rather_than_crashing(tmp_path, body):
    """`read_text` raises UnicodeDecodeError, which is a ValueError and so was
    covered by neither `json.JSONDecodeError` nor `OSError`. It killed the hook
    at its first statement."""
    root = _workspace(tmp_path, body)
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "Session alerts:" in proc.stdout, proc.stdout


def test_the_setup_banner_still_fires_for_a_genuinely_unfinished_workspace(
        hook, tmp_path, capsys):
    """The other direction for `_setup_wizard_banner`: the guards above must not
    have turned the banner off. A stub status script reports 40%."""
    root = _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}',
        wizard_pct=40)
    os.environ.pop("HEADING_OS_WIZARD_QUIET", None)
    saved_ci = os.environ.pop("CI", None)
    try:
        hook._setup_wizard_banner(root)
    finally:
        if saved_ci is not None:
            os.environ["CI"] = saved_ci
    out = capsys.readouterr().out
    assert "not fully set up (40%)" in out, out


def test_the_setup_banner_stays_quiet_for_a_finished_workspace(hook, tmp_path,
                                                              capsys):
    root = _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}',
        wizard_pct=100)
    saved_ci = os.environ.pop("CI", None)
    try:
        hook._setup_wizard_banner(root)
    finally:
        if saved_ci is not None:
            os.environ["CI"] = saved_ci
    assert capsys.readouterr().out == ""


# ============================================================
# 2. The internal budget must fit inside the registered timeout
# ============================================================

def _registered_timeout(rel: str) -> int | None:
    path = ROOT / rel
    if not path.is_file():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for block in (cfg.get("hooks") or {}).get("SessionStart", []):
        for entry in block.get("hooks", []):
            if "session-start.py" in entry.get("command", ""):
                return entry.get("timeout")
    return None


@pytest.mark.parametrize("rel", TEMPLATES)
def test_each_template_registers_the_timeout_the_hook_budgets_for(hook, rel):
    """The hook's arithmetic is written against a number it does not own. When
    the two drift, the budget is correct by coincidence, which is exactly how
    `checkpoint-offer.py` came to budget for 90 against a possible 60."""
    registered = _registered_timeout(rel)
    assert registered is not None, (
        f"{rel} no longer registers session-start.py on SessionStart, or the "
        "hook moved; this guard cannot see the number it is holding")
    assert registered == hook.REGISTERED_TIMEOUT_SECONDS, (
        f"{rel} registers timeout={registered} while the hook budgets for "
        f"{hook.REGISTERED_TIMEOUT_SECONDS}. Claude Code discards the output of "
        "a hook that outruns its registration, so every alert would be lost.")


def test_the_live_settings_agree_too_when_present(hook):
    """`settings.local.json` is gitignored, so absent on a fresh clone. Checked
    when it is there, because it is the file that actually runs."""
    registered = _registered_timeout(LIVE)
    if registered is None:
        pytest.skip("no local settings on this machine")
    assert registered == hook.REGISTERED_TIMEOUT_SECONDS


def test_the_declared_budget_leaves_room_for_the_rest_of_the_hook(hook):
    """The arithmetic, stated once so it cannot be reasoned about twice.

    5 + 10 summed to exactly the registered 15, leaving nothing for the
    staleness scan, the thread panel, the print and the launcher.
    """
    subprocesses = (hook.WIZARD_STATUS_TIMEOUT_SECONDS
                    + hook.CRM_HEALTH_TIMEOUT_SECONDS)
    assert hook.TAIL_BUDGET_SECONDS > 0
    assert subprocesses + hook.TAIL_BUDGET_SECONDS == hook.REGISTERED_TIMEOUT_SECONDS, (
        f"{hook.WIZARD_STATUS_TIMEOUT_SECONDS} + "
        f"{hook.CRM_HEALTH_TIMEOUT_SECONDS} + {hook.TAIL_BUDGET_SECONDS} != "
        f"{hook.REGISTERED_TIMEOUT_SECONDS}; the budget no longer adds up")
    assert subprocesses < hook.REGISTERED_TIMEOUT_SECONDS, (
        "the two child timeouts alone consume the whole registration, which is "
        "the 2026-08-31 defect exactly")


def test_the_two_subprocess_calls_pass_the_declared_constants(hook):
    """Named constants that no call site reads are documentation, not a budget.

    Asked of the AST, because a `timeout=10` literal beside a constant declaring
    8 is precisely the drift the constants exist to prevent, and it reads as
    fixed.
    """
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "run"
                and isinstance(target.value, ast.Name)
                and target.value.id == "subprocess"):
            continue
        for kw in node.keywords:
            if kw.arg == "timeout":
                assert isinstance(kw.value, ast.Name), (
                    "a subprocess timeout is a literal again at line "
                    f"{kw.value.lineno}; the declared budget no longer binds it")
                found.append(kw.value.id)
    assert sorted(found) == ["CRM_HEALTH_TIMEOUT_SECONDS",
                             "WIZARD_STATUS_TIMEOUT_SECONDS"], (
        f"expected exactly the two budgeted child calls, found {found}. A new "
        "subprocess in this hook needs a slice of the budget above.")


@pytest.mark.slow
def test_a_hook_whose_children_both_hang_is_bounded_by_the_declared_budget(
        hook, tmp_path):
    """The measurement, not the arithmetic. Both children sleep 600 s.

    Bounded from BELOW as well as above: a run that finishes early would mean
    the children were never reached, and then this test measures nothing.
    """
    root = _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}',
        wizard_sleep=True, crm_sleep=True)
    budget = (hook.WIZARD_STATUS_TIMEOUT_SECONDS
              + hook.CRM_HEALTH_TIMEOUT_SECONDS)
    started = time.monotonic()
    proc = _run(root, timeout=120)
    elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr
    assert elapsed >= budget - 0.5, (
        f"finished in {elapsed:.2f}s, under the {budget}s the two hung children "
        "should have cost; they were not reached, so this measures nothing")
    assert elapsed <= budget + 1.5, (
        f"took {elapsed:.2f}s against a declared child budget of {budget}s. The "
        f"hook is registered at {hook.REGISTERED_TIMEOUT_SECONDS}s and its "
        "output is discarded past that.")
    assert "CRM HEALTH CHECK NOT RUN" in proc.stdout, (
        "the hung CRM check did not report itself: " + proc.stdout)


# ============================================================
# 3. The workspace-update marker is read and written under a lock
# ============================================================

def _update_marker(root: Path) -> Path:
    (root / ".sync").mkdir(exist_ok=True)
    marker = root / ".sync" / "last-update.json"
    marker.write_text(json.dumps({
        "notified": False, "version": "9.9", "build": "77",
        "summary": "scratch", "applied_at": "2026-08-31T00:00:00",
    }), encoding="utf-8")
    return marker


def _exec_tree(tmp_path) -> Path:
    return _workspace(
        tmp_path, '{"role": "exec", "slug": "jamesbond", "type": "exec-workspace"}')


def test_the_marker_read_modify_write_sits_inside_a_lock(hook):
    """Asked of the AST, so a lock acquired and then released before the write
    cannot pass. The mutation this catches is the original code: read, decide,
    write, no lock anywhere.
    """
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))

    def _marks_notified(node) -> bool:
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            for target in sub.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "notified"):
                    return True
        return False

    assert _marks_notified(tree), (
        "nothing in this hook sets `notified` any more; this guard has lost its "
        "subject and would pass over an absent defect")

    locked = [node for node in ast.walk(tree)
              if isinstance(node, ast.With)
              and any(isinstance(item.context_expr, ast.Name)
                      and item.context_expr.id == "lock"
                      for item in node.items)
              and _marks_notified(node)]
    assert len(locked) == 1, (
        "the read-modify-write of .sync/last-update.json is not inside a "
        "`with lock:` block. Two sessions starting together both read "
        "`notified: false` and both print the banner; measured 2026-08-31, "
        "2 banners from 12 concurrent hooks in 4 of 5 trials.")

    # And the write has to be inside it too, not merely the read.
    writes = [sub for sub in ast.walk(locked[0])
              if isinstance(sub, ast.Call)
              and isinstance(sub.func, ast.Attribute)
              and sub.func.attr == "replace"]
    assert writes, (
        "the lock covers the read but not the `os.replace` that ends the "
        "read-modify-write, which is the same race with a shorter window")

    # `with lock:` over a name bound to `contextlib.nullcontext()` is the shape
    # of a lock and none of the guarantee. The first version of this test read
    # only the `with`, and that mutation survived it, so the binding is checked
    # too: `lock` must be able to be a real `file_lock`.
    bindings = [node for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "lock"
                        for t in node.targets)]
    assert len(bindings) == 1, f"expected one `lock` binding, found {len(bindings)}"
    calls = {sub.func.attr for sub in ast.walk(bindings[0].value)
             if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)}
    assert "file_lock" in calls, (
        "`lock` is never bound to file_lock, so the `with` above holds nothing. "
        f"It calls {sorted(calls)}.")


def test_the_lock_comes_from_the_shared_primitive(hook):
    """`file_lock`, not a second copy. A local reimplementation is the one that
    stops being fixed, and this hook now has an optional-import path for it."""
    src = HOOK.read_text(encoding="utf-8")
    assert "file_lock(" in src
    assert "_load_checkpoint_paths()" in src
    assert "import fcntl" not in src, (
        "the hook grew its own flock; use scripts/utils/checkpoint_paths")


def test_a_held_lock_makes_the_hook_wait_and_say_so(tmp_path):
    """Deterministic contention: the test holds the sidecar lock itself.

    `file_lock` is bounded rather than blocking, so the hook waits, reports
    `busy`, and proceeds. Seeing that line is proof the hook took the lock path
    at all, without depending on a race landing.
    """
    fcntl = pytest.importorskip("fcntl")
    root = _exec_tree(tmp_path)
    marker = _update_marker(root)
    lock_path = marker.with_name(marker.name + ".lock")

    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        proc = _run(root, timeout=120)
        elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr
    assert "busy" in proc.stderr, (
        "the hook did not wait on the marker lock, so nothing serialises the "
        f"read-modify-write:\n{proc.stderr}")
    assert elapsed >= 1.5, (
        f"returned in {elapsed:.2f}s while the lock was held; it cannot have "
        "tried to take it")
    # Degraded, never lost. A banner suppressed because a lock was busy would be
    # a worse defect than the duplicate this fix removes.
    assert "WORKSPACE UPDATE: v9.9" in proc.stdout, proc.stdout


def test_an_uncontended_run_neither_waits_nor_complains(tmp_path):
    """The other direction. A lock that always reports busy would be a stall on
    every exec session start, and this test would be green on it otherwise.

    `busy` is printed only when the wait EXPIRES, so the stderr assertion alone
    would pass a hook that waited 1.9 of the 2.0 seconds and then acquired. The
    elapsed assertion is what covers that, and it is measured against a CONTROL
    run of the same hook on a tree with no marker at all, which never reaches
    the lock block.

    It used to be a fixed `elapsed < 1.5`, which measured the host rather than
    the hook: in the full 16-worker suite run of 2026-09-02 the child
    interpreter start alone reached 1.54s and this test went red while the lock
    behaviour was entirely correct. Subtracting a control taken on the same
    loaded machine cancels that, and the remaining 1.0s budget is half of
    `LOCK_WAIT_SECONDS`, so a run that actually sat out the lock still cannot
    pass. The control is the SLOWER of two runs, so an unluckily fast baseline
    cannot fail the comparison on its own.
    """
    control_root = _exec_tree(tmp_path / "control")
    baseline = 0.0
    for _ in range(2):
        started = time.monotonic()
        control = _run(control_root, timeout=120)
        baseline = max(baseline, time.monotonic() - started)
        assert control.returncode == 0, control.stderr

    root = _exec_tree(tmp_path / "measured")
    _update_marker(root)
    started = time.monotonic()
    proc = _run(root, timeout=120)
    elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr
    assert "busy" not in proc.stderr, proc.stderr
    assert elapsed < baseline + 1.0, (
        f"an uncontended run took {elapsed:.2f}s against a {baseline:.2f}s "
        "control that never reaches the lock at all, so it spent the "
        "difference waiting on a lock nothing was holding")
    assert "WORKSPACE UPDATE: v9.9" in proc.stdout, proc.stdout


def test_the_marker_is_marked_notified_and_the_banner_does_not_repeat(tmp_path):
    root = _exec_tree(tmp_path)
    marker = _update_marker(root)
    first = _run(root)
    assert "WORKSPACE UPDATE" in first.stdout
    assert json.loads(marker.read_text(encoding="utf-8"))["notified"] is True
    second = _run(root)
    assert "WORKSPACE UPDATE" not in second.stdout, second.stdout


@pytest.mark.slow
def test_twelve_concurrent_sessions_deliver_the_banner_exactly_once(tmp_path):
    """The defect as measured: 12 hooks, one fresh marker, three rounds.

    Unlocked, driven from a shell that backgrounded 12 hooks at once, this
    reported 2, 2, 1, 2, 2 banners across five trials on 2026-08-31. Under
    `flock` it is one by construction rather than by timing.

    Two things make it a MEASUREMENT rather than a coin flip, and both were
    added after the unlocked mutation survived an earlier version of this test.

    STDIN IS THE BARRIER. Every hook opens with `json.loads(sys.stdin.read())`,
    so a child spawned with `stdin=PIPE` starts its interpreter, imports, and
    then blocks. Spawning twelve and calling `communicate` on each in turn
    releases them one at a time, staggered by a whole process start each, and the
    windows never overlap. Writing all twelve payloads first and only then
    closing all twelve pipes releases them within microseconds of each other.

    AND THE WINDOW IS WIDENED ON PURPOSE. Even released together, the read and
    the write are microseconds apart, so twelve children collide only sometimes:
    run alone on an idle machine the unlocked mutation still passed. `_pad`
    carries two megabytes the hook never looks at, which the read and
    `json.loads` have to get through anyway, so the exposed span is milliseconds
    and the collision is reliable. Widening a race to see it is the same
    technique the `locked_state` docstring records for the statusline case.
    """
    root = _exec_tree(tmp_path)
    marker = _update_marker(root)
    env = _child_env(root)
    payload = json.dumps({"cwd": str(root)})
    fresh = json.dumps({
        "notified": False, "version": "9.9", "build": "77",
        "summary": "scratch", "applied_at": "2026-08-31T00:00:00",
        "_pad": "x" * 2_000_000,
    })

    for round_ in range(3):
        marker.write_text(fresh, encoding="utf-8")
        children = [
            subprocess.Popen(
                [sys.executable, str(HOOK)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env=env, cwd=str(root))
            for _ in range(12)
        ]
        try:
            for child in children:
                child.stdin.write(payload)
                child.stdin.flush()
            for child in children:      # the barrier lifts here, all at once
                child.stdin.close()
            banners = sum(child.stdout.read().count("WORKSPACE UPDATE")
                          for child in children)
        finally:
            for child in children:
                child.wait(timeout=120)
        assert banners == 1, (
            f"round {round_}: {banners} sessions printed the update banner. "
            "The read-modify-write is not serialised.")


# ============================================================
# 4. The panel head claims only what it established
# ============================================================

def _today():
    """The date the HOOK computes, not the one this host happens to show."""
    return datetime.now(get_default_tz()).date()


@pytest.fixture()
def threads(tmp_path, monkeypatch):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    (root / "personal").mkdir(parents=True)
    monkeypatch.setenv("THREADS_ROOT", str(root))
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return root


def _write_thread(threads: Path, slug: str, *, title=None, days_ago=1,
                  last_touched=None):
    if last_touched is None:
        last_touched = (_today() - timedelta(days=days_ago)).isoformat()
    body = [
        "---", f"id: {slug}", f"title: {title or slug}", "status: active",
        "type: business", "classification: ceo-only", 'opened: "2026-01-01"',
        f'last_touched: "{last_touched}"', "counterparties: []", "links: {}",
        "tags: []", "---", "", "## Log", "",
    ]
    (threads / "business" / f"{slug}.md").write_text(
        "\n".join(body) + "\n", encoding="utf-8")


def _head(hook) -> str:
    lines, note = hook._thread_panel_lines(str(ROOT))
    assert note == "", f"panel reported it could not run: {note}"
    assert lines, "panel produced nothing to read a head from"
    return lines[0]


def test_a_row_with_an_unreadable_date_is_named_in_the_head(hook, threads):
    """The measured case: the head said "the 2 touched in the last 14 days"
    while one of the two had no date the code could read."""
    _write_thread(threads, "2026-08-01-good", title="Good", days_ago=2)
    _write_thread(threads, "2026-08-01-broken", title="Broken",
                  last_touched="sometime")
    head = _head(hook)
    assert "1 with no readable date" in head, (
        f"the head asserts recency for a row whose date failed to parse: {head!r}")


def test_two_unreadable_dates_are_counted_not_flagged(hook, threads):
    """A count, not a boolean. The panel already counts `quiet`, `unreadable`
    and `older` this way, and a reader needs the size to act on it."""
    _write_thread(threads, "2026-08-01-good", title="Good", days_ago=2)
    _write_thread(threads, "2026-08-01-b1", title="B1", last_touched="sometime")
    _write_thread(threads, "2026-08-01-b2", title="B2", last_touched="")
    head = _head(hook)
    assert "2 with no readable date" in head, head


def test_a_head_over_readable_dates_says_nothing_about_dates(hook, threads):
    """The other direction. A head that always mentioned undated rows would be a
    different false claim, and would make the test above pass over any code."""
    _write_thread(threads, "2026-08-01-good", title="Good", days_ago=2)
    _write_thread(threads, "2026-08-01-also", title="Also", days_ago=5)
    head = _head(hook)
    assert "readable date" not in head, head
    assert "Showing the 2 touched in the last" in head, head


def test_the_head_count_agrees_with_the_rows_it_printed(hook, threads):
    """Read the sentence against the panel it heads, both ways at once."""
    _write_thread(threads, "2026-08-01-good", title="Good", days_ago=2)
    _write_thread(threads, "2026-08-01-broken", title="Broken",
                  last_touched="not-a-date")
    lines, note = hook._thread_panel_lines(str(ROOT))
    assert note == ""
    rows = [ln for ln in lines if ln.startswith("- ")]
    no_date_rows = [ln for ln in rows if "(no date)" in ln]
    assert f"Showing the {len(rows)} touched" in lines[0], lines[0]
    assert f"{len(no_date_rows)} with no readable date" in lines[0], lines[0]


def test_the_recency_claim_itself_was_not_reworded_away(hook):
    """The fix had to NARROW the sentence, not delete it.

    Rewording a user-facing claim retires its `tests/test_scope_claims.py`
    registry entry, and dropping this one would hide the window the panel is
    built on rather than qualify it.
    """
    src = HOOK.read_text(encoding="utf-8")
    assert 'f" touched in the last {THREAD_PANEL_DAYS} days"' in src, (
        "the window claim is gone from the head; it was meant to be qualified, "
        "not removed")


def test_an_undated_row_is_still_shown_and_still_sorts_first(hook, threads):
    """The qualification must not have turned into a suppression. A broken date
    is a defect to see, which is why the row is kept and sorted to the top."""
    _write_thread(threads, "2026-08-01-good", title="Good", days_ago=2)
    _write_thread(threads, "2026-08-01-broken", title="Broken",
                  last_touched="sometime")
    lines, _note = hook._thread_panel_lines(str(ROOT))
    rows = [ln for ln in lines if ln.startswith("- ")]
    assert "Broken" in rows[0], rows
    assert "(no date)" in rows[0], rows
