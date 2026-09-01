#!/usr/bin/env python3
"""Shard `scripts-05-p1`: a build gate with two bypasses, and four quiet crashes.

The plugin completeness gate exists to refuse a bundle whose skills reference
scripts the bundle does not carry. It had two holes wide enough to walk a dead
reference through:

* **A basename fallback.** `Path(ref).name in bundled_script_names` accepted a
  match on the FILE NAME, so a bundle carrying `scripts/a/tool.py` passed a
  skill referencing `scripts/b/tool.py` — a different file, not in the bundle.
* **A blanket `utils/` skip.** Every reference starting `utils/` was waved
  through, so `scripts/utils/does_not_exist.py` was never reported. The skip
  was never needed: `collect_bundled_scripts` already adds every real file
  under `scripts/utils/`, so an existing utils path matches exactly and a
  missing one SHOULD fail.

Both ship an installable bundle that is broken at install time, with a green
build. The scanner's remaining blind spot — extensionless paths, `bash
scripts/x.sh`, runtime-built paths — is now written down in the file rather
than left to be discovered; the dotted `python -m scripts.foo.bar` form is
covered because it is exact.

The rest of the shard:

* `daemon-fleet-health.py` imported yaml inside the same `try` whose `except`
  tuple names `yaml.YAMLError`, so a missing PyYAML raised NameError from a
  function documented to return None;
* twice over, a heartbeat holding valid JSON with a NON-STRING timestamp
  (`{"last_heartbeat": 123}`) made `fromisoformat` raise TypeError, which the
  `except ValueError` did not catch — one malformed beat took down the whole
  grid instead of marking one workspace `error`;
* `datastore-extract.py` called `load_workbook`/`Presentation` outside any
  handler, so one corrupt file aborted the batch and every later file went
  unprocessed; its markdown cells were unescaped; `--update-index` crashed for
  a target outside `datastore/`, inserted rows before the FIRST HTML comment
  rather than the last, and accumulated a `> Previous:` prefix per run;
* `dead-letter.py` retry did `entry.get("error", ...)[:120]`, which is a
  TypeError on an explicit `"error": null`, and reported "Dead-letter entry
  removed" even when the unlink had failed;
* `design-studio.py` built shared temp filenames at one-second resolution, so
  two concurrent renders could write the same path.

One finding is fixed but NOT verified by execution:
`delete-legacy-schtasks.ps1` caught `CimJobException` by type and called every
CIM failure "not found", so a real service error left `$failed` at 0 and the
script printed "All clean". This host is WSL with no PowerShell installed, so
the fix is a reading. That is stated in the file too.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# The plugin completeness gate
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bp():
    return _load("build_plugins_mod", "scripts/dev/build-plugins.py")


@pytest.fixture
def fake_repo(tmp_path):
    """A repo with one bundled script and one that is NOT bundled."""
    (tmp_path / "scripts" / "a").mkdir(parents=True)
    (tmp_path / "scripts" / "b").mkdir(parents=True)
    (tmp_path / "scripts" / "utils").mkdir(parents=True)
    (tmp_path / "scripts" / "a" / "tool.py").write_text("x = 1\n")
    (tmp_path / "scripts" / "b" / "tool.py").write_text("x = 1\n")
    (tmp_path / "scripts" / "utils" / "real.py").write_text("x = 1\n")
    (tmp_path / ".claude" / "skills" / "demo").mkdir(parents=True)
    return tmp_path


def _skill(repo: Path, body: str) -> None:
    (repo / ".claude" / "skills" / "demo" / "SKILL.md").write_text(
        f"---\nname: demo\n---\n\n{body}\n", encoding="utf-8")


SPEC = {"scripts": ["a/tool.py"], "skills": ["demo"], "hooks": [], "commands": []}


def test_a_basename_match_is_not_a_bundled_script(bp, fake_repo):
    _skill(fake_repo, "Run `python scripts/b/tool.py --now`.")
    missing = bp.completeness_gate(SPEC, fake_repo)
    assert missing == [".claude/skills/demo/SKILL.md -> scripts/b/tool.py"], (
        f"got {missing}: the bundle carries a/tool.py and the skill references "
        "b/tool.py, a different file that is not in the bundle at all"
    )


def test_the_bundled_script_itself_still_passes(bp, fake_repo):
    """Anchor: exact membership must not refuse what IS bundled."""
    _skill(fake_repo, "Run `python scripts/a/tool.py`.")
    assert bp.completeness_gate(SPEC, fake_repo) == []


def test_a_missing_utils_reference_is_reported(bp, fake_repo):
    _skill(fake_repo, "Run `python scripts/utils/does_not_exist.py`.")
    missing = bp.completeness_gate(SPEC, fake_repo)
    assert missing, (
        "every utils/ reference was skipped wholesale, so a utility that does "
        "not exist shipped as a dead reference"
    )
    assert "does_not_exist.py" in missing[0]


def test_a_real_utils_reference_still_passes(bp, fake_repo):
    """Anchor: collect_bundled_scripts adds every real scripts/utils file."""
    _skill(fake_repo, "Run `python scripts/utils/real.py`.")
    assert bp.completeness_gate(SPEC, fake_repo) == []


def test_a_dotted_module_reference_is_seen(bp, fake_repo):
    _skill(fake_repo, "Run `python -m scripts.utils.does_not_exist`.")
    missing = bp.completeness_gate(SPEC, fake_repo)
    assert missing, (
        "`python -m scripts.foo.bar` reaches the same file as a path "
        "reference and the scanner could not see it at all"
    )
    assert "-m scripts.utils.does_not_exist" in missing[0]


def test_a_dotted_reference_to_a_bundled_module_passes(bp, fake_repo):
    _skill(fake_repo, "Run `python -m scripts.utils.real`.")
    assert bp.completeness_gate(SPEC, fake_repo) == []


def test_a_skill_that_is_not_utf8_is_reported_on_not_crashed_into(bp, fake_repo):
    """One stray byte in one SKILL.md took the whole bundle build down.

    `src.read_text(encoding="utf-8")` raises UnicodeDecodeError, which is a
    ValueError and is caught by nothing between here and `main`. MEASURED
    2026-09-01: the gate died on a traceback naming no file, in a function whose
    entire output is a list of named files.

    Both halves are asserted. The undecodable skill must not stop the scan, and
    the reference inside it must still be FOUND - a fix that swallowed the file
    would turn a crash into a silent unscanned skill, which is the worse of the
    two and passes any test that only checks "did not raise".
    """
    (fake_repo / ".claude" / "skills" / "demo" / "SKILL.md").write_bytes(
        b"---\nname: demo\n---\n\ncaf\xe9\n\nRun `python scripts/b/tool.py`.\n")

    missing = bp.completeness_gate(SPEC, fake_repo)
    assert missing == [".claude/skills/demo/SKILL.md -> scripts/b/tool.py"], missing


def test_a_prose_file_that_is_not_utf8_is_read_too(bp, fake_repo):
    """The second reader, which has the same corpus and had the same hole."""
    _skill(fake_repo, "Nothing here.")
    (fake_repo / ".claude" / "skills" / "demo" / "references").mkdir()
    (fake_repo / ".claude" / "skills" / "demo" / "references" / "howto.md"
     ).write_bytes(b"# How to\n\ncaf\xe9\n\nRun `python scripts/b/tool.py` first.\n")

    missing = bp.completeness_gate(SPEC, fake_repo)
    assert any("scripts/b/tool.py" in m for m in missing), missing


def test_a_decodable_skill_is_unchanged_by_the_replacement(bp, fake_repo):
    """Anchor: `errors="replace"` must not alter what an ordinary file scans as."""
    _skill(fake_repo, "Run `python scripts/a/tool.py`.")
    assert bp.completeness_gate(SPEC, fake_repo) == []


def test_the_gate_writes_down_what_it_cannot_see(bp):
    """scope-claims: the report says "no missing targets", not "no broken
    references", and the difference has to be recorded where the scanner is."""
    src = (ROOT / "scripts" / "dev" / "build-plugins.py").read_text(encoding="utf-8")
    head = src.split("_SCRIPT_REF_RE", 1)[0]
    assert "do NOT see" in head or "do not see" in head, (
        "the blind spot (extensionless paths, .sh, runtime-built paths) is not "
        "written down next to the scanners that have it"
    )


