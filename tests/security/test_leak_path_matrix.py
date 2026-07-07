#!/usr/bin/env python3
"""Leak-path matrix (F-6.3): attack every headless-testable engine/data
enforcement layer on purpose.

Parameterized ``{write-vector} x {data-class target}`` cells assert that each
attempted leak is BLOCKED, by the EXPECTED layer, with that layer's distinctive
message substring (asserted on stable substrings, never full strings). Every
cell runs inside a sandboxed throwaway git repo plus a fake overlay under
``tmp_path``; the matrix never touches the real tree.

Complements (does NOT duplicate):
  - tests/test_engine_tree_clean.py   -- the tree-clean detector on the real tree
  - tests/test_data_root_no_bypass.py -- the static AST engine-root-join guard
  - tests/test_push_all_gate.py       -- engine_clean_scan on crm/contacts (tracked + untracked)

The genuine gaps this closes: engine_content_scan (the real-entity CONTENT wall,
untested), plus one consolidated vector x target x layer artifact that also
covers leak-guard-staged, content-guard, and the data-root seam.

The data-path-redirect PreToolUse hook fires only inside the Claude Code runtime;
it is a DOCUMENTED MANUAL DRILL (docs/SECURITY-MODEL.md#manual-security-drills),
not a headless cell -- simulating it would assert the simulation, not the control.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Real workspace root (tests/security/ -> ../../). Used to load kebab-named
# modules and to assert the sandbox never resolves under the real tree.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import content_denylist  # noqa: E402
from scripts.utils import paths  # noqa: E402
from scripts.utils.engine_guard import scan_engine_repo  # noqa: E402


def _load(mod_name: str, rel: str):
    """Load a kebab-named script as a module (mirrors tests/test_push_all_gate.py)."""
    spec = importlib.util.spec_from_file_location(mod_name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


push_all = _load("push_all", "scripts/push-all.py")
leak_guard = _load("leak_guard", "scripts/leak-guard.py")


# ============================================================
# Layer registry (the meta-test in test_coverage_acceptance asserts on it)
# ============================================================
LAYER_TREECLEAN = "tree-clean guard"
LAYER_LEAKGUARD = "leak-guard"
LAYER_CONTENT = "content-guard"
LAYER_PUSHWALL = "push wall"
LAYER_DATAROOT = "data-root seam"
LAYER_REDIRECT = "data-path redirect"

# Hook-mediated vectors that cannot run headless -> documented manual drills.
MANUAL_DRILLS = [
    {
        "vector": "data-path-redirect hook",
        "layer": LAYER_REDIRECT,
        "why_manual": "fires only inside the Claude Code PreToolUse runtime",
        "drill_ref": "docs/SECURITY-MODEL.md#manual-security-drills",
    },
]


# ============================================================
# Sandbox fixture + write-vector helpers
# ============================================================
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def sandbox(tmp_path):
    """A throwaway engine git repo + fake data overlay, both under tmp_path."""
    engine = tmp_path / "engine"
    engine.mkdir()
    _git(engine, "init", "-q")
    _git(engine, "config", "user.email", "t@t")
    _git(engine, "config", "user.name", "t")
    (engine / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    return SimpleNamespace(engine=engine, overlay=overlay)


def _plant(engine: Path, rel: str, body: str = "synthetic content\n") -> Path:
    p = engine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# Representative data-class targets (all route `private` per config/routing-map.yaml).
# Synthetic names only -- no real entity ever appears in the engine repo.
TARGETS = [
    "crm/contacts/jane-roe.md",
    "threads/acme-telco-deal.md",
    "context/people-synthetic.md",
]


def _v_direct_write(engine, overlay, rel):
    _plant(engine, rel)


def _v_heredoc(engine, overlay, rel):
    p = engine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bash", "-c", f"cat > {p} <<'EOF'\nsynthetic\nEOF"], check=True)


def _v_append(engine, overlay, rel):
    p = engine / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bash", "-c", f"echo synthetic >> {p}"], check=True)


def _v_rename_from_overlay(engine, overlay, rel):
    src = overlay / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("synthetic\n", encoding="utf-8")
    dst = engine / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _v_copy(engine, overlay, rel):
    src = overlay / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("synthetic\n", encoding="utf-8")
    dst = engine / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(dst))


def _v_symlink(engine, overlay, rel):
    src = overlay / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("synthetic\n", encoding="utf-8")
    dst = engine / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(src, dst)


def _v_git_add(engine, overlay, rel):
    _plant(engine, rel)
    _git(engine, "add", rel)


# Ordered so the meta-test count is derived from this dict, not hardcoded.
VECTORS = {
    "directwrite": _v_direct_write,
    "heredoc": _v_heredoc,
    "append": _v_append,
    "mv": _v_rename_from_overlay,
    "cp": _v_copy,
    "symlink": _v_symlink,
    "gitadd": _v_git_add,
}


def _apply_vector(vector, engine, overlay, rel):
    """Apply a write vector, skipping the cell if the platform cannot symlink."""
    try:
        VECTORS[vector](engine, overlay, rel)
    except (OSError, NotImplementedError) as exc:
        if vector == "symlink":
            pytest.skip(f"symlink unsupported on this platform: {exc}")
        raise


# ============================================================
# Fixture safety + manual-drill documentation
# ============================================================
def test_fixture_stays_under_tmp(sandbox, tmp_path):
    """The sandbox must resolve under tmp_path and never under the real tree."""
    for p in (sandbox.engine, sandbox.overlay):
        assert str(p.resolve()).startswith(str(tmp_path.resolve()))
        assert ROOT not in p.resolve().parents


def test_manual_drills_documented():
    """Every hook-mediated manual drill must be present in the security model,
    so a drill can never silently disappear from the doc."""
    doc = (ROOT / "docs" / "SECURITY-MODEL.md").read_text(encoding="utf-8")
    assert "Manual security drills" in doc
    for drill in MANUAL_DRILLS:
        assert drill["vector"] in doc


# ============================================================
# Step 2 -- Placement matrix: every write-vector x data-class target is caught
# by the tree-clean guard (engine_guard.scan_engine_repo), which flags a
# data-class path however the file arrived. Only the vector-varying half is
# asserted; the pure-string find_data_artifacts half is identical across
# vectors and already covered by test_engine_tree_clean.py (scrutiny L1).
# ============================================================
@pytest.mark.parametrize("target", TARGETS, ids=lambda t: t.split("/")[0])
@pytest.mark.parametrize("vector", list(VECTORS), ids=lambda v: v)
def test_placement_treeclean_flags(sandbox, vector, target):
    _apply_vector(vector, sandbox.engine, sandbox.overlay, target)
    flagged = scan_engine_repo(sandbox.engine)
    assert target in flagged, (
        f"vector {vector!r} placed {target} but the tree-clean guard missed it "
        f"(flagged={flagged})"
    )


# Push-wall placement cells: use the vectors/targets test_push_all_gate.py does
# NOT cover (it covers crm/contacts/*.md, tracked + untracked, via direct write).
PUSHWALL_PLACEMENT = [
    ("heredoc", "threads/acme-telco-deal.md"),
    ("mv", "context/people-synthetic.md"),
    ("symlink", "threads/acme-telco-deal.md"),
]


@pytest.mark.parametrize(
    "vector,target", PUSHWALL_PLACEMENT, ids=[f"{v}-{t.split('/')[0]}" for v, t in PUSHWALL_PLACEMENT]
)
def test_pushwall_placement_refuses(sandbox, vector, target, capsys):
    _apply_vector(vector, sandbox.engine, sandbox.overlay, target)
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(sandbox.engine)
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "REFUSING TO PUSH" in out
    assert "data-class artifact" in out


# ============================================================
# Step 3 -- Leak-guard (staged): a data-class path added to the engine index is
# blocked by leak-guard.check_staged. HEADING_OS_ENGINE_REPO=1 forces the
# engine-repo gate on (the documented explicit override) so the cells are
# deterministic on a single-repo CI checkout too. check_paths (source-literal
# lint) is a separate axis, out of this matrix's scope.
# ============================================================
LEAKGUARD_TARGETS = ["crm/contacts/jane-roe.md", "threads/acme-telco-deal.md"]


@pytest.mark.parametrize("target", LEAKGUARD_TARGETS, ids=lambda t: t.split("/")[0])
def test_leakguard_staged_blocks(target, monkeypatch, capsys):
    monkeypatch.setenv("HEADING_OS_ENGINE_REPO", "1")
    rc = leak_guard.check_staged([target])
    assert rc == 1
    assert "BLOCKED - non-engine content staged into the engine repo" in capsys.readouterr().out


def test_leakguard_staged_ignores_engine_code(monkeypatch):
    monkeypatch.setenv("HEADING_OS_ENGINE_REPO", "1")
    assert leak_guard.check_staged(["scripts/foo.py", "tests/bar.py"]) == 0


# ============================================================
# Step 4a -- Content-guard: an engine-routed file carrying a real-entity token
# is blocked by push_all.engine_content_scan. The denylist is built from a FAKE
# overlay, and build_denylist at the default strict=False harvests ONLY the CRM
# filename stem (the slug), never the display name/handle -- so the planted
# token MUST be the slug. Synthetic slug, verified not in the fictional/identity
# allowlists, so zero real data is involved.
# ============================================================
CONTENT_SLUG = "zephyr-liaison"
CONTENT_ENGINE_FILES = ["scripts/leaky_example.py", ".claude/rules/leaky_example.md"]


def _plant_overlay_contact(overlay: Path) -> None:
    p = overlay / "crm" / "contacts" / f"{CONTENT_SLUG}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nname: Zephyr Liaison\n---\nSynthetic contact.\n", encoding="utf-8")


def test_content_denylist_harvests_slug(sandbox):
    """Sanity: the fake overlay's contact slug enters the denylist, so a
    downstream content cell cannot false-green on an empty denylist."""
    _plant_overlay_contact(sandbox.overlay)
    dl = content_denylist.build_denylist(sandbox.overlay)
    assert not dl.degraded
    assert CONTENT_SLUG in dl.tokens


@pytest.mark.parametrize("engine_file", CONTENT_ENGINE_FILES, ids=lambda f: f.split("/")[0])
def test_content_guard_flags_slug(sandbox, engine_file, capsys):
    _plant_overlay_contact(sandbox.overlay)
    _plant(sandbox.engine, engine_file, f"# note referencing {CONTENT_SLUG} in engine code\n")
    _git(sandbox.engine, "add", engine_file)
    with pytest.raises(SystemExit) as exc:
        push_all.engine_content_scan(sandbox.engine, sandbox.overlay)
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "REFUSING TO PUSH" in out
    assert "real-entity CONTENT" in out


def test_content_guard_clean_control(sandbox):
    _plant_overlay_contact(sandbox.overlay)
    _plant(sandbox.engine, "scripts/clean_example.py", "print('no denylisted token here')\n")
    _git(sandbox.engine, "add", "scripts/clean_example.py")
    assert push_all.engine_content_scan(sandbox.engine, sandbox.overlay) is None


# ============================================================
# Step 4b -- Data-root seam: require_writable_data_root refuses to hand back a
# writable root when the overlay is a read-only demo, or when its schema is
# behind the engine (pending migrations). This is the seam's runtime write
# refusal; the static engine-root-join bypass is the complementary mode proven
# by tests/test_data_root_no_bypass.py.
# ============================================================
DATAROOT_MODES = ["demo", "stale_schema"]


@pytest.mark.parametrize("mode", DATAROOT_MODES)
def test_dataroot_seam_refuses(mode, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)
    if mode == "demo":
        monkeypatch.setattr(paths, "data_root_is_demo", lambda: True)
    else:
        monkeypatch.setattr(paths, "data_root_is_demo", lambda: False)
        monkeypatch.setattr(paths, "read_data_schema_version", lambda: -1)
    with pytest.raises(paths.DataRootError):
        paths.require_writable_data_root()


def test_dataroot_seam_allows_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "data_root_is_demo", lambda: False)
    monkeypatch.setattr(paths, "read_data_schema_version", lambda: 10 ** 9)
    assert paths.require_writable_data_root() == tmp_path


# ============================================================
# Step 5 -- The pure-code push wall survives a --no-verify commit. A data-class
# file committed with hooks bypassed still cannot leave: engine_clean_scan runs
# in push-all itself, no skip flag. test_push_all_gate.py covers the tracked /
# untracked crm/contacts baseline; this adds the post---no-verify-commit angle
# on a different target.
#
# Note: a content_scan (secret-like CONTENT) cell was deliberately NOT added --
# a secret-shaped literal in this source would trip the commit-time secret gate
# on the test file itself. The secret wall is real code on the push path; the
# real-entity CONTENT wall is exercised above via engine_content_scan.
# ============================================================
def test_pushwall_survives_no_verify_commit(sandbox, capsys):
    _plant(sandbox.engine, "threads/acme-telco-deal.md", "synthetic\n")
    _git(sandbox.engine, "add", "-A")
    _git(sandbox.engine, "commit", "-m", "x", "--no-verify")
    with pytest.raises(SystemExit) as exc:
        push_all.engine_clean_scan(sandbox.engine)
    assert exc.value.code == 2
    assert "REFUSING TO PUSH" in capsys.readouterr().out


# ============================================================
# Step 6 -- Coverage acceptance (self-checking). Counts are derived from the
# real parametrize lists above, not hardcoded, so adding or removing a cell
# updates the count automatically. A layer dropping below 2 FAILS -- it does
# not silently auto-pass (a real coverage regression). The data-path-redirect
# layer is manual-only and must never appear as an executable cell.
# ============================================================
# layer -> number of executable BLOCKER cells (each proves the layer blocks a leak).
# On the CI target (ubuntu) the symlink vector runs, so it is counted executable;
# it self-skips only on a platform that cannot symlink.
LAYER_CELL_COUNTS = {
    LAYER_TREECLEAN: len(VECTORS) * len(TARGETS),          # placement matrix
    LAYER_PUSHWALL: len(PUSHWALL_PLACEMENT) + 1,           # placement + --no-verify
    LAYER_LEAKGUARD: len(LEAKGUARD_TARGETS),               # staged blocker cells
    LAYER_CONTENT: len(CONTENT_ENGINE_FILES),              # slug-flag blocker cells
    LAYER_DATAROOT: len(DATAROOT_MODES),                   # demo + stale-schema refusals
}


def test_coverage_acceptance():
    total = sum(LAYER_CELL_COUNTS.values())
    assert total >= 24, f"executable blocker cells {total} < 24 (F-6.3 acceptance)"
    for layer, n in LAYER_CELL_COUNTS.items():
        assert n >= 2, f"layer {layer!r} is the blocker in only {n} cell(s); need >= 2"
    # The data-path-redirect hook is documented-manual, never an executable cell.
    assert LAYER_REDIRECT not in LAYER_CELL_COUNTS
    assert any(d["layer"] == LAYER_REDIRECT for d in MANUAL_DRILLS)
