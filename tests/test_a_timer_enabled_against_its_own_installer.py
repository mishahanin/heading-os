#!/usr/bin/env python3
"""A unit whose installed state contradicts the installer that put it there.

MEASURED 2026-09-02 on the operator's laptop. `scripts/install-odin-cadence-timer.sh`
ends with `systemctl --user disable --now odin-cadence.timer`, above a comment
that says why in as many words: "ops-radar folds the Odin collect/reflect signal
into its daily exception-driven push, so enabling this timer too would
double-ping Telegram." The machine reported:

    is-enabled: enabled
    is-active:  active
    LastTriggerUSec=Mon 2026-08-31 09:00:26 +04

and `~/.config/systemd/user/timers.target.wants/odin-cadence.timer` was a symlink
dated 2026-08-07, two months newer than the unit files it points at. So the
retirement decision was taken, written down, and then quietly undone on the one
machine it governed.

The duplicate is real, not theoretical. Both senders reach the SAME Telegram
channel by construction: `scripts/ops-radar-notify.py` resolves
`OPS_RADAR_TELEGRAM_TARGET` -> `ODIN_CADENCE_TELEGRAM_TARGET`, and only the
second is set in this workspace's `.env`, which is the fallback its own docstring
calls "zero-config continuity after the standalone Odin push retires". The Odin
signal reaches that channel daily as ops-radar's Tier-B `odin_cadence` signal
(`scripts/utils/ops_signals.py:600 odin_cadence_state`), and reached it AGAIN
every Monday 09:00 from the retired timer.

What has no owner here is not the decision but the DRIFT. An installer that
disables a unit and a machine on which that unit is enabled means nothing ever
compares the two. Every existing timer test in this repo reads templates and
installer SOURCE; not one of them had ever asked the machine what it actually
runs, so this was invisible to a green suite.

Two layers, because they fail for different reasons:

* The static layer reads every installer's intent and needs no systemd at all.
  It runs in CI, and it catches an installer that contradicts itself.
* The live layer asks systemd. It is the one that would have caught this, and it
  is deliberately hard to skip: the skip conditions are probed, never assumed, so
  on a host that genuinely runs these timers there is no way for it to pass by
  declining to look.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import read_sources, tracked_paths

_ROOT = Path(__file__).resolve().parents[1]

# `systemctl --user enable|disable [flags] <unit> [<unit>...]`, up to the first
# shell separator. `--now`, `-f` and friends are flags, not units; a unit is
# matched by its suffix instead, so a new flag cannot be mistaken for a unit.
_SYSTEMCTL = re.compile(r"systemctl\s+--user\s+(enable|disable)\b([^\n;&|]*)")
_UNIT_TOKEN = re.compile(r"(?<![\w./-])([\w@.-]+\.(?:timer|service|path|socket))(?![\w/])")

# Units whose name an installer builds from a shell variable. Recorded rather
# than dropped: a check that quietly ignores what it could not parse reports
# coverage it never had (`.claude/rules/scope-claims.md`).
_UNRESOLVABLE = re.compile(r"\$\{?\w+")

# A quoted span. Removed before matching, because a `systemctl` inside quotes is
# almost always TEXT, and the one form that is not (a quoted `"$UNIT.service"`
# argument) is a variable this parser cannot resolve anyway and reports as such.
_QUOTED = re.compile(r'"[^"]*"' r"|'[^']*'")

# Units whose installed state deliberately disagrees with their installer, with
# the reason. An entry here is a DECISION on record, not a silenced failure, and
# `test_no_deliberate_divergence_is_stale` fails when one stops diverging, so the
# registry cannot fill up with entries guarding nothing.
# Currently EMPTY, and that is the healthy state: every installed unit agrees
# with its installer, so nothing needs an exemption. It held one entry when this
# file was written on 2026-09-02. `memory-auto-retire.timer` was disabled by the
# operator on 2026-08-07 under the never-prune-on-a-clock directive while
# `install-memory-auto-retire-timer.sh` still ended in `enable --now`, so the
# machine was right and the installer was stale. That was resolved the way the
# entry itself said it had to be, by changing the SCRIPT: the installer now
# renders the units and disables the timer, mirroring
# `install-odin-cadence-timer.sh`, and `test_no_deliberate_divergence_is_stale`
# then failed the entry as guarding nothing, which is what retired it.
#
# Adding an entry is a decision on record, never a way to quiet a red test. The
# staleness guard below is what stops this dict from silently accumulating
# blanket permission for the next drift of the same unit.
DELIBERATE_DIVERGENCE: dict[str, str] = {}


def _shell_code(text: str) -> str:
    """An installer's executable text: comments dropped, quoted spans blanked.

    Not a shell parser, and it does not need to be. It must do two things, and
    both were measured as real false readings on 2026-09-02:

    * Stop a usage COMMENT from passing for the command it describes.
      `scripts/install-odin-propose-timer.sh` line 18 is a comment reading
      "`systemctl --user enable`".
    * Stop an ECHOED help hint from passing for an executed command. Both
      `install-chronicle-timer.sh` and `install-ollama-guard-timer.sh` end with
      `echo "  Disable: systemctl --user disable --now <unit>.timer"`, and the
      first version of this parser read those two lines as disable intents. It
      then reported both timers as drifted on a host where they are enabled,
      correctly, by the same installers. A checker that invents two failures is
      worse than one that misses one: it trains the reader to dismiss it.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return _QUOTED.sub(" ", "\n".join(lines))


