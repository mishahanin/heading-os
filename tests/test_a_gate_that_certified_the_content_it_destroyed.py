#!/usr/bin/env python3
"""Four reads that worked from the wrong surface, audited and re-measured 2026-08-31.

Each of the four asked its question of something one step away from the value it
meant: the whole envelope instead of the one entry leaving it, the regex's match
count instead of the markers actually present, the raw markdown line instead of
its parsed cells, the object key instead of the escaped object key. All four are
silent, and three of them report success while doing the wrong thing.

1. `scripts/calibrate.py:apply_truncation`, quadratic shedding with an unbounded
   input. All three shed loops called `envelope_bytes(envelope)` per dropped
   entry, and that RE-SERIALIZES the whole envelope, so the shed cost one full
   serialization per entry: O(n squared) in envelope size.
   MEASURED on synthetic envelopes: 0.12 MB / 0.100 s / 483 serializations,
   0.24 MB / 0.528 s / 983, 0.49 MB / 1.945 s / 1983, 0.98 MB / 8.312 s / 3983.
   Doubling the input quadrupled the runtime, which is clean O(n squared). After
   the fix, the identical fixtures: 0.003 s, 0.008 s, 0.012 s, 0.023 s, and TWO
   serializations at every size, with byte-identical output (4839, 4839, 4840,
   4840 both before and after).

   Reachability is exact and live, not theoretical: `/calibrate` with no
   `--session` calls `locate_session`, which takes the newest `.jsonl` by mtime,
   and that file on this workspace was 249,961,421 bytes on the day of the
   measurement. Extrapolating the measured curve puts a bare `/calibrate` in an
   unbounded hang rather than a slow run.

   Invisible because `tests/test_the_calibrate_envelope_keeps_what_it_promises.py`
   and `tests/test_a_shed_that_dropped_the_newer_turn.py` both assert the RESULT
   SIZE and never the runtime or the input scale, and a correct result is exactly
   what the quadratic loop produces.

   The scale guards below count whole-envelope serializations rather than
   watching a clock: a wall-clock threshold is a different number on every
   machine, while the serialization count is the same integer everywhere.

2. `scripts/generate-skill-router.py:splice_region`, a gate that self-certified
   its own corruption. The splice pattern `BEGIN + \\n?.*?\\n? + END` under
   DOTALL happily spans a SECOND `BEGIN`, so `subn` returned n == 1 and the
   duplicate guard below it never fired.
   MEASURED on `BEGIN / row A / BEGIN / row B / END`: the returned text held ONE
   `BEGIN`, the second marker and every line between the two were destroyed,
   exit 0, nothing printed. A following `--check` regenerated the same result and
   PASSED. A doubled `END` is the mirror image: the body stops at the first one,
   so `row B` and a stray second `END` survive outside the region and `--check`
   blesses that file too. This runs in pre-commit and in CI.

   Invisible because
   `tests/test_a_gate_that_could_not_be_cleared_by_the_command_it_named.py`
   covers two COMPLETE marker pairs, which the old code did correctly raise on.
   The doubled-single-marker shape was untested, and it is the shape a
   half-applied hand edit actually leaves behind.

3. `scripts/bridge_daemon/sources/pipeline.py:list_pipeline`, legitimate deal
   rows silently dropped. Header-ness and separator-ness were substring tests
   against the raw line rather than tests on the parsed cells.
   MEASURED on three deal rows, ONE survived: a row whose Next Action read
   "Await sign-off --- pending legal" was taken for the separator, a row whose
   Next Action read "Confirm Company, Country and Stage with counsel" was taken
   for the header, and `total_value_usd` reported 2,000,000 against a real
   6,000,000. That is the exact class of silent row loss the `_ROW_RE` comment in
   the same file documents fixing one cell earlier.

   Invisible because `tests/bridge/test_a_markdown_cell_should_not_delete_a_row.py`
   pins the zero-width-cell case and carries no substring-collision case, and
   because a dropped row leaves no trace: the endpoint reports a smaller total
   with the same confidence as a correct one. The bridge daemon is stopped and
   disabled by the operator's decision, so this is not live today, but the code
   ships and the numbers it computes are wrong.

4. `scripts/generate-newsletter-html.py:build_navigation_chart`, raw
   interpolation into mailed HTML. The string-region branch put the raw JSON
   object key into `r-code` and `r-name`; the dict branch two lines below routes
   the same fields through `esc()` and `nl2br()`.
   MEASURED with a region key of `<img src=x onerror=alert(1)>`:
   `<span class="r-code"><IMG SRC=X ONERROR=ALERT(1)></span>` landed in the
   mailed briefing. HTML attribute names are case-insensitive, so the `.upper()`
   does not defuse the payload, it only hides it from a grep for the lowercase
   tag, which is why this one nearly got refuted on re-measurement. A skill
   authors the region keys, so it is narrower than a value-side hole: treat it as
   correctness and consistency with the branch beside it.

   Invisible because no test covers this function at all.

Every guard is pinned from both sides. A refusal-only fix is trivially green, so
each broken input is paired with the good input that must still be accepted, and
each dropped-row case is paired with the real header and separator rows that must
still be skipped.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    """Import a hyphen-or-underscore script by path."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. a shed that re-serialized the whole envelope per dropped entry
# ============================================================

