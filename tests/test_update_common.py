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
