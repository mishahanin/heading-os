"""Shard `scripts-04-p4`: three dev tools that lost their own evidence.

  - `dev/compact-watch.py` writes one line per observed CHANGE, to answer "what
    happened around this compaction, and in what order". `CP.read_json` swallows
    a corrupt read and returns `{}` - the right call for a hook that must not
    stop a turn, the wrong one for an instrument whose output IS a sequence of
    transitions. One torn read (another process rewriting the state file mid-read)
    made every watched key read as `null`, so the watcher logged a `change` with
    all of them dropping to nothing, then logged a second `change` with all of
    them coming back. Measured on a 3-key state file before the fix: 3 fabricated
    transitions per torn read, doubled by the next good poll.
  - `dev/publish-marketplace.py` runs every git and build step through one
    `_run` helper with `capture_output=True, check=True`. That pair hands the
    operator "Command [...] returned non-zero exit status 3." and drops the
    child's own stderr. It costs most on the build step: when the plugin
    completeness gate refuses a bundle it names the exact unbundled references,
    and the publisher swallowed all of them.
  - `dev/wizard-simulate.py` checks the return code of every subprocess it runs
    except the last one. A failed `--status` printed the bare line `STATUS: `,
    discarded stderr, and the harness RETURNED 0 - a replay tool reporting
    success over the command that was meant to confirm the replay. It also
    guarded its canned file with `or {}`, which catches a MISSING body and lets
    a present-but-wrong one through to `.get`.

Fixed 2026-08-24.
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


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# compact-watch.py — a read that did not happen is not a transition
# ===========================================================================

@pytest.fixture(scope="module")
def cw():
    return _load("compact_watch_mod", "scripts/dev/compact-watch.py")


@pytest.fixture()
def state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"used_percentage": 40, "current_bucket": 35,
                                "compact_request_count": 2}), encoding="utf-8")
    return path


def _snap(cw, state, tmp_path):
    return cw._snapshot(state, tmp_path / "archive", "slug")


def test_a_torn_write_is_refused_not_read_as_nulls(cw, state, tmp_path):
    """This is the defect: `{}` and "unreadable" became the same snapshot."""
    state.write_text('{"used_percentage": 40, "current_buck', encoding="utf-8")
    with pytest.raises(cw.Unreadable):
        _snap(cw, state, tmp_path)


@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "42"])
def test_a_state_that_is_not_an_object_is_refused(cw, state, tmp_path, body):
    """`.get` on a list is an AttributeError, past any JSON handler."""
    state.write_text(body, encoding="utf-8")
    with pytest.raises(cw.Unreadable):
        _snap(cw, state, tmp_path)


@pytest.mark.parametrize(
    "raw",
    [b'{"used_percentage": 40, "note": "caf\xe9"}', b"\xff\xfe{}", b"\x80\x81"],
    ids=["latin1-inside", "bad-lead-bytes", "raw-bytes"],
)
def test_a_state_file_that_is_not_utf8_is_refused_as_unreadable(cw, state,
                                                                 tmp_path, raw):
    """The WIDTH of `except ValueError`, which nothing was measuring.

    `_read_state` calls `read_text(encoding="utf-8")` with no `errors=`, so a
    state file holding non-UTF-8 bytes raises `UnicodeDecodeError` BEFORE
    `json.loads` ever runs. It is a `ValueError` and a SIBLING of
    `json.JSONDecodeError`, and the comment on that `except` names only the
    JSON one. MEASURED 2026-09-01: narrowing the clause to
    `except json.JSONDecodeError` left this file green at 39 passed, and the
    decode error then escaped `_snapshot` as something other than `Unreadable` -
    which is precisely the "a read that did not happen was logged as state
    moving" failure this whole section exists to prevent, arriving one
    expression earlier.

    A torn write is exactly how these bytes appear: a partial write can land
    mid-multibyte-character, so the truncated JSON and the truncated UTF-8 are
    two faces of the same event.
    """
    state.write_bytes(raw)
    with pytest.raises(cw.Unreadable):
        _snap(cw, state, tmp_path)


@pytest.mark.parametrize("history", ["null", "[]"], ids=["null", "empty"])
def test_a_null_compact_history_is_zero_length_not_a_crash(cw, state, tmp_path,
                                                            history):
    """`or []`, not `.get(key, [])`, and the difference had no witness.

    A state file carrying `"compact_history": null` is a PRESENT key with a null
    value, so the default never applies and `len(None)` is a TypeError - out of
    the watcher, out of `main`, ending the instrument. MEASURED 2026-09-01:
    rewriting the line as `state.get("compact_history", [])` left this file
    green at 39 passed, because every fixture omits the key entirely.

    `_compact_history_last` carries the same `or [None]` guard for the same
    reason, so it is asserted here beside it.
    """
    state.write_text(
        '{"used_percentage": 40, "compact_history": %s}' % history,
        encoding="utf-8")
    snap = _snap(cw, state, tmp_path)
    assert snap["_compact_history_len"] == 0
    assert snap["_compact_history_last"] is None


def test_a_real_compact_history_is_still_measured(cw, state, tmp_path):
    """Anchor: hard-coding zero would pass both rows above."""
    state.write_text(
        '{"used_percentage": 40, "compact_history": ["a", "b"]}', encoding="utf-8")
    snap = _snap(cw, state, tmp_path)
    assert snap["_compact_history_len"] == 2
    assert snap["_compact_history_last"] == "b"


def test_an_absent_state_file_is_genuinely_empty(cw, tmp_path):
    """Anchor: 'not written yet' IS empty, and must not raise."""
    snap = cw._snapshot(tmp_path / "never.json", tmp_path / "arch", "slug")
    assert snap["used_percentage"] is None
    assert snap["_compact_history_len"] == 0


def test_a_readable_state_still_snapshots(cw, state, tmp_path):
    snap = _snap(cw, state, tmp_path)
    assert snap["used_percentage"] == 40
    assert snap["compact_request_count"] == 2


def test_the_watched_keys_are_all_present_in_a_snapshot(cw, state, tmp_path):
    snap = _snap(cw, state, tmp_path)
    assert set(cw.WATCHED) <= set(snap)


def _run_watch(cw, monkeypatch, tmp_path, state, log_dir, polls):
    """Drive `main` for exactly len(polls) iterations, mutating state per poll."""
    monkeypatch.setattr(cw.CP, "safe_slug", lambda s: "slug")
    monkeypatch.setattr(cw.CP, "project_root", lambda: tmp_path)
    monkeypatch.setattr(cw.CP, "state_path", lambda project, slug: state)
    monkeypatch.setattr(cw.CP, "engine_root", lambda: tmp_path)
    monkeypatch.setattr(cw.CP, "handoff_dir",
                        lambda project, engine: tmp_path / "archive")
    monkeypatch.setattr(cw, "get_data_root", lambda: log_dir)

    steps = iter(polls)
    clock = [0.0]

    def _sleep(_s):
        clock[0] += 1.0
        step = next(steps, None)
        if step is not None:
            state.write_text(step, encoding="utf-8")
        else:
            clock[0] += 10_000.0      # past the deadline; the loop ends

    monkeypatch.setattr(cw.time, "sleep", _sleep)
    monkeypatch.setattr(cw.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sys, "argv",
                        ["compact-watch.py", "--session", "sess", "--minutes", "1"])
    assert cw.main() == 0
    log = log_dir / "outputs" / "operations" / "compact-watch" / "slug.jsonl"
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]


def test_a_torn_read_produces_no_change_event(cw, monkeypatch, tmp_path, state):
    """The whole finding, end to end: the torn read must cost one honest line,
    not a phantom transition on every watched key and another one back."""
    good = json.dumps({"used_percentage": 40, "current_bucket": 35,
                       "compact_request_count": 2})
    records = _run_watch(cw, monkeypatch, tmp_path, state, tmp_path / "data",
                         polls=['{"used_percentage": 40, "current_buck', good])

    events = [r["event"] for r in records]
    assert "read_unparsed" in events, "the unreadable poll must be recorded"
    assert "change" not in events, (
        f"a read that did not happen was logged as state moving: {events}"
    )
    # And the BASELINE survived it. Dropping `previous` on a failed read is the
    # same defect one step quieter: the next good poll then re-announces the
    # whole state as a first observation instead of continuing the sequence.
    assert "first_read" not in events, (
        f"the torn read discarded the baseline it never replaced: {events}"
    )


def test_the_unparsed_record_carries_the_reason(cw, monkeypatch, tmp_path, state):
    records = _run_watch(cw, monkeypatch, tmp_path, state, tmp_path / "data",
                         polls=["[]"])
    unparsed = [r for r in records if r["event"] == "read_unparsed"]
    assert unparsed and "list" in unparsed[0]["error"]


def test_a_real_change_is_still_logged(cw, monkeypatch, tmp_path, state):
    """Anchor: suppressing phantom events must not suppress real ones."""
    moved = json.dumps({"used_percentage": 40, "current_bucket": 35,
                        "compact_request_count": 3})
    records = _run_watch(cw, monkeypatch, tmp_path, state, tmp_path / "data",
                         polls=[moved])
    changes = [r for r in records if r["event"] == "change"]
    assert changes, [r["event"] for r in records]
    assert changes[0]["changed"]["compact_request_count"] == [2, 3]


def test_a_drifting_percentage_alone_is_still_not_an_event(cw, monkeypatch,
                                                           tmp_path, state):
    """Anchor: the pre-existing noise filter must survive the rewrite."""
    drift = json.dumps({"used_percentage": 41, "current_bucket": 35,
                        "compact_request_count": 2})
    records = _run_watch(cw, monkeypatch, tmp_path, state, tmp_path / "data",
                         polls=[drift])
    assert "change" not in [r["event"] for r in records]


def test_an_unreadable_state_at_startup_does_not_seed_the_baseline(cw, monkeypatch,
                                                                   tmp_path):
    """Seeding `previous` from a failed read makes the first good poll look
    like every key moving at once."""
    state = tmp_path / "state.json"
    state.write_text("{ broken", encoding="utf-8")
    good = json.dumps({"used_percentage": 40})
    records = _run_watch(cw, monkeypatch, tmp_path, state, tmp_path / "data",
                         polls=[good])

    start = records[0]
    assert start["event"] == "watch_start"
    assert start["state"] is None
    assert start["unreadable"]
    events = [r["event"] for r in records]
    assert "first_read" in events
    assert "change" not in events, f"the first real read is not a change: {events}"


# ===========================================================================
# publish-marketplace.py — the child's diagnostic reaches the operator
# ===========================================================================

@pytest.fixture(scope="module")
def pm():
    return _load("publish_marketplace_mod", "scripts/dev/publish-marketplace.py")


def test_a_failed_command_prints_what_it_said(pm, capsys):
    """`capture_output=True` with `check=True` reduced the plugin gate's list of
    unbundled references to 'returned non-zero exit status 3'."""
    with pytest.raises(subprocess.CalledProcessError):
        pm._run([sys.executable, "-c",
                 "import sys; print('gate FAILED: scripts/ghost.py', file=sys.stderr); "
                 "sys.exit(3)"])
    err = capsys.readouterr().err
    assert "gate FAILED: scripts/ghost.py" in err, (
        f"the child's diagnostic was swallowed: {err.strip()!r}"
    )
    assert "exited 3" in err


def test_stdout_is_surfaced_too(pm, capsys):
    with pytest.raises(subprocess.CalledProcessError):
        pm._run([sys.executable, "-c", "print('detail on stdout'); raise SystemExit(1)"])
    assert "detail on stdout" in capsys.readouterr().err


def test_the_exception_type_is_unchanged(pm):
    """Callers that already handle CalledProcessError must keep working, and
    the captured streams must still be ON the exception."""
    with pytest.raises(subprocess.CalledProcessError) as exc:
        pm._run([sys.executable, "-c", "import sys; print('x', file=sys.stderr); sys.exit(7)"])
    assert exc.value.returncode == 7
    assert "x" in exc.value.stderr


def test_a_successful_command_prints_nothing_extra(pm, capsys):
    """Anchor: the noise appears on failure only."""
    proc = pm._run([sys.executable, "-c", "print('fine')"])
    assert proc.returncode == 0
    assert proc.stdout.strip() == "fine"
    assert capsys.readouterr().err == ""


def test_check_false_still_returns_the_failure_quietly(pm, capsys):
    """Anchor: the identity probe calls `_run(..., check=False)` and reads the
    result; it must not print or raise on a non-zero exit."""
    proc = pm._run([sys.executable, "-c", "raise SystemExit(4)"], check=False)
    assert proc.returncode == 4
    assert capsys.readouterr().err == ""


# ===========================================================================
# wizard-simulate.py — the last command is checked like the rest
# ===========================================================================

@pytest.fixture(scope="module")
def ws():
    return _load("wizard_simulate_mod", "scripts/dev/wizard-simulate.py")


@pytest.fixture()
def harness(ws, tmp_path, monkeypatch):
    """A workspace plus a stub apply script whose behaviour the test picks."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scripts = tmp_path / "scripts"
    (scripts / "dev").mkdir(parents=True)
    (scripts / "apply-wizard-answers.py").write_text(
        "import sys\n"
        "if '--status' in sys.argv:\n"
        "    print('boom', file=sys.stderr)\n"
        "    raise SystemExit(9)\n"
        "print('ok')\n",
        encoding="utf-8")
    monkeypatch.setattr(ws, "__file__", str(scripts / "dev" / "wizard-simulate.py"))
    return workspace, tmp_path


