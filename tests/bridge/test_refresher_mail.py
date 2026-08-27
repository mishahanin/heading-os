import json
from scripts.bridge_daemon.refreshers.mail import read_email_state, count_unread


def _plant_producer(workspace_root):
    """Put a real `scripts/email-intelligence.py` under the root refresh() reads.

    `refresh()` resolves its target with `producer_script(workspace_root)`, NOT
    with the module constant `PRODUCER_SCRIPT`. Three tests here patched the
    constant to a `_FakePath(True)` and were inert: the fixture root has no
    `scripts/` directory, so `script.exists()` was False, refresh took the
    missing-producer branch, and the patched `subprocess.run` was never called.
    All three asserted only the version bump, which BOTH branches produce, so
    nothing could tell them apart.

    Coverage measured 2026-08-27 on this file alone: lines 126-154 of
    `refreshers/mail.py` - the whole subprocess block including the
    non-zero-returncode warning, the TimeoutExpired handler and the OSError
    handler - were never executed by the three tests named after them.
    """
    script = workspace_root / "scripts" / "email-intelligence.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# stand-in; subprocess.run is patched\n", encoding="utf-8")
    return script

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
    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State

    # The fixture root has no scripts/ directory, so the missing-producer
    # branch is what runs here and nothing needs forcing. It used to patch
    # PRODUCER_SCRIPT to force it, which did nothing at all: refresh() reads
    # `producer_script(workspace_root)`.
    state = State()
    assert state.version("inbox") == 0
    email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == 1
    email_mod.refresh(workspace_root, state)  # second call also bumps
    assert state.version("inbox") == 2


def test_refresh_subprocess_success_bumps_inbox(workspace_root, monkeypatch):
    """When the producer subprocess succeeds, refresh bumps inbox AND the clock.

    `data_time` is the assertion that separates this branch from the
    missing-producer one. The version bump alone does not: `refresh()` bumps on
    every path, and that was the only thing this test checked while it was
    silently running the wrong branch.
    """
    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State
    _plant_producer(workspace_root)
    state = State()
    before = state.version("inbox")
    calls = []

    class FakeResult:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    email_mod.refresh(workspace_root, state)
    assert calls, "the producer was never invoked; the missing-script branch ran"
    assert state.version("inbox") == before + 1
    assert state.data_time("inbox") is not None, (
        "a successful fetch must advance the freshness clock"
    )


def test_refresh_subprocess_failure_still_bumps_inbox(workspace_root, monkeypatch,
                                                      caplog):
    """A non-zero exit is logged, bumps the version, and does NOT claim freshness."""
    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State
    _plant_producer(workspace_root)
    state = State()
    before = state.version("inbox")
    calls = []

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Exchange connection refused"

    def fake_run(*args, **kwargs):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        email_mod.refresh(workspace_root, state)
    assert calls, "the producer was never invoked; the missing-script branch ran"
    assert state.version("inbox") == before + 1
    assert state.data_time("inbox") is None, (
        "a failed run established nothing about how old the inbox is"
    )
    assert "producer exited 1" in caplog.text, caplog.text


def test_refresh_subprocess_timeout_still_bumps_inbox(workspace_root, monkeypatch,
                                                      caplog):
    """A TimeoutExpired is logged, bumps the version, and does NOT claim freshness."""
    import subprocess as sp

    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State
    _plant_producer(workspace_root)
    state = State()
    before = state.version("inbox")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise sp.TimeoutExpired(cmd="email-intelligence.py", timeout=90)

    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        email_mod.refresh(workspace_root, state)
    assert calls, "the producer was never invoked; the missing-script branch ran"
    assert state.version("inbox") == before + 1
    assert state.data_time("inbox") is None
    assert "producer timed out" in caplog.text, caplog.text


def test_refresh_survives_an_os_error_from_the_producer(workspace_root, monkeypatch,
                                                        caplog):
    """The third handler, which no test reached at all.

    `except OSError` catches the case where the interpreter cannot be spawned.
    Coverage on 2026-08-27 showed lines 126-154 unexecuted by this file, and
    this branch had no test anywhere.
    """
    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State
    _plant_producer(workspace_root)
    state = State()

    def fake_run(*args, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(email_mod.subprocess, "run", fake_run)
    with caplog.at_level("WARNING"):
        email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == 1
    assert state.data_time("inbox") is None
    assert "subprocess failed" in caplog.text, caplog.text


def test_refresh_missing_producer_warns_and_bumps(workspace_root, monkeypatch,
                                                  caplog):
    """When the producer script is missing, refresh logs a warning and still
    bumps the inbox version (so the dashboard freshness UI advances).

    The absence is real: the fixture root has no `scripts/` directory, and
    `refresh()` resolves `producer_script(workspace_root)`. It used to force the
    branch by patching `PRODUCER_SCRIPT`, which `refresh()` does not read, so
    the test was right by accident.
    """
    import scripts.bridge_daemon.refreshers.mail as email_mod
    from scripts.bridge_daemon.state import State
    state = State()
    before = state.version("inbox")
    assert not (workspace_root / "scripts" / "email-intelligence.py").exists()

    # subprocess.run must NOT be called - if it is, this test fails noisily.
    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run called when producer is missing")

    monkeypatch.setattr(email_mod.subprocess, "run", boom)
    with caplog.at_level("WARNING"):
        email_mod.refresh(workspace_root, state)
    assert state.version("inbox") == before + 1
    assert state.data_time("inbox") is None
    assert "producer script missing" in caplog.text, caplog.text
