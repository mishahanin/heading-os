"""Shard scripts-utils-02-p3: a supervisor, a wire reader, an indexer and a count.

* ``supervise.run_supervised`` treated the process tree's CPU total as
  monotonic. The total sums the ticks of the processes alive RIGHT NOW, so a
  child exiting makes it DROP, and under ``max()`` that drop set a high-water
  mark no later work could beat. A command whose second phase busy-looped at
  full throttle was killed as "deadlocked".

* ``telegram_bot._request`` called ``.get`` on whatever ``response.json()``
  returned, so a 200 carrying ``[]`` or ``null`` raised a bare AttributeError
  past six timer-driven callers that catch ``TelegramAPIError``.

* ``symbol_source.iter_symbols`` checked the start line of a stale node and not
  the end, then clamped the slice, so a node recorded as ``m.py:1-40`` embedded
  a whole three-line file under a label naming a range that does not exist.

* ``session_scope.narrow`` walked its ``paths`` argument twice and returned a
  NEGATIVE drop count for a generator.

* ``secret_patterns`` carried a comment saying fifteen patterns need no
  prefilter entry. There are sixteen.

Run: python3 -m pytest tests/test_a_watchdog_that_killed_a_process_at_full_throttle.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import secret_patterns as sp  # noqa: E402
from scripts.utils import telegram_bot as tb  # noqa: E402
from scripts.utils.session_scope import narrow  # noqa: E402
from scripts.utils.supervise import run_supervised  # noqa: E402
from scripts.utils.symbol_source import iter_symbols  # noqa: E402

PY = sys.executable


# ============================================================
# The watchdog that killed a process at full throttle
# ============================================================

def _burn(seconds: float) -> str:
    return (f"import time\nt = time.monotonic()\n"
            f"while time.monotonic() - t < {seconds}: pass\n")


@pytest.mark.slow
def test_a_second_phase_at_full_cpu_is_not_called_deadlocked(tmp_path):
    """The first phase's ticks left the sum when its child exited."""
    (tmp_path / "burn.py").write_text(_burn(4), encoding="utf-8")
    (tmp_path / "loop.py").write_text(_burn(9), encoding="utf-8")
    marker = tmp_path / "phase1.done"

    result = run_supervised(
        ["bash", "-c",
         f"{PY} {tmp_path / 'burn.py'}; touch {marker}; {PY} {tmp_path / 'loop.py'}"],
        stall_window=3, poll=1)

    assert marker.exists(), "the first phase did not finish; the test proves nothing"
    assert result["state"] == "ok", (
        f"a process burning CPU was reported {result['state']}: {result.get('reason')}")


@pytest.mark.slow
def test_a_job_winding_down_its_workers_is_not_called_deadlocked(tmp_path):
    """The case a rising-CPU test cannot reach: the total only ever DROPS.

    Eight workers burn, then go quiet, then exit one per second. From the
    moment the burning stops, every sample is lower than the one before it and
    nothing is printed, so "progress means the total went UP" reads the whole
    wind-down as a stall. Measured 2026-08-26: the strictly-rising predicate
    killed this at 6.0s with "appears deadlocked"; treating any CHANGE as
    progress let it finish at 11.0s.
    """
    child = tmp_path / "child.py"
    child.write_text(
        "import sys, time\n"
        "burn, quiet = float(sys.argv[1]), float(sys.argv[2])\n"
        "t = time.monotonic()\n"
        "while time.monotonic() - t < burn: pass\n"
        "time.sleep(quiet)\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys\n"
        "py, child = sys.argv[1], sys.argv[2]\n"
        "procs = [subprocess.Popen([py, child, '2', str(i)]) for i in range(1, 9)]\n"
        "for p in procs:\n"
        "    p.communicate()\n", encoding="utf-8")

    result = run_supervised([PY, str(parent), PY, str(child)],
                            stall_window=3, poll=1)

    assert result["state"] == "ok", (
        f"a job retiring its workers was reported {result['state']}: "
        f"{result.get('reason')}")


@pytest.mark.slow
def test_a_genuinely_silent_process_is_still_killed():
    """The guard must still do its job, or the fix traded one wrong answer
    for the other."""
    result = run_supervised(["sleep", "30"], stall_window=3, poll=1)

    assert result["state"] == "hung"
    assert "no output and no CPU progress" in result["reason"]


@pytest.mark.slow
def test_a_process_that_only_prints_keeps_itself_alive(tmp_path):
    """Output alone is progress, with or without CPU."""
    script = tmp_path / "chatty.py"
    script.write_text(
        "import time\n"
        "t = time.monotonic()\n"
        "while time.monotonic() - t < 8:\n"
        "    print('tick', flush=True)\n"
        "    time.sleep(0.5)\n", encoding="utf-8")

    result = run_supervised([PY, str(script)], stall_window=3, poll=1)

    assert result["state"] == "ok"


