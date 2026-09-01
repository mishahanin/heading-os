"""Tests for scripts/inbox_pulse/paths.py and scripts/inbox_pulse/state.py.

The two modules are tested together because get_state_dir() from paths.py
underpins every helper in state.py. All tests use the INBOX_PULSE_STATE_DIR
env-var override to avoid touching the real workspace state directory.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_paths():
    """Clear the paths module's caches on the already-imported module.

    get_state_dir() and get_workspace_root() cache their results at
    module level. Between tests that alter INBOX_PULSE_STATE_DIR we must
    reset the caches so each test sees a fresh resolution.

    It said "re-import", which is what `_import_state` below does with
    `importlib.reload`. A plain `import` hands back the module already in
    `sys.modules` and re-executes nothing; the two assignments are the whole
    mechanism. A reader who believed the old sentence would have expected
    module-level state other than these two names to be reset as well.
    """
    import scripts.inbox_pulse.paths as mod
    mod._workspace_root_cache = None
    mod._state_dir_cache = None
    return mod


# ---------------------------------------------------------------------------
# paths.py tests
# ---------------------------------------------------------------------------


def test_get_state_dir_honors_env_var(tmp_path, monkeypatch):
    """INBOX_PULSE_STATE_DIR env var takes highest priority."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    paths = _reload_paths()

    result = paths.get_state_dir()

    assert result == tmp_path
    assert tmp_path.exists()


def test_get_state_dir_falls_back_to_data_root(monkeypatch, tmp_path):
    """Without env override, state dir is <data_root>/state/email-triage/.

    Runtime state (cursor, ledger, cost tracker, logs) is data, so it must
    resolve under the DATA root via the data-root seam -- never inside the
    engine tree, which must stay clean. Regression for the Steward-cutover
    finding that email-triage was writing state/email-triage/ into the engine
    clone.
    """
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    paths = _reload_paths()

    monkeypatch.setattr(paths, "_state_dir_cache", None)

    # Mock get_data_root so we don't touch the real data overlay during the test.
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)

    result = paths.get_state_dir()

    expected = tmp_path / "state" / "email-triage"
    assert result == expected
    assert expected.exists()


def test_get_workspace_root_honours_the_shared_override(tmp_path, monkeypatch):
    """This module has no walk of its own; it delegates, and that is the point.

    It used to carry a private copy that walked up from `_THIS_FILE` looking for
    a directory holding both `config/` and `scripts/`, and that copy silently
    ignored the `WORKSPACE_ROOT` override the shared helper honours: two answers
    to one question, with the daemon reading the one that cannot be redirected.
    The copy was deleted; this test kept monkeypatching `_THIS_FILE`, a name the
    module no longer has, so it was pinning a removed implementation and failing
    against the correct one. It now pins the delegation itself.
    """
    workspace = tmp_path / "workspace"
    (workspace / "config").mkdir(parents=True)
    (workspace / "scripts").mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))

    paths = _reload_paths()
    assert paths.get_workspace_root() == workspace.resolve()


def test_get_workspace_root_is_not_a_second_implementation():
    """The other direction: with no override, both modules answer the same.

    A private copy reintroduced here would satisfy the override test above only
    if it also read the env var, and would still be a second answer to drift.
    """
    from scripts.utils.paths import get_workspace_root as shared

    paths = _reload_paths()
    assert paths.get_workspace_root() == shared()


# ---------------------------------------------------------------------------
# state.py tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_paths_cache(tmp_path, monkeypatch):
    """For EVERY test in this module: point INBOX_PULSE_STATE_DIR at tmp_path
    and clear the module-level caches so each test gets a fresh state dir.

    It said "every state.py test". `autouse=True` at module level does not
    scope itself to the tests below it: the three paths.py tests defined above
    also run inside this fixture. They are unaffected only because each of them
    sets the variable and clears the caches again for itself, which is a
    coincidence a reader should not have to rediscover.
    """
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths()
    yield
    # Clear again after the test for clean teardown.
    _reload_paths()


def _import_state():
    """Import state module with reloaded paths cache."""
    import scripts.inbox_pulse.state as mod
    importlib.reload(mod)
    return mod


def test_append_jsonl_writes_one_line_per_call(tmp_path):
    """Two append_jsonl calls produce exactly two parseable JSON lines."""
    state = _import_state()

    state.append_jsonl("test.jsonl", {"a": 1})
    state.append_jsonl("test.jsonl", {"b": 2})

    lines = (tmp_path / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}


def test_load_state_returns_default_when_missing(tmp_path):
    """load_state returns the default value when the file does not exist."""
    state = _import_state()

    result = state.load_state("missing.json", default={"foo": "bar"})

    assert result == {"foo": "bar"}