def _install_path_code(text: str) -> str:
    """`_shell_code`, minus the branches that run only on `--uninstall`.

    The third false reading measured on 2026-09-02. `install-router-accuracy-`,
    `install-datastore-map-` and `install-odin-propose-timer.sh` each open with

        if [[ "${1:-}" == "--uninstall" ... ]]; then
            systemctl --user disable --now <unit>
            rm -f "$DEST_DIR/<unit>"
            exit 0
        fi

    and each then enables that same unit at the bottom. Read whole, all three
    look like installers that contradict themselves. They do not: one branch
    installs and the other removes, and only the first ever reaches the enable.
    The intent this contract compares against is what the installer does when
    run with NO arguments.

    Crude by design, and its crudeness errs toward KEEPING code: only a block
    opened by an `if` line that itself mentions `uninstall` is dropped. The
    top-level unconditional `systemctl --user disable --now odin-cadence.timer`
    in `install-odin-cadence-timer.sh` is in no such block and survives, which is
    the case this whole file exists for.

    ORDER MATTERS, and getting it wrong cost a round here. The guard reads
    `if [[ "${1:-}" == "--uninstall" ... ]]`, so the word this function keys on
    lives INSIDE a quoted span. Blanking quotes first (as `_shell_code` does)
    erases it, the block is never recognised, and all three installers read as
    self-contradicting again. So the blocks are dropped on comment-stripped text
    that still has its quotes, and the quotes are blanked afterwards.
    """
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    out, depth = [], 0
    for line in lines:
        stripped = line.strip()
        if depth == 0:
            if re.match(r"^\s*if\b", line) and "uninstall" in line:
                depth = 1
                continue
            out.append(line)
            continue
        if re.match(r"^\s*(if|case)\b", line):
            depth += 1
        elif stripped in ("fi", "esac"):
            depth -= 1
    return _QUOTED.sub(" ", "\n".join(out))


def _installer_intents() -> tuple[dict[str, dict[str, set[str]]], list[str]]:
    """(unit -> {installer: {"enabled"|"disabled", ...}}, unresolvable strings).

    The innermost value is a SET of every intent that installer expresses for
    that unit, never a single value. The first version assigned into a dict here,
    so a second `systemctl` line for the same unit in the same installer silently
    overwrote the first, and the self-contradiction test below could not fail no
    matter what it read: the contradiction was destroyed before it was asked
    about.
    """
    intents: dict[str, dict[str, set[str]]] = {}
    unresolvable: list[str] = []
    vanished: list[Path] = []
    for installer, text in read_sources(tracked_paths(("scripts/install-*.sh",)), vanished):
        for verb, tail in _SYSTEMCTL.findall(_install_path_code(text)):
            units = _UNIT_TOKEN.findall(tail)
            if not units:
                if _UNRESOLVABLE.search(tail):
                    unresolvable.append(f"{installer.name}: {verb} {tail.strip()}")
                continue
            for unit in units:
                intents.setdefault(unit, {}).setdefault(installer.name, set()).add(
                    "enabled" if verb == "enable" else "disabled")
    return intents, unresolvable


