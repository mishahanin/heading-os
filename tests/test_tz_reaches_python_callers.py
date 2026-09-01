"""The operator timezone must reach a Python caller that only asks for it.

`tests/test_tz_resolver_invocation.py` guards the SHELL resolver -- the
`-m scripts.utils.paths tz` form the twelve timer installers use. It passes
because that module's `__main__` calls `load_env()` before it answers. This file
guards the other half, the in-process one, which had no test at all.

`get_default_tz_name()` read `os.environ` and nothing else. `HEADING_OS_TZ`
reaches `os.environ` only through `load_env()` reading the gitignored `.env`, and
nothing exports it into the shell. So every caller that imported the helper
without separately calling `load_env()` silently got UTC on a machine whose
`.env` names the zone correctly -- the same indistinguishable failure the shell
resolver exists to prevent, arrived at from the other direction.

Measured 2026-08-11 on the laptop, where `.env` carries `HEADING_OS_TZ=Asia/Dubai`:
`python -m scripts.utils.paths tz` printed `Asia/Dubai` while
`from scripts.utils.workspace import get_default_tz` returned `UTC` in the same
checkout, minutes apart. `scripts/thread.py` stamps its dates through the second
path, so a thread opened at 00:45 Dubai was filed under the previous day. 61 of
the 83 files that import the helper never call `load_env`; the daemons
(sentinel, sync-exchange, reminders-notify) do and were unaffected, so the damage
was confined to standalone CLI scripts -- thread, crm-health, odin-cadence,
generate-dashboard, workspace-health among them.

The fix is that the helper loads the `.env` itself. A resolver that answers
correctly only when the caller remembers a second call is a resolver whose
failure mode is silence.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

_ASK = (
    "from scripts.utils.workspace import get_default_tz_name, get_default_tz;"
    "print(get_default_tz_name());"
    "print(get_default_tz().key)"
)


def _ask(tmp_path, env_line, **extra_env):
    """Resolve the zone in a scratch workspace, in a fresh interpreter, with no
    `load_env()` call anywhere in the caller. `Etc/GMT-14` is chosen because it
    is a real zone no host is plausibly set to, so a pass cannot come from the
    ambient machine."""
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / ".env").write_text(env_line, encoding="utf-8")

    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "WORKSPACE_ROOT": str(ws), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra_env)

    proc = subprocess.run([sys.executable, "-c", _ASK], cwd=str(_ROOT),
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, f"resolver failed: {proc.stderr[-500:]}"
    return proc.stdout.split()


def test_the_zone_resolves_without_the_caller_loading_env(tmp_path):
    """The regression. A caller that imports the helper and does nothing else
    must get the configured zone, not the UTC fallback."""
    name, key = _ask(tmp_path, "HEADING_OS_TZ=Etc/GMT-14\n")
    assert name == "Etc/GMT-14", (
        f"the helper answered {name!r} while .env names Etc/GMT-14; "
        f"it is reading os.environ without loading the .env that fills it"
    )
    assert key == "Etc/GMT-14", "the ZoneInfo form disagrees with the name form"


def test_an_exported_variable_still_beats_the_env_file(tmp_path):
    """Precedence is `load_env`'s, unchanged: it uses `setdefault`, so an
    explicit `HEADING_OS_TZ=X <command>` wins over the file. Loading the file
    inside the helper must not invert that."""
    name, _ = _ask(tmp_path, "HEADING_OS_TZ=Etc/GMT-14\n",
                   HEADING_OS_TZ="Etc/GMT+12")
    assert name == "Etc/GMT+12", (
        f"the .env overrode an explicitly exported zone (got {name!r}); "
        f"load_env must keep using setdefault"
    )


def test_utc_remains_the_answer_when_nothing_configures_a_zone(tmp_path):
    """The fallback is still the fallback. A workspace whose `.env` names no
    zone gets UTC, exactly as before -- the fix adds a lookup, not a default."""
    name, key = _ask(tmp_path, "# no zone here\n")
    assert name == "UTC" and key == "UTC", f"expected UTC, got {name!r}/{key!r}"


@pytest.mark.parametrize("line", ["HEADING_OS_TZ=\n", "HEADING_OS_TZ=   \n"])
def test_a_key_with_no_value_falls_back_instead_of_crashing(tmp_path, line):
    """The second half of the same fix, found 2026-09-01 by mutating the
    default out of the `os.environ.get` this helper used to end on.

    `.env` is hand-edited and gitignored, so `HEADING_OS_TZ=` with nothing after
    it is an ordinary typo. The default in `.get(name, "UTC")` fires only on an
    ABSENT key, so the present-but-blank value went straight through: the helper
    answered `''` and `get_default_tz()` raised `ValueError: ZoneInfo keys must
    be normalized relative paths, got:` in every CLI script that asks for a
    zone. Not a silent wrong answer, which is what the rest of this file is
    about, but a crash out of a helper whose docstring promises UTC.

    Asserted through `get_default_tz().key` as well as the name, because the
    name alone would pass on a helper that returned a string no ZoneInfo
    accepts.
    """
    name, key = _ask(tmp_path, line)
    assert name == "UTC", f"a blank HEADING_OS_TZ resolved to {name!r}"
    assert key == "UTC", f"the ZoneInfo form disagreed: {key!r}"


def test_the_two_readers_agree_about_a_blank_zone(tmp_path):
    """Both halves of the seam, over one scratch `.env`.

    The shell resolver handled the blank case from the start and the in-process
    one did not, which is finding 6's shape sitting on the exact seam these two
    files exist to guard: one fix, two readers, and only one of them got it.
    Asked of both in the same workspace so a future divergence fails here rather
    than being discovered on a host whose thread dates land a day out.
    """
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    (ws / ".env").write_text("HEADING_OS_TZ=\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "WORKSPACE_ROOT": str(ws), "PYTHONDONTWRITEBYTECODE": "1"}

    shell = subprocess.run([sys.executable, "-m", "scripts.utils.paths", "tz"],
                           cwd=str(_ROOT), capture_output=True, text=True, env=env)
    in_process = subprocess.run([sys.executable, "-c", _ASK], cwd=str(_ROOT),
                                capture_output=True, text=True, env=env)

    assert shell.returncode == 0, shell.stderr[-500:]
    assert in_process.returncode == 0, in_process.stderr[-500:]
    assert shell.stdout.strip() == in_process.stdout.split()[0] == "UTC", (
        f"the two readers disagree: shell said {shell.stdout.strip()!r}, "
        f"in-process said {in_process.stdout.split()!r}"
    )