def test_the_real_bundles_still_pass_the_tightened_gate(bp):
    """The tightening must not break the bundles this repo actually ships."""
    manifest = bp.load_manifest(ROOT)
    broken = {}
    for name, spec in manifest.items():
        missing = bp.completeness_gate(spec, ROOT)
        if missing:
            broken[name] = missing
    assert not broken, (
        "the exact-membership rule rejects a bundle this repo ships; either "
        f"the bundle is genuinely incomplete or the rule is too tight: {broken}"
    )


# ---------------------------------------------------------------------------
# daemon-fleet-health.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fleet():
    return _load("fleet_mod", "scripts/daemon-fleet-health.py")


def test_a_missing_pyyaml_returns_none_not_a_nameerror(fleet, tmp_path, monkeypatch):
    cfg = tmp_path / "corporate" / "daemon"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text("version: 3\n", encoding="utf-8")

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("No module named 'yaml'")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", no_yaml)
    assert fleet._read_corporate_config_version(tmp_path) is None, (
        "the except tuple names yaml.YAMLError, so an ImportError left `yaml` "
        "unbound and evaluating that tuple raised NameError — from a function "
        "documented to return None when the file cannot be read"
    )


def test_the_config_version_still_reads_when_yaml_is_present(fleet, tmp_path):
    """Anchor: the split import must not break the working path."""
    cfg = tmp_path / "corporate" / "daemon"
    cfg.mkdir(parents=True)
    (cfg / "config.yaml").write_text("version: 7\n", encoding="utf-8")
    assert fleet._read_corporate_config_version(tmp_path) is not None


