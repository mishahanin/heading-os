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

from tests.repo_files import tracked_paths  # noqa: E402

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


# The `_git_ignored` helper that stood here was a third independent copy of
# "ask git what it ignores", written the same day as two others for the same
# incident. `tests/repo_files` is the one implementation; its contract RAISES on
# a git failure rather than returning an empty ignore set, because a sweep that
# silently loses its filter is the defect this guard was rewritten for.


def _python_files():
    for path in tracked_paths(tuple(f"{top}/**/*.py" for top in _SCANNED_DIRS)):
        if not _SKIPPED_PARTS & set(path.parts):
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


def test_a_git_ignored_tree_is_not_part_of_the_repository(tmp_path):
    """An agent worktree lives under `.claude/worktrees/`, which git ignores.

    On 2026-08-29 four of them were inside the tree and this guard walked
    straight into their scratch files, so the suite failed over code that is not
    part of the repository. The filter was a hand-written list of directory
    names, and such a list can only name the places that have already caused
    trouble.
    """
    probe_dir = ROOT / ".claude" / "worktrees" / "probe-not-the-repository"
    probe = probe_dir / "spawner.py"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        "import subprocess\nsubprocess.run(['python3', 'x.py'])\n", encoding="utf-8")
    try:
        assert probe not in set(_python_files())
    finally:
        probe.unlink()
        probe_dir.rmdir()


def test_a_brand_new_untracked_test_is_still_scanned():
    """Dropping every untracked file would exempt the file being written now,
    which is the only moment this guard is useful."""
    probe = ROOT / "tests" / "test_zz_probe_untracked_spawner.py"
    probe.write_text(
        "import subprocess\nsubprocess.run(['python3', 'x.py'])\n", encoding="utf-8")
    try:
        assert probe in set(_python_files())
    finally:
        probe.unlink()


def test_the_scan_is_not_empty():
    """A filter that dropped everything would make this file pass over nothing."""
    found = list(_python_files())
    assert len(found) > 200, f"only {len(found)} python files reached the guard"
