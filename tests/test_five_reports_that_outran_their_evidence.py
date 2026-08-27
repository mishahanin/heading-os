"""Five places where the code said more than it had established.

The shard-32 sibling was about guards that ADMITTED what they refuse. These are
about tools that REPORTED more than they knew, plus one that spends money on a
question nobody asked. Each was reproduced by running it first.

1. `perplexity-research.py` sent an EMPTY question to a billed endpoint whenever
   stdin was a pipe with nothing in it - cron, a daemon, any `< /dev/null`.
2. `pid_liveness`'s Windows branch read another user's process as dead, which is
   the exact defect its own module docstring documents and fixes for POSIX.
3. `watchdog_core` counted alerts ATTEMPTED and the CLI printed them as "fired",
   while `alert()` had been returning which channels actually took them.
4. `html-to-pdf.py` printed every error to STDOUT, the same stream a caller
   reads the generated path from.
5. `migrate-data.py --apply --dry-run` never called `up(..., dry_run=True)`, so
   the contract every migration must honor was unreachable.
"""
from __future__ import annotations

import ast
import ctypes
import importlib.util
import io
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- source claims are asked of the AST, never grepped ---------------------
#
# The first version of the three tests below matched raw text and all three
# failed on their own explaining COMMENTS: the comment "the word now matches the
# method" quotes the old wording, and the pid_liveness comment names
# `ctypes.windll` in order to say why it must not be used. A fourth matched a
# `print(` whose `file=sys.stderr` sat on the next line. Grep reads a file;
# these questions are about CODE, so they go to the parser.

def _tree(rel: str) -> ast.AST:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _string_literals(tree) -> list[str]:
    """Every string a module actually evaluates, f-string pieces included.

    Docstrings are excluded: a docstring is prose about the code, and quoting an
    old wording there in order to explain it is not printing it.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def _attribute_chains(tree) -> set[str]:
    """Dotted names the module actually reads, e.g. `ctypes.windll.kernel32`."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts, cur = [], node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            out.add(".".join(reversed(parts)))
    return out


def _prints_without_stderr(tree) -> list[str]:
    """`print(...)` calls that do NOT route to stderr, with their first literal."""
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        file_kw = next((k for k in node.keywords if k.arg == "file"), None)
        routed = (file_kw is not None
                  and "sys.stderr" in _attribute_chains(file_kw.value))
        if routed:
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append(first.value)
        elif isinstance(first, ast.JoinedStr):
            out.append("".join(v.value for v in first.values
                               if isinstance(v, ast.Constant)
                               and isinstance(v.value, str)))
        else:
            out.append("<non-literal>")
    return out


class _Pipe(io.StringIO):
    """stdin that is not a terminal: cron, a daemon, `< /dev/null`."""

    def isatty(self) -> bool:
        return False


# ============================================================
# Zero: the three helpers above must not be vacuous
# ============================================================
#
# Every claim they support is of the form "assert not offenders". A helper that
# silently returns nothing makes all of them pass over any file at all, which is
# the empty-corpus shape that turns a guard into decoration. Each is therefore
# shown finding the thing it looks for, and NOT finding a near miss.

def test_the_ast_helpers_find_what_they_look_for(tmp_path):
    src = (
        '"""A docstring saying print("boom") and ctypes.windll, as prose."""\n'
        "import sys\n"
        "def f():\n"
        "    print('on stdout')\n"
        "    print('on stderr', file=sys.stderr)\n"
        "    print(f'formatted {x} stdout')\n"
        "    # a comment naming print('commented') and ctypes.windll\n"
        "    return ctypes.WinDLL('kernel32', use_last_error=True)\n"
    )
    tree = ast.parse(src)

    stdout = _prints_without_stderr(tree)
    assert "on stdout" in stdout
    assert "formatted  stdout" in stdout
    assert "on stderr" not in stdout, "the stderr route was not recognised"

    literals = _string_literals(tree)
    assert "on stdout" in literals
    assert not any("as prose" in s for s in literals), (
        "a docstring was read as an evaluated string")
    assert not any("commented" in s for s in literals), (
        "a comment was read as code")

    chains = _attribute_chains(tree)
    assert "ctypes.WinDLL" in chains
    assert "ctypes.windll" not in chains, (
        "the helper is case-insensitive, so it cannot tell the two apart")


