"""Five defects in `scripts/apply-wizard-answers.py`, all about a write.

Found by the 2026-08-24 engine audit campaign (shard `scripts-00-p2`), verified
still present on 2026-09-02, fixed the same day. The script's whole contract is
"the operator knows what was written", and each of these breaks it in a
different direction.

1. `_upsert_env_line` promised in writing that a control character is REFUSED
   and refused three of them. `str.splitlines()`, which its own read side uses,
   breaks on seven more, so a value holding `\\x0b` was written as one line and
   read back as two: an orphan fragment defining nothing, permanently, in the
   operator's `.env`.

2. `cmd_reset` compared `git status --porcelain` paths against `_git_rel`
   spellings without undoing git's C quoting. With `core.quotepath` at its
   default, a path holding a non-ASCII byte arrives as `"caf\\303\\251.md"`, so
   a file the wizard itself wrote was classified as somebody else's uncommitted
   work and `--reset` refused. That is the always-failing gate the code's own
   comment says was fixed once already, reached through quoting instead.

3. A secret answer is stored as its `_mask_secret` stub, which carries the
   credential's REAL last four characters, and both rich-template context
   builders copied it in under the secret's own question id. `.env` is
   gitignored; the rendered document is not.

4. `main` dispatched on the first action flag and returned, so `--status
   --reset` silently discarded a destructive reset and `--skip a --question b`
   silently discarded the skip. argparse had no reason to complain.

5. Four `tmp.write_text` / `os.replace` pairs left `<name>.tmp` behind forever
   when the write failed partway. Nothing cleans it and `cmd_all`'s planning
   glob does not match it. At the fourth site that file is `.env.tmp`, holding
   the credential.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "apply-wizard-answers.py"


def _apply(name="apply_write_defects"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. Every character the read side splits on is refused
# ============================================================

# Verified against CPython rather than copied from a table: each of these makes
# `"a<ch>b".splitlines()` return two elements.
SPLITTERS = ["\r", "\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85",
             "\u2028", "\u2029"]


@pytest.mark.parametrize("ch", SPLITTERS, ids=[repr(c) for c in SPLITTERS])
def test_a_dotenv_value_holding_a_line_splitter_is_refused(ch, tmp_path):
    """The guard has to cover the whole set its own reader splits on.

    The pre-fix guard listed `\\r`, `\\n` and `\\x00`, so eight of these ten
    were written verbatim into a file the next `_upsert_env_line` reads with
    `splitlines()`.
    """
    mod = _apply()
    assert ("a" + ch + "b").splitlines() == ["a", "b"], (
        f"{ch!r} is in this list because CPython splits on it; if that changed, "
        "the list is what needs revisiting, not the guard")
    env = tmp_path / ".env"
    with pytest.raises(mod.SchemaError):
        mod._upsert_env_line(env, "SOME_KEY", "abc" + ch + "def")


def test_a_dotenv_value_holding_a_nul_is_refused(tmp_path):
    """NUL does not split a line, and is refused for its own reasons."""
    mod = _apply("apply_nul")
    with pytest.raises(mod.SchemaError):
        mod._upsert_env_line(tmp_path / ".env", "SOME_KEY", "abc\x00def")


def test_a_written_dotenv_value_still_reads_back_as_one_line(tmp_path):
    """The property behind the guard, stated as the corruption it prevents.

    A refused value can never reach the file, so the file the writer produces
    always holds exactly as many lines as it wrote.
    """
    mod = _apply("apply_roundtrip")
    env = tmp_path / ".env"
    mod._upsert_env_line(env, "FIRST_KEY", "plain-value")
    mod._upsert_env_line(env, "SECOND_KEY", "another-value")
    lines = [ln for ln in env.read_text(encoding="utf-8").splitlines() if ln]
    assert lines == ["FIRST_KEY=plain-value", "SECOND_KEY=another-value"], lines


def test_a_tab_is_still_accepted(tmp_path):
    """The anchor. A tab is a control character and `splitlines()` keeps it.

    Widening the guard to every non-printable character would refuse a paste
    that corrupts nothing, so the guard is scoped to line breakers and the
    docstring no longer claims more.
    """
    mod = _apply("apply_tab")
    env = tmp_path / ".env"
    mod._upsert_env_line(env, "TABBED_KEY", "a\tb")
    assert env.read_text(encoding="utf-8").strip() == "TABBED_KEY=a\tb"


# ============================================================
# 2. Git's own quoting is undone before the compare
# ============================================================


def test_a_porcelain_path_with_an_octal_escape_is_decoded(tmp_path):
    """The unit: what git emits for a non-ASCII path becomes the real path."""
    mod = _apply("apply_quotepath")
    assert mod._unquote_porcelain_path(r'"docs/caf\303\251.md"') == "docs/café.md"
    assert mod._unquote_porcelain_path("docs/plain.md") == "docs/plain.md", (
        "an unquoted path must pass through untouched")
    assert mod._unquote_porcelain_path(r'"a\"b.md"') == 'a"b.md'
    assert mod._unquote_porcelain_path('"') == '"', (
        "a lone quote is not a quoted path and must not be sliced away")


def test_git_really_quotes_a_non_ascii_path(tmp_path):
    """The premise, measured, not assumed.

    If a future git stopped quoting by default, the decoder becomes dead weight
    rather than a fix, and this is the test that would say so.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    (repo / "café.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                         capture_output=True, text=True, check=True)
    assert r"caf\303\251.md" in out.stdout, out.stdout
    mod = _apply("apply_quotepath_live")
    rel = mod._unquote_porcelain_path(out.stdout.splitlines()[0][3:].strip())
    assert rel == "café.md"


def test_a_rename_line_is_still_read_from_its_new_side():
    """The anchor. Splitting on the arrow must survive the reordering."""
    mod = _apply("apply_rename")
    line = 'R  "old\\303\\251.md" -> "new\\303\\251.md"'
    rel = line[3:].strip().split(" -> ")[-1]
    assert mod._unquote_porcelain_path(rel) == "newé.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


@pytest.fixture
def accented_repo(tmp_path):
    """A wizard workspace whose ONLY templated file has a non-ASCII name.

    Same shape as `tests/test_wizard_reset_is_reachable.py`'s fixture, with the
    one change that makes git quote the path in `--porcelain` output.
    """
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "café.md").write_text("Welcome to {COMPANY}.\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wizard-questions.yaml").write_text(
        "- id: company_name\n"
        "  audience: [public]\n"
        "  type: placeholder\n"
        "  required: true\n"
        '  prompt: "Company name?"\n'
        '  example: "Acme"\n'
        "  target:\n"
        '    placeholder: "{COMPANY}"\n'
        '    files: ["café.md"]\n',
        encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


class _ResetArgs:
    def __init__(self, root, force=False):
        self.workspace_root = root
        self.force = force
        self.resolved_audience = "public"


def test_reset_runs_on_its_own_output_when_the_filename_is_not_ascii(
        accented_repo, capsys):
    """The call site, not just the decoder.

    This is the defect end to end: the wizard writes `café.md`, git reports it
    as `"caf\\303\\251.md"`, and before the fix the compare against the
    `_git_rel` spelling could never match, so `--reset` without `--force`
    classified the wizard's own output as somebody else's uncommitted work and
    refused. A test that only exercises `_unquote_porcelain_path` leaves the
    wiring free to be reverted, which a mutation run confirmed on 2026-09-02.
    """
    mod = _apply("apply_reset_accented")
    target = accented_repo / "café.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("{COMPANY}", "Acme"),
        encoding="utf-8")
    mod.save_answers(accented_repo, {
        "schema_version": mod.SCHEMA_VERSION, "audience": "public",
        "answers": {"company_name": {
            "value": "Acme", "status": "answered",
            "answered_at": "2026-09-02T00:00:00+00:00"}}})

    assert r"caf\303\251.md" in _git(accented_repo, "status", "--porcelain").stdout, (
        "this git does not quote the path, so the test is no longer testing "
        "the condition it was written for")

    rc = mod.cmd_reset(_ResetArgs(accented_repo))
    err = capsys.readouterr().err
    assert rc == mod.EXIT_OK, (
        f"reset refused its own output over git's quoting: {err}")
    assert target.read_text(encoding="utf-8") == "Welcome to {COMPANY}.\n"