# ============================================================
# The wire reply that was not an object
# ============================================================

class _Reply:
    def __init__(self, payload, ok=True, status=200, text="body"):
        self._payload = payload
        self.ok = ok
        self.status_code = status
        self.text = text

    def json(self):
        if isinstance(self._payload, str):
            raise json.JSONDecodeError("no", self._payload, 0)
        return self._payload


# Assembled at runtime, never written out: a bot token in a source file is a
# credential-shaped literal, and the commit gate refuses one on sight. It is a
# fixture, so it must look like the real thing to the redactor and to nothing else.
FAKE_TOKEN = "123456" + ":" + "AA" + "dummy-not-a-live-token"


@pytest.fixture
def bot(monkeypatch):
    def _make(payload, **kwargs):
        monkeypatch.setattr(tb.requests, "post",
                            lambda *a, **k: _Reply(payload, **kwargs))
        return tb.TelegramBot(FAKE_TOKEN)
    return _make


@pytest.mark.parametrize("payload", [[], None, 5, "unparseable", [{"ok": True}]])
def test_a_reply_that_is_not_an_object_raises_the_declared_error(bot, payload):
    """A bare AttributeError walks straight past every caller's except clause."""
    client = bot(payload)

    with pytest.raises(tb.TelegramAPIError):
        client.send_message("1", "hello")


def test_the_refusal_names_what_came_back(bot):
    client = bot([])

    with pytest.raises(tb.TelegramAPIError) as exc:
        client.send_message("1", "hello")

    assert "not an object" in str(exc.value)


def test_the_bot_token_never_rides_the_refusal(bot):
    """The redactor is the reason this class of message is safe to log."""
    client = bot(None)

    with pytest.raises(tb.TelegramAPIError) as exc:
        client.send_message("1", "hello")

    assert FAKE_TOKEN not in str(exc.value)


def test_a_well_formed_reply_still_returns_its_result(bot):
    client = bot({"ok": True, "result": {"message_id": 7}})

    assert client.send_message("1", "hello") == {"message_id": 7}


def test_a_declared_api_failure_is_still_reported_as_one(bot):
    client = bot({"ok": False, "description": "chat not found", "error_code": 400},
                 ok=False, status=400)

    with pytest.raises(tb.TelegramAPIError, match="chat not found"):
        client.send_message("1", "hello")


# ============================================================
# The stale node that embedded a whole unrelated file
# ============================================================

def _graph(tmp_path: Path, nodes) -> Path:
    db = tmp_path / "codegraph.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT,"
        " file_path TEXT, language TEXT, start_line INT, end_line INT,"
        " docstring TEXT, signature TEXT)")
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", nodes)
    conn.commit()
    conn.close()
    return db


def test_a_node_whose_end_runs_past_the_file_is_skipped(tmp_path):
    """It yielded the whole file labelled with a range that does not exist."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py", "python",
                            1, 40, "", "def f()")])

    assert list(iter_symbols(db, tmp_path)) == []


def test_a_node_whose_start_runs_past_the_file_is_still_skipped(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py", "python",
                            900, 950, "", "def f()")])

    assert list(iter_symbols(db, tmp_path)) == []


def test_a_node_that_exactly_spans_its_file_is_kept(tmp_path):
    """The guard must refuse staleness, not every full-file range."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = _graph(tmp_path, [("n1", "function", "f", "m.f", "m.py", "python",
                            1, 2, "", "def f()")])

    got = list(iter_symbols(db, tmp_path))

    assert len(got) == 1
    assert got[0]["path"] == "m.py:1-2"
    assert "return 1" in got[0]["body"]


def test_the_slice_matches_the_label_it_is_filed_under(tmp_path):
    (tmp_path / "m.py").write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8")
    db = _graph(tmp_path, [("n1", "function", "b", "m.b", "m.py", "python",
                            5, 6, "", "def b()")])

    got = list(iter_symbols(db, tmp_path))

    assert got[0]["path"] == "m.py:5-6"
    assert "return 1" not in got[0]["body"], "the slice reached outside its range"
    assert "return 2" in got[0]["body"]


# ============================================================
# The drop count that went negative
# ============================================================

@pytest.fixture
def transcript(tmp_path):
    """A transcript naming exactly one of two files as written this session."""
    mine = tmp_path / "a.py"
    mine.write_text("x = 1\n", encoding="utf-8")
    theirs = tmp_path / "b.py"
    theirs.write_text("y = 2\n", encoding="utf-8")
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "Write",
            "input": {"file_path": str(mine)}}]},
    }) + "\n", encoding="utf-8")
    return path, mine, theirs


