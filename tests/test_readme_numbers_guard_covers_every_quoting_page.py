"""Every public page quoting a countable fact must be under the numbers guard.

`scripts/dev/check-readme-numbers.py` derives the security-test count by
collecting `tests/security/` and compares it against the pages that quote it.
It watched README.md and docs/index.html. ROADMAP.md quotes the same count and
was watched by nothing, so it drifted: 554 against a real 563, sitting one file
away from a README that was right.

The lesson generalises past this one number. A guard that covers some of the
pages carrying a fact does not keep the fact true; it keeps ONE page true and
makes the others look verified by association. So this test asserts the
membership rule rather than the current list: any tracked top-level Markdown
page that quotes "N security tests" must be in FRONT_DOORS.

The guard was also loosened at the same time. It used to require BOTH figures
on every listed page, which would have forced ROADMAP.md to invent an
"enforcement layers" sentence purely to satisfy it. It now checks whichever
figures a page carries, and refuses a listed page carrying neither, because
that page reads as covered while nothing about it is checked.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "dev" / "check-readme-numbers.py"
SEC_RE = re.compile(r"\d+\s+security tests", re.IGNORECASE)


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("check_readme_numbers", GUARD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_readme_numbers"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tracked_top_level_markdown() -> list[Path]:
    # `-z`: git C-quotes a non-ASCII path, and a quoted name is not a file this
    # guard can open, so the page would drop out of the scan without a word.
    out = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / rel for rel in out.split("\0")
            if rel and "/" not in rel]


# --- membership -----------------------------------------------------------------

def test_every_top_level_page_quoting_the_count_is_watched(guard):
    watched = {p.resolve() for p in guard.FRONT_DOORS}
    quoting = [p for p in _tracked_top_level_markdown()
               if SEC_RE.search(p.read_text(encoding="utf-8"))]
    assert quoting, "no top-level page quotes a security-test count any more"
    unwatched = sorted(p.name for p in quoting if p.resolve() not in watched)
    assert not unwatched, (
        f"these pages quote a security-test count and nothing checks them: "
        f"{unwatched}. Add them to FRONT_DOORS in {GUARD.name}."
    )


def test_roadmap_is_watched(guard):
    """The page the gap was found on. Named so a future edit to FRONT_DOORS
    that drops it fails loudly rather than quietly reopening the hole."""
    assert (ROOT / "ROADMAP.md").resolve() in {p.resolve() for p in guard.FRONT_DOORS}


def test_every_watched_page_exists(guard):
    missing = [str(p) for p in guard.FRONT_DOORS if not p.exists()]
    assert not missing, f"FRONT_DOORS names pages that do not exist: {missing}"


# --- a page may carry one figure, but not zero -----------------------------------

def test_a_missing_figure_is_tolerated_not_fatal(guard, tmp_path):
    """ROADMAP quotes the test count and never mentions layers. That must pass,
    or the guard forces prose to exist for the guard's benefit."""
    page = tmp_path / "partial.md"
    page.write_text("563 security tests live here.", encoding="utf-8")
    assert guard._extract(guard._LAYER_RE, page.read_text(encoding="utf-8"),
                          page, "enforcement layers") is None


def test_a_watched_page_carrying_no_figure_is_reported(guard, tmp_path, monkeypatch,
                                                       capsys):
    """The opposite error: a page listed as watched that is checked for nothing."""
    blank = tmp_path / "blank.md"
    blank.write_text("no figures here at all\n", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "FRONT_DOORS", [blank])
    monkeypatch.setattr(guard, "derive_security_test_count", lambda: 563)
    monkeypatch.setattr(sys, "argv", ["check-readme-numbers.py", "--quiet"])

    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "neither figure" in err


# --- the guard still catches a real drift ----------------------------------------

def test_the_guard_reports_a_mismatched_count(guard, tmp_path, monkeypatch, capsys):
    page = tmp_path / "stale.md"
    page.write_text("554 security tests live here.", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "FRONT_DOORS", [page])
    monkeypatch.setattr(guard, "derive_security_test_count", lambda: 563)
    monkeypatch.setattr(sys, "argv", ["check-readme-numbers.py", "--quiet"])

    assert guard.main() == 1
    assert "554" in capsys.readouterr().err


def test_the_guard_passes_when_the_count_matches(guard, tmp_path, monkeypatch):
    page = tmp_path / "fresh.md"
    page.write_text("563 security tests live here.", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "FRONT_DOORS", [page])
    monkeypatch.setattr(guard, "derive_security_test_count", lambda: 563)
    monkeypatch.setattr(sys, "argv", ["check-readme-numbers.py", "--quiet"])

    assert guard.main() == 0


# --- the pre-commit hook must fire for the pages the guard checks ----------------

def test_the_precommit_hook_triggers_on_every_watched_page(guard):
    """A guard nothing runs is a guard that does not run. The hook's `files:`
    pattern must reach each watched page, or an edit to that page commits
    unchecked."""
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    block = config.split("id: readme-numbers", 1)
    assert len(block) == 2, "the readme-numbers hook is gone from .pre-commit-config.yaml"
    files_line = next(line for line in block[1].splitlines() if "files:" in line)
    pattern = re.compile(files_line.split("files:", 1)[1].strip().strip("'\""))

    for page in guard.FRONT_DOORS:
        rel = page.relative_to(ROOT).as_posix()
        assert pattern.search(rel), (
            f"the readme-numbers hook does not fire on {rel}, so editing it "
            f"commits without the guard. Hook pattern: {pattern.pattern}"
        )
