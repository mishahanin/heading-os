"""`--reset` must run on the state it exists to undo.

Found by the 2026-08-23 engine audit.

Without `--force`, `cmd_reset` refused whenever ANY tracked file was dirty. But
the wizard's whole job is to make tracked files dirty: it substitutes
placeholders and renders templates in place. So the only flow anyone would ever
want -- run the wizard, dislike the result, reset -- hit
"ERROR: uncommitted changes detected" every single time, and the non-force path
was reachable only when the tree was already clean, i.e. when reset would be a
no-op.

The consequence is worse than the annoyance. A safety check that always fires
trains the operator to reach for `--force`, and `--force` skips the check for
everything, including the hand edits reset then discards without asking. A gate
that is always in the way is a gate that gets routed around.

The machinery for the correct check already existed twenty lines lower:
`cmd_reset` computes `touched`, the exact set of files it is about to revert.
It just ran the dirty check first. Now `touched` is computed first and the
dirty check ignores those paths, so reset refuses for unrelated work and
proceeds for its own.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _wizard():
    path = ROOT / "scripts" / "apply-wizard-answers.py"
    spec = importlib.util.spec_from_file_location("wizard_reset_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


W = _wizard()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo carrying one templated file and one unrelated file."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("Welcome to {COMPANY}.\n", encoding="utf-8")
    (tmp_path / "unrelated.md").write_text("hand written\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_text(
        "- id: company_name\n"
        "  audience: [public]\n"
        "  type: placeholder\n"
        "  required: true\n"
        '  prompt: "Company name?"\n'
        '  example: "Acme"\n'
        "  target:\n"
        '    placeholder: "{COMPANY}"\n'
        '    files: ["README.md"]\n',
        encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


class _Args:
    def __init__(self, root, force=False):
        self.workspace_root = root
        self.force = force
        self.resolved_audience = "public"


def _answer(root: Path, value: str) -> None:
    """Do what the wizard does: substitute, and record the answer."""
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace("{COMPANY}", value),
                 encoding="utf-8")
    W.save_answers(root, {"schema_version": W.SCHEMA_VERSION,
                          "audience": "public", "answers": {
        "company_name": {"value": value, "status": "answered",
                         "answered_at": "2026-08-23T00:00:00+00:00"}}})


def test_the_fixture_really_dirties_a_tracked_file(repo):
    _answer(repo, "Acme")
    dirty = _git(repo, "status", "--porcelain").stdout
    assert " M README.md" in dirty, dirty


def test_reset_runs_on_the_wizard_own_changes(repo, capsys):
    """The whole point. This returned EXIT_SCHEMA_ERROR before the fix."""
    _answer(repo, "Acme")
    rc = W.cmd_reset(_Args(repo))
    out = capsys.readouterr()
    assert rc == W.EXIT_OK, f"reset refused its own output: {out.err}"
    assert (repo / "README.md").read_text(encoding="utf-8") == "Welcome to {COMPANY}.\n"


def test_reset_still_refuses_when_an_unrelated_file_is_dirty(repo, capsys):
    """The check must keep the meaning it was written for."""
    _answer(repo, "Acme")
    (repo / "unrelated.md").write_text("edited by hand\n", encoding="utf-8")
    rc = W.cmd_reset(_Args(repo))
    err = capsys.readouterr().err
    assert rc == W.EXIT_SCHEMA_ERROR, "reset ran over an unrelated hand edit"
    assert "unrelated.md" in err
    assert (repo / "unrelated.md").read_text(encoding="utf-8") == "edited by hand\n"
    assert "Acme" in (repo / "README.md").read_text(encoding="utf-8"), (
        "it refused, yet still reverted"
    )


def test_force_still_overrides_everything(repo, capsys):
    _answer(repo, "Acme")
    (repo / "unrelated.md").write_text("edited by hand\n", encoding="utf-8")
    rc = W.cmd_reset(_Args(repo, force=True))
    assert rc == W.EXIT_OK, capsys.readouterr().err
    assert (repo / "README.md").read_text(encoding="utf-8") == "Welcome to {COMPANY}.\n"


def test_a_clean_tree_still_resets(repo, capsys):
    """Nothing answered, nothing dirty: a no-op that must not error."""
    rc = W.cmd_reset(_Args(repo))
    assert rc == W.EXIT_OK, capsys.readouterr().err


def test_the_dirty_check_runs_after_the_touched_set_is_known():
    """Read the order out of the source. The fix IS the ordering; a version
    that computed `touched` afterwards could only re-introduce the bug."""
    src = (ROOT / "scripts" / "apply-wizard-answers.py").read_text(encoding="utf-8")
    body = src[src.index("def cmd_reset("):]
    body = body[:body.index("\ndef ")] if "\ndef " in body else body
    assert body.index("touched = set()") < body.index('"git", "status", "--porcelain"'), (
        "the dirty check still runs before the wizard knows which files are its own"
    )
