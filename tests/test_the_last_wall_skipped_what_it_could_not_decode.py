"""The UNBYPASSABLE push wall passed silently over any file it could not decode.

`scripts/push-all.py::engine_content_scan` is the last layer between an engine
file and a public remote, and its own docstring calls it unbypassable. Its read
was:

    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue

So an engine-routed file whose bytes are not valid UTF-8 -- a note saved as
UTF-16 by an editor, a stray byte in a patch, a transient read error -- was
dropped with no record, and the push printed clean over a file nobody had read.
Twenty lines above it, the same function REFUSES when the denylist degrades, for
the stated reason that "a silent skip here is exactly the 'looks like coverage'
failure the flag exists to prevent". The two halves of one function disagreed.

The sibling CLI `scripts/content-guard.py` had already closed this on
2026-08-14, down to the wording: it collects what it could not read and exits
non-zero. That fix did not reach the push wall because each gate carried its own
copy of the same six lines, so for eleven days the bypassable layer was strictly
stronger than the unbypassable one.

The fix is one shared selector, `engine_guard.engine_text_files`, and a refusal
in both gates. These tests hold three things: the wall refuses, real binaries are
still skipped deliberately, and the two gates cannot drift apart again.

Run: python3 -m pytest tests/test_the_last_wall_skipped_what_it_could_not_decode.py
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

from scripts.utils.engine_guard import BINARY_SUFFIXES, engine_text_files  # noqa: E402

# push-all.py calls ensure_venv() at MODULE scope; tests/conftest.py sets the
# guard that stops it re-execing the pytest process. Same idiom as
# tests/test_push_all_gate.py.
_spec = importlib.util.spec_from_file_location("push_all_decode", ROOT / "scripts" / "push-all.py")
push_all = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_all)

_gspec = importlib.util.spec_from_file_location(
    "content_guard_lockstep", ROOT / "scripts" / "content-guard.py")
content_guard = importlib.util.module_from_spec(_gspec)
_gspec.loader.exec_module(content_guard)

# UTF-16 with its byte-order mark, which is what an editor writes when nobody
# asked it to. Valid text in its own encoding, invalid UTF-8 from byte zero.
#
# The first draft of this constant used `.encode("utf-16-le")` and every test
# here passed while the wall was still broken: UTF-16LE of pure ASCII is NUL
# bytes between ASCII bytes, and NUL is perfectly valid UTF-8, so nothing ever
# raised. The BOM is what makes the file genuinely undecodable.
UTF16_BYTES = "a real name lives here\n".encode("utf-16")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "engine"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _overlay(tmp_path: Path) -> Path:
    """A minimal DATA overlay, so the gate runs instead of no-opping.

    Every identity here is invented. The engine carries no real entity.
    """
    data = tmp_path / "data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    (data / "crm" / "contacts" / "zenon-makarios.md").write_text(
        "---\nname: Zenon Makarios\n---\n", encoding="utf-8")
    return data


def _write_bytes(repo: Path, rel: str, blob: bytes) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    return p


# ============================================================
# The wall itself
# ============================================================

def test_the_push_wall_refuses_a_file_it_could_not_decode(tmp_path, capsys):
    """The defect, stated as the outcome an operator would have seen.

    Before the fix this returned None and the push went ahead.
    """
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", UTF16_BYTES)
    _git(repo, "add", "-A")

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "could not read" in out
    assert "docs/note.md" in out


def test_the_refusal_names_the_decoding_failure_not_just_the_file(tmp_path, capsys):
    """The operator has to know WHY, or the only available fix is guesswork."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", UTF16_BYTES)
    _git(repo, "add", "-A")

    with pytest.raises(SystemExit):
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert "codec can't decode" in capsys.readouterr().out


def test_an_undecodable_file_is_recorded_in_the_denial_log(tmp_path, monkeypatch):
    """A refusal nobody can count afterwards is half a control."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", UTF16_BYTES)
    _git(repo, "add", "-A")

    recorded: list[dict] = []
    monkeypatch.setattr(push_all, "log_denial", lambda **kw: recorded.append(kw))

    with pytest.raises(SystemExit):
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert [r["path"] for r in recorded] == ["docs/note.md"]
    assert recorded[0]["mechanism"] == "push:engine-content-scan"


def test_a_decodable_engine_file_still_passes(tmp_path):
    """The negative case. A gate that refuses everything is not a gate."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", b"ordinary prose\n")
    _git(repo, "add", "-A")

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None


def test_a_real_binary_is_skipped_deliberately_not_refused(tmp_path):
    """The reason the fix is a suffix filter and not a blanket refusal.

    Brand assets and fonts are engine-routed and legitimately not UTF-8. Refusing
    over them would block every push, which is how a gate gets switched off.
    """
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/assets/logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    _git(repo, "add", "-A")

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None


