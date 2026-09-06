"""One undecodable byte in a path killed a whole xdist worker's report.

THE DEFECT, and it is the answer to why this suite's overlay ratchet flapped.

`tests/conftest.py::pytest_sessionfinish` has each xdist worker put its overlay
numbers into `config.workeroutput`, which xdist sends to the controller as the
`workerfinished` event. Two test modules in this repository deliberately build a
repository whose DIRECTORY NAME holds a non-UTF-8 byte and run `git init` on it:
`tests/test_a_push_wall_that_refused_the_root_it_was_given.py` and
`tests/test_a_diagnostic_that_crashed_the_push_it_narrated.py`. Python hands that
byte back through `sys.argv` as a surrogate escape (`\\udcff`), the guard records
the command verbatim as its example for that test, and the example rides
`overlay_reachable_by_test` onto the wire.

execnet cannot encode a surrogate. MEASURED 2026-09-06, full suite, `-n auto`:

    [gw13] node down: Traceback (most recent call last):
      File ".../execnet/gateway_base.py", line 1688, in _write_unicode_string
        as_bytes = s.encode("utf-8")
    UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcff'
    in position 88: surrogates not allowed

Two workers per run, gw0 and gw13 in the instrumented run, sending 273 and 101
digests. The controller received ZERO from both: `workeroutput` is only set on
`workerfinished`, and that event never arrives, so `pytest_testnodedown` fires
through the error path with the attribute absent. 374 of 2704 distinct tests, and
the same share of the child count, gone. THE RUN IS GREEN: every test passed, the
deaths happen after the last one, and nothing but a `node down` line in the
middle of the output says a thing.

THAT IS THE FLAP. Which worker holds those two modules moves with xdist's
scheduling, so 0 to 3 reports vanished per run: nine runs over one unchanged tree
gave 5537 to 6110 children and 2156 to 2454 distinct tests, while the true union
of every process's own map, dumped straight out of the guard, was 2677, 2678,
2678, 2679 -- a spread of two. The invariant was never moving. The wire was.

THE FIX: `_wire_safe` in `tests/conftest.py`, applied to every value put into
`workeroutput` through `_send_upward`, in the one place anything is put there.
MEASURED AFTER IT, three consecutive full runs at machine loads 0.68, 11.10 and
14.00: 6809 children and 2679 distinct tests every time, spread zero, 18 of 18
workers delivering. The same suite over the preceding ten runs moved 5537-6110
and 2156-2454. NOTE FOR WHOEVER FREEZES THE THRESHOLD: 6809 is above the
committed `reachable_children` of 6100, because that number was itself derived
from runs missing one to three workers.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import overlay_write_guard as guard  # noqa: E402
from tests import conftest as root_conftest  # noqa: E402

# The byte those two modules put in a directory name, as Python hands it back
# out of argv: an unpaired surrogate. Not a hypothetical -- `os.fsdecode(b"re"
# b"\xff" b"po")` is exactly this.
SURROGATE = "re\udcffpo"


class _Config:
    def __init__(self, worker=True):
        self.pluginmanager = self
        if worker:
            self.workerinput = {"workerid": "gw0"}
            self.workeroutput = {}

    def getoption(self, name, default=None):
        return {"collectonly": False}.get(name, default)

    def get_plugin(self, name):
        return None


class _Session:
    def __init__(self):
        self.config = _Config()
        self.exitstatus = 0


@pytest.fixture
def isolated_counters(monkeypatch):
    """The real run's counters must not be disturbed; see the sibling files."""
    monkeypatch.setattr(guard, "_CHILD_SPAWN_COUNT", 0, raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWNS", [], raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWNS_BY_TEST", {}, raising=True)
    monkeypatch.setattr(guard, "_CHILD_SPAWN_UNATTRIBUTED", 0, raising=True)
    monkeypatch.setattr(guard, "_WATCH_BEFORE", {"live": (ROOT, {})}, raising=True)
    monkeypatch.setattr(guard, "_watch_snapshot", lambda: {"live": (ROOT, {})})
    monkeypatch.setattr(guard, "watch_complaints", lambda before, after: [])


def _execnet_encode(value):
    """Serialize exactly the way xdist does, with execnet's own encoder.

    Not a stand-in `value.encode("utf-8")`: the claim is about what survives the
    `workerfinished` send, and only execnet's serializer settles that. It is the
    code that raised in the measured failure.
    """
    from execnet.gateway_base import dumps
    return dumps(value)


def test_a_surrogate_in_a_recorded_command_is_not_put_on_the_wire(
        isolated_counters):
    """The failing half, at the real hook.

    A spawn recorded with an undecodable byte in its command, then the worker
    branch of the real `pytest_sessionfinish`, then execnet's own encoder over
    what it produced. Before `_wire_safe` this raises UnicodeEncodeError, which
    in a real run kills the worker and loses its entire report.
    """
    guard._CHILD_SPAWNS_BY_TEST["tests/test_x.py::test_a"] = [
        1, f"git init -q -b main /tmp/{SURROGATE}"]
    guard._CHILD_SPAWN_COUNT = 1

    session = _Session()
    root_conftest.pytest_sessionfinish(session, 0)

    out = session.config.workeroutput
    assert out["overlay_reachable"] == 1, "the arrangement did not reach the hook"
    assert len(out["overlay_reachable_by_test"]) == 1
    _execnet_encode(out)      # the whole dict, the way xdist sends it


