"""A12 — one criterion, one test, checked by machine.

Retired from `tests/contract/2026-08-02-sc-trace/` into the ordinary suite at
step 13, 2026-08-02, unchanged apart from this note and the root path. The
coverage is worth keeping; the lock on it would bind every later slice to this
one's behaviour.

Two rules this contract PREDATES, both earned after it was frozen, are pinned in
`tests/test_sc_trace_claim_shape.py` instead: a claim OPENS a test's docstring,
and the empty-criteria refusal is the only thing that can fire when nothing is
claimed either. The second exists because mutation found the two SC-5 tests here
green for the wrong reason -- with the empty-criteria branch deleted, their
contract's SC-1 and SC-2 claims become orphans and the orphan branch refuses, so
neither test can tell which rule fired.


Measured on the two slices shipped 2026-08-02: the `gate-yield` gate artifact
carries seven success criteria, its contract carries 28 test functions, and the
string `SC-` appears in those tests three times, all three in prose. Five of the
seven criteria were bound to nothing at all. The criteria and the tests that
decide them are written twice, by hand, and nothing detects a divergence.

This contract fixes the binding: a test claims a criterion in its DOCSTRING, and
`scripts/sc-trace.py` reads the two sides against each other.

Seven of the nineteen tests bound that reading to the `approve` and `freeze`
gates in `scripts/canopus.py`, which refused a contract leaving a criterion
unclaimed. Those commands were deleted on 2026-08-07, so the gate half of A12 is
gone with them and only the report survives. The twelve below are unchanged.

Authoring rule, enforced: every import of the code under test happens INSIDE a
test body. The implementation does not exist yet, so a module-scope import stops
the file collecting, and a file that collects nothing cannot be frozen.

Second authoring rule, earned by two measured failures: a test that reads
working-tree state takes its OWN scratch root, and a test comparing two runs
compares the invariant, never the raw text. Every test below is either pure over
strings or scoped to `tmp_path`.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TRACE = _ROOT / "scripts" / "sc-trace.py"


# ============================================================
# Fixtures — a scratch tree and a scratch gate artifact
# ============================================================

def _make_tree(root: Path) -> Path:
    """A synthetic working tree carrying the test gate the CLI demands."""
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n")
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    root = _make_tree(tmp_path / "tree")
    monkeypatch.chdir(root)
    return root


def _artifact(path: Path, body: str) -> Path:
    """A gate artifact with a Phase 1 section holding *body*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Gate — a scratch slice\n\n"
        "## Phase 0 — Context\n\nNothing here.\n\n"
        "## Phase 1 — Success criteria\n\n"
        f"{body}\n\n"
        "## Phase 2 — Devil's critique\n\nNothing here.\n"
    )
    return path


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    return _artifact(
        tmp_path / "outside" / "gate-artifact.md",
        "- **SC-1** WHEN a thing happens, THE SYSTEM SHALL do the other thing.\n"
        "- **SC-2** WHEN a second thing happens, THE SYSTEM SHALL refuse it.\n",
    )


def _contract(tree: Path, body: str) -> Path:
    directory = tree / "tests" / "contract" / "slice"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_contract.py").write_text(body)
    return directory


_BOTH_CLAIMED = (
    'def test_one():\n'
    '    """SC-1. The first claim."""\n'
    '    from absent_thing import answer\n'
    '    assert answer() == 42\n'
    '\n\n'
    'def test_two():\n'
    '    """SC-2. The second claim."""\n'
    '    assert True\n'
)

_ONE_CLAIMED = (
    'def test_one():\n'
    '    """SC-1. The only claim."""\n'
    '    from absent_thing import answer\n'
    '    assert answer() == 42\n'
    '\n\n'
    'def test_two():\n'
    '    """Nothing is claimed here."""\n'
    '    assert True\n'
)


# ============================================================
# SC-1 — a fully bound artifact traces clean
# ============================================================

def test_a_fully_bound_artifact_traces_clean():
    """SC-1. Every criterion claimed by at least one test is a complete trace.

    The happy path has to be cheap and silent, or the check becomes friction on
    every slice that was already doing the right thing.
    """
    from scripts.utils.sc_trace import read_claims, read_criteria, trace

    criteria = read_criteria(
        "## Phase 1 — Success criteria\n"
        "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
        "- **SC-2** WHEN c, THE SYSTEM SHALL d.\n"
        "## Phase 2\n")
    claims = read_claims({"test_contract.py": _BOTH_CLAIMED})
    result = trace(criteria, claims)

    assert result["unbound"] == []
    assert result["orphan"] == []
    assert set(result["bound"]) == {"SC-1", "SC-2"}