def test_a_content_leak_still_outranks_an_unreadable_file(tmp_path, capsys):
    """Both refuse, so the order only decides which message the operator reads
    first. The leak is the more urgent one and must not be hidden behind the
    read failure."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/leak.md", b"Zenon Makarios was here\n")
    _write_bytes(repo, "docs/note.md", UTF16_BYTES)
    _git(repo, "add", "-A")

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "real-entity CONTENT" in out


# ============================================================
# The same wall, reading the unpushed COMMITS instead of the disk
# ============================================================
#
# `engine_content_scan` grew a history pass on 2026-08-29 and it carries its own
# `except UnicodeDecodeError` beside its own `unscanned.append`. Nothing bound
# it: MEASURED 2026-09-01 by mutation, reducing that clause to a bare `continue`
# -- the exact defect this file exists to close, in the other half of the same
# function -- survived every test here and every test in
# `tests/test_a_wall_that_read_the_present_and_shipped_the_past.py`, which is the
# only other file that reaches `unpushed_blobs`.


def _repo_with_remote(tmp_path: Path) -> Path:
    """A clone with a real bare remote and one PUSHED commit on `origin/main`.

    A real remote, because the history pass asks git what is reachable from HEAD
    and not from `origin/main`. `_init_repo` above has no remote, so everything
    it holds is either staged or already the base, and the history pass reads
    nothing there.
    """
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "seed")
    _git(repo, "push", "-q", "origin", "HEAD:main")
    _git(repo, "fetch", "-q", "origin")
    return repo


def _commit_then_delete(repo: Path, rel: str, blob: bytes) -> None:
    """Add `rel` in one unpushed commit and remove it in the next.

    The deletion is what separates the two halves of the wall: `is_file()` is
    false afterwards, so `engine_text_files` drops the path and the DISK pass
    cannot see it. Only the history pass can, and the push ships the version the
    first commit carries whatever the working copy says now.
    """
    _write_bytes(repo, rel, blob)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "add")
    (repo / rel).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "drop")


def test_an_undecodable_version_in_an_unpushed_commit_is_refused(tmp_path, capsys):
    """The history half of the same hole, stated as the operator's outcome."""
    repo = _repo_with_remote(tmp_path)
    _commit_then_delete(repo, "docs/note.md", UTF16_BYTES)
    assert not (repo / "docs" / "note.md").exists(), \
        "the disk pass must not be able to see this file, or the case proves nothing"

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "could not read" in out
    # `path@blobsha`, so the operator can tell a history finding from a disk one:
    # editing the file now does not remove it.
    assert "docs/note.md@" in out


def test_the_history_refusal_is_recorded_in_the_denial_log(tmp_path, monkeypatch):
    """Same obligation as the disk half. A refusal nobody can count afterwards
    is half a control."""
    repo = _repo_with_remote(tmp_path)
    _commit_then_delete(repo, "docs/note.md", UTF16_BYTES)

    recorded: list[dict] = []
    monkeypatch.setattr(push_all, "log_denial", lambda **kw: recorded.append(kw))

    with pytest.raises(SystemExit):
        push_all.engine_content_scan(repo, _overlay(tmp_path))

    assert [r["mechanism"] for r in recorded] == ["push:engine-content-scan"]
    # The blob sha survives into the log, so a history refusal stays
    # distinguishable there from a disk one after the terminal has scrolled.
    assert recorded[0]["path"].startswith("docs/note.md@")
    assert recorded[0]["reason"] == "engine-routed file could not be read"


def test_a_decodable_version_in_an_unpushed_commit_still_passes(tmp_path):
    """The negative case. A history pass that refused over every deleted file
    would block every ordinary push, which is how a wall gets switched off."""
    repo = _repo_with_remote(tmp_path)
    _commit_then_delete(repo, "docs/note.md", b"ordinary prose\n")

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None


def test_a_binary_version_in_an_unpushed_commit_is_skipped_deliberately(tmp_path):
    """Brand assets are engine-routed and legitimately not UTF-8 in history too."""
    repo = _repo_with_remote(tmp_path)
    _commit_then_delete(repo, "docs/assets/logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe\x00")

    assert push_all.engine_content_scan(repo, _overlay(tmp_path)) is None


# ============================================================
# The shared selector, which is what stops the drift recurring
# ============================================================

def test_the_selector_keeps_engine_text_and_drops_everything_else(tmp_path):
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", b"x")
    _write_bytes(repo, "docs/assets/logo.png", b"x")
    _write_bytes(repo, "crm/contacts/someone.md", b"x")   # routes private
    (repo / "docs" / "adir").mkdir()

    kept = engine_text_files(repo, [
        "docs/note.md", "docs/assets/logo.png", "crm/contacts/someone.md",
        "docs/adir", "docs/absent.md", "", "/docs/note.md",
    ])

    # `/docs/note.md` normalises onto the same entry, so it appears twice: the
    # selector filters, it does not deduplicate, and a caller relying on
    # uniqueness would be relying on something it never promised.
    assert kept == ["docs/note.md", "docs/note.md"]


def test_the_selector_preserves_the_order_it_was_given(tmp_path):
    """`engine_content_scan` sorts its input and expects that order back, so the
    refusal list an operator reads is stable between runs."""
    repo = _init_repo(tmp_path)
    for rel in ("docs/a.md", "docs/b.md", "docs/c.md"):
        _write_bytes(repo, rel, b"x")

    given = ["docs/c.md", "docs/a.md", "docs/b.md"]
    assert engine_text_files(repo, given) == given
    assert engine_text_files(repo, sorted(given)) == sorted(given)


