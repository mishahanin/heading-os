"""Five load-bearing paths that no test in 13,434 executed.

Each one is a cleanup or a fallback: the branch that runs when something goes
wrong, or when the process stops. That is exactly the code nobody drives by
accident, and exactly the code whose failure is invisible until the day it
matters. Found by a coverage sweep on 2026-08-27; every claim below was
re-verified against current source before the test was written.

  1. `list_contacts`' exec-registry filter. An autouse fixture empties the
     registry for every test in tests/bridge/test_sources_contacts.py, and the
     few that override it inject one well-formed non-operator slug. So the
     `owner == self_dir` and malformed-slug guards in the REGISTRY loop never
     ran. The crm-central backstop carried a second, independent copy of the
     same filter, and that copy is the one the suite covered - so the module
     looked tested while one of the two copies was free.

     Updated 2026-08-30: the backstop and its duplicate filter were deleted
     along with the retired root they read, so the registry loop's copy is now
     the only one and these tests are its only coverage.

  2. `pull-service-state`'s scp-timeout rollback. The whole of `main()` was
     unexecuted. The staging-and-swap exists because "this mirror is the only
     local record of the VM's state"; a one-word slip between `staging_abs` and
     `dest_abs` deletes the last good copy on the first unreachable VM, while
     printing "previous mirror left intact".

  3. `sync-exchange-daemon`'s shutdown `finally`. Never executed by any test and
     never driven as a subprocess. `is_daemon_alive()` reads the PID file and
     asks only whether that PID is running, with no identity check, so a leaked
     PID file plus one PID reuse makes `cmd_daemon` refuse to start and
     `cmd_status` report RUNNING with a fabricated uptime, indefinitely.

  4. `Sentinel.shutdown()` and the `finally` in `start()` that calls it.
     Wholly unexecuted. `shutdown()` is the ONLY place the notification-dedupe
     state is saved, so skipping it re-notifies every email and Telegram item on
     the next start.

  5. `recall-inject`'s embedder-outage warning. Its producer only ever emits
     `embed_unavailable` TOGETHER with a non-zero exit, so the hook's ordering -
     parse first, judge the exit code second - is the entire mechanism, and the
     code comment says so. No test ever handed the hook that payload.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json as _json
import logging
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1 - the exec-registry loop's own copy of the self-snapshot filter
# ============================================================

import scripts.bridge_daemon.sources.contacts as contacts_src  # noqa: E402


def _contact_md(name: str, **fm) -> str:
    body = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{body}\n---\n\n# {name}\n\nNotes.\n"


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "crm" / "contacts").mkdir(parents=True)
    return ws


def _exec_overlay(tmp_path: Path, exec_slug: str) -> Path:
    return tmp_path / f".heading-os-data-{exec_slug}" / "crm" / "contacts"


def _per_exec_contact(tmp_path: Path, exec_slug: str, slug: str, name: str, **fm):
    """Write into the exec's DATA overlay, the one live source.

    Was `31c-crm-{slug}/contacts` until 2026-08-30. That root had been retired
    since 2026-08-23 and is absent from disk, so both tests below were building
    their fixtures in a layout no filesystem has.
    """
    d = _exec_overlay(tmp_path, exec_slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(_contact_md(name, **fm), encoding="utf-8")


def test_the_registry_loop_skips_the_operators_own_stale_snapshot(tmp_path, monkeypatch):
    """The registry may list the operator's own slug; their live contacts come
    from crm/contacts/, and the overlay under that slug is a stale copy.

    Mutation-confirmed before this test existed: deleting `or owner == self_dir`
    from the registry loop left the whole bridge suite green (1199 passed),
    because the empty-registry fixture meant the loop never ran with a self
    slug in it.

    Re-confirmed 2026-08-30, and it had gone vacuous in the meantime. The
    fixture below seeded `31c-crm-{slug}/contacts`, a root the migration
    retired, so once the resolver stopped reading it the stale copy could not
    have appeared whatever the guard did: with `or owner == self_dir` deleted,
    this test still passed. The guard is now the ONLY copy of that filter (the
    crm-central backstop that carried the second copy was deleted with the root
    it crawled), so this assertion is the whole of its coverage. Re-mutated
    after the fixture moved: dropping the guard fails here.
    """
    ws = _ws(tmp_path)
    self_slug = contacts_src._crm_central_self_dir()
    # The live CEO contact, and a stale duplicate under the operator's own slug.
    (ws / "crm" / "contacts" / "dana-osei.md").write_text(
        _contact_md("Dana Osei", relationship_type="prospect"), encoding="utf-8")
    _per_exec_contact(tmp_path, self_slug, "dana-osei", "Dana Osei STALE COPY",
                      relationship_type="prospect")
    assert _exec_overlay(tmp_path, self_slug).is_dir()  # the guard's target exists
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: [self_slug])

    d = contacts_src.list_contacts(ws, data_root=ws)
    owners = [r["owner"] for r in d["contacts"]]
    names = [r["name"] for r in d["contacts"]]
    assert self_slug not in owners, (
        f"the operator's own stale mirror was scanned as if it were an "
        f"executive's: owners={owners}"
    )
    assert "Dana Osei STALE COPY" not in names, names
    assert d["total"] == 1 and names == ["Dana Osei"], (d["total"], names)


@pytest.mark.parametrize("bad", [
    "../escape",
    "UPPER",
    "has space",
    "-leading-dash",
    "a" * 65,
    "",
])
def test_the_registry_loop_refuses_a_malformed_slug(tmp_path, monkeypatch, bad):
    """A registry entry is a directory name joined to a path. The pattern guard
    is the only thing between a bad entry and a traversal, and it too ran in no
    test: every override in the module injected a well-formed slug."""
    ws = _ws(tmp_path)
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", lambda: [bad])
    resolved = []
    real = contacts_src._resolve_exec_contacts_dir

    def _spy(root, owner):
        resolved.append(owner)
        return real(root, owner)

    monkeypatch.setattr(contacts_src, "_resolve_exec_contacts_dir", _spy)

    d = contacts_src.list_contacts(ws, data_root=ws)
    assert resolved == [], (
        f"the malformed registry slug {bad!r} was resolved to a directory "
        f"instead of being skipped"
    )
    assert d["total"] == 0


def test_a_well_formed_exec_slug_is_still_resolved(tmp_path, monkeypatch):
    """Anchor. Both tests above pass on a loop that skips EVERY slug, which
    would empty the page for every executive."""
    ws = _ws(tmp_path)
    _per_exec_contact(tmp_path, "marlow-carter", "taylor-reed", "Taylor Reed",
                      relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])
    d = contacts_src.list_contacts(ws, data_root=ws)
    assert [r["owner"] for r in d["contacts"]] == ["marlow-carter"]


# ============================================================
# 2 - the scp-timeout rollback that protects the only local mirror
# ============================================================

pullsvc = _load("pull_service_state_cleanup", "scripts/pull-service-state.py")


def _drive_pull(tmp_path, monkeypatch, runner, seed=None):
    """Run pull-service-state's main() against a fake VM and a temp mirror.

    `seed` runs after the mirror directory exists and before `main()`, which is
    the only window in which a caller can put a last-good copy on disk.
    """
    data_root = tmp_path / "data"
    (data_root / pullsvc.MIRROR_REL).mkdir(parents=True)
    if seed is not None:
        seed(data_root / pullsvc.MIRROR_REL)
    monkeypatch.setattr(pullsvc, "get_data_root", lambda: data_root)
    monkeypatch.setattr(pullsvc, "load_env", lambda: None)
    monkeypatch.setattr(pullsvc, "state_dirs", lambda: [("sentinel", "/srv/sentinel")])
    monkeypatch.setenv("SERVICE_VM_HOST", "vm.invalid")
    monkeypatch.setattr(pullsvc.subprocess, "run", runner)
    return data_root, pullsvc.main()


def test_an_scp_timeout_leaves_the_previous_mirror_intact(tmp_path, monkeypatch, capsys):
    """The message printed on this path is a promise: "previous mirror left
    intact". Nothing checked it, and the rollback deletes a path chosen by name
    from two that are both in scope.

    THE TIMEOUT THAT LEFT NO STAGING TREE, distinct from the sibling below
    where scp got as far as writing a partial one. The rollback has nothing to
    remove here, and must still not reach for the live copy.

    FIXED 2026-08-30. The last-good copy is now SEEDED. A comment claimed "the
    live mirror is written BEFORE the run"; nothing wrote it, `_drive_pull`
    created only the mirror directory, and the closing assertion read
    `not live.exists() or live.is_dir()` -- satisfied by the absence the test
    was supposed to rule out. `rmtree_force(dest_abs)` in place of
    `rmtree_force(staging_abs)` passed it.
    """
    def _timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, pullsvc.SCP_TIMEOUT_S)

    def _seed(mirror: Path) -> None:
        (mirror / "sentinel").mkdir()
        (mirror / "sentinel" / "state.json").write_text(
            '{"last_good": true}', encoding="utf-8")

    data_root, rc = _drive_pull(tmp_path, monkeypatch, _timeout, seed=_seed)
    mirror = data_root / pullsvc.MIRROR_REL
    live = mirror / "sentinel"
    assert rc == 1
    out = capsys.readouterr().out
    assert "previous mirror left intact" in out, out
    assert not (mirror / ".sentinel.incoming").exists(), (
        "the half-finished staging tree was kept"
    )
    assert live.is_dir(), "the rollback deleted the last good mirror"
    assert (live / "state.json").read_text(encoding="utf-8") == '{"last_good": true}'


def test_the_previous_mirror_really_survives_a_timeout(tmp_path, monkeypatch):
    """The sharper form of the same claim: put a last-good copy on disk first,
    then time the transfer out, and read the copy back.

    This is the assertion the one-word slip fails. `rmtree_force(dest_abs)` in
    place of `rmtree_force(staging_abs)` passes every test that only checks the
    printed sentence.
    """
    def _timeout(cmd, **kwargs):
        # Simulate a partial transfer: scp created the staging tree, then hung.
        staging = tmp_path / "data" / pullsvc.MIRROR_REL / ".sentinel.incoming"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "half.json").write_text("{partial", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, pullsvc.SCP_TIMEOUT_S)

    data_root = tmp_path / "data"
    live = data_root / pullsvc.MIRROR_REL / "sentinel"
    live.mkdir(parents=True)
    (live / "state.json").write_text('{"last_good": true}', encoding="utf-8")

    monkeypatch.setattr(pullsvc, "get_data_root", lambda: data_root)
    monkeypatch.setattr(pullsvc, "load_env", lambda: None)
    monkeypatch.setattr(pullsvc, "state_dirs", lambda: [("sentinel", "/srv/sentinel")])
    monkeypatch.setenv("SERVICE_VM_HOST", "vm.invalid")
    monkeypatch.setattr(pullsvc.subprocess, "run", _timeout)

    assert pullsvc.main() == 1
    assert (live / "state.json").read_text(encoding="utf-8") == '{"last_good": true}', (
        "the only local record of the VM's state was deleted by the rollback"
    )
    assert not (data_root / pullsvc.MIRROR_REL / ".sentinel.incoming").exists()


def test_a_successful_pull_swaps_the_staging_tree_in(tmp_path, monkeypatch):
    """Anchor. Every assertion above is satisfied by a main() that does nothing
    at all; this one proves the success path still replaces the mirror."""
    def _ok(cmd, **kwargs):
        staging = tmp_path / "data" / pullsvc.MIRROR_REL / ".sentinel.incoming"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "state.json").write_text('{"fresh": true}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    data_root = tmp_path / "data"
    live = data_root / pullsvc.MIRROR_REL / "sentinel"
    live.mkdir(parents=True)
    (live / "state.json").write_text('{"stale": true}', encoding="utf-8")

    monkeypatch.setattr(pullsvc, "get_data_root", lambda: data_root)
    monkeypatch.setattr(pullsvc, "load_env", lambda: None)
    monkeypatch.setattr(pullsvc, "state_dirs", lambda: [("sentinel", "/srv/sentinel")])
    monkeypatch.setenv("SERVICE_VM_HOST", "vm.invalid")
    monkeypatch.setattr(pullsvc.subprocess, "run", _ok)

    assert pullsvc.main() == 0
    assert (live / "state.json").read_text(encoding="utf-8") == '{"fresh": true}'
    assert not (data_root / pullsvc.MIRROR_REL / ".sentinel.incoming").exists()


# ============================================================
# 3 - the sync-exchange daemon's shutdown finally
# ============================================================

class _FakeScheduler:
    """Records lifecycle calls; runs no job. Replacing the scheduler is what
    keeps this test from reaching the real Exchange transport, since two job
    specs carry `fire_at_start`."""

    def __init__(self, *a, **kw):
        self.jobs = []
        self.started = False
        self.shutdown_calls = []

    def add_job(self, *a, **kw):
        self.jobs.append(kw.get("id"))

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.shutdown_calls.append(wait)


class _LoopProxy:
    """Captures signal handlers instead of registering them.

    A test must not send itself a real SIGTERM: if it lands before the handler
    is installed, the default action kills the pytest worker. Capturing the
    callback and calling it directly drives the identical code path with no
    signal involved.
    """

    def __init__(self, real, captured):
        self._real = real
        self._captured = captured

    def add_signal_handler(self, sig, cb, *args):
        self._captured[sig] = cb

    def __getattr__(self, name):
        return getattr(self._real, name)


def _asyncio_proxy(captured):
    proxy = types.SimpleNamespace()
    for name in dir(asyncio):
        if not name.startswith("_"):
            setattr(proxy, name, getattr(asyncio, name))
    proxy.get_running_loop = lambda: _LoopProxy(asyncio.get_running_loop(), captured)
    return proxy


@pytest.fixture
def sync_daemon(tmp_path, monkeypatch):
    mod = _load("sync_exchange_daemon_cleanup", "scripts/sync-exchange-daemon.py")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(mod, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(mod, "PID_FILE", runtime / "daemon.pid")
    monkeypatch.setattr(mod, "STARTED_AT_FILE", runtime / "started_at")
    monkeypatch.setattr(mod, "STOP_SENTINEL", runtime / "stop")
    monkeypatch.setattr(mod, "load_env", lambda: None)
    monkeypatch.setattr(mod, "AsyncIOScheduler", _FakeScheduler)
    return mod


def _run_until_stopped(mod):
    """Start _run_daemon, fire the captured stop handler, wait for it to finish."""
    captured: dict = {}
    proxy = _asyncio_proxy(captured)

    async def _drive():
        import contextlib
        real_asyncio = mod.asyncio
        mod.asyncio = proxy
        try:
            task = asyncio.ensure_future(_run_daemon_wrapper(mod, real_asyncio))
            for _ in range(200):
                if captured:
                    break
                await asyncio.sleep(0.005)
            assert captured, "no signal handler was ever installed"
            handler = captured.get(signal.SIGTERM) or next(iter(captured.values()))
            handler()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=10)
            return task
        finally:
            mod.asyncio = real_asyncio

    return asyncio.run(_drive())


async def _run_daemon_wrapper(mod, real_asyncio):
    return await mod._run_daemon(logging.getLogger("sync-daemon-test"))


def test_the_daemon_removes_its_pid_file_when_it_stops(sync_daemon):
    """A leaked PID file is not a stray file: `is_daemon_alive()` asks only
    whether that PID is running, so one PID reuse makes the daemon refuse to
    start while `cmd_status` reports RUNNING with a fabricated uptime."""
    mod = sync_daemon
    task = _run_until_stopped(mod)
    assert task.done() and task.exception() is None, task
    assert not mod.PID_FILE.exists(), "the PID file survived a clean shutdown"
    assert not mod.STARTED_AT_FILE.exists(), "the start-time file survived"


def test_the_daemon_wrote_the_pid_file_in_the_first_place(sync_daemon, monkeypatch):
    """Anchor. The test above passes trivially if the daemon never writes a PID
    file, which would make its whole liveness contract vacuous rather than
    fixed. Capture the file's existence at the moment the scheduler starts."""
    mod = sync_daemon
    seen = {}

    class _Watching(_FakeScheduler):
        def start(self):
            seen["pid_exists"] = mod.PID_FILE.exists()
            seen["started_exists"] = mod.STARTED_AT_FILE.exists()
            super().start()

    monkeypatch.setattr(mod, "AsyncIOScheduler", _Watching)
    _run_until_stopped(mod)
    assert seen == {"pid_exists": True, "started_exists": True}, seen


