"""Regression: standalone-CLI scripts in scripts/ use kebab-case filenames (F-L1).

A full grep (2026-06-15) of scripts/*.py snake_case files found that 12 of 13 are
DUAL-ROLE — imported as Python modules by other scripts or tests. Hyphens are
illegal in Python module names, so an imported module MUST stay snake_case.

Only build_data_repo.py is pure-CLI (no Python import callers). It is kept
snake_case as a documented exception: it is one half of the build pair with the
dual-role build_engine_repo.py, and it is a cutover-critical tool referenced by
its current name in the active data-repo cutover plans (plan-4/5/7) and
auto-memory, which the CEO runs by hand. CEO decision 2026-06-16: do not rename.

This test guards against a NEW pure-CLI snake_case script slipping in
unjustified, and against any of the documented exclusions being silently
renamed or deleted.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"

# snake_case scripts imported as Python modules elsewhere (must stay snake_case).
DUAL_ROLE_EXCLUSIONS = {
    "build_engine_repo.py",          # tests/test_build_engine.py
    "canopus_check.py",              # tests/test_canopus_check.py imports its clauses
    "cold_sweep_core.py",            # bridge-daemon.py, cold-sweep.py, test_cold_sweep_routing.py
    "crm_migrate_to_entity_model.py",  # tests/test_crm_migration.py
    "crm_next.py",                   # tests/test_crm_next.py
    "fireside_topics.py",            # fireside-bot.py, test_fireside_topics.py, test_fireside_topic_handlers.py
    "heading_cli.py",                # tests/test_heading_cli.py (F-10.1 dispatcher: dual-role CLI + import)
    "fireside_webhook.py",           # fireside-bot-daemon.py
    "marp_render.py",                # test_marp_integration.py, test_marp_render.py
    "odin_brain_lint.py",            # odin-brain-health.py, test_odin_temporal_validity.py
    "odin_pagerank.py",              # tests/test_odin_pagerank.py
    "resolve_customization.py",      # tests/test_resolve_customization.py
    "resolve_entity.py",             # tests/test_resolve_entity.py
    "rule_split_check.py",           # tests/test_rule_split_check.py (L1 gate: imported + CLI)
    "skill_graph.py",                # next-signal.py, test_skill_graph.py
    "watchdog_core.py",              # daemon-watchdog.py, bridge-daemon.py, test_watchdog.py
}

# Pure-CLI scripts deliberately kept snake_case (documented above).
DOCUMENTED_CLI_EXCEPTIONS = {
    "build_data_repo.py",            # half of build pair; cutover tool referenced by current name
}

ALLOWED_SNAKE = DUAL_ROLE_EXCLUSIONS | DOCUMENTED_CLI_EXCEPTIONS


def _root_cli_scripts():
    """scripts/*.py at the root — not utils/ (library), not archive/, and not
    dunder package files (__init__.py is a package marker, never a CLI script)."""
    return sorted(
        p
        for p in SCRIPTS_DIR.glob("*.py")
        if not (p.name.startswith("__") and p.name.endswith("__.py"))
    )


# The corpus floor. MEASURED 2026-09-01: 204 files match `scripts/*.py` at the
# root, 17 of them snake_case. A sweep is green over an empty corpus, so a glob
# that stops matching - the directory renamed, the tree relocated, `_root_cli_scripts`
# tightened by one predicate too many - would report a clean pass having looked at
# nothing. Well below the measured number on purpose: this pins "the walk found the
# tree", not the tree's exact size, which grows every week.
MIN_ROOT_SCRIPTS = 150


def test_the_walk_actually_finds_the_scripts_directory():
    """A floor, so the two sweeps below cannot pass over an empty corpus."""
    assert SCRIPTS_DIR.is_dir(), f"{SCRIPTS_DIR} is not a directory"
    found = _root_cli_scripts()
    assert len(found) >= MIN_ROOT_SCRIPTS, (
        f"_root_cli_scripts() found {len(found)} file(s), under the floor of "
        f"{MIN_ROOT_SCRIPTS} measured 2026-09-01; the sweep below is checking "
        "almost nothing"
    )
    # And it must still be seeing snake_case files at all, or the predicate that
    # decides a violation has nothing to decide about.
    snake = [p for p in found if "_" in p.stem]
    assert len(snake) >= len(ALLOWED_SNAKE), (
        f"only {len(snake)} snake_case script(s) reach the check while "
        f"{len(ALLOWED_SNAKE)} are excluded by name; the glob is missing files"
    )


def test_standalone_cli_scripts_are_kebab_case():
    """Any snake_case script in scripts/ must be a documented exclusion."""
    violations = []
    for script in _root_cli_scripts():
        if "_" in script.stem and script.name not in ALLOWED_SNAKE:
            violations.append(script.name)
    assert not violations, (
        "These snake_case scripts are neither dual-role nor documented CLI exceptions — "
        "rename to kebab-case or justify and add to the exclusion set:\n"
        + "\n".join(f"  scripts/{v}" for v in violations)
    )


def test_documented_exclusions_still_exist():
    """Guards against silent rename/delete of an excluded script (keeps this list honest)."""
    for name in ALLOWED_SNAKE:
        assert (SCRIPTS_DIR / name).exists(), (
            f"Excluded script {name!r} no longer exists in scripts/. "
            f"Remove it from the exclusion set or update the replacement path."
        )
