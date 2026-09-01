"""Shard `scripts-11-p2`: a check that crashed, and a brief that looked clean.

`prime-health-parallel.py` runs /prime's health checks and renders them. Two of
them, `run_odin_cadence` and `run_ops_radar`, returned their child's stdout and
set `omit_if_empty: True` unconditionally. `render_text` honours that flag
BEFORE it ever consults `status`, so a non-zero child exit with empty stdout
lost the banner, the failure and the captured stderr together: session boot
rendered a clean brief over a check that had died. Both child scripts only ever
`return 0`, so a non-zero exit IS an uncaught exception, which writes the
traceback to stderr and leaves stdout empty. That is precisely the shape that
disappeared.

`run_all._wrap` was never the hole. It catches `TimeoutExpired` and `Exception`
and returns a non-empty output with no `omit_if_empty`, so those DO render. The
repo also already carried both the fix and the test shape: the same defect was
found for `run_dream_shadow` and is pinned by
`tests/test_a_scan_that_never_ran_reported_nothing_to_do.py`. These two checks
were simply left out of that pass.

The rest of the shard:

  - `run_ops_radar` blanked its panel on `"all clear" in out`, searching every
    line of the detailed view. One due item whose summary carried that phrase
    silenced the whole panel, so the brief said nothing while something was due.
  - `render_text` let `omit_if_empty` hide a FAILING check. Both callers are
    fixed at their source, and the renderer now refuses the omission too, so the
    next check added there cannot reintroduce the disappearance.
  - `promote-knowledge.py` claimed in a comment that its promotion marker was
    idempotent. The guard compared the whole date-stamped string, so it
    deduplicated a SAME-DAY re-run and nothing else. The recovery path is a
    next-day re-run, which stacked a second "Promoted to corporate" footer, and
    the two disagreed about when the note was shared - in the file that IS the
    audit trail for the promotion.
  - The same recovery path told the operator to "re-run after resolving". The
    target had already been written before the push, so a plain re-run hits
    "Target already exists" and never reaches the push. The advice could not
    work without `--overwrite`, which it did not mention.
  - `polymarket.py` matched `--keywords` with a plain substring test, six lines
    below its own `_term_in` docstring recording that a plain substring test
    fires on short entries inside unrelated words. "stock" matched Woodstock, so
    the disambiguator passed in to NARROW an ambiguous topic let through exactly
    the markets it existed to exclude.
  - `match_whitelist` stopped at the first matching category. On "Trump AI
    policy" it returned `["ai"]` alone, and `filter_markets` then dropped every
    Trump election market from a brief the topic named. The docstring already
    promised "the terms that actually fired", and "trump" did fire.

Fixed 2026-08-25.
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources  # noqa: E402


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# prime-health-parallel: a crashed child must reach the brief
# ===========================================================================

@pytest.fixture
def ph():
    return _load("ph_11p2", "scripts/prime-health-parallel.py")


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    """A `subprocess` shim returning one canned CompletedProcess."""
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["stub"], returncode=returncode, stdout=stdout, stderr=stderr)
    return types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)


def _workspace(tmp_path: Path, *names: str) -> Path:
    """A workspace root whose scripts/ holds the named (empty) scripts."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for n in names:
        (scripts / n).write_text("", encoding="utf-8")
    return tmp_path


_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "odin-cadence.py", line 1, in <module>\n'
    "ValueError: cadence store unreadable"
)


def test_a_crashed_cadence_check_is_not_erased(ph, tmp_path, monkeypatch):
    """The headline finding: non-zero exit, empty stdout, nothing rendered."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", _TRACEBACK))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    assert res["output"], "a crashed check rendered nothing at all"
    assert res["omit_if_empty"] is False
    assert res["status"] not in ph.NON_FAILURE_STATUSES


def test_the_cadence_failure_names_the_exit_code(ph, tmp_path, monkeypatch):
    """"Something failed" is not actionable; the exit code is."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(3, "", _TRACEBACK))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    assert "3" in res["output"]
    assert "odin-cadence" in res["output"]


