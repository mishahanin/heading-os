"""State files these scripts persist are written atomically (tmp + os.replace).

**This is a floor, not coverage.** The list below is hand-kept, and the
2026-08-23/24 engine audit found two files it did not name — `browser.py`
(the CDP lock) and `build_engine_repo.py` (the build manifest) — each writing
JSON state with a plain `write_text`, each against the workspace's own
no-non-atomic-state-writes rule.

The obvious repair is a derived scan instead of a list, and it was measured
before being rejected: `.write_text(` whose argument reaches `json.dumps` hits
42 sites under `scripts/`, and most are build artifacts, generated reports and
one-shot exports rather than persistent state. Some are inside an atomic helper
already. A detector that cannot be made correct is worse than an honest count,
so the list stays a list and this docstring says what it does not reach.

That reasoning is about WHICH FILES the list names, and it still stands. It said
nothing about how each listed entry is checked, and that half was broken. Both
checks read the source as TEXT: one asked for `target` and `.write_text(` on the
same LINE, the other for `atomic_write_text` anywhere in the file. Measured
2026-09-01 in a scratch tree, reverting `scripts/browser.py` to a non-atomic
lock write spelled over two lines: 10 passed, byte-identical to baseline. The
line check missed it because the two tokens were on different lines, and the
helper check because the import statement at the top of the file already
contained the word. Both are now asked of the AST, the second requires a CALL,
and the detector has its own tests below so the next rewrite cannot go
unmeasured. Found by the shard-8 auditor of the 2026-08-31 tests campaign, which
named the blind spot rather than reaching outside its own twelve files.

Originally F-M4 (crm-health, offboard-exec); extended over the 2026-08-23/24
audit. (The docstring dated the `browser.py` / `build_engine_repo.py` discovery
to 2026-08-24 while the comment on those two list entries dated the same
discovery to 2026-08-23. Both cannot be right, and in a file whose whole purpose
is audit provenance an internally contradictory provenance is the defect. The
audit ran across both days, which is how the rest of the tree cites it, so both
places now say so.)
"""
import ast
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent

# (script, the identifier whose write must be atomic)
STATE_WRITERS = [
    ("scripts/crm-health.py", "people_file"),
    ("scripts/offboard-exec.py", "registry_file"),
    # Found by the 2026-08-23/24 engine audit, shards scripts-03-p2 / p3.
    ("scripts/browser.py", "lock_file"),
    ("scripts/build_engine_repo.py", "src_manifest"),
    # Found by the third defect-class fan-out, 2026-08-27. offboard-exec.py was
    # already on this list and writes the SAME file; emergency-revoke.py rewrote
    # it with a plain write_text, and it runs while an executive's access is
    # being pulled - the worst moment for the roster to parse as empty.
    ("scripts/emergency-revoke.py", "registry_file"),
]


