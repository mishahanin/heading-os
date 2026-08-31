"""`load_github_org()` refused where the seam above it had already learned to degrade.

`scripts/utils/operator_identity` was taught on 2026-08-30 to absorb a data-tier
refusal and fall to its documented lower tiers, which made its "Never raises"
docstring true. `load_github_org()` sits one frame below and did not get the same
treatment: it calls `load_admin_config()` -> `get_data_config_dir()` ->
`get_data_root()`, which raises `DataRootError` when `HEADING_OS_DATA` names a
path that is not a directory.

Three engine scripts bound it at MODULE scope, so the exception arrived during
import, before argparse:

    scripts/admin-health.py:43     GITHUB_ORG = load_github_org()
    scripts/offboard-exec.py:42    GITHUB_ORG = load_github_org()
    scripts/provision-exec.py:55   GITHUB_ORG = load_github_org()

All three now resolve it through a module-level `github_org()` called at use
time instead, so the constant no longer freezes an answer at import. The
degradation absorbed below is still what keeps `--help` alive on a missing
overlay; the call-time resolution moved WHEN the question is asked, not what it
answers.

All three answered `--help` with a traceback and exit 1. `scripts/bootcamp-roster.py`
died the same way at its own module-scope `resolve_config_with_example` call, and
fixing only that line moved the identical crash from line 55 to line 91 -- so this
file pins the whole module, not the one line that was reported.

`load_github_org()` had no covering tests at all before this file (measured by a
codegraph blast radius over its 8 callers), so the success paths are bound here
too. A degradation test alone would let the function start answering '' for every
caller and still pass.

Nothing here reaches the network, GitHub, or the operator's live overlay.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.utils import operator_identity  # noqa: E402
from scripts.utils import workspace  # noqa: E402

PYTHON = REPO / ".venv" / "bin" / "python"

# Invented throughout. This repo is public; see the engine law in CLAUDE.md.
FAKE_ORG = "blackbriar-labs"
FAKE_ADMIN_ORG = "treadstone-holdings"


@pytest.fixture
def clean_seam(monkeypatch):
    """Empty the operator seam so the admin.json tier below it is reachable.

    This pins ONLY the tier ABOVE the one under test, and it is the pattern
    `tests/test_operator_seam.py` already uses. The engine-local
    `config/operator.yaml` is gitignored and absent here and in CI, but a
    developer machine may carry one holding a real org -- and `load_github_org()`
    returns early on any truthy seam value, so without this the degradation cases
    below would pass without ever executing the code they exist to bind.

    The condition under test stays real: `HEADING_OS_DATA` is pointed at a path
    that genuinely does not exist, and the `DataRootError` each case observes is
    raised by `env_data_root()` for real, not returned by a stub.
    """
    for env in (
        "HEADING_OS_OPERATOR_NAME",
        "HEADING_OS_OPERATOR_SLUG",
        "HEADING_OS_OPERATOR_GITHUB_ORG",
        "HEADING_OS_OPERATOR_VOICE_REFERENCE",
        "HEADING_OS_OPERATOR_EMAIL",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(
        operator_identity, "_resolve_file",
        lambda: (operator_identity._EXAMPLE_PATH, False),
    )
    operator_identity._reset_cache()
    monkeypatch.setattr(workspace, "ADMIN_SLUGS", None, raising=False)
    # raising=False on purpose. With the fix reverted the flag does not exist,
    # and a strict setattr would turn every case below into a fixture
    # AttributeError -- red for a mechanical reason instead of red because
    # load_github_org() raised. The mutation must bind on the behaviour.
    monkeypatch.setattr(workspace, "_ORG_OVERLAY_FALLBACK_ANNOUNCED", False,
                        raising=False)
    yield
    operator_identity._reset_cache()


def _child_env(**overrides) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("HEADING_OS_OPERATOR_"):
            del env[key]
    env.update(overrides)
    return env


def _empty_org_overlay(tmp_path) -> str:
    """A REAL overlay whose operator.yaml declares an empty github_org.

    This pins the resolved org to '' on any host, without patching anything.
    An overlay `operator.yaml` that EXISTS wins the resolution outright, so the
    engine-local `config/operator.yaml` tier is never consulted -- and that file
    is gitignored, absent in CI, and the one thing a developer machine might
    carry with a real org in it. `_child_env` drops the env tier above it.
    """
    overlay = tmp_path / "empty-org-overlay"
    (overlay / "config").mkdir(parents=True)
    (overlay / "config" / "operator.yaml").write_text(
        'name: "Operator"\nslug: "operator"\ngithub_org: ""\nemail: ""\n',
        encoding="utf-8")
    return str(overlay)


# ============================================================
# The success paths. load_github_org() had none before this file.
# ============================================================

def test_operator_seam_org_wins_and_admin_json_is_never_consulted(monkeypatch, tmp_path):
    """A configured seam answers directly, without touching the data overlay."""
    monkeypatch.setenv("HEADING_OS_OPERATOR_GITHUB_ORG", FAKE_ORG)
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "no-such-overlay"))
    # raising=False on purpose. With the fix reverted the flag does not exist,
    # and a strict setattr would turn every case below into a fixture
    # AttributeError -- red for a mechanical reason instead of red because
    # load_github_org() raised. The mutation must bind on the behaviour.
    monkeypatch.setattr(workspace, "_ORG_OVERLAY_FALLBACK_ANNOUNCED", False,
                        raising=False)
    operator_identity._reset_cache()
    try:
        # The overlay is unreachable, so reaching admin.json at all would raise.
        # Returning the org proves the seam short-circuited before that.
        assert workspace.load_github_org() == FAKE_ORG
    finally:
        operator_identity._reset_cache()


def test_admin_json_supplies_the_org_when_the_seam_is_empty(clean_seam, monkeypatch, tmp_path):
    """With an empty seam and a real overlay, admin.json's github_org is used."""
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True)
    (overlay / "config" / "admin.json").write_text(
        json.dumps({"github_org": FAKE_ADMIN_ORG}), encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    assert workspace.load_github_org() == FAKE_ADMIN_ORG


def test_empty_string_when_the_seam_and_admin_json_both_say_nothing(clean_seam, monkeypatch, tmp_path):
    """A real overlay with no admin.json is the fresh-clone case: '' , no noise."""
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    assert workspace.load_github_org() == ""


# ============================================================
# The degradation. Real missing HEADING_OS_DATA, no stubbed data tier.
# ============================================================

def test_missing_overlay_answers_empty_instead_of_raising(clean_seam, monkeypatch, tmp_path, capsys):
    """The reported defect: this used to raise DataRootError out of the function."""
    missing = tmp_path / "no-such-overlay"
    assert not missing.exists()
    monkeypatch.setenv("HEADING_OS_DATA", str(missing))

    assert workspace.load_github_org() == ""

    err = capsys.readouterr().err
    # "did not raise" is not the property under test. An operator who gets ''
    # must be told the org was never read, or a downstream 404 looks like a
    # GitHub problem rather than a missing overlay.
    assert "[workspace]" in err
    assert "admin.json was NOT read" in err
    assert str(missing) in err


def test_the_degradation_is_announced_once_per_process(clean_seam, monkeypatch, tmp_path, capsys):
    """Eight callers must not print the same paragraph eight times."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "no-such-overlay"))

    workspace.load_github_org()
    first = capsys.readouterr().err
    workspace.load_github_org()
    second = capsys.readouterr().err

    assert "admin.json was NOT read" in first
    assert second == ""


def test_a_write_guard_is_not_widened_by_this(monkeypatch, tmp_path):
    """The absorb is scoped to this one READ; get_data_root() still refuses.

    The DataRootError guard exists to stop a WRITE landing on the live overlay.
    If a later edit "simplifies" load_github_org() by making the resolver itself
    lenient, every write path silently starts targeting the operator's real data.
    """
    from scripts.utils.paths import DataRootError, get_data_root

    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "no-such-overlay"))
    with pytest.raises(DataRootError):
        get_data_root()


# ============================================================
# The four scripts, end to end, in a child process. No patching at all here.
# ============================================================

@pytest.mark.parametrize("script", [
    "admin-health.py",
    "offboard-exec.py",
    "provision-exec.py",
])
def test_help_survives_a_missing_overlay(script, tmp_path):
    """--help is a documented path and must not need the private data overlay."""
    proc = subprocess.run(
        [str(PYTHON), str(REPO / "scripts" / script), "--help"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env=_child_env(HEADING_OS_DATA=str(tmp_path / "no-such-overlay")),
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "DataRootError" not in proc.stdout
    assert proc.returncode == 0, proc.stderr
    assert f"usage: {script}" in proc.stdout


# ============================================================
# The other half of the fix. Answering '' instead of raising is only an
# improvement if the callers then REFUSE; otherwise a loud crash was traded for
# a quiet wrong run, which is worse. None of the three had an empty-org guard.
# ============================================================

@pytest.mark.parametrize("script,argv,expected_code", [
    ("admin-health.py", [], 1),
    ("offboard-exec.py", ["--exec", "fake-slug"], 1),
    ("provision-exec.py",
     ["--name", "James Bond", "--title", "CSO",
      "--email", "james.bond@example.com", "--role", "cso"], 2),
])
def test_an_unresolved_org_stops_the_run(script, argv, expected_code, tmp_path):
    """Each of the three must STOP before it touches GitHub with a guessed path.

    Every repo path would be `/{name}`: admin-health would render a complete
    table of DEAD rows, offboard-exec would print "not a direct collaborator"
    for a revocation that removed nothing, and provision-exec would half-build a
    workspace against repos that were never created. The exit code is each
    script's own existing refusal code, not a new one.
    """
    proc = subprocess.run(
        [str(PYTHON), str(REPO / "scripts" / script), *argv],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env=_child_env(
            HEADING_OS_DATA=_empty_org_overlay(tmp_path),
            # provision-exec.py refuses as deprecated before argparse; this is
            # the documented transition override, and it lets the case reach the
            # guard under test. Harmless for the other two.
            HEADING_OS_ALLOW_LEGACY_PROVISION="1",
        ),
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == expected_code, (proc.returncode, proc.stderr)
    assert "[STOP]" in proc.stderr
    assert "GitHub org could not be resolved" in proc.stderr
    # The reassuring wording of a run that did nothing must not appear.
    assert "not a direct collaborator" not in proc.stdout
    assert "[ok]" not in proc.stdout


def test_bootcamp_roster_refuses_loudly_rather_than_tracebacks(tmp_path):
    """Every path it reads and writes is overlay data, so it must REFUSE.

    Exit 1 is the code its other refusal (PrelimUnavailable) already uses. A
    traceback, a zero exit, or a partial run that looks complete are all wrong
    answers here, and the first was what it actually did.
    """
    proc = subprocess.run(
        [str(PYTHON), str(REPO / "scripts" / "bootcamp-roster.py")],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env=_child_env(HEADING_OS_DATA=str(tmp_path / "no-such-overlay")),
    )
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert "the private data overlay is unreachable" in proc.stderr
    assert "Refusing to run." in proc.stderr
    # It must not claim to have written anything.
    assert "[OK] Wrote" not in proc.stdout


def test_bootcamp_roster_says_the_org_chart_came_from_the_example(tmp_path):
    """The org chart HAS a documented lower tier, so it degrades and says so.

    Without the sentence, a run on the shipped example would present placeholder
    names as the operator's Tribe.
    """
    proc = subprocess.run(
        [str(PYTHON), str(REPO / "scripts" / "bootcamp-roster.py")],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
        env=_child_env(HEADING_OS_DATA=str(tmp_path / "no-such-overlay")),
    )
    assert "[bootcamp-roster]" in proc.stderr
    assert "bootcamp-org-chart.example.json" in proc.stderr
    assert "generic placeholders" in proc.stderr