def _settled(intents: dict[str, dict[str, set[str]]]) -> dict[str, dict[str, str]]:
    """Only the units every installer is unambiguous about.

    A unit under contradictory intent has no single 'installer's intent' to
    compare live state against, so it is dropped here and reported by
    `test_no_installer_both_enables_and_disables_the_same_unit` instead.
    """
    out: dict[str, dict[str, str]] = {}
    for unit, per_installer in intents.items():
        if any(len(v) != 1 for v in per_installer.values()):
            continue
        resolved = {name: next(iter(v)) for name, v in per_installer.items()}
        if len(set(resolved.values())) == 1:
            out[unit] = resolved
    return out


def _unit_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".config" / "systemd" / "user"


def _systemd_user_is_reachable() -> str | None:
    """None when systemd can answer for this user, else why it cannot.

    Probed, never assumed. `sys.platform == "linux"` would be a skip reason that
    is false on the one host this defect lived on, and a bare "systemd may be
    absent" is a skip reason that can never be false, which is the shape this
    repo treats as a defect in its own right.
    """
    if shutil.which("systemctl") is None:
        return "no systemctl on PATH"
    probe = subprocess.run(
        ["systemctl", "--user", "show", "--property=Version"],
        capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        return f"no systemd user manager for this user ({(probe.stderr or '').strip()[:120]})"
    return None


def installed_state(unit: str) -> str:
    """`systemctl --user is-enabled <unit>` as its WORD, not its exit code.

    `is-enabled` exits non-zero for `disabled`, which is the ordinary healthy
    answer for half the units here. Reading the returncode would call every
    correctly-retired unit an error and every drifted one a pass, exactly
    inverting the check.
    """
    proc = subprocess.run(["systemctl", "--user", "is-enabled", unit],
                          capture_output=True, text=True, check=False)
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0].strip() if (
        proc.stdout or proc.stderr) else ""


def drift(intents: dict[str, dict[str, str]],
          states: dict[str, str]) -> list[str]:
    """Pure comparison: installer intent versus what systemd reports.

    Split out from the systemd calls so the disagreement logic has a negative
    case that does not need a machine in a particular state to exercise.

    `enabled-runtime` counts as enabled (a runtime-only enable still fires) and
    anything else a disable-intent unit reports (`static`, `masked`, `linked`)
    is NOT treated as drift, because none of those states is the one the
    installer's `disable` was written to prevent.
    """
    offenders = []
    for unit, want in sorted(intents.items()):
        state = states.get(unit)
        if state is None:
            continue
        live = state in ("enabled", "enabled-runtime")
        for installer, intent in sorted(want.items()):
            if intent == "enabled" and not live:
                offenders.append(
                    f"{unit}: {installer} enables it, systemd reports {state!r}")
            if intent == "disabled" and live:
                offenders.append(
                    f"{unit}: {installer} DISABLES it, systemd reports {state!r}")
    return offenders


# ---------------------------------------------------------------------------
# Static layer - no systemd required, runs everywhere
# ---------------------------------------------------------------------------


