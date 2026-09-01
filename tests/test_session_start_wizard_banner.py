"""Regression: an ABSENT .workspace-identity.json must suppress the setup-wizard banner.

Bug: _setup_wizard_banner only took its ceo-master early-return when the identity
file EXISTED. .workspace-identity.json is gitignored, so a fresh engine clone or a
relocated workspace starts without it. The documented fallback everywhere else
(scripts/utils/workspace.py:get_workspace_identity, session-start.get_workspace_type)
resolves an absent file to type=ceo-master. The banner did not honour that fallback:
absent file fell through to `apply-wizard-answers.py --status`, which returned 0% and
printed a phantom "Workspace not fully set up (0%)" on every fresh-clone session.

The fix returns early on absent file, treating it as ceo-master like everything else.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "session_start", str(ROOT / ".claude" / "hooks" / "session-start.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# A fake apply-wizard-answers.py that always reports incomplete setup. If the banner
# logic reaches it, it WILL print the phantom banner — which is exactly the regression
# we are guarding against for the absent-file and ceo-master cases.
_FAKE_APPLY_SCRIPT = (
    "import json, sys\n"
    "print(json.dumps({'completion_pct': 0, 'required': {'pending': 3, 'skipped': 0}}))\n"
)


def _make_workspace(tmp_path, identity=None):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "apply-wizard-answers.py").write_text(_FAKE_APPLY_SCRIPT, encoding="utf-8")
    if identity is not None:
        import json

        (tmp_path / ".workspace-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return tmp_path


def test_absent_identity_suppresses_banner(tmp_path, capsys, monkeypatch):
    """Absent identity file == legacy ceo-master: no banner, even though the wizard
    status would report 0% if it were ever consulted."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HEADING_OS_WIZARD_QUIET", raising=False)
    ws = _make_workspace(tmp_path, identity=None)
    _mod._setup_wizard_banner(ws)
    assert capsys.readouterr().out == "", "absent identity file must not print the wizard banner"


