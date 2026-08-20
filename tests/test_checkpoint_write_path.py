"""The checkpoint WRITE path: what reaches disk, and how.

Three properties the two writing hooks are supposed to hold, none of which had a
test before 2026-08-20. The gap was measured rather than assumed: replacing the
body of `write_text_atomic` in checkpoint-save.py with a plain
`path.write_text()` - every one of the seven writes made non-atomic - left the
113 tests that cover these hooks all passing.

  1. checkpoint-save.py writes atomically. `.claude/rules` (and the global
     policy it inherits) require tmp-file-then-`os.replace()` for persistent
     state, and the archive is persistent state nobody can regenerate: the hook
     runs after the session's context has been discarded. A half-written handoff
     is a handoff lost.
  2. checkpoint-precompact.py writes NOTHING. Its own docstring says so, and the
     reason is that PostCompact owns the write; two writers on one compaction is
     how the archive ends up with a half-formed record nobody asked for. A
     docstring is not a gate, so this measures it.
  3. Both hooks resolve their paths from the tree they were LOADED from.

Every test here redirects both roots (`HEADING_OS_DATA`, `CLAUDE_PROJECT_DIR`)
into `tmp_path`. Nothing touches the operator's live archive or the state file of
a running session.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
SAVE_HOOK = ENGINE / ".claude" / "hooks" / "checkpoint-save.py"
PRECOMPACT_HOOK = ENGINE / ".claude" / "hooks" / "checkpoint-precompact.py"

SESSION = "wpath111-2222-3333-4444-555555555555"


def _load(hook: Path, name: str):
    """Import a hook by path, without registering it in sys.modules.

    Import time is when checkpoint-save.py resolves HANDOFF_DIR, so the caller
    sets the environment first and imports second.
    """
    spec = importlib.util.spec_from_file_location(name, str(hook))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _roots(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    data = tmp_path / "data"
    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True)
    data.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return data, project


def _written_files(data: Path, project: Path) -> set[Path]:
    """Every file the hook could have produced, in both roots it writes to.

    `.lock` sidecars are excluded, and the exclusion is narrow on purpose. They
    are created by `CP.locked_state` with a plain `open(..., "a+")`, which is not
    atomic and does not need to be: a lock file carries NO data. Nothing reads
    its contents, it is never replaced, and a torn or empty one costs nothing
    because the guarantee lives in `flock` on the descriptor rather than in the
    bytes. Excluding the whole state directory instead would have hidden the
    state JSON, which does need the guarantee.
    """
    found = {p for p in data.rglob("*") if p.is_file() and p.suffix != ".lock"}
    found |= {p for p in (project / ".claude" / "state").rglob("*")
              if p.is_file() and p.suffix != ".lock"}
    return found


# ---------------------------------------------------------------------------
# 1. Atomicity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("branch", ["saved", "quarantined"])
def test_every_write_lands_through_a_tmp_file_and_os_replace(
    tmp_path, monkeypatch, branch
):
    """No file appears except as the destination of a replace from a sibling tmp.

    `os.replace` is spied rather than mocked out, so the run is the real one and
    the assertion is on what actually reached the filesystem. Both branches are
    exercised because they write different files: the success branch writes the
    dated archive, the quarantine branch writes the quarantined body instead, and
    both write four pointers and the state JSON.
    """
    data, project = _roots(tmp_path, monkeypatch)
    mod = _load(SAVE_HOOK, f"cksave_atomic_{branch}")

    if branch == "quarantined":
        def _raise(_text):
            raise RuntimeError("test: redactor down")

        monkeypatch.setattr(mod, "redact", _raise)

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst, **kwargs):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "session_id": SESSION,
        "trigger": "manual",
        "compact_summary": "atomicity probe body",
        "transcript_path": "",
        "cwd": str(project),
    })))

    assert mod.main() == 0

    files = _written_files(data, project)
    assert files, "the hook wrote nothing at all"

    destinations = {dst for _src, dst in replaced}
    for path in sorted(files):
        assert str(path) in destinations, (
            f"{path} was created without os.replace - the write is not atomic"
        )

    for src, dst in replaced:
        src_p, dst_p = Path(src), Path(dst)
        assert src_p.parent == dst_p.parent, (
            f"tmp file {src} is not a sibling of {dst}; os.replace is only "
            "atomic within one filesystem"
        )
        assert src_p.name.endswith(".tmp"), f"replaced from a non-tmp source: {src}"

    strays = list(tmp_path.rglob("*.tmp"))
    assert not strays, f"tmp files survived the run: {strays}"


# ---------------------------------------------------------------------------
# 2. The PreCompact hook writes nothing
# ---------------------------------------------------------------------------

def test_precompact_creates_no_file_anywhere(tmp_path):
    """PostCompact owns the write. This hook only prints.

    Run as a real process, with both roots redirected into `tmp_path`, and the
    whole tree diffed by (path, size, mtime) rather than by path alone - a hook
    that overwrote an existing pointer in place would leave the path set intact.
    """
    data = tmp_path / "data"
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (data / "outputs" / "operations" / "handoff-archive" / ".latest").mkdir(parents=True)
    seeded = data / "outputs" / "operations" / "handoff-archive" / ".latest" / "summary.md"
    seeded.write_text("pre-existing pointer\n", encoding="utf-8")

    def snapshot() -> set[tuple[str, int, int]]:
        return {
            (str(p), p.stat().st_size, p.stat().st_mtime_ns)
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(data)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(PRECOMPACT_HOOK)],
        input=json.dumps({
            "session_id": SESSION,
            "transcript_path": "",
            "cwd": str(project),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }),
        capture_output=True, text=True, env=env, timeout=60,
    )

    assert proc.returncode == 0, f"exited {proc.returncode}\n{proc.stderr}"
    assert proc.stdout.strip(), "the keep-set was not printed"
    assert snapshot() == before, "checkpoint-precompact.py touched the filesystem"


# ---------------------------------------------------------------------------
# 3. Paths come from the tree the hook was loaded from
# ---------------------------------------------------------------------------

def test_handoff_pointer_uses_the_tree_the_hook_booted_from(tmp_path, monkeypatch):
    """`CP.engine_root()` reads the IMPORTED module's location, not the hook's.

    The two differ under an editable install, where a finder ahead of `sys.path`
    hands a bundled hook the engine's copy of checkpoint_paths - the measured
    2026-08-16 incident that made `handoff_dir()` take the root as an argument.
    checkpoint-save.py and checkpoint-inject.py pass their own walked root;
    checkpoint-precompact.py asked `engine_root()` until 2026-08-20.

    Simulated by pointing `engine_root()` at a tree that is not an engine tree,
    which is exactly what the divergence produces. A hook that consults it
    resolves the archive to `<project>/.claude/handoff/` and finds nothing.
    """
    data = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    data.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(data))

    mod = _load(PRECOMPACT_HOOK, "ckprecompact_bootroot")
    slug = mod.CP.safe_slug(SESSION)
    pointer = (
        data / "outputs" / "operations" / "handoff-archive" / ".latest"
        / slug / "summary.md"
    )
    pointer.parent.mkdir(parents=True)
    pointer.write_text("planted pointer\n", encoding="utf-8")

    decoy = tmp_path / "not-an-engine-tree"
    decoy.mkdir()
    monkeypatch.setattr(mod.CP, "engine_root", lambda: decoy)

    got = mod._handoff_pointer({"session_id": SESSION}, project)
    assert got == f"outputs/operations/handoff-archive/.latest/{slug}/summary.md", (
        f"the pointer fact resolved to {got!r}; the hook did not use the tree it "
        "was loaded from"
    )


# ---------------------------------------------------------------------------
# 4. The archive filename
# ---------------------------------------------------------------------------

def test_an_empty_trigger_names_the_file_unknown(tmp_path, monkeypatch):
    """`safe_slug("")` returns "session", so the old `or "unknown"` never fired.

    A file named `..._handoff_compact-session_...` puts the word "session" in the
    trigger column, one field away from the session column, and says nothing
    about the trigger. The fallback belongs on the value, where it is reachable.
    """
    data, project = _roots(tmp_path, monkeypatch)
    assert mod_safe_slug_is_never_empty(), "safe_slug no longer returns a default"

    mod = _load(SAVE_HOOK, "cksave_trigger_fallback")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "session_id": SESSION,
        "trigger": "",
        "compact_summary": "trigger probe",
        "transcript_path": "",
        "cwd": str(project),
    })))
    assert mod.main() == 0

    archive = data / "outputs" / "operations" / "handoff-archive"
    names = sorted(p.name for p in archive.glob("*.md"))
    assert names, "no archive file was written"
    assert all("_handoff_compact-unknown_" in n for n in names), (
        f"an empty trigger produced {names}"
    )


def mod_safe_slug_is_never_empty() -> bool:
    """The premise of the test above, checked rather than asserted in prose."""
    from scripts.utils.checkpoint_paths import safe_slug

    return safe_slug("") == "session" and safe_slug("---") == "session"