def test_the_cadence_traceback_is_still_carried(ph, tmp_path, monkeypatch):
    """`render_text` prints stderr only for a failing status, so keep it."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", _TRACEBACK))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    assert "cadence store unreadable" in res["stderr"]


def test_a_crashed_radar_check_is_not_erased(ph, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "subprocess", _fake_run(2, "", "boom"))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert res["output"]
    assert res["omit_if_empty"] is False
    assert res["status"] not in ph.NON_FAILURE_STATUSES
    assert "2" in res["output"]


def test_a_quiet_cadence_run_is_still_silent(ph, tmp_path, monkeypatch):
    """Anchor: /prime must not grow a line that fires every session."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(0, "", ""))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    assert res["output"] == ""
    assert res["status"] == "ok"
    assert res["omit_if_empty"] is True


def test_a_real_cadence_nudge_still_reaches_the_brief(ph, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "subprocess", _fake_run(0, "odin: 4 episodes to collect\n"))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    assert res["output"] == "odin: 4 episodes to collect"
    assert res["status"] == "ok"


def test_an_absent_cadence_script_is_still_skipped(ph, tmp_path, monkeypatch):
    """Exec workspaces have no ceo-only script; the section must stay absent."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", "should never run"))
    res = ph.run_odin_cadence(_workspace(tmp_path))
    assert res["status"] == "skipped"
    assert res["output"] == ""
    assert res["omit_if_empty"] is True


def test_an_absent_radar_script_is_still_skipped(ph, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", "should never run"))
    res = ph.run_ops_radar(_workspace(tmp_path))
    assert res["status"] == "skipped"
    assert res["output"] == ""


# ---------------------------------------------------------------------------
# the "all clear" test that searched every line
# ---------------------------------------------------------------------------

def test_an_all_clear_radar_still_omits_the_panel(ph, tmp_path, monkeypatch):
    """Anchor: the quiet-when-healthy behaviour is the point of the check."""
    monkeypatch.setattr(
        ph, "subprocess", _fake_run(0, "ops-radar: all clear - nothing due.\n"))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert res["output"] == ""
    assert res["omit_if_empty"] is True


def test_an_all_clear_radar_in_crunch_mode_still_omits_the_panel(ph, tmp_path,
                                                                monkeypatch):
    """render_detailed inserts ` [CRUNCH]` before the colon."""
    monkeypatch.setattr(
        ph, "subprocess",
        _fake_run(0, "ops-radar [CRUNCH]: all clear - nothing due.\n"))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert res["output"] == ""


def test_a_due_item_mentioning_all_clear_is_not_blanked(ph, tmp_path, monkeypatch):
    """The finding: the substring test reached into the detailed view's body."""
    detailed = (
        "ops-radar: 1 item(s) due\n"
        "  [ warning] sentinel queue is not all clear - 6 items unread\n"
    )
    monkeypatch.setattr(ph, "subprocess", _fake_run(0, detailed))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert "1 item(s) due" in res["output"], (
        "a due item was silenced because its own summary carried the phrase "
        "the all-clear test looks for"
    )


def test_the_all_clear_sentence_is_tested_on_the_first_line(ph, tmp_path,
                                                            monkeypatch):
    """render_detailed opens with the count, so only the FIRST line can be the
    all-clear sentence. A due item quoting it last must not blank the panel."""
    detailed = (
        "ops-radar: 1 item(s) due\n"
        "  [ warning] radar note: sentinel is all clear - nothing due.\n"
    )
    monkeypatch.setattr(ph, "subprocess", _fake_run(0, detailed))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert "1 item(s) due" in res["output"]


def test_ordinary_due_items_still_render(ph, tmp_path, monkeypatch):
    detailed = "ops-radar: 2 item(s) due\n  [critical] backup is 9 days old\n"
    monkeypatch.setattr(ph, "subprocess", _fake_run(0, detailed))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    assert "backup is 9 days old" in res["output"]


