"""A log that can be truncated, duplicated, or read from the wrong tree.

Shard `scripts-07-p3` of the 2026-08-23/24 engine audit. Its input matched the
working tree exactly, so every finding here was live.

The trajectory log is the audit record of an `/implement` run: what ran, in what
order, touching which files. Four of these findings let that record be WRONG
rather than absent -- a truncated line, two runs sharing one id, two step_starts
that never overlapped in the log's own opinion, a plan read from `/tmp`. A
record with a hole in it is worse than a missing record, because it is still
believed.

Findings covered (numbering from `/tmp/audit_out3/scripts-07-p3.md`):

   1  `heading run` executed files outside the workspace
   2  a UNC input produced an unloadable file:// URL
   3  the explicit -ing list flagged mid-sentence use as an ERROR
   4  the title-case check promised an exemption it never implemented
   5  the burstiness docstring and the code disagreed
   6  an unreadable input exited 1, the code for "findings present"
   7  a short write left a truncated JSONL record and returned normally
   8  the Windows unlock targeted the wrong byte range and hid the failure
   9  two runs in one second shared a run_id
  10  the sequencing guard and the append were not one operation
  11  invalid UTF-8 crashed the verifier
  12  a valid-JSON non-object record crashed the verifier
  13  a relative plan path resolved against the caller's cwd
  14  a duplicate wave_start silently replaced the first
  15  `?` and `[ab]` passed the literal-path check, and so did ""
  16  any common suffix counted as the same file
  17  a fixed temp name destroyed a pre-existing destination file
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import data_root_is_demo  # noqa: E402


def _code_only(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_comment_stripper_keeps_the_code(tmp_path):
    f = tmp_path / "s.py"
    f.write_text("# PIPE_BUF\nx = 1\n", encoding="utf-8")
    out = _code_only(f)
    assert "x = 1" in out
    assert "PIPE_BUF" not in out


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


itl = _load("implement_trajectory_log_p7c", "scripts/implement-trajectory-log.py")
ilr = _load("import_legacy_records_p7c", "scripts/import-legacy-records.py")
hcli = _load("heading_cli_p7c", "scripts/heading_cli.py")


@pytest.fixture
def traj(tmp_path, monkeypatch):
    """Redirect trajectory storage into tmp_path and hand back the directory."""
    monkeypatch.setattr(itl, "trajectory_path", lambda rid: tmp_path / f"{rid}.jsonl")
    return tmp_path


@pytest.fixture
def frozen_second(monkeypatch):
    """Stop the clock inside `mint_run_id`, which reads it at second resolution.

    A test whose premise is "these calls happen in the same second" cannot
    inherit that from the machine. Under a 16-worker run on 2026-08-27 a
    sibling test in `test_an_import_that_died_on_the_skip_it_promised.py`
    crossed a second boundary and failed on code that was right.
    """
    from datetime import datetime, timezone

    frozen = datetime(2026, 8, 24, 3, 4, 5, tzinfo=timezone.utc)

    class _Clock:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(itl, "datetime", _Clock)


def _ev(event_type: str, sn=0, **payload) -> str:
    return json.dumps({"timestamp": "2026-08-24T00:00:00Z", "event_type": event_type,
                       "step_number": sn, "payload": payload})


def _write(traj_dir: Path, run_id: str, *lines: str) -> None:
    (traj_dir / f"{run_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# 1 - the CLI stays inside the workspace
# ============================================================

def _heading(*argv):
    return subprocess.run(
        [sys.executable, "scripts/heading_cli.py", *argv],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )


def test_an_absolute_target_is_refused(tmp_path):
    """`root / "/tmp/x"` IS `/tmp/x` under pathlib, so an absolute target
    replaced the workspace root entirely."""
    outside = tmp_path / "outside.py"
    outside.write_text('print("escaped")\n', encoding="utf-8")
    proc = _heading("run", str(outside))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "absolute paths are not accepted" in proc.stderr
    assert "escaped" not in proc.stdout


def test_a_dot_dot_target_is_refused():
    proc = _heading("run", "../../outside.py")
    assert proc.returncode == 2
    assert "outside the workspace root" in proc.stderr


def test_a_real_in_tree_script_still_dispatches():
    """Containment must not have made every target unreachable."""
    proc = _heading("run", "check-build.py")
    assert "outside the workspace root" not in proc.stderr
    assert "absolute paths are not accepted" not in proc.stderr


def test_resolve_returns_a_path_under_the_root():
    resolved = hcli._resolve("check-build.py", ROOT)
    assert resolved.is_relative_to(ROOT)


# ============================================================
# 2 - the file URL is built by the library
# ============================================================

def test_the_file_url_comes_from_as_uri():
    """A hand-built `file:///` + backslash-replace turned a UNC path into
    `file://///server/share/x.html` -- empty host, four spare slashes."""
    body = _code_only(ROOT / "scripts" / "html-to-pdf.py")
    assert "as_uri()" in body
    assert 'file:///' not in body


def test_as_uri_handles_a_unc_path():
    from pathlib import PureWindowsPath
    assert PureWindowsPath("//server/share/page.html").as_uri() == \
        "file://server/share/page.html"


# ============================================================
# 7, 8 - the append writes the whole record or raises
# ============================================================

def test_a_short_write_is_completed_not_truncated(tmp_path, monkeypatch):
    """`os.write` may write fewer bytes than asked and say so. The return value
    was discarded, so a short write left a truncated JSONL line and
    `append_event` returned normally."""
    log = tmp_path / "t.jsonl"
    real_write = os.write

    def short_write(fd, buf):
        return real_write(fd, buf[:5])

    monkeypatch.setattr(itl.os, "write", short_write)
    itl.append_event(log, {"event_type": "test", "payload": "x" * 200})
    monkeypatch.setattr(itl.os, "write", real_write)

    line = log.read_text(encoding="utf-8").strip()
    assert json.loads(line)["payload"] == "x" * 200


def test_a_write_that_makes_no_progress_raises(tmp_path, monkeypatch):
    """Completing a short write must not become an infinite loop."""
    log = tmp_path / "t.jsonl"
    monkeypatch.setattr(itl.os, "write", lambda fd, buf: 0)
    with pytest.raises(OSError, match="short write"):
        itl.append_event(log, {"event_type": "stuck"})


def test_the_posix_docstring_no_longer_credits_pipe_buf():
    """PIPE_BUF is a guarantee about pipes and FIFOs, not regular files, and
    nothing bounded a record to it anyway."""
    body = (ROOT / "scripts" / "implement-trajectory-log.py").read_text(encoding="utf-8")
    fn = body.split("def _append_jsonl_posix", 1)[1].split("\ndef ", 1)[0]
    doc = fn.split('"""')[1]
    assert "PIPE_BUF ensures" not in doc
    assert "not about regular files" in doc


