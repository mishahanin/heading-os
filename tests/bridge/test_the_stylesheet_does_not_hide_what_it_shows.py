"""The dashboard stylesheet: rules that outlived what they were written for.

Found by the 2026-08-23 engine audit, shard `scripts-02-p4`. Every one was
confirmed against `app.css` and `app.js` on 2026-08-24 before it was changed;
none was taken on the audit's word, because a stylesheet cannot be rendered
here and a claim about layout is easy to state and hard to check.

The dominant one is worth naming as a class, because it is the same shape this
campaign keeps finding: **the fix had been applied to one of four.** Every
collapsible panel on this dashboard is toggled with `el.hidden`, and the UA rule
behind that attribute is `[hidden] { display: none }` - which any later
`display:` declaration in this sheet silently defeats. The sheet answered that
one selector at a time: five per-selector `[hidden]` guards, and three panels
that needed one and never got it. Measured from `app.js`: eight elements ship
with the attribute, four of them set `display: flex`, and only one of those four
carried a guard. The other three - "Recently done" on /tasks, "Recently sent" on
/approvals, "Recently dismissed" and "Deferred" on /inbox - rendered as an empty
bordered box while collapsed. One `[hidden]` reset replaces all five guards and
covers any panel written next; the first test below derives the panel list from
`app.js` rather than restating it, so a new one cannot be missed the same way.

The rest, each confirmed by reading the two files:

* `body[data-sidebar="collapsed"]` still reserved a third 56px grid row for the
  footer cmd-bar Phase 1.36 removed, and `.tweaks` still cleared 56px for it.
* `.inbox-row-summary` was declared twice, for two different contexts, and both
  declarations applied to both. The compact-row rule's `-webkit-line-clamp: 2`
  was never unset on the drill-down card, so the "full" analyst summary that the
  second rule styles with `white-space: pre-wrap` was truncated to two lines;
  the drill-down rule's border, padding and 12px margins were applied to the
  row whose own comment says it exists to stay compact.
* Six row-hover rules painted `rgba(0, 0, 0, 0.02)`, which is invisible on a
  dark surface. The sheet ships a full dark token set.
* `.inbox-filter-chip:hover:not(:disabled)` was reported as a guard that never
  fires, because stubs are marked with `.is-stub`. That is REFUTED: all five
  stub chips in `app.js` carry BOTH the class and the `disabled` attribute, so
  the guard worked. The selector was still widened to name `.is-stub`, because
  the class is what this sheet reacts to when it paints the not-allowed cursor,
  and a chip marked one way and not the other would light up on hover.
* `--warn` and `--ok` were referenced six times and defined nowhere, so the
  literal fallbacks always fired and never followed the theme.
* The read-only viewer block hid the submit buttons and left three live
  `<input>` note fields and an action hint with nothing to submit to.
* The day-timeline dot sat beside the rail rather than on it, under both
  density modes.
* `.pulse-footer-row-2up` hardcoded two columns, dropping the responsive
  collapse its base class has and every comparable grid here keeps.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "scripts" / "bridge_daemon" / "web"
CSS = (WEB / "app.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")
# Comments legitimately discuss the patterns this file forbids.
CSS_CODE = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _blocks() -> list[tuple[str, str]]:
    """[(selector list, declarations)] for every top-level rule."""
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS_CODE)]


def _declarations_for(cls: str) -> str:
    """Every declaration reaching a bare `.cls` selector, concatenated."""
    out = []
    for sel, decl in _blocks():
        for one in sel.split(","):
            if one.strip() == f".{cls}":
                out.append(decl)
    return "\n".join(out)


# --- the hidden attribute must win, everywhere -------------------------------

def _classes_shipped_hidden() -> set[str]:
    tags = re.findall(r'<div class="([a-z0-9 _-]+)"[^>]*\bhidden\b', JS)
    return {c for t in tags for c in t.split()}


def test_the_hidden_scan_still_finds_the_panels_it_is_about():
    """A scan that matches nothing passes everything. These four are the ones
    that set `display: flex`; three of them had no guard."""
    found = _classes_shipped_hidden()
    for cls in ("pulse-activity-expanded", "task-done-expanded",
                "appr-sent-expanded", "inbox-dismissed-expanded"):
        assert cls in found, f"app.js no longer ships .{cls} with the hidden attribute"


def test_the_stylesheet_carries_one_reset_for_the_hidden_attribute():
    assert re.search(r"(?m)^\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}", CSS_CODE), (
        "the `[hidden]` reset is gone; every panel that sets a display now "
        "renders while collapsed unless it carries its own guard"
    )


def test_no_rule_re_enables_a_hidden_element():
    """`!important` on the reset beats an ordinary declaration, but not another
    `!important`. Nothing may claim one."""
    offenders = [
        sel for sel, decl in _blocks()
        if "[hidden]" in sel and re.search(r"display:\s*(?!none)[a-z-]+", decl)
    ]
    assert not offenders, offenders


def test_the_per_selector_guards_did_not_come_back():
    """Five of them existed and three panels still went uncovered - which is
    what a per-selector answer to a whole-sheet problem does."""
    guards = re.findall(r"(?m)^(\.[a-z0-9-]+)\[hidden\]\s*\{", CSS_CODE)
    assert not guards, (
        "a per-selector [hidden] guard is back; the reset covers it, and a "
        f"second answer only raises the question of which one is authoritative: {guards}"
    )


# --- the removed footer left no clearance behind -----------------------------

def _grid_rows(selector: str) -> list[str]:
    for sel, decl in _blocks():
        if sel == selector:
            m = re.search(r"grid-template-rows:\s*([^;]+);", decl)
            if m:
                return m.group(1).split()
    raise AssertionError(f"no grid-template-rows on {selector}")


def test_the_collapsed_shell_has_the_same_row_count_as_the_open_one():
    base = _grid_rows("body")
    collapsed = _grid_rows('body[data-sidebar="collapsed"]')
    assert len(collapsed) == len(base) == 2, (
        f"the shell is topbar + canvas; base={base} collapsed={collapsed}"
    )


def test_nothing_still_clears_the_removed_footer_bar():
    hits = [sel for sel, decl in _blocks() if re.search(r"bottom:\s*56px", decl)]
    assert not hits, f"56px of clearance for a footer removed in Phase 1.36: {hits}"


# --- one class, one context --------------------------------------------------

def test_the_compact_row_summary_and_the_drill_down_card_are_different_classes():
    assert ".inbox-detail-summary" in CSS_CODE
    compact = _declarations_for("inbox-row-summary")
    detail = _declarations_for("inbox-detail-summary")
    assert "-webkit-line-clamp" in compact, "the compact row stopped clamping"
    assert "-webkit-line-clamp" not in detail, (
        "the drill-down summary is clamped to two lines again, while the same "
        "rule sets white-space: pre-wrap as if it were showing the full text"
    )
    assert "white-space: pre-wrap" in detail
    assert "border:" not in compact, (
        "the drill-down card's border is back on the row that exists to stay compact"
    )


def test_each_summary_class_is_emitted_by_exactly_one_call_site():
    assert JS.count('class="inbox-row-summary"') == 1
    assert JS.count('class="inbox-detail-summary"') == 1


# --- the theme is followed, not assumed --------------------------------------

def test_no_hover_paints_a_tint_only_the_light_theme_can_show():
    """A 2% black overlay is imperceptible on `--surface` at 0.185 lightness.
    Six clickable row types gave no hover feedback at all in dark mode."""
    bad = [sel for sel, decl in _blocks()
           if ":hover" in sel and re.search(r"background:\s*rgba\(0, *0, *0, *0\.0", decl)]
    assert not bad, bad


def test_every_custom_property_used_without_a_fallback_is_defined():
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)\s*\)", CSS_CODE))
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS_CODE))
    assert used, "the token scan found nothing"
    assert not used - defined, sorted(used - defined)


def test_the_undefined_state_tokens_are_gone():
    """`--warn` and `--ok` were referenced six times and defined nowhere, so the
    literal fallback always fired and never moved with the theme."""
    assert "var(--warn" not in CSS_CODE and "var(--ok" not in CSS_CODE
    assert "var(--state-warn)" in CSS_CODE and "var(--state-ok)" in CSS_CODE


# --- the read-only surface has no live control without a destination ---------

def _read_only_selectors() -> set[str]:
    """The selector list of the block the READ-ONLY VIEWER directive owns."""
    out: set[str] = set()
    for sel, decl in _blocks():
        if re.search(r"display:\s*none\s*!important", decl) and sel != "[hidden]":
            out |= {s.strip() for s in sel.split(",")}
    return out


def test_a_note_field_is_not_left_behind_when_its_submit_button_is_hidden():
    hidden = _read_only_selectors()
    assert ".pulse-approval-actions" in hidden, "the read-only block itself is gone"
    for cls in ("pulse-approval-note", "pipe-touch-note", "inv-send-note",
                "inbox-action-hint"):
        assert f'class="{cls}"' in JS, f"the fixture is stale: .{cls} is gone from app.js"
        assert f".{cls}" in hidden, (
            f".{cls} is rendered but no longer hidden by the read-only block; "
            "it is a focusable input with no button that can submit it"
        )


def test_the_history_toggles_are_deliberately_still_visible():
    """Anchor for the test above: the read-only block must not grow to hide the
    expand toggles. What they reveal is read-only information, which is what
    this surface is for."""
    hidden = _read_only_selectors()
    for cls in ("task-done-foot", "appr-sent-foot", "inbox-dismissed-foot"):
        assert f".{cls}" not in hidden, (
            f".{cls} was added to the read-only block; that hides history, "
            "not an action"
        )


# --- geometry derived from the token it depends on ---------------------------

def test_the_timeline_dot_is_centred_by_derivation_not_by_a_literal():
    """The rail is at a fixed 78px and the body column starts at 70px plus the
    grid gap, which is 16px normally and 12px on the compact density. A literal
    offset can only be right for one of them."""
    decl = _declarations_for("day-card-body::before") or "".join(
        d for s, d in _blocks() if "day-card-body::before" in s)
    m = re.search(r"left:\s*([^;]+);", decl)
    assert m, "the dot lost its offset"
    assert "var(--space-4)" in m.group(1), (
        f"the offset is a literal again: {m.group(1).strip()}"
    )


def test_the_stub_chip_guard_names_the_mechanism_the_chips_use():
    assert ".is-stub" in "".join(
        s for s, _ in _blocks() if ".inbox-filter-chip:hover" in s), (
        "the hover guard names `:disabled` again; stubs are marked with a class"
    )
    assert "is-stub" in JS, "the fixture is stale: app.js no longer marks stub chips"


def test_the_two_up_footer_still_collapses_on_a_narrow_viewport():
    m = re.search(r"@media \(max-width: \d+px\) \{\s*\.pulse-footer-row-2up \{\s*"
                  r"grid-template-columns: 1fr;", CSS)
    assert m, (
        "the 2-up footer hardcodes two columns with no fallback, while its base "
        "class collapses via auto-fit and every comparable grid here has one"
    )