@pytest.mark.parametrize("value", [123, 4.5, True, ["2026-08-24"], {"a": 1}])
def test_a_non_string_heartbeat_is_an_error_not_a_traceback(fleet, value):
    record = {"status": "ok", "last_heartbeat": value}
    assert fleet._classify(record, stale_threshold_s=600, ceo_version=None) == "error", (
        "valid JSON with a non-string timestamp makes fromisoformat raise "
        "TypeError, which `except ValueError` did not catch — one malformed "
        "beat took the whole grid down"
    )


@pytest.mark.parametrize("value", [123, 4.5, ["x"]])
def test_the_per_daemon_beat_has_the_same_guard(fleet, value):
    assert fleet._classify_beat({"last_heartbeat": value},
                                stale_threshold_s=600) == "error"


def test_a_good_heartbeat_is_still_ok(fleet):
    """Anchor: the widened except must not swallow the working case."""
    now = datetime.now(timezone.utc).isoformat()
    assert fleet._classify({"status": "ok", "last_heartbeat": now},
                           stale_threshold_s=600, ceo_version=None) == "ok"
    assert fleet._classify_beat({"last_heartbeat": now},
                                stale_threshold_s=600) == "ok"


def test_a_stale_heartbeat_is_still_stale(fleet):
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    assert fleet._classify({"status": "ok", "last_heartbeat": old},
                           stale_threshold_s=600, ceo_version=None) == "stale"


# ---------------------------------------------------------------------------
# datastore-extract.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dse():
    return _load("dse_mod", "scripts/datastore-extract.py")


def test_one_corrupt_file_does_not_abort_the_batch(dse, tmp_path, monkeypatch,
                                                   capsys):
    good = tmp_path / "good.xlsx"
    bad = tmp_path / "bad.xlsx"
    for p in (good, bad):
        p.write_bytes(b"PK\x03\x04 not really a workbook")

    def flaky(path):
        if path.name == "bad.xlsx":
            raise ValueError("File is not a zip file")
        return "# good\n"

    monkeypatch.setattr(dse, "extract_xlsx", flaky)
    monkeypatch.setattr(dse, "get_companion_path",
                        lambda p: p.with_suffix(".extract.md"))

    extracted = dse.scan_and_extract(target_dir=tmp_path)
    names = [orig.name for orig, _ in extracted]
    assert "good.xlsx" in names, (
        "the exception left the LOOP, so every file after the corrupt one was "
        "never processed and the run ended on a traceback"
    )
    out = capsys.readouterr().out
    assert "bad.xlsx" in out and "could not be extracted" in out, (
        "surviving the bad file is half the fix; naming it is the other half"
    )


def test_a_cell_containing_a_pipe_does_not_add_a_column(dse):
    assert dse._cell("A|B") == r"A\|B"


def test_a_cell_containing_a_newline_does_not_end_the_row(dse):
    assert "\n" not in dse._cell("line one\nline two")
    assert "\r" not in dse._cell("line one\r\nline two")


def test_an_ordinary_cell_is_unchanged(dse):
    """Anchor: escaping must not mangle normal values."""
    assert dse._cell("Universal Exports") == "Universal Exports"
    assert dse._cell(None) == ""
    assert dse._cell(42) == "42"


def test_index_rows_go_before_the_LAST_comment_not_the_first(dse, tmp_path,
                                                             monkeypatch):
    index = tmp_path / "INDEX.md"
    index.write_text(
        "<!-- header note: do not edit above -->\n"
        "> Last updated: 2026-01-01\n\n"
        "| Path | Domain |\n|---|---|\n"
        "<!-- end of table -->\n", encoding="utf-8")
    monkeypatch.setattr(dse, "index_file", lambda p=index: p)
    monkeypatch.setattr(dse, "datastore_dir", lambda p=tmp_path: p)
    orig = tmp_path / "deals" / "x.xlsx"
    orig.parent.mkdir()
    orig.write_bytes(b"x")
    dse.update_index([(orig, orig.with_suffix(".extract.md"))])

    text = index.read_text(encoding="utf-8")
    assert text.startswith("<!-- header note"), (
        "rows were injected above the file's own header comment, because the "
        "code replaced the FIRST `<!--` while the comment said 'the closing one'"
    )
    assert text.index("deals/x.xlsx") < text.index("<!-- end of table -->")


