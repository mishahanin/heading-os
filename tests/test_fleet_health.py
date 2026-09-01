"""Fleet-health per-daemon reconciliation tests (R14, scrutiny M3).

Loads the hyphenated ``scripts/daemon-fleet-health.py`` module via importlib and
exercises the pure classification + verdict + exit-code functions. Asserts:

  - _classify_beat(): fresh -> ok, stale -> stale, no timestamp -> error;
  - a stale per-daemon beat degrades BOTH the verdict (green -> drift) AND the
    exit code (0 -> 1) even when the bridge record is green (M3);
  - with NO per-daemon beats supplied, the verdict text and exit code are
    byte-identical to the legacy behaviour (back-compat).

No live ``.daemon-state`` is read - records and statuses are constructed inline.

Run: python3 -m pytest tests/test_fleet_health.py
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fixed reference instant. The fleet-health classifiers compute age against
# ``datetime.now(timezone.utc)``; the ``fh`` fixture freezes the module's clock
# to exactly this instant (see below) so a record stamped ``NOW - 5s`` is always
# 5s old at assert time, regardless of how long the full suite takes to reach
# this module. Without the freeze the prior module-level ``NOW`` drifted past
# the 120s stale threshold under load, flipping "ok" -> "stale" intermittently.
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """A datetime whose ``now()`` always returns the fixed ``NOW`` instant."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(scope="module")
def fh():
    path = Path(__file__).resolve().parent.parent / "scripts" / "daemon-fleet-health.py"
    spec = importlib.util.spec_from_file_location("daemon_fleet_health_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Freeze the module's wall clock to NOW. The classifiers parse the same
    # ``isoformat()`` timestamps the helpers below produce, so age is exact and
    # never load-coupled. ``datetime.fromisoformat`` is unaffected (it lives on
    # the real datetime, which _FrozenDatetime subclasses).
    mod.datetime = _FrozenDatetime
    return mod


def _ok_bridge_record():
    return {
        "status": "ok",
        "last_heartbeat": (NOW - timedelta(seconds=5)).isoformat(),
        "version": "3",
        "workspace": "ws/ceo-main",  # fake label; never used as a real path
    }


def _fresh_beat(name="bridge"):
    return {"daemon": name, "last_heartbeat": (NOW - timedelta(seconds=5)).isoformat()}


def _stale_beat(name="sentinel"):
    return {"daemon": name, "last_heartbeat": (NOW - timedelta(seconds=6000)).isoformat()}


# ============================================================
# _classify_beat
# ============================================================

def test_classify_beat_fresh_ok(fh):
    assert fh._classify_beat(_fresh_beat(), 120) == "ok"


def test_classify_beat_stale(fh):
    assert fh._classify_beat(_stale_beat(), 120) == "stale"


def test_classify_beat_no_timestamp_is_error(fh):
    assert fh._classify_beat({"daemon": "x"}, 120) == "error"
    assert fh._classify_beat({"status": "error", "daemon": "x"}, 120) == "error"


def test_a_self_reported_error_wins_over_a_fresh_timestamp(fh):
    """The `status == "error"` arm, which nothing could observe.

    Both cases in the test above lack `last_heartbeat`, so both reach `error`
    through the no-timestamp branch and the status check never decides either.
    MEASURED 2026-09-01 by making that comparison unsatisfiable: this module and
    seven sibling fleet-health files stayed green, while a daemon writing a
    fresh beat that SAYS it is broken classified as `ok` and the fleet verdict
    read healthy over it.

    A daemon in trouble is exactly the one still beating: it is running, so the
    heartbeat is current, and the record is the only place it can say so.
    """
    beat = {"daemon": "sentinel", "status": "error",
            "last_heartbeat": (NOW - timedelta(seconds=5)).isoformat()}

    assert fh._classify_beat(beat, 120) == "error"
    # And the negative direction, so this is not satisfied by a classifier that
    # answers "error" for everything: the same fresh beat without the flag is ok.
    assert fh._classify_beat({k: v for k, v in beat.items() if k != "status"},
                             120) == "ok"


# ============================================================
# M3: a stale beat degrades verdict + exit code under a green bridge
# ============================================================

def test_stale_beat_degrades_verdict(fh):
    records = [_ok_bridge_record()]
    beat_statuses = ["ok", "stale"]  # bridge ok, sentinel stale
    text, color = fh._verdict(records, 120, None, None, beat_statuses)
    assert "drift" in text.lower()
    assert color == fh.YELLOW


def test_stale_beat_degrades_exit_code(fh):
    records = [_ok_bridge_record()]
    # Without beats: green bridge -> exit 0.
    assert fh._classify_fleet_exit_code(records, 120, None, None) == 0
    # With a stale beat: degrades to drift -> exit 1.
    assert fh._classify_fleet_exit_code(records, 120, None, None, ["ok", "stale"]) == 1


def test_error_beat_breaks_fleet(fh):
    records = [_ok_bridge_record()]
    assert fh._classify_fleet_exit_code(records, 120, None, None, ["error"]) == 2
    text, color = fh._verdict(records, 120, None, None, ["error"])
    assert "broken" in text.lower()
    assert color == fh.RED


# ============================================================
# Retired-clone discovery exclusion (ceo-main false-stale fix, 2026-06-20)
# ============================================================

@pytest.mark.parametrize("name", [
    "ceo-main", "ceo-main-kimi", "odin-heading-os",
    "CEO-Main",                      # case-insensitive
    ".heading-os-data", "ceo-main-data",  # engine data siblings, by `-data` suffix
])
def test_non_fleet_siblings_excluded(fh, name):
    assert fh._is_non_fleet_sibling(name) is True


@pytest.mark.parametrize("name", [
    ".heading-os",                   # the live engine itself
    "31c-exec-alpha", "exec-bravo",
    # A per-exec DATA overlay. The `-data` SUFFIX rule above does not reach it,
    # because the slug follows the suffix: `.heading-os-data-alpha` ends in
    # `-alpha`. It therefore falls through to the generic local-style check and
    # is skipped there for want of a heartbeat, which is the correct outcome by
    # a different route than the exclusion list. Pinned so a future widening of
    # the suffix rule to `-data-*` has to argue with a test.
    ".heading-os-data-alpha",
])
def test_fleet_siblings_kept(fh, name):
    assert fh._is_non_fleet_sibling(name) is False


# ============================================================
# Back-compat: no beats -> byte-identical legacy output
# ============================================================

def test_no_beats_legacy_verdict_text(fh):
    records = [_ok_bridge_record()]
    text, color = fh._verdict(records, 120, None, None)
    assert text == "Fleet healthy: 1 workspace(s) ok."
    assert color == fh.GREEN


def test_no_beats_legacy_exit_code(fh):
    records = [_ok_bridge_record()]
    assert fh._classify_fleet_exit_code(records, 120, None, None) == 0


# ============================================================
# The verdict must not sum two kinds of thing into one number
# ============================================================

def test_workspaces_and_daemons_are_counted_separately(fh):
    """The misreading this pins: "3 workspace/daemon(s) ok" was read as three
    workspaces when it meant one workspace and two daemons."""
    text, color = fh._verdict([_ok_bridge_record()], 120, None, None,
                              beat_statuses=["ok", "ok"])

    assert text == "Fleet healthy: 1 workspace, 2 daemons ok (this machine only)."
    assert color == fh.GREEN
    assert "3" not in text


def test_the_verdict_says_it_only_looked_at_this_machine(fh):
    """This script scans sibling directories of the workspace root, so a separate
    host running its own daemons is invisible to it. "Fleet" alone promises more
    than it checked, which is the more dangerous of the two misreadings."""
    text, _ = fh._verdict([_ok_bridge_record()], 120, None, None,
                          beat_statuses=["ok"])

    assert "this machine only" in text
    assert text == "Fleet healthy: 1 workspace, 1 daemon ok (this machine only)."


def test_a_drifting_daemon_names_the_daemon_not_a_workspace(fh):
    text, color = fh._verdict([_ok_bridge_record()], 120, None, None,
                              beat_statuses=["stale"])

    assert text == ("Fleet drift: 1 daemon stale, version-mismatch, or "
                    "config-drift (this machine only).")
    assert color == fh.YELLOW
    # the healthy workspace must NOT be counted into the drift tally
    assert "1 workspace," not in text


def test_a_broken_daemon_beside_a_healthy_workspace(fh):
    text, color = fh._verdict([_ok_bridge_record()], 120, None, None,
                              beat_statuses=["error"])

    assert text == "Fleet broken: 1 daemon error or missing (this machine only)."
    assert color == fh.RED


# ============================================================
# `_candidate_workspaces` discovery (2026-08-30)
#
# Until this section existed, `_candidate_workspaces` had NO test at all: the
# three tests in tests/bridge/test_fleet_health.py that mention it monkeypatch
# it away with a lambda. So the loop kept a branch keyed on `31c-crm-<slug>`,
# a repo name retired on 2026-08-23, for a week after the last such directory
# left the disk, and nothing went red.
#
# Every fixture below builds its OWN sibling tree under tmp_path and rebinds
# both discovery roots (`get_workspace_root` and `Path.home`). Reading the
# operator's real siblings from a test would make the result depend on which
# executives happen to be provisioned on the machine running the suite.
# ============================================================

def _fake_fleet(fh, monkeypatch, tmp_path):
    """Sandbox both discovery roots under tmp_path. Returns (parent, ceo, home).

    `_candidate_workspaces` reads exactly two roots: `get_workspace_root()`,
    whose PARENT it scans for siblings, and `Path.home() / "exec-workspaces"`.
    Both are rebound here, so no test in this file can see a real sibling.
    """
    parent = tmp_path / "fleet"
    ceo = parent / ".heading-os"
    ceo.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(fh, "get_workspace_root", lambda: ceo)
    monkeypatch.setattr(fh.Path, "home", staticmethod(lambda: home))
    return parent, ceo, home


def _with_local_heartbeat(path: Path) -> Path:
    """Give `path` the heartbeat the live daemon writes: .daemon-state/heartbeat.json."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".daemon-state").mkdir(exist_ok=True)
    (path / ".daemon-state" / "heartbeat.json").write_text("{}", encoding="utf-8")
    return path


def _with_root_bridge_heartbeat(path: Path) -> Path:
    """Give `path` a root `bridge-heartbeat.json`.

    Nothing in this repository writes this file. `scripts/bridge_daemon/heartbeat.py`
    is the only heartbeat writer and it writes `.daemon-state/heartbeat.json`,
    which `.gitignore` excludes, so no push can carry it into a sibling repo
    under any name. The fixture exists to prove discovery does NOT invent a
    fleet member from a file with no writer.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / "bridge-heartbeat.json").write_text("{}", encoding="utf-8")
    return path


