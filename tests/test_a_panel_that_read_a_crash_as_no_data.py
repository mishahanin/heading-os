"""A dashboard panel that read a crashed child as "nothing to report".

`scripts/generate-dashboard.py:collect_capture_payoff` shells out to
`scripts/odin-cadence.py --json` for the two cadence figures on the Capture
Payoff panel: clusters ripe to promote, and days since the last harvest. It ran
the child with `capture_output=True` and then went straight to
`json.loads(out.stdout) if out.stdout.strip() else {}`. `returncode` was never
read.

`odin-cadence.py` only ever `return 0`s, so a non-zero exit is an uncaught
exception: the traceback goes to stderr and stdout is left EMPTY. Empty stdout
took the `else {}` branch, `{}.get(...)` returned None three times, and None is
already the panel's "this workspace has no cadence helper" state. So a dead
child and an exec workspace produced the same page.

MEASURED 2026-08-29, one recent knowledge note, child forced to `sys.exit(1)`
after writing "ValueError: cadence store unreadable" to stderr:

    state            Clusters to Promote   Since Last Harvest   stderr bytes
    crashed, before  "-"  (no class)       "-"  (no class)      0
    crashed, after   "?"  (danger)         "?"  (danger)        113
    absent helper    "-"  (no class)       "-"  (no class)      0
    healthy child    "3"  (accent)         "9d" (danger)        0

Zero bytes on stderr is the whole finding: nothing was raised, so the existing
`except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError)`
handler and its `odin cadence collect failed` line were never reached. The run
exited 0 and printed a complete dashboard.

The cost is the nudge that did not fire. `days_since >= 7` turns the "Since Last
Harvest" box red, and the healthy child above returned 9. With the child dead
that box read "-" in the default colour, so the one panel whose job is to say
the harvest is overdue said nothing, on exactly the days when something was
wrong enough to kill the helper.

The asymmetry is the evidence. The same child has two other callers and both
inspect `returncode`:

  - `scripts/prime-health-parallel.py:run_odin_cadence` (`--quiet`) returns
    `status: error` naming the exit code, pinned by
    `tests/test_a_crashed_check_that_rendered_a_clean_brief.py`.
  - `scripts/utils/ops_signals.py:odin_cadence_state` (`--json`) gates the
    parse on `returncode == 0 and stdout.strip()`, and additionally refuses a
    non-object payload, pinned by
    `tests/test_a_radar_that_watched_three_of_fourteen_layers.py`.

The dashboard had neither guard. The missing shape guard was live too: with exit
0 and a payload of `[1, 2]`, `data.get` raised AttributeError, which is not in
the handler's tuple, so it killed the whole dashboard run.

Fixed 2026-08-29 by moving the run, the `returncode` check and the object-shape
guard into `scripts/utils/odin_cadence.read_cadence_json`, shared by the two
`--json` call sites. `prime-health-parallel` keeps its own: it runs `--quiet`
for a human one-liner, a different contract. The collector now returns a
`cadence_error` reason, None when the helper is merely absent, and the panel
draws "?" plus a named failure rather than borrowing the look of a blank.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import odin_cadence as oc  # noqa: E402
from scripts.utils import ops_signals as ops  # noqa: E402

DASHBOARD_SRC = ROOT / "scripts" / "generate-dashboard.py"

_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "odin-cadence.py", line 1, in <module>\n'
    "ValueError: cadence store unreadable"
)


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def dash():
    return _load("dashboard_crash_panel", "scripts/generate-dashboard.py")


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    """A `subprocess` shim returning one canned CompletedProcess."""
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["stub"], returncode=returncode, stdout=stdout, stderr=stderr)
    return types.SimpleNamespace(
        run=run,
        TimeoutExpired=subprocess.TimeoutExpired,
        SubprocessError=subprocess.SubprocessError,
    )


def _script(tmp_path: Path, body: str = "") -> Path:
    """A real on-disk cadence script, so `script.exists()` is satisfied."""
    p = tmp_path / "odin-cadence.py"
    p.write_text(body, encoding="utf-8")
    return p


def _brain(tmp_path: Path, day: str):
    """A knowledge tree with one Odin episode captured on `day`."""
    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    (brain / "episodes").mkdir(parents=True, exist_ok=True)
    (brain / "episodes" / "a-briefing-for-james-bond.md").write_text(
        f'---\nid: "1"\ntitle: "a briefing"\ntype: episode\nupdated: {day}\n---\n\nbody\n',
        encoding="utf-8",
    )
    return knowledge, brain


def _boxes(html: str) -> list[tuple[str, str]]:
    """(css class, rendered value) for every metric box, in page order."""
    return re.findall(r'<div class="metric-val ?([^"]*)">([^<]*)</div>', html)


# ===========================================================================
# The shared reader: a crashed child is a reported failure, not an empty dict
# ===========================================================================

def test_a_crashed_cadence_child_is_reported_not_read_as_empty(tmp_path, monkeypatch):
    """The headline finding: exit 1, empty stdout, and nothing said."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(1, "", _TRACEBACK))
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert cadence == {}
    assert error, "a crashed cadence child reported no failure at all"


