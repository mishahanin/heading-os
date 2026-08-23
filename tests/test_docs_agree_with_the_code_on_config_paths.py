"""Where a doc names a live config path, it must be the path the code reads.

`docs/daemons.html` told the operator Sentinel's config was
`scripts/sentinel_config.yaml` — inside the engine repo, which is public.
`docs/TELEGRAM-AND-ALERTS.md` and `scripts/sentinel.py` both say the live file
is `<data-root>/config/sentinel_config.yaml`, resolved through
`get_data_config_dir()`. The code is the tiebreak, so `daemons.html` was wrong,
in the direction that costs most:

  - an operator following it writes chat IDs and monitored-contact names into
    the shareable engine clone, which is the exact leak the segregation
    contract exists to stop; and
  - Sentinel never reads that file, so the operator's config silently does
    nothing and the symptom points nowhere near the cause.

This test asserts the agreement rather than the wording, so a rewrite of either
page keeps the guarantee.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAEMONS = ROOT / "docs" / "daemons.html"
TELEGRAM = ROOT / "docs" / "TELEGRAM-AND-ALERTS.md"
SENTINEL = ROOT / "scripts" / "sentinel.py"

# `scripts/sentinel_config.yaml` — the engine-tree path that does not exist and
# must never be presented as the live one. The `.example.` sibling IS in the
# engine and is fine.
ENGINE_LIVE_PATH = re.compile(r"scripts/sentinel_config\.yaml")


def test_the_engine_ships_only_the_template():
    """Ground truth for the whole test: the non-example file is not here."""
    assert (ROOT / "scripts" / "sentinel_config.example.yaml").exists()
    assert not (ROOT / "scripts" / "sentinel_config.yaml").exists(), (
        "a live sentinel_config.yaml is sitting in the engine tree. That is "
        "instance config in a public repo; move it to <data-root>/config/."
    )


def test_the_code_resolves_the_config_under_the_data_root():
    source = SENTINEL.read_text(encoding="utf-8")
    assert "get_data_config_dir" in source or "config_path" in source
    # The example file is the FALLBACK, not the live path.
    assert "sentinel_config.example.yaml" in source


def test_daemons_page_does_not_present_the_engine_path_as_the_live_config():
    text = DAEMONS.read_text(encoding="utf-8")
    for match in ENGINE_LIVE_PATH.finditer(text):
        window = text[max(0, match.start() - 400):match.end() + 200]
        assert "until 2026-08-23" in window or "never" in window, (
            "docs/daemons.html names scripts/sentinel_config.yaml without "
            "marking it as the wrong path. The live config is "
            "<data-root>/config/sentinel_config.yaml."
        )


def test_daemons_page_names_the_data_overlay():
    text = DAEMONS.read_text(encoding="utf-8")
    assert "config/sentinel_config.yaml" in text
    assert "data" in text.lower() and "overlay" in text.lower(), (
        "the page should say plainly that the live config lives in the private "
        "data overlay, not merely avoid the wrong path"
    )


def test_the_two_pages_do_not_contradict_each_other():
    """The defect was two pages disagreeing with nothing comparing them."""
    telegram = TELEGRAM.read_text(encoding="utf-8")
    assert "config/sentinel_config.yaml" in telegram
    daemons = DAEMONS.read_text(encoding="utf-8")
    assert "config/sentinel_config.yaml" in daemons
