"""The session-start CRM alert must count contacts, not section headers.

`check_crm_health` ran `scripts/crm-health.py` and kept every line matching
`"RED" in line`. The radar prints one header, `RED - Overdue`, and then the
overdue contacts indented under it. A contact line reads

    Alpha Person (Alpha Co) - prospect - 2426 days (cadence: 14)

and contains no RED. So the filter matched the HEADER and nothing else, and
`main()` renders `len(red_contacts)` as the number:

    alerts.append(f"CRM ALERT: {len(red_contacts)} contact(s) need attention today")

Measured 2026-08-23 against a three-overdue fixture: the hook reported 1. It had
been reporting "1 contact(s)" for every non-empty case, however many were
actually overdue, at the top of every session.

The audit found the substring was unanchored (REDACTED, REDMOND, a contact named
FRED would count) and stopped there, which is the smaller half. The larger half
is that the intended lines were never matched at all.

Second defect in the same function, also from that audit: the cache was written
with `os.replace(tmp, cache)` and only then `os.chmod(cache, 0o600)`. Between
those two calls the file sat at the default umask, commonly 0644, holding CRM
contact lines. `os.replace` preserves the source file's mode, so chmod-ing the
temp file first closes the window entirely.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "session-start.py"


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_start_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


RADAR_THREE = """\x1b[1m31C Relationship Radar\x1b[0m

\x1b[91m\x1b[1mRED - Overdue\x1b[0m
  \x1b[91mAlpha Person\x1b[0m (Alpha Co) - prospect - 2426 days (cadence: 14)
  \x1b[91mBeta Person\x1b[0m (Beta Co) - prospect - 2426 days (cadence: 14)
  \x1b[91mGamma Person\x1b[0m (Gamma Co) - prospect - 2426 days (cadence: 14)

\x1b[93m\x1b[1mYELLOW - Approaching\x1b[0m
  \x1b[93mDelta Person\x1b[0m (Delta Co) - partner - 79 days (cadence: 90)

\x1b[1mTotal:\x1b[0m 4 contacts tracked | 3 red | 1 yellow | 0 green
"""

RADAR_NONE = """\x1b[1m31C Relationship Radar\x1b[0m

\x1b[92m\x1b[1mGREEN - On Track\x1b[0m
  \x1b[92mRedmond Fredricks\x1b[0m (REDACTED Holdings) - partner - 1 days (cadence: 30)

\x1b[1mTotal:\x1b[0m 1 contacts tracked | 0 red | 0 yellow | 1 green
"""


def test_three_overdue_contacts_count_as_three(hook):
    found = hook._red_contacts(RADAR_THREE)
    assert len(found) == 3, (
        f"counted {len(found)} overdue contacts out of three: {found}"
    )


def test_the_section_header_is_not_counted_as_a_contact(hook):
    for line in hook._red_contacts(RADAR_THREE):
        assert not line.startswith("RED - "), (
            f"the header was returned as a contact: {line!r}"
        )


def test_the_yellow_section_is_not_swept_in(hook):
    """The parser walks forward from the RED header, so it must stop at the
    blank line rather than running on into YELLOW."""
    found = hook._red_contacts(RADAR_THREE)
    assert not any("Delta" in line for line in found), found


def test_ansi_codes_are_stripped(hook):
    for line in hook._red_contacts(RADAR_THREE):
        assert "\x1b[" not in line, f"escape codes reached the alert text: {line!r}"


def test_a_contact_whose_name_contains_red_is_not_an_alert(hook):
    """The unanchored substring the audit named. `Redmond`, `Fredricks` and
    `REDACTED` all sit in a GREEN section here."""
    assert hook._red_contacts(RADAR_NONE) == []


def test_no_red_section_means_no_alert(hook):
    assert hook._red_contacts("31C Relationship Radar\n\nTotal: 0 contacts\n") == []
    assert hook._red_contacts("") == []


# --- against the real script, not just a fixture ------------------------------

def test_the_parser_matches_the_real_radar_output(hook, tmp_path):
    """A fixture can drift from the tool it imitates. Run crm-health.py for
    real against a three-overdue data root and count what comes back."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    for name in ("Alpha", "Beta", "Gamma"):
        (contacts / f"{name}.md").write_text(
            "---\n"
            f"name: {name} Person\n"
            f"company: {name} Co\n"
            "tier: prospect\n"
            "relationship_type: prospect\n"
            "last_touch: 2020-01-01\n"
            "---\ndemo\n",
            encoding="utf-8",
        )
    env = dict(os.environ, HEADING_OS_DATA=str(tmp_path))
    proc = subprocess.run([sys.executable, "scripts/crm-health.py"],
                          cwd=str(ROOT), capture_output=True, text=True,
                          env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "RED" in proc.stdout, f"the fixture produced no RED section: {proc.stdout!r}"
    assert len(hook._red_contacts(proc.stdout)) == 3


# --- the cache must never be briefly world-readable ---------------------------

def test_the_cache_is_chmodded_before_the_replace():
    src = HOOK.read_text(encoding="utf-8")
    chmod = src.index("os.chmod(tmp_path, 0o600)")
    replace = src.index("os.replace(tmp_path, cache_file)")
    assert chmod < replace, (
        "the CRM cache is chmodded after os.replace, leaving it at the default "
        "umask while it holds contact lines"
    )
    assert "os.chmod(cache_file, 0o600)" not in src, (
        "the post-replace chmod is back; os.replace already carries the temp "
        "file's mode, so a second chmod means the temp file was left open"
    )


def test_the_live_cache_if_present_is_not_group_or_world_readable():
    cache = ROOT / ".sessions" / "crm-health-cache.json"
    if not cache.is_file():
        pytest.skip("no CRM cache on this clone")
    mode = stat.S_IMODE(cache.stat().st_mode)
    assert mode & 0o077 == 0, f"{cache} is mode {oct(mode)}"


def test_the_cache_key_changed_with_the_format():
    """A cache written by the old header-matching filter must not be read back
    by the new parser; the key rename forces a regeneration."""
    src = HOOK.read_text(encoding="utf-8")
    assert '"red_contacts"' in src
    assert '"red_lines"' not in src, (
        "the old cache key is back, so a stale cache holding the header line "
        "would be served as a contact list"
    )


# --- the hook must actually USE the parser ------------------------------------
#
# The first version of this file tested `_red_contacts` alone. Reverting the CALL
# SITE back to the old `"RED" in line` comprehension left all ten tests green,
# because none of them went through `check_crm_health`. A helper nobody calls is
# not a fix. Both tests below close that gap: one reads the call site, one runs
# the whole function.

def test_check_crm_health_calls_the_parser():
    src = HOOK.read_text(encoding="utf-8")
    body = src[src.index("def check_crm_health("):]
    body = body[:body.index("\ndef ", 1)]
    assert "_red_contacts(output)" in body, (
        "check_crm_health no longer calls _red_contacts, so the parser is dead "
        "code and the old header-matching filter is back"
    )
    assert '"RED" in line' not in body, (
        "the unanchored substring filter is back inside check_crm_health"
    )


def test_check_crm_health_end_to_end_counts_three(hook, tmp_path, monkeypatch):
    """Through the real function: three overdue contacts, three in the alert."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    for name in ("Alpha", "Beta", "Gamma"):
        (contacts / f"{name}.md").write_text(
            "---\n"
            f"name: {name} Person\n"
            f"company: {name} Co\n"
            "tier: prospect\n"
            "relationship_type: prospect\n"
            "last_touch: 2020-01-01\n"
            "---\ndemo\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    # The function caches into <project_dir>/.sessions/, and project_dir also
    # locates the real `scripts/crm-health.py` and sets cwd, so the live root is
    # the only value that works here. Move the cache aside rather than reading
    # it into memory and deleting it: a rename is atomic, the operator's bytes
    # are never held only in this process, and a kill mid-test leaves the
    # sidecar on disk beside the original instead of nothing at all.
    #
    # The sidecar carries this process's pid, because the operator runs parallel
    # sessions and a fixed name would have two of them fighting over one file.
    cache = ROOT / ".sessions" / "crm-health-cache.json"
    sidecar = cache.with_name(f"{cache.name}.testbak-{os.getpid()}")
    moved = False
    if cache.is_file():
        os.replace(cache, sidecar)
        moved = True
    try:
        result = hook.check_crm_health(str(ROOT))
        assert result is not None, "three overdue contacts produced no alert"
        assert len(result) == 3, f"alert counted {len(result)}: {result}"
    finally:
        if moved:
            os.replace(sidecar, cache)
            os.chmod(cache, 0o600)
        elif cache.is_file():
            cache.unlink()


# --- two more fail-toward-silence defects in the same hook ---------------------

def test_the_stale_date_parser_survives_trailing_punctuation(hook, tmp_path,
                                                             monkeypatch):
    """`part.strip()` removes whitespace only. A line ending
    "Last verified: 2026-01-01." yielded the token "2026-01-01.", strptime
    raised, and the file was silently never flagged stale. Latent when found
    (no context file carries one today), and a freshness alarm that fails toward
    silence is the worst way for one to fail."""
    context = tmp_path / "context"
    context.mkdir()
    (context / "plain.md").write_text("Last verified: 2020-01-01\n", encoding="utf-8")
    (context / "dotted.md").write_text("Last verified: 2020-01-01.\n", encoding="utf-8")
    (context / "comma.md").write_text("Last verified: 2020-01-01, still true\n",
                                      encoding="utf-8")
    (context / "parens.md").write_text("Last verified: (2020-01-01)\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))

    flagged = {name for name, _days, _sev in hook.check_stale_files(
        str(tmp_path), {"type": "ceo-workspace"})}
    for expected in ("plain.md", "dotted.md", "comma.md", "parens.md"):
        assert expected in flagged, (
            f"{expected} carries a six-year-old date and was not flagged stale; "
            f"flagged={sorted(flagged)}"
        )


def test_the_update_marker_is_written_atomically():
    """`.sync/last-update.json` was rewritten with a plain open(w). A crash
    mid-write truncates it, the read at the top then raises forever, and the
    notification can never be delivered. The global atomic-state-write rule
    requires tmp + os.replace."""
    src = HOOK.read_text(encoding="utf-8")
    body = src[src.index('update["notified"] = True'):]
    body = body[:body.index("except Exception")]
    assert "os.replace(" in body, (
        "the update marker is written without tmp + os.replace"
    )
    assert 'open(update_file, "w"' not in body, (
        "the marker is still opened for writing in place"
    )