def _mentions(node: ast.AST, name: str) -> bool:
    """Does this expression mention `name` anywhere inside it?

    Covers the three spellings a receiver actually takes: the bare name
    (`registry_file`), a call of it (`lock_file()`), and an attribute or
    subscript hanging off either.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == name:
            return True
    return False


def _tainted_names(tree: ast.AST, target: str) -> set[str]:
    """Local names that hold the target path, followed through assignment.

    Run to a fixpoint, so `a = lock_file()` then `b = a` taints both. Without
    this the detector sees only a receiver that spells the target itself, and
    one intermediate variable is all it takes to walk past it.
    """
    tainted = {target}
    for _ in range(8):          # a chain deeper than this is not real code
        grew = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            value = node.value
            if value is None:
                continue
            if not any(_mentions(value, n) for n in tainted):
                continue
            bound = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in bound:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name) and sub.id not in tainted:
                        tainted.add(sub.id)
                        grew = True
        if not grew:
            break
    return tainted


def _bare_write_text_sites(source: str, target: str, filename: str) -> list[int]:
    """Line numbers of every `<target-ish>.write_text(...)` call.

    Asked of the AST rather than of the line, which is the whole point of this
    helper. Until 2026-09-01 the check was
    `target in line and ".write_text(" in line`, so both had to appear on the
    SAME source line.

    MEASURED that day in a scratch tree, reverting `scripts/browser.py` to a
    non-atomic lock write spelled across two lines:

        _target = lock_file()
        _target.write_text(json.dumps(...) + "\\n")

    `tests/test_atomic_scripts.py` reported 10 passed, byte-identical to the
    baseline. Both of this file's tests missed it: the line check because the
    two tokens were on different lines, and the helper check because
    `"atomic_write_text" in src` was already satisfied by the import statement
    at the top of the file, which the mutation did not touch.
    """
    tree = ast.parse(source, filename=filename)
    tainted = _tainted_names(tree, target)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "write_text":
            continue
        if any(_mentions(func.value, n) for n in tainted):
            found.append(node.lineno)
    return sorted(found)


def _calls_named(source: str, name: str, filename: str) -> list[int]:
    """Line numbers where `name` is CALLED, ignoring imports and mentions."""
    tree = ast.parse(source, filename=filename)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called == name:
                out.append(node.lineno)
    return sorted(out)


@pytest.mark.parametrize("script, _target", STATE_WRITERS)
def test_the_script_uses_the_atomic_helper(script, _target):
    """CALLED, not merely imported.

    `"atomic_write_text" in src` was satisfied by the import line alone, so a
    script could import the helper, never call it, and pass. Measured on the
    `browser.py` mutation above: the import survived the change and this test
    stayed green over a non-atomic write.
    """
    path = ENGINE / script
    src = path.read_text(encoding="utf-8")
    calls = _calls_named(src, "atomic_write_text", str(path))
    assert calls, (
        f"{script} persists state but never CALLS atomic_write_text "
        f"(tmp + os.replace). An import alone is not a write; check that the "
        f"call was not replaced by a plain write_text."
    )


@pytest.mark.parametrize("script, target", STATE_WRITERS)
def test_the_state_file_has_no_bare_write_text(script, target):
    path = ENGINE / script
    src = path.read_text(encoding="utf-8")
    bare = _bare_write_text_sites(src, target, str(path))
    assert not bare, (
        f"{script} still writes {target} non-atomically, at line(s) {bare}. A "
        "crash or a concurrent read mid-write leaves truncated JSON, and every "
        "consumer of these files treats a parse failure as 'no state'."
    )


# ---------------------------------------------------------------------------
# The detector's own tests. A source-reading guard that nothing exercises is a
# guard nobody can tell is broken, and this one WAS broken for the whole of its
# life without a single red test.
# ---------------------------------------------------------------------------

_TWO_LINE_EVASION = '''
import json
from scripts.utils.atomic import atomic_write_text

def lock_file():
    return PATH

def save(port):
    _target = lock_file()
    _target.write_text(json.dumps({"port": port}))
'''

_CHAINED_EVASION = '''
import json
def lock_file():
    return PATH

def save(port):
    a = lock_file()
    b = a
    b.write_text(json.dumps({"port": port}))
'''

_ATOMIC_AND_CLEAN = '''
import json
from scripts.utils.atomic import atomic_write_text

def lock_file():
    return PATH

def save(port):
    atomic_write_text(lock_file(), json.dumps({"port": port}))
'''

_UNRELATED_WRITE = '''
def lock_file():
    return PATH

def report(out_path):
    out_path.write_text("a generated report, not state")
'''

# `b = a` is visited BEFORE `a = lock_file()`, because ast.walk is breadth-first
# and the tainting assignment sits one level deeper inside the `if`. One pass
# therefore taints `a` and never revisits `b`; only the fixpoint reaches it.
# _CHAINED_EVASION above does NOT exercise the loop: its two links are siblings
# in source order, so pass 1 already taints both. MEASURED 2026-09-01:
# `range(8)` narrowed to `range(1)` left this file green at 15 passed.
_OUT_OF_ORDER_CHAIN = '''
import json
def lock_file():
    return PATH

def save(port):
    if port:
        a = lock_file()
    b = a
    b.write_text(json.dumps({"port": port}))
'''

# The receiver is an ATTRIBUTE holding the path, not a local name. `_mentions`
# claims to cover this spelling; nothing measured it. MEASURED 2026-09-01:
# deleting the `ast.Attribute` branch of `_mentions` left this file green at
# 15 passed.
_ATTRIBUTE_RECEIVER = '''
import json
class Session:
    def save(self, port):
        self.lock_file.write_text(json.dumps({"port": port}))
'''


def test_the_detector_catches_a_write_split_across_two_lines():
    """The exact mutation that survived, as a permanent case.

    Without this, the AST rewrite above is itself unmeasured, and a later
    simplification back to a line scan would leave every test green.
    """
    assert _bare_write_text_sites(_TWO_LINE_EVASION, "lock_file", "<evasion>"), (
        "the detector missed a non-atomic write whose target and write_text "
        "sit on different lines, which is the defect it was rewritten for")


def test_the_detector_follows_a_chain_of_variables():
    assert _bare_write_text_sites(_CHAINED_EVASION, "lock_file", "<chain>"), (
        "the detector lost the target through one extra assignment")


def test_the_detector_needs_more_than_one_taint_pass():
    """The fixpoint loop, exercised.

    `_CHAINED_EVASION` reaches the same assertion but does not need the loop:
    its two links are siblings in source order, so a single pass taints both.
    This case puts the tainting assignment DEEPER in the tree than the link that
    consumes it, which breadth-first traversal visits second.
    """
    assert _bare_write_text_sites(_OUT_OF_ORDER_CHAIN, "lock_file", "<order>"), (
        "the detector lost the target through a chain whose links are not in "
        "traversal order; the fixpoint loop is what covers that")


def test_the_detector_sees_an_attribute_receiver():
    """`self.lock_file.write_text(...)` is a bare write too.

    `_mentions` says it covers "an attribute or subscript hanging off either";
    without this case that sentence was an unmeasured claim.
    """
    assert _bare_write_text_sites(_ATTRIBUTE_RECEIVER, "lock_file", "<attr>"), (
        "the detector missed a non-atomic write through an attribute receiver")


def test_the_detector_passes_a_genuinely_atomic_write():
    """Anchor against over-refusal.

    A detector that flagged everything would pass both tests above while making
    the real parametrized cases unfixable.
    """
    assert _bare_write_text_sites(_ATOMIC_AND_CLEAN, "lock_file", "<clean>") == []


def test_the_detector_ignores_a_write_to_an_unrelated_path():
    """Only the named state file is in scope.

    Generated reports and one-shot exports legitimately use write_text; the
    module docstring above explains why a tree-wide scan was rejected.
    """
    assert _bare_write_text_sites(_UNRELATED_WRITE, "lock_file", "<other>") == []


def test_the_helper_check_is_not_satisfied_by_an_import():
    """The second half of the same mutation.

    An import line mentioning the helper must not count as using it.
    """
    import_only = "from scripts.utils.atomic import atomic_write_text\n"
    assert _calls_named(import_only, "atomic_write_text", "<import>") == [], (
        "a bare import counted as a call, so a script that imports the atomic "
        "helper and never calls it passes")
    with_call = import_only + "atomic_write_text(p, 'x')\n"
    assert _calls_named(with_call, "atomic_write_text", "<call>") == [2]
