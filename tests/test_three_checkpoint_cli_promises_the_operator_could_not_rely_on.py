"""Three claims `scripts/checkpoint-paths.py` made and did not keep.

Found by the 2026-08-24 engine audit campaign (shard `scripts-03-p4`), verified
still present on 2026-09-02, fixed the same day. All three are about what the
operator is told versus what the tool did, which is the shape
`.claude/rules/scope-claims.md` exists for.

1. An explicit `--unattended off` (or `--auto off`) typed alongside
   `--compact-at N` was applied and then silently undone. `main` runs every
   action flag in DECLARATION order and `--compact-at` is last, so
   `compact_at_switch` re-raised the switch the operator had just lowered and
   printed "Only you lower it: --unattended off" about the very words he had
   typed.

2. `compact_history`'s docstring says "newest last" over a sort of the
   FILENAMES, which are keyed by an opaque session id carrying no timestamp. A
   reader scanning the bottom of the report for the most recent session read
   whichever slug happened to sort last.

3. `compact_at_switch` refused a threshold at or below the current context fill
   on a reading taken BEFORE the lock. `checkpoint-statusline.py` writes that
   same file on every render, so a render landing in the gap let a threshold
   through that would fire at the very next pause, which is the outcome the
   refusal exists to prevent.

Every test drives the real CLI in a scratch state directory pinned through
`HEADING_OS_STATE_DIR`, so nothing here touches the operator's own
`.claude/state/`.
"""
import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLI = ROOT / "scripts" / "checkpoint-paths.py"


def _cli(name="checkpoint_paths_cli_promises"):
    spec = importlib.util.spec_from_file_location(name, str(CLI))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolate(monkeypatch, tmp_path, session="promise-session"):
    """Point the CLI at a scratch state dir and give it a session to key on.

    `HEADING_OS_STATE_DIR` only redirects when the project root IS the engine
    clone (see `state_root`), so `CLAUDE_PROJECT_DIR` is left alone and the pin
    does the work.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HEADING_OS_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.chdir(ROOT)
    return state / f"checkpoint-{session}.json"


def _run(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


# ============================================================
# 1. The operator's explicit negative wins over the convenience raise
# ============================================================


def test_an_unattended_off_typed_with_a_threshold_is_not_raised_back(
        monkeypatch, tmp_path):
    """The whole defect, end to end, through the real CLI.

    Before the fix this left `session_unattended` True and `session_auto` True,
    after the operator had typed `off` in that same command.
    """
    path = _isolate(monkeypatch, tmp_path, "lowered-then-raised")
    module = _cli()

    code, out, _err = _run(module, ["--unattended", "off", "--compact-at", "35"])

    assert code == 0, out
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["session_unattended"] is False, (
        "the operator typed --unattended off in this same command and the "
        "threshold raised it straight back")
    assert state.get("session_auto") is not True, (
        "raise_unattended also raises session_auto, so the re-raise turned on a "
        "second switch he never asked for")
    assert state["session_hard_threshold"] == 35, (
        "the threshold itself must still be set; refusing the raise is not "
        "refusing the number")
    assert "you lowered a switch in this same command" in out, (
        "the operator has to be told the raise was skipped and why; a silent "
        "skip is the same scope claim in the other direction")


def test_an_auto_off_typed_with_a_threshold_is_not_raised_back(
        monkeypatch, tmp_path):
    """`--auto off` is the second half of the same defect.

    `raise_unattended` sets `session_auto` too, so a threshold accepted after an
    explicit `--auto off` used to hand the session back the mode it had just
    been told to drop.
    """
    path = _isolate(monkeypatch, tmp_path, "auto-lowered")
    module = _cli("cli_auto_lowered")

    code, _out, _err = _run(module, ["--auto", "off", "--compact-at", "40"])

    assert code == 0
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["session_auto"] is False, "--auto off was overridden"
    assert state.get("session_unattended") is not True


def test_a_threshold_on_its_own_still_raises_the_switch(monkeypatch, tmp_path):
    """The anchor. The fix must not turn the raise off for everyone.

    Operator directive 2026-08-22: he types the two together, so an accepted
    number raises unattended (and auto with it). Only an explicit `off` in the
    SAME invocation suppresses that.
    """
    path = _isolate(monkeypatch, tmp_path, "plain-threshold")
    module = _cli("cli_plain_threshold")

    code, out, _err = _run(module, ["--compact-at", "35"])

    assert code == 0
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["session_unattended"] is True, (
        "a bare --compact-at still has to raise the mode, or the number is set "
        "and nothing ever acts on it")
    assert state["session_auto"] is True
    assert "unattended is now on as well" in out


def test_main_tells_the_threshold_whether_a_switch_was_lowered(monkeypatch):
    """The seam itself, so the wiring cannot be reverted without a red test.

    The two behaviour tests above go through the state file. This one pins the
    argument `main` computes, which is where the decision is made.
    """
    module = _cli("cli_may_raise_seam")
    seen = []
    monkeypatch.setattr(module, "unattended_switch", lambda v: 0)
    monkeypatch.setattr(module, "auto_switch", lambda v: 0)
    monkeypatch.setattr(
        module, "compact_at_switch",
        lambda v, may_raise=True: seen.append(may_raise) or 0)

    module.main(["--compact-at", "35"])
    module.main(["--unattended", "off", "--compact-at", "35"])
    module.main(["--auto", "off", "--compact-at", "35"])
    module.main(["--unattended", "on", "--compact-at", "35"])

    assert seen == [True, False, False, True], (
        f"may_raise was {seen}; only an explicit `off` in the same invocation "
        "may suppress the raise")


# ============================================================
# 2. "Newest last" has to be true of the order actually printed
# ============================================================


def _history(path: Path, at: str, trigger: str) -> None:
    path.write_text(json.dumps({"compact_history": [
        {"at": at, "trigger": trigger, "used_pct_at_or_above": 40,
         "configured": None},
    ]}), encoding="utf-8")


def test_the_compaction_report_prints_the_newest_session_last(
        monkeypatch, tmp_path):
    """Chronological order, against filenames that sort the other way.

    `zzz` holds the OLDEST entry and `aaa` the newest, so a filename sort puts
    the newest first: the exact inversion a reader scanning bottom-up gets
    wrong. Session ids are opaque and carry no timestamp, so this arrangement is
    ordinary rather than contrived.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HEADING_OS_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "reader")
    monkeypatch.chdir(ROOT)

    _history(state / "checkpoint-zzz.json", "2026-01-01T00:00:00+00:00", "old")
    _history(state / "checkpoint-mmm.json", "2026-05-01T00:00:00+00:00", "mid")
    _history(state / "checkpoint-aaa.json", "2026-08-01T00:00:00+00:00", "new")

    module = _cli("cli_history_order")
    code, out, _err = _run(module, ["--compact-history"])

    assert code == 0
    order = [line.strip() for line in out.splitlines()
             if line.strip() in ("aaa", "mmm", "zzz")]
    assert order == ["zzz", "mmm", "aaa"], (
        f"printed {order}; the docstring promises newest last and the sort key "
        "must be the newest recorded `at`, not the session slug")


