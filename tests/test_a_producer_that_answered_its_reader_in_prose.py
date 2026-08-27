"""A producer that broke its own JSON contract, and two readers that trusted it.

Shard `scripts-03-p3` of the 2026-08 engine audit. The through-line is a
contract stated in a docstring and not kept by the code under it.

  - `crm-health.py --json` promised "output as JSON (for programmatic use)" and,
    on a workspace with no contact files, printed two coloured English lines on
    STDOUT and exited 0. Two comments inside that same function already said
    stdout must stay clean for JSON consumers.
  - `cold_sweep_core._fetch_rows` called `json.loads` on that stdout with no
    handler, so a manual cold sweep on a fresh workspace ended in a raw
    JSONDecodeError traceback naming neither script.
  - `cold-sweep.py` documented "Exit codes: 0 ok, 1 error" and had no path that
    returned 1.
  - `herdr_agent._run` was annotated `-> dict` and returned whatever
    `json.loads` gave it. `agents()` guarded the `agent list` call for that
    reason in August; `submit_compact`, `set_label` and `clear_label` were left
    handing a raw payload to `payload.get(...)` in `compact-now.py`.

Two of the three `crm-health --json` consumers had already grown a private
handler for the producer's prose (`crm_next.py`, `utils/ops_signals.py`). Nobody
had fixed the producer, which is why the third one still died.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import herdr_agent as HA  # noqa: E402


# ============================================================
# The producer: crm-health.py --json on an empty CRM
# ============================================================

def _empty_crm(tmp_path: Path) -> Path:
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    return tmp_path


def _run_health(data_root: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "crm-health.py"), *flags],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={**_env(), "HEADING_OS_DATA": str(data_root)},
    )


def _env() -> dict:
    import os
    return dict(os.environ)


def test_an_empty_crm_answers_json_with_an_empty_list(tmp_path):
    """The regression itself: prose on stdout is what broke every reader."""
    proc = _run_health(_empty_crm(tmp_path), "--json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == []


def test_the_advice_line_does_not_pollute_the_json_stream(tmp_path):
    """`/crm add ...` is help for a human, so it belongs on stderr."""
    proc = _run_health(_empty_crm(tmp_path), "--json")
    assert "crm add" not in proc.stdout
    assert "crm add" in proc.stderr


def test_a_human_run_still_says_the_crm_is_empty(tmp_path):
    """Fixing the JSON stream must not take the terminal message away."""
    proc = _run_health(_empty_crm(tmp_path))
    assert "No contact files found" in proc.stdout


# ============================================================
# The reader: cold_sweep_core._fetch_rows
# ============================================================

@pytest.fixture()
def core():
    from scripts import cold_sweep_core
    return cold_sweep_core


class _Proc:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def _health_says(monkeypatch, core, stdout: str) -> None:
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: _Proc(stdout))


def test_a_health_scorer_that_answers_in_prose_is_named(monkeypatch, core):
    _health_says(monkeypatch, core, "No contact files found in /somewhere\n")
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    msg = str(exc.value)
    assert "crm-health.py" in msg, "the message must name which script misbehaved"
    assert "not JSON" in msg
    assert "No contact files found" in msg, "quote what it actually said"


def test_an_empty_stdout_says_so_rather_than_trailing_off(monkeypatch, core):
    """"...is not JSON: " with nothing after it tells the operator nothing."""
    _health_says(monkeypatch, core, "")
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    assert "(empty)" in str(exc.value)


def test_only_the_first_line_of_a_wall_of_prose_is_quoted(monkeypatch, core):
    """A refusal is one line. A scorer that printed a page must not paste it."""
    _health_says(monkeypatch, core, "first line\nsecond line\nthird line\n")
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    msg = str(exc.value)
    assert "first line" in msg
    assert "second line" not in msg


def test_a_very_long_first_line_is_truncated(monkeypatch, core):
    _health_says(monkeypatch, core, "x" * 400)
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    assert "x" * 120 in str(exc.value)
    assert "x" * 200 not in str(exc.value)


@pytest.mark.parametrize("payload", ['{"health": "red"}', '"a string"', "42", "null"])
def test_valid_json_of_the_wrong_shape_is_refused(monkeypatch, core, payload):
    """The old code answered `[]` here, which reads as "nobody is overdue"."""
    _health_says(monkeypatch, core, payload)
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    assert "expected a list" in str(exc.value)


def test_a_good_list_still_comes_straight_back(monkeypatch, core):
    rows = [{"name": "A", "health": "red", "email": "a@example.com"}]
    _health_says(monkeypatch, core, json.dumps(rows))
    assert core._fetch_rows(ROOT) == rows


def test_a_scorer_that_cannot_be_run_names_itself(monkeypatch, core):
    def _boom(*a, **k):
        raise OSError("no such file")
    monkeypatch.setattr(core.subprocess, "run", _boom)
    with pytest.raises(RuntimeError) as exc:
        core._fetch_rows(ROOT)
    assert "could not be run" in str(exc.value)


def test_a_nonzero_exit_is_a_refusal_not_a_traceback(monkeypatch, core):
    """Let the CHILD exit non-zero; do not hand-raise the exception it causes.

    This test used to raise `CalledProcessError` from the stub itself, which
    tests nothing about `check=True` - the one line that turns a non-zero exit
    into that exception. Deleting `check=True` left the test green, while a real
    `crm-health.py` exit 2 with a diagnostic on stderr fell through to the JSON
    branch and refused with "crm-health.py --json exited 0 but its output is not
    JSON", a message asserting a false fact about the exit code
    (`.claude/rules/scope-claims.md`).

    The bare `pytest.raises(RuntimeError)` was the second half of the defect:
    `_fetch_rows` raises RuntimeError from three sites with three diagnoses, so
    with no `match=` any of them satisfied a test named for one.
    """
    real_run = subprocess.run
    seen = {}

    def _child(cmd, **kwargs):
        seen["check"] = kwargs.get("check")
        # A real child, and it really exits 2. `check=True` is what must turn
        # that into the refusal; nothing here raises on its own.
        return real_run(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('crm-health: bad config\\n'); sys.exit(2)"],
            **kwargs)

    monkeypatch.setattr(core.subprocess, "run", _child)
    with pytest.raises(RuntimeError, match="could not be run"):
        core._fetch_rows(ROOT)
    assert seen["check"] is True, (
        "the producer is run without check=True, so a non-zero exit reaches the "
        "JSON branch and is reported as 'exited 0'")


# ============================================================
# The CLI: cold-sweep.py honours its documented exit codes
# ============================================================

def _cold_sweep(data_root: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "cold-sweep.py"), *flags],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={**_env(), "HEADING_OS_DATA": str(data_root)},
    )


def test_an_empty_crm_is_a_quiet_success_not_a_traceback(tmp_path):
    """End to end over both fixes: this exact run used to raise."""
    proc = _cold_sweep(_empty_crm(tmp_path), "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    assert "no overdue contacts to route" in proc.stdout


def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cold_sweep_cli", ROOT / "scripts" / "cold-sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_cli_reports_a_broken_scorer_in_one_plain_line(capsys, monkeypatch):
    """console-first.md: degrade clearly, never with a stack trace.

    The docstring of `cold-sweep.py` has promised exit 1 on error since the file
    was written, and no path returned it.
    """
    mod = _load_cli()
    monkeypatch.setattr(mod.cold_sweep_core, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("crm-health.py --json is not JSON")))
    monkeypatch.setattr(sys, "argv", ["cold-sweep.py", "--dry-run"])
    assert mod.main() == 1
    err = capsys.readouterr().err
    assert "cold-sweep could not read CRM health" in err
    assert "crm-health.py" in err


# ============================================================
# herdr_agent._run keeps the promise its annotation makes
# ============================================================

@pytest.fixture()
def herdr_present(monkeypatch):
    monkeypatch.setattr(HA.shutil, "which", lambda _name: "/usr/bin/herdr")


def _herdr_says(monkeypatch, stdout: str) -> None:
    class _P:
        returncode = 0
        stderr = ""
    proc = _P()
    proc.stdout = stdout
    monkeypatch.setattr(HA.subprocess, "run", lambda *a, **k: proc)


@pytest.mark.parametrize("raw", ["[1, 2]", '"a string"', "42", "null", "true"])
@pytest.mark.parametrize("call", ["submit_compact", "set_label", "clear_label"])
def test_every_herdr_call_refuses_a_payload_that_is_not_an_object(
    herdr_present, monkeypatch, raw, call
):
    """These three were annotated `-> dict` and returned whatever came back.

    Only `agents()` was guarded. A HERDR release answering a prompt with a bare
    array would have reached `payload.get(...)` in `compact-now.py` and raised
    AttributeError past every HerdrUnavailable handler.
    """
    _herdr_says(monkeypatch, raw)
    fn = getattr(HA, call)
    args = ("w1:p1", "label") if call == "set_label" else ("w1:p1",)
    with pytest.raises(HA.HerdrUnavailable) as exc:
        fn(*args)
    assert "not an object" in str(exc.value)


def test_the_refusal_names_the_type_it_got(herdr_present, monkeypatch):
    _herdr_says(monkeypatch, "[1, 2]")
    with pytest.raises(HA.HerdrUnavailable) as exc:
        HA.submit_compact("w1:p1")
    assert "list" in str(exc.value)


def test_an_object_payload_still_passes_through(herdr_present, monkeypatch):
    _herdr_says(monkeypatch, '{"result": {"agent": {"agent_status": "idle"}}}')
    assert HA.submit_compact("w1:p1") == {
        "result": {"agent": {"agent_status": "idle"}}}


def test_unparseable_output_is_still_its_own_message(herdr_present, monkeypatch):
    """The wrong-shape guard must not swallow the wrong-syntax one."""
    _herdr_says(monkeypatch, "not json at all")
    with pytest.raises(HA.HerdrUnavailable) as exc:
        HA.submit_compact("w1:p1")
    assert "unparseable" in str(exc.value)


# ============================================================
# compact-now.py reads a nested status without trusting its shape
# ============================================================

def _compact_now():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "compact_now_cli", ROOT / "scripts" / "compact-now.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("payload,expected", [
    ({"result": {"agent": {"agent_status": "working"}}}, "working"),
    ({"result": {"agent": {"agent_status": "idle"}}}, "idle"),
    ({}, None),
    ({"result": None}, None),
    ({"result": {}}, None),
    ({"result": {"agent": {}}}, None),
])
def test_a_status_that_is_there_is_read_and_one_that_is_not_is_none(payload, expected):
    assert _compact_now()._agent_status(payload) == expected


@pytest.mark.parametrize("payload", [
    {"result": ["a", "list"]},          # `or {}` kept this and then called .get
    {"result": "a string"},
    {"result": 42},
    {"result": {"agent": ["a", "list"]}},
    {"result": {"agent": "a string"}},
    {"result": {"agent": {"agent_status": ["a", "list"]}}},
    {"result": {"agent": {"agent_status": 42}}},
])
def test_a_wrong_shaped_status_is_absent_not_an_exception(payload):
    """The compact was already submitted. A shape surprise costs one line."""
    assert _compact_now()._agent_status(payload) is None
