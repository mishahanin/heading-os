#!/usr/bin/env python3
"""A fresh clone armed 1 session hook of 17, and no surface said so.

MEASURED 2026-09-02 by comparing the two settings files rather than by reading
any code. `.claude/settings.json` is tracked and registers exactly one hook,
`data-path-redirect.py`. Every other hook is registered in
`.claude/settings.local.json`, which `.gitignore` line 13 excludes, so it does
not exist in a clone until `scripts/setup-platform.sh` writes it.

Among the 16 that a fresh clone therefore lacks is `_dispatch.py`, the single
entry point for eleven PreToolUse walls: the secret scanner, the release gate,
the personal-thread wall, the corporate and docs walls, the cwd anchor, the slow
shell guard, the rate limit, graph-first, fanout-first and the tool budget.

Two independent holes made that state permanent:

1. `scripts/setup-platform.sh` was named in `reference/vps-deployment-guide.md`,
   `scripts/provision-exec.py`, `scripts/vps-sync.sh` and `CHANGELOG.md`, and in
   NONE of `README.md`, `CLAUDE.md`, `docs/QUICKSTART.md` or
   `docs/DEPLOYMENT.md`. A person following the documented setup never ran it.
2. Nothing anywhere compared the live file against the template, so an unarmed
   clone reported healthy from every surface it has.

The fix is `--check` plus a `/prime` health check plus the four documents. This
file holds all three, and holds the prose to the measurement so the numbers
above cannot go stale in silence.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MERGE = ROOT / "scripts" / "merge-platform-settings.py"
SETUP = ROOT / "scripts" / "setup-platform.sh"
HEALTH = ROOT / "scripts" / "prime-health-parallel.py"
TRACKED_SETTINGS = ROOT / ".claude" / "settings.json"
LINUX_TEMPLATE = ROOT / ".claude" / "settings.local.linux.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def merge():
    return _load(MERGE, "merge_platform_settings")


@pytest.fixture(scope="module")
def health():
    return _load(HEALTH, "prime_health_parallel_hooks")


# ============================================================
# The extractor
# ============================================================

def test_hook_registrations_reads_event_and_script_from_a_command(merge):
    """The pair is (event, script), because a script fires under several events."""
    settings = {
        "hooks": {
            "Stop": [
                {"matcher": ".*", "hooks": [
                    {"type": "command", "command": "python3 -c \"...'turn-check.py'...\""},
                    {"type": "command", "command": "python3 -c \"...'bridge-hook.py'...\""},
                ]},
            ],
            "SessionEnd": [
                {"matcher": ".*", "hooks": [
                    {"type": "command", "command": "python3 -c \"...'bridge-hook.py'...\""},
                ]},
            ],
        }
    }
    assert merge.hook_registrations(settings) == {
        ("Stop", "turn-check.py"),
        ("Stop", "bridge-hook.py"),
        ("SessionEnd", "bridge-hook.py"),
    }


@pytest.mark.parametrize("settings", [
    {},
    {"hooks": None},
    {"hooks": []},
    {"hooks": {"Stop": "not-a-list"}},
    {"hooks": {"Stop": [None]}},
    {"hooks": {"Stop": [{"hooks": None}]}},
    {"hooks": {"Stop": [{"hooks": [None]}]}},
    {"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}},
])
def test_a_malformed_hooks_block_yields_fewer_pairs_never_more(merge, settings):
    """Fail toward over-reporting: a shape we cannot read must read as SHORT.

    The opposite direction is the whole defect. A settings file this cannot
    parse must never come back looking armed.
    """
    assert merge.hook_registrations(settings) == set()


# ============================================================
# --check, the three states
# ============================================================

def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_check(template: Path, target: Path):
    return subprocess.run(
        [sys.executable, str(MERGE), str(template), str(target), "--check"],
        capture_output=True, text=True)


def test_check_refuses_a_clone_with_no_live_settings_file(tmp_path):
    """The founding case. Exit 1, and no file created."""
    template = tmp_path / "template.json"
    target = tmp_path / "settings.local.json"
    _write(template, json.loads(LINUX_TEMPLATE.read_text(encoding="utf-8")))

    proc = _run_check(template, target)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "NOT ARMED" in proc.stdout
    assert "setup-platform.sh" in proc.stdout
    assert not target.exists(), "--check wrote the file it was asked to inspect"


def test_check_names_every_missing_registration(tmp_path):
    """A partially armed clone gets the list, not a yes-or-no."""
    full = json.loads(LINUX_TEMPLATE.read_text(encoding="utf-8"))
    template = tmp_path / "template.json"
    target = tmp_path / "settings.local.json"
    _write(template, full)

    short = json.loads(json.dumps(full))
    del short["hooks"]["Stop"]
    _write(target, short)

    proc = _run_check(template, target)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    stop_scripts = {name for event, name in _registrations(full) if event == "Stop"}
    assert stop_scripts, "the linux template registers no Stop hooks; fixture is stale"
    for name in stop_scripts:
        assert name in proc.stdout, f"{name} is missing but --check did not name it"


def test_check_passes_an_armed_clone_and_writes_nothing(tmp_path):
    full = json.loads(LINUX_TEMPLATE.read_text(encoding="utf-8"))
    template = tmp_path / "template.json"
    target = tmp_path / "settings.local.json"
    _write(template, full)
    # A live file with EXTRA local keys is still armed: the check asks about
    # hooks, not about equality.
    live = json.loads(json.dumps(full))
    live["autoMemoryDirectory"] = "/somewhere/private"
    _write(target, live)
    before = target.read_bytes()

    proc = _run_check(template, target)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "armed" in proc.stdout
    assert target.read_bytes() == before, "--check modified the live file"


def test_check_refuses_a_template_that_registers_nothing(tmp_path):
    """A guard green over an empty corpus is not a guard.

    Without this branch, emptying the template certifies every clone on earth.
    """
    template = tmp_path / "template.json"
    target = tmp_path / "settings.local.json"
    _write(template, {"permissions": {"allow": []}})
    _write(target, {})

    proc = _run_check(template, target)

    assert proc.returncode == 1
    assert "registers no hooks" in (proc.stdout + proc.stderr)


def test_check_refuses_an_unparseable_live_file(tmp_path):
    """Cannot tell must never read as armed."""
    template = tmp_path / "template.json"
    target = tmp_path / "settings.local.json"
    _write(template, json.loads(LINUX_TEMPLATE.read_text(encoding="utf-8")))
    target.write_text("{ this is not json", encoding="utf-8")

    proc = _run_check(template, target)

    assert proc.returncode != 0


# ============================================================
# The shell wrapper
# ============================================================

def _clone(tmp_path: Path) -> Path:
    ws = tmp_path / "clone"
    (ws / "scripts").mkdir(parents=True)
    (ws / ".claude").mkdir(parents=True)
    for name in ("setup-platform.sh", "merge-platform-settings.py"):
        (ws / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    for name in ("settings.local.linux.json", "settings.local.macos.json",
                 "settings.local.windows.json"):
        (ws / ".claude" / name).write_bytes((ROOT / ".claude" / name).read_bytes())
    return ws


def _bash(ws: Path, *args: str):
    return subprocess.run(["bash", str(ws / "scripts" / "setup-platform.sh"), *args],
                          capture_output=True, text=True, cwd=str(ws))


@pytest.mark.skipif(sys.platform.startswith("win"),
                    reason="the wrapper is bash; the merge script is covered above "
                           "on every platform")
def test_setup_platform_check_reports_a_fresh_clone_without_arming_it(tmp_path):
    """`--check` must not fall into the first-install copy branch.

    That branch runs when the target is absent, which is exactly the state
    `--check` exists to REPORT. A check that fixes what it measures can never
    report the defect twice.
    """
    ws = _clone(tmp_path)
    proc = _bash(ws, "--check")

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "NOT ARMED" in proc.stdout
    assert not (ws / ".claude" / "settings.local.json").exists(), \
        "--check armed the clone instead of reporting it"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="the wrapper is bash")
def test_setup_platform_check_answers_a_fresh_clone_with_no_python3(tmp_path):
    """The reason the answer is written in bash, held as a test.

    Without an interpreter the merge script cannot run at all, and an operator
    on a half-built machine asking "why is nothing firing?" is exactly who needs
    the answer. Run with a PATH that resolves no python3 and no `.venv`.
    """
    ws = _clone(tmp_path)
    empty_bin = tmp_path / "nothing"
    empty_bin.mkdir()
    # Keep only the utilities bash itself needs; python3 is deliberately absent.
    stub = tmp_path / "bin"
    stub.mkdir()
    for tool in ("bash", "dirname", "uname", "basename", "cp", "chmod"):
        found = shutil.which(tool)
        if found:
            (stub / tool).symlink_to(found)
    proc = subprocess.run(
        ["bash", str(ws / "scripts" / "setup-platform.sh"), "--check"],
        capture_output=True, text=True, cwd=str(ws),
        env={"PATH": str(stub), "HOME": str(tmp_path)})

    assert proc.returncode == 1, (
        "with no python3 on PATH the answer must still be NOT ARMED, not a "
        f"cannot-tell. stdout={proc.stdout!r} stderr={proc.stderr!r}")
    assert "NOT ARMED" in proc.stdout
    assert not (ws / ".claude" / "settings.local.json").exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="the wrapper is bash")
def test_setup_platform_arms_the_clone_and_then_check_passes(tmp_path):
    ws = _clone(tmp_path)

    assert _bash(ws).returncode == 0
    assert (ws / ".claude" / "settings.local.json").is_file()

    after = _bash(ws, "--check")
    assert after.returncode == 0, after.stdout + after.stderr
    assert "armed" in after.stdout


# ============================================================
# The /prime health check
# ============================================================

def test_prime_health_picks_the_same_template_the_shell_does(health):
    """Two template choosers exist, so hold them to the same names.

    `setup-platform.sh` chooses in bash and `_platform_settings_template`
    chooses in Python. A name that drifts in one and not the other sends the
    check against a template nobody installs, which passes for the wrong reason.
    """
    shell = SETUP.read_text(encoding="utf-8")
    for name in ("settings.local.linux.json", "settings.local.macos.json",
                 "settings.local.windows.json"):
        assert name in shell, f"setup-platform.sh no longer installs {name}"
    source = HEALTH.read_text(encoding="utf-8")
    for name in ("settings.local.linux.json", "settings.local.macos.json",
                 "settings.local.windows.json"):
        assert name in source, \
            f"prime-health-parallel.py no longer knows about {name}"


def test_prime_health_is_silent_when_armed_and_loud_when_not(health, tmp_path):
    ws = _clone(tmp_path)
    unarmed = health.run_hooks_armed(ws)
    assert unarmed["status"] == "error", unarmed
    assert unarmed["omit_if_empty"] is False, \
        "an unarmed clone must render, or the finding disappears from the brief"
    assert "NOT ARMED" in unarmed["output"]

    assert _bash(ws).returncode == 0
    armed = health.run_hooks_armed(ws)
    assert armed["status"] == "ok", armed
    assert armed["omit_if_empty"] is True, \
        "an armed clone must not spend a line of every boot saying so"
    assert armed["output"] == ""


def test_prime_health_reports_rather_than_skips_when_it_cannot_check(health, tmp_path):
    """A check that opts out over a missing file is the shape of this finding."""
    ws = tmp_path / "bare"
    (ws / ".claude").mkdir(parents=True)
    result = health.run_hooks_armed(ws)
    assert result["status"] == "missing", result
    assert result["omit_if_empty"] is False
    assert "cannot check" in result["output"]


def test_the_health_check_is_registered_and_rendered(health):
    """A check absent from CHECKS runs never; absent from DISPLAY_ORDER prints never."""
    assert "hooks_armed" in health.CHECKS
    assert "hooks_armed" in health.DISPLAY_ORDER
    assert "hooks_armed" in health.SECTION_BANNERS


# ============================================================
# The prose is held to the measurement
# ============================================================

def _registrations(settings: dict) -> set[tuple[str, str]]:
    """A local copy of the extractor, on purpose.

    The documents below are checked against a number this test derives, so
    deriving it with the code under test would let one bug agree with itself.
    """
    out = set()
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            for entry in group.get("hooks") or []:
                for name in re.findall(r"[\w.-]+\.py", entry.get("command", "")):
                    out.add((event, name))
    return out


@pytest.fixture(scope="module")
def split() -> tuple[int, int, int]:
    """(hooks the tracked file arms, hooks the template arms, the union)."""
    tracked = _registrations(json.loads(TRACKED_SETTINGS.read_text(encoding="utf-8")))
    template = _registrations(json.loads(LINUX_TEMPLATE.read_text(encoding="utf-8")))
    assert tracked, ".claude/settings.json registers no hooks"
    assert template, "the linux template registers no hooks"
    return len(tracked), len(template), len(tracked | template)


#: Each page states the split in its own words. The value is a format string
#: over `tracked`, `template` and `union`, so adding a hook fails every page
#: that has not been updated, by name.
PROSE = {
    "README.md": "{template} of the {union} session hooks",
    "docs/QUICKSTART.md": "{template} of the {union} session hooks",
    "docs/DEPLOYMENT.md": "{template} of the {union} session hooks",
    "CLAUDE.md": "{template} of the {union} hooks",
    # "hooks", plural, since 2026-09-03: session-start.py moved into the
    # tracked file, so a fresh clone arms 2 and not 1.
    "scripts/merge-platform-settings.py": "{tracked} hooks of {union}",
    "scripts/setup-platform.sh": "{tracked} hooks of {union}",
    "scripts/prime-health-parallel.py": "{tracked} hooks of {union}",
    ".claude/skills/prime/SKILL.md": "session hook",
}


def _flow(text: str) -> str:
    """One line of words, with comment markers and line breaks removed.

    A claim that spans a line break is the same claim. Matching the raw bytes
    made this test fail on `1 hook\\n# of 17` in a shell comment, which is a
    guard failing on a re-wrap rather than on a change of meaning.
    """
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*[#*>]+\s?", " ", text))


@pytest.mark.parametrize("rel", sorted(PROSE))
def test_every_page_states_the_split_this_tree_actually_has(rel, split):
    tracked, template, union = split
    expected = PROSE[rel].format(tracked=tracked, template=template, union=union)
    text = _flow((ROOT / rel).read_text(encoding="utf-8"))
    assert expected in text, (
        f"{rel} does not say {expected!r}. The tracked settings file arms "
        f"{tracked} hook(s), the platform template arms {template}, so a fresh "
        f"clone arms {tracked} of {union}. Update the page in the same change "
        f"that moved a hook."
    )


@pytest.mark.parametrize("rel", ["README.md", "docs/QUICKSTART.md",
                                 "docs/DEPLOYMENT.md", "CLAUDE.md"])
def test_every_setup_document_names_the_step_that_arms_the_hooks(rel):
    """The second hole. All four were silent about it until 2026-09-02."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "scripts/setup-platform.sh" in text, (
        f"{rel} documents setup and never names scripts/setup-platform.sh. A "
        "reader following it ends with 1 session hook of 17 armed.")
