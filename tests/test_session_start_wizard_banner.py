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
from pathlib import Path

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
