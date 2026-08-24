"""A reset that destroyed files, and errors that arrived as tracebacks.

Covers the k3 audit shard `scripts-00-p2` for `scripts/apply-wizard-answers.py`
and `scripts/archive-transcripts.py`.

*The one that destroys data.* `cmd_reset` reverts a tracked file and DELETES an
untracked one. Nothing checked that the workspace was a git repository. In a
directory that is not one, `git ls-files --error-unmatch` fails for every file,
so every file took the delete branch: `--reset --force` unlinked the operator's
files outright, with no index to restore them from, under a help line reading
"Revert touched files to git-index state". `--force` skipped the `git status`
gate, so nothing else checked either.

*Errors that bypassed the file's own convention.* This script has a careful
SchemaError-to-exit-1 discipline, and three ways around it. `load_answers`
raises SchemaError on a corrupt `answers.json` and no subcommand caught it, so
a corrupt file still killed every subcommand with a traceback -- the exact
outcome its own inline comment claimed to have fixed. `save_answers` raises
StateWriteError after the workspace has already been modified, and its
docstring complained that this surfaced "as a traceback rather than as the
divergence it is"; nothing caught that either. And the write side of every
apply branch was unguarded, so a read-only directory produced the same
divergence by a third route.

*Two shapes that were not objects.* `detect_audience` and `_read_stdin_payload`
both validated JSON syntax and then called `.get` on the result. `[]`, `"x"`
and `42` all parse, so all three raised AttributeError instead of SchemaError.

*A containment rule enforced on one path of two.* `_collect_matching_files`
refuses a glob that reaches outside the workspace, with a comment stating the
invariant. The rich-question `output` path had no such check, so
`../../tmp/escape.md` was written to -- and `--reset` then deleted it.

*Silence read as success.* `archive-transcripts.py` exits 2 from `--status`
when the transcript directory cannot be resolved, and exited 0 from archive
mode for the same condition, printing "archived 0 ... 0 failed" to a cron job
that reads exit codes and not stderr.

No test here runs git against the real repository, sends anything, or touches
the operator's transcripts.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").replace(".py", ""), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(name: str) -> str:
    """Source minus whole-line comments; each fix left one quoting the old code."""
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def wiz():
    return _load("apply-wizard-answers.py")


# ============================================================
# --reset refuses to delete what it cannot restore
# ============================================================

def _reset_args(wiz, workspace, force):
    ns = wiz.argparse.Namespace() if hasattr(wiz, "argparse") else None
    if ns is None:
        import argparse
        ns = argparse.Namespace()
    ns.workspace_root = workspace
    ns.resolved_audience = "public"
    ns.force = force
    return ns


def _seed_workspace(tmp_path):
    """A workspace with one answered placeholder question and its target file."""
    setup = tmp_path / ".setup"
    setup.mkdir()
    (setup / "answers.json").write_text(json.dumps({
        "schema_version": 1,
        "answers": {"company": {"status": "answered", "value": "Acme"}},
    }), encoding="utf-8")
    # The bank is a YAML LIST at config/wizard-questions.yaml.
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "wizard-questions.yaml").write_text(
        "- id: company\n"
        "  audience: [public]\n"
        "  type: placeholder\n"
        "  required: true\n"
        "  prompt: Company?\n"
        "  example: Acme\n"
        "  target:\n"
        "    files: ['README.md']\n"
        "    placeholder: '{COMPANY}'\n",
        encoding="utf-8")
    (tmp_path / "README.md").write_text("Acme rules\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("force", [True, False])
def test_reset_refuses_outside_a_git_work_tree(wiz, tmp_path, capsys, force):
    """The whole defect: --reset --force UNLINKED files in a non-git workspace."""
    ws = _seed_workspace(tmp_path)
    rc = wiz.cmd_reset(_reset_args(wiz, ws, force))
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert (ws / "README.md").exists(), "the file was deleted, not reverted"
    assert (ws / "README.md").read_text(encoding="utf-8") == "Acme rules\n"
    err = capsys.readouterr().err
    # Without --force the `git status` gate already refuses ("not a git
    # repository"); with --force that gate is skipped and the new work-tree
    # check is the only thing standing between the operator and an unlink.
    assert any(s in err for s in ("not a git work tree", "not a git repository",
                                  "git is not installed")), err


def test_the_force_path_is_the_one_that_had_no_gate(wiz, tmp_path, capsys):
    """--force skips the git status check, so it reaches the new one."""
    ws = _seed_workspace(tmp_path)
    assert wiz.cmd_reset(_reset_args(wiz, ws, True)) == wiz.EXIT_SCHEMA_ERROR
    err = capsys.readouterr().err
    assert "Refusing" in err and "work tree" in err, err
    assert (ws / "README.md").exists()


def test_reset_says_how_many_files_it_declined_to_delete(wiz, tmp_path, capsys):
    ws = _seed_workspace(tmp_path)
    wiz.cmd_reset(_reset_args(wiz, ws, True))
    err = capsys.readouterr().err
    assert "DELETE" in err and "Refusing" in err


def test_reset_reports_a_missing_git_binary_rather_than_crashing(wiz, tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """A raw FileNotFoundError traceback is not an error report."""
    ws = _seed_workspace(tmp_path)

    def _no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _no_git)
    for force in (True, False):
        rc = wiz.cmd_reset(_reset_args(wiz, ws, force))
        assert rc == wiz.EXIT_SCHEMA_ERROR
        assert "git is not installed" in capsys.readouterr().err


def test_the_work_tree_check_runs_before_any_unlink():
    code = _code("apply-wizard-answers.py")
    start = code.index("def cmd_reset(")
    body = code[start:]
    check = body.index('"--is-inside-work-tree"')
    unlink = body.index("path.unlink()")
    assert check < unlink, "checking after deleting is not checking"


# ============================================================
# A JSON value is not necessarily an object
# ============================================================

@pytest.mark.parametrize("blob", ["[]", '"x"', "42", "null", "true"])
def test_a_non_object_identity_file_is_a_schema_error(wiz, tmp_path, blob):
    (tmp_path / ".workspace-identity.json").write_text(blob, encoding="utf-8")
    with pytest.raises(wiz.SchemaError) as exc:
        wiz.detect_audience(tmp_path)
    assert "not an object" in str(exc.value)


def test_a_real_identity_file_still_resolves(wiz, tmp_path):
    (tmp_path / ".workspace-identity.json").write_text(
        '{"type": "exec-workspace"}', encoding="utf-8")
    assert wiz.detect_audience(tmp_path) == "exec"


def test_an_absent_identity_file_is_public(wiz, tmp_path):
    assert wiz.detect_audience(tmp_path) == "public"


@pytest.mark.parametrize("blob", ["[1]", '"x"', "42"])
def test_a_non_object_stdin_payload_is_a_schema_error(wiz, monkeypatch, blob):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(blob))
    with pytest.raises(wiz.SchemaError) as exc:
        wiz._read_stdin_payload()
    assert "not a JSON object" in str(exc.value)


def test_an_object_stdin_payload_is_returned(wiz, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO('{"value": "Acme"}'))
    assert wiz._read_stdin_payload() == {"value": "Acme"}


# ============================================================
# Containment applies to rich outputs too
# ============================================================

@pytest.mark.parametrize("escape", ["../../tmp/escape.md", "../outside.md",
                                    "sub/../../../etc/x.md"])
def test_a_rich_output_may_not_reach_past_the_workspace(wiz, tmp_path, escape):
    with pytest.raises(wiz.SchemaError) as exc:
        wiz._resolve_output_path(tmp_path, escape)
    assert "outside the workspace" in str(exc.value)


def test_a_rich_output_inside_the_workspace_resolves(wiz, tmp_path):
    assert wiz._resolve_output_path(tmp_path, "docs/out.md") == tmp_path / "docs/out.md"


def test_a_path_that_only_looks_like_an_escape_is_allowed(wiz, tmp_path):
    """`a/../b.md` never leaves the tree, so it is not the thing being refused."""
    assert wiz._resolve_output_path(tmp_path, "a/../b.md").resolve() == \
        (tmp_path / "b.md").resolve()


def test_every_rich_path_goes_through_the_resolver():
    """Three call sites now, not two.

    `cmd_question` and `cmd_reset` were the original pair. `_plan_question`'s
    rich branch was the third and had a bare join, which is what let `--all`
    write outside the workspace while `--question` refused the same bank.
    """
    code = _code("apply-wizard-answers.py")
    assert "out_path = workspace_root / out_rel" not in code
    assert "touched.add(workspace_root / out_rel)" not in code
    assert 'plans.append(("write", workspace_root / out_rel' not in code
    assert code.count("_resolve_output_path(workspace_root, out_rel)") == 3


# ============================================================
# The dispatch turns raisers into exit codes
# ============================================================

def test_a_corrupt_answers_file_exits_one_rather_than_crashing(wiz, tmp_path,
                                                               monkeypatch,
                                                               capsys):
    """load_answers raised SchemaError and no subcommand caught it.

    The bank has to be VALID for this to test what it says: with the bank
    missing, cmd_status returns EXIT_SCHEMA_ERROR from the bank branch and
    never reaches load_answers.
    """
    ws = _seed_workspace(tmp_path)
    (ws / ".setup" / "answers.json").write_text("not json", encoding="utf-8")
    monkeypatch.chdir(ws)
    assert wiz.main(["--status"]) == wiz.EXIT_SCHEMA_ERROR
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "Question bank not found" not in err, "failed for the wrong reason"


def test_a_state_write_failure_names_the_divergence(wiz, tmp_path, monkeypatch,
                                                    capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wiz, "cmd_status",
                        lambda _a: (_ for _ in ()).throw(
                            wiz.StateWriteError("disk full")))
    assert wiz.main(["--status"]) == wiz.EXIT_FILE_WRITE_ERROR
    err = capsys.readouterr().err
    assert "disk full" in err and "divergence" in err


def test_an_os_error_on_the_write_side_is_an_exit_code(wiz, tmp_path,
                                                       monkeypatch, capsys):
    """The apply branches guarded their READ and left every write unguarded."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wiz, "cmd_status",
                        lambda _a: (_ for _ in ()).throw(
                            PermissionError("read-only file system")))
    assert wiz.main(["--status"]) == wiz.EXIT_FILE_WRITE_ERROR
    err = capsys.readouterr().err
    assert "PermissionError" in err and "divergence" in err


