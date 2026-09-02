#!/usr/bin/env python3
"""An installer that would re-arm clock-driven deletion of auto-memory.

MEASURED 2026-09-02. `scripts/install-memory-auto-retire-timer.sh` ended in

    systemctl --user enable --now memory-auto-retire.timer

while the machine reported `is-enabled: disabled` and `is-active: inactive`. The
operator switched that timer off by hand on 2026-08-07 under a standing
directive: auto-memory is never pruned or retired on a clock, and deletion
happens only on an explicit instruction from him
(`.claude/rules/memory-discipline.md`, plus the auto-memory records
`memory-is-never-pruned-only-deprioritized`, `memory-auto-retire-expires-field`
and `never-delete-only-annotate`). So the machine was right and the script was
stale, and anyone re-running it, or cloning fresh and following the repo's own
docs, silently overturned a decision nobody had reversed.

What the timer fires is a DELETE, not an annotation, which is why this is worth
a contract of its own. `scripts/memory-auto-retire.py` calls `retire_memory()`,
which `unlink()`s the record from every store (the canonical DATA auto-memory
and every native harness store under `~/.claude/projects/*/memory`), and then
rewrites `MEMORY.md` without its pointer line. The service unit passes no
`--dry-run`. `scripts/utils/memory_stores.py` says in its own docstring that
this is "the only delete that sticks", because the newest-wins reconcile
resurrects a single-store deletion but cannot resurrect an all-store one.

Two layers already stand and neither covers this line:

* `tests/test_memory_expiry.py` covers the REFUSAL gate at the top of the
  installer (exit 9 unless the directive is explicitly overridden) and runs the
  script for real. It stops at the `command -v systemctl` probe by pinning PATH,
  so it never reaches the bottom of the file and says nothing about what the
  installer does to the timer once past the gate.
* `tests/test_a_timer_enabled_against_its_own_installer.py` compares installer
  intent against live systemd. Its live layer SKIPS on a host with no user
  systemd manager, which is every CI runner, so on the machine that matters most
  for a fresh clone it is silent.

This file is the layer between them: it reads the installer's intent as source,
needs no systemd, no HOME and no subprocess, and fails wherever it runs.

The shell parser is IMPORTED from the sibling drift contract rather than copied.
A second copy of a sixty-line shell parser is the copy that stops being fixed,
and importing means a change that breaks the parser breaks both contracts at
once instead of quietly retiring this one.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_a_timer_enabled_against_its_own_installer import (
    _SYSTEMCTL,
    _UNIT_TOKEN,
    _install_path_code,
    _installer_intents,
    _settled,
)

_ROOT = Path(__file__).resolve().parents[1]

UNIT = "memory-auto-retire.timer"
INSTALLER = _ROOT / "scripts" / "install-memory-auto-retire-timer.sh"
SERVICE_TEMPLATE = _ROOT / "scripts" / "templates" / "systemd" / "memory-auto-retire.service"
RETIRE_SCRIPT = _ROOT / "scripts" / "memory-auto-retire.py"
STORES_MODULE = _ROOT / "scripts" / "utils" / "memory_stores.py"


def _intents_in(text: str) -> set[str]:
    """Every intent the given shell TEXT expresses for `UNIT`, install path only.

    Text-level twin of `_installer_intents`, which reads the tracked tree from
    disk. Needed because the decisive assertion below is on a MUTATED copy of
    the real installer, which is on no disk.
    """
    found = set()
    for verb, tail in _SYSTEMCTL.findall(_install_path_code(text)):
        for unit in _UNIT_TOKEN.findall(tail):
            if unit == UNIT:
                found.add("enabled" if verb == "enable" else "disabled")
    return found


def test_the_installer_never_enables_the_memory_retire_timer():
    """The contract. An `enable` for this unit is the defect, in any wording.

    Asserted through the parsed install path rather than a substring, so the
    hints the script echoes ("arm it by hand: systemctl --user enable ...") and
    the comment forbidding the change are not mistaken for the command itself.
    That distinction is the whole reason this reads code and not text: the fixed
    installer deliberately PRINTS the enable command it must never run.
    """
    settled = _settled(_installer_intents()[0])
    assert UNIT in settled, (
        f"no installer expresses a settled intent for {UNIT}; either the unit "
        "was renamed or the parser stopped matching, and this contract cannot "
        "pass by finding nothing")

    assert set(settled[UNIT].values()) == {"disabled"}, (
        f"{UNIT}: installer intent is {settled[UNIT]}, expected 'disabled'.\n"
        "This timer DELETES auto-memory records from every store and strips "
        "their MEMORY.md pointers. The operator disabled it on 2026-08-07 under "
        "the standing directive that memory is never pruned on a clock "
        "(.claude/rules/memory-discipline.md). Re-arming it is his decision to "
        "make by hand, never an installer's to make on his behalf.")


def test_the_check_can_actually_see_an_enable_for_this_unit():
    """The negative case, taken from the real file so it cannot drift apart.

    A guard that only ever reads a passing input proves nothing about what it
    would do with a failing one. Rather than a synthetic snippet, this mutates
    the SHIPPING installer back to the exact form measured on 2026-09-02 and
    requires the parser to call it enabled. If the parser stops matching this
    file's shape, the assertion above starts passing for free and this one
    fails first.
    """
    source = INSTALLER.read_text(encoding="utf-8")
    assert _intents_in(source) == {"disabled"}

    rearmed = source.replace(
        f"systemctl --user disable --now {UNIT}",
        f"systemctl --user enable --now {UNIT}")
    assert rearmed != source, (
        "the disable line was not found verbatim, so the mutation was a no-op "
        "and this negative case measured nothing")
    assert _intents_in(rearmed) == {"enabled"}, (
        "the parser cannot see an enable for this unit even when one is there; "
        "the contract above is green over a broken reader")


def test_the_reason_travels_with_the_code():
    """A bare `disable` with no reason is a line the next reader will 'fix'.

    Narrow on purpose, and it does not assert any particular sentence. It
    requires the three things a reader needs to stop and check before reverting:
    the date the operator acted, the rule that governs it, and the word that
    says what firing costs.
    """
    source = INSTALLER.read_text(encoding="utf-8")
    for token, why in (
        ("2026-08-07", "the date the operator disabled the timer"),
        ("memory-discipline", "the rule that governs the decision"),
        ("DELETE", "what the timer actually does when it fires"),
    ):
        assert token in source, (
            f"{INSTALLER.name} no longer names {why} ({token!r}); the disable "
            "now reads as an unexplained line and will be reverted")


def test_the_severity_this_contract_claims_is_still_true():
    """Deletion, not annotation. Pinned, because the comments assert it.

    If `memory-auto-retire.py` were ever reduced to annotating, the installer's
    comment and this file's docstring would both become false in a direction
    that overstates the danger, and the next reader would rightly stop trusting
    them. Checked against the real files so the claim decays loudly.
    """
    service = SERVICE_TEMPLATE.read_text(encoding="utf-8")
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1, f"expected one ExecStart, read {exec_lines}"
    assert "memory-auto-retire.py" in exec_lines[0], exec_lines[0]
    assert "--dry-run" not in exec_lines[0], (
        "the service now runs the retire pass in dry-run mode; the severity "
        "this contract and the installer's comment claim no longer holds")

    retire = RETIRE_SCRIPT.read_text(encoding="utf-8")
    assert "retire_memory(" in retire, (
        "memory-auto-retire.py no longer calls retire_memory; re-establish what "
        "the timer does before trusting the comments that describe it")

    stores = STORES_MODULE.read_text(encoding="utf-8")
    body = stores.split("def retire_memory(", 1)
    assert len(body) == 2, "retire_memory is gone from scripts/utils/memory_stores.py"
    assert re.search(r"\.unlink\(\)", body[1]), (
        "retire_memory no longer unlinks; if retirement became an annotation, "
        "the 'this DELETES memories' comments in the installer are now wrong")
