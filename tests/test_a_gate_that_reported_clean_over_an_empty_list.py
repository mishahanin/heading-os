#!/usr/bin/env python3
"""The publish-service secret gate called a mirror clean because git had failed.

Measured 2026-08-29 against `scripts/publish-service.py` as it stood at commit
579fbaf, by calling both functions directly on scratch trees under `/tmp`:

    scenario                                              | before | after
    ------------------------------------------------------+--------+-------
    secret_scan, dest not a git repo, ghp_ token present  | True   | False
    secret_scan, ls-files forced to exit 128, same token  | True   | False
    secret_scan, real git repo, one benign file           | True   | True
    secret_scan, real git repo, genuinely zero files      | True   | True
    publish, dest not a git repo (status exits 128)       | exit 0 | exit 1

`git ls-files` writes nothing to stdout when it fails. The listing exit code
was never read, so a failed call parsed to an empty list, hit the `if not
files: return True` shortcut, and reported the mirror clean WITHOUT starting
the scanner even once. A 36-character `ghp_`-shaped token sat in the tree while
the gate said clean. Same shape one function down: `git status --porcelain`
exits 128 with empty stdout, `publish()` read that as "nothing changed" and
returned 0 over a repo it could not read, after `copy_includes` had already
written into it.

Not dead code behind main()'s check. `main()` guards only with
`(dest / ".git").exists()`, and a `.git` GITFILE whose gitdir has been removed
satisfies that check while every git call under the directory exits 128
(measured: `gitdir: /tmp/gitfile-probe/gone` -> `.git` exists True, ls-files
128, status 128). Index lock, permissions, and a half-deleted clone reach the
same place.

Both directions are asserted here. The gate must refuse when git fails, and it
must still pass a tree that is genuinely clean, including a repo whose file
list is empty because the repo really is empty.

Run: .venv/bin/python -m pytest
     tests/test_a_gate_that_reported_clean_over_an_empty_list.py -q
"""

import importlib.util
import string
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pubsvc = _load("publish_service_emptylist", "scripts/publish-service.py")

# Synthesised at import, never a literal: a real-shaped token spelled out in
# this file would be stopped by the repo's own commit gate before the test
# could run. 36 characters after the prefix, which is the shape the scanner
# matches on.
TOKEN = "ghp" + "_" + (string.ascii_lowercase + string.digits * 2)[:36]


def _git_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _dangling_gitfile_repo(path):
    """A directory main() accepts and git refuses.

    `.git` is a gitfile pointing at an administrative directory that does not
    exist. This is the reachable form of the failure, so the tests below use it
    rather than a bare non-repo directory.
    """
    path.mkdir(parents=True)
    (path / ".git").write_text(f"gitdir: {path.parent / 'gone'}\n", encoding="utf-8")
    return path


# ============================================================
# 1 - the reachability the fix rests on
# ============================================================
def test_mains_only_guard_accepts_a_directory_git_cannot_read(tmp_path):
    """If this ever stops holding, the gate below is defending a dead path."""
    dest = _dangling_gitfile_repo(tmp_path / "mirror")
    assert (dest / ".git").exists(), "main()'s guard would have refused this"
    for sub in (["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                ["status", "--porcelain"]):
        proc = subprocess.run(["git", "-C", str(dest), *sub],
                              capture_output=True, text=True)
        assert proc.returncode != 0, f"git {sub[0]} unexpectedly succeeded"
        assert proc.stdout == "", (
            f"git {sub[0]} wrote to stdout on failure; the empty-output "
            "premise of this defect no longer holds")


# ============================================================
# 2 - a failed listing refuses, and says why
# ============================================================
def test_a_failed_listing_refuses_the_publish(tmp_path, capsys):
    dest = _dangling_gitfile_repo(tmp_path / "mirror")
    (dest / "creds.env").write_text(TOKEN + "\n", encoding="utf-8")
    assert pubsvc.secret_scan(dest) is False
    out = capsys.readouterr().out
    assert "REFUSING TO PUBLISH" in out
    assert "128" in out, f"the git exit code is not in the message: {out!r}"


def test_a_failed_listing_refuses_even_when_the_tree_is_innocent(tmp_path):
    """The verdict is about what the gate could SEE, not about what is there.

    A tree with no secret in it still gets refused, because a gate that cannot
    read the tree has not cleared it.
    """
    dest = _dangling_gitfile_repo(tmp_path / "mirror")
    (dest / "README.md").write_text("# nothing secret here\n", encoding="utf-8")
    assert pubsvc.secret_scan(dest) is False


def test_the_scanner_is_never_reached_when_the_listing_fails(tmp_path, monkeypatch):
    """Records the calls rather than asserting on the verdict alone.

    The original bug was not a wrong boolean, it was a scan that never ran. A
    recording stub is the only way to assert that directly.
    """
    dest = _git_repo(tmp_path / "mirror")
    (dest / "creds.env").write_text(TOKEN + "\n", encoding="utf-8")
    real_run = subprocess.run
    calls = []

    def recording_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if isinstance(cmd, list) and "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: forced\n")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(pubsvc.subprocess, "run", recording_run)
    assert pubsvc.secret_scan(dest) is False
    assert len(calls) == 1, f"something ran after the failed listing: {calls}"
    assert str(pubsvc.SCANNER) not in " ".join(calls[0])


def test_a_listing_that_fails_with_partial_output_still_refuses(tmp_path, monkeypatch):
    """Non-zero wins over a non-empty stdout.

    Checking emptiness instead of the exit code would pass this: git printed
    two names before it died, so `files` is truthy and the scanner would run
    over a list that is missing everything after the failure point.
    """
    dest = _git_repo(tmp_path / "mirror")
    real_run = subprocess.run

    def truncated_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "a.txt\0b.txt\0", "fatal: forced\n")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(pubsvc.subprocess, "run", truncated_run)
    assert pubsvc.secret_scan(dest) is False


