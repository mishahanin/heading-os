"""A run that never happened must not be scored as "did not trigger".

Found by the 2026-08-23 audit (shard `skills-05.md`, "Misconfigured/missing
`claude`"). `run_eval.py` shells out to the `claude` CLI and treats any absence
of a trigger event as a negative result. Two ways that lied:

- **Absent from PATH.** `Popen` raised FileNotFoundError, the worker loop caught
  bare `Exception`, appended `False`, and every `should_trigger: false` case
  then PASSED. The JSON reported `"passed": N` where N was exactly the number
  of negative cases — a partial score produced by zero measurement.
- **Present but failing** (auth, bad `--model`, wrong version). No exception at
  all: `stderr=subprocess.DEVNULL` discarded the reason, stdout carried nothing
  parseable, and every query scored 0 triggers. Indistinguishable from a
  description that genuinely never fires.

Both are the `.claude/rules/scope-claims.md` shape: the method established
nothing and the output asserted a measurement.

A second, unrelated defect is pinned here too, because it was found while
reproducing the first: four skill-creator scripts died on import under
`python scripts/<name>.py`. Their `from scripts.utils import ...` resolved to
the workspace's own `scripts` package, which an editable install pins onto
`sys.path` for every `.venv/bin/python` process. `python -m scripts.<name>`
from the skill root worked, so the break was invisible to the documented path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_CREATOR = ROOT / ".claude" / "skills" / "skill-creator"
SCRIPTS = SKILL_CREATOR / "scripts"

def _load_run_eval():
    """Import the skill's run_eval without leaving the host's `scripts` broken.

    The skill's own package is also called `scripts`, and this repo's is already
    in `sys.modules` by the time pytest reaches here. The script's `sys.path`
    shim is enough for a fresh process (that is the shipped invocation, and the
    subprocess tests below cover it); in-process we additionally have to hide
    the host's cached package, then put it back.
    """
    saved = {k: v for k, v in sys.modules.items()
             if k == "scripts" or k.startswith("scripts.")}
    for key in saved:
        del sys.modules[key]
    sys.path.insert(0, str(SKILL_CREATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            "_run_eval_under_test", SCRIPTS / "run_eval.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SKILL_CREATOR))
        for key in [k for k in sys.modules
                    if k == "scripts" or k.startswith("scripts.")]:
            del sys.modules[key]
        sys.modules.update(saved)


run_eval_mod = _load_run_eval()


# --------------------------------------------------------------------------
# 1. The CLI is missing entirely
# --------------------------------------------------------------------------

def test_require_claude_cli_raises_when_the_binary_is_absent(monkeypatch):
    monkeypatch.setattr(run_eval_mod.shutil, "which", lambda _: None)
    with pytest.raises(run_eval_mod.EvalRunError) as exc:
        run_eval_mod.require_claude_cli()
    assert "not on PATH" in str(exc.value)


def test_require_claude_cli_passes_when_the_binary_is_present(monkeypatch):
    monkeypatch.setattr(run_eval_mod.shutil, "which", lambda _: "/usr/bin/claude")
    run_eval_mod.require_claude_cli()  # must not raise


def test_run_eval_refuses_to_score_without_the_cli(monkeypatch):
    """The library entry point, so run_loop inherits the refusal."""
    monkeypatch.setattr(run_eval_mod.shutil, "which", lambda _: None)
    with pytest.raises(run_eval_mod.EvalRunError):
        run_eval_mod.run_eval(
            eval_set=[{"query": "q", "should_trigger": True}],
            skill_name="demo",
            description="demo",
            num_workers=1,
            timeout=1,
            project_root=ROOT,
        )


def test_the_cli_missing_path_exits_2_and_scores_nothing(tmp_path: Path):
    """End to end: before the fix this printed a JSON report with passes."""
    (tmp_path / ".claude").mkdir()
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    eval_set = tmp_path / "eval.json"
    eval_set.write_text(
        json.dumps([
            {"query": "do the thing", "should_trigger": True},
            {"query": "unrelated chatter", "should_trigger": False},
        ]),
        encoding="utf-8",
    )

    env = dict(os.environ, PATH="/nonexistent-bin-dir")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_eval.py"),
         "--eval-set", str(eval_set), "--skill-path", str(skill),
         "--runs-per-query", "1", "--num-workers", "1", "--timeout", "5"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 2, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "not on PATH" in proc.stderr
    # The old behaviour: a JSON body reporting the negative case as a pass.
    assert '"passed": 1' not in proc.stdout


# --------------------------------------------------------------------------
# 2. The CLI is present but produces no usable stream
# --------------------------------------------------------------------------

def _fake_claude(tmp_path: Path, body: str) -> Path:
    """Put a stand-in `claude` on PATH and return the directory holding it."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "claude"
    fake.write_text(body, encoding="utf-8")
    fake.chmod(0o755)
    return bindir


