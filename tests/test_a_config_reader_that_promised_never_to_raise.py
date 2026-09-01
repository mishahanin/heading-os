"""`service_config()` says "NEVER raises" and raised on the first thing it calls.

`scripts/pull-service-state.py::service_config` returns `(config, error)` so that
its callers can turn a bad config into a printed line and an exit code. Its
docstring states the promise without a qualifier, and records exactly why the
promise matters: "this used to run at import, where no handler was in scope",
and "carrying the error as a value lets `state_dirs` raise the ValueError that
`main` already catches and prints properly".

The handler read `except (OSError, UnicodeDecodeError)`, which is complete over
the READ. It is not complete over the line above the read. Resolving WHERE the
file lives runs first, through `resolve_config_with_example` ->
`get_data_config_dir()` -> `get_workspace_root()`, and a workspace root that will
not resolve (marker file gone, an unresolvable `~` in WORKSPACE_ROOT, a UID with
no passwd entry) raises RuntimeError, which is neither of those.

MEASURED 2026-09-01 with a resolver raising RuntimeError:

    service_config()  -> RuntimeError: workspace markers missing
    state_dirs()      -> RuntimeError, past `main`'s `except ValueError`

so the run ended in a traceback instead of the one-line reason, out of the one
function in the file documented not to do that. This is the same shape
`scripts/utils/daemon_heartbeat.beat` was widened for on the same grounds: a
promise of totality that only covered the exception the author was thinking of.

Nothing here reaches the service VM. `state_dirs` is stubbed empty in the one
test that calls `main`, so no scp is ever spawned.

Run: .venv/bin/python -m pytest
tests/test_a_config_reader_that_promised_never_to_raise.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def pull():
    spec = importlib.util.spec_from_file_location(
        "pull_service_state_total_probe", ROOT / "scripts" / "pull-service-state.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pull_service_state_total_probe"] = module
    spec.loader.exec_module(module)
    return module


# Failures the resolve step can genuinely produce and the read step cannot.
# RuntimeError is what `get_workspace_root` raises with its markers gone and
# what `Path.expanduser()` raises with no determinable home; KeyError stands for
# the passwd-lookup family. Neither is an OSError or a UnicodeDecodeError.
RESOLVE_FAULTS = [
    RuntimeError("workspace markers missing"),
    KeyError("getpwuid(): uid not found"),
]


def _fault(exc):
    def _raise(*args, **kwargs):
        raise exc
    return _raise


@pytest.mark.parametrize("exc", RESOLVE_FAULTS, ids=lambda e: type(e).__name__)
def test_a_resolve_failure_comes_back_as_a_value_not_an_exception(pull, monkeypatch,
                                                                  exc):
    monkeypatch.setattr(pull, "resolve_config_with_example", _fault(exc))

    config, error = pull.service_config()

    assert config == {}
    assert error, "the failure was swallowed into a silent empty config"
    assert type(exc).__name__ in error, error


@pytest.mark.parametrize("exc", RESOLVE_FAULTS, ids=lambda e: type(e).__name__)
def test_the_reason_reaches_state_dirs_as_the_error_main_catches(pull, monkeypatch,
                                                                 exc):
    """Returning a value is only useful if it arrives as the type `main` handles.
    `main` catches ValueError and nothing else."""
    monkeypatch.setattr(pull, "resolve_config_with_example", _fault(exc))

    with pytest.raises(ValueError) as excinfo:
        pull.state_dirs()
    assert type(exc).__name__ in str(excinfo.value)


def test_the_run_exits_with_a_code_and_a_named_line(pull, monkeypatch, capsys,
                                                    tmp_path):
    """End to end, which is where the traceback actually landed."""
    monkeypatch.setattr(pull, "load_env", lambda: None)
    monkeypatch.setattr(pull, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(pull, "resolve_config_with_example",
                        _fault(RuntimeError("workspace markers missing")))
    monkeypatch.setenv("SERVICE_VM_HOST", "vm.example.invalid")

    rc = pull.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "service-host.json" in out
    assert "workspace markers missing" in out


def test_a_read_failure_still_gets_its_own_older_wording(pull, monkeypatch,
                                                         tmp_path):
    """The two branches say different things, and the widening must not collapse
    them: "could not be read" is a file that exists and will not open, which is
    a different thing to fix from a root that will not resolve."""
    bad = tmp_path / "service-host.json"
    bad.write_bytes(json.dumps({"state_dirs": []}).encode("utf-16"))
    monkeypatch.setattr(pull, "resolve_config_with_example", lambda *a, **k: bad)

    config, error = pull.service_config()

    assert config == {}
    assert "could not be read" in error, error


def test_a_malformed_json_config_still_gets_its_own_wording(pull, monkeypatch,
                                                            tmp_path):
    bad = tmp_path / "service-host.json"
    bad.write_text('{"state_dirs": [', encoding="utf-8")
    monkeypatch.setattr(pull, "resolve_config_with_example", lambda *a, **k: bad)

    config, error = pull.service_config()

    assert config == {}
    assert "not valid JSON" in error, error


def test_a_good_config_is_still_returned_with_no_error(pull, monkeypatch, tmp_path):
    """The anchor. A handler that returned an error for everything would satisfy
    every test above and make the command permanently broken."""
    good = tmp_path / "service-host.json"
    good.write_text(json.dumps({
        "mirror_dir": "datastore/operations/service-mirror",
        "vm_engine_root": "/srv/engine",
        "state_dirs": [["fireside", "engine", "fireside-state"]],
    }), encoding="utf-8")
    monkeypatch.setattr(pull, "resolve_config_with_example", lambda *a, **k: good)
    for key in ("SERVICE_VM_ENGINE_ROOT", "SERVICE_VM_DATA_ROOT"):
        monkeypatch.delenv(key, raising=False)
        os.environ.pop(key, None)

    config, error = pull.service_config()

    assert error is None
    assert config["mirror_dir"] == "datastore/operations/service-mirror"
    assert pull.state_dirs() == [("fireside", "/srv/engine/fireside-state")]
