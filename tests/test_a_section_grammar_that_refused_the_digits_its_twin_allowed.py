#!/usr/bin/env python3
"""`scripts/utils/doctype_renderer.py` had two placeholder grammars that disagreed.

`_VAR_RE` accepts `{{PHASE_2}}` - a digit anywhere after the first character.
`_SECTION_RE` accepted `[A-Z_]+` only, so `{{#PHASE_2_ITEMS}}` matched nothing.
The failure mode is silent and total: `_render_sections` leaves the block alone,
and `_substitute_scalars` cannot clean up after it either, because `{{#...}}`
and `{{/...}}` carry a `#` and a `/` that `_VAR_RE` rejects. The raw template
syntax and every line of the loop body are emitted verbatim into the rendered
HTML and the PDF, and the render exits successfully.

These are pure-function tests over the two substitution passes. No template file,
no brand asset tree, no datastore.
"""

from scripts.utils.doctype_renderer import (
    _SECTION_RE,
    _VAR_RE,
    _render_sections,
    _substitute_scalars,
)


def _render(template: str, data: dict) -> str:
    return _substitute_scalars(_render_sections(template, data), data)


def test_a_section_name_carrying_a_digit_expands():
    out = _render(
        "{{#PHASE_2_ITEMS}}<li>{{.}}</li>{{/PHASE_2_ITEMS}}",
        {"PHASE_2_ITEMS": ["survey", "pilot"]},
    )
    assert out == "<li>survey</li><li>pilot</li>"
    assert "{{" not in out


def test_no_template_syntax_survives_a_digit_bearing_section():
    """The user-visible half: raw `{{#...}}` reaching the rendered document."""
    out = _render(
        "<ul>{{#TIER_1_ITEMS}}<li>{{name}}</li>{{/TIER_1_ITEMS}}</ul>",
        {"TIER_1_ITEMS": [{"name": "Aston Holdings"}, {"name": "Skyfall Group"}]},
    )
    assert "{{#TIER_1_ITEMS}}" not in out
    assert "{{/TIER_1_ITEMS}}" not in out
    assert out == "<ul><li>Aston Holdings</li><li>Skyfall Group</li></ul>"


def test_the_two_grammars_accept_the_same_names():
    """The invariant behind the fix, asserted over a non-empty sample."""
    names = ["PHASE_2_ITEMS", "TIER_1", "A1", "PLAIN", "_LEADING", "X2Y3"]
    assert names
    for name in names:
        assert _VAR_RE.fullmatch("{{" + name + "}}"), name
        section = "{{#" + name + "}}body{{/" + name + "}}"
        assert _SECTION_RE.fullmatch(section), name


def test_a_name_opening_with_a_digit_is_still_rejected_by_both():
    """The grammars must agree on the NO side too, or this is a straw man."""
    for name in ["2PHASE", "9"]:
        assert not _VAR_RE.fullmatch("{{" + name + "}}"), name
        assert not _SECTION_RE.fullmatch("{{#" + name + "}}b{{/" + name + "}}"), name


def test_a_digit_bearing_section_with_a_falsy_value_is_dropped():
    out = _render("before{{#PHASE_2_ITEMS}}x{{/PHASE_2_ITEMS}}after", {"PHASE_2_ITEMS": []})
    assert out == "beforeafter"


def test_the_digit_free_case_is_unchanged():
    out = _render("{{#ITEMS}}<li>{{.}}</li>{{/ITEMS}}", {"ITEMS": ["one"]})
    assert out == "<li>one</li>"
