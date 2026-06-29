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


@pytest.fixture
def workspace_root(tmp_path):
    """Isolated workspace tree for daemon tests."""
    (tmp_path / ".daemon-state").mkdir()
    (tmp_path / "outputs" / "operations" / "email-intelligence").mkdir(parents=True)
    (tmp_path / "outputs" / "content" / "linkedin").mkdir(parents=True)
    return tmp_path
