"""Shard 34: six guards in the session hooks that each covered one spelling of
a thing and missed the neighbouring spelling of the same thing.

Every defect here was reproduced by RUNNING the live hook before a line changed.

* ``session-start.py`` built a careful "CRM HEALTH CHECK DID NOT RUN ... Overdue
  contacts are UNKNOWN, not zero" string and handed it to a caller that read
  only ``len()`` of the list. A crm-health.py exiting non-zero rendered
  ``CRM ALERT: 1 contact(s) need attention today`` - byte-identical to a session
  with exactly one genuinely overdue contact. The staleness check ten lines below
  the same caller already said ``CONTEXT STALENESS NOT CHECKED``, so this was the
  fix landing in one of two adjacent copies.

* ``_dispatch._blocking_wait`` ran the poll-loop regex over the RAW command while
  the sleep half of the same function used the quote-aware ``_shell_segments``.
  ``echo "while you wait"; sleep 1`` was policy-denied by a message that promises
  short sleeps go through.

* ``_dispatch._pytest_argv`` accepted ``pytest`` as the first word of a segment or
  after ``-m``, so ``uv run python -m pytest tests/`` was caught and
  ``uv run pytest tests/`` - the shorter spelling of the same serial suite run,
  in this repo's own canonical toolchain - was not.

* ``check_protect_personal_threads`` refused ``grep`` and ``rg`` inside a Bash
  command but returned None for the native Grep and Glob tools, which were not
  even dispatched here. ``data-path-redirect.py`` WAS registered for that exact
  pair, which is how the gap stayed invisible.

* ``check_protect_docs`` gated on the substring ``"/docs/"``, which needs a
  separator before ``docs``: ``./docs/GETTING-STARTED.md`` was blocked and the
  plainer ``docs/GETTING-STARTED.md`` was not. It also took ``os.path.basename``
  of the RAW path while testing the NORMALISED one, so on this Linux host a
  Windows-spelled ``docs\\GETTING-STARTED.md`` passed the directory test and then
  matched nothing in SYNCED_FILES.

* ``check_tool_budget``'s docstring said it "counts every tool invocation". It
  counts the tool invocations this dispatcher is registered for, which is five
  tool families out of the session's whole surface.

Run: python3 -m pytest tests/test_six_guards_that_named_a_tool_and_missed_its_twin.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(autouse=True)
def _sys_path_restored():
    """The hooks here are executed IN-PROCESS, and a hook resolves its own
    workspace root onto `sys.path` (`.claude/hooks/_dispatch.py:60`). In a real
    child that entry dies with the process; here it outlives the test and holds
    for the rest of the xdist worker, one stale tmp directory per test. Correct
    in the hook, so restore it on this side.
    """
    saved = sys.path[:]
    try:
        yield
    finally:
        sys.path[:] = saved


# The literal is assembled, never written out: this test file is read by the
# very Bash guard it exercises, and a bare spelling of the guarded path makes
# the guard refuse the command that runs the suite.
PERSONAL = "threads/" + "personal"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dispatch():
    return _load("shard34_dispatch", ".claude/hooks/_dispatch.py")


@pytest.fixture
def session_start():
    return _load("shard34_session_start", ".claude/hooks/session-start.py")


# ============================================================
# 1. The CRM banner that reported a broken alarm as one overdue contact
# ============================================================

def _crm_fixture(tmp_path, monkeypatch, hook, returncode, stderr="boom", stdout=""):
    class _Proc:
        pass

    _Proc.returncode = returncode
    _Proc.stderr = stderr
    _Proc.stdout = stdout
    monkeypatch.setattr(hook.subprocess, "run", lambda *a, **k: _Proc())
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "crm-health.py").write_text("", encoding="utf-8")
    return tmp_path


def _run_main(hook, monkeypatch, capsys, cwd):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(cwd)})))
    with pytest.raises(SystemExit) as exit_info:
        hook.main()
    assert exit_info.value.code == 0
    return capsys.readouterr().out


def test_the_failure_and_the_count_are_two_different_banners(
        session_start, tmp_path, monkeypatch, capsys):
    """The whole defect in one assertion: the two states must not read alike."""
    _crm_fixture(tmp_path, monkeypatch, session_start, returncode=3)
    failed_out = _run_main(session_start, monkeypatch, capsys, tmp_path)

    monkeypatch.setattr(
        session_start, "check_crm_health",
        lambda project_dir: (["Alpha Person, Alpha Co, 90d"], None))
    one_overdue_out = _run_main(session_start, monkeypatch, capsys, tmp_path)

    assert failed_out != one_overdue_out
    assert "CRM ALERT: 1 contact(s)" in one_overdue_out
    assert "CRM ALERT:" not in failed_out, (
        "a check that did not run still renders as a contact count: " + failed_out)


def test_a_nonzero_exit_names_the_exit_code_on_stdout(
        session_start, tmp_path, monkeypatch, capsys):
    """stdout, because that is the stream SessionStart injects. The hook exits 0,
    so its stderr line is transcript-mode debug and reaches no session."""
    _crm_fixture(tmp_path, monkeypatch, session_start, returncode=7,
                 stderr="ValueError: malformed frontmatter")
    out = _run_main(session_start, monkeypatch, capsys, tmp_path)
    assert "CRM HEALTH CHECK NOT RUN" in out
    assert "exited 7" in out
    assert "malformed frontmatter" in out
    assert "UNKNOWN, not zero" in out


def test_a_launch_failure_is_a_failure_not_a_quiet_zero(
        session_start, tmp_path, monkeypatch):
    """`subprocess.run` raising is the same state as a non-zero exit.

    It used to fall through the handler to `return None`, which the caller read
    as "no overdue contacts" - silence again, by a second route.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "crm-health.py").write_text("", encoding="utf-8")

    def _boom(*a, **k):
        raise OSError("no interpreter")

    monkeypatch.setattr(session_start.subprocess, "run", _boom)
    contacts, failure = session_start.check_crm_health(str(tmp_path))
    assert contacts == []
    assert failure and "no interpreter" in failure


