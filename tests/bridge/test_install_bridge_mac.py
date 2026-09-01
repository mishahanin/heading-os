"""Tests for scripts/install-bridge-service-mac.py.

The script is macOS-only (guarded by sys.platform), but the plist
builder is pure data. We import via importlib (kebab-case CLI script
can't be imported as a module) and test the plist shape on any
platform.

**Three of these tests wrote into the live repository.** `install()` calls
`(WORKSPACE_ROOT / ".daemon-state").mkdir(parents=True, exist_ok=True)` at
`install-bridge-service-mac.py:93-94`, and `WORKSPACE_ROOT` resolves from the
script's own `__file__`, not from the plist globals the tests patched. Patching
`PLIST_PATH` and `PLIST_DIR` redirected the plist and left the mkdir pointing at
the checkout. MEASURED 2026-08-31 in a clean tree with `.daemon-state` removed
first::

    $ rm -rf .daemon-state
    $ .venv/bin/python -m pytest tests/bridge/test_install_bridge_mac.py -q
    14 passed in 0.45s
    $ ls -d .daemon-state
    .daemon-state

`.gitignore:218` excludes `.daemon-state/`, so `git status` reported a clean
tree over a directory the suite had just created, which is why running the file
sixteen times never surfaced it. The three `install()` tests now pin
`WORKSPACE_ROOT` to `tmp_path`, and
`test_no_test_in_this_file_calls_install_without_redirecting_the_workspace_root`
is the mechanical guard, because prose in this docstring would not have stopped
the fourth one.
"""
import ast
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "install-bridge-service-mac.py"


