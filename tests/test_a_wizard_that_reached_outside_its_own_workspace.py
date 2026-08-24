"""A setup wizard that wrote, read and deleted past the workspace it was setting up.

Covers the k3 audit shard `scripts-00-p2` for `scripts/apply-wizard-answers.py`
and `scripts/archive-transcripts.py`. The sibling file
`test_a_reset_that_deleted_what_it_promised_to_restore.py` covers the earlier
pass over the same two scripts; nothing here duplicates it.

*Containment enforced on two paths of three.* The file states the invariant
twice in prose, "A question bank may not reach past the workspace root", and
`cmd_question` and `cmd_reset` both routed a rich `output` through the guard
that enforces it. `_plan_question` did not: it joined `workspace_root /
out_rel` bare, so a bank with `output: "../../tmp/escape.md"` was REFUSED by
`--question` and silently written outside the workspace by `--all` -- and
`cmd_reset`, which does check, would then refuse to clean up what `--all`
created. An absolute `out_rel` was worse: `workspace_root / "/abs"` discards
the root entirely under pathlib, and `--all --check` then raised an uncaught
ValueError out of a dry run.

*The read side had no containment at all.* `resolve_read_path` selects between
`root/<rel>` and `root/corporate/<rel>` purely on `.exists()`, and
`target.template` reaches it straight from the bank. `template:
"../../etc/hostname"` was read and RENDERED INTO the output document. The
traversal also skipped the root-then-corporate resolution the function exists
to perform, since `..` climbs out before the fallback is consulted.

*An exception class nothing caught.* `UnicodeDecodeError` subclasses
`ValueError`, not `OSError`. `main` catches SchemaError / StateWriteError /
OSError and `cmd_all`'s planner catches (SchemaError, OSError, KeyError), so
not one read site was covered: a question bank, a `.workspace-identity.json`,
a `.env`, a template or a rendered output holding a single non-UTF-8 byte
produced a raw traceback, including out of the read-only `--all --check`. The
file already stated the intended convention in three places, so the read sites
contradicted its own contract rather than expressing a different one.

*Two spellings of the same path, one of them fatal.* `cmd_reset` fed
`str(path.relative_to(root))` to git on both sides of the conversation.
`str()` yields backslashes on Windows and `git status --porcelain` reports
forward slashes on every platform, so the dirty check saw every file the
wizard had just written as foreign work and refused. The second consumer is
the damaging one: the revert loop passes the same string to `git ls-files
--error-unmatch` and treats a non-zero exit as "untracked", whose branch is
`path.unlink()`. A spelling git will not match routes a TRACKED file, which
`git checkout --` would have restored, into the delete branch. The lexical
`..` case reproduces this on Linux too, and is what the git test below drives.

*A guard that rejected the wrong half of its own rule.* The placeholder-token
check used `fullmatch`, so it refused an answer that IS a token and passed one
that CONTAINS one. `--question` applies a single mapping and the token lands
verbatim; `--all` merges every answered question's mapping per file and applies
them in sequence, so a later question substitutes into the text an earlier
answer inserted. Identical answers, two different files, success reported both
times. `--all` also never called the guard at all.

*A diary entry that overruled the operation it described.* `_log` runs after
the secret is durably recorded and one line before the success JSON. An OSError
there reached `main`'s handler, which printed "the answer went unrecorded" and
returned EXIT_FILE_WRITE_ERROR -- false on every count.

*A torn archive that no later run could see.* `archive-transcripts.py` writes
`<name>.jsonl.gz.tmp` and replaces. On a mid-copy failure the tmp stayed, and
`status()` globs `*.jsonl.gz`, which does not match it. Every failed run left a
compressed partial transcript in the DATA overlay permanently, invisible to the
command that reports what is archived.

No test here runs git against the real repository, reads the operator's
transcripts, writes a real credential, or reaches any path outside tmp_path.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAVE_GIT = shutil.which("git") is not None


def _load(name: str, modname: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(modname, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _code(name: str) -> str:
    """Source minus whole-line comments; each fix left one quoting the old code."""
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def wiz():
    return _load("apply-wizard-answers.py", "wizard_outside_mod")


@pytest.fixture(scope="module")
def arch():
    return _load("archive-transcripts.py", "archive_outside_mod")


# ============================================================
# Bank / workspace builders
# ============================================================

def _bank(tmp_path: Path, body: str) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "wizard-questions.yaml").write_text(body, encoding="utf-8")


def _rich_bank(tmp_path: Path, output: str, template: str = "tpl.md") -> None:
    _bank(tmp_path,
          "- id: bio\n"
          "  audience: [public]\n"
          "  type: rich\n"
          "  required: true\n"
          "  prompt: Bio?\n"
          "  example: A bio\n"
          "  target:\n"
          f"    template: {template!r}\n"
          f"    output: {output!r}\n")
    # Only for a template the bank is allowed to reach; the escape cases point
    # at a file the test creates itself, or at nothing.
    if not Path(template).is_absolute() and ".." not in template:
        tpl = tmp_path / template
        tpl.parent.mkdir(parents=True, exist_ok=True)
        tpl.write_text("Bio: {{ bio_draft }}\n", encoding="utf-8")


def _answers(tmp_path: Path, answers: dict) -> None:
    setup = tmp_path / ".setup"
    setup.mkdir(exist_ok=True)
    (setup / "answers.json").write_text(
        json.dumps({"schema_version": 1, "answers": answers}), encoding="utf-8")


def _rich_answered() -> dict:
    return {"bio": {"status": "answered", "value": "v",
                    "draft": "a draft", "draft_approved": True}}


def _args(wiz, tmp_path, **kw):
    import argparse
    ns = argparse.Namespace()
    ns.workspace_root = tmp_path
    ns.resolved_audience = "public"
    ns.check = False
    ns.force = False
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _run_main(wiz, tmp_path, monkeypatch, argv):
    monkeypatch.chdir(tmp_path)
    return wiz.main(argv)


# ============================================================
# 1. The rich output that escaped through --all only
# ============================================================

ESCAPES = ["../../tmp/escape.md", "../outside.md", "sub/../../../etc/x.md"]


@pytest.mark.parametrize("escape", ESCAPES)
def test_planning_refuses_an_escaping_rich_output(wiz, tmp_path, escape):
    """`_plan_question` is the path that had no guard."""
    _rich_bank(tmp_path, escape)
    q = wiz.load_questions(tmp_path)[0]
    with pytest.raises(wiz.SchemaError) as exc:
        wiz._plan_question(tmp_path, q, _rich_answered()["bio"],
                           _rich_answered(), "public")
    assert "outside the workspace" in str(exc.value)


def _escape_target(tmp_path: Path) -> tuple[str, Path]:
    """A bank output one level above the workspace, named for THIS test.

    The escape lands outside tmp_path by construction, so it lands somewhere
    pytest does not clean between runs. A fixed name would let one run's
    artifact decide the next run's verdict -- and it did: mutation E1 reverted
    the fix, the wizard wrote the escape for real, and the file then failed the
    next unmutated run of a different test.
    """
    name = f"escape-{tmp_path.name}.md"
    return f"../{name}", tmp_path.parent / name


@pytest.mark.parametrize("argv", [["--all"], ["--all", "--check"]])
def test_every_command_refuses_the_same_escaping_bank(wiz, tmp_path,
                                                      monkeypatch, capsys, argv):
    """The point of the fix: one bank, one answer, one verdict.

    `--question` refused and `--all` wrote. `--all --check` did neither; it
    raised ValueError out of a command contracted to write nothing.
    """
    rel, landed = _escape_target(tmp_path)
    _rich_bank(tmp_path, rel)
    _answers(tmp_path, _rich_answered())
    rc = _run_main(wiz, tmp_path, monkeypatch, argv)
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert not landed.exists()


def test_the_single_question_path_refuses_it_too(wiz, tmp_path, monkeypatch,
                                                 capsys):
    """The path that was already right. It is the reference the others matched."""
    rel, landed = _escape_target(tmp_path)
    _rich_bank(tmp_path, rel)
    _answers(tmp_path, _rich_answered())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"value": "v", "draft": "a draft", "draft_approved": True})))
    rc = _run_main(wiz, tmp_path, monkeypatch,
                   ["--question", "bio", "--value-from-stdin"])
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert not landed.exists()


def test_an_absolute_output_does_not_discard_the_workspace_root(wiz, tmp_path,
                                                                monkeypatch,
                                                                capsys):
    """`workspace_root / "/abs"` is `/abs`. pathlib drops the left side."""
    target = tmp_path.parent / "absolute-escape.md"
    _rich_bank(tmp_path, str(target))
    _answers(tmp_path, _rich_answered())
    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert not target.exists()


def test_an_inside_output_still_writes(wiz, tmp_path, monkeypatch, capsys):
    """The guard must not refuse the ordinary case it sits in front of."""
    _rich_bank(tmp_path, "docs/bio.md")
    _answers(tmp_path, _rich_answered())
    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_OK
    assert (tmp_path / "docs" / "bio.md").read_text(encoding="utf-8") == \
        "Bio: a draft\n"


# ============================================================
# 2. The template read that had no containment
# ============================================================

@pytest.mark.parametrize("escape", ["../../etc/hostname", "../outside.md",
                                    "a/../../b.md"])
def test_a_template_may_not_be_read_from_outside_the_workspace(wiz, tmp_path,
                                                               escape):
    with pytest.raises(wiz.SchemaError) as exc:
        wiz.resolve_read_path(tmp_path, escape)
    assert "outside the workspace" in str(exc.value)


def test_an_absolute_template_is_refused(wiz, tmp_path):
    with pytest.raises(wiz.SchemaError):
        wiz.resolve_read_path(tmp_path, "/etc/hostname")


def test_a_host_file_never_reaches_the_rendered_output(wiz, tmp_path,
                                                       monkeypatch, capsys):
    """The whole point: the template's CONTENTS were copied into a workspace file."""
    secret_ish = tmp_path.parent / "host-file.md"
    secret_ish.write_text("HOST-FILE-CONTENTS\n", encoding="utf-8")
    _rich_bank(tmp_path, "docs/bio.md", template="../host-file.md")
    _answers(tmp_path, _rich_answered())
    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_SCHEMA_ERROR
    out = tmp_path / "docs" / "bio.md"
    assert not out.exists() or "HOST-FILE-CONTENTS" not in out.read_text(
        encoding="utf-8")


