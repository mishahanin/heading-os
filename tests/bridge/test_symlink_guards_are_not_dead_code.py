"""A documented control that cannot fire is worse than no control.

Found by the 2026-08-23 engine audit (findings 3 and 6, on two files). Measured
across ``scripts/bridge_daemon/sources/`` on 2026-08-24: NINE of the ten
symlink guards were unreachable, all the same way.

    target = (base / rel_path).resolve()
    target.relative_to(base_resolved)      # containment: works
    if target.is_symlink():                # symlink ban: can never be True
        return {"ok": False, "error": "symlinks not allowed"}

``Path.resolve()`` dereferences every link in the path, so the question was
being asked of the file the link POINTS AT. ``library.py`` even carried a
comment reasoning it through and reaching the wrong conclusion: "the resolve()
above already follows symlinks, then our relative_to check would catch any
escape. Still, explicit is good." Explicit, and inert.

This is not a traversal hole -- containment still held, so nothing outside the
served directory was ever reachable. What was lost is the workspace's
no-symlinks-ever policy INSIDE the tree, while four of these readers list
"No symlinks" among their documented validations. The next author budgets for a
control that is not running.

Two guards, then: the behavioural one (a link is refused) and the structural
one (nobody re-binds the check to a resolved path).
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES = ROOT / "scripts" / "bridge_daemon" / "sources"

sys.path.insert(0, str(ROOT))
from scripts.bridge_daemon._safepath import contains_symlink  # noqa: E402


# --- the helper answers the question the guards meant to ask -----------------

def test_a_plain_file_is_not_flagged(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    assert contains_symlink(tmp_path, tmp_path / "a.md") is False


def test_a_symlinked_file_is_flagged(tmp_path):
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    os.symlink(tmp_path / "real.md", tmp_path / "link.md")
    assert contains_symlink(tmp_path, tmp_path / "link.md") is True


def test_a_symlinked_PARENT_is_flagged(tmp_path):
    """The file itself is real; the directory above it is the link. `.resolve()`
    on the target hid this case as thoroughly as the direct one."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "a.md").write_text("x", encoding="utf-8")
    os.symlink(real_dir, tmp_path / "linked_dir")
    assert contains_symlink(tmp_path, tmp_path / "linked_dir" / "a.md") is True


def test_a_link_ABOVE_the_root_is_not_the_bans_business(tmp_path):
    """The workspace may legitimately sit under a linked mount."""
    real_root = tmp_path / "real_root"
    (real_root / "sub").mkdir(parents=True)
    (real_root / "sub" / "a.md").write_text("x", encoding="utf-8")
    os.symlink(real_root, tmp_path / "linked_root")
    root = tmp_path / "linked_root"
    assert contains_symlink(root, root / "sub" / "a.md") is False


def test_a_target_outside_the_root_is_refused(tmp_path):
    assert contains_symlink(tmp_path / "inside", tmp_path / "elsewhere" / "a.md") is True


# --- the readers actually refuse ---------------------------------------------

def test_read_skill_refuses_a_symlinked_skill_md(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    (skills / "real").mkdir(parents=True)
    (skills / "real" / "SKILL.md").write_text("---\nname: real\n---\nbody\n",
                                              encoding="utf-8")
    (skills / "clone").mkdir()
    os.symlink(skills / "real" / "SKILL.md", skills / "clone" / "SKILL.md")

    from scripts.bridge_daemon.sources.capabilities import read_skill
    assert read_skill(tmp_path, "real")["ok"] is True, "the honest path broke"
    got = read_skill(tmp_path, "clone")
    assert got["ok"] is False and got["error"] == "symlinks not allowed", got


def test_read_one_contact_refuses_a_symlinked_contact(tmp_path):
    data_root = tmp_path / "data"
    contacts = data_root / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "real-person.md").write_text("# Real Person\n", encoding="utf-8")
    os.symlink(contacts / "real-person.md", contacts / "shadow-person.md")

    from scripts.bridge_daemon.sources.contacts import read_one_contact
    ok = read_one_contact(tmp_path, "ceo", "real-person", data_root=data_root)
    assert ok["ok"] is True, ok
    got = read_one_contact(tmp_path, "ceo", "shadow-person", data_root=data_root)
    assert got["ok"] is False and got["error"] == "symlinks not allowed", got


# --- nobody rebinds the check to a resolved path -----------------------------

def _guard_bindings() -> list[tuple[str, int, str, str | None]]:
    """(file, line, variable, the source line that bound it) for each guard."""
    out = []
    for path in sorted(SOURCES.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"(\w+)\.is_symlink\(\)", line)
            if not m:
                continue
            var = m.group(1)
            binding = None
            for j in range(i, max(0, i - 60), -1):
                if re.match(rf"\s*{re.escape(var)}\s*=[^=]", lines[j]):
                    binding = lines[j].strip()
                    break
            out.append((path.name, i + 1, var, binding))
    return out


def test_the_detector_still_finds_a_guard():
    """A scan that matches nothing passes everything. studio.py keeps one
    legitimate `is_symlink()` on an unresolved glob result."""
    assert _guard_bindings(), (
        "no is_symlink() call left under sources/; if the guards were replaced "
        "wholesale, retarget this detector rather than deleting it"
    )


def test_no_symlink_check_is_asked_of_a_resolved_path():
    dead = [f"{f}:{n}  {var}.is_symlink()  <- {binding}"
            for f, n, var, binding in _guard_bindings()
            if binding and ".resolve()" in binding]
    assert not dead, (
        "these guards test a path that resolve() already dereferenced, so they "
        "can never fire; use _safepath.contains_symlink on the UNRESOLVED "
        "path:\n  " + "\n  ".join(dead)
    )


def test_every_reader_that_promises_no_symlinks_calls_the_live_guard():
    """The docstring and the code must agree in the same file."""
    missing = []
    for path in sorted(SOURCES.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "symlinks not allowed" not in src:
            continue
        if "contains_symlink(" not in src:
            missing.append(path.name)
    assert not missing, (
        "these files still return 'symlinks not allowed' from a check that "
        "cannot reach it: " + ", ".join(missing)
    )


def test_the_promise_is_written_where_the_guard_runs():
    """Four readers advertise 'No symlinks' in their validation list. Pin that
    those files are the ones carrying the live guard."""
    promising = []
    for path in sorted(SOURCES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            if re.search(r"No symlinks", doc, re.IGNORECASE):
                promising.append((path.name, node.name))
    assert promising, "the documented promise vanished; retarget this test"
    for fname, func in promising:
        src = (SOURCES / fname).read_text(encoding="utf-8")
        assert "contains_symlink(" in src, (
            f"{fname}:{func} documents 'No symlinks' and the file has no live "
            "guard behind it"
        )
