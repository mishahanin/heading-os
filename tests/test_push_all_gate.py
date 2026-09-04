"""push-all.py enforces the single authoritative test gate AND the unbypassable
engine/data leak wall.

The regression suite is run by the engine's versioned pre-push hook (one gate,
on every push to engine). push-all no longer runs it a second time itself; it
refuses to push when that hook is not armed, so the gate can never be silently
skipped on an un-provisioned clone. These tests cover that enforcement predicate
plus engine_clean_scan() -- the pure-code routing wall that no `--no-verify` can
get past.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

# Every child this file spawns is `git` in a scratch tree, and `git` has never
# read HEADING_OS_DATA. Pinning it away from the operator's live overlay costs
# these tests nothing and removes them from the reachability ratchet in
# tests/conftest.py. See the `scratch_data_root` fixture for the measurement.
pytestmark = pytest.mark.usefixtures("scratch_data_root")

ROOT = Path(__file__).resolve().parent.parent

# push-all.py calls ensure_venv() at MODULE scope, so loading it here would
# os.execv the whole pytest process under any interpreter that is not
# .venv/bin/python. The guard against that is set once in tests/conftest.py,
# which is collected before this module; see the comment there, and
# tests/test_venv_relaunch_guard.py for the test that measures it.
_spec = importlib.util.spec_from_file_location("push_all", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _write(repo, rel, body="x"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_engine_clean_scan_passes_on_clean_tree(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    # No exit -> returns None cleanly.
    assert push_all.engine_clean_scan(repo) is None


def test_engine_clean_scan_refuses_on_data_artifact(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    _write(repo, "crm/contacts/john.md", "name: John\n")  # routes private
    _git(repo, "add", "-A")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "crm/contacts/john.md" in capsys.readouterr().out


def test_engine_clean_scan_refuses_on_untracked_data(tmp_path, capsys):
    # A private file not yet staged is still caught -- `git add -A` would sweep it in.
    repo = _init_repo(tmp_path)
    _write(repo, "outputs/operations/leak.md", "plan\n")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(repo)
    assert exc.value.code == 2
    assert "outputs/operations/leak.md" in capsys.readouterr().out


def test_the_push_delta_includes_untracked_files(tmp_path):
    """The content walls scan this set, and `git add -A` sweeps untracked in.

    Found by the 2026-08-23 audit and confirmed by reading the call order:
    `engine_content_scan` runs at step 0 of `push_repo`, BEFORE the commit, over
    `_push_delta_files`. That set was built from `git diff` alone, which sees
    only tracked files - so a brand-new file was committed and pushed a moment
    later without its CONTENT ever being read. The routing wall beside it
    (`engine_clean_scan`) has always seen untracked files, and the two must
    agree about what is about to be pushed.

    Scanning an untracked file that `--no-commit` will leave behind is the safe
    direction and is what the routing wall already does.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/tracked.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, "docs/brand-new.md", "real name here\n")   # never added

    assert "docs/brand-new.md" in push_all._push_delta_files(repo)


