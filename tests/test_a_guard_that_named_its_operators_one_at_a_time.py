"""The reproduction guard refused a LIST of operators, and a list is short.

`scripts/scrutinize-dispatch.py` runs an operator's `--cmd` as a fixed argv with
no shell. A shell operator in that string therefore arrives at the child as a
literal argument, the child fails for a reason that has nothing to do with the
check, and the non-zero exit is exactly what `REPRODUCED` reads as proof. The
module says so in its own docstring and guards against it.

The guard compared each token against `SHELL_OPERATORS`, an ENUMERATED set. But
`shlex` with `punctuation_chars` returns a RUN of adjacent punctuation as ONE
token, and no combined form was in the set. Measured 2026-08-29 by driving
`--reproduce`:

    --cmd "/bin/cat /shard58-no-such-file 2>&1"
      -> tokens [..., '2', '>&', '1'], guard silent, argv
         ['/bin/cat', '/shard58-no-such-file', '2>&1'], cat exits 1 over two
         filenames that do not exist, record gains verdict "REPRODUCED".

`&>` and `|&` did the same. So did a `#`, one layer earlier: the guard's lexer
took the default comment character and STOPPED at the hash while `shlex.split`
carried on past it, so `/bin/cat /nope #x|/bin/cat` was scanned as two tokens,
the pipe was never seen, and the run recorded REPRODUCED.

Four more defects fell out of measuring, none of them in the report that started
this:

  - `posix=True` STRIPS quotes, so the one thing the guard exists to read was
    already gone: `grep -c ';' /etc/hostname`, whose `;` belongs to grep, was
    refused. Quoting only survived when the quoted region held other characters.
  - the argv-level guard re-imposed that same blindness even after the raw guard
    had cleared the command, because `shlex.split` hands it the stripped token.
  - `text=True` with no `errors=` raised `UnicodeDecodeError` INSIDE
    `subprocess.run`, past every handler: a child printing bytes took the module
    out as a traceback with nothing in the run record.
  - an unbalanced quote left `main` the same way, under a comment that said
    `main` reports it. `main` had no handler at all.
  - and the claude judge branch handed `--verdict` straight to `append_row`,
    which RAISES on a token outside its vocabulary. `--verdict REFUTTED` was an
    uncaught ValueError, exit 1, no verdict row AND no degraded row, so
    `--validate` saw a finding nobody judged. The kimi branch degrades.

The report that opened this shard claimed the last one differently: that
`append_row` does no validation and the misspelling would be RECORDED. It does
validate. Measuring turned a fabricated-record defect into a lost-record defect.
Same branch, same asymmetry, opposite failure mode.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from scripts.utils import scrutinize_record as rec


def _load():
    """Import the kebab-case scripts/scrutinize-dispatch.py as a module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "scrutinize-dispatch.py"
    spec = importlib.util.spec_from_file_location("scrutinize_dispatch_shard58", path)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]`.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


disp = _load()


@pytest.fixture
def runs(tmp_path, monkeypatch):
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rec, "record_path", lambda: path)
    return path


def _rows(path) -> list[dict]:
    """Rows written so far. A missing file means none were, which is a real
    outcome here: several defects below are the ABSENCE of any row."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _kinds(path) -> list[str]:
    return [r.get("kind") for r in _rows(path)]


# A path no machine has. Any command naming it must fail, so a harness that
# reports REPRODUCED for it is reporting the malformed invocation, not a check.
MISSING = "/shard58-no-such-file-anywhere"


# ============================================================
# The combined run - the defect that opened the shard
# ============================================================
@pytest.mark.parametrize("raw, run", [
    (f"/bin/cat {MISSING} 2>&1", ">&"),
    (f"/bin/cat {MISSING} &> /dev/null", "&>"),
    (f"/bin/cat {MISSING} |& /bin/cat", "|&"),
    (f"/bin/cat {MISSING} >| /dev/null", ">|"),
    (f"/bin/cat {MISSING} <> /dev/null", "<>"),
    (f"/bin/cat {MISSING} 2>>&1", ">>&"),
])
def test_a_combined_redirect_run_is_seen_as_an_operator(raw, run):
    assert run in disp.shell_operators_in_source(raw), (
        f"{raw!r} carries the operator run {run!r}; the guard reported "
        f"{disp.shell_operators_in_source(raw)}")


def test_the_operator_test_is_structural_not_an_enumerated_list():
    """The property, not the six spellings above.

    An enumerated set is always one spelling short - that is the whole defect.
    This run appears in no constant in the module and in no other test here, so
    it passes only for code that asks "is every character punctuation?".
    """
    exotic = "><|&;()"
    assert exotic not in disp.SHELL_OPERATORS
    assert exotic not in disp._SHELL_PUNCT
    assert disp.shell_operators_in_source(f"/bin/cat a {exotic} b") == [exotic]


def test_every_named_operator_is_still_caught_by_the_structural_test():
    """The rewrite must not lose what the list already held."""
    for op in sorted(disp.SHELL_OPERATORS):
        assert disp.shell_operators_in_source(f"/bin/cat a {op} b") == [op], op


def test_a_redirect_run_no_longer_fabricates_a_reproduced_verdict(runs):
    """End to end, through the public entry point, on the real filesystem."""
    code = disp.reproduce(run_id="r1", target="t", finding_id="H1",
                          cmd=["/bin/cat", MISSING, "2>&1"],
                          source=f"/bin/cat {MISSING} 2>&1")
    assert code == 4
    assert _kinds(runs) == ["degraded"], (
        "a command that never ran its check must not leave a reproduction row")
    assert ">&" in _rows(runs)[0]["degraded"]


def test_the_refusal_is_recorded_and_not_merely_printed(runs):
    """`--validate` reads rows. A refusal only on stderr is invisible to it."""
    disp.reproduce(run_id="r1", target="t", finding_id="H1",
                   cmd=["/bin/cat", MISSING, "&>", "/dev/null"],
                   source=f"/bin/cat {MISSING} &> /dev/null")
    assert _rows(runs)[0]["finding_id"] == "H1"


# ============================================================
# The comment character, one layer earlier
# ============================================================
def test_a_hash_does_not_end_the_scan():
    raw = f"/bin/cat {MISSING} #note|/bin/cat"
    assert disp.shell_operators_in_source(raw) == ["|"]


def test_the_guard_reads_the_same_text_shlex_split_reads():
    """The two must not disagree about where the command ends.

    Driven, not grepped: whatever `shlex.split` still carries past a `#` is
    text the child will receive, so the guard has to have scanned it too.
    """
    import shlex
    raw = f"/bin/cat {MISSING} #note|/bin/cat"
    argv = shlex.split(raw)
    assert any("|" in tok for tok in argv), "premise: the split keeps the pipe"
    assert disp.shell_operators_in_source(raw), (
        "the split carries a pipe into the argv, so the guard must have seen it")


def test_a_hash_hidden_pipe_no_longer_fabricates_a_verdict(runs):
    raw = f"/bin/cat {MISSING} #note|/bin/cat"
    code = disp.reproduce(run_id="r1", target="t", finding_id="H1",
                          cmd=["/bin/cat", MISSING, "#note|/bin/cat"], source=raw)
    assert code == 4
    assert _kinds(runs) == ["degraded"]


# ============================================================
# Quoting - the thing the guard exists to read
# ============================================================
@pytest.mark.parametrize("raw", [
    "/bin/grep -c ';' /etc/hostname",
    '/bin/grep -c ">&" /etc/hostname',
    "/bin/grep -c '|' /etc/hostname",
    "python3 -c 'import sys; sys.exit(3)'",
    'python3 -c "print(1)"',
    "python3 -c 'a = (1, 2)'",
])
def test_a_quoted_operator_belongs_to_the_child_and_is_allowed(raw):
    assert disp.shell_operators_in_source(raw) == [], (
        f"{raw!r} quotes its punctuation on purpose; refusing it refuses a "
        f"legal command")


def test_a_quoted_operator_that_is_the_whole_token_is_allowed():
    """The case `posix=True` could not express.

    Under the stripped-quote lexer `';'` and `;` were the same three-character
    token, so this exact command was refused. Separated out from the batch
    above because it is the one that only `posix=False` fixes.
    """
    assert disp.shell_operators_in_source("/bin/grep -c ';' /etc/hostname") == []


def test_a_quoted_operator_survives_the_argv_guard_too(runs, tmp_path):
    """The raw check clearing a command must not be undone one line later.

    `shlex.split` hands the argv guard the STRIPPED token `;`, which it refuses
    whole. Driven end to end, because the two guards only interact there.
    """
    target = tmp_path / "haystack.txt"
    target.write_text("no semicolon here\n", encoding="utf-8")
    code = disp.reproduce(run_id="r1", target="t", finding_id="H1",
                          cmd=["/bin/grep", "-c", ";", str(target)],
                          source=f"/bin/grep -c ';' {target}")
    assert code == 0, f"grep should have RUN and found nothing; got exit {code}"
    assert _kinds(runs) == ["reproduction"]


def test_an_in_process_argv_caller_is_still_guarded():
    """No raw string means no quoting to read, so the whole-token check stands."""
    run = disp._run(["/bin/echo", "a", ";", "b"])
    assert run.unusable and "shell syntax" in run.unusable


def test_the_argv_guard_is_reached_only_without_a_raw_string(monkeypatch):
    """Pins WHICH branch skips it, not just that the outcome is right."""
    calls = []
    monkeypatch.setattr(disp, "_reject_shell_syntax",
                        lambda cmd: calls.append(cmd) or None)
    disp._run(["/bin/true"], source="/bin/true")
    assert calls == [], "a raw string was given; the argv guard adds only blindness"
    disp._run(["/bin/true"])
    assert calls == [["/bin/true"]], "no raw string; the argv guard is the only one"


# ============================================================
# A child is allowed to print bytes
# ============================================================
def test_a_child_that_prints_non_utf8_does_not_take_the_harness_down():
    run = disp._run([sys.executable, "-c",
                     "import sys; sys.stdout.buffer.write(b'\\xc0\\xc1'); "
                     "sys.exit(1)"])
    assert run.unusable is None
    assert run.exit_code == 1
    assert isinstance(run.stdout_tail, str)


def test_a_binary_child_still_lands_in_the_record(runs):
    """The point of not crashing: the attempt stays visible to `--validate`."""
    code = disp.reproduce(run_id="r1", target="t", finding_id="H1",
                          cmd=[sys.executable, "-c",
                               "import sys; sys.stderr.buffer.write(b'\\xff\\xfe'); "
                               "sys.exit(2)"])
    assert code == 0
    assert _kinds(runs) == ["reproduction"]


# ============================================================
# An unparseable --cmd
# ============================================================
def test_an_unbalanced_quote_is_a_recorded_refusal_not_a_traceback(runs, capsys):
    code = disp.main(["--reproduce", "--run-id", "r1", "--target", "t",
                      "--finding", "H1", "--cmd", "python3 -c 'unterminated"])
    assert code == 4, "exit 1 here was the interpreter dying, not a decision"
    assert _kinds(runs) == ["degraded"]
    assert "could not be parsed" in _rows(runs)[0]["degraded"]
    assert "could not be parsed" in capsys.readouterr().err


def test_the_guard_stays_silent_on_an_unbalanced_quote():
    """It has nothing to add; `main` owns that refusal. The comment said `main`
    reported it long before `main` did, so this pins the division of labour."""
    assert disp.shell_operators_in_source("python3 -c 'unterminated") == []


# ============================================================
# The claude judge branch
# ============================================================
@pytest.mark.parametrize("bad", ["REFUTTED", "looks fine to me", "refuted",
                                 "REPRODUCED", "FALSIFIED", "-"])
def test_a_claude_verdict_outside_the_vocabulary_degrades(bad, runs):
    code = disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                      brief="", family="claude", verdict=bad)
    assert code == 1
    assert _kinds(runs) == ["degraded"], (
        f"{bad!r} must leave a degraded row, never a verdict row and never "
        f"an uncaught ValueError out of append_row")


def test_a_reproduction_outcome_is_not_a_judge_ruling(runs):
    """`scrutinize_record.VERDICTS` admits REPRODUCED and FALSIFIED. A judge
    does not rule them, so the judge branch must be narrower than the record."""
    assert "REPRODUCED" in rec.VERDICTS
    assert "REPRODUCED" not in disp._JUDGE_VERDICTS
    disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
               brief="", family="claude", verdict="REPRODUCED")
    assert _kinds(runs) == ["degraded"]


def test_the_recorded_verdict_is_the_normalised_token_not_the_raw_string(runs):
    """What `_verdict_in` returns is what lands, or the check bought nothing.

    A judge who types the whole line is accepted, exactly as the kimi branch
    accepts prose. Recording the raw string instead would hand `append_row` a
    value outside its vocabulary and raise, which is the defect this shard came
    to close - and no test that passes a bare token can tell the two apart.
    """
    code = disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                      brief="", family="claude", verdict="VERDICT: refuted")
    assert code == 0
    assert _rows(runs)[0]["verdict"] == "REFUTED"


# Spelled out here rather than read from `disp._JUDGE_VERDICTS`: a test that
# asks the code what it supports agrees with the code by construction and cannot
# notice a token going missing.
@pytest.mark.parametrize("token", sorted({
    "REFUTED", "REFUTE_PARTIAL", "REFUTATION_FAILED",
    "CORRECT", "CORRECT_DOWNGRADE", "INCORRECT", "AMBIGUOUS",
}))
def test_every_real_judge_token_still_records(token, runs):
    code = disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                      brief="", family="claude", verdict=token)
    assert code == 0
    row = _rows(runs)[0]
    assert row["kind"] == "verdict" and row["verdict"] == token
    assert row["judge_family"] == "claude"


def test_an_omitted_verdict_still_degrades_and_does_not_reach_the_token_check(runs):
    """The pre-existing refusal above the new one must survive it."""
    code = disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
                      brief="", family="claude", verdict=None)
    assert code == 1
    assert "without --verdict" in _rows(runs)[0]["degraded"]


def test_the_error_message_names_the_tokens_from_the_one_source(runs, capsys):
    """A hand-typed third copy of the seven is how the first two drifted."""
    disp.judge(run_id="r1", target="t", finding_id="H1", pass_="2.5a",
               brief="", family="claude", verdict="NOPE")
    err = capsys.readouterr().err
    for token in disp._JUDGE_VERDICTS:
        assert token in err


# ============================================================
# One source for the seven tokens
# ============================================================
def test_both_verdict_regexes_are_built_from_the_same_tuple():
    for token in disp._JUDGE_VERDICT_ORDER:
        assert disp._VERDICT_RE.search(f"ruling: {token} done"), token
        assert disp._VERDICT_LINE_RE.search(f"VERDICT: {token}"), token


def test_a_shorter_token_cannot_swallow_a_longer_one():
    """`CORRECT` sits inside `CORRECT_DOWNGRADE`, and the WORD BOUNDARIES are
    what keep them apart, not the order of the alternation.

    Measured 2026-08-29: with `\\b` on both sides either order resolves all
    seven; without it, only longest-first does. Reordering the tuple therefore
    changes nothing, which is why a mutation that reorders it survives. The
    anchor is the load-bearing part, so the anchor is what this pins.
    """
    for token in disp._JUDGE_VERDICT_ORDER:
        assert disp._verdict_in(f"the ruling is {token}.") == token, token
        assert disp._verdict_in(f"VERDICT: {token}") == token, token


def test_a_verdict_token_glued_to_a_word_is_not_a_ruling():
    """The leading `\\b`. Without it `MISCORRECT` reads as `CORRECT`."""
    assert disp._verdict_in("MISCORRECT") is None
    assert disp._verdict_in("UNREFUTED") is None


def test_a_verdict_token_with_a_suffix_is_not_a_ruling():
    """The trailing `\\b`, on both regexes."""
    assert disp._verdict_in("CORRECTNESS") is None
    assert disp._verdict_in("VERDICT: CORRECTNESS") is None


def test_the_judge_vocabulary_is_a_strict_subset_of_the_record_vocabulary():
    assert disp._JUDGE_VERDICTS < rec.VERDICTS
    assert {"REPRODUCED", "FALSIFIED"} == rec.VERDICTS - disp._JUDGE_VERDICTS
