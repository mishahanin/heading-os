#!/usr/bin/env python3
"""`get_client` in scripts/firecrawl.py must import the SDK, not itself.

The script is named `firecrawl.py` and the package it needs is named
`firecrawl`. Run the documented way, `python scripts/firecrawl.py scrape <url>`,
Python puts `scripts/` at `sys.path[0]`, so a bare `import firecrawl` inside the
script resolves to THE SCRIPT, which has no `Firecrawl` attribute. The command
then dies with `AttributeError` before it reaches the network.

`get_client` defends against that on two axes, and each one is a separate way to
lose:

  * it drops `scripts/` from `sys.path` for the duration of the import, so the
    name resolves past the script to the installed package;
  * it pops any `sys.modules["firecrawl"]` first, because a self-import cached
    by an earlier caller is returned by `import` whatever `sys.path` says.

Both are restored afterwards, so the rest of the process sees no change.

Until this file existed, deleting the whole defence left the suite green: the
two tests that drive `cmd_scrape` and friends replace `get_client` wholesale
with `monkeypatch.setattr(fc, "get_client", ...)`, so nothing anywhere ran its
body. These tests run the real function against a stand-in package planted on
`sys.path`, which is why they can tell the difference. No network, no real SDK
construction, and no reading of the script's own source text.
"""
import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Deliberately NOT "firecrawl": loading the script under the very name it is
# fighting over would plant the collision this file is here to measure.
_MODULE_NAME = "selfimport_probe_firecrawl"

# The stand-in package answers to `Firecrawl`, which the script module does not.
# `MARKER` is what proves which of the two the import actually reached.
MARKER = "stand-in-firecrawl-sdk"

_STUB_SOURCE = textwrap.dedent(
    f'''
    """A stand-in for the Firecrawl SDK. Constructs nothing, calls nothing."""

    MARKER = "{MARKER}"


    class Firecrawl:
        def __init__(self, api_key=None, timeout=None):
            self.api_key = api_key
            self.timeout = timeout
            self.marker = MARKER
    '''
)


@pytest.fixture(scope="module")
def fc():
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, str(ROOT / "scripts" / "firecrawl.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def stub_dir(tmp_path):
    """A directory holding a `firecrawl` the import can find, if it looks past us."""
    d = tmp_path / "stand-in-site-packages"
    d.mkdir()
    (d / "firecrawl.py").write_text(_STUB_SOURCE, encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _leave_sys_modules_as_found():
    """`get_client` writes to `sys.modules`; a leak here poisons the session.

    Saved by identity and put back exactly, including the absent case: leaving a
    stand-in package registered under `firecrawl` would hand the next test in
    the session a module the operator never installed.
    """
    sentinel = object()
    before = sys.modules.get("firecrawl", sentinel)
    try:
        yield
    finally:
        if before is sentinel:
            sys.modules.pop("firecrawl", None)
        else:
            sys.modules["firecrawl"] = before


@pytest.fixture()
def no_real_key(fc, monkeypatch):
    """No credential is read and none is needed; the stand-in stores the string."""
    monkeypatch.setattr(fc, "load_api_key", lambda name: "not-a-real-key")


def _scripts_dir(fc) -> str:
    return os.path.dirname(os.path.abspath(fc.__file__))


def test_get_client_reaches_the_sdk_while_the_script_shadows_it(
        fc, stub_dir, no_real_key, monkeypatch):
    """The real CLI layout: `scripts/` first on the path, exactly as python puts it.

    Without the path filter the import resolves to scripts/firecrawl.py, and the
    next line, `firecrawl_pkg.Firecrawl`, raises AttributeError. That is the
    documented `python scripts/firecrawl.py scrape <url>` crash, reproduced.
    """
    monkeypatch.setattr(
        sys, "path", [_scripts_dir(fc), str(stub_dir), *sys.path]
    )
    monkeypatch.delitem(sys.modules, "firecrawl", raising=False)

    client = fc.get_client()

    assert getattr(client, "marker", None) == MARKER, (
        "get_client did not reach the Firecrawl package; it resolved the name "
        f"to {type(client).__module__}"
    )
    # The scanner flags the `api_key` keyword, not the value. The value is the
    # literal string this file's own fixture hands to the stub, and it is named
    # so a reader cannot mistake it for a credential.
    assert client.api_key == "not-a-real-key"  # pragma: allowlist secret
    assert client.timeout == 30.0


def test_get_client_puts_sys_path_back(fc, stub_dir, no_real_key, monkeypatch):
    """The filter is a loan, not a change. A caller that keeps running needs it back."""
    wanted = [_scripts_dir(fc), str(stub_dir), *sys.path]
    monkeypatch.setattr(sys, "path", list(wanted))
    monkeypatch.delitem(sys.modules, "firecrawl", raising=False)

    fc.get_client()

    assert sys.path == wanted, (
        "get_client kept the trimmed sys.path; every later import in the "
        "process now searches a shorter path than the operator configured"
    )


def test_a_cached_self_import_does_not_survive_into_get_client(
        fc, stub_dir, no_real_key, monkeypatch):
    """The second axis, and the one `sys.path` cannot cover.

    Once anything has imported the script under the name `firecrawl`, `import
    firecrawl` returns that cached module and never consults `sys.path` at all.
    The path filter is powerless here; only the `sys.modules.pop` is not.
    """
    monkeypatch.setattr(sys, "path", [str(stub_dir), *sys.path])
    monkeypatch.setitem(sys.modules, "firecrawl", fc)

    client = fc.get_client()

    assert getattr(client, "marker", None) == MARKER, (
        "a cached self-import was handed back to get_client"
    )


def test_the_cached_self_import_is_restored_afterwards(
        fc, stub_dir, no_real_key, monkeypatch):
    """Popping it is fine; keeping it popped changes what the caller imported.

    Whoever put the script in `sys.modules` under that name still holds a
    reference to it. Swapping their entry for a different module underneath them
    is a mutation `get_client` was never asked to make.
    """
    monkeypatch.setattr(sys, "path", [str(stub_dir), *sys.path])
    monkeypatch.setitem(sys.modules, "firecrawl", fc)

    fc.get_client()

    assert sys.modules.get("firecrawl") is fc, (
        "get_client left the stand-in registered under `firecrawl`; the caller's "
        "own module was replaced"
    )
