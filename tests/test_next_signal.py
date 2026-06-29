"""Tests for scripts/next-signal.py — the "what just happened" reader.

The spine under test: the outputs scan excludes the noise dirs (_sync/_tmp/browser/clipboard/
handoff-archive) and dotfiles, returns newest-first, and maps each file to its producing skill;
the handoff summary parses into objective + next_steps; the threads source reads business only;
and a missing outputs/ degrades to a clean non-zero exit rather than a crash. The module is
loaded by path because its filename is kebab-case (not importable as scripts.next_signal).
"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load the kebab-case CLI module by path.
_spec = importlib.util.spec_from_file_location("next_signal", ROOT / "scripts" / "next-signal.py")
next_signal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(next_signal)


@pytest.fixture
def graph_rows():
    return [
        {"skill": "osint", "phase": "intel", "preceded_by": "", "followed_by": "deal-strategy",
         "produces_in": "outputs/intel", "consumes_from": ""},
        {"skill": "dashboard", "phase": "operations", "preceded_by": "", "followed_by": "",
         "produces_in": "outputs/operations/dashboard", "consumes_from": ""},
    ]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A minimal DATA tree with outputs/, threads/business/, and a handoff summary.

    The data sources resolve under the data root via get_outputs_dir() /
    get_threads_dir() (data-root seam), so point HEADING_OS_DATA at the temp tree
    rather than passing a root argument."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    (tmp_path / "outputs" / "intel").mkdir(parents=True)
    (tmp_path / "outputs" / "_sync").mkdir(parents=True)
    (tmp_path / "outputs" / "browser").mkdir(parents=True)
    (tmp_path / "threads" / "business").mkdir(parents=True)
    latest = tmp_path / "outputs" / "operations" / "handoff-archive" / ".latest"
    latest.mkdir(parents=True)

    # Real signal files, with staggered mtimes (older -> newer).
    older = tmp_path / "outputs" / "intel" / "2026-06-01_osint_old.md"
    newer = tmp_path / "outputs" / "intel" / "2026-06-04_osint_new.md"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    base = time.time()
    import os
    os.utime(older, (base - 1000, base - 1000))
    os.utime(newer, (base, base))

    # Noise that must be excluded.
    (tmp_path / "outputs" / "_sync" / "cal.md").write_text("x", encoding="utf-8")
    (tmp_path / "outputs" / "browser" / "page.html").write_text("x", encoding="utf-8")
    (tmp_path / "outputs" / "intel" / "_latest-fetch.json").write_text("{}", encoding="utf-8")
    (latest / "summary.md").write_text(
        "# Latest handoff summary\n\nSource: outputs/operations/handoff-archive/x.md\n"
        "Generated: 2026-06-04T18:00:00+00:00\n\n## Objective\n\nShip Wave 2.\n\n"
        "## Next steps\n\n1. Do the thing.\n2. Then the other thing.\n",
        encoding="utf-8",
    )
    (tmp_path / "threads" / "business" / "2026-06-03-deal-x.md").write_text("t", encoding="utf-8")
    return tmp_path


# --- outputs scan --------------------------------------------------------

def test_recent_outputs_newest_first(ws, graph_rows):
    outs = next_signal.recent_outputs(10, graph_rows)
    paths = [o["path"] for o in outs]
    assert paths[0].endswith("2026-06-04_osint_new.md")


def test_recent_outputs_excludes_noise(ws, graph_rows):
    outs = next_signal.recent_outputs(50, graph_rows)
    joined = " ".join(o["path"] for o in outs)
    assert "_sync" not in joined
    assert "browser" not in joined
    assert "_latest-fetch.json" not in joined


def test_recent_outputs_maps_producing_skill(ws, graph_rows):
    outs = next_signal.recent_outputs(10, graph_rows)
    intel = [o for o in outs if "intel" in o["path"]][0]
    assert "osint" in intel["skills"]


def test_recent_outputs_missing_dir_raises(tmp_path, graph_rows, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        next_signal.recent_outputs(10, graph_rows)


# --- handoff parse -------------------------------------------------------

def test_handoff_parses_objective_and_steps(ws):
    h = next_signal.read_handoff()
    assert h["objective"] == "Ship Wave 2."
    assert h["next_steps"] == ["Do the thing.", "Then the other thing."]


def test_handoff_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    assert next_signal.read_handoff() is None


# --- threads (business only) --------------------------------------------

def test_active_threads_business_only(ws):
    th = next_signal.active_threads(5)
    assert th and th[0]["slug"] == "2026-06-03-deal-x"


def test_active_threads_no_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    assert next_signal.active_threads(5) == []


# --- CLI degrade ---------------------------------------------------------

def test_main_missing_outputs_exits_1(tmp_path, capsys, monkeypatch):
    # Data sources resolve under HEADING_OS_DATA; an empty data tree has no
    # outputs/, so recent_outputs() raises and main() degrades to exit 1.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    code = next_signal.main(["--root", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 1
    assert "outputs/ not found" in err


# --- next_steps ordinal strip (regression: charset lstrip mangled content) ---

def test_handoff_nextsteps_strips_only_real_ordinal(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    latest = tmp_path / "outputs" / "operations" / "handoff-archive" / ".latest"
    latest.mkdir(parents=True)
    (latest / "summary.md").write_text(
        "# H\n\nSource: a/b.md\n\n## Next steps\n\n"
        "1. 2026-Q3 forecast due\n"
        "2. 3D print the bracket\n"
        "- .env rotation\n",
        encoding="utf-8",
    )
    h = next_signal.read_handoff()
    assert h["next_steps"] == ["2026-Q3 forecast due", "3D print the bracket", ".env rotation"]


def test_handoff_body_source_line_does_not_hijack_header(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    latest = tmp_path / "outputs" / "operations" / "handoff-archive" / ".latest"
    latest.mkdir(parents=True)
    (latest / "summary.md").write_text(
        "# H\n\nSource: real/path.md\n\n## Objective\n\n"
        "Source: this is body prose, not the header\n",
        encoding="utf-8",
    )
    h = next_signal.read_handoff()
    assert h["source"] == "real/path.md"
    assert h["objective"] == "Source: this is body prose, not the header"


# --- recent_commits + render_text resilience ---

def test_recent_commits_non_git_returns_empty(tmp_path):
    assert next_signal.recent_commits(tmp_path, 8) == []


def test_render_text_no_crash_on_empty():
    assert next_signal.render_text({}) == ""


def test_render_text_handles_none_handoff():
    sig = {"handoff": None, "recent_outputs": [], "recent_commits": [], "active_threads": []}
    assert next_signal.render_text(sig) == ""