def test_the_all_clear_sentence_the_radar_prints_is_the_one_tested(ph):
    """Two files, one literal, and only this test holds them in step."""
    radar = (ROOT / "scripts" / "ops-radar.py").read_text(encoding="utf-8")
    health = (ROOT / "scripts" / "prime-health-parallel.py").read_text(
        encoding="utf-8")
    assert "all clear - nothing due." in radar
    assert "all clear - nothing due." in health, (
        "prime-health stopped looking for the sentence ops-radar prints; the "
        "panel would fire at every session boot with nothing due"
    )


# ---------------------------------------------------------------------------
# render_text: the second gate
# ---------------------------------------------------------------------------

def _render_one(ph, key: str, res: dict) -> str:
    return ph.render_text({key: res})


def test_render_text_refuses_to_omit_a_failing_check(ph):
    """Structural: the next check added here cannot vanish the same way."""
    out = _render_one(ph, "odin_cadence", {
        "status": "error", "output": "", "stderr": "boom", "omit_if_empty": True})
    assert "### 2.14 Odin Cadence" in out
    assert "[stderr] boom" in out


def test_render_text_still_omits_a_healthy_empty_check(ph):
    """Anchor: quiet-when-healthy is what `omit_if_empty` is for."""
    out = _render_one(ph, "odin_cadence", {
        "status": "ok", "output": "", "omit_if_empty": True})
    assert "2.14" not in out


def test_render_text_still_omits_a_skipped_check(ph):
    """An exec workspace has no ceo-only script and must leak no reference."""
    out = _render_one(ph, "ops_radar", {
        "status": "skipped", "output": "", "omit_if_empty": True})
    assert "Ops-Radar" not in out


def test_the_fixed_cadence_result_actually_renders(ph, tmp_path, monkeypatch):
    """End to end: the check's own return value, through the real renderer."""
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", _TRACEBACK))
    res = ph.run_odin_cadence(_workspace(tmp_path, "odin-cadence.py"))
    out = _render_one(ph, "odin_cadence", res)
    assert "### 2.14 Odin Cadence" in out
    assert "cadence store unreadable" in out


def test_the_fixed_radar_result_actually_renders(ph, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "subprocess", _fake_run(1, "", "radar exploded"))
    res = ph.run_ops_radar(_workspace(tmp_path, "ops-radar.py"))
    out = _render_one(ph, "ops_radar", res)
    assert "### 2.15 Ops-Radar" in out
    assert "radar exploded" in out


# ===========================================================================
# promote-knowledge: one marker, not a stack
# ===========================================================================

@pytest.fixture
def pk():
    return _load("pk_11p2", "scripts/promote-knowledge.py")


_NOTE = "---\ntitle: A note\nstatus: seedling\n---\n\nBody line one.\n"


def _marker(date: str, ktype: str = "signals", name: str = "note.md") -> str:
    return (f"\n\n---\n\n> **Promoted to corporate** on {date} "
            f"-- shared/{ktype}/{name}\n")


def test_a_marker_from_an_earlier_day_is_removed(pk):
    text = _NOTE.rstrip("\n") + _marker("2026-08-24")
    assert "Promoted to corporate" not in pk.strip_promotion_markers(text)


def test_the_note_body_survives_the_strip_byte_for_byte(pk):
    text = _NOTE.rstrip("\n") + _marker("2026-08-24")
    assert pk.strip_promotion_markers(text) == _NOTE.rstrip("\n")


def test_a_note_that_was_never_promoted_is_untouched(pk):
    assert pk.strip_promotion_markers(_NOTE) == _NOTE


def test_an_ordinary_horizontal_rule_survives(pk):
    """The pattern requires the marker sentence right after the rule."""
    text = "Intro paragraph.\n\n---\n\nA second section, hand written.\n"
    assert pk.strip_promotion_markers(text) == text


def test_the_frontmatter_delimiters_survive(pk):
    assert pk.strip_promotion_markers(_NOTE).startswith("---\ntitle: A note")


def test_a_marker_for_a_different_type_is_also_removed(pk):
    """One marker per note, whatever the earlier run promoted it as."""
    text = _NOTE.rstrip("\n") + _marker("2026-08-24", ktype="research")
    assert "Promoted to corporate" not in pk.strip_promotion_markers(text)


