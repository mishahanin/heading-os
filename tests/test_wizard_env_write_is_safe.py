"""Five small defects around the wizard's `.env` write and its error paths.

All from the 2026-08-23 engine audit's LOW band. Individually minor; together
they are the credential-handling surface of the setup wizard, which is the one
place a fresh clone types real keys.

1. **A newline in a secret split `.env` into extra lines.** The value arrives as
   JSON on stdin, where `"\\n"` is a perfectly ordinary character, and
   `_upsert_env_line` wrote `f"{key}={value}"` verbatim. One paste with a
   trailing newline, or a multi-line PEM, silently defined variables nobody
   asked for and corrupted the file for every later reader.

2. **`.env` was world-readable between `os.replace` and `chmod`.** The temp file
   is created with umask defaults, commonly 0644, and the mode was set AFTER the
   rename. The window is short and the file is a credential store. The same
   ordering defect was fixed in `.claude/hooks/session-start.py` and in
   `save_answers` on the same day; this is the third instance.

3. **The Windows ACL comment claimed logging that does not happen.** "Failures
   are logged but non-fatal" sat above a bare `except: pass` and an `icacls` call
   with `check=False` whose exit code is never read. On Windows `.env` can keep
   its inherited ACLs with no signal anywhere.

4. **A corrupt `answers.json` produced a raw traceback.** `json.loads` in
   `load_answers` was unwrapped while the schema check beside it raised a clean
   `SchemaError`, so every subcommand died with `JSONDecodeError` instead of the
   error the file's own contract promises.

5. **`--body-file` on the action queue did the same.** `scripts/action-queue.py`
   documents "Exit codes: 0 ok, 1 request/usage error" and then let a missing
   file raise out of `cmd_edit`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


W = _load("scripts/apply-wizard-answers.py", "wizard_env_under_test")


# --- 1. control characters cannot forge an .env line -------------------------

BAD_VALUES = [
    "abc\nEVIL=1",
    "abc\r\nEVIL=1",
    "abc\rEVIL=1",
    "line1\nline2\nline3",
]


@pytest.mark.parametrize("value", BAD_VALUES, ids=lambda v: repr(v)[:24])
def test_a_newline_in_a_value_is_refused(tmp_path, value):
    env = tmp_path / ".env"
    with pytest.raises(W.SchemaError):
        W._upsert_env_line(env, "KEY", value)
    assert not env.exists() or "EVIL=1" not in env.read_text(encoding="utf-8")


def test_an_ordinary_value_still_writes(tmp_path):
    env = tmp_path / ".env"
    W._upsert_env_line(env, "KEY", "sk-not-a-real-key")
    assert env.read_text(encoding="utf-8").strip() == "KEY=sk-not-a-real-key"


def test_an_existing_key_is_replaced_not_appended(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEY=old\nOTHER=keep\n", encoding="utf-8")
    W._upsert_env_line(env, "KEY", "new")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["KEY=new", "OTHER=keep"]


# --- 2. no world-readable window ---------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_env_is_never_group_or_world_readable(tmp_path, monkeypatch):
    """Patch os.replace to inspect the TEMP file's mode at the moment of the
    rename. Checking the final file cannot see the window; only the temp can."""
    env = tmp_path / ".env"
    seen = {}
    real_replace = os.replace

    def watching(src, dst):
        seen["mode"] = stat.S_IMODE(os.stat(src).st_mode)
        return real_replace(src, dst)

    monkeypatch.setattr(W.os, "replace", watching)
    W._upsert_env_line(env, "KEY", "value")
    assert seen["mode"] & (stat.S_IRGRP | stat.S_IROTH) == 0, (
        f"the temp file was mode {oct(seen['mode'])} at the moment it became "
        f".env; the mode must be set BEFORE the rename, not after"
    )
    assert stat.S_IMODE(env.stat().st_mode) & (stat.S_IRGRP | stat.S_IROTH) == 0


def test_the_existing_env_is_never_truncated_before_the_rename(tmp_path):
    """Atomicity, asserted rather than implied.

    Every test above would pass for a `_upsert_env_line` that opened `.env`
    directly and wrote into it: the final content is the same, and the mode
    test's `os.replace` hook would simply never fire, which reads as a KeyError
    on a dict nobody looks at rather than as a statement about the write. The
    property that matters is that a run killed mid-write cannot leave a
    half-written `.env`, because `load_env` is what every daemon in this
    workspace reads its credentials from and a truncated line is a credential
    that silently becomes absent.

    Observed at the rename: the destination must still hold the PREVIOUS
    document when `os.replace` is called, and the source must not be the
    destination.
    """
    env = tmp_path / ".env"
    env.write_text("OTHER=keep\nKEY=old\n", encoding="utf-8")
    before = env.read_bytes()

    seen = {}
    real_replace = os.replace

    def watching(src, dst):
        seen["dst"] = Path(dst).read_bytes()
        seen["src_text"] = Path(src).read_text(encoding="utf-8")
        seen["same_file"] = Path(src) == Path(dst)
        return real_replace(src, dst)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(W.os, "replace", watching)
        W._upsert_env_line(env, "KEY", "sk-not-a-real-key")
    finally:
        monkeypatch.undo()

    assert seen, "_upsert_env_line never renamed anything into place"
    assert not seen["same_file"], "the temporary and .env are one file"
    assert seen["dst"] == before, (
        "the .env had already been rewritten before the rename, so a run "
        "interrupted at that moment leaves a truncated credential file"
    )
    assert "KEY=sk-not-a-real-key" in seen["src_text"]
    assert env.read_text(encoding="utf-8").splitlines() == [
        "OTHER=keep", "KEY=sk-not-a-real-key"]
    assert not list(tmp_path.glob("*.tmp")), "the temporary outlived the write"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_temp_file_holding_the_secret_is_never_group_or_world_readable(
    tmp_path, monkeypatch
):
    """The other half of the same window, open until 2026-09-01.

    The test above inspects the mode at the moment of the rename, and passes for
    code that writes the secret into a 0644 `.env.tmp` and chmods it a line
    later. That is the identical exposure: same directory, same bytes, a
    different filename, and nothing consults a filename before deciding whether
    it may open a file.

    Measured at the CREATION primitive, because that window is not observable
    any other way: by the time `.env.tmp` exists on disk the chmod has already
    run, so no post-hoc `stat` can tell the two implementations apart. `os.open`
    is therefore hooked, and its absence is itself a failure - a write that does
    not go through a mode-carrying open cannot have controlled the mode.

    `umask` is forced to 0 so a permissive umask is what the assertion sees; a
    developer machine at 0077 would mask the defect and pass either way.
    """
    env = tmp_path / ".env"
    seen: list[int] = []
    real_open = os.open
    old_umask = os.umask(0)

    def watching(path, flags, mode=0o777, **kwargs):
        fd = real_open(path, flags, mode, **kwargs)
        if str(path).endswith(".env.tmp"):
            seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return fd

    try:
        monkeypatch.setattr(W.os, "open", watching)
        W._upsert_env_line(env, "KEY", "sk-not-a-real-key")
    finally:
        os.umask(old_umask)

    assert seen, (
        "the secret was written to .env.tmp through a call that carries no "
        "mode, so it existed at the umask default before any chmod ran"
    )
    assert all(m & (stat.S_IRGRP | stat.S_IROTH) == 0 for m in seen), (
        f"the temp file was mode {[oct(m) for m in seen]} while the credential "
        f"was being written into it"
    )
    assert env.read_text(encoding="utf-8").strip() == "KEY=sk-not-a-real-key"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_leftover_temp_file_from_a_crashed_run_is_re_moded(tmp_path):
    """O_CREAT's mode argument applies only when it CREATES. A `.env.tmp` left
    behind by an interrupted run keeps whatever mode it had, so the fchmod on
    the descriptor is what makes the guard hold in the case that actually
    happens after a crash."""
    env = tmp_path / ".env"
    stale = tmp_path / ".env.tmp"
    stale.write_text("STALE=1\n", encoding="utf-8")
    os.chmod(stale, 0o644)
    W._upsert_env_line(env, "KEY", "sk-not-a-real-key")
    assert stat.S_IMODE(env.stat().st_mode) & (stat.S_IRGRP | stat.S_IROTH) == 0
    assert env.read_text(encoding="utf-8").strip() == "KEY=sk-not-a-real-key"


# --- 3. the Windows comment must match the code ------------------------------

def test_the_windows_branch_does_what_its_comment_says():
    """The branch cannot run here, so read it. `elif` became `if` when the
    chmod moved above the rename, so match on the icacls call instead."""
    src = (ROOT / "scripts" / "apply-wizard-answers.py").read_text(encoding="utf-8")
    start = src.index('if os.name == "nt":', src.index("def _upsert_env_line"))
    end = src.index("\ndef ", start)
    branch = src[start:end]
    assert "icacls" in branch, "the ACL call moved; this guard is unanchored"
    assert "returncode" in branch, (
        "icacls runs with check=False and its exit code is never read, so an "
        "inherited ACL on .env leaves no signal anywhere"
    )
    assert "print(" in branch, (
        "the branch reports nothing; its comment has claimed otherwise since it "
        "was written"
    )


# --- 4. a corrupt answers.json is a clean error ------------------------------

def test_a_corrupt_answers_file_raises_the_documented_error(tmp_path):
    (tmp_path / ".setup").mkdir()
    (tmp_path / ".setup" / "answers.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(W.SchemaError) as exc:
        W.load_answers(tmp_path)
    assert "answers.json" in str(exc.value)


def test_an_answers_file_that_is_not_an_object_is_refused(tmp_path):
    (tmp_path / ".setup").mkdir()
    (tmp_path / ".setup" / "answers.json").write_text("[]", encoding="utf-8")
    with pytest.raises(W.SchemaError):
        W.load_answers(tmp_path)


def test_a_missing_answers_file_is_still_an_empty_skeleton(tmp_path):
    state = W.load_answers(tmp_path)
    assert state["answers"] == {}
    assert state["schema_version"] == W.SCHEMA_VERSION


def test_a_valid_answers_file_still_loads(tmp_path):
    (tmp_path / ".setup").mkdir()
    (tmp_path / ".setup" / "answers.json").write_text(
        json.dumps({"schema_version": W.SCHEMA_VERSION, "answers": {"a": {}}}),
        encoding="utf-8")
    assert W.load_answers(tmp_path)["answers"] == {"a": {}}


# --- 5. --body-file honours the documented exit codes ------------------------

AQ = _load("scripts/action-queue.py", "action_queue_body_file")


class _EditArgs:
    """Every field `cmd_edit` reads, because argparse always supplies all of them.

    `to` was missing until 2026-09-02. `cmd_edit` grew a `--to` option that
    day and its first guard reads `args.to`, so both tests below stopped
    measuring the body-file guard and started raising `AttributeError` two
    lines above it. A double that carries fewer fields than the real Namespace
    does not fail on the field it dropped; it fails on whatever reads that
    field first, and the test's own subject is then never reached.
    """

    def __init__(self, body_file):
        self.id = "whatever"
        self.to = None
        self.subject = None
        self.body_file = body_file


def test_a_missing_body_file_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    """The unreadable-file check must come BEFORE the id lookup.

    Both are usage errors, but the file is the cheaper one to check and the one
    that produced a raw traceback. Reading it first also means the operator is
    told about the typo in the path rather than about the id.
    """
    rc = AQ.cmd_edit(ROOT, tmp_path, _EditArgs(str(tmp_path / "nope.md")))
    assert rc == 1, "cmd_edit did not return the documented usage-error code"
    err = capsys.readouterr().err
    assert "nope.md" in err, err


def test_a_readable_body_file_gets_past_the_check(tmp_path, capsys):
    """The guard must not swallow the normal path: with a real file, the
    command proceeds far enough to fail on the id instead."""
    body = tmp_path / "body.md"
    body.write_text("hello", encoding="utf-8")
    with pytest.raises(SystemExit):
        AQ.cmd_edit(ROOT, tmp_path, _EditArgs(str(body)))
    assert "whatever" in capsys.readouterr().err


def test_the_exit_code_contract_is_still_written_down():
    """The assertion above is only anchored while the contract exists."""
    src = (ROOT / "scripts" / "action-queue.py").read_text(encoding="utf-8")
    assert "Exit codes:" in src and "request/usage error" in src


# --- 6. a bank glob cannot reach outside the workspace -----------------------

def test_a_glob_that_escapes_the_workspace_is_refused(tmp_path):
    """The only thing stopping the substitution engine rewriting files outside
    the workspace was `relative_to` raising an UNCAUGHT ValueError. That is an
    accident, not a containment check, and it reported nothing useful."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.md").write_text("{TOKEN}\n", encoding="utf-8")
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(W.SchemaError) as exc:
        W._collect_matching_files(root, ["../outside/*.md"])
    assert "outside the workspace" in str(exc.value)
    assert (outside / "victim.md").read_text(encoding="utf-8") == "{TOKEN}\n"


