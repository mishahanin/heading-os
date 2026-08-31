"""Three tools that reported an answer their method never established.

Covers the k3 audit shard `scripts-00-p3` for `scripts/audit-deps.py`,
`scripts/bootcamp-roster.py` and `scripts/artifact-evaluator.py`. Every finding
here is one shape: the tool produced a confident result about a scope it had
not actually inspected. `.claude/rules/scope-claims.md` names that shape.

*A CVE gate that could go green over the wrong dependency set.*
`_export_full_requirements` returned a bool, and False meant two different
things: `uv` is not installed, or `uv` is installed and the export FAILED (a
corrupt `uv.lock`, a `uv` too old for `--no-emit-project`). The caller read
both as the first, fell back to auditing whatever happened to be installed in
the active environment, printed `uv unavailable` -- sending anyone reading the
log to look for a binary that was right there -- and could then exit 0. The
full locked graph, which is the entire reason this script exists beside the
narrower `requirements.txt` audit, was never assembled.

*And the docstring named a gate it does not run in.* It claimed to be "the
single auditing primitive shared by the pre-commit hook and the scheduled CI
workflow". The commit-time hook is `pip-audit-cve` in
`.pre-commit-config.yaml`, an inline one-liner over `requirements.txt` alone --
which is the `--no-dev` export, so the commit gate has exactly the
dev-and-transitive blind spot this file was written to close. The sentence was
why nobody was looking.

*An "attend both" rule that could never fire.* `recommend_tracks` checked
InfoSec/TrustONE after the technical and ops substring rules. `"engineering"`
is in the technical list and `"operations"` is in the ops list, so "InfoSec
Engineering" returned Tech-only and "TrustONE Operations" returned Ops-only,
each directly contradicting the comment sitting above the branch, for exactly
the population the branch was written for.

*An attendance column that read the whole spreadsheet.* `load_prelim`'s
docstring says it returns attendee names. It added EVERY non-empty string cell
in the workbook, excluding only the literal header `"name"` -- job titles,
departments, cities, free-text notes. `in_prelim` matches first names, last
names and initials against that set, so anyone whose name equalled any word
standing alone in any cell was marked present. And a missing or corrupt file
was swallowed into an empty set behind one WARN line, so the run exited 0 and
wrote a roster whose entire `In Prelim List?` column read `N`.

*Two checks that reported "pass" for a check that never ran.* `check()`
computes `"warn" if (warn and not passed) else ...`, so `warn=True` is inert
when `passed=True`. Both `run_trigger_test` error paths passed `True`, so a
routing test that timed out, failed to spawn, or emitted garbage was stamped
`"status": "pass"` with a detail line saying it could not run.

*A frontmatter parser that could not satisfy its own caller.* Without PyYAML
the fallback skipped every indented line, so `metadata:` parsed as the empty
string and `evaluate_skill`'s `isinstance(meta, dict)` failed for every skill.
The check measured whether PyYAML was installed, not whether the artifact was
correct.

No test here runs `uv`, `pip-audit` or the LLM-judge, reads the operator's
event spreadsheet, or names a real person. Every roster fixture is invented.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, modname: str):
    spec = importlib.util.spec_from_file_location(modname, str(ROOT / name))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def deps():
    return _load("scripts/audit-deps.py", "audit_deps_scope_mod")


@pytest.fixture(scope="module")
def roster():
    return _load("scripts/bootcamp-roster.py", "bootcamp_roster_scope_mod")


@pytest.fixture(scope="module")
def evaluator():
    return _load("scripts/artifact-evaluator.py", "artifact_eval_scope_mod")


def _done(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ============================================================
# 1. The CVE gate and the scope it did not assemble
# ============================================================

def test_no_uv_and_a_failed_export_are_different_answers(deps):
    """The premise. One bool could not carry two facts, and did not."""
    assert deps.EXPORT_NO_UV != deps.EXPORT_FAILED
    assert deps.EXPORT_OK not in (deps.EXPORT_NO_UV, deps.EXPORT_FAILED)


def test_a_missing_uv_reports_no_uv(deps, tmp_path, monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda _n: None)
    assert deps._export_full_requirements(tmp_path / "r.txt") == deps.EXPORT_NO_UV


def test_a_failed_export_reports_failed_not_missing(deps, tmp_path, monkeypatch,
                                                    capsys):
    monkeypatch.setattr(deps.shutil, "which", lambda _n: "/usr/bin/uv")
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: _done(2, stderr="error: bad uv.lock\n"))
    assert deps._export_full_requirements(tmp_path / "r.txt") == deps.EXPORT_FAILED
    assert "bad uv.lock" in capsys.readouterr().err


def test_a_good_export_writes_the_file_and_reports_ok(deps, tmp_path, monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda _n: "/usr/bin/uv")
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: _done(0, stdout="requests==2.0\n"))
    dest = tmp_path / "r.txt"
    assert deps._export_full_requirements(dest) == deps.EXPORT_OK
    assert dest.read_text(encoding="utf-8") == "requests==2.0\n"


def _neutralise_main(deps, monkeypatch, export_state):
    monkeypatch.setattr(deps, "_reexec_in_venv_if_needed", lambda: None)
    monkeypatch.setattr(deps, "_have", lambda _m: True)
    monkeypatch.setattr(deps, "_export_full_requirements",
                        lambda _d: export_state)
    monkeypatch.setattr(sys, "argv", ["audit-deps.py"])


def test_a_failed_export_refuses_instead_of_auditing_something_else(deps,
                                                                    monkeypatch,
                                                                    capsys):
    """The whole defect: the audit must not silently change scope and pass."""
    ran = []
    _neutralise_main(deps, monkeypatch, deps.EXPORT_FAILED)
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: ran.append(a) or _done(0))
    rc = deps.main()
    assert rc == deps.EXIT_SCOPE_UNAVAILABLE
    assert rc not in (0, 1), "0 reads as clean and 1 reads as vulnerabilities found"
    assert ran == [], "pip-audit ran over a scope this script did not assemble"
    err = capsys.readouterr().err
    assert "Refusing" in err


def test_a_missing_uv_still_falls_back_and_says_so_accurately(deps, monkeypatch,
                                                              capsys):
    """The graceful skip stays. Only its wording had to stop lying."""
    _neutralise_main(deps, monkeypatch, deps.EXPORT_NO_UV)
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: _done(0))
    assert deps.main() == 0
    err = capsys.readouterr().err
    assert "uv not on PATH" in err
    assert "uv unavailable" not in err, "the old wording blamed a missing binary"


def test_a_good_export_audits_the_locked_graph(deps, monkeypatch, capsys):
    seen = {}
    _neutralise_main(deps, monkeypatch, deps.EXPORT_OK)
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda cmd, *a, **k: seen.setdefault("cmd", cmd) and None
                        or _done(0))
    assert deps.main() == 0
    assert "--requirement" in seen["cmd"]
    assert "full locked graph" in capsys.readouterr().err


def test_the_docstring_no_longer_claims_a_precommit_hook():
    """`.claude/rules/scope-claims.md`: state the coverage the method establishes.

    Verified against the file rather than asserted: the commit-time gate is
    `pip-audit-cve`, and it audits `requirements.txt`.
    """
    src = (ROOT / "scripts" / "audit-deps.py").read_text(encoding="utf-8")
    head = src.split('"""')[1]
    assert "NOT pre-commit" in head
    # The old sentence is still in the file, quoted as history. What must not
    # come back is the sentence ASSERTED, so pin the quoting clause in front
    # of it rather than banning the words.
    assert head.index("This paragraph read") < head.index(
        "shared by the pre-commit hook")
    precommit = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "audit-deps.py" not in precommit, (
        "the docstring's claim is now the fact; if this ever changes, change "
        "the docstring in the same commit"
    )
    assert "pip-audit-cve" in precommit


