"""Shard scripts-13-p2: the audit's own dispatcher, its eval harness, and a CLI.

* ``scrutinize-dispatch._reject_shell_syntax`` compared whole tokens to the
  operator set. ``shlex.split`` does not treat ``|`` as a delimiter, so an
  UNSPACED pipeline arrived as one ordinary-looking argument, ran as a fixed
  argv, failed on the mangled path, and its non-zero exit was written to the run
  record as ``verdict: "REPRODUCED"``. The same pipeline written WITH spaces was
  refused. A fabricated proof, carrying its own disproof in the stderr tail.

* ``scrutinize-dispatch.judge`` on the kimi branch recorded ``verdict=None``
  into a ``kind="verdict"`` row and returned 0. ``scrutinize_record.validate()``
  counts verdict rows by KIND, so a k3 side that decided nothing satisfied the
  reconciliation. The claude branch twenty lines above carries a long comment
  refusing exactly this.

* ``run-skill-eval.load_cases`` read case files with an unguarded ``json.load``
  and an unguarded ``case["_path"]`` assignment, so a broken fixture killed the
  process with exit 1 - the code its own table reserves for "checks failed".

* ``run-skill-eval`` compared ``usage["cache_read_input_tokens"] > 0``. The SDK
  types that field ``Optional[int]`` with ``default=None``, so the ``getattr``
  fallback never fired and the run crashed AFTER the API call was paid for.

* ``sanitize-text`` refused ``-o`` with ``--text`` and stdin and not with
  ``--scan``, which is the third branch where ``-o`` is dead.

Run: python3 -m pytest tests/test_a_pipeline_that_proved_itself_by_failing.py
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import scrutinize_record as rec  # noqa: E402


def _load(rel: str, name: str):
    """Import a kebab-case CLI script by path."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sd():
    return _load("scripts/scrutinize-dispatch.py", "sd_under_test")


@pytest.fixture(scope="module")
def rse():
    return _load("scripts/run-skill-eval.py", "rse_under_test")


# ============================================================
# The pipeline that proved itself by failing
# ============================================================

@pytest.mark.parametrize("command,operator", [
    ("/bin/ls /nope|wc -l", "|"),
    ("/bin/ls /nope | wc -l", "|"),
    ("/bin/ls>out.txt", ">"),
    ("/bin/ls > out.txt", ">"),
    ("/bin/true&&/bin/false", "&&"),
    ("/bin/true && /bin/false", "&&"),
    ("/bin/true;/bin/false", ";"),
    ("/bin/cat<in.txt", "<"),
])
def test_shell_syntax_is_refused_spaced_or_not(sd, command, operator):
    """Spacing decided the verdict. The unspaced form wrote a fake proof."""
    found = sd.shell_operators_in_source(command)

    assert found, f"{command!r} would have run as a fixed argv"
    assert operator in found


@pytest.mark.parametrize("command", [
    "/bin/echo $(whoami)",
    "/bin/echo `id`",
])
def test_unquoted_command_substitution_is_refused(sd, command):
    """A fixed argv does not expand it, so the check measures something else."""
    assert sd.shell_operators_in_source(command)


@pytest.mark.parametrize("command", [
    "/bin/ls /tmp",
    ".venv/bin/python -m pytest tests/test_x.py -q",
    "/usr/bin/git status --porcelain",
    'python3 -c "import sys; sys.exit(3)"',
    "python3 -c 'a; b'",
    'python3 -c "print(1|2)"',
])
def test_an_ordinary_command_is_not_refused(sd, command):
    """Quoting is the whole point: a `;` inside a -c payload is Python's."""
    assert sd.shell_operators_in_source(command) == []


def test_an_argv_list_is_judged_on_whole_tokens(sd):
    """A list IS argv, so nothing in it was ever going to reach a shell."""
    assert sd._reject_shell_syntax(["python3", "-c", "import sys; sys.exit(3)"]) is None
    assert sd._reject_shell_syntax(["/bin/ls", "|", "wc"]) is not None


