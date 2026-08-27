"""Two walls that answered "clean" over a source file they could not read.

Both are the same defect at different scales. A guard reads a file, cannot
parse it, and quietly carries on with less than it needed. Absence and
corruption are not the same event, and a guard that treats them alike reports
the safe outcome for the wrong reason.

1. THE CONTENT-LEAK WALL HARVESTED FROM A ROSTER IT COULD NOT PARSE. Five sites
   in `content_denylist` swallowed `(OSError, json.JSONDecodeError)` and
   returned, leaving `degraded` False. `build_denylist`'s own handler was
   written for exactly this - it prints the cause and sets `degraded`, and its
   comment says a silent swallow "switched off the only content-leak layer while
   blaming an absent overlay" - but only `_harvest_curated` ever reached it. A
   corrupt `admin/executives.json` therefore dropped every executive name from
   the token set and `content-guard` printed the engine tree clean. That is the
   operator law's exact failure mode: private names in a public repository.

2. THE CORPORATE WRITE WALL SWITCHED OFF ON A CORRUPT IDENTITY FILE.
   `check_protect_corporate` returned None - allow - when
   `.workspace-identity.json` was present but unparseable, so one bad byte let
   an executive edit `corporate/`, and the next sync silently overwrote it.
   The refusal added here is scoped to the WRITE, not the environment: only
   `corporate/` is blocked, so a broken file costs one message, never a session.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`silent-degradation`. Both were reproduced against the unfixed source first.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import build_denylist  # noqa: E402


# ============================================================
# 1. The content-leak wall
# ============================================================

def _overlay(tmp_path) -> Path:
    """A DATA overlay whose every harvested source is present and well formed."""
    data = tmp_path / ".heading-os-data"
    (data / "admin").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    (data / "crm" / "contacts").mkdir(parents=True)
    state = data / "datastore" / "operations" / "tribe" / "fireside-state"
    state.mkdir(parents=True)

    (data / "admin" / "executives.json").write_text(json.dumps({
        "executives": [{"slug": "quill-marchetti", "name": "Quill Marchetti"}]
    }), encoding="utf-8")
    (data / "config" / "fireside-schedule.json").write_text(json.dumps({
        "weeks": [{"mon": ["Bramwell Okonjo"], "wed": []}]
    }), encoding="utf-8")
    (state / "tribe-roster.json").write_text(json.dumps({
        "members": {"okonjo_b": {"name": "Bramwell Okonjo",
                                 "telegram_user_id": 481920377}}
    }), encoding="utf-8")
    (data / "crm" / "contacts" / "quill-marchetti.md").write_text(
        "---\nemail: quill@example.invalid\n---\n", encoding="utf-8")
    return data


def test_the_reference_overlay_harvests_and_is_not_degraded(tmp_path):
    """The floor. Without it every assertion below passes over an empty corpus."""
    dl = build_denylist(_overlay(tmp_path))
    assert not dl.degraded
    assert len(dl.tokens) >= 4, dl.tokens
    assert "quill-marchetti" in dl.tokens


@pytest.mark.parametrize("relpath", [
    "admin/executives.json",
    "config/fireside-schedule.json",
    "datastore/operations/tribe/fireside-state/tribe-roster.json",
])
def test_a_corrupt_harvest_source_marks_the_denylist_degraded(tmp_path, relpath, capsys):
    """Present-and-unparseable must reach build_denylist's handler, not vanish.

    `egress_proof` and `content-guard` both read `degraded` as the one signal
    that the token set is incomplete. A harvester that returns quietly lies to
    both of them.
    """
    data = _overlay(tmp_path)
    (data / relpath).write_text("{ not json", encoding="utf-8")
    dl = build_denylist(data)
    assert dl.degraded, f"a corrupt {relpath} left the denylist claiming completeness"
    assert "harvest failed" in capsys.readouterr().err


def test_an_executives_file_that_is_a_list_marks_the_denylist_degraded(tmp_path):
    """Valid JSON of the wrong shape is a failed harvest too, not an empty one."""
    data = _overlay(tmp_path)
    (data / "admin" / "executives.json").write_text('["quill"]', encoding="utf-8")
    assert build_denylist(data).degraded


@pytest.mark.parametrize("relpath", [
    "admin/executives.json",
    "datastore/operations/tribe/fireside-state/tribe-roster.json",
    "config/fireside-schedule.json",
])
def test_an_absent_source_is_not_degraded(tmp_path, relpath):
    """The other half of the contract: a public clone has none of these files.

    If absence degraded, the gate would refuse on every clone that has no DATA
    overlay, which is the outcome `build_denylist`'s empty-overlay branch exists
    to avoid.
    """
    data = _overlay(tmp_path)
    (data / relpath).unlink()
    assert not build_denylist(data).degraded


def test_tokens_harvested_before_the_failure_are_kept(tmp_path):
    """Documented contract: `degraded` is the signal, never the token count."""
    data = _overlay(tmp_path)
    roster = data / "datastore/operations/tribe/fireside-state/tribe-roster.json"
    roster.write_text("{ not json", encoding="utf-8")
    dl = build_denylist(data)
    assert dl.degraded
    assert "quill-marchetti" in dl.tokens, "earlier harvesters' work was discarded"


# ============================================================
# 2. The corporate write wall
# ============================================================

@pytest.fixture(scope="module")
def dispatch():
    path = ROOT / ".claude" / "hooks" / "_dispatch.py"
    spec = importlib.util.spec_from_file_location("hook_dispatch_corp", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_workspace(tmp_path, identity: str) -> Path:
    (tmp_path / "corporate").mkdir()
    (tmp_path / ".workspace-identity.json").write_text(identity, encoding="utf-8")
    return tmp_path


def _payload(root: Path, target: Path) -> dict:
    return {"tool_name": "Write", "cwd": str(root),
            "tool_input": {"file_path": str(target)}}


def test_a_readable_exec_identity_still_blocks_corporate(dispatch, tmp_path):
    """The floor for the two tests below."""
    root = _exec_workspace(tmp_path, json.dumps({"type": "exec-workspace"}))
    verdict = dispatch.check_protect_corporate(_payload(root, root / "corporate" / "a.md"))
    assert verdict and verdict["decision"] == "block"
    assert "read-only and managed by the CEO" in verdict["reason"]


@pytest.mark.parametrize("identity", ["{ not json", '["exec-workspace"]', '"exec"'])
def test_an_unreadable_identity_blocks_corporate_instead_of_allowing(
        dispatch, tmp_path, identity):
    """Present but unparseable means the type is UNKNOWN, and unknown is not CEO.

    `_identity_root` only answers a directory that HAS the file, so this is
    never the public-clone case. Returning None here switched the wall off for
    the whole session.
    """
    root = _exec_workspace(tmp_path, identity)
    verdict = dispatch.check_protect_corporate(_payload(root, root / "corporate" / "a.md"))
    assert verdict and verdict["decision"] == "block"
    assert "cannot read" in verdict["reason"]
    assert ".workspace-identity.json" in verdict["reason"]


@pytest.mark.parametrize("identity", ["{ not json", '["exec-workspace"]'])
def test_an_unreadable_identity_blocks_nothing_outside_corporate(
        dispatch, tmp_path, identity):
    """The refusal asks about the WRITE, not about the environment.

    An environment-shaped refusal here would freeze every path in a workspace
    whose identity file lost a byte. Only corporate/ is at stake, so only
    corporate/ is refused.
    """
    root = _exec_workspace(tmp_path, identity)
    (root / "outputs").mkdir()
    assert dispatch.check_protect_corporate(
        _payload(root, root / "outputs" / "note.md")) is None


def test_a_ceo_identity_still_allows_corporate(dispatch, tmp_path):
    """The CEO workspace is the source of truth and must stay unblocked."""
    root = _exec_workspace(tmp_path, json.dumps({"type": "ceo-master"}))
    assert dispatch.check_protect_corporate(
        _payload(root, root / "corporate" / "a.md")) is None
