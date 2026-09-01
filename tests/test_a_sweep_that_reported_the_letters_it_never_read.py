"""Shard scripts-06-p2: five tools that stated more than their method had shown.

1. THE PULSE PRINTED THE WORD "None". Every local status line interpolated
   `poll_age` straight into "last poll {} min ago", so a daemon that had never
   ticked -- or whose newest stamp would not parse -- reported "last poll None
   min ago" on the line the operator reads to decide whether it is healthy.
   `_print_remote_status` had said "no tick recorded" for the same state since it
   was written; only the local path printed the word.

2. THE PULSE CHECKPOINT SHARED ONE SCRATCH NAME. `os.replace` is atomic; a fixed
   `pulse-checkpoint.tmp` is not. `/loop` fires this every ten minutes, so a
   manual run beside the loop had both writing that path and one `replace` moved
   the other's half-written bytes into place as the baseline. Fourth occurrence
   of this exact shape in this audit.

3. THE PULSE DIED ON A ROSTER IT ONLY USED FOR DECORATION. `load_checkpoint` is
   guarded and says why; `load_roster_names` directly below it was not, so a
   truncated `tribe-roster.json` raised out of `main()` and the status tool
   reported nothing at all.

4. `topic-ideas --cycle N --new` LISTED OLD IDEAS AS NEW. `load_ideas` filtered
   by cycle and THEN looked for the cursor. The digest cursor is global, so after
   a rollover it names an idea in the newest cycle, was absent from the filtered
   list, and "cursor not found" falls back to returning everything.

5. THE GAL EXPORT CALLED A HALF SWEEP "[OK]". A prefix query that raised printed
   one WARN, scrolled off a 36-line sweep, and contributed no addresses -- under a
   closing "[OK] N unique entries" identical to the one a complete sweep prints.
   The JSON it writes is what downstream tooling treats as the address book.

Plus two smaller ones: the webhook's message branch lacked the non-dict guard its
own callback branch has, against the file's stated "a malformed body is the
caller's error" boundary; and `gate-yield --root` promised "working tree to
report over" while the only source it has ignores it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fireside_topics as ft  # noqa: E402


@pytest.fixture(scope="module")
def fp():
    """Load fireside-pulse.py as a module (hyphen in filename)."""
    path = ROOT / "scripts" / "fireside-pulse.py"
    spec = importlib.util.spec_from_file_location("fireside_pulse", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. The pulse that printed the word None
# ============================================================

def test_an_absent_tick_is_words_not_the_string_none(fp):
    assert fp.poll_label(None) == "no tick recorded"


def test_a_real_age_still_reads_as_minutes(fp):
    assert fp.poll_label(7) == "7 min ago"


def test_a_zero_minute_age_is_not_mistaken_for_absent(fp):
    """`if age` would call a tick 30 seconds old "no tick recorded"."""
    assert fp.poll_label(0) == "0 min ago"


def test_the_label_never_contains_the_word_none(fp):
    for age in (None, 0, 1, 999):
        assert "None" not in fp.poll_label(age)


def test_an_unparseable_stamp_is_unknown_not_an_age(fp, capsys):
    assert fp.poll_age_minutes("not-a-timestamp") is None
    assert "UNKNOWN" in capsys.readouterr().err


def test_an_unparseable_stamp_says_so_on_stderr(fp, capsys):
    fp.poll_age_minutes("2026-13-45T99:99")
    err = capsys.readouterr().err
    assert "unreadable" in err


def test_an_empty_stamp_is_silent_because_it_is_not_a_failure(fp, capsys):
    """No tick yet is an ordinary state; only an unreadable one is worth a line."""
    assert fp.poll_age_minutes(None) is None
    assert fp.poll_age_minutes("") is None
    assert capsys.readouterr().err == ""


def test_a_naive_stamp_is_still_measurable(fp):
    """dm-log stamps predate the tz-aware writer; they must not read as unknown."""
    from datetime import datetime
    from scripts.utils.workspace import get_default_tz
    naive = datetime.now(get_default_tz()).replace(tzinfo=None).isoformat()
    assert fp.poll_age_minutes(naive) is not None


# ============================================================
# 2. The checkpoint that shared one scratch name
# ============================================================

def test_the_checkpoint_scratch_path_is_not_a_fixed_name(fp, tmp_path, monkeypatch):
    """Two writers must never be handed the same scratch path."""
    target = tmp_path / "pulse-checkpoint.json"
    monkeypatch.setattr(fp, "checkpoint", lambda p=target: p)
    seen = []
    real_mkstemp = tempfile.mkstemp

    def _spy(*a, **k):
        fd, name = real_mkstemp(*a, **k)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(fp.tempfile, "mkstemp", _spy)
    fp.save_checkpoint({"a": 1})
    fp.save_checkpoint({"a": 2})
    assert len(seen) == 2
    assert seen[0] != seen[1], "both writers got the same scratch path"
    assert target.with_suffix(".tmp") not in [Path(s) for s in seen]


def test_the_checkpoint_still_lands_and_reads_back(fp, tmp_path, monkeypatch):
    target = tmp_path / "pulse-checkpoint.json"
    monkeypatch.setattr(fp, "checkpoint", lambda p=target: p)
    fp.save_checkpoint({"started_uids": [1, 2], "session_count": 3})
    assert json.loads(target.read_text(encoding="utf-8"))["session_count"] == 3


def test_the_scratch_file_shares_the_targets_directory(fp, tmp_path, monkeypatch):
    """os.replace is only atomic within one filesystem."""
    target = tmp_path / "nested" / "pulse-checkpoint.json"
    monkeypatch.setattr(fp, "checkpoint", lambda p=target: p)
    seen = []
    real_mkstemp = tempfile.mkstemp

    def _spy(*a, **k):
        fd, name = real_mkstemp(*a, **k)
        seen.append(Path(name).parent)
        return fd, name

    monkeypatch.setattr(fp.tempfile, "mkstemp", _spy)
    fp.save_checkpoint({"a": 1})
    assert seen == [target.parent]


def test_no_scratch_file_survives_a_successful_write(fp, tmp_path, monkeypatch):
    target = tmp_path / "pulse-checkpoint.json"
    monkeypatch.setattr(fp, "checkpoint", lambda p=target: p)
    fp.save_checkpoint({"a": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_no_scratch_file_survives_a_failed_write(fp, tmp_path, monkeypatch):
    target = tmp_path / "pulse-checkpoint.json"
    monkeypatch.setattr(fp, "checkpoint", lambda p=target: p)

    class _Unserialisable:
        pass

    with pytest.raises(TypeError):
        fp.save_checkpoint({"bad": _Unserialisable()})
    assert list(tmp_path.glob("*.tmp")) == []
    assert not target.exists(), "a failed write must not create the target"


# ============================================================
# 3. The roster read that killed the status tool
# ============================================================

def _roster_dir(fp, tmp_path, monkeypatch, body: str):
    monkeypatch.setattr(fp, "state_dir", lambda p=tmp_path: p)
    (tmp_path / "tribe-roster.json").write_text(body, encoding="utf-8")


def test_a_truncated_roster_does_not_raise(fp, tmp_path, monkeypatch, capsys):
    _roster_dir(fp, tmp_path, monkeypatch, '{"a": {"name": "A"')
    assert fp.load_roster_names() == {}
    assert "unreadable" in capsys.readouterr().err


def test_a_roster_that_is_a_list_does_not_raise(fp, tmp_path, monkeypatch, capsys):
    """Valid JSON, wrong shape -- `.items()` is what raised."""
    _roster_dir(fp, tmp_path, monkeypatch, '["a", "b"]')
    assert fp.load_roster_names() == {}
    assert "not an" in capsys.readouterr().err


def test_a_roster_row_that_is_not_an_object_is_skipped(fp, tmp_path, monkeypatch):
    _roster_dir(fp, tmp_path, monkeypatch,
                '{"good": {"name": "Good", "telegram_user_id": 5}, "bad": "oops"}')
    assert fp.load_roster_names() == {5: "Good"}


def test_a_healthy_roster_still_maps_ids_to_names(fp, tmp_path, monkeypatch):
    _roster_dir(fp, tmp_path, monkeypatch,
                '{"h": {"name": "Held Name", "telegram_user_id": 9}}')
    assert fp.load_roster_names() == {9: "Held Name"}


def test_a_missing_roster_is_silent_because_it_is_not_a_failure(fp, tmp_path,
                                                                monkeypatch, capsys):
    monkeypatch.setattr(fp, "state_dir", lambda p=tmp_path / "nothing-here": p)
    assert fp.load_roster_names() == {}
    assert capsys.readouterr().err == ""


# ============================================================
# 4. topic-ideas --cycle N --new
# ============================================================

def _ideas(tmp_path, *rows):
    """rows: (cycle, text). Returns the state dir."""
    d = tmp_path / "state"
    for cycle, text in rows:
        ft.append_idea(d, now_iso="2026-01-01T00:00:00", user_id=1,
                       username="u", name="N", text=text, cycle=cycle)
    return d


def test_an_old_cycles_ideas_are_not_new_after_a_rollover(tmp_path):
    d = _ideas(tmp_path, (2, "old A"), (2, "old B"), (3, "new C"))
    cursor = ft.load_ideas(d)[-1]["idea_id"]          # the global digest cursor
    assert ft.load_ideas(d, cycle=2, since_id=cursor) == []


def test_the_current_cycles_new_ideas_are_still_returned(tmp_path):
    d = _ideas(tmp_path, (3, "seen"), (3, "fresh"))
    cursor = ft.load_ideas(d)[0]["idea_id"]
    got = ft.load_ideas(d, cycle=3, since_id=cursor)
    assert [i["text"] for i in got] == ["fresh"]


def test_the_cursor_is_resolved_across_cycles_not_within_one(tmp_path):
    """Cursor in cycle 2, asking about cycle 3: everything after it, of cycle 3."""
    d = _ideas(tmp_path, (2, "a"), (2, "b"), (3, "c"), (3, "d"))
    cursor = ft.load_ideas(d)[1]["idea_id"]           # "b", a cycle-2 idea
    got = ft.load_ideas(d, cycle=3, since_id=cursor)
    assert [i["text"] for i in got] == ["c", "d"]


def test_a_cycle_filter_alone_is_unchanged(tmp_path):
    d = _ideas(tmp_path, (2, "a"), (3, "b"))
    assert [i["text"] for i in ft.load_ideas(d, cycle=2)] == ["a"]


def test_a_cursor_alone_is_unchanged(tmp_path):
    d = _ideas(tmp_path, (2, "a"), (3, "b"))
    cursor = ft.load_ideas(d)[0]["idea_id"]
    assert [i["text"] for i in ft.load_ideas(d, since_id=cursor)] == ["b"]


def test_an_unknown_cursor_still_returns_everything(tmp_path):
    """new_ideas_since depends on this: an unknown cursor means "all"."""
    d = _ideas(tmp_path, (2, "a"), (3, "b"))
    got = ft.load_ideas(d, since_id="deadbeef")
    assert [i["text"] for i in got] == ["a", "b"]


def test_new_ideas_since_is_unaffected(tmp_path):
    d = _ideas(tmp_path, (2, "a"), (3, "b"))
    new, cursor = ft.new_ideas_since(d, None)
    assert [i["text"] for i in new] == ["a", "b"]
    assert cursor == new[-1]["idea_id"]


def test_a_second_digest_run_sees_only_what_arrived_after(tmp_path):
    d = _ideas(tmp_path, (3, "first"))
    _, cursor = ft.new_ideas_since(d, None)
    ft.append_idea(d, now_iso="2026-01-02T00:00:00", user_id=1, username="u",
                   name="N", text="second", cycle=3)
    new, _ = ft.new_ideas_since(d, cursor)
    assert [i["text"] for i in new] == ["second"]


# ============================================================
# 5. The GAL sweep that called a half read "[OK]"
# ============================================================

@pytest.fixture(scope="module")
def gal():
    path = ROOT / "scripts" / "gal-export.py"
    spec = importlib.util.spec_from_file_location("gal_export", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Proto:
    """Fails every query whose prefix is in `fail`, else returns one mailbox."""

    def __init__(self, fail):
        self.fail = set(fail)
        self.asked: list[str] = []

    def resolve_names(self, queries, **kw):
        q = queries[0]
        self.asked.append(q)
        if q in self.fail:
            raise RuntimeError(f"ErrorServerBusy for {q}")
        return [_Mailbox(f"{q}user@example.test", f"{q} User")]


class _Mailbox:
    def __init__(self, email, name):
        self.email_address = email
        self.name = name
        self.mailbox_type = "Mailbox"


class _Account:
    def __init__(self, fail=()):
        self.protocol = _Proto(fail)


def test_a_failed_prefix_is_returned_not_only_printed(gal):
    _, failed = gal.sweep_gal(_Account(fail={"a", "b"}), "example.test")
    assert sorted(failed) == ["a", "b"]


def test_a_clean_sweep_reports_no_failures(gal):
    records, failed = gal.sweep_gal(_Account(), "example.test")
    assert failed == []
    assert records, "the clean sweep must still collect addresses"


def test_a_failed_prefix_contributes_no_addresses(gal):
    clean, _ = gal.sweep_gal(_Account(), "example.test")
    partial, failed = gal.sweep_gal(_Account(fail={"a"}), "example.test")
    assert len(partial) == len(clean) - 1
    assert failed == ["a"]


def test_the_domain_filter_is_exact_not_a_substring(gal):
    assert gal._in_domain("bob@acme.example", "acme.example")
    assert not gal._in_domain("alice@notacme.example", "acme.example")
    assert not gal._in_domain("bob@acme.example.evil.test", "acme.example")


def test_an_address_with_no_local_part_is_not_in_the_domain(gal):
    assert not gal._in_domain("@acme.example", "acme.example")
    assert not gal._in_domain("", "acme.example")


def test_the_sweep_returns_a_pair_so_the_caller_cannot_ignore_failures(gal):
    """A bare list let `main` print [OK] without ever seeing the failures."""
    out = gal.sweep_gal(_Account(fail={"z"}), "example.test")
    assert isinstance(out, tuple) and len(out) == 2


def test_the_tenant_prefixes_are_derived_from_the_domain_not_written_in(gal):
    """The other claim `sweep_gal` makes about itself, and it had no witness.

    Two of its extra prefixes were the tenant's own name and `@<tenant domain>`,
    typed in as literals; the docstring says both are derived from `domain` now,
    "so the sweep is as thorough on any deployment as it was on the one it was
    written for". MEASURED 2026-09-01: putting the literal back left this file,
    `tests/test_no_tenant_domain_is_compiled_into_the_engine.py` and
    `tests/test_two_controls_that_measured_themselves.py` at 87 passed, so a
    second deployment would silently lose its own label from the sweep and query
    a stranger's instead.

    Asked of the queries the protocol actually RECEIVED, not of the module's
    source, and with two unrelated domains so the answer cannot come from one
    coincidence.
    """
    first, second = _Account(), _Account()
    gal.sweep_gal(first, "acme.example")
    gal.sweep_gal(second, "vesper.test")

    assert "acme" in first.protocol.asked and "@acme.example" in first.protocol.asked
    assert "vesper" in second.protocol.asked and "@vesper.test" in second.protocol.asked
    # And neither sweep carried the other's tenant, which is what a written-in
    # literal would produce.
    assert "vesper" not in first.protocol.asked
    assert "acme" not in second.protocol.asked
    # The shared prefixes are the same in both, so the difference above is the
    # derivation and not two entirely different query sets.
    assert {"a", "z", "0", "info", "sales"} <= set(first.protocol.asked)
    assert {"a", "z", "0", "info", "sales"} <= set(second.protocol.asked)


# ============================================================
# The two smaller ones
# ============================================================

# The webhook message-branch guards were covered here by two tests that
# restated the guard expression in their own bodies and asserted on the
# restatement, so they never loaded `scripts/fireside_webhook.py` and passed
# identically with the guards removed (measured 2026-08-29: three real 500s,
# suite still green). Replaced, not dropped, by behavioural tests that build the
# real app and POST at it:
# `tests/test_controls_that_restated_the_code_they_guarded.py`.


def test_the_gate_yield_root_flag_does_not_claim_a_scope_it_lacks():
    """`read_sources` documents `root` as accepted and unused."""
    import subprocess
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gate-yield.py"), "--help"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60).stdout
    assert "INERT" in out
    assert "working tree to report over" not in out


def test_read_sources_still_ignores_the_root_it_is_handed(tmp_path, monkeypatch):
    """If this ever starts mattering, the help text above must change with it.

    Reads a FIXTURE log, never the live one. The first version called
    `read_sources` twice against the real workspace denial log and compared the
    counts -- which passed alone and failed under the parallel suite, because
    other tests trip real hook denials and append to that file between the two
    reads. A test that consults live shared state is measuring the clock.
    """
    from scripts.utils import denial_log
    from scripts.utils.gate_yield import read_sources

    log = tmp_path / "denials.jsonl"
    log.write_text(
        json.dumps({"ts": "2026-08-01T00:00:00+00:00", "mechanism": "m",
                    "cause": "c"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(denial_log, "denial_log_path", lambda: log)

    a = read_sources(ROOT)
    b = read_sources(Path("/nonexistent-root-for-this-test"))
    assert a == b, "the root argument changed the result; the help text is now wrong"
    assert len(a["denials"]) == 1


# ============================================================
# Gaps a mis-aimed mutation exposed
# ============================================================

def _remote(fp, monkeypatch, svc, listening=True):
    """Drive _print_remote_status down the SSH-probe-failed branch."""
    monkeypatch.setattr(fp, "_svc", lambda p=svc: p)
    monkeypatch.setattr(fp, "_query_service_host", lambda host: None)
    seen = []

    def _listen(host, port, timeout=6.0):
        seen.append(port)
        return listening

    monkeypatch.setattr(fp, "_webhook_listening", _listen)
    fp._print_remote_status("some-host")
    return seen


def test_a_non_numeric_webhook_port_does_not_crash_the_status_line(fp, monkeypatch,
                                                                   capsys):
    """This guard sits on the path that reports "daemon state UNKNOWN".

    A stack trace there is a worse answer than the unknown it was about to give,
    and nothing exercised it -- the mutation aimed at `poll_age_minutes` landed
    on this `except` instead, because the file has two of them, and survived.
    """
    seen = _remote(fp, monkeypatch, {"webhook_port": "not-a-port"})
    assert seen == [8443]
    err = capsys.readouterr().err
    assert "not a port number" in err


def test_a_null_webhook_port_falls_back_too(fp, monkeypatch, capsys):
    seen = _remote(fp, monkeypatch, {"webhook_port": None})
    assert seen == [8443]
    assert "not a port number" in capsys.readouterr().err


def test_a_numeric_string_port_is_accepted_not_replaced(fp, monkeypatch, capsys):
    """JSON configs often carry ports as strings; that is not a misconfiguration."""
    seen = _remote(fp, monkeypatch, {"webhook_port": "9443"})
    assert seen == [9443]
    assert "not a port number" not in capsys.readouterr().err


def test_a_real_port_is_used_verbatim(fp, monkeypatch, capsys):
    seen = _remote(fp, monkeypatch, {"webhook_port": 7777})
    assert seen == [7777]
    assert "not a port number" not in capsys.readouterr().err


def test_an_absent_webhook_port_uses_the_documented_default(fp, monkeypatch, capsys):
    seen = _remote(fp, monkeypatch, {})
    assert seen == [8443]
    assert "not a port number" not in capsys.readouterr().err, \
        "an absent key is a default, not a misconfiguration to warn about"


def test_the_reported_port_is_the_one_actually_probed(fp, monkeypatch, capsys):
    """The message names a port; it must be the port the probe used."""
    _remote(fp, monkeypatch, {"webhook_port": "nope"}, listening=True)
    out = capsys.readouterr().out
    assert "8443" in out


def test_the_domain_filter_ignores_case_on_the_address(gal):
    """Real GAL entries are mixed case; a lowercase-only fixture proves nothing."""
    assert gal._in_domain("Bob@ACME.Example", "acme.example")


def test_the_domain_filter_ignores_case_on_the_filter(gal):
    assert gal._in_domain("bob@acme.example", "ACME.Example")


def test_the_domain_filter_ignores_case_on_both_sides(gal):
    assert gal._in_domain("BOB@ACME.EXAMPLE", "acme.example")
    assert not gal._in_domain("BOB@NOTACME.EXAMPLE", "acme.example")
