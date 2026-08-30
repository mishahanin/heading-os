#!/usr/bin/env python3
"""SEC-002: Verify session-start.py has no bare exception swallowing.

Vulnerability: 'except Exception: pass' hides all errors silently.
Attack vector: CRM health, sync status, stale data checks fail without indication.
Expected safe behavior: All exception handlers log to stderr or handle specifically.
"""

import ast
from pathlib import Path

import pytest

from tests.security.conftest import read_file_content


@pytest.fixture
def session_start_path(hooks_dir):
    return hooks_dir / "session-start.py"


# A handler catching any of these swallows everything the control is about.
# `BaseException` is STRICTLY broader than `Exception` -- it also takes
# KeyboardInterrupt and SystemExit -- and was invisible here until 2026-08-30.
BROAD_NAMES = frozenset({"Exception", "BaseException"})


def _is_broad_exception(handler):
    """True when this handler catches Exception, BaseException, or is bare.

    WIDENED 2026-08-30. Two ordinary spellings evaded it, and both tests in this
    file `continue` past whatever it rejects, so each was skipped entirely while
    the `inspected >= 6` floor still reported a healthy count:

      except BaseException: pass          -> an ast.Name whose id is not
                                             "Exception"; broader than the
                                             construct the control targets.
      except (Exception, OSError): pass   -> an ast.Tuple, not an ast.Name.

    A tuple counts as broad when ANY element is broad, which is the semantics of
    the construct: `except (Exception, OSError)` catches everything `except
    Exception` does. Anything else -- a specific typed exception, an aliased or
    attribute-spelled class this test cannot resolve -- stays out, as before.
    """
    if handler.type is None:
        return True  # bare except:
    parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    return any(isinstance(p, ast.Name) and p.id in BROAD_NAMES for p in parts)


@pytest.mark.parametrize("spelling,broad", [
    ("except Exception: pass", True),
    ("except BaseException: pass", True),
    ("except (Exception, OSError): pass", True),
    ("except (OSError, BaseException): pass", True),
    ("except: pass", True),
    ("except ValueError: pass", False),
    ("except (ValueError, KeyError): pass", False),
])
def test_the_broad_exception_detector_sees_each_spelling(spelling, broad):
    """The negative case the two controls below rest on. NEW 2026-08-30.

    Both of them `continue` past every handler this predicate rejects, so a
    predicate that answered False for everything would leave them asserting
    nothing while still reporting a clean run. Both directions are pinned here:
    the five broad spellings must be caught and the two specific ones must not.
    """
    tree = ast.parse(f"try:\n    risky()\n{spelling}\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad_exception(handler) is broad


def _handler_reports(handler) -> bool:
    """True when the handler's BODY raises, returns, or writes an output call.

    Read off the parsed body, never off the raw source lines. Until 2026-08-30
    this substring-matched the handler's source text, comments included, so a
    handler whose entire body was `...` under
    `except Exception:  # print( and logging. handled upstream` passed: the
    comment contains `print(` and `logging.`, and `# we return nothing here`
    supplies `return`. A control defeated by prose punishes a file for
    documenting itself, which this repository forbids.

    An output call is `raise`, `return`, or a call whose dotted callee mentions
    stderr / print / log -- `print(...)`, `sys.stderr.write(...)`,
    `logging.warning(...)`, `log.exception(...)`, `logger.debug(...)`.
    """
    for node in ast.walk(handler):
        if isinstance(node, (ast.Raise, ast.Return)):
            return True
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            root = callee.split(".")[0].lower()
            leaf = callee.split(".")[-1].lower()
            if callee == "print" or "stderr" in callee.lower():
                return True
            if root in {"log", "logger", "logging"} or leaf in {
                    "warning", "warn", "error", "exception", "critical", "info"}:
                return True
    return False


@pytest.mark.parametrize("body,reports", [
    ("print('x')", True),
    ("sys.stderr.write('x')", True),
    ("logging.warning('x')", True),
    ("logger.exception('x')", True),
    ("raise", True),
    ("return None", True),
    ("...", False),
    ("pass", False),
    ("counter += 1", False),
])
def test_the_output_detector_reads_the_body_not_the_comments(body, reports):
    """The negative case, and the comment-immunity claim, measured. NEW 2026-08-30.

    Every case carries a comment naming `print(`, `logging.` and `return`, so a
    detector that still reads source text scores True on all nine and this test
    fails on the three that report nothing.
    """
    src = ("try:\n    risky()\n"
           "except Exception:  # print( and logging. handled upstream; we return nothing here\n"
           f"    {body}\n")
    handler = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.ExceptHandler))
    assert _handler_reports(handler) is reports


def test_no_bare_except_pass(session_start_path):
    """No 'except Exception: pass' or 'except: pass' blocks allowed.

    Specific typed exceptions (e.g., except ValueError) are allowed since
    they indicate intentional handling of a known error type.
    """
    content = read_file_content(session_start_path)
    tree = ast.parse(content)

    violations = []
    inspected = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if not _is_broad_exception(node):
                continue  # Specific typed exceptions are fine
            inspected += 1
            # Check if body is just 'pass' or 'continue'
            if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                line = node.lineno
                violations.append(
                    f"Line {line}: bare exception handler with pass/continue"
                )

    # Measured 10 broad handlers in session-start.py on 2026-08-26; floor at 6 so
    # retiring a handler does not fail this test. If _is_broad_exception drifted to
    # return False for everything, the continue above would skip every handler and
    # the empty violations list would report PASS while nothing was checked.
    assert inspected >= 6, f"only {inspected} broad exception handler(s) inspected"

    assert not violations, (
        f"Found {len(violations)} bare exception handler(s) that silently swallow errors:\n"
        + "\n".join(violations)
    )


def test_exception_handlers_log_to_stderr(session_start_path):
    """Exception handlers should include stderr output or re-raise."""
    content = read_file_content(session_start_path)
    tree = ast.parse(content)

    inspected = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if not _is_broad_exception(node):
                continue  # Specific typed exceptions are fine
            inspected += 1
            has_output = _handler_reports(node)
            # No `if is_bare_swallow:` gate. It used to be there, and it made
            # this test unreachable: a bare swallow is a handler whose whole
            # body is `pass` or `continue`, and `test_no_bare_except_pass` in
            # this same file already fails the build on those. So the condition
            # was false for every handler, the assertion never executed, and the
            # claim in this test's name - handlers log to stderr - was never
            # checked once. Measured 2026-08-27: 10 broad handlers inspected,
            # 0 assertions run.
            assert has_output, (
                f"Line {node.lineno}: broad exception handler neither logs, "
                f"returns, nor re-raises. Its failure is invisible."
            )

    # Measured 10 broad handlers in session-start.py on 2026-08-26; floor at 6 so
    # retiring a handler does not fail this test. If _is_broad_exception drifted to
    # return False for everything, the continue above would skip every handler and
    # this test would assert nothing at all.
    assert inspected >= 6, f"only {inspected} broad exception handler(s) inspected"
