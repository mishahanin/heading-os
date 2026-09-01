import json
from pathlib import Path

from scripts.utils.update_registry import Component
from scripts.utils import update_common as uc

def _comp(cmd, regex=None):
    cur = {"via": "shell", "cmd": cmd}
    if regex:
        cur["regex"] = regex
    return Component(name="c", tier="auto", current=cur,
                     latest={"via": "pypi", "package": "c"})

def test_resolve_current_first_line_no_regex():
    assert uc.resolve_current(_comp("printf '1.2.3\\nnoise'")) == "1.2.3"

def test_resolve_current_regex_capture():
    assert uc.resolve_current(_comp("echo 'Version: 7.2.92 build'", r"Version:\s*([0-9.]+)")) == "7.2.92"

def test_resolve_current_unmatched_regex_is_empty():
    assert uc.resolve_current(_comp("echo nope", r"Version:\s*([0-9.]+)")) == ""

def test_versions_differ_normalizes():
    assert uc.versions_differ("2026.07.20", "2026.7.20") is False
    assert uc.versions_differ("1.0.0", "1.1.0") is True

def test_versions_differ_empty_is_false():
    assert uc.versions_differ("", "1.0") is False

def test_write_state_atomic(tmp_path):
    p = tmp_path / "s.json"
    uc.write_state({"components": {}}, p)
    assert p.exists() and not (tmp_path / "s.json.tmp").exists()


def test_write_state_renames_a_temporary_into_place(tmp_path, monkeypatch):
    """The assertion the test above is NAMED for and never made.

    "The file exists and no `.tmp` is left behind" is satisfied by a plain
    `path.write_text(...)`, which is the exact write the workspace rule on
    atomic state writes forbids. MEASURED 2026-09-01 by replacing the tmp plus
    `os.replace` body with one `write_text`: both this file and its duplicate in
    `tests/test_update_manager_check.py` stayed green, and
    `tests/test_atomic_scripts.py` does not name this module, so the update
    manager's only state writer had no guard anywhere.

    The property is observed at the rename: the destination must still hold the
    PREVIOUS document when `os.replace` is called. A writer that truncated the
    destination first would already have exposed a half-written state file to
    the `check` CLI reading it concurrently.
    """
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"components": {"old": {"status": "current"}}}),
                 encoding="utf-8")

    seen: list[tuple[str, str, str]] = []
    real_replace = uc.os.replace

    def _watch(src, dst):
        seen.append((Path(src).name, Path(dst).name,
                     Path(dst).read_text(encoding="utf-8")))
        return real_replace(src, dst)

    monkeypatch.setattr(uc.os, "replace", _watch)
    uc.write_state({"components": {"new": {"status": "pending-auto"}}}, p)
    monkeypatch.undo()

    assert len(seen) == 1, "write_state renamed nothing into place"
    src_name, dst_name, dst_at_rename = seen[0]
    assert dst_name == "s.json"
    assert src_name != "s.json", "the temporary and the destination are one file"
    assert "old" in dst_at_rename, (
        "the destination was already overwritten before the rename, so a "
        "concurrent reader could hold a half-written state file")
    assert json.loads(p.read_text(encoding="utf-8"))["components"].keys() == {"new"}
    assert not list(tmp_path.glob("*.tmp")), "the temporary outlived the write"
