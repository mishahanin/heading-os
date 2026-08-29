"""`init-data.py` traced back where the next line promised a refusal.

`init_data` guarded with `target.exists() and any(target.iterdir())`, meaning
"exists and is not empty". When the path exists as a regular FILE, `iterdir()`
raises before `any()` can reach a decision. Measured 2026-08-29 with
`touch /tmp/fake-data-probe` and `--path /tmp/fake-data-probe`: uncaught
`NotADirectoryError: [Errno 20] Not a directory`, no refusal message.

Every case here runs against `tmp_path`. The scaffold must never be pointed at
a real data overlay.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

_SCRIPT = _WORKSPACE / "scripts" / "init-data.py"


def _load_init_data():
    """Import the hyphenated script by path (not a legal module name)."""
    spec = importlib.util.spec_from_file_location("heading_init_data", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_existing_regular_file_is_refused_not_crashed_on(tmp_path, capsys):
    target = tmp_path / "heading-os-data"
    target.write_text("not a data folder\n", encoding="utf-8")

    module = _load_init_data()
    rc = module.init_data(target)

    assert rc == 1
    assert "Refusing to scaffold" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "not a data folder\n"


def test_a_symlink_is_refused(tmp_path, capsys):
    real = tmp_path / "elsewhere"
    real.mkdir()
    target = tmp_path / "heading-os-data"
    target.symlink_to(real, target_is_directory=True)

    module = _load_init_data()
    rc = module.init_data(target)

    assert rc == 1
    assert "Refusing to scaffold" in capsys.readouterr().out
    assert list(real.iterdir()) == [], "the scaffold wrote through the symlink"


def test_a_non_empty_directory_is_still_refused(tmp_path, capsys):
    target = tmp_path / "heading-os-data"
    target.mkdir()
    (target / "crm").mkdir()

    module = _load_init_data()
    rc = module.init_data(target)

    assert rc == 1
    assert "is not empty" in capsys.readouterr().out


def test_an_absent_path_is_still_scaffolded(tmp_path):
    target = tmp_path / "heading-os-data"

    module = _load_init_data()
    rc = module.init_data(target)

    assert rc == 0
    assert module.DATA_DIRS, "DATA_DIRS is empty; this test would prove nothing"
    for directory in module.DATA_DIRS:
        assert (target / directory).is_dir(), directory
    assert (target / ".schema-version").exists()


def test_an_empty_directory_is_still_scaffolded(tmp_path):
    target = tmp_path / "heading-os-data"
    target.mkdir()

    module = _load_init_data()

    assert module.init_data(target) == 0
    assert (target / ".schema-version").exists()
