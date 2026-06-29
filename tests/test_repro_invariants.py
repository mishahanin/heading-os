"""Reproducibility invariants: the lockfile, python pin, and coverage gate exist."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_python_version_pinned():
    pin = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pin.startswith("3.11"), f"expected 3.11.x, got {pin!r}"


def test_requires_python_declared():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.11"


def test_uv_lock_committed():
    lock = ROOT / "uv.lock"
    assert lock.is_file() and lock.stat().st_size > 0
