"""Shard scripts-utils-01-p4: the tool that measures the other tools, and four
readers that answered confidently about something they had not established.

* ``mutation_probe.run_mutations`` read its verdict off a non-zero exit code and
  never ran the command on the UNMUTATED tree. A suite that was ALREADY red
  therefore reported every mutation as ``killed`` with ``trustworthy`` True.
  Reproduced 2026-08-25 on a scratch tree whose assertion failed before any edit.
  The module opens with "refuse a verdict without a control", and its sibling
  ``mutation_harness.py`` has run that control all along.

* ``observability_safe._debug_trace_path`` put the FULL debug payload - raw
  args, kwargs and return values, so e-mail bodies and sender addresses - under
  the ENGINE workspace root. ``state/`` is not gitignored and routes ``engine``:
  the PUBLIC repository. The same defect was already found and fixed once in
  ``scripts/inbox_pulse/cost.py``.

* ``ollama_host.probe`` raised TypeError instead of answering False when an
  endpoint returned HTTP 200 with a JSON scalar, so neither resolver could
  degrade or name what it had tried.

* ``modem_drivers.E5800Driver._ubus`` swallowed a failed reply and returned
  ``{}``, so ``read_status()`` handed back a well-formed dict claiming the modem
  was read and the CLI printed nothing and exited 0.

* ``odin_principles.relevant_principles_for`` suppressed citations for internal
  contacts only as a side effect of an empty domain list, so any pipeline stage
  unioned deal-side domains back on.

Run: python3 -m pytest tests/test_a_probe_that_graded_a_suite_already_red.py
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import odin_principles as op  # noqa: E402
from scripts.utils.modem_drivers import E5800Driver, ModemReadError  # noqa: E402
from scripts.utils.mutation_probe import (  # noqa: E402
    BASELINE_RED, KILLED, SURVIVED, Mutation, render, run_mutations,
)
from scripts.utils.ollama_host import probe  # noqa: E402


# ============================================================
# The probe that graded a suite already red
# ============================================================

def _tree(tmp_path: Path, expected: int) -> Path:
    root = tmp_path / f"tree{expected}"
    root.mkdir()
    (root / "src.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (root / "check.py").write_text(
        f"import src\nassert src.answer() == {expected}, 'contract'\n", encoding="utf-8")
    return root


_BREAK = Mutation("break the answer", (("src.py", "return 42", "return 43"),),
                  lambda sources: None)


def test_a_red_baseline_refuses_every_verdict(tmp_path):
    """It reported `killed` and `trustworthy` True over a suite already failing."""
    results = run_mutations([_BREAK], [sys.executable, "check.py"], _tree(tmp_path, 99))

    assert [r.verdict for r in results] == [BASELINE_RED]
    assert all(not r.trustworthy for r in results)
    assert "does not pass on the unmutated tree" in results[0].detail


def test_a_green_baseline_still_grades_normally(tmp_path):
    results = run_mutations([_BREAK], [sys.executable, "check.py"], _tree(tmp_path, 42))
    assert [r.verdict for r in results] == [KILLED]
    assert all(r.trustworthy for r in results)


def test_a_mutation_the_contract_misses_still_survives(tmp_path):
    """The control must not turn every verdict into a pass."""
    root = _tree(tmp_path, 42)
    harmless = Mutation("rename nothing", (("src.py", "def answer", "def answer"),),
                        lambda sources: None)
    results = run_mutations([harmless], [sys.executable, "check.py"], root)
    assert [r.verdict for r in results] == [SURVIVED]


def test_every_mutation_is_reported_when_the_baseline_is_red(tmp_path):
    """Not just the first: the table must not look partly measured."""
    second = Mutation("another break", (("src.py", "return 42", "return 44"),),
                      lambda sources: None)
    results = run_mutations([_BREAK, second], [sys.executable, "check.py"],
                            _tree(tmp_path, 99))
    assert len(results) == 2
    assert {r.verdict for r in results} == {BASELINE_RED}


def test_the_refusal_is_shouted_in_the_rendered_table(tmp_path):
    results = run_mutations([_BREAK], [sys.executable, "check.py"], _tree(tmp_path, 99))
    table = render(results)
    assert table.startswith("!!")
    assert BASELINE_RED in table


def test_a_command_that_cannot_start_is_also_a_refusal(tmp_path):
    results = run_mutations([_BREAK], ["definitely-not-a-command-zzz"],
                            _tree(tmp_path, 42))
    assert [r.verdict for r in results] == [BASELINE_RED]
    assert "could not start" in results[0].detail


def test_the_tree_is_left_clean_after_a_refusal(tmp_path):
    root = _tree(tmp_path, 99)
    before = (root / "src.py").read_text(encoding="utf-8")
    run_mutations([_BREAK], [sys.executable, "check.py"], root)
    assert (root / "src.py").read_text(encoding="utf-8") == before


# ============================================================
# The raw e-mail bodies that would have landed in the public repo
# ============================================================

def test_the_debug_trace_does_not_land_in_the_engine_clone(monkeypatch):
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    from scripts.utils.observability_safe import _debug_trace_path
    from scripts.utils.workspace import get_workspace_root

    path = _debug_trace_path()
    assert get_workspace_root() not in path.parents, (
        f"raw e-mail bodies would be written to {path}, inside the PUBLIC engine"
    )


def test_the_debug_trace_uses_the_canonical_resolver(monkeypatch):
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    from scripts.inbox_pulse.paths import get_state_dir
    from scripts.utils.observability_safe import _debug_trace_path

    assert _debug_trace_path() == get_state_dir() / "debug-trace.jsonl"


def test_the_explicit_override_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    from scripts.utils.observability_safe import _debug_trace_path

    assert _debug_trace_path() == tmp_path / "debug-trace.jsonl"


def _blind_the_resolver(monkeypatch):
    """Make `from scripts.inbox_pulse.paths import get_state_dir` raise.

    A None entry in sys.modules is the documented way to force an ImportError.
    Without this the fallback branch is never executed by any test, and both a
    fallback pointing back into the engine and a dropped override survive.
    """
    monkeypatch.setitem(sys.modules, "scripts.inbox_pulse.paths", None)


def test_the_fallback_is_outside_the_engine_too(monkeypatch, capsys):
    """The seam can be unreachable; that is no reason to write into the repo."""
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    _blind_the_resolver(monkeypatch)
    from scripts.utils.observability_safe import _debug_trace_path
    from scripts.utils.workspace import get_workspace_root

    path = _debug_trace_path()
    assert get_workspace_root() not in path.parents
    assert get_workspace_root() != path.parent
    assert "state dir unresolved" in capsys.readouterr().err


def test_the_override_survives_an_unreachable_resolver(monkeypatch, tmp_path):
    """This is the whole reason the override is read before the import."""
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _blind_the_resolver(monkeypatch)
    from scripts.utils.observability_safe import _debug_trace_path

    assert _debug_trace_path() == tmp_path / "debug-trace.jsonl"


def test_the_engine_root_helper_is_gone():
    """Its one caller was the defect; leaving it invites the next writer."""
    source = (ROOT / "scripts" / "utils" / "observability_safe.py").read_text(
        encoding="utf-8")
    live = [ln for ln in source.splitlines()
            if "_workspace_root()" in ln and not ln.lstrip().startswith("#")]
    assert live == []


# ============================================================
# The probe that raised instead of answering
# ============================================================

class _Body(http.server.BaseHTTPRequestHandler):
    body = b"null"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def serve():
    servers = []

    def _serve(body: bytes) -> str:
        handler = type("H", (_Body,), {"body": body})
        server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _serve
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("body,expected,why", [
    (b'{"version":"0.5.1"}', True, "a real ollama reply"),
    (b'{"other":1}', False, "an object without a version"),
    (b"null", False, "a JSON null - this raised TypeError"),
    (b"3", False, "a JSON number"),
    (b"true", False, "a JSON bool"),
    (b'"a string"', False, "a JSON string"),
    (b"[1,2]", False, "a JSON array"),
])
def test_the_probe_answers_rather_than_raises(serve, body, expected, why):
    """Its whole contract is True or False; `auto:<port>` invites the rest."""
    assert probe(serve(body), timeout=3) is expected, why


def test_a_non_http_host_is_still_refused_without_a_request():
    assert probe("file:///etc/passwd") is False


# ============================================================
# The modem that was never read and reported nothing to report
# ============================================================

@pytest.mark.parametrize("reply", [
    "Command failed: Not found",
    "",
    "ubus: connection failed",
    "[1, 2, 3]",
])
def test_an_unreadable_reply_raises_instead_of_looking_healthy(reply):
    driver = E5800Driver(lambda *a, **k: reply)
    with pytest.raises(ModemReadError):
        driver.read_status()


def test_a_good_reply_still_reads():
    payload = '{"name": "modem0", "imei": "356938035643809"}'
    driver = E5800Driver(lambda *a, **k: payload)
    status = driver.read_status()
    assert status["device"] == "e5800"


def test_the_cli_refuses_rather_than_printing_nothing():
    source = (ROOT / "scripts" / "modem-tune.py").read_text(encoding="utf-8")
    assert "except ModemReadError" in source
    assert "Could not read the modem" in source


# ============================================================
# The internal contact that got deal-side citations
# ============================================================

@pytest.fixture
def brain(tmp_path):
    principles = tmp_path / "odin-brain" / "principles"
    principles.mkdir(parents=True)
    (principles / "p1.md").write_text(
        "---\nslug: p1\nkeywords: [negotiation, persuasion]\n---\nBody\n",
        encoding="utf-8")
    return tmp_path / "odin-brain"


@pytest.mark.parametrize("internal", ["tribe", "tribe-leadership", "inactive"])
@pytest.mark.parametrize("stage", [None, "Negotiation", "Proposal", "Demo/POC",
                                   "Qualified", "Lead", "Won", "Lost"])
def test_an_internal_contact_gets_no_citation_at_any_stage(brain, internal, stage):
    """The suppression was a side effect of an empty list; a stage undid it."""
    assert op.relevant_principles_for(internal, stage, brain_root=brain) == []


@pytest.mark.parametrize("stage", [None, "Negotiation", "Proposal", "Won", "Lost"])
def test_an_external_contact_is_never_silenced(brain, stage):
    """The early return must key off the type, not off the stage."""
    got = op.relevant_principles_for("prospect", stage, brain_root=brain)
    assert [p["slug"] for p in got] == ["p1"]


def test_a_stage_still_adds_its_own_domains(brain):
    """The union is the behaviour the fix must leave intact for external types.

    `investor-passive` maps to fundraising/negotiation/term-sheet, which do not
    reach a `sales` principle. The `Demo/POC` stage adds `sales`, and only then
    does the principle match.
    """
    sales_only = brain / "principles" / "p2.md"
    sales_only.write_text("---\nslug: p2\nkeywords: [sales]\n---\nBody\n",
                          encoding="utf-8")

    without = op.relevant_principles_for("investor-passive", None, brain_root=brain)
    with_stage = op.relevant_principles_for("investor-passive", "Demo/POC",
                                            brain_root=brain)

    assert "p2" not in [p["slug"] for p in without]
    assert "p2" in [p["slug"] for p in with_stage]


def test_the_internal_set_is_derived_from_the_table():
    """Two hand-maintained lists drift; one derived from the other cannot."""
    derived = frozenset(
        key for key, domains in op.RELATIONSHIP_DOMAINS.items() if not domains)
    assert derived == op.INTERNAL_TYPES
    assert "tribe" in op.INTERNAL_TYPES
    assert "prospect" not in op.INTERNAL_TYPES
