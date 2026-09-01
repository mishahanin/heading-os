"""`next-signal` lost the whole "what just happened" signal to one reference CSV.

`gather()` aggregates four sources: the handoff pointer, the newest files under
outputs/, the last git subjects, and the open business threads. Exactly ONE of
them uses `reference/skill-graph.csv`, and only to enrich each recent output
with the skill that produced it. `gather` loaded that catalog on its first line,
outside any handler.

MEASURED 2026-09-01 by running `scripts/next-signal.py` three times against one
tmp data root, changing only the catalog:

    catalog present      rc=0
    catalog ABSENT       rc=1, stdout EMPTY,
                         stderr `next-signal: [Errno 2] No such file or directory`
    catalog UNDECODABLE  rc=1, stdout EMPTY, raw UnicodeDecodeError traceback

The absent case is not hypothetical: it is the bare-clone state that
`tests/test_a_show_command_that_printed_five_of_six_columns.py` already carries
a `pytest.skip` for, and `scripts/skill_graph.py`'s own `main` has handled it
since it was written - `error: skill-graph catalog not found`, return 2. The
second consumer of the same file had fallen behind the first. The undecodable
case is the decode class: `UnicodeDecodeError` is a SIBLING of
`json.JSONDecodeError` under `ValueError` and is not an `OSError`, so `main`'s
`except OSError` never saw it.

The fix is `load_graph_rows()`, which degrades to an empty catalog and NAMES
what it lost. Naming it is half the fix: a signal that silently drops its skill
attribution reads exactly like a signal whose outputs happen to have no
producing skill.

Nothing here touches the live tree. HEADING_OS_DATA is pinned at tmp_path for
the in-process tests AND for the child process, and the catalog is edited only
in a scratch copy of `reference/`.

Run: python3 -m pytest tests/test_a_catalog_that_took_three_other_signals_down_with_it.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "next-signal.py"
REAL_CATALOG = ROOT / "reference" / "skill-graph.csv"


def _load():
    spec = importlib.util.spec_from_file_location("next_signal_degrade", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["next_signal_degrade"] = mod
    spec.loader.exec_module(mod)
    return mod


NS = _load()

# One byte that is not valid UTF-8, spliced into an otherwise valid CSV. Not a
# hand-written stub: the real catalog's bytes are used, so the only thing wrong
# with the document is the byte under test.
UNDECODABLE = b"\xff"


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """A DATA root with real content in three of the four sources.

    Content matters. Over an empty overlay every source renders empty, so a run
    that lost all four would be indistinguishable from a run that lost none -
    the exact confusion this file measures.
    """
    root = tmp_path / "data"
    outs = root / "outputs" / "intel"
    outs.mkdir(parents=True)
    (outs / "2026-09-01_osint_spectre.md").write_text("dossier\n", encoding="utf-8")
    handoff = root / "outputs" / "operations" / "handoff-archive" / ".latest"
    handoff.mkdir(parents=True)
    (handoff / "summary.md").write_text(
        "Source: session\n\n## Objective\nFinish the Skyfall review\n",
        encoding="utf-8")
    threads = root / "threads" / "business"
    threads.mkdir(parents=True)
    (threads / "skyfall-review.md").write_text("open\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(root))
    return root


@pytest.fixture()
def catalog_at(tmp_path, monkeypatch):
    """Redirect `skill_graph.default_file()` into a scratch reference/ tree.

    The real `reference/skill-graph.csv` is READ once to seed the copy and is
    never written, moved or deleted.
    """
    ref = tmp_path / "engine" / "reference"
    ref.mkdir(parents=True)
    path = ref / "skill-graph.csv"
    shutil.copy(REAL_CATALOG, path)
    monkeypatch.setattr(NS.skill_graph, "default_file", lambda: path)
    return path


def test_the_real_catalog_is_present_and_non_trivial():
    """The floor. Every case below is a modification OF this file; if it were
    absent or empty the 'present' control would prove nothing."""
    assert REAL_CATALOG.is_file(), f"the shipped catalog is missing at {REAL_CATALOG}"
    rows = NS.skill_graph.load(REAL_CATALOG)
    assert len(rows) >= 20, f"the catalog holds {len(rows)} rows; too few to enrich anything"


# ---------------------------------------------------------------------------
# The control: with the catalog intact, all four sources report AND the
# outputs carry their skill attribution.
# ---------------------------------------------------------------------------

def test_with_the_catalog_present_nothing_is_degraded(data_root, catalog_at):
    sig = NS.gather(ROOT, 8)

    assert sig["degraded"] == []
    assert sig["handoff"]["objective"] == "Finish the Skyfall review"
    assert [t["slug"] for t in sig["active_threads"]] == ["skyfall-review"]
    assert len(sig["recent_outputs"]) >= 1
    assert any(o["skills"] for o in sig["recent_outputs"]), (
        "no recent output was attributed to a skill, so the attribution this "
        "file is about is not actually exercised by the fixture")


# ---------------------------------------------------------------------------
# The two failures, each measured on the three sources that must SURVIVE
# ---------------------------------------------------------------------------

def _break_catalog(path: Path, how: str) -> None:
    if how == "absent":
        path.unlink()
    elif how == "undecodable":
        raw = path.read_bytes()
        assert len(raw) > 60
        path.write_bytes(raw[:60] + UNDECODABLE + raw[60:])
    elif how == "directory":
        path.unlink()
        path.mkdir()
    else:                                       # pragma: no cover - typo guard
        raise AssertionError(how)


BREAKAGES = ["absent", "undecodable", "directory"]


@pytest.mark.parametrize("how", BREAKAGES)
def test_the_other_three_signals_survive_a_broken_catalog(data_root, catalog_at, how):
    """The whole point: one enrichment source must not take the other three."""
    _break_catalog(catalog_at, how)

    sig = NS.gather(ROOT, 8)

    assert sig["handoff"]["objective"] == "Finish the Skyfall review", (
        f"{how}: the handoff was lost to the skill catalog")
    assert [t["slug"] for t in sig["active_threads"]] == ["skyfall-review"], (
        f"{how}: the open threads were lost to the skill catalog")
    assert len(sig["recent_outputs"]) >= 1, (
        f"{how}: the recent outputs were lost to the skill catalog")


@pytest.mark.parametrize("how", BREAKAGES)
def test_a_broken_catalog_is_named_and_not_dropped_in_silence(data_root, catalog_at, how):
    """A degraded run that looks complete is the failure, not the degradation."""
    _break_catalog(catalog_at, how)

    sig = NS.gather(ROOT, 8)

    assert sig["degraded"], f"{how}: the loss was silent"
    why = " ".join(sig["degraded"])
    assert str(catalog_at) in why, (
        f"{how}: the reason does not name the file that was dropped: {why!r}")


@pytest.mark.parametrize("how", BREAKAGES)
def test_the_attribution_is_the_only_thing_lost(data_root, catalog_at, how):
    """The bound on the other side: outputs are still listed, just unattributed.

    Without this, `recent_outputs` returning `[]` would satisfy the survival
    test above by way of the `>= 1` on a different key, and the degradation
    would be wider than it is claimed to be.
    """
    intact = NS.gather(ROOT, 8)
    _break_catalog(catalog_at, how)
    broken = NS.gather(ROOT, 8)

    assert [o["path"] for o in broken["recent_outputs"]] == \
           [o["path"] for o in intact["recent_outputs"]], \
        f"{how}: the file list itself changed, not merely its attribution"
    assert all(o["skills"] == [] for o in broken["recent_outputs"])


@pytest.mark.parametrize("how", BREAKAGES)
def test_the_degradation_is_rendered_in_both_output_modes(data_root, catalog_at, how):
    """Reported, not merely recorded. Text is what /next reads."""
    _break_catalog(catalog_at, how)
    sig = NS.gather(ROOT, 8)

    text = NS.render_text(sig)
    assert "degraded" in text, f"{how}: the text mode says nothing: {text!r}"
    assert "Skyfall" in text, f"{how}: the surviving signal is not rendered either"

    assert json.loads(json.dumps(sig))["degraded"] == sig["degraded"]


def test_an_intact_run_says_nothing_about_degradation(data_root, catalog_at):
    """The over-report anchor. A renderer that always printed the banner would
    pass every case above while making the signal unreadable."""
    text = NS.render_text(NS.gather(ROOT, 8))
    assert "degraded" not in text, text


# ---------------------------------------------------------------------------
# End to end, through the real CLI, because `main`'s handler is where the
# absent case was converted into a total loss.
# ---------------------------------------------------------------------------

def _run_cli(data: Path, engine_ref: Path) -> subprocess.CompletedProcess:
    """Drive the script as a child process, with the DATA root pinned there too.

    A child does NOT inherit a monkeypatched `default_file`, so the scratch
    catalog is placed by copying `reference/` into a scratch ENGINE tree and
    running the script from it. The real repository is never written.
    """
    env = dict(os.environ, HEADING_OS_DATA=str(data))
    env.pop("HEADING_OS_TZ", None)
    return subprocess.run([sys.executable, str(engine_ref / "scripts" / "next-signal.py")],
                          cwd=str(engine_ref), capture_output=True, text=True,
                          errors="replace", env=env)


@pytest.fixture()
def engine_copy(tmp_path):
    """A scratch ENGINE tree: the real scripts/ plus a writable reference/."""
    eng = tmp_path / "engine_cli"
    (eng / "reference").mkdir(parents=True)
    shutil.copytree(ROOT / "scripts", eng / "scripts")
    shutil.copy(REAL_CATALOG, eng / "reference" / "skill-graph.csv")
    return eng


@pytest.mark.parametrize("how", BREAKAGES)
def test_the_cli_still_exits_zero_and_prints_the_signal(data_root, engine_copy, how):
    """Before the fix: rc=1 and an EMPTY stdout for `absent`, and an
    uncaught traceback for `undecodable`."""
    intact = _run_cli(data_root, engine_copy)
    assert intact.returncode == 0, intact.stderr
    assert "Skyfall" in intact.stdout

    _break_catalog(engine_copy / "reference" / "skill-graph.csv", how)
    broken = _run_cli(data_root, engine_copy)

    assert broken.returncode == 0, (
        f"{how}: rc={broken.returncode}, stderr={broken.stderr[-500:]}")
    assert "Skyfall" in broken.stdout, (
        f"{how}: stdout lost the surviving signal: {broken.stdout!r}")
    assert "degraded" in broken.stdout, (
        f"{how}: the loss was not reported to the operator: {broken.stdout!r}")
    assert "Traceback" not in broken.stderr, broken.stderr


def test_the_cli_still_fails_loudly_when_outputs_is_unreadable(tmp_path, engine_copy):
    """The refusal that must NOT be softened by the fix.

    The module docstring promises a non-zero exit with a plain message when
    outputs/ is unreadable, and that is a real source rather than an enrichment.
    Without this case the change above could be read as "swallow everything".
    """
    bare = tmp_path / "bare-data"
    bare.mkdir()
    r = _run_cli(bare, engine_copy)

    assert r.returncode == 1, r.stdout
    assert "next-signal:" in r.stderr
    assert "Traceback" not in r.stderr