def _isolate_workspace(monkeypatch, mod, tmp_path):
    """Point the module's WORKSPACE_ROOT at tmp_path.

    `install()` reads the module global at call time for its `.daemon-state`
    mkdir, and `_build_plist` reads it for `WorkingDirectory` and the log paths,
    so one patch covers every write and every path the plist embeds. Returns the
    isolated root so a caller can assert against it.
    """
    monkeypatch.setattr(mod, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


def _load_module():
    # Anchored on __file__, not on the caller's cwd. `Path("scripts/...")`
    # resolves against pytest's working directory, so `cd /tmp && pytest
    # <abs path to this file>` looked for /tmp/scripts/... - a FileNotFoundError
    # at best, and at worst an unrelated file at that relative path.
    assert SCRIPT.is_file(), SCRIPT
    spec = importlib.util.spec_from_file_location(
        "install_bridge_service_mac", SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plist_has_required_launchd_keys():
    mod = _load_module()
    payload = mod._build_plist("/usr/local/bin/python3")
    # launchd minimum-viable shape.
    assert payload["Label"] == "com.31c.bridge-daemon"
    assert "ProgramArguments" in payload
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True


def test_plist_program_arguments_invoke_daemon_with_start():
    mod = _load_module()
    payload = mod._build_plist("/usr/local/bin/python3")
    args = payload["ProgramArguments"]
    assert args[0] == "/usr/local/bin/python3"
    assert args[1].endswith("scripts/bridge-daemon.py") or args[1].endswith("scripts\\bridge-daemon.py")
    assert "--start" in args


def test_plist_working_directory_is_workspace_root():
    mod = _load_module()
    payload = mod._build_plist("/usr/bin/python3")
    # WorkingDirectory should be the parent of scripts/
    wd = Path(payload["WorkingDirectory"])
    assert (wd / "scripts" / "bridge-daemon.py").exists()


def test_plist_log_paths_inside_daemon_state():
    mod = _load_module()
    payload = mod._build_plist("/usr/bin/python3")
    # stdout + stderr should land in <workspace>/.daemon-state/ for visibility.
    assert ".daemon-state" in payload["StandardOutPath"]
    assert payload["StandardOutPath"] == payload["StandardErrorPath"]  # one file, easier tailing


def test_plist_environment_variables_carry_path():
    mod = _load_module()
    payload = mod._build_plist("/usr/bin/python3")
    env = payload["EnvironmentVariables"]
    # 'claude' on the deep-link launch target must be resolvable from the agent.
    assert "PATH" in env
    assert env["PATH"]  # non-empty


def test_plistlib_round_trip(tmp_path):
    """The dict we build must be plistlib-serializable + round-trip cleanly."""
    import plistlib
    mod = _load_module()
    payload = mod._build_plist("/usr/bin/python3")
    out = tmp_path / "test.plist"
    with out.open("wb") as fp:
        plistlib.dump(payload, fp)
    with out.open("rb") as fp:
        reloaded = plistlib.load(fp)
    assert reloaded == payload


def test_ensure_macos_exits_on_non_darwin(monkeypatch):
    """Running the installer on Windows/Linux must refuse with exit 2."""
    mod = _load_module()
    monkeypatch.setattr(sys, "platform", "win32")
    try:
        mod._ensure_macos()
        raised = False
    except SystemExit as e:
        raised = True
        assert e.code == 2
    assert raised, "_ensure_macos should sys.exit(2) on non-darwin"


def test_ensure_macos_passes_on_darwin(monkeypatch):
    """On macOS the guard returns silently."""
    mod = _load_module()
    monkeypatch.setattr(sys, "platform", "darwin")
    mod._ensure_macos()  # must not raise


# Phase N - install() / uninstall() integration tests with mocked launchctl.


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _patch_subprocess(monkeypatch, mod, captured):
    """Capture every subprocess.run call into `captured` and never actually
    shell out. Default return: success."""
    def _fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeCompletedProcess(returncode=0)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)


def test_install_writes_plist_and_loads_via_launchctl(monkeypatch, tmp_path):
    """install() writes a valid plist to PLIST_PATH and shells out to
    'launchctl load -w' on the same path."""
    mod = _load_module()
    plist = tmp_path / "test.plist"
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    monkeypatch.setattr(mod, "PLIST_DIR", plist.parent)
    _isolate_workspace(monkeypatch, mod, tmp_path / "ws")
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    mod.install()

    # 0. The state directory landed under tmp_path, not in the checkout.
    assert (tmp_path / "ws" / ".daemon-state").is_dir()

    # 1. Plist file exists and is valid plist XML.
    assert plist.exists()
    import plistlib
    with plist.open("rb") as fp:
        loaded = plistlib.load(fp)
    assert loaded["Label"] == "com.31c.bridge-daemon"
    assert loaded["RunAtLoad"] is True

    # 2. launchctl was called twice: unload (defensive), then load -w.
    assert len(captured) == 2
    assert captured[0][:3] == ["launchctl", "unload", "-w"]
    assert captured[1][:3] == ["launchctl", "load", "-w"]
    # Both target the same plist path:
    assert captured[0][3] == str(plist)
    assert captured[1][3] == str(plist)


def test_install_is_idempotent(monkeypatch, tmp_path):
    """Re-running install() unloads the old agent first, then re-installs.
    The order matters: unload before write-and-load so launchd picks up
    the new plist."""
    mod = _load_module()
    plist = tmp_path / "test.plist"
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    monkeypatch.setattr(mod, "PLIST_DIR", plist.parent)
    _isolate_workspace(monkeypatch, mod, tmp_path / "ws")
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    mod.install()
    mod.install()

    # Both runs do (unload, load) -> 4 subprocess calls total.
    assert len(captured) == 4
    # Plist still exists with same valid content.
    assert plist.exists()


def test_install_aborts_when_launchctl_load_fails(monkeypatch, tmp_path):
    """If 'launchctl load' returns non-zero, install() exits with code 1."""
    mod = _load_module()
    plist = tmp_path / "test.plist"
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    monkeypatch.setattr(mod, "PLIST_DIR", plist.parent)
    _isolate_workspace(monkeypatch, mod, tmp_path / "ws")

    call_index = [0]
    def _fake_run(cmd, **kwargs):
        call_index[0] += 1
        # First call (unload) succeeds; second call (load) fails.
        if call_index[0] == 1:
            return _FakeCompletedProcess(returncode=0)
        return _FakeCompletedProcess(returncode=1, stderr="agent already loaded")
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    raised = False
    try:
        mod.install()
    except SystemExit as e:
        raised = True
        assert e.code == 1
    assert raised, "install() should sys.exit(1) when launchctl load fails"
    # Plist was still written before the load attempt - that's intentional
    # so a manual 'launchctl load -w' can recover from a transient failure.
    assert plist.exists()


def test_uninstall_removes_plist_and_unloads(monkeypatch, tmp_path):
    """uninstall() shells out to launchctl unload then deletes the plist."""
    mod = _load_module()
    plist = tmp_path / "test.plist"
    plist.write_bytes(b"<placeholder>")
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    mod.uninstall()

    assert not plist.exists()
    assert len(captured) == 1
    assert captured[0][:3] == ["launchctl", "unload", "-w"]


def test_uninstall_is_silent_noop_when_no_plist(monkeypatch, tmp_path):
    """uninstall() must not raise when there's nothing to uninstall."""
    mod = _load_module()
    plist = tmp_path / "test.plist"  # never created
    monkeypatch.setattr(mod, "PLIST_PATH", plist)
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    mod.uninstall()  # must not raise

    # launchctl is NOT called because there's nothing to unload.
    assert captured == []


def test_install_creates_its_state_dir_under_the_root_it_was_given(
        monkeypatch, tmp_path):
    """The finding stated as behaviour: no write escapes to the checkout.

    Fails against the pre-2026-08-31 file, where `WORKSPACE_ROOT` stayed
    anchored on the script's own `__file__` and `install()` mkdir'd
    `.daemon-state` inside the repository. `.gitignore:218` hid that from
    `git status`, so the only way to see it is to ask the live root directly,
    which is what the second half of this test does.
    """
    mod = _load_module()
    ws = tmp_path / "ws"
    monkeypatch.setattr(mod, "PLIST_PATH", tmp_path / "test.plist")
    monkeypatch.setattr(mod, "PLIST_DIR", tmp_path)
    _isolate_workspace(monkeypatch, mod, ws)
    _patch_subprocess(monkeypatch, mod, [])

    live_state = ROOT / ".daemon-state"
    live_existed = live_state.is_dir()
    before = sorted(p.name for p in live_state.iterdir()) if live_existed else None

    mod.install()

    assert (ws / ".daemon-state").is_dir(), "the state dir did not follow the root"
    assert mod._build_plist("/usr/bin/python3")["StandardOutPath"].startswith(str(ws))
    # The checkout is unchanged: neither created, nor added to. A pre-existing
    # `.daemon-state` belongs to the operator's own daemon, so the assertion is
    # on its CONTENTS, not on its absence.
    if live_existed:
        assert sorted(p.name for p in live_state.iterdir()) == before, (
            "install() wrote into the repository's own .daemon-state")
    else:
        assert not live_state.exists(), (
            f"install() created {live_state} in the live checkout; it is "
            f"gitignored, so `git status` will not show it")


def test_no_test_in_this_file_calls_install_without_redirecting_the_workspace_root():
    """The guard, because the docstring at the top would not stop the fourth one.

    Three tests called `install()` with `PLIST_PATH` redirected and
    `WORKSPACE_ROOT` left alone, and each was written by someone who believed
    the patching was complete. This parses THIS file and requires every test
    that calls `mod.install()` to also name `WORKSPACE_ROOT` in the same
    function, whether directly or through `_isolate_workspace`.

    Lexical, like the browser guard in
    `tests/bridge/test_no_test_opens_a_real_browser.py`: a redirect installed by
    a fixture elsewhere would be reported. That is the safe direction, since a
    false report costs one line and a miss writes into the operator's checkout.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    installers: list[str] = []
    unguarded: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls_install = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "install"
            for n in ast.walk(func))
        if not calls_install:
            continue
        installers.append(func.name)
        redirected = any(
            (isinstance(n, ast.Constant) and n.value == "WORKSPACE_ROOT")
            or (isinstance(n, ast.Name) and n.id == "_isolate_workspace")
            for n in ast.walk(func))
        if not redirected:
            unguarded.append(func.name)

    # Anti-vacuity: a walk that matched no install() call would pass forever,
    # which is the failure mode this file just demonstrated.
    assert len(installers) >= 4, (
        f"only {len(installers)} test(s) reached the guard: {installers}")
    assert not unguarded, (
        "these tests call install() without redirecting WORKSPACE_ROOT, so the "
        "`.daemon-state` mkdir lands in the repository checkout. Add "
        "`_isolate_workspace(monkeypatch, mod, tmp_path / \"ws\")`:\n  "
        + "\n  ".join(unguarded))


def test_install_aborts_when_daemon_script_missing(monkeypatch, tmp_path):
    """If scripts/bridge-daemon.py doesn't exist, install() exits 1 before
    touching the filesystem."""
    mod = _load_module()
    bogus_script = tmp_path / "nonexistent.py"
    monkeypatch.setattr(mod, "DAEMON_SCRIPT", bogus_script)
    monkeypatch.setattr(mod, "PLIST_PATH", tmp_path / "test.plist")
    # Redirected even though the DAEMON_SCRIPT check currently returns before
    # the `.daemon-state` mkdir. The guard below asks for this unconditionally,
    # and it is right to: the ordering inside install() is not this test's
    # contract, and reordering those two blocks would silently turn this into a
    # fourth test writing into the checkout.
    _isolate_workspace(monkeypatch, mod, tmp_path / "ws")
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    raised = False
    try:
        mod.install()
    except SystemExit as e:
        raised = True
        assert e.code == 1
    assert raised
    # No launchctl calls, no plist file.
    assert captured == []
    assert not (tmp_path / "test.plist").exists()
