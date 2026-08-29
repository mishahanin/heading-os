"""The recorded reproduction command must be the command that ran.

`reproduce()` and `promote()` both write a `reproduction.cmd` field, and that
field is the evidence a REPRODUCED / FALSIFIED verdict rests on. Both built it
with `" ".join(cmd)` over the ALREADY-SPLIT argv, which throws quoting away:
`--cmd 'python3 -c "import sys; sys.exit(1)"'` was stored as
`python3 -c import sys; sys.exit(1)`.

Measured 2026-08-30. That stored string is not the command that ran, and it is
worse than merely lossy: fed back to `--cmd`, this module's own
`shell_operators_in_source` returns `['(', ')', ';']` and the harness refuses
it. The evidence field of a row fails the guard of the harness that wrote it.

Both directions are asserted here. The raw `--cmd` string must survive verbatim
when the CLI supplied one, and an in-process caller with argv only must get a
spelling that splits back to the same argv and clears the guard.
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    path = ROOT / "scripts" / "scrutinize-dispatch.py"
    spec = importlib.util.spec_from_file_location("scrutinize_dispatch_cmd_fidelity", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


disp = _load()

# Quoting is the whole subject, so the payload carries the two characters that
# only survive it: a `;` that belongs to Python and the parentheses of a call.
_PAYLOAD = "import sys; sys.exit(1)"
_ARGV = [sys.executable, "-c", _PAYLOAD]
_RAW = f'{shlex.quote(sys.executable)} -c "{_PAYLOAD}"'


@pytest.fixture
def runs(tmp_path, monkeypatch):
    from scripts.utils import scrutinize_record as rec
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    return path


def _reproduction_rows(path: Path) -> list[dict]:
    assert path.exists(), "nothing was recorded at all, so there is nothing to judge"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    out = [r for r in rows if r.get("kind") == "reproduction"]
    assert out, "no reproduction row was written"
    return out


def test_the_raw_cmd_string_is_recorded_verbatim(runs):
    assert disp.reproduce(run_id="r-fidelity", target="t", finding_id="H1",
                          cmd=_ARGV, source=_RAW) == 0
    recorded = _reproduction_rows(runs)[0]["reproduction"]["cmd"]
    assert recorded == _RAW


def test_an_argv_only_caller_gets_a_spelling_that_splits_back_to_that_argv(runs):
    assert disp.reproduce(run_id="r-fidelity", target="t", finding_id="H2",
                          cmd=_ARGV, source=None) == 0
    recorded = _reproduction_rows(runs)[0]["reproduction"]["cmd"]
    assert shlex.split(recorded) == _ARGV


def test_the_recorded_command_is_not_refused_by_this_modules_own_guard(runs):
    """The row's evidence must be re-runnable through the harness that wrote it."""
    assert disp.reproduce(run_id="r-fidelity", target="t", finding_id="H3",
                          cmd=_ARGV, source=None) == 0
    recorded = _reproduction_rows(runs)[0]["reproduction"]["cmd"]
    assert disp.shell_operators_in_source(recorded) == []


def test_promote_records_the_same_faithful_command(runs, tmp_path):
    """FALSIFIED joins a stored pre-fix exit to a fresh zero; its row is evidence too."""
    assert disp.reproduce(run_id="r-promote", target="t", finding_id="H4",
                          cmd=_ARGV, source=_RAW) == 0
    green_payload = "import sys; sys.exit(0)"
    green_argv = [sys.executable, "-c", green_payload]
    green_raw = f'{shlex.quote(sys.executable)} -c "{green_payload}"'
    assert disp.promote(run_id="r-promote", target="t", finding_id="H4",
                        cmd=green_argv, source=green_raw) == 0
    falsified = [r for r in _reproduction_rows(runs) if r["verdict"] == "FALSIFIED"]
    assert len(falsified) == 1
    assert falsified[0]["reproduction"]["cmd"] == green_raw


def test_a_plain_command_with_nothing_to_quote_is_unchanged(runs):
    """The anchor: quoting fidelity must not start decorating ordinary argv."""
    argv = [sys.executable, "-c", "raise SystemExit(1)"]
    assert disp.reproduce(run_id="r-plain", target="t", finding_id="H5",
                          cmd=argv, source=None) == 0
    recorded = _reproduction_rows(runs)[0]["reproduction"]["cmd"]
    assert shlex.split(recorded) == argv
    assert recorded.startswith(shlex.quote(sys.executable))
