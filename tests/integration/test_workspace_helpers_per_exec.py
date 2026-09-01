"""One exec-repo topology, pinned in one place.

Rewritten 2026-08-23. The audit found this file and
`tests/integration/test_aggregate_crm_per_exec.py` asserting two different
fleet layouts, both green:

- here: `assert path == workspace.parent / "31c-crm-marlow-carter"`
- there: "The legacy 31c-crm-{slug} model is retired", testing
  `.heading-os-data-{slug}`

Measured, the second is the shipped one. `scripts/aggregate-crm.py` clones
`heading-os-data-{slug}` and reads the fleet from
`<data-root>/admin/executives.json`; `provision_exec.py` and the data-root seam
use the same dotted sibling name. What the engine helper returned was the
retired name, and `scripts/utils/workspace.load_exec_registry` read
`config/exec-registry.json`, a path that exists nowhere - so it always returned
an empty fleet and `admin-health.py` and `transfer-contact.py` saw no execs at
all, silently.

Both helpers now speak the live topology, and the last test here pins the two
implementations against each other so they cannot drift apart again.

THREE OF THESE TESTS COULD NOT FAIL, and that was found on 2026-09-01 by
mutating the code they name.

- `test_active_slugs_are_sorted` read the LIVE roster and asserted
  `slugs == sorted(slugs)`. On a clone with no data overlay that is `[] == []`;
  on the operator's machine the three active slugs happen to be stored in
  alphabetical order already. Deleting `sorted()` from
  `get_all_active_exec_slugs` left 621 tests green across every file that names
  a fleet helper.
- `test_the_per_exec_path_rejects_an_unsafe_slug` parametrised four inputs and
  every one of them was refused by the `/` or `\\` clause, so the `..` clause
  had no case at all. Deleting it left the same 621 green.
- `test_the_live_registry_is_actually_readable` asserted
  `isinstance(registry.get("executives"), list)` over the live overlay.
  `_read_registry_or_empty` returns a list on every path it has, including the
  silent-empty one that test's own docstring called "the whole defect". It is
  gone; the four directions it gestured at are measured in
  `tests/test_five_loaders_that_crashed_on_the_file_they_promised_to_survive.py`
  (absent, corrupt, wrong-shape, readable), and the roster read is pinned below
  over a registry the test controls.

No test in this file reads the operator's live overlay any more, and that is
deliberate rather than incidental. It is the point of the last test here: this
directory now pins `HEADING_OS_DATA` at a tmp tree for every test, so a test
that reached for the real overlay would be asserting something no other machine
can reproduce. The live fleet is still exercised, once, outside this conftest,
by
`tests/test_per_exec_contacts_dir.py::test_the_live_fleet_is_visible_through_the_helper`,
which skips honestly when there is no overlay to read.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.paths import get_data_root  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_all_active_exec_slugs,
    get_per_exec_repo_path,
    get_workspace_root,
)

LIVE_PREFIX = ".heading-os-data-"
RETIRED_PREFIX = "31c-crm-"


def test_the_per_exec_path_is_the_dotted_data_overlay_sibling():
    path = get_per_exec_repo_path("marlow-carter")
    assert path == get_workspace_root().parent / f"{LIVE_PREFIX}marlow-carter"


def test_the_retired_naming_is_gone():
    """The specific regression: `31c-crm-{slug}` must not come back."""
    path = get_per_exec_repo_path("marlow-carter")
    assert RETIRED_PREFIX not in path.name, path


def test_the_per_exec_path_handles_an_arbitrary_slug():
    path = get_per_exec_repo_path("test-slug")
    assert path.name == f"{LIVE_PREFIX}test-slug"
    assert path.parent == get_workspace_root().parent


@pytest.mark.parametrize("bad", [
    "",                 # the emptiness clause
    "path/traversal",   # the "/" clause
    "back\\slash",      # the "\\" clause
    "../escape",        # "/" again: this one never needed the ".." clause
    # ON THE LINE for the ".." clause, which had no case until 2026-09-01 and
    # could be deleted with the whole fleet corpus still green. Neither of
    # these carries a separator, so nothing else in the guard sees them.
    "..",
    "rowan..ashby",
])
def test_the_per_exec_path_rejects_an_unsafe_slug(bad: str):
    with pytest.raises(ValueError):
        get_per_exec_repo_path(bad)


@pytest.mark.parametrize("good", ["marlow-carter", "Rowan.Ashby"])
def test_the_guard_still_accepts_an_ordinary_slug(good: str):
    """The other direction. A guard that refused everything would satisfy every
    case above and provision nobody. A single dot is legal; only a doubled one
    is refused."""
    assert get_per_exec_repo_path(good).name == f"{LIVE_PREFIX}{good}"


def test_the_registry_is_read_from_the_data_overlay(monkeypatch, tmp_path):
    """It read `config/exec-registry.json`, which exists nowhere.

    Three executives rather than one since 2026-09-01. One entry proves the
    loader found the file; it does not prove the loader reports the ROSTER,
    which is what "every caller silently saw a fleet of zero" was about.
    """
    from scripts.utils import workspace as ws

    registry_dir = tmp_path / "admin"
    registry_dir.mkdir()
    (registry_dir / "executives.json").write_text(json.dumps({
        "version": 1,
        "executives": [
            {"slug": "probe", "role": "exec", "status": "active"},
            {"slug": "rowan-ashby", "role": "cfo", "status": "active"},
            {"slug": "delacroix-vane", "role": "exec", "status": "offboarded"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ws, "get_data_root", lambda: tmp_path)
    assert [e["slug"] for e in ws.load_exec_registry()["executives"]] == [
        "probe", "rowan-ashby", "delacroix-vane"], (
        "the loader is the raw roster reader: it reports the file in file "
        "order and filters nothing. get_all_active_exec_slugs does the "
        "filtering, and it is pinned separately below.")


def test_an_absent_registry_is_an_empty_fleet_not_an_error(monkeypatch, tmp_path):
    """A data-less engine clone has no fleet, and that is not a failure."""
    from scripts.utils import workspace as ws

    monkeypatch.setattr(ws, "get_data_root", lambda: tmp_path)
    assert ws.load_exec_registry()["executives"] == []


def test_active_slugs_exclude_admin(monkeypatch):
    from scripts.utils import workspace as ws_module

    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: {
        "version": "test",
        "executives": [
            {"slug": "ceo-test", "role": "admin", "status": "active"},
            {"slug": "exec-test", "role": "exec", "status": "active"},
        ],
    })
    slugs = get_all_active_exec_slugs()
    assert "ceo-test" not in slugs
    assert "exec-test" in slugs


def test_active_slugs_exclude_inactive(monkeypatch):
    from scripts.utils import workspace as ws_module

    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: {
        "version": "test",
        "executives": [
            {"slug": "active-exec", "role": "exec", "status": "active"},
            {"slug": "offboarded-exec", "role": "exec", "status": "offboarded"},
            {"slug": "pending-exec", "role": "exec", "status": "pending"},
        ],
    })
    slugs = get_all_active_exec_slugs()
    assert slugs == ["active-exec"]


def test_active_slugs_are_sorted(monkeypatch):
    """Out of order on purpose, over a roster the test owns.

    This read the live roster and asserted `slugs == sorted(slugs)`, which is
    `[] == []` on a clone with no overlay and, on the operator's machine, an
    already-sorted three. MEASURED 2026-09-01: dropping `sorted()` from
    `get_all_active_exec_slugs` left 621 tests green. `aggregate-crm.py`,
    `admin-health.py` and `generate-crm-dashboard.py` all render the fleet in
    the order this returns, so the order is the contract, not a detail.
    """
    from scripts.utils import workspace as ws_module

    monkeypatch.setattr(ws_module, "load_exec_registry", lambda: {
        "version": "test",
        "executives": [
            {"slug": "rowan-ashby", "role": "exec", "status": "active"},
            {"slug": "delacroix-vane", "role": "cfo", "status": "active"},
            {"slug": "marlow-carter", "role": "exec", "status": "active"},
        ],
    })
    assert get_all_active_exec_slugs() == [
        "delacroix-vane", "marlow-carter", "rowan-ashby"]


def test_the_two_implementations_agree_on_the_layout():
    """The contract test the audit asked for.

    `scripts/aggregate-crm.py` keeps its own root-parameterised copy for
    testability. One name, two functions, is exactly how the topologies drifted
    apart; this pins them together.
    """
    import importlib.util

    root = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "_aggregate_crm_under_test", root / "scripts" / "aggregate-crm.py")
    agg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg)

    workspace = get_workspace_root()
    for slug in ("marlow-carter", "rowan-ashby", "a"):
        assert (agg.get_per_exec_repo_path_for_workspace(workspace, slug)
                == get_per_exec_repo_path(slug)), slug


def test_this_directory_resolves_a_scratch_data_root(tmp_path):
    """`tests/integration/conftest.py` pins HEADING_OS_DATA. Proven from inside.

    The conftest docstring said "File I/O redirected to tmp_path (pytest
    built-in) to avoid touching real state" and there was no such redirection:
    `tmp_state_dir` and `tmp_session_dir` are opt-in fixtures, and nothing at
    all pinned the data root. MEASURED 2026-09-01 with the new autouse fixture
    removed, this test reports
    `/home/administrator/ai/claude-workspaces/.heading-os-data` - the
    operator's live overlay, resolved by every test in this directory.

    The child process half is the reason the pin is an ENVIRONMENT variable and
    not a patched attribute. Four of the seven test files here already ran
    `subprocess.run`, and a patched `get_data_root` stops at the process
    boundary while the in-process overlay write guard cannot see a child at all. The session-finish report in
    `tests/conftest.py` counted 22 such children from this directory "with the
    live data root reachable".

    IF THIS FAILS, CHECK HOW YOU INVOKED PYTEST BEFORE CHANGING ANYTHING. On
    pytest 9.1.1 a directory conftest's autouse fixtures are dropped when
    collection LEAVES that package and comes back, which only a hand-written
    interleaved command line produces. Measured 2026-09-01 in this repository:

        pytest tests/integration/test_aggregate_crm_per_exec.py \\
               tests/test_data_root.py \\
               tests/integration/test_workspace_helpers_per_exec.py

    reports `fixtures used: _isolate_runtime_logs, _no_egress,
    _pin_model_resolution, event_loop_policy, request, tmp_path,
    tmp_path_factory` for this test, with `_pin_the_data_root` simply absent,
    and the pin is not in force. Put the same three files in directory order and
    all of them pass. `tests/bridge` has the identical hole and fails louder:
    the same interleaving turns `tests/bridge/test_config.py` into fixture-not-
    found ERRORS. Neither `pytest tests` nor pytest-randomly produces it, since
    both keep a package's files together, and the full suite is green under
    both.
    """
    assert get_data_root().resolve() == tmp_path.resolve(), (
        f"this test resolved {get_data_root()} instead of its own tmp_path; "
        f"the autouse pin in tests/integration/conftest.py is not in force")

    root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from scripts.utils.paths import get_data_root; print(get_data_root())",
         str(root)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).resolve() == tmp_path.resolve(), (
        f"a child spawned from this directory resolved {proc.stdout.strip()}; "
        f"the pin has to reach the environment, not just this process")
