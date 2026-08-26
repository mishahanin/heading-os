"""Shard scripts-utils-02-p1: the ops radar's own instruments, and two helpers.

* ``ops_signals.index_freshness_state`` watched three directories beside an
  indexer that ingests fourteen layers. A new CRM contact, auto-memory, output,
  reference file, plan, linkedin archive entry, datastore extract, chronicle
  entry, skill or rule was indexed and could never make the signal say stale.

* ``ops_signals._repo_uncommitted`` returned ``(0, 0.0)`` when ``git status``
  itself failed, so a repo git could not read reported as a repo with nothing
  to back up. It also reported ``0.0`` hours when every dirty path was a
  deletion, and 0.0 hours reads as "just now", which keeps the signal quiet.

* ``ops_signals.queue_state`` caught OSError and JSONDecodeError only, so valid
  JSON of the wrong shape took the whole radar down with an AttributeError.
  ``odin_cadence_state`` and ``_read_trend_records`` had the same hole.

* ``rmtree._clear_readonly`` replaced the mode with 0o200 instead of adding the
  write bit, and retried ``func(path)`` when ``func`` was ``os.open``.

* ``proxy_transport.call_model`` classified an empty ``stop`` retry's outcome
  after every branch had been skipped, so a truncated retry was reported as
  "returned an empty answer".

Run: python3 -m pytest tests/test_a_radar_that_watched_three_of_fourteen_layers.py
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ops_signals as ops  # noqa: E402
from scripts.utils import proxy_transport as pt  # noqa: E402
from scripts.utils.rmtree import rmtree_force  # noqa: E402


# ============================================================
# The staleness signal that watched three of fourteen layers
# ============================================================

@pytest.fixture
def index_tree(tmp_path):
    """A data root with an index built an hour ago, and an ISOLATED engine root.

    The engine root holds a copy of the shipped `config/memory-index.yaml` and
    nothing else, so the patterns under test are the real ones while the only
    files that can move the signal are the ones a test writes. Passing the live
    `ROOT` here coupled the result to whether anybody had edited an engine file
    in the last hour: on 2026-08-26 a batch of SKILL.md edits made the "ignores"
    test read stale, and it had been passing by luck rather than by isolation.
    """
    data = tmp_path / "data"
    (data / ".memory-index").mkdir(parents=True)
    db = data / ".memory-index" / "index.db"
    db.write_text("x", encoding="utf-8")
    built = time.time() - 3600
    os.utime(db, (built, built))

    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    (engine / "config" / "memory-index.yaml").write_text(
        (ROOT / "config" / "memory-index.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    os.utime(engine / "config" / "memory-index.yaml", (built, built))
    return engine, data, built


def _touch(data: Path, rel: str) -> None:
    path = data / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("new", encoding="utf-8")
    now = time.time()
    os.utime(path, (now, now))


@pytest.mark.parametrize("rel", [
    "crm/contacts/acme.md",
    "auto-memory/a-note.md",
    "outputs/research/a-brief.md",
    "reference/a-file.md",
    "plans/2026-08-26-a-plan.md",
    "context/strategy.md",
    "threads/business/a-thread.md",
    "knowledge/a-note.md",
    "knowledge/odin-brain/principles/p1.md",
    "datastore/content/linkedin-archive/2026/a-post.md",
    "datastore/deals/a-deal-extract.md",
    "chronicle/business/2026/a-day.md",
])
def test_a_file_the_indexer_ingests_makes_the_index_read_stale(index_tree, rel):
    """Nine of these twelve were invisible to the signal."""
    engine, data, _built = index_tree
    _touch(data, rel)

    signal = ops.index_freshness_state(engine, data)

    assert signal["value"]["sources_newer"] is True, (
        f"{rel} is indexed by config/memory-index.yaml and did not move the signal")
    assert signal["due"] is True


def test_a_file_the_indexer_ignores_does_not_move_the_signal(index_tree):
    """The guard must not simply answer stale for anything at all."""
    engine, data, _built = index_tree
    _touch(data, "outputs/browser/cache.md")
    _touch(data, "chronicle/personal/2026/private.md")

    assert ops.index_freshness_state(engine, data)["value"]["sources_newer"] is False


def test_the_watch_list_is_derived_from_the_indexer_config():
    patterns = ops._index_source_globs(ROOT)

    assert patterns is not None
    assert "crm/contacts/*.md" in patterns
    assert "auto-memory/*.md" in patterns
    assert ".claude/rules/*.md" in patterns
    assert "knowledge/odin-brain/principles/*.md" in patterns, "braces unexpanded"


def test_an_unreadable_config_narrows_and_says_so(index_tree, monkeypatch, capsys):
    """Silence about an exclusion reads as coverage."""
    engine, data, _built = index_tree
    monkeypatch.setattr(ops, "_INDEX_CONFIG_REL", "config/does-not-exist.yaml")
    _touch(data, "knowledge/a-note.md")

    signal = ops.index_freshness_state(engine, data)

    assert signal["value"]["sources_newer"] is True, "the fallback must still work"
    assert "covers only" in capsys.readouterr().err


def test_the_brace_expander_handles_nesting_and_absence():
    assert ops._expand_braces("a/b.md") == ["a/b.md"]
    assert ops._expand_braces("a/{x,y}/*.md") == ["a/x/*.md", "a/y/*.md"]
    assert sorted(ops._expand_braces("{p,q}/{x,y}.md")) == [
        "p/x.md", "p/y.md", "q/x.md", "q/y.md"]
    assert ops._expand_braces("a/{unclosed") == ["a/{unclosed"]


# ============================================================
# The backup signal that called an unreadable repo clean
# ============================================================

def _git(repo: Path, *args) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def broken_repo(tmp_path):
    repo = tmp_path / "brokenrepo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /nonexistent/does/not/exist\n",
                               encoding="utf-8")
    return repo


@pytest.fixture
def deleted_repo(tmp_path):
    repo = tmp_path / "delrepo"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    for i in range(3):
        (repo / f"f{i}.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    for i in range(3):
        (repo / f"f{i}.txt").unlink()
    return repo


def test_a_repo_git_cannot_read_is_not_reported_as_clean(broken_repo):
    """It said "backup: 0 uncommitted ... ok" over a repo it never read."""
    assert ops._run_git(broken_repo, ["status", "--porcelain"])[0] != 0
    assert ops._repo_uncommitted(broken_repo) == (None, None)

    signal = ops.backup_state(broken_repo, broken_repo)

    assert signal["due"] is True
    assert signal["severity"] == "high"
    assert signal["value"]["unreadable"] == 1
    assert "could not read" in signal["summary"]


def test_deletions_report_an_unknown_age_not_zero_hours(deleted_repo):
    """0.0 hours reads as "just now" and is what kept the signal quiet."""
    count, age = ops._repo_uncommitted(deleted_repo)

    assert count == 3
    assert age is None

    signal = ops.backup_state(deleted_repo, deleted_repo)

    assert signal["due"] is True
    assert signal["severity"] == "high"
    assert "age unknown" in signal["summary"]
    assert signal["value"]["oldest_age_hours"] is None


def test_a_clean_repo_is_still_quiet(tmp_path):
    repo = tmp_path / "clean"
    repo.mkdir()
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    assert ops._repo_uncommitted(repo) == (0, 0.0)
    assert ops.backup_state(repo, repo)["severity"] == "ok"


@pytest.mark.parametrize("age,expected", [
    (0.0, "ok"),
    (None, "high"),
])
def test_an_unknown_age_escalates_where_zero_reassures(age, expected):
    assert ops.classify_backup(3, age, 0)["severity"] == expected


def test_the_old_summary_shape_is_unchanged_for_a_measured_age():
    summary = ops.classify_backup(2, 30.0, 1)["summary"]
    assert summary == "backup: 2 uncommitted (30h old), 1 unpushed"


# ============================================================
# The signals that a malformed file could take down
# ============================================================

@pytest.mark.parametrize("payload", [
    "[]", "null", '"just a string"', "123",
    '{"actions": ["oops"]}', '{"actions": null}', '{"actions": {"a": 1}}',
    "{}", "not json at all", "",
])
def test_a_malformed_queue_file_degrades_instead_of_raising(tmp_path, payload):
    """One bad file used to take out every other signal beside it."""
    qpath = tmp_path / "outputs" / "operations" / "action-queue" / "queue.json"
    qpath.parent.mkdir(parents=True)
    qpath.write_text(payload, encoding="utf-8")

    signal = ops.queue_state(tmp_path)

    assert signal["value"] == {"ready": 0, "failed": 0}
    assert signal["severity"] == "ok"


def test_a_well_formed_queue_is_still_counted(tmp_path):
    qpath = tmp_path / "outputs" / "operations" / "action-queue" / "queue.json"
    qpath.parent.mkdir(parents=True)
    qpath.write_text(json.dumps({"actions": [
        {"status": "pending", "draft_status": "ready_for_review"},
        {"status": "send_failed"},
        "a stray string that must be skipped, not fatal",
    ]}), encoding="utf-8")

    signal = ops.queue_state(tmp_path)

    assert signal["value"] == {"ready": 1, "failed": 1}


@pytest.mark.parametrize("line", ["123", '"abc"', "[1,2]", "null", "true"])
def test_a_scalar_trend_line_is_dropped_not_handed_downstream(tmp_path, line):
    trend = tmp_path / "trend.jsonl"
    trend.write_text(f'{line}\n{{"overall_rate": 0.9}}\n', encoding="utf-8")

    records = ops._read_trend_records(trend, 10)

    assert records == [{"overall_rate": 0.9}]


def test_the_router_accuracy_signal_survives_a_scalar_line(tmp_path):
    trend = (tmp_path / "datastore" / "operations" / "router-accuracy"
             / "trend.jsonl")
    trend.parent.mkdir(parents=True)
    trend.write_text('123\n"abc"\n', encoding="utf-8")

    assert ops.router_accuracy_state(tmp_path)["key"] == "router_accuracy"


@pytest.mark.parametrize("stdout", ["null", "[1,2]", "5", '"text"'])
def test_a_cadence_helper_printing_a_non_object_does_not_raise(
        tmp_path, monkeypatch, stdout):
    script = tmp_path / "scripts" / "odin-cadence.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('x')\n", encoding="utf-8")

    monkeypatch.setattr(
        ops.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, ""))

    assert ops.odin_cadence_state(tmp_path)["key"] == "odin_cadence"


# ============================================================
# The rmtree that made the directory less usable than it found it
# ============================================================

def test_an_unreadable_directory_is_removed_not_raised_over(tmp_path):
    """It raised TypeError from `os.open(path)` and left the tree in place."""
    base = tmp_path / "rmt"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "f.txt").write_text("x", encoding="utf-8")
    os.chmod(base / "sub", 0o000)

    try:
        rmtree_force(base)
    finally:
        if (base / "sub").exists():
            os.chmod(base / "sub", 0o700)

    assert not base.exists()


def test_a_read_only_file_is_still_removed(tmp_path):
    """The case the handler was written for must not regress."""
    base = tmp_path / "rmt2"
    base.mkdir()
    target = base / "ro.txt"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o444)

    rmtree_force(base)

    assert not base.exists()


def test_the_handler_adds_the_write_bit_rather_than_replacing_the_mode(tmp_path):
    """The measured mode after the old handler ran was 0o200: write-only."""
    from scripts.utils.rmtree import _clear_readonly

    target = tmp_path / "dir"
    target.mkdir()
    os.chmod(target, 0o500)
    seen = []

    _clear_readonly(seen.append, str(target), None)

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & stat.S_IWUSR, "the write bit was not added"
    assert mode & stat.S_IRUSR, "read was taken away from a directory"
    assert mode & stat.S_IXUSR, "execute was taken away from a directory"
    assert seen == [str(target)], "the removal was not retried"


def test_an_absent_path_is_not_an_error(tmp_path):
    rmtree_force(tmp_path / "never-existed")


# ============================================================
# The truncation reported as an empty answer
# ============================================================

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, reason):
        self.message = _Msg(content)
        self.finish_reason = reason


class _Response:
    def __init__(self, content, reason):
        self.choices = [_Choice(content, reason)]


@pytest.fixture
def scripted_proxy(monkeypatch):
    """Drive `call_model` through a scripted sequence of proxy replies."""
    def _install(sequence):
        state = {"n": 0}

        def _create(**_kwargs):
            content, reason = sequence[min(state["n"], len(sequence) - 1)]
            state["n"] += 1
            return _Response(content, reason)

        client = type("C", (), {"chat": type("Chat", (), {
            "completions": type("Comp", (), {"create": staticmethod(_create)})})})
        monkeypatch.setattr(pt, "_make_client", lambda *a, **k: client)
        monkeypatch.setattr(pt, "load_api_key", lambda *a, **k: "not-a-live-key")
        return state

    return _install


def test_a_truncated_retry_is_named_a_truncation(scripted_proxy):
    """It said "returned an empty answer" over a 38-character reply."""
    partial = "a partial answer cut off mid-wor" + "d" * 6
    scripted_proxy([("", "stop"), (partial, "length"), (partial, "length")])

    with pytest.raises(RuntimeError) as exc:
        pt.call_model("k3", "hi", max_tokens=16, timeout=5)

    message = str(exc.value)
    assert "empty answer" not in message
    assert "cut off mid-word" in message
    assert f"{len(partial)} characters" in message
    assert partial not in message, "the fragment must never ride the exception"


def test_a_genuinely_empty_answer_still_says_empty(scripted_proxy):
    scripted_proxy([("", "stop"), ("", "stop")])

    with pytest.raises(RuntimeError, match="empty answer"):
        pt.call_model("k3", "hi", max_tokens=16, timeout=5)


def test_an_empty_stop_that_answers_on_retry_returns_the_answer(scripted_proxy):
    """The retry's whole reason for existing."""
    state = scripted_proxy([("", "stop"), ("the real answer", "stop")])

    assert pt.call_model("k3", "hi", max_tokens=16, timeout=5) == "the real answer"
    assert state["n"] == 2


