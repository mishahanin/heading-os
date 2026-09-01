#!/usr/bin/env python3
"""A Stop hook that crashed on one byte of its own child's stderr.

`scripts/turn-check.py` was fixed on 2026-09-01: four `subprocess.run(...,
text=True)` calls with no `errors=` got one, because a byte that is not UTF-8
raises `UnicodeDecodeError` out of `subprocess.run` before the caller sees any
output. That error is a `ValueError`. It is NOT a `subprocess.SubprocessError`
and NOT an `OSError`, so the usual handler walks straight past it.

The fix landed in the CHECKER and not in the WRAPPER. `.claude/hooks/
turn-check.py` is a separate 261-line file that the harness invokes on every
Stop; it SPAWNS the 852-line checker and decodes its output. Its own call had
the same defect and its handler reads `except (OSError,
subprocess.TimeoutExpired)`.

MEASURED 2026-09-01, wrapper in a scratch tree, checker replaced by a stub that
writes `b"Traceback: bad path caf\\xe9\\n"` to stderr and `{}` to stdout:

    before   exit 1, uncaught UnicodeDecodeError traceback from
             subprocess.run -> communicate -> _translate_newlines
    after    the wrapper reads the stub's verdict and exits normally

This is the eighteenth confirmed instance of "a fix that landed in one of N
copies" in this campaign, and the fifth in the coordinator's own work. Fixing
the inner file and not looking for an outer one is the whole shape.

## Why only three sites and not seven

An AST sweep of `.claude/hooks/*.py` finds SEVEN calls that decode and capture
with no `errors=`. Four of them are fine, and the difference is the handler,
not the call:

| site | handler | verdict |
|---|---|---|
| `checkpoint-precompact.py` | `(OSError, TimeoutExpired, ValueError)` | safe, ValueError catches it |
| `recall-inject.py` | `except Exception` | safe |
| `session-start.py` (crm-health) | `except Exception` | safe |
| `sync-docs.py` | `except Exception` | safe |
| `checkpoint-statusline.py` | `(SubprocessError, FileNotFoundError, OSError)` | UNSAFE |
| `session-start.py` (wizard status) | `(TimeoutExpired, JSONDecodeError, OSError)` | UNSAFE |
| `.claude/hooks/turn-check.py` | `(OSError, TimeoutExpired)` | UNSAFE |

`json.JSONDecodeError` subclasses `ValueError`, but `UnicodeDecodeError` is its
SIBLING, not its child, so the wizard-status handler does not catch it either.

The structural test at the bottom therefore asks the composed question - has an
`errors=`, OR sits under a handler that can catch a `ValueError` - rather than
counting `errors=` keywords. A guard that asked only about the keyword would
demand four pointless edits and would still miss a future call whose handler is
narrow.

## Why `errors="replace"` and not a wider handler

Widening the handler turns a crash into a discarded result. `errors="replace"`
keeps the output and substitutes the one bad byte, so the hook still does its
job. On valid UTF-8 the two are byte-identical, measured: `b"ok \\xff done"`
decodes to `'ok � done'` with `errors="replace"` and raises without it,
while any all-ASCII payload is unchanged either way.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
WRAPPER = HOOKS / "turn-check.py"

BAD = b"Traceback: bad path caf\xe9\n"


# ---------------------------------------------------------------------------
# 1 - the Stop hook wrapper, end to end
# ---------------------------------------------------------------------------

def _wrapper_tree(tmp_path: Path, stub_body: str) -> Path:
    """A scratch workspace holding the real wrapper and a stub checker.

    The wrapper resolves `CHECKER` as `<its own parent.parent.parent>/scripts/
    turn-check.py`, so placing it at `<ws>/.claude/hooks/` makes it spawn
    `<ws>/scripts/turn-check.py`. The real repository is never touched.
    """
    ws = tmp_path / "ws"
    (ws / ".claude" / "hooks").mkdir(parents=True)
    (ws / "scripts").mkdir(parents=True)
    (ws / ".claude" / "hooks" / "turn-check.py").write_bytes(WRAPPER.read_bytes())
    (ws / "scripts" / "turn-check.py").write_text(stub_body, encoding="utf-8")
    return ws


def _run_wrapper(ws: Path) -> subprocess.CompletedProcess:
    # BINARY on purpose. This test is about a decode failure, so decoding the
    # subject's own output here would reintroduce the defect into the test.
    return subprocess.run(
        [sys.executable, str(ws / ".claude" / "hooks" / "turn-check.py")],
        input=b"{}", capture_output=True, cwd=str(ws), timeout=180,
        env=dict(os.environ, HEADING_OS_DATA=str(ws / "data")))


_STUB_BAD_STDERR = (
    "import sys\n"
    "sys.stderr.buffer.write(b'Traceback: bad path caf\\xe9\\n')\n"
    "sys.stdout.write('{}\\n')\n"
)

_STUB_BAD_STDOUT = (
    "import sys\n"
    "sys.stdout.buffer.write(b'noise caf\\xe9\\n')\n"
    "sys.stdout.write('{}\\n')\n"
)

_STUB_CLEAN = (
    "import sys\n"
    "sys.stdout.write('{}\\n')\n"
)


@pytest.mark.slow
@pytest.mark.parametrize("stub,where", [(_STUB_BAD_STDERR, "stderr"),
                                        (_STUB_BAD_STDOUT, "stdout")])
def test_the_stop_hook_survives_a_byte_that_is_not_utf8(tmp_path, stub, where):
    """The headline. Before the fix this raised out of `subprocess.run`."""
    ws = _wrapper_tree(tmp_path, stub)

    proc = _run_wrapper(ws)
    err = proc.stderr.decode("utf-8", "replace")

    assert "UnicodeDecodeError" not in err, (
        f"the Stop hook died decoding its own checker's {where}. Every turn "
        f"that produced such a byte would end in a hook traceback:\n{err}")
    assert "Traceback (most recent call last)" not in err, (
        f"the Stop hook raised something uncaught:\n{err}")


@pytest.mark.slow
def test_a_clean_checker_still_gets_through_the_wrapper(tmp_path):
    """The anchor against over-refusal.

    A "fix" that swallowed every child result would satisfy the two cases above
    while making the Stop hook useless. This asserts the ordinary path is
    unchanged: no traceback, and nothing complaining about the checker.
    """
    ws = _wrapper_tree(tmp_path, _STUB_CLEAN)

    proc = _run_wrapper(ws)
    err = proc.stderr.decode("utf-8", "replace")

    assert "Traceback (most recent call last)" not in err, (
        f"a healthy checker made the wrapper raise:\n{err}")
    assert "unavailable" not in err and "could not be run" not in err, (
        f"a healthy checker was reported as a failure:\n{err}")


# ---------------------------------------------------------------------------
# 2 - the two in-process readers, driven by making the decode fail
# ---------------------------------------------------------------------------

def _load(name: str):
    """Import a hook by PATH under a private module name.

    Hooks are not importable as a package (`turn-check.py` is not an
    identifier) and binding a plain name here would shadow a real module for
    every later test in the worker.
    """
    import importlib.util
    path = HOOKS / name
    spec = importlib.util.spec_from_file_location(
        f"_hookprobe_{name.replace('-', '_').replace('.py', '')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A REAL child, never a stub that raises. The first draft of these two tests
# patched `subprocess.run` with a function that raised `UnicodeDecodeError`
# unconditionally, and they stayed RED after the fix landed. That was correct of
# them and wrong of me: the fix does not make the caller CATCH the error, it
# stops `subprocess.run` from raising at all. A stub that raises regardless of
# `errors=` measures the handler, which is not the thing that changed.


def test_the_statusline_branch_reader_survives_a_branch_name_that_is_not_utf8(
        tmp_path):
    """`git_branch` runs on every status render, so this is every render.

    Its handler is `(SubprocessError, FileNotFoundError, OSError)`, none of
    which is a `ValueError`. Git stores a ref name as bytes and prints it back
    verbatim, so this input is a real one, not a contrivance.

    No commit is made anywhere. `git init -b <name>` is enough for
    `git branch --show-current` to print the name, and this test must not run
    `git commit` even in a scratch tree: the workspace's release gate refuses
    that verb outright, which is the correct posture and not something to work
    around.
    """
    mod = _load("checkpoint-statusline.py")
    repo = tmp_path / "repo"
    repo.mkdir()
    # BYTES in the argv list, not a str. Python encodes a str argument as UTF-8,
    # so passing "cafébr" hands git the two valid bytes 0xC3 0xA9 and the branch
    # name that comes back decodes cleanly. The first draft of this test did
    # exactly that and stayed green against the UNFIXED hook, which is a guard
    # measuring nothing. Verified with `git branch --show-current` returning
    # b'caf\xe9br\n' after this call.
    subprocess.run([b"git", b"init", b"-q", b"-b", b"caf\xe9br", b"."],
                   cwd=str(repo), capture_output=True, timeout=30)

    branch = mod.git_branch(repo)

    assert isinstance(branch, str), (
        "git_branch did not return a string for a branch name carrying a byte "
        "that is not UTF-8; before the fix it raised UnicodeDecodeError out of "
        "subprocess.run, which none of its three handlers catches")
    assert branch, (
        f"git_branch degraded to the empty string. The byte is recoverable "
        f"with errors=replace and the operator should still see which branch "
        f"they are on: {branch!r}")


def test_the_statusline_branch_reader_still_reports_a_real_branch():
    """Anchor: returning "" unconditionally would pass the test above."""
    mod = _load("checkpoint-statusline.py")

    branch = mod.git_branch(ROOT)

    assert branch, (
        "git_branch returned nothing for the real repository, so the test "
        "above is passing over a function that always fails")


def _wizard_probe(tmp_path: Path, monkeypatch, body: str):
    """Run `_setup_wizard_banner` against a real stub `--status` script.

    Four preconditions, each of which returns EARLY and would make this probe
    measure nothing. The first draft omitted the identity file and the function
    returned at its fourth line, never reaching the subprocess at all, so the
    unfixed hook looked healthy:

      * `CI` unset and `HEADING_OS_WIZARD_QUIET` unset, or it returns at once;
      * `.workspace-identity.json` PRESENT, since an absent one is the
        documented legacy `ceo-master` default and is suppressed;
      * its `type` NOT `ceo-master`, suppressed for the same reason;
      * `scripts/apply-wizard-answers.py` present, or there is nothing to spawn.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("HEADING_OS_WIZARD_QUIET", raising=False)
    mod = _load("session-start.py")
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"type": "exec-workspace"}), encoding="utf-8")
    apply_script = tmp_path / "scripts" / "apply-wizard-answers.py"
    apply_script.parent.mkdir(parents=True, exist_ok=True)
    apply_script.write_text(body, encoding="utf-8")
    return mod._setup_wizard_banner(tmp_path)