def test_an_ordinary_glob_still_matches(tmp_path):
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("x", encoding="utf-8")
    files, _skipped = W._collect_matching_files(root, ["**/*.md"])
    assert [p.name for p in files] == ["a.md"]


# --- 7. timestamps are compared as times ------------------------------------

def test_a_dst_offset_change_does_not_invert_the_order():
    """Answered 01:30-04:00 (05:30 UTC), applied 01:00-05:00 (06:00 UTC): the
    apply is LATER, so nothing is unapplied. String order says the opposite."""
    answered = "2026-11-01T01:30:00-04:00"
    applied = "2026-11-01T01:00:00-05:00"
    assert answered > applied, "the fixture no longer reproduces the string trap"
    assert W._iso_after(answered, applied) is False


def test_a_genuinely_later_answer_is_still_unapplied():
    assert W._iso_after("2026-11-02T09:00:00+00:00",
                        "2026-11-01T09:00:00+00:00") is True


def test_an_unparseable_timestamp_falls_back_rather_than_crashing():
    assert W._iso_after("garbage", "2026-01-01T00:00:00+00:00") is True


# --- 8. a malformed bank fails once, with the question id --------------------

def _bank(tmp_path, body: str) -> Path:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "wizard-questions.yaml").write_text(body, encoding="utf-8")
    return tmp_path


