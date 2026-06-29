"""Tests for scripts/install-bridge-service-mac.py.

The script is macOS-only (guarded by sys.platform), but the plist
builder is pure data. We import via importlib (kebab-case CLI script
can't be imported as a module) and test the plist shape on any
platform.
"""
import importlib.util
import sys
from pathlib import Path


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "install_bridge_service_mac",
        Path("scripts/install-bridge-service-mac.py").resolve(),
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
    captured: list[list[str]] = []
    _patch_subprocess(monkeypatch, mod, captured)

    mod.install()

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


def test_install_aborts_when_daemon_script_missing(monkeypatch, tmp_path):
    """If scripts/bridge-daemon.py doesn't exist, install() exits 1 before
    touching the filesystem."""
    mod = _load_module()
    bogus_script = tmp_path / "nonexistent.py"
    monkeypatch.setattr(mod, "DAEMON_SCRIPT", bogus_script)
    monkeypatch.setattr(mod, "PLIST_PATH", tmp_path / "test.plist")
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
