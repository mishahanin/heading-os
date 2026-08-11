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
