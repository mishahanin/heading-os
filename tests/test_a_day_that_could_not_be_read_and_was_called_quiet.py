"""Shard 08-p2: an inbox report whose failures all looked like good news.

`ssh_read` promised three answers -- text, `""` for an absent file, None for a
transport failure -- and delivered two. Every non-255 non-zero exit became
`""`, `fetch_jsonl_for_date` turned that into `[]`, and the report printed a
quiet inbox and exited 0 for a day whose log existed and could not be read. The
docstring said the three were "different answers and the caller needs them
apart"; the code had already collapsed two of them.

`_yaml_domain_set` built a domain set by splitting each YAML pattern at its `@`
and keeping the right half, so `alice@example.com` made EVERY sender at
example.com count as YAML-covered, while `_pattern_matches_domain` -- the real
matcher, used elsewhere in the same file -- said it did not. Two answers for one
config file.

`health_check` referenced `state_dir` inside the except clause that catches
`get_state_dir()` raising, so the failure path raised UnboundLocalError while
formatting its own FAIL message.

`cost.py` resolved its state file from the ENGINE tree while every other daemon
module resolved the DATA overlay, so the spend ledger and the daemon's logs
lived in different repositories. Fixing that surfaced a second defect one layer
down: `get_state_dir()` cached without keying on INBOX_PULSE_STATE_DIR, so the
documented "test/dev override" stopped overriding after any first call.

`overrides.reload` logged a full parse warning on every 30-second poll while the
file stayed broken, next to a docstring explaining why the missing-file warning
is throttled.

Two user-facing sentences were false: a hint to "run with --days 7" for
averages that are already computed at --days 1, and "The counts above EXCLUDE
those days" said about days that were never in the window.

Tests: this file.
"""
from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "inbox_pulse_report_08p2", ROOT / "scripts" / "inbox-pulse-report.py")
rpt = importlib.util.module_from_spec(_spec)
sys.modules["inbox_pulse_report_08p2"] = rpt
_spec.loader.exec_module(rpt)

from scripts.inbox_pulse import cost, overrides, paths  # noqa: E402


class _Result:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


# ==========================================================================
# 1 - the day that could not be read and was called quiet
# ==========================================================================

