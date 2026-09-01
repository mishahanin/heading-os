#!/usr/bin/env python3
"""Two readers that died on the file they were pointed at, instead of reporting it.

Both are the same shape the tree keeps finding, and both were found by mutation
testing shard 17 on 2026-09-01. A reader guards one half of "I could not read
this" and leaves the other half to raise, and the raise lands somewhere that
reads it as a different answer entirely.

**`scripts/sanitize-text.py` crashed on a file that is not valid UTF-8.**
`main()` opened the file with `encoding="utf-8"` and caught `OSError` only.
`UnicodeDecodeError` is a `ValueError` and NOT an `OSError`, so the handler
could not see it. MEASURED that day on `hello \\xe9 world`, the same bytes in
the two unreadable states:

    mode 0o000  -> exit 2, "error: cannot read <path>: [Errno 13] ..."
    not UTF-8   -> UnicodeDecodeError traceback, exit 1

Exit 1 is the code this script's own contract reserves for "hidden characters
WERE found", and exit 2 is the one it reserves for a file it could not read.
So an undecodable file was reported to every machine caller as a DIRTY file
rather than an unscanned one: `scripts/render-doctype.py` printed a codec stack
trace under `[WARN] Hidden-character scan:`, and `scripts/artifact-evaluator.py`
filed the same stack trace as the reason its `hidden_chars` check failed. Both
sentences are false; the file was never scanned at all. This is
`scripts/leak-guard.py`'s second defect, fixed hours earlier the same day, in a
sibling gate that kept it.

**`scripts/artifact-evaluator.py` crashed on an artifact it could not open.**
Every `evaluate_*` read its artifact with a bare `read_text`. The DECODE half
was closed on 2026-09-01 with `errors="replace"`; the OPEN half was not.
MEASURED on a chmod 000 reference file: `--json` printed a `PermissionError`
stack trace and exited 1, so a caller parsing that JSON got nothing to parse.
`tests/test_a_leak_gate_that_counted_what_it_never_opened.py` named this crash
in a docstring on 2026-08-31 and left it; the campaign rule is that a named
defect is fixed, not tabled, so it is fixed and measured here.

Every case below has its passing twin in the same file: a clean file still
exits 0, a dirty file still exits 1, a real artifact is still evaluated. A
guard that refuses everything measures nothing.

Run: .venv/bin/python -m pytest \\
     tests/test_two_readers_that_crashed_on_the_file_they_were_asked_about.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SANITIZE_TEXT = ROOT / "scripts" / "sanitize-text.py"
EVALUATOR = ROOT / "scripts" / "artifact-evaluator.py"

# One byte that is a legal filesystem byte and not legal UTF-8. Written as bytes
# so this test file itself stays pure ASCII.
NOT_UTF8 = b"hello \xe9 world\n"

skip_if_root = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="chmod 000 does not block root, so an unreadable file cannot be staged")


def _run(*args: str) -> subprocess.CompletedProcess:
    # `errors="replace"`, because this file's whole subject is a child being
    # handed a byte that is not UTF-8. Without it the DECODE of the child's
    # output raises `UnicodeDecodeError` out of `subprocess.run` itself, and the
    # test dies with a traceback instead of making its assertion. That error is
    # a `ValueError`, so it is caught by neither `subprocess.SubprocessError`
    # nor `OSError`.
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          errors="replace", cwd=str(ROOT), timeout=90)


# ============================================================
# 1 - sanitize-text: an undecodable file is UNKNOWN, not dirty
# ============================================================

def test_a_file_that_is_not_utf8_exits_two_rather_than_raising(tmp_path):
    """THE case. Exit 1 said "hidden characters found" about bytes nobody read."""
    target = tmp_path / "latin.md"
    target.write_bytes(NOT_UTF8)
    proc = _run(str(SANITIZE_TEXT), "--scan", str(target))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr


def test_the_undecodable_path_is_named(tmp_path):
    """A hook chain renders a traceback as "the hook failed" and names nothing.
    The path is the whole remedy, so it has to be in the message."""
    target = tmp_path / "latin.md"
    target.write_bytes(NOT_UTF8)
    proc = _run(str(SANITIZE_TEXT), "--scan", str(target))
    assert "cannot read" in proc.stderr
    assert str(target) in proc.stderr


def test_the_sanitize_form_refuses_the_same_file_and_writes_nothing(tmp_path):
    """The rewrite path matters more than the scan path: a decode with
    `errors="replace"` here would silently REWRITE the operator's file with
    U+FFFD where its bytes were. Refusing is the only safe answer."""
    target = tmp_path / "latin.md"
    target.write_bytes(NOT_UTF8)
    out = tmp_path / "out.md"
    proc = _run(str(SANITIZE_TEXT), str(target), "-o", str(out))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert not out.exists(), "the gate wrote an output for a file it never read"
    assert target.read_bytes() == NOT_UTF8, "the source was rewritten"


@skip_if_root
def test_the_permission_denied_half_still_exits_two(tmp_path):
    """The half that already worked. Widening a handler must not narrow it."""
    target = tmp_path / "locked.md"
    target.write_text("plain\n", encoding="utf-8")
    target.chmod(0o000)
    try:
        proc = _run(str(SANITIZE_TEXT), "--scan", str(target))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "cannot read" in proc.stderr
    finally:
        target.chmod(0o644)


def test_a_clean_utf8_file_still_exits_zero(tmp_path):
    """Anchor: a guard that answered 2 for everything would pass every test
    above and would fail every publish."""
    good = tmp_path / "good.md"
    good.write_text("plain ascii prose\n", encoding="utf-8")
    assert _run(str(SANITIZE_TEXT), "--scan", str(good)).returncode == 0


def test_a_dirty_file_still_exits_one(tmp_path):
    """The other anchor, and the one the exit codes are actually about: a real
    hidden character must still be reported as a FINDING, not as a read error."""
    dirty = tmp_path / "dirty.md"
    dirty.write_text("zero\u200bwidth\n", encoding="utf-8")
    proc = _run(str(SANITIZE_TEXT), "--scan", str(dirty))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot read" not in proc.stderr


def test_a_non_utf8_file_carrying_a_hidden_character_is_still_not_called_clean(
        tmp_path):
    """The worst input for this gate: bytes that hold BOTH a zero-width space
    and an undecodable byte. It must never resolve 0."""
    mixed = tmp_path / "mixed.md"
    mixed.write_bytes("zero\u200bwidth ".encode("utf-8") + b"\xe9\n")
    assert _run(str(SANITIZE_TEXT), "--scan", str(mixed)).returncode == 2


# ============================================================
# 2 - artifact-evaluator: an unreadable artifact is a failed check
# ============================================================

def _checks(path: Path) -> list[dict]:
    proc = _run(str(EVALUATOR), "--path", str(path), "--json")
    assert proc.stdout.strip(), (
        f"the evaluator produced no JSON at all; stderr was:\n{proc.stderr[-800:]}")
    return json.loads(proc.stdout)["checks"]


@skip_if_root
def test_an_unreadable_reference_is_reported_rather_than_raised(tmp_path):
    """THE case. `--json` printed a stack trace and no JSON."""
    ref = tmp_path / "ref.md"
    ref.write_text("# Title\n\nProse.\n", encoding="utf-8")
    ref.chmod(0o000)
    try:
        names = [c["name"] for c in _checks(ref)]
        assert "file_readable" in names, names
    finally:
        ref.chmod(0o644)


@skip_if_root
def test_the_unreadable_check_fails_and_names_the_path(tmp_path):
    """A failed check with no reason is the silence one layer down."""
    ref = tmp_path / "secret-ish.md"
    ref.write_text("# Title\n\nProse.\n", encoding="utf-8")
    ref.chmod(0o000)
    try:
        hit = [c for c in _checks(ref) if c["name"] == "file_readable"][0]
        assert hit["status"] == "fail"
        assert "secret-ish.md" in hit["detail"]
    finally:
        ref.chmod(0o644)


@skip_if_root
def test_an_unreadable_script_is_reported_too(tmp_path):
    """The fix belongs to every `evaluate_*`, not to the one that was named.
    Five readers carried the identical bare call; a fix in one of them is the
    shape that leaves the other four to be rediscovered."""
    script = tmp_path / "thing.py"
    script.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
    script.chmod(0o000)
    try:
        hit = [c for c in _checks(script) if c["name"] == "file_readable"]
        assert hit and hit[0]["status"] == "fail", _checks(script)
    finally:
        script.chmod(0o644)


def test_a_readable_reference_is_still_evaluated_in_full(tmp_path):
    """Anchor: returning the read-failure check unconditionally would satisfy
    every case above and would evaluate nothing ever again."""
    ref = tmp_path / "ref.md"
    ref.write_text("# Title\n\nProse.\n\nLast Updated: 2026-09-01\n", encoding="utf-8")
    names = [c["name"] for c in _checks(ref)]
    assert "file_readable" not in names, names
    assert "h1_title" in names, names


def test_a_reference_holding_an_undecodable_byte_is_still_evaluated(tmp_path):
    """The evaluator's decode half stays `errors="replace"`, deliberately: it
    produces a REPORT rather than a verdict, so it should say what it found in
    the bytes it could recover. Only the OPEN half refuses."""
    ref = tmp_path / "ref.md"
    ref.write_bytes(b"# Title\n\nProse with \xe9 in it.\n")
    names = [c["name"] for c in _checks(ref)]
    assert "file_readable" not in names, names
    assert "h1_title" in names, names