def test_the_cadence_failure_names_the_exit_code(tmp_path, monkeypatch):
    """"Something failed" is not actionable; the exit code is."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(3, "", _TRACEBACK))
    _cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert "3" in error
    assert "odin-cadence" in error


def test_the_cadence_failure_carries_the_childs_own_words(tmp_path, monkeypatch):
    """The last stderr line is the diagnosis; dropping it costs the run."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(1, "", _TRACEBACK))
    _cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert "cadence store unreadable" in error


def test_a_child_that_exits_zero_and_prints_nothing_is_reported(tmp_path, monkeypatch):
    """Empty stdout is the same blank; a clean exit does not make it data."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(0, "   \n", ""))
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert cadence == {}
    assert error and "printed nothing" in error


@pytest.mark.parametrize("stdout", ["null", "[1, 2]", "5", '"text"'])
def test_a_non_object_payload_is_reported_and_never_raises(tmp_path, monkeypatch, stdout):
    """`.get` on a list raises AttributeError, which no caller here catches."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(0, stdout, ""))
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert cadence == {}
    assert error and "not a cadence object" in error


def test_unparseable_json_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "subprocess", _fake_run(0, "{oops", ""))
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert cadence == {}
    assert error and "unparseable" in error


def test_a_child_that_times_out_is_reported(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="odin-cadence.py", timeout=30)
    shim = _fake_run(0)
    shim.run = boom
    monkeypatch.setattr(oc, "subprocess", shim)
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert cadence == {}
    assert error and "TimeoutExpired" in error


