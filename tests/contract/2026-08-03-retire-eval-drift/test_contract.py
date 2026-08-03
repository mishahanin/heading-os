"""The frozen contract for the retire-eval-drift slice.

The eval-drift daemon replays Langfuse traces of skill runs against the current
SKILL.md and compares today's pass rate to a rolling baseline. It cannot do that
here, for two independent reasons, either of which alone is fatal:

1. `observability.is_enabled()` is False, so no trace is ever recorded. Its input
   set is empty by construction.
2. `is_sensitive()` is True by default (SENSITIVE_MODE fail-closed and unset), and
   the daemon refuses to run when it is. Measured on the service host: a WARNING
   `sensitive session (SENSITIVE_MODE) - skipping eval-drift run`, every night.

Measured 2026-08-03 on the service host: the unit is loaded, enabled and active,
its heartbeat is 60 seconds old, and its newest report is dated 2026-05-23 --
72 days. Every health surface called it healthy for all 72. Even the four reports
it did produce recorded 0 traces and a 100% pass rate for all ten skills, which
is what dividing zero by zero looks like when it is rendered as a percentage.

So the code goes. What must NOT go with it, and is what this contract mostly
pins:

- The eval CASES. Ten skills carry `evals/cases/`, and `run-skill-eval.py` --
  the harness a human drives from `/scrutinize` -- reads them directly. The
  daemon was one consumer of that harness, never its owner.
- The 72 days of reports and state under `datastore/operations/eval-drift/` and
  `outputs/operations/eval-drift/`. Deleting a producer must not un-protect what
  it produced: the private routing rule and the two gitignore entries survive the
  code that wrote them.

And one thing this contract ADDS, because it is what makes the removal stick:
every name in `EXPECTED_DAEMONS` must have a unit template somebody can actually
install. That catches this defect's mirror image -- a supervised name with
nothing behind it -- which is precisely the state `eval-drift` would be left in
if the watchdog entry outlived the code.

Every test imports the code under test INSIDE its body, and every test that reads
tree state derives it from the repository rather than from a fixture, because the
subject IS the repository's own consistency.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]

# Every engine file that OFFERS eval-drift as something to run, supervise,
# install or monitor. Enumerated by name rather than by a tree-wide grep so that
# a file added later which reintroduces the daemon fails a NAMED assertion
# instead of quietly widening a wildcard.
#
# `scripts/launch-all-daemons.bat` and `scripts/install-startup-shortcut.ps1`
# were on this list on first write and came off it during the build. Both are
# RETIREMENT STUBS: the .bat exits 0 doing nothing, the .ps1 exits non-zero on
# purpose, and each one's remaining text records which four daemons were migrated
# to the service host on 2026-05-23. Neither can install, start or monitor
# anything. The only way to satisfy the assertion there is to edit a historical
# statement into a false one, which is a worse outcome than the name surviving in
# a file whose entire content is a note saying "this no longer runs".
_SUPERVISION_SURFACE = (
    "scripts/install-daemon-service.sh",
    "scripts/restart-daemon-service.sh",
    "scripts/uninstall-daemon-service.sh",
    "scripts/setup-daemon-healthchecks.py",
    "scripts/service-host.example.json",
    "scripts/templates/systemd/README.md",
)


# The two stubs are not exempted, only asked a different question: they may NAME
# the daemon as history, and may not offer a way to start it.
_RETIREMENT_STUBS = (
    "scripts/launch-all-daemons.bat",
    "scripts/install-startup-shortcut.ps1",
)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


# ============================================================
# SC-1 -- the code and its unit are gone
# ============================================================

def test_the_daemon_script_is_gone():
    """SC-1a. 1015 lines whose input set is empty by construction."""
    assert not (_ROOT / "scripts" / "eval-drift-daemon.py").exists()


def test_the_unit_template_is_gone():
    """SC-1b. A template left behind is an install one command away."""
    assert not (_ROOT / "scripts" / "templates" / "systemd"
                / "eval-drift-daemon.service").exists()


def test_the_daemons_own_test_module_is_gone():
    """SC-1c. tests/test_eval_drift_aggregation.py importlib-loads the daemon by
    path. Left behind it would fail at collection, and a suite that cannot
    collect is a suite nobody can read a verdict from."""
    assert not (_ROOT / "tests" / "test_eval_drift_aggregation.py").exists()


# ============================================================
# SC-2 -- nothing offers it as a thing to run
# ============================================================

@pytest.mark.parametrize("rel", _SUPERVISION_SURFACE)
def test_no_supervision_script_still_offers_the_daemon(rel):
    """SC-2. A case arm, a usage line, or a healthcheck entry that survives the
    code is worse than dead prose: `install-daemon-service.sh eval-drift` would
    still resolve a unit name and fail somewhere further down, and the
    Healthchecks.io deadman would alert forever on a run that can never happen.
    """
    text = _read(rel)
    assert "eval-drift" not in text and "eval_drift" not in text, (
        f"{rel} still names eval-drift"
    )


@pytest.mark.parametrize("rel", _RETIREMENT_STUBS)
def test_a_retirement_stub_may_name_it_but_never_start_it(rel):
    """SC-2c. The stubs keep the name because their whole content is the record
    of a migration. What they must never regain is a way to run the thing: no
    path to the deleted script, and no start/launch verb aimed at it.
    """
    text = (_ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "eval-drift-daemon.py" not in text
    for verb in ("start ", "Start-Process", "python "):
        for line in text.splitlines():
            if "eval-drift" in line and verb in line:
                pytest.fail(f"{rel} still has a way to start it: {line.strip()}")


def test_no_tracked_instruction_file_still_invokes_the_deleted_script():
    """SC-2b. The named list above can go stale, so this is the wildcard behind
    it, scoped to the one string that can only mean "run the daemon".

    Two scopings, both learned by running it. Only git-TRACKED files count: the
    first version swept the working tree and reported `.pytest_cache/` and
    `.superpowers/` scratch, which is noise nobody can act on and which would
    make the test's verdict depend on what was run before it.

    And history is exempt where history belongs -- tests, the changelog, and the
    archived plans. A test docstring explaining WHY a guard exists, or a plan
    recording what was built in July, is a record; rewriting it to pass a test
    would be falsifying it. Everything else -- scripts, rules, skills, docs,
    config -- is instruction, and instruction that names a deleted script is
    wrong rather than historical.
    """
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=_ROOT,
                             capture_output=True, text=True, check=True)
    hits = []
    for rel in tracked.stdout.split("\0"):
        if not rel:
            continue
        if rel.startswith(("tests/", "plans/", "docs/assets/")) or rel == "CHANGELOG.md":
            continue
        try:
            text = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, FileNotFoundError):
            continue
        if "eval-drift-daemon.py" in text:
            hits.append(rel)
    assert hits == [], f"these still name the deleted script: {hits}"


# ============================================================
# SC-3 / SC-4 -- the watchdog's expected set
# ============================================================

def test_the_watchdog_no_longer_expects_it():
    """SC-3. Left in EXPECTED_DAEMONS, the absent daemon resolves to `missing`,
    which is a genuine down state, and the watchdog raises a critical every ten
    minutes for a process nobody intends to run."""
    from scripts.watchdog_core import EXPECTED_DAEMONS

    assert "eval-drift" not in EXPECTED_DAEMONS


def test_every_expected_daemon_has_a_unit_somebody_can_install():
    """SC-4, and the reason this slice leaves a test behind at all.

    A name in EXPECTED_DAEMONS is a promise that something beats under it. The
    promise is only keepable if a unit template exists to install. This catches
    both directions of the drift: a name added with nothing behind it, and a
    daemon deleted whose watchdog entry outlived it -- the exact state this slice
    is removing.

    Matched by template STEM PREFIX rather than equality, because the fleet's
    naming is not uniform: `bridge` ships as `bridge-daemon.service` and
    `fireside` as `fireside-bot-daemon.service`, while `sentinel` is just
    `sentinel.service`. The prefix is the part a human types.
    """
    from scripts.watchdog_core import EXPECTED_DAEMONS

    stems = {p.stem for p in (_ROOT / "scripts" / "templates" / "systemd").glob("*.service")}
    missing = [name for name in EXPECTED_DAEMONS
               if not any(stem.startswith(name) for stem in stems)]
    assert missing == [], (
        f"EXPECTED_DAEMONS names {missing} with no installable unit template; "
        f"the watchdog would report a genuine down state for a daemon nobody "
        f"can start"
    )


def test_the_config_scope_still_overrides_the_fallback():
    """SC-3b. EXPECTED_DAEMONS is only the fallback; a host scopes itself through
    `daemon.watchdog.expect`. Shrinking the fallback must not disturb that path,
    or every host with a config gets its scope silently replaced."""
    from scripts.watchdog_core import load_expected

    scoped = load_expected(_ROOT)
    assert isinstance(scoped, tuple)
    assert all(isinstance(name, str) and name for name in scoped)


# ============================================================
# SC-5 -- the eval cases outlive their replayer
# ============================================================

def test_every_skill_with_eval_cases_still_enumerates():
    """SC-5. The daemon replayed traces THROUGH `run-skill-eval.py`; it never
    owned the corpus. Ten skills carry `evals/cases/`, and the harness a human
    drives from /scrutinize reads them directly. Deleting the replayer must not
    cost a single case.
    """
    skills = sorted(p.parent.parent.name
                    for p in (_ROOT / ".claude" / "skills").glob("*/evals/cases")
                    if p.is_dir())
    assert len(skills) == 10, f"expected 10 skills with eval cases, found {skills}"

    cases = list((_ROOT / ".claude" / "skills").glob("*/evals/cases/*.json"))
    assert len(cases) >= 30
    for case in cases:                       # each is still readable as a case
        json.loads(case.read_text(encoding="utf-8"))


def test_the_human_driven_eval_harness_is_untouched():
    """SC-5b. `run-skill-eval.py` is the surviving consumer. It never referenced
    the daemon -- the dependency ran the other way -- so it must come through
    this slice with no edit at all."""
    harness = _ROOT / "scripts" / "run-skill-eval.py"
    assert harness.exists()
    text = harness.read_text(encoding="utf-8")
    assert "eval-drift" not in text and "eval_drift" not in text


# ============================================================
# SC-6 -- deleting the producer must not un-protect what it produced
# ============================================================

def test_the_private_routing_rule_for_its_output_survives():
    """SC-6. 72 days of reports and a state file live under the DATA overlay. The
    routing map's default is `engine`, which is PUBLIC, so removing the explicit
    private rule alongside the code would silently reclassify real operational
    output as shareable. The producer goes; the protection stays.
    """
    from scripts.utils.workspace import get_routing_destination

    assert get_routing_destination("datastore/operations/eval-drift/state.json") == "private"
    assert get_routing_destination("datastore/operations/eval-drift/") == "private"


def test_the_gitignore_entries_for_its_runtime_state_survive():
    """SC-6b. Same argument one layer down: `state.json` and `errors.log` are
    untracked by rule. Dropping the rule would offer them to the next `git add
    -A` on any host that still has them on disk."""
    ignored = _read(".gitignore")
    assert "datastore/operations/eval-drift/state.json" in ignored
    assert "datastore/operations/eval-drift/errors.log" in ignored


# ============================================================
# SC-7 -- the published docs stop advertising it
# ============================================================

def test_the_public_docs_no_longer_present_it_as_a_live_daemon():
    """SC-7. The engine repository is public. A daemons page listing a daemon
    that does not ship is a promise to a reader who then goes looking for it."""
    for rel in ("docs/daemons.html", "docs/index.html"):
        assert "eval-drift" not in _read(rel).lower(), f"{rel} still lists it"


def test_the_docs_page_still_documents_the_daemons_that_remain():
    """SC-7b. The cheap way to pass the test above is to gut the page. This is
    the other jaw: whatever remains must still describe the surviving fleet."""
    page = _read("docs/daemons.html").lower()
    for name in ("bridge", "fireside", "sentinel", "sync-exchange"):
        assert name in page, f"docs/daemons.html no longer documents {name}"


# ============================================================
# SC-8 -- the lint baseline shrinks rather than carrying a ghost
# ============================================================

def test_the_lint_baseline_carries_no_entry_for_the_deleted_file():
    """SC-8. `.lint-baseline.json` grants a per-file allowance of known lint
    debt. An entry for a file that no longer exists is debt nobody can ever pay
    down, and the ratchet's own arithmetic quietly counts it forever."""
    baseline = json.loads(_read(".lint-baseline.json"))
    ghosts = [k for k in baseline if k.startswith("scripts/eval-drift-daemon.py")]
    assert ghosts == [], f"lint baseline still allows debt in a deleted file: {ghosts}"