def _found(fh):
    return [(p.name, kind) for p, kind in fh._candidate_workspaces()]


def test_a_retired_crm_sibling_is_not_discovered(fh, monkeypatch, tmp_path):
    """`31c-crm-<slug>` is the retired per-exec CRM repo name. A directory with
    that name is an artefact, not a fleet member, and must not enter the grid."""
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_root_bridge_heartbeat(parent / "31c-crm-alpha")

    assert _found(fh) == [(".heading-os", "local")]


def test_a_live_exec_overlay_yields_no_crm_mirror(fh, monkeypatch, tmp_path):
    """The live layout is `.heading-os-data-<slug>`. Moving the retired branch
    onto that name instead of deleting it would have been wrong for the same
    reason the branch was wrong: no writer produces the file it looks for.

    If a writer is ever added, this test is the one to argue with.
    """
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_root_bridge_heartbeat(parent / ".heading-os-data-alpha")

    found = _found(fh)
    assert found == [(".heading-os", "local")]
    assert all(kind != "crm-mirror" for _, kind in found)


def test_discovery_produces_only_the_local_kind(fh, monkeypatch, tmp_path):
    """Whole-surface pin: across every shape a sibling can take, the only kind
    the loop emits is `local`. The docstring says so; this makes it measurable."""
    parent, _, home = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_local_heartbeat(parent / "31c-exec-alpha")
    _with_root_bridge_heartbeat(parent / "31c-crm-bravo")
    _with_root_bridge_heartbeat(parent / ".heading-os-data-charlie")
    _with_local_heartbeat(home / "exec-workspaces" / "delta")

    assert {kind for _, kind in fh._candidate_workspaces()} == {"local"}