def test_the_wizard_status_reader_survives_a_byte_that_is_not_utf8(
        tmp_path, monkeypatch, capsys):
    """`session-start` asks the setup wizard for its status on every session.

    Handler is `(TimeoutExpired, JSONDecodeError, OSError)`. `JSONDecodeError`
    IS a `ValueError`, which reads as if the decode case were already covered.
    It is not: `UnicodeDecodeError` is its SIBLING, not its subclass.

    MEASURED against the pre-fix file: `RAISED UnicodeDecodeError: 'utf-8'
    codec can't decode byte 0xe9 in position 8`. Every session start died there.
    """
    _wizard_probe(tmp_path, monkeypatch,
                  "import sys\n"
                  "sys.stderr.buffer.write(b'warn caf\\xe9\\n')\n"
                  "sys.stdout.write('{\"completion_pct\": 40}\\n')\n")

    # Not merely "it did not raise". The banner must still be produced, because
    # a fix that swallowed the child would also stop raising while silently
    # telling a half-configured operator that setup is finished.
    assert "not fully set up (40%)" in capsys.readouterr().out, (
        "the wizard status was lost. One byte on the child's STDERR must not "
        "cost the operator the banner computed from its STDOUT.")


def test_a_healthy_wizard_status_is_still_read(tmp_path, monkeypatch, capsys):
    """Clean-path anchor: a finished setup produces no banner at all."""
    _wizard_probe(tmp_path, monkeypatch,
                  "import sys\n"
                  "sys.stdout.write('{\"completion_pct\": 100}\\n')\n")

    assert "not fully set up" not in capsys.readouterr().out, (
        "a fully configured workspace was nagged to run /setup-wizard")


