"""Tests for the SKILL.md size budget in scripts/skill-metadata-check.py (F-5.3).

The size gate is UNCONDITIONAL: any HARD violation (> 500 lines OR > 18432 bytes)
makes main() exit 1 regardless of --fail-on-missing, so the flagless CI invocation
enforces it. A file in the warn band [16384, 18432] prints a non-blocking WARN and
exits 0; a file under 16384 is silent-OK. All tests run against a tmp fixture skills
tree (module get_workspace_root monkeypatched) so none depends on the real skill sizes,
which change over time. The script filename is kebab-case, so it is loaded via importlib.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK_PATH = ROOT / "scripts" / "skill-metadata-check.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("skill_metadata_check", CHECK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chk = _load_check()


_FRONTMATTER = """---
name: {name}
description: "test skill {name}"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: false
  shared_state: []
  triggers: []
---
# {name}

"""


def _write_skill_of_bytes(skills_dir: Path, name: str, target_bytes: int) -> None:
    """Write a valid-frontmatter SKILL.md padded to exactly target_bytes."""
    base = _FRONTMATTER.format(name=name)
    pad = target_bytes - len(base.encode("utf-8"))
    if pad > 0:
        base += "x" * pad
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(base, encoding="utf-8")


def _write_skill_of_lines(skills_dir: Path, name: str, line_count: int) -> None:
    """Write a valid-frontmatter SKILL.md with more than line_count newlines but small bytes."""
    base = _FRONTMATTER.format(name=name) + ("body\n" * line_count)
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(base, encoding="utf-8")


@pytest.fixture
def fixture_root(tmp_path, monkeypatch):
    """A tmp workspace root with .claude/skills; get_workspace_root patched to it."""
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setattr(chk, "get_workspace_root", lambda: tmp_path)
    return tmp_path, skills_dir


def _run_flagless(monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["skill-metadata-check.py"])
    return chk.main()


def test_over_byte_cap_fails_and_names_file(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _write_skill_of_bytes(skills_dir, "toobig", 19000)

    rc = _run_flagless(monkeypatch)
    out = capsys.readouterr().out

    assert rc == 1
    assert "toobig" in out
    assert "18432" in out  # the hard cap comparison is surfaced


def test_warn_band_passes_but_warns(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _write_skill_of_bytes(skills_dir, "warnish", 17000)

    rc = _run_flagless(monkeypatch)
    out = capsys.readouterr().out

    assert rc == 0
    assert "WARN" in out
    assert "warnish" in out


def test_under_budget_is_silent_ok(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    _write_skill_of_bytes(skills_dir, "small", 15000)

    rc = _run_flagless(monkeypatch)
    out = capsys.readouterr().out

    assert rc == 0
    # No size section is printed when every skill is OK.
    assert "SKILL.md size budget" not in out


def test_over_line_cap_fails(fixture_root, monkeypatch, capsys):
    _, skills_dir = fixture_root
    # 600 body lines -> > 500 newlines, but well under the byte cap.
    _write_skill_of_lines(skills_dir, "toolong", 600)

    rc = _run_flagless(monkeypatch)
    out = capsys.readouterr().out

    assert rc == 1
    assert "toolong" in out
    assert "500 lines" in out


def test_size_gate_fires_without_fail_on_missing(fixture_root, monkeypatch):
    """The size gate is unconditional: HARD exits 1 even flagless (no --fail-on-missing)."""
    _, skills_dir = fixture_root
    _write_skill_of_bytes(skills_dir, "toobig", 19000)

    assert _run_flagless(monkeypatch) == 1


def test_classify_size_boundaries():
    """Direct unit check of the classifier at the threshold boundaries."""
    assert chk.classify_size(0, chk.BYTE_WARN - 1) == "OK"
    assert chk.classify_size(0, chk.BYTE_WARN) == "WARN"
    assert chk.classify_size(0, chk.BYTE_HARD_CAP) == "WARN"
    assert chk.classify_size(0, chk.BYTE_HARD_CAP + 1) == "HARD"
    assert chk.classify_size(chk.LINE_HARD_CAP, 0) == "OK"
    assert chk.classify_size(chk.LINE_HARD_CAP + 1, 0) == "HARD"
