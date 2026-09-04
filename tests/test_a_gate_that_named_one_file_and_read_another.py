#!/usr/bin/env python3
"""Shard 57: a leak gate that took a file's NAME from git and its BYTES from disk.

`sanitize-check.py --staged` asked the index which files are staged and then
opened those paths in the WORKING TREE. When the two differ it scanned bytes
that will never be committed and never saw the bytes that will, and it was
wrong in both directions. MEASURED 2026-08-29 on a real repository:

    staged `ceo@gmail.com`, worktree cleaned, never re-staged
      -> "[PASS] 1 file(s) scanned. No critical terms found.", exit 0,
         and the commit ships the address.
    staged clean, worktree holds the term in a scratch edit
      -> "[FAIL] ... contain critical terms", exit 1, blocking a commit that
         would not have contained it.

The same shape sat in the standalone pre-commit hook `install-hooks.py` writes:
`git diff --cached --name-only | secret-scanner.py --stdin`. MEASURED the same
day by installing that hook in a scratch repository -- an AWS secret was staged,
the worktree copy cleaned without re-staging, the hook printed "No secrets
detected", and the secret landed in the commit. That hook is superseded and the
installer refuses whenever `.pre-commit-config.yaml` exists, so it is not
reachable in THIS repository; it is shipped code in a public repository all the
same, and it now refuses a partially-staged file rather than scanning the wrong
version of it.

WHAT IS NOT BROKEN, measured rather than assumed: the pre-commit FRAMEWORK
stashes unstaged changes before running its hooks, so every `pass_filenames:
true` gate in `.pre-commit-config.yaml` -- `secret-scanner-31c`,
`content-guard-31c`, the leak guards -- already sees the staged bytes. The
safety of some twenty gates rests on that behaviour and nothing recorded it, so
`test_the_precommit_framework_stashes_unstaged_edits` records it now.

Run: .venv/bin/python -m pytest tests/test_a_gate_that_named_one_file_and_read_another.py -q
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Every child this file spawns is `git` in a scratch tree, and `git` has never
# read HEADING_OS_DATA. Pinning it away from the operator's live overlay costs
# these tests nothing and removes them from the reachability ratchet in
# tests/conftest.py. See the `scratch_data_root` fixture for the measurement.
pytestmark = pytest.mark.usefixtures("scratch_data_root")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SANITIZE_CHECK = ROOT / "scripts" / "sanitize-check.py"
LEAK_TERM = "@gmail.com"
# A line in the shape `scripts/utils/secret_patterns.py` looks for
# (`AKIA[0-9A-Z]{16}`), planted so the hook under test has something to find.
#
# ASSEMBLED FROM PARTS on purpose, and not silenced with an allowlist pragma.
# Written as one literal it is an AWS-key-shaped string committed to a PUBLIC
# repository, and the `detect-secrets` gate flagged it three ways -- correctly,
# since the gate reads shapes and cannot know a value is synthetic. Clearing a
# real gate to plant a fake secret is the wrong trade when the alternative costs
# one line.
#
# `test_the_planted_line_is_a_line_the_scanner_actually_detects` asserts the
# assembly still matches, so a pattern change cannot quietly turn the hook tests
# below into tests over a file with nothing in it.
_AKIA = "AKIA" + "Q" * 16
PLANTED_LINE = f'aws_access_key_id = "{_AKIA}"'


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load("s57_sanitize_check", "scripts/sanitize-check.py")
ih = _load("s57_install_hooks", "scripts/install-hooks.py")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit. Hooks disabled unless a test wants
    them: `core.hooksPath=/dev/null` on the seed commit only."""
    work = tmp_path / "repo"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "probe@example.invalid")
    _git(work, "config", "user.name", "Probe")
    _git(work, "config", "commit.gpgsign", "false")
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "seed.txt")
    _git(work, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "seed")
    return work


