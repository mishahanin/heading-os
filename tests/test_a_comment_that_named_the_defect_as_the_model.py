"""Shard 02-p3: three readers that validated the container and stopped, and a
page that derived its names and restated its count.

* ``calibrate.build_envelope`` read a turn as
  ``(ev.get("message") or {}).get("content", "")``. ``or`` only substitutes for
  a FALSY value, so a line carrying ``"message": "hello"`` came through as the
  string and the next ``.get`` raised AttributeError - out of the function, out
  of ``main``, and the whole run died on one odd line. The sting is four lines
  below: the ``tool_use`` branch fixed the identical shape and its comment
  called these two lines "the correct idiom". The comment named the defect as
  the model.

* ``capture-design-exemplars-retry._load_manifest`` validated that ``results``
  was a list and never looked inside it. A row without ``slug`` raised KeyError
  in the merge - AFTER all three captures had run, and with a manifest present,
  so the fallback write never fired. Capture everything, record nothing, which
  is the outcome that fallback exists to prevent.

* ``calibrate``'s module docstring said exit 1 meant "other parser crash". The
  reachable exit-1 path is a bad ``--since-utc``, caught and printed cleanly.

* ``canopus`` printed its candidate NAMES from ``CANDIDATES`` and the word
  "three" from nowhere, then enumerated exactly those three wrongnesses in
  prose. A fourth candidate would print four names beside "three".

Run: python3 -m pytest tests/test_a_comment_that_named_the_defect_as_the_model.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATE = REPO_ROOT / "scripts" / "calibrate.py"
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def calibrate():
    return _load("calibrate_under_test", "scripts/calibrate.py")


@pytest.fixture(scope="module")
def canopus():
    return _load("canopus_under_test", "scripts/canopus.py")


# ============================================================
# The turn reader that trusted `or {}`
# ============================================================

@pytest.mark.parametrize("bad", [
    "hello",                      # the reported reproduction
    ["a", "list"],
    42,
    1.5,
    True,
])
@pytest.mark.parametrize("kind", ["user", "assistant"])
def test_a_truthy_non_dict_message_does_not_kill_the_run(calibrate, kind, bad):
    events = [{"type": kind, "message": bad, "timestamp": "2026-08-24T00:00:00Z"}]
    env = calibrate.build_envelope(Path("s.jsonl"), events)
    assert env["user_turns"] == []
    assert env["assistant_turns"] == []


def test_one_odd_line_does_not_lose_the_good_ones(calibrate):
    """The tolerance `parse_jsonl`'s docstring promises, applied here too."""
    events = [
        {"type": "user", "message": "a bare string", "timestamp": "t1"},
        {"type": "user", "message": {"content": "a real question"}, "timestamp": "t2"},
        {"type": "assistant", "message": None, "timestamp": "t3"},
        {"type": "assistant", "message": {"content": "a real answer"}, "timestamp": "t4"},
    ]
    env = calibrate.build_envelope(Path("s.jsonl"), events)
    assert [t["text"] for t in env["user_turns"]] == ["a real question"]
    assert [t["text"] for t in env["assistant_turns"]] == ["a real answer"]


def test_the_block_list_shape_still_parses(calibrate):
    """Claude Code writes assistant content as a list of blocks."""
    events = [{"type": "assistant", "timestamp": "t", "message": {"content": [
        {"type": "text", "text": "the prose"},
        {"type": "thinking", "thinking": ""},
        {"type": "tool_use", "name": "Bash"},
    ]}}]
    env = calibrate.build_envelope(Path("s.jsonl"), events)
    assert [t["text"] for t in env["assistant_turns"]] == ["the prose"]


@pytest.mark.parametrize("value", [None, {}, "", 0, [], False])
def test_the_falsy_message_forms_keep_their_old_answer(calibrate, value):
    """The old `or {}` handled every falsy form; the fix must not regress them."""
    events = [{"type": "user", "message": value, "timestamp": "t"}]
    assert calibrate.build_envelope(Path("s.jsonl"), events)["user_turns"] == []


