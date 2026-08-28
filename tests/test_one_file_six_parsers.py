"""One file, six parsers, six answers.

Shard 50 of the engine audit. `.env` is read and written in six places, and
every one of them carried its own hand-rolled grammar. MEASURED 2026-08-28,
before this change, on identical lines:

    line                 load_env       load_gh_token   load_env_key
    KEY="quoted"         quoted         quoted          "quoted"
    KEY='quoted'         quoted         quoted          'quoted'
    <space>KEY=v         v              no match        no match
    export KEY=v         key was        no match        no match
                         "export KEY"

Each disagreement is silent and each has a cost:

  * A `.env` in the dotenv-quoted style that `load_env`'s own docstring
    documents as supported sends the healthchecks.io API key WITH its quotes,
    which comes back 401 with nothing in the message to say why.
  * One leading space in front of `GH_TOKEN=` makes every push report "no
    GH_TOKEN in engine .env" while the token sits in the file. That is a wrong
    cause, not a missing feature: the operator goes looking for a token they
    already have.
  * `export KEY=v` set an environment variable literally named "export KEY" and
    left KEY unset.

The two WRITERS were the same defect from the other side. Both matched a line
with a rule the readers do not use, so a pre-existing indented key was invisible
to them: they appended a duplicate, and afterwards the module's own reader and
`load_env` (setdefault, so the FIRST line wins) disagreed about which value was
live. MEASURED on `  HEALTHCHECKS_API_KEY=OLD`: `write_env` appended `=NEW`,
`load_env_key` answered NEW, and `load_env` answered OLD, so the daemons would
have gone on pinging the old check while the provisioner reported the new one.

Everything now parses through `scripts.utils.paths.parse_env_line`. The test
that matters most is the last one in this file: it feeds every reader the same
bytes and requires them to agree, so a seventh hand-rolled grammar fails here
rather than in production.

Nothing in this file touches the operator's real `.env`, reaches the network, or
runs git.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import git_push  # noqa: E402
from scripts.utils import healthchecks_setup as hs  # noqa: E402
from scripts.utils.paths import (  # noqa: E402
    iter_env_pairs,
    load_env,
    parse_env_line,
    read_env_value,
)


@pytest.fixture(autouse=True)
def _env_sandbox(monkeypatch):
    """Hand every test in this file its own os.environ.

    Several of them call `load_env`, which writes into the real one, and a
    half-loading version of it would write two thousand variables. A test that
    leaks poisons whatever runs next, and `delenv` cannot clean up names the
    test never predicted.
    """
    monkeypatch.setattr(os, "environ", dict(os.environ))


def _wizard():
    """Load apply-wizard-answers.py, which is not an importable module name."""
    path = ROOT / "scripts" / "apply-wizard-answers.py"
    spec = importlib.util.spec_from_file_location("apply_wizard_answers", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# The shared grammar
# ============================================================

@pytest.mark.parametrize("line, expected", [
    # The four rows of the measured divergence table.
    ('KEY="quoted"', ("KEY", "quoted")),
    ("KEY='quoted'", ("KEY", "quoted")),
    ("  KEY=indented", ("KEY", "indented")),
    ("export KEY=exported", ("KEY", "exported")),
    # ...and the rest of the grammar.
    ("KEY=plain", ("KEY", "plain")),
    ("\tKEY=tabbed", ("KEY", "tabbed")),
    ("export\tKEY=tabexport", ("KEY", "tabexport")),
    ("KEY=trailing   ", ("KEY", "trailing")),
    ("KEY=", ("KEY", "")),
    ("KEY=a=b", ("KEY", "a=b")),
    ("lower_key=v", ("lower_key", "v")),
])
def test_the_grammar_reads_every_shape_the_shell_would(line, expected):
    assert parse_env_line(line) == expected


@pytest.mark.parametrize("line, expected", [
    ("KEY =v", ("KEY", "v")),
    ("KEY= v", ("KEY", "v")),
    ("KEY = v", ("KEY", "v")),
    ("  KEY  =  v  ", ("KEY", "v")),
    ("KEY =", ("KEY", "")),
    ('KEY = "v"', ("KEY", "v")),
])
def test_a_space_around_the_equals_sign_is_not_part_of_the_key(line, expected):
    """`KEY = value` is a shape python-dotenv accepts and the shell does not,
    and this file is only ever read by Python. It parsed before this change and
    still does, but nothing tested it: a mutation that dropped `key.strip()`
    survived the whole suite. A differential run over 6663 generated lines put
    the blind spot at 1012 of them, every one of the form `KEY =...`.
    """
    assert parse_env_line(line) == expected


@pytest.mark.parametrize("line", [
    "",
    "   ",
    "# KEY=commented",
    "#KEY=commented",
    "KEY",              # no '=' at all
    "=novalue",         # no key at all
    "1KEY=v",           # not a valid environment-variable name
    "KE-Y=v",           # nor this
    "KE Y=v",           # nor this
])
def test_a_line_that_assigns_nothing_answers_none(line):
    """None means "this line assigns nothing".

    A writer needs the same answer a reader gets, or it replaces the wrong line
    or appends a duplicate beside the right one.
    """
    assert parse_env_line(line) is None


def test_a_key_that_only_starts_with_export_keeps_its_name():
    """`export` is a prefix WORD, not a prefix string.

    Stripping six characters unconditionally would rename `exportKEY` to `KEY`
    and quietly write the value into a different variable.
    """
    assert parse_env_line("exportKEY=v") == ("exportKEY", "v")
    assert parse_env_line("exportedKEY=v") == ("exportedKEY", "v")


@pytest.mark.parametrize("line, expected", [
    # ONE matching pair, and only when it IS a pair.
    ('KEY="unbalanced', '"unbalanced'),
    ("KEY=unbalanced'", "unbalanced'"),
    ('KEY="\'nested\'"', "'nested'"),
    ('KEY=has"quotes"inside', 'has"quotes"inside'),
    ('KEY="  padded  "', "  padded  "),
    ('KEY="', '"'),
    ("KEY=''", ""),
])
def test_quotes_are_stripped_as_a_pair_never_as_a_character_class(line, expected):
    """The chained `.strip('"').strip("'")` two of the readers used is a
    character-class strip. It took the trailing quote off `KEY="unbalanced`,
    which has no pair at all, and unwrapped `KEY="'x'"` twice down to `x`."""
    assert parse_env_line(line)[1] == expected


def test_a_single_quote_character_is_not_a_pair():
    """`len(value) >= 2` is load-bearing: `"` alone is its own first and last
    character, so a length-blind pair test would slice it to the empty string."""
    assert parse_env_line("KEY=\"")[1] == '"'
    assert parse_env_line("KEY='")[1] == "'"


@pytest.mark.parametrize("line", [
    "  # KEY=v",        # an indented comment
    "\t#KEY=v",
    "   ",              # a whitespace-only line
    "  KEY=v",          # an indented assignment
])
def test_two_guards_in_this_parser_are_backstops_and_not_the_guard(line):
    """A recorded finding, not a new rule.

    Two mutations survived the whole suite and neither is a gap. Dropping the
    `line.strip()`, and dropping the `line.startswith("#")` comment skip, each
    change NOTHING: a differential run over 6663 generated lines found 0 inputs
    where either version disagreed with the real one. `_ENV_NAME_RE` catches
    both cases on its own, because an indented comment leaves `#` in the key and
    an indented assignment is re-stripped by `key.strip()`.

    Both were REMOVED from `.tmp/audit/mut_env_readers.py` rather than left to
    survive: an unkillable mutation in the set teaches the next run to expect
    survivors, which is how a real survivor gets waved through. The two lines
    stay in the code because they state the file format plainly, and because
    reaching the same answer through a name validator is a coincidence a reader
    should not have to reconstruct. This test pins the OUTCOME, which is what
    actually matters, so a change to either layer is caught here.
    """
    assert parse_env_line(line) == (("KEY", "v") if line.strip().startswith("KEY")
                                    else None)


def test_iter_env_pairs_keeps_file_order_and_keeps_duplicates():
    """Order and duplicates both matter: `load_env` uses `setdefault`, so it is
    the FIRST line that reaches os.environ, and a reader that silently collapsed
    duplicates could not tell which one that was."""
    text = "A=1\n# comment\n\nB=2\nA=3\nnonsense\n"
    assert list(iter_env_pairs(text)) == [("A", "1"), ("B", "2"), ("A", "3")]


# ============================================================
# read_env_value: fail-soft, and FIRST wins
# ============================================================

def test_the_first_assignment_wins_and_matches_what_load_env_exports(tmp_path,
                                                                     monkeypatch):
    """FIRST, not last, because `load_env` uses `setdefault`.

    Any other answer would disagree with the environment the same file produces,
    which is the exact class of defect this shard exists to end.
    """
    env = tmp_path / ".env"
    env.write_text("SHARD50_DUP=first\nSHARD50_DUP=second\n", encoding="utf-8")
    monkeypatch.delenv("SHARD50_DUP", raising=False)

    load_env(tmp_path)

    assert os.environ["SHARD50_DUP"] == "first"
    assert read_env_value(env, "SHARD50_DUP") == "first"


def test_an_absent_key_answers_the_default(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert read_env_value(env, "MISSING") is None
    assert read_env_value(env, "MISSING", default="fallback") == "fallback"


@pytest.mark.parametrize("prepare", [
    pytest.param(lambda p: None, id="missing-file"),
    pytest.param(lambda p: (p / ".env").write_bytes(b"K=a\xffb\n"), id="not-utf8"),
    pytest.param(lambda p: (p / ".env").mkdir(), id="directory-not-file"),
])
def test_an_unreadable_env_answers_the_default_instead_of_raising(tmp_path, prepare):
    """A wall built to fail open must not carry a hard-crash path.

    `load_gh_token` is evaluated EAGERLY by every `supervised_push` caller,
    including `offboard-exec` and `create-data-repo`, which never use the token.
    A single non-UTF-8 byte in `.env` must not crash them.
    """
    prepare(tmp_path)
    assert read_env_value(tmp_path / ".env", "K") is None
    assert read_env_value(tmp_path / ".env", "K", default="soft") == "soft"


# ============================================================
# load_env
# ============================================================

@pytest.mark.parametrize("line, expected", [
    ('SHARD50_LE="quoted"', "quoted"),
    ("  SHARD50_LE=indented", "indented"),
    ("export SHARD50_LE=exported", "exported"),
])
def test_load_env_reads_every_shape(tmp_path, monkeypatch, line, expected):
    """`export SHARD50_LE=v` used to set a variable named "export SHARD50_LE"
    and leave SHARD50_LE unset, with nothing said about it."""
    (tmp_path / ".env").write_text(line + "\n", encoding="utf-8")
    monkeypatch.delenv("SHARD50_LE", raising=False)
    monkeypatch.delenv("export SHARD50_LE", raising=False)

    load_env(tmp_path)

    assert os.environ["SHARD50_LE"] == expected
    assert "export SHARD50_LE" not in os.environ


def test_an_exported_environment_variable_still_beats_the_file(tmp_path,
                                                               monkeypatch):
    """setdefault, unchanged. tests/conftest.py depends on this precedence to
    keep a real ping URL out of the suite, and get_default_tz_name documents it
    for HEADING_OS_TZ."""
    (tmp_path / ".env").write_text("SHARD50_PREC=from-file\n", encoding="utf-8")
    monkeypatch.setenv("SHARD50_PREC", "from-shell")

    load_env(tmp_path)

    assert os.environ["SHARD50_PREC"] == "from-shell"


def test_a_non_utf8_env_sets_nothing_at_all(tmp_path, monkeypatch):
    """All-or-nothing, and the file has to be BIG to prove it.

    The streaming version decoded one read buffer at a time, so on a small file
    it also raised before setting anything. MEASURED 2026-08-28 with the bad
    byte at the end: a 1711-byte file set 0 variables, an 18911-byte file set
    1749. A test written with a two-line file therefore proves nothing about
    this, and the first version of this test did exactly that: the mutation
    restoring the streaming read survived it.

    The padding here is deliberate and load-bearing, not decoration.
    """
    pad = "".join(f"SHARD50_PAD{i}=x\n" for i in range(2000)).encode()
    (tmp_path / ".env").write_bytes(
        b"SHARD50_HALF=loaded\n" + pad + b"SHARD50_BAD=a\xffb\n")
    with pytest.raises(UnicodeDecodeError):
        load_env(tmp_path)

    # The _env_sandbox fixture gives this test its own mapping, so anything
    # named SHARD50_* in it was set by the call above and nothing else.
    leaked = [k for k in os.environ if k.startswith("SHARD50_")]
    assert leaked == [], leaked[:5]


def test_a_missing_env_is_not_an_error(tmp_path):
    load_env(tmp_path)  # no .env in tmp_path; must simply return


# ============================================================
# load_gh_token - the reader six push callers depend on
# ============================================================

@pytest.mark.parametrize("line, expected", [
    ("GH_TOKEN=ghp_plain", "ghp_plain"),
    ('GH_TOKEN="ghp_quoted"', "ghp_quoted"),
    ("GH_TOKEN='ghp_quoted'", "ghp_quoted"),
    ("  GH_TOKEN=ghp_indented", "ghp_indented"),
    ("\tGH_TOKEN=ghp_tabbed", "ghp_tabbed"),
    ("export GH_TOKEN=ghp_exported", "ghp_exported"),
])
def test_the_push_token_is_found_in_every_shape(tmp_path, monkeypatch,
                                                line, expected):
    """A single leading space used to make the token invisible here while
    `load_env` read it perfectly well. safe-push then printed "no GH_TOKEN in
    engine .env" and exited 3, naming a cause that was not true."""
    (tmp_path / ".env").write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: tmp_path)

    assert git_push.load_gh_token() == expected