def test_the_ceo_workspace_is_first_and_appears_once(fh, monkeypatch, tmp_path):
    """The CEO root is seeded before the sibling scan, so the scan must skip it
    rather than add a second entry for the same path.

    The CEO root is given a heartbeat here deliberately. Without one the sibling
    scan would drop it at the heartbeat check anyway and the `child == ceo`
    dedupe would be doing nothing this test could see.
    """
    _, ceo, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_local_heartbeat(ceo)

    found = _found(fh)
    assert found[0] == (".heading-os", "local")
    assert found.count((".heading-os", "local")) == 1


def test_a_sibling_with_a_heartbeat_is_a_fleet_member(fh, monkeypatch, tmp_path):
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_local_heartbeat(parent / "31c-exec-alpha")

    assert _found(fh) == [(".heading-os", "local"), ("31c-exec-alpha", "local")]


def test_a_sibling_without_a_heartbeat_is_not_a_fleet_member(fh, monkeypatch, tmp_path):
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    (parent / "31c-exec-alpha").mkdir()

    assert _found(fh) == [(".heading-os", "local")]


def test_a_plain_file_beside_the_workspace_is_skipped(fh, monkeypatch, tmp_path):
    """Behaviour coverage, not a guard proof, and the difference is stated so
    nobody counts it as one. Deleting the `child.is_dir()` clause leaves this
    test green: a regular file cannot hold `.daemon-state/heartbeat.json`, so
    the heartbeat check below rejects it a second time. The clause is a cheap
    pre-filter with no independently observable effect, and no test in this file
    can make it refuse.
    """
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    (parent / "notes.md").write_text("x", encoding="utf-8")

    assert _found(fh) == [(".heading-os", "local")]