def test_the_windows_unlock_seeks_back_before_unlocking():
    """`msvcrt.locking` works from the CURRENT position, which the write had
    advanced -- so the unlock targeted the range AFTER the new bytes."""
    body = _code_only(ROOT / "scripts" / "implement-trajectory-log.py")
    fn = body.split("def _append_jsonl_windows", 1)[1].split("\ndef ", 1)[0]
    assert "locked_at = f.tell()" in fn
    assert "f.seek(locked_at)" in fn
    assert "pass  # best-effort unlock" not in fn


# ============================================================
# 9 - a run_id no other run holds
# ============================================================

def test_three_runs_in_one_second_get_three_ids(traj, frozen_second):
    """One-second resolution is not unique: the second run died on
    FileExistsError, and two concurrent ones both passed `exists()`.

    `frozen_second` is what makes the name true. Without it the three mints are
    only in the same second when the machine happens to be fast enough, and on a
    slow one they get three DIFFERENT bases - so the assertion below still
    passes while the collision path it exists for never runs. A test that is
    green whether or not its premise held measures nothing.
    """
    base = itl.mint_run_id("plans/2026-08-24-demo.md")
    ids = [itl.mint_unique_run_id("plans/2026-08-24-demo.md") for _ in range(3)]
    assert all(i.startswith(base) for i in ids), (
        f"the three mints did not share one second: {ids} against {base}"
    )
    assert len(set(ids)) == 3, ids
    for run_id in ids:
        assert (traj / f"{run_id}.jsonl").exists(), "the id was not reserved on disk"


