#!/usr/bin/env python3
"""Run a Canopus contract test set and read its shape from a JUnit report.

This module runs pytest, so nothing loaded on a PreToolUse path may import it.
That was the reason it was split out of the retired freeze primitive, and the
constraint outlives the split.

Two questions are answered here, both by running the contract once:

  * How many items does each contract file yield when collected whole? That
    number is what closes the node-id subset hole: `pytest file::test_one`
    reports 1 against 7.
  * Is the contract red? A test that is green before the implementation exists
    asserts nothing, and approving it would cement a contract that cannot fail.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional, Sequence
from xml.etree import ElementTree

# The child's half of the handshake, imported rather than spelled again. Two
# copies of an environment-variable name is a rename on one side away from a
# child that claims nothing, two runs that agree on a red the rule never fires
# over, and a suite that stays green while the verdict is silently always empty.
# Importing the plugin module runs no pytest hook: hooks are registered by
# `-p`, not by import, and this module is stdlib-only.
from scripts.utils.canopus_nullstub import (
    CANDIDATE_VAR,
    CANDIDATES,
    GREEDY_MARKER,
    GREEDY_PAYLOAD_VAR,
    MODULES_VAR,
    NULLSTUB_STDERR_MARKER,
    STUB_NAME_SEPARATOR,
    VALUES_VAR,
    _expand_claims,
    greedy_payload,
)
# `venv_python` ONLY, never `ensure_venv`. The first is a pure path computation;
# the second re-execs the running process, and this module is imported by the
# CLI, by the gate's callers and by the suite. An import that could re-exec is a
# module that cannot be imported from a test.
from scripts.utils.venv import interpreter_identity, venv_python

DEFAULT_PATTERNS = ("test_*.py",)
RED_OUTCOMES = ("failure", "error")
# The outcomes under a stub run that do NOT prove a test read the stubbed value.
# `_outcome` emits exactly four tokens, so the complement of this set is the
# single token "failure": a test is proved to assert something only by FAILING
# under the stub, and passing, skipping or erroring all leave it unproved. Named
# here rather than spelled inline in `run_null_stub` because the operator-facing
# documents state this direction in prose, and
# `tests/test_canopus_steps.py::test_the_documents_state_the_vacuity_direction_the_code_implements`
# holds them to THIS tuple. Two definitions of the rule is how the prose inverted
# itself against the code once already.
UNPROVED_OUTCOMES = ("passed", "skipped", "error")


def pytest_child_env(**overrides: str) -> dict:
    """The environment for a pytest child this codebase launches: ours, minus PYTEST_.

    Blanket prefix, never a denylist. PYTEST_ADDOPTS alone can load a plugin that
    overrides pytest_pyfunc_call and makes every contract test report passed
    without executing, and naming the variables you thought of leaves whichever
    one you did not. The same shape as `canopus_check.git_child_env`, which does
    this for GIT_.

    ONE definition, because the two children it serves are COMPARED against each
    other: `scripts/canopus_check.py` launches the per-file evidence run and this
    module launches the contract run, and the check reads the first against the
    contract the second measured. While only one child was scrubbed, the reading
    was a photograph of the operator's shell. Measured on a scratch tree: a clean
    shell captured

        ['dist:_pytest', 'dist:anyio', 'dist:pytest_asyncio', 'dist:pytest_cov',
         'dist:xdist']

    and the same run with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 exported captured
    ['dist:_pytest'] alone.

    It lives HERE, in the module that launches the contract child, rather than in
    a module of its own: its previous home was the freeze gate, and that gate was
    deleted with the rest of the lifecycle on 2026-08-07. A 14-line module named
    after a gate that no longer exists is worse than the function sitting beside
    its in-tree consumer.

    The CANOPUS_ names are deliberately NOT scrubbed here. CANOPUS_NO_ATTEST is
    how a caller tells a child what it is for, and it is passed in as an
    *override* by the callers that need it.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_")}
    env.update(overrides)
    return env


def contract_interpreter() -> Path:
    """The interpreter every Canopus child is launched with.

    The project venv when it exists on disk, and the invoking interpreter
    otherwise. One function rather than a `sys.executable` at each launch site,
    because a second spelling of this rule would disagree with the first
    SILENTLY: both return a path that runs pytest, and the difference only
    surfaces as a plugin set nothing can match.

    Measured 2026-08-04, at full cost. `freeze` captures its plugin baseline from
    a pytest CHILD, and that child used to inherit `sys.executable`. Invoked as
    bare `python` rather than `.venv/bin/python`, on a machine where the two are
    different interpreters, the freeze recorded a plugin set DISJOINT from the
    one every run of the suite loads. Nothing refused at capture time; the
    symptom arrived after a full suite run as seventeen lines naming plugins,
    which points a reader at plugin injection rather than at the interpreter.
    `plugins` is inside `root_hash_payload`, so correcting it cost a whole
    retake.

    The fallback is not a courtesy, it is the case for a public clone that has
    not run `uv sync` and for an operator on a system-wide install. Preferring
    the venv only when it EXISTS keeps this from imposing a layout on a tree that
    does not have one. `scripts/run-tests.py` re-execs into the same interpreter
    via `ensure_venv`, so the two agree and the baseline describes whatever will
    actually run the suite — which is the whole point.

    That sentence read "agree by construction" when it was written on 2026-08-04,
    and it was false the same day. `ensure_venv` decided "already there" by
    resolving both paths, and a stdlib `python -m venv` symlinks
    `.venv/bin/python` to the system interpreter, so on that layout it skipped
    the re-exec and the suite ran outside the venv. They agree because both now
    ask `venv.interpreter_identity`, not because the layout guarantees it.

    Not `ensure_venv`, which re-execs. That is what `run-tests.py` does and what
    `scripts/canopus.py` cannot: the CLI module is imported by
    `tests/test_canopus_cli.py`, and a re-exec at import time takes the suite
    down with it. Choosing the CHILD's interpreter reaches the same end without
    touching the parent process.
    """
    target = venv_python()
    return target if target.exists() else Path(sys.executable)


def interpreter_notice(chosen: Path, invoking: Path) -> str:
    """One line when the capture used a different interpreter, "" when it did not.

    Pure, so the decision is testable without a subprocess and the CLI keeps only
    the printing.

    Silence when they agree is half the requirement, not an optimisation. A
    notice that fires on every invocation is one an operator stops reading, and
    this line exists precisely to be read on the rare day it appears.

    "The same" is `venv.interpreter_identity`, never a resolved-path comparison. See
    that function: resolving both leaves is what made this notice silent on the
    commonest venv layout there is.

    BOTH paths are named. "A different interpreter" without saying which sends
    the reader back to the guessing this line was written to end.
    """
    if interpreter_identity(chosen) == interpreter_identity(invoking):
        return ""
    return (f"the contract child ran under {chosen}, not the {invoking} that "
            f"invoked this command; the plugin baseline describes the former")

