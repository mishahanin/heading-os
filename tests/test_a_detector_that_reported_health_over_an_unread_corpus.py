"""`scripts/memory-hygiene.py` printed a green all-clear over a corpus it never opened.

MEASURED 2026-09-02, with `pathlib.Path.read_text` and `pathlib.Path.open`
counted per run:

  - operator overlay: 267 distinct files opened under the memory directory,
    "1 objective defect(s) across 1 category", exit 1.
  - `HEADING_OS_DATA` pointed at a directory with no `auto-memory/`: 0 files
    opened, "0 objective defect(s) across 0 categories" in GREEN, exit 0.
  - `HEADING_OS_DATA` pointed at an EMPTY `auto-memory/`: 0 files opened, the
    same green line, exit 0.

A bare public clone lands in the second case by construction: `get_data_root()`
falls through to `<workspace_root>/examples`, which ships no `auto-memory/` at
all. So the weekly Monday timer could report a healthy memory store on a machine
whose memory store had moved, and `docs/memory-lifecycle.md` describes this
script as the thing that "exits non-zero when any defect is present".

The blindness was never a path bug: `get_data_root() / "auto-memory"` resolves
off `__file__` and is correct from any working directory. It was the arithmetic
in `main()`, which summed three empty finding lists to `gate_count == 0` and
never asked whether those lists were empty because the corpus was clean or
because it was never read. `compute_memory_defects()` returns `status:
"missing"` for exactly that state and `gather()` dropped the key.

What is pinned here: a healthy corpus passes, a planted defect fails, an EMPTY
corpus REFUSES (exit 2) instead of passing, and a MISSING overlay is told apart
from an empty one (exit 0 with an explicit "NOTHING was checked").

Every fixture is invented. Nothing in this file reads or writes the operator's
private overlay; the whole suite runs inside `tmp_path`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_hygiene():
    spec = importlib.util.spec_from_file_location(
        "memory_hygiene_unread_corpus_mod", ROOT / "scripts" / "memory-hygiene.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures. Invented content only: this repo is public.
# ---------------------------------------------------------------------------

def _healthy_corpus(memory_dir: Path) -> None:
    """Two fact files, both pointed at from the index, index well under budget."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "the-kettle-boils-at-ninety-eight.md").write_text(
        "The kettle in the sample fixture boils at 98 degrees.\n", encoding="utf-8")
    (memory_dir / "the-blue-folder-is-the-second-drawer.md").write_text(
        "The blue folder lives in the second drawer.\n", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text(
        "# Memory index\n\n"
        "- Kitchen: [kettle](the-kettle-boils-at-ninety-eight.md)\n"
        "- Filing: [blue folder](the-blue-folder-is-the-second-drawer.md)\n",
        encoding="utf-8",
    )


def _corpus_with_planted_orphan(memory_dir: Path) -> None:
    """As above plus one fact file the index carries no pointer to."""
    _healthy_corpus(memory_dir)
    (memory_dir / "nobody-points-at-this-one.md").write_text(
        "A fact with no hook in the index.\n", encoding="utf-8")


def _empty_corpus(memory_dir: Path) -> None:
    """The directory exists and holds no memory at all."""
    memory_dir.mkdir(parents=True, exist_ok=True)


def _index_only_corpus(memory_dir: Path) -> None:
    """An index and zero fact files. An index is not a corpus."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("# Memory index\n\n", encoding="utf-8")


def _offline(mod, monkeypatch, *, overlay: bool, brain_errors=None):
    """Pin the three things that would otherwise leave this machine.

    The brain compile shells out to `odin-brain-health.py`; the redundancy scan
    builds the default embedder and speaks HTTP to the pinned ollama host; and
    `data_overlay_present()` reads the real filesystem. All three are supplied
    here so every verdict below is a verdict about the fixture in `tmp_path`.

    `brain_errors=None` means the brain is unavailable, which is the normal
    state of a clone with no Odin content.
    """
    monkeypatch.setattr(mod, "data_overlay_present", lambda: overlay)
    monkeypatch.setattr(mod, "collect_brain_compile", lambda: {
        "ok": brain_errors is not None,
        "data": ({"temporal_validity": {"errors": brain_errors, "warnings": []}}
                 if brain_errors is not None else None),
        "note": "" if brain_errors is not None else "brain unavailable (test)",
    })
    real_scan = mod.scan_redundancy

    def offline_scan(memory_dir, **kw):
        def orthogonal(texts):
            return [[1.0 if i == j else 0.0 for j in range(len(texts))]
                    for i in range(len(texts))]
        return real_scan(memory_dir, **{**kw, "embedder": orthogonal})

    monkeypatch.setattr(mod, "scan_redundancy", offline_scan)


def _run(mod, data_root: Path, monkeypatch, *, overlay: bool, brain_errors=None,
         argv=("--no-report",)) -> int:
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    monkeypatch.setattr(mod, "get_outputs_dir", lambda: data_root / "outputs")
    monkeypatch.setattr(mod, "load_env", lambda *a, **k: None)
    _offline(mod, monkeypatch, overlay=overlay, brain_errors=brain_errors)
    monkeypatch.setattr("sys.argv", ["memory-hygiene.py", *argv])
    return mod.main()


# ---------------------------------------------------------------------------
# The four states the exit code must tell apart
# ---------------------------------------------------------------------------

def test_a_healthy_corpus_passes(tmp_path, monkeypatch, capsys):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=True) == 0
    out = capsys.readouterr().out
    assert "0 objective defect(s)" in out
    # The count of files READ is on the pass line. Without it a pass over two
    # files and a pass over none are the same sentence, which is the defect.
    assert "over 2 memory file(s)" in out


def test_a_planted_orphan_fails(tmp_path, monkeypatch, capsys):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _corpus_with_planted_orphan(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=True) == 1
    out = capsys.readouterr().out
    assert "1 objective defect(s)" in out
    assert "orphan memory file(s)" in out


def test_an_empty_corpus_refuses_instead_of_passing(tmp_path, monkeypatch, capsys):
    """The regression. Pre-fix this exited 0 with a green "0 defects" line."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _empty_corpus(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=True) == 2
    cap = capsys.readouterr()
    assert "REFUSES" in cap.err
    # Both numbers named, per the refusal contract.
    assert "read 0 memory file(s)" in cap.err
    assert "of 3 gate categories" in cap.err
    assert "0 objective defect(s)" not in cap.out


def test_an_index_with_no_facts_under_it_refuses(tmp_path, monkeypatch, capsys):
    """`compute_memory_defects` counts MEMORY.md in `file_count`, so a lone
    index reads as one file. It is still an empty corpus."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _index_only_corpus(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=True) == 2
    assert "read 0 memory file(s)" in capsys.readouterr().err


def test_an_absent_corpus_directory_refuses_when_an_overlay_is_present(
        tmp_path, monkeypatch, capsys):
    """A moved or wiped `auto-memory/` under a live overlay is a setup defect."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    data_root.mkdir()

    assert _run(mod, data_root, monkeypatch, overlay=True) == 2
    err = capsys.readouterr().err
    assert "REFUSES" in err
    assert "evaluated 0 of 3 gate categories" in err


def test_zero_categories_evaluated_refuses_even_with_files_on_disk(
        tmp_path, monkeypatch, capsys):
    """The second half of the floor, driven at its own seam.

    `fact_files == 0` and `n_eval == 0` are redundant TODAY: `orphans` is
    counted as evaluated whenever the memory directory exists, so any run that
    found fact files has evaluated at least one category. Mutating the floor
    down to `fact_files == 0` alone therefore survived every behavioural case
    (MEASURED 2026-09-02: 14 passed under that mutation).

    The clause stays, because "evaluated zero categories" is the contract this
    detector owes its reader and `GATE_CATEGORIES` is a list that will grow; a
    floor that only holds while one category happens to be unconditional is a
    floor waiting to be removed by someone who reads the redundancy and not the
    reason. Driving `coverage()` directly is the honest way to hold it: the
    state is unreachable through the filesystem, so a fixture cannot reach it
    either, and pretending otherwise would be a straw-man case.
    """
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")
    monkeypatch.setattr(mod, "coverage", lambda *a, **k: {
        "memory_dir": str(data_root / "auto-memory"),
        "overlay_present": True,
        "corpus_status": "ok",
        "fact_files": 2,
        "categories_total": 3,
        "categories_evaluated": [],
        "categories_not_evaluated": list(mod.GATE_CATEGORIES),
    })

    assert _run(mod, data_root, monkeypatch, overlay=True) == 2
    err = capsys.readouterr().err
    assert "REFUSES" in err
    assert "read 2 memory file(s)" in err
    assert "evaluated 0 of 3 gate categories" in err


def test_a_missing_overlay_is_told_apart_from_an_empty_one(
        tmp_path, monkeypatch, capsys):
    """A public clone: nothing is wrong, and nothing was checked. Say both."""
    mod = _load_hygiene()
    data_root = tmp_path / "examples"
    data_root.mkdir()

    assert _run(mod, data_root, monkeypatch, overlay=False) == 0
    cap = capsys.readouterr()
    assert "SKIP" in cap.err
    assert "no private data overlay" in cap.err
    assert "NOTHING was checked" in cap.err
    # It must NOT read as a clean scan.
    assert "0 objective defect(s)" not in cap.out
    assert "REFUS" not in cap.err


def test_a_present_overlay_with_a_real_corpus_is_never_skipped(
        tmp_path, monkeypatch, capsys):
    """The skip branch is gated on there being nothing to read, not on the
    overlay flag alone. A legacy in-tree workspace reads as "no overlay" and
    must still be scanned."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _corpus_with_planted_orphan(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=False) == 1
    assert "SKIP" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Coverage is reported, not inferred
# ---------------------------------------------------------------------------

def test_coverage_counts_categories_evaluated_not_categories_with_findings(
        tmp_path, monkeypatch):
    """The old summary counted non-empty finding lists, so a clean corpus and an
    unread one both printed "across 0 categories"."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _offline(mod, monkeypatch, overlay=True, brain_errors=[])

    result = mod.gather()
    cov = result["coverage"]
    assert result["gate_count"] == 0            # clean: no findings at all
    assert cov["fact_files"] == 2
    assert cov["categories_evaluated"] == ["orphans", "over_budget",
                                           "temporal_errors"]
    assert cov["categories_not_evaluated"] == []


def test_an_unavailable_brain_drops_its_category_from_the_evaluated_set(
        tmp_path, monkeypatch):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _offline(mod, monkeypatch, overlay=True, brain_errors=None)

    cov = mod.gather()["coverage"]
    assert cov["categories_not_evaluated"] == ["temporal_errors"]
    assert cov["fact_files"] == 2


def test_a_missing_index_drops_the_budget_category_and_orphans_everything(
        tmp_path, monkeypatch, capsys):
    """No MEMORY.md means "0/200 lines, not over budget" is a verdict over
    nothing, so that category is not claimed as evaluated. The orphan category
    still is: every fact file is unreferenced, and that IS the finding."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    mem = data_root / "auto-memory"
    _healthy_corpus(mem)
    (mem / "MEMORY.md").unlink()

    assert _run(mod, data_root, monkeypatch, overlay=True) == 1
    out = capsys.readouterr().out
    assert "2 objective defect(s) over 2 memory file(s)" in out
    assert "1/3 gate categories evaluated" in out
    assert "not evaluated this run: over_budget, temporal_errors" in out


def test_the_report_header_carries_the_coverage_line(tmp_path, monkeypatch):
    """The dated report outlives the terminal, so the coverage travels with it."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _offline(mod, monkeypatch, overlay=True, brain_errors=[])

    text = mod.render_report(mod.gather(), "2026-09-02T00:00:00+04:00")
    assert "**Coverage:** 2 memory file(s) read, 3/3 gate categories evaluated" in text


def test_a_refusal_writes_no_report(tmp_path, monkeypatch):
    """A report headed "0 objective defects" over an unread corpus is the
    artifact this refusal exists to not produce, and it outlives the run."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _empty_corpus(data_root / "auto-memory")

    # `argv` without --no-report: the report path is live and must stay unused.
    assert _run(mod, data_root, monkeypatch, overlay=True, argv=()) == 2
    assert not (data_root / "outputs").exists()


# ---------------------------------------------------------------------------
# Working directory
# ---------------------------------------------------------------------------

def test_the_corpus_resolves_the_same_from_any_working_directory(
        tmp_path, monkeypatch):
    """`get_data_root()` walks from `__file__`, never the CWD. Pinned because
    "a path resolved against the CWD" is the first thing this shape is blamed
    on, and here it is not the cause."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")
    monkeypatch.setattr(mod, "get_data_root", lambda: data_root)
    _offline(mod, monkeypatch, overlay=True, brain_errors=[])

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert mod.gather()["coverage"]["fact_files"] == 2

    monkeypatch.chdir(ROOT)
    assert mod.gather()["coverage"]["fact_files"] == 2