# ============================================================
# 3 - the other direction: a clean tree still passes
# ============================================================
def test_a_successful_but_empty_listing_still_passes(tmp_path):
    """An empty list IS a clean verdict when git produced it successfully.

    A fix that refused on emptiness rather than on the exit code would fail
    here, and would refuse every first publish into a fresh clone.
    """
    dest = _git_repo(tmp_path / "mirror")
    assert pubsvc.secret_scan(dest) is True


def test_a_clean_downstream_clone_still_passes_and_is_actually_scanned(
        tmp_path, monkeypatch):
    dest = _git_repo(tmp_path / "mirror")
    (dest / "README.md").write_text("# nothing secret here\n", encoding="utf-8")
    real_run = subprocess.run
    calls = []

    def recording_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(pubsvc.subprocess, "run", recording_run)
    assert pubsvc.secret_scan(dest) is True
    assert any(str(pubsvc.SCANNER) in " ".join(c) for c in calls), calls


def test_a_secret_in_the_downstream_clone_still_blocks(tmp_path, capsys):
    """Regression guard on the wall this shard is extending, not replacing."""
    dest = _git_repo(tmp_path / "mirror")
    (dest / "creds.env").write_text(TOKEN + "\n", encoding="utf-8")
    assert pubsvc.secret_scan(dest) is False
    assert "REFUSING TO PUBLISH" in capsys.readouterr().out


# ============================================================
# 4 - publish() does not report success over an unreadable repo
# ============================================================
def test_a_failed_status_fails_the_publish(tmp_path, capsys):
    dest = _dangling_gitfile_repo(tmp_path / "mirror")
    (dest / "creds.env").write_text(TOKEN + "\n", encoding="utf-8")
    assert pubsvc.publish(dest, push=False) == 1
    out = capsys.readouterr().out
    assert "No changes to publish" not in out
    assert "128" in out, f"the git exit code is not in the message: {out!r}"


def test_a_genuinely_unchanged_repo_still_reports_nothing_to_publish(tmp_path, capsys):
    """The over-refusal direction for publish(): exit 0 on a real quiet repo."""
    dest = _git_repo(tmp_path / "mirror")
    assert pubsvc.publish(dest, push=False) == 0
    assert "No changes to publish" in capsys.readouterr().out


def test_a_real_changeset_still_commits(tmp_path, monkeypatch):
    """End to end on the happy path, so neither guard blocks a working publish."""
    for var, value in (("GIT_AUTHOR_NAME", "James Bond"),
                       ("GIT_AUTHOR_EMAIL", "james.bond@example.com"),
                       ("GIT_COMMITTER_NAME", "James Bond"),
                       ("GIT_COMMITTER_EMAIL", "james.bond@example.com")):
        monkeypatch.setenv(var, value)
    dest = _git_repo(tmp_path / "mirror")
    (dest / "README.md").write_text("# nothing secret here\n", encoding="utf-8")
    assert pubsvc.publish(dest, push=False) == 0
    log = subprocess.run(["git", "-C", str(dest), "log", "--oneline"],
                         capture_output=True, text=True, check=True)
    assert "publish build 1" in log.stdout, log.stdout


def test_a_secret_still_stops_publish_before_it_commits(tmp_path):
    """The exit-2 path, and nothing reaches the object store."""
    dest = _git_repo(tmp_path / "mirror")
    (dest / "creds.env").write_text(TOKEN + "\n", encoding="utf-8")
    assert pubsvc.publish(dest, push=False) == 2
    log = subprocess.run(["git", "-C", str(dest), "log", "--oneline"],
                         capture_output=True, text=True)
    assert log.returncode != 0 or log.stdout.strip() == "", log.stdout