# The most the greedy candidate's payload may carry across the process boundary.
# Linux caps ONE `execve` string at MAX_ARG_STRLEN (32 pages, 131072 bytes) and
# answers E2BIG above it; `subprocess` raises that as `OSError`, which is not the
# `ContractError` this module promises its callers. Set below the real ceiling on
# purpose: the marker, the newline separators and the platform's own accounting
# all live inside the same string, and a probe is not worth tuning to the byte.
# Enforced by `passable_literals`, beside the NUL rule that guards the other half
# of the identical boundary.
PAYLOAD_BUDGET = 96 * 1024

# The only two exits a probe run can be READ from. 0 is all green; 1 is tests
# failed, which is the ordinary state of an unimplemented contract under a stub.
# Every other exit means the run measured something other than the contract:
# 2 is interrupted, 3 is an internal error, 4 is a usage error, 5 is nothing
# collected. Measured: an interrupted session exits 2 and still writes a PARTIAL
# JUnit report, so a probe that reads the report without reading the exit code
# computes its verdict over the survivors of a run that stopped early, and two
# children truncated the same way AGREE with each other, which is the reading
# that looks like a measurement.
#
# 5 is in the refused set deliberately, and it is not only an interruption case.
# Measured: a contract file that skips at MODULE level under the stub exits 5
# while xunit1 still writes ONE synthetic testcase named after the module, so the
# population is not empty, the emptiness guard below cannot see it, and the
# verdict came back carrying ('c/test_lost.py', 'c.test_lost'), an id that is
# not a test and that a caller would print to the operator as a vacuous test.
PROBE_RETURNCODES = (0, 1)


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

    A member that resolves OUTSIDE *root* is refused with a sentence naming the
    root, never left to `relative_to`. Measured 2026-08-07: `canopus.py probe`
    on an existing file outside the tree died with a raw
    `ValueError: ... is not in the subpath of ...` traceback, because `main`
    catches `ContractError` and `OSError` and this was neither. Its own stated
    policy is that a filesystem fault produces a refusal the operator can act on
    rather than a stack trace that reads as a bug in the tool, and a path
    argument pointing somewhere else is the most ordinary way to reach it.
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
            resolved = candidate.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise ContractError(f"{resolved} is outside the tree being probed ({resolved_root}), so it is "
                                    f"not part of that tree's contract. Pass --root to name the tree it is in.")
            found.add(resolved.relative_to(resolved_root).as_posix())
    return sorted(found)


CONFTEST_PATTERNS = ("conftest.py",)


def contract_source_files(
    paths: Sequence[Path],
    root: Path,
    patterns: Sequence[str] = DEFAULT_PATTERNS,
) -> list[str]:
    """The contract's own SOURCE: its test modules and the conftests beside them.

    A separate accessor rather than a wider default on `contract_files`, because
    the two answer different questions and only one of them may grow.
    `contract_files` says which files are contract TESTS: it feeds the per-file
    baseline the manifest records and the collected-nothing refusal. A conftest
    yields no test items, so counting one there would record a baseline of zero
    for a file that can never move off it, and every contract carrying a conftest
    would be refused for collecting nothing.

    A conftest IS contract source, and the AST reader has to see it. Measured
    through the CLI: a fixture whose body is `from absent_thing import Widget`
    puts the contract's only absent import in a file the `test_*.py` glob never
    reads, so the claim set came back empty, nothing was stubbed, and `freeze`
    took a contract whose one test asserted `len(widget.items()) == 0` against a
    subject that did not exist. Building the subject in a fixture is ordinary
    pytest, not an exotic shape.

    A path named as a FILE brings its own directory's conftest with it, because
    pytest loads that conftest for that file too. Only the immediate parent: a
    directory argument is walked whole below, and climbing further from a file
    argument would claim modules named by files the contract does not contain.
    """
    targets = list(paths)
    for raw in paths:
        target = Path(raw)
        if target.is_dir():
            continue
        for pattern in CONFTEST_PATTERNS:
            sibling = target.parent / pattern
            if sibling.is_file():
                targets.append(sibling)
    return contract_files(targets, root, tuple(patterns) + CONFTEST_PATTERNS)


_DYNAMIC_IMPORT_CALLEES = ("import_module", "__import__", "importorskip")


def contract_imports(paths: Sequence[Path], root: Path) -> set[str]:
    """Dotted module names the contract's own source imports.

    Source means `contract_source_files`, so the contract's conftests are read
    alongside its test modules; the reasoning is in that function's docstring.

    Read from the AST rather than from the child's failure text, and that is the
    whole slice. `try/except ImportError` around a plain `import` or `from`
    statement erases the failure MESSAGE, so the revision this replaces saw
    nothing to stub and the refusal could not fire. It cannot erase the import
    STATEMENT itself, because the AST is what the interpreter executes: the node
    is there whether or not the author routes around its exception.

    That guarantee holds only for `import` and `from ... import ...` statements.
    It does NOT hold for a dynamic import whose module name is computed at run
    time: `importlib.import_module(name)`, `__import__(name)`,
    `pytest.importorskip(name)` with `name` a variable emit no `Import` or
    `ImportFrom` node at all, and there is no literal string here to collect
    either. Nor does it hold for two other spellings of a name that IS known at
    compile time: an f-string (`f"absent_thing"`) and a concatenation
    (`"absent" + "_thing"`) are each their own AST node, not an `ast.Constant`,
    so neither contributes a string this function can read. A third spelling of
    the same idea, implicit adjacent concatenation (`"absent" "_thing"`), is
    different: the parser folds it into one `ast.Constant` before this function
    ever walks the tree, so that spelling IS collected. These missed forms,
    among others, are unread by this function, and it fails OPEN on them:
    never stubbed, never proved vacuous. Only the run-time-computed name
    (`import_module(name)` with `name` a variable) is invisible to ANY
    static reader; the other two are merely unread by this one, which reads
    literal strings only. A callee that is neither a bare name nor a plain
    attribute access, such as `registry["fn"]("absent_thing")` or a call built
    through `getattr`, is skipped outright: `func` matches neither
    `ast.Name` nor `ast.Attribute`, so `callee` is `None` and the call's
    arguments are never inspected at all. What IS collected is every `str`
    `ast.Constant` found among the positional
    arguments and the keyword-argument values of those same three calls,
    matched on the bare callee name rather than on the resolved object.
    Matching by name over-reports rather than under-reports (a shadowed local
    function named `import_module` also gets picked up), and over-reporting is
    the safe direction here: a wider claim set can only turn a passing probe
    test into a vacuity label, never hide one. The consumer is
    `_passable_claims`, which tolerates the junk that direction produces rather
    than assuming every element is an importable dotted name.

    Relative imports are skipped: `from . import x` names no absolute module, and
    a name no import statement can produce is a claim that can only be wrong.

    A file that will not parse, or cannot be read as UTF-8, raises rather than
    contributing nothing. An empty set here is a real answer ("this contract's
    source names no module"), and `run_null_stub` refuses on it rather than
    reading it as a verdict, so the two must not be conflated at this level.
    """
    modules: set[str] = set()
    for rel in contract_source_files(paths, root):
        path = Path(root) / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as exc:
            raise ContractError(
                f"the contract file {rel} could not be parsed, so the imports it "
                f"names could not be read: {exc}"
            ) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    callee = func.id
                elif isinstance(func, ast.Attribute):
                    callee = func.attr
                else:
                    callee = None
                if callee in _DYNAMIC_IMPORT_CALLEES:
                    candidates = list(node.args) + [
                        kw.value for kw in node.keywords
                    ]
                    for value_node in candidates:
                        if isinstance(value_node, ast.Constant) and isinstance(
                            value_node.value, str
                        ):
                            modules.add(value_node.value)
    return modules