def test_the_installer_walk_finds_a_real_corpus_of_intents():
    """A guard is green over an empty corpus, so floor the corpus.

    Without this, a regex that stopped matching (a reworded `systemctl` call, a
    renamed installer glob) would empty the intent map and every assertion below
    would pass by having nothing to check. Measured 2026-09-02: 16 installers
    express an intent over 15 distinct units.
    """
    intents, _ = _installer_intents()
    settled = _settled(intents)
    assert len(settled) >= 10, (
        f"only {len(settled)} settled unit intent(s) parsed out of "
        f"scripts/install-*.sh; the parser has probably stopped matching: "
        f"{sorted(settled)}")
    assert any(i == "disabled" for w in settled.values() for i in w.values()), (
        "no installer expresses a DISABLE intent; this whole contract exists "
        "because one does, so a parser that finds none is broken")


def test_the_parser_reads_commands_and_not_the_help_text_beside_them():
    """The false-positive case, pinned on the two literals that produced it.

    `install-chronicle-timer.sh` and `install-ollama-guard-timer.sh` both ENABLE
    their timer and then echo a "Disable:" hint naming the same unit. Read
    naively, each file expresses both intents; read correctly, each expresses
    only the enable. Asserted against the real files, so a rewrite of either
    installer that turns the hint back into a command is caught.
    """
    intents, _ = _installer_intents()
    for unit in ("chronicle.timer", "ollama-guard.timer"):
        verbs = {v for per in intents[unit].values() for v in per}
        assert verbs == {"enabled"}, (
            f"{unit}: expected an enable intent only, read {verbs}; an echoed "
            "help hint is being counted as an executed command")


def test_an_uninstall_branch_is_not_read_as_the_installers_intent():
    """The stripper, and the case it must NOT strip.

    Positive: three real installers disable a unit inside an `--uninstall`
    branch and enable the same unit on the install path. Only the enable is the
    installer's intent, so each must read as a single settled `enabled`.

    Negative, and this is the one that matters: a top-level unconditional
    disable must survive the stripper. A stripper tuned slightly too wide would
    silently swallow `install-odin-cadence-timer.sh`'s retirement line and this
    entire contract would go quiet on the defect it was written for.
    """
    settled = _settled(_installer_intents()[0])
    for unit in ("router-accuracy.timer", "datastore-map.timer", "odin-propose.timer"):
        assert set(settled[unit].values()) == {"enabled"}, (
            f"{unit}: an --uninstall branch is being read as the install intent")

    assert set(settled["odin-cadence.timer"].values()) == {"disabled"}, (
        "the unconditional top-level disable was stripped along with the "
        "uninstall branches; the stripper is too wide")

    # And prove the stripper on a synthetic file, so the assertion above cannot
    # pass merely because the real file changed shape.
    guarded = ('if [[ "${1:-}" == "--uninstall" ]]; then\n'
               "    systemctl --user disable --now demo.timer\n"
               "    exit 0\n"
               "fi\n"
               "systemctl --user enable --now demo.timer\n")
    assert "disable" not in _install_path_code(guarded)
    assert "enable" in _install_path_code(guarded)
    assert "disable" in _install_path_code("systemctl --user disable --now demo.timer\n")


def test_no_installer_both_enables_and_disables_the_same_unit():
    """An installer that contradicts itself has no intent to check against."""
    intents, _ = _installer_intents()
    confused = {f"{unit} ({installer})": sorted(verbs)
                for unit, per_installer in intents.items()
                for installer, verbs in per_installer.items()
                if len(verbs) > 1}
    assert not confused, f"self-contradicting installer intent: {confused}"

    split = {unit: {k: sorted(v) for k, v in per.items()}
             for unit, per in intents.items()
             if len({next(iter(v)) for v in per.values() if len(v) == 1}) > 1}
    assert not split, (
        "two installers disagree about the same unit, so 'the installer's "
        f"intent' is not a single fact: {split}")


def test_the_contradiction_check_can_actually_see_a_contradiction():
    """The negative case for the parser, not for the machine.

    The first version of `_installer_intents` assigned into a dict, so two
    `systemctl` lines for one unit in one installer collapsed to whichever was
    read last and the test above could never fail. This proves the shape that
    carries a contradiction survives long enough to be asked about, and that
    `_settled` refuses to hand a contradicted unit to the live comparison.
    """
    source = (
        "systemctl --user enable --now demo.timer\n"
        "systemctl --user disable --now demo.timer\n"
    )
    verbs = {("enabled" if verb == "enable" else "disabled")
             for verb, tail in _SYSTEMCTL.findall(_shell_code(source))
             for _ in _UNIT_TOKEN.findall(tail)}
    assert verbs == {"enabled", "disabled"}
    assert _settled({"demo.timer": {"install-demo.sh": verbs}}) == {}


