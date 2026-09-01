"""The push wall listed paths, read them after, and refused when one moved.

`scripts/push-all.py::engine_content_scan` gathers the push delta and then reads
each file. `engine_text_files` filters on `is_file()` in between, so the window
is narrow but open, and `_push_delta_files` includes `git ls-files --others
--exclude-standard`, which puts transient untracked files in scope. A file
created and deleted inside that window made `read_text` raise; the handler put it
in `unscanned`, and `unscanned` prints REFUSING TO PUSH and exits 2.

That is the gate blocking on its own timing over a file carrying nothing, and it
is not hypothetical: `scripts/run-tests.py` builds the pre-push gate as `-n auto
-m "not acceptance"` without deselecting `slow`, so a test that writes a scratch
file under `tests/` runs INSIDE the gate while other workers sweep. One `git
push` was enough on 2026-09-01; no second agent was needed.

## Why "the file is gone, so skip it" is the WRONG argument

It is wrong in general. A tracked file deleted from the worktree keeps its
content in the INDEX, and if the index were what got committed, a blind skip
would let a staged secret past the last wall by the simple move of deleting the
file after staging it. A first version of this fix therefore read the index copy
and scanned that instead.

The measurement retired that version. `scripts/push-all.py` stages with
`git add -A`, and MEASURED 2026-09-01 in a scratch repo: a file added to the
index and then removed from the worktree had an index entry holding its content
before `add -A` and NO entry after it. `git add -A` stages the deletion. So a
path absent from the worktree at scan time is absent from the commit this run
makes, and there is genuinely nothing for the push to carry.

The skip is safe because of the staging command, not because of the missing file.
`test_the_skip_is_only_safe_because_the_commit_stages_deletions` and
`test_the_push_step_still_stages_with_add_dash_A` hold that dependency together,
so narrowing the staging command turns the skip back into a hole and fails HERE
rather than in silence.

Run: .venv/bin/python -m pytest \\
        tests/test_a_wall_that_refused_over_a_file_it_could_not_push.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# push-all.py calls ensure_venv() at MODULE scope; tests/conftest.py sets the
# guard that stops it re-execing the pytest process. Same idiom as
# tests/test_the_last_wall_skipped_what_it_could_not_decode.py.
PUSH_ALL = ROOT / "scripts" / "push-all.py"
_spec = importlib.util.spec_from_file_location("push_all_vanished", PUSH_ALL)
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

# The name lives in the invented overlay below and nowhere else. The engine
# carries no real entity, so the fixture cannot either.
REAL_ENTITY = "Zenon Makarios"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path, name: str = "engine") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _overlay(tmp_path: Path) -> Path:
    """A minimal DATA overlay, so the gate runs instead of no-opping."""
    data = tmp_path / "data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    (data / "crm" / "contacts" / "zenon-makarios.md").write_text(
        f"---\nname: {REAL_ENTITY}\n---\n", encoding="utf-8")
    return data


# ============================================================
# The defect: a path that vanished must not block the push
# ============================================================


def _phantom_past_the_filter(monkeypatch, rel: str = "docs/ghost-1234.md"):
    """Hand the read loop a path that is not on disk, as the race does.

    WHY `engine_text_files` AND NOT `_push_delta_files`. The selector filters on
    `is_file()` (`scripts/utils/engine_guard.py`), so a path injected into the
    DELTA is dropped before the read and the branch under test is never reached
    -- measured while writing this file, when three tests failed for exactly
    that reason. The window the race lives in is between that `is_file()` and
    the `read_text()` a few lines later, so the read is where the missing path
    has to arrive. Patching here reproduces that state deterministically,
    instead of racing a real file and passing or failing by luck.
    """
    real = push_all.engine_text_files
    monkeypatch.setattr(push_all, "engine_text_files",
                        lambda root, candidates: real(root, candidates) + [rel])
    return rel


def test_a_path_that_vanished_does_not_refuse_the_push(tmp_path, capsys, monkeypatch):
    """The reported failure. Nothing to scan is not the same as unverified."""
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("nothing to see\n", encoding="utf-8")
    _git(repo, "add", "-A")
    rel = _phantom_past_the_filter(monkeypatch)

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None, (
        "a path that vanished before it could be read blocked the push; "
        "`git add -A` stages that deletion, so there is no content for the "
        "push to carry")

    out = capsys.readouterr().out
    assert rel in out, (
        "the dropped path was not named, so the gate narrowed its corpus in "
        "silence while still printing like a complete pass")
    assert "REFUSING TO PUSH" not in out


def test_the_dropped_path_is_reported_as_vanished_not_as_unreadable(
        tmp_path, capsys, monkeypatch):
    """Two different states, and an operator acts differently on each.

    "Could not read" says go re-save the file as UTF-8. "Vanished" says there is
    nothing to do. Collapsing them sends the operator hunting for a file that is
    not there.
    """
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("fine\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _phantom_past_the_filter(monkeypatch)

    push_all.engine_content_scan(repo, _overlay(tmp_path))

    out = capsys.readouterr().out
    assert "vanished before they could be read" in out
    assert "could not read" not in out


# ============================================================
# What makes the skip safe. Both halves, or the skip is a hole.
# ============================================================


def test_the_skip_is_only_safe_because_the_commit_stages_deletions(tmp_path):
    """MEASURED, not assumed: `git add -A` removes the index entry.

    This is the whole justification for skipping. If `git add -A` kept the
    staged content of a file deleted from the worktree, the push would carry
    content the content gate declined to read, and the skip above would be the
    way past the last wall.

    Asked of git itself rather than of a docstring, because it is a claim about
    another program's behaviour and this repository has been burned by claims
    that were reasoned instead of run.
    """
    repo = _init_repo(tmp_path, "staging")
    (repo / "docs").mkdir()
    leak = repo / "docs" / "leak.md"
    leak.write_text(f"contact {REAL_ENTITY}\n", encoding="utf-8")

    _git(repo, "add", "docs/leak.md")
    staged_before = subprocess.run(
        ["git", "-C", str(repo), "show", ":docs/leak.md"],
        capture_output=True, text=True)
    assert staged_before.returncode == 0 and REAL_ENTITY in staged_before.stdout, (
        "the fixture never staged anything, so the rest of this test is vacuous")

    leak.unlink()
    _git(repo, "add", "-A")

    staged_after = subprocess.run(
        ["git", "-C", str(repo), "show", ":docs/leak.md"],
        capture_output=True, text=True)
    assert staged_after.returncode != 0, (
        "`git add -A` LEFT the staged content of a file deleted from the "
        "worktree. The skip in engine_content_scan is now a hole: that content "
        "would be committed and pushed without ever being scanned. Either scan "
        "the index copy, or refuse.")


def test_the_push_step_still_stages_with_add_dash_A():
    """The other half. The measurement above is about `git add -A` specifically.

    Asked of the SOURCE, because reaching the commit step needs a remote, a
    clean gate run and a real push. A narrower staging command (`git add .`,
    `git add <paths>`, `git commit` on a hand-built index) does not necessarily
    stage deletions, and the skip's safety would evaporate with no test failing.
    """
    tree = ast.parse(PUSH_ALL.read_text(encoding="utf-8"))

    staged_all = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List):
            continue
        parts = [e.value for e in first.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if parts[:3] == ["git", "add", "-A"]:
            staged_all = True

    assert staged_all, (
        "scripts/push-all.py no longer stages with `git add -A`. The content "
        "gate SKIPS a delta path that vanished before it could be read, and "
        "that is only safe because `add -A` stages the deletion too. Re-read "
        "the FileNotFoundError branch in engine_content_scan before changing "
        "how this script stages.")


# ============================================================
# Not over-caught: every other failure still refuses
# ============================================================


def test_a_file_that_is_there_and_cannot_be_decoded_still_refuses(tmp_path, capsys):
    """The other handler must survive the new one being added above it.

    A file whose bytes are not UTF-8 is a real fault about a path that IS there,
    and it must not be filed under "vanished". UTF-16 with its byte-order mark
    is what an editor writes when nobody asked it to: valid text in its own
    encoding, invalid UTF-8 from byte zero. (Plain `utf-16-le` of ASCII would
    NOT work here -- it is NUL bytes between ASCII bytes, all of which is valid
    UTF-8, and an earlier sibling test passed over a broken wall for exactly
    that reason. The BOM is what makes it genuinely undecodable.)
    """
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_bytes("a name lives here\n".encode("utf-16"))
    _git(repo, "add", "-A")

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "could not read" in out and "docs/note.md" in out
    assert "vanished" not in out, (
        "an undecodable file was filed as vanished, so widening the new "
        "handler to OSError would silently drop unreadable content")


def test_the_new_handler_is_narrow_and_the_old_one_survived():
    """Asked of the SOURCE, because the two handlers sit on one `try`.

    The behavioural tests above prove each arm in isolation. This one proves the
    SHAPE: a `FileNotFoundError` arm that skips, and a wider arm below it that
    still records and refuses. Widening the first to `OSError` would make both
    behavioural tests above pass while every unreadable file was dropped as
    "vanished", because a vanished file is an OSError too.
    """
    tree = ast.parse(PUSH_ALL.read_text(encoding="utf-8"))

    shapes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or len(node.handlers) < 2:
            continue
        names = [tuple(sorted(n.id for n in ast.walk(h.type or ast.Name(id="?"))
                              if isinstance(n, ast.Name)))
                 for h in node.handlers]
        shapes.append(names)

    assert ("FileNotFoundError",) in [n for s in shapes for n in s], (
        "no `except FileNotFoundError` arm remains in scripts/push-all.py")
    assert any(s[0] == ("FileNotFoundError",)
               and any("OSError" in later for later in s[1:])
               for s in shapes), (
        "the narrow FileNotFoundError arm no longer sits ABOVE a wider "
        "OSError arm. Either the skip now swallows real read errors, or the "
        "refusal for an unreadable engine file is gone.")


def test_a_real_entity_in_a_file_that_is_there_is_still_refused(tmp_path, capsys):
    """The gate must still do its job. A fix that turns a crash into a pass,
    or a refusal into a skip, is worse than the thing it replaced."""
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text(
        f"contact {REAL_ENTITY} about this\n", encoding="utf-8")
    _git(repo, "add", "-A")

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "REFUSING TO PUSH" in out and "docs/note.md" in out


def test_a_present_clean_repository_still_passes(tmp_path):
    """The ordinary case. A gate that refuses everything is not a gate."""
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("nothing sensitive\n", encoding="utf-8")
    _git(repo, "add", "-A")

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None
