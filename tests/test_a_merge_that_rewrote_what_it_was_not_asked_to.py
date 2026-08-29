#!/usr/bin/env python3
"""Shard 10-p2: nine findings across five tools, and one lesson about corpora.

Four of the nine sit in `merge-contacts.py`, which rewrites a whole CRM record
in order to change one field. Every defect there is therefore a silent edit to
data the operator never asked to touch:

  - a nested mapping (`address:` with indented children) had no branch at all.
    The block-list scan broke on the first non-`- ` line, the children fell
    through to the outer loop, and each was written back as a TOP-LEVEL key with
    the parent left empty. `merge_frontmatter`'s union then carried the hoisted
    keys into the target on the first merge.
  - `if ":" not in line: continue` deleted every colon-free comment and blank
    line, because the serializer rebuilds the block from the dict alone. A
    comment WITH a colon failed the other way and became a real key.
  - `last_touch` and `cadence` were assigned unconditionally from helpers that
    return None when NEITHER record carries the field, so the merged file gained
    the literal text `cadence: None` -- which re-parses as the string "none",
    which `CADENCE_RANK` scores as a genuine label at rank 8.
  - `extract_interaction_log` folded everything after the last dated entry INTO
    that entry, inverting its own comment. Because `merge_notes` then sorts
    entries by date, an unrelated `## Follow-ups` section was RELOCATED by the
    date of the entry it had been stuck to.

The fifth tool taught the lesson. The first attempt at the empty-value fix
emitted `key:` without a trailing space, on the reasoning that a trailing space
is a diff nobody asked for. The live 334-record corpus writes `timezone: ` WITH
the space in 132 records, and the existing corpus test caught it immediately.
The style is now CARRIED, the way this file already carries quote style and
block-list indent. An abstract tidiness argument lost to a measurement.

The rest:
  - `memory.py recall --layer odin "my query"` ran `query odin --layer "my
    query"` -- the flag's value searched as the query, silently.
  - `mullvad-fastest.py` let a read timeout past both of its handlers, because
    `socket.timeout` IS `TimeoutError`, an OSError SIBLING of URLError.
  - `modem-tune.py` left one SSH call per command unguarded in the post-reset
    window, where a hung session raises `subprocess.TimeoutExpired`.

Two report claims are REFUTED here with proof rather than dropped: the modem
transport does NOT raise on a refused connection, and `Path.rglob` on this
interpreter DOES suppress PermissionError. Both refutations are pinned, because
an unpinned refutation is re-found by the next audit.

Run: .venv/bin/python -m pytest tests/test_a_merge_that_rewrote_what_it_was_not_asked_to.py
"""
from __future__ import annotations

import argparse
import http.client
import importlib.util
import socket
import sys
import types
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mem = _load("scripts/memory.py", "memory_facade_10p2")
mc = _load("scripts/merge-contacts.py", "merge_contacts_10p2")
mv = _load("scripts/mullvad-fastest.py", "mullvad_10p2")
ns = _load("scripts/next-signal.py", "next_signal_10p2")
mt = _load("scripts/modem-tune.py", "modem_tune_10p2")


# ============================================================
# Finding 1 -- a flag whose value became the query
# ============================================================

def test_argparse_really_does_bind_the_flags_value_to_the_positional():
    """The premise, pinned. If a future CPython puts the operand back in
    `extras`, the guard below becomes unnecessary and this test says so."""
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("recall")
    sp.add_argument("text")
    args, extras = p.parse_known_args(["recall", "--layer", "odin", "my query"])
    assert args.text == "odin"
    assert extras == ["--layer", "my query"]


def test_a_leading_flag_on_recall_is_refused():
    msg = mem.leading_flag_error(["recall", "--layer", "odin", "my query"])
    assert msg is not None
    assert "--layer" in msg
    assert "FIRST" in msg


def test_the_refusal_shows_the_wrong_form_and_the_right_one():
    """A refusal that does not say what to type instead is a dead end."""
    msg = mem.leading_flag_error(["recall", "--top-k", "3", "q"])
    assert "wrong:" in msg and "right:" in msg


def test_the_refusal_reaches_the_exit_code(capsys):
    assert mem.main(["recall", "--top-k", "3", "sovereign deep packet"]) == 2
    assert "FIRST" in capsys.readouterr().err


def test_the_documented_order_is_not_refused():
    assert mem.leading_flag_error(["recall", "my query", "--layer", "odin"]) is None