_COMMON = ("  audience: [public]\n  required: true\n"
           '  prompt: "p"\n  example: "e"\n')


@pytest.mark.parametrize("qtype,target,missing", [
    ("placeholder", '    files: ["*.md"]\n', "placeholder"),
    ("placeholder", '    placeholder: "{X}"\n', "files"),
    ("list", '    files: ["*.md"]\n', "placeholders"),
    ("rich", '    template: "t.md"\n', "output"),
    ("secret", '    files: ["*.md"]\n', "env_var"),
])
def test_a_target_missing_a_required_key_is_a_schema_error(tmp_path, qtype, target, missing):
    """`load_questions` checked only that `target` EXISTED. `cmd_question` then
    indexed `target["placeholder"]` and friends directly and caught only
    SchemaError, so a malformed bank entry crashed with a bare KeyError.
    `cmd_all` already caught KeyError while planning, which is what showed the
    gap was unintended rather than a decision."""
    root = _bank(tmp_path, f"- id: q1\n{_COMMON}  type: {qtype}\n  target:\n{target}")
    with pytest.raises(W.SchemaError) as exc:
        W.load_questions(root)
    assert "q1" in str(exc.value) and missing in str(exc.value)


def test_a_target_that_is_not_a_mapping_is_refused(tmp_path):
    root = _bank(tmp_path, f'- id: q1\n{_COMMON}  type: placeholder\n  target: "oops"\n')
    with pytest.raises(W.SchemaError) as exc:
        W.load_questions(root)
    assert "q1" in str(exc.value)


def test_the_real_shipped_bank_still_loads():
    """The validator must not reject the bank the engine actually ships."""
    assert W.load_questions(ROOT), "config/wizard-questions.yaml no longer validates"


# --- 9. a failed state write is reported, not a traceback --------------------

def test_a_state_write_failure_names_the_divergence(tmp_path, monkeypatch):
    """Every cmd_question branch modifies the workspace and saves afterwards,
    so an OSError here means files changed and the answer went unrecorded."""
    def boom(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(W.StateWriteError) as exc:
        W.save_answers(tmp_path, {"answers": {}})
    assert "could not be recorded" in str(exc.value)
    assert "--status" in str(exc.value)


def test_an_ordinary_save_still_works(tmp_path):
    W.save_answers(tmp_path, {"answers": {"a": {}}})
    assert json.loads((tmp_path / ".setup" / "answers.json").read_text())["answers"]
