"""Shard scripts-15-p1: four gates that reported a pass they never earned, and
a watchdog one typo could silence.

* `load_cadence` guarded only the `daemon` level of
  `daemon.watchdog.cadence`, and that chain sat OUTSIDE the try that wraps the
  config read. `watchdog: off` - one YAML typo turning a mapping into a scalar -
  called `.get` on a str, raised AttributeError out of `load_cadence` and
  `check_once`, and landed in the bridge daemon's per-tick handler, which logs
  and carries on. The 2-minute tick then did nothing forever while the daemon
  reported itself healthy. The `_seconds` docstring in the same function says
  this must not happen; the fix had been applied to malformed VALUES only.

* `_read_beat` returned whatever `json.loads` produced, so a beat file holding a
  JSON array, string or number reached `.get` in `_age_seconds`. That
  AttributeError took the WHOLE fleet's classification down every tick, not just
  that daemon's. `classify`'s own docstring says a file with no parseable
  timestamp is `missing`.

* `verify-skills-lock` counted issues but never counted the trees it hashed, so
  a lock with an absent, empty, or all-`vendored: false` `skills` map printed
  "Vendored skills verified." and exited 0 in CI and at pre-push. Deleting the
  one real entry disarmed the gate while it kept reporting a pass. A non-string
  `skillPath` was also the one lock shape that reached `Path()` and raised
  TypeError instead of getting the clean FAIL line its siblings get.

* `cliproxyapi_update._current_version` ran the binary ABOVE `main`'s try block,
  so a missing binary died with a traceback and exit 1 - the code the module
  docstring reserves for "the swap or the health gate failed and a rollback was
  attempted". Nothing had been touched.

* `visual-discipline-check baseline check` computed `above` and consulted it
  only on the failure path, so a non-strict run with warning-severity findings
  above the baseline printed "No findings above the baseline." over a non-zero
  count, and printed none of the findings.

* `validate-crm-schema --dir` was read only in the all-records branch, so
  `--dir staged --contact x` validated the LIVE CRM tree. Its `--quiet` help and
  the module docstring both described the opposite of what the flag does.

Nothing here reads the live CRM or the live heartbeat directory.

Run: python3 -m pytest tests/test_a_watchdog_silenced_by_one_yaml_typo.py
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import watchdog_core as wc  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vsl():
    return _load("scripts/verify-skills-lock.py", "vsl_under_test")


@pytest.fixture(scope="module")
def crm():
    return _load("scripts/validate-crm-schema.py", "crm_under_test")


@pytest.fixture(scope="module")
def vdc():
    return _load("scripts/visual-discipline-check.py", "vdc_under_test")


@pytest.fixture(scope="module")
def cpu():
    return _load("scripts/updaters/cliproxyapi_update.py", "cpu_under_test")


# ============================================================
# The watchdog one typo could silence
# ============================================================

@pytest.fixture
def cadence_cfg(monkeypatch):
    """Feed `load_cadence` an arbitrary config without touching a real one."""
    from scripts.bridge_daemon import config as bdc

    def _set(cfg):
        monkeypatch.setattr(bdc, "load_config", lambda root: cfg)
    monkeypatch.setattr(wc, "load_expected", lambda root: ["bridge", "sentinel"])
    return _set


@pytest.mark.parametrize("cfg", [
    {"daemon": "off"},
    {"daemon": {"watchdog": False}},
    {"daemon": {"watchdog": "off"}},
    {"daemon": {"watchdog": 7}},
    {"daemon": {"watchdog": ["a"]}},
    {"daemon": {"watchdog": {"cadence": "off"}}},
    {"daemon": {"watchdog": {"cadence": 3}}},
    {"daemon": {"watchdog": {"cadence": ["a"]}}},
])
def test_a_malformed_config_node_does_not_silence_the_watchdog(cadence_cfg, cfg):
    """It raised out of check_once into a handler that logs and carries on, so
    the tick did nothing forever while the daemon reported itself healthy."""
    cadence_cfg(cfg)

    out = wc.load_cadence(Path("/nonexistent"))

    assert out == {"bridge": (wc.DEFAULT_EXPECTED_S, wc.DEFAULT_GRACE_S),
                   "sentinel": (wc.DEFAULT_EXPECTED_S, wc.DEFAULT_GRACE_S)}


def test_a_malformed_config_node_is_said_out_loud(cadence_cfg, caplog):
    """Silently falling back to defaults is how a long-cadence daemon gets
    classified silent with no line anywhere explaining why."""
    cadence_cfg({"daemon": {"watchdog": "off"}})

    with caplog.at_level(logging.WARNING, logger=wc.logger.name):
        wc.load_cadence(Path("/nonexistent"))

    assert "malformed" in caplog.text


def test_a_malformed_cadence_leaf_is_said_out_loud_too(cadence_cfg, caplog):
    """The deepest level takes a different branch: every key resolves, and the
    VALUE at the end is the scalar. That branch has its own warning, and without
    it a `cadence: off` typo reverts every daemon to the defaults in silence."""
    cadence_cfg({"daemon": {"watchdog": {"cadence": "off"}}})

    with caplog.at_level(logging.WARNING, logger=wc.logger.name):
        wc.load_cadence(Path("/nonexistent"))

    assert "daemon.watchdog.cadence is a str" in caplog.text


def test_a_good_config_is_still_read(cadence_cfg):
    """The guard must not swallow a correct cadence."""
    cadence_cfg({"daemon": {"watchdog": {"cadence": {
        "bridge": {"expected": 300, "grace": 600}}}}})

    assert wc.load_cadence(Path("/nonexistent"))["bridge"] == (300, 600)


def test_an_absent_config_still_yields_defaults(cadence_cfg):
    cadence_cfg({})

    assert wc.load_cadence(Path("/nonexistent"))["bridge"] == (
        wc.DEFAULT_EXPECTED_S, wc.DEFAULT_GRACE_S)


def test_a_malformed_value_is_still_handled(cadence_cfg):
    """The case that was already fixed, held down."""
    cadence_cfg({"daemon": {"watchdog": {"cadence": {
        "bridge": {"expected": "soon"}}}}})

    assert wc.load_cadence(Path("/nonexistent"))["bridge"] == (
        wc.DEFAULT_EXPECTED_S, wc.DEFAULT_GRACE_S)


# ============================================================
# The beat file that took the whole fleet down
# ============================================================

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("record", [[1, 2, 3], "a string", 42, 3.5, True])
def test_a_non_object_beat_is_missing_not_a_crash(record):
    """`classify`'s docstring already promised `missing` here."""
    assert wc.classify(record, 60, NOW) == "missing"


