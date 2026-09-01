#!/usr/bin/env python3
"""The shared cadence reader raised where its docstring promised a reason.

`scripts/utils/odin_cadence.read_cadence_json` exists so that two callers of
`odin-cadence.py --json` cannot each pick up half the guards. Its docstring is a
total contract: it returns `(cadence, error)`, and `error` "is None on success
and a short human-readable reason otherwise, so a caller that renders can say
WHY it has no numbers instead of drawing a blank that looks measured".

It ran the child with `text=True` and no `errors=`, which decodes STRICT UTF-8
inside `subprocess.run` itself. `UnicodeDecodeError` is a `ValueError`; it is
not an `OSError` and it is not a `subprocess.SubprocessError`, so it went
straight past `except (OSError, subprocess.SubprocessError)` and out of the
function. That is the promise broken at the one moment it is worth anything: the
child is already misbehaving.

MEASURED 2026-09-01, a stand-in cadence child writing a raw `0xff` to stderr and
exiting 1:

    before   UnicodeDecodeError out of read_cadence_json, uncaught by both
             `--json` callers; `scripts/generate-dashboard.py` produced NO page
             at all, where the whole point of the 2026-08-29 fix was that it
             produce a page with a named failure on it
    after    ({}, "odin-cadence.py exited 1: ValueError: \\ufffd\\ufffd bad")

This is a one-of-N miss, not a new idea. The sibling readers of the same shape
already had it: `scripts/scrutinize-dispatch.py` passes `errors="replace"` on
the same grounds, with a comment recording the same measurement against
`--cmd "/bin/cat /bin/cat"`, and `.claude/hooks/checkpoint-precompact.py:_git`
names `ValueError` in its handler. `scripts/utils/odin_cadence.py` was extracted
after both and picked up neither.

`tests/test_a_panel_that_read_a_crash_as_no_data.py` could not see it: every
crash case there goes through a `subprocess` shim that hands back a
`CompletedProcess` already carrying `str` fields, so the decode never happens.
Its one un-shimmed test spawns a real child that writes clean ASCII.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.utils import odin_cadence as oc  # noqa: E402
from scripts.utils import ops_signals as ops  # noqa: E402

#: A child that writes raw bytes no UTF-8 decoder accepts. `os.write` on the
#: file descriptor, so Python's own stream encoding cannot quietly fix it.
_BAD_STDERR = (
    "import os, sys\n"
    "os.write(2, b'ValueError: cadence store \\xff\\xfe unreadable')\n"
    "sys.exit(1)\n"
)
_BAD_STDOUT = (
    "import os\n"
    "os.write(1, b'{\"reflect_clusters\": \\xff\\xfe}')\n"
)
_BAD_STDOUT_INSIDE_A_STRING = (
    "import os\n"
    "os.write(1, b'{\"last_collect\": \"2026-08-20\\xff\", \"days_since\": 9}')\n"
)


def _child(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "odin-cadence.py"
    path.write_text(body, encoding="utf-8")
    return path


# ============================================================
# The reader keeps its contract on undecodable output
# ============================================================

def test_an_undecodable_stderr_is_reported_and_never_raises(tmp_path):
    """The headline. A real child, no shim, one byte that is not UTF-8."""
    cadence, error = oc.read_cadence_json(
        tmp_path, script=_child(tmp_path, _BAD_STDERR))

    assert cadence == {}
    assert error, "an undecodable crash reported no failure at all"
    assert "exited 1" in error
    assert "cadence store" in error, (
        f"the child's own words were lost from the reason: {error!r}")


def test_an_undecodable_stdout_is_reported_and_never_raises(tmp_path):
    """The other stream. Exit 0, so the returncode guard cannot cover for it."""
    cadence, error = oc.read_cadence_json(
        tmp_path, script=_child(tmp_path, _BAD_STDOUT))

    assert cadence == {}
    assert error and "unparseable" in error, error


def test_a_bad_byte_inside_a_string_value_still_yields_a_report(tmp_path):
    """`errors="replace"`, not a wider handler, is why this case survives.

    The document is still valid JSON once the byte becomes U+FFFD, so the
    numbers the panel wants arrive. A handler that merely caught the decode
    error would have thrown the whole report away over one byte in a field
    nobody reads.
    """
    cadence, error = oc.read_cadence_json(
        tmp_path, script=_child(tmp_path, _BAD_STDOUT_INSIDE_A_STRING))

    assert error is None, error
    assert cadence["days_since"] == 9


# ============================================================
# Both `--json` callers inherit it, which is why the reader is shared
# ============================================================

def test_the_dashboard_collector_survives_an_undecodable_child(tmp_path,
                                                               monkeypatch):
    """The caller the 2026-08-29 fix was written for. It must still render."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dashboard_undecodable_child", ROOT / "scripts" / "generate-dashboard.py")
    dash = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = dash
    spec.loader.exec_module(dash)

    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    (brain / "episodes").mkdir(parents=True)
    (brain / "episodes" / "a-briefing-for-james-bond.md").write_text(
        '---\nid: "1"\ntitle: "a briefing"\ntype: episode\n'
        'updated: 2026-08-27\n---\n\nbody\n', encoding="utf-8")

    dash.odin_brain_dir = lambda p=brain: p
    dash.knowledge_dir = lambda p=knowledge: p
    dash.ODIN_CADENCE_SCRIPT = _child(tmp_path, _BAD_STDERR)
    dash.TODAY = __import__("datetime").date(2026, 8, 29)

    payoff = dash.collect_capture_payoff()

    assert payoff["available"] is True
    assert payoff["cadence_error"], "the undecodable crash arrived as a blank"
    html = dash.build_capture_payoff(payoff)
    assert "Odin cadence unread" in html


def test_the_radar_signal_survives_an_undecodable_child(tmp_path):
    """`ops_signals.odin_cadence_state` derives the path from the engine root,
    so the child has to sit where it looks for it."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "odin-cadence.py").write_text(_BAD_STDERR, encoding="utf-8")

    signal = ops.odin_cadence_state(tmp_path)

    assert signal["key"] == "odin_cadence"
    assert signal["due"] is False


# ============================================================
# The other direction: a clean child is unaffected
# ============================================================

def test_a_clean_child_still_returns_its_whole_report(tmp_path):
    cadence, error = oc.read_cadence_json(tmp_path, script=_child(
        tmp_path,
        "import json\n"
        "print(json.dumps({'reflect_clusters': 3, 'last_collect': '2026-08-20',\n"
        "                  'days_since': 9}))\n"))

    assert error is None
    assert cadence == {"reflect_clusters": 3, "last_collect": "2026-08-20",
                       "days_since": 9}


def test_a_non_ascii_but_valid_utf8_report_is_untouched(tmp_path):
    """`errors="replace"` must not be a licence to mangle legitimate text.

    The name is invented, and it carries a character outside ASCII so the decode
    has something real to get right.
    """
    body = (
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print('{\"last_collect\": \"2026-08-20\", \"note\": \"caf\\u00e9\"}')\n"
    )
    cadence, error = oc.read_cadence_json(tmp_path, script=_child(tmp_path, body))

    assert error is None, error
    assert cadence["note"] == "caf\u00e9"
    assert "\ufffd" not in cadence["note"]
