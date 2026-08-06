"""What `shape_refusal` answers, and what a fault inside it costs.

This file was written to pin the checker's WIRING: the frozen contract for its
slice bound no criterion to the two call sites, which is the "a renderer nothing
calls" gap in its own right. Both call sites were the freeze lifecycle's — the
candidate builder in scripts/canopus.py and the attestation writer in
scripts/utils/canopus_gate.py — and both were deleted on 2026-08-07. The wiring
assertions went with them rather than being re-pointed at something that does not
call it; what remains here is the checker's own behaviour, which is unchanged and
still worth pinning.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_reachability_alone_is_not_an_accusation(tmp_path):
    """H3 from /scrutinize: reachability is not use.

    scripts/canopus.py reaches denial_log.py in three hops through gate_yield,
    and a contract that merely imports canopus was told "the code under test
    reads denial_log" — false, on the most common contract shape here. The
    accusation now needs a directly imported module to read the store itself.
    """
    from scripts.utils.production_shape import shape_refusal

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_contract.py").write_text(
        "def test_it():\n    from scripts.canopus import main\n    assert main\n",
        encoding="utf-8",
    )

    assert shape_refusal([probe], _ROOT) == ""


def test_a_directly_imported_store_reader_is_still_accused(tmp_path):
    """H3's other arm: narrowing must not disarm the check.

    scripts/utils/gate_yield.py imports from denial_log itself, so a contract
    importing IT and never calling the writer is the real defect and must still
    be refused.
    """
    from scripts.utils.production_shape import shape_refusal

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_contract.py").write_text(
        "def test_it():\n    from scripts.utils.gate_yield import summarise\n"
        "    assert summarise\n",
        encoding="utf-8",
    )

    assert "denial_log" in shape_refusal([probe], _ROOT)


def test_a_fixture_minted_in_conftest_satisfies_the_check(tmp_path):
    """M2 from /scrutinize: conftest.py is where a pytest fixture lives.

    A contract that does exactly what this gate demands, and puts the fixture
    where pytest expects it, was refused for it.
    """
    from scripts.utils.production_shape import shape_refusal

    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_contract.py").write_text(
        "def test_it():\n    from scripts.utils.gate_yield import summarise\n"
        "    assert summarise\n",
        encoding="utf-8",
    )
    (probe / "conftest.py").write_text(
        "from scripts.utils.denial_log import log_denial\n\n\n"
        "def seed():\n    log_denial(mechanism='m', action='a', reason='r')\n",
        encoding="utf-8",
    )

    assert shape_refusal([probe], _ROOT) == ""


def test_a_fault_inside_the_checker_is_reported_and_not_swallowed(capsys):
    """H2 from /scrutinize: total is not the same as silent.

    Refusing nothing on a fault is the requirement. Saying nothing about it is
    a separate choice and the wrong one, because a fault leaves the gate
    quietly toothless. Both siblings sharing this shape bind and report, and
    the workspace rule forbids a handler that neither logs nor re-raises.
    """
    from scripts.utils.production_shape import shape_refusal

    assert shape_refusal([1], Path("/nonexistent-root")) == ""

    assert "faulted and refused nothing" in capsys.readouterr().err


def test_an_unexpected_fault_inside_the_checker_refuses_nothing():
    """Totality, and the frozen contract does not pin it.

    Found by mutation at step 11: deleting the catch-all in shape_refusal kills
    none of the ten contract tests. Both SC-6 cases return "" before they ever
    reach it, one on an empty file list and one on the inner SyntaxError
    handler, so the guarantee that a bug in this checker cannot become a wall no
    slice can pass was measured by nothing.

    An int is not path-like, so Path() raises TypeError well inside the try,
    which is a fault of a kind no inner handler names.
    """
    from scripts.utils.production_shape import shape_refusal

    assert shape_refusal([1], Path("/nonexistent-root")) == ""