def test_the_new_exit_code_is_documented():
    src = (ROOT / "scripts" / "audit-deps.py").read_text(encoding="utf-8")
    head = src.split('"""')[1]
    assert "  2  " in head, "an undocumented exit code is a caller's next bug"


# ============================================================
# 2. The track rule that ran too late, and the roster it fed
# ============================================================

@pytest.mark.parametrize("function", [
    "InfoSec Engineering",          # would have matched `engineering` first
    "TrustONE Operations",          # would have matched `operations` first
    "InfoSec",
    "TrustONE Governance",
])
def test_infosec_and_trustone_attend_both_passes(roster, function):
    tech, ops, why = roster.recommend_tracks("someone", function, "Specialist")
    assert (tech, ops) == ("Y", "Y"), why
    assert "InfoSec/TrustONE" in why


def test_a_plain_technical_function_is_still_tech_only(roster):
    """Moving the specific rule up must not swallow the generic ones."""
    assert roster.recommend_tracks("someone", "Engineering", "Dev")[:2] == ("Y", "N")


def test_a_plain_ops_function_is_still_ops_only(roster):
    assert roster.recommend_tracks("someone", "Marketing", "Manager")[:2] == ("N", "Y")


def test_an_unknown_function_still_asks_for_confirmation(roster):
    assert roster.recommend_tracks("someone", "Falconry", "Falconer")[:2] == ("?", "?")


