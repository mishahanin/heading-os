"""The envelope must read turns whose content is a list of blocks.

`build_envelope` kept a turn only when its `message.content` was a plain string:

    if isinstance(content, str) and content:

Claude Code writes a plain string for a typed user message and a LIST OF BLOCKS
for everything else — every assistant turn, and every user turn carrying a tool
result. So the filter silently dropped the whole assistant side of the record.

Measured 2026-08-22 on a real 1.7 MB session (c9bbd8dc): 27,405 characters of
readable prose, of which 26,270 (96%) were discarded. Eleven user turns survived,
1,135 characters between them, and zero assistant turns. The Chronicle's model
was handed 1,211 characters of a session and asked what was decided and why.

That is the mechanical reason a Chronicle entry reads as a bare fact: the
reasoning lives in the assistant turns, and the assistant turns never arrived.

Only `text` blocks are recovered. `tool_use` and `tool_result` payloads are
machine traffic that would swamp the prose, and `thinking` blocks are written
with an empty `thinking` field and a signature only, so there is nothing in them
to read.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]

# `build_envelope` only reads this path's STEM, for the session id it records, so
# nothing here is opened and no file needs to exist. Deliberately not under /tmp:
# a literal temp path in a test reads to ruff (S108) as a real insecure write.
SESSION_PATH = "sessions/c9bbd8dc.jsonl"


@pytest.fixture(scope="module")
def calibrate():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location(
        "calibrate_mod", WORKSPACE / "scripts" / "calibrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibrate_mod"] = module
    spec.loader.exec_module(module)
    return module


def _events():
    return [
        {"type": "user", "timestamp": "2026-08-21T10:00:00Z",
         "message": {"content": "a typed question"}},
        {"type": "assistant", "timestamp": "2026-08-21T10:00:05Z",
         "message": {"content": [
             {"type": "thinking", "thinking": "", "signature": "CAIS0wwK..."},
             {"type": "text", "text": "I measured both hosts before choosing."},
             {"type": "tool_use", "id": "t1", "name": "Bash",
              "input": {"command": "ls -la /very/long/output/path"}},
         ]}},
        {"type": "user", "timestamp": "2026-08-21T10:00:09Z",
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "noise"},
             {"type": "text", "text": "use the second one"},
         ]}},
    ]


def test_assistant_prose_in_blocks_reaches_the_envelope(calibrate):
    env = calibrate.build_envelope(Path(SESSION_PATH), _events())
    prose = " ".join(t["text"] for t in env["assistant_turns"])
    assert "I measured both hosts before choosing." in prose, (
        "the assistant side of the record was dropped"
    )


def test_user_prose_in_blocks_reaches_the_envelope(calibrate):
    env = calibrate.build_envelope(Path(SESSION_PATH), _events())
    prose = " ".join(t["text"] for t in env["user_turns"])
    assert "a typed question" in prose      # the plain-string shape still works
    assert "use the second one" in prose    # ...and the block shape now does too


def test_machine_traffic_stays_out(calibrate):
    """Tool payloads would swamp the prose and teach the model nothing."""
    env = calibrate.build_envelope(Path(SESSION_PATH), _events())
    everything = " ".join(
        t["text"] for t in env["user_turns"] + env["assistant_turns"]
    )
    assert "/very/long/output/path" not in everything
    assert "noise" not in everything
    assert "CAIS0wwK" not in everything, "a thinking signature is not prose"


def test_a_turn_with_no_readable_text_produces_no_turn(calibrate):
    """A pure tool round-trip is not a turn; an empty entry is worse than none."""
    events = [
        {"type": "assistant", "timestamp": "t",
         "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                                  "input": {"file_path": "/a"}}]}},
        {"type": "user", "timestamp": "t",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "x",
                                  "content": "body"}]}},
    ]
    env = calibrate.build_envelope(Path(SESSION_PATH), events)
    assert env["assistant_turns"] == []
    assert env["user_turns"] == []


def test_malformed_content_does_not_raise(calibrate):
    """A transcript is third-party data; one odd row must not kill the build."""
    events = [
        {"type": "assistant", "timestamp": "t", "message": {"content": None}},
        {"type": "user", "timestamp": "t", "message": {"content": [None, 42, {}]}},
        {"type": "assistant", "timestamp": "t", "message": {}},
        {"type": "user", "timestamp": "t"},
    ]
    env = calibrate.build_envelope(Path(SESSION_PATH), events)
    assert env["user_turns"] == [] and env["assistant_turns"] == []


def test_a_real_transcript_yields_far_more_than_the_string_only_filter(calibrate):
    """The regression this exists to hold, measured on whatever is on disk.

    Skips rather than fails when no transcript is present: a fresh clone and CI
    have none, and this assertion is about the shape of real harness output.
    """
    import json

    source = Path.home() / ".claude" / "projects"
    candidates = sorted(source.glob("*/*.jsonl"), key=lambda p: p.stat().st_size)
    big = [p for p in candidates if p.stat().st_size > 200_000]
    if not big:
        pytest.skip("no substantial transcript on this machine")

    events = []
    for line in big[-1].read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue

    env = calibrate.build_envelope(big[-1], events)
    recovered = sum(len(t["text"]) for t in env["assistant_turns"])
    assert recovered > 0, (
        "not one assistant turn survived a real transcript — the block shape is "
        "how the harness writes every one of them"
    )
