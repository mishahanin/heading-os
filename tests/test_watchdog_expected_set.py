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


def test_the_expected_set_is_not_empty():
    """The cheap way to pass the test above is to empty the tuple. A watchdog
    that expects nothing never alerts, which reads identically to a fleet that is
    entirely healthy."""
    from scripts.watchdog_core import EXPECTED_DAEMONS

    assert len(EXPECTED_DAEMONS) >= 4
    assert all(isinstance(n, str) and n for n in EXPECTED_DAEMONS)