# ---------------------------------------------------------------------------
# load_prelim
# ---------------------------------------------------------------------------

def _sheet(tmp_path: Path, rows: list[list], name: str = "prelim.xlsx") -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


@pytest.fixture
def prelim(roster, monkeypatch):
    """Bind openpyxl the way the CLI does, and never touch the real workbook."""
    roster._ensure_openpyxl()

    def _use(path: Path):
        monkeypatch.setattr(roster, "prelim_xlsx", lambda p=path: p)
    return _use


def test_only_the_name_column_is_read(roster, prelim, tmp_path):
    """The defect, driven end to end. `Bond` appears in a NOTES column."""
    path = _sheet(tmp_path, [
        ["Name", "Department", "Notes"],
        ["Alice Moreau", "Engineering", "vegetarian"],
        ["Victor Sadiq", "Marketing", "Bond room booked"],
    ])
    prelim(path)
    names = roster.load_prelim()
    assert names == {"alice moreau", "victor sadiq"}
    assert "bond" not in names
    assert "engineering" not in names


def test_someone_named_after_a_stray_cell_is_not_marked_present(roster, prelim,
                                                                tmp_path):
    """What the over-read actually did to the column the CEO reads.

    The sheet holds SHORT names, which is the shape `in_prelim` matches
    against a full GAL display name.
    """
    path = _sheet(tmp_path, [
        ["Name", "Notes"],
        ["Alice", "Bond room booked"],
    ])
    prelim(path)
    names = roster.load_prelim()
    assert roster.in_prelim("Alice Moreau", "amoreau", names) is True
    assert roster.in_prelim("James Bond", "jbond", names) is False, (
        "`Bond` came from a catering note, not from the attendee list"
    )


def test_the_header_row_itself_is_not_a_name(roster, prelim, tmp_path):
    path = _sheet(tmp_path, [["Attendee"], ["Alice Moreau"]])
    prelim(path)
    assert roster.load_prelim() == {"alice moreau"}


def test_a_header_below_a_title_row_is_still_found(roster, prelim, tmp_path):
    """Real sheets carry a title above the table."""
    path = _sheet(tmp_path, [
        ["Bootcamp preliminary list"],
        [],
        ["Name", "Function"],
        ["Alice Moreau", "Engineering"],
    ])
    prelim(path)
    assert roster.load_prelim() == {"alice moreau"}


def test_a_sheet_with_no_recognised_header_degrades_out_loud(roster, prelim,
                                                             tmp_path, capsys):
    """Refusing would break a shape nobody has described here. Say it instead."""
    path = _sheet(tmp_path, [["Alice Moreau"], ["Victor Sadiq"]])
    prelim(path)
    names = roster.load_prelim()
    assert names == {"alice moreau", "victor sadiq"}
    out = capsys.readouterr().out
    assert "no name column found" in out
    assert "over-reports" in out


