"""In-memory state. Per-component monotonic counters bumped by Watchdog
events and refresh jobs. ETags derive from counter values."""
import threading
from datetime import datetime, timezone
from typing import TypedDict

COMPONENTS = (
    "inbox", "inflight", "pulse", "day", "studio", "tribe",
    "capabilities", "library", "tasks", "pipeline", "investors",
    "approvals", "calendar", "crm", "prime", "status",
    # Phase 1.97: dedicated pages that gained Watchdog/refresh coverage
    # later. Listing them here lets the sync-pill refresh succeed.
    "conversations", "threads",
    # Phase 1.101: signals page (derived from pipeline data but routed
    # separately so the sync-pill works without a 422).
    "signals",
    # Phase 1.127a: critical items log (CEO-flagged action queue).
    "critical",
    # Phase 1.35: full CRM contacts page (CEO's + every exec's).
    "contacts",
    # R12/R1 spine (2026-06-03): Action Queue - proactive drafted actions
    # (Cold-Sweep + future autonomy) for one-click CEO go/no-go.
    "action_queue",
)

Snapshot = TypedDict("Snapshot", {
    "global": int,
    "components": dict[str, int],
    "data_times": dict[str, str | None],
})


class State:
    def __init__(self):
        self._lock = threading.Lock()
        self._versions = dict.fromkeys(COMPONENTS, 0)
        self._data_times = dict.fromkeys(COMPONENTS)

    def bump(self, component: str, *, fresh: bool = True) -> int:
        """Advance the component's version, and its data_time when `fresh`.

        The two are deliberately separable. The version drives the ETag, so a
        refresher that FAILED still bumps it -- the browser must re-read to see
        the failure. `data_time` answers a different question, "how old is this
        data", and a refresher that fetched nothing has no right to answer it:
        advancing it made a stale inbox render as refreshed seconds ago.
        Defaults to True so the call sites that genuinely did fetch are
        unchanged.
        """
        with self._lock:
            if component not in self._versions:
                self._versions[component] = 0
            self._versions[component] += 1
            if fresh:
                self._data_times[component] = datetime.now(timezone.utc).isoformat()
            return self._versions[component]

    def version(self, component: str) -> int:
        with self._lock:
            return self._versions.get(component, 0)

    def global_version(self) -> int:
        with self._lock:
            return sum(self._versions.values())

    def etag(self, component: str) -> str:
        return f'"v{self.version(component)}"'

    def data_time(self, component: str) -> str | None:
        with self._lock:
            return self._data_times.get(component)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return {
                "global": sum(self._versions.values()),
                "components": dict(self._versions),
                "data_times": dict(self._data_times),
            }
