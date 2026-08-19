#!/usr/bin/env python3
"""Run a Canopus contract test set and read its shape from a JUnit report.

This module runs pytest, so nothing loaded on a PreToolUse path may import it.
That was the reason it was split out of the retired freeze primitive, and the
constraint outlives the split.

See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#canopus_contract-module
(The design record routes private and lives in the DATA overlay, never in
this repository. A public clone does not carry it; the pointers below name
the section, so the reasoning is one grep away for whoever has the overlay.)
"""
from __future__ import annotations

import ast
import json
import os
import site
import subprocess
import sys
import sysconfig
import tempfile
from fnmatch import fnmatch
from importlib.util import find_spec
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
    REPLACED_REPORT,
    REPLACE_VAR,
    STUB_NAME_SEPARATOR,
    VALUES_VAR,
    _expand_claims,
    greedy_payload,
)
# The plugin module OBJECT, alongside the names above, for one purpose:
# `replaceable_claims` has to know what the instrument itself is running out of,
# and it reads that off `__name__` rather than from a string written here. A
# spelled-out "scripts.utils.canopus_nullstub" would survive the file being moved
# or renamed, and what it would survive INTO is a probe that replaces its own
# plugin's module and cannot say why it died. Measured; see that function.
from scripts.utils import canopus_nullstub as _nullstub
# `venv_python` ONLY, never `ensure_venv`. The first is a pure path computation;
# the second re-execs the running process, and this module is imported by the
# CLI, by the gate's callers and by the suite. An import that could re-exec is a
# module that cannot be imported from a test.
from scripts.utils.venv_guard import interpreter_identity, venv_python

DEFAULT_PATTERNS = ("test_*.py",)
RED_OUTCOMES = ("failure", "error")
# The outcomes under a stub run that do NOT prove a test read the stubbed
# value; the complement of this set is the single token "failure".
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#unproved_outcomes
UNPROVED_OUTCOMES = ("passed", "skipped", "error")
# The one token `_outcome` emits for a test that was collected and NEVER RAN.
# Named because two readings depend on telling it apart from every other token
# and neither may spell it for itself: see `tests_that_never_ran`.
SKIPPED_OUTCOME = "skipped"


def pytest_child_env(**overrides: str) -> dict:
    """The environment for a pytest child this codebase launches: ours, minus PYTEST_.

    Every PYTEST_-prefixed variable is dropped, by blanket prefix rather than by
    denylist, and *overrides* are applied on top. The CANOPUS_ names are deliberately
    NOT scrubbed: CANOPUS_NO_ATTEST is how a caller tells a child what it is for, and
    it is passed in as an override by the callers that need it.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pytest_child_env
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PYTEST_")}
    env.update(overrides)
    return env


def contract_interpreter() -> Path:
    """The interpreter every Canopus child is launched with.

    The project venv's interpreter when it exists on disk, and the invoking
    `sys.executable` otherwise. Never `ensure_venv`, which re-execs: this module is
    imported by the CLI and by the suite.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#contract_interpreter
    """
    target = venv_python()
    return target if target.exists() else Path(sys.executable)


def interpreter_notice(chosen: Path, invoking: Path) -> str:
    """One line when the capture used a different interpreter, "" when it did not.

    "The same" is `venv_guard.interpreter_identity`, not a resolved-path comparison,
    and both paths are named in the sentence it returns. Pure: it prints nothing.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#interpreter_notice
    """
    if interpreter_identity(chosen) == interpreter_identity(invoking):
        return ""
    return (f"the contract child ran under {chosen}, not the {invoking} that "
            f"invoked this command; the plugin baseline describes the former")

# The most the greedy candidate's payload may carry across the process
# boundary, set below the platform's own MAX_ARG_STRLEN ceiling.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#payload_budget
PAYLOAD_BUDGET = 96 * 1024

# The only two exits a probe run can be READ from: 0 is all green, 1 is
# tests failed. Every other exit measured something else.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#probe_returncodes
PROBE_RETURNCODES = (0, 1)


class ContractError(Exception):
    """The contract could not be run at all."""


def contract_files(
    paths: Sequence[Path],
    root: Path,
    patterns: Sequence[str] = DEFAULT_PATTERNS,
) -> list[str]:
    """Every test module under *paths*, as sorted root-relative POSIX strings.

    *patterns* defaults to `test_*.py` and must agree with pytest's `python_files`.
    Symlinks are excluded: the workspace forbids them and a symlinked contract file
    could point outside the tree. A member that resolves OUTSIDE *root* raises
    ContractError naming that root.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#contract_files
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

    The return shape of `contract_files`, plus the immediate-parent conftest of each
    path named as a FILE; a path named as a directory is walked whole.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#contract_source_files
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

    Source means `contract_source_files`, so conftests are read alongside the test
    modules, and the names come from the AST: `import`, `from ... import`, and the
    string literals passed to `import_module`, `__import__` and `importorskip`.
    Relative imports are skipped; a name computed at run time is not read at all, and
    this reader fails OPEN there. It over-reports by design, so a member is not
    guaranteed to be an importable name. Raises ContractError on a file it cannot
    parse or read as UTF-8; an empty set is a real answer.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#contract_imports
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

    The union of every `str` constant in `contract_source_files` and the names
    `contract_imports` reads. Over-reports on purpose. Raises ContractError on a file
    that will not parse; an empty set is a real answer a caller must be able to tell
    apart from a file it could not read.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#contract_literals
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


