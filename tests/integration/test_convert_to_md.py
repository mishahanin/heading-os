"""Integration tests for scripts/convert-to-md.py."""
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
SCRIPT = WORKSPACE / "scripts" / "convert-to-md.py"


def run_script(*args, input_bytes=None, expect_exit=None):
    """Helper: invoke convert-to-md.py with given args, return (stdout, stderr, returncode)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        input=input_bytes,
        timeout=30,
    )
    if expect_exit is not None:
        assert result.returncode == expect_exit, (
            f"expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    return stdout, stderr, result.returncode


def test_help_exits_zero():
    stdout, stderr, rc = run_script("--help", expect_exit=0)
    assert "convert" in stdout.lower() or "convert" in stderr.lower()
    assert "input" in stdout.lower() or "input" in stderr.lower()


def test_no_args_errors():
    """argparse exits 2 when required arg is missing."""
    _, stderr, rc = run_script(expect_exit=2)
    assert "input" in stderr.lower() or "required" in stderr.lower()


FIXTURE_DOCX = WORKSPACE / "tests" / "integration" / "fixtures" / "sample.docx"


def test_convert_docx_to_stdout():
    stdout, stderr, rc = run_script(str(FIXTURE_DOCX), expect_exit=0)
    assert "Test Heading" in stdout
    assert "First paragraph" in stdout
    assert "Second paragraph" in stdout


def test_convert_docx_to_file(tmp_path):
    output_file = tmp_path / "out.md"
    stdout, stderr, rc = run_script(
        str(FIXTURE_DOCX), "-o", str(output_file), expect_exit=0
    )
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "Test Heading" in content
    assert "First paragraph" in content
    # Status message goes to stderr
    assert "Wrote" in stderr or "wrote" in stderr


FIXTURE_CORRUPT = WORKSPACE / "tests" / "integration" / "fixtures" / "corrupt.docx"
FIXTURE_UNSUPPORTED = WORKSPACE / "tests" / "integration" / "fixtures" / "unsupported.bin"


def test_missing_input_file():
    _, stderr, rc = run_script("/nonexistent/path/to/file.docx", expect_exit=1)
    assert "File not found" in stderr


def test_corrupt_input_raises_clean_error():
    """Corrupted DOCX should surface as a markitdown exception, not a Python traceback."""
    _, stderr, rc = run_script(str(FIXTURE_CORRUPT), expect_exit=1)
    # Must NOT contain a Python traceback marker
    assert "Traceback" not in stderr
    # Must contain one of our error labels
    assert any(
        label in stderr
        for label in [
            "Unsupported format",
            "Conversion failed",
            "Missing dependency",
            "Markitdown error",
        ]
    )


def test_unsupported_extension_raises_clean_error():
    """A file with an extension markitdown does not handle surfaces cleanly."""
    _, stderr, rc = run_script(str(FIXTURE_UNSUPPORTED), expect_exit=1)
    assert "Traceback" not in stderr
    assert any(
        label in stderr
        for label in [
            "Unsupported format",
            "Conversion failed",
            "Markitdown error",
        ]
    )


def test_output_is_hidden_char_clean():
    """Default conversion output must pass sanitize-text.py --scan."""
    stdout, _, rc = run_script(str(FIXTURE_DOCX), expect_exit=0)
    # Verify by piping through sanitize-text.py --scan
    sanitizer = WORKSPACE / "scripts" / "sanitize-text.py"
    scan = subprocess.run(
        [sys.executable, str(sanitizer), "--scan", "-"],
        input=stdout.encode("utf-8"),
        capture_output=True,
        timeout=10,
    )
    # --scan returns 0 when clean, non-zero when chars found
    assert scan.returncode == 0, f"output had hidden chars: {scan.stdout.decode()}"


def test_no_sanitize_skips_sanitization(tmp_path):
    """--no-sanitize must bypass the sanitizer entirely.

    Proves the bypass works by running in a sandbox where sanitize-text.py is missing -
    if --no-sanitize were silently ignored, the missing-sanitizer guard would fire and exit 1.
    """
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts" / "utils").mkdir(parents=True)
    import shutil
    shutil.copy(SCRIPT, sandbox / "scripts" / "convert-to-md.py")
    for util in ["__init__.py", "colors.py", "workspace.py", "paths.py"]:
        shutil.copy(WORKSPACE / "scripts" / "utils" / util, sandbox / "scripts" / "utils" / util)

    sandbox_script = sandbox / "scripts" / "convert-to-md.py"
    output_file = tmp_path / "out.md"
    result = subprocess.run(
        [sys.executable, str(sandbox_script), str(FIXTURE_DOCX), "-o", str(output_file), "--no-sanitize"],
        capture_output=True,
        timeout=30,
        cwd=str(sandbox),
    )
    # If --no-sanitize were silently ignored, the sandbox would trigger the missing-sanitizer
    # guard and exit 1. Exit 0 + output file existing proves the flag bypassed the guard.
    assert result.returncode == 0, f"stderr: {result.stderr.decode()}"
    assert output_file.exists()


def test_sanitizer_missing_on_disk(tmp_path):
    """If sanitize-text.py is missing AND --no-sanitize is not set, exit 1 with clear error.

    Simulates by pointing the script at a workspace where sanitize-text.py does not exist.
    Implementation: copy script to a sandbox dir without the sanitizer.
    """
    # Set up a sandbox with the script but no sanitize-text.py
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts" / "utils").mkdir(parents=True)
    # Copy the script and its utils dependencies
    import shutil
    shutil.copy(SCRIPT, sandbox / "scripts" / "convert-to-md.py")
    for util in ["__init__.py", "colors.py", "workspace.py", "paths.py"]:
        shutil.copy(WORKSPACE / "scripts" / "utils" / util, sandbox / "scripts" / "utils" / util)

    sandbox_script = sandbox / "scripts" / "convert-to-md.py"
    result = subprocess.run(
        [sys.executable, str(sandbox_script), str(FIXTURE_DOCX)],
        capture_output=True,
        timeout=30,
        cwd=str(sandbox),
    )
    assert result.returncode == 1
    stderr = result.stderr.decode("utf-8")
    assert "Sanitizer missing" in stderr or "sanitizer missing" in stderr.lower()
