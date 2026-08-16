#!/usr/bin/env python3
"""Path and config seam shared by the four checkpoint hooks and /checkpoint.

Every path the checkpoint system writes is keyed by SESSION, because this
workspace routinely has several sessions open on one tree and anything shared
between them is last-writer-wins. Measured on 2026-08-16 against the pre-fix
hooks: session A at 46% wrote the state file that session B's Stop hook read, so
the idle session was told to checkpoint and the full one was not.

Two roots, deliberately not the same thing:

  - the PROJECT root comes from the hook payload and is where `.claude/state/`
    lives. It follows the session's own tree, so a plugin installed in someone
    else's repository writes there and not into the plugin cache.
  - the ENGINE root is found by walking up from this file until the tree that
    contains `scripts/utils/`. In the monorepo that is the engine; inside a
    built plugin bundle it is the bundle. It decides which layout applies.

The archive follows the engine's data seam when this IS a HEADING OS tree
(`config/routing-map.yaml` present, so the operator has a data overlay) and
falls back to project-local `.claude/handoff/` when it is not, which is the
case for any repository that installs the plugin bundle.

Stdlib only, and it stays that way: `checkpoint-save.py` runs after the session
context has been discarded, so an import this module cannot satisfy costs a
handoff nobody can regenerate.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Pointer dirs and state files are per session and never revisited once that
# session ends, so without a bound they grow forever. The nexi plugin pruned the
# pointer dirs and left the state files: 30 sessions, 26 dirs, 30 state files.
KEEP_DAYS = 14
KEEP_MAX = 25

# What a pointer may carry. The live shared pointer reached 32261 bytes against
# an 8000-character inject cap, so three quarters of it never reached a session
# and the quarter that did was cut mid-sentence. Bounding it at the WRITE keeps
# the file on disk equal to the text that gets injected.
MAX_POINTER_SUMMARY = 6000


def force_utf8() -> None:
    """Windows defaults to CP1252, which breaks the status bar's box characters."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001 - never break a hook over stream setup
                print(f"checkpoint: stream reconfigure failed: {exc}", file=sys.stderr)


def engine_root() -> Path:
    """The tree that owns `scripts/utils/` — engine monorepo or plugin bundle."""
    return Path(__file__).resolve().parent.parent.parent


def bootstrap_root(start: Path) -> Path | None:
    """Find the tree a HOOK can import this module from.

    Hooks sit at `.claude/hooks/x.py` in the monorepo and at `hooks/x.py` inside
    a built bundle, so a fixed number of `.parent` calls is wrong in one of the
    two layouts. Walk instead, and let the marker answer.
    """
    for candidate in [start, *start.parents]:
        if (candidate / "scripts" / "utils" / "checkpoint_paths.py").is_file():
            return candidate
    return None


def is_engine_tree(root: Path) -> bool:
    """True for the HEADING OS monorepo, false for a built plugin bundle.

    `config/routing-map.yaml` is the engine's routing input and is not bundled,
    so its presence separates "this operator has a data overlay" from "this is
    somebody's repository with our plugin installed" without guessing.
    """
    return (root / "config" / "routing-map.yaml").is_file()


def project_root(payload: dict | None = None) -> Path:
    """Where `.claude/state/` belongs: the tree this session is working in."""
    payload = payload or {}
    candidates: list[str] = []
    workspace = payload.get("workspace") or {}
    if isinstance(workspace, dict):
        for key in ("project_dir", "current_dir"):
            if workspace.get(key):
                candidates.append(workspace[key])
    if payload.get("cwd"):
        candidates.append(payload["cwd"])
    for var in ("CLAUDE_PROJECT_DIR", "WORKSPACE_ROOT"):
        if os.environ.get(var):
            candidates.append(os.environ[var])
    candidates.append(os.getcwd())
    for candidate in candidates:
        try:
            path = Path(candidate).expanduser()
            if path.is_dir():
                return path.resolve()
        except Exception as exc:  # noqa: BLE001 - a bad candidate is skipped, not fatal
            print(f"checkpoint: unusable project candidate {candidate!r}: {exc}",
                  file=sys.stderr)
    return Path(os.getcwd()).resolve()


def handoff_dir(project: Path, root: Path | None = None) -> Path:
    """The archive root: the operator's data overlay, or project-local.

    `root` is the tree the CALLER resolved, and passing it is not ceremony. This
    function used to ask `engine_root()`, which reads THIS module's `__file__` -
    and the module a hook ends up importing is not always the copy beside it. In
    a venv where the engine is installed as a package, an editable-install
    finder runs ahead of `sys.path`, so a hook inside a plugin bundle imported
    the ENGINE's copy of this file and was told it was in an engine tree. It
    then wrote a stranger's handoff into the operator's data root. Measured
    2026-08-16. The hook already knows which tree it is in; it says so here.
    """
    root = root or engine_root()
    if is_engine_tree(root):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from scripts.utils.workspace import get_outputs_dir

        return get_outputs_dir() / "operations" / "handoff-archive"
    return project / ".claude" / "handoff"


def latest_root(handoff: Path) -> Path:
    return handoff / ".latest"


def latest_dir(handoff: Path, slug: str) -> Path:
    """This session's pointer dir — the only one safe to inject."""
    return latest_root(handoff) / slug


def state_path(project: Path, slug: str) -> Path:
    return project / ".claude" / "state" / f"checkpoint-{slug}.json"


