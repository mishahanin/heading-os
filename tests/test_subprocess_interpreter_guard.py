"""Guard: a spawned Python child is launched by THIS interpreter, never a bare `python`.

Reported against a fresh clone at v0.8.0 by an external contributor: four calls in
`tests/test_x_pulse.py` spawned `["python", ...]`. On a host that ships only
`python3` those four tests die with FileNotFoundError before asserting anything.

The quieter half of the same defect is the worse half. Where a bare `python` DOES
resolve, it resolves to the ambient system interpreter, so the child runs WITHOUT
the pinned dependency set and a green run attests an environment the suite never
ran in. That is the exact failure `tests/test_capture_interpreter.py` was written
for after `capture-interpreter` recorded a plugin baseline from the wrong
interpreter; that file holds the contract for one call site in `scripts/canopus.py`.
This guard holds it for the whole tree, so the next author cannot reintroduce it in
a new file and wait for a stranger to find it.

`CLAUDE.md` states the same rule for humans -- "Invoke `.venv/bin/python`
explicitly rather than a bare `python`, so a machine-wide interpreter without the
pinned dependencies cannot silently run the suite". `sys.executable` is its
in-process form.

Scope note: this checks the FIRST element of a literal argument list, which is the
interpreter slot. A command built at runtime, or one that names a real path
(`/decoy/python` in the capture-interpreter contract), is not matched and is not
the pattern this closes.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Trees whose Python is ours to hold to the rule. `.claude` carries skill-local
# scripts and hooks that spawn children exactly like `scripts/` does.
_SCANNED_DIRS = ("tests", "scripts", ".claude")

# Never scan a checked-out dependency tree or a build artifact.
_SKIPPED_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git", "site-packages"}

# Bare interpreter names: resolved by PATH, so they mean a different interpreter on
# every host -- or none at all. `python3.11` is equally ambient, hence the version form.
_BARE_INTERPRETER = re.compile(r"^python(3(\.\d+)?)?$")

# subprocess entry points that take an argument vector.
_SPAWN_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}


def _python_files():
    for top in _SCANNED_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if _SKIPPED_PARTS & set(path.parts):
                continue
            yield path


def _first_arg_vector(call: ast.Call):
    """The literal argument vector of a spawn call, or None if it is not one."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _SPAWN_FUNCS:
        return None
    if not call.args:
        return None
    vector = call.args[0]
    if not isinstance(vector, (ast.List, ast.Tuple)) or not vector.elts:
        return None
    return vector


def _bare_interpreter_calls(tree: ast.AST):
    """Spawn calls whose interpreter slot is a bare `python` / `python3` literal."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        vector = _first_arg_vector(node)
        if vector is None:
            continue
        head = vector.elts[0]
        if (
            isinstance(head, ast.Constant)
            and isinstance(head.value, str)
            and _BARE_INTERPRETER.match(head.value)
        ):
            hits.append((node.lineno, head.value))
    return hits


def test_no_bare_python_interpreter_in_spawned_commands():
    """No subprocess vector may start with a bare `python` / `python3`."""
    violations = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _bare_interpreter_calls(tree):
            violations.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}: spawns {name!r}")

    assert not violations, (
        f"{len(violations)} subprocess call(s) launch a bare interpreter. On a host with "
        f"only `python3` these raise FileNotFoundError; where the name does resolve, the "
        f"child runs outside the pinned environment and the result proves nothing. "
        f"Use `sys.executable`:\n  " + "\n  ".join(violations)
    )


def test_the_guard_recognises_the_pattern_it_forbids():
    """The detector fires on the shape that was reported, and spares the fixed shape.

    A guard that silently matches nothing passes forever. This pins the detector
    itself against both the defect and its fix.
    """
    offending = ast.parse('subprocess.run(["python", str(PATH), "--flag"])')
    assert _bare_interpreter_calls(offending) == [(1, "python")]

    fixed = ast.parse('subprocess.run([sys.executable, str(PATH), "--flag"])')
    assert _bare_interpreter_calls(fixed) == []

    real_path = ast.parse('subprocess.run(["/decoy/python", str(PATH)])')
    assert _bare_interpreter_calls(real_path) == []
