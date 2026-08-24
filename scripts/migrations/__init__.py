"""Data-overlay migrations (F-9.7).

Ordered `NNNN_<slug>.py` modules, each exposing:
  - ``VERSION: int``  - the schema version this migration brings the overlay TO.
  - ``up(data_root: Path, dry_run: bool = False) -> None`` - the forward step,
    idempotent and backup-first.

`scripts/migrate-data.py` is the runner. `paths.require_writable_data_root()`
refuses writes when the overlay schema is behind ``max_version()`` (pending
migrations). This package imports nothing from ``scripts.utils.paths`` at module
load, so the lazy import inside ``require_writable_data_root`` cannot cycle.
"""
import importlib
import pkgutil
from pathlib import Path


def registered_migrations():
    """Return ``[(version, module), ...]`` sorted by version.

    Discovers sibling modules whose name starts with a 4-digit prefix and which
    expose an integer ``VERSION``. Non-conforming modules are ignored.
    """
    out = []
    pkg_dir = Path(__file__).parent
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        name = info.name
        if len(name) < 4 or not name[:4].isdigit():
            continue
        mod = importlib.import_module(f"{__name__}.{name}")
        ver = getattr(mod, "VERSION", None)
        if isinstance(ver, int):
            out.append((ver, mod))
    out.sort(key=lambda t: t[0])
    # Two modules shipping the same VERSION both ran, in whatever order the sort
    # happened to produce, and `_write_version` stamped the number twice. The
    # runner's entire contract is ORDERED migrations, so a duplicate is a config
    # error, not a tie to break silently.
    seen: dict = {}
    for ver, mod in out:
        if ver in seen:
            raise ValueError(
                f"duplicate migration VERSION {ver}: {seen[ver].__name__} and "
                f"{mod.__name__} both claim it; migrations must be ordered")
        seen[ver] = mod
    return out


def max_version() -> int:
    """Highest registered migration version, or 0 when none are registered."""
    migs = registered_migrations()
    return migs[-1][0] if migs else 0