def test_a_session_whose_history_is_all_malformed_is_still_listed(
        monkeypatch, tmp_path):
    """Sorting must not quietly drop a session from the report.

    The pre-fix loop printed the session header before filtering entries, so a
    history of nothing but non-dicts still appeared. Losing it to tidy the sort
    would be a second defect in the place the first one was.
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HEADING_OS_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "reader")
    monkeypatch.chdir(ROOT)

    (state / "checkpoint-junk.json").write_text(
        json.dumps({"compact_history": ["not-a-dict", 7]}), encoding="utf-8")
    _history(state / "checkpoint-real.json", "2026-08-01T00:00:00+00:00", "new")

    module = _cli("cli_history_junk")
    code, out, _err = _run(module, ["--compact-history"])

    assert code == 0
    assert "junk" in out, "a session with an unreadable history vanished"
    assert "real" in out


# ============================================================
# 3. The refusal reads the fill under the lock that protects it
# ============================================================


def test_the_threshold_refusal_reads_the_fill_written_after_it_started(
        monkeypatch, tmp_path):
    """The race, made deterministic at the seam the statusline writes through.

    `locked_state` re-reads the file inside the lock. The stand-in below writes
    a HIGHER `used_percentage` at the moment the lock is taken, which is exactly
    what a status-line render landing in that window does. Before the fix the
    guard had already been evaluated against the stale 50 and wrote a threshold
    of 60 over a live fill of 65.
    """
    path = _isolate(monkeypatch, tmp_path, "raced-fill")
    path.write_text(json.dumps({"used_percentage": 50}), encoding="utf-8")

    module = _cli("cli_refusal_race")
    real_locked = module.CP.locked_state

    def racing_locked_state(target, **kwargs):
        # The concurrent writer, landing between the old unlocked read and the
        # locked write. One shot: the second call would fight the CLI's own.
        current = json.loads(target.read_text(encoding="utf-8"))
        if current.get("used_percentage") == 50:
            current["used_percentage"] = 65
            target.write_text(json.dumps(current), encoding="utf-8")
        return real_locked(target, **kwargs)

    monkeypatch.setattr(module.CP, "locked_state", racing_locked_state)

    code, _out, err = _run(module, ["--compact-at", "60"])

    assert code == 2, (
        "a threshold at or below the LIVE fill has to be refused; the guard "
        "read a stale copy taken before the lock")
    assert "65.0% used" in err, (
        f"the refusal must quote the reading it actually judged: {err!r}")
    state = json.loads(path.read_text(encoding="utf-8"))
    assert "session_hard_threshold" not in state, (
        "a refused value must leave the state file unmutated")


def test_an_accepted_threshold_still_writes_under_the_same_lock(
        monkeypatch, tmp_path):
    """The anchor: moving the guard inside the lock must not stop the write."""
    path = _isolate(monkeypatch, tmp_path, "accepted-fill")
    path.write_text(json.dumps({"used_percentage": 20}), encoding="utf-8")

    module = _cli("cli_refusal_accept")
    code, out, _err = _run(module, ["--compact-at", "60"])

    assert code == 0, out
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["session_hard_threshold"] == 60
    assert state["used_percentage"] == 20, "the reading must survive the write"


def test_an_unparseable_fill_is_not_a_refusal(monkeypatch, tmp_path):
    """Hand-edited state files are an anticipated input, per `_session_hard`.

    A CLI that tracebacks on a bad sample is worse than one that says it could
    not check.
    """
    path = _isolate(monkeypatch, tmp_path, "junk-fill")
    path.write_text(json.dumps({"used_percentage": "lots"}), encoding="utf-8")

    module = _cli("cli_refusal_junk")
    code, out, _err = _run(module, ["--compact-at", "60"])

    assert code == 0, out
    assert "has not reported a usable context reading" in out
    assert json.loads(path.read_text(encoding="utf-8"))[
        "session_hard_threshold"] == 60
