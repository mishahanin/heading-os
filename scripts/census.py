#!/usr/bin/env python3
"""/census - answer an aggregating question by traversing the corpus, not retrieving it.

The technique is Recursive Language Models (RLM, arXiv:2512.24601): the corpus
does NOT enter the model's context. It sits on disk, a traversal program walks
it, and only the question, the corpus metadata and a structured result travel
back. Recursion depth is exactly 1 - a traversal never spawns another traversal.

WHY THIS EXISTS, in one measurement. On 2026-08-13 the incumbent retrieval path
was measured against code-computed truth over this workspace. Questions whose
answer requires visiting many small files scored a retrieval CEILING of 0.000 -
all seven at exactly 0.00. That is not a tuning problem: the answer sits in
no single chunk, so top-K cannot return it at any depth. Questions that compare
two dense files scored 0.667, so `/census` is deliberately NOT built for those -
`/recall` already delivers the material, and SRLM (arXiv:2603.15653) reports a
traversal primitive on an in-window corpus actively hurts.

WHAT MAKES EXECUTING GENERATED CODE ACCEPTABLE HERE. It is not that the code is
trusted. The traversal runs inside bubblewrap with no network, an empty
environment, a read-only corpus and one writable output directory, and its
return must satisfy a schema of counts, paths and pairs. The carve-out from the
global ban on executing generated code, and the four conditions that VOID it,
are written in `.claude/rules/generated-code-execution.md`. Read that before
changing anything here.

THE ROOT MODEL IS THE SESSION, not a model inside this script. The session
writes the traversal program and passes it with `--program`; this script runs it
and validates what comes back. That keeps the engine deterministic and testable,
and it is why `--return-budget` bounds what flows BACK into the session's
context: a script cannot meter the tokens of a context it does not own, but it
can refuse to hand back more corpus-derived text than was asked for.

Usage:
    python scripts/census.py "how many active threads are stale?" \\
        --program /tmp/traverse.py --corpus threads

    python scripts/census.py "..." --program t.py --corpus threads --corpus crm \\
        --emit-answers answers.json --question-id agg-01

The traversal program's contract, inside the box:
    read from   /data/<scope>/...      (read-only, one directory per --corpus)
    write to    /out/answer.json       (the only writable path)
    return      {"kind": "count"|"paths"|"pairs", ..., "sources": [...]}

Exit codes:
  0  answered
  2  bad arguments
  3  the traversal failed, or its return did not satisfy the schema
  4  the corpus fits in the context window - use /recall instead
  5  the sandbox refused the run (no bubblewrap, air-gapped path, timeout)
  6  the return exceeded the return budget
  7  the run completed but its record could not be written to --emit-answers

`--emit-answers` records a run that was REFUSED by the sandbox (exit 5) as an
answer of None, which the scorer counts as a refusal rather than a wrong
answer. That is deliberate: the attempt happened and the acceptance file is a
record of attempts. The same holds for an argument failure that names its
question (exit 2 for a missing --program or an unknown scope).

Exactly two exits write no row, and both because no row could say anything.
Exit 4, the corpus that fits the context window, returns before a traversal is
attempted at all. Exit 2 for --emit-answers without --question-id has no
question to file the row under.

Tests: tests/test_a_guard_that_stopped_one_level_short.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import census_schema  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.census_oracles import CorpusPaths  # noqa: E402
from scripts.utils.census_state import run_state  # noqa: E402
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.sandbox import air_gap_reason, run_sandboxed  # noqa: E402
from scripts.utils.workspace import get_default_tz  # noqa: E402

# ============================================================
# Configuration
# ============================================================

EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_TRAVERSAL_FAILED = 3
EXIT_CORPUS_FITS_WINDOW = 4
EXIT_SANDBOX_REFUSED = 5
EXIT_RETURN_BUDGET = 6
EXIT_ANSWERS_WRITE_FAILED = 7

# Below this, reading the corpus outright beats traversing it.
#
# What establishes the number, honestly: it is a judgement calibrated on one
# measurement, not a derived constant. The traversal-class ceiling was measured on the
# default scope set, which is ~1.4 MB. 200 KB is roughly 50k tokens - an amount
# a session reads directly without strain, where a traversal adds a sandbox, a
# subprocess and a schema for nothing. Raising it toward the real context limit
# would refuse the very corpus this primitive was built for.
CORPUS_WINDOW_BYTES = 200_000

# Characters of corpus-derived text allowed back into the caller's context.
DEFAULT_RETURN_BUDGET = 20_000

DEFAULT_TIMEOUT_S = 180

ANSWER_FILENAME = "answer.json"

# The traversal-class scopes: where the questions with a zero ceiling live.
DEFAULT_SCOPES = ("threads", "crm", "context", "auto_memory")

CORPUS_SUFFIXES = (".md", ".json", ".yaml", ".yml", ".txt")


# ============================================================
# Corpus resolution
# ============================================================

def known_scopes() -> dict[str, Path]:
    """Named scopes the operator can pass instead of a path."""
    corpus = CorpusPaths.from_workspace()
    return {
        "threads": corpus.threads,
        "crm": corpus.crm,
        "context": corpus.context,
        "auto_memory": corpus.auto_memory,
        "auto-memory": corpus.auto_memory,
        "knowledge": corpus.knowledge,
        "outputs": corpus.outputs,
    }


def resolve_corpus(names: list[str]) -> tuple[list[Path], dict[Path, str], str | None]:
    """Map scope names or paths to directories.

    Returns (paths, mount names, error). The mount name is the scope name the
    OPERATOR typed, not the directory basename: `threads` resolves to
    `<data>/threads/business`, and mounting that at `/data/business` would leave
    a traversal written against `/data/threads` reading an empty tree and
    returning zero - a wrong answer with no error anywhere. Caught on the first
    live run of this engine, 2026-08-13.
    """
    scopes = known_scopes()
    resolved: list[Path] = []
    mounts: dict[Path, str] = {}
    for name in names:
        if name in scopes:
            path = scopes[name]
        else:
            candidate = Path(name).expanduser()
            if not candidate.exists():
                return [], {}, (f"unknown corpus scope {name!r}; known scopes are "
                                f"{', '.join(sorted(scopes))}, or pass an existing path")
            path = candidate
        if path in mounts:
            # `--corpus threads --corpus threads` put one directory in the list
            # twice and `corpus_bytes` summed it twice, so a corpus that really
            # fits the window could measure at double its size and sail past the
            # exit-4 refusal this primitive exists to make. `mounts` is keyed by
            # Path and always collapsed to one entry, so the mount table and the
            # byte count disagreed with each other.
            continue
        resolved.append(path)
        mounts[path] = _mount_name_for(path, name)
    return resolved, mounts, None


def _mount_name_for(path: Path, requested: str) -> str:
    """Mount a scope at its DATA-ROOT-RELATIVE path, so returned paths line up.

    The oracles that grade this primitive report data-root-relative paths
    (`threads/business/x.md`). Mounting the same directory at `/data/threads`
    would make a traversal return `threads/x.md`, and every answer would then
    have to be re-mapped before it could be compared - a normalisation step is
    somewhere for a scoring bug to live. Mounting at `threads/business` removes
    the step: strip the `/data/` prefix and the path is already in the oracle's
    spelling.
    """
    try:
        root = Path(CorpusPaths.from_workspace().root).resolve()
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        # A stable, collision-free fallback. `requested.replace("/", "-")`
        # mapped `../foo` and `foo` both to `foo`, and `mounts` is keyed by
        # PATH -- so two distinct scopes kept two entries under one mount name
        # and one silently shadowed the other inside the sandbox. The digest of
        # the resolved path is what makes the name unique to the directory.
        stem = requested.replace("/", "-").strip("-.") or "corpus"
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
        return f"{stem}-{digest}"


DIAGNOSTIC_CHARS = 400


def _diagnostic(stderr: str | None) -> str:
    """A traversal's stderr, bounded and labelled before it reaches the caller.

    The failure path was the one place untrusted corpus text reached the
    orchestrator without meeting the schema: 800 unbounded characters of a
    traversal's stderr went straight into `record["error"]` and from there into
    the session's context, untagged and ungated by `--free-text`. That is the
    channel control #4 exists to close, standing open on the error branch.

    It is not dropped, because an operator debugging a failed traversal needs the
    interpreter's message. It is bounded to a quarter of the old size, stripped
    of line breaks so it cannot forge structure in the transcript, and wrapped in
    a marker that says what it is. A diagnostic is never an answer.
    """
    text = " ".join((stderr or "").split())
    if not text:
        return "no stderr."
    clipped = text[-DIAGNOSTIC_CHARS:]
    elision = "..." if len(text) > DIAGNOSTIC_CHARS else ""
    return f"[untrusted traversal stderr, last {DIAGNOSTIC_CHARS} chars] {elision}{clipped}"


def corpus_bytes(paths: list[Path]) -> int:
    """Total size of the corpus, skipping anything that vanishes mid-scan.

    `stat()` between `rglob`/`is_file` and the read is a TOCTOU: a file removed
    while this walks raised an uncaught OSError and killed the CLI before the
    window check could answer. The sibling `_candidate_docs` already guarded
    exactly this. Skipping an unreadable file UNDER-counts, which errs toward
    "the corpus fits the window" -- so the skip is reported rather than
    swallowed.
    """
    total = 0
    skipped = 0
    for path in paths:
        try:
            if path.is_file():
                # The suffix filter applies to a file scope too. Without it the
                # `is_file` branch counted any named file whole, so a 300 KB
                # `.docx` measured nonzero while holding nothing this traversal
                # can read - and `refuse_if_corpus_fits_window`, whose docstring
                # states that `corpus_bytes` counts only CORPUS_SUFFIXES, could
                # never reach its "0 bytes of readable content, check the scope"
                # branch for exactly the case that branch describes.
                if path.suffix.lower() in CORPUS_SUFFIXES:
                    total += path.stat().st_size
                continue
        except OSError:
            skipped += 1
            continue
        for item in path.rglob("*"):
            try:
                if item.is_file() and item.suffix.lower() in CORPUS_SUFFIXES:
                    total += item.stat().st_size
            except OSError:
                skipped += 1
    if skipped:
        print(f"warning: {skipped} corpus file(s) vanished or were unreadable "
              f"during sizing; the total below is a LOWER bound", file=sys.stderr)
    return total


def refuse_if_corpus_fits_window(paths: list[Path],
                                 threshold: int | None = None) -> str | None:
    """Why this corpus does not need a traversal, or None.

    SRLM's finding is that a traversal primitive on a corpus that fits the
    window is worse than reading it. Refusing is therefore not politeness, it is
    the primitive declining to make an answer worse.

    The zero case is answered separately. `corpus_bytes` counts only
    CORPUS_SUFFIXES, so a scope holding nothing of those types measures 0 and
    would otherwise be refused as "it fits the window" - the wrong reason, and
    one that sends the operator to `/recall` for a corpus `/recall` cannot read
    either.
    """
    limit = CORPUS_WINDOW_BYTES if threshold is None else threshold
    size = corpus_bytes(paths)
    if size >= limit:
        return None
    if size == 0:
        return (f"corpus measures 0 bytes of readable content: nothing under "
                f"{', '.join(CORPUS_SUFFIXES)} was found in the named scope(s), "
                "so there is nothing to traverse. Check the scope, not /recall.")
    return (f"corpus is {size:,} bytes, under the {limit:,}-byte floor: it fits "
            "in the context window, so reading it directly is both cheaper and "
            "more accurate than traversing it. Use /recall instead.")


# ============================================================
# Running one traversal
# ============================================================

def run_census(*, question: str, program: Path, corpus_paths: list[Path],
               free_text: bool, return_budget: int, timeout_s: int,
               out_root: Path,
               mount_names: dict[Path, str] | None = None) -> tuple[int, dict]:
    """Run one traversal. Returns (exit_code, record).

    The record is what a caller may print or append to an answers file. It
    carries the answer only when the answer earned the right to travel.
    """
    out_dir = out_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    result = run_sandboxed(program=program, corpus_paths=corpus_paths,
                           out_dir=out_dir, timeout_s=timeout_s,
                           mount_names=mount_names)
    elapsed = time.perf_counter() - started

    record: dict = {
        "question": question,
        "mounts": dict(result.mounts),
        "corpus": [str(p) for p in corpus_paths],
        "elapsed_s": round(elapsed, 3),
        "answer": None,
        "error": None,
    }

    if result.refused:
        record["error"] = result.refused
        return EXIT_SANDBOX_REFUSED, record

    if result.exit_code != 0:
        record["error"] = (f"traversal exited {result.exit_code}. "
                           f"{_diagnostic(result.stderr)}")
        return EXIT_TRAVERSAL_FAILED, record

    answer_path = out_dir / ANSWER_FILENAME
    if not answer_path.is_file():
        record["error"] = (f"traversal wrote no {ANSWER_FILENAME}: a run that "
                           "produces no return is not an answer")
        return EXIT_TRAVERSAL_FAILED, record

    try:
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        record["error"] = f"return is not readable JSON: {exc}"
        return EXIT_TRAVERSAL_FAILED, record

    reason = census_schema.validate(answer, free_text_allowed=free_text)
    if reason:
        record["error"] = f"return rejected by the schema: {reason}"
        return EXIT_TRAVERSAL_FAILED, record

    size = census_schema.size_of(answer)
    record["return_chars"] = size
    if size > return_budget:
        record["error"] = (
            f"return is {size:,} characters, over the {return_budget:,}-character "
            "budget; it was DISCARDED, not truncated, so there is no partial "
            "answer to read - narrow the question or raise --return-budget "
            "deliberately")
        record["discarded"] = True
        return EXIT_RETURN_BUDGET, record

    record["answer"] = answer
    return EXIT_OK, record


# ============================================================
# Answers file
# ============================================================

def _emit_record(args, record: dict) -> int | None:
    """Append `record` to `--emit-answers`. An exit code on failure, else None.

    One writer, called from both exit-5 paths. Guarded, and that guard is the
    reason it exists: `append_answer` raises RuntimeError on an answers file it
    cannot use and OSError on a failed write, and NOTHING in `main` caught
    either — so a traversal that had already run for up to 180 seconds printed
    its answer and then died on a traceback with exit 1, a code this file's
    docstring does not define, and the record was lost.
    """
    if not args.emit_answers:
        return None
    corpus = CorpusPaths.from_workspace()
    state = run_state(corpus.root, Path(__file__).resolve().parent.parent,
                      datetime.now(tz=get_default_tz()).date(),
                      tz=get_default_tz())
    try:
        append_answer(args.emit_answers, record, args.question_id, state)
    except (RuntimeError, OSError) as exc:
        print(f"{RED}the run finished but its record was NOT written to "
              f"{args.emit_answers}: {exc}{RESET}", file=sys.stderr)
        print(f"{YELLOW}The answer above is real; the acceptance file is "
              f"missing it.{RESET}", file=sys.stderr)
        return EXIT_ANSWERS_WRITE_FAILED
    return None


def _refused_before_running(args, corpus_paths: list[Path], reason: str,
                            exit_code: int = EXIT_BAD_ARGS) -> int:
    """Record a refusal that happened before any traversal ran, and return.

    One shape for every pre-run refusal that knows which question it refused.
    The record carries `answer: None` and the reason, which the scorer counts as
    a refusal; a MISSING row is counted as "not answered" instead, and the
    reason is gone.
    """
    record = {"question": args.question, "mounts": {},
              "corpus": [str(p) for p in corpus_paths],
              "elapsed_s": 0.0, "answer": None, "error": reason}
    failed = _emit_record(args, record)
    return failed if failed is not None else exit_code


def append_answer(path: Path, record: dict, question_id: str,
                  state: dict) -> None:
    """Append one record to the acceptance answers file, atomically.

    The file assembles itself from what the engine actually returned, so it
    cannot drift from the run it claims to describe.
    """
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Refuse rather than start a fresh file over the top: silently
            # replacing a half-written answers file loses every answer already
            # recorded, and the run would still report success.
            raise RuntimeError(
                f"cannot read the existing answers file {path}: {exc}; refusing "
                "to overwrite it, move it aside or repair it") from exc
        # The same refusal for a file that DECODES and is the wrong shape. Only
        # unreadable and undecodable were guarded, so `[]` reached
        # `payload["answers"]` as a TypeError and a dict without the key reached
        # it as a KeyError. Neither is RuntimeError or OSError, so `main`'s
        # handler let both through: traceback, exit 1, record lost after a
        # traversal that may have run for 180 seconds — the exact failure the
        # comment above that handler says was fixed.
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), list):
            raise RuntimeError(
                f"the existing answers file {path} is valid JSON of the wrong "
                "shape (no 'answers' list); refusing to overwrite it, move it "
                "aside or repair it")
        # The ELEMENTS, for the same reason and by the same rule. The guard
        # above validated the container and stopped, so a list of non-dicts
        # reached `a.get(...)` as an AttributeError and a dict written by an
        # older schema reached `a["question_id"]` as a KeyError. Neither is
        # RuntimeError or OSError, so `_emit_record`'s handler let both
        # through - traceback, exit 1, record lost after a traversal that may
        # have run for 180 seconds. That is verbatim the failure the comment
        # above claims to have ended; it just stopped one level short.
        bad = [i for i, a in enumerate(payload["answers"])
               if not isinstance(a, dict)
               or not isinstance(a.get("question_id"), str)
               or not a["question_id"]]
        if bad:
            raise RuntimeError(
                f"the existing answers file {path} has records without a usable "
                f"'question_id' at positions {bad}; refusing to overwrite it, "
                "move it aside or repair it")
    else:
        payload = {"schema_version": 1, "run_state": state, "answers": []}
    entry = dict(record)
    entry["question_id"] = question_id
    # The file-level `run_state` is stamped once, at creation. Every answer also
    # carries its OWN state, because a corpus that moves mid-run would otherwise
    # grade all fifteen answers against the world as of question one - exactly
    # the drift `corpus_content_sha256` was added to catch, invisible again one
    # level down. `run_state_drift` names the answers produced against a
    # different corpus than the file claims.
    entry["run_state"] = state
    # Dedup FIRST, then compute drift over what the file will actually contain.
    # The order was reversed, so a question re-run against a now-consistent
    # corpus kept its old answer's question_id in `run_state_drift` -- the flag
    # whose whole job is to name a drifted answer named one that is no longer
    # in the file.
    payload["answers"] = [a for a in payload["answers"]
                          if a.get("question_id") != question_id]
    payload["answers"].append(entry)
    payload["answers"].sort(key=lambda a: a.get("question_id", ""))
    first = payload.get("run_state") or {}
    payload["run_state_drift"] = sorted({
        a["question_id"] for a in payload["answers"]
        if (a.get("run_state") or first).get("corpus_content_sha256")
        != first.get("corpus_content_sha256")
    })
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


# ============================================================
# Output
# ============================================================

def print_program(program: Path) -> None:
    """Shown before the run unless `--no-print-program`. NOT an approval gate.

    Nothing waits for input here, so this prints what is about to run and then it
    runs. Do not describe it as operator approval: the carve-out in
    `.claude/rules/generated-code-execution.md` rests on four sandbox controls,
    and a fifth control that does not exist is worse than four that do.
    """
    print(f"{BOLD}Программа обхода{RESET} {GRAY}{program}{RESET}")
    print(f"{GRAY}{'-' * 60}{RESET}")
    print(program.read_text(encoding="utf-8").rstrip())
    print(f"{GRAY}{'-' * 60}{RESET}")


def print_record(record: dict, exit_code: int) -> None:
    if exit_code == EXIT_OK:
        answer = record["answer"]
        print(f"{GREEN}{BOLD}Ответ{RESET} {GRAY}({record['elapsed_s']} с, "
              f"{record.get('return_chars', 0)} симв.){RESET}")
        print(json.dumps(answer, ensure_ascii=False, indent=2))
        return
    colour = YELLOW if exit_code == EXIT_CORPUS_FITS_WINDOW else RED
    print(f"{colour}{record['error']}{RESET}", file=sys.stderr)


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Answer an aggregating question by traversing the corpus "
                     "inside a sandbox (RLM, depth 1)."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="the aggregating question, in quotes")
    parser.add_argument("--program", required=True, type=Path,
                        help="the traversal program to run inside the sandbox")
    parser.add_argument("--corpus", action="append", metavar="SCOPE",
                        help=("scope name or path; repeatable. Default: "
                              + ", ".join(DEFAULT_SCOPES)))
    parser.add_argument("--free-text", action="store_true",
                        help="permit a kind 'text' return, tagged untrusted")
    parser.add_argument("--no-print-program", action="store_true",
                        help="do not print the traversal program before running it")
    parser.add_argument("--return-budget", type=int, default=DEFAULT_RETURN_BUDGET,
                        metavar="CHARS",
                        help=f"characters allowed back (default {DEFAULT_RETURN_BUDGET})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                        metavar="SECONDS")
    parser.add_argument("--json", action="store_true",
                        help="print the whole record as JSON")
    parser.add_argument("--emit-answers", type=Path, metavar="FILE",
                        help="append this run to an acceptance answers file")
    parser.add_argument("--question-id", metavar="ID",
                        help="benchmark question id; required with --emit-answers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.emit_answers and not args.question_id:
        print(f"{RED}--emit-answers needs --question-id: an answer that does not "
              f"name its question cannot be graded{RESET}", file=sys.stderr)
        return EXIT_BAD_ARGS

    # Both argument failures below happen AFTER --question-id is known, so both
    # can name the question they failed on - and a row saying why is the only
    # thing that distinguishes "this question was attempted and refused" from
    # "this question was never run". The scorer reads a missing row as the
    # latter, which is how a setup error reads as an untouched question. The
    # air-gap refusal further down already reasoned exactly this way for exit 5;
    # these two exits were left out of it.
    if not args.program.is_file():
        message = f"traversal program not found: {args.program}"
        print(f"{RED}{message}{RESET}", file=sys.stderr)
        return _refused_before_running(args, [], message)

    corpus_paths, mount_names, error = resolve_corpus(
        list(args.corpus or DEFAULT_SCOPES))
    if error:
        print(f"{RED}{error}{RESET}", file=sys.stderr)
        return _refused_before_running(args, [], error)

    # The air-gap refusal comes FIRST, ahead of the window refusal, and the order
    # is the point. `run_sandboxed` already refuses an air-gapped mount, so
    # nothing was ever exposed - but the window check ran earlier and won, so a
    # small air-gapped scope was answered with "it fits in the context window,
    # use /recall instead". That is advice to read, by another route, a branch
    # that must not be read at all. A security refusal never sits behind a
    # convenience refusal. Found by a live smoke run, 2026-08-13.
    for path in corpus_paths:
        denial = air_gap_reason(path)
        if denial:
            print(f"{RED}{denial}{RESET}", file=sys.stderr)
            # Recorded, like the other exit-5 refusal. This returned straight
            # out, so the one exit code this file's docstring PROMISES is
            # written as `answer: None` left no row at all — and a missing row
            # is read by the scorer as "not answered", which loses the reason.
            #
            # The comment here used to say the docstring carved out "exactly
            # one" unrecorded exit and that this was "a second". Both halves
            # were wrong on the face of this same function: the three
            # EXIT_BAD_ARGS returns above also wrote no row, so this was at
            # least the fourth, and a reader trusting the count would never
            # look for the exit-2 gaps. Two of those three now record; the
            # third cannot, and the docstring names both survivors instead of
            # counting.
            return _refused_before_running(args, corpus_paths, denial,
                                           EXIT_SANDBOX_REFUSED)

    fits = refuse_if_corpus_fits_window(corpus_paths)
    if fits:
        print(f"{YELLOW}{fits}{RESET}", file=sys.stderr)
        return EXIT_CORPUS_FITS_WINDOW

    if not args.no_print_program:
        print_program(args.program)

    with tempfile.TemporaryDirectory(prefix="census-") as scratch:
        exit_code, record = run_census(
            question=args.question, program=args.program,
            corpus_paths=corpus_paths, free_text=args.free_text,
            return_budget=args.return_budget, timeout_s=args.timeout,
            out_root=Path(scratch), mount_names=mount_names)

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print_record(record, exit_code)

    failed = _emit_record(args, record)
    if failed is not None:
        return failed

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
