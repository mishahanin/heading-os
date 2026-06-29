"""Tests for scripts.thread CLI."""
import subprocess
import sys
from datetime import date
from pathlib import Path
from scripts.utils.threads_lib import parse_thread_file


def test_thread_open_creates_file_and_index_entry(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    cmd = [sys.executable, "scripts/thread.py", "open", "business", "Porkbun TrustONE phishing"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr

    files = list((threads_root / "business").glob("*.md"))
    assert len(files) == 1
    parsed = parse_thread_file(files[0])
    assert parsed.title == "Porkbun TrustONE phishing"
    assert parsed.status == "active"
    assert parsed.type == "business"

    mem = memory_md.read_text(encoding="utf-8")
    assert "Porkbun TrustONE phishing" in mem
    assert "### Business" in mem


def test_thread_log_appends_entry_and_updates_hook(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Test thread"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "log", thread_id, "Sent reply to abuse desk",
         "--artifact", "outputs/email-drafts/2026-04-29_email-draft_porkbun-abuse-reply.md"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    parsed = parse_thread_file(files[0])
    assert "Sent reply to abuse desk" in parsed.body
    assert "outputs/email-drafts/2026-04-29_email-draft_porkbun-abuse-reply.md" in parsed.links["outputs"]
    mem = memory_md.read_text(encoding="utf-8")
    assert "Sent reply to abuse desk" in mem


def test_thread_close_removes_from_index_keeps_file(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Closeable"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "close", thread_id],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    parsed = parse_thread_file(files[0])
    assert parsed.status == "closed"
    mem = memory_md.read_text(encoding="utf-8")
    assert "Closeable" not in mem


def test_thread_hold_and_reopen_round_trip(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Holdable"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem

    subprocess.run([sys.executable, "scripts/thread.py", "hold", thread_id], check=True)
    parsed = parse_thread_file(threads_root / "business" / f"{thread_id}.md")
    assert parsed.status == "on-hold"
    mem = memory_md.read_text(encoding="utf-8")
    assert "Holdable" not in mem

    subprocess.run([sys.executable, "scripts/thread.py", "reopen", thread_id], check=True)
    parsed = parse_thread_file(threads_root / "business" / f"{thread_id}.md")
    assert parsed.status == "active"
    mem = memory_md.read_text(encoding="utf-8")
    assert "Holdable" in mem


def test_thread_list_shows_active_threads(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Alpha thread"], check=True)
    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Bravo thread"], check=True)

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "list"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Alpha thread" in result.stdout
    assert "Bravo thread" in result.stdout


def test_thread_find_matches_title_substring(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Porkbun TrustONE"], check=True)
    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "ExampleTelco negotiation"], check=True)

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "find", "Porkbun"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Porkbun TrustONE" in result.stdout
    assert "ExampleTelco" not in result.stdout


def test_thread_archive_scan_moves_old_closed_threads(tmp_path: Path, monkeypatch) -> None:
    from datetime import timedelta
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Old"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem
    subprocess.run([sys.executable, "scripts/thread.py", "close", thread_id], check=True)

    # Backdate the file's last_touched to 100 days ago
    parsed = parse_thread_file(files[0])
    old_date = (date.today() - timedelta(days=100)).isoformat()
    parsed.last_touched = old_date
    from scripts.utils.threads_lib import write_thread_file
    write_thread_file(files[0], parsed)

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "archive-scan", "--apply"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    archived = list((threads_root / "archive").rglob("*.md"))
    assert len(archived) == 1
    assert not files[0].exists()
    # H3 regression: MEMORY.md must not retain a link pointing at the moved file.
    mem = memory_md.read_text(encoding="utf-8")
    assert f"threads/business/{thread_id}.md" not in mem


def test_thread_show_prints_file_content(tmp_path: Path, monkeypatch) -> None:
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Showable thread"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "show", thread_id],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Showable thread" in result.stdout
    assert "## Open follow-ups" in result.stdout


def test_thread_show_returns_error_on_missing_thread(tmp_path: Path, monkeypatch) -> None:
    """C-1 regression: missing thread should print clean error, not Python traceback."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "show", "nonexistent-thread"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "error:" in result.stderr.lower()
    assert "Traceback" not in result.stderr


# ======================================
# Scrutiny regressions (2026-04-30)
# ======================================


def test_log_two_follow_ups_does_not_duplicate_section(tmp_path: Path, monkeypatch) -> None:
    """H1 regression: appending two follow-ups must not corrupt or duplicate the section."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Two follow-ups"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem
    subprocess.run([sys.executable, "scripts/thread.py", "log", thread_id, "e1", "--follow-up", "First"], check=True)
    subprocess.run([sys.executable, "scripts/thread.py", "log", thread_id, "e2", "--follow-up", "Second"], check=True)

    body = files[0].read_text(encoding="utf-8")
    assert body.count("## Open follow-ups") == 1, "section header was duplicated"
    assert "## Open follow-ups\n\n- [ ] First\n- [ ] Second" in body


def test_log_three_decisions_does_not_duplicate_section(tmp_path: Path, monkeypatch) -> None:
    """H1 regression: same corruption pattern affects --decision, not just --follow-up."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Decisions stack"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem
    for txt in ("Alpha", "Bravo", "Charlie"):
        subprocess.run(
            [sys.executable, "scripts/thread.py", "log", thread_id, f"e-{txt}", "--decision", txt],
            check=True,
        )
    body = (threads_root / "business" / f"{thread_id}.md").read_text(encoding="utf-8")
    assert body.count("## Decisions") == 1


def test_log_done_indexes_remain_stable_after_multiple_adds(tmp_path: Path, monkeypatch) -> None:
    """H1 regression: --done <N> must target the right item after multiple --follow-up adds."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Done index"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem
    for txt in ("First", "Second", "Third"):
        subprocess.run(
            [sys.executable, "scripts/thread.py", "log", thread_id, f"e-{txt}", "--follow-up", txt],
            check=True,
        )
    subprocess.run([sys.executable, "scripts/thread.py", "log", thread_id, "tick", "--done", "1"], check=True)
    body = (threads_root / "business" / f"{thread_id}.md").read_text(encoding="utf-8")
    assert "- [x] Second" in body
    assert "- [ ] First" in body
    assert "- [ ] Third" in body


def test_log_collapses_whitespace_in_event_hook(tmp_path: Path, monkeypatch) -> None:
    """L1 regression: multi-paragraph event must not leave double-spaced hook in MEMORY.md."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Whitespace test"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem
    subprocess.run(
        [sys.executable, "scripts/thread.py", "log", thread_id, "line one\nline two\n\nline three"],
        check=True,
    )
    mem = memory_md.read_text(encoding="utf-8")
    assert "line one line two line three" in mem
    assert "line one  line two" not in mem  # no double spaces


def test_open_rejects_empty_slug_with_clean_error(tmp_path: Path, monkeypatch) -> None:
    """H5 + M3 regression: empty-slug title must produce clean rc=1, not a traceback."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "open", "business", "!!!"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "error:" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_log_aborts_when_memory_md_missing(tmp_path: Path, monkeypatch) -> None:
    """Atomicity: if MEMORY.md does not exist, log must fail BEFORE mutating the thread."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))
    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Atomic test"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem
    thread_path = threads_root / "business" / f"{thread_id}.md"
    body_before = thread_path.read_text(encoding="utf-8")

    # Repoint MEMORY_MD at a missing file.
    monkeypatch.setenv("MEMORY_MD", str(tmp_path / "missing" / "MEMORY.md"))
    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "log", thread_id, "should-not-land"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert thread_path.read_text(encoding="utf-8") == body_before


def test_list_warns_about_corrupted_threads(tmp_path: Path, monkeypatch) -> None:
    """Corrupted threads must surface as a stderr warning, not silently disappear."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))
    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Healthy thread"], check=True)

    # Plant a corrupted thread file (id-stem mismatch triggers L3 ValueError).
    bad = threads_root / "business" / "2026-04-30-corrupted.md"
    bad.write_text(
        "---\nid: wrong-id\ntitle: t\nstatus: active\ntype: business\n"
        "classification: ceo-only\nopened: 2026-04-30\nlast_touched: 2026-04-30\n"
        "counterparties: []\nlinks: {}\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "list"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "Healthy thread" in result.stdout
    assert "warning" in result.stderr.lower()
    assert "2026-04-30-corrupted.md" in result.stderr


def test_log_accepts_multiple_followups_artifacts_decisions_in_one_call(tmp_path: Path, monkeypatch) -> None:
    """Repeatable-flag regression: passing --follow-up / --artifact / --decision
    twice or more in one log call must record EVERY value, not just the last.
    """
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Repeatable flags"], check=True)
    files = list((threads_root / "business").glob("*.md"))
    thread_id = files[0].stem

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "log", thread_id, "multi-flag event",
         "--artifact", "outputs/a/one.md",
         "--artifact", "outputs/a/two.pdf",
         "--follow-up", "Follow-up alpha",
         "--follow-up", "Follow-up bravo",
         "--follow-up", "Follow-up charlie",
         "--decision", "Decision one",
         "--decision", "Decision two"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    parsed = parse_thread_file(files[0])
    assert "outputs/a/one.md" in parsed.links["outputs"]
    assert "outputs/a/two.pdf" in parsed.links["outputs"]

    body = files[0].read_text(encoding="utf-8")
    assert "- [ ] Follow-up alpha" in body
    assert "- [ ] Follow-up bravo" in body
    assert "- [ ] Follow-up charlie" in body
    assert "Decision one" in body
    assert "Decision two" in body
    assert body.count("## Open follow-ups") == 1
    assert body.count("## Decisions") == 1


def test_log_self_heals_corrupted_memory_section(tmp_path: Path, monkeypatch) -> None:
    """M1 regression: log must self-heal a hand-edited MEMORY.md missing the index line."""
    threads_root = tmp_path / "threads"
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(threads_root))
    monkeypatch.setenv("MEMORY_MD", str(memory_md))

    subprocess.run([sys.executable, "scripts/thread.py", "open", "business", "Self-heal test"], check=True)
    thread_id = list((threads_root / "business").glob("*.md"))[0].stem

    # Wipe the entire ## Active Threads section as if user had hand-edited it.
    memory_md.write_text("# Persistent Memory\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/thread.py", "log", thread_id, "after wipe"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    mem = memory_md.read_text(encoding="utf-8")
    assert "## Active Threads" in mem
    assert "Self-heal test" in mem
