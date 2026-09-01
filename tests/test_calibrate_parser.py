"""Unit tests for scripts/calibrate.py (the JSONL parser).

Covers cases listed in plans/2026-05-13-calibrate-skill.md Phase 1 Success Criterion:
  - newest-session selection by mtime
  - --session override
  - exit codes (0 ok, 2 no session, 3 unreadable)
  - malformed lines tolerated
  - envelope schema completeness
  - tool_errors / system_reminders extraction
  - --since-utc filter
  - --max-bytes truncation
  - workspace block enumeration
  - hidden-character cleanliness of stdout
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "calibrate.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "calibrate"


# ---------- subprocess helper for CLI tests ----------

def run_parser(*args):
    """Run calibrate.py with given args, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(SCRIPT), "--no-workspace", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return result.returncode, result.stdout, result.stderr


# ---------- session location ----------

def test_locate_newest_session_by_mtime(tmp_path):
    """Parser picks the newest .jsonl by mtime when --session is not given."""
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    older.write_text('{"type":"user","timestamp":"2026-05-13T10:00:00Z","message":{"role":"user","content":"older"}}\n', encoding="utf-8")
    newer.write_text('{"type":"user","timestamp":"2026-05-13T10:00:00Z","message":{"role":"user","content":"newer"}}\n', encoding="utf-8")
    # The ordering this test is about is STATED, not raced for. A 50 ms sleep
    # was the only thing separating the two mtimes, and 50 ms is not a
    # separation on a filesystem with 1 s or 2 s timestamp granularity (FAT,
    # exFAT, some network mounts, some CI tmpfs): both files then share an
    # mtime and "newest" is whatever the directory order happens to be.
    # os.utime takes the clock out of it and removes the sleep with it.
    now = time.time()
    os.utime(older, (now - 3600, now - 3600))
    os.utime(newer, (now, now))
    rc, out, err = run_parser("--sessions-dir", str(tmp_path))
    assert rc == 0, f"stderr: {err}"
    envelope = json.loads(out)
    assert envelope["session_path"].endswith("newer.jsonl"), envelope


# ---------- envelope filtering via --session override ----------