def test_unbalanced_quotes_leave_the_guard_silent(sd):
    """`shlex.split` raises for these and `main` reports it; no second voice."""
    assert sd.shell_operators_in_source('python3 -c "unterminated') == []


def test_an_empty_command_is_not_shell_syntax(sd):
    assert sd._reject_shell_syntax([]) is None
    assert sd.shell_operators_in_source("") == []


def test_the_refusal_writes_a_degraded_row(sd, run_record):
    """Refusing at the CLI would print and return, and --validate would see
    a finding with no attempt against it - the defect `reproduce` already
    documents on its other failure path."""
    code = sd.reproduce(run_id="r9", target="t", finding_id="H9",
                        cmd=["/bin/ls", "/nope|wc", "-l"],
                        source="/bin/ls /nope|wc -l")

    assert code == 4
    rows = _rows(run_record)
    assert [r["kind"] for r in rows] == ["degraded"]
    assert "shell syntax" in rows[0]["degraded"]


def test_a_quoted_payload_still_runs(sd, run_record):
    """The false refusal this guard must not produce."""
    code = sd.reproduce(run_id="r10", target="t", finding_id="H10",
                        cmd=[sys.executable, "-c", "import sys; sys.exit(3)"],
                        source=f'{sys.executable} -c "import sys; sys.exit(3)"')

    assert code == 0
    rows = _rows(run_record)
    assert [r["kind"] for r in rows] == ["reproduction"]
    assert rows[0]["reproduction"]["exit_before"] == 3


# ============================================================
# The judge that decided nothing and reported a verdict
# ============================================================

@pytest.fixture
def run_record(monkeypatch, tmp_path):
    path = tmp_path / "record.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    return path


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture
def kimi(monkeypatch, sd, run_record):
    def _answer(text):
        monkeypatch.setattr(sd, "sensitivity_is_declared", lambda: False)
        monkeypatch.setattr(sd, "kimi_model", lambda: "not-a-live-model")
        monkeypatch.setattr(sd, "call_model", lambda *a, **k: text)
        return run_record
    return _answer


@pytest.mark.parametrize("answer", [
    "I considered the finding at length but cannot decide.",
    "",
    "Here is a long analysis with no verdict token in it at all.",
])
def test_an_answer_with_no_verdict_records_a_degradation(sd, kimi, answer):
    """It wrote `verdict: null` into a verdict row and returned 0."""
    path = kimi(answer)

    code = sd.judge(run_id="AUDIT1", target="t", finding_id="H1", pass_="2.5a",
                    brief="brief", family="kimi")

    assert code == 1
    kinds = [r["kind"] for r in _rows(path)]
    assert "verdict" not in kinds, "a row with no verdict was filed as a verdict"
    assert "degraded" in kinds


def test_a_real_verdict_is_still_recorded(sd, kimi):
    path = kimi("After review the finding is REFUTED on the evidence shown.")

    code = sd.judge(run_id="AUDIT1", target="t", finding_id="H1", pass_="2.5a",
                    brief="brief", family="kimi")

    assert code == 0
    rows = [r for r in _rows(path) if r["kind"] == "verdict"]
    assert len(rows) == 1
    assert rows[0]["verdict"] == "REFUTED"


def test_an_empty_verdict_no_longer_satisfies_the_reconciliation(sd, kimi, tmp_path):
    """The whole point: --validate is what makes an omission visible."""
    path = kimi("I cannot decide.")
    sd.judge(run_id="AUDIT1", target="t", finding_id="H1", pass_="2.5a",
             brief="brief", family="kimi")
    rec.append_row(run_id="AUDIT1", kind="pass_start", target="t")

    report = tmp_path / "report.md"
    report.write_text("Refutation: complete, all findings judged\n"
                      "Findings: 0 BLOCKER, 1 HIGH, 0 MEDIUM\n", encoding="utf-8")

    defects = rec.validate(run_id="AUDIT1", report_path=report)

    assert defects, "a pass that judged nothing validated clean"
    assert str(path)  # the record path is the one the fixture pinned