def test_the_root_layout_still_resolves(wiz, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "x.md").write_text("root", encoding="utf-8")
    assert wiz.resolve_read_path(tmp_path, "config/x.md") == tmp_path / "config/x.md"


def test_the_corporate_fallback_still_resolves(wiz, tmp_path):
    """An exec workspace keeps config/ under corporate/. That path must survive."""
    corp = tmp_path / "corporate" / "config"
    corp.mkdir(parents=True)
    (corp / "x.md").write_text("corp", encoding="utf-8")
    assert wiz.resolve_read_path(tmp_path, "config/x.md") == corp / "x.md"


def test_a_missing_path_still_returns_the_primary(wiz, tmp_path):
    """Callers print this one, so it must keep pointing at the expected location."""
    assert wiz.resolve_read_path(tmp_path, "config/gone.md") == \
        tmp_path / "config" / "gone.md"


def test_a_dotdot_that_stays_inside_is_allowed(wiz, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    assert wiz.resolve_read_path(tmp_path, "a/../b.md").resolve() == \
        (tmp_path / "b.md").resolve()


def test_one_definition_enforces_containment(wiz):
    """Three call sites, one rule. The third site is how the read hole opened."""
    code = _code("apply-wizard-answers.py")
    assert code.count("def _require_inside(") == 1
    assert code.count("_require_inside(") >= 4          # definition + 3 uses


# ============================================================
# 3. UnicodeDecodeError: a ValueError no handler expected
# ============================================================

BAD_BYTES = b"\xff\xfe not utf-8 \xff"


def test_unicode_decode_error_is_not_an_oserror():
    """The premise. If this ever changes, most of this section is moot."""
    assert issubclass(UnicodeDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, OSError)


def test_read_text_names_the_file_it_could_not_decode(wiz, tmp_path):
    """UnicodeDecodeError carries the codec, the byte and the offset, never a path."""
    bad = tmp_path / "bank.yaml"
    bad.write_bytes(BAD_BYTES)
    with pytest.raises(wiz.SchemaError) as exc:
        wiz._read_text(bad)
    assert str(bad) in str(exc.value)
    assert "UTF-8" in str(exc.value)


def test_a_non_utf8_question_bank_is_a_schema_error(wiz, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_bytes(BAD_BYTES)
    with pytest.raises(wiz.SchemaError):
        wiz.load_questions(tmp_path)


def test_a_non_utf8_identity_file_is_a_schema_error(wiz, tmp_path):
    (tmp_path / ".workspace-identity.json").write_bytes(BAD_BYTES)
    with pytest.raises(wiz.SchemaError):
        wiz.detect_audience(tmp_path)


def test_a_non_utf8_env_is_a_schema_error(wiz, tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(BAD_BYTES)
    with pytest.raises(wiz.SchemaError):
        wiz._upsert_env_line(env, "SOME_KEY", "placeholder-value")


def test_a_non_utf8_env_does_not_traceback_out_of_planning(wiz, tmp_path,
                                                            capsys):
    """The secret branch reads `.env` to warn about a missing key."""
    (tmp_path / ".env").write_bytes(BAD_BYTES)
    _bank(tmp_path,
          "- id: key\n"
          "  audience: [public]\n"
          "  type: secret\n"
          "  required: true\n"
          "  prompt: Key?\n"
          "  example: abc\n"
          "  target:\n"
          "    env_var: SOME_KEY\n")
    _answers(tmp_path, {"key": {"status": "answered", "value": "****",
                                "env_written": True}})
    rc = wiz.cmd_all(_args(wiz, tmp_path))
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert "not valid UTF-8" in capsys.readouterr().err


def test_a_non_utf8_template_does_not_traceback_out_of_all(wiz, tmp_path,
                                                            capsys):
    _rich_bank(tmp_path, "docs/bio.md")
    (tmp_path / "tpl.md").write_bytes(BAD_BYTES)
    _answers(tmp_path, _rich_answered())
    rc = wiz.cmd_all(_args(wiz, tmp_path))
    assert rc == wiz.EXIT_SCHEMA_ERROR


def test_a_non_utf8_output_does_not_crash_the_dry_run(wiz, tmp_path, capsys):
    """`--check` writes nothing, so it is the last place a crash is acceptable."""
    _rich_bank(tmp_path, "docs/bio.md")
    _answers(tmp_path, _rich_answered())
    out = tmp_path / "docs"
    out.mkdir()
    (out / "bio.md").write_bytes(BAD_BYTES)
    rc = wiz.cmd_all(_args(wiz, tmp_path, check=True))
    assert rc == wiz.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    # Undecodable is not equal to the rendered text, so it counts as pending.
    assert payload["would_update"] == 1
    assert (out / "bio.md").read_bytes() == BAD_BYTES, "the dry run wrote"


def test_a_non_utf8_output_is_overwritten_not_crashed_into(wiz, tmp_path,
                                                            capsys):
    _rich_bank(tmp_path, "docs/bio.md")
    _answers(tmp_path, _rich_answered())
    out = tmp_path / "docs"
    out.mkdir()
    (out / "bio.md").write_bytes(BAD_BYTES)
    rc = wiz.cmd_all(_args(wiz, tmp_path))
    assert rc == wiz.EXIT_OK
    assert (out / "bio.md").read_text(encoding="utf-8") == "Bio: a draft\n"


def test_the_whole_command_exits_one_rather_than_tracebacking(wiz, tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """End to end through `main`, which is where the traceback surfaced."""
    _rich_bank(tmp_path, "docs/bio.md")
    (tmp_path / "tpl.md").write_bytes(BAD_BYTES)
    _answers(tmp_path, _rich_answered())
    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert "Traceback" not in capsys.readouterr().err


def test_the_substitution_sweep_still_skips_rather_than_refuses(wiz, tmp_path):
    """A deliberate carve-out, not an oversight.

    `_apply_placeholder_substitution` walks whatever the glob matched, so an
    undecodable file there is skipped. Only the named single-file reads raise.
    """
    binary = tmp_path / "x.md"
    binary.write_bytes(BAD_BYTES)
    assert wiz._apply_placeholder_substitution(binary, {"{A}": "v"}) is False


def test_text_or_none_reports_undecodable_as_absent(wiz, tmp_path):
    bad = tmp_path / "b.md"
    bad.write_bytes(BAD_BYTES)
    assert wiz._text_or_none(bad) is None
    good = tmp_path / "g.md"
    good.write_text("hello", encoding="utf-8")
    assert wiz._text_or_none(good) == "hello"


# ============================================================
# 4. The two path spellings git was given
# ============================================================

def test_git_rel_always_uses_forward_slashes(wiz, tmp_path):
    assert wiz._git_rel(tmp_path, tmp_path / "docs" / "setup.md") == "docs/setup.md"
    assert "\\" not in wiz._git_rel(tmp_path, tmp_path / "a" / "b" / "c.md")


def test_the_forward_slash_is_asserted_on_the_source_not_the_result(wiz):
    """This one has to read the code, and here is why.

    On POSIX `str(PurePath(...))` and `.as_posix()` return the same string for
    every input, so no value this suite can compute tells them apart -- the
    test above passes with either. The separator only diverges on
    `WindowsPath`, which cannot be constructed on Linux. A mutation swapping
    `.as_posix()` for `str()` therefore survives every behavioural check while
    reintroducing the Windows defect in full.

    So the claim is pinned where it is decidable: the spelling in the source.
    """
    body = _code("apply-wizard-answers.py").split("def _git_rel(")[1].split(
        "\ndef ")[0]
    assert ".as_posix()" in body
    assert ".resolve()" in body


def test_git_rel_normalises_a_dotdot_segment(wiz, tmp_path):
    """`relative_to` is lexical, so `docs/../x.md` survived it verbatim."""
    assert wiz._git_rel(tmp_path, tmp_path / "docs" / ".." / "x.md") == "x.md"


def test_reset_no_longer_hands_git_an_os_native_path(wiz):
    """Scoped to `cmd_reset`. The `planned` list in `cmd_all` is display JSON,
    never handed to git, so its OS-native spelling is not this defect."""
    code = _code("apply-wizard-answers.py")
    body = code.split("def cmd_reset(")[1]
    assert ".relative_to(workspace_root)" not in body
    assert body.count("_git_rel(workspace_root,") == 2


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=T",
         *args],
        cwd=repo, capture_output=True, text=True, check=False)


@pytest.mark.skipif(not HAVE_GIT, reason="git is not installed")
def test_reset_reverts_a_tracked_file_it_reached_by_a_dotdot_path(wiz, tmp_path,
                                                                  capsys):
    """The damaging half of the spelling bug, driven on Linux.

    The revert loop reads a non-zero `git ls-files --error-unmatch` as
    "untracked" and unlinks. Give git a spelling it will not match and a
    tracked file is DELETED where it should have been restored.
    """
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.md"
    tracked.write_text("committed\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    _git(tmp_path, "add", "tracked.md")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    # The bank reaches the same file through a `..` segment.
    _rich_bank(tmp_path, "docs/../tracked.md")
    _git(tmp_path, "add", "config", "tpl.md")
    _git(tmp_path, "commit", "-q", "-m", "bank")
    _answers(tmp_path, _rich_answered())
    tracked.write_text("what the wizard wrote\n", encoding="utf-8")

    rc = wiz.cmd_reset(_args(wiz, tmp_path, force=True))
    assert rc == wiz.EXIT_OK
    assert tracked.exists(), "a tracked file was DELETED instead of reverted"
    assert tracked.read_text(encoding="utf-8") == "committed\n"


@pytest.mark.skipif(not HAVE_GIT, reason="git is not installed")
def test_the_dirty_check_recognises_the_wizards_own_output(wiz, tmp_path,
                                                           capsys):
    """The refusal half. git reports forward slashes; `ours` must match them."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "bio.md"
    target.write_text("committed\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    _rich_bank(tmp_path, "docs/bio.md")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    _answers(tmp_path, _rich_answered())
    target.write_text("what the wizard wrote\n", encoding="utf-8")

    rc = wiz.cmd_reset(_args(wiz, tmp_path, force=False))
    assert rc == wiz.EXIT_OK, capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "committed\n"


@pytest.mark.skipif(not HAVE_GIT, reason="git is not installed")
def test_the_dirty_check_still_refuses_foreign_changes(wiz, tmp_path, capsys):
    """The gate must keep doing the one job it exists for."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "bio.md"
    target.write_text("committed\n", encoding="utf-8")
    stranger = tmp_path / "unrelated.md"
    stranger.write_text("original\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    _rich_bank(tmp_path, "docs/bio.md")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    _answers(tmp_path, _rich_answered())
    stranger.write_text("a hand edit nobody asked the wizard about\n",
                        encoding="utf-8")

    rc = wiz.cmd_reset(_args(wiz, tmp_path, force=False))
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert "unrelated.md" in capsys.readouterr().err
    assert stranger.read_text(encoding="utf-8").startswith("a hand edit")


# ============================================================
# 5. The answer that carried another question's token
# ============================================================

def test_an_answer_that_is_a_token_is_still_refused(wiz):
    """The half `fullmatch` already caught. It must not regress."""
    with pytest.raises(wiz.SchemaError):
        wiz._reject_token_values({"{A}": "{B}"})


def test_an_answer_that_contains_a_token_is_now_refused(wiz):
    """The half `fullmatch` let through, and the one that diverges."""
    with pytest.raises(wiz.SchemaError) as exc:
        wiz._reject_token_values({"{A}": "see {B} for details"})
    assert "{A}" in str(exc.value)


def test_an_ordinary_answer_passes(wiz):
    wiz._reject_token_values({"{COMPANY}": "Acme Robotics", "{CITY}": "Lisbon"})


def test_a_lowercase_brace_is_not_a_token(wiz):
    """The token grammar is uppercase. A stray `{x}` must not be refused."""
    wiz._reject_token_values({"{A}": "the set {x} of things"})


def test_the_all_path_now_runs_the_guard_too(wiz, tmp_path, monkeypatch,
                                             capsys):
    """The report's exact scenario, end to end.

    Q1 -> `{A}`, Q2 -> `{B}`, same target file. Answer Q1 with `x {B} y`.
    `--question` writes `x {B} y`; `--all` merged both mappings and rewrote it
    to `x z y`. Two commands, the same answers, two different files.
    """
    _bank(tmp_path,
          "- id: q1\n"
          "  audience: [public]\n"
          "  type: placeholder\n"
          "  required: true\n"
          "  prompt: One?\n"
          "  example: a\n"
          "  target:\n"
          "    files: ['README.md']\n"
          "    placeholder: '{A}'\n"
          "- id: q2\n"
          "  audience: [public]\n"
          "  type: placeholder\n"
          "  required: true\n"
          "  prompt: Two?\n"
          "  example: b\n"
          "  target:\n"
          "    files: ['README.md']\n"
          "    placeholder: '{B}'\n")
    _answers(tmp_path, {"q1": {"status": "answered", "value": "x {B} y"},
                        "q2": {"status": "answered", "value": "z"}})
    readme = tmp_path / "README.md"
    readme.write_text("{A} and {B}\n", encoding="utf-8")

    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_SCHEMA_ERROR
    assert readme.read_text(encoding="utf-8") == "{A} and {B}\n", \
        "--all rewrote the file it should have refused"


def test_all_still_applies_ordinary_answers(wiz, tmp_path, monkeypatch, capsys):
    """The guard must not break the merge it now sits in front of."""
    _bank(tmp_path,
          "- id: q1\n"
          "  audience: [public]\n"
          "  type: placeholder\n"
          "  required: true\n"
          "  prompt: One?\n"
          "  example: a\n"
          "  target:\n"
          "    files: ['README.md']\n"
          "    placeholder: '{A}'\n")
    _answers(tmp_path, {"q1": {"status": "answered", "value": "Acme"}})
    readme = tmp_path / "README.md"
    readme.write_text("{A} rules\n", encoding="utf-8")
    rc = _run_main(wiz, tmp_path, monkeypatch, ["--all"])
    assert rc == wiz.EXIT_OK
    assert readme.read_text(encoding="utf-8") == "Acme rules\n"


def test_one_definition_guards_both_paths(wiz):
    code = _code("apply-wizard-answers.py")
    assert code.count("def _reject_token_values(") == 1
    assert code.count("_reject_token_values(") == 3      # definition + 2 uses
    assert "_PLACEHOLDER_TOKEN_RE.fullmatch(" not in code


# ============================================================
# 6. The diary entry that overruled its own operation
# ============================================================

def test_a_blocked_log_warns_and_returns(wiz, tmp_path, capsys):
    (tmp_path / ".setup").mkdir()
    (tmp_path / ".setup" / "wizard.log").mkdir()
    wiz._log(tmp_path, "SOME_KEY: [written, len=8]")
    assert "WARNING" in capsys.readouterr().err


def test_a_blocked_log_does_not_turn_a_recorded_secret_into_a_failure(
        wiz, tmp_path, monkeypatch, capsys):
    """`.env` written, answer saved, then the diary fails. That is a success."""
    _bank(tmp_path,
          "- id: key\n"
          "  audience: [public]\n"
          "  type: secret\n"
          "  required: true\n"
          "  prompt: Key?\n"
          "  example: abc\n"
          "  target:\n"
          "    env_var: SOME_KEY\n")
    (tmp_path / ".setup").mkdir()
    (tmp_path / ".setup" / "wizard.log").mkdir()
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"value": "placeholder-not-a-key"})))
    rc = _run_main(wiz, tmp_path, monkeypatch,
                   ["--question", "key", "--value-from-stdin"])
    assert rc == wiz.EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out)["applied"] == ["key"]
    assert "SOME_KEY=" in (tmp_path / ".env").read_text(encoding="utf-8")
    state = json.loads((tmp_path / ".setup" / "answers.json").read_text(
        encoding="utf-8"))
    assert state["answers"]["key"]["status"] == "answered"


