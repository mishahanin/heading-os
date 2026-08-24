"""Send-email finalizer. It locates a draft. It has never sent anything.

The name and the old return value both said otherwise. `send_drafted` answered
`{"sent": True}` when the sidecar file merely existed, and the browser action it
is wired to is called "send-email" — so a click reported a delivered email that
was never handed to any transport. Found by the 2026-08-23 audit.

**Phase 2 is not "wire the send in here", and that plan is withdrawn.** Two rules
landed after this file was written:

* `.claude/rules/lethal-trifecta.md` — every outbound send is gated behind an
  explicit human approval, and since 2026-06-27 that approval IS the operator
  typing `scripts/action-queue.py approve <id>`, which sends synchronously in
  that same command. A browser POST is not that click.
* `.claude/rules/console-first.md` — a web view is never the only mutator. The
  bridge dashboard's action-queue page is read-only by design.

So this stays a locator, and the send lives where the human is.
"""
import re
from pathlib import Path

from scripts.utils.paths import get_data_root

# Allowlist matches what /email-respond is documented to produce: alphanumeric
# id with optional hyphens/underscores, bounded length. Rejects path traversal
# (no '.', '/', '\') and any other shape that could escape the drafts dir.
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_NOT_A_SEND = (
    "this endpoint locates a draft; it does not send. Approve and send with "
    "`python scripts/action-queue.py approve <id>`"
)


def send_drafted(data_root: "Path | None", artifact_id: str) -> dict:
    """Locate a drafted email by `artifact_id`. NEVER sends.

    Looks under `outputs/operations/email-intelligence/drafts/{artifact_id}.json`.
    The sidecar format is established by `/email-respond`.

    Returns `sent: False` in every case, because nothing here can make it True.
    `found` is what actually varies. The two keys are separate on purpose: a
    caller that reads only `sent` gets the honest answer, and a caller that wants
    to know whether the draft exists has a field that says so.

    HEADING OS engine/data split: the draft sidecar is DATA, so it resolves
    under `data_root`, which falls back to the `get_data_root()` seam when not
    supplied. It never fell back to a caller-passed root: the dead leading
    `workspace_root` this took until 2026-08-24 was read by nothing, and this
    line promising a fallback to it was wrong the whole time.
    """
    if data_root is None:
        data_root = get_data_root()
    if not _ARTIFACT_ID_RE.match(artifact_id):
        raise ValueError(f"invalid artifact_id: {artifact_id!r}")
    draft = data_root / "outputs" / "operations" / "email-intelligence" / "drafts" / f"{artifact_id}.json"
    if not draft.exists():
        return {"sent": False, "found": False, "error": f"draft {artifact_id} not found"}
    return {"sent": False, "found": True, "draft": str(draft), "error": _NOT_A_SEND}
