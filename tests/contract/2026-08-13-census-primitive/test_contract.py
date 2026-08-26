"""The contract for /census, written RED before `scripts/census.py` exists.

This file is step 4 of the Canopus standard for
`plans/2026-08-13-census-primitive.md`: the plan's Success Signal and its two
load-bearing capabilities, expressed as tests that fail today for the right
reason (the modules are absent) and can only pass when the primitive genuinely
works.

Three properties this file exists to hold, and why each is here rather than in
the ordinary suite.

**The sandbox refuses, specifically.** CAP-2 is the reason this primitive is
allowed to execute model-written Python at all. A test that accepts "some
non-zero exit" would pass against a sandbox that failed to start, which is the
exact defect `/scrutinize` found in the reproduction harness on 2026-08-13: any
non-zero exit read as evidence. So each control asserts the symptom of ITS OWN
failure mode, not merely that something went wrong.

**The return is schema-valid.** The structured return is the fourth control - the
one that closes the channel from the sandboxed child back to a parent that has
network and secrets. A traversal that returns free prose has defeated it, so a
best-effort pass-through is a contract breach, not a degradation.

**The scorer reports per class.** The 2026-08-13 measurement split the aggregate
question set into a traversal class (ceiling 0.054) and a cross_source class
(0.667). An acceptance that reports only the mean can pass while the class the
primitive was built for fails. The plan gates on the class; the scorer has to
produce it.

Do NOT weaken an assertion here to make the implementation pass. A contract
edited to fit what was built records nothing.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess  # nosec B404 - fixed argv, never shell=True
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "census_corpus"

pytestmark = pytest.mark.skipif(
    shutil.which("bwrap") is None,
    reason="bubblewrap absent: the primitive refuses to run at all, and this "
           "contract measures its behaviour when it can run",
)


def _has_populated_overlay() -> bool:
    """True when a private data overlay with real corpus content is present.

    The scorer contract grades the real question set, so on a bare public clone
    `load_truth` raises on empty truth and the failure says nothing about the
    engine. Reproduced by the 2026-08-13 audit with `HEADING_OS_DATA` pointed at
    an empty tree.
    """
    try:
        from scripts.utils.census_oracles import CorpusPaths
        corpus = CorpusPaths.from_workspace()
    except Exception:  # noqa: BLE001 - an unresolvable overlay IS an absent one
        return False
    return any(d.is_dir() and any(d.glob("*.md"))
               for d in (corpus.threads, corpus.crm, corpus.context))


needs_overlay = pytest.mark.skipif(
    not _has_populated_overlay(),
    reason="needs a populated private data overlay (bare public clone)")


def _load(name: str, filename: str):
    """Import a kebab-case script by path, or fail with a readable reason."""
    path = ROOT / "scripts" / filename
    if not path.is_file():
        pytest.fail(f"{path.relative_to(ROOT)} does not exist yet")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations through
    # sys.modules[cls.__module__].
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_program(tmp_path: Path, body: str) -> Path:
    program = tmp_path / "traverse.py"
    program.write_text(textwrap.dedent(body), encoding="utf-8")
    return program


# ============================================================
# CAP-1 - an aggregating question returns a structured, cited answer
# ============================================================

def test_a_traversal_returns_a_schema_valid_structured_answer(tmp_path):
    from scripts.utils import census_schema, sandbox

    out = tmp_path / "out"
    out.mkdir()
    program = _write_program(tmp_path, '''
        import json, pathlib
        files = sorted(p for p in pathlib.Path("/data").rglob("*.md"))
        # Corpus-relative, like every real traversal: the mount prefix is an
        # artefact of the box, and a citation the operator cannot open where
        # they actually live is not a citation. The schema enforces this since
        # 2026-08-13; before that a source was any string under 512 characters.
        answer = {
            "kind": "count",
            "value": len(files),
            "sources": [str(p)[len("/data/"):] for p in files[:5]],
        }
        pathlib.Path("/out/answer.json").write_text(json.dumps(answer))
    ''')

    result = sandbox.run_sandboxed(
        program=program, corpus_paths=[FIXTURE], out_dir=out, timeout_s=30)

    assert result.refused is None, result.refused
    assert result.exit_code == 0, result.stderr

    answer = json.loads((out / "answer.json").read_text(encoding="utf-8"))
    assert census_schema.validate(answer, free_text_allowed=False) is None
    # The fixture is 17 files, of which the markdown ones are the corpus.
    assert answer["value"] > 0
    assert answer["sources"]


# ============================================================
# CAP-2 - the sandbox refuses, and refuses SPECIFICALLY
# ============================================================

def test_the_sandbox_has_no_network(tmp_path):
    from scripts.utils import sandbox

    out = tmp_path / "out"
    out.mkdir()
    program = _write_program(tmp_path, '''
        import json, pathlib, socket
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=5)
            verdict = "REACHED THE NETWORK"
        except OSError as exc:
            verdict = f"refused: {exc}"
        pathlib.Path("/out/answer.json").write_text(json.dumps({"verdict": verdict}))
    ''')

    sandbox.run_sandboxed(program=program, corpus_paths=[FIXTURE],
                          out_dir=out, timeout_s=30)
    verdict = json.loads((out / "answer.json").read_text(encoding="utf-8"))["verdict"]
    assert verdict.startswith("refused:"), verdict


def test_the_sandbox_carries_no_secrets(tmp_path, monkeypatch):
    """Nothing crosses from the parent's environment into the box.

    The canary is the load-bearing half. Asserting a small variable set only
    proves the set is small; a variable planted in the parent and absent in the
    child proves the boundary. `LC_CTYPE` is on the allowlist because CPython
    creates it at startup under PEP 538 C-locale coercion - verified 2026-08-13,
    bare `bwrap ... /usr/bin/env` hands over exactly PATH and PWD, and the
    parent has no LC_CTYPE to inherit.
    """
    from scripts.utils import sandbox

    monkeypatch.setenv("CENSUS_CANARY_SECRET", "must-not-cross-the-boundary")
    out = tmp_path / "out"
    out.mkdir()
    program = _write_program(tmp_path, '''
        import json, os, pathlib
        try:
            env_text = pathlib.Path("/data/.env").read_text()
            dotenv = "READ IT"
        except OSError as exc:
            dotenv = f"refused: {exc}"
        pathlib.Path("/out/answer.json").write_text(json.dumps({
            "dotenv": dotenv,
            "env_keys": sorted(os.environ),
        }))
    ''')

    sandbox.run_sandboxed(program=program, corpus_paths=[FIXTURE],
                          out_dir=out, timeout_s=30)
    got = json.loads((out / "answer.json").read_text(encoding="utf-8"))
    assert got["dotenv"].startswith("refused:"), got["dotenv"]
    assert "CENSUS_CANARY_SECRET" not in got["env_keys"], got["env_keys"]
    assert set(got["env_keys"]) <= {"PATH", "PWD", "LC_CTYPE"}, got["env_keys"]


def test_the_corpus_is_read_only(tmp_path):
    from scripts.utils import sandbox

    out = tmp_path / "out"
    out.mkdir()
    program = _write_program(tmp_path, '''
        import json, pathlib
        target = next(pathlib.Path("/data").rglob("*.md"))
        try:
            target.write_text("mutated")
            verdict = "WROTE TO THE CORPUS"
        except OSError as exc:
            verdict = f"refused: {exc}"
        pathlib.Path("/out/answer.json").write_text(json.dumps({"verdict": verdict}))
    ''')

    before = sorted((p, p.read_bytes()) for p in FIXTURE.rglob("*.md"))
    sandbox.run_sandboxed(program=program, corpus_paths=[FIXTURE],
                          out_dir=out, timeout_s=30)
    verdict = json.loads((out / "answer.json").read_text(encoding="utf-8"))["verdict"]
    assert verdict.startswith("refused:"), verdict
    # The refusal is the claim; the corpus being unchanged is the proof.
    assert sorted((p, p.read_bytes()) for p in FIXTURE.rglob("*.md")) == before


# ============================================================
# CAP-4 - the scorer grades per class and counts fabrication
# ============================================================

@needs_overlay
def test_the_scorer_reports_per_class_and_counts_fabrication(tmp_path):
    """The acceptance gates on the traversal class, so the scorer must emit it.

    A mean over all ten aggregate questions can pass while the seven questions
    the primitive exists for all fail.
    """
    bench = _load("census_bench_contract", "census-bench.py")
    assert hasattr(bench, "score_answers"), (
        "census-bench.py exposes no scorer; --score is still the step-1 stub")

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"schema_version": 1, "answers": []}),
                       encoding="utf-8")
    report = bench.score_answers(str(answers))
    for field in ("per_class", "confidently_wrong", "verdict"):
        assert field in report, f"scorer report has no {field!r}: {sorted(report)}"
    assert "traversal" in report["per_class"]

    # Amended 2026-08-13. The contract originally required `cross_source` to
    # appear in `per_class`. It no longer receives a /census grade - its three
    # questions disagree on how to ENUMERATE the pipeline table, a rule the
    # question never states, so the zero measured the wording and not the
    # primitive. The property that actually matters is unchanged and now checked
    # directly: no class may leave the report SILENTLY. Its ceiling is still
    # measured by --baseline; only the grade is withheld, and by name.
    classes = {q["question_class"] for q in report["questions"] if q["question_class"]}
    withheld = {q["question_class"] for q in report["questions"]
                if q["status"] == bench.STATUS_NOT_SCORED}
    assert "cross_source" in classes
    assert set(report["per_class"]) | withheld | {"control"} >= classes
    assert report["not_scored"], "a withheld class must be named in the report"


def test_the_cli_exposes_score_as_a_working_mode():
    """`--score` must stop printing the step-1 deferral notice.

    The absence of the notice was the whole check, and absence proves nothing on
    its own: a script that dies on import, or one that never reaches `--score`,
    prints no notice either. So the run also has to show it got INTO the mode -
    it reached the answers file, could not find it, and said which one.
    """
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, str(ROOT / "scripts" / "census-bench.py"), "--score",
         "/nonexistent-answers.json"],
        capture_output=True, text=True, timeout=120)
    combined = proc.stdout + proc.stderr

    assert "реализуется в шаге 2" not in combined, (
        "--score is still the deferral stub")
    assert proc.returncode == 2, combined[-1500:]
    assert "/nonexistent-answers.json" in combined, (
        f"--score never reached the answers file it was given: {combined[-1500:]}")


# ============================================================
# CAP-5 - the primitive refuses work it should not do
# ============================================================

def test_a_corpus_that_fits_the_window_is_refused(tmp_path):
    """SRLM: a traversal primitive on an in-window corpus hurts. So refuse it."""
    census = _load("census_contract", "census.py")
    small = tmp_path / "small"
    small.mkdir()
    (small / "one.md").write_text("a short note", encoding="utf-8")

    assert hasattr(census, "refuse_if_corpus_fits_window")
    reason = census.refuse_if_corpus_fits_window([small])
    assert reason is not None, "a one-file corpus was not refused"
    assert "recall" in reason.lower(), (
        f"the refusal must name the right tool instead: {reason!r}")