def test_a_missing_workbook_raises_instead_of_returning_empty(roster, prelim,
                                                              tmp_path):
    """An empty set is indistinguishable from an empty list."""
    prelim(tmp_path / "does-not-exist.xlsx")
    with pytest.raises(roster.PrelimUnavailable):
        roster.load_prelim()


def test_a_corrupt_workbook_raises_too(roster, prelim, tmp_path):
    bad = tmp_path / "corrupt.xlsx"
    bad.write_bytes(b"this is not a zip container")
    prelim(bad)
    with pytest.raises(roster.PrelimUnavailable):
        roster.load_prelim()


def test_the_run_refuses_rather_than_writing_a_false_column(roster, monkeypatch,
                                                            capsys):
    """`main` must not ship a roster whose attendance column is all `N`."""
    wrote = []

    def _boom():
        raise roster.PrelimUnavailable("cannot read the prelim list")

    monkeypatch.setattr(roster, "_ensure_openpyxl", lambda: None)
    monkeypatch.setattr(roster, "build_roster", _boom)
    monkeypatch.setattr(roster, "write_excel",
                        lambda *a, **k: wrote.append(a))
    assert roster.main() == 1
    assert wrote == [], "a roster was written from a list that could not be read"
    err = capsys.readouterr().err
    assert "Refusing to write it" in err


def test_the_entry_point_propagates_the_exit_code():
    src = (ROOT / "scripts" / "bootcamp-roster.py").read_text(encoding="utf-8")
    assert "raise SystemExit(main())" in src, (
        "a bare `main()` under __main__ exits 0 whatever main returns"
    )


# ============================================================
# 3. The evaluator that graded a check it never ran
# ============================================================

def test_warn_is_inert_when_passed_is_true(evaluator):
    """The premise, asserted so the two fixes below cannot drift from it."""
    assert evaluator.check("x", True, "d", warn=True)["status"] == "pass"
    assert evaluator.check("x", False, "d", warn=True)["status"] == "warn"
    assert evaluator.check("x", False, "d", warn=False)["status"] == "fail"


def _skill_with_triggers(tmp_path: Path) -> Path:
    d = tmp_path / "someskill"
    d.mkdir()
    (d / "triggers.json").write_text("[]", encoding="utf-8")
    return d


def test_a_trigger_test_that_could_not_spawn_is_not_a_pass(evaluator, tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(evaluator.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no exec")))
    res = evaluator.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] == "warn"
    assert "could not run" in res["detail"]


def test_a_trigger_test_that_timed_out_is_not_a_pass(evaluator, tmp_path,
                                                     monkeypatch):
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=300)

    monkeypatch.setattr(evaluator.subprocess, "run", _timeout)
    res = evaluator.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] == "warn"


def test_unparseable_output_is_not_a_pass(evaluator, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator.subprocess, "run",
                        lambda *a, **k: _done(1, stdout="not json"))
    res = evaluator.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] == "warn"
    assert "unparseable" in res["detail"]


def test_the_deliberate_skip_is_still_a_clean_pass(evaluator, tmp_path,
                                                   monkeypatch):
    """Exit 3 means no API key. That is a real skip, not a broken run."""
    monkeypatch.setattr(evaluator.subprocess, "run", lambda *a, **k: _done(3))
    res = evaluator.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] == "pass"


def test_a_real_routing_result_still_reports_its_rate(evaluator, tmp_path,
                                                      monkeypatch):
    payload = json.dumps({"overall_rate": 1.0, "total_passed": 8,
                          "total_cases": 8})
    monkeypatch.setattr(evaluator.subprocess, "run",
                        lambda *a, **k: _done(0, stdout=payload))
    res = evaluator.run_trigger_test(_skill_with_triggers(tmp_path))
    assert res["status"] == "pass"
    assert "8/8" in res["detail"]


