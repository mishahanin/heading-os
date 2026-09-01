"""F-M7: S110 and S112 must not be suppressed in pre-commit; push-all.py must not swallow exceptions."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.repo_files import tracked_python_files  # noqa: E402

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


#: The trees the sweep walks. Named once so the corpus floor below and the ruff
#: argv cannot disagree: point the run at a tree with no Python in it and the
#: floor is what says so, instead of ruff exiting 0 over nothing.
SWEEP_ROOTS = ("scripts", ".claude")


def _ruff(*paths, cwd=ENGINE):
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "S110,S112",
         *paths, "--output-format=concise"],
        cwd=str(cwd), capture_output=True, text=True,
    )


def test_no_s110_s112_violations_in_tree():
    """Ruff with S110/S112 enabled must report zero violations across scripts/ and .claude/."""
    result = _ruff(*(f"{d}/" for d in SWEEP_ROOTS))
    assert result.returncode == 0, \
        f"S110/S112 violations remain (F-M7):\n{result.stdout}\n{result.stderr}"


def test_the_sweep_has_a_corpus_under_each_root():
    """Ruff exits 0 when it finds no files, which is the same green as a clean tree.

    MEASURED 2026-09-01: repointing the invocation above at `docs/` (which holds
    no Python) left this file's four tests passing. A guard whose empty result is
    indistinguishable from its clean result checks nothing and reports success.

    The floor is PER ROOT, not over the union: a total floor is satisfied by
    `scripts/` alone while `.claude/` contributes zero, and `.claude/hooks/` is
    where a silently-swallowing handler does the most damage, because a hook that
    eats its own exception fails open on every turn.

    Corpus via `tests.repo_files`, never a hand-rolled walk: an agent worktree
    under `.claude/worktrees/` would otherwise double the count and make the
    floor meaningless.
    """
    counts = {d: len(tracked_python_files([d])) for d in SWEEP_ROOTS}

    assert counts["scripts"] >= 200, counts       # 428 on 2026-09-01
    assert counts[".claude"] >= 10, counts        # 41 on 2026-09-01


def test_the_selected_rules_still_report_the_thing_they_are_selected_for(tmp_path):
    """A positive control. Zero findings is only meaningful if a finding is possible.

    `--select S110,S112` is three characters away from selecting nothing that
    exists (a typo, a ruff release retiring a code, a `per-file-ignores` entry
    widened to `*`), and every one of those failure modes reports the tree clean.
    So the same invocation is aimed at a file that IS in violation, and both
    codes have to come back.
    """
    (tmp_path / "violation.py").write_text(
        "def f(xs):\n"
        "    for x in xs:\n"
        "        try:\n"
        "            g(x)\n"
        "        except Exception:\n"
        "            continue\n"
        "    try:\n"
        "        h()\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8")

    result = _ruff("violation.py", cwd=tmp_path)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "S110" in result.stdout, result.stdout
    assert "S112" in result.stdout, result.stdout


def test_the_repo_ruff_config_does_not_ignore_either_code():
    """The control above runs on a scratch file, so it cannot see THIS config.

    Ruff resolves settings from the checked file's ancestors, and a file under
    `tmp_path` has none of this repository's. MEASURED 2026-09-01: adding
    `per-file-ignores = {"*" = ["S110", "S112"]}` under `[tool.ruff]` left every
    other test in this file green, including the positive control, because the
    tree sweep then found nothing and the control was never subject to the
    suppression. So the config is read directly here, which is the same question
    `_ignore_args` asks of `.pre-commit-config.yaml` one layer up.

    Derived from the parsed table, not from a list of key names written out by
    hand, so a suppression under a key nobody anticipated is still seen.
    """
    import tomllib

    ruff = tomllib.loads(
        (ENGINE / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]

    suppressed = []

    def walk(node, trail):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, trail + [str(key)])
        elif isinstance(node, list):
            for item in node:
                walk(item, trail)
        elif (isinstance(node, str) and node in {"S110", "S112"}
                # `select` and `extend-select` name a code to TURN IT ON; every
                # other key naming one turns it off somewhere.
                and not any(part.endswith("select") for part in trail)):
            suppressed.append((".".join(trail), node))

    walk(ruff, ["tool", "ruff"])

    assert not suppressed, (
        f"pyproject.toml suppresses the codes this file exists to enforce: "
        f"{suppressed}. The tree sweep would then report clean over a tree that "
        f"is not.")
    # Floor: a `[tool.ruff]` that lost its lint table would walk nothing.
    assert ruff.get("lint", {}).get("select"), "no [tool.ruff.lint] select to read"
