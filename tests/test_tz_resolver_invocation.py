"""The shell timezone resolver must work when invoked the way installers invoke it.

`scripts/utils/paths.py tz` is how the timer installers learn the operator's
zone before rendering `{{TZ}}` into a unit (14 of them on 2026-08-26; the
docstring said twelve until that count was measured). It was shipped on
2026-08-03 invoked as a FILE:

    "$PYTHON" "$WORKSPACE/scripts/utils/paths.py" tz || echo UTC

Running a file puts its own directory at `sys.path[0]`, and `scripts/utils/`
contains `operator.py`. The stdlib `collections` does `from operator import eq`
during `functools` import, so on an interpreter that has not already cached the
real `operator`, the workspace's file wins and the process dies with a circular
import before reaching a line of its own code.

Measured the same day: fatal on Python 3.12 (the service host), silent on 3.11
(the laptop, where `operator` is already in `sys.modules` by the time it matters).
And the `|| echo UTC` fallback turned that crash into the indistinguishable
"no timezone is configured", which is the whole failure this resolver exists to
prevent. A guard that fails into its own error case is worse than absent.

The fix is `-m scripts.utils.paths`, run from the workspace root: `-m` puts the
CWD on the path, not the module's directory, so nothing under `scripts/utils/`
can shadow a stdlib name.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_files import tracked_paths

_ROOT = Path(__file__).resolve().parents[1]
_INSTALLERS = tracked_paths(("scripts/install-*-timer.sh",))


def test_there_are_installers_to_check():
    """Both guards below are green over an empty glob, and in two ways at once.

    `test_no_installer_invokes_a_utils_file_by_path` is parametrized over this
    list: an empty parametrize is ONE SKIP to pytest, not a failure. Its partner
    builds a `missing` list and asserts it empty, which an empty corpus satisfies
    by construction. So a renamed prefix (`install-*-timer.sh` to anything else)
    or a moved directory would switch off the guard that keeps the shadowing fix
    in place, and nothing in this file would say so.

    Measured 2026-08-26: 14 installers match. The floor is set below that on
    purpose, so retiring a timer does not fail an unrelated test.
    """
    assert len(_INSTALLERS) >= 9, (
        f"the scan collapsed to {len(_INSTALLERS)} installers"
    )


def test_the_resolver_answers_when_run_as_a_module(tmp_path):
    """The positive case, driven exactly as an installer drives it: from the
    workspace root, as a module, with the zone coming from a scratch `.env`."""
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / ".env").write_text("HEADING_OS_TZ=Etc/GMT-14\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "scripts.utils.paths", "tz"],
        cwd=str(_ROOT), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "WORKSPACE_ROOT": str(ws), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, f"resolver failed: {proc.stderr[-500:]}"
    assert proc.stdout.strip() == "Etc/GMT-14"


def test_the_shape_that_broke_no_longer_resolves_to_a_workspace_file():
    """The hazard this file was written around is gone, and this is the proof.

    The original probe put `scripts/utils/` first on the path and imported
    `operator`, expecting the workspace file to answer. It never could:
    `operator` is already in `sys.modules` before a `-c` body runs, so the
    import returned the cached stdlib module every time and the assertion
    passed through its `or cached` branch. The `shadowed` half was unreachable
    from the day it was written, and it stayed unreachable through 2026-08-09,
    when `scripts/utils/operator.py` was renamed to `operator_identity.py`
    (with `html`, `trace` and `venv` beside it) and the subject of the
    demonstration ceased to exist without the test noticing.

    Dropping the cache first makes the probe real. With `sys.modules` cleared,
    a file in `scripts/utils/` genuinely does answer the standard library's
    name, which is the whole reason the installers below may not invoke a
    utils file by path. So this now fails if anyone puts a stdlib-named module
    back in that directory, rather than passing whatever happens.
    `tests/test_no_stdlib_shadowing.py` is the broad guard; this is the
    executable statement of the specific shape this module exists to prevent.
    """
    probe = (
        "import sys; sys.modules.pop('operator', None);"
        "sys.path.insert(0, r'%s');"
        "import importlib; print(importlib.import_module('operator').__file__)"
        % (_ROOT / "scripts" / "utils")
    )
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"probe failed: {proc.stderr[-300:]}"
    resolved = proc.stdout.strip()
    assert "scripts/utils/" not in resolved, (
        f"a workspace file answered `import operator`: {resolved}. "
        f"Rename it; see tests/test_no_stdlib_shadowing.py for why."
    )


@pytest.mark.parametrize("installer", _INSTALLERS, ids=lambda p: p.name)
def test_no_installer_invokes_a_utils_file_by_path(installer):
    """The guard that keeps the fix. An installer that reaches back to
    `"$PYTHON" ".../scripts/utils/<x>.py"` re-arms the shadow, and the failure
    is invisible because every one of these call sites has a `|| echo UTC`.
    """
    text = installer.read_text(encoding="utf-8")
    offenders = [line.strip() for line in text.splitlines()
                 if "scripts/utils/" in line and ".py" in line
                 and "-m " not in line and not line.lstrip().startswith("#")]
    assert offenders == [], (
        f"{installer.name} invokes a scripts/utils file by path: {offenders}"
    )


def test_every_installer_resolves_the_zone_through_the_module_form():
    """The other jaw: an installer that stops resolving the zone at all would
    pass the test above by doing nothing. Each one must still ask."""
    missing = [p.name for p in _INSTALLERS
               if "-m scripts.utils.paths tz" not in p.read_text(encoding="utf-8")]
    assert missing == [], f"these no longer resolve the operator zone: {missing}"
