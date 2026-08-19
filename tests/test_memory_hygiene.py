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
# Live-state hooks (the retired ## Active Threads shape)
# ---------------------------------------------------------------------------
#
# Guards the 2026-08-20 removal of the `## Active Threads` block from MEMORY.md.
# Every one of its 29 rows quoted a live status and a live date, which
# memory-discipline.md forbids in an index hook; the block was 4,820 of 17,639
# chars of a file injected inside the cached prompt prefix at every SessionStart,
# and it was already stale (30 threads active on disk, 29 listed, 1 of the 29
# closed). These cases pin the detector that stops it regrowing unannounced.

_THREAD_ROW = (
    "- [ExampleTelco demo](threads/business/2026-07-01-exampletelco-demo.md)"
    " - active, last 2026-08-17\n"
)
_QUIET_ROW = (
    "- [Contoso Advisory alliance](threads/business/2026-05-20-contoso-advisory.md)"
    " - [quiet until 2026-08-25] active, last 2026-06-22\n"
)
_STATUS_ROW = "- [Some plan](plans/2026-08-01-thing.md) - status: in progress\n"


def test_live_state_rows_flags_the_retired_active_threads_shape(tmp_path):
    mod = _load_hygiene()
    mem = tmp_path / "auto-memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(
        "# Memory index\n\n## Active Threads\n\n### Business\n"
        + _THREAD_ROW + _QUIET_ROW + _STATUS_ROW,
        encoding="utf-8",
    )
    res = mod.scan_live_state_rows(mem)
    assert res["ok"] is True
    assert len(res["flagged"]) == 3
    signals = {f["target"]: f["signals"] for f in res["flagged"]}
    assert "last-touched date" in signals[
        "threads/business/2026-07-01-exampletelco-demo.md"
    ]
    assert set(signals["threads/business/2026-05-20-contoso-advisory.md"]) == {
        "last-touched date",
        "quiet-until date",
    }
    assert signals["plans/2026-08-01-thing.md"] == ["inline status"]


def test_live_state_rows_clean_on_the_real_pointer_index(tmp_path):
    """Topic + pointer hooks, and the prose that replaced the block, stay clean.

    The replacement section names the defect in prose and links the CLI, so the
    detector must key on the hook SHAPE, not on the words: a paragraph or a bare
    bullet carrying no link target is never a hook.
    """
    mod = _load_hygiene()
    mem = tmp_path / "auto-memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text(
        "# Memory index\n\n"
        "- Memory: [never delete, only annotate](never-delete-only-annotate.md)\n"
        "- [DPI quote archive price benchmarks](dpi-quote-archive-price-benchmarks.md)"
        " — 174 quotes 2014-2022\n\n"
        "## Active Threads\n\n"
        "Not listed here. Every row quoted a live status and a live date, e.g.\n"
        "active, last 2026-08-19, which memory-discipline.md forbids.\n\n"
        "- `python scripts/thread.py list` (`--type`, `--status` to narrow)\n"
        "- `/prime`, whose active-threads check surfaces the stale ones\n\n"
        "### Business\n\n### Personal (CEO-ONLY)\n",
        encoding="utf-8",
    )
    assert mod.scan_live_state_rows(mem)["flagged"] == []


def test_live_state_rows_is_advisory_never_gates(tmp_path, monkeypatch):
    """thread.py re-adds rows by design, so this reports; it must not gate."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _make_clean_memory(data_root / "auto-memory")
    mem_md = data_root / "auto-memory" / "MEMORY.md"
    mem_md.write_text(mem_md.read_text(encoding="utf-8") + _THREAD_ROW, encoding="utf-8")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _patch_brain(mod, monkeypatch, errors=[])

    result = mod.gather()
    assert len(result["live_state_rows"]["flagged"]) == 1
    assert result["gate_count"] == 0
    assert "### Hooks quoting live status / date: 1" in mod.render_report(
        result, "2026-08-20T00:00:00+04:00"
    )


def test_live_state_rows_missing_memory_md(tmp_path):
    mod = _load_hygiene()
    res = mod.scan_live_state_rows(tmp_path / "does-not-exist")
    assert res["ok"] is True and res["flagged"] == []


def test_the_live_index_is_reported_but_never_gated():
    """The shipped MEMORY.md is MEASURED here, never gated on.

    The detector's own docstring says why: `thread.py open|log` re-adds rows
    through ensure_active_threads_section() / add_thread_to_index(), so a hard
    assertion on the live file turns the next legitimate thread write into a red
    suite. The first version of this test asserted `flagged == []` against the
    real overlay, which contradicted the advisory design it was written to
    protect and would have failed within a day.

    What IS held: the scanner runs on the real file without crashing and returns
    a well-formed result. The detector's correctness is pinned on synthetic
    fixtures above, where a regression cannot be masked by whatever the operator
    happened to write today.
    """
    mod = _load_hygiene()
    mem = mod.get_data_root() / "auto-memory"
    if not (mem / "MEMORY.md").exists():
        pytest.skip("private data overlay not present (bare engine clone)")
    res = mod.scan_live_state_rows(mem)
    assert set(res) >= {"ok", "flagged", "note"}
    assert isinstance(res["flagged"], list)
    for row in res["flagged"]:
        assert {"target", "line", "signals"} <= set(row), row


def test_real_memory_index_keeps_managed_marker_on_the_header_line():
    """`## Active Threads` must be followed IMMEDIATELY by the managed-by marker.

    /dream skips "any level-2 section whose body begins (immediately after the
    header) with an HTML comment `<!-- managed-by: ... -->`", and that is the
    exact shape threads_lib.ensure_active_threads_section() writes. When the
    block was retired on 2026-08-20 the replacement prose was inserted ABOVE the
    marker, pushing it 10 lines down, which takes the section out of /dream's
    managed set and lets a consolidation pass re-order it. Prose belongs under
    the marker, not in front of it.
    """
    mod = _load_hygiene()
    memory_md = mod.get_data_root() / "auto-memory" / "MEMORY.md"
    if not memory_md.exists():
        pytest.skip("private data overlay not present (bare engine clone)")
    lines = memory_md.read_text(encoding="utf-8").splitlines()
    if "## Active Threads" not in lines:
        pytest.skip("no ## Active Threads section (no thread ever opened)")
    idx = lines.index("## Active Threads")
    assert lines[idx + 1].startswith("<!-- managed-by: /thread"), (
        "managed-by marker is not on the line after '## Active Threads'; "
        f"found {lines[idx + 1]!r}"
    )


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
    from datetime import datetime, timezone

    path = mod.write_report("# report\n", datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc))
    report_dir = out_root / "operations" / "memory-hygiene"
    files = list(report_dir.iterdir())
    assert files == [path]
    assert path.name == "2026-06-26_memory-hygiene_report.md"
    assert path.read_text(encoding="utf-8") == "# report\n"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
