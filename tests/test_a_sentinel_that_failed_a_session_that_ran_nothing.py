"""The overlay sentinel, and the one session that can never be the writer.

`tests/conftest.py::pytest_sessionfinish` snapshots the operator's live overlay
at session start, diffs it at session end, and sets `session.exitstatus = 1`
when anything moved. It is a suspect list by design: a child process writes
outside the interpreter, so the diff can say WHAT changed and never WHO.

That is the right trade for a run that executes tests. It is the wrong trade for
a run that executes none.

MEASURED 2026-09-02. The full suite went red on exactly one test,
`tests/test_a_guard_that_was_green_over_an_absent_tree.py::test_the_guard_still_passes_on_this_repository`.
`scripts/dev/check-readme-numbers.py` derives the security-test count by
spawning `pytest tests/security --collect-only`; that child loads this
repository's conftest, so the sentinel armed inside it; and the compaction hook
wrote a handoff file into the operator's overlay during the 0.7 seconds the
child spent collecting. The child printed "556 tests collected" and exited 1.
The count was right, the guard was right, the tree was right, and the suite was
red.

Two defects, both fixed here:

* The sentinel accused a session that ran no test body. It now returns early on
  `--collect-only`. Nothing is lost: an import-time write is seen again by the
  ordinary run that imports the same modules.
* The guard's failure message carried the tail of stderr, and pytest writes its
  diagnosis to stdout. The message named a failure it could not explain.

Run: python3 -m pytest tests/test_a_sentinel_that_failed_a_session_that_ran_nothing.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.conftest as root_conftest  # noqa: E402
from scripts.utils import overlay_write_guard as _guard  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NUMBERS_REL = "scripts/dev/check-readme-numbers.py"


# ============================================================
# A session object thin enough to be read, real enough to drive the hook
# ============================================================

class _Reporter:
    def __init__(self):
        self.lines = []

    def write_line(self, line, **kwargs):
        self.lines.append(line)


class _PluginManager:
    def __init__(self, reporter):
        self._reporter = reporter

    def get_plugin(self, name):
        return self._reporter if name == "terminalreporter" else None


class _Config:
    def __init__(self, reporter, options):
        self.pluginmanager = _PluginManager(reporter)
        self._options = options

    def getoption(self, name, default=None):
        return self._options.get(name, default)


class _Session:
    def __init__(self, reporter, **options):
        self.config = _Config(reporter, options)
        self.exitstatus = 0


def _armed(monkeypatch, complaints):
    """Arm the sentinel's decision inputs without touching any filesystem.

    The snapshot and the diff are the guard's, tested in its own file. What is
    under test here is the conftest hook's DECISION, so the two are replaced by
    fixed answers and the hook is then run for real.
    """
    monkeypatch.setattr(_guard, "_WATCH_BEFORE", {"live": (ROOT, {})}, raising=True)
    monkeypatch.setattr(_guard, "_watch_snapshot", lambda: {"live": (ROOT, {})})
    monkeypatch.setattr(_guard, "watch_complaints", lambda before, after: list(complaints))
    monkeypatch.setattr(_guard, "_CHILD_SPAWNS", [], raising=True)


# ============================================================
# Both directions
# ============================================================

def test_a_session_that_ran_tests_is_still_failed(monkeypatch):
    """The refusal this sentinel exists for, and nothing tested it before today.

    A wall no test has watched refuse has never been observed refusing.
    """
    reporter = _Reporter()
    session = _Session(reporter, collectonly=False)
    _armed(monkeypatch, ["1 file(s) appeared in the operator's live overlay"])

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1
    assert any("ERROR:" in line for line in reporter.lines)
    assert any("appeared in the operator's live overlay" in line
               for line in reporter.lines)


def test_a_collect_only_session_is_not_accused(monkeypatch):
    """The 2026-09-02 failure, reduced to its decision.

    Identical inputs to the test above. The only difference is that this session
    runs no test body, so it cannot be the writer.
    """
    reporter = _Reporter()
    session = _Session(reporter, collectonly=True)
    _armed(monkeypatch, ["1 file(s) appeared in the operator's live overlay"])

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    assert reporter.lines == [], (
        "a collect-only session was told about writes it could not have made")


def test_a_quiet_overlay_leaves_a_real_session_alone(monkeypatch):
    """The other half of both directions: no complaints, no accusation. A hook
    that failed every session would satisfy the refusal test above."""
    reporter = _Reporter()
    session = _Session(reporter, collectonly=False)
    _armed(monkeypatch, [])

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0
    assert reporter.lines == []


def test_the_exemption_is_decided_by_the_option_and_not_by_a_missing_reporter(monkeypatch):
    """A run with no terminal reporter (xdist workers, `-p no:terminal`) still
    gets its exit status set. Before the reporter was ever consulted this was
    the only signal, and losing it would make a whole class of runs silent."""
    session = _Session(None, collectonly=False)
    _armed(monkeypatch, ["1 file(s) appeared"])

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 1


def test_an_unarmed_guard_accuses_nobody(monkeypatch):
    """`_WATCH_BEFORE` empty means no snapshot was taken, so there is nothing to
    diff and no claim to make."""
    reporter = _Reporter()
    session = _Session(reporter, collectonly=False)
    monkeypatch.setattr(_guard, "_WATCH_BEFORE", None, raising=True)
    monkeypatch.setattr(
        _guard, "watch_complaints",
        lambda before, after: pytest.fail("diffed without a snapshot"))

    root_conftest.pytest_sessionfinish(session, 0)

    assert session.exitstatus == 0


def test_the_hook_reads_the_option_pytest_actually_defines():
    """`collectonly` is the destination pytest gives `--collect-only`. A typo
    here reads as False forever and the exemption silently never applies, which
    is the failure this whole file descends from, one level up.

    Asked of a real pytest config rather than of the string in the source.
    """
    from _pytest.config import get_config

    config = get_config(["--collect-only"])
    config.parse(["--collect-only"])
    assert config.getoption("collectonly", None) is True

    plain = get_config([])
    plain.parse([])
    assert plain.getoption("collectonly", None) is False


def test_the_source_carries_the_measurement():
    """The exemption is a narrowing of a security-adjacent guard. It has to
    carry the date and the observation that justified it, or the next reader
    cannot tell a measured carve-out from a convenient one."""
    source = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    hook = source[source.index("def pytest_sessionfinish"):]
    hook = hook[:hook.index("def pytest_collection_modifyitems")]
    assert "collectonly" in hook
    assert "MEASURED 2026-09-02" in hook


# ============================================================
# The guard that could not explain its own failure
# ============================================================

def test_the_collection_failure_message_carries_stdout(monkeypatch):
    """pytest reports on stdout. A message carrying only stderr printed an empty
    section under a red headline, which is what happened on 2026-09-02."""
    numbers = _load("readme_numbers_under_test", NUMBERS_REL)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="ERROR: the overlay moved under the run\n", stderr="")

    monkeypatch.setattr(numbers.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        numbers.derive_security_test_count()

    message = str(excinfo.value)
    assert "the overlay moved under the run" in message, (
        "the guard reported a failure and threw away the only text explaining it")
    assert "stdout tail" in message
    assert "stderr tail" in message


def test_the_unparsable_output_message_still_carries_stdout(monkeypatch):
    """The sibling branch. Exit 0 with no count line is a different failure and
    it must not lose its evidence either."""
    numbers = _load("readme_numbers_under_test_2", NUMBERS_REL)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="nothing useful", stderr="")

    monkeypatch.setattr(numbers.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        numbers.derive_security_test_count()

    assert "nothing useful" in str(excinfo.value)


# ============================================================
# The real thing, under the churn that broke it
# ============================================================

class _Churn:
    """Writes into a scratch overlay until told to stop.

    A background writer rather than a single write: the child's snapshot window
    is internal to it, and the only way to land inside that window from out here
    is to keep writing. Same method the 2026-09-01 reproduction used on the
    sibling defect, at 150 ms.
    """

    def __init__(self, root: Path):
        self.root = root
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.written = 0

    def _run(self):
        index = 0
        while not self._stop.is_set():
            index += 1
            try:
                (self.root / f"churn-{index}.md").write_text(
                    "x" * index, encoding="utf-8")
                self.written += 1
            except OSError:
                pass
            time.sleep(0.1)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def _scratch_overlay(tmp_path: Path) -> Path:
    root = tmp_path / "overlay"
    (root / "outputs").mkdir(parents=True)
    (root / "outputs" / "seed.md").write_text("seed\n", encoding="utf-8")
    return root


@pytest.mark.slow
def test_the_guard_derives_its_count_while_the_overlay_is_being_written(
        monkeypatch, tmp_path):
    """The 2026-09-02 failure, driven through the real entry point.

    `derive_security_test_count` spawns a collect-only child that loads this
    repository's conftest. Before the exemption, any write to a watched overlay
    inside that child's window turned a correct count into `SystemExit`.

    The floor is measured: `tests/security` collected 556 items on 2026-09-02.
    Asserting a number keeps this test from passing on a collection that found
    nothing, which is the shape the whole campaign was about.
    """
    numbers = _load("readme_numbers_live", NUMBERS_REL)
    overlay = _scratch_overlay(tmp_path)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    with _Churn(overlay) as churn:
        count = numbers.derive_security_test_count()

    assert churn.written >= 1, "the writer never wrote; the run proves nothing"
    assert count >= 400, f"tests/security collected {count}; 556 on 2026-09-02"


@pytest.mark.slow
def test_a_child_that_runs_tests_is_still_accused_under_the_same_churn(
        monkeypatch, tmp_path):
    """The exemption narrows the sentinel; it must not have removed it.

    Same scratch overlay, same writer, a child that RUNS a test rather than
    collecting one. Asserted on the sentinel's own message rather than on the
    exit status: a child whose tests failed would exit 1 too, and that would let
    this test pass over a dead sentinel.
    """
    overlay = _scratch_overlay(tmp_path)
    env = {**dict(__import__("os").environ), "HEADING_OS_DATA": str(overlay)}

    with _Churn(overlay) as churn:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
             "--no-header", "tests/test_data_root.py"],
            cwd=str(ROOT), capture_output=True, text=True, errors="replace",
            env=env, timeout=300)

    assert churn.written >= 1
    assert "in the operator's" in proc.stdout or "ERROR:" in proc.stdout, (
        "a run that executed tests while a watched overlay changed said nothing "
        f"about it.\n{proc.stdout[-2000:]}")
    assert str(overlay) in proc.stdout, (
        "the session-scoped scratch root was not among the watched roots, so "
        "this test measured the live overlay's weather instead of its own churn")
    assert proc.returncode == 1
