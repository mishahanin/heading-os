"""A scanner that printed the green line over a file it never opened.

`--scan-dir` skips any file over WALK_MAX_BYTES. The skip was printed to
stderr, and then `print_results({})` printed the unqualified green
"No secrets detected." on stdout and `main` exited 0.

MEASURED 2026-08-30, by running the CLI:

    6 MB file holding a live AWS key, via --scan-dir  -> "No secrets
                                                          detected.", exit 0
    the SAME file named explicitly                    -> exit 1, key found

The module's own exit-code contract calls a file that exists and was not read
"UNKNOWN coverage, never a pass", and `_walk_scannable`'s docstring says in as
many words that a file skipped for size "is coverage this scanner did not have,
not a file it found clean". Both were true of stderr and neither was true of
the two channels a caller actually reads: the verdict line and the exit code.

Only `--scan-dir` can lose coverage this way. `--stdin` and the explicit-file
mode scan exactly the paths they are handed, so the gates that drive them (the
pre-commit hook, push-all.py, publish-service.py) see no verdict change; the
`--stdin` cases below pin that.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "secret-scanner.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _scanner_module():
    """Import the hyphenated script by path (not a legal module name)."""
    spec = importlib.util.spec_from_file_location("heading_secret_scanner", SCANNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Split so this file is not itself a secret-scanner hit; it is the documented
# AWS example key, which `scripts/utils/secret_patterns.py` matches.
PLANTED_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the scanner CLI with its denial log pinned inside tmp_path."""
    env = dict(os.environ, WORKSPACE_LOG_DIR=str(tmp_path / "logs"))
    proc = subprocess.run([sys.executable, str(SCANNER), *args],
                          capture_output=True, text=True, env=env)
    proc.stdout = ANSI.sub("", proc.stdout)
    proc.stderr = ANSI.sub("", proc.stderr)
    return proc


def _oversized(path: Path, *, with_key: bool) -> Path:
    """A file above WALK_MAX_BYTES, optionally carrying a real secret."""
    with path.open("w", encoding="utf-8") as fh:
        fh.write("filler line\n" * 460_000)   # ~5.5 MB, over the 5 MB ceiling
        if with_key:
            fh.write(f"aws_secret_access_key = {PLANTED_KEY}\n")
    assert path.stat().st_size > 5 * 1024 * 1024, "fixture is not oversized"
    return path


# --- the positive control: the pattern really does fire on this content ---

def test_the_planted_key_is_detected_when_the_file_is_named_directly(tmp_path):
    """Without this, every exit-2 assertion below could pass over a clean file."""
    small = tmp_path / "creds.env"
    small.write_text(f"aws_secret_access_key = {PLANTED_KEY}\n", encoding="utf-8")
    proc = _run(tmp_path, str(small))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SECRETS DETECTED" in proc.stdout


# --- the defect ---