def test_a_first_call_that_completes_makes_no_retry(scripted_proxy):
    state = scripted_proxy([("done", "stop")])

    assert pt.call_model("k3", "hi", max_tokens=16, timeout=5) == "done"
    assert state["n"] == 1


def test_a_content_filter_is_still_named_a_safety_block(scripted_proxy):
    scripted_proxy([("", "content_filter")])

    with pytest.raises(RuntimeError, match="content_filter"):
        pt.call_model("k3", "hi", max_tokens=16, timeout=5)


def test_a_non_empty_answer_never_reaches_the_empty_answer_raise():
    """Why the tail says "empty" and means it.

    `_is_complete` rejects exactly two things: no visible text, and
    finish_reason == "length". Every `length` path raises inside its own branch,
    so the only way to fall through to the tail is with no text at all. This
    pins that contract, which is what makes the tail's wording honest and what
    makes a fragment guard there dead code.
    """
    assert pt._is_complete("text", "tool_calls") is True
    assert pt._is_complete("text", "stop") is True
    assert pt._is_complete("text", None) is True
    assert pt._is_complete("text", "length") is False
    assert pt._is_complete("", "stop") is False


def test_an_unclassified_finish_reason_without_text_still_says_empty(scripted_proxy):
    scripted_proxy([("", "tool_calls")])

    with pytest.raises(RuntimeError, match="empty answer"):
        pt.call_model("k3", "hi", max_tokens=16, timeout=5)


def test_an_engine_side_source_also_makes_the_index_read_stale(index_tree, tmp_path):
    """Only the data root was swept in one direction of the fix.

    `.claude/rules/` and `.claude/skills/` live in the ENGINE clone, so a rule
    edited after the last build must move the signal exactly as a note does.
    """
    engine, data, _built = index_tree
    rule = engine / ".claude" / "rules" / "a-rule.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("new", encoding="utf-8")
    now = time.time()
    os.utime(rule, (now, now))

    signal = ops.index_freshness_state(engine, data)

    assert signal["value"]["sources_newer"] is True
    assert signal["due"] is True
