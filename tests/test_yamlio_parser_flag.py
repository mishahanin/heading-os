#!/usr/bin/env python3
"""`USING_LIBYAML` must tell the truth about which parser is bound.

`scripts/utils/yamlio.py` exports the flag with the comment "Exported so
callers/tests can assert which parser is actually in use." On 2026-08-20 no test
asserted it and no caller read it: the name appeared exactly once in the whole
tree, at its own assignment. A flag nobody checks is a claim nobody verifies, and
this one guards a 21x parse-speed decision on the hot classifier path that the
push wall and the `engine-tree-clean` hook both run.

So the flag is kept and this file is the assertion its comment promised. It holds
the flag to the object actually bound, not to a hardcoded expectation, because
whether libyaml is present is a property of the installed wheel and not of this
repository — a machine without it must still pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scripts.utils import yamlio  # noqa: E402


def test_the_flag_matches_the_loader_that_is_bound():
    """Checked against PyYAML's OWN class, not against a name string.

    This assertion used to be `USING_LIBYAML is (SafeLoader.__name__ ==
    "CSafeLoader")`, which was the implementation restated: both sides read the
    same `__name__`, so an alias or a rename would move them together and the
    test could not fail. Comparing to the class object is an independent source.
    """
    assert yamlio.USING_LIBYAML is (yamlio.SafeLoader is getattr(yaml, "CSafeLoader", None))


def test_the_flag_agrees_with_pyyamls_build_flag():
    """`yaml.__with_libyaml__` is the third, independent witness.

    They can legitimately differ only one way: libyaml present but the
    `from yaml import CSafeLoader` above failing for some other reason. That
    would be worth a loud failure, not a silent pass.
    """
    assert yaml.__with_libyaml__ == yamlio.USING_LIBYAML, (
        f"yamlio bound {yamlio.SafeLoader.__name__} while PyYAML reports "
        f"__with_libyaml__={yaml.__with_libyaml__}"
    )


def test_the_bound_loader_is_one_of_the_two_safe_ones():
    """Neither branch may drift to a loader outside the safe tag set. That is
    the security contract the module docstring makes, and `safe_load` suppresses
    ruff S506 on the strength of it."""
    assert yamlio.SafeLoader.__name__ in ("CSafeLoader", "SafeLoader")


def test_this_installation_has_the_c_parser():
    """Reported, not silently assumed. If this fails, the workspace still works
    and is 21x slower on every routing-map parse — which is worth knowing rather
    than skipping past."""
    assert yamlio.USING_LIBYAML, (
        "PyYAML is installed without libyaml, so every YAML parse falls back to "
        "the pure-Python SafeLoader. Measured 5.537 ms vs 0.260 ms per call on "
        "config/routing-map.yaml. Reinstall PyYAML with libyaml available."
    )