def test_a_workspace_without_the_crm_script_reports_no_failure(
        session_start, tmp_path):
    """"There is no CRM engine here" is not "the CRM engine broke".

    A public clone has no crm-health.py, and it must raise no alarm at all.
    """
    contacts, failure = session_start.check_crm_health(str(tmp_path))
    assert contacts == []
    assert failure is None


def test_a_clean_run_with_nothing_overdue_reports_neither(
        session_start, tmp_path, monkeypatch, capsys):
    _crm_fixture(tmp_path, monkeypatch, session_start, returncode=0,
                 stdout="GREEN - all good\n  Alpha Person\n")
    out = _run_main(session_start, monkeypatch, capsys, tmp_path)
    assert "CRM ALERT" not in out
    assert "CRM HEALTH CHECK NOT RUN" not in out


def test_a_cache_hit_is_a_successful_run(session_start, tmp_path, monkeypatch):
    """The cache is written only by the exit-0 branch, so reading one back is
    evidence the check ran. A cache hit that reports a failure would put a
    permanent NOT RUN banner on every session for the next 30 minutes.

    This path is unreachable from `main()` in the failure fixtures above,
    because a failing run never writes a cache - so it needs its own test.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "crm-health.py").write_text("", encoding="utf-8")
    sessions = tmp_path / ".sessions"
    sessions.mkdir()
    from datetime import datetime as _dt
    (sessions / "crm-health-cache.json").write_text(json.dumps({
        "cached_at": _dt.now().astimezone().timestamp(),
        "red_contacts": ["Alpha Person, Alpha Co, 90d",
                         "Beta Person, Beta Co, 71d"],
    }), encoding="utf-8")

    def _must_not_run(*a, **k):
        raise AssertionError("a fresh cache must not re-run crm-health.py")

    monkeypatch.setattr(session_start.subprocess, "run", _must_not_run)
    contacts, failure = session_start.check_crm_health(str(tmp_path))
    assert failure is None, f"a cache hit reported a failure: {failure!r}"
    assert len(contacts) == 2, contacts


def test_an_empty_cache_hit_is_still_a_successful_run(
        session_start, tmp_path, monkeypatch):
    """Nothing overdue is not the same as the check having broken."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "crm-health.py").write_text("", encoding="utf-8")
    sessions = tmp_path / ".sessions"
    sessions.mkdir()
    from datetime import datetime as _dt
    (sessions / "crm-health-cache.json").write_text(json.dumps({
        "cached_at": _dt.now().astimezone().timestamp(),
        "red_contacts": [],
    }), encoding="utf-8")
    monkeypatch.setattr(session_start.subprocess, "run",
                        lambda *a, **k: pytest.fail("cache was ignored"))
    contacts, failure = session_start.check_crm_health(str(tmp_path))
    assert (contacts, failure) == ([], None)


