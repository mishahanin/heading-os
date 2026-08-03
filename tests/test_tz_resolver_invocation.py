"""The shell timezone resolver must work when invoked the way installers invoke it.

`scripts/utils/paths.py tz` is how twelve timer installers learn the operator's
zone before rendering `{{TZ}}` into a unit. It was shipped on 2026-08-03 invoked
as a FILE:

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

_ROOT = Path(__file__).resolve().parents[1]
_INSTALLERS = sorted(_ROOT.glob("scripts/install-*-timer.sh"))


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


def test_running_the_file_directly_is_the_shape_that_broke():
    """Not a demand that direct invocation work -- a demonstration of WHY the
    installers may not use it, kept executable so the reason cannot rot into
    folklore.

    `scripts/utils/` is placed first on the path exactly as a direct file run
    would place it, and the stdlib name is imported in a child that has not
    cached it. If this ever stops resolving to the workspace file, the hazard is
    gone and this test says so by failing -- at which point delete it.
    """
    probe = (
        "import sys; sys.path.insert(0, r'%s');"
        "import importlib; m = importlib.import_module('operator');"
        "print(m.__file__)" % (_ROOT / "scripts" / "utils")
    )
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    shadowed = proc.stdout.strip().endswith("scripts/utils/operator.py")
    cached = "python3" in proc.stdout or "lib/python" in proc.stdout
    assert shadowed or cached, (
        f"neither shadowed nor stdlib-cached: {proc.stdout.strip()}"
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
