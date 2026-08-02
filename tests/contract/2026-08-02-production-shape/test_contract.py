"""Frozen contract for the production-shape slice.

Gives teeth to the fifth planning-gate rule: a fixture must produce the shape the
real source produces. Today that rule is prose the author must remember. This
makes the gate refuse when the code under test reads a record store and no test
in the contract builds its fixtures by calling that store's WRITER.

The witness is the writer, not the live file. A test that reads live mutable
state is a bad test and this workspace deliberately does not write them; a
fixture minted by the real writer carries the real shape by construction, and
stays hermetic.

Every test imports the code under test INSIDE its body: the implementation does
not exist when this contract is frozen.

Each docstring OPENS with the criterion it claims, per scripts/sc-trace.py.

Measured 2026-08-02, and this is the whole reason the slice exists: at a2cb7d1^
the gate-yield contract held 23 tests, its code read the denial store, it called
the real writer ZERO times, and it hand-authored `"ts": "2026-08-02T00:00:00+00:00"`
while the writer emits a time.time() float. It shipped useless for half its
mechanisms and a 23-test frozen contract said nothing.
"""

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

HISTORICAL_PARENT = "a2cb7d1^"
HISTORICAL_CONTRACT = "tests/contract/2026-08-02-gate-yield/test_contract.py"
HISTORICAL_MODULE = "scripts/utils/gate_yield.py"

DENIAL_LOG = (
    "from pathlib import Path\n\n\n"
    "def denial_log_path() -> Path:\n"
    "    return Path('.logs/denials/denials.jsonl')\n\n\n"
    "def log_denial(*, mechanism, action, path=None, reason=''):\n"
    "    return True\n\n\n"
    "def read_denials(path=None):\n    return []\n"
)
READS_STORE = (
    "from scripts.utils.denial_log import read_denials\n\n\n"
    "def summarise():\n    return read_denials()\n"
)
HAND_AUTHORED = (
    "def test_it():\n"
    "    from scripts.utils.under_test import summarise\n\n"
    "    records = [{'ts': '2026-08-02T00:00:00+00:00', 'mechanism': 'x'}]\n"
    "    assert summarise() is not None and records\n"
)
WRITER_MINTED = (
    "def test_it():\n"
    "    from scripts.utils.denial_log import log_denial\n"
    "    from scripts.utils.under_test import summarise\n\n"
    "    log_denial(mechanism='x', action='commit', reason='r')\n"
    "    assert summarise() is not None\n"
)


def _tree(tmp_path, *, contract_body, module_body):
    """A scratch root shaped like the engine. Its own root, never the engine's."""
    (tmp_path / "tests" / "contract" / "slice").mkdir(parents=True)
    (tmp_path / "scripts" / "utils").mkdir(parents=True)
    (tmp_path / "tests" / "contract" / "slice" / "test_contract.py").write_text(
        contract_body, encoding="utf-8"
    )
    (tmp_path / "scripts" / "utils" / "under_test.py").write_text(
        module_body, encoding="utf-8"
    )
    (tmp_path / "scripts" / "utils" / "denial_log.py").write_text(
        DENIAL_LOG, encoding="utf-8"
    )
    return tmp_path / "tests" / "contract" / "slice"


# ---------------------------------------------------------------------------
# SC-1 - a contract whose fixtures come from the real writer passes
# ---------------------------------------------------------------------------


def test_a_contract_that_mints_fixtures_from_the_writer_is_not_refused(tmp_path):
    """SC-1. The check must be silent when the discipline was followed, or it
    becomes noise every slice learns to route around."""
    from scripts.utils.production_shape import shape_refusal

    slice_dir = _tree(tmp_path, contract_body=WRITER_MINTED, module_body=READS_STORE)

    assert shape_refusal([slice_dir], tmp_path) == ""


# ---------------------------------------------------------------------------
# SC-2 - a hand-authored contract is refused, and the store is named
# ---------------------------------------------------------------------------


def test_a_contract_that_hand_authors_the_record_is_refused(tmp_path):
    """SC-2. This is the defect: the code reads a store, every fixture is
    invented, and nothing ever compares the invention to the writer."""
    from scripts.utils.production_shape import shape_refusal

    slice_dir = _tree(tmp_path, contract_body=HAND_AUTHORED, module_body=READS_STORE)
    out = shape_refusal([slice_dir], tmp_path)

    # Asserts on CONTENT, not on emptiness: `!= ""` passes against a null stub
    # and proves nothing, which the vacuity probe caught on the first draft.
    assert "denial_log" in out


def test_the_refusal_names_the_store_and_the_writer_to_call(tmp_path):
    """SC-2. A refusal that does not say which writer to call leaves the author
    guessing, which is how a gate becomes something to disable."""
    from scripts.utils.production_shape import shape_refusal

    slice_dir = _tree(tmp_path, contract_body=HAND_AUTHORED, module_body=READS_STORE)
    out = shape_refusal([slice_dir], tmp_path)

    assert "log_denial" in out


# ---------------------------------------------------------------------------
# SC-3 - a module the contract names but that does not exist yet
# ---------------------------------------------------------------------------


