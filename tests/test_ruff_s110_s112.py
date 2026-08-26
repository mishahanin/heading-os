"""F-M7: S110 and S112 must not be suppressed in pre-commit; push-all.py must not swallow exceptions."""
import re
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent


def _ignore_args():
    src = (ENGINE / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    found = re.findall(r"--ignore['\s,]+([A-Z0-9,]+)", src)
    # A floor under the corpus, not a claim about the config. The two callers
    # loop over this list and assert a code is absent, which is trivially true
    # over an empty list: a reformatted config that this regex no longer matches
    # would leave both guards watching nothing and still reporting green.
    # One --ignore list on 2026-08-26. If the last one is ever removed on
    # purpose, this line is what says so out loud instead of going quiet.
    assert found, "no --ignore argument parsed; the regex stopped matching"
    return found


def test_precommit_does_not_ignore_s110():
    for arg in _ignore_args():
        codes = [c.strip() for c in arg.split(",")]
        assert "S110" not in codes, \
            f"S110 must not be suppressed in pre-commit (F-M7), found in: {arg}"


def test_precommit_does_not_ignore_s112():
    for arg in _ignore_args():
        codes = [c.strip() for c in arg.split(",")]
        assert "S112" not in codes, \
            f"S112 must not be suppressed in pre-commit (F-M7), found in: {arg}"


def test_push_all_gh_token_logs_exception():
    src = (ENGINE / "scripts/push-all.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    inspected = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "gh_token":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    inspected += 1
                    body = child.body
                    assert not (len(body) == 1 and isinstance(body[0], ast.Pass)), \
                        "gh_token() except block is a bare pass (F-M7 S110)"
    # The only assertion above sits two loops deep. Rename gh_token, or drop its
    # try/except, and zero assertions run while the test still reports green.
    # One handler inside one gh_token on 2026-08-26.
    assert inspected >= 1, \
        f"no gh_token exception handler reached the check ({inspected} inspected)"


def test_no_s110_s112_violations_in_tree():
    """Ruff with S110/S112 enabled must report zero violations across scripts/ and .claude/."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S110,S112",
         "scripts/", ".claude/", "--output-format=concise"],
        cwd=ENGINE, capture_output=True, text=True,
    )
    assert result.returncode == 0, \
        f"S110/S112 violations remain (F-M7):\n{result.stdout}\n{result.stderr}"