def test_the_push_token_keeps_a_quote_it_was_never_given_a_pair_for(tmp_path,
                                                                    monkeypatch):
    """`.strip('"')` is a character-class strip: it removed the trailing quote
    from a value that had no opening one, handing git a token one byte short of
    the real thing."""
    (tmp_path / ".env").write_text('GH_TOKEN=ghp_odd"\n', encoding="utf-8")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: tmp_path)

    assert git_push.load_gh_token() == 'ghp_odd"'


@pytest.mark.parametrize("prepare", [
    pytest.param(lambda p: None, id="missing-file"),
    pytest.param(lambda p: (p / ".env").write_bytes(b"GH_TOKEN=a\xffb\n"),
                 id="not-utf8"),
    pytest.param(lambda p: (p / ".env").write_text("OTHER=1\n", encoding="utf-8"),
                 id="key-absent"),
])
def test_the_push_token_answers_none_instead_of_raising(tmp_path, monkeypatch,
                                                        prepare):
    prepare(tmp_path)
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: tmp_path)

    assert git_push.load_gh_token() is None


# ============================================================
# load_env_key - the reader that sent quotes to healthchecks.io
# ============================================================

@pytest.mark.parametrize("line, expected", [
    ("HEALTHCHECKS_API_KEY=abc", "abc"),
    ('HEALTHCHECKS_API_KEY="abc"', "abc"),
    ("HEALTHCHECKS_API_KEY='abc'", "abc"),
    ("  HEALTHCHECKS_API_KEY=abc", "abc"),
    ("export HEALTHCHECKS_API_KEY=abc", "abc"),
])
def test_the_healthchecks_key_arrives_without_its_quotes(tmp_path, monkeypatch,
                                                         line, expected):
    """MEASURED before this change: `KEY="abc"` yielded the 5-character string
    '"abc"', which went out as the X-Api-Key header and came back 401 with
    nothing in the message to say why."""
    env = tmp_path / ".env"
    env.write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    assert hs.load_env_key() == expected


