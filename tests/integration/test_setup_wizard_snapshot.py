"""Snapshot test: rendered rich-doc output must match committed golden files.

Catches accidental drift in wizard-templates/*.tmpl.
Set env UPDATE_GOLDEN=1 to regenerate goldens after intentional template changes.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent


def test_rendered_tree_matches_golden(tmp_path):
    src = REPO / "tests" / "fixtures" / "pristine_heading_os"
    dest = tmp_path / "workspace"
    shutil.copytree(src, dest)

    # Seed answers.json with the fully-answered fixture
    (dest / ".setup").mkdir()
    (dest / ".setup" / "answers.json").write_text(
        (REPO / "tests" / "fixtures" / "answers-full.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Apply all answers
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "apply-wizard-answers.py"), "--all"],
        cwd=dest, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    golden_root = REPO / "tests" / "fixtures" / "expected"
    expected_files = [
        "reference/ceo-voice.md",
        "context/personal-info.md",
        "context/business-info.md",
    ]

    # Normalize: strip the non-deterministic generated_date and CRLF
    def _normalize(s: str) -> str:
        s = s.replace("\r\n", "\n")
        return re.sub(r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD", s)

    update = os.environ.get("UPDATE_GOLDEN") == "1"

    drifts = []
    for rel in expected_files:
        produced_path = dest / rel
        assert produced_path.exists(), f"expected rendered file missing: {rel}"
        produced = _normalize(produced_path.read_text(encoding="utf-8"))
        golden_path = golden_root / rel

        if update or not golden_path.exists():
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(produced, encoding="utf-8")
            continue

        golden = _normalize(golden_path.read_text(encoding="utf-8"))
        if produced != golden:
            drifts.append(f"\n--- DRIFT in {rel} ---\n"
                          f"EXPECTED (golden):\n{golden}\n"
                          f"ACTUAL (produced):\n{produced}\n")

    assert not drifts, (
        "Rendered output drifted from golden fixtures. "
        "If the change is intentional, re-run with UPDATE_GOLDEN=1 to refresh the goldens. "
        + "".join(drifts)
    )
