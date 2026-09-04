"""`--check` establishes that the timer is armed, one mechanism at a time.

An installer that was merged is not a timer that is armed, and from a directory
listing the two are indistinguishable. This tree carried that exact defect twice
on 2026-09-04, found by an audit rather than by anything mechanical:

  - `scripts/night-repair.py` line 8 says `--run` is what "the timer calls".
    No night-repair timer has ever existed: no template, no installer, nothing in
    `systemctl --user list-unit-files`.
  - `bridge-daemon.service` sat installed and DISABLED, inactive since
    2026-09-04 00:01:51, while its snapshot reader kept serving a file frozen at
    2026-09-03T20:01:35Z under a "computed X ago" label derived from the
    snapshot's own timestamp. Nothing said the daemon had stopped.

Both are one failure: the artifact that says a thing is running is not the thing
running. `install-nightly-refresh-timer.sh --check` is the answer for this timer,
and this file is what stops it becoming a third instance of the same defect.

REBOOT SURVIVAL NEEDS THREE MECHANISMS AND THEY FAIL INDEPENDENTLY
(`.claude/rules/development-standards.md`). Two of the three fail silently, so
each is asserted here on its own, with the other two held correct:

  1. `Persistent=true` in the RENDERED unit  -- a fire missed while the machine
     was off is never replayed without it.
  2. `systemctl --user is-enabled` == `enabled`  -- the unit file exists and the
     timer does not fire.
  3. `loginctl show-user` reports `Linger=yes`  -- the timer stays silent after
     an unattended reboot while looking installed.

Mechanism 3 was ALREADY TRUE on the operator's machine when this was written.
It is asserted anyway. A `loginctl disable-linger`, a new user or a fresh machine
removes it silently, and a check that skips a condition because it happened to be
true once is the shape this workspace keeps finding.

The failing half of every case below fails against a version that asks only
whether the unit FILE exists, which is the version this file is named for: cases
2 through 5 all present a unit file that is present on disk and still not armed.

WHAT THESE TESTS ESTABLISH, AND WHAT THEY DO NOT. Every run here happens against
a throwaway main clone with stub `systemctl` and `loginctl` first on PATH, and
the stub log is asserted so a `--check` that quietly enabled something would go
red. So these establish that `--check` reads the three mechanisms correctly and
writes nothing. They do NOT establish that a real systemd accepts the rendered
unit or that the timer fires: firing it is a machine action, it belongs to the
main clone, and it was deliberately not taken here.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_NAME = "install-nightly-refresh-timer.sh"
INSTALLER = ROOT / "scripts" / INSTALLER_NAME
TEMPLATES = ROOT / "scripts" / "templates" / "systemd"

_SYSTEMCTL_STUB = """\
#!/usr/bin/env bash
printf '%s\\n' "systemctl $*" >> "$STUB_LOG"
if [ "$1" = "--user" ] && [ "$2" = "is-enabled" ]; then
    if [ -n "${IS_ENABLED_OUT:-}" ]; then
        printf '%s\\n' "$IS_ENABLED_OUT"
        exit 0
    fi
    exit 1
fi
exit 0
"""

_LOGINCTL_STUB = """\
#!/usr/bin/env bash
printf '%s\\n' "loginctl $*" >> "$STUB_LOG"
if [ "$1" = "show-user" ]; then
    printf 'Linger=%s\\n' "${LINGER_OUT:-no}"