# ---------------------------------------------------------------------------
# The operator's live overlay: read-only, never asserted against
# ---------------------------------------------------------------------------

def test_the_live_run_reads_more_than_zero_files_when_an_overlay_exists():
    """On a machine that HAS the overlay, the detector must open memory.

    Deliberately not an assertion about what it finds: the corpus is the
    operator's and changes daily. What is held is the one thing the green line
    could not distinguish. Skips on a bare clone, which is the state this whole
    file exists to keep distinguishable.
    """
    mod = _load_hygiene()
    mem = mod.get_data_root() / "auto-memory"
    if not mem.is_dir():
        pytest.skip("private data overlay not present (bare engine clone)")
    facts = [p for p in mem.glob("*.md") if p.name != "MEMORY.md"]
    if not facts:
        pytest.skip("overlay present but auto-memory carries no fact files")
    cov = mod.coverage(mem, {"status": "ok", "index_readable": True}, brain_ok=False)
    assert cov["fact_files"] == len(facts)
    assert "orphans" in cov["categories_evaluated"]


# ---------------------------------------------------------------------------
# A report that could not be produced is a script error, not a dirty corpus
# ---------------------------------------------------------------------------
#
# `gather()` was guarded and the two lines after it were not, so `render_report`
# raising on an unexpected shape, or `write_report` meeting a full or read-only
# disk, left as a traceback and exited 1. This file's contract reserves 1 for
# "objective defect(s) present", so an infrastructure failure and a dirty memory
# store printed the same exit code at the cron that reads it.