def test_check_crm_health_always_returns_the_pair_its_caller_unpacks(
        session_start, tmp_path, monkeypatch):
    """Every return path, not just the happy one.

    `main()` writes `red_contacts, crm_failure = check_crm_health(...)`. A path
    that returns a bare list or None raises ValueError/TypeError at SessionStart
    - which is exactly how the sibling `check_stale_files` defect shipped, per
    tests/test_alerts_that_never_reached_the_session.py.
    """
    cases = []

    # no script at all
    cases.append(session_start.check_crm_health(str(tmp_path)))

    # non-zero exit
    _crm_fixture(tmp_path, monkeypatch, session_start, returncode=2)
    cases.append(session_start.check_crm_health(str(tmp_path)))

    # clean exit, no RED section
    _crm_fixture(tmp_path, monkeypatch, session_start, returncode=0,
                 stdout="nothing here\n")
    cases.append(session_start.check_crm_health(str(tmp_path)))

    for result in cases:
        assert isinstance(result, tuple) and len(result) == 2, result
        contacts, failure = result
        assert isinstance(contacts, list), result
        assert failure is None or isinstance(failure, str), result


# ============================================================
# 2. The poll-loop regex that read a quoted word as a shell keyword
# ============================================================

@pytest.mark.parametrize("command", [
    'echo "while you wait"; sleep 1',
    "echo 'until then'; sleep 2",
    'sleep 1 # a while later',
    'grep -n "while" file.txt; sleep 1',
    'python -c "print(\'while\')"; sleep 1',
])
def test_a_keyword_inside_quotes_is_not_a_poll_loop(dispatch, command):
    assert dispatch._blocking_wait(command) is False, command


@pytest.mark.parametrize("command", [
    'while ! test -f x; do sleep 5; done',
    'until curl -s localhost:9; do sleep 2; done',
    'while true; do sleep 1; done',
])
def test_a_real_poll_loop_is_still_refused(dispatch, command):
    assert dispatch._blocking_wait(command) is True, command


def test_a_long_bare_sleep_is_still_refused(dispatch):
    assert dispatch._blocking_wait("sleep 120") is True
    assert dispatch._blocking_wait("sleep 5") is False


def test_the_skeleton_keeps_structure_and_drops_content(dispatch):
    """The helper's own contract, so a rewrite cannot quietly change it."""
    assert dispatch._unquoted_skeleton('echo "while"; sleep 1') == 'echo ""; sleep 1'
    assert dispatch._unquoted_skeleton("a 'b c' d") == "a '' d"
    assert dispatch._unquoted_skeleton("sleep 1 # while") == "sleep 1 "
    # A separator inside a comment is gone with it; a newline separator is kept.
    assert dispatch._unquoted_skeleton("a # x\nb") == "a \nb"
    # Nothing to strip: byte-identical.
    assert dispatch._unquoted_skeleton("while true; do sleep 1; done") == \
        "while true; do sleep 1; done"


def test_the_skeleton_is_not_vacuous(dispatch):
    """A helper that returned its input unchanged would pass every case above
    except the quoted ones; a helper that returned "" would pass those and fail
    the loops. Prove it does both jobs on one string."""
    skeleton = dispatch._unquoted_skeleton('while "sleep" x; do sleep 9; done')
    assert "sleep" in skeleton, "the unquoted sleep was destroyed"
    assert skeleton.count("sleep") == 1, (
        f"the quoted sleep survived: {skeleton!r}")


