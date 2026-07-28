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

from scripts.utils.canopus_gate import pytest_child_env
# The child's half of the handshake, imported rather than spelled again. Two
# copies of an environment-variable name is a rename on one side away from a
# child that claims nothing, two runs that agree on a red the rule never fires
# over, and a suite that stays green while the verdict is silently always empty.
# Importing the plugin module runs no pytest hook: hooks are registered by
# `-p`, not by import, and this module is stdlib-only.
from scripts.utils.canopus_nullstub import (
    MODULES_VAR,
    NULLSTUB_STDERR_MARKER,
    STUB_NAME_SEPARATOR,
    VALUES_VAR,
)

DEFAULT_PATTERNS = ("test_*.py",)
RED_OUTCOMES = ("failure", "error")

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


_DYNAMIC_IMPORT_CALLEES = ("import_module", "__import__", "importorskip")


def contract_imports(paths: Sequence[Path], root: Path) -> set[str]:
    """Dotted module names the contract's own source imports.

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
    contributing nothing. An empty set is indistinguishable from "this contract
    imports nothing", which stubs nothing, which cannot refuse.
    """
    modules: set[str] = set()
    for rel in contract_files(paths, root):
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
    plugin_dump: Optional[Path] = None,
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

    `plugin_dump` is where the child writes the plugin set it loaded, and it is
    the whole capture mechanism for the freeze-time plugin baseline: the child
    is already a real pytest session running the contract, so the set comes from
    the recorder that computes it anyway rather than from a second run or a
    second describer. The caller owns the path because this function's own
    scratch directory is gone by the time it returns.

    Measured before it was relied on: `-o addopts=` above makes this child a
    different topology from a gate run (no coverage, no `-n auto`), and the two
    still load the same DISTRIBUTIONS. That is why one capture point serves
    both, and it is also why the comparison is over distributions rather than
    over raw plugin names.

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
        #
        # The PYTEST_ scrub is the gate child's, taken from the one definition
        # both share (canopus_gate.pytest_child_env). It is not tidiness either:
        # this child CAPTURES the plugin baseline the gate child is later held
        # to, so while it inherited the whole environment an exported
        # PYTEST_DISABLE_PLUGIN_AUTOLOAD froze the operator's shell into the
        # baseline and every later gate run refused. The measurement is in that
        # function's docstring.
        env = pytest_child_env(
            CANOPUS_NO_ATTEST="1", PYTHONDONTWRITEBYTECODE="1",
        )
        if plugin_dump is not None:
            env["CANOPUS_PLUGIN_DUMP"] = str(plugin_dump)
        else:
            # Never inherited. A dump path left in the environment by an outer
            # freeze would have this child overwrite a capture it knows nothing
            # about, and the null-stub run below is exactly such a child: its
            # plugin set carries the stub plugin and is not the contract's.
            env.pop("CANOPUS_PLUGIN_DUMP", None)
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


