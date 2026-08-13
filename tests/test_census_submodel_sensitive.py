"""The egress guard on the sub-model bench, in BOTH directions.

`scripts/census-submodel-bench.py` ships workspace text to a third party, so it
refuses whenever `SENSITIVE_MODE` is not explicitly cleared - the fail-closed
`is_sensitive()`, not `sensitivity_is_declared()`. That default is deliberate
and is pinned below.

What the original guard could not do was say WHY it fired: on an ordinary
machine, where nobody has ever set the variable, it printed "сессия объявлена
чувствительной" - a declaration that had not happened
(`.claude/rules/scope-claims.md`). The run that shipped it recorded a passing
check for the firing direction only, which is exactly how the false sentence
survived. So these tests assert the refusal AND the sentence, in the declared
case, the undeclared-default case, and the cleared case.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_submodel_bench():
    """Import the hyphenated CLI script by path; its name is not a module name."""
    spec = importlib.util.spec_from_file_location(
        "census_submodel_bench", ROOT / "scripts" / "census-submodel-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_submodel_bench"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_submodel_bench()


def test_declared_sensitive_refuses_and_names_the_declaration(monkeypatch, capsys):
    monkeypatch.setenv("SENSITIVE_MODE", "on")
    with pytest.raises(SystemExit) as exc:
        bench._refuse_if_sensitive()
    assert exc.value.code == 2
    assert "объявлена чувствительной" in capsys.readouterr().err


def test_unset_refuses_without_claiming_a_declaration(monkeypatch, capsys):
    """The direction the shipping run never checked.

    Unset is the machine's default, not an operator's declaration. The guard
    still refuses - that is the fail-closed property - but it may not report the
    refusal as something a person decided.
    """
    monkeypatch.delenv("SENSITIVE_MODE", raising=False)
    with pytest.raises(SystemExit) as exc:
        bench._refuse_if_sensitive()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "объявлена чувствительной" not in err
    assert "умолчание машины" in err
    assert "SENSITIVE_MODE=off" in err


@pytest.mark.parametrize("cleared", ["off", "0", "false", "no", "cleared", "OFF"])
def test_explicitly_cleared_permits_the_run(monkeypatch, cleared):
    monkeypatch.setenv("SENSITIVE_MODE", cleared)
    bench._refuse_if_sensitive()  # must not raise


def test_empty_string_is_absence_not_clearance(monkeypatch, capsys):
    """Empty is the same absence as unset, spelled shorter - so it refuses."""
    monkeypatch.setenv("SENSITIVE_MODE", "")
    with pytest.raises(SystemExit) as exc:
        bench._refuse_if_sensitive()
    assert exc.value.code == 2
    assert "объявлена чувствительной" not in capsys.readouterr().err


# ============================================================
# The audit's H5-H7: the harness must be able to fail
# ============================================================

def test_the_sensitivity_guard_is_wired_into_the_run_path():
    """The guard is only a guard where main() actually calls it.

    Every other test here calls `_refuse_if_sensitive` directly, so deleting its
    single call site left the suite green while every run shipped workspace text
    to a third party. This one drives `main()`.
    """
    import os
    from unittest import mock

    bench = _load_submodel_bench()
    with mock.patch.dict(os.environ, {"SENSITIVE_MODE": "1"}), \
            mock.patch.object(sys, "argv", ["census-submodel-bench.py", "accuracy"]), \
            mock.patch.object(bench, "score_accuracy") as scored, \
            mock.patch.object(bench, "score_speed") as sped, \
            pytest.raises(SystemExit) as exit_info:
        bench.main()
    assert exit_info.value.code != 0, "a sensitive session must not reach the network"
    scored.assert_not_called()
    sped.assert_not_called()


def test_a_width_whose_cases_share_one_truth_is_called_degenerate():
    """A constant answer scored 90/90 at width 50000, and the score ROSE with width.

    The filled-fraction guard saw 28/30 filled and printed "годен", so the cell
    that measured nothing looked exactly like the cells that measured something.
    """
    bench = _load_submodel_bench()

    class _C:
        def __init__(self, truth):
            self.truth = truth
            self.filled = True

    same = [_C({"field": None, "checkboxes": 0, "mentions": False}) for _ in range(30)]
    assert bench.distinct_truth_fraction(same) < bench.MIN_DISTINCT_TRUTH_FRACTION
    assert bench.constant_baseline(same) == (90, 90)

    varied = [_C({"field": f"2026-01-{i:02d}", "checkboxes": i % 4,
                  "mentions": bool(i % 2)}) for i in range(1, 31)]
    assert bench.distinct_truth_fraction(varied) >= bench.MIN_DISTINCT_TRUTH_FRACTION
    assert bench.constant_baseline(varied)[0] < 90


def test_the_substring_probe_has_two_answers(tmp_path):
    """A probe absent from every case gives a third of the score away.

    Driven through `_plant` and `_truth` rather than `build_cases`, so it holds
    on a bare public clone where there is no corpus to sample.
    """
    bench = _load_submodel_bench()
    marker = bench.DEFAULT_MARKER
    truths = [bench._truth(bench._plant("body text\n", marker, i), marker)["mentions"]
              for i in range(6)]
    positives = sum(truths)
    assert 0 < positives < len(truths), (
        f"probe truth must be mixed, got {positives}/{len(truths)}")