def test_a_cli_that_fails_is_an_error_not_a_negative_result(tmp_path: Path):
    """A misconfigured CLI used to score every query 0/1 and 'pass' negatives."""
    (tmp_path / ".claude").mkdir()
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    eval_set = tmp_path / "eval.json"
    eval_set.write_text(
        json.dumps([
            {"query": "do the thing", "should_trigger": True},
            {"query": "unrelated chatter", "should_trigger": False},
        ]),
        encoding="utf-8",
    )
    bindir = _fake_claude(
        tmp_path,
        "#!/bin/sh\necho 'Invalid API key - please run /login' >&2\nexit 1\n",
    )

    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_eval.py"),
         "--eval-set", str(eval_set), "--skill-path", str(skill),
         "--runs-per-query", "1", "--num-workers", "2", "--timeout", "10"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 2, proc.stdout[-2000:] + proc.stderr[-2000:]

    payload = json.loads(proc.stdout)
    summary = payload["summary"]
    assert summary["errored"] == 2, summary
    assert summary["passed"] == 0, summary
    for row in payload["results"]:
        assert row["pass"] is None, row
        assert row["trigger_rate"] is None, row
        assert row["runs"] == 0, row
    # The CLI's own reason survives instead of being sent to DEVNULL.
    assert "Invalid API key" in proc.stdout or "Invalid API key" in proc.stderr


def test_a_working_cli_still_produces_a_normal_score(tmp_path: Path):
    """The detector must not turn every run into an error."""
    (tmp_path / ".claude").mkdir()
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    eval_set = tmp_path / "eval.json"
    eval_set.write_text(
        json.dumps([{"query": "unrelated chatter", "should_trigger": False}]),
        encoding="utf-8",
    )
    # Emits one well-formed terminal event and never uses a tool: a genuine
    # "did not trigger", which the negative case expects.
    bindir = _fake_claude(
        tmp_path,
        '#!/bin/sh\necho \'{"type":"result","subtype":"success"}\'\nexit 0\n',
    )

    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_eval.py"),
         "--eval-set", str(eval_set), "--skill-path", str(skill),
         "--runs-per-query", "1", "--num-workers", "1", "--timeout", "10"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["summary"] == {
        "total": 1, "passed": 1, "failed": 0, "errored": 0, "runs_errored": 0,
    }


# --------------------------------------------------------------------------
# 3. The import trap found while reproducing the above
# --------------------------------------------------------------------------

# Each script, with the exit code it gives when run with NO arguments.
#
# No arguments, not `--help`. Two reasons, both measured 2026-08-26. First,
# `package_skill.py` has no argparse at all: it reads `sys.argv[1]` as a path,
# so `--help` was taken as a skill folder named `--help`, printed "Skill folder
# not found", and the test still passed because the only thing it looked for was
# the ABSENCE of an import error. Second, absence proves nothing on its own - a
# script that dies of a SyntaxError, or one that silently does nothing, carries
# no "ModuleNotFoundError" either. Every one of these four prints a usage line
# when called bare, which is a POSITIVE signal that the import chain resolved
# and the script reached its own argument handling.
_BARE_EXIT = {
    "run_eval": 2,             # argparse: missing required arguments
    "run_loop": 2,             # argparse
    "package_skill": 1,        # hand-rolled sys.argv check
    "improve_description": 2,  # argparse
}


@pytest.mark.parametrize("name,expected_exit", sorted(_BARE_EXIT.items()))
def test_the_intra_skill_importers_run_from_a_plain_path(name: str, expected_exit: int):
    """`python scripts/<name>.py` must not resolve `scripts.*` to the repo root."""
    proc = subprocess.run(
        [sys.executable, f"scripts/{name}.py"],
        cwd=SKILL_CREATOR, capture_output=True, text=True, timeout=60,
    )
    combined = proc.stdout + proc.stderr

    assert "ModuleNotFoundError" not in combined, combined[-1500:]
    assert "ImportError" not in combined, combined[-1500:]
    assert "Traceback" not in combined, combined[-1500:]
    assert proc.returncode == expected_exit, combined[-1500:]
    assert "usage" in combined.lower(), (
        f"scripts/{name}.py printed no usage line, so nothing here shows it "
        f"reached its own argument handling: {combined[-1500:]}")


def test_the_bare_usage_line_names_where_the_script_actually_lives():
    """It said `python utils/package_skill.py` while the file has always sat in
    `scripts/`. There is no `utils/` directory in this skill, so an operator who
    copied the line got "No such file or directory". Six occurrences, three of
    them in the module docstring. The old absence-only assertions could not see
    it, because a wrong path is not an ImportError.
    """
    proc = subprocess.run(
        [sys.executable, "scripts/package_skill.py"],
        cwd=SKILL_CREATOR, capture_output=True, text=True, timeout=60,
    )
    combined = proc.stdout + proc.stderr

    assert "scripts/package_skill.py" in combined, combined[-1500:]
    assert "utils/package_skill.py" not in combined, combined[-1500:]
    named = SKILL_CREATOR / "scripts" / "package_skill.py"
    assert named.is_file(), f"the usage line names {named}, which does not exist"


@pytest.mark.parametrize("name,expected_exit", sorted(_BARE_EXIT.items()))
def test_the_documented_module_form_still_works(name: str, expected_exit: int):
    proc = subprocess.run(
        [sys.executable, "-m", f"scripts.{name}"],
        cwd=SKILL_CREATOR, capture_output=True, text=True, timeout=60,
    )
    combined = proc.stdout + proc.stderr

    assert "ModuleNotFoundError" not in combined, combined[-1500:]
    assert "ImportError" not in combined, combined[-1500:]
    assert "Traceback" not in combined, combined[-1500:]
    assert proc.returncode == expected_exit, combined[-1500:]
    assert "usage" in combined.lower(), combined[-1500:]
