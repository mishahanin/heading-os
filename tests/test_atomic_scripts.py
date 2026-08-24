"""State files these scripts persist are written atomically (tmp + os.replace).

**This is a floor, not coverage.** The list below is hand-kept, and on
2026-08-24 the engine audit found two files it did not name — `browser.py`
(the CDP lock) and `build_engine_repo.py` (the build manifest) — each writing
JSON state with a plain `write_text`, each against the workspace's own
no-non-atomic-state-writes rule.

The obvious repair is a derived scan instead of a list, and it was measured
before being rejected: `.write_text(` whose argument reaches `json.dumps` hits
42 sites under `scripts/`, and most are build artifacts, generated reports and
one-shot exports rather than persistent state. Some are inside an atomic helper
already. A detector that cannot be made correct is worse than an honest count,
so the list stays a list and this docstring says what it does not reach.

Originally F-M4 (crm-health, offboard-exec); extended 2026-08-24.
"""
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent

# (script, the identifier whose write must be atomic)
STATE_WRITERS = [
    ("scripts/crm-health.py", "PEOPLE_FILE"),
    ("scripts/offboard-exec.py", "registry_file"),
    # Found by the 2026-08-23 engine audit, shards scripts-03-p2 / p3.
    ("scripts/browser.py", "LOCK_FILE"),
    ("scripts/build_engine_repo.py", "src_manifest"),
]


@pytest.mark.parametrize("script, _target", STATE_WRITERS)
def test_the_script_uses_the_atomic_helper(script, _target):
    src = (ENGINE / script).read_text(encoding="utf-8")
    assert "atomic_write_text" in src, (
        f"{script} persists state; write it through atomic_write_text "
        "(tmp + os.replace), never a plain write_text"
    )


@pytest.mark.parametrize("script, target", STATE_WRITERS)
def test_the_state_file_has_no_bare_write_text(script, target):
    lines = (ENGINE / script).read_text(encoding="utf-8").splitlines()
    bare = [l.strip() for l in lines if target in l and ".write_text(" in l]
    assert not bare, (
        f"{script} still writes {target} non-atomically: {bare}. A crash or a "
        "concurrent read mid-write leaves truncated JSON, and every consumer of "
        "these files treats a parse failure as 'no state'."
    )
