#!/usr/bin/env python3
"""Nine installers baked the first python on PATH into the unit they installed.

MEASURED 2026-09-06 in HELM: seven installed systemd units executed on
`/usr/bin/python3` rather than the pinned `.venv/bin/python` (bridge-daemon,
datastore-map, odin-cadence, odin-propose, ops-radar, reminders,
sync-exchange-daemon). The units were correct; the installers were not. Of the
eighteen scripts under `scripts/` that render `{{PYTHON}}` into a unit template,
nine chose the interpreter as

    PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

which puts whatever PATH happened to hold at install time into `ExecStart=`.
The other nine already preferred the workspace venv, in three different spellings
of the same three lines. That is this repository's dominant defect shape: a rule
written correctly in some of N copies and wrongly in the rest.

Why it matters beyond tidiness. The system 3.12 on this machine happens to carry
numpy and pyyaml today, so the wrong interpreter did not announce itself; that is
luck, not a contract, and it can be withdrawn by any distribution upgrade.
Worse, a unit that starts on the wrong interpreter hands it on: everything it
respawns through `sys.executable` inherits the same wrong Python, so one bad
`ExecStart=` reaches processes nobody installed.

The fix is one owner, `scripts/lib/resolve-python.sh`, sourced by all eighteen,
rather than an eighteenth hand-written copy.

WHAT THIS TEST DOES, AND WHY IT IS NOT A GREP

The corpus comes from the tree (every tracked `scripts/install-*.sh` that
substitutes `{{PYTHON}}`), never from a list written here, so a nineteenth
installer is covered the day it lands. For each one it harvests that file's OWN
interpreter-selection lines and RUNS them, in a scratch workspace, under three
environments: venv present, venv absent, and an explicit `PYTHON=`. The
assertion is on the path the installer would have written into `ExecStart=`, not
on the shape of the source line. `test_the_shape_that_shipped_still_fails_this`
runs the historical defect line through the same harness and requires it to come
out wrong, so the harness has a half that can fail.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from tests.repo_files import read_sources, tracked_paths

_ROOT = Path(__file__).resolve().parents[1]
_OWNER = _ROOT / "scripts" / "lib" / "resolve-python.sh"

# The corpus is "an installer that renders an interpreter into a systemd unit",
# derived from the tree. Reading the sources once, at collection, closes the
# walk-then-read window that `-n auto` opens between the glob and a per-case read.
_VANISHED: list[Path] = []
_CORPUS = [
    (path, text)
    for path, text in read_sources(tracked_paths(("scripts/install-*.sh",)), _VANISHED)
    if "s|{{PYTHON}}|" in text
]

# The lines that decide the interpreter, and nothing else in the file.
_SELECTION = re.compile(
    r'^(?:WORKSPACE=|PYTHON=|source "\$\(dirname "\$0"\)/lib/resolve-python\.sh")'
)

# The line as it shipped, kept here as the harness's own failing half.
_DEFECT = 'PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"'


def _selection_lines(name: str, text: str) -> list[str]:
    """The installer's own top-level interpreter-selection statements, verbatim.

    Fails rather than returning a short list: a refactor that indents the
    assignment or renames WORKSPACE would otherwise hand every case below an
    empty program, which exits 0 and prints nothing, and three tests would go
    green over an installer they never exercised.
    """
    lines = [ln for ln in text.splitlines() if _SELECTION.match(ln)]
    workspace = [ln for ln in lines if ln.startswith("WORKSPACE=")]
    python = [ln for ln in lines if ln.startswith("PYTHON=")]
    assert len(workspace) == 1, f"{name}: expected one WORKSPACE= line, got {workspace}"
    assert len(python) == 1, f"{name}: expected one PYTHON= line, got {python}"
    return lines


def _run_selection(tmp_path: Path, name: str, lines: list[str], *,
                   venv: bool, explicit: str | None, venv_executable: bool = True) -> str:
    """Execute those lines in a scratch workspace; return the resolved path.

    The scratch tree mirrors the real layout the lines resolve against
    (`<ws>/scripts/<name>.sh`, `<ws>/scripts/lib/`, `<ws>/.venv/bin/python`), so
    `$(dirname "$0")/..` lands on a workspace this test controls rather than on
    the checkout, whose real `.venv` would make every case pass for free.
    """
    ws = tmp_path / "ws"
    (ws / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
    (ws / "scripts" / "lib" / "resolve-python.sh").write_text(
        _OWNER.read_text(encoding="utf-8"), encoding="utf-8")

    if venv:
        (ws / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        stub = ws / ".venv" / "bin" / "python"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755 if venv_executable else 0o644)

    # A python3 that is only ever asked "do you exist?", placed FIRST on PATH so
    # the fallback branch has one deterministic answer instead of the host's.
    fakebin = tmp_path / "bin"
    fakebin.mkdir(exist_ok=True)
    fake_python = fakebin / "python3"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)

    script = ws / "scripts" / name
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + "\n".join(lines)
        + '\nprintf %s "$PYTHON"\n',
        encoding="utf-8")
    script.chmod(0o755)

    # `dirname` is external, so /usr/bin and /bin stay on PATH; fakebin is ahead
    # of them, which is what makes the fallback branch answer predictably.
    env = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    if explicit is not None:
        env["PYTHON"] = explicit

    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"{name}: selection exited {proc.returncode}: {proc.stderr[-400:]}"
    return proc.stdout.strip()


_IDS = [p.name for p, _ in _CORPUS]


def test_there_are_installers_to_check():
    """Every parametrized case below is green over an empty corpus, and pytest
    reports an empty parametrize as a SKIP rather than a failure. So the floor
    is asserted outside the loop: a renamed prefix or a moved directory switches
    the guard off, and this is the line that says so.

    MEASURED 2026-09-06: 18 installers render `{{PYTHON}}` into a unit. The floor
    sits below that on purpose, so retiring a timer does not fail this file.
    """
    assert not _VANISHED, f"installers disappeared between the glob and the read: {_VANISHED}"
    assert len(_CORPUS) >= 14, f"the scan collapsed to {len(_CORPUS)} installers"


@pytest.mark.parametrize("path,text", _CORPUS, ids=_IDS)
def test_the_default_interpreter_is_the_pinned_venv(tmp_path, path, text):
    """The defect itself: with no override and a venv present, what goes into
    `ExecStart=` must be the venv interpreter, not the first python3 on PATH."""
    resolved = _run_selection(tmp_path, path.name, _selection_lines(path.name, text),
                              venv=True, explicit=None)
    assert resolved == str(tmp_path / "ws" / ".venv" / "bin" / "python"), (
        f"{path.name} would install a unit running {resolved!r}"
    )


@pytest.mark.parametrize("path,text", _CORPUS, ids=_IDS)
def test_an_explicit_python_still_wins(tmp_path, path, text):
    """The other side. Preferring the venv must not cost the operator the
    override; `PYTHON=/some/other/python scripts/install-...sh` is how a second
    interpreter is pinned, and it beats the venv even when the venv is present.
    """
    resolved = _run_selection(tmp_path, path.name, _selection_lines(path.name, text),
                              venv=True, explicit="/opt/pinned/bin/python")
    assert resolved == "/opt/pinned/bin/python", (
        f"{path.name} overrode an explicit PYTHON with {resolved!r}"
    )


@pytest.mark.parametrize("path,text", _CORPUS, ids=_IDS)
def test_the_system_interpreter_is_still_the_fallback(tmp_path, path, text):
    """The third side, and the reason the fix is a preference and not a
    hard-coded path: on a clone where `uv sync` has not run there is no venv,
    and the installer must still resolve something rather than render an empty
    `ExecStart=`."""
    resolved = _run_selection(tmp_path, path.name, _selection_lines(path.name, text),
                              venv=False, explicit=None)
    assert resolved == str(tmp_path / "bin" / "python3"), (
        f"{path.name} resolved {resolved!r} with no venv present"
    )


@pytest.mark.parametrize("path,text", _CORPUS, ids=_IDS)
def test_a_venv_python_that_cannot_run_is_not_chosen(tmp_path, path, text):
    """"Fall back only when the venv is genuinely absent" has a second reading,
    and this is the one that bites: a `.venv/bin/python` left behind by an
    interrupted `uv sync`, present but not executable. A test that only ever
    creates a working stub cannot tell `-x` from `-e`, and an installer asking
    `-e` would render an `ExecStart=` that fails at every fire with status 203.
    """
    resolved = _run_selection(tmp_path, path.name, _selection_lines(path.name, text),
                              venv=True, explicit=None, venv_executable=False)
    assert resolved == str(tmp_path / "bin" / "python3"), (
        f"{path.name} chose a non-executable {resolved!r} over the PATH interpreter"
    )


def test_the_shape_that_shipped_still_fails_this(tmp_path):
    """The harness's failing half, run against the line as it actually shipped.

    Without this, every assertion above could be passing because the harness
    resolves nothing and compares two empty strings. Here the historical
    selection line goes through the same code path with a venv present, and the
    answer must be the PATH python: that is the bug, reproduced, and it is what
    the three tests above now refuse.
    """
    lines = ['WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"', _DEFECT]
    resolved = _run_selection(tmp_path, "install-historical-defect.sh", lines,
                              venv=True, explicit=None)
    assert resolved == str(tmp_path / "bin" / "python3"), (
        "the pre-2026-09-06 line no longer reproduces the defect; if the "
        "harness stopped resolving, the guards above are measuring nothing"
    )
    assert resolved != str(tmp_path / "ws" / ".venv" / "bin" / "python")

    # And the override half of the old shape did work, which is why the fix had
    # to preserve it rather than replace it.
    assert _run_selection(tmp_path, "install-historical-defect.sh", lines,
                          venv=True, explicit="/opt/pinned/bin/python") == "/opt/pinned/bin/python"


def test_the_rule_has_one_owner():
    """Ownership, not behaviour, and the distinction is the point.

    A nineteenth installer could hand-roll the three lines correctly and pass
    every test above. It would still be the shape that produced this defect:
    N copies of one rule, of which some get fixed. This jaw fails that, and it
    is the only jaw here that a correct-but-copied installer trips.
    """
    assert _OWNER.exists(), f"the single owner is gone: {_OWNER}"
    lost = [p.name for p, text in _CORPUS
            if 'lib/resolve-python.sh' not in text]
    assert lost == [], f"these choose an interpreter without the shared owner: {lost}"

    copies = [p.name for p, text in _CORPUS if "command -v python3" in text]
    assert copies == [], (
        f"these carry their own copy of the PATH fallback: {copies}. "
        f"The fallback belongs in {_OWNER.name} and nowhere else."
    )
