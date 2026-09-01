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

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DAEMONS = ROOT / "docs" / "daemons.html"
TELEGRAM = ROOT / "docs" / "TELEGRAM-AND-ALERTS.md"
SENTINEL = ROOT / "scripts" / "sentinel.py"
EXAMPLE = ROOT / "scripts" / "sentinel_config.example.yaml"


@pytest.fixture(scope="module")
def sentinel():
    """`scripts/sentinel.py` loaded by path, so `config_file()` can be CALLED.

    Importing the daemon module runs no daemon: everything below the constants
    is behind `main()`.
    """
    spec = importlib.util.spec_from_file_location("_shard43_sentinel", SENTINEL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_shard43_sentinel"] = module
    spec.loader.exec_module(module)
    return module


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


def test_the_code_resolves_the_config_under_the_data_root(sentinel, tmp_path, monkeypatch):
    """The tiebreak the whole file rests on, ASKED OF THE RESOLVER.

    This test used to read the source text and accept
    `"get_data_config_dir" in source or "config_path" in source`. Neither
    disjunct binds anything: MEASURED 2026-09-01, `get_data_config_dir` occurs in
    `scripts/sentinel.py` exactly once, inside a COMMENT, and `config_path` is
    the name of a local variable and a CLI argument. Replacing the whole body of
    `config_file()` with `return WORKSPACE_ROOT / "scripts" / "sentinel_config.yaml"`
    (the precise defect `docs/daemons.html` documented, an engine-tree path in a
    public repo) left all five tests in this file green. The guard measured the
    comment that explains the fix.

    So call it. With a real config in the overlay, the resolver must return that
    file, under the data root and nowhere else.
    """
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True)
    live = overlay / "config" / "sentinel_config.yaml"
    live.write_text("monitoring: {}\n", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    resolved = sentinel.config_file()
    assert resolved == live, (
        f"sentinel resolves its config to {resolved}, not to the data overlay. "
        f"docs/daemons.html and docs/TELEGRAM-AND-ALERTS.md both promise "
        f"<data-root>/config/sentinel_config.yaml."
    )


def test_the_engine_example_is_the_fallback_and_never_a_live_engine_path(
    sentinel, tmp_path, monkeypatch
):
    """With no config in the overlay the resolver falls back to the SHIPPED
    example, not to a `scripts/sentinel_config.yaml` that does not exist.

    The second half is the one that matters: a fallback pointing into the engine
    tree is what would invite an operator to create the file there.
    """
    overlay = tmp_path / "empty-overlay"
    (overlay / "config").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))

    resolved = sentinel.config_file()
    assert resolved == EXAMPLE, f"fallback resolved to {resolved}, not {EXAMPLE}"
    assert resolved.name.endswith(".example.yaml"), resolved
    assert resolved != ROOT / "scripts" / "sentinel_config.yaml"


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