def test_the_criteria_are_read_in_the_order_the_artifact_states_them():
    """SC-1. Order is the operator's, not the parser's.

    A report that renumbers the operator's own criteria is a report he has to
    translate before he can act on it.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-3** third.\n- **SC-1** first.\n- **SC-2** second.\n"
            "## Phase 2\n")

    assert read_criteria(text) == ["SC-3", "SC-1", "SC-2"]


# ============================================================
# SC-2 — an unbound criterion is named, and refuses approve and freeze
# ============================================================

def test_a_criterion_no_test_claims_is_named_unbound():
    """SC-2. The whole point: absence is detected and the criterion is named."""
    from scripts.utils.sc_trace import read_claims, read_criteria, trace

    criteria = read_criteria(
        "## Phase 1 — Success criteria\n"
        "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
        "- **SC-2** WHEN c, THE SYSTEM SHALL d.\n"
        "## Phase 2\n")
    result = trace(criteria, read_claims({"test_contract.py": _ONE_CLAIMED}))

    assert result["unbound"] == ["SC-2"]


# ============================================================
# SC-3 — a claim on an undefined criterion is an orphan
# ============================================================

def test_a_claim_on_an_undefined_criterion_is_an_orphan():
    """SC-3. A typo in a docstring binds nothing and must not read as clean.

    `SC-9` claimed against an artifact defining SC-1 and SC-2 is the failure
    mode where the author believes the criterion is covered and it is not.
    """
    from scripts.utils.sc_trace import read_claims, read_criteria, trace

    criteria = read_criteria(
        "## Phase 1 — Success criteria\n- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
        "## Phase 2\n")
    body = ('def test_one():\n    """SC-1."""\n    assert True\n'
            '\n\ndef test_two():\n    """SC-9."""\n    assert True\n')
    result = trace(criteria, read_claims({"test_contract.py": body}))

    assert result["orphan"] == ["SC-9"]
    assert result["unbound"] == []


# ============================================================
# SC-4 — no contract means no trace, and nothing else changes
# ============================================================

# ============================================================
# SC-5 — an artifact defining no criterion is refused, never called clean
# ============================================================

# ============================================================
# SC-6 — the CLI reports one row per criterion
# ============================================================

def test_the_cli_prints_one_row_per_criterion(tree, anchor):
    """SC-6. The operator can read the binding without running the gate."""
    directory = _contract(tree, _BOTH_CLAIMED)

    proc = subprocess.run(
        [sys.executable, str(_TRACE), "--anchor", str(anchor),
         "--contract", str(directory)],
        capture_output=True, text=True, cwd=str(tree))

    assert proc.returncode == 0
    assert "SC-1" in proc.stdout
    assert "SC-2" in proc.stdout
    assert "test_contract.py" in proc.stdout


def test_the_cli_exits_nonzero_on_an_unbound_criterion(tree, anchor):
    """SC-6. The report and the gate agree, or one of them is decoration."""
    directory = _contract(tree, _ONE_CLAIMED)

    proc = subprocess.run(
        [sys.executable, str(_TRACE), "--anchor", str(anchor),
         "--contract", str(directory)],
        capture_output=True, text=True, cwd=str(tree))

    assert proc.returncode == 1
    assert "SC-2" in proc.stdout + proc.stderr


# ============================================================
# What a naive parser gets wrong — measured against the real artifacts
# ============================================================

def test_a_criterion_id_in_prose_outside_the_section_is_not_a_definition():
    """SC-1. Measured false positive: an archived artifact opens a line with
    `SC-1 to SC-7, from the spec, restated there.` outside Phase 1. A parser
    scanning the whole file reads that as defining SC-1 and SC-7.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("# Gate\n\n"
            "## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n\n"
            "## Phase 5 — Test contract\n"
            "SC-1 to SC-7, from the spec, restated there.\n")

    assert read_criteria(text) == ["SC-1"]


def test_a_criterion_id_inside_a_table_row_is_not_a_definition():
    """SC-1. Measured false positive: `| H1 | HIGH | SC-13 rewritten: ... |`
    inside a critique table. A definition opens its line; a mention does not.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
            "| H1 | HIGH | SC-13 rewritten: fall back to plaintext. |\n"
            "700 lines that had not fired. MUST FIX BEFORE: SC-6 is not covered.\n"
            "## Phase 2\n")

    assert read_criteria(text) == ["SC-1"]


def test_the_section_ends_at_the_next_heading_of_its_own_level():
    """SC-1. The scoping has an END, and nothing bound it until 2026-09-01.

    The two false-positive cases above both put the stray identifier on a line
    that does NOT open with an `SC-` token, so `CRITERION_LINE` refuses it on its
    own and the section boundary is never consulted. Deleting the boundary
    outright (`len(heading.group(1)) < 0`, so the loop never breaks) left every
    test in this file and its two neighbours green.

    A later phase that opens a line with a criterion id is the ordinary shape:
    a Phase 5 test-contract section restating `- **SC-2** ...` from the spec, or
    a Phase 2 critique listing `SC-3` as a bullet. Read as definitions, those
    become criteria the operator never stated and the trace refuses the slice
    for failing to test them.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
            "## Phase 5 — Test contract\n"
            "- **SC-2** restated from the spec, not a criterion of this gate.\n"
            "SC-3 [failure-mode]: also restated.\n")

    assert read_criteria(text) == ["SC-1"]


