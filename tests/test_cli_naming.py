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
    "cold_sweep_core.py",            # bridge-daemon.py, cold-sweep.py, test_cold_sweep_routing.py
    "crm_migrate_to_entity_model.py",  # tests/test_crm_migration.py
    "crm_next.py",                   # tests/test_crm_next.py
    "fireside_topics.py",            # fireside-bot.py, test_fireside_topics.py, test_fireside_topic_handlers.py
    "fireside_webhook.py",           # fireside-bot-daemon.py
    "marp_render.py",                # test_marp_integration.py, test_marp_render.py
    "odin_brain_lint.py",            # odin-brain-health.py, test_odin_temporal_validity.py
    "odin_pagerank.py",              # tests/test_odin_pagerank.py
    "resolve_customization.py",      # tests/test_resolve_customization.py
    "resolve_entity.py",             # tests/test_resolve_entity.py
    "skill_graph.py",                # next-signal.py, test_skill_graph.py
    "watchdog_core.py",              # daemon-watchdog.py, bridge-daemon.py, test_watchdog.py
}

# Pure-CLI scripts deliberately kept snake_case (documented above).
DOCUMENTED_CLI_EXCEPTIONS = {
    "build_data_repo.py",            # half of build pair; cutover tool referenced by current name
}

ALLOWED_SNAKE = DUAL_ROLE_EXCLUSIONS | DOCUMENTED_CLI_EXCEPTIONS


def _root_cli_scripts():
    """scripts/*.py at the root — not utils/ (library), not archive/."""
    return sorted(p for p in SCRIPTS_DIR.glob("*.py"))


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
