"""Three guards in front of a delete, each of which let the root through.

Both publication paths clear a destination before they write it:
`publish-service.copy_includes` calls `rmtree_force` on a directory include
before copying into it, and `pull-service-state` deletes the previous mirror
copy before renaming the new one in. The value that names that destination came
from a hand-edited config file in each case, and in each case the guard that was
written to bound it did not bound the one value that matters.

MEASURED 2026-08-29, by calling the functions:

    _contained(dest, ".")        -> dest itself     (rmtree of the whole clone,
    _contained(dest, "")         -> dest itself      including its .git)
    _vm_path(["..",   ...])      -> accepted        (deletes the mirror's parent)
    _vm_path([".",    ...])      -> accepted        (deletes the mirror)
    _vm_path(["/tmp", ...])      -> accepted        (deletes /tmp)
    _vm_path(["x", "engine", 5]) -> AttributeError  (not the ValueError main catches)

None of these fires on the config files as they stand today. Each needs one
hand edit, and a hand-edited config is precisely the input class all three
guards exist for. `publish-service`'s own `(dest / ".git").exists()` check runs
BEFORE `copy_includes`, so it cannot save the clone.

The fourth case here is smaller and the same shape: a `SERVICE-BUILD.json`
holding valid JSON that is not an object raised AttributeError on `.get`, which
is neither JSONDecodeError nor ValueError, so it walked through the handler
written for a corrupted marker. It lands after the mirror has already been
rmtree'd and rewritten, leaving the downstream clone dirty and uncommitted.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def publish():
    return _load("publish_service_probe", "scripts/publish-service.py")


@pytest.fixture(scope="module")
def pull():
    return _load("pull_service_state_probe", "scripts/pull-service-state.py")


# ============================================================
# 1. an include that resolves to the destination root
# ============================================================

ROOT_SPELLINGS = [".", "", "./", "./.", "sub/.."]


@pytest.mark.parametrize("rel", ROOT_SPELLINGS, ids=[repr(r) for r in ROOT_SPELLINGS])
def test_an_include_that_is_the_root_is_refused(publish, tmp_path, rel):
    dest = tmp_path / "clone"
    dest.mkdir()
    with pytest.raises(ValueError, match="IS "):
        publish._contained(dest, rel)


ESCAPES = ["../escape", "../../escape", "sub/../../escape"]


@pytest.mark.parametrize("rel", ESCAPES)
def test_an_include_that_escapes_is_still_refused(publish, tmp_path, rel):
    dest = tmp_path / "clone"
    dest.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        publish._contained(dest, rel)


CONTAINED = ["sub", "sub/deep", "a/b/c.md", "docs/index.html"]


@pytest.mark.parametrize("rel", CONTAINED)
def test_an_ordinary_include_still_resolves(publish, tmp_path, rel):
    """The other direction. A guard that refused everything would satisfy both
    sets above and break every real manifest entry."""
    dest = tmp_path / "clone"
    dest.mkdir()
    assert publish._contained(dest, rel) == (dest / rel).resolve()


def test_the_three_verdicts_are_all_reachable(publish, tmp_path):
    dest = tmp_path / "clone"
    dest.mkdir()

    def verdict(rel):
        try:
            publish._contained(dest, rel)
        except ValueError as exc:
            return "root" if " IS " in str(exc) else "escape"
        return "ok"

    assert verdict(".") == "root"
    assert verdict("../x") == "escape"
    assert verdict("sub") == "ok"


# ============================================================
# 2. a build marker holding the wrong kind of JSON
# ============================================================

MARKERS = [
    ("[1]", 1),
    ('"x"', 1),
    ("null", 1),
    ("3", 1),
    ("{", 1),                 # not JSON at all
    ('{"build": 4}', 5),      # the working case
    ("{}", 1),
]


@pytest.mark.parametrize("content, expected", MARKERS,
                         ids=[c for c, _ in MARKERS])
def test_a_wrong_shaped_marker_restarts_the_count(publish, tmp_path, content,
                                                  expected):
    dest = tmp_path / "clone"
    dest.mkdir()
    (dest / "SERVICE-BUILD.json").write_text(content, encoding="utf-8")
    publish.write_build_marker(dest)
    written = json.loads((dest / "SERVICE-BUILD.json").read_text(encoding="utf-8"))
    assert written["build"] == expected


# ============================================================
# 3. a mirror name that is a path
# ============================================================

# The absolute case is what makes this a delete OUTSIDE the mirror, so it has
# to be here; `noqa: S108` because the linter reads any absolute scratch path as
# an insecure temp file, and this one is a fixture the guard must refuse.
BAD_NAMES = ["..", ".", "", "/tmp", "a/b", "../../etc", "sub/"]  # noqa: S108


@pytest.mark.parametrize("name", BAD_NAMES, ids=[repr(n) for n in BAD_NAMES])
def test_a_mirror_name_that_is_a_path_is_refused(pull, name):
    with pytest.raises(ValueError, match="plain directory name"):
        pull._vm_path([name, "engine", "x"], {"engine": "/srv/heading"})


BAD_TYPES = [
    ["x", "engine", 5],
    ["x", 5],
    [5, "engine", "x"],
    ["x", "engine", None],
]


@pytest.mark.parametrize("entry", BAD_TYPES, ids=[str(e) for e in BAD_TYPES])
def test_a_non_string_entry_is_a_value_error_not_a_traceback(pull, entry):
    """`main` catches ValueError only, so an AttributeError out of `lstrip`
    ended the run in a traceback instead of the named reason."""
    with pytest.raises(ValueError):
        pull._vm_path(entry, {"engine": "/srv/heading"})


GOOD_ENTRIES = [
    (["ok", "engine", "x"], ("ok", "/srv/heading/x")),
    (["ok", "engine"], ("ok", "/srv/heading/engine")),
    (["state", "data", "sub/dir"], ("state", "/srv/data/sub/dir")),
]


@pytest.mark.parametrize("entry, expected", GOOD_ENTRIES,
                         ids=[e[0][0] + "-" + str(len(e[0])) for e in GOOD_ENTRIES])
def test_an_ordinary_entry_still_resolves(pull, entry, expected):
    """The other direction, and it covers both the 2- and 3-element forms."""
    assert pull._vm_path(entry, {"engine": "/srv/heading",
                                 "data": "/srv/data"}) == expected


def test_a_non_string_vm_root_is_also_named(pull):
    with pytest.raises(ValueError, match="must be a string"):
        pull._vm_path(["ok", "engine", "x"], {"engine": 5})


# ============================================================
# 4. the config reader that promised never to raise
# ============================================================

def test_a_non_utf8_service_config_does_not_kill_the_import(pull, tmp_path,
                                                            monkeypatch):
    """`UnicodeDecodeError` is a ValueError, not an OSError, and this reader
    ran at import, so a config saved as UTF-16 tracebacked before `main`
    could print the named message the docstring promises.

    The reader is `service_config()`, resolved on call, since 2026-08-31; the
    never-raises promise it is tested for here is unchanged.
    """
    cfg = tmp_path / "service-host.json"
    cfg.write_bytes(b'{"vm_engine_root": "\xff\xfe/srv"}')
    # The function resolves its own path, so the seam is the resolver.
    monkeypatch.setattr(pull, "resolve_config_with_example", lambda *a, **k: cfg)
    data, error = pull.service_config()
    assert data == {}
    assert error and "could not be read" in error


def test_a_good_service_config_still_loads(pull, tmp_path, monkeypatch):
    """The other direction, so a reader that returned `({}, error)` for
    everything could not satisfy the case above."""
    cfg = tmp_path / "service-host.json"
    cfg.write_text('{"vm_engine_root": "/srv/heading"}', encoding="utf-8")
    monkeypatch.setattr(pull, "resolve_config_with_example", lambda *a, **k: cfg)
    data, error = pull.service_config()
    assert error is None
    assert data == {"vm_engine_root": "/srv/heading"}
