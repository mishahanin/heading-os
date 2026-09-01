#!/usr/bin/env python3
"""The corporate line-ending gate had a handler that could not catch its read.

`publish-corporate.corporate_gitattributes_ok` decides exit code 8: the
corporate repo's `.gitattributes` must carry `* text=auto`, or exec clones
accumulate CRLF churn and `git pull --ff-only` silently stalls (build 84). It
reads the file with `encoding="utf-8"` under `except OSError`.

`UnicodeDecodeError` is a `ValueError`. It is not an `OSError`, so that handler
caught nothing on the one input class most likely to reach a hand-maintained
pattern file: a stray byte outside UTF-8.

MEASURED 2026-09-01 on a scratch corporate root whose `.gitattributes` held
`b"* text=auto\\n\\xff\\xfe not utf8\\n"`:

    corporate_gitattributes_ok()  -> UnicodeDecodeError, uncaught

The exception left `corporate_gitattributes_ok`, so `mode_copy` never reached
its `return 8` and `verify_corporate_repo` never printed its warning. The
operator got a traceback naming a codec instead of a sentence naming the file.

WHY THE OLD TESTS COULD NOT SEE IT. Nothing exercised the handler at all.
MEASURED by mutation over the fifteen test files that touch this publisher:
flipping `except OSError: return False` to `except OSError: return True` --
which turns an absent or unreadable corporate `.gitattributes` into a clean
verdict and reopens build 84 -- left the run at 397 passed, 1 skipped, exactly
the unmutated count. The refusal path had no case on it in either direction.

Both directions are pinned here: the gate must clear a file that really carries
the pattern, and refuse absence, a directory, a non-UTF-8 body, and a body that
simply lacks the line.

Nothing here touches the real corporate repo. `CORPORATE_ROOT` is rebound to a
tmp_path for every case.

Run: .venv/bin/python -m pytest \\
    tests/test_a_gitattributes_gate_that_crashed_on_the_bytes_it_read.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "publish-corporate.py"


@pytest.fixture(scope="module")
def publisher():
    spec = importlib.util.spec_from_file_location(
        "publish_corporate_gitattributes_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_corporate_gitattributes_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def corporate(publisher, tmp_path, monkeypatch):
    """A stand-in corporate root. The real one is never read or written."""
    root = tmp_path / "heading-os-corporate"
    root.mkdir()
    monkeypatch.setattr(publisher, "CORPORATE_ROOT", root)
    return root


# ============================================================
# The read that raised past its own handler
# ============================================================

def test_a_non_utf8_gitattributes_is_refused_rather_than_raised(
        publisher, corporate):
    """The finding. This raised UnicodeDecodeError out of the gate."""
    (corporate / ".gitattributes").write_bytes(
        b"* text=auto\n\xff\xfe not utf8\n")

    assert publisher.corporate_gitattributes_ok() is False


def test_the_copy_mode_reports_exit_eight_rather_than_a_traceback(
        publisher, corporate, monkeypatch, capsys):
    """The consequence as the operator meets it, through the real entry point.

    `mode_copy` documents 8 for a corporate `.gitattributes` without the
    pattern. An exception escaping the gate skipped that return entirely.
    """
    (corporate / ".gitattributes").write_bytes(
        b"* text=auto\n\xff\xfe not utf8\n")
    monkeypatch.setattr(publisher, "verify_admin_identity", lambda: None)
    monkeypatch.setattr(publisher, "verify_corporate_repo", lambda: None)

    assert publisher.mode_copy() == 8
    assert "Traceback" not in capsys.readouterr().err


def test_a_gitattributes_that_is_a_directory_is_refused(publisher, corporate):
    """The OSError half, so widening the tuple did not cost the case it had."""
    (corporate / ".gitattributes").mkdir()

    assert publisher.corporate_gitattributes_ok() is False


def test_an_absent_gitattributes_is_refused(publisher, corporate):
    """Absent is the build-84 state itself, and it must not read as clean."""
    assert not (corporate / ".gitattributes").exists()

    assert publisher.corporate_gitattributes_ok() is False


# ============================================================
# The other direction: a guard that refuses everything is not a guard
# ============================================================

def test_a_gitattributes_carrying_the_pattern_is_cleared(publisher, corporate):
    (corporate / ".gitattributes").write_text(
        "* text=auto\n*.png binary\n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is True


def test_the_pattern_is_found_with_surrounding_whitespace(publisher, corporate):
    (corporate / ".gitattributes").write_text(
        "\n   * text=auto   \n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is True


def test_the_pattern_is_found_with_a_trailing_attribute(publisher, corporate):
    """`* text=auto eol=lf` still normalises; the gate accepts the prefix form."""
    (corporate / ".gitattributes").write_text(
        "* text=auto eol=lf\n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is True


def test_a_gitattributes_without_the_pattern_is_refused(publisher, corporate):
    (corporate / ".gitattributes").write_text(
        "*.png binary\n*.pdf -text\n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is False


def test_a_lookalike_pattern_is_not_accepted(publisher, corporate):
    """`*.md text=auto` normalises one extension, not the tree."""
    (corporate / ".gitattributes").write_text("*.md text=auto\n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is False


def test_a_prefix_of_the_pattern_is_not_accepted(publisher, corporate):
    """`* text=autonomous` starts with the literal and means something else.

    The gate matches `* text=auto` exactly or followed by a SPACE, never by an
    arbitrary continuation, and this is the case that tells the two apart.
    """
    (corporate / ".gitattributes").write_text("* text=automatic\n", encoding="utf-8")

    assert publisher.corporate_gitattributes_ok() is False
