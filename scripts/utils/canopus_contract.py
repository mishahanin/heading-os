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
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Sequence
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


def parse_junit(xml_text: str) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """Turn a JUnit report into per-file counts and per-test outcomes.

    Only testcases carrying a `file` attribute are counted, and only when they
    represent a real collected item. run_contract asks for `junit_family=xunit1`
    precisely so the attribute is there; see its docstring for why the default
    family makes this function match nothing.

    A module that failed to import is skipped rather than counted, so it lands at
    zero and refusal_reasons names it with the authoring rule. That is the
    behaviour the zero-item refusal relies on, and it is enforced here rather than
    inferred from a missing attribute.

    A DOCTYPE is refused before parsing. `xml.etree.ElementTree` does not resolve
    external entities, but it does expand internal ones, which is the whole
    mechanism behind entity-expansion denial of service. pytest's JUnit writer
    never emits a DOCTYPE, so refusing one costs nothing and removes the entire
    class without adding defusedxml as a dependency. The input is a file this
    process just wrote in its own temporary directory, so this is defence in
    depth rather than a live threat.
    """
    if "<!DOCTYPE" in xml_text:
        raise ContractError(
            "the contract report carries a DOCTYPE, which pytest never writes; "
            "refusing to parse it"
        )
    counts: dict[str, int] = {}
    outcomes: list[tuple[str, str, str]] = []
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
    root = ElementTree.fromstring(xml_text)  # noqa: S314  # nosec B314
    for case in root.iter("testcase"):
        rel = case.get("file")
        if not rel or _is_collection_failure(case):
            continue
        rel = Path(rel).as_posix()
        counts[rel] = counts.get(rel, 0) + 1
        outcomes.append((rel, case.get("name") or "", _outcome(case)))
    return counts, outcomes


def run_contract(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
    """Run pytest over *paths* once and read the report.

    `-o addopts=` neutralises the repository's configured addopts (coverage,
    parallel workers) so the report is deterministic and cheap. CANOPUS_NO_ATTEST
    stops the child session writing an attestation over the real one: `probe` can
    legitimately run while a freeze is held.

    `-o junit_family=xunit1` is LOAD-BEARING, not a style choice. pytest defaults
    to `junit_family=xunit2`, whose schema permits only name, classname, time,
    assertions and status on a testcase, so `file` and `line` are filtered out.
    Measured on pytest 9.1.1: the default emits
    `<testcase classname="c.test_one" name="test_a" time="0.001">` with no `file`,
    so parse_junit below matches nothing, every count is zero, and `freeze
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
        ]
        env = dict(os.environ, CANOPUS_NO_ATTEST="1")
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
            return parse_junit(report.read_text(encoding="utf-8"))
        except (OSError, ElementTree.ParseError) as exc:
            raise ContractError(f"the contract report is unreadable: {exc}") from exc


def refusal_reasons(
    counts: dict[str, int],
    outcomes: Sequence[tuple[str, str, str]],
    expected: Sequence[str],
) -> list[str]:
    """Why this contract cannot be frozen. Empty means it can.

    Two conditions, and they do not overlap. A collection error yields zero items
    for its file, so it is caught by the first rather than needing its own rule.

    Redness is required of the SET, not of each test. A single honest case
    ("returns an empty list for empty input") can legitimately pass against a
    stub, and demanding redness everywhere is an incentive to write contorted
    tests for the indicator's sake.
    """
    reasons: list[str] = []
    for rel in expected:
        if counts.get(rel, 0) == 0:
            reasons.append(
                f"contract file collected nothing: {rel}. Import the code under "
                f"test inside the test body, not at module scope, so the file "
                f"collects before its implementation exists."
            )
    if not any(outcome in RED_OUTCOMES for _rel, _name, outcome in outcomes):
        reasons.append(
            "no contract test failed: a contract that is green before the code "
            "exists asserts nothing"
        )
    return reasons
