import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "leak-guard.py"

_spec = importlib.util.spec_from_file_location("leak_guard", GUARD)
leak_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(leak_guard)


def _run(args, **kw):
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        capture_output=True, text=True, cwd=ROOT, **kw
    )


def test_check_paths_flags_hardcoded_data_path(tmp_path):
    bad = tmp_path / "bad_script.py"
    bad.write_text('p = root / "crm/contacts" / name\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(bad)])
    assert r.returncode == 1
    assert "crm/contacts" in r.stdout


def test_check_paths_allows_helper_usage(tmp_path):
    good = tmp_path / "good_script.py"
    good.write_text("p = get_crm_contacts_dir() / name\n", encoding="utf-8")
    r = _run(["check-paths", "--files", str(good)])
    assert r.returncode == 0


def test_check_paths_skips_the_seam_file():
    # workspace.py legitimately contains the literal inside the helper definition
    seam = ROOT / "scripts" / "utils" / "workspace.py"
    r = _run(["check-paths", "--files", str(seam)])
    assert r.returncode == 0


def test_check_paths_ignores_url_substring(tmp_path):
    # Anchored regex (M4): a URL merely CONTAINING outputs/ must not flag.
    ok = tmp_path / "url_script.py"
    ok.write_text('u = "https://example.com/outputs/x"\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(ok)])
    assert r.returncode == 0


def test_check_paths_respects_inline_suppression(tmp_path):
    # A legitimate relative-path usage annotated with the suppression marker
    # must not be flagged.
    ok = tmp_path / "relkeys.py"
    ok.write_text('PATHS = ["crm/contacts/"]  # leak-guard: ok (relative prefix)\n', encoding="utf-8")
    r = _run(["check-paths", "--files", str(ok)])
    assert r.returncode == 0


def test_check_paths_skips_test_files():
    # Test files legitimately embed data-path literals as fixtures (this very
    # file does). The lint must skip anything under tests/.
    r = _run(["check-paths", "--files", "tests/test_routing_map.py", "tests/test_leak_guard.py"])
    assert r.returncode == 0


def test_check_paths_skips_archived_scripts():
    # Archived scripts under scripts/archive/ are inert dead code retained for
    # history; they route to 'engine' but must not be linted (Plan 2 Task 6).
    r = _run(["check-paths", "--files", "scripts/archive/2026-04-24-export-sync.py"])
    assert r.returncode == 0


def test_check_staged_blocks_private_via_autodetect_no_marker():
    # Regression for the 2026-06-22 leak: with NO env marker, the guard must still
    # block a private-routing file because this clone is the split-topology engine
    # (get_data_root() != workspace root). The hand-set marker is no longer the sole
    # trigger -- relying on it is exactly why the guard sat inert while specs leaked.
    env = {k: v for k, v in os.environ.items() if k != "HEADING_OS_ENGINE_REPO"}
    r = _run(["check-staged", "--files", "crm/contacts/x.md"], env=env)
    assert r.returncode == 1
    assert "crm/contacts/x.md" in r.stdout


def test_check_staged_blocks_private_file_in_engine():
    r = _run(["check-staged", "--files", "crm/contacts/x.md"],
             env={**os.environ, "HEADING_OS_ENGINE_REPO": "1"})
    assert r.returncode == 1
    assert "crm/contacts/x.md" in r.stdout


def test_check_staged_allows_engine_file_in_engine():
    r = _run(["check-staged", "--files", "scripts/foo.py"],
             env={**os.environ, "HEADING_OS_ENGINE_REPO": "1"})
    assert r.returncode == 0


def test_in_engine_repo_inert_on_single_repo(monkeypatch, tmp_path):
    # Pre-cutover single repo (data_root == workspace_root): the guard must be inert
    # so legitimately-tracked data files are not flagged. Marker absent.
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)
    same = tmp_path / "single-repo"
    monkeypatch.setattr(leak_guard, "get_data_root", lambda: same)
    monkeypatch.setattr(leak_guard, "get_workspace_root", lambda: same)
    assert leak_guard._in_engine_repo() is False
    assert leak_guard.check_staged(["crm/contacts/x.md"]) == 0


def test_in_engine_repo_active_when_split(monkeypatch, tmp_path):
    # Split topology (data in a sibling): auto-active even without the marker.
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)
    monkeypatch.setattr(leak_guard, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(leak_guard, "get_workspace_root", lambda: tmp_path / "engine")
    assert leak_guard._in_engine_repo() is True


def test_in_engine_repo_fails_closed_on_seam_error(monkeypatch):
    # If the data-root seam cannot resolve, assume engine and enforce (fail-closed).
    monkeypatch.delenv("HEADING_OS_ENGINE_REPO", raising=False)

    def _boom():
        raise RuntimeError("seam unreadable")

    monkeypatch.setattr(leak_guard, "get_data_root", _boom)
    assert leak_guard._in_engine_repo() is True