def test_no_deliberate_divergence_is_stale():
    """A registry entry that has stopped diverging is an entry guarding nothing.

    Without this, resolving a divergence properly (the installer changes, or the
    operator flips the unit) would leave a permanent exemption behind that
    silently blesses the NEXT drift of the same unit.
    """
    reason = _systemd_user_is_reachable()
    if reason is not None:
        pytest.skip(reason)
    unit_dir = _unit_dir()
    if not unit_dir.is_dir():
        pytest.skip(f"no user unit directory at {unit_dir}")

    settled = _settled(_installer_intents()[0])
    stale = []
    for unit, why in DELIBERATE_DIVERGENCE.items():
        if not (unit_dir / unit).is_file():
            continue          # not deployed here; this host cannot judge it
        if not drift({unit: settled.get(unit, {})}, {unit: installed_state(unit)}):
            stale.append(f"{unit} no longer diverges; drop its entry ({why[:60]}...)")
    assert not stale, "\n".join(stale)


def test_the_retired_odin_cadence_timer_is_still_written_as_retired():
    """The decision itself, pinned where a reader of the diff will see it.

    Narrow on purpose. It does NOT assert that the timer is retired forever; it
    asserts that IF the installer still disables it, the reason travels with the
    code. Un-retiring it is a decision the operator may take, and taking it means
    editing this test, which is the point: the double-ping is the failure mode,
    and `scripts/ops-radar-notify.py` falling back to
    `ODIN_CADENCE_TELEGRAM_TARGET` is what makes both senders land in one channel.
    """
    want = _settled(_installer_intents()[0]).get("odin-cadence.timer")
    assert want, "no installer expresses a settled intent for odin-cadence.timer"
    assert set(want.values()) == {"disabled"}, (
        f"odin-cadence.timer intent changed to {want}; if the weekly Telegram "
        "push is being un-retired, ops-radar's Tier-B odin_cadence signal "
        "(scripts/utils/ops_signals.py) has to stop sending first, or the "
        "operator gets the same nudge twice")

    source = (_ROOT / "scripts" / "install-odin-cadence-timer.sh").read_text(encoding="utf-8")
    assert "ops-radar" in source, (
        "the installer disables the timer without naming what replaced it; the "
        "next reader will 'fix' the disable")


def test_drift_reports_a_disagreement_in_both_directions():
    """The negative case. A comparison with no failing input is not a guard.

    Both directions matter: the live defect was a DISABLED intent found enabled,
    and the opposite (an installer enables a timer that is not running, so a
    scheduled job is silently dead) is the same class of drift.
    """
    intents = {"a.timer": {"install-a.sh": "disabled"},
               "b.timer": {"install-b.sh": "enabled"}}
    assert drift(intents, {"a.timer": "enabled", "b.timer": "enabled"}) == [
        "a.timer: install-a.sh DISABLES it, systemd reports 'enabled'"]
    assert drift(intents, {"a.timer": "disabled", "b.timer": "disabled"}) == [
        "b.timer: install-b.sh enables it, systemd reports 'disabled'"]
    assert drift(intents, {"a.timer": "disabled", "b.timer": "enabled"}) == []
    # enabled-runtime still fires, so it is not an escape from a disable intent.
    assert drift(intents, {"a.timer": "enabled-runtime"}) == [
        "a.timer: install-a.sh DISABLES it, systemd reports 'enabled-runtime'"]
    # A unit systemd has never heard of is not drift; it is not installed.
    assert drift(intents, {}) == []


# ---------------------------------------------------------------------------
# Live layer - the one that would have caught it
# ---------------------------------------------------------------------------