def test_a_healthy_child_still_returns_its_report(tmp_path, monkeypatch):
    """The other direction: nothing about the guards blocks a good run."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(
        0, '{"reflect_clusters": 3, "last_collect": "2026-08-20", "days_since": 9}'))
    cadence, error = oc.read_cadence_json(tmp_path, script=_script(tmp_path))
    assert error is None
    assert cadence["reflect_clusters"] == 3
    assert cadence["days_since"] == 9


def test_the_reader_asks_the_child_for_json_from_the_engine_root(tmp_path, monkeypatch):
    """Recorded, not inferred. Every other shim here discards its argv, so a
    reader that dropped `--json` passed all of them: the child would print its
    one-line human nudge instead of a report, and that is not machine-readable.
    `cwd` matters too, because `odin-cadence.py` resolves the brain from it.
    """
    seen = []

    def run(argv, **kwargs):
        seen.append((list(argv), kwargs.get("cwd")))
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    shim = _fake_run(0, "{}")
    shim.run = run
    monkeypatch.setattr(oc, "subprocess", shim)
    script = _script(tmp_path)

    oc.read_cadence_json(tmp_path, script=script)

    assert seen, "the reader never spawned a child"
    argv, cwd = seen[0]
    assert argv == [sys.executable, str(script), "--json"]
    assert cwd == str(tmp_path)


def test_the_reader_derives_the_child_path_when_none_is_given(tmp_path, monkeypatch):
    """`ops_signals` passes only the engine root; the reader owns the path."""
    seen = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    shim = _fake_run(0, "{}")
    shim.run = run
    monkeypatch.setattr(oc, "subprocess", shim)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "odin-cadence.py").write_text("", encoding="utf-8")

    oc.read_cadence_json(tmp_path)

    assert seen[0][1] == str(tmp_path / "scripts" / "odin-cadence.py")


def test_an_absent_helper_is_a_blank_and_not_a_failure(tmp_path, monkeypatch):
    """Exec workspaces have no ceo-only cadence script. That is not an error."""
    monkeypatch.setattr(oc, "subprocess", _fake_run(1, "", "should never run"))
    cadence, error = oc.read_cadence_json(tmp_path, script=tmp_path / "not-here.py")
    assert cadence == {}
    assert error is None


# ===========================================================================
# The collector: a real crashed child, end to end
# ===========================================================================

def test_a_real_crashed_child_reaches_the_collector_as_an_error(dash, tmp_path, capsys):
    """No shim anywhere: a genuine non-zero exit with a genuine empty stdout."""
    knowledge, brain = _brain(tmp_path, "2026-08-27")
    crasher = _script(tmp_path, (
        "import sys\n"
        "print('ValueError: cadence store unreadable', file=sys.stderr)\n"
        "sys.exit(1)\n"
    ))
    dash.odin_brain_dir = lambda p=brain: p
    dash.knowledge_dir = lambda p=knowledge: p
    dash.ODIN_CADENCE_SCRIPT = crasher
    dash.TODAY = __import__("datetime").date(2026, 8, 29)

    payoff = dash.collect_capture_payoff()
    assert payoff["available"] is True
    assert payoff["signals_week"] == 1, "the local capture count is still real data"
    assert payoff["cadence_error"], "the crash reached the panel as a silent blank"
    assert "exited 1" in payoff["cadence_error"]

    err = capsys.readouterr().err
    assert "odin cadence collect failed" in err, "the crash said nothing on stderr"


def test_a_real_healthy_child_still_fills_the_collector(dash, tmp_path):
    """The other direction, also with no shim."""
    knowledge, brain = _brain(tmp_path, "2026-08-27")
    healthy = _script(tmp_path, (
        "import json\n"
        "print(json.dumps({'reflect_clusters': 3, 'last_collect': '2026-08-20',\n"
        "                  'days_since': 9}))\n"
    ))
    dash.odin_brain_dir = lambda p=brain: p
    dash.knowledge_dir = lambda p=knowledge: p
    dash.ODIN_CADENCE_SCRIPT = healthy
    dash.TODAY = __import__("datetime").date(2026, 8, 29)

    payoff = dash.collect_capture_payoff()
    assert payoff["cadence_error"] is None
    assert payoff["promote_ready"] == 3
    assert payoff["days_since"] == 9


# ===========================================================================
# The panel: a crash must not borrow the look of a legitimate blank
# ===========================================================================

_CRASHED = {
    "available": True, "signals_week": 1, "recent_titles": ["a briefing"],
    "promote_ready": None, "last_collect": None, "days_since": None,
    "cadence_error": "odin-cadence.py exited 1: ValueError: cadence store unreadable",
}
_ABSENT = dict(_CRASHED, cadence_error=None)
_HEALTHY = {
    "available": True, "signals_week": 1, "recent_titles": ["a briefing"],
    "promote_ready": 3, "last_collect": "2026-08-20", "days_since": 9,
    "cadence_error": None,
}


def test_the_panel_names_the_cadence_failure_on_the_page(dash):
    """stderr scrolls past; the CEO reads the rendered file hours later."""
    html = dash.build_capture_payoff(_CRASHED)
    assert "Odin cadence unread" in html
    assert "exited 1" in html


def test_a_crashed_panel_does_not_render_the_absent_helper_dash(dash):
    """The exact confusion measured: both states drew "-" in the same colour."""
    crashed = _boxes(dash.build_capture_payoff(_CRASHED))
    absent = _boxes(dash.build_capture_payoff(_ABSENT))
    assert crashed != absent, "a dead helper still renders as an empty workspace"
    assert crashed[1:] == [("danger", "?"), ("danger", "?")]
    assert absent[1:] == [("", "-"), ("", "-")]


def test_an_absent_helper_still_renders_the_quiet_panel(dash):
    """The exec-workspace path must not grow a failure it does not have."""
    html = dash.build_capture_payoff(_ABSENT)
    assert "Odin cadence unread" not in html
    assert "Capture Payoff" in html


def test_a_healthy_panel_still_renders_its_real_numbers(dash):
    """The other direction: the guards cost a good run nothing."""
    html = dash.build_capture_payoff(_HEALTHY)
    assert "Odin cadence unread" not in html
    assert _boxes(html) == [("up", "1"), ("accent", "3"), ("danger", "9d")]
    assert "ripe to promote" in html


def test_an_overdue_harvest_is_never_hidden_behind_a_crash(dash):
    """`days_since >= 7` is the nudge this panel exists for. A crash used to
    silence it with an uncoloured dash; it must now shout, not whisper."""
    crashed = dash.build_capture_payoff(_CRASHED)
    healthy = dash.build_capture_payoff(_HEALTHY)
    assert "danger" in crashed, "a crash rendered no warning of any kind"
    assert "danger" in healthy


def test_the_panel_escapes_the_failure_reason(dash):
    """The reason carries a child's stderr, which is untrusted text."""
    payload = dict(_CRASHED, cadence_error='exited 1: <script>alert("x")</script>')
    html = dash.build_capture_payoff(payload)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_hidden_panel_is_still_hidden(dash):
    """No Odin brain at all keeps rendering nothing, error field or not."""
    assert dash.build_capture_payoff({"available": False}) == ""