def _run_gate(repo: Path, *argv: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(repo)
    env.pop("HEADING_OS_DATA", None)
    proc = subprocess.run([sys.executable, str(SANITIZE_CHECK), *argv],
                          cwd=str(repo), env=env, capture_output=True,
                          text=True, check=False, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


# ============================================================
# 1 - the two directions of the defect
# ============================================================


def test_a_term_staged_and_cleaned_in_the_worktree_still_blocks(repo):
    """THE fail-open. The gate cleared a commit that ships the term."""
    doc = repo / "post.md"
    doc.write_text(f"Write to me at ceo{LEAK_TERM}\n", encoding="utf-8")
    _git(repo, "add", "post.md")
    doc.write_text("Write to me at ceo@example.invalid\n", encoding="utf-8")

    code, out = _run_gate(repo, "--staged")
    assert code == 1, out
    assert LEAK_TERM in out
    # And the premise of the test: the index really does still hold it.
    assert LEAK_TERM in _git(repo, "show", ":post.md").stdout


def test_a_term_only_in_the_worktree_does_not_block(repo):
    """The other direction. A scratch edit that will not be committed must not
    refuse the publish -- a gate that cries wolf gets bypassed."""
    doc = repo / "post.md"
    doc.write_text("Clean staged line.\n", encoding="utf-8")
    _git(repo, "add", "post.md")
    doc.write_text(f"scratch note ceo{LEAK_TERM}\n", encoding="utf-8")

    code, out = _run_gate(repo, "--staged")
    assert code == 0, out
    assert "[PASS]" in out


def test_a_file_staged_then_deleted_from_the_worktree_is_still_scanned(repo):
    """The index is the source of truth, so a deleted working copy changes
    nothing. Under the old reader `scan_file` returned None for the missing
    path and the file landed in the gray "not scanned" note."""
    doc = repo / "post.md"
    doc.write_text(f"ceo{LEAK_TERM}\n", encoding="utf-8")
    _git(repo, "add", "post.md")
    doc.unlink()

    code, out = _run_gate(repo, "--staged")
    assert code == 1, out
    assert LEAK_TERM in out


def test_explicit_file_mode_still_reads_the_working_tree(repo):
    """Unchanged on purpose. `/publish-corporate` names the rendered files it is
    about to publish; those are worktree artifacts and may not be staged at all.
    """
    doc = repo / "post.md"
    doc.write_text(f"ceo{LEAK_TERM}\n", encoding="utf-8")
    code, out = _run_gate(repo, "post.md")
    assert code == 1, out
    assert LEAK_TERM in out


# ============================================================
# 2 - the decoding half of the same fix
# ============================================================


def test_a_cyrillic_staged_path_is_scanned_from_the_index(repo):
    """`text=True` with no encoding decodes git's raw `-z` path bytes with the
    caller's locale. On a cp1252 console the path became mojibake, no such file
    existed, and the gate exited 0 with a gray note -- the same fail-open the
    `-z` fix claims to have closed, one layer down."""
    doc = repo / "документ.md"
    doc.write_text(f"ceo{LEAK_TERM}\n", encoding="utf-8")
    _git(repo, "add", "документ.md")
    doc.write_text("cleaned\n", encoding="utf-8")

    code, out = _run_gate(repo, "--staged")
    assert code == 1, out
    assert LEAK_TERM in out


def test_the_staged_listing_survives_a_nul_separated_pair(monkeypatch):
    """Two paths, NUL-separated, the second non-ASCII. A `splitlines()` reader
    returns one path; a locale-decoding reader mangles the second."""
    class Fake:
        returncode = 0
        stdout = "one.md\0документ.md\0".encode("utf-8")
        stderr = b""

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: Fake())
    assert sc.staged_files() == [Path("one.md"), Path("документ.md")]


# ============================================================
# 3 - coverage claims about the staged path
# ============================================================


def test_an_unreadable_staged_blob_is_exit_2_never_a_pass(repo, monkeypatch):
    """A name that came out of `git diff --cached` and has no blob a moment
    later means the index moved under the gate. UNKNOWN coverage, never clean.
    """
    doc = repo / "post.md"
    doc.write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "post.md")

    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(repo)
    env.pop("HEADING_OS_DATA", None)
    # Make `git cat-file` fail for every path while `git diff` keeps working:
    # a shim earlier on PATH that rejects exactly that subcommand.
    shim_dir = repo / ".shim"
    shim_dir.mkdir()
    real_git = shutil.which("git")
    (shim_dir / "git").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "cat-file" ]; then echo "fatal: shimmed" >&2; exit 128; fi\n'
        f'exec {real_git} "$@"\n',
        encoding="utf-8")
    (shim_dir / "git").chmod(0o755)
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"

    proc = subprocess.run([sys.executable, str(SANITIZE_CHECK), "--staged"],
                          cwd=str(repo), env=env, capture_output=True,
                          text=True, check=False, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not scanned" in (proc.stdout + proc.stderr).lower()


def test_a_binary_staged_blob_is_reported_as_binary_not_as_a_race(repo):
    """`.claude/rules/scope-claims.md`: say what the method established. In
    --staged mode the bytes come from the index, so "gone between the diff and
    the scan" names a race that cannot happen on that path."""
    blob = repo / "logo.bin"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    _git(repo, "add", "logo.bin")

    code, out = _run_gate(repo, "--staged")
    assert code == 0, out
    assert "not scanned (binary)" in out
    assert "gone between the diff" not in out


def test_the_pass_line_counts_only_blobs_it_actually_read(repo):
    """A skipped binary must not be counted in "N file(s) scanned"."""
    (repo / "a.md").write_text("clean prose\n", encoding="utf-8")
    (repo / "b.bin").write_bytes(b"\x00\x01\x02binary")
    _git(repo, "add", "a.md", "b.bin")

    code, out = _run_gate(repo, "--staged")
    assert code == 0, out
    assert "1 file(s) scanned" in out
    assert "1 file(s) not scanned" in out


# ============================================================
# 4 - the blob-level primitives
# ============================================================


def test_is_text_blob_and_is_text_file_make_the_same_call(tmp_path):
    """Two callers, one rule. A second copy of the text/binary decision is how
    the staged path and the explicit path would start disagreeing."""
    cases = {
        "note.md": b"# prose\n",
        "logo.bin": b"\x00\x01\x02",
        "no-extension": b"plain text, no suffix\n",
        "Makefile": b"all:\n\techo hi\n",
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert sc.is_text_blob(name, data) == sc.is_text_file(path), name


def test_scan_blob_returns_none_for_binary_and_findings_for_text():
    terms = {LEAK_TERM}
    assert sc.scan_blob("logo.bin", b"\x00binary", terms, set()) is None
    findings = sc.scan_blob("post.md", f"ceo{LEAK_TERM}\n".encode(), terms, set())
    assert findings and findings[0][0] == LEAK_TERM


def test_scan_blob_survives_bytes_that_are_not_utf8():
    """A staged blob is arbitrary bytes. Decoding must not raise: an exception
    here would take the whole gate down on one odd file."""
    data = b"prose \xff\xfe more prose ceo" + LEAK_TERM.encode()
    findings = sc.scan_blob("post.md", data, {LEAK_TERM}, set())
    assert findings and findings[0][0] == LEAK_TERM


def test_staged_blob_returns_the_index_bytes_not_the_worktree(repo, monkeypatch):
    doc = repo / "post.md"
    doc.write_text("INDEX\n", encoding="utf-8")
    _git(repo, "add", "post.md")
    doc.write_text("WORKTREE\n", encoding="utf-8")
    monkeypatch.setattr(sc, "get_workspace_root", lambda: repo)
    assert sc.staged_blob(Path("post.md")) == b"INDEX\n"


# ============================================================
# 5 - the standalone hook, and the framework that superseded it
# ============================================================


def test_the_planted_line_is_a_line_the_scanner_actually_detects(tmp_path):
    """The premise of the two hook tests below.

    They assert that the hook refuses BEFORE reaching the scanner, so they would
    both pass over a file the scanner does not care about at all -- a green pair
    measuring nothing. Assert the plant lands.
    """
    ss = _load("s57_secret_scanner", "scripts/secret-scanner.py")
    planted = tmp_path / "creds.py"
    planted.write_text(PLANTED_LINE + "\n", encoding="utf-8")
    findings = ss.scan_file(str(planted))
    assert findings, f"the planted line no longer matches any pattern: {PLANTED_LINE!r}"


def test_the_standalone_hook_refuses_a_partially_staged_file(repo):
    """MEASURED before the fix: the hook printed "No secrets detected" and the
    staged AWS key was committed. It has no stash, so it cannot see the bytes
    git is about to write; refusing is the only honest answer."""
    shutil.copytree(ROOT / "scripts", repo / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(ih.PRE_COMMIT_HOOK, encoding="utf-8")
    hook.chmod(0o755)

    creds = repo / "creds.py"
    creds.write_text(PLANTED_LINE + "\n", encoding="utf-8")
    _git(repo, "add", "creds.py")
    creds.write_text("# cleaned in the worktree only\n", encoding="utf-8")

    result = _git(repo, "commit", "-m", "probe")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "COMMIT BLOCKED" in (result.stdout + result.stderr)
    # And nothing was committed.
    assert _git(repo, "show", "HEAD:creds.py").returncode != 0


def test_the_standalone_hook_still_passes_a_fully_staged_clean_commit(repo):
    """The refusal must not become "no commit ever works"."""
    shutil.copytree(ROOT / "scripts", repo / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(ih.PRE_COMMIT_HOOK, encoding="utf-8")
    hook.chmod(0o755)

    (repo / "notes.md").write_text("nothing secret here\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    result = _git(repo, "commit", "-m", "clean")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "show", "HEAD:notes.md").stdout == "nothing secret here\n"


def test_the_precommit_framework_stashes_unstaged_edits(tmp_path):
    """The premise the whole `.pre-commit-config.yaml` gate set rests on.

    Some twenty hooks there use `pass_filenames: true` and read the paths off
    disk. They are correct ONLY because the framework stashes unstaged changes
    first, so the working tree equals the index while they run. Nothing in this
    repository recorded that, and it is exactly the kind of assumption that is
    true until an upgrade or a config flag makes it false. Measure it.
    """
    if shutil.which("pre-commit") is None:
        pytest.skip("the pre-commit binary is not on PATH in this environment")

    work = tmp_path / "fw"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "probe@example.invalid")
    _git(work, "config", "user.name", "Probe")
    _git(work, "config", "commit.gpgsign", "false")
    sink = tmp_path / "seen.txt"
    (work / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: record-bytes\n"
        "        name: record what the hook reads\n"
        "        language: system\n"
        "        entry: python3 -c \"import sys;open(r'" + str(sink) +
        "','a').write(''.join(open(f).read() for f in sys.argv[1:]))\"\n"
        "        pass_filenames: true\n"
        "        files: 'post[.]md'\n",
        encoding="utf-8")
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "seed")
    installed = subprocess.run(["pre-commit", "install"], cwd=str(work),
                               capture_output=True, text=True, check=False,
                               timeout=180)
    assert installed.returncode == 0, installed.stdout + installed.stderr

    doc = work / "post.md"
    doc.write_text("STAGED-BYTES\n", encoding="utf-8")
    _git(work, "add", "post.md")
    doc.write_text("WORKTREE-BYTES\n", encoding="utf-8")
    committed = _git(work, "commit", "-m", "probe")
    assert committed.returncode == 0, committed.stdout + committed.stderr

    seen = sink.read_text(encoding="utf-8") if sink.exists() else ""
    assert seen, "the hook never ran, so this test measured nothing"
    assert "STAGED-BYTES" in seen, (
        "the pre-commit framework no longer stashes unstaged edits, so every "
        "pass_filenames hook in .pre-commit-config.yaml now reads the working "
        "tree instead of the bytes being committed")
    assert "WORKTREE-BYTES" not in seen


def test_the_standalone_installer_still_refuses_beside_the_framework(tmp_path):
    """The reason the hook above is not reachable HERE. If this ever stops being
    true, the two mechanisms fight again -- the May 2026 incident where every
    hook was silently bypassed."""
    work = tmp_path / "both"
    (work / ".git" / "hooks").mkdir(parents=True)
    (work / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "install-hooks.py")],
        cwd=str(work), env={**os.environ, "WORKSPACE_ROOT": str(work)},
        capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode == 1
    assert "Refusing to install" in proc.stdout + proc.stderr
