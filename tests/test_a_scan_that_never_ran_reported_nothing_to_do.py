"""Shard `scripts-05-p1`: an outage that looked exactly like a clean result.

The headline finding runs through TWO files, and neither is wrong on its own.

`dream-shadow.py` degrades gracefully when the embedder is unavailable: it sets
`merge.ok = False` and writes the reason into the report. It writes that reason
as an ordinary bullet under `## Merge Candidates`. `prime-health-parallel.py`
counts merge candidates out of that section with `^- .+<->.+$`, and the reason
line carries no `<->`, so the count came back 0 - and 0 means
`{"status": "ok", "output": "", "omit_if_empty": True}`, which /prime drops
entirely. The embedder could be down every night for a month and session boot
would never say one word. `dream-shadow`'s own summary line had the same hole:
`0 merge candidate(s)` for a scan that never ran, and `--quiet` prints ONLY that
line. The file already refuses to print `clean` in this case, forty lines below
the summary that said zero.

The rest of the shard:

  - `elicit.py` served ANY csv as the technique catalog. `setdefault(k, "")`
    filled every missing column, so a two-row `name,email` file was reported as
    one category - the empty string - holding 2 methods, and `list` printed two
    rows of nothing at exit 0. Reachable by a typo'd `--file`.
  - `elicit list --category <typo>` printed an empty string and exited 0, which
    reads as "that category is empty", while `random --category <typo>` said
    "# no methods match" and exited 1. One tool, two answers to one mistake.
  - `elicit find` kept the LAST row for a repeated `method_name`, so `show "X"`
    would return one of two different methods with no signal.
  - `draft-critique.py` refused a body that was not JSON and accepted valid JSON
    of the wrong shape, reaching `.get` on a list as an AttributeError. And a
    card with no draft at all (a note, an alert, a pipeline update) fell out at
    "model unavailable, missing API key, or empty draft body" - three causes,
    none of them the real one.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# dream-shadow -> prime-health: an outage is not a clean result
# ===========================================================================

@pytest.fixture(scope="module")
def ds():
    return _load("dream_shadow_shard", "scripts/dream-shadow.py")


@pytest.fixture(scope="module")
def ph():
    return _load("prime_health_shard", "scripts/prime-health-parallel.py")


_DOWN = {"ok": False, "note": "embedder unavailable: connection refused",
         "pairs": []}
_CLEAN = {"ok": True, "note": "", "pairs": []}
_PAIR = {"ok": True, "note": "", "pairs": [
    {"a": "one.md", "b": "two.md", "score": 0.91,
     "a_salience": 0.5, "b_salience": 0.4, "rank_salience": 0.5}]}


# Any path string; the report only echoes it into a header line. Not a literal
# `/tmp/...`, which ruff flags as S108 (hardcoded temp directory).
_MEM_DIR = "auto-memory"


def _report(ds, merge: dict) -> str:
    return ds.render_report({"memory_dir": _MEM_DIR, "dormant": [], "merge": merge},
                            "2026-08-24T03:10:00+04:00")


def test_the_report_marks_an_outage_as_an_outage(ds):
    text = _report(ds, _DOWN)
    assert ds.MERGE_UNAVAILABLE_MARKER in text
    assert "connection refused" in text, "the reason must survive the marker"


def test_a_clean_scan_does_not_carry_the_outage_marker(ds):
    """Anchor: 'nothing found' must stay distinguishable from 'did not run'."""
    text = _report(ds, _CLEAN)
    assert ds.MERGE_UNAVAILABLE_MARKER not in text
    assert "None today." in text


def test_a_real_pair_does_not_carry_the_outage_marker(ds):
    text = _report(ds, _PAIR)
    assert ds.MERGE_UNAVAILABLE_MARKER not in text
    assert "one.md <-> two.md" in text


def _health(ph, ds, tmp_path, monkeypatch, merge: dict) -> dict:
    report_dir = tmp_path / "operations" / "dream"
    report_dir.mkdir(parents=True)
    (report_dir / "2026-08-24_dream-shadow_report.md").write_text(
        _report(ds, merge), encoding="utf-8")
    monkeypatch.setattr(ph, "get_outputs_dir", lambda *a, **k: tmp_path)
    return ph.run_dream_shadow(tmp_path)


def test_session_boot_surfaces_the_outage(ph, ds, tmp_path, monkeypatch):
    """The whole finding: a month of nightly failures said nothing at /prime."""
    result = _health(ph, ds, tmp_path, monkeypatch, _DOWN)
    assert result["output"], (
        "the merge scan never ran and session boot rendered nothing at all"
    )
    assert "did not run" in result["output"]
    assert "connection refused" in result["output"], "name the reason"


def test_the_outage_line_is_not_omitted_when_empty(ph, ds, tmp_path, monkeypatch):
    """`omit_if_empty` is what dropped it; rendering must not depend on the
    status string, which `render_text` only consults for stderr."""
    result = _health(ph, ds, tmp_path, monkeypatch, _DOWN)
    assert result["omit_if_empty"] is False
    assert result["status"] not in ph.NON_FAILURE_STATUSES


def test_a_clean_scan_is_still_silent_at_boot(ph, ds, tmp_path, monkeypatch):
    """Anchor: /prime must not grow a line that fires every session."""
    result = _health(ph, ds, tmp_path, monkeypatch, _CLEAN)
    assert result["output"] == ""
    assert result["status"] == "ok"


def test_a_real_candidate_is_still_counted(ph, ds, tmp_path, monkeypatch):
    """Anchor: the marker branch must not shadow the counting branch."""
    result = _health(ph, ds, tmp_path, monkeypatch, _PAIR)
    assert "1 merge candidates" in result["output"]


def test_the_two_files_agree_on_the_marker_text(ph):
    """They match the same literal with separate regexes - prime-health runs at
    session boot and importing a kebab-case module for one string is not worth
    it there - so nothing but this test holds them in step."""
    ds_src = (ROOT / "scripts" / "dream-shadow.py").read_text(encoding="utf-8")
    ph_src = (ROOT / "scripts" / "prime-health-parallel.py").read_text(encoding="utf-8")
    marker = re.search(r'MERGE_UNAVAILABLE_MARKER = "([^"]+)"', ds_src).group(1)
    assert marker in ph_src, (
        f"dream-shadow writes {marker!r} and prime-health does not look for it; "
        "the outage would go back to reading as a clean result"
    )


def test_the_summary_line_does_not_call_an_outage_zero(ds, monkeypatch, capsys):
    """`--quiet` prints ONLY this line, and it is the mode a check would use."""
    monkeypatch.setattr(ds, "gather", lambda: {
        "memory_dir": _MEM_DIR, "dormant": [], "merge": _DOWN})
    monkeypatch.setattr(sys, "argv", ["dream-shadow.py", "--quiet", "--no-report"])
    assert ds.main() == 0
    out = capsys.readouterr().out
    assert "0 merge candidate(s)" not in out, (
        f"a scan that never ran was reported as a count: {out.strip()!r}"
    )
    assert "UNAVAILABLE" in out


def test_the_summary_still_counts_a_real_scan(ds, monkeypatch, capsys):
    monkeypatch.setattr(ds, "gather", lambda: {
        "memory_dir": _MEM_DIR, "dormant": [], "merge": _PAIR})
    monkeypatch.setattr(sys, "argv", ["dream-shadow.py", "--quiet", "--no-report"])
    assert ds.main() == 0
    assert "1 merge candidate(s)" in capsys.readouterr().out


def test_a_clean_scan_reports_zero_which_is_true(ds, monkeypatch, capsys):
    """Anchor: zero is the right word when the scan ran and found nothing."""
    monkeypatch.setattr(ds, "gather", lambda: {
        "memory_dir": _MEM_DIR, "dormant": [], "merge": _CLEAN})
    monkeypatch.setattr(sys, "argv", ["dream-shadow.py", "--quiet", "--no-report"])
    assert ds.main() == 0
    assert "0 merge candidate(s)" in capsys.readouterr().out


def test_the_report_never_proposes_removing_a_memory(ds):
    """Anchor on the operator's standing rule: auto-memory is never pruned, so
    this report must keep saying so whatever else changes in it. Both places
    say it, and both are pinned - the dormant section's disclaimer and the
    closing advisory - because a report that keeps one and loses the other
    reads as a worklist for deletion in exactly the section that lists files."""
    text = _report(ds, _PAIR)
    assert "candidate for removal" in text, "the dormant-section disclaimer"
    assert "never mutates memory and never proposes removing a fact" in text, (
        "the closing advisory: 'never mutates' alone still leaves the tool "
        "free to PROPOSE a removal, which is the thing it must not do"
    )


# ===========================================================================
# elicit.py — it must be the catalog, and a typo must be answered
# ===========================================================================

@pytest.fixture(scope="module")
def el():
    return _load("elicit_shard", "scripts/elicit.py")


_CATALOG = ("num,category,method_name,description,output_pattern\n"
            "1,risk,Pre-mortem,Imagine it failed,a -> b\n"
            "2,risk,Red Team,Attack the plan,c -> d\n"
            "3,framing,Reframe,Change the frame,e -> f\n")


def _csv(tmp_path: Path, body: str, name: str = "cat.csv") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_a_csv_that_is_not_the_catalog_is_refused(el, tmp_path):
    """It was served as N methods in a category named ''."""
    with pytest.raises(ValueError) as exc:
        el.load(_csv(tmp_path, "name,email\nAlice,a@x.com\nBob,b@x.com\n"))
    assert "not the elicitation catalog" in str(exc.value)
    assert "category" in str(exc.value), "name the columns that are missing"


def test_the_refusal_shows_the_header_it_did_find(el, tmp_path):
    with pytest.raises(ValueError, match="name, email"):
        el.load(_csv(tmp_path, "name,email\nAlice,a@x.com\n"))


def test_an_empty_file_is_refused_too(el, tmp_path):
    with pytest.raises(ValueError, match="empty file"):
        el.load(_csv(tmp_path, ""))


@pytest.mark.parametrize("header,absent", [
    ("category,description", "method_name"),
    ("category,method_name", "description"),
    ("num,category,output_pattern", "method_name"),
])
def test_a_near_miss_csv_is_refused_on_the_column_it_lacks(el, tmp_path, header,
                                                           absent):
    """The realistic mistake is not `name,email`; it is another CSV that shares
    SOME columns. Checking only `category` would wave all three of these
    through, and a catalog with no `method_name` serves nameless methods -
    the original defect, one column narrower."""
    with pytest.raises(ValueError) as exc:
        el.load(_csv(tmp_path, header + "\nrisk,x,y\n"))
    assert absent in str(exc.value)


def test_the_real_catalog_still_loads(el):
    """Anchor: the shipped file must satisfy the guard."""
    rows = el.load(ROOT / "reference" / "elicitation-methods.csv")
    assert len(rows) > 20
    assert all(r["method_name"] for r in rows)


def test_an_optional_column_may_still_be_absent(el, tmp_path):
    """`num` and `output_pattern` are optional; only the three that carry the
    method's identity are required."""
    rows = el.load(_csv(tmp_path, "category,method_name,description\n"
                                  "risk,Pre-mortem,Imagine it failed\n"))
    assert rows[0]["output_pattern"] == ""
    assert rows[0]["method_name"] == "Pre-mortem"