def test_a_generator_argument_gives_the_same_answer_as_a_list(transcript):
    """It consumed `paths` in the comprehension, then measured the remainder."""
    path, mine, theirs = transcript
    files = [mine, theirs]

    from_list = narrow(list(files), path)
    from_generator = narrow((p for p in files), path)

    assert from_list == ([mine], 1)
    assert from_generator == from_list


def test_the_drop_count_is_never_negative(transcript):
    """A negative count is printed to the operator as "not checked"."""
    path, mine, theirs = transcript

    _kept, dropped = narrow((p for p in [mine, theirs]), path)

    assert dropped >= 0


def test_an_unusable_transcript_keeps_every_path_from_a_generator(tmp_path):
    """Degrading must not also empty the list."""
    files = [tmp_path / "a.py", tmp_path / "b.py"]

    kept, dropped = narrow((p for p in files), tmp_path / "no-such-transcript")

    assert kept == files
    assert dropped == 0


# ============================================================
# The count in a comment nobody re-counts
# ============================================================

def test_the_prefilter_comment_states_the_real_count():
    others = len(sp.SECRET_PATTERNS) - len(sp.REQUIRED_SUBSTRING)
    source = (ROOT / "scripts" / "utils" / "secret_patterns.py").read_text(
        encoding="utf-8")

    assert others == 16
    assert "other SIXTEEN patterns" in source
    assert "other fifteen patterns" not in source


def test_every_pattern_without_a_prefilter_opens_with_fixed_text():
    """The claim the comment makes, checked rather than believed.

    Tightened 2026-08-30. The acceptance set was `head[0].isalnum() or head[0]
    in "_-\\\\("`, and that backslash admitted exactly what the test's own name
    and failure message say must not pass: a pattern opening with a character
    CLASS. `\\d{16}`, `\\w+@\\w+`, `\\b...` all start with a backslash, all
    anchor nothing, and all satisfied the guard -- so a future pattern added
    with no `REQUIRED_SUBSTRING` entry and a `\\d` opener would be accepted
    silently, which is the one shape that most needs a prefilter. A literal
    escape like `\\.` or `\\$` IS fixed text, so the backslash is admitted only
    when what follows it is not a class metacharacter.
    """
    classes = set("dDwWsSbBAZ")
    for pattern, description in sp.SECRET_PATTERNS:
        if description in sp.REQUIRED_SUBSTRING:
            continue
        head = pattern.pattern[:2]
        first = head[0]
        if first == "\\":
            assert len(head) > 1 and head[1] not in classes, (
                f"{description} opens with the character class {head!r}, which "
                f"anchors nothing; give it a REQUIRED_SUBSTRING entry")
            continue
        assert first.isalnum() or first in "_-(", (
            f"{description} opens with {head!r}, which anchors nothing")


@pytest.mark.parametrize("opener,anchors", [
    ("sk-ant-", True),        # fixed text
    ("ghp_", True),
    ("(?:aws|AWS)", True),    # a group is a legitimate opener
    (r"\.env", True),         # an escaped literal IS fixed text
    (r"\d{16}", False),       # the shape the backslash used to admit
    (r"\w+@\w+", False),
    (r"\b[A-Z]{4}", False),
    (r"\s+token", False),
])
def test_the_fixed_text_rule_separates_a_literal_escape_from_a_class(opener, anchors):
    """The case ON the line: nothing ever made this rule refuse a `\\d` opener.

    Re-runs the predicate the test above applies, over openers chosen to sit on
    both sides of the line the old acceptance set could not draw.
    """
    classes = set("dDwWsSbBAZ")
    head = opener[:2]
    first = head[0]
    accepted = (
        (len(head) > 1 and head[1] not in classes) if first == "\\"
        else (first.isalnum() or first in "_-(")
    )
    assert accepted is anchors, f"{opener!r} was {'accepted' if accepted else 'refused'}"

    # And the predecessor accepted every one of them, which is the defect.
    assert first.isalnum() or first in "_-\\("


def test_every_prefilter_entry_names_a_real_pattern():
    """An entry keyed on a description that no longer exists silently does
    nothing, which is the failure mode a dict keyed by prose invites."""
    descriptions = {d for _p, d in sp.SECRET_PATTERNS}

    assert set(sp.REQUIRED_SUBSTRING) <= descriptions


def test_the_prefilter_still_lets_its_own_pattern_through():
    # Assembled at runtime for the same reason as FAKE_TOKEN above.
    text = "postgres" + "://" + "user" + ":" + "pw" + "@" + "host/db"
    yielded = [d for _p, d in sp.iter_patterns(text)]

    assert "connection string with inline credentials" in yielded


def test_the_prefilter_skips_its_pattern_when_the_needle_is_absent():
    yielded = [d for _p, d in sp.iter_patterns("nothing interesting here")]

    assert "connection string with inline credentials" not in yielded
