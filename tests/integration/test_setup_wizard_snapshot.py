"""Snapshot test: rendered rich-doc output must match committed golden files.

Catches accidental drift in the SHIPPED `config/wizard-templates/*.tmpl` and
`config/wizard-questions.yaml`. Until 2026-08-27 it did not: the fixture tree
carried its own copies, `apply-wizard-answers.py` resolves both against
`Path.cwd()`, and the test runs it with `cwd=dest` - so it rendered the
duplicates and the shipped files were never read. See
`tests/integration/wizard_fixture.py` for the drift that had already happened.

Set env UPDATE_GOLDEN=1 to regenerate goldens after intentional template changes.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.integration.wizard_fixture import (SHIPPED_CONFIG,
                                              build_workspace)

REPO = Path(__file__).parent.parent.parent


def test_the_fixture_carries_no_wizard_config_of_its_own():
    """The duplication must not come back.

    A second copy of a config is a second thing to keep in step, and it is the
    copy that stops being updated. This one drifted on three `example:` lines
    and nothing said so for months.
    """
    pristine = REPO / "tests" / "fixtures" / "pristine_heading_os"
    strays = [rel for rel in SHIPPED_CONFIG if (pristine / rel).exists()]
    assert not strays, (
        f"the pristine fixture carries its own {strays}. The wizard resolves "
        f"config against cwd, so these shadow the shipped files and the "
        f"integration tests stop testing what ships. Delete them; "
        f"tests/integration/wizard_fixture.py copies the real ones in."
    )


def test_rendered_tree_matches_golden(tmp_path):
    dest = build_workspace(tmp_path / "workspace")

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

    # Normalise CRLF, and blank EVERY ISO date, not only the generated_date this
    # was written for. The wider reach is deliberate but it is a real cost: a
    # date that a template changes on purpose is invisible to this snapshot.
    def _normalize(s: str) -> str:
        s = s.replace("\r\n", "\n")
        return re.sub(r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD", s)

    update = os.environ.get("UPDATE_GOLDEN") == "1"

    drifts = []
    missing = []
    for rel in expected_files:
        produced_path = dest / rel
        assert produced_path.exists(), f"expected rendered file missing: {rel}"
        produced = _normalize(produced_path.read_text(encoding="utf-8"))
        golden_path = golden_root / rel

        if update:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(produced, encoding="utf-8")
            continue

        # An absent golden FAILS. It is not an invitation to write one.
        #
        # The branch above read `if update or not golden_path.exists()`, so a
        # golden that had been deleted, and any file newly added to
        # `expected_files`, was written from whatever the code produced and the
        # run went green having compared nothing. That certifies current
        # behaviour as intended behaviour, which is the one thing a snapshot
        # test exists to refuse. `tests/test_docx_helpers.py` already fails on a
        # missing golden and names the same env switch in its message.
        if not golden_path.exists():
            missing.append(rel)
            continue

        golden = _normalize(golden_path.read_text(encoding="utf-8"))
        if produced != golden:
            drifts.append(f"\n--- DRIFT in {rel} ---\n"
                          f"EXPECTED (golden):\n{golden}\n"
                          f"ACTUAL (produced):\n{produced}\n")

    assert not missing, (
        f"no golden fixture for {missing}. If these are new, or were "
        "regenerated on purpose, re-run with UPDATE_GOLDEN=1 and commit the "
        "result so a human has seen it."
    )
    assert not drifts, (
        "Rendered output drifted from golden fixtures. "
        "If the change is intentional, re-run with UPDATE_GOLDEN=1 to refresh the goldens. "
        + "".join(drifts)
    )