def test_the_scheduler_is_stopped_on_the_way_out(sync_daemon, monkeypatch):
    """The other half of the same `finally`. Without it the APScheduler thread
    outlives the daemon and keeps firing two-hour Exchange syncs."""
    mod = sync_daemon
    made = []

    class _Recording(_FakeScheduler):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            made.append(self)

    monkeypatch.setattr(mod, "AsyncIOScheduler", _Recording)
    _run_until_stopped(mod)
    assert made and made[0].shutdown_calls, "scheduler.shutdown() was never called"
    assert made[0].shutdown_calls == [False], (
        f"shutdown must not block the exit path: {made[0].shutdown_calls}"
    )


# ============================================================
# 4 - Sentinel.shutdown(), and the finally in start() that calls it
# ============================================================

@pytest.fixture
def sentinel_mod():
    return pytest.importorskip("scripts.sentinel")


def _sentinel_stub(sen, tmp_path, monkeypatch):
    """A Sentinel with only the attributes shutdown() touches."""
    monkeypatch.setattr(sen, "PID_FILE", tmp_path / "sentinel.pid")
    (tmp_path / "sentinel.pid").write_text("12345", encoding="utf-8")

    saved = []
    disconnected = []

    class _State:
        def save(self):
            saved.append(True)

    class _Telegram:
        async def disconnect(self):
            disconnected.append(True)

    stub = types.SimpleNamespace(
        _heartbeat_task=None,
        state=_State(),
        telegram_source=_Telegram(),
        logger=logging.getLogger("sentinel-shutdown-test"),
    )
    stub.shutdown = sen.Sentinel.shutdown.__get__(stub)
    return stub, saved, disconnected


