#!/usr/bin/env python3
"""`crm_next.py`, and the suppression control that existed three times over.

Shard `scripts-04-p4` of the 2026-08-23 engine audit. Its first finding —
`rank_candidates` swallowing a `radar_freeze_until` parse failure into `pass`,
so a do-not-contact marker became an outreach card — is the same defect fixed
in `cold_sweep_core._frozen` earlier the same night. Looking for the second
copy found a third: `scripts/utils/crm.is_radar_frozen`, whose docstring said
in as many words that it "matches the freeze semantics already honored by
cold_sweep_core.route() and crm_next.rank_candidates()". Three implementations
of one control, all silently fail-open, and a fix applied to any one of them
left the other two wrong.

They are one function now. `is_radar_frozen` fails CLOSED and prints why; the
other two call it. The two errors are not symmetric: a contact wrongly held
back is a question the operator can ask, and the other direction is a message
to someone who was explicitly frozen.

The rest of the shard, all in `crm_next.py`:

* `-int(c.get("days_overdue", 0))` defaulted only on a MISSING key, so an
  explicit `None` or `""` raised inside the sort key and produced no queue at
  all;
* `json.loads` on a zero-exit `crm-health.py --json` was unguarded, and a dict
  instead of a list got iterated as keys and failed further from the cause;
* `get_crm_contacts_dir() / c["file"]` trusted that value as a path — under
  pathlib an ABSOLUTE `file` discards the base entirely, and `../../x` walks
  out, after which the excerpt is copied into the queue the operator reads;
* the queue was written non-atomically to a deterministic same-day path;
* both printed send instructions used `--body "<body>"`, putting outreach text
  in `ps` and shell history, when `--body-stdin` has existed since 2026-08-23
  with a help string naming that exact problem;
* `name.split()[0] if name else 'there'` treats `"   "` as truthy and
  `"   ".split()` is `[]`;
* two fenced blocks carry text this script does not control, and a triple
  backtick in either closed the fence early and rendered the rest as queue
  markdown.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.cold_sweep_core as sweep  # noqa: E402
import scripts.crm_next as nxt  # noqa: E402
from scripts.utils import crm as crm_utils  # noqa: E402

TODAY = "2026-08-24"


# ---------------------------------------------------------------------------
# One implementation
# ---------------------------------------------------------------------------

FREEZE_CALLERS = ("scripts/crm_next.py", "scripts/cold_sweep_core.py")


def _functions_that_parse_the_freeze(path: Path, label: str | None = None) -> list[str]:
    """Functions that both touch `radar_freeze_until` and parse a date.

    Per FUNCTION, via the AST. Two coarser drafts of this test both cried wolf:
    file-scoped flagged `crm_next` for `date.fromisoformat(today)` — a
    different field in a different function — and line-scoped flagged a
    docstring and the legitimate `_frozen(row.get("radar_freeze_until"), now)`
    delegate call. A detector nobody can trust gets switched off, so it has to
    be able to tell "reads the field" from "re-implements the parse".
    """
    import ast
    rel = label or str(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Mentions of the field that sit INSIDE an is_radar_frozen(...) call are
        # the point of the fix, not a violation. `rank_candidates` parses a date
        # in the same body — `date.fromisoformat(today)`, a different field —
        # and a rule that only counted "touches" and "parses" per function could
        # not tell that from a re-implementation.
        delegated = set()
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            fname = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if fname != "is_radar_frozen":
                continue
            for inner in ast.walk(call):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    delegated.add(id(inner))
                if isinstance(inner, ast.Name):
                    delegated.add(id(inner))

        loose = [
            n for n in ast.walk(node)
            if id(n) not in delegated
            and ((isinstance(n, ast.Constant) and n.value == "radar_freeze_until")
                 or (isinstance(n, ast.Name) and n.id == "radar_freeze_until"))
        ]
        parses = any(
            isinstance(n, ast.Attribute) and n.attr in ("fromisoformat", "strptime")
            for n in ast.walk(node)
        )
        if loose and parses:
            offenders.append(f"{rel}:{node.lineno}:{node.name}")
    return offenders


def test_there_is_exactly_one_freeze_parse():
    """A second copy is the one that stops being fixed."""
    offenders = [o for rel in FREEZE_CALLERS
                 for o in _functions_that_parse_the_freeze(ROOT / rel, rel)]
    assert not offenders, (
        "these re-implement the radar_freeze_until parse instead of calling "
        "crm.is_radar_frozen; that is how one fix reached one of three:\n  "
        + "\n  ".join(offenders)
    )


def test_the_freeze_detector_would_catch_a_reverted_copy(tmp_path):
    """Guard the premise: a detector that matches nothing passes everything.

    Runs the REAL detector against the code as it stood before the fix, rather
    than re-asserting its two conditions by hand. Re-stating a rule is not the
    same as exercising it, and this detector was wrong twice before it was
    right.
    """
    reverted = tmp_path / "reverted.py"
    reverted.write_text(
        "from datetime import date\n"
        "def rank(c, today):\n"
        "    freeze = c.get('radar_freeze_until', '')\n"
        "    if freeze:\n"
        "        try:\n"
        "            return date.fromisoformat(freeze) > today\n"
        "        except ValueError:\n"
        "            pass\n"
        "    return False\n",
        encoding="utf-8")
    found = _functions_that_parse_the_freeze(reverted, "reverted.py")
    assert found == ["reverted.py:2:rank"], (
        f"the detector missed the exact code it exists to catch: {found}"
    )


def test_the_freeze_detector_accepts_the_delegating_shape(tmp_path):
    """And does not fire on a function that parses a DIFFERENT date."""
    ok = tmp_path / "ok.py"
    ok.write_text(
        "from datetime import date\n"
        "def rank(c, today):\n"
        "    today_date = date.fromisoformat(today)\n"
        "    if is_radar_frozen(c.get('radar_freeze_until'), today_date):\n"
        "        return None\n"
        "    return c\n",
        encoding="utf-8")
    assert _functions_that_parse_the_freeze(ok, "ok.py") == []


def test_the_freeze_detector_reads_real_files():
    """And that the files it is scoped to still exist and still call the helper."""
    for rel in FREEZE_CALLERS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "radar_freeze_until" in src, f"{rel} no longer touches the field"


def test_both_call_sites_reach_the_shared_helper():
    for rel in ("scripts/crm_next.py", "scripts/cold_sweep_core.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "is_radar_frozen" in src, f"{rel} no longer calls the helper"


@pytest.mark.parametrize("value", ["not-a-date", "last week", "2026-13-45", "soon"])
def test_an_unparseable_freeze_is_frozen(value, capsys):
    assert crm_utils.is_radar_frozen(value, date(2026, 8, 24)) is True
    assert "not an ISO date" in capsys.readouterr().err, (
        "failing closed silently is only half the fix; a typo has to be visible"
    )


def test_an_empty_freeze_is_not_frozen():
    """Anchor: fail-closed must not mean always-closed."""
    for value in ("", "   ", None):
        assert crm_utils.is_radar_frozen(value, date(2026, 8, 24)) is False


def test_a_past_freeze_has_expired():
    assert crm_utils.is_radar_frozen("2020-01-01", date(2026, 8, 24)) is False


def test_a_future_freeze_holds():
    assert crm_utils.is_radar_frozen("2099-01-01", date(2026, 8, 24)) is True


def test_a_datetime_freeze_and_a_datetime_today_both_work():
    """cold_sweep passes an aware datetime; crm_next passes a date."""
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert crm_utils.is_radar_frozen("2099-01-01T00:00:00Z", now) is True
    assert crm_utils.is_radar_frozen("2020-01-01T00:00:00Z", now) is False


def test_cold_sweep_still_answers_through_the_delegate():
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert sweep._frozen("2099-01-01", now) is True
    assert sweep._frozen("2020-01-01", now) is False


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------

def _red(**kw):
    base = {"health": "red", "stage": "Lead", "days_overdue": 10,
            "name": "James Bond", "file": "james-bond.md"}
    base.update(kw)
    return base


def test_a_frozen_contact_with_a_broken_date_is_still_excluded(capsys):
    out = nxt.rank_candidates([_red(radar_freeze_until="not-a-date")], today=TODAY)
    assert out == [], (
        "the parse failure was swallowed into `pass`, so a contact the "
        "operator explicitly froze sat in the outreach queue"
    )


def test_a_genuinely_unfrozen_contact_is_still_ranked():
    """Anchor: a filter that drops everything is not a filter."""
    out = nxt.rank_candidates([_red(radar_freeze_until="2020-01-01")], today=TODAY)
    assert len(out) == 1


def test_a_contact_with_no_freeze_field_is_ranked():
    out = nxt.rank_candidates([_red()], today=TODAY)
    assert len(out) == 1


@pytest.mark.parametrize("value", [None, "", "unknown", [], {}])
def test_a_non_numeric_days_overdue_does_not_kill_the_queue(value):
    out = nxt.rank_candidates([_red(days_overdue=value)], today=TODAY)
    assert len(out) == 1, (
        f"days_overdue={value!r} raised inside the sort key and NO queue was "
        "generated; a missing number is not a reason to skip every follow-up"
    )


def test_the_ranking_order_still_holds():
    """Anchor: the coercion must not flatten the sort."""
    out = nxt.rank_candidates([
        _red(name="A", stage="Lead", days_overdue=1),
        _red(name="B", stage="Negotiation", days_overdue=1),
        _red(name="C", stage="Lead", days_overdue=99),
    ], top_n=3, today=TODAY)
    assert [c["name"] for c in out] == ["B", "C", "A"]


def test_non_red_contacts_are_still_filtered():
    assert nxt.rank_candidates([_red(health="yellow")], today=TODAY) == []


# ---------------------------------------------------------------------------
# The contact path
# ---------------------------------------------------------------------------

def test_an_absolute_contact_file_is_refused(capsys):
    assert nxt._contact_path({"file": "/etc/passwd", "name": "x"}) is None, (
        "under pathlib `base / '/etc/passwd'` IS '/etc/passwd'; the base is "
        "discarded entirely and the excerpt lands in the review file"
    )
    assert "outside" in capsys.readouterr().err


def test_a_traversing_contact_file_is_refused(capsys):
    assert nxt._contact_path({"file": "../../secret.md", "name": "x"}) is None
    assert "outside" in capsys.readouterr().err


def test_a_missing_file_key_is_not_a_keyerror(capsys):
    assert nxt._contact_path({"name": "x"}) is None
    assert "no `file`" in capsys.readouterr().err


def test_an_ordinary_contact_file_resolves():
    """Anchor: the containment check must not refuse the normal case."""
    resolved = nxt._contact_path({"file": "james-bond.md", "name": "x"})
    assert resolved is not None
    assert resolved.name == "james-bond.md"
    assert resolved.parent == nxt.get_crm_contacts_dir().resolve()


# ---------------------------------------------------------------------------
# render_draft
# ---------------------------------------------------------------------------

def test_a_whitespace_only_name_does_not_crash():
    out = nxt.render_draft({"name": "   ", "days_overdue": 1, "cadence": 14},
                           "(no prior interaction)")
    assert "Hey there," in out, (
        '`"   "` is truthy and `"   ".split()` is `[]`, so `[0]` raised '
        "IndexError; None and empty were handled, this was not"
    )


@pytest.mark.parametrize("name, expect", [
    ("James Bond", "Hey James,"),
    ("", "Hey there,"),
    (None, "Hey there,"),
    ("  Felix  Leiter ", "Hey Felix,"),
])
def test_the_greeting_still_uses_the_first_name(name, expect):
    assert expect in nxt.render_draft(
        {"name": name, "days_overdue": 1, "cadence": 14}, "(no prior)")


def test_a_non_numeric_cadence_does_not_crash():
    out = nxt.render_draft({"name": "X", "days_overdue": None, "cadence": None},
                           "(no prior)")
    assert "14 days" in out


# ---------------------------------------------------------------------------
# The fenced blocks
# ---------------------------------------------------------------------------

def test_a_body_containing_a_fence_cannot_close_its_own_block():
    body = "before\n```\n# INJECTED HEADING\n```\nafter"
    lines = nxt._fenced(body)
    fence = lines[0]
    assert len(fence) > 3, "a three-backtick fence is closed by the content"
    assert fence not in body
    assert lines[-1] == fence


def test_an_ordinary_body_gets_an_ordinary_fence():
    """Anchor: don't widen the fence when nothing threatens it."""
    assert nxt._fenced("plain text")[0] == "```"