@pytest.mark.parametrize("record", [None, [], {}, {"last_heartbeat": None}])
def test_the_cases_that_already_worked_still_work(record):
    assert wc.classify(record, 60, NOW) == "missing"


def test_a_fresh_beat_is_still_ok():
    fresh = {"last_heartbeat": (NOW - timedelta(seconds=5)).isoformat()}

    assert wc.classify(fresh, 60, NOW) == "ok"


def test_an_old_beat_is_still_silent():
    old = {"last_heartbeat": (NOW - timedelta(seconds=600)).isoformat()}

    assert wc.classify(old, 60, NOW) == "silent"


@pytest.mark.parametrize("body", ["[1, 2, 3]", '"a string"', "42"])
def test_a_non_object_beat_file_reads_as_no_beat(tmp_path, body, caplog):
    beats = tmp_path / wc.HEARTBEATS_DIR
    beats.mkdir(parents=True)
    (beats / "sentinel.json").write_text(body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=wc.logger.name):
        assert wc._read_beat(tmp_path, "sentinel") is None

    assert "not an object" in caplog.text


def test_a_well_formed_beat_file_still_reads(tmp_path):
    beats = tmp_path / wc.HEARTBEATS_DIR
    beats.mkdir(parents=True)
    (beats / "sentinel.json").write_text(
        json.dumps({"last_heartbeat": NOW.isoformat()}), encoding="utf-8")

    assert wc._read_beat(tmp_path, "sentinel") == {"last_heartbeat": NOW.isoformat()}


# ============================================================
# The integrity gate that hashed nothing
# ============================================================

