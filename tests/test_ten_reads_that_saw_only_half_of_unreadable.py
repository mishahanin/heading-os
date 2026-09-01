"""Ten reads whose recovery saw only one of the two ways a file is unreadable.

`UnicodeDecodeError` subclasses `ValueError`. `json.JSONDecodeError` also
subclasses `ValueError`. They are SIBLINGS, so `except json.JSONDecodeError`
does not catch a decode failure, and neither does `except OSError`.

The class has two spellings and this shard's blast radius carried both. Seven
places wrapped a `read_text(encoding="utf-8")` or a `json.load` on a text file
in a handler that could see only one half; three more guarded the read with
nothing at all, which no sweep for a NARROW handler can find, because there is
no handler to be narrow. Every one of the ten sits directly above, or directly
inside, a NAMED recovery a non-UTF-8 file could not reach.

MEASURED 2026-09-01, each with `b"\\xff\\xfe\\x00binary"` in place of the file
it reads. The eight bound in THIS file:

  fireside-bot-daemon.cmd_status         printed the RUNNING line, then died on
                                         the registered-jobs file
  firecrawl.check_cache                  died instead of returning None
  prime-health-parallel
    .run_email_intel_status              died instead of the named
                                         "state.json unreadable" panel
  prime-health-parallel.run_updates      the same, one function over
  prime-health-parallel.run_dream_shadow `except OSError` alone, directly above
                                         a message reading "dream-shadow report
                                         unreadable"
  odin-cadence.count_viraid              died instead of appending to `skipped`,
                                         which is the list that stops the JSON
                                         asserting a complete pass it did not
                                         make
  chromium_cookies.find_profile_folder   died instead of raising the sentence
                                         its own handler exists to raise
  firecrawl.load_blocked_domains         NO handler; a content-quality control
                                         died with a traceback where its
                                         docstring promises "says so on stderr
                                         when it loads nothing"

Two more are on CREDENTIAL paths and are bound beside the rest of their own
subject, not here:

  healthchecks_setup.write_env           NO handler, reading the same `.env`
                                         that `load_env_key` twenty lines up
                                         already guards. Bound by
                                         `tests/test_a_credential_load_that_ran_
                                         before_anyone_asked_for_it.py`.
  chromium_cookies._get_keys_win         NO handler, reading the same Local
                                         State that `find_profile_folder`
                                         above already guards. Bound by
                                         `tests/test_a_cookie_reader_that_
                                         answered_with_the_wrong_bytes.py`.

The fix widens each tuple to `(OSError, ValueError)`, which is the form
`chromium_cookies._merge_playwright` and `fireside-bot-daemon`'s own
started-at reader already use for exactly this reason, and gives each unguarded
read the guard its own twin already had. `ValueError` is not over-broad here:
the only calls inside each of those `try` blocks are the read and the JSON
parse, so it means precisely {UnicodeDecodeError, JSONDecodeError}.

Not touched, and deliberately: `firecrawl` catches `json.JSONDecodeError` around
`json.loads(schema_str)`. The argument is already a `str`, so no decoding
happens and `UnicodeDecodeError` is unreachable there. `prime-health-parallel`
reads one file with `errors="ignore"` under an `except OSError`, where a decode
failure is impossible by construction. The structural guard at the bottom of
this file is scoped so it does not claim either is a defect.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Invalid as UTF-8 at byte 0, and short enough to read in a failure message.
UNDECODABLE = b"\xff\xfe\x00binary"


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_two_errors_really_are_siblings():
    """The premise. If either stopped subclassing ValueError, or one became a
    subclass of the other, every case below would be measuring something else.
    """
    assert issubclass(UnicodeDecodeError, ValueError)
    assert issubclass(json.JSONDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, json.JSONDecodeError)
    assert not issubclass(json.JSONDecodeError, UnicodeDecodeError)
    assert not issubclass(UnicodeDecodeError, OSError)


# ---------------------------------------------------------------------------
# fireside-bot-daemon.cmd_status
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fbd():
    return _load("fbd_undecodable", "scripts/fireside-bot-daemon.py")


@pytest.fixture
def daemon_files(fbd, tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    jobs = tmp_path / "registered-jobs.json"
    monkeypatch.setattr(fbd, "PID_FILE", pid_file)
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", tmp_path / "absent.started")
    monkeypatch.setattr(fbd, "REGISTERED_JOBS_FILE", jobs)
    return jobs


def test_status_survives_a_registered_jobs_file_it_cannot_decode(
        fbd, daemon_files, capsys):
    """`status` is the command an operator runs to find out what is going on.
    Dying halfway through it is the worst moment to die."""
    daemon_files.write_bytes(UNDECODABLE)

    assert fbd.cmd_status(None) is None

    out = capsys.readouterr().out
    assert f"RUNNING pid={os.getpid()}" in out
    assert "jobs.json is missing or unreadable" in out


def test_status_survives_a_registered_jobs_file_that_is_not_json(
        fbd, daemon_files, capsys):
    """Anchor: the half of the handler that always worked must keep working."""
    daemon_files.write_text("{not json", encoding="utf-8")

    fbd.cmd_status(None)

    assert "jobs.json is missing or unreadable" in capsys.readouterr().out


def test_status_still_reads_a_good_registered_jobs_file(fbd, daemon_files, capsys):
    """The other jaw. A recovery that fires on every input reports nothing.

    The file has to name THIS pid: `cmd_status` has a second refusal for a file
    written by a different process, and it prints a different sentence.
    """
    daemon_files.write_text(
        json.dumps({"pid": os.getpid(), "jobs": ["heartbeat", "poll"]}),
        encoding="utf-8")

    fbd.cmd_status(None)

    out = capsys.readouterr().out
    assert "jobs registered: heartbeat, poll" in out
    assert "unreadable" not in out


# ---------------------------------------------------------------------------
# firecrawl.check_cache
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fc():
    return _load("firecrawl_undecodable", "scripts/firecrawl.py")


@pytest.fixture
def cache(fc, tmp_path, monkeypatch):
    directory = tmp_path / "cache"
    directory.mkdir()
    monkeypatch.setattr(fc, "cache_dir", lambda: directory)
    return directory


def test_a_cache_entry_that_cannot_be_decoded_is_a_miss(fc, cache):
    """A cache entry is the most disposable thing in this script."""
    (cache / "k.json").write_bytes(UNDECODABLE)

    assert fc.check_cache("k", 24) is None


def test_a_cache_entry_that_is_not_json_is_still_a_miss(fc, cache):
    (cache / "k.json").write_text("{not json", encoding="utf-8")

    assert fc.check_cache("k", 24) is None


def test_an_undecodable_domains_file_blocks_nothing_and_says_so(fc, tmp_path,
                                                                monkeypatch,
                                                                capsys):
    """`search-domains.md` is OPERATOR-authored, in a bilingual RU/EN workspace,
    so a copy an editor saved as cp1251 is an ordinary accident.

    The read had no handler at all. Its docstring says the function "says so on
    stderr when it loads nothing", and instead the whole command died with a
    traceback out of a content-quality control. MEASURED 2026-09-01 with a file
    of Latin-1 Cyrillic prose: `UnicodeDecodeError` from `load_blocked_domains`.
    """
    bad = tmp_path / "search-domains.md"
    # "## Blocked Domains" plus a Cyrillic comment encoded cp1251, which is not
    # valid UTF-8. Written as escapes so this file stays pure ASCII on disk.
    bad.write_bytes(
        "## Blocked Domains\n\n\u041a\u043e\u043c\u043c\u0435\u043d\u0442\n"
        .encode("cp1251"))
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: bad)

    assert fc.load_blocked_domains() == []
    err = capsys.readouterr().err
    assert "could not be read" in err
    assert "NO domains are being blocked" in err
    assert "UnicodeDecodeError" in err


def test_a_readable_domains_file_is_still_parsed(fc, tmp_path, monkeypatch):
    """The other jaw. A guard that returns [] for every file is the exact no-op
    this control was rewritten to stop being."""
    good = tmp_path / "search-domains.md"
    good.write_text("## Blocked Domains\n\n- pinterest.test\n- quora.test\n",
                    encoding="utf-8")
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: good)

    assert fc.load_blocked_domains() == ["pinterest.test", "quora.test"]


def test_a_good_cache_entry_is_still_a_hit(fc, cache, monkeypatch):
    """Anchor: returning None unconditionally would pass both tests above.

    The payload lives under `content` and the TTL is measured against
    `timestamp`. The clock is replaced by rebinding the module-level name `time`
    INSIDE firecrawl, never by assigning to `time.time` itself, which would
    rebind the stdlib for every other test in the session.
    """
    import types

    (cache / "k.json").write_text(
        json.dumps({"timestamp": 1_800_000_000, "content": {"ok": 1}}),
        encoding="utf-8")
    monkeypatch.setattr(
        fc, "time", types.SimpleNamespace(time=lambda: 1_800_000_000 + 3600))

    assert fc.check_cache("k", 24) == {"ok": 1}
    assert fc.check_cache("k", 0.5) is None, "the TTL gate stopped biting"


# ---------------------------------------------------------------------------
# prime-health-parallel: two checks, one shape
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ph():
    return _load("ph_undecodable", "scripts/prime-health-parallel.py")


@pytest.fixture
def outputs(ph, tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    (root / "operations" / "email-intelligence").mkdir(parents=True)
    (root / "operations" / "updates").mkdir(parents=True)
    monkeypatch.setattr(ph, "get_outputs_dir", lambda: root)
    return root


def test_the_email_panel_reports_an_undecodable_state_instead_of_raising(
        ph, outputs, tmp_path):
    state = outputs / "operations" / "email-intelligence" / "state.json"
    state.write_bytes(UNDECODABLE)

    res = ph.run_email_intel_status(tmp_path)

    assert res["status"] == "error"
    assert "state.json unreadable" in res["output"]


def test_the_email_panel_still_reports_a_json_error(ph, outputs, tmp_path):
    state = outputs / "operations" / "email-intelligence" / "state.json"
    state.write_text("{not json", encoding="utf-8")

    assert "state.json unreadable" in ph.run_email_intel_status(tmp_path)["output"]


def test_the_email_panel_still_reads_a_good_state(ph, outputs, tmp_path):
    """Anchor: an error branch that fires on every state file says nothing."""
    state = outputs / "operations" / "email-intelligence" / "state.json"
    state.write_text(json.dumps({"last_run": "2026-09-01"}), encoding="utf-8")

    res = ph.run_email_intel_status(tmp_path)

    assert "unreadable" not in res["output"]


def test_the_updates_panel_reports_an_undecodable_state_instead_of_raising(
        ph, outputs, tmp_path):
    (outputs / "operations" / "updates" / "state.json").write_bytes(UNDECODABLE)

    res = ph.run_updates(tmp_path)

    assert res["status"] == "error"
    assert "updates state unreadable" in res["output"]


def test_the_updates_panel_still_reports_a_json_error(ph, outputs, tmp_path):
    (outputs / "operations" / "updates" / "state.json").write_text(
        "{not json", encoding="utf-8")

    assert "updates state unreadable" in ph.run_updates(tmp_path)["output"]


def test_the_dream_panel_reports_an_undecodable_report_instead_of_raising(
        ph, outputs, tmp_path):
    """The third handler in this module, and the one an AST sweep for
    `JSONDecodeError` could not see: it was spelled `except OSError` alone,
    directly above a message reading "dream-shadow report unreadable"."""
    report = outputs / "operations" / "dream" / "2026-09-01_dream-shadow_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(UNDECODABLE)

    res = ph.run_dream_shadow(tmp_path)

    assert res["status"] == "error"
    assert "dream-shadow report unreadable" in res["output"]


def test_the_dream_panel_still_reads_a_good_report(ph, outputs, tmp_path):
    """The other jaw. An error branch that fires on every report says nothing.

    The check is quiet unless the report names merge candidates, so a report
    with none must produce no panel rather than an error.
    """
    report = outputs / "operations" / "dream" / "2026-09-01_dream-shadow_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Dream shadow\n\nNothing to merge.\n", encoding="utf-8")

    res = ph.run_dream_shadow(tmp_path)

    assert "unreadable" not in res["output"]
    assert res["status"] != "error"


def test_the_updates_panel_stays_quiet_on_a_good_state(ph, outputs, tmp_path):
    """Anchor: silent when everything is current is what the docstring promises."""
    (outputs / "operations" / "updates" / "state.json").write_text(
        json.dumps({"updates": {}}), encoding="utf-8")

    res = ph.run_updates(tmp_path)

    assert "unreadable" not in res["output"]


# ---------------------------------------------------------------------------
# odin-cadence.count_viraid
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def oc():
    return _load("odin_cadence_undecodable", "scripts/odin-cadence.py")


def _viraid_state(oc, tmp_path: Path) -> Path:
    state = tmp_path / oc.VIRAID_STATE
    state.parent.mkdir(parents=True, exist_ok=True)
    return state


def test_an_undecodable_viraid_state_is_recorded_as_skipped(oc, tmp_path):
    """`skipped` is what stops the JSON asserting a pass it did not make, and
    the crash bypassed it entirely."""
    _viraid_state(oc, tmp_path).write_bytes(UNDECODABLE)
    skipped: list[str] = []

    assert oc.count_viraid(tmp_path, "2026-01-01", skipped) == 0
    assert len(skipped) == 1
    assert "unreadable" in skipped[0]
    assert "UnicodeDecodeError" in skipped[0], skipped


def test_a_non_json_viraid_state_names_its_own_error_type(oc, tmp_path):
    """Anchor, and it proves the type name is read off the exception rather
    than hardcoded to the case above."""
    _viraid_state(oc, tmp_path).write_text("{not json", encoding="utf-8")
    skipped: list[str] = []

    assert oc.count_viraid(tmp_path, "2026-01-01", skipped) == 0
    assert "JSONDecodeError" in skipped[0], skipped


def test_a_readable_viraid_state_skips_nothing(oc, tmp_path):
    """The other jaw. A counter that always returns 0 and always skips would
    satisfy both cases above.

    `messages` is a MAPPING of message id to message; the reader iterates
    `.items()`, so an empty list here would crash for a reason that has nothing
    to do with decoding.
    """
    _viraid_state(oc, tmp_path).write_text(json.dumps({"messages": {}}),
                                           encoding="utf-8")
    skipped: list[str] = []

    assert oc.count_viraid(tmp_path, "2026-01-01", skipped) == 0
    assert skipped == []


# ---------------------------------------------------------------------------
# The structural guard: no sixth copy
# ---------------------------------------------------------------------------

GUARDED = [
    "scripts/fireside-bot-daemon.py",
    "scripts/firecrawl.py",
    "scripts/prime-health-parallel.py",
    "scripts/odin-cadence.py",
    "scripts/utils/chromium_cookies.py",
]

# A `try` body that DECODES: `read_text` runs the codec, and `json.load` decodes
# through the text wrapper it is handed. `json.loads` does neither, because its
# argument is already a `str` - that is why the literal below carries the closing
# parenthesis, so `json.loads(` cannot match it.
_DECODING_CALLS = ("read_text(", "json.load(", "open(")

# `errors="replace"` / `"ignore"` / `"surrogateescape"` make UnicodeDecodeError
# impossible, and a body reading BYTES never decodes at all. Flagging either
# would be a false positive, and a detector that cries wolf gets switched off.
_NO_DECODE_POSSIBLE = ("errors=", "read_bytes(", '"rb"', "'rb'")

# A handler naming either of these is CLAIMING to recover from "the file could
# not be read". That claim is what obliges it to admit ValueError.
#
# WIDENED 2026-09-01. This read `JSONDecodeError` alone, so a bare
# `except OSError` over a decoding read walked straight past it -- and one did,
# in a module already on the GUARDED list: `run_dream_shadow` in
# `prime-health-parallel.py` had `except OSError` above a message reading
# "dream-shadow report unreadable". The detector was named for the defect and
# shaped to one of its spellings, which is the same hole the campaign keeps
# finding in security detectors.
_CLAIMS_UNREADABLE = ("JSONDecodeError", "OSError")

# Anything here already means "and a decode failure too".
_ADMITS_DECODE = {"ValueError", "Exception", "BaseException",
                  "UnicodeDecodeError", "UnicodeError"}


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    return {ast.unparse(p) for p in parts}


def _decoding_tries(source: str):
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        body = "\n".join(ast.unparse(stmt) for stmt in node.body)
        if any(safe in body for safe in _NO_DECODE_POSSIBLE):
            continue
        if any(call in body for call in _DECODING_CALLS):
            yield node, body


def _narrow_handlers(relpath: str, source: str) -> list[str]:
    """Every handler over a decoding read that claims unreadability and cannot
    see a decode failure."""
    offenders = []
    for node, _body in _decoding_tries(source):
        for handler in node.handlers:
            names = _handler_names(handler)
            if not any(claim in n for n in names for claim in _CLAIMS_UNREADABLE):
                continue
            if names & _ADMITS_DECODE:
                continue
            offenders.append(f"{relpath}:{handler.lineno} {sorted(names)}")
    return offenders


@pytest.mark.parametrize("relpath", GUARDED)
def test_no_decoding_read_narrows_its_recovery_to_a_json_error(relpath):
    """A handler claiming a file is unreadable must admit BOTH ways it can be.

    `except OSError` and `except json.JSONDecodeError` each cover one half, and
    `UnicodeDecodeError` is neither: it is a ValueError, a SIBLING of
    JSONDecodeError. A handler naming either without also admitting ValueError
    recovers from one half of its own claim.
    """
    source = (ROOT / relpath).read_text(encoding="utf-8")
    offenders = _narrow_handlers(relpath, source)
    assert offenders == [], (
        "UnicodeDecodeError is a ValueError and a sibling of "
        f"json.JSONDecodeError, so these handlers miss it: {offenders}")


def test_the_structural_guard_finds_the_tries_it_is_looking_for():
    """A scan over zero `try` blocks passes every file it is pointed at."""
    counted = {rel: len(list(_decoding_tries(
        (ROOT / rel).read_text(encoding="utf-8")))) for rel in GUARDED}
    assert all(n > 0 for n in counted.values()), counted


@pytest.mark.parametrize("handler", [
    "except (OSError, json.JSONDecodeError):",
    "except json.JSONDecodeError:",
    "except OSError:",
    "except OSError as exc:",
    "except (json.JSONDecodeError, OSError) as exc:",
])
def test_the_structural_guard_refuses_every_spelling_of_the_narrow_handler(handler):
    """The case ON the line, once per spelling.

    Until 2026-09-01 the reader looked for `JSONDecodeError` alone, so the
    third and fourth entries here walked past it, and one of them was live in
    an already-GUARDED module: `run_dream_shadow` had `except OSError` over a
    branch whose message says "dream-shadow report unreadable". A detector
    tested only with the spelling its author happened to write keeps its hole.
    """
    bad = ("import json\n"
           "try:\n"
           "    d = json.loads(p.read_text(encoding='utf-8'))\n"
           f"{handler}\n"
           "    d = None\n")
    assert _narrow_handlers("sample.py", bad), handler


@pytest.mark.parametrize("handler", [
    "except (OSError, ValueError):",
    "except ValueError:",
    "except (OSError, UnicodeDecodeError):",
    "except Exception:",
])
def test_the_structural_guard_accepts_a_handler_that_admits_a_decode_failure(handler):
    """The other direction: a guard that refuses every handler is not a guard,
    and it would refuse the six this file exists to have fixed."""
    fine = ("import json\n"
            "try:\n"
            "    d = json.loads(p.read_text(encoding='utf-8'))\n"
            f"{handler}\n"
            "    d = None\n")
    assert _narrow_handlers("sample.py", fine) == [], handler


def test_the_structural_guard_leaves_a_loads_on_a_string_alone():
    """`json.loads(some_str)` cannot raise UnicodeDecodeError: the argument is
    already decoded. Flagging it would be a false positive that gets the guard
    switched off, and `scripts/firecrawl.py` has exactly that call."""
    fine = ("import json\n"
            "try:\n"
            "    schema = json.loads(schema_str)\n"
            "except json.JSONDecodeError:\n"
            "    schema = None\n")
    assert list(_decoding_tries(fine)) == []


@pytest.mark.parametrize("body", [
    "    t = p.read_text(encoding='utf-8', errors='replace')",
    "    t = p.read_text(encoding='utf-8', errors='ignore')",
    "    t = p.read_bytes()",
    "    t = open(p, 'rb').read()",
])
def test_the_structural_guard_leaves_a_read_that_cannot_fail_alone(body):
    """`errors=` makes UnicodeDecodeError impossible and a byte read never
    decodes. `prime-health-parallel` has an `errors='ignore'` read under an
    `except OSError`, and flagging it would be the guard crying wolf about the
    one handler in the file that is already correct."""
    fine = f"try:\n{body}\nexcept OSError:\n    t = ''\n"
    assert list(_decoding_tries(fine)) == [], body