def test_the_dispatch_wraps_every_subcommand():
    code = _code("apply-wizard-answers.py")
    start = code.index("def main(")
    body = code[start:code.index("def _depends_on_satisfied(", start)]
    for cmd in ("cmd_status", "cmd_question", "cmd_skip", "cmd_all", "cmd_reset"):
        assert f"return {cmd}(args)" in body
    assert "except SchemaError as e:" in body
    assert "except StateWriteError as e:" in body
    assert "except OSError as e:" in body


# ============================================================
# Smaller truths
# ============================================================

def test_an_env_key_is_matched_line_anchored_not_by_substring():
    """`OTHER_API_KEY=x` satisfied a check for `API_KEY`, so the warning slept."""
    code = _code("apply-wizard-answers.py")
    assert 'if f"{env_var}=" not in env_content:' not in code
    assert 'line.startswith(f"{env_var}=")' in code


def test_archiving_a_question_with_no_draft_is_refused():
    code = _code("apply-wizard-answers.py")
    start = code.index('if payload.get("archive_draft"):')
    body = code[start:start + 900]
    assert 'if not entry.get("draft"):' in body
    assert "no draft to archive" in body
    refuse = body.index("no draft to archive")
    claim = body.index('{"archived": q["id"]}')
    assert refuse < claim, "the refusal must precede the success report"