def test_reset_still_refuses_an_unrelated_dirty_file_with_a_quoted_name(
        accented_repo, capsys):
    """The anchor. Decoding must not turn the gate into a rubber stamp."""
    mod = _apply("apply_reset_accented_guard")
    stranger = accented_repo / "réadme.md"
    stranger.write_text("hand written\n", encoding="utf-8")
    _git(accented_repo, "add", "-A")
    _git(accented_repo, "commit", "-q", "-m", "add stranger")
    stranger.write_text("edited by hand\n", encoding="utf-8")

    rc = mod.cmd_reset(_ResetArgs(accented_repo))
    err = capsys.readouterr().err
    assert rc == mod.EXIT_SCHEMA_ERROR, "reset ran over an unrelated hand edit"
    assert stranger.read_text(encoding="utf-8") == "edited by hand\n"
    assert "uncommitted changes" in err


# ============================================================
# 3. A masked secret never reaches a rendered document
# ============================================================


def test_a_masked_secret_is_kept_out_of_the_template_context():
    """The mask carries the credential's real last four characters.

    `.env` is gitignored. The document a rich question renders is not, so a
    template naming the secret's question id put those four into a file that
    gets committed and pushed.
    """
    mod = _apply("apply_ctx")
    answers = {
        "api_token": {"value": "************9f2c", "env_written": True,
                      "status": "answered"},
        "company_name": {"value": "Northwind Freight", "status": "answered"},
    }
    ctx = mod._template_context(answers)
    assert "api_token" not in ctx, (
        "the secret's mask reached the render context under its own id")
    assert ctx["company_name"] == "Northwind Freight", (
        "an ordinary answer must still render")
    assert "generated_date" in ctx


