"""A name the watchdog expects must be a daemon somebody can actually install.

`EXPECTED_DAEMONS` is the fallback set a host uses when it declares no
`daemon.watchdog.expect` scope of its own. A name in it is a promise that
something beats under it: a daemon in the expected set with no heartbeat file
resolves to `missing`, which is a genuine down state, and the watchdog raises a
tiered alert on it every cycle.

That promise is only keepable if a unit template exists to install. Two ways it
breaks, and this module catches both:

- A name is added with nothing behind it. The watchdog then reports a real down
  state, forever, for a process nobody can start.
- A daemon is deleted and its watchdog entry outlives the code. That is exactly
  the state `eval-drift` would have been left in on 2026-08-03: its script, unit
  template and supervision arms all removed, its name still in this tuple. It had
  already spent 72 days alive-and-producing-nothing while every health surface
  called it healthy, so the last thing that fleet needed was a second way to look
  supervised while being neither.

Written as the standing residue of that slice. The contract that gated the
deletion is gone; this is the part of it worth keeping, because it constrains
every future change to the tuple rather than that one removal.
"""

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "scripts" / "templates" / "systemd"

#: Long-running units that deliberately do NOT beat, with the reason. Empty
#: today. An entry here is a decision on the record, which is the point: the
#: alternative is a daemon dropping out of supervision because nobody noticed
#: the tuple was hand-maintained.
_UNWATCHED: dict[str, str] = {}


def _daemon_templates() -> list[str]:
    """Unit templates that describe a LONG-RUNNING process, derived not listed.

    Two signals, both read off the template: not `Type=oneshot` (a oneshot is a
    task that exits and has nothing to beat), and no sibling `.timer` (a unit a
    timer starts is scheduled, not supervised). Measured 2026-09-01 over the 18
    shipped templates: 4 match, and they are exactly the four names in
    EXPECTED_DAEMONS.

    The test is NOT `Type=simple`, deliberately. `simple` is what the fleet uses
    today, but `notify`, `exec` and `forking` are long-running too, and a unit
    with no `Type=` line at all defaults to `simple` - so an allowlist would let
    a new daemon out of supervision by spelling its type differently, which is
    the failure this whole module exists to refuse.
    """
    out = []
    for unit in sorted(_TEMPLATES.glob("*.service")):
        if unit.with_suffix(".timer").exists():
            continue
        if "Type=oneshot" in unit.read_text(encoding="utf-8"):
            continue
        out.append(unit.stem)
    return out


def test_every_expected_daemon_has_a_unit_somebody_can_install():
    """Matched by template STEM PREFIX rather than equality, because the fleet's
    naming is not uniform: `bridge` ships as `bridge-daemon.service` and
    `fireside` as `fireside-bot-daemon.service`, while `sentinel` is just
    `sentinel.service`. The prefix is the part a human types at
    `install-daemon-service.sh`.
    """
    from scripts.watchdog_core import EXPECTED_DAEMONS

    stems = {p.stem for p in _TEMPLATES.glob("*.service")}
    missing = [name for name in EXPECTED_DAEMONS
               if not any(stem.startswith(name) for stem in stems)]
    assert missing == [], (
        f"EXPECTED_DAEMONS names {missing} with no installable unit template "
        f"under {_TEMPLATES.name}/; the watchdog would report a genuine down "
        f"state for a daemon nobody can start"
    )


def test_every_installable_daemon_is_in_the_expected_set():
    """The other direction, which nothing checked until 2026-09-01.

    The test above walks EXPECTED_DAEMONS and asks whether a template exists.
    That catches a name whose code is gone. It cannot catch the opposite, and
    the opposite is the commoner mistake: a NEW long-running daemon ships with
    its unit template and its installer, and nobody adds it to a tuple in
    `watchdog_core.py`. The watchdog then never expects a beat from it, so it can
    die and every health surface stays green -- which is precisely the 72-day
    eval-drift failure the module docstring above describes, arrived at from the
    other side.

    MEASURED before this test existed: a scratch `Type=simple` template with no
    sibling timer added under `scripts/templates/systemd/` left both tests in
    this file green, 14 passed.

    The expected name is a PREFIX of the template stem, the same rule the test
    above uses in reverse, because the fleet's naming is not uniform.
    """
    from scripts.watchdog_core import EXPECTED_DAEMONS

    stems = _daemon_templates()
    assert len(stems) >= 4, (
        f"only {len(stems)} long-running unit templates found under "
        f"{_TEMPLATES.name}/; the derivation has collapsed and this guard is "
        f"green over an empty corpus"
    )
    unwatched = [s for s in stems
                 if s not in _UNWATCHED
                 and not any(s.startswith(name) for name in EXPECTED_DAEMONS)]
    assert unwatched == [], (
        f"{unwatched} ship a long-running unit template that no name in "
        f"EXPECTED_DAEMONS covers, so the watchdog will never expect a beat "
        f"from them and they can die silently. Add the daemon to "
        f"EXPECTED_DAEMONS, or add it to _UNWATCHED here with the reason."
    )


def test_the_derivation_only_counts_long_running_units(tmp_path):
    """Anchor for the two signals above. A derivation that answered "every
    template" would satisfy the test above only by accident, and one that
    answered "none" would satisfy it vacuously past the floor."""
    stems = _daemon_templates()
    assert "update-manager" not in stems, "a timer-driven oneshot is not a daemon"
    assert "ops-radar" not in stems
    assert len(stems) < len(list(_TEMPLATES.glob("*.service"))), (
        "the derivation returned every service template, so it is not deriving "
        "anything"
    )


def test_the_expected_set_is_not_empty():
    """The cheap way to pass the test above is to empty the tuple. A watchdog
    that expects nothing never alerts, which reads identically to a fleet that is
    entirely healthy."""
    from scripts.watchdog_core import EXPECTED_DAEMONS

    assert len(EXPECTED_DAEMONS) >= 4
    assert all(isinstance(n, str) and n for n in EXPECTED_DAEMONS)