@pytest.fixture(scope="module")
def cal():
    return _load("scripts/calibrate.py", "calibrate_under_scale_test")


def _envelope(n: int, chars: int = 200) -> dict:
    """An envelope with `n` turns on each side, the shape `build_envelope` emits."""
    return {
        "session_id": "s",
        "session_path": "p",
        "started_at_utc": "",
        "ended_at_utc": "",
        "event_count": 2 * n,
        "truncated": False,
        "user_turns": [{"ts": f"2026-08-22T00:00:{i % 60:02d}Z", "text": "x" * chars}
                       for i in range(n)],
        "assistant_turns": [{"ts": f"2026-08-22T00:01:{i % 60:02d}Z", "text": "y" * chars}
                            for i in range(n)],
        "tool_errors": [],
        "system_reminders": [],
    }


def _shed_cost(cal, monkeypatch, n: int, max_bytes: int = 5000) -> tuple[int, int]:
    """Run one shed and report (whole-envelope serializations, bytes serialized).

    The bytes figure is the sum of the sizes those serializations returned, which
    is the work the shed actually did. Both numbers are integers on any machine,
    unlike a stopwatch reading.
    """
    calls: list[int] = []
    real = cal.envelope_bytes

    def counting(envelope):
        size = real(envelope)
        calls.append(size)
        return size

    monkeypatch.setattr(cal, "envelope_bytes", counting)
    cal.apply_truncation(_envelope(n), max_bytes)
    monkeypatch.setattr(cal, "envelope_bytes", real)
    return len(calls), sum(calls)


def test_the_shed_does_not_reserialize_the_envelope_once_per_dropped_entry(
        cal, monkeypatch):
    """Quadrupling the entries shed must not quadruple the serializations.

    Measured before the fix: 100 turns per side cost 399 serializations and 400
    cost 1599, one per dropped entry. After it, 2 at both sizes.
    """
    small_calls, _ = _shed_cost(cal, monkeypatch, 100)
    big_calls, _ = _shed_cost(cal, monkeypatch, 400)
    assert big_calls <= small_calls * 2, (
        f"4x the shedding cost {big_calls} whole-envelope serializations against "
        f"{small_calls}, so the cost still scales with the number of entries shed")


def test_the_shedding_work_grows_sub_quadratically_with_the_input(cal, monkeypatch):
    """Doubling the input must cost less than three times the work.

    A quadratic shed costs 4x on a 2x input (measured: 0.528 s -> 1.945 s ->
    8.312 s across three doublings). A linear one costs 2x. The 3x line
    separates them with room for constant overhead, and it holds on any machine
    because the measured quantity is bytes serialized, not seconds.
    """
    _, small_bytes = _shed_cost(cal, monkeypatch, 500)
    _, big_bytes = _shed_cost(cal, monkeypatch, 1000)
    assert big_bytes < small_bytes * 3, (
        f"doubling the input took the shed from {small_bytes} to {big_bytes} "
        f"bytes serialized, a ratio of {big_bytes / max(small_bytes, 1):.1f}x")


def _naive_apply_truncation(cal, envelope: dict, max_bytes: int) -> dict:
    """The pre-fix loop, kept here as the oracle for byte-identical output.

    It re-serializes per shed, which is the defect, so it is only ever run on
    the small envelopes below.
    """
    def size(env):
        return len(json.dumps(env, ensure_ascii=False).encode("utf-8"))

    if size(envelope) <= max_bytes:
        return envelope
    envelope["truncated"] = True
    while envelope["system_reminders"] and size(envelope) > max_bytes:
        envelope["system_reminders"].pop(0)
    while size(envelope) > max_bytes:
        if not cal._pop_oldest(envelope, ("user_turns", "assistant_turns")):
            break
    while envelope["tool_errors"] and size(envelope) > max_bytes:
        envelope["tool_errors"].pop(0)
    return envelope


