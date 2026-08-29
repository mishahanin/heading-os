"""A secret scanner and a style gate, each reporting a pass over unread files.

MEASURED 2026-08-29, by running the two scripts.

THE SECRET SCANNER, three ways.

    a real AWS key inside a directory at mode 0444  -> "No secrets detected.", exit 0
    a named file that does not exist                -> "No secrets detected.", exit 0
    a real AWS key in a 6 MB file, via --scan-dir   -> "No secrets detected.", exit 0

The first is the worst, and it needs no exotic filesystem: a directory that
`os.walk` can LIST but not stat into answers EACCES, `_walk_scannable` dropped
the entry, and `os.path.isfile` in `scan_files` swallows the same EACCES and
answers False — so a path the scanner was REFUSED information about read exactly
like a path that is simply not there. Both had to be fixed; either alone leaves
the miss. `Path.is_file()` is the distinction: False for ENOENT, raises for
EACCES.

The second already had its fix written three files away, in
`sanitize-check.py`, which exits 2 on an explicitly named absent file because
"an EXPLICITLY named file that is absent is an invocation error, not a clean
file". It was never carried here. The silent skip stays for `--stdin` and
`--scan-dir`, where a path that vanished between the listing and the scan is
ordinary, and `push-all.py` uses `--stdin`.

The third is not a finding but named coverage: an oversized file is skipped by
design, and now the skip is printed rather than folded into the green line.

THE STYLE GATE. `ste-check.py --all` and `--skills` run in pre-commit and in CI.
Over a scope that resolves to zero files, `--quiet` printed nothing and exited 0
— a green gate over no pages at all. `--json` was worse: `next(iter({}.values()))`
raised StopIteration and the process exited 1, the code this file's own table
reserves for "findings present", so a crash was indistinguishable from a style
failure to every machine consumer. One guard closes both, because `targets` can
only be empty when the requested scope resolved to nothing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "secret-scanner.py"
STE = ROOT / "scripts" / "ste-check.py"

# Split so the literal never appears whole in this file: the workspace's own
# secret gates read their own test fixtures.
FAKE_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _run(script: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, cwd=str(ROOT),
                          timeout=300, env=env)


# ============================================================
# The scanner: unknown is never clean
# ============================================================

@pytest.fixture()
def leaky_tree(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "leak.txt").write_text(FAKE_KEY + "\n", encoding="utf-8")
    yield tmp_path, sub
    # Restore the execute bit so pytest can remove its own tmp tree. `noqa`
    # because this is a scratch directory the test itself created, not a
    # permission decision about anything shipped.
    os.chmod(sub, 0o755)  # noqa: S103


def test_a_readable_directory_still_finds_the_key(leaky_tree):
    """The control. Without it, every assertion below could pass because the
    scanner stopped detecting anything at all."""
    root, _sub = leaky_tree
    proc = _run(SCANNER, "--scan-dir", str(root))
    assert proc.returncode == 1
    assert "SECRETS DETECTED" in proc.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_a_directory_that_cannot_be_stat_into_is_not_clean(leaky_tree):
    root, sub = leaky_tree
    os.chmod(sub, 0o444)
    proc = _run(SCANNER, "--scan-dir", str(root))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "could not be read" in proc.stderr


def test_a_named_file_that_is_not_there_is_an_invocation_error(tmp_path):
    proc = _run(SCANNER, str(tmp_path / "nope.env"))
    assert proc.returncode == 2
    assert "not a readable file" in proc.stderr


def test_a_named_directory_is_an_invocation_error(tmp_path):
    proc = _run(SCANNER, str(tmp_path))
    assert proc.returncode == 2


def test_a_real_clean_file_is_still_clean(tmp_path):
    """The other direction. A guard that refused every named path would satisfy
    both cases above and break every real invocation."""
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing interesting here\n", encoding="utf-8")
    proc = _run(SCANNER, str(clean))
    assert proc.returncode == 0
    assert "No secrets detected" in proc.stdout


def test_an_oversized_file_is_named_not_hidden(tmp_path):
    """Skipping it stays correct; folding it into the green line does not."""
    big = tmp_path / "huge.log"
    big.write_text("filler line\n" * 500_000 + FAKE_KEY + "\n", encoding="utf-8")
    assert big.stat().st_size > 5 * 1024 * 1024
    proc = _run(SCANNER, "--scan-dir", str(tmp_path))
    assert "were NOT scanned" in proc.stderr
    assert big.name in proc.stderr


def test_a_small_file_is_never_reported_as_oversized(tmp_path):
    (tmp_path / "small.txt").write_text("nothing\n", encoding="utf-8")
    proc = _run(SCANNER, "--scan-dir", str(tmp_path))
    assert "were NOT scanned" not in proc.stderr
    assert proc.returncode == 0


# ============================================================
# The style gate: a pass over nothing is not a pass
# ============================================================

@pytest.fixture()
def empty_workspace(tmp_path):
    """A tree with the workspace markers and no documents at all.

    `WORKSPACE_ROOT` is the documented override and `get_workspace_root` reads
    it first, so this is a supported invocation, not a broken checkout.
    """
    root = tmp_path / "steroot"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("marker\n", encoding="utf-8")
    return dict(os.environ, WORKSPACE_ROOT=str(root))


EMPTY_MODES = [["--all", "--quiet"], ["--all", "--json"], ["--skills", "--json"],
               ["--skills", "--quiet"], ["--all"], ["--skills"]]


@pytest.mark.parametrize("args", EMPTY_MODES, ids=[" ".join(a) for a in EMPTY_MODES])
def test_an_empty_scope_is_a_script_error_not_a_pass(empty_workspace, args):
    proc = _run(STE, *args, env=empty_workspace)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "resolved to 0 files" in proc.stderr


@pytest.mark.parametrize("args", [["--all", "--json"], ["--skills", "--json"]],
                         ids=["all-json", "skills-json"])
def test_an_empty_scope_no_longer_raises(empty_workspace, args):
    proc = _run(STE, *args, env=empty_workspace)
    assert "StopIteration" not in (proc.stdout + proc.stderr)
    assert "Traceback" not in (proc.stdout + proc.stderr)


def test_a_named_file_is_still_checked_without_a_scope(tmp_path):
    """The other direction, twice over: an explicit target must still work, and
    the empty-scope guard must not fire when no scope flag was passed."""
    doc = tmp_path / "page.md"
    doc.write_text("# Title\n\nOpen the door.\n", encoding="utf-8")
    proc = _run(STE, str(doc))
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    assert "resolved to 0 files" not in proc.stderr


def test_a_named_file_that_cannot_be_read_keeps_its_own_message(tmp_path):
    """The guard is scoped to `--all`/`--skills` on purpose, and mutation is
    what showed the scoping matters. A named file that cannot be read also
    leaves `targets` empty, and the honest answer there is the unreadable
    report that names the file, not "the scope resolved to 0 files" about a
    scope the operator never asked for. Both exit 2, so only the message
    distinguishes them."""
    proc = _run(STE, str(tmp_path / "absent.md"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "does not exist" in proc.stderr
    assert "resolved to 0 files" not in proc.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
def test_a_named_file_that_exists_but_cannot_be_read_keeps_its_own_message(tmp_path):
    """Both named-file failures are refused BEFORE the empty-scope guard, and
    each names the file it means.

    This is also the honest answer to a mutation that survives: dropping the
    `args.all or args.skills` condition, leaving a bare `if not targets:`,
    changes no reachable verdict, because an absent named file exits at its
    existence check and an unreadable one exits at its read. The condition is
    kept as a statement of scope, not because a test can see it, and this pair
    of assertions is what pins the two messages that do the naming.
    """
    doc = tmp_path / "locked.md"
    doc.write_text("# Title\n\nOpen the door.\n", encoding="utf-8")
    os.chmod(doc, 0o000)
    try:
        proc = _run(STE, str(doc))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "cannot read" in proc.stderr
        assert doc.name in proc.stderr
        assert "resolved to 0 files" not in proc.stderr
    finally:
        os.chmod(doc, 0o644)


def test_a_real_scope_is_never_called_empty():
    """The live workspace resolves to many pages, so the guard must stay
    silent here. A guard that always fired would pass every case above."""
    proc = _run(STE, "--all", "--quiet")
    assert "resolved to 0 files" not in proc.stderr
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
