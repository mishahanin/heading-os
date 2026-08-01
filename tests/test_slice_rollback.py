"""A slice that fails mid-flight must have a way back that deletes nothing.

Recovery from a failed slice is manual git surgery today, done by hand more than
once in the week this was written, and it gets worse the moment the unattended
loop runs: a build that fails at 03:00 leaves a half-written tree nobody is awake
to read. See `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A10.

Two properties carry the weight:

1. **Nothing is deleted, ever.** Every file the rollback replaces is copied aside
   first, and the command prints where. The operator's standing instruction is
   that nothing goes without his word, and a recovery tool is exactly where that
   gets quietly violated in the name of cleanliness.
2. **Dry by default.** The command prints the plan and changes nothing unless
   `--apply` is passed. At 03:00 the useful output is what WOULD happen.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "slice-rollback.py"


def _run(args, root: Path, log_root: Path = None):
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(root)
    if log_root is not None:
        env["WORKSPACE_LOG_DIR"] = str(log_root)
    return subprocess.run([sys.executable, str(_CLI), *args], capture_output=True,
                          text=True, cwd=str(root), env=env, timeout=120)


def _git(args, cwd: Path):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def slice_repo(tmp_path):
    """A workspace-shaped git repo with one committed file and a freeze over it."""
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("probe workspace\n", encoding="utf-8")
    (root / "scripts").mkdir()
    target = root / "scripts" / "thing.py"
    target.write_text("ORIGINAL = 1\n", encoding="utf-8")

    _git(["init", "-q"], root)
    _git(["config", "user.email", "probe@example.invalid"], root)
    _git(["config", "user.name", "Probe"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "frozen state"], root)
    sha = _git(["rev-parse", "HEAD"], root)

    canopus = root / ".canopus"
    canopus.mkdir()
    (canopus / "freeze.json").write_text(json.dumps({
        "label": "probe-slice",
        "frozen_at": "2026-08-01T00:00:00+00:00",
        "git_sha": sha,
        "root": "deadbeef",
        "anchor": "",
        "files": {"scripts/thing.py": "hash-not-checked-here"},
        "dirs": {},
        "baseline": {},
        "recipe": "canopus-freeze-v5",
        "plugins": {},
    }), encoding="utf-8")
    return root, target


def test_a_clean_tree_reports_nothing_to_roll_back(slice_repo):
    root, _target = slice_repo
    proc = _run([], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing" in proc.stdout.lower()


def test_a_drifted_file_is_reported_without_being_touched(slice_repo):
    root, target = slice_repo
    target.write_text("HALF_WRITTEN = 2\n", encoding="utf-8")
    proc = _run([], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "scripts/thing.py" in proc.stdout
    assert target.read_text(encoding="utf-8") == "HALF_WRITTEN = 2\n", (
        "the dry run modified the tree"
    )


def test_apply_restores_the_frozen_content(slice_repo):
    root, target = slice_repo
    target.write_text("HALF_WRITTEN = 2\n", encoding="utf-8")
    proc = _run(["--apply"], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target.read_text(encoding="utf-8") == "ORIGINAL = 1\n"


def test_apply_keeps_a_copy_of_what_it_replaced(slice_repo, tmp_path):
    """Property 1. A recovery tool is where deletion gets excused as tidiness."""
    root, target = slice_repo
    log_root = tmp_path / "logs"
    target.write_text("HALF_WRITTEN = 2\n", encoding="utf-8")
    proc = _run(["--apply"], root, log_root=log_root)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    saved = list((log_root / "rollback").rglob("thing.py"))
    assert saved, f"nothing was set aside; stdout:\n{proc.stdout}"
    assert saved[0].read_text(encoding="utf-8") == "HALF_WRITTEN = 2\n"
    assert str(saved[0].parent) in proc.stdout or "rollback" in proc.stdout


def test_an_untracked_file_is_named_but_never_moved(slice_repo):
    """We cannot know whether an untracked file belongs to the slice, so we say
    it is there and leave it alone rather than guess."""
    root, _target = slice_repo
    stray = root / "scripts" / "stray.py"
    stray.write_text("NEW = 3\n", encoding="utf-8")
    proc = _run(["--apply"], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert stray.exists(), "an untracked file was removed"
    assert "stray.py" in proc.stdout


def test_it_refuses_when_no_freeze_is_held(tmp_path):
    root = tmp_path / "bare"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    _git(["init", "-q"], root)
    proc = _run([], root)
    assert proc.returncode != 0
    assert "freeze" in proc.stdout.lower() + proc.stderr.lower()


def test_json_output_is_machine_readable(slice_repo):
    root, target = slice_repo
    target.write_text("HALF_WRITTEN = 2\n", encoding="utf-8")
    proc = _run(["--json"], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["label"] == "probe-slice"
    assert "scripts/thing.py" in payload["drifted"]
    assert payload["applied"] is False


def test_a_manifest_that_does_not_validate_is_still_usable(slice_repo):
    """The slice that fails badly enough to need this is the slice whose manifest
    may be what broke. Refusing on a schema mismatch would make the tool useless
    in the only situation it exists for."""
    root, target = slice_repo
    state = root / ".canopus" / "freeze.json"
    manifest = json.loads(state.read_text(encoding="utf-8"))
    manifest["recipe"] = "canopus-freeze-v1-from-the-past"
    state.write_text(json.dumps(manifest), encoding="utf-8")
    target.write_text("HALF_WRITTEN = 2\n", encoding="utf-8")

    proc = _run(["--apply"], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target.read_text(encoding="utf-8") == "ORIGINAL = 1\n"
    assert "does not validate" in proc.stderr


def test_a_manifest_with_no_commit_to_restore_from_refuses(slice_repo):
    root, _target = slice_repo
    state = root / ".canopus" / "freeze.json"
    state.write_text(json.dumps({"label": "x", "recipe": "wrong"}), encoding="utf-8")
    proc = _run([], root)
    assert proc.returncode != 0
    assert "restore from" in (proc.stdout + proc.stderr)