def test_the_binary_suffix_list_is_matched_case_insensitively(tmp_path):
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/LOGO.PNG", b"x")

    assert engine_text_files(repo, ["docs/LOGO.PNG"]) == []


def test_the_bin_suffix_stays_on_the_list(tmp_path):
    """`tests/integration/fixtures/unsupported.bin` is a committed engine
    fixture. While `.bin` was missing, every sweep tried to decode it, hit the
    unreadable branch, and called the result clean."""
    assert ".bin" in BINARY_SUFFIXES


def test_neither_gate_carries_its_own_copy_of_the_filter(tmp_path):
    """The anti-drift assertion, and the actual lesson of this shard.

    The hole survived because the fix landed in one of two copies. A second
    definition of either name outside `engine_guard.py` puts the two gates back
    on separate code paths, so the AST is asked directly rather than trusting a
    convention.

    `engine_text_rels` joined the watchlist on 2026-09-01. It was split out of
    `engine_text_files` on 2026-08-29 for the push wall's history pass and the
    detector had never been told its name, which is the same blindness
    `tests/test_three_gates_that_read_one_file_three_ways.py` records for
    `parse_yaml_frontmatter`: a name-keyed sweep sees only the spellings it was
    given, and the spelling carrying the defect is the one nobody added.
    """
    owner = ROOT / "scripts" / "utils" / "engine_guard.py"
    watched = {"engine_text_files", "engine_text_rels", "BINARY_SUFFIXES",
               "_engine_text_files", "_engine_text_rels", "_BINARY_SUFFIXES"}
    offenders: list[str] = []

    for path in (ROOT / "scripts" / "content-guard.py",
                 ROOT / "scripts" / "push-all.py", owner):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name in watched
                    and (path != owner or node.name.startswith("_"))):
                offenders.append(f"{path.name}:{node.lineno} def {node.name}")
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Name) and tgt.id in watched
                            and (path != owner or tgt.id.startswith("_"))):
                        offenders.append(f"{path.name}:{node.lineno} {tgt.id} =")

    assert offenders == [], f"the filter was copied again: {offenders}"


def test_the_owner_really_defines_both_names(tmp_path):
    """Pins the check above against decay. A watchlist that matches nothing
    passes every file, including a repo where the shared module was deleted."""
    tree = ast.parse((ROOT / "scripts" / "utils" / "engine_guard.py").read_text(encoding="utf-8"))
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}

    assert "engine_text_files" in funcs
    assert "engine_text_rels" in funcs
    assert "BINARY_SUFFIXES" in names


# ============================================================
# The two gates, held in lockstep by behaviour rather than by prose
# ============================================================

def _cli(monkeypatch, repo: Path, data: Path, *files: str) -> int:
    """Drive `content-guard.main()` over `repo` and return its exit code.

    In-process rather than as a subprocess because the CLI resolves its own root
    with `get_workspace_root()` and ignores the working directory, so a
    subprocess would scan the real engine tree instead of the fixture. Its
    `--data-root` flag is real; the root is the only thing patched.
    """
    monkeypatch.setattr(content_guard, "get_workspace_root", lambda: repo)
    monkeypatch.setattr(sys, "argv", [
        "content-guard.py", "--files", *files, "--data-root", str(data)])
    return content_guard.main()


def test_the_cli_gate_refuses_the_same_file_the_push_wall_refuses(
        tmp_path, monkeypatch, capsys):
    """Both gates, same file, same verdict.

    This is the assertion that would have failed for the eleven days between the
    two fixes: the bypassable gate said 1, the unbypassable one said "clean".
    """
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", UTF16_BYTES)
    _git(repo, "add", "-A")
    data = _overlay(tmp_path)

    assert _cli(monkeypatch, repo, data, "docs/note.md") == 1
    assert "could not be scanned" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(repo, data)
    assert exc.value.code == 2


def test_the_cli_gate_passes_the_same_binary_the_push_wall_passes(
        tmp_path, monkeypatch):
    """The other half of lockstep. Agreeing to refuse is worth little if the two
    disagree about what may be skipped."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/assets/logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    _git(repo, "add", "-A")
    data = _overlay(tmp_path)

    assert _cli(monkeypatch, repo, data, "docs/assets/logo.png") == 0
    assert push_all.engine_content_scan(repo, data) is None


def test_the_cli_gate_agrees_a_readable_engine_file_is_clean(
        tmp_path, monkeypatch):
    """The negative case for the lockstep pair, so a helper that always returned
    the same number could not carry the two tests above."""
    repo = _init_repo(tmp_path)
    _write_bytes(repo, "docs/note.md", b"ordinary prose\n")
    _git(repo, "add", "-A")
    data = _overlay(tmp_path)

    assert _cli(monkeypatch, repo, data, "docs/note.md") == 0
    assert push_all.engine_content_scan(repo, data) is None