def test_shutdown_saves_the_dedupe_state_and_clears_the_pid(sentinel_mod, tmp_path, monkeypatch):
    """`shutdown()` is the ONLY place the notification-dedupe state is written.
    Skipping it means the next start re-notifies every email and Telegram item
    the last run already sent, and leaves a PID for the next process to collide
    with."""
    sen = sentinel_mod
    stub, saved, disconnected = _sentinel_stub(sen, tmp_path, monkeypatch)

    asyncio.run(stub.shutdown())

    assert saved == [True], "the dedupe state was not saved on shutdown"
    assert disconnected == [True], "the Telegram client was left connected"
    assert not sen.PID_FILE.exists(), "the PID file survived shutdown"


def test_shutdown_cancels_the_heartbeat_before_returning(sentinel_mod, tmp_path, monkeypatch):
    """A live heartbeat task after shutdown keeps telling the watchdog the
    daemon is healthy.

    The state is read INSIDE the loop, on purpose. `asyncio.run()` cancels every
    still-pending task on its way out, so a `task.cancelled()` check written
    after it returns is answered by the RUNNER's cleanup rather than by
    `shutdown()`. Measured 2026-08-27: the first version of this test asserted
    exactly that, and stayed green with the cancel deleted from `shutdown()`
    outright. `done` is asserted beside `cancelled` because a task the runner
    has not reached yet is neither.
    """
    sen = sentinel_mod
    stub, _, _ = _sentinel_stub(sen, tmp_path, monkeypatch)

    async def _drive():
        async def _beat():
            while True:
                await asyncio.sleep(3600)

        stub._heartbeat_task = asyncio.ensure_future(_beat())
        await asyncio.sleep(0)
        assert not stub._heartbeat_task.done(), "the heartbeat never started"
        await stub.shutdown()
        return stub._heartbeat_task.done(), stub._heartbeat_task.cancelled()

    done, cancelled = asyncio.run(_drive())
    assert done, "shutdown() returned with the heartbeat task still pending"
    assert cancelled, "the heartbeat task was still running after shutdown"