def test_the_mask_docstring_no_longer_denies_the_file_mode():
    """It claimed answers.json "got no mode of its own" while save_answers chmods it.

    The corrected text still quotes the old claim, so this asserts the
    correction is present rather than that the phrase is absent.
    """
    code = (ROOT / "scripts" / "apply-wizard-answers.py").read_text(encoding="utf-8")
    assert "chmod(tmp, 0o600)" in code.replace("os.chmod", "chmod")
    assert "`save_answers` chmods the file 0600" in code
    assert "as though it were current" in code


# ============================================================
# archive-transcripts tells automation the truth
# ============================================================

def test_an_unresolved_transcript_dir_is_not_exit_zero(tmp_path, monkeypatch,
                                                       capsys):
    """Exit 0 here reads as success to a cron job, which does not read stderr."""
    arch = _load("archive-transcripts.py")
    monkeypatch.setattr(arch, "archive",
                        lambda dry_run=False: {"archived": 0, "skipped": 0,
                                               "too_fresh": 0, "failed": 0,
                                               "unresolved": 1})
    assert arch.main([]) == 2, "--status already exits 2 for this condition"


def test_a_normal_archive_run_still_exits_zero(monkeypatch):
    arch = _load("archive-transcripts.py")
    monkeypatch.setattr(arch, "archive",
                        lambda dry_run=False: {"archived": 3, "skipped": 1,
                                               "too_fresh": 2, "failed": 0})
    assert arch.main([]) == 0


