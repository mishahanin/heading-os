#!/usr/bin/env python3
"""Two readers that lost an entire history to a single undecodable byte.

Shard 15 of the 2026-09-01 tests/ audit. Both defects are the same shape and
neither was reachable by mutation testing, because in both cases the code was
already wrong: no mutation was needed to make them fail, only an input nobody
had passed.

`scripts/utils/denial_log.py::read_denials` opens the refusal log with
`read_text(encoding="utf-8")` under `except OSError`. A `UnicodeDecodeError` is
a ValueError, so the handler never saw it. MEASURED 2026-09-01 on a log holding
one intact record followed by a half-written multi-byte character:

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd0 in position 65

That input is not exotic. `append_denial` writes one JSON line per refusal from
whichever hook process is refusing, so a torn append lands mid-character, and
"a truncated write must not cost the rest of the history" is the first promise
the function's own docstring makes. `scripts/denials.py::main` calls it with no
handler at all, so the console-first answer to "is any guard catching anything,
or is it ceremony?" was a traceback over the whole history.

`scripts/workspace-health.py::main` ran its thirteen checks in a bare loop, so
any section that raised ended the run: the later sections never executed, no
summary printed, and the operator got a traceback instead of a verdict, in
front of `/push-updates`. `check_build_sync` has carried a comment naming that
amplifier since 2026-08-23 and guarded only its own read. MEASURED the same
day, five reads under that module still had none at all, and a
`context/pipeline.md` carrying one invalid byte took the entire run down.

The fixes are at the two layers the defects live at. `read_denials` decodes with
`errors="replace"`, so the torn line fails `json.loads` and is skipped one line
at a time while every intact record survives. `main` wraps the CALL, so a
section that cannot run costs itself and is counted as an issue rather than
swallowed.

Run:
    .venv/bin/python -m pytest tests/test_one_bad_byte_that_ended_a_whole_run.py \\
        -q --no-header -p no:randomly
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import denial_log  # noqa: E402

# One intact record, then an append torn in the middle of a two-byte character.
# `0xd0` is the lead byte of the Cyrillic range, so this is the exact shape a
# process killed mid-write leaves behind.
TORN_TAIL = b'{"ts": 2.0, "mechanism": "\xd0'


def _log(tmp_path: Path, *, intact: int = 1) -> Path:
    path = tmp_path / "denials.jsonl"
    good = b"".join(
        json.dumps({"ts": float(i), "mechanism": "leak-guard"}).encode() + b"\n"
        for i in range(intact)
    )
    path.write_bytes(good + TORN_TAIL + b"\n")
    return path


# ============================================================
# read_denials survives the truncated write it promises to survive
# ============================================================


def test_a_torn_append_does_not_raise(tmp_path):
    """The measured failure: UnicodeDecodeError out of `read_text`."""
    denial_log.read_denials(_log(tmp_path))


def test_every_intact_record_before_the_tear_still_reads(tmp_path):
    """Skipping must cost the torn line and nothing else.

    A handler that widened to ValueError and returned `[]` would pass the test
    above while losing the whole history, which is the same defect wearing the
    other hat: "0 refusals recorded" is what a clean log says too.
    """
    records = denial_log.read_denials(_log(tmp_path, intact=3))

    assert len(records) == 3, records
    assert all(r["mechanism"] == "leak-guard" for r in records)
    assert [r["ts"] for r in records] == [0.0, 1.0, 2.0]


def test_the_torn_line_itself_is_not_returned_as_a_record(tmp_path):
    """`errors="replace"` makes it text, and text that is not JSON is skipped."""
    records = denial_log.read_denials(_log(tmp_path, intact=1))

    assert len(records) == 1
    assert all(isinstance(r, dict) for r in records)


def test_a_log_that_is_only_a_torn_line_reads_as_no_records(tmp_path):
    """The boundary: nothing intact to keep, and still not an exception."""
    path = tmp_path / "denials.jsonl"
    path.write_bytes(TORN_TAIL + b"\n")

    assert denial_log.read_denials(path) == []


def test_the_summary_survives_the_same_log(tmp_path):
    """`summarize` is the caller that turned the old raise into a lost history."""
    assert denial_log.summarize(
        denial_log.read_denials(_log(tmp_path, intact=2))
    ) == {"leak-guard": 2}


def test_a_clean_log_is_unchanged_by_the_replacement(tmp_path):
    """Anchor: `errors="replace"` must not alter a log with nothing wrong."""
    path = tmp_path / "denials.jsonl"
    path.write_text(
        json.dumps({"ts": 1.0, "mechanism": "secret-scanner"}) + "\n"
        + json.dumps({"ts": 2.0, "mechanism": "leak-guard"}) + "\n",
        encoding="utf-8")

    assert denial_log.read_denials(path) == [
        {"ts": 1.0, "mechanism": "secret-scanner"},
        {"ts": 2.0, "mechanism": "leak-guard"},
    ]


def test_a_non_ascii_mechanism_still_decodes(tmp_path):
    """Anchor: the replacement must not be reached by ordinary valid UTF-8.

    A record whose text is multi-byte but WHOLE has to survive byte-for-byte,
    or the fix would quietly corrupt every non-ASCII record to buy the torn one.
    """
    path = tmp_path / "denials.jsonl"
    # Escaped rather than written literally, so this file stays pure ASCII on
    # disk. The value is the eight-letter Cyrillic word for "check"; what the
    # test needs is only that it is valid multi-byte UTF-8.
    name = "\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430"
    path.write_text(json.dumps({"ts": 1.0, "mechanism": name}) + "\n",
                    encoding="utf-8")

    assert denial_log.read_denials(path) == [{"ts": 1.0, "mechanism": name}]


# ============================================================
# one section that cannot run does not end the health run
# ============================================================


@pytest.fixture(scope="module")
def wh():
    spec = importlib.util.spec_from_file_location(
        "wh_one_bad_byte", ROOT / "scripts" / "workspace-health.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["wh_one_bad_byte"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def poisoned_context(wh, tmp_path, monkeypatch):
    """A context/ whose pipeline.md and people.md hold one invalid byte."""
    context = tmp_path / "context"
    context.mkdir()
    (context / "pipeline.md").write_bytes(
        b"| Company | Stage |\n|---|---|\n| Acme | \xff |\n")
    (context / "people.md").write_bytes(b"# People\n\xff\n")
    monkeypatch.setattr(wh, "context_dir", lambda p=context: p)
    return context


def test_an_undecodable_pipeline_is_a_finding_and_not_the_end_of_the_run(
        wh, poisoned_context, monkeypatch, capsys):
    """The read is guarded in place too, as of 2026-09-01, and both layers count.

    This test was written expecting `check_pipeline_health` to RAISE, and it
    failed on the first run because that read had been handled in the same hour
    by a parallel pass. Recorded rather than quietly rewritten: the two fixes
    are at different layers and neither makes the other redundant. This one
    pins the per-read layer; the two below pin the loop, driven by an injected
    check so they cannot be satisfied by a handler somebody adds later.
    """
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "pipeline"])

    with pytest.raises(SystemExit) as exc:
        wh.main()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "NOT checked" in out, out
    assert "Summary" in out, "the run ended without printing a verdict"


def test_a_crashing_section_is_reported_and_counted(wh, monkeypatch, capsys):
    """It exits 1 with a verdict, not 1 with a traceback and nothing said.

    The raising check is INJECTED. Reaching the loop guard through a real
    check's real crash would tie this test to whichever read is unguarded this
    week, which is exactly how the version above went stale in one hour.
    """
    def _boom():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(wh, "check_pipeline_health", _boom)
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "pipeline"])

    with pytest.raises(SystemExit) as exc:
        wh.main()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "could not run" in out, out
    assert "pipeline" in out
    assert "verified nothing" in out


def test_a_crashing_section_does_not_stop_the_ones_after_it(wh, poisoned_context,
                                                             monkeypatch, capsys):
    """The whole point: the run reaches its summary.

    Driven over a stand-in check registry rather than the real thirteen, so the
    assertion is about the loop and not about whichever sections happen to pass
    on this clone.
    """
    ran: list[str] = []

    def _ok(name):
        def check():
            ran.append(name)
            return 0
        return check

    def _boom():
        ran.append("boom")
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(sys, "argv", ["workspace-health.py"])
    monkeypatch.setattr(wh, "check_reference_validation", _ok("first"))
    monkeypatch.setattr(wh, "check_pipeline_health", _boom)
    monkeypatch.setattr(wh, "check_build_sync", _ok("last"))

    with pytest.raises(SystemExit) as exc:
        wh.main()

    assert "first" in ran, "the section before the crash never ran"
    assert "boom" in ran
    assert "last" in ran, (
        "a section AFTER the crash never ran; one bad byte still ends the run")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Summary" in out, "the run ended without printing a verdict"


def test_a_healthy_section_is_still_a_pass(wh, tmp_path, monkeypatch, capsys):
    """Anchor: the wrapper must not turn a clean section into an issue.

    A guard that counts every section as failed would satisfy every assertion
    above and make the whole health check useless.
    """
    context = tmp_path / "context"
    context.mkdir()
    (context / "pipeline.md").write_text(
        "| Company | Stage |\n|---|---|\n| Acme | Won |\n" + "x" * 4000,
        encoding="utf-8")
    monkeypatch.setattr(wh, "context_dir", lambda p=context: p)
    monkeypatch.setattr(sys, "argv", ["workspace-health.py", "--section", "pipeline"])

    with pytest.raises(SystemExit) as exc:
        wh.main()

    assert exc.value.code == 0
    assert "could not run" not in capsys.readouterr().out
