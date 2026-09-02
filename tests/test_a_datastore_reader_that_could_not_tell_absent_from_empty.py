"""A datastore reader that could not tell "absent" from "empty".

`scripts/datastore-log.py` answers "what appeared, changed, or vanished in the
datastore" by reading git. Every one of its answers is a count, and a count of
zero is the shape that two completely different states collapse into: a quiet
week, and a tool pointed at nothing at all. The second one prints a clean,
confident, entirely fictional report.

So the refusals get as much test surface as the answers. A missing data root, a
data root that is not a git repository, and a repository with no datastore
directory each exit 1 and name themselves on stderr; a week with nothing new
exits 0 and says zero. The tests below pin both directions, because a checker
that cannot fail is not a checker.

The second thing pinned here is the boundary git cannot see. A file that was
never committed is invisible to `new`, `changed` and `gone`, and visible only to
`untracked`. That is the whole reason `untracked` exists, and a regression there
would look like an empty list rather than an error.

Every test builds its own throwaway git repository under `tmp_path` and points
the data-root seam at it with `HEADING_OS_DATA`. Nothing reads the operator's
overlay, nothing writes to it, and no git invocation here runs inside it. The
fixture names are invented.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_ROOT))

SCRIPT = ENGINE_ROOT / "scripts" / "datastore-log.py"

_COUNT_LINE = re.compile(r"^\s*count:\s+(\d+)\s*$", re.MULTILINE)


def _load_module():
    """Import the CLI by path. Its filename is kebab-case, so `import` cannot."""
    spec = importlib.util.spec_from_file_location("datastore_log_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


datastore_log = _load_module()


# --------------------------------------------------------------------------
# scratch repositories
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _init(root: Path) -> Path:
    """A throwaway repository with a pinned identity and no ambient signing."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture Author")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def _write(root: Path, relative: str, text: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _save(root: Path, message: str) -> None:
    """Record the working tree. Scoped entirely to a tmp_path fixture repo."""
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


@pytest.fixture
def data_root(tmp_path):
    """A scratch data repo whose datastore holds two recorded files."""
    root = _init(tmp_path / "scratch-data")
    _write(root, "datastore/notes/alpha.md", "alpha\n")
    _write(root, "datastore/notes/beta.md", "beta\n")
    _write(root, "outside.md", "not in the datastore\n")
    _save(root, "seed the datastore")
    return root


def _run(root, *args: str):
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(root)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ENGINE_ROOT), env=env,
    )


def _count(stdout: str) -> int:
    match = _COUNT_LINE.search(stdout)
    assert match, f"no count line in the report:\n{stdout}"
    return int(match.group(1))


# --------------------------------------------------------------------------
# the answers
# --------------------------------------------------------------------------

def test_a_file_committed_inside_the_window_is_reported_new(data_root):
    """The ordinary case: a recorded file lands in `new` and the run succeeds."""
    result = _run(data_root, "new", "--since", "7d")

    assert result.returncode == 0, result.stderr
    assert "datastore/notes/alpha.md" in result.stdout
    assert _count(result.stdout) == 2
    # Scoping is real: a file recorded in the same commit outside the datastore
    # is not this tool's business.
    assert "outside.md" not in result.stdout


def test_a_window_with_nothing_in_it_is_an_answer_not_a_failure(data_root):
    """Zero exits 0. This is the case the refusals must not be confused with."""
    result = _run(data_root, "changed", "--since", "7d")

    assert result.returncode == 0, result.stderr
    assert _count(result.stdout) == 0
    assert "nothing" in result.stdout


def test_a_deleted_and_committed_file_is_reported_by_gone(data_root):
    _git(data_root, "rm", "-q", "datastore/notes/beta.md")
    _save(data_root, "retire beta")

    result = _run(data_root, "gone", "--since", "7d")

    assert result.returncode == 0, result.stderr
    assert _count(result.stdout) == 1
    assert "datastore/notes/beta.md" in result.stdout


def test_a_modified_and_committed_file_is_reported_by_changed(data_root):
    _write(data_root, "datastore/notes/alpha.md", "alpha, revised\n")
    _save(data_root, "revise alpha")

    result = _run(data_root, "changed", "--since", "7d")

    assert result.returncode == 0, result.stderr
    assert _count(result.stdout) == 1
    assert "datastore/notes/alpha.md" in result.stdout


