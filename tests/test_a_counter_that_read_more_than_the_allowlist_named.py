#!/usr/bin/env python3
"""What `scripts/odin-cadence.py` counts, versus what it says it counts.

The module docstring promises the counted source set "MUST equal /odin collect's
allowlist (mode-catalog.md) ... same globs, same exclusions, same gate". Two
measured divergences, one in each direction.

  - OVER-COUNT. `head.startswith(s)` for s in `("Log", "Recent activity",
    "Decisions")` is true for `## Logistics`, `## Login attempts` and `## Logic`.
    Measured on a two-section thread, `count_threads` returned 2 for dated
    bullets in sections /odin collect never harvests. The comment above
    `THREAD_SECTIONS` licenses prefix matching only for suffixes like
    "## Log (newest first)" -- which open with a space.

  - UNDER-COUNT, and silent. `_fm_scalar` and `_fm_list` are hand-rolled while
    `odin-brain-health.py` reads the same files through `yaml.safe_load`, so the
    two tools disagreed about one file on disk. Measured: an episode whose
    frontmatter reads `status: raw  # draft` came back as the string
    `"raw  # draft"`, failed `!= "raw"`, and dropped out of clustering -- one
    cluster became zero, with nothing appended to `compute()`'s `skipped` list,
    so the JSON asserted a complete pass it had not made.

Every date here is a literal and every cluster is aged against an explicit
`today`; nothing reads the host clock.

Run: .venv/bin/python -m pytest
     tests/test_a_counter_that_read_more_than_the_allowlist_named.py -q
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "odin-cadence.py"

SINCE = "2026-01-01"
TODAY = date(2026, 8, 20)


def _load():
    spec = importlib.util.spec_from_file_location("odin_cadence_counter", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oc = _load()


def _thread(root: Path, name: str, body: str) -> None:
    d = root / "threads" / "business"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        "---\ntype: business\nclassification: ceo-only\n---\n\n" + body,
        encoding="utf-8",
    )


# ============================================================
# 1 - a section whose name merely begins the same way
# ============================================================

@pytest.mark.parametrize(
    "heading",
    ["Logistics", "Login attempts", "Logic", "Recent activityfeed", "Decisionsmith"],
)
def test_a_lookalike_section_is_not_harvested(tmp_path, heading):
    """`## Logistics` is not `## Log`, and its dated bullets are not collect's."""
    _thread(tmp_path, "quantum-of-solace.md",
            f"## {heading}\n\n- 2026-08-20 - shipped the samples\n")
    assert oc.count_threads(tmp_path, SINCE) == 0


@pytest.mark.parametrize(
    "heading",
    ["Log", "Log (newest first)", "Log - 2026", "Log:", "Recent activity",
     "Recent activity (last 30d)", "Decisions", "Decisions taken"],
)
def test_a_real_section_is_still_harvested(tmp_path, heading):
    """The other direction. A guard that refuses everything is not a guard.

    `## Log (newest first)` is the exact header the prefix match exists for, so
    a fix that tightened to an equality test would fail here.
    """
    _thread(tmp_path, "octopussy.md",
            f"## {heading}\n\n- 2026-08-20 - moved the pieces\n")
    assert oc.count_threads(tmp_path, SINCE) == 1


def test_a_lookalike_section_does_not_carry_its_neighbour(tmp_path):
    """Mixed file: the real section counts, the rhyming one does not.

    Entering `## Logistics` must also CLOSE `## Log`, or the bullets below it
    keep counting under the previous heading's membership.
    """
    _thread(
        tmp_path,
        "goldeneye.md",
        "## Log\n\n"
        "- 2026-08-18 - agreed the scope with Auric Sterling\n\n"
        "## Logistics\n\n"
        "- 2026-08-19 - crates cleared customs\n"
        "- 2026-08-20 - crates collected\n\n"
        "## Decisions\n\n"
        "- 2026-08-20 - chose the second supplier\n",
    )
    assert oc.count_threads(tmp_path, SINCE) == 2


def test_the_section_regex_is_built_from_the_declared_list(tmp_path):
    """Derived, not retyped: adding a section must not need this test edited."""
    assert oc.THREAD_SECTIONS, "empty section list: nothing below measures anything"
    for section in oc.THREAD_SECTIONS:
        assert oc.THREAD_SECTION_RE.match(section), section
        assert oc.THREAD_SECTION_RE.match(section + " (newest first)"), section
        assert not oc.THREAD_SECTION_RE.match(section + "xyz"), section


# ============================================================
# 2 - a trailing YAML comment stops erasing the value
# ============================================================

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("raw  # draft", "raw"),
        ("raw\t# draft", "raw"),
        ("raw", "raw"),
        ("", ""),
        ('"raw # not a comment"', "raw # not a comment"),
        ("'raw # nor this'", "raw # nor this"),
        # No leading whitespace: YAML says this is part of the value.
        ("#fff", "#fff"),
        ("colour#7", "colour#7"),
    ],
)
def test_scalar_values_survive_their_comments(raw, expected):
    assert oc._fm_scalar(f"status: {raw}\n", "status") == expected


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("keywords: [alpha, beta]  # note\n", ["alpha", "beta"]),
        ("keywords: [alpha, beta]\n", ["alpha", "beta"]),
        ("keywords:  # note\n  - alpha\n  - beta\n", ["alpha", "beta"]),
        ("keywords:\n  - alpha  # first\n  - beta\n", ["alpha", "beta"]),
        ("keywords: []  # nothing yet\n", []),
        ("keywords: [alpha]\nentities: [zulu]\n", ["alpha"]),
    ],
)
def test_list_values_survive_their_comments(block, expected):
    assert oc._fm_list(block, "keywords") == expected


def test_an_episode_with_a_commented_status_still_clusters(tmp_path):
    """The end-to-end shape: the comment cost a whole cluster, silently."""
    episodes = tmp_path / "knowledge" / "odin-brain" / "episodes"
    episodes.mkdir(parents=True)

    def write(name: str, status_line: str) -> None:
        (episodes / name).write_text(
            "---\n"
            f"{status_line}\n"
            "created: 2026-08-01\n"
            "keywords: [thunderball, moonraker, skyfall]\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

    write("e1.md", "status: raw")
    write("e2.md", "status: raw  # draft")

    result = oc.analyze_reflect_clusters(tmp_path, today=TODAY)
    assert result["count"] == 1, result
    assert sorted(result["clusters"][0]["episodes"]) == ["e1.md", "e2.md"], result


def test_a_genuinely_non_raw_status_still_drops_out(tmp_path):
    """The negative case: comment-stripping must not admit graduated episodes."""
    episodes = tmp_path / "knowledge" / "odin-brain" / "episodes"
    episodes.mkdir(parents=True)
    for name, status in (("e1.md", "raw"), ("e2.md", "matured  # was raw")):
        (episodes / name).write_text(
            "---\n"
            f"status: {status}\n"
            "created: 2026-08-01\n"
            "keywords: [thunderball, moonraker, skyfall]\n"
            "---\n\nbody\n",
            encoding="utf-8",
        )

    result = oc.analyze_reflect_clusters(tmp_path, today=TODAY)
    assert result["count"] == 0, result