@pytest.mark.parametrize("name", ["ceo-main", ".heading-os-data"])
def test_a_non_fleet_sibling_is_excluded_even_holding_a_heartbeat(
    fh, monkeypatch, tmp_path, name
):
    """The exclusion is belt-and-braces: a stale heartbeat in a retired clone or
    in the engine's own data sibling must not resurrect it as a fleet member."""
    parent, _, _ = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_local_heartbeat(parent / name)

    assert _found(fh) == [(".heading-os", "local")]


def test_the_exec_workspaces_home_directory_is_discovered(fh, monkeypatch, tmp_path):
    _, _, home = _fake_fleet(fh, monkeypatch, tmp_path)
    _with_local_heartbeat(home / "exec-workspaces" / "alpha")
    (home / "exec-workspaces" / "bravo").mkdir()  # no heartbeat -> not a member

    assert _found(fh) == [(".heading-os", "local"), ("alpha", "local")]


# ============================================================
# The docstring is the contract, so it is asserted like one
# ============================================================

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "daemon-fleet-health.py"

RETIRED_REPO_NAME = "31c-crm-"


def _discovery_contract_bullets(doc: str) -> str:
    """The bulleted discovery contract from the module docstring.

    Scoped to the bullet list rather than the whole docstring on purpose. A
    blanket ban on the retired name would also ban the paragraph that records
    WHY the branch went, and that paragraph is what stops it being re-added.
    What must stay false is narrower and sharper: the retired surface may not
    appear as a bullet in the list a reader audits the loop against.
    """
    prefix = doc.split("Usage:")[0]
    paragraphs = [p for p in prefix.split("\n\n") if p.lstrip().startswith("- ")]
    assert len(paragraphs) == 1, (
        f"expected exactly one bulleted discovery contract, found {len(paragraphs)}"
    )
    return paragraphs[0]


def test_the_module_docstring_advertises_no_retired_discovery_surface(fh):
    """The contract list carried a `31c-crm-<slug>` -> `crm-mirror` bullet for a
    surface `_candidate_workspaces` no longer has. A docstring describing a
    discovery surface the loop does not implement is what hid the dead branch:
    a reader auditing the contract read the claim instead of the code."""
    bullets = _discovery_contract_bullets(fh.__doc__)

    assert RETIRED_REPO_NAME not in bullets
    assert "crm-mirror" not in bullets
    # Every bullet in the list names the one kind discovery emits.
    assert bullets.count("(kind `local`)") == bullets.count("(kind ")


def test_the_module_docstring_records_the_deletion(fh):
    """The removal note is load-bearing. Without it the next reader finds a
    discovery loop with no mirror branch, no explanation, and a live-looking
    `crm-mirror` arm still in `_read_heartbeat`, and re-adds the branch."""
    doc = fh.__doc__

    assert RETIRED_REPO_NAME in doc, "the docstring no longer says which name was retired"
    assert "2026-08-30" in doc
    assert "bridge-heartbeat.json" in doc


def test_the_discovery_docstring_lists_only_the_kind_it_returns(fh):
    doc = fh._candidate_workspaces.__doc__
    assert RETIRED_REPO_NAME not in doc
    assert "crm-mirror" not in doc


def test_no_retired_repo_name_survives_in_executable_code(fh):
    """AST, not grep: a comment may record the history, but no string the code
    actually evaluates may name the retired repo. A `continue` on a name that
    can never occur is not a safety net, it is a false claim about the disk."""
    import ast

    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and RETIRED_REPO_NAME in node.value
    ]
    assert offenders == [], (
        f"daemon-fleet-health.py evaluates a retired repo name: {offenders}. "
        "The per-exec CRM repos were retired on 2026-08-23; the live layout is "
        "the sibling data overlay."
    )