# ============================================================
# One: an empty question is still a paid request
# ============================================================

@pytest.fixture()
def perplexity(monkeypatch):
    """The module with its transport replaced, so no request is ever purchased.

    Reproducing this defect for real would have BOUGHT an empty query. The stub
    records what would have been sent, which is the whole claim.
    """
    mod = _load("perplexity_under_test", "scripts/perplexity-research.py")
    sent: list = []
    monkeypatch.setattr(mod, "query_perplexity",
                        lambda q, **kw: sent.append(q))
    return mod, sent


def test_an_empty_pipe_is_refused_before_anything_is_billed(
        perplexity, monkeypatch):
    """`question` came back "" from stdin and nothing looked again.

    The tty branch refuses correctly; the branch that READS rather than asks had
    no matching refusal, so an empty pipe reached the billed endpoint.
    """
    mod, sent = perplexity
    monkeypatch.setattr(sys, "stdin", _Pipe(""))
    monkeypatch.setattr(sys, "argv", ["perplexity-research.py"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code != 0
    assert sent == [], f"a request was still sent: {sent!r}"


@pytest.mark.parametrize("payload", ["", "   ", "\n\n", "\t \n"])
def test_whitespace_only_stdin_is_empty_too(perplexity, monkeypatch, payload):
    """`.strip()` turns each of these into "", which is the same defect."""
    mod, sent = perplexity
    monkeypatch.setattr(sys, "stdin", _Pipe(payload))
    monkeypatch.setattr(sys, "argv", ["perplexity-research.py"])

    with pytest.raises(SystemExit):
        mod.main()
    assert sent == []


def test_a_real_piped_question_still_goes_through(perplexity, monkeypatch):
    """The guard must not have closed the stdin path it was protecting."""
    mod, sent = perplexity
    monkeypatch.setattr(sys, "stdin", _Pipe("  what is DPI?  \n"))
    monkeypatch.setattr(sys, "argv", ["perplexity-research.py"])

    mod.main()
    assert sent == ["what is DPI?"]


def test_the_refusal_says_it_would_have_been_billed(perplexity, monkeypatch,
                                                    capsys):
    """The operator needs the reason, not just a usage line.

    An empty stdin in a cron job is silent by nature; a message that names the
    cost is what makes the next reader fix the caller.
    """
    mod, _ = perplexity
    monkeypatch.setattr(sys, "stdin", _Pipe(""))
    monkeypatch.setattr(sys, "argv", ["perplexity-research.py"])
    with pytest.raises(SystemExit):
        mod.main()
    assert "billed" in capsys.readouterr().err


# ============================================================
# Two: another user's process is not a dead process
# ============================================================

class _Kernel32:
    """The three kernel32 calls the Windows branch makes, and nothing else."""

    def __init__(self, handle: int, exit_code: int = 259):
        self.handle = handle
        self.exit_code = exit_code
        self.closed: list[int] = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - Win32 name
        return self.handle

    def GetExitCodeProcess(self, handle, ref):  # noqa: N802 - Win32 name
        ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):  # noqa: N802 - Win32 name
        self.closed.append(handle)
        return 1


def test_access_denied_means_the_process_exists():
    """The defect, forty lines under its own post-mortem.

    `OpenProcess` returns NULL with ERROR_ACCESS_DENIED when the PID belongs to
    another user - the case the POSIX branch handles through `PermissionError`,
    and which this module's docstring says two earlier copies got wrong. A
    daemon under a service account read as dead, `stop` no-opped, and the pulse
    script started a second one beside it.
    """
    from scripts.utils import pid_liveness

    assert pid_liveness._windows_pid_is_running(
        4321, _Kernel32(handle=0), lambda: 5, ctypes.c_ulong, ctypes.byref) is True


@pytest.mark.parametrize("err,label", [
    (87, "ERROR_INVALID_PARAMETER: genuinely no such PID"),
    (0, "no error recorded: unknown failure"),
    (6, "ERROR_INVALID_HANDLE: unknown failure"),
])
def test_any_other_open_failure_is_still_dead(err, label):
    """Only access-denied flips. Everything else stays False, because only that
    one is evidence the process EXISTS."""
    from scripts.utils import pid_liveness

    assert pid_liveness._windows_pid_is_running(
        4321, _Kernel32(handle=0), lambda: err,
        ctypes.c_ulong, ctypes.byref) is False, label


def test_a_live_handle_reads_the_exit_code_and_closes():
    from scripts.utils import pid_liveness

    k = _Kernel32(handle=99, exit_code=259)   # STILL_ACTIVE
    assert pid_liveness._windows_pid_is_running(
        4321, k, lambda: 0, ctypes.c_ulong, ctypes.byref) is True
    assert k.closed == [99], "the handle was leaked"


def test_an_exited_process_reads_as_dead_and_the_handle_is_closed():
    from scripts.utils import pid_liveness

    k = _Kernel32(handle=99, exit_code=0)
    assert pid_liveness._windows_pid_is_running(
        4321, k, lambda: 0, ctypes.c_ulong, ctypes.byref) is False
    assert k.closed == [99]


def test_the_windows_branch_asks_for_the_real_last_error():
    """`ctypes.windll` does NOT populate the ctypes-private error slot.

    So `get_last_error()` on it returns a stale zero and access-denied would
    read as "unknown failure" - the branch silently reverting to the defect. The
    handle must come from a `WinDLL(..., use_last_error=True)`.
    """
    tree = _tree("scripts/utils/pid_liveness.py")
    assert "ctypes.windll" not in _attribute_chains(tree), (
        "ctypes.windll does not carry the last error into ctypes.get_last_error")
    win_dll = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call)
               and "ctypes.WinDLL" in _attribute_chains(n.func)]
    assert win_dll, "the Windows branch no longer builds its own kernel32 handle"
    for call in win_dll:
        kw = next((k for k in call.keywords if k.arg == "use_last_error"), None)
        assert kw is not None and getattr(kw.value, "value", None) is True, (
            "WinDLL without use_last_error=True: get_last_error() would return "
            "a stale zero and access-denied would read as unknown failure")


