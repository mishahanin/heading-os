"""``capabilities.list_capabilities`` returned two payload shapes, not one.

The populated path returned ``skills``, ``count``, ``category_counts``,
``category_order`` and ``data_time``. The path taken when ``.claude/skills/``
does not exist returned three of those five, dropping ``category_counts`` and
``category_order``. The browser iterates ``category_order`` to draw its section
headers, so the shape broke on exactly one kind of install: the fresh clone with
no skills yet, the install least able to diagnose it.

Measured 2026-08-29 before the fix::

    degraded keys : ['count', 'data_time', 'skills']
    populated keys: ['category_counts', 'category_order', 'count', 'data_time',
                     'skills']

The key set is DERIVED from a real populated call rather than hardcoded here, so
the test still binds when a sixth key is added to one path and not the other.
That is the whole defect: a key added to one return and not its twin.
"""
import pytest

from scripts.bridge_daemon.sources import capabilities


@pytest.fixture
def populated(tmp_path):
    """A workspace with one real skill, so the populated path is exercised."""
    ws = tmp_path / "populated"
    d = ws / ".claude" / "skills" / "dossier"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: dossier\ndescription: Build a dossier on a target.\n---\nbody\n",
        encoding="utf-8",
    )
    return ws


@pytest.fixture
def degraded(tmp_path):
    """A workspace with no .claude/skills/ at all."""
    ws = tmp_path / "degraded"
    ws.mkdir()
    return ws


def test_the_populated_path_really_is_populated(populated):
    """Corpus guard: an empty populated result would make the diff vacuous."""
    result = capabilities.list_capabilities(populated)
    assert result["count"] == 1
    assert result["skills"], "no skills parsed, so the key comparison proves nothing"


def test_the_degraded_path_really_is_the_degraded_one(degraded):
    assert not (degraded / ".claude" / "skills").exists()
    assert capabilities.list_capabilities(degraded)["skills"] == []


def test_both_paths_return_the_same_key_set(populated, degraded):
    populated_keys = set(capabilities.list_capabilities(populated))
    degraded_keys = set(capabilities.list_capabilities(degraded))
    assert degraded_keys == populated_keys, (
        "missing from the degraded return: "
        f"{sorted(populated_keys - degraded_keys)}; extra: "
        f"{sorted(degraded_keys - populated_keys)}"
    )


def test_the_degraded_return_carries_the_render_order_the_browser_iterates(degraded):
    result = capabilities.list_capabilities(degraded)
    assert result["category_order"] == capabilities.CATEGORY_ORDER


def test_the_degraded_return_carries_an_empty_count_map_not_a_missing_one(degraded):
    result = capabilities.list_capabilities(degraded)
    assert result["category_counts"] == {}


def test_the_docstring_promises_every_key_the_function_returns(populated):
    """The Returns block named three of the five keys the code produced.

    Read off the live docstring rather than a copy of it, so the check cannot
    drift from the function it describes.
    """
    doc = capabilities.list_capabilities.__doc__ or ""
    returned = capabilities.list_capabilities(populated)
    assert returned, "empty result, nothing to check the docstring against"
    for key in returned:
        assert key in doc, f"{key!r} is returned but the Returns block never names it"
