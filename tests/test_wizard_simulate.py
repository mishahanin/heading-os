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


# ---------------------------------------------------------------------------
# A question id that YAML did not parse as a string
# ---------------------------------------------------------------------------
#
# The harness validates the shape of every container around its two loops:
# `canned` must be a dict, `answers:` a dict, `skipped:` a list, each with a
# comment explaining what a wrong shape does. The ELEMENTS were never checked.
# `answers: {7: "Acme"}` is ordinary YAML, an unquoted numeric key, and it
# parses to an `int` that reached `subprocess.run` as `--question 7`, raising
# `TypeError: expected str, bytes or os.PathLike object, not int` in a file
# whose whole design is a clean `ERROR: ...` and exit 2 over malformed input.

def _questions(tmp_path):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "wizard-questions.yaml").write_text(yaml.safe_dump([
        {"id": "company_short_name", "audience": ["public"], "type": "placeholder",
         "required": True, "prompt": "?", "example": "e",
         "target": {"placeholder": "{COMPANY}", "files": ["**/*.md"]}}
    ]))
    (tmp_path / "about.md").write_text("{COMPANY} rules.\n")


def _simulate(tmp_path, canned_body):
    canned = tmp_path / "canned.yaml"
    canned.write_text(canned_body)
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(canned), "--workspace", str(tmp_path)],
        capture_output=True, text=True,
    )


def test_a_numeric_answer_id_is_refused_rather_than_crashing(tmp_path):
    _questions(tmp_path)
    result = _simulate(tmp_path, "answers:\n  7: Acme\n")

    assert result.returncode == 2, (
        f"expected the documented exit 2, got {result.returncode}:\n{result.stderr}")
    assert "Traceback" not in result.stderr, result.stderr
    assert "question ids must be strings" in result.stderr, result.stderr
    assert "int" in result.stderr, "the offending type is not named"


def test_a_numeric_skip_id_is_refused_rather_than_crashing(tmp_path):
    """`skipped:` has the same loop and the same hole."""
    _questions(tmp_path)
    result = _simulate(tmp_path, "skipped:\n  - 7\n")

    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "question ids must be strings" in result.stderr, result.stderr


def test_a_quoted_numeric_id_is_still_accepted(tmp_path):
    """The anchor. A guard that refused every id would satisfy both tests above
    and break the harness. A quoted id is a string, whatever it spells, and the
    unknown-question refusal below it is the apply script's to make, not this
    one's."""
    _questions(tmp_path)
    result = _simulate(tmp_path, 'answers:\n  "7": Acme\n')

    assert "question ids must be strings" not in result.stderr, result.stderr


def test_a_string_id_still_runs_the_whole_replay(tmp_path):
    """The other anchor, end to end: the ordinary path is untouched."""
    _questions(tmp_path)
    result = _simulate(tmp_path, "answers:\n  company_short_name: Acme\n")

    assert result.returncode == 0, result.stderr
    assert "Acme rules." in (tmp_path / "about.md").read_text()