def test_shutdown_survives_a_pid_file_that_is_already_gone(sentinel_mod, tmp_path, monkeypatch):
    """A crash-and-restart can remove the file first. An OSError here would
    escape a `finally` and hide whatever ended the daemon."""
    sen = sentinel_mod
    stub, saved, _ = _sentinel_stub(sen, tmp_path, monkeypatch)
    sen.PID_FILE.unlink()
    asyncio.run(stub.shutdown())
    assert saved == [True]


def test_start_calls_shutdown_from_a_finally(sentinel_mod):
    """The behavioural tests above prove shutdown() does its work. This proves
    it is REACHED when the wait loop is interrupted.

    Dedenting `await self.shutdown()` out of the `finally` is a plausible tidy
    that leaves every other test green: a Ctrl-C or a SystemExit out of the
    interval wait then exits with the dedupe state unwritten. `start()` is a
    long-lived orchestrator that constructs live Exchange and Telegram clients,
    so its placement is read from the AST rather than driven.
    """
    import ast

    tree = ast.parse(Path(ROOT / "scripts" / "sentinel.py").read_text(encoding="utf-8"))
    start = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "start"
         and any(isinstance(x, ast.Try) for x in ast.walk(n))),
        None)
    assert start is not None, "Sentinel.start() is gone; this guard covers nothing"

    in_finally = []
    for node in ast.walk(start):
        if isinstance(node, ast.Try):
            for stmt in node.finalbody:
                in_finally.append(ast.unparse(stmt))
    assert any("self.shutdown()" in s for s in in_finally), (
        f"start() does not call shutdown() from a finally, so an interrupted "
        f"wait exits without saving the dedupe state. finally bodies: {in_finally}"
    )


