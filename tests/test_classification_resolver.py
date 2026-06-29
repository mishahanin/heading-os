#!/usr/bin/env python3
"""Regression tests for the corporate-vs-ceo-only classification resolver.

get_classification() in scripts/utils/workspace.py is the SSOT deciding whether
a file is shared with all executives (corporate) or stays CEO-private (ceo-only).
A regression here could leak a ceo-only file to the corporate repo.

HEADING OS step 7 (2026-06-14): the resolver no longer reads config/classification.json.
It is now a thin two-value collapse of the three-value routing map
(config/routing-map.yaml), the single classification input:

    routing 'private'   -> 'ceo-only'
    routing 'corporate' -> 'corporate'
    routing 'engine'    -> 'corporate'

These tests pin (a) the collapse mapping, (b) the is_corporate wrapper, and
(c) the broken-map fail-closed-to-ceo-only behavior, by stubbing the routing layer.

Originally added 2026-06-09 workspace deep audit; rewritten for step 7.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import workspace  # noqa: E402


@pytest.fixture
def route(monkeypatch):
    """Stub get_routing_destination with a path->dest dict (default 'engine')."""
    def _set(mapping, default="engine"):
        monkeypatch.setattr(
            workspace, "get_routing_destination",
            lambda p: mapping.get(p.replace("\\", "/").lstrip("/"), default),
        )
    return _set


def test_collapse_private_to_ceo_only(route):
    route({"crm/contacts/x.md": "private"})
    assert workspace.get_classification("crm/contacts/x.md") == "ceo-only"


def test_collapse_corporate_to_corporate(route):
    route({"context/strategy.md": "corporate"})
    assert workspace.get_classification("context/strategy.md") == "corporate"


def test_collapse_engine_to_corporate(route):
    """Engine code is not private — it collapses into the shareable bucket."""
    route({"scripts/browser.py": "engine"})
    assert workspace.get_classification("scripts/browser.py") == "corporate"


def test_unmatched_uses_routing_default(route):
    """Routing default is 'engine' -> a code-ish unmatched path resolves corporate."""
    route({}, default="engine")
    assert workspace.get_classification("scripts/new_thing.py") == "corporate"


def test_broken_map_fails_closed_to_ceo_only(route):
    """A broken routing-map.yaml forces default 'private' -> everything ceo-only."""
    route({}, default="private")
    assert workspace.get_classification("anything/at/all.md") == "ceo-only"


def test_path_normalization(route):
    route({"scripts/x.py": "engine"})
    assert workspace.get_classification("\\scripts\\x.py") == "corporate"
    assert workspace.get_classification("/scripts/x.py") == "corporate"


def test_is_corporate_wrapper(route):
    route({"scripts/x.py": "engine", "threads/business/x.md": "private"})
    assert workspace.is_corporate("scripts/x.py") is True
    assert workspace.is_corporate("threads/business/x.md") is False


def test_ceo_only_helpers_read_private_routing_keys(monkeypatch):
    """get_ceo_only_scripts/references derive from explicit 'private' rule keys."""
    monkeypatch.setattr(workspace, "load_routing_map", lambda: {"default": "engine", "rules": {
        "scripts/modem-tune.py": "private",
        "scripts/browser.py": "engine",
        "reference/misha-voice.md": "private",
        "reference/": "engine",  # trailing-slash dir key must be ignored for refs
        "scripts/archive/": "private",  # dir key (not *.py file) must be ignored for scripts
    }})
    assert workspace.get_ceo_only_scripts() == {"modem-tune.py"}
    assert workspace.get_ceo_only_references() == {"misha-voice.md"}