def test_help_still_reaches_argparse():
    """`--help` is not a mis-binding; refusing it would break discovery."""
    assert mem.leading_flag_error(["recall", "--help"]) is None
    assert mem.leading_flag_error(["recall", "-h"]) is None


def test_other_subcommands_are_untouched():
    """`retire` binds a leading flag correctly (nargs='+' is greedy the other
    way), so widening the guard would refuse something that works."""
    assert mem.leading_flag_error(["retire", "--force", "a.md"]) is None
    assert mem.leading_flag_error(["status"]) is None
    assert mem.leading_flag_error(["hygiene", "--json"]) is None


def test_recall_with_no_arguments_at_all_is_left_to_argparse():
    assert mem.leading_flag_error(["recall"]) is None


# ============================================================
# Findings 2, 3, 4, 5 -- the merge that rewrote the record
# ============================================================

def _round_trip(fm_text: str) -> str:
    fm, body = mc.parse_frontmatter(fm_text)
    return mc.serialize_frontmatter(fm) + body


NESTED = """---
name: Jane
address:
  street: 1 Main St
  city: Metropolis
owner: misha
---

body
"""


def test_a_nested_mapping_survives_byte_for_byte():
    assert _round_trip(NESTED) == NESTED


def test_the_children_of_a_nested_mapping_do_not_become_top_level_keys():
    fm, _ = mc.parse_frontmatter(NESTED)
    assert "street" not in fm and "city" not in fm
    assert {k for k in fm if not k.startswith(mc.RAW_KEY)} == {
        "name", "address", "owner"}


def test_a_nested_mapping_is_not_hoisted_into_the_target_by_a_merge():
    """The propagation step: hoisted children survived the union as first-class
    keys, so one bad parse spread into the other record."""
    fm_from, _ = mc.parse_frontmatter(NESTED)
    fm_into, _ = mc.parse_frontmatter("---\nname: John\nowner: rob\n---\n\nb\n")
    merged = mc.merge_frontmatter(fm_from, fm_into, "jane", "john")
    assert "street" not in merged and "city" not in merged
    assert "address" in merged


def test_a_deeper_nesting_is_also_carried_whole():
    text = ("---\nname: X\nmeta:\n  a:\n    b: 1\n  c: 2\nowner: y\n---\n\nb\n")
    assert _round_trip(text) == text


COMMENTED = """---
name: Jane

# a plain comment with no colon
# reviewed: 2026-01
owner: misha
---

body
"""


def test_a_colon_free_comment_is_no_longer_deleted():
    assert "# a plain comment with no colon" in _round_trip(COMMENTED)


def test_a_comment_with_a_colon_does_not_become_a_key():
    fm, _ = mc.parse_frontmatter(COMMENTED)
    assert "# reviewed" not in fm


def test_a_blank_line_inside_the_frontmatter_survives():
    assert _round_trip(COMMENTED) == COMMENTED


def test_comments_are_not_imported_from_the_source_record():
    """A comment describes the record it was written in. Importing one drops it
    into the target at an arbitrary position under a synthetic key."""
    fm_from, _ = mc.parse_frontmatter(COMMENTED)
    fm_into, _ = mc.parse_frontmatter("---\nname: John\nowner: rob\n---\n\nb\n")
    merged = mc.merge_frontmatter(fm_from, fm_into, "jane", "john")
    assert not any(k.startswith(mc.RAW_KEY) for k in merged)
    assert "a plain comment" not in mc.serialize_frontmatter(merged)


def test_the_targets_own_comments_survive_the_merge():
    fm_from, _ = mc.parse_frontmatter("---\nname: John\n---\n\nb\n")
    fm_into, _ = mc.parse_frontmatter(COMMENTED)
    merged = mc.merge_frontmatter(fm_from, fm_into, "john", "jane")
    assert "# a plain comment with no colon" in mc.serialize_frontmatter(merged)


def test_an_empty_value_keeps_the_trailing_space_it_was_written_with():
    """132 of the live 334 records write it this way. The first fix normalised
    the space away and the corpus test caught it on the same run."""
    text = "---\nname: Jane\ntimezone: \nowner: m\n---\n\nb\n"
    assert _round_trip(text) == text


def test_an_empty_value_written_without_a_space_keeps_none():
    text = "---\nname: Jane\ntimezone:\nowner: m\n---\n\nb\n"
    assert _round_trip(text) == text