def test_the_push_delta_leaves_ignored_files_out(tmp_path):
    """An ignored file is not going to be pushed, so scanning it would only
    produce refusals over scratch files nobody is sending anywhere."""
    repo = _init_repo(tmp_path)
    _write(repo, ".gitignore", ".sessions/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _write(repo, ".sessions/token.json", "secret\n")

    assert ".sessions/token.json" not in push_all._push_delta_files(repo)


def test_the_push_delta_reports_non_ascii_names_unquoted(tmp_path):
    """Same 2026-08-23 defect as in `engine_guard.repo_carried_paths`.

    Without `-z`, git returns `"docs/\\320\\276\\320\\261\\320\\267\\320\\276\\321\\200.md"`.
    The content scanner is then handed a name that opens no file, so the scan
    reads nothing and reports clean.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "docs/обзор.md", "text\n")
    _git(repo, "add", "-A")

    delta = push_all._push_delta_files(repo)
    assert "docs/обзор.md" in delta, delta
    assert not any(f.startswith('"') for f in delta)


def test_the_push_delta_survives_a_carriage_return_in_a_name(tmp_path):
    """`_z_paths` reads git's bytes itself instead of using the shared text-mode
    `run`, and this is the case that makes the difference observable.

    Text mode turns on universal newlines with no `newline=` knob on
    `subprocess`, so every CR byte becomes LF. `-z` is no defence: git already
    emitted the name verbatim and the rewrite happens afterwards, in Python. The
    mangled name opens no file, so a file about to be pushed reaches
    `_run_scanner` and `engine_content_scan` as a name that reads nothing, and
    both report clean.

    MEASURED 2026-09-01: switching `_z_paths` to `text=True` left this file,
    tests/test_push_all_orchestration.py and eleven push-wall neighbours green,
    including the two that exist for non-ASCII names. Cyrillic is valid UTF-8 and
    contains no CR, so every existing case passed through the defect untouched.
    """
    repo = _init_repo(tmp_path)
    name = "two\rlines.md"
    (repo / name).write_text("body\n", encoding="utf-8")
    _git(repo, "add", "-A")

    delta = push_all._push_delta_files(repo)
    assert name in delta, delta
    assert "two\nlines.md" not in delta, (
        "the CR was rewritten to LF: the scanner is handed a name that opens "
        "nothing")
    assert (repo / name).is_file(), "the reported name does not open the file"


def test_the_push_delta_survives_a_byte_that_is_not_utf8_in_a_name(tmp_path):
    """`surrogateescape`, not `replace`, and the two are only distinguishable on
    a name that is not valid UTF-8 at all.

    `replace` substitutes U+FFFD, which is lossy and irreversible: the resulting
    string names no file on disk, so the same silent skip follows. The existing
    non-ASCII case uses Cyrillic, which IS valid UTF-8 and decodes identically
    under both handlers, so swapping them left every test in this file and its
    eleven neighbours green when measured on 2026-09-01.

    ext4 filenames are bytes, so `b"\\xff"` in one is legal and reachable; the
    DATA clone carries non-ASCII names today, and one damaged byte in a name is
    the ordinary way this arrives.
    """
    repo = _init_repo(tmp_path)
    raw = b"od\xffd.md"
    (repo / os.fsdecode(raw)).write_text("body\n", encoding="utf-8")
    _git(repo, "add", "-A")

    delta = push_all._push_delta_files(repo)
    assert os.fsdecode(raw) in delta, delta
    assert not any("�" in f for f in delta), (
        f"a name was decoded lossily and now opens nothing: {delta}")
    assert (repo / os.fsdecode(raw)).is_file()


def _make_hook(tmp_path, body: str):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(body, encoding="utf-8")
    return tmp_path


def test_gate_armed_true_when_hook_runs_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\nexec python scripts/run-tests.py\n")
    assert push_all._pre_push_gate_armed(repo) is True


def test_gate_not_armed_when_hook_missing(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    assert push_all._pre_push_gate_armed(tmp_path) is False


def test_gate_not_armed_when_hook_does_not_run_tests(tmp_path):
    repo = _make_hook(tmp_path, "#!/usr/bin/env bash\necho noop\n")
    assert push_all._pre_push_gate_armed(repo) is False


# ============================================================
# RepoNotPushable: a refusal about one repo, not about the run
#
# Promoted from tests/contract/2026-07-30-backup-per-repo-refusal/, the frozen
# contract of the slice that introduced the type. These five were written before
# RepoNotPushable existed and are unchanged apart from this banner.
# ============================================================

def _repo_on_branch(tmp_path, branch):
    """A git repo with one commit, checked out on *branch*."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["git", "init", "-q"],
                 ["git", "config", "user.email", "builder@example.invalid"],
                 ["git", "config", "user.name", "Builder"]):
        push_all.run(args, repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    push_all.run(["git", "add", "."], repo)
    push_all.run(["git", "commit", "-q", "-m", "one"], repo)
    if branch != "main":
        push_all.run(["git", "checkout", "-q", "-b", branch], repo)
    else:
        push_all.run(["git", "branch", "-M", "main"], repo)
    return repo


def test_a_branch_that_is_not_main_raises_rather_than_exiting(tmp_path):
    """sys.exit here is what silently cancelled the DATA backup. The type says
    this repository cannot be pushed, never that the run must stop."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert "feat/x" in str(caught.value)


def test_the_branch_check_is_reached_under_dry_run(tmp_path):
    """The dry-run return sat ABOVE the branch check, so a dry run reported no
    skip at all. A dry run that hides the one thing this change surfaces lies."""
    repo = _repo_on_branch(tmp_path, "feat/x")

    with pytest.raises(push_all.RepoNotPushable):
        push_all.push_repo("R", repo, "m", False, True, {})


def test_an_unarmed_suite_gate_raises_and_names_its_installer(tmp_path):
    repo = _repo_on_branch(tmp_path, "main")

    with pytest.raises(push_all.RepoNotPushable) as caught:
        push_all.push_repo("ENGINE", repo, "m", False, True, {},
                           is_engine=True, test_gate=True)
    assert "install-git-hooks" in str(caught.value)


def test_the_suite_gate_is_keyed_on_test_gate_not_on_is_engine(tmp_path):
    """The two flags are separate on purpose and this is the test that says why.

    `is_engine` turns on the engine LEAK scans; `test_gate` turns on the suite
    precondition. `main()` checked the suite gate ABOVE its single-repo branch,
    so it covered the pre-cutover mode too, and that mode pushes this same
    engine clone with `is_engine` deliberately OFF because its data files are
    tracked legitimately there. One flag serving both would have narrowed a
    security check from two modes to one while looking like a pure move.
    """
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.push_repo("repo", repo, "m", False, True, {},
                              is_engine=True) is None
    with pytest.raises(push_all.RepoNotPushable):
        push_all.push_repo("repo", repo, "m", False, True, {}, test_gate=True)


def test_the_suite_gate_is_not_a_precondition_of_the_data_overlay(tmp_path):
    """A do-not-break guard rather than new behaviour. The DATA overlay has no
    pre-push gate and never needed one; requiring it there would refuse every
    data backup on every machine."""
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.push_repo("DATA", repo, "m", False, True, {}) is None


# ============================================================
# The remote-identity wall as a push-all precondition
#
# Stop-the-world, not per-repository. A refusal about a branch says this repo
# cannot be pushed and nothing about the others. A misconfigured remote says
# the configuration is wrong in a way the operator must see before anything
# else leaves the machine, so every other repository in the run is suspect for
# the same reason.
# ============================================================

def test_a_remote_objection_exits_2_rather_than_raising(tmp_path, monkeypatch, capsys):
    repo = _repo_on_branch(tmp_path, "main")
    monkeypatch.setattr(push_all, "remote_objection",
                        lambda *a, **k: "R pushes to the ENGINE remote (x/y).")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert exc.value.code == 2
    assert "ENGINE remote" in capsys.readouterr().out


def test_the_remote_objection_is_not_a_reponotpushable(tmp_path, monkeypatch):
    """_attempt absorbs RepoNotPushable and lets everything else fly. This
    refusal must be in the second group, so the type is asserted directly."""
    repo = _repo_on_branch(tmp_path, "main")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit):
        push_all.push_repo("R", repo, "m", False, False, {})


def test_a_remote_objection_beats_a_branch_skip(tmp_path, monkeypatch):
    """Ordering, and it is a security decision rather than a style one. A repo
    on a feature branch raises RepoNotPushable, which _attempt absorbs. If the
    branch check ran first, a misconfigured remote on that repo would never be
    reported at all."""
    repo = _repo_on_branch(tmp_path, "feat/x")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", False, False, {})
    assert exc.value.code == 2


def test_the_refusal_is_reported_under_dry_run_and_writes_nothing(
        tmp_path, monkeypatch):
    """A preview that hides a refusal lies. Evaluating a precondition writes
    nothing, so a dry run can afford to be honest here too."""
    repo = _repo_on_branch(tmp_path, "main")
    before = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    monkeypatch.setattr(push_all, "remote_objection", lambda *a, **k: "nope")

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", True, True, {})
    assert exc.value.code == 2
    after = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    assert after == before  # no commit was made
    assert (repo / "b.txt").exists()  # and the working tree is untouched


def test_a_repo_with_no_remote_at_all_raises_no_objection(tmp_path):
    """Every other test in this file builds a remoteless repo and expects the
    old behaviour, so the real un-stubbed function must stay silent on one.
    This asserts that directly rather than leaving it implicit."""
    repo = _repo_on_branch(tmp_path, "main")

    assert push_all.remote_objection(repo) is None
    assert push_all.push_repo("DATA", repo, "m", False, True, {}) is None


# ============================================================
# The composition: a REAL objection reaching a real exit code
#
# Promoted from tests/contract/2026-07-30-remote-identity-wall/, the frozen
# contract of the slice that introduced the wall. Unchanged apart from this
# banner. Every other refusal test above stubs remote_objection, so the two
# legs -- an objection is produced, an objection becomes exit 2 -- are each
# proved and their join is not. A wall wired to a consumer that never calls it
# passes all of them and protects nothing. These two measure the join.
# ============================================================

def _split_repo_aimed_at_the_engine(monkeypatch, tmp_path):
    """A DATA overlay whose origin is the ENGINE's bare remote, posed split.

    The pose is patched on git_push because that is where the wall reads the
    two roots from, and push_all imported the function itself rather than the
    module, so the roots must be moved where the function looks for them.
    """
    import scripts.utils.git_push as git_push

    def _make(base, label):
        remote, work = base / "remote.git", base / label
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                       check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(work)],
                       check=True, capture_output=True)
        _git(work, "config", "user.email", "builder@example.invalid")
        _git(work, "config", "user.name", "Builder")
        (work / "f.txt").write_text("x\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "one")
        _git(work, "remote", "add", "origin", str(remote))
        return remote, work

    engine_remote, engine = _make(tmp_path / "e", "engine")
    _data_remote, data = _make(tmp_path / "d", "data-overlay")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: engine)
    monkeypatch.setattr(git_push, "get_data_root", lambda: data)
    _git(data, "remote", "set-url", "origin", str(engine_remote))
    return engine_remote, data


def test_push_all_exits_2_on_a_real_objection_end_to_end(monkeypatch, tmp_path):
    """dry_run is True on purpose: the refusal must be reported by a preview as
    well, and evaluating a precondition writes nothing, so the honest preview
    and the safe test are the same run."""
    _engine_remote, data = _split_repo_aimed_at_the_engine(monkeypatch, tmp_path)

    with pytest.raises(SystemExit) as caught:
        push_all.push_repo("DATA", data, "m", False, True, {})
    assert caught.value.code == 2


def test_the_end_to_end_refusal_pushes_nothing(monkeypatch, tmp_path):
    """Refuse, then push, is not a wall. The bare remote must stay empty.

    The failing output of this test at freeze time was the defect itself:
    "pushed & verified [0 0] in sync with origin/main". The overlay reached the
    engine's remote in a sandbox.
    """
    engine_remote, data = _split_repo_aimed_at_the_engine(monkeypatch, tmp_path)

    with pytest.raises(SystemExit):
        push_all.push_repo("DATA", data, "m", False, False, {})
    has_main = subprocess.run(
        ["git", "-C", str(engine_remote), "show-ref", "--verify",
         "refs/heads/main"],
        capture_output=True,
    )
    assert has_main.returncode != 0


# ============================================================
# The content gate must not skip silently when the overlay IS present
# ============================================================
def _overlay(tmp_path: Path, curated: str | None = None) -> Path:
    data = tmp_path / "data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    (data / "crm" / "contacts" / "zenon-makarios.md").write_text(
        "---\nname: Zenon Makarios\n---\n", encoding="utf-8")
    if curated is not None:
        (data / "config" / "content-denylist.yaml").write_text(curated, encoding="utf-8")
    return data


def test_the_content_gate_refuses_when_the_denylist_breaks_with_an_overlay(tmp_path, capsys):
    """The 2026-08-23 hole's other half. `build_denylist` degrading made
    `engine_content_scan` return silently, so a broken curated list produced a
    clean-looking push with no content wall at all. Silence is only correct when
    there is no overlay to harvest.
    """
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    data = _overlay(tmp_path, curated="companies: [unclosed\n  - x: : :\n")

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, data)
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "denylist could not be built" in out


def test_the_content_gate_stays_quiet_with_no_overlay(tmp_path):
    """A public clone or CI run has no overlay. Skipping is correct and silent."""
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    assert push_all.engine_content_scan(repo, tmp_path / "no-such-overlay") is None


def test_the_content_gate_runs_normally_on_a_healthy_overlay(tmp_path):
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/foo.py", "print(1)\n")
    _git(repo, "add", "-A")
    data = _overlay(tmp_path, curated='companies: ["Krellide Systems"]\n')
    assert push_all.engine_content_scan(repo, data) is None


def test_the_content_gate_refuses_an_engine_file_it_could_not_decode(tmp_path, capsys):
    """"Unverified is not clean." A file whose bytes are not UTF-8 is RECORDED
    and refused, never skipped with a `continue`.

    The refusal had no witness: MEASURED 2026-09-01, replacing `if unscanned:`
    with `if False:` left this file and eleven push-wall neighbours green, so the
    last wall could have gone back to passing a push over a file nobody read.
    """
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    # UTF-16 bytes under a .md suffix: `engine_text_files` keeps it (the suffix
    # says text), and `read_text(encoding="utf-8")` cannot decode it.
    (repo / "docs" / "note.md").write_bytes("a note\n".encode("utf-16"))
    _git(repo, "add", "-A")
    data = _overlay(tmp_path, curated='companies: ["Krellide Systems"]\n')

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, data)
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "could not read" in out, out
    assert "docs/note.md" in out, out


# ============================================================
# THE JOIN: push_repo actually CALLS each wall, and stops there
#
# Every test above proves a wall REFUSES when it is called. None of them proved
# it is called. That distinction is the one this file's own banner at the
# remote-identity section states -- "A wall wired to a consumer that never calls
# it passes all of them and protects nothing" -- and it was measured for exactly
# one wall, `remote_objection`.
#
# MEASURED 2026-09-01 by mutation against this file, tests/test_push_all_
# orchestration.py, and eleven push-wall neighbours (the history wall, the
# different-world wall, the leak gate that counted what it never opened, the
# rename wall, the engine tree-clean gate, the two secret-filename walls, the
# wrong-moment walls, the quoted-path credential wall, the leak path matrix, and
# the locale wall). Deleting the CALL from `push_repo` left every one of them
# green:
#
#     content_scan(repo)                     deleted -> SURVIVED
#     engine_clean_scan(repo)                deleted -> SURVIVED
#     engine_content_scan(repo, data_root)   deleted -> SURVIVED
#     the SECRET_TRACKED filename filter     disabled -> SURVIVED
#     the .memory-index/ check               disabled -> SURVIVED
#
# So five of the walls this whole file exists to hold could have been unwired by
# a one-line edit, with a green suite and a push that reported success.
#
# These tests assert the SIDE EFFECT, never a printed refusal: `supervised_push`
# is replaced by a recorder that must never fire, and HEAD must not move, so a
# wall that printed and returned (the `.githooks/pre-push-data` shape found on
# 2026-08-31: "push blocked" on stdout, exit 0) fails here.
# ============================================================

# Synthesised at import, never spelled as one literal: a real-shaped token in
# this file would be stopped by the repository's own commit gate before the test
# could run. Same construction as
# tests/test_two_secret_walls_that_split_a_filename_in_half.py.
_FAKE_TOKEN = "ghp" + "_" + ("abcdefghijklmnopqrstuvwxyz" + "0123456789" * 2)[:36]


def _wired_repo(tmp_path):
    """A repo with one committed file, ready for `push_repo` to be driven over."""
    repo = _init_repo(tmp_path)
    _write(repo, "scripts/base.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    # `git init` names the default branch from the host's config, and step 4
    # refuses anything that is not `main`. Pinned so the anti-vacuity case below
    # reaches the push primitive on a machine whose default is `master`.
    _git(repo, "branch", "-M", "main")
    return repo


@pytest.mark.parametrize("wall,rel,body,is_engine,with_overlay,expected", [
    pytest.param("content_scan", "notes.md", _FAKE_TOKEN + "\n", False, False,
                 "secret-like CONTENT", id="secret-content"),
    pytest.param("engine_clean_scan", "crm/contacts/invented.md",
                 "name: Invented Person\n", True, False,
                 "data-class artifact", id="routing-leak"),
    pytest.param("engine_content_scan", "docs/leak.md",
                 "a note about Zenon Makarios\n", True, True,
                 "real-entity CONTENT", id="real-entity-content"),
    pytest.param("secret-tracked-filename", ".env", "SOME_KEY=placeholder\n",
                 False, False, "secret-like tracked files", id="secret-filename"),
    pytest.param("memory-index", ".memory-index/index.db", "rebuildable\n",
                 False, False, ".memory-index/ would be pushed", id="memory-index"),
])
def test_push_repo_stops_at_each_wall_before_committing_or_pushing(
        tmp_path, monkeypatch, capsys, wall, rel, body, is_engine, with_overlay,
        expected):
    repo = _wired_repo(tmp_path)
    _write(repo, rel, body)
    _git(repo, "add", "-A")
    head_before = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    pushed = []
    monkeypatch.setattr(push_all, "supervised_push",
                        lambda *a, **k: pushed.append(a) or {"state": "ok",
                                                            "elapsed_s": 0})
    data_root = _overlay(tmp_path, curated='companies: ["Krellide Systems"]\n') \
        if with_overlay else None

    with pytest.raises(SystemExit) as exc:
        push_all.push_repo("R", repo, "m", True, False, {},
                           is_engine=is_engine, data_root=data_root)

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert expected in out, f"{wall} did not produce its own refusal:\n{out}"
    assert not pushed, f"{wall} refused and the push ran anyway"
    head_after = push_all.run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    assert head_after == head_before, (
        f"{wall} refused AFTER the commit; the offending content is now in local "
        "history and the repair is a history scrub rather than an edit")


def test_the_join_fixture_pushes_when_nothing_is_wrong(tmp_path, monkeypatch):
    """Anti-vacuity for the five cases above.

    Without this, a `push_repo` that refused EVERYTHING -- a typo in a shared
    predicate, a wall that fires on a clean tree -- would satisfy all five and
    read as five walls working. The same fixture with nothing planted must reach
    the push primitive.
    """
    repo = _wired_repo(tmp_path)
    _write(repo, "scripts/extra.py", "print(2)\n")
    _git(repo, "add", "-A")

    pushed = []
    monkeypatch.setattr(push_all, "supervised_push",
                        lambda *a, **k: pushed.append(a) or {"state": "ok",
                                                             "elapsed_s": 0})
    data = _overlay(tmp_path, curated='companies: ["Krellide Systems"]\n')
    assert push_all.push_repo("R", repo, "m", True, False, {},
                              is_engine=True, data_root=data) is None
    assert pushed, "a clean tree never reached the push primitive"