def read_plugin_dump(path: Path) -> list[str]:
    """The plugin identities the contract child recorded, or [] when it did not.

    Empty is the fail-closed answer, not a shrug: a freeze that captures no
    plugin baseline attests NOTHING afterwards, the same rule a freeze with no
    test files already gets. The callers say so on the way past, because a
    baseline that silently failed to capture would only announce itself much
    later, as an attestation nobody can explain.

    Damage is reported rather than raised for the same reason the attestation
    reader treats damage as absence: this file is a measurement, and an
    unreadable measurement is one that was not taken.
    """
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        print(f"canopus: the contract's plugin dump at {path} is unreadable: "
              f"{exc}", file=sys.stderr)
        return []
    if not isinstance(data, list) or any(not isinstance(name, str) for name in data):
        print(f"canopus: the contract's plugin dump at {path} is not a list of "
              f"plugin names", file=sys.stderr)
        return []
    return sorted(set(data))


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

    The stub set comes from the contract's own AST. Nothing the child SAYS is
    read; its JUnit report is, for the outcomes, and the distinction is the point
    rather than a caveat. An outcome is pytest's verdict on a test; the prose the
    earlier revision parsed was the contract author's.

    One escape family stays open, by construction rather than by oversight: a
    claimed module that EXISTS and whose own body raises at import time is never
    stubbed, so its test stays red for its original reason and never enters the
    intersection. The claim set is what the contract's AST named, and this is the
    price of that. The child reports what it swallowed on stderr, and
    run_pytest_report forwards it, so the operator has the thread to pull.

    `expected_population` is the REAL run's `(file, test, outcome)` triples, and
    it is optional only so the documented two-argument call keeps working. Every
    other guard below reads the two stub runs against EACH OTHER, and two runs
    truncated the same way agree; the real run is the only witness to which tests
    were supposed to be there at all. Supply it. Without it this function cannot
    tell a test that was measured and found honest from a test the stub runs
    never collected, and the quiet answer to the second is an acquittal.
    """
    modules = _passable_claims(contract_imports(paths, root))
    if not modules:
        return set()
    files = contract_files(paths, root)
    # A stub standing in for the contract's OWN package would poison collection
    # silently. A stub lands in sys.modules and is returned by every later
    # import_module even after the finder is gone, because sys.modules is read
    # before sys.meta_path; under --import-mode=importlib pytest builds each
    # collected module's parent packages through exactly that path. `__path__` is
    # refused, but `__getattr__` answers everything else, so the damage is
    # invisible rather than mock-shaped. This repository's `pythonpath = ["."]`
    # makes `tests` resolve, so the prefix filter never claims it and the case
    # cannot arise today; it arises under a different rootdir, so the refusal is
    # written now rather than left as a property of one config file.
    own_packages = {rel.split("/", 1)[0] for rel in files if "/" in rel}
    collision = own_packages & set(modules)
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
        # An ERRORED test is neither vacuous nor innocent, and this set is what
        # refuses it below rather than letting it fall through either way. The
        # reasoning is at that refusal.
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
        unproved_each.append(
            {(rel, name) for rel, name, outcome in outcomes
             if outcome in ("passed", "skipped")}
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
    # A contract file that vanished from BOTH stub runs. The guard above compares
    # the two runs with each other, and two runs that lost the same file agree,
    # so it sees nothing; this compares them with the contract. Measured: a file
    # carrying a module-scope reference to a real module's real value stops
    # collecting the moment the contract also names a child of that module,
    # because the finder then has to stub the plain module WHOLE. Both stub runs
    # lost it, both agreed, and a two-file contract whose every test asserts
    # nothing froze.
    #
    # It cannot misfire on the freeze path. `refusal_reasons` already refuses any
    # file that collected nothing in the REAL run, and the freeze runs this probe
    # only when no refusal reason fired, so every file here is one the real run
    # measured. If it fires, the stub is what lost the file.
    lost = [rel for rel in files if any(rel not in counts for counts in counts_each)]
    if lost:
        raise ContractError(
            "a contract file collected nothing under the stub, so vacuity was "
            "NOT measured for any test in it: " + ", ".join(lost) + ". The real "
            "run collected it, so the stub is what lost it: most often a "
            "module-scope statement reading a value from a module the contract "
            "also names a child of, which this probe must stub whole. Move that "
            "statement inside the test body."
        )
    if expected_population is not None:
        # The per-file guard above is not the whole of it, and this is the half
        # it cannot reach. Measured: under the stub the lost file skipped at
        # MODULE level, which xunit1 records as ONE synthetic testcase named
        # after the module, so the file still counted one item and the per-file
        # guard passed. The verdict then carried ('c/test_lost.py',
        # 'c.test_lost'), which is not a test at all and would be printed to the
        # operator as a vacuous test id that does not exist.
        #
        # Only RED tests are weighed, the same evidence rule the rest of this
        # probe follows: a test that PASSED for real never had an absent import
        # for the stub to resolve, so its absence here proves nothing.
        unmeasured = sorted(
            f"{rel}::{name}"
            for rel, name, outcome in expected_population
            if outcome in RED_OUTCOMES and (rel, name) not in populations[0]
        )
        if unmeasured:
            raise ContractError(
                "vacuity was NOT measured for tests the real run recorded red, "
                "because the stub runs never collected them: "
                + ", ".join(unmeasured)
                + ". Not measured is not proved innocent, and an intersection "
                "computed over the survivors reads exactly like a clean verdict."
            )
    # An `error` under the stub is neither vacuous nor innocent, and this is a
    # decision rather than an omission. The instrument did not measure the test:
    # the error is most often this probe's OWN stub meeting a stdlib API that
    # type-checks its argument (`open(CONFIG['path'])` with CONFIG stubbed), so
    # counting it vacuous would be a false accusation manufactured here, and a
    # false accusation teaches the operator to route around the gate. Leaving it
    # acquitted is worse: measured, a wholly vacuous contract whose two tests
    # shared such a fixture errored under both runs, `error` was in neither the
    # passed nor the skipped set, the intersection came back empty, and the
    # contract froze. The identical contract spelled with `pytest.skip` WAS
    # refused. So it is refused as unmeasurable, the same posture the population
    # guards above take, and the operator is told which tests and why.
    errored = sorted(
        f"{rel}::{name}" for rel, name in errored_each[0] | errored_each[1]
    )
    if errored:
        raise ContractError(
            "vacuity was NOT measured for tests that ERRORED under the stub, so "
            "they are neither proved vacuous nor proved to assert anything: "
            + ", ".join(errored)
            + ". An error is usually the stub itself reaching a caller that "
            "type-checks it, so it says nothing about what the test asserts. "
            "Narrow the fixture, or take the value the stub cannot stand in for "
            "inside the test body."
        )
    return unproved_each[0] & unproved_each[1]


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
