"""Send-email finalizer.

Phase 1: validates the draft sidecar exists on disk and returns metadata.
The real send via scripts/send-email.py is wired in Phase 2 once the
/email-respond skill is producing draft sidecars in the documented format.
"""
import re
from pathlib import Path

from scripts.utils.paths import get_data_root

# Allowlist matches what /email-respond is documented to produce: alphanumeric
# id with optional hyphens/underscores, bounded length. Rejects path traversal
# (no '.', '/', '\') and any other shape that could escape the drafts dir.
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def send_drafted(workspace_root: Path, artifact_id: str, data_root: "Path | None" = None) -> dict:
    """PHASE 1 STUB: does NOT actually send; only verifies the sidecar exists.

    Look up a drafted email by artifact_id under
    outputs/operations/email-intelligence/drafts/{artifact_id}.json.

    The drafted-email file format is established by /email-respond, which
    is expected to write a sidecar with to/cc/subject/body fields. Phase 1
    only confirms the sidecar exists; Phase 2 reads it and subprocess.runs
    scripts/send-email.py with the parsed fields.

    HEADING OS engine/data split: the draft sidecar is DATA, so it resolves
    under ``data_root`` (falls back to ``workspace_root`` when not supplied).
    """
    if data_root is None:
        data_root = get_data_root()
    if not _ARTIFACT_ID_RE.match(artifact_id):
        raise ValueError(f"invalid artifact_id: {artifact_id!r}")
    draft = data_root / "outputs" / "operations" / "email-intelligence" / "drafts" / f"{artifact_id}.json"
    if not draft.exists():
        return {"sent": False, "error": f"draft {artifact_id} not found"}
    return {"sent": True, "draft": str(draft)}