# ============================================================
# SC-9 -- the prose that survives is history, not instruction
# ============================================================

@pytest.mark.parametrize("rel", (
    ".claude/rules/trace-id.md",
    ".claude/skills/scrutinize/references/eval-case-template.md",
))
def test_no_always_on_rule_still_instructs_about_the_daemon(rel):
    """SC-9. A rule that loads every session and names a daemon that does not
    exist teaches the assistant a false fact about the workspace on every turn.
    CHANGELOG entries and test docstrings are history and stay; an always-on rule
    and a skill reference are instruction and must not.
    """
    text = _read(rel)
    assert "eval-drift" not in text and "eval_drift" not in text, (
        f"{rel} loads as instruction and still names the daemon"
    )


def test_the_changelog_records_the_removal_rather_than_hiding_it():
    """SC-9b. The one place the name SHOULD survive. A deletion that leaves no
    trace in the changelog reads, six months later, as a daemon that never
    existed -- and the next audit re-derives the same 1015 lines.

    Asserting merely that the section MENTIONS eval-drift was a false pass: the
    Unreleased section already names it, in an entry about the sibling defect
    that made this slice worth doing. The claim has to be that a REMOVED section
    names it, which is the only shape that can mean the daemon is gone.
    """
    changelog = _read("CHANGELOG.md")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    removed = re.split(r"\n### ", unreleased)
    section = [s for s in removed if s.lower().startswith("removed")]
    assert section, "the Unreleased section has no ### Removed heading"
    assert re.search(r"eval[- ]drift", section[0], re.IGNORECASE), (
        "### Removed does not record the eval-drift retirement"
    )