def test_scan_dir_does_not_exit_clean_over_an_unscanned_oversized_file(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    _oversized(tree / "dump.log", with_key=True)

    proc = _run(tmp_path, "--scan-dir", str(tree))

    assert proc.returncode == 2, (
        f"exit {proc.returncode}: a 5 MB+ file holding {PLANTED_KEY[:4]}... was "
        f"never opened, so the run has UNKNOWN coverage and must not pass.\n"
        f"{proc.stdout}\n{proc.stderr}")
    assert "dump.log" in proc.stderr


def test_the_verdict_line_says_coverage_was_incomplete(tmp_path):
    """The green line is a claim about the whole scope, not about what was read."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ok.txt").write_text("nothing interesting\n", encoding="utf-8")
    _oversized(tree / "dump.log", with_key=False)

    proc = _run(tmp_path, "--scan-dir", str(tree))

    assert proc.returncode == 2
    verdict = [ln for ln in proc.stdout.splitlines() if "No secrets detected" in ln]
    assert verdict, f"no verdict line at all in:\n{proc.stdout}"
    assert verdict[0].strip() != "No secrets detected.", (
        "the unqualified green line still stands over a file that was skipped")
    assert "incomplete" in verdict[0].lower()


def test_an_unreadable_file_also_qualifies_the_verdict_line(tmp_path):
    """Same class, already exit 2 before this change; the LINE was still green."""
    tree = tmp_path / "tree"
    tree.mkdir()
    locked = tree / "locked"
    locked.mkdir()
    (locked / "creds.txt").write_text(f"aws_secret = {PLANTED_KEY}\n", encoding="utf-8")
    os.chmod(locked, 0o444)
    try:
        proc = _run(tmp_path, "--scan-dir", str(tree))
    finally:
        # Restoring the mode the fixture directory was created with, so
        # tmp_path teardown can remove it. Nothing outside tmp_path is touched.
        os.chmod(locked, 0o755)  # noqa: S103

    if proc.returncode == 1:
        pytest.skip("this filesystem let the walk stat into a 0444 directory, "
                    "so the key was read rather than refused")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    verdict = [ln for ln in proc.stdout.splitlines() if "No secrets detected" in ln]
    assert verdict and verdict[0].strip() != "No secrets detected."


# --- the other direction: the change must not refuse an ordinary sweep ---

def test_a_fully_scanned_clean_tree_still_exits_zero_with_the_green_line(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("hello\n", encoding="utf-8")
    (tree / "b.md").write_text("# notes\n", encoding="utf-8")

    proc = _run(tmp_path, "--scan-dir", str(tree))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No secrets detected." in proc.stdout
    assert "incomplete" not in proc.stdout.lower()


# --- the floor: a verdict is only worth the corpus it was read over ---

# One of every ordinary shape a repository tree holds, including a name with no
# extension at all. None of them is in SKIP_EXTENSIONS, so every one of them is
# a file the walk is obliged to hand to `scan_files`.
_ORDINARY = ("notes.md", "a.txt", "mod.py", "conf.json", "creds.env",
             "ci.yaml", "Makefile", "deep/nested/inner.md")


def test_the_walk_yields_every_ordinary_file_it_was_pointed_at(tmp_path):
    """The count the green line rests on, asserted rather than assumed.

    `test_a_fully_scanned_clean_tree_still_exits_zero_with_the_green_line`
    asserts exit 0 and the unqualified green sentence, and a walk that silently
    drops a whole class of file produces exactly that: no findings, no
    `oversized`, no `unreadable`, so `covered` stays True. MEASURED 2026-09-01
    by inserting `if name.endswith(".md"): continue` at the top of
    `_walk_scannable`'s inner loop -- all eight cases in this file stayed green
    while a live AWS key in a `.md` under `--scan-dir` went unreported and the
    run exited 0.

    Set equality, not a count alone: a count is satisfied by yielding the wrong
    eight paths. The floor assertion below is what fails when the corpus
    shrinks, and it is checkable that it CAN fail, because the .md mutation
    takes it from 8 to 6.
    """
    module = _scanner_module()
    tree = tmp_path / "tree"
    for name in _ORDINARY:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("nothing interesting\n", encoding="utf-8")

    oversized: list = []
    yielded = {p.relative_to(tree).as_posix() for p in
               module._walk_scannable(tree, oversized)}

    assert len(_ORDINARY) >= 8, "the corpus must be wide or this proves nothing"
    assert yielded == set(_ORDINARY), (
        f"the walk lost coverage: missing {set(_ORDINARY) - yielded}, "
        f"unexpected {yielded - set(_ORDINARY)}")
    assert oversized == []


def test_the_walk_still_prunes_the_directories_it_is_meant_to(tmp_path):
    """The other direction, or the floor above is satisfied by yielding all.

    Without this, "yield every path under root" passes the coverage floor and
    the pruning that keeps `.git` packfiles out of the pattern set is free to
    disappear.
    """
    module = _scanner_module()
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    assert module.WALK_SKIP_DIRS, "an empty skip set would make this vacuous"
    for skipped in sorted(module.WALK_SKIP_DIRS):
        (tree / skipped).mkdir()
        (tree / skipped / "buried.txt").write_text("x\n", encoding="utf-8")

    yielded = {p.relative_to(tree).as_posix() for p in module._walk_scannable(tree)}

    assert yielded == {"src/keep.py"}, f"pruning was lost: {sorted(yielded)}"


def test_a_key_in_a_markdown_file_is_reported_by_scan_dir(tmp_path):
    """The floor above, restated as the verdict a caller actually reads.

    The extension is deliberately one no other case here uses, so a walk that
    drops it is invisible to every other assertion in this file.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "runbook.md").write_text(f"aws_secret_access_key = {PLANTED_KEY}\n",
                                     encoding="utf-8")

    proc = _run(tmp_path, "--scan-dir", str(tree))

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "runbook.md" in proc.stdout


def test_a_detected_secret_still_outranks_an_oversized_skip(tmp_path):
    """The docstring's precedence: 1 beats 2. A leak is never a tool malfunction."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "creds.env").write_text(f"aws_secret_access_key = {PLANTED_KEY}\n",
                                    encoding="utf-8")
    _oversized(tree / "dump.log", with_key=False)

    proc = _run(tmp_path, "--scan-dir", str(tree))

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SECRETS DETECTED" in proc.stdout


# --- the gate-driving modes are untouched ---

def test_stdin_mode_over_clean_files_still_exits_zero(tmp_path):
    """publish-service.py and push-all.py drive --stdin; their verdict must not move."""
    a = tmp_path / "a.txt"
    a.write_text("hello\n", encoding="utf-8")
    big = _oversized(tmp_path / "dump.log", with_key=False)

    env = dict(os.environ, WORKSPACE_LOG_DIR=str(tmp_path / "logs"))
    proc = subprocess.run([sys.executable, str(SCANNER), "--stdin"],
                          input=f"{a}\n{big}\n", capture_output=True, text=True,
                          env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No secrets detected." in ANSI.sub("", proc.stdout)


def test_stdin_mode_still_refuses_a_secret(tmp_path):
    bad = tmp_path / "creds.env"
    bad.write_text(f"aws_secret_access_key = {PLANTED_KEY}\n", encoding="utf-8")
    env = dict(os.environ, WORKSPACE_LOG_DIR=str(tmp_path / "logs"))
    proc = subprocess.run([sys.executable, str(SCANNER), "--stdin"],
                          input=f"{bad}\n", capture_output=True, text=True, env=env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