# --------------------------------------------------------------------------
# the boundary git history cannot see
# --------------------------------------------------------------------------

def test_an_untracked_file_is_reported_by_untracked_and_never_by_new(data_root):
    """The reason `untracked` exists.

    A file that was never recorded is absent from every history answer, so a
    datastore where things appear and disappear without a commit reads as
    perfectly quiet until this subcommand is asked.
    """
    _write(data_root, "datastore/notes/gamma.md", "never recorded\n")

    untracked = _run(data_root, "untracked")
    assert untracked.returncode == 0, untracked.stderr
    assert "datastore/notes/gamma.md" in untracked.stdout
    assert "untracked:  1" in untracked.stdout

    new = _run(data_root, "new", "--since", "7d")
    assert new.returncode == 0, new.stderr
    assert "datastore/notes/gamma.md" not in new.stdout


def test_a_tracked_file_with_uncommitted_edits_is_kept_out_of_untracked(data_root):
    """Two different states, reported on two different lines.

    Merging them would be the same defect as merging absent with empty: an
    uncommitted edit to a recorded file is recoverable, and a file git has
    never seen is not.
    """
    _write(data_root, "datastore/notes/alpha.md", "edited, not recorded\n")

    result = _run(data_root, "untracked")

    assert result.returncode == 0, result.stderr
    assert "untracked:  0" in result.stdout
    assert "modified:   1" in result.stdout
    assert "tracked but modified" in result.stdout