def test_an_explicitly_quoted_empty_string_is_not_an_empty_field():
    text = '---\nname: ""\nowner: m\n---\n\nb\n'
    assert _round_trip(text) == text


def test_an_empty_value_still_compares_equal_to_the_empty_string():
    """Nothing downstream should need to know `_Empty` exists."""
    fm, _ = mc.parse_frontmatter("---\ntimezone: \n---\n\nb\n")
    assert fm["timezone"] == ""
    assert not fm["timezone"]


def test_a_merge_with_neither_date_nor_cadence_writes_neither_key():
    merged = mc.merge_frontmatter({"name": "A"}, {"name": "B"}, "x", "y")
    out = mc.serialize_frontmatter(merged)
    assert "last_touch" not in out
    assert "cadence" not in out
    assert "None" not in out


def test_a_merge_still_carries_a_date_that_one_side_has():
    merged = mc.merge_frontmatter(
        {"last_touch": "2026-01-01"}, {"name": "B"}, "x", "y")
    assert merged["last_touch"] == "2026-01-01"


def test_a_merge_still_picks_the_shorter_cadence_interval():
    merged = mc.merge_frontmatter(
        {"cadence": "weekly"}, {"cadence": "quarterly"}, "x", "y")
    assert merged["cadence"] == "weekly"


def test_the_invented_cadence_would_have_outranked_a_real_one():
    """Why `cadence: None` was worse than a cosmetic wart: the string it
    re-parses to is a REAL label in the rank table, not an unknown."""
    assert mc.CADENCE_RANK.get("none") == 8
    assert mc.pick_higher_cadence("none", "as-needed") == "as-needed"
    assert mc.pick_higher_cadence("none", "totally-made-up") == "none", (
        "rank 8 beats the unknown-value 99, so the invented label wins")


LOGGED = (
    "pre stuff\n\n"
    "## Interaction Log\n\n"
    "### 2026-01-01\nentry one\n\n"
    "## Follow-ups\ntrailing section\n"
)


def test_a_trailing_section_is_post_log_not_part_of_the_last_entry():
    pre, entries, post, _lead = mc.extract_interaction_log(LOGGED)
    assert entries == ["### 2026-01-01\nentry one\n"]
    assert post.startswith("## Follow-ups")
    assert "Follow-ups" not in entries[0]


def test_an_entry_header_is_not_mistaken_for_a_section_header():
    """`### ` is three hashes, so the level-2 pattern cannot align with it."""
    body = ("## Interaction Log\n\n### 2026-01-01\na\n\n### 2026-02-01\nb\n")
    _, entries, post, _lead = mc.extract_interaction_log(body)
    assert len(entries) == 2
    assert post == ""


def test_a_trailing_section_is_no_longer_relocated_by_an_entrys_date():
    """The consequence that made this more than a wrong comment: the section
    rode inside a 2026-01-01 entry, and the chronological sort moved it above
    an older entry from the other record."""
    into = "## Interaction Log\n\n### 2026-01-01\nnewer\n\n## Follow-ups\nkeep me last\n"
    frm = "## Interaction Log\n\n### 2025-12-01\nolder\n"
    out = mc.merge_notes(frm, into, "a", "b")
    assert out.index("older") < out.index("newer"), "entries still sort by date"
    assert out.index("newer") < out.index("keep me last"), (
        "the trailing section was carried above an entry by a date not its own")


def test_a_dated_line_below_a_later_section_is_not_pulled_back_into_the_log():
    """The bound has to filter the ENTRY SCAN too, not only the slice end.

    A `### 2026-03-01` line inside a `## Follow-ups` section is not a log entry.
    Collecting it anyway made the first entry run all the way to it (swallowing
    the whole section), produced an empty second entry, and then returned the
    same text AGAIN in post_log -- so a merge duplicated it.
    """
    body = (
        "## Interaction Log\n\n"
        "### 2026-01-01\nentry one\n\n"
        "## Follow-ups\nnotes\n\n"
        "### 2026-03-01\nnot a log entry\n"
    )
    _, entries, post, _lead = mc.extract_interaction_log(body)
    assert entries == ["### 2026-01-01\nentry one\n"]
    assert "Follow-ups" not in entries[0]
    assert post.count("2026-03-01") == 1, "the trailing block was duplicated"
    assert "notes" in post