def test_posix_still_treats_permission_error_as_alive(monkeypatch):
    """The original fix, still pinned: the Windows work must not have moved it."""
    from scripts.utils import pid_liveness

    def boom(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(pid_liveness.os, "name", "posix")
    monkeypatch.setattr(pid_liveness.os, "kill", boom)
    assert pid_liveness.pid_is_running(4321) is True


# ============================================================
# Three: raised is not delivered
# ============================================================

@pytest.mark.parametrize("result,delivered,why", [
    ({"telegram": False, "card": False, "log": True}, False, "log only"),
    ({"telegram": True, "card": False, "log": True}, True, "telegram took it"),
    ({"telegram": False, "card": True, "log": True}, True, "the queue took it"),
    ({"log": True}, False, "no channel key at all"),
    (None, True, "an injected alert_fn that returns nothing"),
    ("sent", True, "an injected alert_fn returning something else"),
])
def test_delivery_is_read_from_what_alert_returned(result, delivered, why):
    """`alert()` has always returned which channels fired; both call sites in
    the watchdog threw it away.

    An unrecognised return counts as DELIVERED on purpose: a test double or a
    third-party callable must not be able to inflate the undelivered count into
    a false alarm about the alerting path itself.
    """
    from scripts.watchdog_core import _delivered

    assert _delivered(result) is delivered, why


def _silent_cadence(tmp_path, monkeypatch):
    """A workspace whose one daemon has never written a heartbeat."""
    from scripts import watchdog_core

    monkeypatch.setattr(watchdog_core, "load_cadence",
                        lambda root: {"demo-daemon": (60, 30)})
    return watchdog_core


def test_an_alert_that_reached_no_channel_is_counted_separately(
        tmp_path, monkeypatch):
    """The number that decides whether a human learned about a dead daemon.

    "3 alert(s) fired" could mean three that reached nothing but a log file, and
    the grid printed the word "fired" over it.
    """
    watchdog_core = _silent_cadence(tmp_path, monkeypatch)
    calls: list = []

    def log_only(sev, summary, detail, source=""):
        calls.append(sev)
        return {"telegram": False, "card": False, "log": True}

    report = watchdog_core.check_once(
        tmp_path, alert_fn=log_only, state_path=tmp_path / "state.json")
    assert calls, "the daemon was never alerted about"
    assert report["alerts_fired"] == 1
    assert report["alerts_undelivered"] == 1


def test_a_delivered_alert_is_not_counted_as_undelivered(tmp_path, monkeypatch):
    watchdog_core = _silent_cadence(tmp_path, monkeypatch)

    def telegram_ok(sev, summary, detail, source=""):
        return {"telegram": True, "card": False, "log": True}

    report = watchdog_core.check_once(
        tmp_path, alert_fn=telegram_ok, state_path=tmp_path / "state.json")
    assert report["alerts_fired"] == 1
    assert report["alerts_undelivered"] == 0


def _grid(report: dict, capsys) -> str:
    """Render the CLI grid and return what it printed, colour codes stripped."""
    import re as _re

    mod = _load("daemon_watchdog_under_test", "scripts/daemon-watchdog.py")
    mod._print_grid(report)
    return _re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)


