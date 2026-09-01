"""Three directory walkers that raised on a file they could not decode.

`Path.read_text(encoding="utf-8")` raises `UnicodeDecodeError`, which is a
`ValueError` and NOT an `OSError`, so `except OSError` does not catch it.
`sources/library.py` learned that once and says so in a comment: "one note
saved in Latin-1 used to abort the walk and 500 /library". Three walkers that
read operator-authored markdown the same way never got the same fix, and each
one's own single-file reader (`read_thread`, `read_contact`) already catches
both. The fix landed in the reader and not in the walker, which is this
repository's recurring "a fix that landed in one of two copies".

MEASURED 2026-08-31 against the pre-fix tree, one Latin-1 title per fixture:

    list_active_threads    -> UnicodeDecodeError: 'utf-8' codec can't decode
                              byte 0xe9 in position 22: invalid continuation byte
    threads_state_preview  -> UnicodeDecodeError (same byte)
    pulse_data             -> UnicodeDecodeError (same byte)
    list_tribe             -> UnicodeDecodeError: ... byte 0xe9 in position 62

`pulse_data` is the worst of the four: it calls `threads_state_preview` with no
`try` of its own (`raise_progress` and `tribe_state_preview` beside it both
guard internally), so ONE thread file that is not UTF-8 took the entire /pulse
payload down and not merely the threads card. The good thread and the good
contact in each fixture were lost with it.

Why the old tests could not see it: every fixture in `test_sources_threads.py`,
`test_sources_tribe.py` and `test_sources_pulse.py` is written with
`write_text(..., encoding="utf-8")`, so no test in `tests/bridge/` had ever
handed these walkers a byte sequence that is not valid UTF-8. A guard clause
that names only `OSError` reads as complete until something feeds it the other
error. `write_bytes` is the whole trick.

The correct behaviour is the one `library.py` already had: skip the file, keep
walking, and SAY SO in the log, because an operator file dropping out of a view
they read to make decisions must not be silent. The library case is asserted
here too, as the control: it is the copy that was already right, and if it ever
regresses this file names it.
"""
import logging
from pathlib import Path

import pytest

from scripts.bridge_daemon.sources.library import list_library
from scripts.bridge_daemon.sources.pulse import pulse_data, threads_state_preview
from scripts.bridge_daemon.sources.threads import list_active_threads
from scripts.bridge_daemon.sources.tribe import list_tribe

# A title carrying an e-acute encoded Latin-1: byte 0xe9, which is never a
# valid standalone UTF-8 sequence. This is what a file saved by a Windows
# editor with a legacy code page looks like on disk.
LATIN1_TITLE = "Caf\xe9 negotiation"


def _thread(root: Path, slug: str, *, title: str, latin1: bool = False) -> Path:
    p = root / "threads" / "business" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = (f"---\nid: {slug}\ntitle: {title}\nstatus: active\n"
            f"last_touched: '2026-08-30'\n---\n\nbody\n")
    if latin1:
        p.write_bytes(text.encode("latin-1"))
    else:
        p.write_text(text, encoding="utf-8")
    return p


def _contact(root: Path, slug: str, *, name: str, latin1: bool = False) -> Path:
    p = root / "crm" / "contacts" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = (f"---\nrelationship_type: tribe\nlast_touch: 2026-08-30\n---\n\n"
            f"# {name} ({slug})\n\nBody.\n")
    if latin1:
        p.write_bytes(text.encode("latin-1"))
    else:
        p.write_text(text, encoding="utf-8")
    return p


def _note(root: Path, name: str, *, title: str, latin1: bool = False) -> Path:
    p = root / "knowledge" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\ntitle: {title}\ntype: principle\nupdated: 2026-08-30\n---\n\nbody\n"
    if latin1:
        p.write_bytes(text.encode("latin-1"))
    else:
        p.write_text(text, encoding="utf-8")
    return p


def test_a_thread_that_is_not_utf8_does_not_take_the_listing_down(tmp_path, caplog):
    """/threads keeps the readable threads and names the one it dropped."""
    _thread(tmp_path, "good", title="Live negotiation")
    bad = _thread(tmp_path, "legacy", title=LATIN1_TITLE, latin1=True)

    with caplog.at_level(logging.WARNING):
        got = list_active_threads(tmp_path)

    assert got["total"] == 1
    assert [t["id"] for t in got["threads"]] == ["good"]
    assert any(bad.name in r.getMessage() for r in caplog.records), caplog.text


def test_the_pulse_threads_card_survives_the_same_file(tmp_path, caplog):
    """The sibling walker in pulse.py, which had the identical guard."""
    _thread(tmp_path, "good", title="Live negotiation")
    _thread(tmp_path, "legacy", title=LATIN1_TITLE, latin1=True)

    with caplog.at_level(logging.WARNING):
        card = threads_state_preview(tmp_path)

    assert card is not None
    assert card["active_total"] == 1
    assert [t["id"] for t in card["threads"]] == ["good"]


def test_the_whole_pulse_payload_survives_the_same_file(tmp_path):
    """The blast radius that made this HIGH rather than a card-level bug.

    `pulse_data` wraps most sub-sources and does NOT wrap
    `threads_state_preview`, so the raise reached the endpoint. Asserting the
    payload (not the card) is what pins that: a future refactor that re-guards
    the card but leaves another unguarded walker in `pulse_data` still fails
    here.
    """
    _thread(tmp_path, "good", title="Live negotiation")
    _thread(tmp_path, "legacy", title=LATIN1_TITLE, latin1=True)

    payload = pulse_data(tmp_path)

    assert payload["kpi"]["threads_state"]["active_total"] == 1


def test_a_contact_that_is_not_utf8_does_not_take_the_tribe_down(tmp_path, caplog):
    """/tribe keeps the readable contacts and names the one it dropped."""
    _contact(tmp_path, "james-bond", name="James Bond")
    bad = _contact(tmp_path, "legacy-person", name=LATIN1_TITLE, latin1=True)

    with caplog.at_level(logging.WARNING):
        got = list_tribe(tmp_path)

    assert [m["slug"] for m in got["members"]] == ["james-bond"]
    assert got["counts"] == {"tribe": 1}
    assert any(bad.name in r.getMessage() for r in caplog.records), caplog.text


def test_the_library_walker_is_still_the_copy_that_was_right(tmp_path):
    """Control. `library.py` already caught both; nothing here may regress it."""
    _note(tmp_path, "ok.md", title="A readable note")
    _note(tmp_path, "legacy.md", title=LATIN1_TITLE, latin1=True)

    got = list_library(tmp_path)

    assert got["total"] == 1
    assert [n["title"] for n in got["notes"]] == ["A readable note"]


@pytest.mark.parametrize("walker", ["threads", "tribe", "library"])
def test_an_undecodable_file_is_the_only_thing_lost(tmp_path, walker):
    """The case ON the line, from the other side: a walk whose ONLY file is
    undecodable returns the empty payload rather than raising.

    Without this, a guard that skipped the bad file but crashed on an empty
    result set would still pass every test above.
    """
    if walker == "threads":
        _thread(tmp_path, "legacy", title=LATIN1_TITLE, latin1=True)
        assert list_active_threads(tmp_path)["total"] == 0
        assert threads_state_preview(tmp_path) is None
    elif walker == "tribe":
        _contact(tmp_path, "legacy-person", name=LATIN1_TITLE, latin1=True)
        assert list_tribe(tmp_path)["members"] == []
    else:
        _note(tmp_path, "legacy.md", title=LATIN1_TITLE, latin1=True)
        assert list_library(tmp_path)["total"] == 0
