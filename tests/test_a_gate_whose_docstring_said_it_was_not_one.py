"""scripts/ste-check.py told its reader it was not a gate. It is one.

Two sentences in the module docstring denied gate status while the rest of the
same file asserted it:

  * "Neither is a gate." sat six lines above a usage block whose own comment
    reads "Gate form: errors only".
  * The `--skills` usage line was annotated "(ungated)" while `SKILLS_HELP`
    twenty lines further down reads "Gated since 2026-08-17".

Neither was a matter of taste. `.pre-commit-config.yaml` registers two hooks
(`documentation-style`, `documentation-style-skills`) and
`.github/workflows/ci.yml` two steps, all four running this script, and an
error exits 1 and fails the commit. A reader who believed the docstring would
edit a gated documentation page expecting an advisory warning and be refused
at commit time with no idea why.

The premise is asserted here too, not assumed: if the pre-commit and CI wiring
were ever removed, "not a gate" would become the TRUE sentence and this file's
regression test would be guarding a falsehood. `test_the_gate_wiring_that_makes
_the_docstring_wrong_is_real` fails first in that case, so the two claims can
never drift apart silently.

Run: python3 -m pytest tests/test_a_gate_whose_docstring_said_it_was_not_one.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "ste-check.py"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# Sentences that deny gate status. Deliberately narrow: each is an assertion
# ABOUT this script's own enforcement, not an appearance of the word "gate".
# The accurate replacement text says "This one GATES" and "Gate form" and must
# keep passing, which is what `test_the_detector_still_accepts_the_accurate
# _sentence` pins.
_DENIALS = (
    r"neither is a gate",
    r"\bis not a gate\b",
    r"\bnot a gate\b",
    r"\bungated\b",
    r"\bno gate\b",
)


def _denials(text: str) -> list[str]:
    """Every gate-denying phrase in `text`, lowercased, in source order."""
    found = []
    low = text.lower()
    for pattern in _DENIALS:
        found.extend(m.group(0) for m in re.finditer(pattern, low))
    return found


def _module_docstring(path: Path) -> str:
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""


# ============================================================
# The premise: this script really is wired into two gates
# ============================================================

def test_the_gate_wiring_that_makes_the_docstring_wrong_is_real():
    """The anchor. Without live wiring the regression below guards nothing."""
    precommit = PRECOMMIT.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")

    assert "scripts/ste-check.py --all --quiet" in precommit
    assert "scripts/ste-check.py --skills --quiet" in precommit
    assert "scripts/ste-check.py --all --quiet" in ci
    assert "scripts/ste-check.py --skills --quiet" in ci


def test_an_error_still_exits_one_so_the_gate_can_refuse():
    """The other half of the premise: a gate that never refuses is not a gate.

    Driven through the pure `audit()`, so no file and no subprocess. A step
    well over the 20-word procedural limit is the cheapest real error.
    """
    ste = _load_ste()
    step = "1. " + " ".join(["configure"] + ["the setting"] * 15) + "."
    result = ste.audit(step)
    assert result["summary"]["errors"] >= 1
    assert result["passed"] is False


def _load_ste():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ste_check_gate_doc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ste_check_gate_doc"] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# The regression
# ============================================================

def test_the_module_docstring_does_not_deny_being_a_gate():
    found = _denials(_module_docstring(SCRIPT))
    assert found == [], (
        f"scripts/ste-check.py's module docstring denies gate status "
        f"{found!r}, while pre-commit and CI both run it and an error fails "
        f"the commit"
    )


def test_the_skills_usage_line_is_not_marked_ungated():
    """Called out on its own because it is the half the audit missed.

    "Neither is a gate" and "(ungated)" are one defect wearing two hats: the
    `--skills` half was armed on 2026-08-17 and the usage line was never
    updated.
    """
    lines = [ln for ln in _module_docstring(SCRIPT).splitlines()
             if "--skills" in ln]
    assert lines, "the --skills usage line has gone; this guard is now blind"
    for line in lines:
        assert _denials(line) == [], f"stale gate denial on: {line.strip()}"


# ============================================================
# Teeth: the detector refuses, rather than accepting everything
# ============================================================

@pytest.mark.parametrize("retired", [
    "Companion to humanization-check.py. Neither is a gate.",
    "  python scripts/ste-check.py --skills --quiet  # skill bodies (ungated)",
    "This script is not a gate.",
])
def test_the_detector_fires_on_the_retired_sentences(retired):
    assert _denials(retired) != []


def test_the_detector_still_accepts_the_accurate_sentence():
    """The anchor for the detector itself.

    A guard that flagged every mention of "gate" would pass the regression
    above by refusing the correct text too. The replacement wording has to
    survive it.
    """
    accurate = (
        "That one is advisory. This one GATES: `.pre-commit-config.yaml` and "
        "`.github/workflows/ci.yml` both run `--all --quiet` and "
        "`--skills --quiet`, and an error fails the commit.\n"
        "  python scripts/ste-check.py --all --quiet    # Gate form: errors only"
    )
    assert _denials(accurate) == []