def test_load_state_raises_on_corrupted_json(tmp_path):
    """load_state raises json.JSONDecodeError on corrupted content (loud failure)."""
    state = _import_state()
    (tmp_path / "corrupt.json").write_text("not { valid json !!!", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        state.load_state("corrupt.json")


def test_save_state_leaves_no_tmp_orphans_and_roundtrips(tmp_path):
    """The happy path, named for what it measures.

    RENAMED 2026-09-01 from `test_save_state_is_atomic`. Neither assertion below
    has anything to do with atomicity: a plain truncating `open(path, "w")`
    leaves no `.tmp` file either (it never makes one) and roundtrips a
    successful write perfectly. Atomicity is a claim about the INTERRUPTED
    write, and it is now measured by the test underneath this one.
    """
    state = _import_state()
    payload = {"version": 1, "items": [1, 2, 3]}

    state.save_state("roundtrip.json", payload)

    # No .tmp files should remain.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    # Data roundtrips.
    loaded = state.load_state("roundtrip.json")
    assert loaded == payload


def test_an_interrupted_save_leaves_the_previous_state_intact(tmp_path):
    """The atomicity claim, measured. NEW 2026-09-01.

    `save_state`'s docstring promises "A crash at any point leaves the previous
    file intact". Until this test nothing exercised a crash, so the promise was
    prose. MEASURED that day by replacing the mkstemp + os.replace body with the
    naive form the docstring rules out:

        -   tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        -   ... write, fsync, os.replace ...
        +   with open(path, "w", encoding="utf-8") as fh:
        +       json.dump(data, fh, indent=2, ensure_ascii=False)

        .venv/bin/python -m pytest tests/inbox_pulse -q
            -> 226 passed          (baseline: 226 passed)

    and against the 45 test files anywhere in tests/ that name inbox_pulse,
    observability_safe, healthchecks or hc_ping, plus tests/contract:

        -> 7 failed, 1199 passed, 3 skipped
           (baseline: the identical 7 failed, 1199 passed, 3 skipped;
            those 7 are sandbox-environment failures, present either way)

    Nothing anywhere noticed. The interruption is produced without patching the
    stdlib: `json.dump` streams, so a value it cannot serialise lands the
    earlier bytes on disk and then raises, which is what a crash mid-write looks
    like from the file's side. Under the naive form the target is left holding
    `{\\n  "cursor": "xxx...",\\n  "boom": ` and the previous state is gone.

    Why it matters here rather than in the abstract: the daemon persists its
    inbox cursor through this function. A truncated cursor file is not valid
    JSON, `load_state` is documented to raise on that, and `get_cursor()` runs
    at the top of `_main_loop` OUTSIDE its try, so the next start dies before
    the first poll.
    """
    state = _import_state()
    good = {"cursor": "2026-09-01T09:00:00+04:00"}
    state.save_state("inbox_cursor.json", good)

    target = tmp_path / "inbox_cursor.json"
    before = target.read_bytes()

    # `set()` is not JSON-serialisable. Ordered after a long string so the
    # encoder has already emitted real bytes by the time it gives up.
    with pytest.raises(TypeError):
        state.save_state("inbox_cursor.json",
                         {"cursor": "x" * 200, "boom": set()})

    assert target.read_bytes() == before, (
        "an interrupted save left the cursor file changed; the previous state "
        "must survive byte for byte")
    assert state.load_state("inbox_cursor.json") == good
    assert list(tmp_path.glob("*.tmp")) == [], (
        "the failed write left its scratch file behind")


def test_load_state_returns_the_default_for_an_empty_file(tmp_path):
    """The second documented default path, which had no case. NEW 2026-09-01.

    `load_state`'s docstring says "Returns `default` when the file is missing or
    empty". Only the missing half was covered. MEASURED 2026-09-01 by deleting
    the empty check:

        -   text = path.read_text(encoding="utf-8").strip()
        -   if not text:
        -       return default
        +   text = path.read_text(encoding="utf-8").strip()

        tests/inbox_pulse            -> 226 passed  (baseline: 226 passed)
        the 45-file wide set + contract -> 7 failed, 1199 passed, 3 skipped
                                        (identical to baseline)

    A zero-byte state file is not exotic: it is what a pre-atomic-era write, a
    full disk, or an `install -m` style touch leaves behind, and `json.loads("")`
    raises. The consequence is the same one as the test above: the daemon's
    `get_cursor()` sits outside `_main_loop`'s try and takes the process down.

    Whitespace-only is included because the code `.strip()`s before testing, so
    that branch is a second, separate behaviour claim.
    """
    state = _import_state()
    (tmp_path / "empty.json").write_text("", encoding="utf-8")
    (tmp_path / "blank.json").write_text("   \n\t\n", encoding="utf-8")

    sentinel = {"fresh": True}
    assert state.load_state("empty.json", default=sentinel) is sentinel
    assert state.load_state("blank.json", default=sentinel) is sentinel
    assert state.load_state("empty.json") is None, "the None default too"


def test_a_file_with_real_content_is_still_parsed(tmp_path):
    """Anchor for the two tests above. A `load_state` hard-wired to `default`
    satisfies the empty-file case and the missing-file case both, and would
    hand the daemon a bootstrap cursor on every start, silently re-polling from
    now and skipping everything that arrived while it was down."""
    state = _import_state()
    (tmp_path / "real.json").write_text('{"cursor": "2026-09-01T09:00:00+04:00"}',
                                        encoding="utf-8")

    assert state.load_state("real.json", default={"fresh": True}) == {
        "cursor": "2026-09-01T09:00:00+04:00"}


def test_write_heartbeat_records_pid_and_timestamp(tmp_path):
    """write_heartbeat() writes last_heartbeat, daemon_pid, and queue_depth."""
    state = _import_state()

    state.write_heartbeat()

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "last_heartbeat" in data
    assert "daemon_pid" in data
    assert "queue_depth" in data
    assert data["daemon_pid"] == os.getpid()
    assert data["queue_depth"] == 0
    # last_heartbeat must parse as ISO-8601.
    parsed = datetime.fromisoformat(data["last_heartbeat"])
    assert parsed.tzinfo is not None


def test_write_heartbeat_merges_extra(tmp_path):
    """write_heartbeat(extra=...) merges extra fields on top of defaults."""
    state = _import_state()

    extra = {"queue_depth": 5, "last_email_processed_at": "2026-05-27T12:00:00+04:00"}
    state.write_heartbeat(extra=extra)

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["queue_depth"] == 5
    assert data["last_email_processed_at"] == "2026-05-27T12:00:00+04:00"
    # Base fields still present.
    assert "last_heartbeat" in data
    assert "daemon_pid" in data