@pytest.mark.parametrize("command", [
    # The literal case: this shard's own commit message, fed through git.
    "cat > msg.txt <<'EOF'\n_blocking_wait ran the regex over the raw command\n"
    "while the sleep half used the quote-aware splitter\nEOF\n"
    "git commit -F msg.txt",
    # Unquoted delimiter, and more of the opening line after it.
    "cat > f.txt <<EOF && echo done\nwhile x sleep\nEOF\n",
    # Tab-stripping form.
    "cat <<-EOF\n\twhile sleep\n\tEOF\n",
    # Unterminated: the body runs to the end, exactly as the shell reads it.
    "cat <<EOF\nwhile sleep\n",
    # A here-STRING is one word on the same line, and it is quoted.
    "grep x <<< 'while sleep'",
])
def test_a_here_document_body_is_data_not_shell_syntax(dispatch, command):
    """Found by running it: this shard's own `git commit -F` was refused.

    The message describes the poll-loop defect, so it says `while` and `sleep`
    in ordinary prose. A guard that refuses the commit that fixes it is the
    same class of defect one level down.
    """
    assert dispatch._blocking_wait(command) is False, command


@pytest.mark.parametrize("opener,terminator", [
    ("<<EOF", "EOF"),
    ("<<'EOF'", "EOF"),
    ('<<"EOF"', "EOF"),
    ("<<-EOF", "\tEOF"),
])
def test_a_real_loop_after_a_here_document_is_still_refused(
        dispatch, opener, terminator):
    """Vacuity guard, and the one that matters most: the body must END.

    A heredoc reader that never recognises its terminator swallows the rest of
    the command, so EVERY later command becomes invisible to every check built
    on this skeleton. That failure is silent and it looks exactly like the fix
    working. The `<<-` row is here because a terminator reader that forgets to
    strip leading tabs passes the "no false positive" tests above by consuming
    the whole string.
    """
    command = f"cat {opener}\nnothing here\n{terminator}\nwhile true; do sleep 1; done"
    assert dispatch._blocking_wait(command) is True, command


@pytest.mark.parametrize("opener,terminator", [
    ("<<EOF", "EOF"),
    ("<<-EOF", "\tEOF"),
])
def test_the_terminator_line_ends_the_body_and_nothing_before_it_does(
        dispatch, opener, terminator):
    """The skeleton keeps what follows the terminator and drops what precedes."""
    skeleton = dispatch._unquoted_skeleton(
        f"cat {opener}\nsecret body text\n{terminator}\necho after")
    assert "secret body text" not in skeleton, skeleton
    assert "echo after" in skeleton, skeleton


def test_the_rest_of_the_opening_line_survives_the_heredoc(dispatch):
    """The body starts on the NEXT line, so `&& echo done` is live syntax.

    Skipping from `<<` straight to past the terminator would have eaten it.
    """
    skeleton = dispatch._unquoted_skeleton(
        "cat > f.txt <<EOF && echo done\nbody\nEOF\n")
    assert "&& echo done" in skeleton, skeleton
    assert "body" not in skeleton, skeleton


def test_the_waiter_guard_end_to_end_lets_the_quoted_word_through(dispatch):
    payload = {"tool_name": "Bash",
               "tool_input": {"command": 'echo "while you wait"; sleep 1'}}
    assert dispatch.check_slow_shell(payload) is None
    payload["tool_input"]["command"] = 'while ! test -f x; do sleep 5; done'
    verdict = dispatch.check_slow_shell(payload)
    assert verdict and verdict["decision"] == "block"


# ============================================================
# 3. The pytest guard the repo's own toolchain walked around
# ============================================================