def test_a_log_with_no_entries_still_keeps_its_content():
    body = "## Interaction Log\n\nfreeform note, no dated entries\n"
    _, entries, post, _lead = mc.extract_interaction_log(body)
    assert entries == []
    assert "freeform note" in post


def test_a_record_with_no_log_header_is_returned_untouched():
    pre, entries, post, _lead = mc.extract_interaction_log("just a body\n")
    assert (pre, entries, post) == ("just a body\n", [], "")


# ============================================================
# Findings 6 and 7 -- the SSH calls in the post-reset window
# ============================================================

def test_the_modem_transport_does_not_raise_on_a_refused_connection():
    """REFUTES finding 7's stated mechanism, and half of finding 6's.

    `modem_ssh.ssh` runs `subprocess.run` with no `check=True` and never reads
    `returncode`, so ssh exiting 255 on a refused connection returns its stderr
    as an ordinary string. The report built both findings on a raise that does
    not happen.
    """
    import subprocess
    src = (ROOT / "scripts/utils/modem_ssh.py").read_text(encoding="utf-8")
    assert "check=True" not in src
    assert subprocess.run(["false"], capture_output=True).returncode == 1, (
        "subprocess.run without check=True does not raise on a non-zero exit")


@pytest.fixture
def modem_ctx(monkeypatch):
    """cmd_verify / cmd_reset with no router, no ledger write and no sleeping."""
    monkeypatch.setattr(mt.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mt.mc, "save_ledger", lambda *a, **k: None)
    calls = {"read": 0, "reboot": 0, "waited": False}

    class Drv:
        def read_imei(self):
            calls["read"] += 1
            if calls["read"] < 3:
                raise __import__("subprocess").TimeoutExpired(cmd="ssh", timeout=15)
            return "123456789012345"

    monkeypatch.setattr(mt, "_device_ctx",
                        lambda args: ("e5800", "10.0.0.1", Drv(), {}))

    def waited(drv, settle):
        calls["waited"] = True
        return True
    monkeypatch.setattr(mt, "_wait_for_router", waited)
    return calls


def test_verify_survives_a_hung_ssh_and_keeps_retrying(modem_ctx, capsys):
    """A TimeoutExpired on attempt 1 of 30 used to escape as a traceback and
    throw away the other 29 -- and the ledger update they exist to reach."""
    rc = mt.cmd_verify(types.SimpleNamespace(device=None, expect="123456789012345"))
    assert rc == 0
    assert modem_ctx["read"] == 3
    assert "IMEI read attempt failed" in capsys.readouterr().err


def test_verify_survives_a_device_that_has_no_current_imei_yet():
    """NOT in the audit report; surfaced by the fixture above.

    `device_ledger` initialises a new device with `"current": None`, so the key
    is PRESENT and `dled.get("current", {})` returns None rather than the
    default. `.get("imei")` on that raised AttributeError right after a
    successful read -- the verify had already worked, and the command still
    ended in a traceback.
    """
    from scripts.utils import modem_core
    led = {"devices": {}, "used": []}
    dled = modem_core.device_ledger(led, "e5800", "")
    assert dled["current"] is None
    assert dled.get("current", {}) is None, (
        "the premise: a present-but-null key never reaches the default")
    assert (dled.get("current") or {}).get("imei") is None


def test_verify_still_fails_when_the_imei_never_matches(monkeypatch, capsys):
    """The guard must not turn a real mismatch into a pass."""
    monkeypatch.setattr(mt.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mt.mc, "save_ledger", lambda *a, **k: None)

    class Drv:
        def read_imei(self):
            return "999999999999999"
    monkeypatch.setattr(mt, "_device_ctx",
                        lambda args: ("e5800", "10.0.0.1", Drv(), {}))
    rc = mt.cmd_verify(types.SimpleNamespace(device=None, expect="123456789012345"))
    assert rc == 1
    assert "Mismatch" in capsys.readouterr().err


def test_reset_still_waits_for_the_router_when_the_reboot_call_hangs(
        modem_ctx, monkeypatch, capsys):
    """The reboot is sent over the session it kills. A hang past the 15s bound
    used to skip the wait entirely, failing the command on the router that
    rebooted hardest."""
    import subprocess

    def boom(host):
        def _call(cmd, timeout=15):
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout)
        return _call
    monkeypatch.setattr(mt, "_ssh_for", boom)

    assert mt.cmd_reset(types.SimpleNamespace(device=None)) == 0
    assert modem_ctx["waited"] is True
    assert "did not return cleanly" in capsys.readouterr().err