_SKIP_MARKER_NAMES = ("skip", "skipif", "xfail")


def _skip_marker_name(node: ast.expr) -> Optional[str]:
    """The marker family a decorator or an assigned value names, or None.

    Matches `<anything>.mark.<family>` and a bare `mark.<family>`, called or not, for
    family in `skip`, `skipif`, `xfail`. Matched by name over the attribute chain
    rather than by resolving the object, so it over-reports.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_skip_marker_name
    """
    base = node.func if isinstance(node, ast.Call) else node
    if not isinstance(base, ast.Attribute) or base.attr not in _SKIP_MARKER_NAMES:
        return None
    mark = base.value
    if isinstance(mark, ast.Attribute):
        return base.attr if mark.attr == "mark" else None
    if isinstance(mark, ast.Name) and mark.id == "mark":
        return base.attr
    return None


def _skip_states_reason(node: ast.expr, marker: str) -> bool:
    """Whether *node* (the decorator, or the value `pytestmark` was assigned) documents a reason.

    A non-empty `reason=` string constant states a reason for every member of the
    family. The first POSITIONAL string constant states one for `skip` ALONE, since
    `skipif` and `xfail` take a condition there. A reason that is not a string
    constant is read as stating one (fail open). The bare, uncalled form states none.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_skip_states_reason
    """
    if not isinstance(node, ast.Call):
        return False
    for kw in node.keywords:
        if kw.arg == "reason":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value != ""
            return True  # not a string constant: fail open, treat as reasoned
    if marker == "skip" and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value != ""
        return True  # fail open, same rule as the keyword branch above
    return False


def _unreasoned_pytestmark(statements: Sequence[ast.stmt]) -> bool:
    """Whether a `pytestmark` among *statements* carries an undocumented skip marker.

    Reads the statements it is HANDED and never walks below them, so a caller passes
    `tree.body` for a module and `node.body` for a class. The list form
    `pytestmark = [pytest.mark.skip, ...]` is accepted, each element checked.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_unreasoned_pytestmark
    """
    for stmt in statements:
        if not (
            isinstance(stmt, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in stmt.targets
            )
        ):
            continue
        values = (
            stmt.value.elts if isinstance(stmt.value, ast.List) else [stmt.value]
        )
        for value in values:
            marker = _skip_marker_name(value)
            if marker and not _skip_states_reason(value, marker):
                return True
    return False


def skip_markers_without_reason(paths: Sequence[Path], root: Path) -> list[str]:
    """SORTED names of tests carrying a skip-family marker that states no reason.

    Four shapes are read from the contract's own source: a decorator on a function,
    a decorator on a class (named by the class), and `pytestmark` at module scope
    (named `"<module>"`) or in a class body. A runtime `pytest.skip()` and a
    module-scope `pytest.importorskip()` are NOT read. Raises ContractError on a
    contract file that will not parse.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#skip_markers_without_reason
    """
    names: set[str] = set()
    for rel in contract_source_files(paths, root):
        path = Path(root) / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as exc:
            raise ContractError(
                f"the contract file {rel} could not be parsed, so the skip "
                f"markers it carries could not be read: {exc}"
            ) from exc
        if _unreasoned_pytestmark(tree.body):
            names.add("<module>")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _unreasoned_pytestmark(node.body):
                names.add(node.name)
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            for decorator in node.decorator_list:
                marker = _skip_marker_name(decorator)
                if marker and not _skip_states_reason(decorator, marker):
                    names.add(node.name)
                    break
    return sorted(names)


