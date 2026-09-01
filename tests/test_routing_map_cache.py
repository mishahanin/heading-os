#!/usr/bin/env python3
"""Guards for the routing-map parse cache in scripts/utils/workspace.py.

Before 2026-08-20 load_routing_map() yaml-parsed config/routing-map.yaml on EVERY
get_routing_destination() call: measured 6.404 ms/call, so the 1535-file
engine-tree-clean scan spent 9.63 s parsing the same file 1535 times. Caching it
took scan_engine_repo() to 0.202 s.

The cache is keyed on file IDENTITY (path + st_mtime_ns + st_size), not on nothing.
That distinction is the point of this file: routing-map.yaml is the classifier that
decides what counts as private data, so a long-running daemon (bridge-daemon,
sentinel) going blind to an edit of it is a leak path, not a staleness annoyance.

Three properties are pinned:
  (a) two calls against an unchanged file parse ONCE (the speed claim);
  (b) a rewritten map with a different mtime IS re-read (the daemon-blindness guard);
  (c) a caller mutating the returned dict cannot corrupt the next caller's copy.
"""
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import workspace, yamlio  # noqa: E402

MAP = """\
default: engine
rules:
  crm/: private
  knowledge/shared/: corporate
"""

MAP_V2 = """\
default: private
rules:
  crm/: private
  scripts/: engine
"""


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Point load_routing_map() at a temp workspace and clear the cache around it."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "routing-map.yaml").write_text(MAP, encoding="utf-8")
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    workspace._load_routing_map_cached.cache_clear()
    yield tmp_path
    workspace._load_routing_map_cached.cache_clear()


@pytest.fixture
def parse_counter(monkeypatch):
    """Count real YAML parses through the loader workspace.py actually calls."""
    calls = []
    real = yamlio.safe_load

    def counting(stream):
        calls.append(1)
        return real(stream)

    monkeypatch.setattr(yamlio, "safe_load", counting)
    return calls


def test_unchanged_file_parses_once(fake_root, parse_counter):
    """(a) Repeated calls against an unchanged map hit the cache, not the parser."""
    for _ in range(50):
        assert workspace.load_routing_map()["default"] == "engine"
    assert len(parse_counter) == 1, f"expected 1 parse, got {len(parse_counter)}"


def test_get_routing_destination_does_not_reparse(fake_root, parse_counter):
    """The hot path — the one that cost 9.63 s — parses once for many resolutions."""
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"
    assert workspace.get_routing_destination("knowledge/shared/x.md") == "corporate"
    assert workspace.get_routing_destination("scripts/foo.py") == "engine"
    assert len(parse_counter) == 1


def test_edited_map_is_reread(fake_root, parse_counter):
    """(b) Daemon-blindness guard: a rewritten map with a new mtime is re-parsed.

    Written with an explicit mtime bump rather than a sleep, so the guard holds on a
    filesystem with coarse timestamps as well as on this one.
    """
    assert workspace.load_routing_map()["default"] == "engine"

    path = fake_root / "config" / "routing-map.yaml"
    path.write_text(MAP_V2, encoding="utf-8")
    st = path.stat()
    bumped = st.st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(bumped, bumped))

    m = workspace.load_routing_map()
    assert m["default"] == "private", "edit to routing-map.yaml was not seen"
    assert m["rules"]["scripts/"] == "engine"
    assert "knowledge/shared/" not in m["rules"]
    assert len(parse_counter) == 2


def test_touch_only_content_change_is_reread(fake_root):
    """Same-length rewrite still re-reads: mtime alone is enough to miss the cache."""
    assert workspace.load_routing_map()["rules"]["knowledge/shared/"] == "corporate"
    path = fake_root / "config" / "routing-map.yaml"
    same_size = MAP.replace("corporate", "private  ")  # identical byte length
    assert len(same_size) == len(MAP)
    time.sleep(0.01)
    path.write_text(same_size, encoding="utf-8")
    assert workspace.load_routing_map()["rules"]["knowledge/shared/"] == "private"


def test_caller_mutation_does_not_corrupt_next_caller(fake_root):
    """(c) The returned map is a copy — a caller that mutates it poisons nobody."""
    first = workspace.load_routing_map()
    first["rules"]["crm/"] = "engine"
    first["rules"]["injected/"] = "engine"
    first["default"] = "corporate"

    second = workspace.load_routing_map()
    assert second["rules"]["crm/"] == "private"
    assert "injected/" not in second["rules"]
    assert second["default"] == "engine"
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"


def test_a_bare_prefix_neighbour_does_not_inherit_a_rule(fake_root):
    """The boundary, over a synthetic map so it cannot drift with the real one.

    `crm/` is the only private rule in MAP, so `crmx/` must fall through to the
    `engine` default rather than being swept up by a prefix comparison. Held
    here as well as against the live map (`tests/test_routing_map.py`) because
    the live map's rule set is the operator's and can change under the test,
    while this one is three lines at the top of this file.
    """
    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"
    assert workspace.get_routing_destination("crmx/contacts/a.md") == "engine"
    assert workspace.get_routing_destination("knowledge/shared-notes/x.md") == "engine"
    assert workspace.get_routing_destination("knowledge/shared/x.md") == "corporate"


