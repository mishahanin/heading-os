"""The VPS guide must install a systemd unit that can actually start.

Both files here are engine-routed, so this guide is public advice. Two defects
in it were found by the 2026-08-23 engine audit and reproduced before fixing:

  1. `reference/sentinel.service` is a TEMPLATE. Its own header says to
     substitute `@WORKSPACE_ROOT@` with `sed` at install time. Step 8.1 of the
     guide did a bare `cp` instead, so the installed unit kept the literal
     placeholder in `WorkingDirectory` and `ExecStart`. Measured:

         $ systemd-analyze verify /tmp/sentinel-asguide.service
         :33: WorkingDirectory= path is not absolute: @WORKSPACE_ROOT@
         Unit configuration has fatal error, unit will not be started.

     Step 8.5 then told the reader to expect "active (running)", which was
     unreachable. Every VPS deployment following the guide had a dead Sentinel.

  2. The sync timer ran `scripts/vps-sync.sh` as root, from inside the git tree
     that the same script pulls from GitHub. That script re-runs
     `scripts/setup-platform.sh` and `pip install -r requirements.txt` after a
     pull, both from the pulled tree, so push access to the repository was a
     path to root on the server within thirty minutes. The script itself calls
     `sudo` for the one privileged action it needs, so it was already written
     for an unprivileged user; only the guide put it under root.
"""
from __future__ import annotations

import getpass
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "reference" / "vps-deployment-guide.md"
UNIT = ROOT / "reference" / "sentinel.service"


@pytest.fixture(scope="module")
def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


# --- the template is a template, and the guide must treat it as one -----------

def test_the_unit_template_still_carries_the_placeholder():
    """If this ever fails the template was flattened, and the guide's sed is
    then harmless but pointless. Read the file before changing this test."""
    text = UNIT.read_text(encoding="utf-8")
    assert "WorkingDirectory=@WORKSPACE_ROOT@" in text
    assert "ExecStart=@WORKSPACE_ROOT@" in text


# --- the daemon that reads untrusted mail must not be root --------------------

def test_the_unit_does_not_hardcode_root():
    """Sentinel parses email and Telegram, which is attacker-supplied content.
    `User=root` made any flaw in it or its dependencies a host compromise."""
    text = UNIT.read_text(encoding="utf-8")
    assert "\nUser=root" not in text, (
        "reference/sentinel.service runs Sentinel as root again. Substitute the "
        "account that owns the workspace via @RUN_USER@ instead."
    )
    assert "User=@RUN_USER@" in text


def test_the_unit_carries_the_cheap_hardening():
    """Directives that cost nothing for this workload. ProtectSystem is
    deliberately `full`, not `strict`: strict would also freeze the workspace
    and the data overlay, and Sentinel writes state into both."""
    text = UNIT.read_text(encoding="utf-8")
    for directive in ("NoNewPrivileges=true", "PrivateTmp=true",
                      "ProtectSystem=full"):
        assert directive in text, f"the unit lost {directive}"
    assert "ProtectSystem=strict" not in text, (
        "ProtectSystem=strict makes the workspace read-only and stops Sentinel "
        "writing its state. If this is intended, it needs ReadWritePaths for "
        "both the workspace and the data overlay."
    )


def test_the_guide_substitutes_the_user_placeholder(guide):
    assert "@RUN_USER@" in guide, (
        "the guide no longer fills @RUN_USER@, so the unit fails to start with "
        "an unknown-user error"
    )
    assert "stat -c %U" in guide


def test_the_guide_tells_the_reader_to_confirm_it_is_not_root(guide):
    assert "systemctl show -p User sentinel" in guide, (
        "nothing tells the reader to verify the service is not running as root"
    )


