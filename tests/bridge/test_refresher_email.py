import json
from scripts.bridge_daemon.refreshers.email import read_email_state, count_unread


class _FakePath:
    """Stand-in for the module-level PRODUCER_SCRIPT Path.

    pathlib.WindowsPath.exists is a read-only attribute, so monkeypatching
    it on a real Path instance fails. We swap the whole module attribute
    for an object that supports .exists() and str()/repr().
    """
    def __init__(self, exists: bool, path: str = "fake-producer.py"):
        self._exists = exists
        self._path = path

    def exists(self) -> bool:
        return self._exists

    def __str__(self) -> str:
        return self._path

    def __fspath__(self) -> str:
        return self._path

def test_read_state_missing_returns_empty(workspace_root):
    assert read_email_state(workspace_root) == {"messages": []}

def test_count_unread(workspace_root):
    state_file = workspace_root / "outputs/operations/email-intelligence/state.json"
    state_file.write_text(json.dumps({
        "messages": [
            {"id": "1", "unread": True, "subject": "a"},
            {"id": "2", "unread": False, "subject": "b"},
            {"id": "3", "unread": True, "subject": "c"},
        ]
    }))
    state = read_email_state(workspace_root)
    assert count_unread(state) == 2


def test_refresh_bumps_inbox_version(workspace_root, monkeypatch):
    """refresh() must call state_obj.bump('inbox'), even when state.json missing."""
    import scripts.bridge_daemon.refreshers.email as email_mod
    from scripts.bridge_daemon.state import State

    # Force missing-producer branch so this test stays hermetic and doesn't
    # actually try to subprocess email-intelligence.py.
    monkeypatch.setattr(email_mod, "PRODUCER_SCRIPT", _FakePath(False))

    state = State()
    assert state.version("inbox") == 0
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == 1
    email_mod.refresh(workspace_root, state)  # second call also bumps
    assert state.version("inbox") == 2


def test_refresh_subprocess_success_bumps_inbox(workspace_root, monkeypatch):
    """When the producer subprocess succeeds, refresh bumps inbox version."""
    import scripts.bridge_daemon.refreshers.email as email_mod
    from scripts.bridge_daemon.state import State
    state = State()
    before = state.version("inbox")
    # Mock subprocess.run to simulate a clean run.
    class FakeResult:
        returncode = 0
        stdout = "ok"
        stderr = ""
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    # Ensure the producer "exists" so the missing-script branch isn't taken.
    monkeypatch.setattr(email_mod, "PRODUCER_SCRIPT", _FakePath(True))
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == before + 1


def test_refresh_subprocess_failure_still_bumps_inbox(workspace_root, monkeypatch):
    """A non-zero exit from the producer is logged but doesn't suppress the bump."""
    import scripts.bridge_daemon.refreshers.email as email_mod
    from scripts.bridge_daemon.state import State
    state = State()
    before = state.version("inbox")
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Exchange connection refused"
    def fake_run(*args, **kwargs):
        return FakeResult()
    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(email_mod, "PRODUCER_SCRIPT", _FakePath(True))
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == before + 1


def test_refresh_subprocess_timeout_still_bumps_inbox(workspace_root, monkeypatch):
    """A TimeoutExpired from the producer is logged but doesn't crash refresh."""
    import scripts.bridge_daemon.refreshers.email as email_mod
    from scripts.bridge_daemon.state import State
    import subprocess as sp
    state = State()
    before = state.version("inbox")
    def fake_run(*args, **kwargs):
        raise sp.TimeoutExpired(cmd="email-intelligence.py", timeout=90)
    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(email_mod, "PRODUCER_SCRIPT", _FakePath(True))
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == before + 1


def test_refresh_missing_producer_warns_and_bumps(workspace_root, monkeypatch):
    """When the producer script is missing, refresh logs a warning and still
    bumps the inbox version (so the dashboard freshness UI advances)."""
    import scripts.bridge_daemon.refreshers.email as email_mod
    from scripts.bridge_daemon.state import State
    state = State()
    before = state.version("inbox")
    # Force PRODUCER_SCRIPT.exists() to False.
    monkeypatch.setattr(email_mod, "PRODUCER_SCRIPT", _FakePath(False))
    # subprocess.run must NOT be called - if it is, this test fails noisily.
    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run called when producer is missing")
    monkeypatch.setattr(email_mod.subprocess, "run", boom)
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == before + 1
