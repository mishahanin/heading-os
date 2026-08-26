#!/usr/bin/env python3
"""Shard scripts-10-p1: the ways content left this machine unguarded.

Five distinct holes in the paths that publish, promote, and provision:

  - `publish-service` was a SECOND publication path out of the workspace with
    no secret scan at all, a bare `git push` that can exit 0 without advancing
    the ref, and manifest `include` entries joined onto the destination with no
    containment check -- so `../../x` copied (and rmtree'd) outside the clone.
  - `promote-knowledge` recorded the operator's fully resolved private-overlay
    path into frontmatter it then pushed to every exec, demoted a failed push to
    a warning, and printed "Promotion complete" anyway.
  - `rmtree_force` exists because two scripts called `shutil.rmtree(onexc=...)`,
    a keyword added in Python 3.12, on a workspace pinned to >=3.11 and running
    3.11.15. Both call sites raised TypeError.
  - `pull-service-state` read its .env overrides at import time, BEFORE the
    `load_env()` in main() that puts them in the environment.
  - `provision-exec` marked the CRM seed step complete on a failed clone, so the
    idempotent re-run skipped the only step that could have repaired it.

Run: .venv/bin/python -m pytest tests/test_a_publish_path_with_no_wall.py -q
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.rmtree import rmtree_force  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pubsvc = _load("publish_service_p10a", "scripts/publish-service.py")
pullsvc = _load("pull_service_state_p10a", "scripts/pull-service-state.py")
pk = _load("promote_knowledge_p10a", "scripts/promote-knowledge.py")


# ============================================================
# 1 - a manifest entry cannot write outside the downstream repo
# ============================================================
@pytest.mark.parametrize("rel", ["../escape", "../../etc/x", "a/../../../out"])
def test_a_traversing_include_is_refused(tmp_path, rel):
    dest = tmp_path / "downstream"
    dest.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        pubsvc._contained(dest, rel)


def test_an_absolute_include_is_refused(tmp_path):
    dest = tmp_path / "downstream"
    dest.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        pubsvc._contained(dest, "/etc/passwd")


def test_an_ordinary_include_resolves_under_the_destination(tmp_path):
    dest = tmp_path / "downstream"
    dest.mkdir()
    got = pubsvc._contained(dest, "scripts/utils")
    assert got == (dest / "scripts" / "utils").resolve()


def test_a_dotdot_that_stays_inside_is_allowed(tmp_path):
    """Containment, not a ban on `..`: `a/../b` never leaves the destination."""
    dest = tmp_path / "downstream"
    dest.mkdir()
    assert pubsvc._contained(dest, "a/../b") == (dest / "b").resolve()


# ============================================================
# 2 - the second publication path now has a secret gate
# ============================================================
def test_a_secret_in_the_downstream_clone_blocks_the_publish(tmp_path, capsys):
    repo = tmp_path / "downstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    # Synthesised, never a literal: a real-shaped token written into this file
    # would be caught by the repo's own commit hook before the test could run.
    # The AWS docs example key is allowlisted by the scanner, so it proves
    # nothing here.
    import string
    token = "ghp" + "_" + (string.ascii_lowercase + string.digits * 2)[:36]
    (repo / "creds.env").write_text(token + "\n", encoding="utf-8")
    assert pubsvc.secret_scan(repo) is False
    assert "REFUSING TO PUBLISH" in capsys.readouterr().out


def test_a_clean_downstream_clone_passes_the_gate(tmp_path):
    repo = tmp_path / "downstream"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("# nothing secret here\n", encoding="utf-8")
    assert pubsvc.secret_scan(repo) is True


# ============================================================
# 3 - rmtree works on the Python this workspace actually runs
# ============================================================
def test_rmtree_force_removes_a_tree_on_this_interpreter(tmp_path):
    """`onexc=` is a 3.12 keyword and this workspace runs 3.11: both original
    call sites raised TypeError before ever deleting anything."""
    tree = tmp_path / "tree" / "deep"
    tree.mkdir(parents=True)
    (tree / "f.txt").write_text("x", encoding="utf-8")
    rmtree_force(tmp_path / "tree")
    assert not (tmp_path / "tree").exists()


def test_rmtree_force_clears_a_read_only_file(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    victim = tree / "ro.txt"
    victim.write_text("x", encoding="utf-8")
    victim.chmod(0o444)
    rmtree_force(tree)
    assert not tree.exists()


def test_rmtree_force_tolerates_an_absent_path(tmp_path):
    rmtree_force(tmp_path / "never-existed")


def test_no_script_passes_the_312_only_keyword():
    """The whole point: a 3.12-only keyword must not come back on a 3.11 pin."""
    scripts = sorted((ROOT / "scripts").rglob("*.py"))
    # A floor under the corpus. An emptiness claim over zero files is green and
    # says nothing. 371 scripts on 2026-08-26.
    assert len(scripts) >= 250, f"the scan collapsed to {len(scripts)} scripts"
    offenders = [p for p in scripts
                 if "onexc=" in p.read_text(encoding="utf-8", errors="replace")
                 and p.name != "rmtree.py"]
    assert offenders == [], offenders


# ============================================================
# 4 - .env overrides are read after load_env(), not before it
# ============================================================
def test_the_env_override_beats_the_config_file(monkeypatch):
    monkeypatch.setenv("SERVICE_VM_ENGINE_ROOT", "/srv/from-env")
    assert pullsvc.vm_roots()["engine"] == "/srv/from-env"


def test_the_config_value_stands_when_no_override_is_set(monkeypatch):
    monkeypatch.delenv("SERVICE_VM_ENGINE_ROOT", raising=False)
    assert pullsvc.vm_roots()["engine"] == pullsvc._SVC.get("vm_engine_root", "")


def test_an_empty_vm_root_is_named_not_turned_into_a_root_path():
    """An empty base used to yield "/rel" -- scp then pulled from the VM's root
    filesystem instead of failing."""
    with pytest.raises(ValueError, match="empty"):
        pullsvc._vm_path(["mirror", "engine", "some/dir"], {"engine": ""})


def test_a_malformed_state_dirs_entry_is_named(monkeypatch):
    with pytest.raises(ValueError, match="malformed"):
        pullsvc._vm_path(["only-one"], {"engine": "/srv"})


def test_a_well_formed_entry_joins_cleanly():
    name, path = pullsvc._vm_path(["mirror", "data", "/state/x"], {"data": "/srv/data/"})
    assert (name, path) == ("mirror", "/srv/data/state/x")


# ============================================================
# 5 - promoted provenance carries no absolute private path
# ============================================================
def test_provenance_is_relative_to_the_data_root(monkeypatch, tmp_path):
    data_root = tmp_path / ".heading-os-data"
    (data_root / "knowledge" / "research").mkdir(parents=True)
    note = data_root / "knowledge" / "research" / "n.md"
    note.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pk, "get_data_root", lambda: data_root)
    got = pk._provenance(note.resolve())
    assert got == "knowledge/research/n.md"
    assert str(tmp_path) not in got


def test_provenance_outside_the_data_root_degrades_to_the_bare_name(
        monkeypatch, tmp_path):
    monkeypatch.setattr(pk, "get_data_root", lambda: tmp_path / "elsewhere")
    stray = tmp_path / "somewhere" / "note.md"
    stray.parent.mkdir()
    stray.write_text("x", encoding="utf-8")
    got = pk._provenance(stray.resolve())
    assert got == "note.md"
    assert os.sep not in got


# ============================================================
# 6 - one knowledge-type list, consumed by both scripts
# ============================================================
def test_both_scripts_read_the_same_knowledge_types():
    from scripts.utils.knowledge import KNOWLEDGE_TYPES
    src = (ROOT / "scripts" / "provision-exec.py").read_text(encoding="utf-8")
    assert "KNOWLEDGE_SUBDIRS = list(KNOWLEDGE_TYPES)" in src
    assert list(KNOWLEDGE_TYPES) == pk.VALID_TYPES
    assert len(KNOWLEDGE_TYPES) == len(set(KNOWLEDGE_TYPES))


# ============================================================
# 7 - a failed CRM seed leaves the step retryable
# ============================================================
def test_a_failed_seed_clone_does_not_mark_the_step_done():
    """`mark_step_done` on the failure path made the idempotent re-run skip the
    only step that could have seeded the repo."""
    src = (ROOT / "scripts" / "provision-exec.py").read_text(encoding="utf-8")
    block = src.split("Could not clone for seed", 1)[1].split("\n\n", 1)[0]
    assert "mark_step_done" not in block, block


# ============================================================
# 8 - the exit-code contract matches the code
# ============================================================
def test_publish_corporate_documents_the_gitattributes_refusal():
    src = (ROOT / "scripts" / "publish-corporate.py").read_text(encoding="utf-8")
    doc = src.split('"""', 2)[1]
    assert "return 8" in src
    assert "8  corporate .gitattributes" in doc