def test_session_override_via_flag():
    """--session <path> overrides auto-detection."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0, err
    env = json.loads(out)
    assert env["session_path"].endswith("simple-correction.jsonl")
    assert env["event_count"] == 5
    assert len(env["user_turns"]) == 2
    assert len(env["assistant_turns"]) == 2
    assert len(env["system_reminders"]) == 1


# ---------- exit codes ----------

def test_no_session_exits_code_2(tmp_path):
    """Empty sessions dir -> exit code 2."""
    rc, out, err = run_parser("--sessions-dir", str(tmp_path))
    assert rc == 2
    assert "no session JSONL found" in err


# ---------- malformed line tolerance ----------

def test_malformed_jsonl_lines_skipped():
    """Malformed lines logged to stderr, parsing continues."""
    fixture = FIXTURES / "malformed.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    assert "skipped 2 malformed" in err
    env = json.loads(out)
    assert env["event_count"] == 5  # 7 total lines, 2 malformed
    assert len(env["user_turns"]) == 3


# ---------- tool_errors extraction ----------

def test_tool_errors_extracted():
    """tool_result BLOCKS flagged is_error -> tool_errors array.

    The fixture used to model tool activity as top-level JSONL events carrying
    `exit_code` and `stderr` keys, and `build_envelope` dispatched on exactly
    that. Claude Code has never written that shape: measured 2026-08-27 across
    all 1115 transcripts under ~/.claude/projects, zero such events exist, while
    one session's transcript alone carried 13,503 tool_use blocks and 192 failed
    tool_result blocks. So this test passed on invented data while the array was
    always empty in production, and /calibrate's "Errors / friction" section -
    which its own reference file calls the highest-quality category, because the
    signal is structured - reported nothing, every run.

    Both the fixture and the parser now use the real shape.
    """
    fixture = FIXTURES / "tool-errors.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    env = json.loads(out)
    errors = env["tool_errors"]
    assert len(errors) == 3, errors
    assert errors[0]["exit_code"] == 2
    assert errors[0]["tool"] == "Bash"
    assert "linkedin_archive.py" in errors[0]["stderr"]
    # cmd comes from the tool_use block with the MATCHING id, not from "the last
    # command seen for this tool name": two Bash calls in one turn used to
    # attribute the second command to the first result.
    assert errors[0]["cmd"] == "python scripts/linkedin_archive.py"
    assert errors[1]["cmd"] == "python scripts/missing.py"
    # A non-Bash failure carries no exit code anywhere in the transcript. None
    # says so; 0 would read as success.
    assert errors[2]["tool"] == "Read"
    assert errors[2]["exit_code"] is None, errors[2]
    assert errors[2]["stderr"] == "File does not exist.", errors[2]


def test_a_successful_tool_result_is_not_an_error():
    """Anchor. The fixture's middle call succeeds, so a parser that recorded
    every tool_result would report four errors instead of three."""
    fixture = FIXTURES / "tool-errors.jsonl"
    rc, out, _ = run_parser("--session", str(fixture))
    assert rc == 0
    env = json.loads(out)
    assert all("linkedin-archive.py" not in e["cmd"] for e in env["tool_errors"]), (
        env["tool_errors"]
    )


def test_the_parser_reads_the_shape_this_machine_actually_writes():
    """Read a REAL transcript, not a fixture, and require the branch to fire.

    A fixture is an assertion about the world, and this one was wrong for
    months. This test asks the world directly: it walks the newest transcripts
    on disk, finds one containing a failed tool_result block, and requires
    build_envelope to extract at least that many errors from it.

    Skipped where no transcript is available (a fresh clone, or CI).
    """
    import importlib.util

    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        pytest.skip("no Claude Code transcripts on this machine")
    spec = importlib.util.spec_from_file_location("calibrate_real", SCRIPT)
    cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cal)

    candidates = sorted(projects.rglob("*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:40]
    for path in candidates:
        try:
            events, _ = cal.parse_jsonl(path)
        except OSError:
            continue
        expected = sum(
            1
            for ev in events
            for b in (cal._message_content(ev) if isinstance(cal._message_content(ev), list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")
        )
        if expected < 1:
            continue
        env = cal.build_envelope(path, events)
        assert len(env["tool_errors"]) == expected, (
            f"{path.name}: the transcript carries {expected} failed tool_result "
            f"blocks and the parser extracted {len(env['tool_errors'])}"
        )
        assert any(e["tool"] for e in env["tool_errors"]), (
            "every extracted error has an empty tool name, so the tool_use "
            "pairing is not working on real data"
        )
        return
    pytest.skip("no recent transcript on this machine carries a failed tool call")


# ---------- a byte that is not UTF-8 ----------

def test_a_transcript_with_one_bad_byte_is_still_parsed(tmp_path):
    """`parse_jsonl` promises to tolerate malformed lines; one byte killed it.

    MEASURED 2026-09-01 against the unfixed parser: a transcript whose second
    line carries a single 0xff exited 1 with a `UnicodeDecodeError` traceback
    raised out of `for lineno, line in enumerate(fh)` - the decode happens inside
    the READ, so it never reaches the `json.JSONDecodeError` handler two lines
    down, and `main`'s `except (PermissionError, FileNotFoundError)` is not on
    its ancestry either. `UnicodeDecodeError` is a `ValueError`.

    A transcript is the whole input to /calibrate. Losing every turn of a session
    over one byte, with a traceback rather than the documented exit 3, is the
    worst of the available answers; the parser already knows how to carry on past
    a line it cannot use.
    """
    fixture = tmp_path / "one-bad-byte.jsonl"
    fixture.write_bytes(
        b'{"type":"user","timestamp":"2026-01-01T00:00:00Z",'
        b'"message":{"role":"user","content":"first turn"}}\n'
        b'{"type":"user","timestamp":"2026-01-01T00:00:01Z",'
        b'"message":{"role":"user","content":"' + b"\xff" + b'"}}\n'
        b'{"type":"user","timestamp":"2026-01-01T00:00:02Z",'
        b'"message":{"role":"user","content":"third turn"}}\n'
    )
    rc, out, err = run_parser("--session", str(fixture))
    assert "UnicodeDecodeError" not in err, err
    assert rc == 0, err
    env = json.loads(out)
    texts = [t["text"] for t in env["user_turns"]]
    assert "first turn" in texts and "third turn" in texts, (
        f"turns on the clean lines were lost with the bad byte: {texts}"
    )


def test_a_bad_byte_costs_at_most_its_own_line(tmp_path):
    """The bound, stated. Salvage is not "some of it survived": every line the
    file holds is still accounted for, as an event or as a skipped line."""
    fixture = tmp_path / "one-bad-byte.jsonl"
    fixture.write_bytes(
        b'{"type":"user","timestamp":"2026-01-01T00:00:00Z",'
        b'"message":{"role":"user","content":"first turn"}}\n'
        b'{"type":"user","timestamp":"2026-01-01T00:00:01Z",'
        b'"message":{"role":"user","content":"' + b"\xff" + b'"}}\n'
    )
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0, err
    env = json.loads(out)
    skipped = 0
    if "skipped" in err:
        skipped = int(re.search(r"skipped (\d+) malformed", err).group(1))
    assert env["event_count"] + skipped == 2, (
        f"{env['event_count']} events plus {skipped} skipped does not account "
        "for the file's 2 lines"
    )


def test_a_clean_transcript_is_unaffected_by_the_decode_tolerance(tmp_path):
    """The negative case. A reader that replaced bytes it should not would
    corrupt every ordinary run, and no test above would notice."""
    fixture = tmp_path / "clean.jsonl"
    text = "русский текст and an em-dash — kept verbatim"
    fixture.write_text(
        json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"role": "user", "content": text}},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0, err
    env = json.loads(out)
    assert env["user_turns"][0]["text"] == text
    assert "�" not in out, "a clean transcript came back with replacement chars"


def test_a_directory_passed_as_a_session_is_reported_not_traced(tmp_path):
    """The same exit-code contract, through the other uncaught OSError.

    `main` caught `PermissionError` and `FileNotFoundError` by name. Every other
    `OSError` - `IsADirectoryError` is the one a fat-fingered `--session` reaches
    first - came out as a traceback under an exit code that means nothing.
    """
    target = tmp_path / "a-directory"
    target.mkdir()
    rc, out, err = run_parser("--session", str(target))
    assert rc == 3, f"exit {rc}; stderr: {err}"
    assert "unreadable" in err.lower()
    assert "Traceback" not in err, err


def test_nonexistent_session_exits_code_3(tmp_path):
    """--session <nonexistent-path> -> exit code 3 (session unreadable)."""
    fake = tmp_path / "does-not-exist.jsonl"
    rc, out, err = run_parser("--session", str(fake))
    assert rc == 3
    assert "session unreadable" in err.lower()


# ---------- --since-utc filter ----------

def test_since_utc_filter():
    """--since-utc excludes events older than the timestamp."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture), "--since-utc", "2026-05-13T08:42:50Z")
    assert rc == 0
    env = json.loads(out)
    # Original: 5 events spanning 08:42:11 to 08:43:15
    # After filter at 08:42:50: only events at 08:42:55, 08:43:10, 08:43:15 (3 events)
    assert env["event_count"] == 3


