#!/usr/bin/env python3
"""Shard 08-p4: the abort that called itself a skip, and four smaller promises.

Two tools, five findings, one shape between them: each file's own docstring
described behaviour the code did not perform.

`scripts/import-legacy-records.py` promises that a destination file which
already exists is "counted as skipped and reported". Two ways in, it was not.
A dangling symlink at the destination is a directory entry whose name is taken,
but `Path.exists()` follows the link and answers False, so the name passed the
collision check and `os.link` refused it with EEXIST -- every time, no race
needed. And the check-then-link race the copy exists to catch raised the same
`FileExistsError` through a caller with no `try`. Either way the import died
with a traceback and every remaining file and subtree went unprocessed.

`scripts/implement-trajectory-log.py` carries an "Exit codes" section that
promises 3 for a filesystem error, but minting ran outside `cmd_new`'s `try`, so
a read-only outputs tree escaped as a traceback with interpreter exit 1. Its
`--verify` docstring listed the deviation-ordering advisory among the
plan-derived checks and called it silent without a plan; the check reads the
JSONL only and fires unconditionally. Its git files reconciliation compared raw
strings while the plan reconciliation ten lines away normalised both sides, so a
path recorded as `engine/README.md` was flagged as unrecorded. And the
sequencing guard formatted its rejection with `sorted()` over a set of untyped
step numbers, raising TypeError in the one branch that exists to reject cleanly.
"""
import errno
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _load(stem: str, name: str):
    """Load a kebab-case CLI script by path (not importable as a name)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


itl = _load("implement-trajectory-log", "implement_trajectory_log")


# ============================================================
# Finding 1 -- the importer's abort that called itself a skip
# ============================================================
@pytest.fixture
def importer(tmp_path, monkeypatch):
    """A fresh importer module bound to a tmp data root."""
    d = tmp_path / ".heading-os-data"
    d.mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    monkeypatch.delenv("THREADS_ROOT", raising=False)
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    return _load("import-legacy-records", "import_legacy_records")


@pytest.fixture
def old_root(tmp_path):
    """A legacy knowledge subtree with three files, in sorted walk order."""
    old = tmp_path / "old-workspace"
    (old / "knowledge").mkdir(parents=True)
    for name in ("a.md", "b.md", "c.md"):
        (old / "knowledge" / name).write_text(name + "\n", encoding="utf-8")
    return old


def _run(mod, argv):
    old_argv = sys.argv
    sys.argv = ["import-legacy-records.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv


def _dest():
    from scripts.utils.workspace import get_knowledge_dir
    d = get_knowledge_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_a_dangling_symlink_is_a_name_that_exists_even_though_exists_says_no(importer):
    """The premise of the defect, pinned so the rest is not arguing with itself."""
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    assert (dest / "b.md").exists() is False
    assert (dest / "b.md").is_symlink() is True
    import os
    assert os.path.lexists(dest / "b.md") is True


def test_the_importer_docstring_states_the_collision_rule_it_now_keeps(importer):
    """The contract the fix restored. It was true of the docstring and false of
    the code for as long as the docstring existed."""
    doc = " ".join((importer.__doc__ or "").split())
    assert '"Already exists" means the NAME is taken' in doc
    assert "a dangling symlink counts" in doc
    assert doc.index("dangling symlink counts") < doc.index("Both were aborts")


def test_a_dangling_symlink_at_the_destination_does_not_abort_the_import(importer, old_root):
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    rc = _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert rc == 0


def test_the_files_after_the_collision_are_still_imported(importer, old_root, capsys):
    """`c.md` sorts after `b.md`, so the abort took it with it."""
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert (dest / "a.md").read_text(encoding="utf-8") == "a.md\n"
    assert (dest / "c.md").read_text(encoding="utf-8") == "c.md\n"


def test_a_dangling_symlink_is_counted_as_skipped_not_as_imported(importer, old_root, capsys):
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    out = _plain(capsys.readouterr().out)
    assert "imported 2" in out
    assert "skipped 1 (already exist)" in out


def test_the_dangling_symlink_itself_is_left_alone(importer, old_root):
    """Never overwritten means the entry is still the link it was."""
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert (dest / "b.md").is_symlink()
    assert Path(dest / "b.md").readlink().name == "never-created.md"


def test_the_import_leaves_no_scratch_file_behind_after_a_collision(importer, old_root):
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    leftovers = [p.name for p in dest.iterdir() if ".tmp-import" in p.name]
    assert leftovers == []


def test_the_check_then_link_race_is_a_skip_not_an_abort(importer, old_root, monkeypatch, capsys):
    """The genuine race: the name is free at the check and taken at the link."""
    real_copy = importer._atomic_copy
    calls = []

    def flaky(src_file, dest_file):
        calls.append(dest_file.name)
        if dest_file.name == "b.md":
            raise FileExistsError(f"{dest_file} appeared after the existence check")
        return real_copy(src_file, dest_file)

    monkeypatch.setattr(importer, "_atomic_copy", flaky)
    rc = _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert rc == 0
    assert calls == ["a.md", "b.md", "c.md"], "the walk stopped at the collision"
    out = _plain(capsys.readouterr().out)
    assert "imported 2" in out
    assert "skipped 1 (already exist)" in out


def test_an_error_that_is_not_a_collision_still_propagates(importer, old_root, monkeypatch):
    """The catch is narrow. A full disk is not a skip, and must not read as one."""
    def full_disk(src_file, dest_file):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(importer, "_atomic_copy", full_disk)
    with pytest.raises(OSError) as exc:
        _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert exc.value.errno == errno.ENOSPC


def test_an_ordinary_existing_file_still_skips(importer, old_root, capsys):
    """Regression: the plain collision path the importer always handled."""
    dest = _dest()
    (dest / "b.md").write_text("KEEP-ME\n", encoding="utf-8")
    rc = _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert rc == 0
    assert (dest / "b.md").read_text(encoding="utf-8") == "KEEP-ME\n"
    assert "skipped 1 (already exist)" in _plain(capsys.readouterr().out)


def test_dry_run_over_a_dangling_symlink_reports_the_skip_and_writes_nothing(importer, old_root, capsys):
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    rc = _run(importer, ["--from", str(old_root), "--only", "knowledge", "--dry-run"])
    assert rc == 0
    out = _plain(capsys.readouterr().out)
    assert "would import 2" in out
    assert "skipped 1 (already exist)" in out
    assert not (dest / "a.md").exists()


def test_a_second_run_over_the_symlink_stays_idempotent(importer, old_root, capsys):
    dest = _dest()
    (dest / "b.md").symlink_to(dest / "never-created.md")
    _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    capsys.readouterr()
    rc = _run(importer, ["--from", str(old_root), "--only", "knowledge"])
    assert rc == 0
    assert "imported 0" in _plain(capsys.readouterr().out)


# ============================================================
# Finding 2 -- the documented exit code 3 that minting skipped
# ============================================================
@pytest.fixture
def traj_dir(tmp_path, monkeypatch):
    d = tmp_path / "impl"
    d.mkdir()
    monkeypatch.setattr(itl, "TRAJECTORY_DIR", d)
    return d


def test_an_unwritable_outputs_tree_returns_the_documented_exit_code(tmp_path, monkeypatch, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(itl, "TRAJECTORY_DIR", locked / "implement")
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    try:
        rc = itl.main(["--new", "--plan", str(plan)])
    finally:
        locked.chmod(0o700)
    assert rc == 3, "the module's Exit codes section promises 3 for a filesystem error"


def test_the_filesystem_error_is_reported_on_stderr_not_stdout(tmp_path, monkeypatch, capsys):
    """`/implement` captures stdout as the run_id. An error there becomes an id."""
    locked = tmp_path / "locked2"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(itl, "TRAJECTORY_DIR", locked / "implement")
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    try:
        itl.main(["--new", "--plan", str(plan)])
    finally:
        locked.chmod(0o700)
    cap = capsys.readouterr()
    assert cap.out.strip() == ""
    assert "ERROR" in _plain(cap.err)


def test_running_out_of_collision_attempts_returns_three(traj_dir, tmp_path, monkeypatch, capsys):
    """Every suffix collides, so minting exhausts its attempts and gives up."""
    class _Fixed:
        hex = "abcdef"

    monkeypatch.setattr(itl.uuid, "uuid4", lambda: _Fixed())
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    assert itl.main(["--new", "--plan", str(plan)]) == 0     # takes the bare id
    capsys.readouterr()
    assert itl.main(["--new", "--plan", str(plan)]) == 0     # takes the -abcdef id
    capsys.readouterr()
    rc = itl.main(["--new", "--plan", str(plan)])            # nothing left to take
    assert rc == 3
    assert "ERROR" in _plain(capsys.readouterr().err)


def test_minting_still_succeeds_on_the_ordinary_path(traj_dir, tmp_path, capsys):
    """Regression: the fix moved a call, it did not change what a good run does."""
    plan = tmp_path / "2026-08-25-some-plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    rc = itl.main(["--new", "--plan", str(plan)])
    cap = capsys.readouterr()
    assert rc == 0
    assert cap.out.strip().endswith("_some-plan")
    assert itl.trajectory_path(cap.out.strip()).exists()


# ============================================================
# Finding 3 -- the advisory that never reads a plan
# ============================================================
def _doc(obj) -> str:
    return " ".join((obj.__doc__ or "").split())


def test_the_module_docstring_no_longer_calls_the_ordering_check_plan_derived():
    doc = _doc(itl)
    assert "two plan-derived advisories" in doc
    assert "three plan-derived advisories" not in doc


def test_the_module_docstring_says_the_ordering_check_ignores_the_plan():
    doc = _doc(itl)
    assert "computed purely from the JSONL" in doc
    assert doc.index("computed purely from the JSONL") < doc.index("Until 2026-08-25")


def test_the_verify_docstring_names_the_ordering_advisory_among_its_own():
    doc = _doc(itl.verify_trajectory)
    assert "four advisory checks" in doc
    assert "before that step's step_start" in doc


def _events_with_missing_plan(*, deviation_first=True):
    """A trajectory naming a plan that cannot be located, and one deviation."""
    base = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "event_type": "run_start",
         "step_number": 0,
         "payload": {"git_head": "unknown", "plan_path": "plans/no-such-plan-exists.md"}},
    ]
    dev = {"timestamp": "2026-01-01T00:00:01+00:00", "event_type": "deviation",
           "step_number": 2, "payload": {"reason": "r"}}
    start = {"timestamp": "2026-01-01T00:00:02+00:00", "event_type": "step_start",
             "step_number": 2, "payload": {"step": 2}}
    end = {"timestamp": "2026-01-01T00:00:03+00:00", "event_type": "step_end",
           "step_number": 2, "payload": {"step": 2, "files_affected": [], "status": "ok"}}
    tail = [
        {"timestamp": "2026-01-01T00:00:04+00:00", "event_type": "validation_check",
         "step_number": None, "payload": {"check": "suite", "passed": True}},
        {"timestamp": "2026-01-01T00:00:05+00:00", "event_type": "run_end",
         "step_number": None, "payload": {}},
    ]
    middle = [dev, start, end] if deviation_first else [start, dev, end]
    return base + middle + tail


def _write(run_id, events):
    p = itl.trajectory_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_the_ordering_advisory_fires_even_when_the_plan_cannot_be_located(traj_dir):
    """The behaviour the docstring denied."""
    _write("ord1", _events_with_missing_plan(deviation_first=True))
    defects = itl.verify_trajectory("ord1")
    assert any("precedes that step's step_start" in d for d in defects)


def test_the_genuinely_plan_derived_advisories_stay_silent_in_that_same_run(traj_dir):
    _write("ord2", _events_with_missing_plan(deviation_first=True))
    defects = itl.verify_trajectory("ord2")
    assert not any("Files affected" in d for d in defects)
    assert not any("Implementation Notes" in d for d in defects)


def test_a_deviation_after_its_step_start_is_not_flagged(traj_dir):
    _write("ord3", _events_with_missing_plan(deviation_first=False))
    defects = itl.verify_trajectory("ord3")
    assert not any("precedes" in d for d in defects)


# ============================================================
# Finding 4 -- two reconciliations, one function, two rules
# ============================================================
def _recon_events(recorded_paths, git_head="abc123"):
    return [
        {"timestamp": "2026-01-01T00:00:00+00:00", "event_type": "run_start",
         "step_number": 0,
         "payload": {"git_head": git_head, "plan_path": "plans/no-such-plan-exists.md"}},
        {"timestamp": "2026-01-01T00:00:01+00:00", "event_type": "step_start",
         "step_number": 1, "payload": {"step": 1}},
        {"timestamp": "2026-01-01T00:00:02+00:00", "event_type": "step_end",
         "step_number": 1,
         "payload": {"step": 1, "files_affected": list(recorded_paths), "status": "ok"}},
        {"timestamp": "2026-01-01T00:00:03+00:00", "event_type": "validation_check",
         "step_number": None, "payload": {"check": "suite", "passed": True}},
        {"timestamp": "2026-01-01T00:00:04+00:00", "event_type": "run_end",
         "step_number": None, "payload": {}},
    ]


def _recon(run_id, recorded, changed, monkeypatch):
    monkeypatch.setattr(itl, "_git_changed_files", lambda head: set(changed))
    _write(run_id, _recon_events(recorded))
    return [d for d in itl.verify_trajectory(run_id) if "was modified in this run" in d]


def test_an_engine_prefixed_recorded_path_covers_the_repo_relative_one(traj_dir, monkeypatch):
    assert _recon("r1", ["engine/README.md"], {"README.md"}, monkeypatch) == []


def test_an_absolute_recorded_path_covers_the_repo_relative_one(traj_dir, monkeypatch):
    abs_path = str(itl.WORKSPACE_ROOT / "scripts" / "foo.py")
    assert _recon("r2", [abs_path], {"scripts/foo.py"}, monkeypatch) == []


def test_a_dot_slash_recorded_path_covers_the_repo_relative_one(traj_dir, monkeypatch):
    assert _recon("r3", ["./scripts/foo.py"], {"scripts/foo.py"}, monkeypatch) == []


def test_a_root_directory_name_prefix_covers_the_repo_relative_one(traj_dir, monkeypatch):
    prefixed = f"{itl.WORKSPACE_ROOT.name}/scripts/foo.py"
    assert _recon("r4", [prefixed], {"scripts/foo.py"}, monkeypatch) == []


def test_a_genuinely_unrecorded_file_is_still_flagged(traj_dir, monkeypatch):
    hits = _recon("r5", ["engine/README.md"], {"README.md", "scripts/other.py"}, monkeypatch)
    assert len(hits) == 1
    assert "scripts/other.py" in hits[0]


def test_a_path_that_merely_shares_a_suffix_is_still_flagged(traj_dir, monkeypatch):
    """The normalisation strips ONE named prefix; it is not suffix matching."""
    hits = _recon("r6", ["vendor/scripts/foo.py"], {"scripts/foo.py"}, monkeypatch)
    assert len(hits) == 1
    assert "scripts/foo.py" in hits[0]


def test_the_flagged_paths_stay_in_sorted_order(traj_dir, monkeypatch):
    hits = _recon("r7", [], {"z.py", "a.py", "m.py"}, monkeypatch)
    assert [h.split()[1] for h in hits] == ["a.py", "m.py", "z.py"]


def test_an_unknown_git_head_still_skips_the_reconciliation_entirely(traj_dir, monkeypatch):
    monkeypatch.setattr(itl, "_git_changed_files",
                        lambda head: pytest.fail("git must not be consulted"))
    _write("r8", _recon_events([], git_head="unknown"))
    assert not any("was modified in this run" in d for d in itl.verify_trajectory("r8"))


# ============================================================
# Finding 5 -- the clean rejection that crashed
# ============================================================
def _seed(run_id):
    itl.write_run_start(run_id, "plans/no-such-plan-exists.md")


def test_a_string_step_beside_an_int_step_rejects_instead_of_crashing(traj_dir, capsys):
    _seed("s1")
    itl.main(["--event", "--run-id", "s1", "--type", "step_start",
              "--data-json", '{"step": "prep"}'])
    itl.main(["--event", "--run-id", "s1", "--type", "wave_start",
              "--data-json", '{"wave": 1, "parallel": true, "step_count": 2}'])
    itl.main(["--event", "--run-id", "s1", "--type", "step_start", "--step", "1"])
    itl.main(["--event", "--run-id", "s1", "--type", "wave_end",
              "--data-json", '{"wave": 1, "successes": 0}'])
    rc = itl.main(["--event", "--run-id", "s1", "--type", "step_start", "--step", "2"])
    assert rc == 5


def test_the_rejection_names_both_the_string_step_and_the_int_step(traj_dir, capsys):
    _seed("s2")
    itl.main(["--event", "--run-id", "s2", "--type", "step_start",
              "--data-json", '{"step": "prep"}'])
    itl.main(["--event", "--run-id", "s2", "--type", "wave_start",
              "--data-json", '{"wave": 1, "parallel": true, "step_count": 2}'])
    itl.main(["--event", "--run-id", "s2", "--type", "step_start", "--step", "1"])
    itl.main(["--event", "--run-id", "s2", "--type", "wave_end",
              "--data-json", '{"wave": 1, "successes": 0}'])
    capsys.readouterr()
    itl.main(["--event", "--run-id", "s2", "--type", "step_start", "--step", "2"])
    err = _plain(capsys.readouterr().err)
    assert "'prep'" in err
    assert "1" in err


def test_a_null_step_beside_an_int_step_rejects_instead_of_crashing(traj_dir):
    _seed("s3")
    itl.main(["--event", "--run-id", "s3", "--type", "step_start",
              "--data-json", '{"note": "no step key at all"}'])
    itl.main(["--event", "--run-id", "s3", "--type", "wave_start",
              "--data-json", '{"wave": 1, "parallel": true, "step_count": 2}'])
    itl.main(["--event", "--run-id", "s3", "--type", "step_start", "--step", "1"])
    itl.main(["--event", "--run-id", "s3", "--type", "wave_end",
              "--data-json", '{"wave": 1, "successes": 0}'])
    assert itl.main(["--event", "--run-id", "s3", "--type", "step_start", "--step", "2"]) == 5


def test_the_all_int_rejection_still_reads_in_numeric_order(traj_dir, capsys):
    """Regression: sorting by repr alone would print [1, 10, 2]."""
    _seed("s4")
    itl.main(["--event", "--run-id", "s4", "--type", "wave_start",
              "--data-json", '{"wave": 1, "parallel": true, "step_count": 3}'])
    for step in ("2", "10", "1"):
        itl.main(["--event", "--run-id", "s4", "--type", "step_start", "--step", step])
    itl.main(["--event", "--run-id", "s4", "--type", "wave_end",
              "--data-json", '{"wave": 1, "successes": 0}'])
    capsys.readouterr()
    itl.main(["--event", "--run-id", "s4", "--type", "step_start", "--step", "3"])
    assert "[1, 2, 10]" in _plain(capsys.readouterr().err)


def test_the_sort_key_puts_ints_first_in_numeric_order():
    assert sorted({10, 2, 1}, key=itl._step_sort_key) == [1, 2, 10]


def test_the_sort_key_puts_everything_else_after_the_ints():
    assert sorted({"prep", 2, None, 1}, key=itl._step_sort_key)[:2] == [1, 2]


def test_the_sort_key_is_total_over_every_type_a_payload_can_carry():
    mixed = [1, "prep", None, 2.5, True, ("a",)]
    assert len(sorted(mixed, key=itl._step_sort_key)) == len(mixed)


def test_the_sort_key_orders_the_non_ints_among_themselves():
    """A tie for every odd value makes the rejection message arbitrary: the set
    it sorts has no order of its own, so the same violation reads differently
    from one run to the next."""
    assert sorted(["prep", None, "alpha"], key=itl._step_sort_key) == \
        ["alpha", "prep", None]


def test_a_step_end_for_a_string_step_still_reconciles(traj_dir):
    """`in` on a mixed set was never the crash; keep it working."""
    _seed("s5")
    assert itl.main(["--event", "--run-id", "s5", "--type", "step_start",
                     "--data-json", '{"step": "prep"}']) == 0
    assert itl.main(["--event", "--run-id", "s5", "--type", "step_end",
                     "--data-json", '{"step": "prep", "status": "ok"}']) == 0


def test_an_ordinary_int_only_sequencing_violation_still_exits_five(traj_dir):
    """Regression: the guard's normal path."""
    _seed("s6")
    itl.main(["--event", "--run-id", "s6", "--type", "step_start", "--step", "1"])
    assert itl.main(["--event", "--run-id", "s6", "--type", "step_start", "--step", "2"]) == 5
