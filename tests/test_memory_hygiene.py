"""Regression tests for the memory-hygiene objective-defect detector.

Encodes the plan's Success Signal: a fixture with one dangling superseded_by
ref, one orphan memory file, and an over-budget MEMORY.md flags exactly those
defects and gates (exit 1); a clean fixture does not (exit 0). Also asserts the
detector never mutates memory and writes exactly one report file.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_hygiene():
    spec = importlib.util.spec_from_file_location(
        "memory_hygiene_mod", ROOT / "scripts" / "memory-hygiene.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from scripts.utils.memory_health import compute_memory_defects  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_defect_memory(memory_dir: Path) -> None:
    """One orphan file + an over-budget MEMORY.md + one stale file."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "linked-fact.md").write_text("a linked fact\n", encoding="utf-8")
    (memory_dir / "orphan-fact.md").write_text("an orphan fact\n", encoding="utf-8")
    stale = memory_dir / "stale-fact.md"
    stale.write_text("a stale fact\n", encoding="utf-8")
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(stale, (sixty_days_ago, sixty_days_ago))
    # MEMORY.md references linked-fact + stale-fact but NOT orphan-fact, and runs
    # past the 200-line budget.
    index = ["# Memory index", "", "- linked-fact.md", "- stale-fact.md", ""]
    index += [f"- filler line {i}" for i in range(250)]
    (memory_dir / "MEMORY.md").write_text("\n".join(index), encoding="utf-8")


def _make_clean_memory(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "linked-fact.md").write_text("a linked fact\n", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text(
        "# Memory index\n\n- linked-fact.md\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# compute_memory_defects (pure util)
# ---------------------------------------------------------------------------

def test_compute_flags_orphan_overbudget_stale(tmp_path):
    mem = tmp_path / "auto-memory"
    _make_defect_memory(mem)
    d = compute_memory_defects(mem)
    assert d["status"] == "ok"
    assert "orphan-fact.md" in d["orphans"]
    assert "linked-fact.md" not in d["orphans"]
    assert d["over_budget"] is True
    assert any(name == "stale-fact.md" for name, _ in d["stale"])


def test_compute_clean(tmp_path):
    mem = tmp_path / "auto-memory"
    _make_clean_memory(mem)
    d = compute_memory_defects(mem)
    assert d["orphans"] == []
    assert d["over_budget"] is False
    assert d["stale"] == []


def test_compute_missing_dir(tmp_path):
    d = compute_memory_defects(tmp_path / "does-not-exist")
    assert d["status"] == "missing"
    assert d["orphans"] == [] and d["over_budget"] is False


# ---------------------------------------------------------------------------
# gather() gate (Success Signal)
# ---------------------------------------------------------------------------

def _patch_brain(mod, monkeypatch, *, errors):
    fake = {
        "ok": bool(errors is not None),
        "data": {"temporal_validity": {"errors": errors or [], "warnings": []}}
        if errors is not None
        else None,
        "note": "" if errors is not None else "brain unavailable (test)",
    }
    monkeypatch.setattr(mod, "collect_brain_compile", lambda: fake)


def test_gather_gates_on_objective_defects(tmp_path, monkeypatch):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _make_defect_memory(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _patch_brain(mod, monkeypatch, errors=[{"message": "dangling superseded_by", "file": "positions/x.md"}])

    result = mod.gather()
    # 1 temporal error + 1 orphan + over_budget = 3
    assert result["gate_count"] == 3
    assert len(result["gate"]["temporal_errors"]) == 1
    assert result["gate"]["memory_orphans"] == ["orphan-fact.md"]
    assert result["gate"]["over_budget"] is True


def test_gather_clean(tmp_path, monkeypatch):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _make_clean_memory(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _patch_brain(mod, monkeypatch, errors=[])

    result = mod.gather()
    assert result["gate_count"] == 0


def test_gather_degrades_when_brain_unavailable(tmp_path, monkeypatch):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _make_clean_memory(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _patch_brain(mod, monkeypatch, errors=None)  # brain absent

    result = mod.gather()
    assert result["brain_ok"] is False
    assert result["gate_count"] == 0  # auto-memory half still evaluated, clean


# ---------------------------------------------------------------------------
# No-mutation + single-file-write
# ---------------------------------------------------------------------------

def test_detector_never_mutates_memory(tmp_path, monkeypatch):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    mem = data_root / "auto-memory"
    _make_defect_memory(mem)
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _patch_brain(mod, monkeypatch, errors=[])

    before = {p.name: (p.read_text(encoding="utf-8"), p.stat().st_mtime) for p in mem.glob("*.md")}
    mod.gather()
    mod.render_report(mod.gather(), "2026-06-26T00:00:00+04:00")
    after = {p.name: (p.read_text(encoding="utf-8"), p.stat().st_mtime) for p in mem.glob("*.md")}
    assert before == after


def test_write_report_single_file(tmp_path, monkeypatch):
    mod = _load_hygiene()
    out_root = tmp_path / "out"
    monkeypatch.setattr(mod, "get_outputs_dir", lambda: out_root)
    from datetime import datetime

    path = mod.write_report("# report\n", datetime(2026, 6, 26, 12, 0, 0))
    report_dir = out_root / "operations" / "memory-hygiene"
    files = list(report_dir.iterdir())
    assert files == [path]
    assert path.name == "2026-06-26_memory-hygiene_report.md"
    assert path.read_text(encoding="utf-8") == "# report\n"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
