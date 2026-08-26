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


@pytest.fixture
def workspace_root(tmp_path):
    """Isolated workspace tree for daemon tests."""
    (tmp_path / ".daemon-state").mkdir()
    (tmp_path / "outputs" / "operations" / "email-intelligence").mkdir(parents=True)
    (tmp_path / "outputs" / "content" / "linkedin").mkdir(parents=True)
    return tmp_path
