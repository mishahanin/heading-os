"""An air gap that folded case with the wrong function, and an alert that raised.

`air_gap.is_denied` documents "case-folded: a path whose segment is `Personal`
is denied exactly as one whose segment is `personal`. Letter-case is never a
boundary." It implemented that with `str.lower()`, which is not case-folding: a
character whose fold mapping differs from its lowercase mapping passes straight
through. U+017F LATIN SMALL LETTER LONG S is the canonical instance - already
lowercase, so `.lower()` leaves it alone, while `.casefold()` maps it to `s`. A
one-character bypass of a hard-coded segment, in the module this tree calls the
single source of truth for what must never be read.

`alert._send_telegram` promises "False on any failure (missing token,
unresolvable target, transport/API error). NEVER raises", the module docstring
promises the same, and `alert()`'s own docstring says "Never raises" - over a
function with no handler at all. The callers are watchdogs, so the exception
escapes exactly when the network is already broken, which is the one moment the
contract has to hold.

`canopus_nullstub.Stub` declared any two stubs equal and hashed them by `id()`,
breaking Python's equal-implies-equal-hash invariant, so set and dict lookups
over stubs silently disagreed with the equality the vacuity reading rests on.

Nothing here touches the network or the filesystem outside `tmp_path`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.alert as alert_module
from scripts.utils.air_gap import (
    HARDCODED_DENY_PREFIXES,
    HARDCODED_DENY_SEGMENTS,
    is_denied,
)
from scripts.utils.canopus_nullstub import Stub

# U+017F LATIN SMALL LETTER LONG S. `.lower()` leaves it; `.casefold()` -> "s".
LONG_S = "ſ"


def test_the_two_case_functions_really_do_differ_here():
    """Pin the premise: without this the cases below prove nothing.

    A test that asserts a fold works is worthless if the character it feeds is
    one `.lower()` would also have handled.
    """
    folded = f"per{LONG_S}onal"
    assert folded.casefold() == "personal"
    assert folded.lower() != "personal"


def test_a_long_s_does_not_open_the_air_gap():
    """The defect, in one character."""
    assert is_denied(f"per{LONG_S}onal/todo.md") is True


@pytest.mark.parametrize(
    "rel_path",
    [
        "personal/todo.md",
        "Personal/todo.md",
        "PERSONAL/todo.md",
        "threads/personal/x.md",
        "threads/Personal/x.md",
        f"threads/per{LONG_S}onal/x.md",
        "_secure/x.md",
        "_SECURE/x.md",
        "_Secure/x.md",
    ],
)
def test_every_spelling_of_a_hard_coded_deny_is_denied(rel_path):
    assert is_denied(rel_path) is True


@pytest.mark.parametrize(
    "rel_path",
    ["threads/business/x.md", "crm/contacts/james-bond.md", "docs/ARCHITECTURE.md",
     "personality/x.md", "impersonal.md"],
)
def test_folding_did_not_widen_what_is_denied(rel_path):
    """`personality` and `impersonal` contain the segment as a SUBSTRING only."""
    assert is_denied(rel_path) is False


def test_a_caller_supplied_deny_folds_too():
    """The hard-coded set is not the only one that must survive the spelling."""
    assert is_denied(
        f"Rö{LONG_S}tered/x.md", deny_segments=("RÖSTERED",)
    ) is True
    assert is_denied("VAULT/x.md", deny_prefixes=("vault/",)) is True


def test_the_hard_coded_denies_are_still_populated():
    """A fold over an empty deny set would pass every case above vacuously."""
    assert HARDCODED_DENY_PREFIXES
    assert HARDCODED_DENY_SEGMENTS


def test_traversal_is_still_collapsed_before_the_fold():
    assert is_denied("threads/business/../../_secure/x.md") is True
    assert is_denied("../x.md") is True
    assert is_denied(".") is False


# --------------------------------------------------------------------------
# alert
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error",
    [OSError("network unreachable"), RuntimeError("api 500"),
     ValueError("bad target"), KeyError("token")],
)
def test_a_telegram_transport_error_degrades_instead_of_raising(monkeypatch, error):
    """The defect: the exception propagated through `alert()` to the watchdog."""
    monkeypatch.setattr(
        alert_module.telegram_notify, "notify",
        lambda target, message: (_ for _ in ()).throw(error),
    )

    result = alert_module.alert("critical", "daemon sentinel silent 6m",
                                source="watchdog")

    assert result["telegram"] is False
    assert result["log"] is True


def test_the_log_channel_still_fires_when_telegram_explodes(monkeypatch, caplog):
    monkeypatch.setattr(
        alert_module.telegram_notify, "notify",
        lambda target, message: (_ for _ in ()).throw(OSError("down")),
    )

    with caplog.at_level("WARNING"):
        alert_module.alert("critical", "daemon down", source="watchdog")

    assert any("telegram send failed" in r.getMessage() for r in caplog.records)
    assert any("daemon down" in r.getMessage() for r in caplog.records)


def test_a_clean_send_is_still_reported_as_sent(monkeypatch):
    """The guard must not swallow the success it exists to protect.

    `critical`, because that is the only severity that reaches the Telegram
    channel at all (`warning` is card+log) - a test on `warning` would report
    False for a reason that has nothing to do with this guard.
    """
    monkeypatch.setattr(
        alert_module.telegram_notify, "notify", lambda target, message: True
    )
    assert alert_module.alert("critical", "disk full", source="watchdog")["telegram"] is True


def test_a_refusing_send_is_still_reported_as_not_sent(monkeypatch):
    monkeypatch.setattr(
        alert_module.telegram_notify, "notify", lambda target, message: False
    )
    assert alert_module.alert("critical", "disk full", source="watchdog")["telegram"] is False


# --------------------------------------------------------------------------
# canopus_nullstub.Stub
# --------------------------------------------------------------------------

VALUES = {"len": 0, "int": 1, "bool": True, "contains": False, "item": "a"}


def test_equal_stubs_hash_equally():
    """The Python invariant the `__eq__` comment never reached."""
    first = Stub(VALUES)
    second = first._sibling()

    assert first == second
    assert hash(first) == hash(second)


def test_stub_membership_agrees_with_stub_equality():
    """The defect: `s1 == s2` was True while `s1 in {s2}` was False."""
    first = Stub(VALUES)
    second = first._sibling()

    assert first in {second}
    assert len({first, second}) == 1
    assert {first: "value"}[second] == "value"


def test_a_stub_still_loses_against_a_real_value():
    """The property the whole vacuity reading rests on, unchanged.

    A constant hash must not let `assert answer() in {42}` start passing.
    """
    stub = Stub(VALUES)
    assert (stub == 42) is False
    assert stub not in {42}
    assert stub not in {"a", "b"}