def session_id(payload: dict | None = None) -> str:
    """The session's own id, from the hook payload or from the environment.

    `/checkpoint` is model-driven and gets no payload, so it reads
    CLAUDE_CODE_SESSION_ID, which Claude Code exports to child processes
    (verified on 2.1.228). That is what puts the skill and the hooks in the
    same directory instead of two.
    """
    payload = payload or {}
    for candidate in (payload.get("session_id"), os.environ.get("CLAUDE_CODE_SESSION_ID")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "session"


def safe_slug(value: str, max_len: int = 32) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in (value or "")
    )
    return cleaned[:max_len].strip("-") or "session"


def session_slug(payload: dict | None = None) -> str:
    return safe_slug(session_id(payload))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 100) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "auto")


def auto_mode(state: dict | None = None) -> bool:
    """Is the hands-off mode on for THIS session?

    Two inputs, and the session's own flag wins in BOTH directions.
    CLAUDE_HANDOFF_AUTO is a launch-time decision for the whole workspace;
    `session_auto` is the running one, taken mid-work in a single window ("this
    is going to be long, stop asking"). Symmetric on purpose: a window may also
    want the question back while the workspace default is silence.

    Read from `session_auto` and never from `auto`. The statusline rewrites
    `auto` after every turn as its echo of the RESOLVED mode, so an operator
    choice stored under that key would survive roughly one turn.
    """
    if state:
        flag = state.get("session_auto")
        if flag is not None:
            return bool(flag)
    return env_bool("CLAUDE_HANDOFF_AUTO", False)


def config(state: dict | None = None) -> dict:
    """Thresholds and mode.

    `auto` is OFF unless the operator turns it on. The capability ships; the
    activation is a switch they flip, never a default they discover. Pass the
    session's state file to let its own switch answer instead of the workspace
    default.
    """
    soft = env_int("CLAUDE_HANDOFF_SOFT_THRESHOLD", 25)
    hard = env_int("CLAUDE_HANDOFF_HARD_THRESHOLD", 30)
    if soft >= hard:
        soft, hard = 25, 30
    return {
        "soft": soft,
        "hard": hard,
        "step": env_int("CLAUDE_HANDOFF_REMIND_STEP", 5, minimum=1),
        "auto": auto_mode(state),
    }


def compact_point() -> tuple[str, str] | None:
    """Where native auto-compact is configured to fire, or None if nothing says.

    A hook knows only what the environment tells it. Saying "compaction frees
    context near 55%" when nothing set a window is an assertion the method never
    established, so this returns None and the caller says so instead of guessing.

    The percentage form is preferred over the token form on purpose: a token
    count is measured against `min(setting, the model's own window)`, so it
    silently means something else the moment the operator changes model.
    """
    percent = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "").strip()
    if percent.isdigit():
        return ("percent", percent)
    window = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "").strip()
    if window.isdigit():
        return ("tokens", window)
    return None


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt state file must not stop a turn
        print(f"checkpoint: unreadable state at {path.name}: {exc}", file=sys.stderr)
        return {}


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        # The temp file is cleanup, not the failure. `raise` below re-raises the
        # real error, so suppressing an already-absent temp file hides nothing.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def bound_summary(text: str, archive_ref: str, limit: int = MAX_POINTER_SUMMARY) -> str:
    """Cut the pointer's copy at a stated place, naming where the rest lives."""
    if len(text) <= limit:
        return text
    return (
        text[:limit].rstrip()
        + f"\n\n[Cut at {limit} characters. The full summary is in {archive_ref}.]\n"
    )


def _prune(entries: list[tuple[float, Path]], remove) -> None:
    entries.sort(key=lambda item: item[0], reverse=True)
    cutoff = utc_now().timestamp() - KEEP_DAYS * 86400
    doomed = [p for mtime, p in entries if mtime < cutoff]
    doomed.extend(p for _, p in entries[KEEP_MAX:] if p not in doomed)
    for path in doomed:
        try:
            remove(path)
        except OSError as exc:
            print(f"checkpoint: could not prune {path.name}: {exc}", file=sys.stderr)


def prune_pointer_dirs(handoff: Path, keep_slug: str) -> None:
    """Drop pointer dirs of sessions that are long gone.

    Only the per-session DIRS. The shared `.latest/summary.md` and
    `.latest/prompt.md` are files, not dirs, and `scripts/next-signal.py` reads
    them; the archives the pointers name are the actual record and are never
    touched by anything here.
    """
    base = latest_root(handoff)
    if not base.is_dir():
        return
    entries: list[tuple[float, Path]] = []
    for child in base.iterdir():
        if not child.is_dir() or child.name == keep_slug:
            continue
        try:
            mtime = max(
                (f.stat().st_mtime for f in child.iterdir()),
                default=child.stat().st_mtime,
            )
        except OSError:
            continue
        entries.append((mtime, child))

    def remove(path: Path) -> None:
        for f in path.iterdir():
            if f.is_file():
                f.unlink()
        path.rmdir()

    _prune(entries, remove)


def prune_state_dir(state_dir: Path, keep_name: str) -> None:
    """The half the nexi plugin left growing: one JSON per session, forever.

    Takes the directory rather than the project root, because checkpoint-save.py
    exposes `STATE_DIR` as the seam its sandboxed tests redirect.
    """
    base = state_dir
    if not base.is_dir():
        return
    keep = keep_name
    entries: list[tuple[float, Path]] = []
    for child in base.glob("checkpoint-*.json"):
        if child.name == keep or not child.is_file():
            continue
        try:
            entries.append((child.stat().st_mtime, child))
        except OSError:
            continue
    _prune(entries, lambda path: path.unlink())