def test_no_installed_unit_contradicts_the_installer_that_placed_it():
    """The live check. Asks systemd, on the machine, unit by unit.

    Every skip below is probed and can be false, and on the host where this
    defect was measured none of them are: systemctl is present, the user manager
    answers, and 15 of the 15 intent units have unit files in
    `~/.config/systemd/user/`. A host with no systemd skips honestly; a host that
    runs these timers cannot.
    """
    reason = _systemd_user_is_reachable()
    if reason is not None:
        pytest.skip(reason)

    raw, unresolvable = _installer_intents()
    intents = _settled(raw)
    unit_dir = _unit_dir()
    if not unit_dir.is_dir():
        pytest.skip(f"no user unit directory at {unit_dir}")

    # Only units this machine actually has. `is-enabled` on an absent unit says
    # `not-found`, which is not drift, and asking about all of them would turn a
    # bare clone into a wall of noise.
    present = sorted(u for u in intents if (unit_dir / u).is_file())
    if not present:
        pytest.skip(
            f"none of the {len(intents)} installer-managed units are installed "
            f"in {unit_dir}; nothing deployed to compare")

    states = {unit: installed_state(unit) for unit in present}
    offenders = [o for o in drift(intents, states)
                 if o.split(":", 1)[0] not in DELIBERATE_DIVERGENCE]

    assert not offenders, (
        "installed unit state contradicts the installer that placed it.\n"
        + "\n".join(f"  {o}" for o in offenders)
        + "\n\nFix the MACHINE, not this test, unless the decision itself changed:\n"
          "  systemctl --user disable --now <unit>   (installer says disabled)\n"
          "  systemctl --user enable  --now <unit>   (installer says enabled)\n"
        + (f"\n({len(unresolvable)} intent(s) built from a shell variable were "
           f"not resolved and are NOT covered: {unresolvable})" if unresolvable else ""))


def test_every_enabled_intent_timer_can_survive_a_reboot():
    """Reboot survival needs all three, and two of them are machine state.

    `.claude/rules/development-standards.md` requires `Persistent=true`,
    `systemctl --user enable`, and `loginctl enable-linger`. The template tests
    in this repo cover only the first, and only in the template. This asks the
    INSTALLED unit and the INSTALLED user record, which is where the other two
    live and where the first can still be edited away by hand.
    """
    reason = _systemd_user_is_reachable()
    if reason is not None:
        pytest.skip(reason)

    intents = _settled(_installer_intents()[0])
    unit_dir = _unit_dir()
    if not unit_dir.is_dir():
        pytest.skip(f"no user unit directory at {unit_dir}")

    wanted = sorted(u for u, want in intents.items()
                    if u.endswith(".timer")
                    and set(want.values()) == {"enabled"}
                    and u not in DELIBERATE_DIVERGENCE
                    and (unit_dir / u).is_file())
    if not wanted:
        pytest.skip(f"no enable-intent timer installed in {unit_dir}")

    missing_persistent = [
        u for u in wanted
        if "persistent=true" not in (unit_dir / u).read_text(encoding="utf-8").lower()]
    assert not missing_persistent, (
        "installed timer without Persistent=true: a fire missed while the host "
        f"was off is lost forever: {missing_persistent}")

    not_enabled = [u for u in wanted
                   if installed_state(u) not in ("enabled", "enabled-runtime")]
    assert not not_enabled, (
        f"enable-intent timer not enabled, so it will not start at boot: {not_enabled}")

    linger = subprocess.run(
        ["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"],
        capture_output=True, text=True, check=False)
    if linger.returncode != 0:
        pytest.skip(f"loginctl cannot report on this user ({(linger.stderr or '').strip()[:120]})")
    assert linger.stdout.strip() == "Linger=yes", (
        "loginctl reports "
        f"{linger.stdout.strip()!r}; without lingering, these user timers stay "
        "silent after an unattended reboot until someone logs in. "
        f'Fix: loginctl enable-linger "$USER" ({len(wanted)} timer(s) affected)')