def _lock(vsl, tmp_path, payload) -> Path:
    path = tmp_path / "skills-lock.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("skills", [
    {},
    None,
    {"x": {"vendored": False, "note": "plugin-managed"}},
    {"x": {"vendored": False}, "y": {"vendored": False}},
])
def test_a_lock_that_hashes_nothing_is_not_a_pass(vsl, tmp_path, monkeypatch,
                                                   skills, capsys):
    """Deleting the one real entry disarmed the gate and kept the green line."""
    payload = {"recipe": vsl.RECIPE}
    if skills is not None:
        payload["skills"] = skills
    monkeypatch.setattr(vsl, "LOCK_PATH", _lock(vsl, tmp_path, payload))

    rc = vsl.verify(relock=False, quiet=True)

    assert rc == 1
    assert "No vendored skill was hashed" in capsys.readouterr().out


def test_the_shipped_lock_still_verifies(vsl, capsys):
    """The regression that matters: the real lock has one hashed entry."""
    assert vsl.verify(relock=False, quiet=False) == 0
    assert "1 tree(s) hashed" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [42, ["a"], {"a": 1}, 3.5, None, ""])
def test_a_non_string_skill_path_is_refused_not_raised(vsl, bad):
    """Every other wrong shape gets a clean FAIL line; this one raised."""
    assert vsl._vendored_dir(ROOT, {"skillPath": bad}) is None


def test_a_real_skill_path_still_resolves(vsl):
    got = vsl._vendored_dir(ROOT, {"skillPath": ".claude/skills/ast-grep/SKILL.md"})

    assert got is not None
    assert got.name == "ast-grep"


# ============================================================
# The updater that died above its own handler
# ============================================================

def test_a_missing_binary_does_not_raise(cpu, monkeypatch, capsys):
    """It sat above `main`'s try, so it died with a traceback and exit 1 - the
    code reserved for a failed swap with a rollback attempted."""
    monkeypatch.setattr(cpu, "BIN", Path("/nonexistent/cli-proxy-api"))

    assert cpu._current_version() == ""
    assert "could not run" in capsys.readouterr().out