def passable_literals(literals) -> list[str]:
    """The literals that can survive the trip to the child, sorted.

    Drops any literal carrying a NUL, then keeps the SHORTEST literals that fit
    PAYLOAD_BUDGET and drops the rest, printing one note on stderr when it does.
    Never raises: dropping can only make the greedy candidate satisfy less.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#passable_literals
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


def _library_roots() -> frozenset:
    """Where this interpreter keeps code that is not the tree's own.

    The stdlib, the platform stdlib and both site-packages directories, read off
    `sysconfig` and `site`; a getter that is absent or raises contributes nothing.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_library_roots
    """
    roots: set[Path] = set()
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = paths.get(key)
        if value:
            roots.add(Path(value).resolve())
    for getter in (site.getsitepackages, site.getusersitepackages):
        try:
            value = getter()
        except (AttributeError, OSError, TypeError):
            continue
        for entry in ([value] if isinstance(value, str) else list(value)):
            roots.add(Path(entry).resolve())
    return frozenset(roots)


_LIBRARY_ROOTS = _library_roots()


def _resolves_inside(name: str, root: Path) -> Optional[bool]:
    """True in the tree, False elsewhere, None when the name does not resolve here.

    `built-in` and `frozen` answer False, not None. Resolution happens in THIS
    process under the parent's `sys.path`, and `find_spec` executes ancestor
    packages, so both `Exception` and `SystemExit` are caught and answered None.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_resolves_inside
    """
    try:
        spec = find_spec(name)
    except (Exception, SystemExit):  # noqa: BLE001 - reported by the caller
        return None
    if spec is None:
        return None
    origin = spec.origin
    if origin in ("built-in", "frozen"):
        return False
    if origin is None:
        locations = list(spec.submodule_search_locations or [])
        if not locations:
            return False
        target = Path(locations[0])
    else:
        target = Path(origin)
    try:
        target = target.resolve()
    except OSError:
        return None
    if not target.is_relative_to(Path(root).resolve()):
        return False
    return not any(target.is_relative_to(library) for library in _LIBRARY_ROOTS)


def replaceable_claims(names: Sequence[str], root: Path) -> list[str]:
    """The claims a REPLACING candidate may install on, sorted.

    Drops a name that resolves outside *root*, or inside this interpreter's stdlib or
    site-packages, and drops any claim this probe's own plugin module lies under.
    Each drop RULE prints one line on stderr. Applies to replacement only: the
    absent-name path keeps every claim.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#replaceable_claims
    """
    instrument = _nullstub.__name__
    kept: list[str] = []
    foreign: list[str] = []
    swept: list[str] = []
    for name in sorted(set(names)):
        if instrument == name or instrument.startswith(f"{name}."):
            swept.append(name)
        elif _resolves_inside(name, root) is False:
            foreign.append(name)
        else:
            kept.append(name)
    if foreign:
        print(
            "canopus: not replacing " + ", ".join(foreign) + ": each resolves "
            "outside the tree being probed, or inside this interpreter's own "
            "stdlib or site-packages, so none of them is the code under test. "
            "Replacing one rewrites a module the pytest child is standing on, "
            "which ends the run rather than measuring it.",
            file=sys.stderr,
        )
    if swept:
        print(
            "canopus: not replacing under the claim(s) " + ", ".join(swept)
            + f": this probe's own plugin module ({instrument}) lies under "
            "them, and a claim reaches every name below it, so the candidates "
            "would replace the instrument taking the measurement. Name the "
            "subject's own module rather than its parent package.",
            file=sys.stderr,
        )
    return kept


def _replaced_by_the_child(notes: Sequence[str]) -> Optional[set[str]]:
    """The module names one candidate child says it replaced, or None if it said nothing.

    None is NOT an empty set: a child that replaced nothing reports an empty line,
    and a child that never reported at all cannot be spoken for. The last matching
    line in *notes* wins.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_replaced_by_the_child
    """
    prefix = f"{NULLSTUB_STDERR_MARKER} {REPLACED_REPORT}"
    reported = None
    for line in notes:
        if line.startswith(prefix):
            reported = {
                name for name in line[len(prefix):].strip().split(",") if name
            }
    return reported


def _was_replaced(claim: str, replaced: Sequence[str]) -> bool:
    """Whether anything the child replaced lies AT or under *claim*.

    Mirrors `_NamedFinder._claims` in the child: the claim `pkg` is armed for
    `pkg.sub` as well.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_was_replaced
    """
    return any(
        name == claim or name.startswith(f"{claim}.") for name in replaced
    )


def run_pass_candidates(
    paths: Sequence[Path],
    root: Path,
    *,
    timeout: int = 900,
    expected_population: Optional[Sequence[tuple[str, str, str]]] = None,
    replace_existing: bool = False,
    outcomes_out: Optional[dict[str, list[tuple[str, str, str]]]] = None,
    claims_out: Optional[dict[str, list[str]]] = None,
) -> dict[str, set[tuple[str, str]]]:
    """For each candidate, the (file, test) pairs it turned green.

    One pytest session per entry in CANDIDATES, plus an unstubbed baseline unless
    *expected_population* supplies the real run's (file, test, outcome) triples.
    *replace_existing* answers the names a module HAS as well as the ones it lacks.
    *outcomes_out* and *claims_out*, when supplied, are FILLED with each candidate's
    report triples and with `claimed`/`dropped`. Raises ContractError when nothing
    could be stood in for, or when no candidate child replaced anything.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pass_candidates
    """
    modules = _passable_claims(contract_imports(paths, root))
    dropped: list[str] = []
    if replace_existing:
        # Narrowed ONLY here, never on the absent-name path. A claim that costs
        # nothing when it merely supplies a missing name destroys a live module
        # when it replaces the names that module has, and some of those modules
        # are what the running pytest child is standing on. The measurement, and
        # both drop rules, are in `replaceable_claims`.
        narrowed = replaceable_claims(modules, root)
        if modules and not narrowed:
            raise ContractError(
                "every module this contract's source names was dropped from the "
                "replacing claim set, so no wrong implementation was put in "
                "front of it: " + ", ".join(modules) + ". A replacing candidate "
                "may only stand in for the tree's own code, and not for the "
                "package this probe's own plugin lives under. The reason for "
                "each drop is on stderr above."
            )
        dropped = [name for name in modules if name not in set(narrowed)]
        modules = narrowed
    if claims_out is not None:
        claims_out["claimed"] = list(modules)
        claims_out["dropped"] = dropped
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
        REPLACE_VAR: "1" if replace_existing else "",
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
    stood_in_for: list[set[str]] = []
    for name in CANDIDATES:
        notes: list[str] = []
        xml_text = run_pytest_report(
            paths, root, timeout=timeout,
            extra_env={**base_env, CANDIDATE_VAR: name},
            extra_args=("-p", "scripts.utils.canopus_nullstub"),
            allowed_returncodes=PROBE_RETURNCODES,
            notes_out=notes,
        )
        if replace_existing:
            reported = _replaced_by_the_child(notes)
            if reported is None:
                raise ContractError(
                    f"the {name} pass-candidate did not report which modules it "
                    f"replaced, so this run cannot say what any wrong "
                    f"implementation stood in for. Its claim set is what it was "
                    f"ARMED for, and printing that as what happened is how a "
                    f"page comes to name a module nothing touched."
                )
            stood_in_for.append(reported)
        _counts, outcomes = parse_junit(xml_text)
        collected = {(rel, test) for rel, test, _o in outcomes}
        if outcomes_out is not None:
            outcomes_out[name] = list(outcomes)
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
    if replace_existing:
        # What the children REPLACED, in place of what they were armed for: the
        # intersection, computed whether or not the caller wants `claims_out`.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pass_candidates-replaced-intersection
        replaced = set.intersection(*stood_in_for)
        claimed = [name for name in modules if _was_replaced(name, replaced)]
        untouched = [name for name in modules if name not in set(claimed)]
        if untouched:
            print(
                "canopus: not replacing " + ", ".join(untouched)
                + ": no candidate child ever imported them, so nothing stood in "
                "for them and no test below was measured against a wrong "
                "implementation of them. A claim reaches only the names an "
                "import reaches; a module read exclusively inside a skipped "
                "test is the ordinary way this happens.",
                file=sys.stderr,
            )
        if not claimed:
            # The third route to "nothing was stood in for", decided by what the
            # children actually did rather than by the claim set.
            #
            # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pass_candidates-nothing-replaced-refusal
            raise ContractError(
                "no candidate child replaced anything, so no wrong "
                "implementation was ever put in front of any test and this run "
                "measured NOTHING: every test that stayed green stayed green "
                "against the real code. The modules armed for replacement were "
                + ", ".join(modules) + ", and no import in the run reached any "
                "of them. Most often the subject is imported only inside a test "
                "that skipped, or is reached only through a package prefix the "
                "narrowing dropped; import it by its own name inside a test "
                "that runs."
            )
        if claims_out is not None:
            claims_out["claimed"] = claimed
            claims_out["dropped"] = sorted(dropped + untouched)
    return taken


# What each candidate DID, and what the contract has to gain to survive it. One
# sentence per candidate, and the refusal prints exactly one of them: naming the
# candidate is only useful if the reader is spared the other two.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_candidate_cure
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

    Whole-contract: one reason naming the single candidate that took every test the
    real run recorded RED, and nothing at all when that red set is empty. Only red
    tests are weighed.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#pass_candidate_refusal
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


def tests_that_never_ran(
    outcomes: Sequence[tuple[str, str, str]],
) -> list[tuple[str, str]]:
    """The (file, test) pairs whose EVERY row in the report is `skipped`, sorted.

    A pair carrying a skipped row AND a row that is not skipped counts as having RUN.
    Read from the outcome tokens the caller already holds.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#tests_that_never_ran
    """
    ran = {
        (rel, name) for rel, name, outcome in outcomes
        if outcome != SKIPPED_OUTCOME
    }
    return sorted(
        {(rel, name) for rel, name, _outcome in outcomes} - ran
    )


def tests_the_candidates_never_ran(
    candidate_outcomes: dict[str, Sequence[tuple[str, str, str]]],
) -> list[tuple[str, str]]:
    """The pairs a candidate SKIPPED and no candidate ever recorded red, sorted.

    ONE candidate skipping is enough to name a pair; a pair any candidate recorded
    RED is never named. Read from the outcome tokens the caller already holds.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#tests_the_candidates_never_ran
    """
    parked: set[tuple[str, str]] = set()
    red: set[tuple[str, str]] = set()
    for outcomes in candidate_outcomes.values():
        parked.update(tests_that_never_ran(outcomes))
        red.update(
            (rel, name) for rel, name, outcome in outcomes
            if outcome in RED_OUTCOMES
        )
    return sorted(parked - red)


def verification_gaps(
    outcomes: Sequence[tuple[str, str, str]],
    taken: dict[str, set[tuple[str, str]]],
    candidate_outcomes: Optional[
        dict[str, Sequence[tuple[str, str, str]]]
    ] = None,
) -> list[tuple[str, str]]:
    """The tests that stayed green under EVERY candidate, sorted.

    A name here is NOT a bad test: the only claim is that it could not tell right
    from wrong under these three wrongnesses. Tests that never ran leave the
    population first. With *candidate_outcomes* a pair counts as measured when EVERY
    candidate collected it; without it, the weaker union of the passing sets is used.
    Raises ContractError on an empty *taken*, an empty population, or any pair not
    known to have been measured.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#verification_gaps
    """
    if not taken:
        raise ContractError(
            "no candidate was run, so nothing was put in front of these tests "
            "and no test can be called green under every candidate: an answer "
            "computed over no candidates names every test in the population, "
            "which is the shape of a completed measurement and the content of "
            "none"
        )
    # The tests that RAN, and no others: a skipped test is green under every
    # candidate in the same empty way an uncollected one is.
    #
    # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#verification_gaps-skipped-tests
    never_ran = set(tests_that_never_ran(outcomes))
    # The same rule, applied to what the CANDIDATES did; the real run cannot
    # see this shape at all.
    #
    # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#verification_gaps-candidate-skips
    if candidate_outcomes is not None:
        never_ran |= set(tests_the_candidates_never_ran(candidate_outcomes))
    population = sorted(
        {(rel, name) for rel, name, _outcome_token in outcomes} - never_ran
    )
    if not population:
        raise ContractError(
            "every test here was skipped, either in the real run or under the "
            "candidates, so nothing was measured and there is no gap reading to "
            "make: an answer computed over no tests that ran names "
            "none of them as a survivor, which is the shape of a completed "
            "measurement and the content of none. The tests that never ran: "
            + ", ".join(f"{rel}::{name}" for rel, name in sorted(never_ran))
        )
    # `is not None`, never truthiness: supplied-and-empty reports that no
    # candidate collected anything, which is not the same as not supplied.
    #
    # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#verification_gaps-candidate_outcomes-is-not-none
    if candidate_outcomes is None:
        measured = set().union(*(set(pairs) for pairs in taken.values()))
    elif not candidate_outcomes:
        measured = set()
    else:
        measured = set.intersection(*(
            {(rel, name) for rel, name, _outcome_token in rows}
            for rows in candidate_outcomes.values()
        ))
    unmeasured = [pair for pair in population if pair not in measured]
    if unmeasured:
        raise ContractError(
            # NOT "were never put in front of a wrong implementation": under the
            # two-argument form a test red under all three reads the same way.
            #
            # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#verification_gaps-unmeasured-wording
            "these tests are not known to have been put in front of a wrong "
            "implementation, so the gap reading cannot speak for them and "
            "calling them clear would name the one test nobody measured as the "
            "one test that is fine: "
            + ", ".join(f"{rel}::{name}" for rel, name in unmeasured)
            + ". Most often a candidate run never collected them, which the "
            "candidate probe reports on stderr; a module-scope statement "
            "reading a value the candidates stand in for is the usual cause."
        )
    return [
        pair for pair in population
        if all(pair in taken[candidate] for candidate in taken)
    ]


def _outcome(case: ElementTree.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return "failure" if tag == "failure" else tag
    return "passed"


def _is_collection_failure(case: ElementTree.Element) -> bool:
    """True when this entry stands for a module that never collected at all.

    Under xunit1 pytest tags such an entry `message="collection failure"`, and a
    genuine setup or call error never carries that string.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_is_collection_failure
    """
    error = case.find("error")
    return error is not None and error.get("message") == "collection failure"


def _qualified_name(rel: str, classname: str, name: str) -> str:
    """The test's name, carrying its class chain when it has one.

    Under xunit1 `name` is the bare method name and `classname` holds the module's
    dotted path plus any class chain; the WHOLE tail becomes the prefix. A classname
    that does not begin with the module's own dotted path is left alone.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_qualified_name
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

    Raises ContractError for a report carrying a DOCTYPE, which pytest never writes,
    and for one that will not parse. Every reader of a report goes through here.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_parse_report
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

    Counts only testcases carrying a `file` attribute, which requires
    `junit_family=xunit1` (see `run_pytest_report`), and skips collection-failure
    entries so a module that failed to import lands at zero. Test names carry their
    class chain, per `_qualified_name`.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#parse_junit
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
    notes_out: Optional[list[str]] = None,
) -> str:
    """Run pytest over *paths* once and return the raw JUnit XML.

    *extra_env* is merged over os.environ, so a daemon's trace id still reaches the
    child; *extra_args* are appended to a flag set that is load-bearing whole
    (`-o addopts=`, `--import-mode=importlib`, `-o junit_family=xunit1`,
    `--continue-on-collection-errors`). Child stderr lines carrying
    NULLSTUB_STDERR_MARKER are forwarded, and copied into *notes_out* when given. The
    exit code is ignored unless *allowed_returncodes* names the ones a caller can
    read. Raises ContractError when no report was written, or on a refused exit.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pytest_report
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
        # PYTHONDONTWRITEBYTECODE keeps a __pycache__ out of the frozen contract
        # tree; the PYTEST_ scrub is the one every pytest child here shares.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pytest_report-child-environment
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
        # The child's stub diagnostics, forwarded rather than dropped and scoped to
        # the marker. One cause reports once per stub child, which is expected.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_pytest_report-forwarded-stderr
        for line in (proc.stderr or "").splitlines():
            if line.startswith(NULLSTUB_STDERR_MARKER):
                print(line, file=sys.stderr)
                if notes_out is not None:
                    notes_out.append(line)
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
    skipped_without_reason: Sequence[str] = (),
) -> list[str]:
    """Why this contract cannot be frozen. Empty means it can.

    Three conditions: a file that collected nothing (one reason per file), no red
    test anywhere in *outcomes*, and a non-empty *skipped_without_reason* (one reason
    for all of them). Redness is required of the SET, not of each test. *green_ok*
    waives the redness condition and nothing else.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#refusal_reasons
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
    if skipped_without_reason:
        reasons.append(
            "skip marker states no reason, so the test it covers cannot be told "
            "apart from one that ran and passed: the suite reports green either "
            "way, and a test that never ran is not evidence of anything. Name a "
            "reason, or replace the marker with the assertion it stands in for: "
            + ", ".join(skipped_without_reason)
        )
    return reasons