fi
exit 0
"""


def _render(text: str, workspace: str, tz: str = "Etc/UTC") -> str:
    """The installer's own three substitutions, applied the installer's way."""
    return (text.replace("{{WORKSPACE}}", workspace)
                .replace("{{PYTHON}}", f"{workspace}/.venv/bin/python")
                .replace("{{TZ}}", tz))


@pytest.fixture()
def helm(tmp_path: Path) -> dict:
    """A throwaway MAIN clone (`.git` is a directory) holding the installer.

    The clone guard's predicate is the shape of `.git`, so `git init` is what
    makes this HELM rather than a YARD. Without it the installer refuses at line
    one and every case below would pass for the wrong reason.
    """
    clone = tmp_path / "clone"
    (clone / "scripts" / "lib").mkdir(parents=True)
    (clone / "scripts" / "templates" / "systemd").mkdir(parents=True)
    for name in (INSTALLER_NAME,):
        target = clone / "scripts" / name
        target.write_text(INSTALLER.read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    guard = ROOT / "scripts" / "lib" / "require-main-clone.sh"
    (clone / "scripts" / "lib" / "require-main-clone.sh").write_text(
        guard.read_text(encoding="utf-8"), encoding="utf-8")
    for unit in ("nightly-refresh.service", "nightly-refresh.timer"):
        (clone / "scripts" / "templates" / "systemd" / unit).write_text(
            (TEMPLATES / unit).read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True,
                   capture_output=True)

    binstub = tmp_path / "bin"
    binstub.mkdir()
    (binstub / "systemctl").write_text(_SYSTEMCTL_STUB, encoding="utf-8")
    (binstub / "loginctl").write_text(_LOGINCTL_STUB, encoding="utf-8")
    for name in ("systemctl", "loginctl"):
        (binstub / name).chmod(0o755)

    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    return {"clone": clone, "bin": binstub, "home": home,
            "units": home / ".config" / "systemd" / "user",
            "log": tmp_path / "stub.log"}


def _install_units(helm: dict, *, timer_text: str | None = None,
                   service: bool = True, tz: str = "Etc/UTC") -> None:
    """Render the real templates into the fake user-unit directory."""
    text = timer_text if timer_text is not None else _render(
        (TEMPLATES / "nightly-refresh.timer").read_text(encoding="utf-8"),
        str(helm["clone"]), tz)
    (helm["units"] / "nightly-refresh.timer").write_text(text, encoding="utf-8")
    if service:
        (helm["units"] / "nightly-refresh.service").write_text(
            _render((TEMPLATES / "nightly-refresh.service").read_text(encoding="utf-8"),
                    str(helm["clone"]), tz), encoding="utf-8")


def _check(helm: dict, *, is_enabled: str = "enabled",
           linger: str = "yes") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{helm['bin']}:{env['PATH']}"
    env["HOME"] = str(helm["home"])
    env["STUB_LOG"] = str(helm["log"])
    env["IS_ENABLED_OUT"] = is_enabled
    env["LINGER_OUT"] = linger
    return subprocess.run(
        ["bash", str(helm["clone"] / "scripts" / INSTALLER_NAME), "--check"],
        capture_output=True, text=True, check=False, env=env,
        cwd=str(helm["clone"]))


def _log(helm: dict) -> str:
    try:
        return helm["log"].read_text(encoding="utf-8")
    except OSError:
        return ""


# ============================================================
# The armed state: --check must be able to say yes
# ============================================================

def test_all_three_mechanisms_present_exits_zero(helm):
    """A guard that refuses everything satisfies every refusal test."""
    _install_units(helm)
    result = _check(helm)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mechanism 1/3" in result.stdout
    assert "mechanism 2/3" in result.stdout
    assert "mechanism 3/3" in result.stdout


def test_check_writes_nothing_and_enables_nothing(helm):
    """--check reports. It must never be the thing that arms the timer."""
    _install_units(helm)
    assert _check(helm).returncode == 0
    calls = _log(helm)
    assert "is-enabled" in calls, "the check never asked systemd anything"
    for verb in ("enable", "start", "daemon-reload", "disable"):
        assert f"systemctl --user {verb}" not in calls, (
            f"--check reached systemd with '{verb}'")
    assert "enable-linger" not in calls


# ============================================================
# The four states a file-existence check cannot tell apart
# ============================================================

def test_not_installed_is_reported_as_not_installed(helm):
    result = _check(helm)
    assert result.returncode == 1
    assert "not installed" in result.stderr


def test_installed_but_disabled_is_a_distinct_failure_from_not_installed(helm):
    """These two look identical in a directory listing and are not the same.

    This is the `bridge-daemon.service` state: the unit file is on disk, every
    file-existence check passes, and the timer does not fire.
    """
    _install_units(helm)
    result = _check(helm, is_enabled="disabled")
    assert result.returncode == 1
    assert "INSTALLED BUT NOT ENABLED" in result.stderr
    assert "disabled" in result.stderr
    assert "mechanism 2/3" in result.stderr
    # The not-installed VERDICT is the `[MISSING]` line, not the phrase: the
    # disabled message quotes "not installed" in order to say it is a different
    # state, and a plain substring scan reads that explanation as the thing it
    # describes.
    assert "[MISSING]" not in result.stderr


def test_systemd_not_knowing_the_unit_is_its_own_failure(helm):
    _install_units(helm)
    result = _check(helm, is_enabled="")
    assert result.returncode == 1
    assert "does not know" in result.stderr


def test_a_rendered_timer_without_persistent_fails_naming_mechanism_one(helm):
    """Mechanism 1, held alone: enabled, lingering, and still not reboot-safe."""
    text = _render((TEMPLATES / "nightly-refresh.timer").read_text(encoding="utf-8"),
                   str(helm["clone"]))
    _install_units(helm, timer_text=text.replace("Persistent=true", "Persistent=false"))
    result = _check(helm, is_enabled="enabled", linger="yes")
    assert result.returncode == 1
    assert "mechanism 1/3" in result.stderr
    assert "Persistent=true is NOT" in result.stderr


def test_linger_off_fails_naming_mechanism_three(helm):
    """The mechanism that was already true on this machine, checked anyway."""
    _install_units(helm)
    result = _check(helm, is_enabled="enabled", linger="no")
    assert result.returncode == 1
    assert "mechanism 3/3" in result.stderr
    assert "enable-linger" in result.stderr


def test_a_missing_wantedby_fails(helm):
    text = _render((TEMPLATES / "nightly-refresh.timer").read_text(encoding="utf-8"),
                   str(helm["clone"]))
    _install_units(helm, timer_text=text.replace("WantedBy=timers.target", "WantedBy=default.target"))
    result = _check(helm)
    assert result.returncode == 1
    assert "WantedBy=timers.target is NOT" in result.stderr


def test_an_unsubstituted_token_fails(helm):
    """A unit that still carries {{TZ}} is installed and inert."""
    _install_units(helm, timer_text=(TEMPLATES / "nightly-refresh.timer")
                   .read_text(encoding="utf-8"))
    result = _check(helm)
    assert result.returncode == 1
    assert "unsubstituted" in result.stderr


def test_a_timer_with_no_service_fails(helm):
    _install_units(helm, service=False)
    result = _check(helm)
    assert result.returncode == 1
    assert "fires into nothing" in result.stderr


# ============================================================
# The templates themselves
# ============================================================

def test_the_rendered_units_carry_the_three_mechanisms_and_no_locale(helm):
    """Rendering, not firing. This is a claim about text on disk.

    `tests/test_timer_timezone.py` holds the same invariants across every
    template in the tree; asserted here too because this pair is the one whose
    absence would leave the nightly silently unscheduled.
    """
    timer = _render((TEMPLATES / "nightly-refresh.timer").read_text(encoding="utf-8"),
                    str(helm["clone"]), tz="Etc/UTC")
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    assert "OnCalendar=*-*-* 01:30:00 Etc/UTC" in timer
    assert "{{" not in timer

    raw = (TEMPLATES / "nightly-refresh.timer").read_text(encoding="utf-8")
    assert "{{TZ}}" in raw, "the template must take its zone from the installer"

    service = _render((TEMPLATES / "nightly-refresh.service").read_text(encoding="utf-8"),
                      str(helm["clone"]))
    assert "{{" not in service
    # Directives only. The template's own comment names the banned directive in
    # order to forbid it, and a plain substring scan over the whole file reads
    # that explanation as the violation, which teaches people to stop
    # explaining.
    directives = [line for line in service.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    assert not any(line.startswith("Environment=HEADING_OS_TZ") for line in directives), (
        "a zone pinned into the unit becomes a second, staler source than .env")
    assert any(line.startswith("Environment=TZ=") for line in directives)
    # A bound the unit chooses. `Type=oneshot` DISABLES the start timeout by
    # default (`man systemd.service`: "except when Type=oneshot is used, in
    # which case the timeout is disabled by default"), so this is not restoring
    # a protection systemd would deny. It is pinned here so a future edit cannot
    # quietly drop the cap and leave a wedged nightly running forever, and so
    # the cap cannot be set below anything a real run has taken: measured
    # 2026-09-05 in HELM, 979s clean at load 26.
    assert "TimeoutStartSec=" in service
    timeout = int(next(line.split("=", 1)[1] for line in directives
                       if line.startswith("TimeoutStartSec=")))
    assert timeout >= 1800, f"TimeoutStartSec={timeout}s cannot hold a full suite"


def test_the_installer_ships_all_three_reboot_mechanisms(helm):
    """The installer, read as text: the third one is the one always forgotten."""
    text = INSTALLER.read_text(encoding="utf-8")
    body = [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]
    joined = "\n".join(body)
    assert "systemctl --user enable --now" in joined
    assert "loginctl enable-linger" in joined


def test_the_clone_guard_precedes_every_machine_verb(helm):
    """A guard placed after one is a guard reporting on work already done.

    Held generically by `tests/test_guarded_shell_installers_refuse_from_a_worktree.py`
    once this installer is on its list; asserted here as well because `--check`
    reaches systemd too, and it would be easy to move it above the guard to make
    it testable from a worktree.
    """
    lines = INSTALLER.read_text(encoding="utf-8").splitlines()
    def first(needle: str) -> int | None:
        for index, raw in enumerate(lines, 1):
            line = raw.strip()
            if line and not line.startswith("#") and needle in line:
                return index
        return None
    guard = first("require_main_clone")
    assert guard is not None
    for verb in ("systemctl", "loginctl"):
        at = first(verb)
        assert at is not None, f"{verb} is never called; this test measures nothing"
        assert guard < at, f"the guard sits below the first {verb} call"