def test_missing_map_fails_closed_private(tmp_path, monkeypatch):
    """No config/routing-map.yaml at all: unchanged fail-closed behaviour."""
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    workspace._load_routing_map_cached.cache_clear()
    assert workspace.load_routing_map() == {"default": "private", "rules": {}}
    assert workspace.get_routing_destination("anything/at/all.md") == "private"


def test_broken_map_fails_closed_private(fake_root):
    """Unparseable YAML: still default 'private', and the failure is not cached wrong."""
    path = fake_root / "config" / "routing-map.yaml"
    time.sleep(0.01)
    path.write_text("default: engine\nrules:\n  - [unclosed\n", encoding="utf-8")
    assert workspace.load_routing_map() == {"default": "private", "rules": {}}

    # And once the operator fixes it, the fix is picked up (not pinned to the failure).
    time.sleep(0.01)
    path.write_text(MAP, encoding="utf-8")
    assert workspace.load_routing_map()["default"] == "engine"


def test_yamlio_matches_pyyaml_safe_load():
    """The C loader is a drop-in: same result as yaml.safe_load on the real map."""
    import yaml

    src = (Path(__file__).resolve().parent.parent / "config" / "routing-map.yaml").read_text(
        encoding="utf-8"
    )
    assert yamlio.safe_load(src) == yaml.safe_load(src)
    assert yamlio.SafeLoader.__name__ in {"CSafeLoader", "SafeLoader"}


def test_yamlio_strictness_divergence_fails_closed(fake_root):
    """An unsupported MINOR %YAML version: CSafeLoader rejects, pure-Python accepts.

    Pinned because the fail-closed contract rests on the rejection arriving as a
    yaml.YAMLError: anything else escapes the ``except (OSError, yaml.YAMLError)``
    handler and turns a malformed routing map into a traceback instead of an
    all-private classification.

    The docstring here used to say this was "the one divergence" and that the
    divergence direction was always STRICTER. Both halves were false, and the
    14-case corpus behind the claim contained no tab. The two looser cases are
    pinned below.
    """
    path = fake_root / "config" / "routing-map.yaml"
    time.sleep(0.01)
    path.write_text("%YAML 1.9\n---\ndefault: engine\nrules: {}\n", encoding="utf-8")

    assert workspace.load_routing_map() == {"default": "private", "rules": {}}
    assert workspace.get_routing_destination("scripts/anything.py") == "private"


def test_an_unsupported_major_version_is_rejected_by_both_loaders():
    """The narrowing. `%YAML 2.0` is NOT a divergence; only a minor bump is."""
    for directive in ("%YAML 2.0", "%YAML 9.9"):
        src = f"{directive}\n---\ndefault: engine\n"
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(src)
        with pytest.raises(yaml.YAMLError):
            yamlio.safe_load(src)


@pytest.mark.parametrize("src,expected", [
    ("default: engine\nrules:\n  crm/:\tprivate\n",
     {"default": "engine", "rules": {"crm/": "private"}}),
    ("default: engine\nrules:\n  crm/: pri\tvate\n",
     {"default": "engine", "rules": {"crm/": "pri\tvate"}}),
])
def test_the_tab_divergence_runs_the_other_way(src, expected):
    """The second and third divergences, and they are LOOSER, not stricter.

    A tab between a key and its value is a ScannerError under the pure-Python
    SafeLoader that `yaml.safe_load` binds, and parses fine under CSafeLoader.
    The module docstring promised the only divergence was in the stricter
    direction, so a future fail-closed handler could have been built on that
    promise.
    """
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(src)

    assert yamlio.safe_load(src) == expected


def test_a_tab_cannot_widen_what_is_shareable(fake_root):
    """The consequence that matters, held down.

    The classifier's safety comes from validating the DESTINATION, not from the
    parser refusing the file. A tab-borne value that is not a legal destination
    still fails closed to private; a legal one is the destination the file's
    author wrote.
    """
    path = fake_root / "config" / "routing-map.yaml"
    time.sleep(0.01)
    path.write_text("default: engine\nrules:\n  crm/: pri\tvate\n", encoding="utf-8")

    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"


def test_a_tab_between_key_and_value_yields_the_written_destination(fake_root):
    path = fake_root / "config" / "routing-map.yaml"
    time.sleep(0.01)
    path.write_text("default: engine\nrules:\n  crm/:\tprivate\n", encoding="utf-8")

    assert workspace.get_routing_destination("crm/contacts/a.md") == "private"
    assert workspace.get_routing_destination("scripts/x.py") == "engine"