def _canned(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "canned.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_failed_status_is_not_reported_as_success(ws, harness, tmp_path, capsys):
    workspace, root = harness
    answers = _canned(root, "answers: {}\n")
    rc = ws.main(["--answers", str(answers), "--workspace", str(workspace)])
    assert rc == 9, "the harness returned 0 over a --status that exited 9"
    err = capsys.readouterr().err
    assert "boom" in err, "and it discarded the reason as well as the code"


def test_a_working_status_still_returns_zero(ws, harness, tmp_path, capsys):
    """Anchor: checking the code must not fail the happy path."""
    workspace, root = harness
    (root / "scripts" / "apply-wizard-answers.py").write_text(
        "print('all answered')\n", encoding="utf-8")
    answers = _canned(root, "answers: {}\n")
    assert ws.main(["--answers", str(answers), "--workspace", str(workspace)]) == 0
    assert "all answered" in capsys.readouterr().out


def test_a_failed_answer_step_stops_the_replay(ws, harness, tmp_path, capsys):
    """The premise of the whole finding, unmeasured.

    The fix for `--status` is justified as "Checked, like every other call above
    it" - and neither of those other two calls had a test. MEASURED 2026-09-01:
    deleting the `--question` step's `if result.returncode != 0` left this file
    green at 45 passed. A replay that carries on after an answer was rejected
    applies the REST of the answers to a workspace whose earlier state never
    landed, which is worse than the `--status` defect it sits above: that one
    only misreported, this one keeps writing.
    """
    workspace, root = harness
    (root / "scripts" / "apply-wizard-answers.py").write_text(
        "import sys\n"
        "if '--question' in sys.argv:\n"
        "    print('rejected: unknown question', file=sys.stderr)\n"
        "    raise SystemExit(5)\n"
        "print('ok')\n", encoding="utf-8")
    answers = _canned(root, "answers: {tone: warm, cadence: weekly}\n")

    rc = ws.main(["--answers", str(answers), "--workspace", str(workspace)])

    assert rc == 5, "the replay reported the apply script's failure as success"
    err = capsys.readouterr().err
    assert "FAILED on" in err and "rejected: unknown question" in err, err


def test_a_failed_skip_step_stops_the_replay(ws, harness, tmp_path, capsys):
    """The second of the two, and it has its own message and its own branch.

    Measured 2026-09-01: deleting the `--skip` step's return-code check left
    this file green at 45 passed. `test_a_real_skipped_list_still_runs` only
    covers the success direction.
    """
    workspace, root = harness
    (root / "scripts" / "apply-wizard-answers.py").write_text(
        "import sys\n"
        "if '--skip' in sys.argv:\n"
        "    print('cannot skip a required question', file=sys.stderr)\n"
        "    raise SystemExit(6)\n"
        "print('ok')\n", encoding="utf-8")
    body = "answers: {}\nskipped: [calendar_policy, tone]\n"

    rc = ws.main(["--answers", str(_canned(root, body)),
                  "--workspace", str(workspace)])

    assert rc == 6
    err = capsys.readouterr().err
    assert "FAILED on skip calendar_policy" in err, err
    assert "cannot skip a required question" in err


@pytest.mark.parametrize("body", ["answers: {\n", "a: [1,\n", "\ta: 1\n"],
                         ids=["unclosed-map", "unclosed-list", "tab-indent"])
def test_an_unparseable_canned_file_is_refused_with_its_reason(ws, harness,
                                                                tmp_path, body,
                                                                capsys):
    """`yaml.YAMLError` in the read guard, which had no witness.

    Every canned fixture in this file is valid YAML, so the clause covering the
    PARSE was never exercised - only the shape guards below it. MEASURED
    2026-09-01: narrowing that `except (OSError, yaml.YAMLError)` to `(OSError,)`
    left this file green at 45 passed, and a hand-edited canned file then ended
    the harness with a raw YAML traceback instead of exit 2 and a sentence.
    """
    workspace, root = harness
    rc = ws.main(["--answers", str(_canned(root, body)),
                  "--workspace", str(workspace)])
    assert rc == 2
    assert "could not read" in capsys.readouterr().err


def test_a_canned_file_that_is_not_utf8_is_refused_not_a_traceback(
        ws, harness, tmp_path, capsys):
    """A LIVE defect found here, and the same class as the `--status` one.

    `read_text(encoding="utf-8")` runs before `yaml.safe_load`, and the guard
    named `OSError` and `yaml.YAMLError`. `UnicodeDecodeError` is a `ValueError`
    and `yaml.YAMLError` descends from `Exception`, so neither name covered the
    DECODE. MEASURED 2026-09-01 before the fix: a canned file holding one
    latin-1 byte produced a raw `UnicodeDecodeError` traceback and exit 1 -
    which is exactly what the comment above the `is_file()` check says was
    removed for a typo'd path, arriving one line further down.
    """
    workspace, root = harness
    bad = root / "canned.yaml"
    bad.write_bytes(b"answers: {tone: caf\xe9}\n")

    rc = ws.main(["--answers", str(bad), "--workspace", str(workspace)])

    assert rc == 2, "an undecodable canned file crashed instead of being refused"
    assert "could not read" in capsys.readouterr().err


def test_a_missing_canned_file_keeps_its_own_message(ws, harness, tmp_path,
                                                      capsys):
    """The `is_file()` pre-check ABOVE the try, which answers first.

    Worth pinning separately: a reader looking only at the `except (OSError,
    ...)` would conclude a missing file lands there, and it does not - the exit
    one block above is the branch that actually fires. Both must stay, and they
    must keep saying different things, because "not found" and "found but
    unreadable" are different instructions to the operator.
    """
    workspace, root = harness
    rc = ws.main(["--answers", str(root / "no-such-file.yaml"),
                  "--workspace", str(workspace)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "answers file not found" in err, err
    assert "could not read" not in err


@pytest.mark.parametrize("body", ["- a\n- b\n", "just a string\n", "42\n"])
def test_a_canned_file_that_is_not_a_mapping_is_refused(ws, harness, tmp_path,
                                                        body, capsys):
    """`or {}` guards a MISSING body; a list is non-empty and reaches `.get`."""
    workspace, root = harness
    answers = _canned(root, body)
    assert ws.main(["--answers", str(answers), "--workspace", str(workspace)]) == 2
    assert "not a mapping" in capsys.readouterr().err


def test_an_empty_canned_file_is_still_allowed(ws, harness, tmp_path):
    """Anchor: `answers.yaml` with nothing in it means nothing to replay."""
    workspace, root = harness
    (root / "scripts" / "apply-wizard-answers.py").write_text(
        "print('none')\n", encoding="utf-8")
    assert ws.main(["--answers", str(_canned(root, "\n")),
                    "--workspace", str(workspace)]) == 0


@pytest.mark.parametrize("body", ["answers: [1, 2]\n", "answers: a string\n"])
def test_a_non_mapping_answers_block_is_refused(ws, harness, tmp_path, body,
                                                capsys):
    workspace, root = harness
    assert ws.main(["--answers", str(_canned(root, body)),
                    "--workspace", str(workspace)]) == 2
    assert "`answers:`" in capsys.readouterr().err


def test_a_string_skipped_block_is_refused_not_iterated_per_letter(ws, harness,
                                                                   tmp_path,
                                                                   capsys):
    """A bare string iterates per CHARACTER, firing one --skip per letter."""
    workspace, root = harness
    body = "answers: {}\nskipped: calendar_policy\n"
    assert ws.main(["--answers", str(_canned(root, body)),
                    "--workspace", str(workspace)]) == 2
    assert "`skipped:`" in capsys.readouterr().err


def test_a_real_skipped_list_still_runs(ws, harness, tmp_path, capsys):
    """Anchor: the list form is the documented one and must survive."""
    workspace, root = harness
    (root / "scripts" / "apply-wizard-answers.py").write_text(
        "print('done')\n", encoding="utf-8")
    body = "answers: {}\nskipped: [calendar_policy, tone]\n"
    assert ws.main(["--answers", str(_canned(root, body)),
                    "--workspace", str(workspace)]) == 0
    out = capsys.readouterr().out
    assert "SKIP calendar_policy" in out and "SKIP tone" in out


def test_a_ceo_master_workspace_is_still_refused(ws, harness, tmp_path, capsys):
    """Anchor: the safety guard is the reason this harness exists in dev/."""
    workspace, root = harness
    (workspace / ".workspace-identity.json").write_text(
        json.dumps({"type": "ceo-master"}), encoding="utf-8")
    assert ws.main(["--answers", str(_canned(root, "answers: {}\n")),
                    "--workspace", str(workspace)]) == 2
    assert "REFUSED" in capsys.readouterr().err


# ===========================================================================
# exec_lines.py — read in this shard, no defect found; the definition is pinned
# ===========================================================================

@pytest.fixture(scope="module")
def el():
    return _load("exec_lines_mod", "scripts/dev/exec_lines.py")


def test_a_hash_inside_a_string_is_not_a_comment(el):
    """The docstring's claim about `tokenize`, asserted rather than trusted.

    Note what does the work: `tokenize` never emits a COMMENT token for a `#`
    inside a string literal, so this passes with or without the guard on the
    next line down. The guard is about something else - see the trailing-comment
    test below - and a first draft of this file credited it with this behaviour.
    """
    assert el.count_lines('x = "# not a comment"\n') == (1, 1)


def test_a_line_with_a_trailing_comment_is_still_executable(el):
    """What the `tok.line[: tok.start[1]].strip()` guard actually buys. Without
    it, ANY line carrying a comment is excluded, so `x = 1  # note` disappears
    from the count and every measured file shrinks by its commented lines."""
    assert el.count_lines("x = 1  # note\ny = 2\n") == (2, 2)


def test_a_comment_only_line_is_excluded(el):
    """Anchor: the guard must not stop the exclusion it is guarding."""
    assert el.count_lines("# just a comment\nx = 1\n") == (1, 2)


def test_a_multiline_string_used_as_a_value_is_executable(el):
    """Its other claim: `ast` distinguishes a docstring from a string VALUE."""
    assert el.count_lines('x = """a\nb\nc"""\n')[0] == 3


def test_a_leading_expression_that_is_not_a_constant_is_not_a_docstring(el):
    """The `isinstance(first.value, ast.Constant)` half of the docstring test.
    A module opening on a bare call has `body[0] = Expr(Call)`, and reaching for
    `.value.value` on that is an AttributeError, not a miscount."""
    assert el.count_lines('print("x")\ny = 1\n') == (2, 2)


def test_a_module_docstring_is_excluded(el):
    assert el.count_lines('"""doc\nline two"""\nx = 1\n') == (1, 3)


def test_a_blank_line_outside_a_docstring_is_excluded(el):
    """The blank-line rule on its own. Put the blank INSIDE the docstring and
    the docstring range hides whether this rule ran at all."""
    assert el.count_lines("x = 1\n\ny = 2\n") == (2, 3)


def test_a_blank_line_inside_a_docstring_is_counted_once(el):
    """It is both blank and inside the docstring; a list would double-count."""
    assert el.count_lines('"""doc\n\nend"""\nx = 1\n') == (1, 4)


def test_a_tokenize_failure_is_reported_as_one(el):
    """The handler exists for its message: an unterminated string fails BOTH
    tokenize and ast, so swallowing it still raises - just with a parse error
    that says nothing about which stage gave up."""
    with pytest.raises(SyntaxError, match="tokenize failed"):
        el.count_lines('"""unterminated\n')


def test_unparseable_source_raises_syntaxerror(el):
    with pytest.raises(SyntaxError):
        el.count_lines("def f(:\n")


# The eight characters `str.splitlines()` breaks on and Python's tokenizer does
# not. Written out by hand from the `str.splitlines` documentation, not derived
# from the code under test, and built with `chr()` so this file carries none of
# them literally (`.claude/rules/hidden-chars.md`).
NON_TERMINATORS = {
    "U+000B line tabulation": 0x0B,
    "U+000C form feed": 0x0C,
    "U+001C file separator": 0x1C,
    "U+001D group separator": 0x1D,
    "U+001E record separator": 0x1E,
    "U+0085 next line": 0x85,
    "U+2028 line separator": 0x2028,
    "U+2029 paragraph separator": 0x2029,
}


def test_the_separator_corpus_really_does_fool_splitlines():
    """A vacuous parametrize otherwise. Every codepoint below must actually
    split under `str.splitlines()` and must NOT be a newline to Python, or the
    rows that follow prove nothing about the difference between the two."""
    for label, cp in NON_TERMINATORS.items():
        src = "x = 1" + chr(cp) + "y = 2"
        assert len(src.splitlines()) == 2, label
        assert "\n" not in src, label


@pytest.mark.parametrize("label,cp", sorted(NON_TERMINATORS.items()),
                         ids=sorted(NON_TERMINATORS))
def test_a_line_holding_a_non_terminator_is_still_one_line(el, label, cp):
    """A LIVE defect found here: `source.splitlines()` was cutting on eight
    characters the tokenizer treats as ordinary text.

    Both returned numbers were inflated, and worse, every line number after the
    first occurrence fell out of step with the ones `tokenize` and `ast` report
    - so a blank-line or docstring exclusion was subtracted from the wrong line.

    MEASURED 2026-09-01, and reachable on this repository rather than in theory:
    `scripts/utils/sanitize_text.py` is tracked engine source, it contains
    U+2028 and U+2029, and it came back as 272 physical lines against a true
    268. Two test files over-reported by 2 apiece. Minimally, the row below was
    `(2, 2)`.
    """
    assert el.count_lines('x = "a' + chr(cp) + 'b"\n') == (1, 1), label
    # And in a COMMENT, where the same cut lands on the exclusion arithmetic.
    assert el.count_lines("x = 1  # note" + chr(cp) + "more\n") == (1, 1), label


def test_the_repositorys_own_sources_are_counted_at_their_real_length(el):
    """The end-to-end version, over the files that actually carry these
    characters, so the row above cannot pass over a corpus of one-liners.

    The expected length is computed HERE, by the definition Python itself uses
    (split on newline, drop the trailing empty), rather than read back from the
    function under test.
    """
    carriers = [p for p in (ROOT / "scripts" / "utils" / "sanitize_text.py",
                            ROOT / "tests" / "test_session_scope_line_splitting.py")
                if p.is_file()]
    assert carriers, "the corpus is empty, so this asserts nothing"
    checked = 0
    for path in carriers:
        src = path.read_text(encoding="utf-8")
        if not any(chr(cp) in src for cp in NON_TERMINATORS.values()):
            continue
        checked += 1
        expected = len(src.split("\n")) - (1 if src.endswith("\n") else 0)
        assert el.count_lines(src)[1] == expected, path.name
    assert checked, (
        "no tracked file still carries one of these characters, so this test "
        "measured nothing; keep the synthetic rows above, which do not depend "
        "on the corpus")


@pytest.mark.parametrize("src,expected", [
    ("x = 1\r\ny = 2\r\n", (2, 2)),
    ("x = 1\ry = 2\r", (2, 2)),
    ("x = 1", (1, 1)),
    ("", (0, 0)),
    ("\n\n", (0, 2)),
])
def test_the_real_line_endings_and_the_edges_are_unchanged(el, src, expected):
    """Anchor for the fix: normalising `\\r\\n` and `\\r` and dropping the
    trailing empty element must not move the ordinary answers. A bare
    `split("\\n")` would report `x = 1\\n` as two lines and CRLF source as one."""
    assert el.count_lines(src) == expected