# The two characters a claim may not contain: the wire separator, and a NUL,
# which cannot cross the process boundary at all.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_unpassable_in_a_claim
_UNPASSABLE_IN_A_CLAIM = (STUB_NAME_SEPARATOR, "\x00")


def _passable_claims(collected: set[str]) -> list[str]:
    """The collected strings that can survive the trip to the child, sorted.

    Drops any name carrying STUB_NAME_SEPARATOR or a NUL: `contract_imports`
    over-reports, so some of what it returns was never a module name. Each drop is
    reported on stderr, because a claim silently removed is a verdict silently
    widened.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_passable_claims
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

    Equal to the counts `parse_junit` returns, so a caller-supplied population and
    this module's own baseline run are read the same way.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#_counts_by_file
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

    Each one is proved to assert nothing about the code under test: it passed while
    the implementation was absent, and its outcome did not change when the stub's
    values changed. Costs three pytest sessions per verdict, two when
    *expected_population* supplies the real run's triples, and *timeout* is PER
    CHILD. Raises ContractError when nothing could be stubbed, when the two stub runs
    collected different tests, or when they lost a test the real run recorded red.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub
    """
    modules = _passable_claims(contract_imports(paths, root))
    if not modules:
        # Nothing was stubbed, so nothing was measured, and an empty verdict from
        # that state is not evidence.
        #
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub-empty-claim-set
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
    # silently. Intersected against the EXPANDED claim set, not the literal one.
    #
    # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub-own-package-collision
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
        # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub-unproved-outcomes
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
    # What the stub runs LOST relative to the real run, compared over the real
    # run's RED tests BY NAME rather than over raw per-file counts.
    #
    # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub-lost-tests
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