def test_the_incremental_shed_is_byte_identical_to_the_reserializing_one(cal):
    """Anchor: a fast shed that sheds the wrong entries would pass both tests above.

    Randomized over mixed offset notations (which is what `_pop_oldest` sorts on),
    over non-ASCII text (where a byte count and a character count diverge) and
    over budgets from "sheds one entry" to "sheds everything".
    """
    # Seeded deliberately: a property test that cannot be replayed is a property
    # test whose failures cannot be investigated. Not a cryptographic use.
    rnd = random.Random(20260831)  # noqa: S311 - reproducible fixtures, not crypto
    for trial in range(60):
        n = rnd.randint(1, 40)

        # `trial=trial` binds the loop variable AT DEFINITION. Without it the
        # closure reads whatever `trial` holds when it is CALLED, which is
        # harmless only for as long as every call stays inside the iteration
        # that defined it. Moving one `text()` call below the loop, or into a
        # comprehension evaluated later, would silently switch every trial to
        # the last one's parity and the test would still pass while covering
        # half of what it claims.
        def text(trial=trial):
            if trial % 2:
                return "é中" * rnd.randint(1, 120)
            return "x" * rnd.randint(1, 300)
        env = {
            "session_id": "s",
            "truncated": False,
            "user_turns": [{"ts": f"2026-08-22T{i % 24:02d}:00:00Z", "text": text()}
                           for i in range(n)],
            "assistant_turns": [{"ts": f"2026-08-22T{i % 24:02d}:30:00+05:00",
                                 "text": text()} for i in range(n)],
            "tool_errors": [{"ts": "2026-08-22T01:00:00Z", "stderr": text()}
                            for _ in range(n // 5)],
            "system_reminders": [{"ts": "2026-08-22T00:00:00Z", "text": text()}
                                 for _ in range(n // 4)],
        }
        budget = rnd.randint(40, cal.envelope_bytes(env))
        fast = cal.apply_truncation(copy.deepcopy(env), budget)
        oracle = _naive_apply_truncation(cal, copy.deepcopy(env), budget)
        assert json.dumps(fast, ensure_ascii=False, sort_keys=True) == \
            json.dumps(oracle, ensure_ascii=False, sort_keys=True), (
            f"trial {trial} (n={n}, budget={budget}) diverged from the pre-fix shed")


def test_the_shed_still_converges_on_an_envelope_with_no_truncated_key(cal):
    """Anchor: `truncated` is ADDED on some callers' envelopes, not flipped.

    The running size is re-read after the flag is set for exactly this reason,
    and one existing test in
    `tests/test_a_shed_that_dropped_the_newer_turn.py` passes this shape.
    """
    env = {
        "user_turns": [{"ts": f"2026-08-22T{h:02d}:00:00Z", "text": "x" * 500}
                       for h in range(10)],
        "assistant_turns": [{"ts": f"2026-08-22T{h:02d}:30:00Z", "text": "y" * 500}
                            for h in range(10)],
        "tool_errors": [],
        "system_reminders": [],
    }
    out = cal.apply_truncation(copy.deepcopy(env), 2000)
    oracle = _naive_apply_truncation(cal, copy.deepcopy(env), 2000)
    assert out == oracle
    assert cal.envelope_bytes(out) <= 2000


# ============================================================
# 2. a splice that swallowed the marker it was counting
# ============================================================

@pytest.fixture(scope="module")
def gsr():
    return _load("scripts/generate-skill-router.py", "gen_skill_router_dup_markers")


def test_a_doubled_begin_marker_refuses_instead_of_destroying_the_region(gsr):
    """The measured shape: `BEGIN / row A / BEGIN / row B / END`."""
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    text = f"head\n{B}\nrow A\n{B}\nrow B\n{E}\ntail\n"
    with pytest.raises(ValueError) as exc:
        gsr.splice_region(text, "NEW")
    assert "2 BEGIN" in str(exc.value), (
        f"the refusal did not name the duplicated marker: {exc.value}")


def test_a_doubled_end_marker_refuses_too(gsr):
    """The mirror image: the non-greedy body stops at the first END."""
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    text = f"head\n{B}\nrow A\n{E}\nrow B\n{E}\ntail\n"
    with pytest.raises(ValueError) as exc:
        gsr.splice_region(text, "NEW")
    assert "2 END" in str(exc.value), (
        f"the refusal did not name the duplicated marker: {exc.value}")


@pytest.mark.parametrize("shape", ["doubled-begin", "doubled-end"])
def test_a_file_with_duplicate_markers_is_never_rewritten(gsr, shape):
    """The defect was the WRITE, not a bad message, so pin the write.

    Before the fix the doubled-BEGIN case returned a string holding one BEGIN,
    with `row A`, the second marker and `row B` gone, and exit 0. The
    doubled-END case returned a file still carrying two ENDs, with the second
    row stranded outside the region. Neither may be written at all.
    """
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    if shape == "doubled-begin":
        text = f"head\n{B}\nrow A\n{B}\nrow B\n{E}\ntail\n"
    else:
        text = f"head\n{B}\nrow A\n{E}\nrow B\n{E}\ntail\n"
    try:
        out = gsr.splice_region(text, "NEW")
    except ValueError:
        return
    pytest.fail(f"no refusal, so a duplicate-marker file was rewritten: {out!r}")


def test_a_duplicate_marker_file_is_not_silently_reproducible(gsr):
    """The self-certification: --check re-derives the corrupt result and passes.

    Pinned as a property rather than through the CLI: if the splice refuses,
    there is no second run that can bless its own output.
    """
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    text = f"head\n{B}\nrow A\n{B}\nrow B\n{E}\ntail\n"
    with pytest.raises(ValueError):
        first = gsr.splice_region(text, "NEW")
        # Only reached when the splice did not refuse; the second pass is the
        # `--check` that used to agree with the first and report no drift.
        assert gsr.splice_region(first, "NEW") != first, (
            "the corrupt splice is idempotent, so --check certifies it")


def test_exactly_one_pair_still_splices_and_preserves_the_surroundings(gsr):
    """Anchor: refusing every input would pass all four tests above."""
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    out = gsr.splice_region(f"HEAD\n{B}\nold\n{E}\nTAIL\n", "NEW")
    assert out == f"HEAD\n{B}\nNEW\n{E}\nTAIL\n"


def test_adjacent_markers_with_an_empty_region_still_splice(gsr):
    """Anchor on the shape the previous fix in this function existed for."""
    B, E = gsr.MARKER_BEGIN, gsr.MARKER_END
    out = gsr.splice_region(f"HEAD\n{B}\n{E}\nTAIL\n", "NEW")
    assert out == f"HEAD\n{B}\nNEW\n{E}\nTAIL\n"


def test_the_live_router_file_still_carries_exactly_one_pair(gsr):
    """The guard is only meaningful if the real file passes it."""
    text = gsr.ROUTER_FILE.read_text(encoding="utf-8")
    assert text.count(gsr.MARKER_BEGIN) == 1
    assert text.count(gsr.MARKER_END) == 1


# ============================================================
# 3. a deal row deleted by a substring of its own cells
# ============================================================

from scripts.bridge_daemon.sources import pipeline as pl  # noqa: E402

HEADER = ("| Company | Country | Stage | Est. Value | Stage Date | Owner | "
          "Next Action | Due Date |")
SEPARATOR = "|---|---|---|---|---|---|---|---|"


def _pipeline(tmp_path: Path, rows: list[str]) -> dict:
    (tmp_path / "context").mkdir(exist_ok=True)
    body = "\n".join(["## Active Deals", "", HEADER, SEPARATOR, *rows, ""])
    (tmp_path / "context" / "pipeline.md").write_text(body, encoding="utf-8")
    return pl.list_pipeline(tmp_path)


ROW_PLAIN = ("| Acme Ltd | UK | Proposal | $2,000,000 | 2026-08-01 | MH | "
             "Send deck | 2026-09-01 |")
ROW_DASHES = ("| Bravo Telecom | AE | Demo/POC | $3,000,000 | 2026-08-02 | MH | "
              "Await sign-off --- pending legal | 2026-09-02 |")
ROW_HEADER_WORDS = ("| Charlie Networks | SA | Lead | $1,000,000 | 2026-08-03 | "
                    "MH | Confirm Company, Country and Stage with counsel | "
                    "2026-09-03 |")


def test_a_cell_containing_three_dashes_does_not_delete_its_row(tmp_path):
    out = _pipeline(tmp_path, [ROW_PLAIN, ROW_DASHES])
    assert [d["company"] for d in out["deals"]] == ["Acme Ltd", "Bravo Telecom"], (
        "a Next Action containing --- was read as the table separator")


def test_a_cell_naming_the_columns_does_not_delete_its_row(tmp_path):
    out = _pipeline(tmp_path, [ROW_PLAIN, ROW_HEADER_WORDS])
    assert [d["company"] for d in out["deals"]] == ["Acme Ltd", "Charlie Networks"], (
        "a Next Action naming Company, Country and Stage was read as the header")


def test_a_company_name_containing_stage_does_not_delete_its_row(tmp_path):
    row = ("| Stagecraft Country Company | UK | Won | $4,000,000 | 2026-08-04 | "
           "MH | Sign | 2026-09-04 |")
    out = _pipeline(tmp_path, [row])
    assert [d["company"] for d in out["deals"]] == ["Stagecraft Country Company"]


def test_the_total_is_the_sum_of_every_row_and_not_of_the_survivors(tmp_path):
    """The number the operator reads. Measured 2,000,000 against a real 6,000,000."""
    out = _pipeline(tmp_path, [ROW_PLAIN, ROW_DASHES, ROW_HEADER_WORDS])
    assert len(out["deals"]) == 3
    assert out["total_value_usd"] == 6_000_000


def test_the_real_header_row_is_still_skipped(tmp_path):
    """Anchor: admitting everything would pass all four tests above."""
    out = _pipeline(tmp_path, [ROW_PLAIN])
    companies = [d["company"] for d in out["deals"]]
    assert companies == ["Acme Ltd"], f"the header leaked in as a deal: {companies}"


@pytest.mark.parametrize("sep", [
    "|---|---|---|---|---|---|---|---|",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    "|:---|:---:|---:|---|---|---|---|---|",
    "|-|-|-|-|-|-|-|-|",
])
def test_every_separator_spelling_is_still_skipped(tmp_path, sep):
    """Anchor: the separator must go on cell content, in each form GFM allows."""
    (tmp_path / "context").mkdir(exist_ok=True)
    body = "\n".join(["## Active Deals", "", HEADER, sep, ROW_PLAIN, ""])
    (tmp_path / "context" / "pipeline.md").write_text(body, encoding="utf-8")
    out = pl.list_pipeline(tmp_path)
    assert [d["company"] for d in out["deals"]] == ["Acme Ltd"], (
        f"separator spelling {sep!r} was ingested as a deal")


def test_a_zero_width_next_action_still_survives(tmp_path):
    """Anchor on the fix this file's `_ROW_RE` comment already records."""
    row = "| Delta SA | FR | Lead | $500,000 | 2026-08-05 | MH || 2026-09-05 |"
    out = _pipeline(tmp_path, [row])
    assert [d["company"] for d in out["deals"]] == ["Delta SA"]
    assert out["deals"][0]["next_action"] == ""


# ============================================================
# 4. a region key interpolated raw into mailed HTML
# ============================================================

@pytest.fixture(scope="module")
def newsletter():
    return _load("scripts/generate-newsletter-html.py", "newsletter_html_esc_test")


PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
    "gcc & cis",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_string_region_key_is_escaped_before_it_reaches_the_markup(
        newsletter, payload):
    """`.upper()` is not a defence: HTML attribute names are case-insensitive."""
    html = newsletter.build_navigation_chart({payload: "Demand steady."})
    assert "<IMG" not in html and "<SCRIPT" not in html
    assert "<img" not in html and "<script" not in html
    assert payload.upper() not in html, (
        "the raw key survived into the markup, upper-cased rather than escaped")
    assert "&lt;" in html or "&amp;" in html, (
        f"nothing was escaped, so the key never went through esc(): {html!r}")


@pytest.mark.parametrize("payload", PAYLOADS)
def test_the_dict_branch_escapes_the_same_payload(newsletter, payload):
    """Anchor from the other branch: the two must not disagree about escaping."""
    html = newsletter.build_navigation_chart(
        {"gcc": {"code": payload, "name": payload, "body": "Demand steady."}})
    assert "<img" not in html.lower() and "<script" not in html.lower()


def test_a_benign_string_region_still_renders_its_code_and_body(newsletter):
    """Anchor: escaping everything to nothing would pass both tests above."""
    html = newsletter.build_navigation_chart({"gcc": "Gulf demand steady."})
    assert '<span class="r-code">GCC</span>' in html
    assert '<span class="r-name">GCC</span>' in html
    assert "Gulf demand steady." in html