# ===========================================================================
# The other two call sites keep their own guards
# ===========================================================================

def test_the_radar_still_refuses_a_crashed_cadence_child(tmp_path, monkeypatch):
    """`ops_signals` moved onto the shared reader; its behaviour did not move."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "odin-cadence.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(oc, "subprocess", _fake_run(1, "", _TRACEBACK))
    signal = ops.odin_cadence_state(tmp_path)
    assert signal["key"] == "odin_cadence"
    assert signal["due"] is False


def test_the_radar_still_reads_a_healthy_cadence_child(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "odin-cadence.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(oc, "subprocess", _fake_run(
        0, '{"nudge": true, "unharvested_total": 12, "reflect_clusters": 2, '
           '"stale_clusters": 1, "min_entries": 5}'))
    signal = ops.odin_cadence_state(tmp_path)
    assert signal["due"] is True
    assert signal["severity"] == "high"
    assert signal["value"] == {"unharvested": 12, "clusters": 2, "stale": 1}


def test_the_dashboard_no_longer_spawns_the_cadence_child_itself(dash):
    """Structural, so the next edit cannot quietly reintroduce a third
    convention: no `subprocess.run` in the dashboard may name the cadence
    script. Asked of the AST, because a comment or an f-string mentioning
    `ODIN_CADENCE_SCRIPT` must not satisfy it."""
    tree = ast.parse(DASHBOARD_SRC.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "run"
                and isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if "ODIN_CADENCE_SCRIPT" in names:
            offenders.append(node.lineno)
    assert offenders == [], (
        f"generate-dashboard.py runs the cadence child directly at lines "
        f"{offenders}; the guarded reader is scripts/utils/odin_cadence.py")


def test_prime_health_keeps_its_own_returncode_check(tmp_path, monkeypatch):
    """The third call site runs `--quiet`, not `--json`, and is untouched."""
    ph = _load("ph_crash_panel", "scripts/prime-health-parallel.py")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "odin-cadence.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", _TRACEBACK))
    res = ph.run_odin_cadence(tmp_path)
    assert res["status"] not in ph.NON_FAILURE_STATUSES
    assert res["omit_if_empty"] is False