# One advisory used to stand here, and it is DELETED rather than left unused.
#
# See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#run_null_stub-deleted-advisory

_IMPORT_MARKERS = ("ModuleNotFoundError", "ImportError")


def parse_failure_modes(xml_text: str) -> dict[tuple[str, str], str]:
    """How each failing test failed: "import", "assertion", or "other".

    A heuristic over the failure message, never an input to a refusal, and labelled
    as one wherever it is printed. When a testcase carries both a `failure` and an
    `error` child, the LAST one wins.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#parse_failure_modes
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
                # The MESSAGE, never the body: the body carries the test's source and its
                # docstring, so the bare word "assert" in prose mislabelled the failure.
                #
                # See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#parse_failure_modes-message-not-body
                modes[(Path(rel).as_posix(), name)] = "assertion"
            else:
                modes[(Path(rel).as_posix(), name)] = "other"
    return modes


def vacuity_refusal(
    outcomes: Sequence[tuple[str, str, str]],
    vacuous: set[tuple[str, str]],
) -> list[str]:
    """The one refusal the null-stub probe raises: every RED test is vacuous.

    Whole-contract: one reason, or none. Only tests the real run recorded RED are
    weighed, and an empty red set returns nothing. Partial vacuity is the caller's to
    print rather than this function's to refuse, and the text returned names the
    other readings the probe cannot tell apart.

    See .heading-os-data/docs/superpowers/specs/2026-08-20-canopus-contract-probe-design.md#vacuity_refusal
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