def contract_literals(paths: Sequence[Path], root: Path) -> set[str]:
    """Every string the contract's own source names, for the greedy candidate.

    Two sources, unioned, and both are strings the contract WROTE. Every `str`
    `ast.Constant` in its source, which is what a substring assertion greps for;
    and the module names `contract_imports` reads, because a contract that greps
    for the subject's own name is grepping for a string it wrote too, and the
    import statement is where it wrote it.

    Read from the contract and NOWHERE else. A candidate carrying an alphabet,
    or a random blob, or the repository's vocabulary would satisfy substring
    assertions the contract never made, and every refusal it then produced would
    be manufactured by the instrument. Here the payload can only satisfy a grep
    the contract itself performs.

    It over-reports on purpose, exactly as `contract_imports` does: a docstring's
    prose and a fixture's file name are collected alongside the assertions'
    needles. Over-reporting can only make the greedy payload satisfy MORE, which
    can only refuse a contract, and the refusal is whole-contract, so a single
    incidental match cannot produce one on its own.

    Raises on a file that will not parse, like `contract_imports`, and for the
    same reason: an empty set is a real answer that a caller must be able to tell
    apart from a file it could not read.
    """
    literals: set[str] = set(contract_imports(paths, root))
    for rel in contract_source_files(paths, root):
        path = Path(root) / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as exc:
            raise ContractError(
                f"the contract file {rel} could not be parsed, so the strings it "
                f"names could not be read: {exc}"
            ) from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    return literals


def passable_literals(literals) -> list[str]:
    """The literals that can survive the trip to the child, sorted.

    The greedy payload crosses the process boundary as ONE environment value, so
    a literal carrying a NUL would make that value carry one, and an environment
    value holding a NUL raises `ValueError: embedded null byte` out of
    `subprocess` — not the `ContractError` this module promises its callers, so
    the CLI that catches `ContractError` would die with a traceback instead of a
    refusal. Dropped rather than escaped, on the same rule and for the same
    reason `_passable_claims` drops one from the claim set.

    The separator is NOT a drop rule here, and that is the difference between
    this function and its neighbour. A claim set is joined on a comma because no
    importable dotted name can contain one; a literal can contain anything, so
    the payload is joined on a newline and never split apart again — the child
    receives the finished string and re-derives nothing.

    SIZE is the other half of the same boundary, and it was missing until
    2026-08-04. Linux caps ONE `execve` string at MAX_ARG_STRLEN — 32 pages,
    131072 bytes — and answers E2BIG above it, which `subprocess` raises as
    `OSError`. That is no more a `ContractError` than the NUL is: `canopus.py`
    catches it under the sentence "the frozen contract could not be read, so it
    cannot be verified" and files it in the ledger as `unreadable`, so the
    operator is told the wrong thing about the wrong file and the yield report
    counts the wrong cause. Measured on this repository the same day: the
    pass-candidates contract's payload is 11160 bytes, and the whole of `tests/`
    read as one contract is 798034 — six times over, so the ceiling is reachable
    by a contract set, not only in theory.

    The budget leaves head-room under the real limit because the marker, the
    newline separators and the platform's own accounting all sit inside the same
    string. SMALLEST literals are kept first, deliberately: what a substring
    assertion greps for is a short needle, and what makes a payload enormous is a
    docstring paragraph, so spending the budget on the short strings keeps almost
    all of the probe's reach while bounding the value.

    Dropping can only make the greedy candidate satisfy LESS, so it can only
    fail to refuse a weak contract, never refuse an honest one. That is the
    direction this instrument is allowed to err in — both drop rules err in it,
    which is why neither raises.
    """
    passable = sorted(value for value in literals if "\x00" not in value)
    budget = PAYLOAD_BUDGET - len(GREEDY_MARKER.encode("utf-8")) - 1
    spent = 0
    kept: list[str] = []
    dropped = 0
    for value in sorted(passable, key=lambda text: len(text.encode("utf-8"))):
        cost = len(value.encode("utf-8")) + 1
        if spent + cost > budget:
            dropped += 1
            continue
        spent += cost
        kept.append(value)
    if dropped:
        # Named, never silent, on the rule `run_pass_candidates` follows for a
        # candidate that lost tests: a probe that measured part of a contract
        # must not print the same page as one that measured all of it.
        print(
            f"canopus: the greedy pass-candidate's payload would exceed "
            f"{PAYLOAD_BUDGET} bytes, which is more than one environment value "
            f"can carry, so {dropped} of the contract's longest string literal(s) "
            f"were dropped from it. The candidate therefore satisfies LESS than "
            f"the contract wrote, which can only make it fail to refuse a weak "
            f"contract, never refuse an honest one.",
            file=sys.stderr,
        )
    return sorted(kept)


