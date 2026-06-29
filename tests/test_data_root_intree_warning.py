"""F-M2: get_data_root() must warn when the in-tree heuristic fires.

When the engine clone carries its own ``crm/contacts/`` or ``knowledge/``
directory and HEADING_OS_DATA is unset, get_data_root() returns the workspace
root itself (the legacy transitional path for ceo-main).  On a proper
data-less engine clone this indicates a misconfiguration and the caller should
be warned to set HEADING_OS_DATA or use the sibling .heading-os-data repo.
"""
import logging
import os
from pathlib import Path

import pytest

import scripts.utils.paths as paths


def test_intree_heuristic_emits_warning_crm_contacts(tmp_path, monkeypatch, caplog):
    """Heuristic matches via crm/contacts/ — a WARNING must be logged."""
    (tmp_path / "crm" / "contacts").mkdir(parents=True)

    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    monkeypatch.setattr("scripts.utils.paths.get_workspace_root", lambda: tmp_path)

    with caplog.at_level(logging.WARNING, logger="scripts.utils.paths"):
        result = paths.get_data_root()

    assert result == tmp_path
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING from get_data_root() in-tree branch"
    combined = " ".join(r.message for r in warnings).lower()
    assert "in-tree" in combined or "data-root" in combined or "data root" in combined, (
        f"WARNING message should mention in-tree or data-root; got: {combined!r}"
    )


def test_intree_heuristic_emits_warning_knowledge(tmp_path, monkeypatch, caplog):
    """Heuristic matches via knowledge/ — a WARNING must be logged."""
    (tmp_path / "knowledge").mkdir(parents=True)

    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    monkeypatch.setattr("scripts.utils.paths.get_workspace_root", lambda: tmp_path)

    with caplog.at_level(logging.WARNING, logger="scripts.utils.paths"):
        result = paths.get_data_root()

    assert result == tmp_path
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected at least one WARNING from get_data_root() in-tree branch"


def test_env_override_no_warning(tmp_path, monkeypatch, caplog):
    """When HEADING_OS_DATA is set and valid, the in-tree branch does not run."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "engine" / "crm" / "contacts").mkdir(parents=True)

    monkeypatch.setenv("HEADING_OS_DATA", str(data_dir))
    monkeypatch.setattr(
        "scripts.utils.paths.get_workspace_root",
        lambda: tmp_path / "engine",
    )

    with caplog.at_level(logging.WARNING, logger="scripts.utils.paths"):
        result = paths.get_data_root()

    assert result == data_dir.resolve()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, (
        "No WARNING should be emitted when HEADING_OS_DATA env override wins"
    )


def test_sibling_no_warning(tmp_path, monkeypatch, caplog):
    """When sibling .heading-os-data exists and no in-tree data, no WARNING."""
    engine = tmp_path / ".heading-os"
    engine.mkdir()
    sibling = tmp_path / ".heading-os-data"
    sibling.mkdir()

    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    monkeypatch.setattr("scripts.utils.paths.get_workspace_root", lambda: engine)

    with caplog.at_level(logging.WARNING, logger="scripts.utils.paths"):
        result = paths.get_data_root()

    assert result == sibling.resolve()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not warnings, (
        "No WARNING should be emitted when sibling .heading-os-data is used"
    )