# ---------------------------------------------------------------------------
# 3 - the structural jaw: every hook, composed question
# ---------------------------------------------------------------------------

_CATCHES_VALUE_ERROR = {"ValueError", "UnicodeDecodeError", "Exception",
                        "BaseException", "<bare>"}


def _handler_names(node: ast.Try) -> set[str]:
    names: set[str] = set()
    for handler in node.handlers:
        if handler.type is None:
            names.add("<bare>")
            continue
        parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
                 else [handler.type])
        for p in parts:
            if isinstance(p, ast.Name):
                names.add(p.id)
            elif isinstance(p, ast.Attribute):
                names.add(p.attr)
    return names


def test_no_hook_decodes_a_child_it_cannot_survive():
    """Has `errors=`, OR sits under a handler that can catch a ValueError.

    Deliberately the composed question. Asking only "is `errors=` present"
    would flag four calls that are already safe, and a reader who has to
    dismiss four false findings stops reading the fifth.
    """
    offenders = []
    scanned = 0
    sites = 0
    for path in sorted(HOOKS.glob("*.py")):
        scanned += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            offenders.append(f"{path.name}: unreadable ({exc})")
            continue

        # every Call that lives inside a Try whose handler can catch ValueError
        sheltered: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not (_handler_names(node) & _CATCHES_VALUE_ERROR):
                continue
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    sheltered.add(id(inner))

        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "attr", None) != "run":
                continue
            if getattr(getattr(call.func, "value", None), "id", None) != "subprocess":
                continue
            kw = {k.arg for k in call.keywords}
            decodes = bool(kw & {"text", "universal_newlines", "encoding"})
            captures = bool(kw & {"capture_output", "stdout", "stderr"})
            if not (decodes and captures):
                continue
            sites += 1
            if "errors" in kw or id(call) in sheltered:
                continue
            offenders.append(
                f"{path.name}:{call.lineno} decodes a child's output with no "
                f"errors= and no handler that can catch a ValueError")

    assert scanned >= 10, (
        f"only {scanned} hook file(s) scanned; this guard is green over an "
        f"almost empty corpus and measures nothing until that is resolved")
    assert sites >= 5, (
        f"only {sites} decoding call site(s) found across the hooks; the shape "
        f"this test looks for has moved and it is no longer measuring it")
    assert not offenders, (
        "a hook can be ended by one byte of its child's output:\n  "
        + "\n  ".join(offenders)
        + "\nUnicodeDecodeError is a ValueError and a SIBLING of "
          "json.JSONDecodeError. Neither OSError nor subprocess.SubprocessError "
          "nor json.JSONDecodeError catches it. Add errors=\"replace\".")