def test_a_module_that_does_not_exist_yet_does_not_abort_the_closure(tmp_path):
    """SC-3. At freeze time the code under test is absent by construction, so
    the walk must step over the hole and keep going."""
    from scripts.utils.production_shape import first_party_closure

    _tree(tmp_path, contract_body=HAND_AUTHORED, module_body=READS_STORE)
    out = first_party_closure(
        ["scripts/utils/under_test.py", "scripts/utils/absent.py"], tmp_path
    )

    assert "scripts/utils/denial_log.py" in out


# ---------------------------------------------------------------------------
# SC-4 - the package form of an import must not escape the closure
# ---------------------------------------------------------------------------


def test_the_closure_follows_a_from_package_import_of_a_module(tmp_path):
    """SC-4. `from scripts.utils import denial_log` names a package, not a
    file; following only the module string is the exact escape the enforcer-set
    guard already had to learn about."""
    from scripts.utils.production_shape import first_party_closure

    _tree(
        tmp_path,
        contract_body=HAND_AUTHORED,
        module_body="from scripts.utils import denial_log as _d\n",
    )
    out = first_party_closure(["scripts/utils/under_test.py"], tmp_path)

    assert "scripts/utils/denial_log.py" in out


# ---------------------------------------------------------------------------
# SC-5 - the registry is explicit and maps a store to its writer
# ---------------------------------------------------------------------------


def test_the_registry_maps_a_store_module_to_its_writer(tmp_path):
    """SC-5. A store absent from the registry is unguarded; the registry being
    one enumerated place is what turns that hole into a fixable one."""
    from scripts.utils.production_shape import RECORD_STORES

    assert isinstance(RECORD_STORES, dict)
    assert RECORD_STORES.get("scripts/utils/denial_log.py") == "log_denial"


# ---------------------------------------------------------------------------
# SC-6 - totality: an internal fault refuses nothing
# ---------------------------------------------------------------------------


def test_an_absent_contract_directory_refuses_nothing(tmp_path):
    """SC-6. Same shape as the sibling gates: a check that breaks must not
    become a wall no slice can pass."""
    from scripts.utils.production_shape import shape_refusal

    assert shape_refusal([tmp_path / "does-not-exist"], tmp_path) == ""


def test_an_unparseable_contract_file_refuses_nothing(tmp_path):
    """SC-6. Unparseable input is an internal fault, not evidence of a
    violation, and reporting it as one would be a false accusation."""
    from scripts.utils.production_shape import shape_refusal

    slice_dir = _tree(
        tmp_path, contract_body="def test_it(:\n    pass\n", module_body=READS_STORE
    )

    assert shape_refusal([slice_dir], tmp_path) == ""


# ---------------------------------------------------------------------------
# SC-7 / SC-8 - the measured A/B against the real past defect
# ---------------------------------------------------------------------------


def _blob(rev_path: str) -> str:
    out = subprocess.run(
        ["git", "show", rev_path], cwd=_ROOT,
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def test_the_historical_blind_contract_is_refused(tmp_path):
    """SC-7. Arm one of the A/B: at a2cb7d1^ the gate-yield contract read the
    denial store, called its writer zero times, and hand-authored an ISO stamp
    the writer has never emitted."""
    from scripts.utils.production_shape import shape_refusal

    (tmp_path / "tests" / "contract" / "gate-yield").mkdir(parents=True)
    (tmp_path / "scripts" / "utils").mkdir(parents=True)
    (tmp_path / "tests" / "contract" / "gate-yield" / "test_contract.py").write_text(
        _blob(f"{HISTORICAL_PARENT}:{HISTORICAL_CONTRACT}"), encoding="utf-8"
    )
    (tmp_path / "scripts" / "utils" / "gate_yield.py").write_text(
        _blob(f"{HISTORICAL_PARENT}:{HISTORICAL_MODULE}"), encoding="utf-8"
    )
    (tmp_path / "scripts" / "utils" / "denial_log.py").write_text(
        _blob(f"{HISTORICAL_PARENT}:scripts/utils/denial_log.py"), encoding="utf-8"
    )

    verdict = shape_refusal(
        [tmp_path / "tests" / "contract" / "gate-yield"], tmp_path
    )

    # Content, not emptiness: the refusal must name the writer the contract
    # never called, or it has not identified the defect it claims to catch.
    assert "log_denial" in verdict


def test_a_suite_that_mints_from_the_writer_is_not_refused():
    """SC-8. Arm two, plus the case that decides whether the check is worth
    anything. tests/test_denial_counter.py calls log_denial 19 times, so the
    discipline is followable and firing on it would be a false accusation.

    The third assertion is the discriminating one, and it is the blob that
    misled the author of this contract: tests/test_gate_yield.py names
    log_denial in two docstrings and calls it never. A substring check answers
    True there and hands the gate an escape through a comment.
    """
    from scripts.utils.production_shape import calls_writer

    assert calls_writer(_blob("637a634:tests/test_denial_counter.py"), "log_denial") is True
    assert calls_writer(
        _blob(f"{HISTORICAL_PARENT}:{HISTORICAL_CONTRACT}"), "log_denial"
    ) is False
    assert calls_writer(_blob("637a634:tests/test_gate_yield.py"), "log_denial") is False
