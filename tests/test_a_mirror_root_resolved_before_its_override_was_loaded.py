"""The mirror root was resolved one line above the load that supplies it.

`pull-service-state.main()` opened with:

    data_root = get_data_root()
    load_env()

`get_data_root()` reads HEADING_OS_DATA out of `os.environ`, and `load_env()`
is the function that copies `.env` into `os.environ`. So on any instance whose
data root is pinned in `.env` -- which is how this workspace pins it -- the
override was read before it existed, and the mirror landed under whatever the
fallback resolved to while the run printed `pulled=N` and exited 0.

MEASURED 2026-08-30 with HEADING_OS_DATA=<overlay> in a scratch .env:

    get_data_root() before load_env()  -> <workspace>/examples
    get_data_root() after  load_env()  -> <overlay>

This file already burned this exact ordering once: `vm_roots` carries a
docstring explaining that it stopped being a module-level dict because
"`load_env()` ... runs inside main(), which is AFTER module import, so the
overrides the docstring above promises were read before they existed". The
same run then did it again, in main, two lines down.

The real `get_data_root` and `load_env` are used here, against a scratch
workspace; nothing is faked but the workspace root they resolve from.
`state_dirs` returns an empty list so no scp is ever spawned.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import paths as paths_mod  # noqa: E402


@pytest.fixture(scope="module")
def pull():
    spec = importlib.util.spec_from_file_location(
        "pull_service_state_env_probe", ROOT / "scripts" / "pull-service-state.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pull_service_state_env_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A workspace whose .env pins HEADING_OS_DATA at a sibling overlay."""
    workspace = tmp_path / "engine"
    workspace.mkdir()
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (workspace / ".env").write_text(
        f"HEADING_OS_DATA={overlay}\nSERVICE_VM_HOST=vm.example.invalid\n",
        encoding="utf-8")

    monkeypatch.setattr(paths_mod, "get_workspace_root", lambda: workspace)
    # load_env uses os.environ.setdefault, so both keys must start absent AND
    # be removed afterwards: monkeypatch cannot restore a key it never saw set.
    for key in ("HEADING_OS_DATA", "SERVICE_VM_HOST"):
        monkeypatch.delenv(key, raising=False)
    try:
        yield workspace, overlay
    finally:
        for key in ("HEADING_OS_DATA", "SERVICE_VM_HOST"):
            os.environ.pop(key, None)


def test_the_env_override_actually_moves_the_data_root(scratch):
    """The premise. Without it the assertion below could pass on a tie."""
    workspace, overlay = scratch

    before = paths_mod.get_data_root()
    paths_mod.load_env()
    after = paths_mod.get_data_root()

    assert before != overlay, "the fallback already resolved to the overlay"
    assert after == overlay


def test_the_mirror_lands_under_the_root_that_dot_env_names(pull, scratch,
                                                            monkeypatch, capsys):
    workspace, overlay = scratch
    monkeypatch.setattr(pull, "state_dirs", list)

    rc = pull.main()
    capsys.readouterr()

    assert rc == 0
    expected = overlay / pull.mirror_rel()
    assert expected.is_dir(), (
        f"the mirror was not created under the .env root; the tree under "
        f"{workspace} holds "
        f"{sorted(p.name for p in workspace.iterdir())}")


def test_no_mirror_is_created_under_the_fallback_root(pull, scratch, monkeypatch,
                                                      capsys):
    """The wrong-root write is the damage, not just the wrong report."""
    workspace, overlay = scratch
    monkeypatch.setattr(pull, "state_dirs", list)

    pull.main()
    capsys.readouterr()

    stray = workspace / "examples" / pull.mirror_rel()
    assert not stray.exists(), (
        f"the mirror was written under the examples fallback at {stray}")


def test_the_host_still_comes_from_the_same_load(pull, scratch, monkeypatch,
                                                 capsys):
    """SERVICE_VM_HOST is read from .env too; moving load_env must not break it."""
    monkeypatch.setattr(pull, "state_dirs", list)

    rc = pull.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "SERVICE_VM_HOST not set" not in out
    assert "vm.example.invalid" in out


def test_an_explicit_environment_value_still_beats_dot_env(pull, scratch,
                                                           monkeypatch, capsys,
                                                           tmp_path):
    """load_env uses setdefault; the precedence must survive the reorder."""
    workspace, overlay = scratch
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(explicit))
    monkeypatch.setattr(pull, "state_dirs", list)

    pull.main()
    capsys.readouterr()

    assert (explicit / pull.mirror_rel()).is_dir()
    assert not (overlay / pull.mirror_rel()).exists()
