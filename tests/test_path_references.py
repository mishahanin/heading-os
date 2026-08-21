#!/usr/bin/env python3
"""The engine's prose never names an engine path that does not exist.

Documentation rot is silent: a script is renamed, the prose that names it keeps
pointing at nothing, and the next reader pastes a command that fails. The
2026-08-21 sweep found eight such sites accumulated over months -- among them a
`/odin` ingest command with a dead path and a `docs/SECURITY-MODEL.md` paragraph
describing two hook files deleted in ba1affd.

This asserts the gate that scripts/check-path-references.py enforces, plus the
two properties that keep the gate honest: it must actually detect a dangling
path, and it must NOT flag a path that lives in the private overlay (absent on a
public clone, so its absence is not evidence -- see .claude/rules/scope-claims.md).
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

_SRC = get_workspace_root() / "scripts" / "check-path-references.py"
_spec = importlib.util.spec_from_file_location("check_path_references", _SRC)
cpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpr)


def test_engine_prose_names_no_missing_engine_path():
    """The real gate: nothing dangling beyond the frozen baseline."""
    found = cpr.scan(get_workspace_root())
    new = {p: sites for p, sites in found.items() if p not in cpr.BASELINE}
    assert not new, (
        "engine prose names path(s) that do not exist:\n"
        + "\n".join(
            f"  {p}  ({', '.join(f'{r}:{n}' for r, n in sites[:3])})"
            for p, sites in sorted(new.items())
        )
        + "\nFix the path, or add it to BASELINE with the reason it should not exist."
    )


def test_detector_flags_a_planted_dangling_path(tmp_path, monkeypatch):
    """A regex that matches nothing would pass everything. Prove it still bites."""
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["planted.md"])
    (tmp_path / "planted.md").write_text(
        "Run `python scripts/definitely-not-a-real-script.py --now` to do the thing.\n",
        encoding="utf-8",
    )
    found = cpr.scan(tmp_path)
    assert "scripts/definitely-not-a-real-script.py" in found


def test_detector_skips_a_path_that_routes_to_the_overlay(tmp_path, monkeypatch):
    """A private-overlay path is absent on a public clone; absence is not rot."""
    monkeypatch.setattr(cpr, "tracked_markdown", lambda root: ["planted.md"])
    (tmp_path / "planted.md").write_text(
        "Voice guide: `reference/misha-voice.md`.\n", encoding="utf-8"
    )
    found = cpr.scan(tmp_path)
    assert "reference/misha-voice.md" not in found


def test_every_baseline_entry_states_a_reason():
    """An entry without a reason is indistinguishable from rot someone gave up on."""
    unexplained = [p for p, reason in cpr.BASELINE.items() if not (reason or "").strip()]
    assert not unexplained, f"BASELINE entries missing a reason: {unexplained}"


def test_baseline_carries_no_entry_that_is_already_clean():
    """The ratchet only shrinks; a stale entry hides a path that could be re-broken."""
    found = cpr.scan(get_workspace_root())
    stale = sorted(p for p in cpr.BASELINE if p not in found)
    assert not stale, (
        "BASELINE lists path(s) the prose no longer names -- drop them:\n"
        + "\n".join(f"  {p}" for p in stale)
    )