def test_the_first_id_keeps_its_readable_shape(traj):
    """A suffix is added only on a real collision."""
    run_id = itl.mint_unique_run_id("plans/2026-08-24-demo.md")
    assert run_id.endswith("_demo")


def test_write_run_start_accepts_the_empty_file_its_own_minting_left(traj):
    """The reservation creates the file empty; a plain `exists()` check would
    make write_run_start refuse the very run it just reserved."""
    run_id = itl.mint_unique_run_id("plans/2026-08-24-demo.md")
    itl.write_run_start(run_id, "plans/2026-08-24-demo.md")
    events = [json.loads(x) for x in
              (traj / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [e["event_type"] for e in events] == ["run_start"]


def test_write_run_start_still_refuses_a_real_existing_trajectory(traj):
    _write(traj, "taken", _ev("run_start"))
    with pytest.raises(FileExistsError):
        itl.write_run_start("taken", "plans/x.md")


def test_the_verifier_rejects_two_run_starts(traj):
    """The only check was that the FIRST event is a run_start, which stays true
    when a second one is appended."""
    _write(traj, "dup", _ev("run_start"), _ev("run_start"), _ev("run_end"))
    assert any("run_start events in one trajectory" in d
               for d in itl.verify_trajectory("dup"))


# ============================================================
# 10 - the guard and the append are one operation
# ============================================================

def test_the_guard_and_the_append_share_one_lock():
    """Two concurrent step_start calls could both read "no open step" and both
    append -- a non-parallel interleaving the VERIFIER also accepts, because it
    deliberately does not assert step-bracket non-overlap."""
    body = _code_only(ROOT / "scripts" / "implement-trajectory-log.py")
    fn = body.split("def cmd_event", 1)[1].split("\ndef ", 1)[0]
    lock_at = fn.index("with file_lock(")
    guard_at = fn.index("_open_state(_read_events(path))")
    append_at = fn.index("append_event(path, record)")
    assert lock_at < guard_at < append_at, "the guard or the append is outside the lock"


# ============================================================
# 11, 12 - the verifier reports instead of crashing
# ============================================================

def test_invalid_utf8_becomes_a_defect(traj):
    (traj / "badutf8.jsonl").write_bytes(b'{"event_type": "run_start"}\n\xff\n')
    defects = itl.verify_trajectory("badutf8")
    assert defects and "could not be read" in defects[0]


def test_a_valid_json_non_object_becomes_a_defect(traj):
    """`[]`, `"x"` and `null` all decode, and every `.get()` then raises."""
    _write(traj, "nondict", _ev("run_start"), "[]", _ev("run_end"))
    assert any("not an event object" in d for d in itl.verify_trajectory("nondict"))


def test_a_clean_trajectory_is_still_clean(traj):
    """The three guards must not have made every trajectory defective."""
    _write(traj, "clean", _ev("run_start"), _ev("validation_check"), _ev("run_end"))
    assert itl.verify_trajectory("clean") == []


# ============================================================
# 13 - the plan is not read from the caller's cwd
# ============================================================

def test_a_relative_plan_is_not_resolved_against_the_cwd(tmp_path, monkeypatch):
    """Verifying from `/tmp` read `/tmp/plans/foo.md` -- an unrelated or planted
    file -- and computed the plan-derived advisories from it."""
    decoy_root = tmp_path / "decoy"
    (decoy_root / "plans").mkdir(parents=True)
    (decoy_root / "plans" / "foo.md").write_text("DECOY", encoding="utf-8")

    real_plans = tmp_path / "realplans"
    real_plans.mkdir()
    (real_plans / "foo.md").write_text("REAL", encoding="utf-8")

    monkeypatch.setattr(itl, "get_plans_dir", lambda: real_plans)
    monkeypatch.chdir(decoy_root)

    found = itl.resolve_plan_path("plans/foo.md")
    assert found is not None
    assert found.read_text(encoding="utf-8") == "REAL", "the cwd decoy won"


def test_an_absolute_plan_path_is_still_honoured(tmp_path, monkeypatch):
    plan = tmp_path / "abs.md"
    plan.write_text("ABS", encoding="utf-8")
    monkeypatch.setattr(itl, "get_plans_dir", lambda: tmp_path / "nope")
    assert itl.resolve_plan_path(str(plan)) == plan


# ============================================================
# 14 - a wave cannot be opened twice unnoticed
# ============================================================

def test_a_duplicate_wave_start_is_reported(traj):
    """A dict keyed by wave number let the second start overwrite the first, so
    one wave_end reconciled the second and the first vanished."""
    _write(traj, "dupwave",
           _ev("run_start"),
           _ev("wave_start", wave=1, step_count=1, parallel=True),
           _ev("wave_start", wave=1, step_count=1, parallel=True),
           _ev("wave_end", wave=1, successes=0),
           _ev("validation_check"),
           _ev("run_end"))
    defects = itl.verify_trajectory("dupwave")
    assert any("opens a wave already opened" in d for d in defects)
    assert any("never closed by a wave_end" in d for d in defects)


def test_a_properly_paired_wave_is_still_clean(traj):
    _write(traj, "okwave",
           _ev("run_start"),
           _ev("wave_start", wave=1, step_count=0, parallel=True),
           _ev("wave_end", wave=1, successes=0),
           _ev("validation_check"),
           _ev("run_end"))
    assert itl.verify_trajectory("okwave") == []


# ============================================================
# 15 - a literal path is literal
# ============================================================

@pytest.mark.parametrize("entry", [
    "scripts/foo?.py",       # single-character wildcard
    "scripts/[ab].py",       # character class
    "scripts/*.py",          # star
    "scripts/{a,b}.py",      # this project's own brace shorthand
    "",                      # empty
    "   ",                   # blank
])
def test_a_non_literal_files_affected_entry_is_flagged(traj, entry):
    _write(traj, "glob",
           _ev("run_start"),
           _ev("step_start", sn=1),
           _ev("step_end", sn=1, status="ok", files_affected=[entry]),
           _ev("run_end"))
    assert any("not a literal path" in d for d in itl.verify_trajectory("glob")), entry


def test_a_genuine_path_is_not_flagged(traj):
    """The widened check must not have started rejecting real paths."""
    _write(traj, "lit",
           _ev("run_start"),
           _ev("step_start", sn=1),
           _ev("step_end", sn=1, status="ok", files_affected=["scripts/real_file.py"]),
           _ev("validation_check"),
           _ev("run_end"))
    assert not any("not a literal path" in d for d in itl.verify_trajectory("lit"))


# ============================================================
# 16 - the same file, not merely the same ending
# ============================================================

@pytest.mark.parametrize("recorded,planned,same", [
    ("scripts/a.py", "scripts/a.py", True),
    ("engine/scripts/alpha.py", "scripts/alpha.py", True),
    (".heading-os-data/reference/x.md", "reference/x.md", True),
    ("./scripts/a.py", "scripts/a.py", True),
    # The defect: a different repo-relative path with the same ending.
    ("other/scripts/reports/a.py", "scripts/reports/a.py", False),
    ("scripts/scrutinize_record.py", "record.py", False),
])
def test_covers_requires_a_known_prefix_not_any_suffix(recorded, planned, same):
    if recorded.startswith(".heading-os-data/") and data_root_is_demo():
        pytest.skip(
            "no private data overlay on this clone, so get_data_root() resolves to "
            "the in-tree examples root and '.heading-os-data' is not one of the "
            "prefixes _tree_prefixes() names. Not measured here: that an overlay-"
            "spelled recording reconciles against its engine-relative plan entry. "
            "The other five cases, including both defect cases, still run."
        )
    assert itl._covers(recorded, planned) is same


def test_the_prefix_set_is_named_and_finite():
    if data_root_is_demo():
        pytest.skip(
            "no private data overlay on this clone. Both names this asserts on come "
            "from directories that exist only on an operator machine: the overlay "
            "sibling '.heading-os-data' and the engine clone '.heading-os'. In demo "
            "mode the set carries the examples root instead. Not measured here: that "
            "the prefix set names the engine tree and the overlay tree."
        )
    prefixes = itl._tree_prefixes()
    assert "engine" in prefixes
    assert any(p.endswith(".heading-os-data") for p in prefixes)
    assert any(p.endswith(".heading-os") for p in prefixes)


# ============================================================
# 17 - the importer destroys nothing
# ============================================================

def test_a_pre_existing_scratch_named_file_survives_an_import(tmp_path):
    """The fixed `<dest>.tmp-import` name meant `copy2` overwrote a file
    already at that name and `os.replace` then moved it away -- against this
    importer's one documented invariant, that an existing destination file is
    never overwritten."""
    src = tmp_path / "src.md"
    src.write_text("SOURCE", encoding="utf-8")
    dest = tmp_path / "out" / "foo"
    dest.parent.mkdir()
    decoy = dest.parent / "foo.tmp-import"
    decoy.write_text("PRE-EXISTING DATA", encoding="utf-8")

    ilr._atomic_copy(src, dest)

    assert decoy.read_text(encoding="utf-8") == "PRE-EXISTING DATA"
    assert dest.read_text(encoding="utf-8") == "SOURCE"


def test_the_import_leaves_no_scratch_file_behind(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("SOURCE", encoding="utf-8")
    dest = tmp_path / "out" / "foo"
    ilr._atomic_copy(src, dest)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["foo"]


def test_a_destination_that_appears_mid_import_is_not_overwritten(tmp_path):
    """`os.link` is a create-if-absent the filesystem enforces, closing the gap
    between the caller's existence check and this write."""
    src = tmp_path / "src.md"
    src.write_text("SOURCE", encoding="utf-8")
    dest = tmp_path / "out" / "foo"
    dest.parent.mkdir()
    dest.write_text("ALREADY HERE", encoding="utf-8")

    with pytest.raises(FileExistsError):
        ilr._atomic_copy(src, dest)
    assert dest.read_text(encoding="utf-8") == "ALREADY HERE"


def test_the_importer_docstring_no_longer_promises_os_replace():
    header = (ROOT / "scripts" / "import-legacy-records.py").read_text(
        encoding="utf-8").split('"""', 2)[1]
    assert "then os.replace" not in header
    assert "os.link" in header


# ============================================================
# 20 - uniqueness lives at the call site, not only in the minter
# ============================================================
def test_two_starts_of_one_plan_in_the_same_second_get_two_trajectories(
        tmp_path, monkeypatch):
    """Every test above calls `mint_unique_run_id` directly, so swapping the
    CALL SITE in `cmd_new` back to the one-second `mint_run_id` left them all
    green. That is the whole defect: two runs of the same plan inside one second
    minted one id, and either the second died on FileExistsError or, run
    concurrently, both appended a `run_start` into a single trajectory.
    """
    monkeypatch.setattr(itl, "trajectory_path", lambda rid: tmp_path / f"{rid}.jsonl")
    monkeypatch.setattr(itl, "mint_run_id", lambda plan: "2026-08-24_030405_demo")

    args = SimpleNamespace(plan="plans/2026-08-24-demo.md")
    assert itl.cmd_new(args) == 0
    assert itl.cmd_new(args) == 0

    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 2, [p.name for p in files]
    for path in files:
        records = [json.loads(ln) for ln in
                   path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        starts = [r for r in records if r.get("event_type") == "run_start"]
        assert len(starts) == 1, f"{path.name} holds {len(starts)} run_start events"