def test_explicit_ceo_master_suppresses_banner(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HEADING_OS_WIZARD_QUIET", raising=False)
    ws = _make_workspace(tmp_path, identity={"type": "ceo-master", "role": "admin"})
    _mod._setup_wizard_banner(ws)
    assert capsys.readouterr().out == "", "ceo-master must not print the wizard banner"


def test_exec_workspace_incomplete_still_prints_banner(tmp_path, capsys, monkeypatch):
    """The fix must NOT silence a genuinely-unfinished exec workspace."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HEADING_OS_WIZARD_QUIET", raising=False)
    ws = _make_workspace(tmp_path, identity={"type": "exec-workspace", "slug": "jane-doe"})
    _mod._setup_wizard_banner(ws)
    out = capsys.readouterr().out
    assert "not fully set up" in out, "an incomplete exec workspace must still surface the banner"
    assert "0%" in out


# ---------------------------------------------------------------------------
# Two inputs this function reads without checking their type, and the caller
# that makes both fatal.
#
# `main()` calls `_setup_wizard_banner(workspace_root)` as the FIRST thing it
# does after resolving the tree, outside any try. Anything raised here is raised
# out of the SessionStart hook, which exits 1 and delivers none of the alerts it
# went on to compute: CRM red debt, the corporate update, the dep marker, stale
# context, the thread panel. That is the same consequence the file's own comment
# on `get_workspace_type` records for the identity file, and the identity file
# was guarded on 2026-08-31 while these two were not.
#
# MEASURED 2026-09-01 by driving the shipped function directly, before the fix:
#
#     identity `[]`                        AttributeError: 'list' object has no
#                                          attribute 'get'
#     status `[]`                          AttributeError, same shape
#     status `{"completion_pct": "0"}`     TypeError: '>=' not supported between
#                                          instances of 'str' and 'int'
#
# Nothing in this file reached any of them: every case above fed a well-formed
# dict to both readers.
# ---------------------------------------------------------------------------

def _quiet_env(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HEADING_OS_WIZARD_QUIET", raising=False)


@pytest.mark.parametrize("identity_json", ["[]", '"ceo-master"', "3", "null"],
                         ids=["list", "string", "number", "null"])
def test_an_identity_that_is_not_an_object_suppresses_rather_than_raises(
        tmp_path, capsys, monkeypatch, identity_json):
    """`json.loads` succeeds on any well-formed JSON, not only on an object."""
    _quiet_env(monkeypatch)
    ws = _make_workspace(tmp_path, identity=None)
    (ws / ".workspace-identity.json").write_text(identity_json, encoding="utf-8")

    _mod._setup_wizard_banner(ws)   # must not raise

    captured = capsys.readouterr()
    assert captured.out == "", f"a malformed identity printed a banner: {captured.out!r}"
    assert "not an object" in captured.err, (
        "the degrade is silent on stderr too, so nobody can tell a suppressed "
        f"banner from a broken identity file: {captured.err!r}")


@pytest.mark.parametrize("identity_json", ["[]", '"ceo-master"', "3", "null"],
                         ids=["list", "string", "number", "null"])
def test_get_workspace_type_hands_its_callers_a_dict_whatever_the_file_holds(
        tmp_path, identity_json):
    """The SECOND reader of the same file, in the same hook, unbound until now.

    `get_workspace_type` carries its own copy of the identity read and its own
    copy of the isinstance guard, and its docstring records the measurement:
    with `[]` in the file it returned `[]`, and `check_sync_status`,
    `check_corporate_updates`, `check_dep_update_marker` and
    `_setup_wizard_banner` each begin with `identity.get(...)`. `main()` reaches
    two of those unguarded, so the hook exited 1 with a traceback and every
    alert was lost.

    MEASURED 2026-09-01 with that guard deleted: the five session-start test
    files stayed green at 90 passed. The guard above it, on the banner's own
    read, went red. One fix, two copies, one of them tested.
    """
    (tmp_path / ".workspace-identity.json").write_text(identity_json, encoding="utf-8")

    identity = _mod.get_workspace_type(str(tmp_path))

    assert isinstance(identity, dict), identity
    # The documented degrade, not merely "some dict": every caller branches on
    # `type`, and ceo-master is the legacy default the rest of the tree uses.
    assert identity.get("type") == "ceo-master", identity


def _workspace_with_status(tmp_path, body: str):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "apply-wizard-answers.py").write_text(body, encoding="utf-8")
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"type": "exec-workspace", "slug": "jane-doe"}), encoding="utf-8")
    return tmp_path


def _status_printing(raw_json: str) -> str:
    """A stand-in `apply-wizard-answers.py` that prints exactly `raw_json`.

    The JSON text is passed through verbatim rather than round-tripped through
    `json.dumps`, so a case can carry a shape Python has no literal for.
    """
    return "print(%r)\n" % raw_json


@pytest.mark.parametrize("printed", ["[]", '"done"', "3", "null"],
                         ids=["list", "string", "number", "null"])
def test_a_status_payload_that_is_not_an_object_suppresses_rather_than_raises(
        tmp_path, capsys, monkeypatch, printed):
    _quiet_env(monkeypatch)
    ws = _workspace_with_status(tmp_path, _status_printing(printed))

    _mod._setup_wizard_banner(ws)   # must not raise

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not an object" in captured.err, captured.err


@pytest.mark.parametrize("value", ['"0"', "null", "[]", '{"a": 1}', "true"],
                         ids=["str", "null", "list", "dict", "bool"])
def test_a_completion_pct_that_is_not_a_number_suppresses_rather_than_raises(
        tmp_path, capsys, monkeypatch, value):
    """`.get(key, default)` is not a type check. The default fires only on an
    ABSENT key; a present-but-wrong value goes straight into the comparison."""
    _quiet_env(monkeypatch)
    ws = _workspace_with_status(
        tmp_path, _status_printing('{"completion_pct": %s}' % value))

    _mod._setup_wizard_banner(ws)   # must not raise

    captured = capsys.readouterr()
    assert captured.out == "", f"a malformed percentage printed a banner: {captured.out!r}"
    assert "not a number" in captured.err, captured.err


@pytest.mark.parametrize("pct", ["100", "100.0", "137"])
def test_a_finished_exec_workspace_prints_nothing(tmp_path, capsys, monkeypatch, pct):
    """The bound the banner turns on, which nothing asserted.

    Every case above this block feeds 0%, so `pct >= 100` could become
    `pct > 100` with the whole file green, and a fully set up exec workspace
    would be told to run the wizard at the top of every session.
    """
    _quiet_env(monkeypatch)
    ws = _workspace_with_status(
        tmp_path, _status_printing('{"completion_pct": %s}' % pct))

    _mod._setup_wizard_banner(ws)

    assert capsys.readouterr().out == "", (
        f"a {pct}%-complete workspace was told to run the wizard")
