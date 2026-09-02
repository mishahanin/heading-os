#!/usr/bin/env python3
"""One word in an unrelated config file switched this gate off, in silence.

`scripts/check-path-references.py --check` runs in pre-commit and in CI. It
extracted every repo-shaped path out of tracked Markdown and then asked
`get_routing_destination(path) != "engine"` before judging it, so that a path
belonging to the private overlay -- absent on a public clone, where its absence
proves nothing -- was skipped rather than reported as rot.

That question routes an UNMATCHED path through `config/routing-map.yaml`'s
top-level `default:`, and nearly every prefix the scanner recognises (`scripts/`,
`docs/`, `tests/`, `config/`, `.claude/`, `.github/`, `examples/`) matches no
rule key at all. MEASURED 2026-09-02 on a scratch workspace holding one Markdown
file that named two nonexistent paths:

    default: engine   -> 2 dangling, `--check` exit 1
    default: private  -> 0 dangling, "OK -- no new dangling path references.",
                         exit 0

The failure is byte-identical to the success. A gate whose green is
indistinguishable from its off state is worse than no gate, because the green
gets quoted later as evidence. `scripts/classification-health.py` carried the
same disease and was fixed on 2026-09-02.

Two changes close it, and this file holds both to their claims:

  * scope no longer reads the default. Only an EXPLICIT routing rule excludes a
    path; an unmatched path is judged, which is the over-reporting direction
    `.claude/rules/scope-claims.md` requires when evidence is absent.
  * the scanner refuses (exit 2, distinct from both 0 and 1) when its corpus
    collapses: zero files read, zero references left in scope after filtering,
    or a routing map that is absent or will not parse.

Everything below runs against a scratch tree under `tmp_path`, except the last
test, which asserts the LIVE repository's verdict is unchanged -- teeth without
a new failure on a healthy tree.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
CHECKER = ROOT / "scripts" / "check-path-references.py"

# Two invented paths that exist nowhere, in a tree of invented content. Engine
# law: this repository is public, so no fixture may carry a real path, person or
# company from the operator's private overlay.
PLANTED = ("scripts/ghost-tool.py", "docs/nowhere-page.md")

_PROSE = (
    "Run `scripts/ghost-tool.py --check` before the sweep.\n"
    "Background reading lives in docs/nowhere-page.md.\n"
    "The sweep itself is `scripts/real-tool.py`, which does exist.\n"
)

_DEFAULT_RULES = '  "outputs/": private\n  "vault/": private\n'


def _write_map(ws: Path, *, default: str = "engine", rules: str = _DEFAULT_RULES,
               raw: str | None = None) -> None:
    """Write the scratch routing map. `raw` bypasses the shape for the bad-YAML case."""
    body = raw if raw is not None else f"version: 1\ndefault: {default}\nrules:\n{rules}"
    (ws / "config" / "routing-map.yaml").write_text(body, encoding="utf-8")


def _scratch(tmp_path: Path, *, prose: str = _PROSE, **map_kwargs) -> Path:
    """A minimal workspace: root markers, a routing map, one prose file, a git index.

    `git add` and no commit is deliberate. `tracked_markdown()` shells out to
    `git ls-files`, which reads the INDEX, so staging is enough to make a file
    tracked and the fixture never needs to author a commit.
    """
    ws = tmp_path / "ws"
    for sub in (".claude", "config", "docs", "scripts"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    (ws / "CLAUDE.md").write_text("# scratch workspace\n", encoding="utf-8")
    _write_map(ws, **map_kwargs)
    (ws / "docs" / "NOTES.md").write_text(prose, encoding="utf-8")
    (ws / "scripts" / "real-tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "."], cwd=ws, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    return ws


def _run(args: list[str], *, ws: Path | None, data_root: Path) -> subprocess.CompletedProcess:
    """Invoke the checker as the gate invokes it, pointed at `ws`.

    `HEADING_OS_DATA` is repointed at a scratch directory on EVERY call,
    including the live-repository one. No test here may read or write the
    operator's private overlay, and inheriting the ambient value would let one
    do so the first time somebody adds a `--coverage` case below.
    """
    env = dict(os.environ)
    env["HEADING_OS_DATA"] = str(data_root)
    if ws is None:
        env.pop("WORKSPACE_ROOT", None)
    else:
        env["WORKSPACE_ROOT"] = str(ws)
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    d = tmp_path / "scratch-data"
    d.mkdir()
    return d


# --- the positive case is real ---------------------------------------------

def test_a_healthy_scratch_tree_has_a_real_corpus_and_catches_the_planted_paths(
        tmp_path, data_root):
    """A scanner that reads nothing passes everything. Prove it reads, and bites.

    The corpus assertion is not decoration. Every later test here compares
    against this run, and a comparison between two empty scans is satisfied by
    a scanner that was switched off in both.
    """
    ws = _scratch(tmp_path)
    out = _run(["--json"], ws=ws, data_root=data_root)
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)

    assert report["files_read"] > 0, "no Markdown was read"
    assert report["in_scope"] > 0, "every extracted reference was filtered away"
    assert report["in_scope"] == report["references_seen"], (
        "no rule in this fixture's map covers docs/ or scripts/, so nothing "
        "should have been excluded"
    )
    assert sorted(report["new"]) == sorted(PLANTED)

    gate = _run(["--check"], ws=ws, data_root=data_root)
    assert gate.returncode == 1, gate.stdout + gate.stderr
    for path in PLANTED:
        assert path in gate.stderr


def test_a_path_an_explicit_rule_sends_to_the_overlay_is_still_skipped(
        tmp_path, data_root):
    """The exclusion the scanner exists to make must survive the fix.

    Widening scope to "judge anything unmatched" would be a false economy if it
    also started reporting overlay-owned paths, which are absent from a public
    clone by design. An EXPLICIT rule still excludes.
    """
    ws = _scratch(
        tmp_path,
        # A second, in-scope reference is required, or the run refuses for the
        # empty-scope reason and proves nothing about the exclusion.
        prose=("The voice guide is `reference/invented-voice.md`.\n"
               "The sweep is `scripts/real-tool.py`.\n"),
        rules='  "outputs/": private\n  "reference/invented-voice.md": private\n',
    )
    out = _run(["--json"], ws=ws, data_root=data_root)
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["new"] == []
    assert report["references_seen"] == 2
    assert report["in_scope"] == 1, "the explicitly-private path was not excluded"
    assert "reference/invented-voice.md" not in report["dangling"]


# --- the defect: a one-word edit to an unrelated config file ----------------

def test_flipping_the_map_default_no_longer_narrows_the_corpus(tmp_path, data_root):
    """The reproduction, inverted into a guard.

    Before the fix these two runs differed by everything: 2 findings and exit 1
    against 0 findings and exit 0, with the second printing OK. The only
    difference between the trees is one word in a file that says nothing about
    either planted path.
    """
    ws = _scratch(tmp_path, default="engine")
    with_engine = json.loads(_run(["--json"], ws=ws, data_root=data_root).stdout)

    _write_map(ws, default="private")
    flipped_run = _run(["--json"], ws=ws, data_root=data_root)
    flipped = json.loads(flipped_run.stdout)

    assert flipped["in_scope"] == with_engine["in_scope"], (
        f"the corpus shrank from {with_engine['in_scope']} in-scope reference(s) "
        f"to {flipped['in_scope']} because of one word in the routing map"
    )
    assert sorted(flipped["new"]) == sorted(with_engine["new"]) == sorted(PLANTED)

    gate = _run(["--check"], ws=ws, data_root=data_root)
    assert gate.returncode == 1, (
        "`--check` passed against a map whose default was flipped to private; "
        f"stdout={gate.stdout!r} stderr={gate.stderr!r}"
    )
    assert "OK" not in gate.stdout.split("Baseline entries")[0] or "FAIL" in gate.stderr


# --- a collapsed corpus is a refusal, never a pass --------------------------

def test_a_zero_file_corpus_is_a_refusal(tmp_path, data_root):
    """An empty git index used to print OK and exit 0 over nothing at all.

    A wrong `WORKSPACE_ROOT`, a `git ls-files` that fails, and a tree whose
    Markdown was never staged all land here, and all three used to be reported
    as a clean tree.
    """
    ws = _scratch(tmp_path)
    subprocess.run(["git", "rm", "-r", "-q", "--cached", "."], cwd=ws, check=True)

    out = _run(["--check"], ws=ws, data_root=data_root)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "REFUSED" in out.stderr
    assert "empty corpus" in out.stderr
    assert "OK" not in out.stdout


def test_a_scope_that_excludes_every_reference_is_a_refusal(tmp_path, data_root):
    """The belt behind the scope fix: rules, not a default, can also empty the scope.

    Marking `docs/` and `scripts/` private is a larger edit than the one word
    that started this, and it is the shape a future migration would take. The
    scanner must still not print a pass over the nothing that is left.
    """
    ws = _scratch(tmp_path, rules='  "docs/": private\n  "scripts/": private\n')
    out = _run(["--check"], ws=ws, data_root=data_root)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "REFUSED" in out.stderr
    assert "excluded as overlay-owned" in out.stderr
    assert str(ws / "config" / "routing-map.yaml") in out.stderr


# --- the map itself is a dependency, so its absence is a refusal ------------

def test_an_absent_routing_map_is_a_refusal_that_names_it(tmp_path, data_root):
    """`load_routing_map()` answers this question by failing closed and silent.

    An absent map yields `{"default": "private", "rules": {}}`, which is the
    right answer for the leak wall and an unusable one here: "no rule excludes
    anything" would make this scanner judge every overlay path in the tree.
    Refusing, and naming the file, is the third option.
    """
    ws = _scratch(tmp_path)
    mapfile = ws / "config" / "routing-map.yaml"
    mapfile.unlink()

    out = _run(["--check"], ws=ws, data_root=data_root)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "REFUSED" in out.stderr
    # The whole phrase, not just the path. A bare `str(mapfile) in stderr`
    # survived a mutation that deleted the path from the refusal message,
    # because `FileNotFoundError` prints the filename itself and the message
    # interpolates the exception. The test would then have been pinning the
    # standard library's formatting rather than this scanner's sentence.
    assert f"the routing map {mapfile} could not be read" in out.stderr


def test_an_unparseable_routing_map_is_a_refusal_that_names_it(tmp_path, data_root):
    """Same refusal for YAML that will not parse, and it says which file."""
    ws = _scratch(tmp_path, raw="version: 1\nrules:\n  - a list\n   bad: [\n")

    out = _run(["--check"], ws=ws, data_root=data_root)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "did not parse" in out.stderr
    assert str(ws / "config" / "routing-map.yaml") in out.stderr


@pytest.mark.parametrize("raw", [
    "version: 1\ndefault: engine\n",              # truncated: the key is gone
    "version: 1\ndefault: engine\nrules: {}\n",   # present and empty
])
def test_a_map_with_no_rules_is_a_refusal(tmp_path, data_root, raw):
    """Valid YAML of a shape that cannot express the exclusion this tool claims.

    A truncated map parses fine and carries no `rules:`. Under the fixed scope
    every path is then unmatched and therefore judged, so the run would not go
    quiet -- it would go loud and wrong, reporting the whole overlay as rot.

    Both shapes, because they take different branches. The missing-key case
    alone left a mutation alive: dropping the emptiness half of the guard
    (`or not rules`) still refused, since `data.get("rules")` returns None and
    fails the isinstance test on its own. `rules: {}` is the case that only the
    emptiness half catches, and it is the shape a half-finished edit produces.
    """
    ws = _scratch(tmp_path, raw=raw)

    out = _run(["--check"], ws=ws, data_root=data_root)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "no `rules:` mapping" in out.stderr


def test_a_refusal_reaches_a_json_consumer_rather_than_reading_as_clean(
        tmp_path, data_root):
    """A machine reading only stdout must not see a refusal as an empty finding list.

    `--json` prints its object on stdout; a refusal that wrote to stderr alone
    would leave that consumer parsing nothing and concluding nothing is wrong.
    """
    ws = _scratch(tmp_path)
    (ws / "config" / "routing-map.yaml").unlink()

    out = _run(["--json"], ws=ws, data_root=data_root)
    assert out.returncode == 2
    payload = json.loads(out.stdout)
    assert payload["new"] is None, "an empty list here would read as 'no findings'"
    assert "routing map" in payload["error"]


def test_the_three_exit_codes_are_distinct(tmp_path, data_root):
    """0 clean, 1 findings, 2 refused. A refusal must borrow neither verdict."""
    clean = _scratch(tmp_path / "a", prose="Nothing here names a path.\n")
    dirty = _scratch(tmp_path / "b")
    broken = _scratch(tmp_path / "c")
    (broken / "config" / "routing-map.yaml").unlink()

    # The clean tree still needs a real in-scope reference, or it would refuse
    # for the empty-scope reason rather than pass.
    (clean / "docs" / "NOTES.md").write_text(
        "The sweep is `scripts/real-tool.py`.\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=clean, check=True)

    assert _run(["--check"], ws=clean, data_root=data_root).returncode == 0
    assert _run(["--check"], ws=dirty, data_root=data_root).returncode == 1
    assert _run(["--check"], ws=broken, data_root=data_root).returncode == 2


# --- the live tree: teeth, not a new failure -------------------------------

def test_the_live_repository_still_passes_over_a_large_corpus(data_root):
    """The change gives the gate teeth without changing its verdict on a healthy tree.

    Both halves matter. An exit code alone would be satisfied by a scanner that
    had been switched off, which is the very defect under repair, so the corpus
    size is asserted beside it. Measured 2026-09-02: 341 files read, 2073
    references extracted, 1895 in scope. The floors are set well below those to
    catch a collapse rather than to track the tree's growth.
    """
    out = _run(["--json"], ws=None, data_root=data_root)
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout)

    assert report["new"] == [], f"live repo has new dangling paths: {report['new']}"
    assert report["files_read"] >= 250, report["files_read"]
    assert report["in_scope"] >= 1500, report["in_scope"]
    assert report["references_seen"] > report["in_scope"], (
        "the live map routes real content to the overlay, so some references "
        "must be excluded; equality means the exclusion stopped working"
    )

    gate = _run(["--check"], ws=None, data_root=data_root)
    assert gate.returncode == 0, gate.stdout + gate.stderr