def test_a_deeper_heading_stays_inside_the_section():
    """SC-1. The paired case: the break is on level, not on any heading at all.

    A `###` subsection under a `##` criteria section is part of it, so a
    criterion stated below one is the operator's and must still be read.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
            "### Failure modes\n"
            "- **SC-2** WHEN c, THE SYSTEM SHALL refuse.\n"
            "## Phase 2\n"
            "- **SC-9** not a criterion.\n")

    assert read_criteria(text) == ["SC-1", "SC-2"]


def test_an_artifact_with_no_criteria_section_states_no_criteria():
    """SC-5. Zero criteria is the answer that makes `refusal` fire.

    `read_criteria` returning [] for a document with no success-criteria heading
    is what routes an unstructured artifact into the empty-criteria refusal
    rather than into a clean trace. Falling back to scanning the whole file
    instead (`start = 0`) survived a mutation run over this file and its two
    neighbours: the document below would then define SC-4 out of a critique
    table and report a trace over criteria nobody wrote.
    """
    from scripts.utils.sc_trace import read_criteria, refusal, trace

    text = ("# Gate — a slice with no criteria section\n\n"
            "## Phase 2 — Devil's critique\n"
            "- **SC-4** rewritten: fall back to plaintext.\n")

    assert read_criteria(text) == []
    assert "no success criteria" in refusal(trace(read_criteria(text), {}))


def test_a_criterion_stated_twice_is_reported_once():
    """SC-1. The operator's order is preserved; his duplicates are not.

    An artifact that restates `SC-1` inside its own criteria section (an edit
    that split a criterion and left both bullets) must not produce two rows for
    one criterion, and must not report the second copy as separately unbound.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
            "- **SC-2** WHEN c, THE SYSTEM SHALL d.\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b (restated).\n"
            "## Phase 2\n")

    assert read_criteria(text) == ["SC-1", "SC-2"]


def test_both_written_shapes_of_a_criterion_are_read():
    """SC-1. Two shapes exist in the real corpus and both are the operator's.

    `- **SC-1** ...` is what the last two artifacts use; `SC-1 [happy-path]: ...`
    is what the planning gate's own format block prescribes. A parser that reads
    only the one it was written against silently reports every artifact of the
    other shape as having no criteria at all.
    """
    from scripts.utils.sc_trace import read_criteria

    text = ("## Phase 1 — Success criteria\n"
            "- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
            "SC-2 [failure-mode]: WHEN c, THE SYSTEM SHALL refuse.\n"
            "## Phase 2\n")

    assert read_criteria(text) == ["SC-1", "SC-2"]


# ============================================================
# Totality — this check may never be the reason a slice cannot proceed
# ============================================================

def test_a_contract_file_that_cannot_be_parsed_does_not_refuse():
    """SC-4. Fail OPEN on an internal fault, and say so.

    This runs inside `scripts/sc-trace.py`, which reports which criterion each
    contract test claims to decide. A raise on one unparseable file would take
    the whole report down and leave the operator nothing, including the reading
    that would tell him which file to repair. A definite unbound criterion
    refuses; a parser that could not establish an answer reports and stands
    aside. The same reasoning as the retired commit-time depth gate being
    deliberately bypassable: process discipline is not a leak wall.

    (The callers named here were `approve` and `freeze` until 2026-08-07, and
    the repair was `/canopus back`. All three went with the freeze lifecycle.)
    """
    from scripts.utils.sc_trace import read_claims

    claims = read_claims({"broken.py": "def test_one(  :\n  syntax error\n"})

    assert claims == {}


def test_the_refusal_text_is_empty_when_the_trace_is_clean():
    """SC-1. The seam the gate calls returns a sentence or nothing at all."""
    from scripts.utils.sc_trace import read_claims, read_criteria, refusal, trace

    criteria = read_criteria(
        "## Phase 1 — Success criteria\n- **SC-1** WHEN a, THE SYSTEM SHALL b.\n"
        "- **SC-2** WHEN c, THE SYSTEM SHALL d.\n## Phase 2\n")
    result = trace(criteria, read_claims({"test_contract.py": _BOTH_CLAIMED}))

    assert refusal(result) == ""


def test_a_docstringless_test_claims_nothing_and_does_not_crash():
    """SC-4. Most tests in the workspace carry no docstring at all.

    `ast.get_docstring` returns None for them, and a reader that assumes a
    string raises inside the shared builder.
    """
    from scripts.utils.sc_trace import read_claims

    claims = read_claims({"test_contract.py":
                          "def test_one():\n    assert True\n"})

    assert claims == {}