def test_two_stacked_markers_both_go(pk):
    """The state a pre-fix workspace is already in must be recoverable."""
    text = (_NOTE.rstrip("\n") + _marker("2026-08-24") + _marker("2026-08-25"))
    assert pk.strip_promotion_markers(text) == _NOTE.rstrip("\n")


def test_prose_quoting_the_marker_format_survives(pk):
    """A note DOCUMENTING the promotion format is not a promoted note. The
    pattern is anchored on the rule and the block quote for exactly this: a
    looser one eats a sentence a knowledge note wrote on purpose."""
    text = ("The promoter appends: **Promoted to corporate** on 2026-01-01 "
            "-- shared/signals/example.md\n")
    assert pk.strip_promotion_markers(text) == text


def test_the_marker_regex_matches_what_main_writes(pk):
    """A pattern that drifts from the writer silently stops deduplicating."""
    src = (ROOT / "scripts" / "promote-knowledge.py").read_text(encoding="utf-8")
    assert 'f"\\n\\n---\\n\\n> **Promoted to corporate** on {today} "' in src, (
        "the marker literal moved; re-derive PROMOTION_MARKER_RE against it"
    )


# ---------------------------------------------------------------------------
# the whole promotion, twice, on two different days
# ---------------------------------------------------------------------------

