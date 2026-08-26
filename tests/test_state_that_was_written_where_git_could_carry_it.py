#!/usr/bin/env python3
"""Three writers put runtime state inside the public engine clone.

Operator law, restated 2026-08-26: no data from the DATA repository may ever sit
in the engine, and everything under `examples/` must be invented. The mechanism
that broke it is a write path, not authored text. With no private overlay
`get_data_root()` falls to its documented last resort `<workspace_root>/examples`,
which is INSIDE the clone, so any tool that writes to the data root writes into
the repository that gets pushed.

Found by running the suite in a worktree with no sibling overlay, not by reading.
What was on disk afterwards:

    examples/datastore/operations/tribe/fireside-state/errors.log
    examples/outputs/research/_drafts/exemplars/

The second one is the worse shape. `capture-design-exemplars.py` and its retry
sibling each called `OUTPUT_DIR.mkdir(parents=True, exist_ok=True)` at MODULE
level, so merely collecting the module wrote to disk: no call, no CLI run, no
flag. Both scripts already keep their playwright binding lazy for the same
import-purity reason (F-2.1) and the mkdir sat four lines below that comment.

Every gate passed all of it, because `config/routing-map.yaml` has no entry for
`examples/` and each path there fell to the `engine` default. The demo tree is a
closed manifest since the same day (`DEMO_MANIFEST`), which is the belt; these
are the braces, refusing the write at the source.

Guards the fix, in both directions: a refusal that also fired with a real overlay
would cost the operator their fireside state and every capture, and would pass
every leak assertion here while doing it.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from scripts.utils.paths import DataRootError  # noqa: E402

EXEMPLAR_SCRIPTS = [
    "capture-design-exemplars.py",
    "capture-design-exemplars-retry.py",
]


def _load(script: str, name: str):
    """Import a hyphenated CLI script by path. The names are not importable."""
    spec = importlib.util.spec_from_file_location(name, str(ENGINE / "scripts" / script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Importing a capture script writes nothing, anywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", EXEMPLAR_SCRIPTS)
def test_importing_a_capture_script_creates_no_directory(script, tmp_path):
    """Measured in a child process with the data root pointed at an empty tree.

    A child, not `importlib` in-process, because the assertion is about what the
    IMPORT does and this module has already imported plenty. Pointing
    HEADING_OS_DATA at a fresh empty directory makes the check independent of
    whether the machine running it has an overlay: whatever the resolver answers,
    nothing may appear under it.
    """
    data = tmp_path / "data"
    data.mkdir()
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, "-c",
         "import importlib.util,sys;"
         f"spec=importlib.util.spec_from_file_location('m', {str(ENGINE / 'scripts' / script)!r});"
         "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)"],
        cwd=str(ENGINE),
        env={"PATH": "/usr/bin:/bin", "HEADING_OS_DATA": str(data),
             "PYTHONPATH": str(ENGINE), "HOME": str(tmp_path)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"importing {script} failed:\n{proc.stderr}"
    made = sorted(p.relative_to(data) for p in data.rglob("*"))
    assert made == [], (
        f"importing {script} created {made} under the data root; the mkdir must "
        "run from main(), not at module level"
    )


@pytest.mark.parametrize("script", EXEMPLAR_SCRIPTS)
def test_the_capture_dir_is_still_created_when_the_script_actually_runs(
    script, tmp_path, monkeypatch
):
    """The other jaw. Deleting the mkdir would pass the test above and break the
    thing it was added for: without the directory every `page.screenshot(path=...)`
    failed, the error was swallowed into `result["error"]`, and the retry script
    printed ERR three times and exited 0."""
    target = tmp_path / "exemplars"
    mod = _load(script, f"cap_{script.replace('-', '_').removesuffix('.py')}")
    monkeypatch.setattr(mod, "OUTPUT_DIR", target)

    got = mod.prepare_output_dir()
    assert got.is_dir(), f"{script}: prepare_output_dir() did not create {got}"
    assert got == target.resolve(), (
        f"{script}: the capture directory landed at {got}, not at {target}"
    )


@pytest.mark.parametrize("script", EXEMPLAR_SCRIPTS)
def test_a_capture_refuses_a_directory_inside_the_engine_clone(
    script, tmp_path, monkeypatch
):
    """The guard asks where the write is going, not whether this machine has an
    overlay. Pointing OUTPUT_DIR at the demo tree is the exact resolution a clone
    with no overlay produces, and nothing may be created there."""
    mod = _load(script, f"capdemo_{script.replace('-', '_').removesuffix('.py')}")
    doomed = ENGINE / "examples" / "outputs" / "research" / "_drafts" / "exemplars"
    monkeypatch.setattr(mod, "OUTPUT_DIR", doomed)
    # Swept before AND after. A leftover from an earlier run turns this into a
    # test that reports the wrong thing: it would fail with "refused, but only
    # after creating" over a directory it never touched. A mutation run that
    # removed the guard left exactly that behind on 2026-08-27.
    shutil.rmtree(doomed.parent.parent, ignore_errors=True)
    try:
        with pytest.raises(DataRootError):
            mod.prepare_output_dir()
        assert not doomed.exists(), (
            f"{script} refused, but only after creating {doomed} inside the clone"
        )
    finally:
        shutil.rmtree(doomed.parent.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. Fireside state never lands in the engine clone
# ---------------------------------------------------------------------------
#
# Five writers shared one `STATE_DIR` and four of them carried their own
# `path.parent.mkdir(parents=True, exist_ok=True)`. They now share one funnel,
# `require_writable_state_dir()`, because a guard added to some writers is a
# guard the next writer will not have.


def _fireside(monkeypatch, state_dir: Path, tag: str):
    """Load the bot with STATE_DIR pointed wherever the caller needs it.

    Redirecting the module constant is how the whole fireside suite already
    works, and it is what an environment-shaped guard got wrong: those runs were
    aimed at a tmp_path and could not reach the clone, yet a guard that asked
    `data_overlay_present()` refused fifty of them.
    """
    bot = _load("fireside-bot.py", f"fireside_state_{tag}")
    monkeypatch.setattr(bot, "STATE_DIR", state_dir)
    return bot


DOOMED_STATE = ENGINE / "examples" / "datastore" / "operations" / "tribe" / "fireside-state"


@pytest.fixture
def doomed_state():
    """A state path inside the clone, swept before AND after.

    Sweeping both ends is not tidiness. A leftover from an earlier run makes
    every assertion below report the wrong thing: "refused, but only after
    creating" over a directory this test never touched. A mutation run with the
    guard removed left exactly that behind on 2026-08-27.
    """
    shutil.rmtree(ENGINE / "examples" / "datastore", ignore_errors=True)
    yield DOOMED_STATE
    shutil.rmtree(ENGINE / "examples" / "datastore", ignore_errors=True)


def test_fireside_state_refuses_a_directory_inside_the_engine_clone(monkeypatch,
                                                                    doomed_state):
    bot = _fireside(monkeypatch, doomed_state, "doomed")
    with pytest.raises(DataRootError):
        bot.require_writable_state_dir()
    assert not doomed_state.exists(), (
        f"the guard created {doomed_state} before refusing"
    )


@pytest.mark.parametrize("writer", ["save_state", "append_jsonl", "ensure_state_dir"])
def test_every_fireside_writer_goes_through_the_funnel(writer, monkeypatch,
                                                      doomed_state):
    """Named one by one rather than asserted over a list, so a writer that stops
    calling the funnel fails with its own name in the report.

    The disk assertion is the one that does the work, and `pytest.raises` alone
    is NOT enough: a mutation that put the bare `STATE_DIR.mkdir(parents=True)`
    back into `ensure_state_dir` SURVIVED a raises-only version of this test,
    because that function goes on to initialise its state files and the refusal
    then arrived from `save_state` one frame deeper. The exception was the right
    type, raised for the wrong reason, after the directory had already been
    created. What matters is that nothing reached the disk.
    """
    state = doomed_state
    bot = _fireside(monkeypatch, state, f"writer_{writer}")
    args = {
        "save_state": ("probe.json", {"a": 1}),
        "append_jsonl": ("probe.jsonl", {"a": 1}),
        "ensure_state_dir": (),
    }[writer]

    with pytest.raises(DataRootError):
        getattr(bot, writer)(*args)
    assert not state.exists(), (
        f"{writer} refused, but only after creating {state}; with no overlay "
        "that path is inside the engine clone"
    )


def test_the_error_log_reports_to_stderr_instead_of_raising(monkeypatch, capsys,
                                                           doomed_state):
    """`main()` is wrapped so an uncaught exception lands in `log_error`. A
    refusal thrown from there replaces the error being reported with a second
    error, and the first one is lost."""
    bot = _fireside(monkeypatch, doomed_state, "errorlog")
    bot.log_error("a probe message")
    err = capsys.readouterr().err
    assert "a probe message" in err, (
        "the error text did not reach stderr; refusing the location must not "
        f"also drop the error. stderr was: {err!r}"
    )


def test_a_state_dir_outside_the_clone_is_created_normally(
    tmp_path, monkeypatch
):
    """Without this the funnel could refuse unconditionally, pass every
    assertion above, and leave the bot with no memory of the Tribe."""
    bot = _fireside(monkeypatch, tmp_path / "fireside-state", "ok")
    got = bot.require_writable_state_dir()
    assert got.is_dir() and got == tmp_path / "fireside-state", (
        f"with a real overlay the state dir resolved to {got}"
    )