def test_a_working_binary_still_reports_its_version(cpu, monkeypatch, tmp_path):
    """The guard must not swallow the ordinary path."""
    fake = tmp_path / "cli-proxy-api"
    fake.write_text("#!/bin/sh\necho 'Version: 1.2.3'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(cpu, "BIN", fake)

    assert cpu._current_version() == "1.2.3"


def test_a_binary_that_prints_nothing_useful_yields_empty(cpu, monkeypatch,
                                                           tmp_path):
    fake = tmp_path / "cli-proxy-api"
    fake.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(cpu, "BIN", fake)

    assert cpu._current_version() == ""


# ============================================================
# The baseline check that said none over some
# ============================================================

def _results(count, severity="warning"):
    findings = [{"severity": severity, "rule": "r", "message": "m", "file": "f"}
                for _ in range(count)]
    return [{"source": "f", "findings": findings,
             "summary": {"total_findings": count, "errors": 0,
                         "warnings": count}}]


def test_findings_above_the_baseline_are_reported(vdc, monkeypatch, tmp_path,
                                                   capsys):
    """It printed "No findings above the baseline." over a non-zero count."""
    import argparse
    monkeypatch.setattr(vdc, "_run_audit",
                        lambda *a, **k: (_results(3), False, None))
    monkeypatch.setattr(vdc, "print_report", lambda res: None)

    rc = vdc._cmd_baseline(argparse.Namespace(
        path=str(tmp_path), action="check", strict=False, profile=None,
        include_internal=False))

    out = capsys.readouterr().out
    assert rc == 0
    assert "3 finding(s) above the baseline" in out
    assert "No findings above the baseline" not in out


def test_a_genuinely_clean_check_still_says_so(vdc, monkeypatch, tmp_path,
                                                capsys):
    import argparse
    monkeypatch.setattr(vdc, "_run_audit",
                        lambda *a, **k: (_results(0), False, None))

    rc = vdc._cmd_baseline(argparse.Namespace(
        path=str(tmp_path), action="check", strict=False, profile=None,
        include_internal=False))

    assert rc == 0
    assert "No findings above the baseline" in capsys.readouterr().out


def test_a_failing_check_still_fails(vdc, monkeypatch, tmp_path, capsys):
    import argparse
    monkeypatch.setattr(vdc, "_run_audit",
                        lambda *a, **k: (_results(2, "error"), True, None))
    monkeypatch.setattr(vdc, "print_report", lambda res: None)

    rc = vdc._cmd_baseline(argparse.Namespace(
        path=str(tmp_path), action="check", strict=False, profile=None,
        include_internal=False))

    assert rc == 1
    assert "2 finding(s) above the baseline" in capsys.readouterr().out


def test_a_degraded_deep_engine_still_refuses(vdc, monkeypatch, tmp_path):
    """The guard already there must not be reordered away."""
    import argparse
    monkeypatch.setattr(vdc, "_run_audit",
                        lambda *a, **k: (_results(0), False, "cli missing"))

    rc = vdc._cmd_baseline(argparse.Namespace(
        path=str(tmp_path), action="check", strict=False, profile=None,
        include_internal=False))

    assert rc == 2


# ============================================================
# The staged corpus that was never opened
# ============================================================

CONTACT_MD = """---
name: Example Person
slug: example-person
---
body
"""


def test_dir_is_honoured_in_contact_mode(crm, monkeypatch, tmp_path, capsys):
    """`--dir staged --contact x` validated the LIVE tree."""
    staged = tmp_path / "staged" / "contacts"
    staged.mkdir(parents=True)
    (staged / "example-person.md").write_text(CONTACT_MD, encoding="utf-8")
    monkeypatch.setattr(crm, "CONTACTS_DIR", tmp_path / "live" / "contacts")
    monkeypatch.setattr(sys, "argv", [
        "validate-crm-schema.py", "--dir", str(tmp_path / "staged"),
        "--contact", "example-person"])

    rc = crm.main()

    assert rc in (0, 1), "it used to exit 2 pointing at the live tree"
    assert "not found" not in capsys.readouterr().err


def test_a_missing_staged_record_names_the_staged_path(crm, monkeypatch,
                                                        tmp_path, capsys):
    staged = tmp_path / "staged" / "contacts"
    staged.mkdir(parents=True)
    live = tmp_path / "live" / "contacts"
    live.mkdir(parents=True)
    (live / "example-person.md").write_text(CONTACT_MD, encoding="utf-8")
    monkeypatch.setattr(crm, "CONTACTS_DIR", live)
    monkeypatch.setattr(sys, "argv", [
        "validate-crm-schema.py", "--dir", str(tmp_path / "staged"),
        "--contact", "example-person"])

    rc = crm.main()

    err = capsys.readouterr().err
    assert rc == 2
    assert "staged" in err, "it validated the live record and reported a pass"


def test_contact_mode_without_dir_still_uses_the_live_tree(crm, monkeypatch,
                                                            tmp_path, capsys):
    live = tmp_path / "live" / "contacts"
    live.mkdir(parents=True)
    (live / "example-person.md").write_text(CONTACT_MD, encoding="utf-8")
    monkeypatch.setattr(crm, "CONTACTS_DIR", live)
    monkeypatch.setattr(sys, "argv", [
        "validate-crm-schema.py", "--contact", "example-person"])

    rc = crm.main()

    assert rc in (0, 1)
    assert "not found" not in capsys.readouterr().err


def test_quiet_drops_the_summary_and_keeps_the_failures(crm, monkeypatch,
                                                         tmp_path, capsys):
    """The help said "Emit only the failure summary". The summary is the ONE
    line `--quiet` suppresses, and every FAIL line still prints."""
    contacts = tmp_path / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "example-person.md").write_text(CONTACT_MD, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "validate-crm-schema.py", "--dir", str(tmp_path), "--quiet"])

    crm.main()

    out = capsys.readouterr().out
    assert "FAIL" in out, "the failure detail is what --quiet keeps"
    assert "records fail schema" not in out, "the summary is what it drops"


def test_the_quiet_help_describes_what_quiet_does(crm):
    src = (ROOT / "scripts" / "validate-crm-schema.py").read_text(encoding="utf-8")

    assert "Emit only the failure summary" not in src
    assert "Every FAIL line and every schema error still prints" in src


def test_the_docstring_stops_promising_exit_code_only(crm):
    doc = crm.__doc__

    assert "exit code only" not in doc
    assert "failures only, no summary" in doc
