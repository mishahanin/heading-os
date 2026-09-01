#!/usr/bin/env python3
"""Arm the overlay write guard in EVERY process of this venv, not just pytest.

Usage:
    python scripts/overlay-guard-install.py --install
    python scripts/overlay-guard-install.py --check
    python scripts/overlay-guard-install.py --uninstall

Why this exists. `scripts/utils/overlay_write_guard.py` refuses a write to the
operator's private overlay, and until 2026-08-31 the only thing that armed it was
`tests/conftest.py`. A conftest is imported by pytest and by nothing else, so a
plain `.venv/bin/python` ran with no guard at all: that is how a scratch probe
called an entry point blind, `openpyxl` saved, and a real operator workbook was
overwritten with every test in the suite green.

How it arms. A `.pth` file in site-packages whose line begins with `import` is
EXECUTED by `site.py` at interpreter startup, before any user code. That is the
same mechanism `coverage` uses (`a1_coverage.pth`) and `coloredlogs` uses
(`coloredlogs.pth`), both already in this venv, so it is a supported path rather
than a trick. Ordering is alphabetical, and the editable install that puts the
engine on `sys.path` is `_editable_impl_heading_os_engine.pth`; `_` sorts before
the `zz_` prefix used here, so the import works by the time this line runs.

Three deliberate constraints on that line:

* It does NOTHING unless `HEADING_OS_OVERLAY_GUARD` is set to a mode this build
  recognises. Default-off, because a line that runs at startup for every process
  in the venv must have a state where it cannot be blamed for anything, and an
  operator who breaks their interpreter needs an escape that is not "edit a file
  inside site-packages".
* Every failure is swallowed. An exception raised from a `.pth` during `site.py`
  is printed and, worse, arrives before almost anything is set up; a guard that
  cannot arm must let the process run unguarded rather than take the process out.
* It imports one module and calls one function. No argument parsing, no config
  read, no filesystem walk beyond what `arm()` already does.

What this does NOT do: survive `uv sync`. `.venv/` is gitignored and a sync
rebuilds site-packages, so the file goes away silently and the guard is off again
with nothing to say so. `--check` is the answer to that, and
`tests/test_the_guard_that_only_armed_under_pytest.py` fails when the venv is
missing the file, so the suite says it out loud instead of passing quietly.
Re-run `--install` after any `uv sync`.
"""
from __future__ import annotations

import argparse
import sys
import sysconfig
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, GRAY, RED, RESET, YELLOW  # noqa: E402

# `zz_` so it sorts after the editable-install `.pth` that puts the engine on
# `sys.path`. Without that ordering the import inside the line cannot resolve.
PTH_NAME = "zz_heading_os_overlay_guard.pth"

# ONE line, and it must stay one line: `site.py` executes each line of a `.pth`
# separately, so a statement split across two lines is two broken statements.
# Written with `exec()` of a string containing newlines, which is how both
# `a1_coverage.pth` and `coloredlogs.pth` in this same venv do it.
GUARD_SOURCE = Path(__file__).resolve().parent / "utils" / "overlay_write_guard.py"


def pth_line(guard_source: Path | None = None) -> str:
    """The single line `site.py` executes, built around one absolute path.

    Loaded BY PATH under a private module name, never as `scripts.utils.
    overlay_write_guard`. MEASURED 2026-08-31, the first time this armed by
    default: an `import scripts...` here binds the name `scripts` in
    `sys.modules` to the engine's package during `site.py`, before any user code
    runs, and after that `python -m scripts.anything` from a directory with its
    own `scripts/` package resolves against the engine instead. 62 tests went
    red, all of them running skill-local `scripts.*` modules that had stopped
    being findable. A line that runs in every process must not decide what a
    top-level package name means for the rest of the interpreter.

    The path is baked in, which is correct for a file that lives inside one
    venv's site-packages and is re-written by `--install`. `--check` compares the
    file against this function, so a moved workspace reports stale rather than
    arming something that is no longer there.

    `guard_source=None` rather than `=GUARD_SOURCE`, because a default is
    evaluated once at import and a caller patching the module global cannot
    redirect it. `tests/test_defaults_that_froze_a_path_at_import.py` caught this
    exact line the first time it was written; the rule exists because eight
    earlier cases of it had to be found by hand.
    """
    if guard_source is None:
        guard_source = GUARD_SOURCE
    return (
        "import os, sys; exec("
        "'if os.environ.get(\"HEADING_OS_OVERLAY_GUARD\") != \"off\":\\n'"
        "'    try:\\n'"
        "'        import importlib.util as _u\\n'"
        f"'        _s = _u.spec_from_file_location(\"_heading_os_overlay_guard\", r\"{guard_source}\")\\n'"
        "'        _m = _u.module_from_spec(_s)\\n'"
        "'        sys.modules[\"_heading_os_overlay_guard\"] = _m\\n'"
        "'        _s.loader.exec_module(_m)\\n'"
        "'        _m.arm_process_wide()\\n'"
        "'    except Exception:\\n'"
        "'        pass\\n'"
        ")"
    )


PTH_LINE = pth_line()


def site_packages() -> Path:
    """This interpreter's own site-packages, asked of the interpreter.

    `sysconfig` rather than a hand-built `.venv/lib/pythonX.Y/site-packages`
    path: the version segment is exactly the kind of literal that rots when the
    pinned interpreter moves, and the answer is available for free.
    """
    return Path(sysconfig.get_paths()["purelib"])


def pth_path() -> Path:
    return site_packages() / PTH_NAME


def install() -> int:
    target = pth_path()
    if not target.parent.is_dir():
        print(f"{RED}no site-packages at {target.parent}{RESET}")
        print("run this with the venv interpreter: .venv/bin/python scripts/overlay-guard-install.py --install")
        return 1
    existing = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(PTH_LINE + "\n", encoding="utf-8")
    verb = "unchanged" if existing == PTH_LINE + "\n" else ("updated" if existing else "installed")
    print(f"{GREEN}{verb}{RESET} {target}")
    print(f"{GRAY}the line is inert until HEADING_OS_OVERLAY_GUARD is set to record or refuse{RESET}")
    return 0


def check() -> int:
    target = pth_path()
    if not target.exists():
        print(f"{RED}absent{RESET} {target}")
        print("the guard arms under pytest only; run --install")
        return 1
    text = target.read_text(encoding="utf-8")
    if text.strip() != PTH_LINE:
        print(f"{YELLOW}stale{RESET} {target}")
        print("the file exists but does not match this build's line; run --install")
        return 1
    print(f"{GREEN}armed{RESET} {target}")
    return 0


def uninstall() -> int:
    target = pth_path()
    if not target.exists():
        print(f"{GRAY}already absent{RESET} {target}")
        return 0
    target.unlink()
    print(f"{GREEN}removed{RESET} {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="write the .pth into site-packages")
    group.add_argument("--check", action="store_true", help="exit 1 when it is absent or stale")
    group.add_argument("--uninstall", action="store_true", help="remove the .pth")
    args = parser.parse_args()
    if args.install:
        return install()
    if args.check:
        return check()
    return uninstall()


if __name__ == "__main__":
    sys.exit(main())
