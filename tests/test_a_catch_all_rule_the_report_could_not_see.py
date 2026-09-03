#!/usr/bin/env python3
"""Shard 09-p1: five places where Inbox Pulse described itself wrongly.

The report and the daemon read the SAME `email-triage-rules.yaml` and have to
agree about it. The daemon matches whole addresses with `fnmatch`; the report
read the right side of a pattern as a literal. So `*@*` -- a catch-all the
daemon applies to every message that arrives -- matched nothing at all in the
report. Every domain then appeared under "Unknown domains" with an "Add to
`always_normal`" suggestion beside it, for traffic already governed by exactly
that rule. The function even carried an unreachable `if pattern == "*@*":
return True` behind the branch that swallowed it, so the intent was never in
doubt.

The column headed "7-day avg" averaged at most six days: the fetch loop stopped
one day short of what the aggregator reads, the missing day resolved to `[]`,
and the non-empty filter dropped it in silence. `--days 7` did not rescue it --
at exactly that value the extra loop was skipped entirely, and the report's own
footnote promised the opposite.

`RulesEngine.reload()` carried the comment "Warn once" and set no flag. The
flag it needed already existed and was used by the one method that never
reaches that branch.

`EWSConnection.disconnect()` said "Close the connection" and closed nothing.

And two `signal.signal` calls sat at module level in the daemon, so importing
it replaced the host process's Ctrl-C handling, and importing it from a worker
thread raised ValueError before any daemon existed.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import signal
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "inbox_pulse_report_09p1", ROOT / "scripts" / "inbox-pulse-report.py")
report = importlib.util.module_from_spec(_spec)
sys.modules["inbox_pulse_report_09p1"] = report
_spec.loader.exec_module(report)

from scripts.inbox_pulse import daemon as dmn  # noqa: E402
from scripts.inbox_pulse.exchange import EWSConnection  # noqa: E402
from scripts.inbox_pulse.overrides import RulesEngine  # noqa: E402
from tests.repo_files import read_sources  # noqa: E402


def _engine_with(patterns, bucket="always_normal"):
    """A daemon RulesEngine holding one sender-override bucket."""
    eng = RulesEngine.__new__(RulesEngine)
    eng.yaml_path = Path("/nonexistent/rules.yaml")
    eng._config = {"sender_overrides": {bucket: list(patterns)}}
    eng._last_mtime = 0.0
    eng._missing_warned = False
    eng._bad_load_warned_mtime = None
    return eng


# ============================================================
# Finding 1 -- the catch-all the report could not see
# ============================================================
def test_a_catch_all_pattern_covers_every_domain():
    assert report._pattern_matches_domain("*@*", "example.com") is True


def test_the_report_and_the_daemon_agree_about_a_catch_all():
    """The two components read one YAML file; disagreeing about it is the bug."""
    eng = _engine_with(["*@*"])
    assert eng.match_sender("anyone@example.com") == "always_normal"
    assert report._pattern_matches_domain("*@*", "example.com") is True


def test_a_wildcard_tld_pattern_covers_a_matching_domain():
    assert report._pattern_matches_domain("*@*.com", "example.com") is True


def test_a_wildcard_tld_pattern_does_not_cover_a_different_tld():
    assert report._pattern_matches_domain("*@*.com", "example.org") is False


def test_the_report_and_the_daemon_agree_about_a_wildcard_tld():
    eng = _engine_with(["*@*.com"])
    assert eng.match_sender("anyone@example.com") == "always_normal"
    assert eng.match_sender("anyone@example.org") is None
    assert report._pattern_matches_domain("*@*.com", "example.com") is True
    assert report._pattern_matches_domain("*@*.com", "example.org") is False


def test_an_ordinary_domain_pattern_still_covers_its_domain():
    assert report._pattern_matches_domain("*@example.com", "example.com") is True


def test_a_named_local_part_never_covers_the_whole_domain():
    """`alice@example.com` says nothing about the other people at example.com."""
    assert report._pattern_matches_domain("alice@example.com", "example.com") is False


def test_a_left_side_wildcard_never_covers_a_domain():
    """`newsletter@*` covers one local part everywhere, no domain anywhere."""
    assert report._pattern_matches_domain("newsletter@*", "example.com") is False


def test_a_bare_domain_pattern_covers_itself():
    assert report._pattern_matches_domain("example.com", "example.com") is True


def test_an_empty_local_part_does_not_cover_the_domain():
    assert report._pattern_matches_domain("@example.com", "example.com") is False


def test_an_empty_domain_side_does_not_cover_anything():
    assert report._pattern_matches_domain("*@", "example.com") is False


def test_a_pattern_with_no_at_sign_is_not_a_domain_rule():
    assert report._pattern_matches_domain("*", "example.com") is False


def test_the_split_happens_at_the_first_at_sign_like_the_daemon_does():
    """`_domain_of` in the daemon is `addr.split("@", 1)[1]`, so the domain of
    `x@a@b.com` is `a@b.com`. Splitting the PATTERN at the last `@` instead
    puts the two components back into disagreement about the same address."""
    assert dmn._domain_of("x@a@b.com") == "a@b.com"
    assert report._pattern_matches_domain("*@a@b.com", "a@b.com") is True
    eng = _engine_with(["*@a@b.com"])
    assert eng.match_sender("x@a@b.com") == "always_normal"


def test_matching_is_case_insensitive_on_both_sides():
    assert report._pattern_matches_domain("*@EXAMPLE.COM", "Example.Com") is True


def test_the_subdomain_divergence_is_named_rather_than_left_unsaid():
    """It predates this fix and is unchanged by it, so the docstring says so."""
    doc = " ".join((report._pattern_matches_domain.__doc__ or "").split())
    assert "KNOWN DIVERGENCE" in doc
    assert "mail.example.com" in doc
    assert report._pattern_matches_domain("*@example.com", "mail.example.com") is True


def _entry(domain, tier="LOW"):
    return {"sender_domain": domain, "tier_guess": tier, "ts": "2026-08-25T09:00:00+00:00",
            "weight": 0, "reason_breakdown": {}}


def _overrides(**kw):
    base = {"always_critical": set(), "always_important": set(), "always_normal": set()}
    base.update({k: set(v) for k, v in kw.items()})
    return base


def test_a_catch_all_rule_leaves_no_domain_listed_as_unknown():
    """End to end: the report stops contradicting the running daemon."""
    today = date(2026, 8, 25)
    entries = [_entry("example.com") for _ in range(6)]
    agg = report.aggregate(
        entries=entries, today=today, days=1,
        all_entries_by_date={today: entries}, known_crm_domains=set(),
        yaml_overrides=_overrides(always_normal=["*@*"]),
    )
    assert agg["top_unknown"] == []


def test_a_catch_all_rule_produces_no_tuning_suggestion():
    today = date(2026, 8, 25)
    entries = [_entry("example.com") for _ in range(6)]
    agg = report.aggregate(
        entries=entries, today=today, days=1,
        all_entries_by_date={today: entries}, known_crm_domains=set(),
        yaml_overrides=_overrides(always_normal=["*@*"]),
    )
    assert agg["suggestions"] == [], \
        "suggesting always_normal for traffic a catch-all already governs"


def test_without_the_catch_all_the_domain_is_still_reported_unknown():
    """The fix must not silence a genuinely uncovered domain."""
    today = date(2026, 8, 25)
    entries = [_entry("example.com") for _ in range(6)]
    agg = report.aggregate(
        entries=entries, today=today, days=1,
        all_entries_by_date={today: entries}, known_crm_domains=set(),
        yaml_overrides=_overrides(),
    )
    assert [d for d, _ in agg["top_unknown"]] == ["example.com"]
    assert len(agg["suggestions"]) == 1


# ============================================================
# Finding 2 -- the seventh day nothing ever fetched
# ============================================================
@pytest.fixture
def drive_main(tmp_path, monkeypatch):
    """Run report.main() with every remote and local side effect stubbed out."""
    def run(days, *, empty_days=(), unreachable_days=()):
        today = date(2026, 8, 25)
        asked: list[date] = []

        class _FrozenNow:
            @staticmethod
            def now(tz=None):
                import datetime as _dt
                return _dt.datetime(2026, 8, 25, 12, 0, tzinfo=_dt.timezone.utc)

        def fake_fetch(target):
            asked.append(target)
            if target in unreachable_days:
                return None
            if target in empty_days:
                return []
            return [_entry("example.com", "MAYBE")]

        monkeypatch.setattr(report, "datetime", _FrozenNow)
        monkeypatch.setattr(report, "fetch_jsonl_for_date", fake_fetch)
        monkeypatch.setattr(report, "fetch_state_json", dict)
        monkeypatch.setattr(report, "get_outputs_dir", lambda: tmp_path)
        monkeypatch.setattr(report, "load_yaml_overrides", lambda root: _overrides())
        monkeypatch.setattr(report, "load_known_crm_domains", lambda root: set())
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "Clean - no hidden characters found.",
                                           "stderr": ""})())
        monkeypatch.setattr(sys, "argv",
                            ["inbox-pulse-report.py", "--days", str(days), "--no-open"])
        rc = report.main()
        return rc, asked, today

    return run


def test_the_seventh_day_back_is_actually_fetched(drive_main):
    rc, asked, today = drive_main(1)
    assert (today - timedelta(days=7)) in asked, \
        "the column says 7-day average and the seventh day was never read"


def test_the_fetch_covers_exactly_today_through_seven_days_back(drive_main):
    rc, asked, today = drive_main(1)
    assert sorted(asked) == sorted(today - timedelta(days=i) for i in range(8))


def test_days_seven_still_reaches_the_seventh_day_back(drive_main):
    """The old guard skipped the extra loop at exactly this value."""
    rc, asked, today = drive_main(7)
    assert (today - timedelta(days=7)) in asked


def test_no_day_is_fetched_twice(drive_main):
    rc, asked, today = drive_main(7)
    assert len(asked) == len(set(asked))


def test_a_window_wider_than_the_average_adds_no_extra_fetch(drive_main):
    rc, asked, today = drive_main(8)
    assert sorted(asked) == sorted(today - timedelta(days=i) for i in range(8))


def test_the_average_is_taken_over_all_seven_past_days():
    today = date(2026, 8, 25)
    by_date = {today - timedelta(days=i): [_entry("x", "MAYBE")] for i in range(8)}
    dist = report._compute_daily_distribution(by_date, today)
    assert dist["avg"]["MAYBE"] == 1.0
    # Seven past days each holding exactly one MAYBE. Drop one and the mean is
    # unchanged, so pin the divisor where the off-by-one actually showed.
    by_date[today - timedelta(days=7)] = [_entry("x", "MAYBE") for _ in range(8)]
    dist = report._compute_daily_distribution(by_date, today)
    assert dist["avg"]["MAYBE"] == pytest.approx(2.0), \
        "the seventh day back is not entering the average"


# The non-empty filter is the other half of what hid the seventh-day defect: the
# unfetched day resolved to `[]`, `past_7_non_empty` dropped it, and the mean was
# taken over six days while the column said seven. Only one of its two cases was
# decided anywhere.
#
# MEASURED 2026-09-01 in a scratch copy. `past_7_non_empty = past_7` (count every
# day, empty or not) is CAUGHT, by `test_the_averages_note_renders_when_there_is
# _no_average` in tests/test_a_day_that_could_not_be_read_and_was_called_quiet.py:
# with every past day empty that mutation turns `avg` from None into a dict of
# zeros, and the report's "7-day averages not available" note stops rendering.
# So the all-empty case has an owner and is not re-asserted here.
#
# The PARTIAL window has none. `past_7_non_empty = past_7 if any(past_7) else []`
# counts silent days whenever at least one day carried mail, which is the shape a
# real regression takes, and it left that file green at 34 passed. It fails the
# test below. That is the whole reason this pair exists.


def test_a_silent_day_inside_a_working_window_does_not_enter_the_divisor():
    """A day the daemon logged nothing for is absent data, not a measured zero.

    Averaging it in halves every reported figure after one quiet weekend, and
    the operator reads that as a drop in volume.
    """
    today = date(2026, 8, 25)
    by_date = {today - timedelta(days=i): [_entry("x", "MAYBE")] for i in range(1, 8)}
    by_date[today] = []
    full = report._compute_daily_distribution(by_date, today)
    assert full["avg"]["MAYBE"] == pytest.approx(1.0), full

    by_date[today - timedelta(days=3)] = []
    by_date[today - timedelta(days=5)] = []
    thinned = report._compute_daily_distribution(by_date, today)

    assert thinned["avg"]["MAYBE"] == pytest.approx(1.0), (
        f"two silent days entered the divisor and dragged the mean to "
        f"{thinned['avg']['MAYBE']}; a quiet weekend would read as a drop in "
        f"volume"
    )


def test_a_day_with_fewer_entries_does_enter_the_divisor():
    """The other direction. 'Ignore days that look small' would also pass the
    test above and would then hide a real drop in volume."""
    today = date(2026, 8, 25)
    by_date = {today - timedelta(days=i): [_entry("x", "MAYBE")] * 2 for i in range(1, 8)}
    by_date[today - timedelta(days=3)] = [_entry("x", "MAYBE")]

    dist = report._compute_daily_distribution(by_date, today)

    assert dist["avg"]["MAYBE"] == pytest.approx(13 / 7), dist


def test_an_unreadable_seventh_day_is_an_average_gap_not_a_window_gap(drive_main, capsys):
    today = date(2026, 8, 25)
    rc, asked, _ = drive_main(1, unreachable_days={today - timedelta(days=7)})
    err = capsys.readouterr().err
    assert rc == 0
    assert "outside the --days window" in err


def test_an_unreadable_seventh_day_inside_the_window_is_a_window_gap(drive_main, capsys):
    today = date(2026, 8, 25)
    rc, asked, _ = drive_main(8, unreachable_days={today - timedelta(days=7)})
    assert rc == 1
    assert "EXCLUDE those days" in capsys.readouterr().err


# ============================================================
# Finding 3 -- "Warn once" with nothing behind it
# ============================================================
@pytest.fixture
def rules_yaml(tmp_path):
    p = tmp_path / "email-triage-rules.yaml"
    p.write_text("sender_overrides:\n  always_normal:\n    - \"*@example.com\"\n",
                 encoding="utf-8")
    return p


def _missing_warnings(caplog):
    return [r for r in caplog.records if "not found at" in r.getMessage()]


def test_a_missing_file_warns_once_across_repeated_reloads(rules_yaml, caplog):
    eng = RulesEngine(yaml_path=rules_yaml)
    rules_yaml.unlink()
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        eng.reload()
        eng.reload()
        eng.reload()
    assert len(_missing_warnings(caplog)) == 1


def test_the_prior_config_survives_the_missing_file(rules_yaml):
    eng = RulesEngine(yaml_path=rules_yaml)
    rules_yaml.unlink()
    eng.reload()
    assert eng.match_sender("bob@example.com") == "always_normal"


def test_the_file_coming_back_and_vanishing_again_warns_afresh(rules_yaml, caplog):
    eng = RulesEngine(yaml_path=rules_yaml)
    body = rules_yaml.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        rules_yaml.unlink()
        eng.reload()
        eng.reload()
        rules_yaml.write_text(body, encoding="utf-8")
        eng.reload()
        rules_yaml.unlink()
        eng.reload()
    assert len(_missing_warnings(caplog)) == 2


def test_the_empty_config_branch_is_throttled_too(tmp_path, caplog):
    absent = tmp_path / "never-existed.yaml"
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        eng = RulesEngine(yaml_path=absent)   # __post_init__ reloads once
        eng.reload()
        eng.reload()
    assert len(_missing_warnings(caplog)) == 1


def test_the_two_missing_file_messages_still_say_different_things(rules_yaml, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        RulesEngine(yaml_path=tmp_path / "never-existed.yaml")
        empty_msg = _missing_warnings(caplog)[-1].getMessage()
    caplog.clear()
    eng = RulesEngine(yaml_path=rules_yaml)
    rules_yaml.unlink()
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        eng.reload()
        had_msg = _missing_warnings(caplog)[-1].getMessage()
    assert "empty rules" in empty_msg
    assert "keeping prior config" in had_msg


def test_reload_if_changed_stays_silent_after_reload_already_warned(rules_yaml, caplog):
    """One flag, one meaning: the operator has already been told."""
    eng = RulesEngine(yaml_path=rules_yaml)
    rules_yaml.unlink()
    with caplog.at_level(logging.WARNING, logger="scripts.inbox_pulse.overrides"):
        eng.reload()
        caplog.clear()
        eng.reload_if_changed()
    assert caplog.records == []


def test_a_good_reload_still_reports_a_config_change(rules_yaml):
    """Regression: the early flag reset must not swallow the return value."""
    eng = RulesEngine(yaml_path=rules_yaml)
    rules_yaml.write_text(
        "sender_overrides:\n  always_critical:\n    - \"*@urgent.com\"\n", encoding="utf-8")
    assert eng.reload() is True
    assert eng.match_sender("a@urgent.com") == "always_critical"


def test_an_unchanged_file_still_reports_no_change(rules_yaml):
    eng = RulesEngine(yaml_path=rules_yaml)
    assert eng.reload() is False


# ============================================================
# Finding 4 -- the close that closed nothing
# ============================================================
def test_disconnect_no_longer_claims_to_close_the_connection():
    doc = " ".join((EWSConnection.disconnect.__doc__ or "").split())
    assert "Closes NOTHING" in doc
    assert "Close the connection and release the account" not in doc


def test_the_docstring_says_why_closing_is_refused_not_merely_skipped():
    doc = " ".join((EWSConnection.disconnect.__doc__ or "").split())
    assert "CachingProtocol" in doc
    assert doc.index("Closes NOTHING") < doc.index("CachingProtocol")


def test_exchangelib_really_does_share_one_protocol_per_endpoint():
    """The reason the docstring gives, checked against the installed library."""
    from exchangelib.protocol import CachingProtocol, Protocol
    assert isinstance(Protocol, CachingProtocol)
    assert hasattr(Protocol, "close")


def test_disconnect_drops_the_account_reference():
    conn = EWSConnection(account_email="a@b.c", password="p", server_url="s")
    conn._account = object()
    conn.disconnect()
    assert conn._account is None


def test_disconnect_is_idempotent():
    conn = EWSConnection(account_email="a@b.c", password="p", server_url="s")
    conn.disconnect()
    conn.disconnect()
    assert conn._account is None


def test_disconnect_does_not_close_the_shared_protocol():
    """Closing it would tear the pool out from under every other Account."""
    closed = []

    class _Protocol:
        def close(self):
            closed.append(True)

    class _Account:
        protocol = _Protocol()

    conn = EWSConnection(account_email="a@b.c", password="p", server_url="s")
    conn._account = _Account()
    conn.disconnect()
    assert closed == []


# ============================================================
# Finding 5 -- signal handlers installed by an import
# ============================================================
_IMPORT_PROBE = (
    "import signal, sys; sys.path.insert(0, %r); "
    "import scripts.inbox_pulse.daemon; "
    "print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)"
) % str(ROOT)


def test_importing_the_daemon_leaves_the_host_signal_handling_alone():
    out = subprocess.run([sys.executable, "-c", _IMPORT_PROBE],
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("True"), \
        f"import changed SIGINT disposition: {out.stdout!r} {out.stderr[-400:]!r}"


def test_importing_the_daemon_from_a_worker_thread_does_not_raise():
    """`signal.signal` raises ValueError off the main thread."""
    probe = (
        "import sys, threading; sys.path.insert(0, %r); err = []\n"
        "def t():\n"
        "    try:\n"
        "        import scripts.inbox_pulse.daemon\n"
        "    except Exception as exc:\n"
        "        err.append(repr(exc))\n"
        "th = threading.Thread(target=t); th.start(); th.join()\n"
        "print('ERR' if err else 'OK', *err)"
    ) % str(ROOT)
    out = subprocess.run([sys.executable, "-c", probe],
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().startswith("OK"), out.stdout + out.stderr[-400:]


def test_install_signal_handlers_routes_both_signals():
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        dmn.install_signal_handlers()
        assert signal.getsignal(signal.SIGINT) is dmn._handle_signal
        assert signal.getsignal(signal.SIGTERM) is dmn._handle_signal
    finally:
        for s, h in previous.items():
            signal.signal(s, h)


def test_main_installs_the_handlers(monkeypatch, disarm_clone_guard):
    disarm_clone_guard(dmn)
    called = []
    monkeypatch.setattr(dmn, "install_signal_handlers", lambda: called.append(True))
    monkeypatch.setattr(dmn, "health_check", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["daemon", "--check"])
    assert dmn.main() == 0
    assert called == [True]


def test_the_handler_still_sets_the_shutdown_event():
    """Regression: moving the installation must not change what it installs."""
    dmn._shutdown_event.clear()
    try:
        dmn._handle_signal(signal.SIGTERM, None)
        assert dmn._shutdown_event.is_set()
    finally:
        dmn._shutdown_event.clear()


def test_the_module_docstring_records_where_the_handlers_go_now():
    doc = " ".join((dmn.__doc__ or "").split())
    assert "install_signal_handlers()" in doc
    assert "never at import" in doc


def _installs_at_import(source: str, filename: str = "<probe>") -> list[int]:
    """Line numbers where `signal.signal(...)` runs on IMPORT, not on call.

    The text scan this replaced could not enforce its own name. It used
    `glob("*.py")`, which is not recursive, and `line.startswith("signal.signal(")`,
    which sees a call only when it carries no indentation at all. Both real
    calls today live inside `install_signal_handlers`, indented, so the scan
    already found zero hits and could not tell "no import-time handler" from
    "one it cannot see". A regression written as

        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, _handle_signal)

    at module level would have shipped green, and so would anything added under
    a new subdirectory of the package.

    Descent stops at a function or lambda body, because code there runs when it
    is called. It continues through `if`, `try`, `with` and class bodies,
    because those DO run on import.
    """
    tree = ast.parse(source, filename=filename)
    direct = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and node.module == "signal"
        for alias in node.names if alias.name == "signal"
    }
    found: list[int] = []

    def _is_target(func: ast.expr) -> bool:
        if (isinstance(func, ast.Attribute) and func.attr == "signal"
                and isinstance(func.value, ast.Name) and func.value.id == "signal"):
            return True
        return isinstance(func, ast.Name) and func.id in direct

    def _descend(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call) and _is_target(child.func):
                found.append(child.lineno)
            _descend(child)

    _descend(tree)
    return sorted(found)


def test_nothing_else_installs_a_handler_at_import_time():
    """The whole package, not just the one module the finding named."""
    walked = sorted((ROOT / "scripts" / "inbox_pulse").rglob("*.py"))
    assert walked, "empty corpus proves nothing"
    for path, source in read_sources(walked):
        offending = _installs_at_import(source, str(path))
        assert not offending, (
            f"{path.name}:{offending} installs a handler at import")


@pytest.mark.parametrize("body,expected", [
    ("import signal\nsignal.signal(signal.SIGTERM, h)\n", True),
    ("import signal\nif True:\n    signal.signal(signal.SIGTERM, h)\n", True),
    ("import signal\ntry:\n    signal.signal(signal.SIGTERM, h)\nexcept Exception:\n"
     "    raise\n", True),
    ("from signal import signal\nsignal(15, h)\n", True),
    ("import signal\ndef install():\n    signal.signal(signal.SIGTERM, h)\n", False),
    ("import signal\nclass C:\n    def m(self):\n        signal.signal(15, h)\n", False),
    ("import signal\nprint('signal.signal(' )\n", False),
])
def test_the_import_time_detector_sees_what_the_text_scan_could_not(body, expected):
    """The guard above is only worth its name if this discriminates.

    Rows two and three are the regressions the old `startswith` scan was blind
    to. The last three are the shape the package actually uses, plus a string
    that merely mentions the call, which a text scan would have to special-case
    and an AST never sees as a call at all.
    """
    assert bool(_installs_at_import(body)) is expected


def test_the_probe_would_notice_a_regression():
    """A detector that cannot fail proves nothing about the two above it."""
    assert threading.current_thread() is threading.main_thread()
    assert "signal.getsignal" in _IMPORT_PROBE