def test_a_message_dict_without_content_is_not_a_turn(calibrate):
    events = [{"type": "user", "message": {"role": "user"}, "timestamp": "t"}]
    assert calibrate.build_envelope(Path("s.jsonl"), events)["user_turns"] == []


def test_the_helper_is_the_single_reader(calibrate):
    """Both branches route through it, so the next fix lands on both.

    The defect was precisely that one branch was guarded and two were not.
    """
    src = CALIBRATE.read_text(encoding="utf-8")
    assert src.count("_turn_text(_message_content(ev))") == 2
    # The correction QUOTES the old idiom, so pin the order rather than
    # asserting the string is absent: it may appear only inside the docstring
    # that explains it, which comes after the words "used to be".
    assert src.index("The read used to be") < src.index('(ev.get("message") or {})')


def test_the_tool_use_comment_no_longer_points_at_the_defect(calibrate):
    src = CALIBRATE.read_text(encoding="utf-8")
    assert "_message_content" in src
    assert "The correct idiom is four lines above" not in src


def test_a_malformed_message_survives_the_whole_cli(tmp_path):
    """End to end, because the crash was fatal to the process, not to a turn."""
    session = tmp_path / "s.jsonl"
    session.write_text(
        json.dumps({"type": "user", "message": "hello",
                    "timestamp": "2026-08-24T00:00:00Z"}) + "\n"
        + json.dumps({"type": "user", "message": {"content": "real"},
                      "timestamp": "2026-08-24T00:01:00Z"}) + "\n",
        encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CALIBRATE), "--session", str(session), "--no-workspace"],
        capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "AttributeError" not in proc.stderr
    env = json.loads(proc.stdout)
    assert [t["text"] for t in env["user_turns"]] == ["real"]


# ============================================================
# The exit code that said "engine bug" for a typo
# ============================================================

def test_a_bad_since_utc_exits_one_without_a_traceback(tmp_path):
    session = tmp_path / "s.jsonl"
    session.write_text(json.dumps({"type": "user", "message": {"content": "x"},
                                   "timestamp": "2026-08-24T00:00:00Z"}) + "\n",
                       encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(CALIBRATE), "--session", str(session),
         "--no-workspace", "--since-utc", "not a date"],
        capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr


def test_the_exit_code_line_names_the_caller_error(calibrate):
    """An operator paging on "exit 1 = engine bug" was woken by a typo."""
    doc = calibrate.__doc__
    assert "caller error" in doc
    assert "--since-utc" in doc


# ============================================================
# The manifest row the merge could not key
# ============================================================

@pytest.fixture
def retry_mod(tmp_path, monkeypatch):
    mod = _load("retry_under_test", "scripts/capture-design-exemplars-retry.py")
    monkeypatch.setattr(mod, "manifest_path", lambda p=tmp_path / "manifest.json": p)
    return mod


def _manifest(mod, payload) -> None:
    mod.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    mod.manifest_path().write_text(json.dumps(payload), encoding="utf-8")


def test_a_row_without_a_slug_is_dropped_not_raised(retry_mod, capsys):
    """The reported reproduction: KeyError after every capture had run."""
    _manifest(retry_mod, {"results": [{"url": "https://example.com"},
                                      {"slug": "good", "url": "u"}],
                          "ok": 1, "errors": 1, "total": 2})
    got = retry_mod._load_manifest()
    assert [r["slug"] for r in got["results"]] == ["good"]


@pytest.mark.parametrize("row", [
    {"url": "u"},          # no slug at all
    {"slug": None},
    {"slug": ""},
    {"slug": 7},
    "not a row",
    None,
    ["slug"],
])
def test_every_unkeyable_row_shape_is_dropped(retry_mod, row):
    _manifest(retry_mod, {"results": [row]})
    assert retry_mod._load_manifest()["results"] == []


