"""Smoke tests for scripts/dev/wizard-simulate.py."""
import subprocess
import sys
import yaml
from pathlib import Path

REPO = Path(__file__).parent.parent


def test_simulate_runs_with_canned_answers(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "company_short_name", "audience": ["public"], "type": "placeholder",
         "required": True, "prompt": "?", "example": "e",
         "target": {"placeholder": "{COMPANY}", "files": ["**/*.md"]}}
    ]))
    (tmp_path / "about.md").write_text("{COMPANY} rules.\n")
    canned = tmp_path / "canned.yaml"
    canned.write_text(yaml.safe_dump({"answers": {"company_short_name": "Acme"}}))

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(canned), "--workspace", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Acme rules." in (tmp_path / "about.md").read_text()


def test_simulate_refuses_ceo_master_workspace(tmp_path):
    """Harness must refuse to run against any workspace tagged ceo-master, no override.

    The fixture carries real work to refuse. It was an empty question bank and an
    empty answer set until 2026-09-01, which made the refusal unfalsifiable in
    the direction that matters: a guard that printed its line and then ran anyway
    would have left the same empty workspace behind, and the test could only ever
    have seen the exit code. A refusal is a claim about a side effect that did
    not happen, so the side effect is what is asserted.
    """
    (tmp_path / ".workspace-identity.json").write_text(
        '{"type": "ceo-master", "slug": "test"}'
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "company_short_name", "audience": ["public"], "type": "placeholder",
         "required": True, "prompt": "?", "example": "e",
         "target": {"placeholder": "{COMPANY}", "files": ["**/*.md"]}}
    ]))
    untouched = "{COMPANY} rules.\n"
    (tmp_path / "about.md").write_text(untouched)
    canned = tmp_path / "canned.yaml"
    canned.write_text(yaml.safe_dump({"answers": {"company_short_name": "Acme"}}))

    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(canned), "--workspace", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "ceo-master" in (result.stderr + result.stdout).lower()
    assert (tmp_path / "about.md").read_text() == untouched, (
        "it printed the refusal and substituted anyway"
    )
    assert not (tmp_path / ".setup").exists(), (
        "a refused run still recorded state in .setup/"
    )
