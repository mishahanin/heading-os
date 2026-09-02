"""Four dashboard panels that drew a fact nothing had measured.

All four live in `scripts/generate-dashboard.py`, and all four share a shape:
the page prints something confident where the code knows less than the print
implies.

* `_as_percent` clamps with `max(0.0, min(100.0, value))` and never asks
  whether the value is FINITE. Both builtins are order-dependent around NaN,
  because every comparison against NaN is False: `min(100.0, nan)` keeps its
  first operand and hands back 100.0, and `max(0.0, 100.0)` then draws a
  perfect score. MEASURED on the current tree before the fix:

      value      rendered   stderr
      nan        100.0      nothing
      inf        100.0      nothing
      -inf       0.0        nothing

  `json.loads` accepts the bare tokens `NaN`, `Infinity` and `-Infinity` by
  default, so a truncated or corrupt viraid `state.json` reaches this by the
  ordinary door rather than an exotic one. Of the four findings here it is the
  only one that puts a WRONG NUMBER in front of a person, and it puts the best
  possible one there, silently.

* The content-cadence dots were a two-way ternary beside a three-way caption.
  `nl_status` and `li_status` each carry ON TRACK, BEHIND and NO DATA; the
  colour variables were given all three states in an earlier pass and the dots
  were not, so `{'g' if status == 'ON TRACK' else 'r'}` drew a RED dot next to
  a grey "NO DATA" label. The same fact, on the same card, in two colours, and
  the red one is a verdict on a cadence nothing had looked at.

* `build_viraid` computed `rate_str` inside the `else` of
  `if not viraid.get("tasks_read")`. The comment directly above that branch
  already said the old code "threw away `completion_rate`, which comes from a
  DIFFERENT file and may have been read perfectly well" - and the fix had
  landed on only one half of that sentence. A missing `tasks.md` with a
  perfectly readable `state.json` still rendered "No Viraid tasks data
  available" and discarded the measured rate.

* The per-contact CRM handler raised inside itself. It catches `AttributeError`
  and then calls `c.get('name', '?')` in the message, which is the very
  operation that raises for a record that is not a mapping. The second
  `AttributeError` escaped to the broad `except Exception` below, which set
  `result["failed"]` and returned the EMPTY skeleton, so one non-mapping record
  emptied every bucket. That is the exact outcome the comment at the top of the
  loop says is fixed: "A bad contact is now dropped alone, and named."

Run: .venv/bin/python -m pytest tests/test_a_clamp_that_read_a_corrupt_number_as_a_perfect_score.py
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DASHBOARD_SRC = ROOT / "scripts" / "generate-dashboard.py"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gd():
    return _load("dashboard_corrupt_clamp", "scripts/generate-dashboard.py")


# ============================================================
# 1. A clamp that could not refuse NaN or an infinity
# ============================================================

@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"),
    "nan", "inf", "-inf",           # the string branch coerces to the same floats
])
def test_a_non_finite_completion_rate_is_not_a_measurement(gd, value, capsys):
    """0%, and a line on stderr. Never 100%, and never in silence.

    0.0 is the same answer every other refusal in this function gives, and it
    is the safe direction: the panel already renders a rate it could not read
    as "-" when `rate_known` is False, and a rate it read as garbage should not
    outrank one it never read at all.
    """
    assert gd._as_percent(value) == 0.0
    err = capsys.readouterr().err
    assert "completion_rate" in err, "a refused value must say so"


def test_the_clamp_order_is_why_nan_scored_a_hundred(gd):
    """The mechanism, pinned, because it is the non-obvious part.

    Nothing here calls the dashboard. It records that `min`/`max` silently
    return the OTHER operand when one is NaN, which is what made a corrupt
    figure render as the best possible one rather than as an error.
    """
    assert min(100.0, float("nan")) == 100.0
    assert max(0.0, float("nan")) == 0.0
    assert math.isnan(min(float("nan"), 100.0))


def test_a_nan_in_state_json_never_reaches_the_page(gd, tmp_path, monkeypatch):
    """End to end, through the reader that actually runs.

    `json` parses the bare token `NaN`, so this is the ordinary shape of a
    corrupt stats block, not a hand-built float.
    """
    tasks = tmp_path / "tasks.md"
    state = tmp_path / "state.json"
    tasks.write_text("## Active\n- [ ] `P1` | do a thing\n", encoding="utf-8")
    state.write_text('{"stats": {"completion_rate": NaN}}', encoding="utf-8")
    monkeypatch.setattr(gd, "viraid_tasks_file", lambda p=tasks: p)
    monkeypatch.setattr(gd, "viraid_state_file", lambda p=state: p)

    assert math.isnan(json.loads(state.read_text())["stats"]["completion_rate"]), (
        "the fixture must actually carry a NaN, or this test proves nothing")

    result = gd.collect_viraid()
    assert result["completion_rate"] == 0.0
    assert "100%" not in gd.build_viraid(result)


def test_a_real_percentage_still_survives_the_finiteness_check(gd):
    """The negative case: the guard must not eat ordinary values."""
    assert gd._as_percent(87) == 87.0
    assert gd._as_percent(0.87) == pytest.approx(87.0)
    assert gd._as_percent(150) == 100.0


# ============================================================
# 2. A dot with two states beside a caption with three
# ============================================================

def _cadence(nl_status, li_status):
    return {
        "newsletter_days": None if nl_status == "NO DATA" else 2,
        "newsletter_status": nl_status,
        "newsletter_last": None if nl_status == "NO DATA" else "2026-05-10",
        "linkedin_count_week": 0 if li_status == "NO DATA" else 3,
        "linkedin_status": li_status,
    }


def test_no_data_draws_neither_the_green_dot_nor_the_red_one(gd):
    """A grey caption beside a red dot is the page disagreeing with itself."""
    html = gd.build_content_cadence(_cadence("NO DATA", "NO DATA"))
    # The closing quote is part of every needle here: 'heading-dot g' is a
    # PREFIX of 'heading-dot gray', so the loose form counts the grey dots as
    # green ones and the test passes on the defect it is about.
    assert 'heading-dot r"' not in html, (
        "NO DATA drew the BEHIND dot, next to a grey NO DATA caption")
    assert html.count('heading-dot gray"') == 2


def test_behind_still_draws_the_red_dot(gd):
    """The negative case. A measured miss must stay red."""
    html = gd.build_content_cadence(_cadence("BEHIND", "BEHIND"))
    assert html.count('heading-dot r"') == 2
    assert 'heading-dot gray"' not in html


def test_on_track_still_draws_the_green_dot(gd):
    html = gd.build_content_cadence(_cadence("ON TRACK", "ON TRACK"))
    assert html.count('heading-dot g"') == 2


def test_one_half_may_be_grey_while_the_other_is_measured(gd):
    """The two halves read different sources, so they band independently."""
    html = gd.build_content_cadence(_cadence("ON TRACK", "NO DATA"))
    assert html.count('heading-dot g"') == 1
    assert html.count('heading-dot gray"') == 1
    assert 'heading-dot r"' not in html


# ============================================================
# 3. A completion rate discarded by the panel next door
# ============================================================

def _viraid(tasks_read, rate_known, rate=87.0):
    return {"active_total": 0, "p1": 0, "p2": 0, "p3": 0, "aging": 0,
            "completion_rate": rate,
            "tasks_read": tasks_read, "rate_known": rate_known}


def test_a_measured_rate_survives_an_unread_tasks_file(gd):
    """The two sources are two files. One being absent does not unmeasure the
    other, and `collect_viraid` already reports them separately."""
    html = gd.build_viraid(_viraid(tasks_read=False, rate_known=True))
    assert "87%" in html, (
        "a completion rate read from state.json was discarded because "
        "tasks.md was missing")


def test_an_unread_tasks_file_still_says_the_counts_are_unknown(gd):
    """Showing the rate must not smuggle in five zeroes nothing counted."""
    html = gd.build_viraid(_viraid(tasks_read=False, rate_known=True))
    assert "unknown, not zero" in html
    assert ">0<" not in html, "an unread tasks.md must not print measured zeroes"


def test_both_sources_unread_is_still_one_plain_sentence(gd):
    """Nothing read means nothing to draw, and that branch is unchanged."""
    html = gd.build_viraid(_viraid(tasks_read=False, rate_known=False))
    assert "No Viraid tasks data available" in html
    assert "metrics-strip" not in html


def test_a_read_tasks_file_still_prints_its_zeroes(gd):
    """The negative case, pinned by the panel's older finding: a file read to
    the end with nothing outstanding reports measured zeroes, not dashes."""
    html = gd.build_viraid(_viraid(tasks_read=True, rate_known=True))
    assert ">0<" in html
    assert "unknown, not zero" not in html
    assert "87%" in html


# ============================================================
# 4. A handler that raised the same error it had just caught
# ============================================================

def _good_contact(name="Vesper Lynd"):
    return {"name": name, "company": "Universal", "type": "partner",
            "last_touch": "2026-01-01", "cadence": 30, "health": "red",
            "days_since": 40, "commitments": [], "file": "v.md"}


@pytest.mark.parametrize("bad", ["not-a-dict", 17, None, ["a", "list"]])
def test_a_non_mapping_record_is_dropped_alone_and_named(gd, monkeypatch,
                                                         capsys, bad):
    """The handler called `c.get(...)` to name the record, which is exactly the
    call that raised for a record with no `.get`. The second AttributeError
    escaped to the broad `except Exception`, which set `failed` and returned
    the empty skeleton, so ONE bad record emptied every bucket."""
    monkeypatch.setattr(gd, "_crm_parse_config", lambda p: {})
    monkeypatch.setattr(gd, "_crm_scan_contacts",
                        lambda cfg, today=None: ([bad, _good_contact()],
                                                 [], [], [], []))
    result = gd.collect_crm_health()
    assert result["failed"] == "", (
        "a single malformed record marked the WHOLE scan failed")
    assert [c["name"] for c in result["contacts"]] == ["Vesper Lynd"]
    assert result["total"] == 1
    assert len(result["red"]) == 1
    assert "malformed contact" in capsys.readouterr().err


def test_the_surviving_contact_still_reaches_the_page(gd, monkeypatch):
    """The buckets are what the panel renders, so assert the rendered page and
    not only the dict: an empty skeleton draws the CRM failure card."""
    monkeypatch.setattr(gd, "_crm_parse_config", lambda p: {})
    monkeypatch.setattr(gd, "_crm_scan_contacts",
                        lambda cfg, today=None: (["not-a-dict",
                                                  _good_contact()],
                                                 [], [], [], []))
    html = gd.build_urgent(gd.collect_crm_health())
    assert "Vesper Lynd" in html
    assert "CRM Data Unavailable" not in html


def test_a_scan_that_really_fails_still_sets_the_flag(gd, monkeypatch):
    """The negative case. Widening the per-contact handler must not swallow a
    genuine scan failure, which has to stay loud."""
    def _boom(path):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(gd, "_crm_parse_config", _boom)
    result = gd.collect_crm_health()
    assert "RuntimeError" in result["failed"]