def test_a_missing_healthchecks_env_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "_ENV_FILE", tmp_path / ".env")
    with pytest.raises(SystemExit) as e:
        hs.load_env_key()
    assert ".env not found" in str(e.value)


def test_an_absent_healthchecks_key_says_it_is_not_set(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    with pytest.raises(SystemExit) as e:
        hs.load_env_key()
    assert "not set in .env" in str(e.value)


def test_an_unreadable_healthchecks_env_says_so_instead_of_a_traceback(
        tmp_path, monkeypatch):
    """It used to raise UnicodeDecodeError out of a provisioning CLI, and the
    operator got a stack trace where a reason belonged. It must also NOT be
    reported as "not set", which was the other wrong answer available."""
    env = tmp_path / ".env"
    env.write_bytes(b"HEALTHCHECKS_API_KEY=a\xffb\n")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    with pytest.raises(SystemExit) as e:
        hs.load_env_key()
    assert "could not read" in str(e.value)
    assert "not set in .env" not in str(e.value)


# ============================================================
# The writers: what a writer cannot see, it duplicates
# ============================================================

@pytest.mark.parametrize("existing", [
    "HEALTHCHECKS_API_KEY=OLD\n",
    "  HEALTHCHECKS_API_KEY=OLD\n",
    "\tHEALTHCHECKS_API_KEY=OLD\n",
    "export HEALTHCHECKS_API_KEY=OLD\n",
    'HEALTHCHECKS_API_KEY="OLD"\n',  # pragma: allowlist secret
])
def test_write_env_replaces_the_line_instead_of_appending_a_twin(
        tmp_path, monkeypatch, existing):
    """MEASURED on `  HEALTHCHECKS_API_KEY=OLD`: the `^KEY=.*$` substitution
    matched nothing, `KEY=NEW` was appended, and afterwards this module's own
    reader answered NEW while `load_env` answered OLD. The daemons read the ping
    URL through `load_env`, so they would have kept pinging the old check while
    the provisioner reported the new one written."""
    env = tmp_path / ".env"
    env.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    hs.write_env({"HEALTHCHECKS_API_KEY": "NEW"})  # pragma: allowlist secret

    pairs = list(iter_env_pairs(env.read_text(encoding="utf-8")))
    assert pairs == [("HEALTHCHECKS_API_KEY", "NEW")], env.read_text()
    assert hs.load_env_key() == "NEW"


def test_write_env_and_load_env_agree_after_the_write(tmp_path, monkeypatch):
    """The two answers that diverged, asked side by side on the same file."""
    env = tmp_path / ".env"
    env.write_text("  SHARD50_PING=old\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)
    monkeypatch.delenv("SHARD50_PING", raising=False)

    hs.write_env({"SHARD50_PING": "new"})
    load_env(tmp_path)

    assert os.environ["SHARD50_PING"] == "new"
    assert read_env_value(env, "SHARD50_PING") == "new"


def test_write_env_appends_a_key_the_file_does_not_have(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    hs.write_env({"NEWKEY": "v"})

    assert list(iter_env_pairs(env.read_text(encoding="utf-8"))) == [
        ("OTHER", "1"), ("NEWKEY", "v")]


def test_write_env_leaves_lines_it_was_not_asked_about_alone(tmp_path,
                                                             monkeypatch):
    """Surgical: comments, blank lines and unrelated keys survive byte-for-byte,
    because `.env` is a file the operator hand-edits."""
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nUNRELATED=keep me\nTARGET=old\n",
                   encoding="utf-8")
    monkeypatch.setattr(hs, "_ENV_FILE", env)

    hs.write_env({"TARGET": "new"})

    assert env.read_text(encoding="utf-8") == (
        "# a comment\n\nUNRELATED=keep me\nTARGET=new\n")


@pytest.mark.parametrize("existing", [
    "SECRET_KEY=old\n",
    "  SECRET_KEY=old\n",
    "export SECRET_KEY=old\n",
])
def test_the_wizard_replaces_a_secret_instead_of_appending_a_twin(
        tmp_path, existing):
    """Same defect from the writer side. The wizard appended, `load_env` (FIRST
    line wins) then handed every caller the OLD secret, and the wizard reported
    the new one written."""
    wizard = _wizard()
    env = tmp_path / ".env"
    env.write_text(existing, encoding="utf-8")

    wizard._upsert_env_line(env, "SECRET_KEY", "new")

    assert list(iter_env_pairs(env.read_text(encoding="utf-8"))) == [
        ("SECRET_KEY", "new")], env.read_text()
    assert read_env_value(env, "SECRET_KEY") == "new"


def test_the_wizard_still_refuses_a_value_that_would_split_the_line(tmp_path):
    """Unchanged contract: a newline or NUL in a secret is a paste accident, and
    writing it verbatim defined variables nobody asked for."""
    wizard = _wizard()
    env = tmp_path / ".env"
    for bad in ("a\nb", "a\rb", "a\x00b"):
        with pytest.raises(wizard.SchemaError):
            wizard._upsert_env_line(env, "SECRET_KEY", bad)


# ============================================================
# The anti-drift guard
# ============================================================

# One line, every reader, one answer. A seventh hand-rolled grammar fails HERE
# rather than in production. Each entry is (line-template, expected value or
# None for "this line assigns nothing").
_SHARED_TABLE = [
    ("{k}=plain", "plain"),
    ('{k}="quoted"', "quoted"),
    ("{k}='quoted'", "quoted"),
    ("  {k}=indented", "indented"),
    ("\t{k}=tabbed", "tabbed"),
    ("export {k}=exported", "exported"),
    ("{k}=trailing  ", "trailing"),
    ("{k}=", ""),
    ("{k}=a=b", "a=b"),
    ('{k}="unbalanced', '"unbalanced'),
    ('{k}="\'nested\'"', "'nested'"),
    ("#{k}=commented", None),
    ("# {k}=commented", None),
    ("{k}", None),
]


@pytest.mark.parametrize("template, expected", _SHARED_TABLE)
def test_every_reader_of_this_file_gives_the_same_answer(tmp_path, monkeypatch,
                                                         template, expected):
    """The property this shard exists to establish.

    Before the change this table produced three different answers per row for
    the quoted, indented and exported shapes. It is written as one table fed to
    every reader on purpose: a test that checked each reader separately would
    pass while they drifted apart again.
    """
    env = tmp_path / ".env"
    env.write_text(
        template.format(k="GH_TOKEN") + "\n"
        + template.format(k="HEALTHCHECKS_API_KEY") + "\n"
        + template.format(k="SHARD50_AGREE") + "\n",
        encoding="utf-8")
    monkeypatch.setattr(git_push, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(hs, "_ENV_FILE", env)
    monkeypatch.delenv("SHARD50_AGREE", raising=False)

    # load_env
    load_env(tmp_path)
    from_load_env = os.environ.get("SHARD50_AGREE")

    # load_gh_token
    from_gh = git_push.load_gh_token()

    # load_env_key (exits when the key is absent; that IS its "None")
    try:
        from_hc = hs.load_env_key()
    except SystemExit:
        from_hc = None

    assert from_load_env == expected
    assert from_gh == expected
    assert from_hc == expected
