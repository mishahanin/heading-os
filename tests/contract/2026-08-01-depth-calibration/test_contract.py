"""Frozen contract — A11, depth calibrated to risk by machine.

Canopus contract for `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A11.

Canopus runs the same eleven steps through a CHANGELOG typo and through a change
to the credential patterns. The document's earlier answer was to collapse twelve
steps to about four, which trades rigour for speed EVERYWHERE, including on the
slices where rigour is the entire point. Calibrated depth refuses that trade.

We did not fail to think of this. `/pre-impl` says "skip for trivial one-liner
fixes", its Phase 3 says "skip for small, low-architectural-risk plans", and the
Odin brain carries `right-size-the-harness-calibrated-not-maximalist`. All three
are a human deciding each time, which is what THE LAW calls already dead. This
contract is that principle turned into a function whose answer binds.

Three properties carry the weight and are asserted here rather than reviewed:

1. **The floor cannot be diluted.** One enforcement-surface path among any number
   of prose paths still yields full depth. A calibration that can be watered down
   by adding unrelated files to a commit is worse than no calibration, because it
   reads as rigour while granting none.
2. **The floor cannot rot.** Every entry in the surface must name a path that
   still exists. A renamed file silently dropping off the surface is how this
   mechanism dies quietly, and nothing else would notice.
3. **A bypass is visible.** The gate refuses; the override exists, requires a
   stated reason, and is COUNTED through the same instrument as a refusal, so
   "we overrode it every time" is a readable fact rather than folklore.

The gate is driven as production drives it: `scripts/depth-gate.py` invoked with
staged filenames on argv, the shape pre-commit uses.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_GATE = _ROOT / "scripts" / "depth-gate.py"
_CLI = _ROOT / "scripts" / "slice-depth.py"

# Prose paths, used wherever a test needs files that must NOT raise the depth.
_PROSE = ["CHANGELOG.md", "docs/ARCHITECTURE.md", "README.md"]


def _run(script: Path, args, root: Path = None, log_root: Path = None,
         extra_env: dict = None):
    env = dict(os.environ)
    if root is not None:
        env["WORKSPACE_ROOT"] = str(root)
    if log_root is not None:
        env["WORKSPACE_LOG_DIR"] = str(log_root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, cwd=str(_ROOT),
                          env=env, timeout=120)


def _records(log_root: Path) -> list:
    path = log_root / "denials" / "denials.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _unfrozen_root(tmp_path: Path) -> Path:
    """A workspace root with no freeze held."""
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("probe", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

def test_the_three_depths_are_named():
    from scripts.utils.slice_depth import DEPTH_FULL, DEPTH_LIGHT, DEPTH_STANDARD

    assert {DEPTH_FULL, DEPTH_STANDARD, DEPTH_LIGHT} == {"full", "standard", "light"}


def test_prose_only_is_light():
    from scripts.utils.slice_depth import classify

    assert classify(_PROSE)["depth"] == "light"


def test_ordinary_code_is_standard():
    from scripts.utils.slice_depth import classify

    assert classify(["scripts/utils/markdown.py"])["depth"] == "standard"


def test_an_unrecognised_path_is_standard_not_light():
    """Fail toward ceremony. A path nobody classified is not evidence of safety."""
    from scripts.utils.slice_depth import classify

    assert classify(["some/new/thing.py"])["depth"] == "standard"


def test_an_empty_change_set_is_light():
    from scripts.utils.slice_depth import classify

    assert classify([])["depth"] == "light"


def test_the_enforcement_surface_is_not_empty():
    from scripts.utils.slice_depth import ENFORCEMENT_SURFACE

    assert len(ENFORCEMENT_SURFACE) >= 10


def test_every_surface_entry_forces_full_depth():
    from scripts.utils.slice_depth import ENFORCEMENT_SURFACE, classify

    entries = list(ENFORCEMENT_SURFACE)
    assert len(entries) >= 10
    for entry in entries:
        assert isinstance(entry, str)
        probe = entry.rstrip("/") + "/probe.py" if entry.endswith("/") else entry
        assert classify([probe])["depth"] == "full", f"{probe} did not force full depth"


def test_no_surface_entry_names_a_path_that_no_longer_exists():
    """Property 2. A rename silently drops a file off the floor, and nothing else
    in the repository would notice: the guard keeps passing, on a smaller set."""
    from scripts.utils.slice_depth import ENFORCEMENT_SURFACE

    missing = [e for e in ENFORCEMENT_SURFACE if not (_ROOT / e.rstrip("/")).exists()]
    assert not missing, f"enforcement surface names paths that do not exist: {missing}"


def test_the_floor_cannot_be_diluted_by_unrelated_files():
    """Property 1. Fifty prose files plus one hook file is still full depth."""
    from scripts.utils.slice_depth import classify

    paths = [f"docs/page-{i}.md" for i in range(50)] + [".claude/hooks/_dispatch.py"]
    assert classify(paths)["depth"] == "full"


def test_the_result_names_which_path_raised_the_depth():
    """An answer nobody can audit is an answer nobody will trust."""
    from scripts.utils.slice_depth import classify

    result = classify(["CHANGELOG.md", "scripts/utils/secret_patterns.py"])
    triggers = list(result["triggers"])
    assert len(triggers) == 1
    assert any("secret_patterns.py" in t["path"] for t in triggers)
    assert all("CHANGELOG.md" not in t["path"] for t in triggers)
    assert result["reason"]


@pytest.mark.parametrize("path", [
    ".claude/hooks/_dispatch.py",
    "scripts/utils/secret_patterns.py",
    "scripts/secret-scanner.py",
    "scripts/push-all.py",
    "scripts/utils/engine_guard.py",
    "scripts/leak-guard.py",
    "scripts/content-guard.py",
    "scripts/utils/tool_risk.py",
    "config/tool-risk.json",
    "config/routing-map.yaml",
    ".claude/rules/security.md",
    ".claude/rules/lethal-trifecta.md",
])
def test_the_named_floor_from_the_design_is_covered(path):
    """The design names this floor in prose. Prose is not a mechanism."""
    from scripts.utils.slice_depth import classify

    assert classify([path])["depth"] == "full", f"{path} is outside the floor"


def test_a_windows_style_path_separator_does_not_escape_the_floor():
    from scripts.utils.slice_depth import classify

    assert classify([".claude\\hooks\\_dispatch.py"])["depth"] == "full"


def test_a_path_under_a_live_freeze_forces_full_depth():
    """Frozen files are load-bearing for the slice in flight, whatever they are."""
    from scripts.utils.slice_depth import classify

    manifest = {"files": {"scripts/utils/markdown.py": "deadbeef"}}
    assert classify(["scripts/utils/markdown.py"], freeze=manifest)["depth"] == "full"


# ---------------------------------------------------------------------------
# The binding — driven as pre-commit drives it
# ---------------------------------------------------------------------------

def test_the_gate_allows_prose_with_no_freeze_held(tmp_path):
    proc = _run(_GATE, _PROSE, root=_unfrozen_root(tmp_path), log_root=tmp_path / "logs")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_gate_allows_ordinary_code_with_no_freeze_held(tmp_path):
    proc = _run(_GATE, ["scripts/utils/markdown.py"],
                root=_unfrozen_root(tmp_path), log_root=tmp_path / "logs")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_gate_refuses_the_enforcement_surface_with_no_freeze_held(tmp_path):
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=_unfrozen_root(tmp_path), log_root=tmp_path / "logs")
    assert proc.returncode != 0
    assert "_dispatch.py" in proc.stdout


def test_the_gate_allows_the_enforcement_surface_while_a_freeze_is_held(tmp_path):
    root = _unfrozen_root(tmp_path)
    (root / ".canopus").mkdir()
    (root / ".canopus" / "freeze.json").write_text(
        json.dumps({"label": "probe", "files": {}}), encoding="utf-8")
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=root, log_root=tmp_path / "logs")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_refusal_is_counted(tmp_path):
    log_root = tmp_path / "logs"
    _run(_GATE, [".claude/hooks/_dispatch.py"],
         root=_unfrozen_root(tmp_path), log_root=log_root)
    records = _records(log_root)
    assert [r for r in records if r["mechanism"] == "depth-gate"]


def test_an_override_with_a_reason_allows_and_is_counted(tmp_path):
    """Property 3. The escape exists, and it is not invisible."""
    log_root = tmp_path / "logs"
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=_unfrozen_root(tmp_path), log_root=log_root,
                extra_env={"HEADING_OS_DEPTH_OVERRIDE": "hotfix, gate wedged"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    overrides = [r for r in _records(log_root) if r["mechanism"] == "depth-gate:override"]
    assert overrides
    assert "hotfix" in overrides[0]["reason"]


def test_an_override_with_no_reason_still_refuses(tmp_path):
    """An escape that costs nothing to use is not an escape, it is the default."""
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=_unfrozen_root(tmp_path), log_root=tmp_path / "logs",
                extra_env={"HEADING_OS_DEPTH_OVERRIDE": "   "})
    assert proc.returncode != 0


def test_the_gate_prints_what_it_wants_done_next(tmp_path):
    """A refusal that does not say how to proceed trains the operator to override."""
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=_unfrozen_root(tmp_path), log_root=tmp_path / "logs")
    assert "canopus" in proc.stdout.lower()
    assert "HEADING_OS_DEPTH_OVERRIDE" in proc.stdout


def test_a_broken_denial_log_does_not_turn_a_refusal_into_a_pass(tmp_path):
    """Same invariant the counter carries: telemetry never weakens a gate."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    proc = _run(_GATE, [".claude/hooks/_dispatch.py"],
                root=_unfrozen_root(tmp_path), log_root=blocker)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# The console read path
# ---------------------------------------------------------------------------

def test_the_cli_reports_the_depth_for_named_files(tmp_path):
    proc = _run(_CLI, ["--files", "CHANGELOG.md"], root=_unfrozen_root(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "light" in proc.stdout


def test_the_cli_emits_machine_readable_json(tmp_path):
    proc = _run(_CLI, ["--files", ".claude/hooks/_dispatch.py", "--json"],
                root=_unfrozen_root(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["depth"] == "full"
    assert payload["triggers"]


def test_the_cli_classifies_the_staged_set_by_default(tmp_path):
    """Console-first: the operator asks 'how deep is what I am about to commit?'"""
    proc = _run(_CLI, [])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(word in proc.stdout for word in ("full", "standard", "light"))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_the_gate_is_registered_as_a_commit_hook():
    """The classifier binds only if something calls it. THE LAW applied to A11."""
    config = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "depth-gate" in config
    assert "scripts/depth-gate.py" in config
