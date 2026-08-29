#!/usr/bin/env python3
"""Regression tests for nine measured defects in scripts/marp_render.py.

The file is named for the one that was misfiring on the day it was fixed: with
the pin at `@marp-team/marp-cli@4.4.0` and marp printing
`@marp-team/marp-cli v4.5.0 (w/ @marp-team/marp-core v4.4.0)`, the substring
test `pinned_num in installed_version` matched the bundled marp-core and
declared the version fine while a different marp-cli rendered every deck.

The other eight, each measured on 2026-08-29 before it was patched:

* `render` answered `{"ok": True, "outputs": []}` for `--pdf-only --html-only`.
* `fence_mask` ignored fence length, so a three-backtick sample closed a
  four-backtick wrapper and `auto_slide_breaks` broke a slide inside it.
* `paginate_heavy` split paragraphs on any blank line, cutting a fence in half.
* `transform_workspace_md` never passed its subtitle to the frontmatter.
* `watch` rejected `--verbose` after the subcommand.
* A YAML null in existing frontmatter round-tripped to the string "None".
* The PNG glob reported files an earlier run had written.
* A missing theme template escaped as a raw FileNotFoundError.

Subject under test: scripts/marp_render.py.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import marp_render as mr  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "marp_render.py"


# --- 1. the version check that matched the wrong package -------------------


@pytest.fixture
def pinned_440(monkeypatch):
    monkeypatch.setattr(mr, "get_pinned_version", lambda: "@marp-team/marp-cli@4.4.0")


def test_the_bundled_marp_core_version_is_not_a_match(pinned_440):
    """The exact string this machine printed on 2026-08-29."""
    installed = "@marp-team/marp-cli v4.5.0 (w/ @marp-team/marp-core v4.4.0)"
    assert mr.check_version_match(installed) is False


def test_a_different_major_version_is_not_a_match(pinned_440):
    assert mr.check_version_match("14.4.0") is False


def test_a_prerelease_of_the_pin_is_not_a_match(pinned_440):
    assert mr.check_version_match("4.4.0-beta.1") is False


def test_the_pinned_version_itself_still_matches(pinned_440):
    assert mr.check_version_match("v4.4.0") is True
    assert mr.check_version_match("@marp-team/marp-cli v4.4.0 (w/ core v4.3.0)") is True


def test_an_unparsable_version_string_warns_rather_than_claims_a_match(pinned_440):
    assert mr.check_version_match("unknown") is False


def test_the_real_pin_file_is_not_satisfied_by_a_bundled_package():
    """Derived from the committed pin, so a pin bump cannot rot this case."""
    pin = mr.get_pinned_version()
    assert pin.strip(), "the version pin resolved empty; nothing was measured"
    pinned_num = pin.rsplit("@", 1)[-1]
    installed = f"@marp-team/marp-cli v9.9.9 (w/ @marp-team/marp-core v{pinned_num})"
    assert mr.check_version_match(installed) is False
    assert mr.check_version_match(f"@marp-team/marp-cli v{pinned_num}") is True


# --- 2. a shorter inner fence closed the outer one -------------------------


NESTED_FENCE_DECK = "````markdown\n```\n## Example\n```\nsome text\n````\ntail"


def test_a_three_backtick_sample_does_not_close_a_four_backtick_wrapper():
    lines = NESTED_FENCE_DECK.split("\n")
    mask = mr.fence_mask(lines)
    assert mask == [True, True, True, True, True, True, False]


def test_no_slide_break_is_pushed_inside_the_outer_fence():
    assert mr.auto_slide_breaks(NESTED_FENCE_DECK, "h2") == NESTED_FENCE_DECK


def test_an_equal_length_fence_still_closes():
    lines = ["```", "code", "```", "after"]
    assert mr.fence_mask(lines) == [True, True, True, False]


def test_a_tilde_fence_is_not_closed_by_backticks():
    lines = ["~~~", "```", "~~~", "after"]
    assert mr.fence_mask(lines) == [True, True, True, False]


# --- 3. the paragraph splitter was fence-blind -----------------------------


def _slide_with_a_blank_line_inside_a_fence() -> str:
    head = "\n".join(f"a{i} = {i}" for i in range(45))
    tail = "\n".join(f"b{i} = {i}" for i in range(14))
    return "# Head\n\n```python\n" + head + "\n\n" + tail + "\n```\n"


def test_a_blank_line_inside_a_fence_is_not_a_paragraph_boundary():
    slide = _slide_with_a_blank_line_inside_a_fence()
    paragraphs = mr.fence_aware_paragraphs(slide)
    fenced = [p for p in paragraphs if "```" in p]
    assert len(fenced) == 1
    assert fenced[0].count("```") == 2


def test_paginate_heavy_never_splits_a_fence_across_two_slides():
    slide = _slide_with_a_blank_line_inside_a_fence()
    assert len(slide.split()) > mr.WORD_OVERFLOW_THRESHOLD, "the slide is not heavy; nothing was measured"
    slides = mr.paginate_heavy(slide).split("\n\n---\n\n")
    assert len(slides) > 1, "the slide was never paginated; nothing was measured"
    for produced in slides:
        assert produced.count("```") % 2 == 0


def test_a_blank_line_outside_a_fence_still_splits():
    prose_a = " ".join(["alpha"] * 100)
    prose_b = " ".join(["bravo"] * 100)
    slides = mr.paginate_heavy(f"{prose_a}\n\n{prose_b}").split("\n\n---\n\n")
    assert len(slides) == 2


# --- 4. the computed subtitle never reached the frontmatter ----------------


def test_the_subtitle_reaches_the_generated_frontmatter(tmp_path, monkeypatch):
    source = tmp_path / "acme-telecom-note.md"
    source.write_text("# Acme Telecom\n\nBriefing for James Bond.\n", encoding="utf-8")

    seen = {}

    def fake_render(rendered_source, **kwargs):
        seen["text"] = Path(rendered_source).read_text(encoding="utf-8")
        return {"ok": True, "outputs": [], "errors": []}

    monkeypatch.setattr(mr, "render", fake_render)
    mr.transform_workspace_md(source, subtitle="Q3 Review", output_dir=tmp_path)

    frontmatter, _ = mr.parse_frontmatter(seen["text"])
    assert frontmatter["subtitle"] == "Q3 Review"


# --- 5. --verbose after the subcommand ------------------------------------


@pytest.mark.parametrize("argv", [
    ["render", "missing.md", "--verbose"],
    ["from", "missing.md", "--verbose"],
    ["watch", "--verbose"],
])
def test_verbose_is_accepted_after_every_subcommand(argv):
    proc = subprocess.run([sys.executable, str(SCRIPT), *argv],
                          capture_output=True, text=True, timeout=60)
    assert "unrecognized arguments: --verbose" not in proc.stderr


# --- 6. conflicting format flags produced nothing and said it worked -------


def test_both_format_flags_are_refused_by_the_library(tmp_path):
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")
    result = mr.render(deck, output_dir=tmp_path, pdf_only=True, html_only=True)
    assert result["ok"] is False
    assert result["error"] == "conflicting-format-flags"
    assert result.get("outputs", []) == []


def test_both_format_flags_are_refused_by_the_cli(tmp_path):
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "render", str(deck), "--pdf-only", "--html-only"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "not allowed with" in proc.stderr


def test_one_format_flag_alone_is_still_accepted(tmp_path, monkeypatch):
    """The guard must refuse the pair only, never a single flag."""
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")
    monkeypatch.setattr(mr, "check_marp_installed", lambda: (False, ""))
    for flags in ({"pdf_only": True}, {"html_only": True}):
        result = mr.render(deck, output_dir=tmp_path, **flags)
        assert result["error"] == "marp-not-installed"


# --- 7. a YAML null became the string "None" -------------------------------


def test_a_null_frontmatter_value_survives_the_round_trip():
    source = "---\nmarp: true\nreviewed_by:\n---\n# Deck\n"
    injected = mr.inject_frontmatter(source)
    assert "reviewed_by: None" not in injected
    frontmatter, _ = mr.parse_frontmatter(injected)
    assert frontmatter["reviewed_by"] is None


def test_paginate_heavy_preserves_a_null_frontmatter_value():
    source = "---\nmarp: true\nreviewed_by:\n---\n# Deck\n\nShort body.\n"
    frontmatter, _ = mr.parse_frontmatter(mr.paginate_heavy(source))
    assert frontmatter["reviewed_by"] is None


def test_the_scalar_emitter_keeps_the_shapes_it_already_handled():
    assert mr.yaml_scalar(True) == "true"
    assert mr.yaml_scalar(False) == "false"
    assert mr.yaml_scalar("16:9") == '"16:9"'
    assert mr.yaml_scalar('Says "hello"') == '"Says \\"hello\\""'
    assert mr.yaml_scalar("31c") == "31c"
    assert mr.yaml_scalar(None) == "null"


# --- 8. the PNG glob collected an earlier run's files ----------------------


@pytest.fixture
def marp_that_writes_nothing(monkeypatch):
    """A marp that exits 0 and produces no file, which is the failure case."""
    monkeypatch.setattr(mr, "check_marp_installed", lambda: (True, "4.4.0"))
    monkeypatch.setattr(mr, "check_version_match", lambda _v: True)
    monkeypatch.setattr(mr, "_resolve_marp_bin", lambda: "/bin/true")
    monkeypatch.setattr(mr, "probe_browser", lambda: None)
    monkeypatch.setattr(mr, "_run_marp",
                        lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""))


def test_a_stale_png_is_not_reported_as_this_run_s_output(tmp_path, marp_that_writes_nothing):
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")
    stale = tmp_path / "deck.004.png"
    stale.write_bytes(b"written by an earlier run")

    result = mr.render(deck, output_dir=tmp_path, images_png=True, pdf_only=True)

    assert [o for o in result["outputs"] if o["type"] == "png"] == []
    assert any(e["error"] == "no-output" for e in result["errors"] if e["type"] == "png")
    assert stale.exists(), "the fix must not delete files it did not write"


def test_a_png_this_run_wrote_is_reported(tmp_path, monkeypatch):
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")
    monkeypatch.setattr(mr, "check_marp_installed", lambda: (True, "4.4.0"))
    monkeypatch.setattr(mr, "check_version_match", lambda _v: True)
    monkeypatch.setattr(mr, "_resolve_marp_bin", lambda: "/bin/true")
    monkeypatch.setattr(mr, "probe_browser", lambda: None)

    def marp_that_writes_one_png(cmd):
        if "--images" in cmd:
            (tmp_path / "deck.001.png").write_bytes(b"fresh")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mr, "_run_marp", marp_that_writes_one_png)
    result = mr.render(deck, output_dir=tmp_path, images_png=True, pdf_only=True)

    pngs = [o for o in result["outputs"] if o["type"] == "png"]
    assert [Path(o["path"]).name for o in pngs] == ["deck.001.png"]
    assert not [e for e in result["errors"] if e["type"] == "png"]


# --- 9. a missing theme template escaped as a traceback --------------------


@pytest.fixture
def theme_template_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "THEME_TEMPLATE", tmp_path / "absent-theme.css.tmpl")


def test_render_answers_a_structured_result_when_the_theme_is_missing(
        tmp_path, theme_template_is_gone, monkeypatch):
    monkeypatch.setattr(mr, "check_marp_installed", lambda: (True, "4.4.0"))
    monkeypatch.setattr(mr, "check_version_match", lambda _v: True)
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")

    result = mr.render(deck, output_dir=tmp_path)

    assert result == mr.theme_missing_result()
    assert result["ok"] is False
    assert result["error"] == "theme-missing"


def test_watch_start_answers_a_structured_result_when_the_theme_is_missing(
        tmp_path, theme_template_is_gone, monkeypatch):
    monkeypatch.setattr(mr, "WATCH_STATE_FILE", tmp_path / "watch.json")
    monkeypatch.setattr(mr, "_resolve_marp_bin", lambda: "/bin/true")
    deck = tmp_path / "deck.md"
    deck.write_text("# Acme Telecom\n", encoding="utf-8")

    result = mr.watch_start(deck)

    assert result["error"] == "theme-missing"
    assert not (tmp_path / "watch.json").exists()


def test_the_self_test_reports_the_missing_theme_instead_of_dying(
        tmp_path, theme_template_is_gone, monkeypatch):
    monkeypatch.setattr(mr, "check_marp_installed", lambda: (True, "4.4.0"))
    monkeypatch.setattr(mr, "check_version_match", lambda _v: True)
    monkeypatch.setattr(mr, "probe_browser", lambda: None)
    # Fails the test rather than rendering for real if the check is skipped.
    monkeypatch.setattr(mr, "render", lambda *a, **k: pytest.fail(
        "self_test reached the render step with no theme template"))

    results = mr.self_test()

    assert results["ok"] is False
    named = [c for c in results["checks"] if c["name"] == "theme template"]
    assert len(named) == 1 and named[0]["ok"] is False