def test_the_claude_branch_still_refuses_an_omitted_verdict(sd, run_record):
    """The branch that was already right must stay right."""
    code = sd.judge(run_id="AUDIT2", target="t", finding_id="H2", pass_="2.5a",
                    brief="brief", family="claude", verdict="")

    assert code == 1
    assert [r["kind"] for r in _rows(run_record)] == ["degraded"]


# ============================================================
# The broken fixture reported as a failed check
# ============================================================

@pytest.fixture
def fake_skill(tmp_path):
    skill = tmp_path / "fakeskill"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: fakeskill\n---\nbody\n",
                                    encoding="utf-8")
    return skill


@pytest.mark.parametrize("body,why", [
    ('{"id": "case-2", "input": ', "truncated JSON"),
    ('[{"id": "case-1", "input": "x"}]', "an array at the top level"),
    ('"just a string"', "a scalar at the top level"),
    ("", "an empty file"),
])
def test_a_broken_case_file_is_a_setup_error(rse, fake_skill, body, why):
    """Exit 1 means "checks failed", and sends the reader to the wrong place."""
    (fake_skill / "evals" / "cases" / "case.json").write_text(body, encoding="utf-8")

    with pytest.raises(rse.CaseFileError):
        rse.load_cases(fake_skill)


def test_the_cli_exits_two_on_a_broken_case_file(rse, fake_skill, monkeypatch,
                                                 capsys):
    (fake_skill / "evals" / "cases" / "case.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(rse, "SKILLS_DIR", fake_skill.parent)
    monkeypatch.setattr(sys, "argv",
                        ["run-skill-eval.py", "--skill", "fakeskill", "--dry-run"])

    assert rse.main() == 2
    assert "case file error" in capsys.readouterr().err


def test_a_good_case_file_still_loads(rse, fake_skill):
    (fake_skill / "evals" / "cases" / "case.json").write_text(
        json.dumps({"id": "case-1", "input": "x"}), encoding="utf-8")

    cases = rse.load_cases(fake_skill)

    assert [c["id"] for c in cases] == ["case-1"]
    assert cases[0]["_path"].endswith("case.json")


# ============================================================
# The usage field the SDK types Optional
# ============================================================

class _Usage:
    """Shaped like `anthropic.types.Usage`: the cache fields default to None."""

    input_tokens = 10
    output_tokens = 20
    cache_creation_input_tokens = None
    cache_read_input_tokens = None


def test_the_sdk_really_defaults_those_fields_to_none():
    """The premise, checked rather than assumed."""
    from anthropic.types import Usage

    for field in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        info = Usage.model_fields[field]
        assert info.default is None, f"{field} no longer defaults to None"


def test_a_none_cache_count_does_not_crash_the_comparison():
    """`None > 0` raised TypeError after the API call was already paid for."""
    usage = {
        "input_tokens": _Usage.input_tokens,
        "output_tokens": _Usage.output_tokens,
        "cache_creation_input_tokens":
            getattr(_Usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(_Usage, "cache_read_input_tokens", 0) or 0,
    }

    assert usage["cache_read_input_tokens"] == 0
    assert usage["cache_creation_input_tokens"] == 0
    # The comparison the runner makes, which raised TypeError on None.
    assert (usage["cache_read_input_tokens"] > 0) is False


def test_the_raw_getattr_default_would_still_have_crashed():
    """Why `or 0` and not a bigger getattr default: the attribute EXISTS."""
    raw = getattr(_Usage, "cache_read_input_tokens", 0)

    assert raw is None, "the getattr fallback fired, so the premise is wrong"
    with pytest.raises(TypeError):
        _ = raw > 0


def test_the_source_coerces_rather_than_relying_on_the_getattr_default():
    source = (ROOT / "scripts" / "run-skill-eval.py").read_text(encoding="utf-8")

    assert 'getattr(response.usage, "cache_read_input_tokens", 0) or 0' in source
    assert 'getattr(response.usage, "cache_creation_input_tokens", 0) or 0' in source


# ============================================================
# The output path that was named and never written
# ============================================================

SANITIZE = [sys.executable, str(ROOT / "scripts" / "sanitize-text.py")]


def test_scan_with_an_output_path_is_refused(tmp_path):
    """It printed a clean report, exited 0, and wrote no file."""
    target = tmp_path / "doc.md"
    target.write_text("plain text\n", encoding="utf-8")
    out = tmp_path / "out.md"

    proc = subprocess.run(SANITIZE + [str(target), "--scan", "-o", str(out)],
                          capture_output=True, text=True, cwd=ROOT)

    assert proc.returncode != 0
    assert "does nothing" in proc.stderr
    assert not out.exists()


def test_scan_without_an_output_path_still_works(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("plain text\n", encoding="utf-8")

    proc = subprocess.run(SANITIZE + [str(target), "--scan"],
                          capture_output=True, text=True, cwd=ROOT)

    assert proc.returncode == 0
    assert "Clean" in proc.stdout


def test_sanitizing_to_an_output_path_still_writes_it(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("plain text\n", encoding="utf-8")
    out = tmp_path / "out.md"

    proc = subprocess.run(SANITIZE + [str(target), "-o", str(out)],
                          capture_output=True, text=True, cwd=ROOT)

    assert proc.returncode == 0
    assert out.read_text(encoding="utf-8") == "plain text\n"


def test_text_with_an_output_path_is_still_refused(tmp_path):
    """The two cases the guard already covered must not regress."""
    out = tmp_path / "out.md"
    proc = subprocess.run(SANITIZE + ["--text", "hello", "-o", str(out)],
                          capture_output=True, text=True, cwd=ROOT)

    assert proc.returncode != 0
    assert not out.exists()


def test_the_cli_refuses_an_unspaced_pipeline_end_to_end(tmp_path):
    """Through `main`, not by calling `reproduce` directly.

    The raw `--cmd` string only reaches the guard because the CLI hands it
    down; a test that calls `reproduce(source=...)` itself cannot tell whether
    that wiring is there. HEADING_OS_DATA redirects the run record, so nothing
    is written to the real overlay.
    """
    import os

    data = tmp_path / "data"
    (data / "outputs" / "operations" / "scrutiny").mkdir(parents=True)
    env = dict(os.environ, HEADING_OS_DATA=str(data))

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scrutinize-dispatch.py"),
         "--reproduce", "--run-id", "cli1", "--target", "t",
         "--finding", "H1", "--cmd", "/bin/ls /definitely-not-here|wc -l"],
        capture_output=True, text=True, cwd=ROOT, env=env)

    assert proc.returncode == 4, proc.stderr
    assert "shell syntax" in proc.stderr

    record = data / "outputs" / "operations" / "scrutiny" / "runs.jsonl"
    rows = _rows(record)
    assert [r["kind"] for r in rows] == ["degraded"], (
        "the refusal wrote no degraded row, so --validate cannot see it")


def test_the_cli_still_runs_a_quoted_payload_end_to_end(tmp_path):
    import os

    data = tmp_path / "data"
    (data / "outputs" / "operations" / "scrutiny").mkdir(parents=True)
    env = dict(os.environ, HEADING_OS_DATA=str(data))

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "scrutinize-dispatch.py"),
         "--reproduce", "--run-id", "cli2", "--target", "t",
         "--finding", "H2", "--cmd",
         f'{sys.executable} -c "import sys; sys.exit(3)"'],
        capture_output=True, text=True, cwd=ROOT, env=env)

    assert proc.returncode == 0, proc.stderr
    rows = _rows(data / "outputs" / "operations" / "scrutiny" / "runs.jsonl")
    assert [r["kind"] for r in rows] == ["reproduction"]
    assert rows[0]["reproduction"]["exit_before"] == 3
