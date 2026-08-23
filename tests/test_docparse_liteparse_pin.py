"""The installer must install the version the parser was written against.

Found by the 2026-08-23 audit. `scripts/docparse.py` named three liteparse
versions and one of them was wrong:

* the module docstring and `setup --check` told the operator `2.0.0`;
* `parse_document` was written against the 2.0 API, where `dpi`,
  `target_pages` and `password` moved into the `LiteParse(...)` constructor;
* `setup --install` — the command the failure message points at — ran
  `pip install liteparse==1.2.1`.

So the documented repair path installed a package whose constructor rejects
every keyword the parser passes. Setup printed "Setup complete", and the first
document raised a TypeError with nothing connecting the two.

Version literals in prose drift because nothing reads them. These tests read
them.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_DOCPARSE = ROOT / "scripts" / "docparse.py"


def _load():
    spec = importlib.util.spec_from_file_location("docparse_pin", _DOCPARSE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["docparse_pin"] = module
    spec.loader.exec_module(module)
    return module


docparse = _load()
SOURCE = _DOCPARSE.read_text(encoding="utf-8")


def test_every_liteparse_pin_in_the_file_is_the_same_version():
    """Includes the docstring, which is what the operator reads first."""
    found = set(re.findall(r"liteparse==([0-9][0-9.]*)", SOURCE))
    assert found == {docparse.LITEPARSE_VERSION}, (
        f"docparse.py names liteparse versions {sorted(found)}; "
        f"LITEPARSE_VERSION is {docparse.LITEPARSE_VERSION}"
    )


def test_the_pin_is_the_major_version_the_parser_calls():
    """`parse_document` passes 2.0-only constructor keywords. Pin 2.x or fix it."""
    assert docparse.LITEPARSE_VERSION.startswith("2."), (
        "parse_document builds LiteParse(dpi=..., target_pages=..., password=...), "
        "which is the 2.0 constructor; a 1.x pin cannot run it"
    )
    for keyword in ("dpi", "target_pages", "password"):
        assert f'"{keyword}"' in SOURCE or f"{keyword}=" in SOURCE


def test_the_installer_uses_the_constant_rather_than_a_literal():
    """The structural half: no fourth copy to drift."""
    install_line = next(
        line for line in SOURCE.splitlines() if '"pip", "install"' in line
    )
    assert "LITEPARSE_VERSION" in install_line, (
        "setup --install hardcodes a version again: " + install_line.strip()
    )