def test_the_log_call_is_not_a_bare_append(wiz):
    code = _code("apply-wizard-answers.py")
    body = code.split("def _log(")[1].split("\ndef ")[0]
    assert "except OSError" in body


# ============================================================
# 7. The archive tmp nothing cleaned and nothing could see
# ============================================================

@pytest.fixture
def tree(tmp_path, monkeypatch, arch):
    source = tmp_path / "projects" / "-ws"
    source.mkdir(parents=True)
    dest = tmp_path / "data" / "chronicle" / "transcripts"
    monkeypatch.setattr(arch, "transcript_dir", lambda: source)
    monkeypatch.setattr(arch, "archive_root", lambda: dest)
    return source, dest


def _settled(source: Path, name: str = "s1.jsonl") -> Path:
    path = source / name
    path.write_text(
        json.dumps({"timestamp": "2026-08-01T10:00:00Z", "type": "user"}) + "\n",
        encoding="utf-8")
    old = 1_700_000_000
    os.utime(path, (old, old))
    return path


def test_a_torn_copy_leaves_no_tmp_behind(arch, tree, monkeypatch, capsys):
    """The tmp is named `.jsonl.gz.tmp`, which `status()` does not glob."""
    source, dest = tree
    _settled(source)

    def _boom(src, dst, *a, **kw):
        dst.write(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(arch.shutil, "copyfileobj", _boom)
    counts = arch.archive(now=2_000_000_000)
    assert counts["failed"] == 1
    assert counts["archived"] == 0
    assert list(dest.glob("**/*.tmp")) == [], "a partial archive was left behind"


def test_a_successful_run_leaves_no_tmp_either(arch, tree, capsys):
    source, dest = tree
    _settled(source)
    counts = arch.archive(now=2_000_000_000)
    assert counts["archived"] == 1
    assert list(dest.glob("**/*.tmp")) == []
    assert list(dest.glob("**/*.jsonl.gz"))


def test_the_status_glob_really_does_miss_the_tmp(arch, tree):
    """The reason the leak was invisible, asserted rather than assumed."""
    source, dest = tree
    year = dest / "2026"
    year.mkdir(parents=True)
    (year / "2026-08-01-s1.jsonl.gz.tmp").write_bytes(b"partial")
    assert list(dest.glob("**/*.jsonl.gz")) == []


def test_a_second_run_after_a_torn_copy_still_archives(arch, tree, monkeypatch,
                                                       capsys):
    """Cleanup must not cost the retry the archiver depends on."""
    source, dest = tree
    _settled(source)
    calls = {"n": 0}
    real = arch.shutil.copyfileobj

    def _once(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(5, "I/O error")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(arch.shutil, "copyfileobj", _once)
    assert arch.archive(now=2_000_000_000)["failed"] == 1
    assert arch.archive(now=2_000_000_000)["archived"] == 1
    assert list(dest.glob("**/*.tmp")) == []
