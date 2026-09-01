#!/usr/bin/env python3
"""A handoff filename is stamped in the operator's calendar day, not in UTC.

The workspace splits datetime use in two, and the DTZ lint ruleset is held at
zero to keep the split honest:

  - SERIALIZED (JSON `ts`, `created_at`, audit lines) -> `datetime.now(timezone.utc)`
  - DISPLAY / calendar-day (formatting, "today" for a filename or header)
    -> `datetime.now(get_default_tz())`

Every checkpoint timestamp used the first form, including the two that build a
filename. On an operator at UTC+4 that files a handoff written at 02:56 local
as `2026-08-19-2256..`, under the previous calendar day — and midnight to 04:00
local is exactly when this operator works. Found 2026-08-20 with the live
archive showing a file stamped `2026-08-19-225625` whose mtime was
`2026-08-20 02:56:25`.

The change is safe to make forward-only because nothing reads the stamp back:
every consumer orders by `st_mtime`. These tests pin both halves — the display
stamp must be local, and the stored timestamps must stay UTC, because moving
those would be the opposite mistake.
"""
from __future__ import annotations

import re
from datetime import timezone
from pathlib import Path
from tests.repo_files import tracked_paths

ROOT = Path(__file__).resolve().parent.parent

STAMP_CALL = re.compile(r"(\w+(?:\.\w+)?)\(\)\.strftime\(\"%Y-%m-%d-%H%M%S\"\)")
STAMP_VIA_VAR = re.compile(r"^\s*(\w+)\s*=\s*datetime\.now\(timezone\.utc\)\s*$", re.M)


def test_local_now_and_utc_now_are_both_present_and_differ_in_role():
    import sys

    sys.path.insert(0, str(ROOT))
    from scripts.utils import checkpoint_paths as CP

    assert CP.utc_now().tzinfo is timezone.utc
    local = CP.local_now()
    assert local.tzinfo is not None, "local_now must be tz-aware"
    # Same instant, possibly a different wall clock and calendar day.
    assert abs((CP.utc_now() - local).total_seconds()) < 5


def test_local_now_follows_the_operators_configured_zone(monkeypatch):
    """The property this whole file is named for, and nothing bound it.

    MEASURED 2026-09-01: replacing `local_now`'s body with
    `datetime.now(timezone.utc)` - reverting the 2026-08-20 fix exactly - left
    the full suite green at 4765 passed. Every other stamp assertion here
    compares a value against `CP.local_now()` itself, so the same clock stands on
    both sides of the `==` and the comparison holds in whatever zone that clock
    happens to be. `test_no_filename_stamp_is_built_from_a_utc_clock` reads the
    SOURCE and sees the call spelled `local_now()`, which is still true when
    `local_now` answers UTC.

    So the zone is asserted against a configured value rather than against the
    function under test. Two fixed-offset zones, neither of them the suite's own
    `Etc/GMT-4` pin, so a leftover environment cannot satisfy this by accident.
    The sign in the `Etc/GMT*` names is inverted by POSIX convention: `Etc/GMT-7`
    is UTC+7.
    """
    import sys
    from datetime import timedelta

    sys.path.insert(0, str(ROOT))
    from scripts.utils import checkpoint_paths as CP

    for zone, offset in (("Etc/GMT-7", 7), ("Etc/GMT+3", -3)):
        monkeypatch.setenv("HEADING_OS_TZ", zone)
        local = CP.local_now()
        assert local.utcoffset() == timedelta(hours=offset), (
            f"HEADING_OS_TZ={zone} but local_now() answered "
            f"{local.utcoffset()}; the filename stamp is not on the operator's "
            "clock"
        )
        # The case ON the line for the filename claim: a whole-hour offset moves
        # the %H the archive name carries, which is the 02:56-filed-as-yesterday
        # defect stated as an assertion instead of a docstring.
        assert local.strftime("%Y-%m-%d-%H") != CP.utc_now().strftime("%Y-%m-%d-%H"), (
            f"at {zone} the local and UTC stamps agree to the hour, so this "
            "assertion cannot tell the two clocks apart"
        )
    # And utc_now must NOT have moved with it - the opposite mistake.
    assert CP.utc_now().utcoffset() == timedelta(0)


def test_the_offer_floor_lands_on_a_literal_wall_clock(monkeypatch):
    """`_stamp` converts into the operator's zone, asserted against a LITERAL.

    `test_a_utc_floor_is_converted_into_the_filename_s_clock` below builds its
    expected value out of `CP.local_now().tzinfo`, so it agrees with `_stamp`
    whatever zone that is. This one pins the zone and writes the answer out, so
    the pair can no longer both be satisfied by a clock that never moved.
    """
    monkeypatch.setenv("HEADING_OS_TZ", "Etc/GMT-7")
    mod = _offer_hook()
    assert mod._stamp("2026-08-20T00:00:00+00:00") == "2026-08-20-070000"
    assert mod._stamp("2026-08-19T20:30:00Z") == "2026-08-20-033000", (
        "the Z form must convert too, including across the day boundary"
    )


def test_local_now_defers_its_import():
    """Five hooks import this module every turn; `get_default_tz` reads .env and
    costs ~50 ms cold. Only a caller that builds a filename should pay it."""
    import subprocess
    import sys

    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "from scripts.utils import checkpoint_paths as CP\n"
        "before = 'scripts.utils.workspace' in sys.modules\n"
        "CP.local_now()\n"
        "after = 'scripts.utils.workspace' in sys.modules\n"
        "print(before, after)\n" % str(ROOT)
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=ROOT, timeout=60).stdout.strip()
    assert out == "False True", f"expected deferred import, got {out!r}"