# ============================================================
# 9 - --help is readable without the legacy override
# ============================================================
def test_provision_exec_help_works_without_the_legacy_override():
    env = dict(os.environ)
    env.pop("HEADING_OS_ALLOW_LEGACY_PROVISION", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "provision-exec.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout


def test_provision_exec_still_refuses_a_real_run():
    """The --help carve-out must not open the refusal it sits in front of."""
    env = dict(os.environ)
    env.pop("HEADING_OS_ALLOW_LEGACY_PROVISION", None)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "provision-exec.py"),
         "--name", "X", "--title", "Y", "--email", "z@example.com", "--role", "r"],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=60)
    assert proc.returncode == 2
    assert "REFUSED" in proc.stderr


# ============================================================
# 10 - a build bump keeps keys it does not manage
# ============================================================
def test_bump_build_preserves_an_unmanaged_key():
    """This test is TEXTUAL, and it now says so in its own shape.

    It used to open with a tmp BUILD.json and
    `monkeypatch.setattr(pc, "CORPORATE_BUILD", build_file, raising=False)`,
    which read as behavioural setup. It was not: `publish-corporate.py` has no
    name `CORPORATE_BUILD`, so the patch bound a new attribute nobody reads and
    the temp file was never opened by anything. `raising=False` is what hid it.
    The module load was dead with them.
    """
    src = (ROOT / "scripts" / "publish-corporate.py").read_text(encoding="utf-8")
    # Behavioural assertion is not reachable without a corporate repo, so the
    # contract is pinned at the seam that produced the loss: the payload must
    # start from the file as found, not from a fresh literal.
    body = src.split("def bump_build", 1)[1]
    assert "payload = dict(cur)" in body
    assert 'payload = {\n        "version"' not in body


def test_the_retry_handler_clears_the_bit_before_retrying(tmp_path):
    """Exercised directly, because on Linux a read-only FILE inside a writable
    directory is still unlinkable -- the bit only blocks deletion on Windows, so
    a whole-tree test cannot tell a working handler from a missing chmod.
    """
    from scripts.utils.rmtree import _clear_readonly
    victim = tmp_path / "ro.txt"
    victim.write_text("x", encoding="utf-8")
    victim.chmod(0o444)
    called = []
    _clear_readonly(lambda p: called.append(p), str(victim), None)
    assert called == [str(victim)]
    assert victim.stat().st_mode & 0o200, "write bit was not restored before the retry"
