"""html-to-pdf.py printed its install hints on stdout and lost them.

The module docstring's contract is that every failure goes to stderr and stdout
carries the result and nothing else. The two `[HINT]` prints on the ImportError
path carried no `file=sys.stderr`, so a missing `playwright` (or a missing
transitive dep such as `greenlet`) put

    [HINT] If 'playwright' itself is missing: pip install playwright ...

on stdout while the process exited 1.

Measured before the fix, by running the script in a subprocess with
`sys.modules["playwright.sync_api"] = None`:
  rc=1, stderr = the one `[ERROR]` line, stdout = BOTH `[HINT]` lines.

The harm is concrete rather than stylistic. `scripts/render-doctype.py` is the
only in-repo caller; it runs this with `capture_output=True`, passes the
destination in as argv[2], and on a non-zero exit prints `result.stderr` and
nothing else. The two lines naming the package to install were therefore
captured and discarded, and the operator saw an import error with no remedy.

Playwright is never imported and no browser is ever launched here: the import is
blocked at the seam, which is exactly the failure under test.
"""

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "html-to-pdf.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("html_to_pdf_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_exists() -> None:
    assert SCRIPT.is_file(), f"nothing to test at {SCRIPT}"


def test_import_failure_writes_nothing_to_stdout(tmp_path, monkeypatch, capsys) -> None:
    """The whole ImportError path -- error line and both hints -- is stderr."""
    module = _load_module()
    page = tmp_path / "skyfall.html"
    page.write_text("<html><body>q branch</body></html>", encoding="utf-8")

    # Blocking the submodule in sys.modules makes `from playwright.sync_api
    # import sync_playwright` raise ImportError without a browser existing.
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setattr(sys, "argv", ["html-to-pdf.py", str(page)])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "", (
        f"stdout is reserved for the result; got {captured.out!r}"
    )
    assert "[ERROR] Cannot import playwright.sync_api" in captured.err
    assert captured.err.count("[HINT]") == 2, (
        "both install hints must survive on stderr, where the only in-repo "
        f"caller reads them; got {captured.err!r}"
    )
    assert "pip install playwright" in captured.err


def test_usage_and_missing_input_also_stay_off_stdout(tmp_path, monkeypatch, capsys) -> None:
    """The other two exit-1 paths, so the contract is asserted whole."""
    module = _load_module()

    monkeypatch.setattr(sys, "argv", ["html-to-pdf.py"])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Usage:" in captured.err

    monkeypatch.setattr(sys, "argv", ["html-to-pdf.py", str(tmp_path / "absent.html")])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[ERROR] Input file not found" in captured.err


def test_import_failure_stdout_is_clean_in_a_real_subprocess(tmp_path) -> None:
    """The end-to-end shape the caller sees: separate process, captured pipes.

    This is the measurement that produced the finding, kept as the regression.
    """
    page = tmp_path / "skyfall.html"
    page.write_text("<html><body>q branch</body></html>", encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(
        textwrap.dedent(f"""
        import runpy, sys
        sys.modules["playwright.sync_api"] = None
        sys.argv = ["html-to-pdf.py", {str(page)!r}]
        runpy.run_path({str(SCRIPT)!r}, run_name="__main__")
        """).lstrip(),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )

    assert proc.returncode == 1
    assert proc.stdout == "", f"caller reads stdout as the result; got {proc.stdout!r}"
    assert proc.stderr.count("[HINT]") == 2
