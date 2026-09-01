#!/usr/bin/env python3
"""Tests for the templates/ data-seam: the shared-doc source of truth resolves
under the data root, not the engine root.

HEADING OS engine/data separation. templates/ routes `private`
(config/routing-map.yaml), so the five shared documentation sources
(GETTING-STARTED, CEO-ADMIN-GUIDE, EMERGENCY-PROCEDURES, CLAUDE.md.template) live
in the data overlay. Before this seam was wired,
get_templates_dir() did not exist and workspace-health.py hardcoded
WORKSPACE / "templates" (the engine root) -- which is empty after the split, so
the health check reported every shared doc as "missing" on every run (13 phantom
issue lines). These tests pin the helper to the data root and guard the health
script against the regression.
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HEALTH_SRC = ROOT / "scripts" / "workspace-health.py"

#: The four members of the shared-doc set `check_doc_versions` opens. Spelled
#: out rather than imported from the script, so the two disagreeing is a
#: failure here rather than an agreement by construction.
SHARED_DOCS = ("GETTING-STARTED.md", "CEO-ADMIN-GUIDE.md",
               "EMERGENCY-PROCEDURES.md", "CLAUDE.md.template")


def _load_health():
    """Load workspace-health.py by path; its filename is kebab-case."""
    spec = importlib.util.spec_from_file_location("workspace_health", HEALTH_SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """A data root distinct from the workspace root, with a templates/ dir."""
    d = tmp_path / ".heading-os-data"
    (d / "templates").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    # workspace.py caches identity per-root; reset so is_ceo_workspace() is fresh.
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    return d


def test_templates_dir_resolves_under_data_root(data_root):
    from scripts.utils.workspace import get_templates_dir, get_workspace_root
    tpl = get_templates_dir()
    assert tpl == data_root / "templates"
    assert tpl != get_workspace_root() / "templates"  # NOT the engine root (the bug)


def test_health_script_calls_the_helper_it_imported():
    """workspace-health.py must resolve templates/ through get_templates_dir(),
    never by joining the engine WORKSPACE root (the pre-seam regression).

    Asked of the AST, not of a substring. `"get_templates_dir" in src` was true
    of the import line alone, so deleting every CALL and leaving the import
    behind kept this green while the phantom-missing bug was back. Both call
    sites are required: `check_docs_sync` and `check_doc_versions` each resolve
    the directory independently, and a fix in one of two is this repository's
    usual defect.
    """
    src = HEALTH_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    callers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "get_templates_dir"
                for c in ast.walk(fn))
    }
    assert {"check_docs_sync", "check_doc_versions"} <= callers, (
        "workspace-health.py must CALL get_templates_dir() in both checks that "
        f"resolve templates/; it calls it in {sorted(callers)}"
    )
    assert 'WORKSPACE / "templates"' not in src, \
        "workspace-health.py still hardcodes WORKSPACE / 'templates' (engine root) -- the phantom-missing bug"


def test_version_marker_check_reads_the_data_side_templates(data_root):
    """check_doc_versions must read the data-side templates, so a marked file is
    recognised rather than reported missing.

    The assertion is the RETURN VALUE of the check, which is what the sentence
    above is about. Until 2026-09-01 this test wrote one file and then asserted
    only that the file existed and that `get_templates_dir()` pointed at it:
    `check_doc_versions` was never called, so the claim in its own name was the
    one thing it did not measure.
    """
    templates = data_root / "templates"
    for name in SHARED_DOCS:
        (templates / name).write_text(
            "<!-- version: 9.9.9 | last-updated: 2099-01-01 -->\n# Guide\n",
            encoding="utf-8")

    wh = _load_health()
    assert wh.get_templates_dir() == templates
    assert wh.check_doc_versions() == 0, (
        "a complete, marked template set under the DATA root was still reported "
        "as issues; the check is resolving templates/ somewhere else"
    )


def test_the_version_check_still_counts_a_document_that_is_absent(data_root):
    """Anchor for the test above: a check that returns 0 over anything is not a
    check. One member removed must cost exactly one issue."""
    templates = data_root / "templates"
    for name in SHARED_DOCS[1:]:
        (templates / name).write_text(
            "<!-- version: 9.9.9 | last-updated: 2099-01-01 -->\n# Guide\n",
            encoding="utf-8")

    wh = _load_health()
    assert wh.check_doc_versions() == 1


def test_both_checks_degrade_to_a_warning_when_there_is_no_data_overlay(
        tmp_path, monkeypatch, capsys):
    """The public-clone state: HEADING_OS_DATA resolves, templates/ does not.

    A bare engine clone has no private overlay, so the shared-doc set is absent
    rather than incomplete. Both checks must return 0 and SAY the set was not
    examined; returning 0 in silence would read as "all shared docs verified"
    on the clone where none were.
    """
    empty = tmp_path / "no-overlay"
    empty.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(empty))
    from scripts.utils import workspace
    workspace._reset_identity_cache()

    wh = _load_health()
    assert not wh.get_templates_dir().is_dir(), "the fixture must have no templates/"
    assert wh.check_doc_versions() == 0
    assert wh.check_docs_sync() == 0
    out = capsys.readouterr().out
    assert out.count("no data overlay") == 2, out