def test_a_warn_never_fails_the_run(evaluator):
    """`main` exits 1 only on `fail`, and the trigger test is appended AFTER
    the --strict warn-to-fail conversion. Both halves are load-bearing."""
    src = (ROOT / "scripts" / "artifact-evaluator.py").read_text(encoding="utf-8")
    strict_at = src.index('if args.strict:')
    append_at = src.index("checks.append(tt)")
    assert strict_at < append_at, (
        "appended before the conversion, --strict would turn an advisory "
        "routing miss into a hard failure"
    )
    assert 'if c["status"] == "fail"' in src


# ---------------------------------------------------------------------------
# The frontmatter fallback
# ---------------------------------------------------------------------------

SKILL_FM = (
    "name: example-skill\n"
    "description: does a thing\n"
    "metadata:\n"
    "  author: A Person\n"
    '  version: "1.0"\n'
    "x-heading-orchestration:\n"
    "  parallel_safe: false\n"
    "  shared_state:\n"
    "    - crm/contacts/\n"
    "    - context/pipeline.md\n"
    "allowed-tools: Read, Bash\n"
)


def test_the_fallback_keeps_nested_mappings(evaluator):
    data = evaluator._frontmatter_without_pyyaml(SKILL_FM)
    assert isinstance(data["metadata"], dict), (
        "the evaluator asks isinstance(metadata, dict); a str can never pass"
    )
    assert data["metadata"] == {"author": "A Person", "version": "1.0"}


def test_the_fallback_keeps_nested_lists(evaluator):
    """A list under a second-level key must not replace that key's siblings."""
    data = evaluator._frontmatter_without_pyyaml(SKILL_FM)
    block = data["x-heading-orchestration"]
    assert block["parallel_safe"] == "false"
    assert block["shared_state"] == ["crm/contacts/", "context/pipeline.md"]


def test_the_fallback_keeps_top_level_scalars(evaluator):
    data = evaluator._frontmatter_without_pyyaml(SKILL_FM)
    assert data["name"] == "example-skill"
    assert data["allowed-tools"] == "Read, Bash"


def test_a_scalar_after_a_block_closes_it(evaluator):
    """`allowed-tools` follows a nested block and must not join it."""
    data = evaluator._frontmatter_without_pyyaml(SKILL_FM)
    assert "allowed-tools" not in data["x-heading-orchestration"]


def test_stray_indentation_cannot_overwrite_a_scalar(evaluator):
    """A fallback parser is exactly what meets malformed input.

    A top-level key with a VALUE opens no block, so an indented line beneath
    it belongs to nothing and is dropped. Track the key regardless and the
    next indented line converts `description` from its string into a dict,
    silently destroying the field the evaluator then checks.
    """
    data = evaluator._frontmatter_without_pyyaml(
        "description: does a thing\n"
        "  stray: value\n"
        "name: example-skill\n"
    )
    assert data["description"] == "does a thing"
    assert data["name"] == "example-skill"


def test_a_list_under_a_nested_key_is_collected(evaluator):
    data = evaluator._frontmatter_without_pyyaml(
        "triggers:\n  - one\n  - two\n")
    assert data["triggers"] == ["one", "two"]


def test_the_fallback_result_satisfies_the_metadata_check(evaluator, tmp_path,
                                                          monkeypatch):
    """End to end: with PyYAML absent, a correct skill must stop being warned at.

    The `import yaml` inside `parse_yaml_frontmatter` is what the ImportError
    branch hangs off, so the absence is simulated there rather than by
    uninstalling a core dependency.
    """
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError("no module named yaml")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _no_yaml)
    fm, err = evaluator.parse_yaml_frontmatter(f"---\n{SKILL_FM}---\nbody\n")
    assert err is None
    # Assert the CONTENT, not `isinstance(fm.get("metadata", {}), dict)`.
    # That form reads the default on a miss, so an empty result passes it and
    # a parser returning `{}` would look correct.
    assert fm["name"] == "example-skill"
    assert fm["metadata"] == {"author": "A Person", "version": "1.0"}
    # `"false"`, a string. PyYAML would give the bool `False`, so this also
    # proves the ImportError branch is the one that ran.
    assert fm["x-heading-orchestration"]["parallel_safe"] == "false"