def test_the_guide_never_copies_the_template_unrendered(guide):
    """A bare `cp ... sentinel.service /etc/systemd/system/` is the defect."""
    bare_copy = re.compile(
        r"^\s*(sudo\s+)?cp\s+\S*reference/sentinel\.service\s+/etc/systemd/",
        re.M,
    )
    assert not bare_copy.search(guide), (
        "Step 8.1 copies the unit template into systemd without substituting "
        "@WORKSPACE_ROOT@. systemd rejects the result: 'WorkingDirectory= path "
        "is not absolute'."
    )


def test_the_guide_substitutes_the_placeholder(guide):
    assert "@WORKSPACE_ROOT@" in guide and "sed" in guide, (
        "the guide no longer shows the substitution the template requires"
    )
    assert "scripts/utils/paths.py" in guide, (
        "the guide should resolve the workspace root with paths.py rather than "
        "asking the reader to type an absolute path"
    )


def test_the_guide_tells_the_reader_to_verify_the_rendered_unit(guide):
    assert "systemd-analyze verify" in guide, (
        "without a verify step the reader learns the unit is broken only at "
        "`systemctl start`, which reports a generic failure"
    )


# --- the substitution the guide prescribes must actually produce a valid unit -

@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="systemd-analyze not available on this host")
def test_the_unrendered_unit_is_rejected_by_systemd(tmp_path):
    """Pins defect 1 itself, so the fix cannot be judged on the guide's prose alone."""
    staged = tmp_path / "unrendered.service"
    staged.write_text(UNIT.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(["systemd-analyze", "verify", str(staged)],
                            capture_output=True, text=True)
    combined = result.stdout + result.stderr
    assert "not absolute" in combined, (
        f"expected systemd to reject the placeholder; got: {combined!r}"
    )


@pytest.mark.skipif(shutil.which("systemd-analyze") is None,
                    reason="systemd-analyze not available on this host")
def test_the_rendered_unit_passes_systemd(tmp_path):
    rendered = (UNIT.read_text(encoding="utf-8")
                .replace("@WORKSPACE_ROOT@", str(ROOT))
                .replace("@RUN_USER@", getpass.getuser()))
    staged = tmp_path / "rendered.service"
    staged.write_text(rendered, encoding="utf-8")
    assert "@WORKSPACE_ROOT@" not in rendered and "@RUN_USER@" not in rendered
    result = subprocess.run(["systemd-analyze", "verify", str(staged)],
                            capture_output=True, text=True)
    combined = result.stdout + result.stderr
    # The exit code IS the contract the test's name states. `systemd-analyze
    # verify` rejects a unit with exit 1 for whole classes of defect without
    # ever printing the words "fatal error", so the substring check alone let a
    # unit that systemd refuses pass a test called "passes systemd". The
    # substring stays as the failure message, not as the assertion.
    assert result.returncode == 0, (
        f"the substitution the guide prescribes yields a unit systemd refuses "
        f"(exit {result.returncode}): {combined!r}"
    )
    assert "fatal error" not in combined, combined


# --- the sync timer must not run privileged code from a pulled tree -----------

def test_the_sync_timer_is_not_installed_as_root(guide):
    root_cron = re.compile(r"^\s*\*/\d+ \* \* \* \* /root/", re.M)
    assert not root_cron.search(guide), (
        "the guide schedules vps-sync.sh as root from /root/. That script runs "
        "setup-platform.sh and pip install from the tree it just pulled, so push "
        "access to the repository becomes root on the server."
    )


def test_the_guide_states_the_risk_it_is_asking_the_reader_to_accept(guide):
    """A silent hardening teaches nothing. The reader has to know why."""
    assert "push" in guide and "root" in guide
    assert "sudoers" in guide or "NOPASSWD" in guide, (
        "the guide should show the narrow sudo grant that lets an unprivileged "
        "user restart Sentinel, otherwise the reader falls back to root"
    )


def test_vps_sync_still_uses_sudo_for_the_privileged_step():
    """The evidence that the script was always meant to run unprivileged. If it
    stops calling sudo, the guide's non-root recipe needs revisiting."""
    script = (ROOT / "scripts" / "vps-sync.sh").read_text(encoding="utf-8")
    assert "sudo systemctl restart sentinel" in script


def test_vps_sync_still_executes_pulled_code_so_the_warning_stays_true():
    """The warning claims the script runs code from the tree it pulled. Pin that
    claim to the script, so the warning cannot outlive the behaviour."""
    script = (ROOT / "scripts" / "vps-sync.sh").read_text(encoding="utf-8")
    assert "bash scripts/setup-platform.sh" in script
    assert "pip install -r requirements.txt" in script


# --- the guide must not persist a live API key in plaintext -------------------

def test_the_guide_does_not_write_the_api_key_into_bashrc(guide):
    """Step 3.4 appended `export ANTHROPIC_API_KEY=...` to ~/.bashrc. That put a
    live key in two plaintext files (bashrc and bash_history), exported it to
    every child process, and silently outranked the .env the workspace loads
    credentials from, so a rotated key kept failing with an error pointing
    nowhere near the cause. `.claude/rules/security.md`: secrets load from env
    vars or a gitignored .env, nowhere else."""
    offenders = re.findall(r"^.*ANTHROPIC_API_KEY.*bashrc.*$", guide, re.M)
    live = [line for line in offenders if line.strip().startswith("echo ")]
    assert not live, (
        f"the guide still writes the API key into ~/.bashrc: {live}"
    )


def test_the_guide_points_the_key_at_the_gitignored_env_file(guide):
    assert ".env" in guide and "chmod 600 .env" in guide, (
        "the guide should name the gitignored .env, with restrictive "
        "permissions, as the place a key may live"
    )


def test_the_guide_tells_anyone_who_followed_the_old_version_how_to_recover(guide):
    """The old instructions were shipped. Someone ran them."""
    assert "bash_history" in guide, (
        "no cleanup path for a reader who already pasted a real key under the "
        "previous instructions"
    )
    assert "rotate" in guide.lower(), (
        "a key that sat in two plaintext files is compromised; say so"
    )


def test_the_placeholder_the_guide_names_is_the_placeholder_it_shows(guide):
    """The instruction said 'replace sk-ant-your-key-here' while every command
    beside it showed `<your-anthropic-api-key>`. A reader searching for the
    named string finds nothing and leaves the real placeholder in place.

    One spelling is the fix. Two spellings for one value is the defect, whichever
    one survives: `sk-ant-your-key-here` now appears once, in the `.env` example,
    and nothing else names a different form for the same slot."""
    spellings = {s for s in ("sk-ant-your-key-here", "<your-anthropic-api-key>")
                 if s in guide}
    assert len(spellings) <= 1, (
        f"the guide uses {len(spellings)} different placeholders for the same "
        f"API key: {sorted(spellings)}. A reader told to replace one will not "
        "find it in the command showing the other."
    )


# --- a verification step must be able to fail ---------------------------------

def test_no_verification_check_hides_its_errors_and_its_exit_code(guide):
    """Check 15 read `python3 scripts/crm-health.py 2>/dev/null | head -5`. The
    redirect hid every error, and the pipe handed the reader `head`'s exit
    status, so a script crashing on line one printed nothing and read as a pass.
    Same shape as the two pre-commit gates that printed BLOCKED and exited 0."""
    blind = re.compile(r"^\s*\S*python\S*\s+scripts/\S+\.py.*2>/dev/null.*\|", re.M)
    offenders = blind.findall(guide)
    assert not offenders, (
        f"a verification step discards stderr and pipes away its exit code: "
        f"{offenders}"
    )


def test_the_crm_health_check_uses_the_pinned_interpreter(guide):
    """The rest of the guide invokes .venv/bin/python; a bare python3 runs the
    script under an interpreter holding none of the pinned dependencies."""
    assert ".venv/bin/python scripts/crm-health.py" in guide