def test_the_grid_prints_the_undelivered_count_when_there_is_one(capsys):
    """Driven, not inspected.

    The source check below passes on a version that reads `alerts_undelivered`
    and then never prints it, which is exactly what a mutation showed. What the
    operator sees is the deliverable, so the output is what gets asserted.
    """
    out = _grid({"verdict": "down", "alerts_fired": 3,
                 "alerts_undelivered": 3, "daemons": []}, capsys)
    assert "3 alert(s) raised" in out
    assert "reached no channel but the log" in out


def test_the_grid_stays_quiet_when_every_alert_was_delivered(capsys):
    """A line that always appears is a line nobody reads."""
    out = _grid({"verdict": "down", "alerts_fired": 2,
                 "alerts_undelivered": 0, "daemons": []}, capsys)
    assert "2 alert(s) raised" in out
    assert "reached no channel" not in out


def test_the_grid_survives_a_report_from_an_older_watchdog(capsys):
    """`alerts_undelivered` absent must not crash the CLI: a state file or a
    cached report written before this change carries no such key."""
    out = _grid({"verdict": "ok", "alerts_fired": 0, "daemons": []}, capsys)
    assert "0 alert(s) raised" in out
    assert "reached no channel" not in out


def test_the_grid_no_longer_calls_an_attempt_a_delivery():
    """The word had to change with the number; "fired" was the claim."""
    tree = _tree("scripts/daemon-watchdog.py")
    literals = _string_literals(tree)
    assert any("alert(s) raised" in s for s in literals)
    offenders = [s for s in literals if "alert(s) fired" in s]
    assert not offenders, (
        f"the grid still prints attempts under the word delivered: {offenders}")
    assert "alerts_undelivered" in literals, (
        "the grid never reads the count that says nobody was told")


# ============================================================
# Four: an error is not a result
# ============================================================