def test_a_target_outside_the_datastore_does_not_crash_the_index(dse, tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    index = tmp_path / "INDEX.md"
    index.write_text("> Last updated: 2026-01-01\n\n<!-- end -->\n", encoding="utf-8")
    monkeypatch.setattr(dse, "index_file", lambda p=index: p)
    datastore = tmp_path / "datastore"
    monkeypatch.setattr(dse, "datastore_dir", lambda p=datastore: p)
    (tmp_path / "datastore").mkdir()
    outside = tmp_path / "elsewhere" / "y.xlsx"
    outside.parent.mkdir()
    outside.write_bytes(b"x")

    dse.update_index([(outside, outside.with_suffix(".extract.md"))])
    assert "outside" in capsys.readouterr().out, (
        "relative_to raised ValueError AFTER every file had been extracted"
    )


def test_the_previous_date_does_not_accumulate(dse, tmp_path, monkeypatch):
    index = tmp_path / "INDEX.md"
    index.write_text("> Last updated: 2026-01-01\n\n<!-- end -->\n", encoding="utf-8")
    monkeypatch.setattr(dse, "index_file", lambda p=index: p)
    monkeypatch.setattr(dse, "datastore_dir", lambda p=tmp_path: p)
    (tmp_path / "deals").mkdir()

    for n in (1, 2, 3):
        orig = tmp_path / "deals" / f"x{n}.xlsx"
        orig.write_bytes(b"x")
        dse.update_index([(orig, orig.with_suffix(".extract.md"))])

    text = index.read_text(encoding="utf-8")
    assert text.count("> Previous:") == 1, (
        f"one Previous line per run accumulated:\n{text[:300]}"
    )
    assert text.count("> Last updated:") == 1


# ---------------------------------------------------------------------------
# dead-letter.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dlq():
    return _load("dlq_mod", "scripts/dead-letter.py")


def test_a_null_error_does_not_block_the_retry(dlq):
    assert dlq._why({"error": None}) == "failed send", (
        "`.get(k, default)[:120]` on an explicit null is a TypeError, and a "
        "retryable send could not be re-enqueued at all"
    )


def test_a_missing_error_key_still_defaults(dlq):
    assert dlq._why({}) == "failed send"


def test_a_non_string_error_is_coerced(dlq):
    assert dlq._why({"error": 500}) == "500"


def test_a_real_error_is_carried_and_capped(dlq):
    long = "x" * 400
    assert dlq._why({"error": long}) == "x" * 120


def test_a_failed_unlink_is_not_reported_as_removed():
    src = (ROOT / "scripts" / "dead-letter.py").read_text(encoding="utf-8")
    assert "Dead-letter entry KEPT" in src, (
        "the unlink error was swallowed and the operator was told recovery had "
        "completed while the artifact stayed on disk as a duplicate"
    )
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "except OSError:\n            pass" not in body


# ---------------------------------------------------------------------------
# design-studio.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def studio():
    return _load("studio_mod", "scripts/design-studio.py")


def test_two_scratch_names_in_the_same_second_differ(studio):
    names = {studio.scratch_name("render", ".html") for _ in range(10)}
    assert len(names) == 10, (
        "a one-second timestamp in a SHARED tmp dir meant two concurrent "
        "renders could write the same path, and one could screenshot or delete "
        "the other's HTML"
    )


def test_the_scratch_name_carries_the_pid(studio):
    import os
    assert f"-{os.getpid()}-" in studio.scratch_name("render", ".html"), (
        "a counter alone is per-process; two PROCESSES need the pid too"
    )


def test_the_scratch_name_keeps_its_shape(studio):
    name = studio.scratch_name("pdf", ".html")
    assert name.startswith("pdf-") and name.endswith(".html")


def test_both_temp_paths_go_through_the_helper():
    src = (ROOT / "scripts" / "design-studio.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'get_tmp_dir() / f"render-{timestamp()}.html"' not in body
    assert 'get_tmp_dir() / f"pdf-{timestamp()}.html"' not in body
    assert body.count("scratch_name(") >= 3  # the two call sites plus the def


# ---------------------------------------------------------------------------
# delete-legacy-schtasks.ps1 — fixed by reading, NOT executed
# ---------------------------------------------------------------------------

def test_the_cim_catch_checks_the_category_not_just_the_type():
    """This host is WSL with no PowerShell, so this is a source check and says
    so. `CimJobException` covers every CIM failure, and calling all of them
    'not found' left $failed at 0 while the script printed 'All clean'."""
    src = (ROOT / "scripts" / "delete-legacy-schtasks.ps1").read_text(encoding="utf-8")
    assert "$_.CategoryInfo.Category -eq 'ObjectNotFound'" in src
    assert "FAILED (CIM)" in src, "a non-absence CIM error must reach $failed"
    assert "NOT EXECUTED" in src, (
        "the fix was not run on a Windows host; the file has to say so rather "
        "than read as verified"
    )