def test_a_report_that_cannot_be_written_exits_two_not_one(tmp_path, monkeypatch, capsys):
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")

    def refuse(*_a, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod, "write_report", refuse)

    assert _run(mod, data_root, monkeypatch, overlay=True, argv=()) == 2
    cap = capsys.readouterr()
    assert "could not produce its report" in cap.err, cap.err
    assert "No space left on device" in cap.err, cap.err
    # The scan finished, so its result is still delivered. Losing the count as
    # well would make an unwritable disk look like an unread corpus.
    assert "0 objective defect(s)" in cap.out, cap.out


def test_a_report_that_cannot_be_rendered_exits_two_not_one(tmp_path, monkeypatch, capsys):
    """The other half of the same two lines. `render_report` is the one that
    raises on a shape change, and it sat outside the guard too."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _healthy_corpus(data_root / "auto-memory")

    def refuse(*_a, **_kw):
        raise KeyError("stale")

    monkeypatch.setattr(mod, "render_report", refuse)

    assert _run(mod, data_root, monkeypatch, overlay=True, argv=()) == 2
    assert "could not produce its report" in capsys.readouterr().err


def test_a_defective_corpus_with_a_working_report_still_exits_one(tmp_path, monkeypatch, capsys):
    """Without this, a main() that returned 2 unconditionally would satisfy the
    two tests above and lose the gate entirely."""
    mod = _load_hygiene()
    data_root = tmp_path / "data"
    _corpus_with_planted_orphan(data_root / "auto-memory")

    assert _run(mod, data_root, monkeypatch, overlay=True, argv=()) == 1
    assert "1 objective defect(s)" in capsys.readouterr().out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