def test_the_wait_docstring_no_longer_names_a_mechanism_the_transport_lacks():
    """A comment that states a coverage claim the code cannot deliver is the
    same defect class the audit exists to find, so the fix is not just code."""
    doc = " ".join(mt._wait_for_router.__doc__.split())
    assert "a REFUSED connection does not raise" in doc
    assert "TimeoutExpired" in doc
    assert "SSH is refused while the router is still booting; that raises" not in doc


# ============================================================
# Finding 8 -- the timeout that was nobody's URLError
# ============================================================

def test_socket_timeout_is_a_sibling_of_urlerror_not_a_subclass():
    """The premise, pinned. Both are OSError children, so neither handler
    catches the other."""
    assert socket.timeout is TimeoutError
    assert issubclass(TimeoutError, OSError)
    assert issubclass(urllib.error.URLError, OSError)
    assert not issubclass(TimeoutError, urllib.error.URLError)


def _urlopen_raising(exc):
    def _fake(req, timeout=None):
        raise exc
    return _fake


def test_a_read_timeout_becomes_the_scripts_own_clean_error(monkeypatch):
    monkeypatch.setattr(mv.urllib.request, "urlopen",
                        _urlopen_raising(TimeoutError("timed out")))
    with pytest.raises(RuntimeError) as e:
        mv.fetch_relays()
    assert "Could not read from the Mullvad API" in str(e.value)


def test_a_truncated_body_becomes_the_scripts_own_clean_error(monkeypatch):
    """IncompleteRead is an HTTPException, not an OSError at all, so widening
    the handler to OSError alone would still have missed it."""
    monkeypatch.setattr(mv.urllib.request, "urlopen",
                        _urlopen_raising(http.client.IncompleteRead(b"ab", 99)))
    with pytest.raises(RuntimeError) as e:
        mv.fetch_relays()
    assert "cut short" in str(e.value)


def test_the_http_and_url_handlers_still_answer_first(monkeypatch):
    """Both are OSError subclasses, so a broad OSError handler placed above
    them would have swallowed their named messages."""
    monkeypatch.setattr(mv.urllib.request, "urlopen", _urlopen_raising(
        urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)))
    with pytest.raises(RuntimeError) as e:
        mv.fetch_relays()
    assert "HTTP 503" in str(e.value)

    monkeypatch.setattr(mv.urllib.request, "urlopen", _urlopen_raising(
        urllib.error.URLError("name resolution failed")))
    with pytest.raises(RuntimeError) as e:
        mv.fetch_relays()
    assert "Could not reach" in str(e.value)


# ============================================================
# Finding 9 -- REFUTED, and the narrower hole behind it
# ============================================================

def test_rglob_on_this_interpreter_does_suppress_permission_denied(tmp_path):
    """REFUTES the report. It claimed a permission-denied subdirectory raises
    out of `rglob` on Python <= 3.12. CPython 3.11 catches PermissionError in
    `_iterate_directories`; 3.13 only WIDENED that to all OSError.
    """
    if sys.version_info >= (3, 13):
        pytest.skip("3.13+ suppresses all OSError, so this proves nothing")
    (tmp_path / "open").mkdir()
    (tmp_path / "open" / "f.md").write_text("x", encoding="utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.md").write_text("y", encoding="utf-8")
    locked.chmod(0o000)
    try:
        names = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    finally:
        locked.chmod(0o755)
    assert names == ["f.md"], "rglob raised instead of skipping the locked tree"


def test_an_os_error_from_the_walk_exits_cleanly_instead_of_tracebacking(
        monkeypatch, capsys):
    """The narrower hole the report walked past. 3.11 suppresses PermissionError
    only, so a vanished directory, a symlink loop or an I/O error still comes
    out of `gather` -- and the handler caught FileNotFoundError alone."""
    def boom(root, limit):
        raise OSError(40, "Too many levels of symbolic links")
    monkeypatch.setattr(ns, "gather", boom)
    rc = ns.main(["--limit", "3"])
    assert rc == 1
    assert "symbolic links" in capsys.readouterr().err


def test_the_missing_outputs_directory_still_reports_itself(monkeypatch, capsys):
    def boom(root, limit):
        raise FileNotFoundError("outputs/ not found at /nowhere")
    monkeypatch.setattr(ns, "gather", boom)
    assert ns.main([]) == 1
    assert "outputs/ not found" in capsys.readouterr().err