def test_a_failed_archive_still_exits_one(monkeypatch):
    arch = _load("archive-transcripts.py")
    monkeypatch.setattr(arch, "archive",
                        lambda dry_run=False: {"archived": 1, "skipped": 0,
                                               "too_fresh": 0, "failed": 2})
    assert arch.main([]) == 1


# ============================================================
# Gaps the mutation harness found
# ============================================================

def test_a_bare_git_dir_is_refused_even_though_rev_parse_succeeds(wiz, tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """`--is-inside-work-tree` exits 0 and prints "false" inside a .git dir.

    Checking only the return code passed that case straight through to the
    unlink loop, which is the branch that destroys files.
    """
    ws = _seed_workspace(tmp_path)

    class _R:
        returncode = 0
        stdout = "false\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert wiz.cmd_reset(_reset_args(wiz, ws, True)) == wiz.EXIT_SCHEMA_ERROR
    assert "not a git work tree" in capsys.readouterr().err
    assert (ws / "README.md").exists()


def test_a_failed_git_is_refused_even_when_stdout_says_true(wiz, tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Both halves of the guard have to hold, not either one.

    A mutation dropping the return-code half survived until this test existed:
    every real-git outcome is caught by the stdout half alone, so nothing
    observable changed. The half is kept anyway, because "git printed the word
    true while exiting non-zero" is exactly the confused state in which the
    next branch deletes files.
    """
    ws = _seed_workspace(tmp_path)

    class _R:
        returncode = 128
        stdout = "true\n"
        stderr = "fatal: detected dubious ownership in repository\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert wiz.cmd_reset(_reset_args(wiz, ws, True)) == wiz.EXIT_SCHEMA_ERROR
    assert (ws / "README.md").exists()
    err = capsys.readouterr().err
    assert "Refusing" in err


def test_a_git_that_failed_for_another_reason_is_quoted_not_guessed(wiz,
                                                                    tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """"dubious ownership" reported as "not a git work tree" is a wrong lead."""
    ws = _seed_workspace(tmp_path)

    class _R:
        returncode = 128
        stdout = ""
        stderr = "fatal: detected dubious ownership in repository at '/x'\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert wiz.cmd_reset(_reset_args(wiz, ws, True)) == wiz.EXIT_SCHEMA_ERROR
    err = capsys.readouterr().err
    assert "dubious ownership" in err, "git's own reason was swallowed"
    assert "is not a git work tree" not in err, "guessed the wrong cause"


def test_a_real_work_tree_is_accepted(wiz, tmp_path, monkeypatch):
    """The guard must not refuse the case it exists to allow."""
    ws = _seed_workspace(tmp_path)
    calls = []

    class _R:
        returncode = 0
        stdout = "true\n"
        stderr = ""

    def _run(cmd, **k):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(subprocess, "run", _run)
    assert wiz.cmd_reset(_reset_args(wiz, ws, True)) == wiz.EXIT_OK
    assert any("--is-inside-work-tree" in c for c in calls)


def test_a_malformed_identity_file_is_a_clean_exit_not_a_traceback(wiz, tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """`resolve_audience` catches its own SchemaError and sys.exits, so this
    arrives as SystemExit(1) rather than through main's handler. Either way it
    is an exit code with a message, which is what the F2 fix was for: before
    it, `[]` raised AttributeError from `data.get`."""
    (tmp_path / ".workspace-identity.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        wiz.main(["--status"])
    assert exc.value.code == wiz.EXIT_SCHEMA_ERROR
    assert "not an object" in capsys.readouterr().err


def test_the_audience_is_resolved_inside_the_handler():
    """Placement, not a fix: a raiser belongs inside the guarded region."""
    code = _code("apply-wizard-answers.py")
    start = code.index("def main(")
    body = code[start:code.index("def _depends_on_satisfied(", start)]
    assert body.index("try:") < body.index("resolve_audience(args, workspace_root)")