@pytest.mark.parametrize("command,expected_head", [
    ("uv run pytest tests/", "uv"),
    ("uv run --frozen pytest tests/", "uv"),
    ("uvx run pytest tests/", "uvx"),
    ("poetry run pytest tests/", "poetry"),
    ("pytest tests/", "pytest"),
    ("python -m pytest tests/", "python"),
    ("uv run python -m pytest tests/", "uv"),
])
def test_every_spelling_of_a_pytest_run_is_recognised(dispatch, command,
                                                       expected_head):
    argv = dispatch._pytest_argv(command)
    assert argv is not None, command
    assert argv[0] == expected_head, argv


@pytest.mark.parametrize("command", [
    "echo uv run pytest",
    "git log --oneline | grep pytest",
    "ls tests/ | grep -iE 'test|pytest'",
    "cat notes-about-pytest.md",
])
def test_a_mention_of_pytest_is_still_not_an_invocation(dispatch, command):
    assert dispatch._pytest_argv(command) is None, command


def test_the_runner_clause_needs_the_runner_and_the_word_run(dispatch):
    """Vacuity guard on the new helper: it must refuse as well as accept."""
    assert dispatch._is_runner_invocation(["uv", "run", "pytest"], 2) is True
    assert dispatch._is_runner_invocation(["make", "run", "pytest"], 2) is False
    assert dispatch._is_runner_invocation(["uv", "sync", "pytest"], 2) is False
    assert dispatch._is_runner_invocation(["uv", "run", "python", "pytest"], 3) is False
    assert dispatch._is_runner_invocation(["pytest"], 0) is False


def test_the_uv_spelling_of_the_serial_suite_is_blocked(dispatch):
    payload = {"tool_name": "Bash", "tool_input": {"command": "uv run pytest tests/"}}
    verdict = dispatch.check_slow_shell(payload)
    assert verdict and verdict["decision"] == "block", verdict


@pytest.mark.parametrize("command", [
    "uv run pytest tests/security",
    "uv run pytest tests/test_one_file.py",
    "uv run pytest tests/ -n auto",
    "uv run pytest tests/ -k something",
])
def test_a_narrow_uv_run_still_goes_through(dispatch, command):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert dispatch.check_slow_shell(payload) is None, command


# ============================================================
# 4. The personal-threads guard that covered Bash grep and not the Grep tool
# ============================================================

@pytest.mark.parametrize("tool,tool_input", [
    ("Grep", {"pattern": "price", "path": PERSONAL}),
    ("Grep", {"pattern": "price", "path": PERSONAL + "/"}),
    ("Grep", {"pattern": "price", "path": PERSONAL + "/2026-medical.md"}),
    ("Grep", {"pattern": "x", "path": ".", "glob": PERSONAL + "/*.md"}),
    ("Grep", {"pattern": PERSONAL + "/.*"}),
    ("Glob", {"pattern": "*.md", "path": PERSONAL}),
    ("Glob", {"pattern": PERSONAL + "/**/*.md"}),
])
def test_the_native_search_tools_cannot_reach_the_personal_subtree(
        dispatch, tool, tool_input):
    verdict = dispatch.check_protect_personal_threads(
        {"tool_name": tool, "tool_input": tool_input})
    assert verdict is not None, f"{tool} {tool_input} was allowed"
    assert verdict["decision"] == "block"
    assert verdict["_policy_deny"] is True