def test_a_surrogate_in_a_test_id_is_not_put_on_the_wire(isolated_counters):
    """The other place the byte can enter: the NODEID.

    `PYTEST_CURRENT_TEST` carries a parametrised id, and a parameter can be a
    path. Sanitising the command alone would leave this half open, and it is one
    dict key away from the same worker death.
    """
    guard._CHILD_SPAWNS_BY_TEST[f"tests/test_x.py::test_a[{SURROGATE}]"] = [
        1, "git init -q"]
    guard._CHILD_SPAWN_COUNT = 1

    session = _Session()
    root_conftest.pytest_sessionfinish(session, 0)

    _execnet_encode(session.config.workeroutput)


def test_a_leaked_scratch_path_with_the_same_byte_is_not_put_on_the_wire(
        isolated_counters, monkeypatch):
    """The temp-leak guard rides the same dict, so it has the same exposure.

    Both halves of `workeroutput` are written by one conftest; one unencodable
    string anywhere in it loses ALL of it, including the other guard's numbers.
    """
    from tests import tmp_leak_guard as tmpguard
    # The stand-in survivors are rooted at the temp directory the guard actually
    # watches, asked of the platform rather than spelled out. Nothing here is
    # created; only the surrogate in the name matters to what is being asserted.
    leaked = f"{tempfile.gettempdir()}/{SURROGATE}"
    monkeypatch.setattr(tmpguard, "survivors", lambda: [leaked])
    monkeypatch.setattr(
        tmpguard, "survivors_by_test",
        lambda limit: [(f"tests/test_x.py::t[{SURROGATE}]", 1, leaked)])
    monkeypatch.setattr(root_conftest, "_TMP_LEAK_BASELINE", ROOT / "does-not-exist")

    session = _Session()
    # That guard answers only for the session pytest actually started, by
    # identity: see `_REAL_SESSION_CONFIG`. Claiming the identity is what lets a
    # synthetic session reach it, and it is restored with the monkeypatch.
    monkeypatch.setattr(root_conftest, "_REAL_SESSION_CONFIG", session.config)
    root_conftest._tmp_leak_sessionfinish(session)

    out = session.config.workeroutput
    assert out["tmp_leak_survivors"] == 1, "the arrangement did not reach the hook"
    _execnet_encode(out)


def test_a_clean_report_is_unchanged_by_the_sanitiser(isolated_counters):
    """The direction a blunt fix would break: ordinary rows must survive intact.

    A sanitiser that mangled, dropped or re-typed ordinary values would satisfy
    every test above and lose the report it exists to deliver.
    """
    guard._CHILD_SPAWNS_BY_TEST["tests/test_x.py::test_a"] = [3, "git init -q"]
    guard._CHILD_SPAWN_COUNT = 3

    session = _Session()
    root_conftest.pytest_sessionfinish(session, 0)

    out = session.config.workeroutput
    assert out["overlay_reachable"] == 3
    assert out["overlay_reachable_by_test"] == [("tests/test_x.py::test_a",
                                                 "git init -q", 3)]
    assert out["overlay_reachable_unattributed"] == 0
    assert out["overlay_reachable_map_capped"] == 0
    assert len(out["overlay_reachable_test_digests"]) == 1
    _execnet_encode(out)


def test_the_sanitiser_reaches_every_container_shape_it_claims_to():
    """The helper itself, over the shapes a future sender can reach for.

    Written because a mutation survived: nothing this conftest currently sends
    is a dict, so `_wire_safe` returning dicts untouched broke no test while
    leaving the hole open for the first `output["x"] = {...}` anyone adds. A
    branch with no test is a branch that is not there.
    """
    safe = root_conftest._wire_safe
    out = safe({SURROGATE: [("a", SURROGATE, 1)],
                "plain": {"nested": (SURROGATE, 2)}})
    _execnet_encode(out)
    assert list(out) == ["re?po", "plain"], out
    assert out["re?po"] == [("a", "re?po", 1)]
    assert out["plain"]["nested"] == ("re?po", 2)
    # Non-strings pass through as themselves, types intact: a sanitiser that
    # stringified them would corrupt every count on the wire.
    assert safe(7) == 7 and safe(None) is None and safe(True) is True
    assert safe(("a", 1)) == ("a", 1) and isinstance(safe(("a",)), tuple)


def test_the_two_modules_that_hold_the_byte_still_hold_it():
    """The corpus this defect actually came from, asserted rather than recalled.

    If both modules stop building a non-UTF-8 path, the tests above still hold
    the invariant but the docstring's account of WHERE the byte came from has
    gone stale, and a future reader would be chasing a repository that no longer
    behaves that way. This fails loudly instead.
    """
    named = ["tests/test_a_push_wall_that_refused_the_root_it_was_given.py",
             "tests/test_a_diagnostic_that_crashed_the_push_it_narrated.py"]
    holding = []
    for rel in named:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "\\xff" in text or "surrogate" in text:
            holding.append(rel)
    assert holding == named, (
        f"only {holding} still build a non-UTF-8 path; the account in this "
        "module's docstring needs re-measuring"
    )


def test_the_real_spawn_path_records_a_byte_it_cannot_encode(tmp_path):
    """Where the surrogate comes from, driven end to end rather than asserted.

    A directory whose NAME is not valid UTF-8, a real `subprocess.run` against
    it, and the string the guard would keep as its example. This is the step the
    two modules above take by accident; if Python ever stopped surrogate-escaping
    argv, this test would go green for the wrong reason and say so by failing
    the encode assertion below.
    """
    weird = tmp_path / b"re\xffpo".decode("utf-8", "surrogateescape")
    weird.mkdir()
    proc = subprocess.run(["git", "init", "-q", str(weird)],
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:200]

    head = " ".join(str(a) for a in ["git", "init", "-q", str(weird)])[:120]
    with pytest.raises(UnicodeEncodeError):
        head.encode("utf-8")
    # And the repair makes exactly that string sendable.
    _execnet_encode(root_conftest._wire_safe(head))


if __name__ == "__main__":                      # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
