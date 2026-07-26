#!/usr/bin/env python3
"""Run a Canopus contract test set and read its shape from a JUnit report.

Separate from scripts/utils/canopus_freeze.py, and the separation is the point:
that module is imported by the PreToolUse dispatcher on every Write/Edit and is
stdlib-only with no subprocess. This one runs pytest, so it can never be
imported from there.

Two questions are answered here, both by running the contract once before it is
frozen:

  * How many items does each contract file yield when collected whole? That
    number becomes the manifest baseline, and it is what closes the node-id
    subset hole: `pytest file::test_one` then reports 1 against 7.
  * Is the contract red? A test that is green before the implementation exists
    asserts nothing, and freezing it would cement a contract that cannot fail.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Optional, Sequence
from xml.etree import ElementTree

DEFAULT_PATTERNS = ("test_*.py",)
RED_OUTCOMES = ("failure", "error")


class ContractError(Exception):
    """The contract could not be run at all."""


def contract_files(
    paths: Sequence[Path],
    root: Path,
    patterns: Sequence[str] = DEFAULT_PATTERNS,
) -> list[str]:
    """Every test module under *paths*, as sorted root-relative POSIX strings.

    Symlinks are excluded, matching the freeze primitive: the workspace forbids
    them and a symlinked contract file could point outside the tree.

    The default pattern is hardcoded rather than read from pytest's `python_files`
    because this runs CLI-side, before a pytest config object exists. The two must
    agree: frozen_test_files() on the attestation side reads `python_files`, so a
    repository that renamed the convention would record a baseline keyed on files
    the recorder never tallies. The engine pins `python_files = ["test_*.py"]` in
    pyproject.toml, so they agree today; *patterns* is the override if that ever
    stops being true.
    """
    resolved_root = Path(root).resolve()
    found: set[str] = set()
    for raw in paths:
        target = Path(raw)
        candidates = sorted(target.rglob("*")) if target.is_dir() else [target]
        for candidate in candidates:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if not any(fnmatch(candidate.name, pattern) for pattern in patterns):
                continue
            found.add(candidate.resolve().relative_to(resolved_root).as_posix())
    return sorted(found)


def _outcome(case: ElementTree.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return "failure" if tag == "failure" else tag
    return "passed"


def _is_collection_failure(case: ElementTree.Element) -> bool:
    """True when this entry stands for a module that never collected at all.

    Under xunit1 a collection error is written as a testcase carrying the FILE
    attribute of the module that failed to import. Counted naively it becomes one
    collected item with a red outcome, which satisfies BOTH refusal conditions at
    once and would freeze a baseline of 1 for a file that yields nothing: the
    exact fail-open the zero-item rule exists to prevent. pytest tags it
    `message="collection failure"`; a genuine setup or call error never carries
    that string (it reads `failed on setup with "..."`), so the two are
    distinguishable without inspecting the traceback text.
    """
    error = case.find("error")
    return error is not None and error.get("message") == "collection failure"


def _parse_report(xml_text: str) -> ElementTree.Element:
    """The one XML entry point: refuse a DOCTYPE, wrap a parse failure.

    Every reader of a contract report parses it through here, and separate copies
    of this guard are how one of them ends up without it. A DOCTYPE is refused
    before parsing because ElementTree expands internal entities, which is the
    whole billion-laughs mechanism; pytest never writes one, so refusing costs
    nothing and removes the class without adding defusedxml as a dependency.
    """
    if "<!DOCTYPE" in xml_text:
        raise ContractError(
            "the contract report carries a DOCTYPE, which pytest never writes; "
            "refusing to parse it"
        )
    # Two suppressions, justified rather than waved through, and both flagged to
    # the operator when this landed. Ruff S314 and bandit B314 are the same
    # finding: stdlib XML on UNTRUSTED input. Every attack they stand for against
    # ElementTree needs a DOCTYPE -- external-entity resolution, billion laughs,
    # quadratic blowup all declare entities -- and the guard above refuses a
    # DOCTYPE before any parsing happens, with a test pinning the refusal.
    # ElementTree additionally never resolves external entities at all. The input
    # is a report this process just wrote in its own temporary directory.
    # The alternative, defusedxml, is a new runtime dependency, which is a
    # stop-and-flag decision rather than something a lint fix makes quietly.
    try:
        return ElementTree.fromstring(xml_text)  # noqa: S314  # nosec B314
    except ElementTree.ParseError as exc:
        raise ContractError(f"the contract report is unreadable: {exc}") from exc


def parse_junit(xml_text: str) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """Turn a JUnit report into per-file counts and per-test outcomes.

    Only testcases carrying a `file` attribute are counted, and only when they
    represent a real collected item. run_pytest_report asks for
    `junit_family=xunit1` precisely so the attribute is there; see its docstring
    below for why the default family makes this function match nothing.

    A module that failed to import is skipped rather than counted, so it lands at
    zero and refusal_reasons names it with the authoring rule. That is the
    behaviour the zero-item refusal relies on, and it is enforced here rather than
    inferred from a missing attribute.

    A DOCTYPE is refused before parsing, in the shared `_parse_report` entry
    point above, and the reasoning lives there.
    """
    counts: dict[str, int] = {}
    outcomes: list[tuple[str, str, str]] = []
    root = _parse_report(xml_text)
    for case in root.iter("testcase"):
        rel = case.get("file")
        if not rel or _is_collection_failure(case):
            continue
        rel = Path(rel).as_posix()
        counts[rel] = counts.get(rel, 0) + 1
        outcomes.append((rel, case.get("name") or "", _outcome(case)))
    return counts, outcomes


def run_pytest_report(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
    extra_env: Optional[dict] = None,
    extra_args: Sequence[str] = (),
) -> str:
    """Run pytest over *paths* once and return the raw JUnit XML.

    Extracted from run_contract so the null-stub probe can run the same command
    with two extra arguments instead of duplicating the flag set. Every flag here
    is load-bearing, and each is explained below.

    extra_env is merged over os.environ rather than replacing it, so the trace id
    a daemon exported still reaches the child (.claude/rules/trace-id.md).

    `-o addopts=` neutralises the repository's configured addopts (coverage,
    parallel workers) so the report is deterministic and cheap. CANOPUS_NO_ATTEST
    stops the child session writing an attestation over the real one: `probe` can
    legitimately run while a freeze is held.

    `-o junit_family=xunit1` is LOAD-BEARING, not a style choice. pytest defaults
    to `junit_family=xunit2`, whose schema permits only name, classname, time,
    assertions and status on a testcase, so `file` and `line` are filtered out.
    Measured on pytest 9.1.1: the default emits
    `<testcase classname="c.test_one" name="test_a" time="0.001">` with no `file`,
    so parse_junit above matches nothing, every count is zero, and `freeze
    --contract` refuses a contract that is perfectly well formed. xunit1 restores
    `file="c/test_one.py"`. Deriving the path from the dotted `classname` instead
    was rejected: it cannot round-trip a directory containing a dot, and it is
    empty on exactly the collection-error entry that has to be told apart.

    `--continue-on-collection-errors` is load-bearing for the same reason. Without
    it pytest ABORTS the whole session on the first module that fails to import
    (exit 2), so one broken contract file leaves every sibling unmeasured and
    refusal_reasons blames all of them for collecting nothing. The plan's authoring
    rule already forbids module-scope imports, but the diagnostic a builder reads
    when they break it should name the one file that broke, not the whole set.

    The return code is deliberately ignored. A contract that has not been
    implemented yet EXITS NONZERO, and that is the state this function exists to
    observe.
    """
    resolved_root = Path(root).resolve()
    rels = [str(Path(p).resolve()) for p in paths]
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "contract.xml"
        command = [
            sys.executable, "-m", "pytest", *rels,
            "--junit-xml", str(report),
            "-o", "addopts=",
            "-o", "junit_family=xunit1",
            "--continue-on-collection-errors",
            "-p", "no:cacheprovider",
            "-q",
            *extra_args,
        ]
        # PYTHONDONTWRITEBYTECODE is load-bearing, not tidiness. pytest's
        # assertion rewriter caches a .pyc for every test module it imports, so a
        # plain run drops a __pycache__ directory INSIDE the contract tree. That
        # tree is frozen recursively, and a directory that appeared after the
        # freeze reads as tampering to the very lock this tool installs. The
        # measured symptom was `['__pycache__', 'test_one.py']` where only
        # test_one.py had been written.
        env = dict(
            os.environ, CANOPUS_NO_ATTEST="1", PYTHONDONTWRITEBYTECODE="1",
        )
        if extra_env:
            env.update(extra_env)
        try:
            proc = subprocess.run(
                command, cwd=str(resolved_root), capture_output=True, text=True,
                timeout=timeout, env=env, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContractError(f"the contract could not be run: {exc}") from exc
        if not report.is_file():
            # The child's own words, or this is a diagnosis tool that refuses to
            # diagnose. Measured: `probe` is documented as runnable while a freeze
            # is HELD, and if that freeze has moved, tests/conftest.py raises
            # pytest.UsageError at session start, no report is written at all,
            # and a bare "pytest wrote no JUnit report" hides LOSS OF LOCK behind
            # a message about file plumbing.
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = "; ".join(detail[-3:]) if detail else "no output"
            raise ContractError(
                f"pytest wrote no JUnit report, so the contract could not be "
                f"measured (exit {proc.returncode}): {tail}"
            )
        try:
            return report.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContractError(f"the contract report is unreadable: {exc}") from exc


def run_contract(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """Run the contract once and read the report. See run_pytest_report."""
    return parse_junit(run_pytest_report(paths, root, timeout=timeout))


def refusal_reasons(
    counts: dict[str, int],
    outcomes: Sequence[tuple[str, str, str]],
    expected: Sequence[str],
    *,
    green_ok: bool = False,
) -> list[str]:
    """Why this contract cannot be frozen. Empty means it can.

    Two conditions, and they do not overlap. A collection error yields zero items
    for its file, so it is caught by the first rather than needing its own rule.

    Redness is required of the SET, not of each test. A single honest case
    ("returns an empty list for empty input") can legitimately pass against a
    stub, and demanding redness everywhere is an incentive to write contorted
    tests for the indicator's sake.

    `green_ok` waives the redness condition and NOTHING else. It exists for the
    one state the rule is wrong about: a RETAKE of a freeze whose contract has
    already been implemented and is now green by the slice's own work. Refusing
    there is what pushed the previous retake into passing the contract directory
    POSITIONALLY, which silently gave up the baseline, and with it the
    attestation's per-file subset check, the collected-nothing refusal, the
    vacuity re-proof, and the ledger's already-green note. A named waiver that
    keeps every other protection is strictly better than a workaround that
    drops them all.

    It is a PARAMETER rather than a filter applied to the returned list. The
    caller that filtered by string would silently start waiving any future
    reason whose wording happened to match, and it could not tell a waived
    reason from a reason that never fired. Here the suppression is at the one
    site that produces it, and the per-file zero-item refusals below are
    untouched by construction rather than by careful matching.
    """
    reasons: list[str] = []
    for rel in expected:
        if counts.get(rel, 0) == 0:
            reasons.append(
                f"contract file collected nothing: {rel}. Import the code under "
                f"test inside the test body, not at module scope, so the file "
                f"collects before its implementation exists."
            )
    if not green_ok and not any(
        outcome in RED_OUTCOMES for _rel, _name, outcome in outcomes
    ):
        reasons.append(
            "no contract test failed: a contract that is green before the code "
            "exists asserts nothing"
        )
    return reasons


_MISSING_PATTERN = re.compile(r"No module named ['\"]([A-Za-z_][A-Za-z0-9_.]*)")
# The other half of "the code under test is not there yet". A module file that
# exists but does not yet carry the name the contract imports raises
# `ImportError: cannot import name 'x' from 'y'`, which the pattern above does not
# match at all. Without this the probe returns an empty name set for every
# partially built module, run_null_stub does nothing, and vacuity_refusal can
# never fire in exactly the mid-build state where a retake is taken.
_MISSING_NAME_PATTERN = re.compile(
    r"cannot import name ['\"][A-Za-z_][A-Za-z0-9_]*['\"] from "
    r"['\"]([A-Za-z_][A-Za-z0-9_.]*)"
)


def missing_modules(xml_text: str) -> set[str]:
    """FULL dotted module names the contract run could not import.

    Read out of the failure messages the child itself produced, rather than
    resolved in this process: the child's sys.path is the one that matters, and
    it is not necessarily ours.

    The dot belongs in the character class. Truncating at the first segment turns
    one absent `scripts.utils.canopus_git` into a stub over the entire `scripts`
    package, so modules that exist are mocked away, every test passes, and a good
    contract is refused as vacuous.

    The text read here is text the contract's own test code can shape, and the
    two directions are NOT symmetrical. Be exact about both, because an earlier
    revision of this docstring claimed "the direction is fail-closed" without
    qualification and that was true of only one of them.

    WIDENING is the safe one. A test that merely mentions the literal string
    `No module named 'scripts'` gets that package stubbed for the probe run; a
    wider stub can only turn a passing probe test into a vacuity label, never
    hide one. The names reach the child through an environment variable only,
    never through argv.

    NARROWING is author-controlled and FAIL-OPEN, and it costs one keyword:

        def test_vacuous():
            try:
                from absent_thing import answer
            except ImportError:
                raise AssertionError('not implemented yet') from None
            assert answer() is not None

    `from None` suppresses the chained traceback, so the child's failure text
    never carries `No module named 'absent_thing'`, this function returns an
    empty set, `run_null_stub` returns immediately with nothing stubbed, and
    `vacuity_refusal` cannot fire over a contract every test of which asserts
    nothing. Measured: freeze exits 0 and writes a manifest. Without `from None`
    the chained ModuleNotFoundError leaks into the report and the refusal fires
    correctly.

    Nothing here closes that, and pretending otherwise is worse than naming it:
    the instrument reads the child's own words, and the contract author writes
    the child. What the callers DO about it is refuse to stay silent: see
    `vacuity_unmeasured` below, which the operator-facing commands print when a
    red contract names no absent module at all, so a probe that measured
    nothing never reads like a probe that measured vacuity and found none.
    """
    root = _parse_report(xml_text)
    found: set[str] = set()
    for case in root.iter("testcase"):
        for child in case:
            message = child.get("message") or ""
            text = child.text or ""
            for blob in (message, text):
                found.update(_MISSING_PATTERN.findall(blob))
                found.update(_MISSING_NAME_PATTERN.findall(blob))
    return found


def run_null_stub(
    paths: Sequence[Path],
    root: Path,
    modules: Iterable[str],
    *,
    timeout: int = 900,
) -> set[tuple[str, str]]:
    """The (file, test) pairs that PASS with every absent module mocked.

    Each one is proved to assert nothing. Returns an empty set when there is
    nothing to stub, which is the ordinary state of a mid-build retake where the
    implementation already imports.
    """
    names = sorted(set(modules))
    if not names:
        return set()
    engine_root = str(Path(__file__).resolve().parent.parent.parent)
    env = {
        "CANOPUS_STUB_MODULES": ",".join(names),
        "PYTHONPATH": os.pathsep.join(
            [engine_root, os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }
    xml_text = run_pytest_report(
        paths, root, timeout=timeout, extra_env=env,
        extra_args=("-p", "scripts.utils.canopus_nullstub"),
    )
    _counts, outcomes = parse_junit(xml_text)
    return {
        (rel, name) for rel, name, outcome in outcomes if outcome == "passed"
    }


def vacuity_unmeasured(
    outcomes: Sequence[tuple[str, str, str]], modules: Iterable[str]
) -> str:
    """One sentence when the vacuity instrument did not run, or "" when it did.

    A red contract that names NO absent module leaves `run_null_stub` with
    nothing to stub, so it returns an empty set, `vacuity_refusal` finds no
    vacuous test, and the freeze proceeds. Two very different worlds reach that
    same silence: a contract genuinely failing on assertions against code that
    already exists, and a contract that hid its absent module from the report
    (see `missing_modules` above, `from None`). This function does not tell them
    apart, and it does not try.

    It is deliberately NOT a refusal. Tests failing on assertions against
    existing code are a legitimate, ordinary contract, and refusing there would
    make the tool something builders route around. What it removes is the
    silence: a measurement that did not happen is reported as one, rather than
    read as a measurement that came back clean.
    """
    if not any(outcome in RED_OUTCOMES for _rel, _name, outcome in outcomes):
        return ""
    if sorted(set(modules)):
        return ""
    return (
        "vacuity was NOT measured: the contract is red but its report names no "
        "absent module, so no mock could stand in for one and no test could be "
        "proved to assert nothing. That is not the same as measuring vacuity "
        "and finding none. Ordinary when the contract fails on assertions "
        "against code that already exists; also what a suppressed exception "
        "chain (`raise ... from None` around the import) looks like."
    )


_IMPORT_MARKERS = ("ModuleNotFoundError", "ImportError")


def parse_failure_modes(xml_text: str) -> dict[tuple[str, str], str]:
    """How each failing test failed: "import", "assertion", or "other".

    A heuristic over the failure message, and labelled as one wherever it is
    printed. It never feeds a refusal; it answers the question an operator asks
    first, which is whether anything failed for a reason other than the code
    being absent.

    The report is read through the shared `_parse_report` entry point, like every
    other reader here. A second `ElementTree.fromstring` in this function would
    parse the same text without the DOCTYPE refusal, which is precisely the
    silent way the class that guard removes comes back.

    LAST CHILD WINS, and that is documented rather than fixed. A testcase can
    carry both a `failure` and an `error` child (a call that failed inside a
    fixture that then errored on teardown), and the loop below overwrites, so
    the label describes whichever child pytest wrote last. Fixing it means
    picking a precedence, and there is no principled one: the call failure and
    the teardown error are both true of that test. Since this value feeds no
    refusal and no manifest, and is printed beside the word "heuristic", an
    arbitrary precedence would buy the appearance of precision and nothing else.
    Anyone who later makes this label decide something must resolve the tie
    first.
    """
    root = _parse_report(xml_text)
    modes: dict[tuple[str, str], str] = {}
    for case in root.iter("testcase"):
        rel = case.get("file")
        name = case.get("name")
        if not rel or not name:
            continue
        for child in case:
            if child.tag not in ("failure", "error"):
                continue
            message = child.get("message") or ""
            blob = f"{message}\n{child.text or ''}"
            if any(marker in blob for marker in _IMPORT_MARKERS):
                modes[(Path(rel).as_posix(), name)] = "import"
            elif "AssertionError" in message or message.lstrip().startswith("assert"):
                # The MESSAGE, never the body. The body carries the test's source
                # and its docstring, so the bare word "assert" anywhere in the
                # prose labelled the failure an assertion. Measured on wire 2.2's
                # own contract at its Fix 1 probe: eleven tests failing on one
                # identical TypeError printed as seven assertions and four others,
                # decided entirely by which docstrings happened to use the word.
                # The import branch still reads the whole blob, because its
                # markers are specific sentences rather than a common English
                # verb.
                modes[(Path(rel).as_posix(), name)] = "assertion"
            else:
                modes[(Path(rel).as_posix(), name)] = "other"
    return modes


def vacuity_refusal(
    outcomes: Sequence[tuple[str, str, str]],
    vacuous: set[tuple[str, str]],
) -> list[str]:
    """The one refusal the null-stub probe raises: every RED test is vacuous.

    Partial vacuity is printed by name and not refused, because "these three
    tests assert nothing" is a decision for a human. A test that legitimately
    asserts absence lands on that list, and striking it off by eye is cheap;
    teaching the probe to tell the two apart is not.

    Only tests that were RED in the real run are weighed, and the filter is about
    EVIDENCE rather than leniency. The stub proves a test vacuous by making its
    absent import succeed; a test that PASSED for real never had a failing import
    to fix, so its pass under the stub has another explanation and the probe
    learned nothing from it. It is worth being exact about the direction: a green
    test almost always passes under the stub too, so dropping it out of `cases`
    usually changes no answer at all. It changes one, and that one is why the
    filter is here. A test that asserts the code is still ABSENT passes for real
    and FAILS under the stub, and counting it would leave `cases` outside
    `vacuous` and wave through a contract whose every red test asserts nothing.
    Redness is what the freeze gate demands of the SET, so redness is what this
    refusal audits.

    The emptiness guard is load-bearing for the neighbouring reason: with no red
    test at all the subset holds vacuously, and an all-green contract would be
    refused here with a sentence about mocks that never ran, instead of by
    `refusal_reasons`, which owns that case and says why.

    The membership test is `outcome in RED_OUTCOMES`, and the near-miss
    `outcome != "passed"` is a fail-open the tool shipped with. `_outcome`
    emits four tokens, not two: failure, error, skipped, passed. A skipped test
    is never in `vacuous`, because `vacuous` is built from what PASSED under the
    stub, so one `pytest.skip` anywhere in a wholly vacuous contract put a
    member in `cases` that could never be in `vacuous`, the subset failed, and
    the refusal went silent. Measured before the fix: the same contract froze at
    exit 0 with a manifest written once one skipped test was added, and an
    `xfail` did it too, because xunit1 records an expected failure as skipped.
    The contract author is the adversary here, so a one-line escape hatch is the
    whole finding.
    """
    cases = {
        (rel, name) for rel, name, outcome in outcomes if outcome in RED_OUTCOMES
    }
    if cases and cases <= vacuous:
        return [
            "every contract test that is red passes with the code under test "
            "mocked away, so the contract's redness asserts nothing: it measures "
            "that the code is absent, not that the tests check anything"
        ]
    return []
