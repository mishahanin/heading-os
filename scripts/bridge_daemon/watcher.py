"""Watchdog observer with per-component debounce.

Maps filesystem events to component names, then debounces bumps so a
burst of writes coalesces into one version increment.
"""
import threading
from pathlib import Path, PurePosixPath
from typing import Callable
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from scripts.utils.paths import get_data_root

PATH_TO_COMPONENT = {
    "outputs/_sync/calendar/": "day",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/operations/email-intelligence/": "inbox",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/content/linkedin/": "inflight",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/intel/": "inflight",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/negotiations/": "inflight",  # leak-guard: ok (relative prefix/match key, not path construction)
    "context/pipeline.md": "pipeline",
    "crm/contacts/": "tribe",  # leak-guard: ok (relative prefix/match key, not path construction)
    ".claude/skills/": "capabilities",
    "knowledge/": "library",
    "outputs/operations/viraid/": "tasks",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/operations/fundraising/": "investors",  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/communications/email/": "approvals",  # leak-guard: ok (relative prefix/match key, not path construction)
}

# Components the daemon actively keeps fresh: either Watchdog has a file-path
# mapping above, or a background refresher recomputes them on a schedule. The
# /pulse, /inbox, /inflight entries here track refresher-backed components
# (refresher set lives in bridge-daemon.py:start_daemon -> jobs dict).
# UI uses this to render the "live"/"on-demand" status next to data_time.
REFRESHER_COMPONENTS = {"pulse", "inbox", "inflight"}
WATCHED_COMPONENTS = set(PATH_TO_COMPONENT.values()) | REFRESHER_COMPONENTS

def classify_path(rel_path: str) -> str | None:
    p = str(PurePosixPath(rel_path.replace("\\", "/")))
    for prefix, component in PATH_TO_COMPONENT.items():
        if p.startswith(prefix):
            return component
    return None

class DebouncedBumper:
    """Fires `bump_fn(component)` after `interval` seconds of quiet.
    Subsequent schedule() calls reset the timer."""
    def __init__(self, bump_fn: Callable[[str], None], interval: float = 0.5):
        self.bump_fn = bump_fn
        self.interval = interval
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def schedule(self, component: str) -> None:
        with self._lock:
            if t := self._timers.get(component):
                t.cancel()
            t = threading.Timer(self.interval, self._fire, args=[component])
            t.daemon = True
            self._timers[component] = t
            t.start()

    def _fire(self, component: str) -> None:
        with self._lock:
            self._timers.pop(component, None)
        self.bump_fn(component)

class _Handler(FileSystemEventHandler):
    def __init__(self, workspace_root: Path, bumper: DebouncedBumper):
        self.root = workspace_root
        self.bumper = bumper

    def on_any_event(self, event):
        if event.is_directory:
            return
        try:
            rel = Path(event.src_path).relative_to(self.root)
        except ValueError:
            return
        component = classify_path(str(rel))
        if component:
            self.bumper.schedule(component)

def start_observer(workspace_root: Path, state, interval: float = 0.5,
                   data_root: "Path | None" = None) -> Observer:
    """Observe the engine root and, post-cutover, the data overlay too.

    Most watched paths (outputs/, crm/, threads/, knowledge/, context/pipeline.md)
    live under ``data_root``; ``.claude/skills`` (capabilities) lives under the
    engine ``workspace_root``. When the two roots are identical (transitional
    ceo-main) a single recursive handler covers everything - scheduling a second
    one would only double-fire events the debouncer already coalesces, so we
    skip it. When they differ (a data-less engine clone + its data sibling) each
    handler is rooted at its own tree; ``classify_path`` keys are relative
    prefixes, so each handler simply never matches paths absent from its root.
    """
    if data_root is None:
        data_root = get_data_root()
    bumper = DebouncedBumper(lambda c: state.bump(c), interval=interval)
    observer = Observer()
    observer.schedule(_Handler(workspace_root, bumper), str(workspace_root), recursive=True)
    if Path(data_root).resolve() != Path(workspace_root).resolve():
        observer.schedule(_Handler(data_root, bumper), str(data_root), recursive=True)
    observer.start()
    return observer
