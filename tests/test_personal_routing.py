"""CAP-2: personal/ fails closed to private so exec PII can never default to engine
and slip past the engine-tree-clean guard. (2026-06-26 exec-deferral lift.)

Before the fix, config/routing-map.yaml had no rule for personal/, so legacy-shaped
exec data under personal/ resolved to the engine default and the engine guard
(which flags only private/corporate destinations) never caught it. Only GitHub's
server-side write-deny stopped a real leak on Dima's machine; these tests pin the
local guard so the protection no longer depends on remote permissions.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_routing_destination
from scripts.utils.engine_guard import find_data_artifacts


def test_personal_dir_routes_private():
    for p in [
        "personal/context/personal-info.md",
        "personal/reference/voice.md",
        "personal/reference/calendar-policy.md",
        "personal/crm/contacts/alice.md",
        "personal/outputs/report.md",
    ]:
        assert get_routing_destination(p) == "private", p


def test_engine_guard_flags_personal_file():
    flagged = find_data_artifacts([
        "personal/context/personal-info.md",
        "scripts/foo.py",
        "config/routing-map.yaml",
    ])
    assert "personal/context/personal-info.md" in flagged
    # engine code is never flagged
    assert "scripts/foo.py" not in flagged
    assert "config/routing-map.yaml" not in flagged


def test_personal_wins_over_engine_default():
    # an unmatched path still routes engine (default unchanged by this rule)
    assert get_routing_destination("some/random/path.py") == "engine"
    # but any personal/ path fails closed to private
    assert get_routing_destination("personal/anything.md") == "private"