def test_a_bad_file_exits_two_not_a_traceback(el, tmp_path, capsys):
    rc = el.main(["--file", str(_csv(tmp_path, "a,b\n1,2\n")), "categories"])
    assert rc == 2
    assert "not the elicitation catalog" in capsys.readouterr().err


def test_an_unknown_category_is_named_not_answered_with_silence(el, tmp_path,
                                                                capsys):
    rc = el.main(["--file", str(_csv(tmp_path, _CATALOG)),
                  "list", "--category", "framming"])
    assert rc == 1, "an empty print at exit 0 reads as 'that category is empty'"
    err = capsys.readouterr().err
    assert "framming" in err
    assert "risk" in err and "framing" in err, "show what the real ones are"


def test_a_known_category_still_lists(el, tmp_path, capsys):
    """Anchor: the guard must not refuse a category that exists."""
    rc = el.main(["--file", str(_csv(tmp_path, _CATALOG)),
                  "list", "--category", "risk"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Pre-mortem" in out and "Red Team" in out


def test_list_all_is_unaffected_by_the_category_guard(el, tmp_path, capsys):
    rc = el.main(["--file", str(_csv(tmp_path, _CATALOG)), "list", "--all"])
    assert rc == 0
    assert "Reframe" in capsys.readouterr().out


def test_random_still_refuses_an_unknown_category(el, tmp_path, capsys):
    """Anchor: the behaviour `list` was made to match."""
    rc = el.main(["--file", str(_csv(tmp_path, _CATALOG)),
                  "random", "--category", "framming"])
    assert rc == 1
    assert "no methods match" in capsys.readouterr().err


def test_a_duplicate_method_name_is_reported(el, tmp_path, capsys):
    """`show` would hand back one of two different methods, silently."""
    body = _CATALOG + "4,framing,Pre-mortem,A DIFFERENT method,g -> h\n"
    rows = el.load(_csv(tmp_path, body))
    found, missing = el.find(rows, ["Pre-mortem"])
    err = capsys.readouterr().err
    assert "two catalog rows are named" in err
    assert "'Pre-mortem'" in err
    assert len(found) == 1 and not missing
    # And the warning has to be TRUE. It says "`show` returns the later one",
    # so the code must return the later one: a version that quietly keeps the
    # first row still warns, and now the message is the wrong half of the story.
    assert found[0]["description"] == "A DIFFERENT method", (
        "the warning promises the later row and the code returned the earlier"
    )
    assert found[0]["num"] == "4"


def test_no_warning_when_names_are_unique(el, tmp_path, capsys):
    """Anchor: the shipped catalog has no duplicates and must stay quiet."""
    rows = el.load(_csv(tmp_path, _CATALOG))
    el.find(rows, ["Pre-mortem", "Reframe"])
    assert capsys.readouterr().err == ""


def test_the_shipped_catalog_has_no_duplicate_names(el, capsys):
    rows = el.load(ROOT / "reference" / "elicitation-methods.csv")
    el.find(rows, [])
    assert capsys.readouterr().err == "", "a duplicate landed in the catalog"


def test_a_missing_name_is_still_reported_as_missing(el, tmp_path):
    """Anchor: the dedupe rewrite must not lose the not-found path."""
    rows = el.load(_csv(tmp_path, _CATALOG))
    found, missing = el.find(rows, ["Pre-mortem", "Nonexistent"])
    assert [r["method_name"] for r in found] == ["Pre-mortem"]
    assert missing == ["Nonexistent"]


# ===========================================================================
# draft-critique.py — the right refusal for the right reason
# ===========================================================================

@pytest.fixture(scope="module")
def dc():
    return _load("draft_critique_shard", "scripts/draft-critique.py")


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def daemon(dc, monkeypatch, tmp_path):
    """A reachable daemon whose response body the test chooses."""
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "token").write_text("t0ken", encoding="utf-8")
    (state / "port").write_text("8899", encoding="utf-8")

    def _serve(body: bytes):
        monkeypatch.setattr(dc.urllib.request, "urlopen",
                            lambda req, timeout=None: _Resp(body))
    return tmp_path, _serve


@pytest.mark.parametrize("body", [b"[]", b"null", b'"a string"', b"42",
                                  b'{"error": "nope"}'])
def test_valid_json_of_the_wrong_shape_is_refused(dc, daemon, body, capsys):
    """The ValueError handler catches a body that is not JSON; this one IS."""
    root, serve = daemon
    serve(body)
    with pytest.raises(SystemExit) as exc:
        dc._fetch_card(root, "abc")
    assert exc.value.code == 1
    assert "not an action-queue payload" in capsys.readouterr().err


def test_a_non_dict_item_is_skipped_not_crashed_on(dc, daemon):
    root, serve = daemon
    serve(json.dumps({"items": ["junk", None,
                                {"id": "abc123", "draft_body": "hi"}]}).encode())
    assert dc._fetch_card(root, "abc")["id"] == "abc123"


def test_a_real_payload_still_resolves(dc, daemon):
    """Anchor: the shape guard must not refuse the working case."""
    root, serve = daemon
    serve(json.dumps({"items": [{"id": "abc123", "draft_body": "hi"}]}).encode())
    assert dc._fetch_card(root, "abc123")["id"] == "abc123"


def test_a_body_that_is_not_json_is_still_refused(dc, daemon, capsys):
    """Anchor: the pre-existing handler must survive the new one."""
    root, serve = daemon
    serve(b"<html>proxy error</html>")
    with pytest.raises(SystemExit) as exc:
        dc._fetch_card(root, "abc")
    assert exc.value.code == 1
    assert "not JSON" in capsys.readouterr().err


def test_an_ambiguous_prefix_is_still_refused(dc, daemon, capsys):
    root, serve = daemon
    serve(json.dumps({"items": [{"id": "abc1"}, {"id": "abc2"}]}).encode())
    with pytest.raises(SystemExit):
        dc._fetch_card(root, "abc")
    assert "ambiguous prefix" in capsys.readouterr().err


def test_a_card_with_no_draft_says_so(dc, daemon, monkeypatch, capsys):
    """It fell out at "model unavailable, missing API key, or empty draft
    body" - three causes and the real one absent, sending the operator to
    check an API key over a card that was never a draft."""
    root, serve = daemon
    serve(json.dumps({"items": [{"id": "note0001",
                                 "action_type": "pipeline_update"}]}).encode())
    monkeypatch.setattr(dc, "get_workspace_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["draft-critique.py", "note0001"])

    called = []
    monkeypatch.setattr(dc.draft_critique, "critique_draft",
                        lambda *a, **k: called.append(1))

    assert dc.main() == 1
    err = capsys.readouterr().err
    assert "carries no draft body" in err
    assert "pipeline_update" in err, "name the card type that has no draft"
    assert "missing API key" not in err, "the old message blamed three innocents"
    assert not called, "and it must not spend a model call to find that out"


def test_a_card_with_a_draft_still_reaches_the_critic(dc, daemon, monkeypatch,
                                                      capsys):
    """Anchor: the early exit must not swallow a real draft."""
    root, serve = daemon
    serve(json.dumps({"items": [{"id": "mail0001", "action_type": "email_send",
                                 "draft_body": "Hello there.",
                                 "subject": "S", "to": "a@b.c"}]}).encode())
    monkeypatch.setattr(dc, "get_workspace_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["draft-critique.py", "mail0001", "--json"])
    monkeypatch.setattr(dc.draft_critique, "critique_draft",
                        lambda subject, body, to, model=None: {
                            "risk": "low", "summary": "fine", "flags": [],
                            "model": "haiku"})
    assert dc.main() == 0
    assert json.loads(capsys.readouterr().out)["risk"] == "low"