def test_a_staged_rename_does_not_derail_the_entry_after_it(data_root):
    """git emits a rename's ORIGIN path as an extra field with no status code.

    Read as an ordinary entry, its first two characters are parsed as a status
    code and the rest as a path, so one rename silently invents a bogus row and
    shifts every entry after it. The untracked file added here is the entry
    after it, and it is what makes the misparse visible rather than merely
    possible.
    """
    _git(data_root, "mv", "datastore/notes/alpha.md", "datastore/notes/renamed.md")
    _write(data_root, "datastore/notes/gamma.md", "never recorded\n")

    result = _run(data_root, "untracked", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["untracked"] == ["datastore/notes/gamma.md"]
    assert payload["modified"] == ["datastore/notes/renamed.md"]
    assert payload["ignored"] == []


def test_an_ignored_file_is_counted_apart_from_an_untracked_one(data_root):
    _write(data_root, ".gitignore", "datastore/notes/scratch.tmp\n")
    _save(data_root, "add an ignore rule")
    _write(data_root, "datastore/notes/scratch.tmp", "transient\n")

    result = _run(data_root, "untracked")

    assert result.returncode == 0, result.stderr
    assert "untracked:  0" in result.stdout
    assert "ignored:    1" in result.stdout
    assert "datastore/notes/scratch.tmp" in result.stdout


# --------------------------------------------------------------------------
# the refusals
# --------------------------------------------------------------------------

def test_an_absent_data_root_is_refused_and_named(tmp_path):
    """Exit 1, and the path that is not there is printed.

    The failure mode this guards is a clean-looking zero. Without the refusal,
    a mistyped root reports an immaculate datastore with no new files.
    """
    missing = tmp_path / "no-such-data-root"

    result = _run(missing, "summary")

    assert result.returncode == 1
    assert str(missing) in result.stderr
    assert _COUNT_LINE.search(result.stdout) is None


def test_a_data_root_that_is_not_a_git_repository_says_so(tmp_path):
    root = tmp_path / "plain-directory"
    (root / "datastore").mkdir(parents=True)

    result = _run(root, "new")

    assert result.returncode == 1
    assert "not a git repository" in result.stderr
    assert str(root) in result.stderr


def test_a_repository_with_no_datastore_directory_is_refused(tmp_path):
    root = _init(tmp_path / "no-datastore")
    _write(root, "readme.md", "nothing to log\n")
    _save(root, "seed without a datastore")

    result = _run(root, "summary")

    assert result.returncode == 1
    assert "datastore" in result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("command", ["untracked", "new", "summary"])
def test_a_failing_git_invocation_exits_one_and_prints_gits_message(tmp_path, command):
    """A `.git` that exists but is not a repository.

    `resolve_scope` gets past its own checks here, so the failure surfaces from
    the git call itself, which is the branch that must not be swallowed.

    `new` is in the parameter list for a reason. Its history read first asks
    whether the repository has any commit, and the first version of that helper
    treated every non-zero exit as "no commits yet". An unreadable repository
    therefore reported zero new files and exited 0, which is this tool's own
    founding defect reproduced inside it.
    """
    root = tmp_path / "broken-repo"
    (root / "datastore").mkdir(parents=True)
    (root / ".git").mkdir()

    result = _run(root, command)

    assert result.returncode == 1
    assert "git failed" in result.stderr
    assert result.stderr.strip() != "[FAIL] git failed:"
    assert _COUNT_LINE.search(result.stdout) is None


def test_a_repository_with_no_commits_is_a_zero_not_an_error(tmp_path):
    """The other side of the same fork.

    An empty history holds no new file, and saying so is an answer. Only an
    unreadable repository is a failure.
    """
    root = _init(tmp_path / "no-commits")
    (root / "datastore").mkdir()

    result = _run(root, "new")

    assert result.returncode == 0, result.stderr
    assert _count(result.stdout) == 0


def test_an_unparseable_since_is_rejected_rather_than_widened(data_root):
    """git reads a date it cannot parse as the epoch and answers with everything.

    A silently widened window is worse than an error, because it looks like a
    result.
    """
    result = _run(data_root, "new", "--since", "last tuesday-ish")

    assert result.returncode == 2
    assert "--since" in result.stderr


# --------------------------------------------------------------------------
# the flags
# --------------------------------------------------------------------------

def test_limit_caps_the_printed_list_without_changing_the_count(data_root):
    for name in ("delta", "epsilon", "zeta"):
        _write(data_root, f"datastore/notes/{name}.md", f"{name}\n")
    _save(data_root, "add three more")

    result = _run(data_root, "new", "--since", "7d", "--limit", "2")

    assert result.returncode == 0, result.stderr
    assert _count(result.stdout) == 5
    listed = [
        line.strip() for line in result.stdout.splitlines()
        if line.strip().startswith("datastore/")
    ]
    assert len(listed) == 2
    assert "--limit 2" in result.stdout


def test_the_json_counts_match_the_human_counts(data_root):
    _write(data_root, "datastore/notes/gamma.md", "never recorded\n")

    human = _run(data_root, "new", "--since", "7d")
    machine = _run(data_root, "new", "--since", "7d", "--json")

    assert human.returncode == 0 and machine.returncode == 0
    payload = json.loads(machine.stdout)
    assert payload["count"] == _count(human.stdout)
    assert payload["subcommand"] == "new"
    assert payload["since"] == "7 days ago"

    human_untracked = _run(data_root, "untracked")
    machine_untracked = _run(data_root, "untracked", "--json")
    untracked = json.loads(machine_untracked.stdout)
    assert untracked["untracked_count"] == 1
    assert f"untracked:  {untracked['untracked_count']}" in human_untracked.stdout


def test_json_stays_parseable_when_the_list_is_capped(data_root):
    for name in ("delta", "epsilon", "zeta"):
        _write(data_root, f"datastore/notes/{name}.md", f"{name}\n")
    _save(data_root, "add three more")

    result = _run(data_root, "new", "--since", "7d", "--json", "--limit", "1")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 5
    assert len(payload["files"]) == 1
    assert payload["truncated"] is True


def test_summary_separates_what_is_on_disk_from_what_is_tracked(data_root):
    _write(data_root, "datastore/notes/gamma.md", "never recorded\n")

    result = _run(data_root, "summary", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["tracked_count"] == 2
    assert payload["on_disk_count"] == 3
    assert payload["untracked_count"] == 1
    assert payload["new_count"] == 2
    assert payload["gone_count"] == 0


# --------------------------------------------------------------------------
# the --since converter, directly
# --------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("7d", "7 days ago"),
    ("30d", "30 days ago"),
    ("24h", "24 hours ago"),
    ("2026-08-01", "2026-08-01"),
])
def test_since_converts_to_a_string_git_accepts(given, expected):
    assert datastore_log.parse_since(given) == expected


@pytest.mark.parametrize("given", ["", "7", "d7", "7 days", "2026-13-01", "yesterday"])
def test_since_refuses_what_git_would_silently_misread(given):
    with pytest.raises(Exception) as caught:
        datastore_log.parse_since(given)
    assert "duration" in str(caught.value) or "ISO date" in str(caught.value)