def _promote(pk, monkeypatch, tmp_path, note: Path, day: str, *, overwrite=False):
    corp = tmp_path / "corp"
    (corp / "knowledge" / "shared" / "signals").mkdir(parents=True, exist_ok=True)
    fixed = dt.datetime(2026, 8, int(day[-2:]), 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(pk, "validate_admin", lambda: True)
    monkeypatch.setattr(pk, "get_corporate_repo_path", lambda: corp)
    monkeypatch.setattr(pk, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(pk, "git_commit_and_push", lambda *a, **k: None)
    monkeypatch.setattr(
        pk, "datetime", types.SimpleNamespace(now=lambda _tz=None: fixed))
    argv = ["promote-knowledge.py", "--note", str(note), "--type", "signals"]
    if overwrite:
        argv.append("--overwrite")
    monkeypatch.setattr(sys, "argv", argv)
    pk.main()
    return corp


def test_a_next_day_rerun_leaves_exactly_one_marker(pk, tmp_path, monkeypatch):
    """The finding: the guard compared a date-stamped string, so it only ever
    deduplicated a same-day re-run - and the recovery path is the NEXT day."""
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-25", overwrite=True)
    body = note.read_text(encoding="utf-8")
    assert body.count("Promoted to corporate") == 1, (
        f"two footers disagree about when the note was shared:\n{body}"
    )


def test_the_surviving_marker_carries_the_latest_date(pk, tmp_path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-25", overwrite=True)
    body = note.read_text(encoding="utf-8")
    assert "on 2026-08-25" in body
    assert "on 2026-08-24" not in body


def test_a_same_day_rerun_is_a_byte_for_byte_no_op(pk, tmp_path, monkeypatch):
    """Anchor: the one case the old guard did handle must keep working."""
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    first = note.read_bytes()
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-24", overwrite=True)
    assert note.read_bytes() == first


def test_the_first_promotion_still_marks_the_original(pk, tmp_path, monkeypatch):
    """Anchor: marked-but-not-promoted is the deliberate crash-safe order."""
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    body = note.read_text(encoding="utf-8")
    assert "> **Promoted to corporate** on 2026-08-24" in body
    assert "shared/signals/note.md" in body


def test_the_promoted_copy_still_lands_in_the_corporate_repo(pk, tmp_path,
                                                            monkeypatch):
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    corp = _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    target = corp / "knowledge" / "shared" / "signals" / "note.md"
    assert target.exists()
    assert "promoted_date: 2026-08-24" in target.read_text(encoding="utf-8")


def test_the_promoted_copy_carries_no_marker(pk, tmp_path, monkeypatch):
    """The marker is the SOURCE's audit trail; the shared copy is the note."""
    note = tmp_path / "note.md"
    note.write_text(_NOTE, encoding="utf-8")
    corp = _promote(pk, monkeypatch, tmp_path, note, "2026-08-24")
    target = corp / "knowledge" / "shared" / "signals" / "note.md"
    assert "Promoted to corporate" not in target.read_text(encoding="utf-8")


def test_the_recovery_advice_names_the_flag_that_makes_it_work(pk):
    """The target is written BEFORE the push, so a plain re-run is refused.

    SCOPED TO THE HANDLER 2026-08-30. This read
    `src.split("git commit/push failed")[1]` -- the entire remainder of the file
    after the marker -- so ANY later `--overwrite` satisfied it, including the
    `add_argument("--overwrite")` definition. Removing the flag from the advice
    while leaving it anywhere below kept the test green, which is precisely the
    defect the test exists to pin. The `except` block that prints the message is
    located in the syntax tree and only ITS string constants are read.
    """
    src = (ROOT / "scripts" / "promote-knowledge.py").read_text(encoding="utf-8")
    handlers = [node for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.ExceptHandler)
                and "git commit/push failed" in ast.unparse(node)]
    assert len(handlers) == 1, (
        f"expected exactly one push-failure handler, found {len(handlers)}")
    printed = " ".join(
        node.value for node in ast.walk(handlers[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, str))
    assert "--overwrite" in printed, (
        "the push-failure message tells the operator to re-run, and a plain "
        f"re-run dies at 'Target already exists'. It says: {printed!r}"
    )


# ===========================================================================
# polymarket: a disambiguator that did not disambiguate
# ===========================================================================

@pytest.fixture
def pm():
    return _load("pm_11p2", "scripts/polymarket.py")


def _market(question: str, volume: float = 1_000_000.0) -> dict:
    return {"question": question, "volume": volume,
            "outcomes": '["Yes","No"]', "outcomePrices": '["0.6","0.4"]',
            "slug": "s"}


def test_a_keyword_no_longer_matches_inside_a_longer_word(pm):
    """The finding: `kw in question` fired on Woodstock for `--keywords stock`."""
    # The question must carry the TOPIC needle too, or the earlier gate drops it
    # and the keyword test is never reached.
    got = pm.filter_markets(
        [_market("Will Apple headline Woodstock 2027?")],
        topic="Apple", keywords=["stock"], min_volume_usd=0,
        match_terms=["apple"])
    assert got == [], "the narrowing keyword matched inside an unrelated word"


def test_a_keyword_still_matches_a_real_whole_word(pm):
    got = pm.filter_markets(
        [_market("Will Apple stock close above 300?")],
        topic="Apple", keywords=["stock"], min_volume_usd=0,
        match_terms=["apple"])
    assert len(got) == 1


def test_a_symbol_prefixed_keyword_can_match_at_all(pm):
    """`\\b$aapl\\b` asserts a word char before the `$`, so it never matched and
    the keyword silently excluded every market instead of narrowing them."""
    got = pm.filter_markets(
        [_market("Will Apple ($AAPL) close above 300?")],
        topic="Apple", keywords=["$aapl"], min_volume_usd=0,
        match_terms=["apple"])
    assert len(got) == 1


def test_the_short_term_lesson_still_holds(pm):
    """Anchor: `ai` must not match Bahrain, which is why `_term_in` exists."""
    assert pm._term_in("ai", "bahrain telecom bid") is False
    assert pm._term_in("ai", "ai agents in 2027") is True


def test_a_term_at_the_start_of_a_longer_word_is_rejected(pm):
    """Both boundaries are load-bearing; this one pins the trailing side."""
    assert pm._term_in("ai", "aid package approved") is False


def test_a_term_at_the_end_of_a_longer_word_is_rejected(pm):
    """And this one pins the leading side."""
    assert pm._term_in("ai", "thai election poll") is False


def test_a_keyword_list_still_excludes_an_unrelated_market(pm):
    """Anchor: whole-word matching must not become match-everything."""
    got = pm.filter_markets(
        [_market("Will Apple ship a foldable?")],
        topic="Apple", keywords=["stock"], min_volume_usd=0,
        match_terms=["apple"])
    assert got == []


def test_the_volume_floor_still_drops_a_thin_market(pm):
    """Anchor: the keyword change must not reorder the filter's other gates."""
    got = pm.filter_markets(
        [_market("Will Apple stock close above 300?", volume=10.0)],
        topic="Apple", keywords=["stock"], min_volume_usd=10_000,
        match_terms=["apple"])
    assert got == []


# ---------------------------------------------------------------------------
# a cross-domain topic keeps every term that fired
# ---------------------------------------------------------------------------

def test_a_cross_domain_topic_keeps_terms_from_every_category(pm):
    """The finding: the loop broke at the first category, so half the terms
    that fired were discarded before `filter_markets` ever saw them."""
    _cat, _neg, terms = pm.match_whitelist("Trump AI policy")
    assert "ai" in terms
    assert "trump" in terms, "the election term fired and was thrown away"


def test_the_reported_category_is_still_the_first_one(pm):
    """Contract anchor: `whitelist_match` is single-valued and two SKILL.md
    files read it, so collecting more terms must not change it."""
    cat, _neg, _terms = pm.match_whitelist("Trump AI policy")
    assert cat == "ai_big_tech"


def test_the_dropped_term_now_reaches_the_market_filter(pm):
    """End to end: the market the old narrowing removed from the brief."""
    _cat, _neg, terms = pm.match_whitelist("Trump AI policy")
    got = pm.filter_markets(
        [_market("Will Trump win the 2028 primary?")],
        topic="Trump AI policy", keywords=None, min_volume_usd=0,
        match_terms=terms)
    assert len(got) == 1


def test_a_single_category_topic_is_unchanged(pm):
    """Anchor: the common case must return exactly what it always did."""
    cat, _neg, terms = pm.match_whitelist("bitcoin etf")
    assert cat == "crypto"
    assert sorted(terms) == ["bitcoin", "etf"]


def test_a_topic_outside_the_whitelist_still_skips(pm):
    """Anchor: dropping the `break` must not make everything match."""
    cat, neg, terms = pm.match_whitelist("sovereign telecom procurement")
    assert cat is None
    assert terms == []
    assert neg is True


def test_an_unknown_topic_is_distinguishable_from_a_suppressed_one(pm):
    """Anchor: `negative_match` is the field that tells the two apart."""
    res = pm.query_polymarket("municipal drainage tenders")
    assert res["skip_reason"] == "outside_whitelist"
    assert res["negative_match"] is False


# ===========================================================================
# A deprecation the suite was already printing
# ===========================================================================

def test_no_python_file_carries_an_invalid_escape_sequence():
    r"""Found by this shard's own suite run, not by the shard.

    `scripts/pencil-export.py` opened with `\<` inside a plain (non-raw)
    docstring. Python 3.12 warns on an unrecognised escape and the deprecation
    is on the path to becoming a SyntaxError, so the file would stop importing.
    The rendered text was already wrong for a second reason: `\\wsl` collapsed
    to one backslash, and a UNC path takes two, so the docstring named a host
    that does not exist.

    One file matched across scripts/, tests/ and .claude/hooks/, so this guard
    needs no grandfathered baseline. Keep it that way.
    """
    import warnings

    offenders = []
    roots = [ROOT / "scripts", ROOT / "tests", ROOT / ".claude" / "hooks"]
    # SCAN: a .py file that vanished between the rglob and the read carries no
    # escape sequence to warn about, so skipping it is correct. `read_sources`
    # warns naming it so the narrowing is not silent.
    vanished: list[Path] = []
    for root in roots:
        for path, src in read_sources(sorted(root.rglob("*.py")), vanished):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    compile(src, str(path), "exec")
                except SyntaxError:
                    # Not this guard's job; the import-time suite catches those.
                    continue
            for entry in caught:
                if "escape sequence" in str(entry.message):
                    rel = path.relative_to(ROOT)
                    offenders.append(f"{rel}:{entry.lineno} {entry.message}")
    assert offenders == [], (
        "an unrecognised escape is a deprecation today and a SyntaxError "
        f"later; make the string raw or double the backslash "
        f"({len(vanished)} file(s) vanished mid-walk):\n"
        + "\n".join(offenders)
    )