def test_both_render_paths_build_their_context_the_same_way():
    """One implementation, because there were two byte-identical copies.

    `cmd_question`'s rich branch and `_plan_question` each had their own loop, so
    a fix landing in one of two copies is the shape this repository keeps
    finding. The source is asked, because a behavioural test can only reach the
    copy it drives.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count("def _template_context(") == 1
    assert source.count("_template_context(") == 3, (
        "one definition and exactly two callers; a third render path that "
        "builds its own context is how the secret gets back in")
    assert source.count('ctx[aid] = aentry["value"]') == 1, (
        "the context loop appears more than once in the file, so a render path "
        "is building its own again and the secret gets back in through it")


# ============================================================
# 4. An action the operator asked for is never dropped in silence
# ============================================================

CONFLICTS = [
    ("--status", "--reset"),
    ("--skip", "some_id", "--all"),
    ("--question", "other_id", "--all"),
    ("--question", "other_id", "--status"),
    ("--skip", "some_id", "--question", "other_id"),
]


@pytest.mark.parametrize("argv", CONFLICTS, ids=[" ".join(c) for c in CONFLICTS])
def test_two_action_flags_are_refused_rather_than_one_being_dropped(argv):
    """argparse says no, loudly, instead of the dispatch discarding the loser.

    `--status --reset` is the one that matters most: the reset is destructive
    and was dropped while a status printed like the whole answer.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=REPO, capture_output=True, text=True,
    )
    # argparse's own usage exit, which is 2 here because `apply-wizard-answers`
    # does not override `parser.error`. Asserting only "non-zero" would pass on
    # the unrelated ceo-master abort this repository produces for any wizard
    # invocation, so the code and the message are both pinned.
    assert result.returncode == 2, (
        f"{' '.join(argv)} exited {result.returncode}; a flag conflict has to "
        f"be an argparse usage refusal, not a run that drops one action: "
        f"{result.stderr[:300]}")
    assert "not allowed with" in result.stderr, result.stderr


@pytest.mark.parametrize("argv", [
    ["--status"],
    ["--status", "--audience", "public"],
    ["--all", "--check"],
    ["--reset", "--force"],
])
def test_a_modifier_beside_an_action_is_still_accepted(argv):
    """The anchor. `--check`, `--audience`, `--force` and `--value-from-stdin`
    qualify an action rather than being one, so they stay outside the group.

    Only argparse's own usage refusal is ruled out here; the run may still fail
    for want of a question bank in this repository, which is a different exit.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=REPO, capture_output=True, text=True,
    )
    assert "not allowed with" not in result.stderr, (
        f"{' '.join(argv)} was refused as a flag conflict: {result.stderr}")


# ============================================================
# 5. A failed write leaves no temp file behind
# ============================================================


def test_a_failed_substitution_write_leaves_no_orphan_temp_file(
        tmp_path, monkeypatch):
    """The full-disk case, at the seam that fails.

    `write_text` writes what it can and raises, exactly as a full filesystem
    does. Before the fix `<name>.md.tmp` stayed on disk forever: nothing cleans
    it, and `cmd_all`'s planning glob does not match it.
    """
    mod = _apply("apply_tmp_subst")
    target = tmp_path / "voice.md"
    target.write_text("PLACEHOLDER here\n", encoding="utf-8")

    real_write_text = Path.write_text

    def failing_write_text(self, data, *args, **kwargs):
        if self.suffix == ".tmp":
            real_write_text(self, data[:3], *args, **kwargs)
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    with pytest.raises(OSError):
        mod._apply_placeholder_substitution(target, {"PLACEHOLDER": "value"})

    monkeypatch.undo()
    assert list(tmp_path.glob("*.tmp")) == [], (
        f"orphan temp file left behind: {list(tmp_path.glob('*.tmp'))}")
    assert target.read_text(encoding="utf-8") == "PLACEHOLDER here\n", (
        "the target must be untouched when the write never completed")


def test_a_failed_env_write_leaves_no_temp_file_holding_the_secret(
        tmp_path, monkeypatch):
    """The site the audit did not name, and the one that costs the most.

    `.env.tmp` is created at 0600 with the credential already in it. A failure
    between that write and the rename used to leave the secret on disk under a
    name nothing ever looks at. The `os.open` comment in `_upsert_env_line`
    already records finding such a leftover from an earlier crashed run.
    """
    mod = _apply("apply_tmp_env")
    env = tmp_path / ".env"

    def failing_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mod.os, "replace", failing_replace)

    with pytest.raises(OSError):
        mod._upsert_env_line(env, "SOME_KEY", "sk-fixture-value-0000")

    monkeypatch.undo()
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"a temp file holding the secret survived: {leftovers}"


def test_every_temp_write_in_the_script_is_cleaned_up():
    """The other two sites, which no unit can reach without a question bank.

    `cmd_question`'s rich branch and `cmd_all`'s write loop use the identical
    pair. Asking the source is the honest way to cover them here, and it says
    what it checks: every `.tmp` name built in this file is followed by a
    `finally` that unlinks it.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    built = source.count('.with_suffix(')
    unlinked = source.count("tmp.unlink(missing_ok=True)")
    assert built >= 4, f"only {built} temp names found; the shape changed"
    assert unlinked == 4, (
        f"{unlinked} of the temp writes clean up after themselves; there are "
        "four sites and each needs its own finally")