def test_no_filename_stamp_is_built_from_a_utc_clock():
    """The guard. A new `strftime("%Y-%m-%d-%H%M%S")` on a UTC source is the
    exact regression this file exists to stop."""
    offenders = []
    targets = [ROOT / "scripts" / "checkpoint-paths.py",
               ROOT / "scripts" / "utils" / "checkpoint_paths.py"]
    targets += tracked_paths((".claude/hooks/checkpoint-*.py",))

    for path in targets:
        text = path.read_text(encoding="utf-8")
        utc_vars = set(STAMP_VIA_VAR.findall(text))
        for lineno, line in enumerate(text.splitlines(), 1):
            m = STAMP_CALL.search(line)
            if m and m.group(1).split(".")[-1] == "utc_now":
                offenders.append(f"{path.name}:{lineno} {line.strip()}")
                continue
            # `now = datetime.now(timezone.utc)` … `now.strftime("%Y-%m-%d-%H%M%S")`
            m2 = re.search(r"(\w+)\.strftime\(\"%Y-%m-%d-%H%M%S\"\)", line)
            if m2 and m2.group(1) in utc_vars:
                offenders.append(f"{path.name}:{lineno} {line.strip()}")

    assert not offenders, (
        "a filename stamp is being built from a UTC clock; use local_now() so "
        "the file lands on the operator's calendar day:\n  "
        + "\n  ".join(offenders)
    )


def test_the_detector_is_not_vacuous():
    """A matcher that matches nothing passes everything."""
    sample = 'stamp = CP.utc_now().strftime("%Y-%m-%d-%H%M%S")'
    m = STAMP_CALL.search(sample)
    assert m and m.group(1) == "CP.utc_now"
    sample2 = "    now = datetime.now(timezone.utc)\n"
    assert STAMP_VIA_VAR.findall(sample2) == ["now"]


def test_stored_timestamps_stay_utc():
    """The opposite mistake. Anything written into JSON state is serialized and
    must stay UTC; moving those to a local zone would break every comparison
    against a stored value."""
    text = (ROOT / "scripts" / "utils" / "checkpoint_paths.py").read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if ".isoformat()" in line and "local_now()" in line:
            raise AssertionError(
                f"checkpoint_paths.py:{lineno} serializes a local timestamp: "
                f"{line.strip()}"
            )


# ============================================================
# The floor the archive filename is compared against
# ============================================================

def _offer_hook():
    import contextlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "checkpoint_offer", ROOT / ".claude" / "hooks" / "checkpoint-offer.py")
    mod = importlib.util.module_from_spec(spec)
    with contextlib.suppress(SystemExit):  # the hook exits when handed no payload
        spec.loader.exec_module(mod)
    return mod


def test_a_utc_floor_is_converted_into_the_filename_s_clock():
    """`last_offer_at` is stored in UTC; the archive filename is stamped local.
    Comparing the two wall clocks as strings compares different clocks."""
    mod = _offer_hook()
    from scripts.utils import checkpoint_paths as CP

    iso = "2026-08-20T00:00:00+00:00"
    expected = CP.local_now().tzinfo
    from datetime import datetime as _dt

    want = _dt.fromisoformat(iso).astimezone(expected).strftime("%Y-%m-%d-%H%M%S")
    assert mod._stamp(iso) == want
    assert mod._stamp("2026-08-20T00:00:00Z") == want, "the Z form must convert too"


def test_a_handoff_written_before_the_offer_is_not_accepted_as_after_it():
    """The failure this prevents loses work.

    `_handoff_since` gates the driven compaction on a handoff being newer than
    the last offer. Before the conversion, on a UTC+4 operator, an offer stamped
    UTC 00:00 produced the floor `2026-08-20-000000` while a handoff written an
    hour EARLIER (local 03:00, UTC 23:00 the previous day) was filed as
    `2026-08-20-030000`. String comparison said newer, the boundary fired, and
    the session's real work was never saved.
    """
    from datetime import datetime as _dt
    from datetime import timedelta

    from scripts.utils import checkpoint_paths as CP

    mod = _offer_hook()
    offer_utc = _dt.fromisoformat("2026-08-20T00:00:00+00:00")
    floor = mod._stamp(offer_utc.isoformat())

    # Both filenames are derived from the SAME instant in the SAME local zone the
    # writer uses, so the test states the property instead of a UTC+4 arithmetic
    # it would only be right about on this machine. On a CI box at UTC the offset
    # is zero and the property still has to hold.
    local = CP.local_now().tzinfo

    def name_at(delta: timedelta) -> str:
        return (offer_utc + delta).astimezone(local).strftime("%Y-%m-%d-%H%M%S")

    assert not (name_at(timedelta(hours=-1)) > floor), \
        "a handoff written an hour BEFORE the offer was accepted as after it"
    assert name_at(timedelta(hours=+1)) > floor, \
        "a handoff written an hour AFTER the offer must still be accepted"


def test_stamp_degrades_rather_than_blocking_on_input_it_cannot_zone():
    """A naive timestamp has no zone to convert from. Returning "" would set the
    floor to nothing and block the compaction forever, so the old string surgery
    stays as the fallback."""
    mod = _offer_hook()
    assert mod._stamp("2026-08-20T00:00:00") == "2026-08-20-000000"
    assert mod._stamp("not-a-time") == ""
    assert mod._stamp("") == ""
