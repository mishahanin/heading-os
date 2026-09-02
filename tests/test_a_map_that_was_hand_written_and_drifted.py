"""The datastore map was written by hand in April and never regenerated.

`.claude/rules/datastore.md` carried a bullet list of the datastore's subtrees,
authored 2026-04-20. MEASURED 2026-09-02: it named fourteen subtrees and omitted
three whole top-level directories, roughly 150 files. A hand-maintained
inventory of a tree that grows every week falls behind silently, and a stale map
is worse than no map because it is read with confidence.

Worse, that rule file resolves `engine` and the engine repository is public. So
the obvious repair, regenerating the list in place, would have published the
real directory names on its first run. `scripts/datastore-map.py` therefore
generates into the PRIVATE overlay and the public rule keeps only the policy.

## The bug this file exists to prevent recurring

The generator's FIRST version called `get_reference_dir()`. On the operator's
own workspace that returns the ENGINE root, because `reference/` is engine
content that ships in the public clone. Running it wrote a file naming every
real datastore directory straight into the public repository. It was caught only
because the file was still untracked, which is luck rather than a control:
`scripts/leak-guard.py` grades tracked paths and the push scan runs on tracked
content, so neither would have seen it.

Two things came out of that, and both are tested below: the resolver is
`get_corporate_root()`, and `refuse_if_inside_engine()` asks about the WRITE
rather than about the environment, so it holds however the path was derived.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "datastore-map.py"
sys.path.insert(0, str(ROOT))


def _run(overlay: Path, *args: str, readonly: bool = False):
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(overlay)
    if readonly:
        env["HEADING_OS_DATA_READONLY"] = "1"
    else:
        env.pop("HEADING_OS_DATA_READONLY", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    """A scratch data overlay holding a small, known datastore.

    Deliberately built OUTSIDE the engine checkout. A scratch root placed inside
    it trips `refuse_if_inside_engine`, which is the guard doing its job and not
    a fixture bug.
    """
    ds = tmp_path / "datastore"
    (ds / "alpha").mkdir(parents=True)
    (ds / "beta").mkdir()
    (ds / "alpha" / "note.md").write_text("readable\n", encoding="utf-8")
    (ds / "beta" / "report.pdf").write_bytes(b"%PDF-1.4 not a real pdf")
    (ds / "beta" / "report-extract.md").write_text("companion\n", encoding="utf-8")
    (ds / "beta" / "orphan.pdf").write_bytes(b"%PDF-1.4 no companion")
    return tmp_path


def _map_file(overlay: Path) -> Path:
    return overlay / "reference" / "datastore-map.md"


# ------------------------------------------------------------------
# The write goes to the private overlay, and nowhere near the engine
# ------------------------------------------------------------------

def test_the_map_lands_in_the_overlay_and_not_in_the_engine(overlay):
    proc = _run(overlay)
    assert proc.returncode == 0, proc.stderr
    assert _map_file(overlay).is_file(), (
        f"no map written.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert not (ROOT / "reference" / "datastore-map.md").exists(), (
        "the map was written into the public engine checkout, which is the "
        "exact failure this script was rewritten to prevent"
    )


def test_a_target_inside_the_engine_is_refused(tmp_path):
    """The belt beside the resolver's brace.

    Imported and called directly, because the condition cannot be reached
    through the CLI once `map_path()` is correct, and a guard with no negative
    case is not a guard.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("dsmap", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with pytest.raises(SystemExit) as caught:
        mod.refuse_if_inside_engine(ROOT / "reference" / "datastore-map.md")
    assert "REFUSING" in str(caught.value)

    # The control: an outside path must pass, or the refusal above proves
    # nothing except that the function raises unconditionally.
    mod.refuse_if_inside_engine(tmp_path / "elsewhere" / "datastore-map.md")


def test_the_target_is_resolved_at_call_time(tmp_path, monkeypatch):
    """A frozen module constant would answer once, at import.

    That is how `scripts/datastore-extract.py` once wrote the operator's real
    overlay from inside a test that had repointed the data root. The docstring
    at its `datastore_dir()` records it; this asserts the lesson stuck.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("dsmap2", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Both roots must EXIST. The data-root seam refuses a `HEADING_OS_DATA`
    # pointing at a missing directory rather than falling back, because the
    # fallback on an operator machine is the live private overlay.
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    monkeypatch.setenv("HEADING_OS_DATA", str(first))
    a = mod.map_path()
    monkeypatch.setenv("HEADING_OS_DATA", str(second))
    b = mod.map_path()

    assert a != b, (
        f"map_path() returned {a} both times; the data root was frozen at "
        "import and a caller that repoints it is ignored"
    )


# ------------------------------------------------------------------
# Refusals. A checker that cannot fail is a report, not a gate.
# ------------------------------------------------------------------

def test_a_missing_datastore_refuses(tmp_path):
    proc = _run(tmp_path)
    assert proc.returncode == 1
    assert "no datastore" in proc.stderr


def test_an_empty_datastore_refuses(tmp_path):
    """Zero files walked is what a wrong data root looks like from outside.

    Every count then prints a clean-looking zero, which reads as a healthy
    empty tree rather than as a misconfiguration.
    """
    (tmp_path / "datastore").mkdir()
    proc = _run(tmp_path)
    assert proc.returncode == 1
    assert "empty corpus" in proc.stderr


def test_check_without_a_map_refuses(overlay):
    proc = _run(overlay, "--check")
    assert proc.returncode == 1
    assert "no map" in proc.stderr


# ------------------------------------------------------------------
# Staleness, in both directions
# ------------------------------------------------------------------

def test_check_passes_immediately_after_a_write(overlay):
    assert _run(overlay).returncode == 0
    proc = _run(overlay, "--check")
    assert proc.returncode == 0, (
        f"a map written seconds ago reported stale.\nstderr:\n{proc.stderr}"
    )


def test_check_fails_once_the_tree_changes(overlay):
    assert _run(overlay).returncode == 0
    (overlay / "datastore" / "alpha" / "second.md").write_text("new\n",
                                                              encoding="utf-8")
    proc = _run(overlay, "--check")
    assert proc.returncode == 1
    assert "STALE" in proc.stderr


def test_the_timestamp_alone_does_not_make_the_map_stale(overlay):
    """`--check` compares content, never the clock.

    Including the generation stamp in the comparison would report drift on
    every run, and a checker that always fires is a checker nobody reads.
    """
    assert _run(overlay).returncode == 0
    target = _map_file(overlay)
    text = target.read_text(encoding="utf-8")
    assert "Generated " in text, "the map lost its generation stamp"
    target.write_text(
        text.replace(
            [ln for ln in text.splitlines() if ln.startswith("Generated ")][0],
            "Generated 1999-01-01T00:00:00+00:00 by `scripts/datastore-map.py`.",
        ),
        encoding="utf-8",
    )
    assert _run(overlay, "--check").returncode == 0, (
        "changing only the timestamp reported the map as stale"
    )


# ------------------------------------------------------------------
# The read-only mirror
# ------------------------------------------------------------------

def test_a_readonly_mirror_is_not_written(overlay):
    """One tracked write wedges every later `git pull --ff-only`.

    That happened on the Steward VM on 2026-08-30: five CRM cards were rewritten
    as a side effect of a send, the mirror sat five commits behind for three and
    a half days, and systemd reported SUCCESS the whole time.
    """
    proc = _run(overlay, readonly=True)
    assert proc.returncode == 0, (
        "a read-only host should skip cleanly, not fail; a failing unit there "
        "is noise that trains the operator to ignore it"
    )
    assert not _map_file(overlay).exists(), (
        "the map was written on a host that only mirrors the data repository"
    )
    assert "READONLY" in proc.stderr


def test_a_readonly_mirror_can_still_check(overlay):
    """Reading is not writing, so the gate sits between them."""
    assert _run(overlay).returncode == 0
    assert _run(overlay, "--check", readonly=True).returncode == 0


# ------------------------------------------------------------------
# What the map actually measures
# ------------------------------------------------------------------

def test_a_binary_without_a_companion_is_counted_as_unreachable(overlay):
    """The number the operator asked for: what can I actually read.

    The fixture holds one PDF WITH an `-extract.md` companion and one without.
    A count that ignored companions would report both the same way, and the
    whole point of the column is that it does not.
    """
    proc = _run(overlay, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)

    assert data["opaque_files"] == 2, data
    assert data["opaque_unreachable"] == 1, (
        f"expected exactly one binary with no companion, got "
        f"{data['opaque_unreachable']}: {data}"
    )
    assert data["total_files"] == 4, data


def test_every_subtree_carries_its_routing_destination(overlay):
    proc = _run(overlay, "--json")
    data = json.loads(proc.stdout)
    for name, entry in data["subtrees"].items():
        assert entry["routing"], f"subtree {name} has no routing destination"


def test_stdout_mode_writes_no_file(overlay):
    """A dry run must be a dry run."""
    proc = _run(overlay, "--stdout")
    assert proc.returncode == 0
    assert "DataStore map" in proc.stdout
    assert not _map_file(overlay).exists()