def test_an_unreadable_file_is_not_an_empty_day(monkeypatch, capsys):
    """Permission denied, disk error, broken remote shell: all exit non-zero."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Result(1, err="cat: Permission denied"))
    assert rpt.fetch_jsonl_for_date(date(2026, 8, 23)) is None, \
        "a remote-side failure was reported as a genuinely empty day"


def test_an_unreadable_file_says_why(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Result(1, err="cat: Permission denied"))
    rpt.ssh_read("/state/log-2026-08-23.jsonl")
    err = capsys.readouterr().err
    assert "Permission denied" in err, "the remote's own reason was swallowed"
    assert "log-2026-08-23.jsonl" in err, "the failing path was not named"


def test_a_confirmed_absent_file_is_an_empty_day(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Result(rpt.REMOTE_FILE_ABSENT))
    assert rpt.fetch_jsonl_for_date(date(2026, 8, 23)) == []


def test_absence_is_proved_by_the_remote_not_inferred_here(monkeypatch):
    """The probe must actually ask whether the file exists."""
    seen = {}

    def _capture(argv, **kwargs):
        seen["argv"] = argv
        return _Result(0, "")

    monkeypatch.setattr(subprocess, "run", _capture)
    rpt.ssh_read("/state/log.jsonl")
    remote = seen["argv"][-1]
    assert "test -f" in remote, "nothing on the remote side checks for absence"
    assert str(rpt.REMOTE_FILE_ABSENT) in remote, \
        "the absence sentinel never reaches the remote shell"


def test_the_remote_command_is_a_single_argument(monkeypatch):
    """ssh joins its remaining argv with spaces; quoting must survive that."""
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **k: (seen.update(argv=argv), _Result(0, ""))[1])
    rpt.ssh_read("/state/a b/log.jsonl")
    remote = seen["argv"][-1]
    assert seen["argv"].index(rpt.VM_HOST) == len(seen["argv"]) - 2, \
        "the remote command was split across several ssh arguments"
    assert "'/state/a b/log.jsonl'" in remote, "a path with a space was not quoted"


def test_a_transport_failure_is_still_distinct(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result(255))
    assert rpt.fetch_jsonl_for_date(date(2026, 8, 23)) is None


def test_a_good_day_still_parses(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Result(0, '{"a": 1}\n\n{"b": 2}\n'))
    assert rpt.fetch_jsonl_for_date(date(2026, 8, 23)) == [{"a": 1}, {"b": 2}]


# ==========================================================================
# 2 - one YAML file, two answers
# ==========================================================================

def _overrides(critical=(), important=(), normal=()):
    return {"always_critical": set(critical),
            "always_important": set(important),
            "always_normal": set(normal)}


def test_an_exact_address_does_not_make_its_whole_domain_known():
    yaml_overrides = _overrides(important=["alice@example.com"])
    assert rpt._domain_in_yaml("example.com", yaml_overrides) is False, \
        "a rule naming one person was read as covering the domain"


def test_the_two_readers_agree_about_an_exact_address():
    """The set-builder and the matcher must not disagree about one file."""
    pats = {"alice@example.com"}
    assert rpt._domain_matches_any("example.com", pats) == \
        rpt._pattern_matches_domain("alice@example.com", "example.com")


def test_a_wildcard_domain_rule_does_cover_the_domain():
    assert rpt._domain_matches_any("noreply.com", {"*@noreply.com"}) is True


def test_a_wildcard_domain_rule_covers_its_subdomains():
    assert rpt._domain_matches_any("mail.noreply.com", {"*@noreply.com"}) is True


def test_a_left_side_wildcard_never_synthesises_a_domain():
    """`newsletter@*` must not contribute the literal domain `*`."""
    assert rpt._domain_matches_any("*", {"newsletter@*"}) is False
    assert rpt._domain_matches_any("mailer.foo.com", {"newsletter@*"}) is False


def test_a_bare_domain_pattern_matches():
    assert rpt._domain_matches_any("example.com", {"example.com"}) is True


def test_an_exact_address_still_covers_nothing_at_a_sibling_domain():
    assert rpt._domain_matches_any("other.com", {"alice@example.com"}) is False


def test_the_aggregate_does_not_flag_a_neighbour_of_an_overridden_address():
    """End to end: bob@example.com is not covered by a rule about alice."""
    entries = [{"tier_guess": rpt.TIER_LOW, "sender_domain": "example.com",
                "ts": "2026-08-25T09:00:00"}]
    data = rpt.aggregate(
        entries=entries, today=date(2026, 8, 25), days=1,
        all_entries_by_date={date(2026, 8, 25): entries},
        known_crm_domains=set(),
        yaml_overrides=_overrides(important=["alice@example.com"]),
    )
    assert data["known_good_low"] == {}, \
        "a LOW item was called a false negative on the strength of another sender's rule"
    assert [d for d, _ in data["top_unknown"]] == ["example.com"], \
        "the domain was suppressed from the unknown list it belongs in"


# ==========================================================================
# 3 - the health check that crashed while reporting a failure
# ==========================================================================

def test_the_state_dir_failure_prints_fail_rather_than_raising(monkeypatch, capsys):
    from scripts.inbox_pulse import daemon

    for var in ("EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"):
        monkeypatch.setenv(var, "x")

    def _boom():
        raise PermissionError("parent is read-only")

    monkeypatch.setattr(daemon, "get_state_dir", _boom)
    assert daemon.health_check() == 1
    err = capsys.readouterr().err
    assert "FAIL" in err and "state dir not writable" in err
    assert "parent is read-only" in err, "the underlying cause was lost"


# ==========================================================================
# 4 - the ledger that lived in the other repository
# ==========================================================================

def test_the_cost_file_sits_with_the_rest_of_the_daemon_state(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    assert cost._state_path().parent == paths.get_state_dir(), \
        "the spend ledger resolved a different directory from the daemon's state"


def test_the_cost_file_is_not_written_into_the_engine_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("INBOX_PULSE_STATE_DIR", raising=False)
    assert ROOT not in cost._state_path().parents, \
        "runtime state was written into the engine repository"


def test_a_changed_override_is_honoured(monkeypatch, tmp_path):
    """The cache must key on the env var it claims to be overridable by."""
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(first))
    assert paths.get_state_dir() == first
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(second))
    assert paths.get_state_dir() == second, \
        "a cached answer outlived the override it was resolved from"


def test_the_cache_still_avoids_re_resolving_an_unchanged_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    assert paths.get_state_dir() is paths.get_state_dir()


def test_a_recorded_call_lands_in_the_override_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost, "_today_str", lambda: "2026-08-25")
    cost.record_call("claude-haiku-4-5-20251001", 1000, 100)
    assert (tmp_path / "cost-tracker.json").exists()


# ==========================================================================
# 5 - the warning that repeated every thirty seconds
# ==========================================================================

def _engine(tmp_path, text):
    yaml_path = tmp_path / "email-triage-rules.yaml"
    yaml_path.write_text(text, encoding="utf-8")
    return overrides.RulesEngine(yaml_path=yaml_path), yaml_path


def _parse_warnings(records):
    return [r for r in records
            if r.levelno == logging.WARNING
            and ("Failed to parse" in r.getMessage()
                 or "non-dict" in r.getMessage())]


def test_a_broken_file_warns_once_not_every_cycle(tmp_path, caplog):
    engine, _ = _engine(tmp_path, "always_normal:\n  - '*@ok.com'\n")
    (tmp_path / "email-triage-rules.yaml").write_text(
        "always_normal: [unclosed\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            engine.reload()
    assert len(_parse_warnings(caplog.records)) == 1, \
        "a persistently broken file logged on every poll cycle"


def test_saving_the_file_still_broken_warns_again(tmp_path, caplog):
    engine, yaml_path = _engine(tmp_path, "always_normal: [unclosed\n")
    with caplog.at_level(logging.WARNING):
        engine.reload()
        import os
        os.utime(yaml_path, (1_700_000_000, 1_700_000_000))
        engine.reload()
    assert len(_parse_warnings(caplog.records)) == 2, \
        "an operator who saved a fix and did not fix it heard nothing"


def test_a_second_breakage_at_the_same_mtime_still_warns(tmp_path, caplog):
    """The mtime key alone is not enough on a coarse-granularity filesystem.

    Clearing the throttle on a GOOD load is what covers three writes that share
    one mtime -- break, fix, break. With nanosecond timestamps the mtimes
    differ and the clear looks redundant, which is why the mutation that
    removed it survived. os.utime pins them equal, which is the case the clear
    exists for.
    """
    import os
    engine, yaml_path = _engine(tmp_path, "always_normal:\n  - '*@ok.com'\n")
    stamp = (1_700_000_000, 1_700_000_000)

    def _write(text):
        yaml_path.write_text(text, encoding="utf-8")
        os.utime(yaml_path, stamp)
        engine.reload()

    with caplog.at_level(logging.WARNING):
        _write("always_normal: [unclosed\n")
        _write("always_normal:\n  - '*@ok.com'\n")
        _write("always_normal: [broken again\n")
    assert len(_parse_warnings(caplog.records)) == 2, \
        "a good load did not clear the throttle, so the re-breakage was silent"


def test_a_repaired_file_rearms_the_warning(tmp_path, caplog):
    engine, yaml_path = _engine(tmp_path, "always_normal: [unclosed\n")
    with caplog.at_level(logging.WARNING):
        engine.reload()
        yaml_path.write_text("always_normal:\n  - '*@ok.com'\n", encoding="utf-8")
        engine.reload()
        yaml_path.write_text("always_normal: [unclosed again\n", encoding="utf-8")
        engine.reload()
    assert len(_parse_warnings(caplog.records)) == 2, \
        "a good load did not clear the throttle"


def test_a_broken_file_never_replaces_good_config(tmp_path):
    engine, yaml_path = _engine(tmp_path, "always_normal:\n  - '*@ok.com'\n")
    before = dict(engine._config)
    yaml_path.write_text("always_normal: [unclosed\n", encoding="utf-8")
    assert engine.reload() is False
    assert engine._config == before, "a parse failure discarded the working rules"


# ==========================================================================
# 6 - two sentences that were not true
# ==========================================================================

def _report_run(monkeypatch, tmp_path, unreachable_days, empty_days=()):
    """Drive main() with a fake remote, returning its exit code."""
    today = rpt.datetime.now(rpt.get_default_tz()).date()

    def _fake_fetch(target):
        age = (today - target).days
        if age in unreachable_days:
            return None
        if age in empty_days:
            return []
        return [{"tier_guess": rpt.TIER_LOW, "sender_domain": "x.test",
                 "ts": "2026-08-25T09:00:00"}]

    monkeypatch.setattr(rpt, "fetch_jsonl_for_date", _fake_fetch)
    monkeypatch.setattr(rpt, "fetch_state_json", dict)
    monkeypatch.setattr(rpt, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["inbox-pulse-report.py", "--days", "1", "--no-open"])
    return rpt.main()


def test_a_day_outside_the_window_does_not_fail_the_run(monkeypatch, tmp_path, capsys):
    """Day 5 is fetched only for the average. It was never in the counts."""
    code = _report_run(monkeypatch, tmp_path, unreachable_days={5})
    err = capsys.readouterr().err
    assert code == 0, "a scheduler was told the report failed over a day it never asked for"
    assert "The counts above EXCLUDE those days" not in err, \
        "the report claimed to have excluded a day that was never included"
    assert "outside the" in err and "--days window" in err, \
        "the degraded 7-day average went unmentioned"


def test_a_day_inside_the_window_still_fails_the_run(monkeypatch, tmp_path, capsys):
    code = _report_run(monkeypatch, tmp_path, unreachable_days={0})
    err = capsys.readouterr().err
    assert code == 1, "a missing day inside the window read as success"
    assert "The counts above EXCLUDE those days" in err


def test_a_fully_readable_window_is_quiet(monkeypatch, tmp_path, capsys):
    code = _report_run(monkeypatch, tmp_path, unreachable_days=set())
    err = capsys.readouterr().err
    assert code == 0
    assert "could not be read" not in err


def _report_text(tmp_path):
    reports = list((tmp_path / "operations" / "inbox-pulse").glob("*.md"))
    assert reports, "the report file was never written"
    return "\n".join(f.read_text(encoding="utf-8") for f in reports)


def test_the_averages_note_renders_when_there_is_no_average(monkeypatch, tmp_path):
    """Every past day empty -> avg_counts is None -> the note is reached.

    The first version of this test ran with a full window, so `has_7day` was
    true, the note never rendered, and asserting on its absence could not fail.
    """
    _report_run(monkeypatch, tmp_path, unreachable_days=set(),
                empty_days={1, 2, 3, 4, 5, 6, 7})
    assert "7-day averages not available" in _report_text(tmp_path)


def test_the_averages_note_does_not_send_the_operator_after_a_flag(monkeypatch, tmp_path):
    """The hint must not prescribe --days 7, which changes nothing."""
    _report_run(monkeypatch, tmp_path, unreachable_days=set(),
                empty_days={1, 2, 3, 4, 5, 6, 7})
    text = _report_text(tmp_path)
    assert "7-day averages not available" in text, "the note under test did not render"
    assert "(run with --days 7 or more to enable)" not in text, \
        "the report still tells the operator to pass a flag that changes nothing"
    assert "does not change this" in text, \
        "the note no longer says the flag is not the remedy"