@pytest.mark.parametrize("argv,expect", [
    ([], "Usage:"),
    (["/nonexistent/definitely-absent.html"], "Input file not found"),
])
def test_html_to_pdf_puts_failures_on_stderr(tmp_path, argv, expect):
    """`render-doctype.py` runs this and reads STDOUT for the generated path.

    An error on that same stream is indistinguishable from a result by channel,
    which is the entire reason two channels exist.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "html-to-pdf.py"), *argv],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert expect in result.stderr
    assert result.stdout.strip() == "", (
        f"stdout carried a failure message: {result.stdout!r}")


def test_no_error_path_in_html_to_pdf_writes_to_stdout():
    """All four exit-1 paths, not just the one a test happens to reach.

    Two of them need Playwright and a broken render to trigger, which no unit
    test can arrange cheaply; the source is what establishes the claim for
    those, so it is what gets asserted.
    """
    tree = _tree("scripts/html-to-pdf.py")
    offenders = [s for s in _prints_without_stderr(tree)
                 if "[ERROR]" in s or s.startswith("Usage:")]
    assert not offenders, f"these still print a failure to stdout: {offenders}"


def test_the_success_lines_still_go_to_stdout():
    """The fix must not have moved the RESULT to stderr as well.

    A caller reads the generated path from stdout, so an over-eager sweep would
    have traded one broken contract for another.
    """
    tree = _tree("scripts/html-to-pdf.py")
    on_stdout = _prints_without_stderr(tree)
    assert any(s.startswith("PDF generated:") for s in on_stdout), on_stdout
    assert any(s.startswith("Size:") for s in on_stdout), on_stdout


# ============================================================
# Five: a dry run that asked the migration nothing
# ============================================================

def _runner_with_one_pending(monkeypatch, up):
    """The migration runner, with a single fake pending migration."""
    mod = _load("migrate_data_under_test", "scripts/migrate-data.py")
    fake = types.ModuleType("scripts.migrations.0002_fake")
    fake.VERSION = 2
    fake.up = up
    monkeypatch.setattr(mod, "registered_migrations", lambda: [(2, fake)])
    monkeypatch.setattr(mod, "read_data_schema_version", lambda: 1)
    monkeypatch.setattr(mod, "max_version", lambda: 2)
    monkeypatch.setattr(mod, "data_root_is_demo", lambda: False)
    return mod


def test_a_dry_run_asks_the_migration_what_it_would_do(monkeypatch, tmp_path):
    """The contract `0001_baseline.py` states, which nothing reached.

    Every migration MUST "honor dry_run (describe, change nothing)", and the
    only call site passed `dry_run=False` unconditionally while the dry-run
    branch `continue`d above it. So the first real migration would ship a
    dry-run branch no code path executes, and `--dry-run` would print the
    runner's one-line guess instead of the migration's own account.
    """
    seen: list = []
    mod = _runner_with_one_pending(
        monkeypatch, lambda data_root, dry_run=False: seen.append(dry_run))
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path)
    wrote: list = []
    monkeypatch.setattr(mod, "_write_version", lambda *a: wrote.append(a))

    assert mod.cmd_apply(dry_run=True) == 0
    assert seen == [True], f"up() was called with {seen}, not dry_run=True"
    assert wrote == [], "a dry run stamped the version"


def test_a_real_apply_still_passes_dry_run_false_and_stamps(monkeypatch,
                                                            tmp_path):
    seen: list = []
    mod = _runner_with_one_pending(
        monkeypatch, lambda data_root, dry_run=False: seen.append(dry_run))
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path)
    wrote: list = []
    monkeypatch.setattr(mod, "_write_version", lambda *a: wrote.append(a))

    assert mod.cmd_apply(dry_run=False) == 0
    assert seen == [False]
    assert len(wrote) == 1


def test_a_dry_run_that_cannot_describe_itself_fails_the_run(monkeypatch,
                                                             tmp_path, capsys):
    """A dry run that raises is not a dry run that passed.

    Swallowing it would restore the old silence in a new place: the operator
    would read exit 0 as "the plan is sound".
    """
    def boom(data_root, dry_run=False):
        raise RuntimeError("cannot read the overlay")

    mod = _runner_with_one_pending(monkeypatch, boom)
    monkeypatch.setattr(mod, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "_write_version", lambda *a: None)

    assert mod.cmd_apply(dry_run=True) == 1
    assert "cannot read the overlay" in capsys.readouterr().err


def test_the_baseline_migration_still_honors_the_contract_it_states():
    """`0001_baseline.py` is a no-op, but it must accept the argument.

    A migration whose `up` has no `dry_run` parameter would now raise a
    TypeError on every dry run rather than being quietly skipped.
    """
    import inspect

    from scripts.migrations import registered_migrations

    for version, mod in registered_migrations():
        params = inspect.signature(mod.up).parameters
        assert "dry_run" in params, (
            f"migration v{version} has no dry_run parameter; the runner now "
            f"calls it on every --dry-run")