def test_a_longer_run_widens_the_fence_further():
    assert nxt._fenced("`````")[0] == "``````"


def test_the_queue_writer_uses_the_helper():
    src = (ROOT / "scripts" / "crm_next.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert body.count('lines.append("```")') == 0, (
        "a bare fence is back; the excerpt and the draft both carry text this "
        "script does not control"
    )
    assert body.count("_fenced(") >= 2


# ---------------------------------------------------------------------------
# generate_queue
# ---------------------------------------------------------------------------

def _health_output(payload, returncode=0):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess([], returncode, text, "")


def test_non_json_health_output_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.setattr(nxt.subprocess, "run",
                        lambda *a, **k: _health_output("not json"))
    with pytest.raises(SystemExit) as exc:
        nxt.generate_queue(today=TODAY)
    assert exc.value.code == 1
    assert "not JSON" in capsys.readouterr().err, (
        "a zero exit with malformed stdout killed the daily job on a traceback"
    )


def test_a_dict_instead_of_a_list_is_a_clean_error(monkeypatch, capsys):
    monkeypatch.setattr(nxt.subprocess, "run",
                        lambda *a, **k: _health_output({"contacts": []}))
    with pytest.raises(SystemExit) as exc:
        nxt.generate_queue(today=TODAY)
    assert exc.value.code == 1
    assert "not a list" in capsys.readouterr().err, (
        "rank_candidates iterated the KEYS and failed later, further from the "
        "cause"
    )


def test_a_good_payload_still_writes_the_queue(monkeypatch, tmp_path):
    """Anchor: the guards must not refuse the normal case."""
    monkeypatch.setattr(nxt, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(nxt.subprocess, "run",
                        lambda *a, **k: _health_output([_red()]))
    out = nxt.generate_queue(today=TODAY)
    assert out.exists()
    assert "James Bond" in out.read_text(encoding="utf-8")


def test_the_queue_is_written_atomically():
    src = (ROOT / "scripts" / "crm_next.py").read_text(encoding="utf-8")
    assert "atomic_write_text(out_file" in src, (
        "the path is deterministic per day, so two runs, or one run while the "
        "operator has the file open, raced on a plain write_text"
    )
    assert 'out_file.write_text(' not in src


def test_the_send_instructions_keep_the_body_off_the_command_line():
    src = (ROOT / "scripts" / "crm_next.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert '--body \\"<body>\\"' not in body and '--body "<body>"' not in body, (
        "an argv element is readable by any local account through `ps` for the "
        "life of the send, and outreach text also lands in shell history"
    )
    assert body.count("--body-stdin") >= 2, (
        "both the queue file's instruction and the --send stub print one"
    )


def test_send_email_really_does_offer_body_stdin():
    """Guard the premise: an instruction pointing at a flag that does not exist
    is worse than the flag it replaced."""
    src = (ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8")
    assert '"--body-stdin"' in src
