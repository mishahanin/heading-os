"""F-M4: crm-health.py and offboard-exec.py must import and use atomic_write_text."""
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent


def test_crm_health_imports_atomic_write():
    src = (ENGINE / "scripts/crm-health.py").read_text(encoding="utf-8")
    assert "atomic_write_text" in src, \
        "crm-health.py must use atomic_write_text (F-M4)"


def test_offboard_exec_imports_atomic_write():
    src = (ENGINE / "scripts/offboard-exec.py").read_text(encoding="utf-8")
    assert "atomic_write_text" in src, \
        "offboard-exec.py must use atomic_write_text (F-M4)"


def test_crm_health_no_bare_write_text_for_people_file():
    src = (ENGINE / "scripts/crm-health.py").read_text(encoding="utf-8")
    # The PEOPLE_FILE write must not use the bare method-call form (.write_text(...))
    lines = src.splitlines()
    people_writes = [l for l in lines if "PEOPLE_FILE" in l and ".write_text(" in l]
    assert not people_writes, \
        f"crm-health.py still has bare write_text for PEOPLE_FILE: {people_writes}"


def test_offboard_exec_no_bare_write_text_for_registry():
    src = (ENGINE / "scripts/offboard-exec.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    registry_writes = [l for l in lines if "registry_file" in l and ".write_text(" in l]
    assert not registry_writes, \
        f"offboard-exec.py still has bare write_text for registry_file: {registry_writes}"
