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

# ONE path can feed SEVERAL pages, so a prefix maps to a TUPLE.
#
# Until 2026-08-23 this was a prefix -> single component dict, and it was
# missing entries the rest of the daemon already declared:
#
#   - `outputs/documents/` and `outputs/content/tribe/` are both in
#     `sources/pulse.IN_FLIGHT_DIRS` and `sources/studio.IN_FLIGHT_DIRS`, and
#     neither had a mapping. Writing a document never bumped the in-flight
#     count.
#   - `threads` is in `state.COMPONENTS` under a comment saying it "gained
#     Watchdog/refresh coverage". Nothing mapped to it and it is not in
#     REFRESHER_COMPONENTS, so the Threads page went stale until a manual
#     refresh. The comment was the only coverage it had.
#   - `studio` had no mapping at all, though the Studio page reads the same
#     in-flight trees plus `datastore/content/linkedin-archive/`.
#
# The in-flight prefixes are DERIVED from `sources.pulse.IN_FLIGHT_DIRS` rather
# than retyped. That list is already duplicated once (studio.py carries a copy
# under a "Must stay in sync" comment); a third hand-maintained copy here is how
# the four entries went missing in the first place. Found by the 2026-08-23
# audit.
from scripts.bridge_daemon.sources.pulse import IN_FLIGHT_DIRS as _IN_FLIGHT_DIRS

# Prefixes whose page assignment is more specific than "it is an output".
_EXTRA_COMPONENTS = {
    "outputs/operations/email-intelligence": ("inbox",),  # leak-guard: ok (relative prefix/match key, not path construction)
    "outputs/operations/fundraising": ("investors",),  # leak-guard: ok (relative prefix/match key, not path construction)
}


def _build_path_map() -> dict[str, tuple[str, ...]]:
    mapping: dict[str, tuple[str, ...]] = {}
    for directory in _IN_FLIGHT_DIRS:
        prefix = directory.rstrip("/") + "/"
        extra = _EXTRA_COMPONENTS.get(directory.rstrip("/"), ())
        mapping[prefix] = ("inflight", "studio", *extra)
    mapping.update({
        "outputs/_sync/calendar/": ("day",),  # leak-guard: ok (relative prefix/match key, not path construction)
        "context/pipeline.md": ("pipeline",),
        "crm/contacts/": ("tribe",),  # leak-guard: ok (relative prefix/match key, not path construction)
        ".claude/skills/": ("capabilities",),
        "knowledge/": ("library",),
        "outputs/operations/viraid/": ("tasks",),  # leak-guard: ok (relative prefix/match key, not path construction)
        "outputs/communications/email/": ("approvals",),  # leak-guard: ok (relative prefix/match key, not path construction)
        "threads/": ("threads",),  # leak-guard: ok (relative prefix/match key, not path construction)
        "datastore/content/linkedin-archive/": ("studio",),  # leak-guard: ok (relative prefix/match key, not path construction)
    })
    return mapping


PATH_TO_COMPONENTS = _build_path_map()

# Components the daemon actively keeps fresh: either Watchdog has a file-path
# mapping above, or a background refresher recomputes them on a schedule. The
# /pulse, /inbox, /inflight entries here track refresher-backed components
# (refresher set lives in bridge-daemon.py:start_daemon -> jobs dict).
# UI uses this to render the "live"/"on-demand" status next to data_time.
REFRESHER_COMPONENTS = {"pulse", "inbox", "inflight"}
WATCHED_COMPONENTS = (
    {c for components in PATH_TO_COMPONENTS.values() for c in components}
    | REFRESHER_COMPONENTS
)


def classify_path(rel_path: str) -> tuple[str, ...]:
    """Every component a change to this path invalidates. Empty when none.

    LONGEST prefix wins, so `outputs/content/linkedin/` is not shadowed by a
    shorter `outputs/` entry if one is ever added.
    """
    p = str(PurePosixPath(rel_path.replace("\\", "/")))
    best: tuple[str, ...] = ()
    best_len = -1
    for prefix, components in PATH_TO_COMPONENTS.items():
        if p.startswith(prefix) and len(prefix) > best_len:
            best, best_len = components, len(prefix)
    return best

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
        for component in classify_path(str(rel)):
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