# ---------- workspace block enumeration ----------

def test_workspace_block_populated_by_default():
    """Without --no-workspace, envelope includes the workspace block."""
    fixture = FIXTURES / "simple-correction.jsonl"
    cmd = [sys.executable, str(SCRIPT), "--session", str(fixture)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    env = json.loads(result.stdout)
    assert "workspace" in env
    assert isinstance(env["workspace"]["skills"], list)
    assert len(env["workspace"]["skills"]) > 0  # at least some skills exist
    assert isinstance(env["workspace"]["rules"], list)
    assert isinstance(env["workspace"]["ceo_only_paths"], list)


def test_no_workspace_flag_omits_block():
    """--no-workspace omits the workspace block."""
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))  # run_parser passes --no-workspace
    env = json.loads(out)
    assert "workspace" not in env


# ---------- --max-bytes truncation ----------

def test_max_bytes_truncation(tmp_path):
    """When envelope would exceed --max-bytes, oldest user_turns are dropped, truncated=True."""
    fixture = tmp_path / "large.jsonl"
    with fixture.open("w", encoding="utf-8") as fh:
        for i in range(500):
            event = {
                "type": "user",
                "timestamp": f"2026-05-13T{i//60:02d}:{i%60:02d}:00Z",
                "message": {"role": "user", "content": "x" * 2000},  # 2KB each
            }
            fh.write(json.dumps(event) + "\n")
    rc, out, err = run_parser("--session", str(fixture), "--max-bytes", "50000")
    assert rc == 0
    env = json.loads(out)
    assert env["truncated"] is True
    assert len(env["user_turns"]) < 500  # some dropped
    assert len(out.encode("utf-8")) <= 60000  # budget plus small overhead


