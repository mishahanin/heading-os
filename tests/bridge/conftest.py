import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_data_root(tmp_path, monkeypatch):
    """Pin the data-root seam to the test's tmp tree.

    The bridge source/finalizer functions resolve DATA under ``get_data_root()``
    when a caller omits the optional ``data_root`` argument (fail-safe fallback,
    F-H8). In production that is the real data sibling; in unit tests it must be
    the per-test tmp dir the test writes its fixtures into. Tests pass the SAME
    root as ``workspace_root``, so HEADING_OS_DATA == tmp_path keeps the read/write
    isolated. Tests with a non-tmp_path data tree (e.g. a nested workspace) pass
    ``data_root=`` explicitly, which overrides this fallback entirely.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))


@pytest.fixture(autouse=True)
def _pin_the_operator_zone(monkeypatch):
    """Pin the operator's timezone to UTC+4 for every bridge unit test.

    Not a preference. Thirty-seven tests in this directory already assume it,
    in writing: `test_sources_agenda.py` says "date is still set (local (UTC+4)
    today)" and "10:00 UTC = 14:00 local (UTC+4)", and the pulse, adoption and
    ops suites do the same arithmetic without saying so. Every bridge source
    defines "today" through `get_default_tz()`, which reads `HEADING_OS_TZ`
    fresh on each call, so those comments were true only because the host
    happened to be set that way.

    Measured 2026-08-27 by running the suite at UTC-12: 37 failures, none of
    them a defect in the code. `test_empty_when_file_missing` alone reported
    `'2026-05-17' == '2026-05-18'` for a source that was behaving exactly as
    documented.

    Pinned via the environment rather than by patching each module's imported
    `get_default_tz`, because there are four such modules and the next source
    added would not be covered. A bridge test that genuinely needs another zone
    overrides this with its own `monkeypatch.setenv` or a patched module
    attribute, as `test_compaction_probe.py` does for Dubai and UTC.
    """
    monkeypatch.setenv("HEADING_OS_TZ", "Etc/GMT-4")


@pytest.fixture(autouse=True)
def _no_graphical_session(monkeypatch):
    """Every bridge test runs headless unless it says otherwise.

    `scripts/bridge_daemon/terminal.py` decides whether to spawn a GUI attach
    with `_is_linux_gui_session()`, which reads DISPLAY and WAYLAND_DISPLAY off
    the ambient environment. This workstation has both; the CI runner has
    neither. Measured 2026-08-27 with `--cov-branch`: with a display,
    `find_linux_terminal()` (lines 93-97) and the attach Popen (line 492) are
    covered; with `env -u DISPLAY -u WAYLAND_DISPLAY` they are not covered at
    all, and the suite passes either way. So the launch path had two different
    shapes on two machines and nothing said which one a test was measuring.

    `test_endpoints.py` said it in a comment and got it wrong: "Windows = 1
    call, macOS/Linux = 2" is true here and false on CI.

    Headless is the deterministic default because it is what CI has. A test
    that wants the GUI branch sets DISPLAY itself; see
    `test_a_launcher_must_not_report_a_window_that_never_opened.py`, which now
    covers both sides on purpose.
    """
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


@pytest.fixture
def workspace_root(tmp_path):
    """Isolated workspace tree for daemon tests."""
    (tmp_path / ".daemon-state").mkdir()
    (tmp_path / "outputs" / "operations" / "email-intelligence").mkdir(parents=True)
    (tmp_path / "outputs" / "content" / "linkedin").mkdir(parents=True)
    return tmp_path