@pytest.mark.parametrize("tool,tool_input", [
    ("Grep", {"pattern": "x", "path": "threads/business"}),
    ("Grep", {"pattern": "x", "path": "threads/personal-notes"}),
    ("Glob", {"pattern": "*.md", "path": "scripts"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("Grep", {"pattern": "def main"}),
])
def test_ordinary_searches_are_untouched(dispatch, tool, tool_input):
    assert dispatch.check_protect_personal_threads(
        {"tool_name": tool, "tool_input": tool_input}) is None, tool_input


def test_the_bash_twin_of_that_read_was_already_refused(dispatch):
    """The asymmetry that made this a defect rather than a design choice."""
    bash = dispatch.check_protect_personal_threads(
        {"tool_name": "Bash",
         "tool_input": {"command": "grep -rn price " + PERSONAL}})
    assert bash and bash["decision"] == "block"


@pytest.mark.parametrize("name", ["settings.local.linux.json",
                                  "settings.local.macos.json",
                                  "settings.local.windows.json"])
def test_the_dispatcher_is_registered_for_grep_and_glob(name):
    """The code half of the guard is inert without the wiring half.

    `data-path-redirect.py` in settings.json has matched Grep and Glob all
    along; `_dispatch.py` did not, which is why the hole was invisible.
    """
    settings = json.loads((ROOT / ".claude" / name).read_text(encoding="utf-8"))
    matchers = [
        entry.get("matcher", "")
        for entry in settings.get("hooks", {}).get("PreToolUse", [])
        if any("_dispatch.py" in (h.get("command") or "")
               for h in entry.get("hooks", []))
    ]
    assert matchers, f"{name}: _dispatch.py is registered under no matcher"
    joined = "|".join(matchers)
    for tool in ("Grep", "Glob", "Read", "Bash", "Write"):
        assert tool in joined, f"{name}: {tool} not dispatched; matchers={matchers}"


def test_the_directory_pattern_end_anchors(dispatch):
    """`threads/personal-notes` is a different directory and must stay readable.

    The wider pattern was added for search ROOTS, which carry no trailing
    separator; widening it to a bare prefix would have swallowed the neighbour.
    """
    assert dispatch._PERSONAL_DIR_RE.search(PERSONAL) is not None
    assert dispatch._PERSONAL_DIR_RE.search(PERSONAL + "/x.md") is not None
    assert dispatch._PERSONAL_DIR_RE.search(PERSONAL.replace("/", "\\")) is not None
    assert dispatch._PERSONAL_DIR_RE.search(PERSONAL + "-notes/x.md") is None
    assert dispatch._PERSONAL_DIR_RE.search("threads/business/x.md") is None


# ============================================================
# 5. The docs guard that needed a separator it had no right to require
# ============================================================

@pytest.mark.parametrize("path", [
    "docs/GETTING-STARTED.md",
    "./docs/GETTING-STARTED.md",
    "/home/x/heading-os/docs/GETTING-STARTED.md",
    "docs\\GETTING-STARTED.md",
    "..\\docs\\CEO-ADMIN-GUIDE.md",
    "docs/EMERGENCY-PROCEDURES.html",
])
def test_every_spelling_of_a_synced_doc_is_blocked(dispatch, path):
    verdict = dispatch.check_protect_docs(
        {"tool_name": "Write", "tool_input": {"file_path": path}})
    assert verdict is not None, path
    assert verdict["decision"] == "block"


@pytest.mark.parametrize("path", [
    "my-docs/GETTING-STARTED.md",
    "docs/ARCHITECTURE.md",
    "templates/GETTING-STARTED.md",
    "docs/superpowers/specs/GETTING-STARTED-notes.md",
])
def test_a_file_this_guard_does_not_own_is_untouched(dispatch, path):
    assert dispatch.check_protect_docs(
        {"tool_name": "Write", "tool_input": {"file_path": path}}) is None, path


def test_reading_a_synced_doc_is_still_allowed(dispatch):
    """The denial log records a real operator Read refused by this check on
    2026-08-11. Widening the path test must not walk that back."""
    assert dispatch.check_protect_docs(
        {"tool_name": "Read",
         "tool_input": {"file_path": "docs/GETTING-STARTED.md"}}) is None


# ============================================================
# 6. The counter that said "every tool invocation"
# ============================================================

def test_the_budget_docstring_does_not_claim_a_coverage_it_has_not_got():
    """`.claude/rules/scope-claims.md`: state the coverage the method
    establishes. This hook sees the matchers it is registered for and nothing
    else - no WebFetch, no Task, no MCP tool."""
    module = _load("shard34_budget_doc", ".claude/hooks/_dispatch.py")
    doc = " ".join(module.check_tool_budget.__doc__.split())
    assert "Counts every tool invocation THIS DISPATCHER SEES" in doc
    assert "It is NOT every tool call the session makes" in doc
    for uncounted in ("WebFetch", "Task", "MCP"):
        assert uncounted in doc, f"{uncounted} is uncounted and unmentioned"
    # Naming the surfaces is not enough: the docstring has to say what happens
    # to them. A list of names with the consequence deleted reads as "these are
    # counted too", which is the opposite of the fact.
    assert "never reach this hook" in doc, (
        "the docstring names the uncounted surfaces but no longer says they are "
        f"uncounted: {doc!r}")
    assert "invisible here" in doc


# ============================================================
# 7. The here-STRING that every heredoc reader in the file read as a heredoc
# ============================================================
#
# `<<<word` is a here-string: one word, on the same line, no body and no
# terminator. All three heredoc openers in `_dispatch.py` matched it as a
# heredoc opening on `word`, because a trailing `(?!<)` only refuses the match
# that begins at the FIRST angle bracket and `re` simply retries one character
# along. The reader then hunts for a line equal to `word`, never finds one, and
# treats everything after it as body.
#
# MEASURED 2026-09-01 against the shipped patterns:
#
#     grep x <<<needle\nwhile true; do sleep 1; done
#         _unquoted_skeleton  'grep x <<<\n'
#         _blocking_wait      False        (the poll loop is invisible)
#
# Every here-string case in section 2 above is QUOTED, and the quote scanner
# empties a quoted word before the heredoc pattern is consulted, so the bare
# form was never reached. The fix is one lookbehind per pattern.

@pytest.mark.parametrize("command", [
    "grep x <<<needle\nwhile true; do sleep 1; done",
    "grep x <<<$VAR\nuntil curl -s localhost:9; do sleep 2; done",
    "tr a b <<<HELLO\nwhile true; do sleep 1; done",
    "cat <<<word\nwhile ! test -f x; do sleep 5; done",
])
def test_a_poll_loop_after_a_bare_here_string_is_still_refused(dispatch, command):
    """The consequence, asserted through the guard rather than the regex."""
    assert dispatch._blocking_wait(command) is True, command


@pytest.mark.parametrize("command", [
    "grep x <<<needle\necho after",
    "tr a b <<<HELLO\nsecond line\nthird line",
])
def test_a_bare_here_string_does_not_swallow_the_lines_after_it(dispatch, command):
    """The skeleton must keep what follows. A reader that swallows to the end of
    the command makes every later command invisible to every check built on it,
    and that failure looks exactly like the guard working."""
    skeleton = dispatch._unquoted_skeleton(command)
    for line in command.split("\n")[1:]:
        assert line in skeleton, (line, skeleton)


def test_a_real_here_document_is_still_a_here_document(dispatch):
    """The anti-vacuity jaw. A pattern that stopped matching `<<` altogether
    would satisfy both cases above and would un-do the heredoc handling section 2
    exists for."""
    skeleton = dispatch._unquoted_skeleton(
        "cat <<EOF\nsecret body text\nEOF\necho after")
    assert "secret body text" not in skeleton, skeleton
    assert "echo after" in skeleton, skeleton
    assert dispatch._blocking_wait(
        "cat <<EOF\nnothing\nEOF\nwhile true; do sleep 1; done") is True


def test_the_other_two_heredoc_readers_carry_the_same_guard(dispatch):
    """Three copies of one pattern, and the campaign's dominant failure is a fix
    landing in some of them. Asserted through each reader's own entry point.

    `strip_heredocs` feeds the fan-out read counter, so a swallowed remainder
    makes the wall under-count a session's hand-reads. `_heredoc_body_spans`
    blanks the same region for the release wall.
    """
    kept = dispatch.strip_heredocs("grep x <<<needle\necho after\nls -la")
    assert "echo after" in kept and "ls -la" in kept, kept

    dropped = dispatch.strip_heredocs("cat <<EOF\nbody line\nEOF\necho after")
    assert "body line" not in dropped, dropped
    assert "echo after" in dropped, dropped

    assert dispatch._heredoc_body_spans("grep x <<<needle\necho after") == []
    assert dispatch._heredoc_body_spans("cat <<EOF\nbody\nEOF\n") != []