def test_the_structural_detector_can_actually_fire(tmp_path):
    """The negative case, against synthetic files rather than the tree."""
    def offending_lines(src: str) -> list[int]:
        tree = ast.parse(src)
        sheltered: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and (_handler_names(node) & _CATCHES_VALUE_ERROR):
                for stmt in node.body:
                    for inner in ast.walk(stmt):
                        sheltered.add(id(inner))
        out = []
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "attr", None) != "run":
                continue
            if getattr(getattr(call.func, "value", None), "id", None) != "subprocess":
                continue
            kw = {k.arg for k in call.keywords}
            if not (kw & {"text", "universal_newlines", "encoding"}):
                continue
            if not (kw & {"capture_output", "stdout", "stderr"}):
                continue
            if "errors" in kw or id(call) in sheltered:
                continue
            out.append(call.lineno)
        return out

    bad = ("import subprocess\n"
           "try:\n"
           "    subprocess.run(['x'], capture_output=True, text=True)\n"
           "except OSError:\n"
           "    pass\n")
    by_errors = ("import subprocess\n"
                 "subprocess.run(['x'], capture_output=True, text=True,"
                 " errors='replace')\n")
    by_handler = ("import subprocess\n"
                  "try:\n"
                  "    subprocess.run(['x'], capture_output=True, text=True)\n"
                  "except ValueError:\n"
                  "    pass\n")
    no_capture = ("import subprocess\n"
                  "subprocess.run(['x'], text=True)\n")

    assert offending_lines(bad) == [3], (
        "the detector did not flag a decode under an OSError-only handler, so "
        "the green result above means nothing")
    assert offending_lines(by_errors) == [], "errors= was flagged anyway"
    assert offending_lines(by_handler) == [], (
        "a call sheltered by an except ValueError was flagged, which would "
        "demand a pointless edit and train the reader to ignore this test")
    assert offending_lines(no_capture) == [], (
        "a call that does not capture was flagged; with no pipe there is "
        "nothing for this process to decode")


def test_the_sibling_relationship_this_file_rests_on_is_real():
    """Bind the reasoning to the language, not to a docstring.

    Every claim above depends on `UnicodeDecodeError` being a `ValueError` and
    NOT a subclass of `json.JSONDecodeError`, `OSError` or
    `subprocess.SubprocessError`. If a future Python changed that, this file
    should fail and say the guard can be retired.
    """
    assert issubclass(UnicodeDecodeError, ValueError)
    assert issubclass(json.JSONDecodeError, ValueError)
    assert not issubclass(UnicodeDecodeError, json.JSONDecodeError)
    assert not issubclass(UnicodeDecodeError, OSError)
    assert not issubclass(UnicodeDecodeError, subprocess.SubprocessError)
    assert issubclass(TimeoutError, OSError), (
        "TimeoutError stopped subclassing OSError, so the campaign's rule that "
        "(OSError, TimeoutError) is not a sibling gap no longer holds")