def run_pass_candidates(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
    expected_population: Optional[Sequence[tuple[str, str, str]]] = None,
) -> dict[str, set[tuple[str, str]]]:
    """For each candidate, the (file, test) pairs it turned green.

    The null-stub probe asks whether a contract test passes while the code under
    test is ABSENT. This asks the other question: whether it passes while the
    code is PRESENT AND WRONG. A test red for a perfectly real reason before the
    implementation exists can still be satisfied by an implementation nobody
    would accept, and until this ran the only instrument that saw that was
    mutation at step 11 — after the code is written, after the contract is
    frozen, when correcting it costs a window and a retake.

    ONE claim set, computed once and shared with the stub runs, because two
    probes standing in for different names would print two verdicts about two
    different contracts on one page.

    ONE pytest session per candidate and no more. The cost is stated because an
    instrument whose cost drifts upward unmeasured is one the operator stops
    running, and this one runs inside `approve` and `freeze` as well as `probe`.

    `expected_population` is the REAL run's triples. Supplied, it saves a
    session; omitted, this function runs its own unstubbed baseline rather than
    weighing a candidate against a population nobody measured. Identical to the
    contract `run_null_stub` carries, deliberately: two probes with two dialects
    of one parameter is a defect waiting for whichever caller passes it to only
    one of them.

    A candidate run that collected FEWER of the real run's red tests is reported
    on stderr and does not refuse. The arithmetic already errs safe there — a
    test missing from a candidate's passed set breaks the whole-contract subset,
    so a loss can only make refusal LESS likely, never manufacture one — and
    that asymmetry is why this reports where `run_null_stub` raises. Its loss
    reads as a clean vacuity verdict; this one reads as a contract no candidate
    took, which is the status quo before this probe existed. Silence would still
    let a probe that measured half a contract print the same page as one that
    measured all of it, so the loss is named.
    """
    modules = _passable_claims(contract_imports(paths, root))
    if not modules:
        # The identical posture `run_null_stub` takes, one step earlier and for
        # the identical reason: nothing was stood in for, so no wrong
        # implementation was ever put in front of this contract, and the empty
        # verdict a caller would receive is the same value a completed
        # measurement that found nothing returns.
        raise ContractError(
            "the contract's source names no module this probe could stand in "
            "for, so no wrong implementation was ever put in front of it and "
            "the pass-candidate check measured NOTHING. Import the code under "
            "test inside the test body or a fixture, so this probe can read the "
            "name and replace it."
        )
    payload = greedy_payload(passable_literals(contract_literals(paths, root)))
    engine_root = str(Path(__file__).resolve().parent.parent.parent)
    base_env = {
        MODULES_VAR: STUB_NAME_SEPARATOR.join(modules),
        GREEDY_PAYLOAD_VAR: payload,
        "PYTHONPATH": os.pathsep.join(
            [engine_root, str(Path(root).resolve()),
             os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }
    if expected_population is None:
        _real_counts, real_outcomes = run_contract(paths, root, timeout=timeout)
    else:
        real_outcomes = list(expected_population)
    real_red = {
        (rel, name) for rel, name, outcome in real_outcomes
        if outcome in RED_OUTCOMES
    }
    taken: dict[str, set[tuple[str, str]]] = {}
    for name in CANDIDATES:
        xml_text = run_pytest_report(
            paths, root, timeout=timeout,
            extra_env={**base_env, CANDIDATE_VAR: name},
            extra_args=("-p", "scripts.utils.canopus_nullstub"),
            allowed_returncodes=PROBE_RETURNCODES,
        )
        _counts, outcomes = parse_junit(xml_text)
        collected = {(rel, test) for rel, test, _o in outcomes}
        taken[name] = {
            (rel, test) for rel, test, outcome in outcomes if outcome == "passed"
        }
        lost = sorted(f"{rel}::{test}" for rel, test in real_red - collected)
        if lost:
            print(
                f"canopus: the {name} pass-candidate never collected these tests "
                f"the real run recorded red, so it did not measure them and they "
                f"cannot be called taken or cleared: " + ", ".join(lost)
                + ". Most often a module-scope statement reads a value this probe "
                "stands in for; move it inside the test body.",
                file=sys.stderr,
            )
    return taken


# What each candidate DID, and what the contract has to gain to survive it. One
# sentence per candidate, and the refusal prints exactly one of them: naming the
# candidate is only useful if the reader is spared the other two.
#
# The wording avoids the other candidates' names deliberately. `none` is an
# ordinary English word, so a sentence about `greedy` that reached for "none of
# them" would put the wrong candidate's name in a refusal about this one, and
# every reader who greps the text would be misled. The contract pins that.
_CANDIDATE_CURE = {
    "none": (
        "That candidate returns nothing from every call, so the contract never "
        "reads a value at all: it checks that the code RAN. Assert what a call "
        "returns."
    ),
    "echo": (
        "That candidate hands back its first argument unchanged, so the "
        "contract accepts a pass-through: it checks that something came out, "
        "not that anything was done to it. Assert a value the input alone "
        "cannot produce."
    ),
    "greedy": (
        "That candidate answers with every string this contract itself wrote, "
        "so the contract is satisfied by a grep: it checks that a word appears "
        "somewhere rather than what the value IS. Replace the substring check "
        "with an equality against the whole value."
    ),
}


def pass_candidate_refusal(
    outcomes: Sequence[tuple[str, str, str]],
    taken: dict[str, set[tuple[str, str]]],
) -> list[str]:
    """The one refusal the pass-candidate probe raises: a candidate took it all.

    WHOLE-CONTRACT, mirroring `vacuity_refusal`, and the mirror is deliberate
    rather than tidy. "These three tests assert too little" is a judgement for a
    human, and a contract legitimately carrying one substring assertion beside
    one equality assertion would be refused by any per-test rule. "Every single
    thing this contract checks is satisfied by an implementation that returns
    None" is not a judgement call.

    Only tests the real run recorded RED are weighed, which is the evidence rule
    the rest of this module follows. A test that PASSED for real had no absent
    import for a candidate to satisfy, so its pass under one has another
    explanation; counting it would let a single green test drag a genuinely loose
    contract out of the refusal.

    The empty-red-set guard is load-bearing and is written here rather than
    inherited, because `set() <= anything` is True. Without it an all-green
    contract would be refused with a sentence about wrong implementations
    instead of by `refusal_reasons`, which owns that case and says why.

    ONLY the candidate that took the contract is named, and the other two are
    not so much as mentioned. The operator's next action depends on WHICH
    wrongness sufficed, and a refusal that recited the whole glossary would put
    the reader back to working out which line applied to them — which is the
    work the naming exists to save. A first draft did recite all three and the
    frozen contract caught it.
    """
    red = {(rel, name) for rel, name, outcome in outcomes
           if outcome in RED_OUTCOMES}
    if not red:
        return []
    for candidate in CANDIDATES:
        if red <= taken.get(candidate, set()):
            return [
                f"every contract test that is red passes against the "
                f"`{candidate}` pass-candidate, an implementation that EXISTS "
                f"and is wrong, so the contract's redness measures that the code "
                f"is absent rather than that the tests check what it does. "
                + _CANDIDATE_CURE[candidate]
            ]
    return []


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


def _qualified_name(rel: str, classname: str, name: str) -> str:
    """The test's name, carrying its class chain when it has one.

    A report entry identifies a test by `(file, name)` everywhere in this module,
    and under xunit1 `name` is the BARE method name with the class held separately
    in `classname`. Measured: `class TestVacuous: def test_x` and `class
    TestHonest: def test_x` in one file both arrive as `test_x`, collapse to one
    pair, and the pair then lands in the vacuous set from the first and in the
    case list from the second, so `cases <= vacuous` holds and a contract with one
    honest test is refused whole. `class TestRead` beside `class TestWrite` is not
    an adversarial shape.

    The tuple is not widened, because the frozen contract asserts two-tuples such
    as `{("c/test_one.py", "test_a")}`. The NAME is qualified instead, and only
    when the classname says there is a class to qualify with. For a module-level
    function xunit1 writes the MODULE's dotted path as the classname, so `test_a`
    in `c/test_one.py` arrives as `classname="c.test_one"`, which matches the
    path exactly and leaves the name alone; a method in `TestVacuous` arrives as
    `classname="c.test_one.TestVacuous"`, and the tail becomes the prefix. The
    WHOLE tail, not its last segment: `TestA.TestInner.test_x` and
    `TestB.TestInner.test_x` are the same collision one level down.

    Anything that does not begin with the module's own dotted path is left alone
    rather than guessed at. A synthetic module-level entry carries no classname at
    all, and a rootdir this function cannot reconstruct the dotted path under
    would otherwise have its names rewritten on a coincidence.
    """
    if not classname:
        return name
    module = Path(rel).with_suffix("").as_posix().replace("/", ".")
    prefix = module + "."
    if classname.startswith(prefix):
        return f"{classname[len(prefix):]}.{name}"
    return name


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

    A test method's name carries its class chain, per `_qualified_name` above:
    two classes in one file may hold a method of the same name, and the bare name
    collapses them into one identity.
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
        name = _qualified_name(
            rel, case.get("classname") or "", case.get("name") or ""
        )
        outcomes.append((rel, name, _outcome(case)))
    return counts, outcomes


def run_pytest_report(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
    extra_env: Optional[dict] = None,
    extra_args: Sequence[str] = (),
    allowed_returncodes: Optional[Sequence[int]] = None,
) -> str:
    """Run pytest over *paths* once and return the raw JUnit XML.

    Extracted from run_contract so the null-stub probe can run the same command
    with two extra arguments instead of duplicating the flag set. Every flag here
    is load-bearing, and each is explained below.

    extra_env is merged over os.environ rather than replacing it, so the trace id
    a daemon exported still reaches the child (.claude/rules/trace-id.md).

    Returns the XML and, on the way past, forwards any line of the child's stderr
    that carries NULLSTUB_STDERR_MARKER. See the comment at that loop: it is the
    stub plugin's report of an exception it swallowed, and this is the only place
    it can still be read.

    `-o addopts=` neutralises the repository's configured addopts (coverage,
    parallel workers) so the report is deterministic and cheap. CANOPUS_NO_ATTEST
    stops the child session writing an attestation over the real one: `probe` can
    legitimately run while a freeze is held.

    `--import-mode=importlib` is then restored EXPLICITLY, because `-o addopts=`
    deletes the repository's pin along with the coverage flags it was aimed at,
    and this child must read the contract in the same import mode the gate does.
    Every Canopus slice writes its contract to
    `tests/contract/{date}-{slug}/test_contract.py`, so the convention
    GUARANTEES a basename collision between slices, which is exactly the class
    pyproject.toml pins importlib to remove. Measured under the inherited
    default: a contract spanning two slice directories collected one of its two
    files, the other was silently dropped from the report, and the builder was
    told to move imports that were already inside the test body. Not an escape,
    because all three children of one verdict carry the same flags and every
    guard compares like with like; the cost is a false diagnosis, and a gate
    that misdiagnoses is one the operator learns to route around. The flag is
    spelled here rather than left to `addopts` so the probe's mode is a property
    of this command instead of a property of whatever config it inherits.

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

    The return code is deliberately ignored BY DEFAULT. A contract that has not
    been implemented yet EXITS NONZERO, and that is the state this function
    exists to observe, so the baseline run reads its report whatever the child
    exited with.

    `allowed_returncodes` is how a caller that CANNOT tolerate a truncated report
    says so, and only the null-stub probe does. Measured: a session interrupted
    mid-run exits 2 and still writes a partial JUnit report holding one of its
    three tests. The baseline would simply record a smaller contract; the probe
    computes a differential over two populations, and two children truncated the
    same way AGREE with each other, so the verdict is taken over the survivors
    and reads exactly like a completed measurement. Refused there, ignored here.
    """
    resolved_root = Path(root).resolve()
    rels = [str(Path(p).resolve()) for p in paths]
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "contract.xml"
        command = [
            # NOT sys.executable. See contract_interpreter: the child's plugin
            # set becomes the freeze's baseline, so it has to be the interpreter
            # that will run the suite rather than the one that typed the command.
            str(contract_interpreter()), "-m", "pytest", *rels,
            "--junit-xml", str(report),
            "-o", "addopts=",
            "--import-mode=importlib",
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
        #
        # The PYTEST_ scrub comes from the one definition every pytest child in
        # this codebase shares (`pytest_child_env`, above). It is not tidiness:
        # while this child inherited the whole environment, an exported
        # PYTEST_DISABLE_PLUGIN_AUTOLOAD made the measurement a photograph of the
        # operator's shell. The measurement is in that function's docstring.
        env = pytest_child_env(
            CANOPUS_NO_ATTEST="1", PYTHONDONTWRITEBYTECODE="1",
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
        # The child's stub diagnostics, forwarded rather than dropped. The stub
        # plugin STUBS a claimed name whose resolution raised and reports the
        # exception instead of propagating it, so this line is the only trace
        # that anything went wrong at all. Discarded, a first-party module that
        # blows up on import reaches the operator as a bare vacuity refusal with
        # nothing to explain it. Scoped to the marker rather than echoing the
        # whole stream: an ordinary contract run loads no plugin, writes no such
        # line, and is unaffected.
        #
        # ONE CAUSE, TWICE ON THE STREAM, and that is expected rather than a
        # second fault. This function forwards the stderr of the child it ran,
        # and run_null_stub runs two stub children over the same claim set, so a
        # name whose resolution raises reports once in each. Deduplicating here
        # is not possible (this call sees one child) and deduplicating in the
        # caller would hide the case where only ONE of the two runs hit it,
        # which is the more interesting reading of the pair.
        for line in (proc.stderr or "").splitlines():
            if line.startswith(NULLSTUB_STDERR_MARKER):
                print(line, file=sys.stderr)
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
        if (
            allowed_returncodes is not None
            and proc.returncode not in allowed_returncodes
        ):
            # Checked AFTER the missing-report branch above, which says more: a
            # child that wrote nothing at all names loss of lock, and that
            # diagnosis should not be replaced by one about an exit code.
            raise ContractError(
                f"the probe child did not run the contract to completion: "
                f"pytest exited {proc.returncode}, and only "
                + " and ".join(str(code) for code in allowed_returncodes)
                + " are exits a probe verdict can be read from (2 interrupted, "
                "3 internal error, 4 usage error, 5 nothing collected). Its "
                "JUnit report is therefore partial or empty, and a verdict read "
                "from one is a verdict over whichever tests happened to run."
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


# The two characters a claim may not contain, and why each is refused. The
# separator is the wire format: a string carrying it would arrive in the child as
# two fragments the contract never named, and a fragment can claim a module that
# EXISTS, replacing real values with stand-ins for the length of the probe. The
# NUL cannot cross the process boundary at all: an environment value holding one
# raises `ValueError: embedded null byte` out of `subprocess`, which is not the
# `ContractError` this module promises its callers, so the CLI that catches
# `ContractError` would die with a traceback instead of a refusal.
#
# Neither can appear in an importable dotted name, so dropping loses no claim
# that could ever have been imported, and claims nothing the contract did not
# write. Task 1's AST reader over-reports on purpose, which is what puts strings
# like these in the set in the first place.
_UNPASSABLE_IN_A_CLAIM = (STUB_NAME_SEPARATOR, "\x00")


def _passable_claims(collected: set[str]) -> list[str]:
    """The collected strings that can survive the trip to the child, sorted.

    `contract_imports` OVER-reports by design, and some of what it returns was
    never a module name: every string constant among a dynamic import's
    arguments is collected, so `pytest.importorskip("x", reason="needs, the
    thing")` contributes the prose too, and `__import__("a\\x00b")` contributes a
    string no environment can carry. Both are dropped rather than escaped, for
    the reasons recorded at `_UNPASSABLE_IN_A_CLAIM` above.

    The drop is reported, because a claim silently removed is a verdict silently
    widened.

    Sorted so the value handed to the child is a function of the set alone. Two
    runs of the same contract that differ only in iteration order would otherwise
    be two different probes.
    """
    passable = sorted(
        name for name in collected
        if not any(bad in name for bad in _UNPASSABLE_IN_A_CLAIM)
    )
    for name in sorted(collected - set(passable)):
        print(f"canopus: the contract names {name!r} where a module name was "
              f"expected, and it carries a character this probe cannot pass a "
              f"name with, so it is not claimed", file=sys.stderr)
    return passable


def _counts_by_file(outcomes: Sequence[tuple[str, str, str]]) -> dict[str, int]:
    """How many items each file yielded, read off the per-test outcomes.

    `parse_junit` appends an outcome and bumps a count in the same loop
    iteration, so this is equal to the counts it returns; deriving it here lets
    the probe treat a caller-supplied population and its own baseline run
    identically, instead of carrying counts for one and inferring them for the
    other.
    """
    counts: dict[str, int] = {}
    for rel, _name, _outcome_token in outcomes:
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def run_null_stub(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
    expected_population: Optional[Sequence[tuple[str, str, str]]] = None,
) -> set[tuple[str, str]]:
    """The (file, test) pairs that pass under BOTH stub value sets.

    Each one is proved to assert nothing about the code under test: it passed
    while the implementation was absent, and its outcome did not change when the
    stub's values changed, so it cannot be reading those values.

    Two runs, not one, and the second is not belt-and-braces. Measured: under a
    single stub `assert len(result) == 0` passes and earns a vacuity label it did
    not deserve, along with `assert key not in result` and `assert int(v) == 1`.
    Nine of nine assertions classify correctly under the differential rule; four
    are wrong under the single-stub rule, every one toward refusing a good
    contract, which is the direction that teaches a builder to route around the
    gate.

    The stub set comes from the contract's own AST, over its test modules AND its
    conftests. Nothing the child SAYS is read; its JUnit report is, for the
    outcomes, and the distinction is the point rather than a caveat. An outcome is
    pytest's verdict on a test; the prose the earlier revision parsed was the
    contract author's.

    A claim set that comes back EMPTY raises rather than returning an empty
    verdict. Nothing was stubbed there, so nothing was measured, and the empty set
    a caller would have received is the same value a completed measurement that
    found nothing vacuous returns.

    One escape family stays open, by construction rather than by oversight: a
    claimed module that EXISTS and whose own body raises at import time is never
    stubbed, so its test stays red for its original reason and never enters the
    intersection. A claimed PACKAGE whose `__init__` imports a module the
    contract never named is the same shape, because the package resolves, the
    wrapping loader runs its body, and the body raises. Both measured through
    this function, and both returned an empty verdict. The neighbouring case
    behaves differently and is worth telling apart: when that unwritten module
    lies BELOW the claimed package it is already claimed by the prefix rule, so
    it IS stubbed and its test is labelled, which was measured too.

    So a refusal must never be read as "your test asserts nothing" on its own.
    For this family vacuity was not measured at all, and the truth may be that
    the contract's own package does not import; `vacuity_refusal` says so in the
    text it returns. The claim set is what the contract's AST named, and this is
    the price of that. The child reports what it swallowed on stderr, and
    run_pytest_report forwards it, so the operator has the thread to pull.

    `expected_population` is the REAL run's `(file, test, outcome)` triples, and
    it is optional because the documented two-argument call must keep working AND
    must keep the guards below armed. Every other guard here reads the two stub
    runs against EACH OTHER, and two runs truncated the same way agree; the real
    run is the only witness to which tests were supposed to be there at all. So
    when the caller supplies none, this function RUNS ONE: an unstubbed baseline
    over the same paths, whose population and per-file counts are then the real
    ones. That is a third pytest session per verdict, which is the cost the plan
    budgeted rather than a new one, and the alternative is a two-argument call
    whose guards are silently off.

    That baseline is `run_contract` over the same paths, which is exactly what a
    caller supplying `expected_population` ran to obtain it, so supplying it and
    omitting it produce the same verdict rather than two dialects of one probe.
    It deliberately does NOT carry the stub runs' PYTHONPATH additions: the two
    runs are then the same measurement a caller would have made, and the stub is
    the only difference this function reads.

    THE PRICE, stated so a caller can budget it: three pytest sessions per
    verdict, or two when the caller supplies `expected_population`. TIMEOUT IS
    PER CHILD, so the worst case is three times the caller's value.
    """
    modules = _passable_claims(contract_imports(paths, root))
    if not modules:
        # Nothing was stubbed, so nothing was measured, and an empty verdict from
        # that state is not evidence. Returning `set()` here was the same silent
        # acquittal the guards below refuse, one step earlier: the caller cannot
        # tell it from "measured, and nothing was vacuous", so it printed no
        # vacuity word, exited 0, and wrote the manifest. Measured through the
        # CLI on `def test_a(): assert 1 == 2`, and on a contract whose only
        # absent import lived in a fixture in its conftest.
        raise ContractError(
            "the contract's source names no module this probe could stand in "
            "for, so nothing was stubbed and vacuity was NOT measured: the "
            "contract's redness has not been shown to mean anything. A contract "
            "that never names the code under test cannot be shown to assert "
            "something about it. Import the code under test inside the test "
            "body or a fixture; if the contract reaches it only through a name "
            "computed at run time, spell that name in a plain import statement "
            "too, so this probe can read it."
        )
    files = contract_files(paths, root)
    # A stub standing in for the contract's OWN package would poison collection
    # silently. A stub lands in sys.modules and is returned by every later
    # import_module even after the finder is gone, because sys.modules is read
    # before sys.meta_path; under `--import-mode=importlib` pytest builds each
    # collected module's parent packages through exactly that path, and that is
    # the mode the probe child runs in, pinned on its own command line in
    # `run_pytest_report` rather than inherited from a config it neutralises.
    # (The blocker was originally measured by FORCING the flag onto a child that
    # then inherited whatever mode its config carried; it now describes the
    # running probe.) `__path__` is
    # refused, but `__getattr__` answers everything else, so the damage is
    # invisible rather than mock-shaped. This repository's `pythonpath = ["."]`
    # makes `tests` resolve, so the prefix filter never claims it and the case
    # cannot arise today; it arises under a different rootdir, so the refusal is
    # written now rather than left as a property of one config file.
    #
    # Intersected against the EXPANDED claim set, not the literal one, and that
    # is the difference between a guard and a decoration. Prefix expansion
    # happens in the child, so a contract importing `<own_top>.helper` spells no
    # literal `<own_top>` at all while the child claims it as an unresolvable
    # prefix and stands a stub in for the contract's own package: exactly the
    # different-rootdir case the paragraph above says this refusal was written
    # for, and the one shape the literal intersection could not see.
    #
    # `_expand_claims` rather than a syntactic walk over every dotted prefix,
    # because the child's rule is what has to be predicted here and it
    # deliberately leaves a prefix that RESOLVES TO A PACKAGE to `PathFinder`.
    # A syntactic walk would refuse a contract for a stub the child never
    # installs. The cost of borrowing the child's own function is named: it
    # resolves names in THIS process, so an ancestor package's `__init__` runs
    # here too, and it resolves under the parent's `sys.path` rather than the
    # child's extra PYTHONPATH entries, so a name importable only from the
    # contract root reads as unresolvable and is claimed. The first of those
    # costs was measured false for one release: `_expand_claims`'s handler
    # named only `Exception`, so an ancestor's ordinary `sys.exit(0)` walked
    # past it, past this function, and past `cmd_freeze`'s own `except
    # ContractError` — there is no child process boundary here to contain the
    # escape the way there is inside the probe — and the CLI exited 0 having
    # measured nothing. `_expand_claims` now catches `SystemExit` alongside
    # `Exception` for exactly this call site, so both costs push toward
    # claiming more, which can only refuse a contract, never wave one through.
    own_packages = {rel.split("/", 1)[0] for rel in files if "/" in rel}
    collision = own_packages & _expand_claims(modules)
    if collision:
        raise ContractError(
            "the contract imports a name that is also a package prefix of its own "
            "files, so stubbing it would stand in for the contract itself: "
            + ", ".join(sorted(collision))
        )
    # The ENGINE root is where the plugin lives (`-p scripts.utils.canopus_nullstub`
    # must import); the CONTRACT root makes the tree's own modules importable, so a
    # named module that exists is WRAPPED rather than stubbed whole.
    engine_root = str(Path(__file__).resolve().parent.parent.parent)
    base_env = {
        MODULES_VAR: STUB_NAME_SEPARATOR.join(modules),
        "PYTHONPATH": os.pathsep.join(
            [engine_root, str(Path(root).resolve()),
             os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }
    # The REAL run, which is what every guard below is measured against. Taken
    # after the collision refusal above, so the cheap refusal still costs nothing,
    # and before the stub runs, so a contract that cannot be measured at all is
    # not measured twice under a stub first.
    if expected_population is None:
        _real_counts, real_outcomes = run_contract(paths, root, timeout=timeout)
    else:
        real_outcomes = list(expected_population)
    # Derived from the outcomes in BOTH cases rather than taken from parse_junit
    # in one and derived in the other. parse_junit appends an outcome and bumps a
    # count in the same loop iteration, so the two are equal by construction, and
    # one expression here is one thing to keep true instead of two.
    real_counts = _counts_by_file(real_outcomes)
    populations = []
    unproved_each = []
    counts_each = []
    errored_each = []
    for label in ("A", "B"):
        xml_text = run_pytest_report(
            paths, root, timeout=timeout,
            extra_env={**base_env, VALUES_VAR: label},
            extra_args=("-p", "scripts.utils.canopus_nullstub"),
            allowed_returncodes=PROBE_RETURNCODES,
        )
        counts, outcomes = parse_junit(xml_text)
        counts_each.append(counts)
        populations.append({(rel, name) for rel, name, _o in outcomes})
        # Kept only to REPORT the instrument's own hand on stderr below. It
        # decides nothing: an errored test is labelled by the unproved rule that
        # follows, exactly like a skipped one.
        errored_each.append(
            {(rel, name) for rel, name, outcome in outcomes if outcome == "error"}
        )
        # "passed" is not the whole of "was not proved to assert anything".
        # Measured on the prototype: `pytest.skip("not implemented yet")` at the
        # top of a vacuous test yields `skipped` under BOTH runs, so it never
        # enters an intersection of PASSES and the freeze proceeds. That is a
        # one-call bypass, cheaper than the `from None` this slice closes, and it
        # is a recurrence: wire 2.3 already found that a skipped test is never in
        # the vacuous set. A test that did not run was not proved innocent.
        #
        # `error` is in this set for the same reason, and it is a REVERSAL of the
        # previous revision, which refused the whole contract on any errored test.
        # Review measured that refusal against five realistic contract shapes and
        # it fired on four, three of them fully honest: a fixture calling
        # `json.loads(RAW)`, `Path(ROOT) / "x"`, `re.compile(PATTERN)`, or
        # `datetime.strptime(STAMP, ...)` errors the moment the stub reaches a
        # library that type-checks its argument. Building the subject in a fixture
        # is ordinary pytest and the authoring rule permits it, so a blanket
        # refusal lands squarely on it, and a gate that refuses that is one the
        # operator routes around, after which the gate proves nothing while
        # looking as though it does.
        #
        # The three answers, and why this one:
        #   * ACQUIT an errored test and the escape is arithmetic: a contract of
        #     one vacuous test that passes and one vacuous test that errors is not
        #     WHOLLY vacuous by the caller's subset test, so it freezes.
        #   * REFUSE the contract and one fixture touching a stdlib API costs a
        #     good multi-test contract everything.
        #   * NAME IT PER TEST and the cost is that one test's innocence, on the
        #     rule the skip case already settles: an outcome invariant to the stub
        #     value was not proved innocent. A contract carrying any test that
        #     asserts something is unaffected, because that test is not in this
        #     set.
        # An error in ONE run beside a pass in the other is named too, which is
        # the same over-reach the skip rule already carries, and the stderr report
        # below is what keeps it visible rather than silent.
        unproved_each.append(
            {(rel, name) for rel, name, outcome in outcomes
             if outcome in UNPROVED_OUTCOMES}
        )
    # An intersection is only evidence over one population. Two runs that
    # collected different tests were never compared, and two that collected
    # nothing measured nothing; both reach the same empty verdict, which reads
    # exactly like "measured, and nothing was vacuous". Refused instead, because
    # the quiet reading is the one that freezes a contract asserting nothing.
    if populations[0] != populations[1]:
        raise ContractError(
            "the two stub runs did not measure the same tests, so their verdicts "
            "cannot be compared: "
            + ", ".join(
                f"{rel}::{name}"
                for rel, name in sorted(populations[0] ^ populations[1])
            )
        )
    if not populations[0]:
        raise ContractError(
            "the stub runs collected no test at all, so nothing was proved "
            "either way: vacuity was NOT measured, which is not the same claim "
            "as measured and found absent"
        )
    # What the stub runs LOST relative to the real run. The guard above compares
    # the two stub runs with each other, and two runs that lost the same thing
    # agree, so it sees nothing; this compares them with the run that knows what
    # was supposed to be there.
    #
    # It is one guard rather than a file-level one and a test-level one, and the
    # merge is the point. The revision this replaces asked whether a contract file
    # still had a KEY in the stub runs' per-file counts, which one surviving test
    # satisfies. Built and measured, one file and two tests, both vacuous: a
    # module-scope `HIDDEN = (mod.X == mod.Y)` guarding the second test's `def` is
    # answered the same way under BOTH stub value sets, so the stub runs collected
    # 1 where the real run collected 2, the two stub runs agreed with each other,
    # the exit code was 1, no test errored, the verdict named one pair, the
    # caller's subset test failed, and the contract FROZE.
    #
    # The comparison is over the real run's RED tests BY NAME, not over raw
    # per-file counts, and the two differ in exactly two places.
    #   * A red test lost while the file's COUNT holds. Measured: a file that
    #     skips at MODULE level under the stub is recorded by xunit1 as ONE
    #     synthetic testcase named after the module, so the count is unchanged and
    #     only the names show the loss. A count comparison is blind there.
    #   * A GREEN test lost. Only RED tests are weighed, the same evidence rule
    #     the rest of this probe follows: a test that PASSED for real never had an
    #     absent import for the stub to resolve, so nothing vacuous can hide in
    #     its absence, and refusing on it would be an accusation the instrument
    #     manufactured. A raw count comparison cannot tell which colour it lost,
    #     so it would refuse there too.
    # Counts are still what the operator is TOLD, because "2 became 1" is the
    # sentence that points at the module-scope statement.
    #
    # A file the REAL run also lost is not the stub's doing and cannot appear
    # here: it recorded no red test for that file, so there is nothing to miss.
    # `refusal_reasons` owns that file and diagnoses it correctly. Blaming the
    # stub for it states something false, and on `probe`, which calls this
    # function unconditionally before any table is printed, it aborted the whole
    # command and handed the operator the wrong cause.
    lost = sorted(
        f"{rel}::{name}"
        for rel, name, outcome in real_outcomes
        if outcome in RED_OUTCOMES and (rel, name) not in populations[0]
    )
    if lost:
        shrank = sorted(
            f"{rel} (the real run collected {real_counts[rel]}, the stub runs "
            + " and ".join(str(counts.get(rel, 0)) for counts in counts_each)
            + ")"
            for rel in {rel for rel, _name, _o in real_outcomes}
            if any(counts.get(rel, 0) != real_counts[rel] for counts in counts_each)
        )
        raise ContractError(
            "vacuity was NOT measured for tests the real run recorded red, "
            "because the stub runs never collected them: "
            + ", ".join(lost)
            + ". Not measured is not proved innocent, and an intersection "
            "computed over the survivors reads exactly like a clean verdict."
            + (
                " The stub is what lost them, and the per-file tally says where: "
                + ", ".join(shrank)
                + ". Most often a module-scope statement reads a value from a "
                "module the contract also names a child of, which this probe must "
                "stub whole; move that statement inside the test body."
                if shrank else ""
            )
        )
    # The instrument's own contribution, said out loud. An errored test is named
    # vacuous by the unproved rule above, and an error is most often this probe's
    # stub meeting a library that type-checks its argument rather than anything
    # the contract did, so an operator reading a vacuity refusal has to be able to
    # see which entries came from the instrument. Reported, never refused: the
    # reversal that made this a label is argued at that rule.
    errored = sorted(
        f"{rel}::{name}" for rel, name in errored_each[0] | errored_each[1]
    )
    if errored:
        print(
            "canopus: these contract tests ERRORED under the stub rather than "
            "passing or failing, so they are named vacuous on the rule that an "
            "outcome invariant to the stub value was not proved innocent, and an "
            "error is often this probe's own stand-in reaching a caller that "
            "type-checks it: " + ", ".join(errored),
            file=sys.stderr,
        )
    return unproved_each[0] & unproved_each[1]


# One advisory used to stand here, and it is DELETED rather than left unused. It
# said "the contract is red and its report names no absent module, so nothing
# could be stubbed", reading that name set off the reader of the child's failure
# text that this module no longer carries either.
#
# The state it described is REACHABLE. An earlier revision of this comment
# claimed the AST reader made it unreachable, and review measured that false: the
# claim set is empty whenever the contract's source names no importable module at
# all (a test that imports nothing, a contract whose only import is relative), and
# whenever every name it does read is dropped as unpassable. What changed is not
# that the state went away but where it LANDS. It used to be an advisory printed
# beside an exit 0, which reads as a clean bill; it is now a `ContractError` out
# of `run_null_stub`, like every other state in which nothing was measured, and
# the callers turn those into refusals. Two families stay outside even that: a
# name computed at run time is invisible to any static reader, and a claimed
# module that EXISTS and raises on import is never stubbed. Both fail OPEN, and
# both are argued where they arise rather than here.

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
        # The same key space `parse_junit` builds, through the same helper. A
        # caller looks a mode up by the id the outcome list carries, so a reader
        # that qualified a method name and one that did not would miss on every
        # method and print no mode at all.
        name = _qualified_name(
            Path(rel).as_posix(), case.get("classname") or "", name
        )
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

    THE TEXT NAMES THE OTHER READINGS, and that sentence is load-bearing rather
    than a courtesy. The reader of this refusal is about to edit a test, and
    three separate worlds arrive here wearing the same label.
      * A module the contract named is stood in for whenever it does not
        resolve, and "does not resolve" covers an unwritten implementation, an
        extra that is not installed, and a first-party circular import that made
        the resolution raise. The probe cannot separate them, and the direction
        is toward refusal rather than acceptance, so it can only cost a correct
        contract an argument, never wave a bad one through. An operator who is
        not told will edit a correct test.
      * A test that ERRORED under both stub runs is in `vacuous` on the rule
        that an outcome invariant to the stub value was not proved innocent, and
        an error is most often this probe's own stand-in reaching a caller that
        type-checks its argument (`json.loads`, `Path`, `re.compile`,
        `datetime.strptime`). When every entry arrived that way, "the contract's
        redness asserts nothing" is FALSE: it was not measured. This function
        receives outcomes from the REAL run and cannot tell which entries those
        were, so it names the reading unconditionally instead of asserting the
        one it cannot prove; `run_null_stub` lists the errored tests on stderr.
    One string rather than a second list element, because the caller prints one
    line per reason and a second element would read as a second defect.

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
            "that the code is absent, not that the tests check anything. Before "
            "editing a test, rule out the readings this probe cannot tell apart: "
            "a module it named is stood in for whether the implementation is "
            "unwritten, an extra is not installed, or a first-party circular "
            "import made the resolution raise; and a test that ERRORED under "
            "both stub runs is named here because its outcome did not move with "
            "the stub value, which is not measured rather than proof that it "
            "asserts nothing (the probe lists those tests on stderr)."
        ]
    return []