# ============================================================
# 5 - recall-inject's embedder-outage warning
# ============================================================

HOOK = ROOT / ".claude" / "hooks" / "recall-inject.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("recall_inject_outage", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _feed(monkeypatch, prompt: str):
    monkeypatch.setattr(sys, "stdin", io.StringIO(_json.dumps({"prompt": prompt})))


def _canned(payload, returncode):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=_json.dumps(payload), stderr="embedder unreachable")
    return _run


OUTAGE = {"embed_unavailable": {"reason": "no ollama at http://172.30.48.1:11434"}}


@pytest.mark.parametrize("returncode", [1, 3])
def test_an_embedder_outage_is_announced_even_though_the_backend_exited_nonzero(
        monkeypatch, capsys, returncode):
    """Both emission sites in the producer return non-zero (3 for an unset host,
    1 for an EmbeddingError), so this warning is ONLY ever reachable together
    with a failing exit code. Moving the exit-code check above the parse - the
    natural "handle errors first" refactor, and the one the code comment warns
    against - silences it, and every existing test stays green.

    The cost of the silence: "not in memory" reads as an empty memory rather
    than an outage.
    """
    mod = _load_hook()
    _feed(monkeypatch, "что мы решили по ценообразованию и почему")
    monkeypatch.setattr(mod.subprocess, "run", _canned(OUTAGE, returncode))

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0, "a hook must never break the turn"
    out = capsys.readouterr().out
    assert out.strip(), "the hook emitted nothing at all during an outage"
    ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "the embedder is down" in ctx, ctx
    assert "172.30.48.1:11434" in ctx, (
        "the warning does not say WHICH host is unreachable, so the operator "
        "cannot act on it"
    )


def test_a_plain_backend_failure_still_emits_nothing(monkeypatch, capsys):
    """Anchor, and the property the ordering must not break in the other
    direction: a non-zero exit with no outage payload stays silent."""
    mod = _load_hook()
    _feed(monkeypatch, "что мы решили по ценообразованию и почему")
    monkeypatch.setattr(mod.subprocess, "run", _canned({"hits": [], "gap": False}, 1))

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == ""