def test_the_dropped_rows_are_counted_on_stderr(retry_mod, capsys):
    """Silently discarding a row is the same defect one level quieter."""
    _manifest(retry_mod, {"results": [{"url": "a"}, {"url": "b"},
                                      {"slug": "keep"}]})
    retry_mod._load_manifest()
    err = capsys.readouterr().err
    assert "2" in err and "slug" in err


def test_a_clean_manifest_is_unchanged(retry_mod, capsys):
    rows = [{"slug": "a", "url": "ua"}, {"slug": "b", "url": "ub"}]
    _manifest(retry_mod, {"results": rows, "ok": 2, "errors": 0, "total": 2})
    got = retry_mod._load_manifest()
    assert got["results"] == rows
    assert capsys.readouterr().err == ""


def test_a_corrupt_manifest_still_refuses_to_merge(retry_mod, capsys):
    """The outer guard must survive the inner one being added."""
    retry_mod.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    retry_mod.manifest_path().write_text("{not json", encoding="utf-8")
    assert retry_mod._load_manifest() is None


@pytest.mark.parametrize("payload", ['[]', '{"results": "a string"}', '"text"', '7'])
def test_a_wrong_container_shape_still_refuses_to_merge(retry_mod, payload):
    retry_mod.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    retry_mod.manifest_path().write_text(payload, encoding="utf-8")
    assert retry_mod._load_manifest() is None


def test_a_missing_manifest_is_still_none(retry_mod):
    assert retry_mod._load_manifest() is None


# ============================================================
# The count the page restated beside a list it derived
# ============================================================

def test_the_meaning_paragraph_counts_the_real_candidates(canopus):
    n = len(canopus.CANDIDATES)
    assert f"{n} implementations" in canopus._AFTER_BUILD_MEANING
    assert f"{n} different ways" in canopus._AFTER_BUILD_MEANING
    assert f"those {n} wrongnesses" in canopus._AFTER_BUILD_MEANING


def test_every_candidate_is_described_in_the_meaning_paragraph(canopus):
    for name in canopus.CANDIDATES:
        assert canopus._CANDIDATE_WRONGNESS[name] in canopus._AFTER_BUILD_MEANING


def test_the_word_three_is_no_longer_hardcoded(canopus):
    """The names were derived and the count was not. Now both are."""
    src = (REPO_ROOT / "scripts" / "canopus.py").read_text(encoding="utf-8")
    assert "three implementations" not in src
    assert "wrong in three different ways" not in src


def test_an_undescribed_candidate_refuses_at_import(monkeypatch):
    """A candidate with no wrongness line must fail loudly, not print silence.

    Deriving the count alone would still leave a fourth candidate undescribed
    while the sentence read as if it covered every one.

    This re-imports the module with a fourth candidate injected, rather than
    re-deriving the guard's predicate in the test. The first version did the
    latter and a mutation emptying the guard survived it: a test that
    re-implements the check it is checking asserts only that the test is
    consistent with itself.
    """
    import scripts.utils.canopus_nullstub as nullstub
    monkeypatch.setattr(nullstub, "CANDIDATES",
                        ("none", "echo", "greedy", "invented"))
    with pytest.raises(RuntimeError, match="invented"):
        _load("canopus_with_a_fourth_candidate", "scripts/canopus.py")


def test_the_guard_names_where_to_add_the_description(monkeypatch):
    """The refusal has to be actionable, not just loud."""
    import scripts.utils.canopus_nullstub as nullstub
    monkeypatch.setattr(nullstub, "CANDIDATES", ("none", "invented"))
    with pytest.raises(RuntimeError) as exc:
        _load("canopus_with_an_undescribed_candidate", "scripts/canopus.py")
    assert "_CANDIDATE_WRONGNESS" in str(exc.value)