# ---------- permission error handling ----------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod required for unreadable test")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root bypasses permission bits, so chmod 0o000 is still readable")
def test_permission_error_exits_code_3(tmp_path):
    """Unreadable session JSONL -> exit code 3.

    The win32 skip was the only guard, and it says nothing about uid 0. `chmod
    0o000` does not make a file unreadable to root on any mainstream Linux
    filesystem, so in a root-run container (the default in the stock `python:`
    images) `calibrate.py` reads the file, exits 0, and `assert rc == 3` fails
    with nothing wrong. The condition is now measured, not assumed.
    """
    unreadable = tmp_path / "no-read.jsonl"
    unreadable.write_text("{}\n", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        rc, out, err = run_parser("--session", str(unreadable))
        assert rc == 3
        assert "unreadable" in err.lower()
    finally:
        unreadable.chmod(0o600)  # cleanup


# ---------- system reminders extraction ----------

def test_system_reminders_extracted():
    """System events with content -> system_reminders array."""
    fixture = FIXTURES / "with-system-reminders.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    env = json.loads(out)
    assert len(env["system_reminders"]) == 2
    assert "system-reminder" in env["system_reminders"][0]["text"]


# ---------- output hidden-character cleanliness ----------

def test_parser_output_has_no_hidden_unicode():
    """Parser stdout must not contain INVISIBLE characters.

    The docstring used to say "zero-width characters, em-dashes, etc." and
    U+2014 is not in the list below, so it named a guarantee that did not
    exist. The list is the right side: an em-dash is visible, this envelope
    reproduces user turns verbatim, and `.claude/rules/voice.md` preserves the
    em-dash in reproduced text. The docstring is what changed.
    """
    fixture = FIXTURES / "simple-correction.jsonl"
    rc, out, err = run_parser("--session", str(fixture))
    assert rc == 0
    # A scan over empty output is not a pass. If the parser ever printed
    # nothing, every assertion below would hold and say nothing.
    assert out.strip(), "nothing was scanned: the parser produced no stdout"
    # Parser itself must not inject hidden chars. User content passthrough is fine.
    # Build forbidden chars from escape sequences to keep source clean
    forbidden = [
        chr(0x200B),  # zero-width space
        chr(0x200C),  # zero-width non-joiner
        chr(0x200D),  # zero-width joiner
        chr(0x00AD),  # soft hyphen
        chr(0xFEFF),  # byte order mark
    ]
    for ch in forbidden:
        assert ch not in out, f"forbidden char U+{ord(ch):04X} in output"
