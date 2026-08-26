#!/usr/bin/env python3
"""Acceptance benchmark for /census: measure what the incumbent path can reach.

The question this answers is not "is /census good" -- /census does not exist. It
is "is the aggregation gap real on THIS corpus". The concept for /census rests on
the claim that top-K retrieval cannot answer an aggregating question by
construction. That claim is plausible, is supported by OOLONG-PAIRS externally,
and had never been measured here.

Method, in one line: compute each question's answer with code, ask the retrieval
layer the same question at saturation depth, and report what fraction of the
answer the retrieval layer could physically carry.

    recall_ceiling = |truth & retrieved| / |truth|

It is a CEILING, not a score. A model composing over those hits can only do
worse, never better -- it cannot cite what it never saw. So a low ceiling is
proof of a gap, while a high ceiling proves only that retrieval is not the
bottleneck. No model is called at any point in this measurement.

Why one deep query instead of a sweep over k
--------------------------------------------
`scripts/memory-index.py` applies `top_k` as the last operation, truncating an
already-fused, already-sorted list; the internal fusion caps do not depend on it.
So the top-20 of a k=200 query IS the k=20 query, and every point of the curve
follows from one deep call by arithmetic. Measured 2026-08-13, the pool saturates
around 116 hits and stops growing. Recording the RANK of each truth item is
therefore strictly more informative than four separate numbers, at a quarter of
the calls.

The verdict rule is fixed BEFORE any run, in
`plans/2026-08-13-census-acceptance-benchmark.md`, so a number cannot be fitted
to a preferred outcome afterwards.

Usage:
    python scripts/census-bench.py --show-truth
    python scripts/census-bench.py --baseline
    python scripts/census-bench.py --score answers.json
    python scripts/census-bench.py --operating-point
    python scripts/census-bench.py --recall-crosscheck [--crosscheck-answers FILE]

Exit codes. They exist for scripting, so they are listed per mode: 1 is a real
outcome everywhere it appears, never an error.

    0  the run produced the favourable reading
    1  the run produced the unfavourable one, which differs by mode:
         --baseline           verdict FIX-RECALL, or the RECALL-BROKEN flag
         --score              verdict REJECTED, or NOT-COMPARABLE (which
                              includes "no baseline report to compare against")
         --recall-crosscheck  the ceiling's meaning as an upper bound was
                              contradicted by at least one answer
    2  instrument failure: empty truth, index missing, too few measurable
       questions, an unreadable or malformed input file, or a mode that is not
       implemented yet
    3  the retrieval layer could not be called

Until 2026-08-24 this table named only the two --baseline outcomes and gave
them as the whole meaning of 1, so a wrapper reading exit codes labelled a
NOT-COMPARABLE acceptance run, or a falsified ceiling, as FIX-RECALL.

Tests: tests/test_a_typo_that_was_filed_as_a_falsified_benchmark.py
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.atomic import atomic_write_text
from scripts.utils.census_oracles import (
    CorpusPaths,
    OracleAnswer,
    UnreadableCorpus,
    resolve,
)
from scripts.utils.census_state import (
    ORACLE_PINS,
    PINNED_KEYS,  # noqa: F401 - re-exported so tests pin the pin set from one place
    RETRIEVAL_PINS,
    run_state,
    states_comparable,
)
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_default_tz, get_outputs_dir, get_workspace_root

# ============================================================
# Configuration -- the pre-registered rule
# ============================================================

QUESTIONS_FILE = "config/census-bench-questions.json"

# Query depth. Well past the measured saturation point (116 on 2026-08-13) so the
# pool can be observed to stop growing rather than assumed to.
QUERY_DEPTH = 200
# Threshold 0.0 disables the salience gate: this measures the reachable ceiling,
# not the default posture. Using the default 0.55 would confound "retrieval is
# blind" with "the operator's configured confidence gate is strict".
QUERY_THRESHOLD = 0.0
QUERY_COLLECTION = "all"

# The verdict rule. Written down before the first run; see the plan's decision 6.
BUILD_BELOW = 0.30
FIX_RECALL_AT_OR_ABOVE = 0.70
CONTROL_HEALTHY_AT_OR_ABOVE = 0.80
MIN_MEASURABLE_AGGREGATES = 6

VERDICT_BUILD = "BUILD"
VERDICT_FIX_RECALL = "FIX-RECALL"
VERDICT_NARROW = "NARROW"
FLAG_RECALL_BROKEN = "RECALL-BROKEN"

# ------------------------------------------------------------
# The ACCEPTANCE rule (--score), restated by class before the first scored run.
#
# The concept pre-registered "8 of 10 aggregating questions correct, 0
# confidently-wrong". The 2026-08-13 measurement then split the aggregate set by
# class - traversal 0.054, cross_source 0.667 - and the build was scoped to the
# traversal class alone. Gating on the 10-question aggregate would then do one
# of two wrong things: let three cross_source wins carry a traversal failure, or
# fail the build for a class it was explicitly told not to serve.
#
# So the gate is the traversal class, and the number is proportional to the
# original: 8 of 10 becomes 6 of 7.
#
# cross_source carried one further condition when it was written - "must not
# fall below the incumbent path" - and that condition was never implemented in
# code. It is RETIRED rather than left claimed, because on 2026-08-13 the class
# stopped being graded at all: its three questions disagree on how to enumerate
# the pipeline table, a rule the question never states, so their score measured
# the wording and not the primitive. A condition on a score nobody computes is a
# sentence, not a rule. The class keeps its measured retrieval ceiling, which is
# the evidence for the Non-Goal, and `not_scored` names the withheld ids on
# every run.
#
# This is a deviation from the concept's stated number and is recorded as one in
# `plans/2026-08-13-census-primitive.md`. It was written down before the first
# scored run and is not moved afterwards, whatever the run returns.
# ------------------------------------------------------------
ACCEPT_TRAVERSAL_AT_LEAST = 6
ACCEPT_TRAVERSAL_OF = 7
ACCEPT_CONFIDENTLY_WRONG_MAX = 0

VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_REJECTED = "REJECTED"
VERDICT_NOT_COMPARABLE = "NOT-COMPARABLE"

GATED_CLASS = "traversal"


# ============================================================
# Result records
# ============================================================

@dataclass
class QuestionResult:
    id: str
    group: str
    question_class: str
    truth_cardinality: int
    truth_paths: list[str]
    truth_value: Any
    # rank of each truth path in the saturated ranked list; None = absent entirely
    ranks: dict[str, int | None] = field(default_factory=dict)
    retrieval_pool_size: int = 0
    recall_ceiling: float = 0.0
    # smallest depth at which the whole truth set is present; None if never
    k_min_full: int | None = None
    measurable: bool = True
    unmeasurable_reason: str = ""
    elapsed_s: float = 0.0
    language_used: str = ""
    collections: dict[str, int] = field(default_factory=dict)
    # Reserved for --score at step 2. Empty on a baseline run by design: changing
    # the report schema between baseline and acceptance would destroy comparability.
    verdict_per_question: str = ""

    def ceiling_at(self, k: int) -> float:
        """The ceiling that a query of depth k would have produced."""
        if not self.truth_cardinality:
            return 0.0
        found = sum(1 for r in self.ranks.values() if r is not None and r < k)
        return found / self.truth_cardinality


# ============================================================
# Truth loading -- shared by every mode
# ============================================================

def load_questions(root: Path) -> list[dict]:
    path = root / QUESTIONS_FILE
    if not path.exists():
        raise FileNotFoundError(f"question set not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["questions"]


def load_truth(questions: list[dict], corpus: CorpusPaths,
               today: date) -> dict[str, OracleAnswer]:
    """Compute every question's truth, refusing a degenerate one at either end.

    The guard lives here rather than in --show-truth because --baseline runs
    standalone: an empty truth set downstream is a division by zero at best and a
    silent 0.0 ceiling at worst, and a 0.0 that means "the question was bad" is
    indistinguishable in a report from a 0.0 that means "retrieval is blind".

    Two ends, not one. The empty end shipped first and caught two questions on
    2026-08-13 (agg-02, agg-10). The saturated end was added the same day, after
    `oracle_agg_06` was found selecting all seven of its seven candidates: no
    counterparty string in the live corpus matched a card verbatim, so the
    predicate was constant-true and the question had quietly become "which
    active threads name a counterparty at all". An oracle that selects its whole
    population measures the population, and a traversal that answers the question
    as written is then marked wrong for being right.

    An oracle that declares no population is NOT checked at the saturated end,
    and `unchecked` says which ones, so the caller never reads this guard as
    wider coverage than it has.
    """
    truth: dict[str, OracleAnswer] = {}
    empty: list[str] = []
    saturated: list[str] = []
    unchecked: list[str] = []
    for question in questions:
        answer = resolve(question["oracle"])(corpus, today)
        if answer.is_empty():
            empty.append(question["id"])
        elif answer.is_saturated():
            saturated.append(f"{question['id']} ({answer.selected}/{answer.population})")
        elif answer.population is None:
            unchecked.append(question["id"])
        truth[question["id"]] = answer
    if empty:
        raise ValueError(
            "empty truth for question(s): " + ", ".join(empty) +
            " -- a question whose oracle finds nothing measures nothing; "
            "rewrite the question or fix the oracle before running",
        )
    if saturated:
        raise ValueError(
            "saturated truth for question(s): " + ", ".join(saturated) +
            " -- the oracle selected its entire candidate population, so its "
            "predicate never fired negative and the question measures the "
            "population instead; fix the oracle before running",
        )
    if unchecked:
        print(f"{GRAY}note: no declared population, saturation unchecked for: "
              f"{', '.join(unchecked)}{RESET}", file=sys.stderr)
    return truth


# ============================================================
# Run state -- what makes two runs comparable
# ============================================================

# `run_state` and `states_comparable` live in `scripts/utils/census_state.py`:
# the engine WRITES a state into its answers file and this scorer READS one, and
# a comparison the whole acceptance rests on must not exist in two copies.


def _run_state(corpus: CorpusPaths, root: Path, today: date) -> dict:
    """Local spelling of the shared helper, so call sites here stay unchanged."""
    return run_state(corpus.root, root, today, tz=get_default_tz())


# ============================================================
# Retrieval measurement
# ============================================================

# Everything a call into `memory-index.py` can fail with, in ONE place, so the
# three call sites cannot drift apart again. They had: `mode_baseline` caught
# `RuntimeError` alone, `mode_operating_point` caught three types, and the
# crosscheck print pass caught nothing. `subprocess.TimeoutExpired` is a
# SubprocessError and NOT a RuntimeError, so the 600-second and 300-second
# timeouts escaped every one of them and surfaced as a traceback with exit 1 —
# where the docstring documents exit 3, "the retrieval layer could not be
# called".
QUERY_FAILURES = (RuntimeError, OSError, json.JSONDecodeError, subprocess.SubprocessError)


def query_index(root: Path, text: str, depth: int = QUERY_DEPTH) -> tuple[list[dict], float]:
    """One deep query. Returns (hits, elapsed_seconds).

    Argument list, never a shell string: the question text is corpus-derived and
    must never be parsed by a shell.
    """
    cmd = [
        sys.executable, str(root / "scripts" / "memory-index.py"), "query", text,
        "--json", "--top-k", str(depth),
        "--threshold", str(QUERY_THRESHOLD), "--collection", QUERY_COLLECTION,
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(
            f"memory-index query failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:300]}",
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"memory-index returned non-JSON: {exc}") from exc
    return payload.get("hits", []), elapsed


def measure_question(root: Path, question: dict, answer: OracleAnswer) -> QuestionResult:
    """Measure one question in both languages, keeping the better result.

    The index is cross-lingual, so penalising it for the language the question
    happened to be written in would measure the phrasing, not the retrieval.
    """
    result = QuestionResult(
        id=question["id"], group=question["group"],
        question_class=question.get("question_class", ""),
        truth_cardinality=answer.cardinality,
        truth_paths=sorted(answer.paths),
        truth_value=answer.value,
    )
    best: tuple[float, dict[str, int | None], int, float, str, dict[str, int]] | None = None
    for lang_key, lang in (("question_ru", "ru"), ("question_en", "en")):
        text = question.get(lang_key)
        if not text:
            continue
        hits, elapsed = query_index(root, text)
        # FIRST occurrence wins. A dict comprehension keeps the LAST index for
        # a duplicated path, so a truth item returned twice was recorded at its
        # worst rank — overstating the rank and understating every k_min_full
        # and ceiling derived from it.
        order: dict[str, int] = {}
        for i, hit in enumerate(hits):
            path = hit.get("path")
            if path and path not in order:
                order[path] = i
        ranks = {p: order.get(p) for p in sorted(answer.paths)}
        found = sum(1 for r in ranks.values() if r is not None)
        ceiling = found / answer.cardinality if answer.cardinality else 0.0
        collections: dict[str, int] = {}
        for hit in hits:
            key = str(hit.get("collection") or hit.get("layer") or "?")
            collections[key] = collections.get(key, 0) + 1
        if best is None or ceiling > best[0]:
            best = (ceiling, ranks, len(hits), elapsed, lang, collections)

    if best is None:
        raise ValueError(f"{question['id']}: no question text in any language")

    ceiling, ranks, pool, elapsed, lang, collections = best
    result.recall_ceiling = round(ceiling, 4)
    result.ranks = ranks
    result.retrieval_pool_size = pool
    result.elapsed_s = round(elapsed, 3)
    result.language_used = lang
    result.collections = collections

    found_ranks = [r for r in ranks.values() if r is not None]
    if found_ranks and len(found_ranks) == answer.cardinality:
        result.k_min_full = max(found_ranks) + 1

    if answer.cardinality > pool:
        result.measurable = False
        result.unmeasurable_reason = (
            f"truth cardinality {answer.cardinality} exceeds the saturated "
            f"retrieval pool of {pool}: the ceiling would be capped by the size "
            f"of the output, not by the quality of retrieval"
        )
    return result


# ============================================================
# The pre-registered verdict
# ============================================================

def verdict(agg_mean: float | None, ctl_mean: float | None,
            n_measurable: int) -> tuple[str, str]:
    """Apply the rule fixed before the run. Pure: reads no file, no clock.

    Returns (verdict, the rule that selected it).
    """
    if n_measurable < MIN_MEASURABLE_AGGREGATES:
        return "NO-VERDICT", (
            f"measurable aggregating questions {n_measurable} < "
            f"{MIN_MEASURABLE_AGGREGATES}: the question set is unfit, rewrite "
            f"questions to have smaller truth sets rather than reading a verdict "
            f"off too few points"
        )
    # A run with no measurable control reached a BUILD verdict with no
    # index-health check at all: `ctl_mean is None` skipped the guard entirely,
    # so the one condition that says "the index works" was silently optional.
    # An unchecked index is not a healthy index.
    if ctl_mean is None:
        return "NO-VERDICT", (
            "no control question produced a ceiling, so the index was never "
            "shown to work; an aggregating verdict beside an unchecked index "
            "measures the index, not the gap"
        )
    if ctl_mean < CONTROL_HEALTHY_AT_OR_ABOVE:
        return FLAG_RECALL_BROKEN, (
            f"control mean {ctl_mean:.3f} < {CONTROL_HEALTHY_AT_OR_ABOVE}: the "
            f"index cannot reliably reach single facts, so any aggregating "
            f"verdict beside it would be a comparison against a broken index"
        )
    if agg_mean is None:
        return "NO-VERDICT", "no measurable aggregating question produced a ceiling"
    if agg_mean < BUILD_BELOW:
        return VERDICT_BUILD, (
            f"aggregate mean {agg_mean:.3f} < {BUILD_BELOW}: retrieval cannot "
            f"carry the answer even at saturation depth"
        )
    if agg_mean >= FIX_RECALL_AT_OR_ABOVE:
        return VERDICT_FIX_RECALL, (
            f"aggregate mean {agg_mean:.3f} >= {FIX_RECALL_AT_OR_ABOVE}: "
            f"retrieval already reaches the answer; the gap, if any, is not "
            f"retrieval and /census would be the wrong fix"
        )
    return VERDICT_NARROW, (
        f"{BUILD_BELOW} <= aggregate mean {agg_mean:.3f} < "
        f"{FIX_RECALL_AT_OR_ABOVE}: retrieval carries some classes and not "
        f"others; scope /census to the questions whose ceiling is below "
        f"{BUILD_BELOW}"
    )


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


# ============================================================
# Reporting
# ============================================================

def _fmt_ceiling(value: float) -> str:
    colour = RED if value < BUILD_BELOW else (YELLOW if value < FIX_RECALL_AT_OR_ABOVE else GREEN)
    return f"{colour}{value:5.2f}{RESET}"


def print_table(results: list[QuestionResult]) -> None:
    print(f"\n{BOLD}{'id':<8} {'группа':<10} {'класс':<13} "
          f"{'|истина|':>8} {'пул':>5} {'потолок':>8} {'k_full':>7} {'сек':>6}{RESET}")
    for r in results:
        k_full = str(r.k_min_full) if r.k_min_full is not None else "-"
        flag = f" {RED}UNMEASURABLE{RESET}" if not r.measurable else ""
        print(f"{r.id:<8} {r.group:<10} {r.question_class:<13} "
              f"{r.truth_cardinality:>8} {r.retrieval_pool_size:>5} "
              f"{_fmt_ceiling(r.recall_ceiling):>8} {k_full:>7} "
              f"{r.elapsed_s:>6.2f}{flag}")


def build_report(results: list[QuestionResult], state: dict,
                 verdict_name: str, rule: str,
                 agg_mean: float | None, ctl_mean: float | None,
                 n_unmeasurable: int) -> dict:
    latencies = [r.elapsed_s for r in results]
    return {
        "schema_version": 1,
        "mode": "baseline",
        "generated": datetime.now(get_default_tz()).isoformat(),
        "run_state": state,
        "verdict": verdict_name,
        "verdict_rule": rule,
        "aggregate_mean_ceiling": round(agg_mean, 4) if agg_mean is not None else None,
        "control_mean_ceiling": round(ctl_mean, 4) if ctl_mean is not None else None,
        # The per-class split carries more than the mean does. The verdict rule is
        # deliberately not computed from it -- the rule was pre-registered on the
        # aggregate mean and re-deriving it here would be fitting a threshold to a
        # result -- but a single mean over two classes that behave oppositely
        # would hide the actual finding, so both are reported.
        "aggregate_mean_by_class": {
            cls: round(m, 4)
            for cls in sorted({r.question_class for r in results if r.group == "aggregate"})
            if (m := _mean([r.recall_ceiling for r in results
                            if r.group == "aggregate" and r.measurable
                            and r.question_class == cls])) is not None
        },
        "unmeasurable_count": n_unmeasurable,
        "thresholds": {
            "build_below": BUILD_BELOW,
            "fix_recall_at_or_above": FIX_RECALL_AT_OR_ABOVE,
            "control_healthy_at_or_above": CONTROL_HEALTHY_AT_OR_ABOVE,
            "min_measurable_aggregates": MIN_MEASURABLE_AGGREGATES,
        },
        "query": {
            "depth": QUERY_DEPTH, "threshold": QUERY_THRESHOLD,
            "collection": QUERY_COLLECTION,
        },
        "latency_baseline_s": {
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
            "per_question": {r.id: r.elapsed_s for r in results},
        },
        "questions": [asdict(r) for r in results],
    }


def render_markdown(report: dict) -> str:
    state = report["run_state"]
    lines = [
        "# Приёмочный бенчмарк /census: базовая линия",
        "",
        f"Дата прогона: {state['today']}",
        f"Вердикт: **{report['verdict']}**",
        "",
        f"> {report['verdict_rule']}",
        "",
        "## Состояние прогона",
        "",
        "Сравнение с другим прогоном действительно, только если совпадают все четыре.",
        "",
        "| Величина | Значение |",
        "| --- | --- |",
        f"| SHA корпуса | `{state['corpus_sha'][:12]}`{' (дерево грязное)' if state['corpus_dirty'] else ''} |",
        f"| Дата прогона | {state['today']} |",
        f"| sha256 конфига индекса | `{state['index_config_sha256']}` |",
        f"| Сборка индекса | {state['index_built']} |",
        "",
        "## Результаты",
        "",
        "`потолок` это доля истины, которую выдача физически способна донести на "
        "глубине насыщения. Это верхняя граница: модель поверх неё может только "
        "ухудшить, процитировать невиданное она не может.",
        "",
        "| id | группа | класс | мощность истины | пул | потолок | k_full | сек |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for q in report["questions"]:
        k_full = q["k_min_full"] if q["k_min_full"] is not None else "-"
        mark = " **UNMEASURABLE**" if not q["measurable"] else ""
        lines.append(
            f"| {q['id']} | {q['group']} | {q['question_class']} | "
            f"{q['truth_cardinality']} | {q['retrieval_pool_size']} | "
            f"{q['recall_ceiling']:.2f} | {k_full} | {q['elapsed_s']:.2f}{mark} |",
        )
    agg = report["aggregate_mean_ceiling"]
    ctl = report["control_mean_ceiling"]
    lat = report["latency_baseline_s"]
    by_class = report.get("aggregate_mean_by_class", {})
    lines += [
        "",
        f"Среднее по измеримым агрегирующим: **{agg if agg is not None else 'n/a'}**  ",
        f"Среднее по контрольным: **{ctl if ctl is not None else 'n/a'}**  ",
        f"Исключено как UNMEASURABLE: **{report['unmeasurable_count']}**",
        "",
        "### По классам вопроса",
        "",
        "Вердикт считается по общему среднему, как записано до прогона. Но два "
        "класса ведут себя противоположно, и одно среднее по ним обоим прячет "
        "именно то, ради чего замер затевался.",
        "",
        "| класс | среднее |",
        "| --- | --- |",
        *[f"| {cls} | {value} |" for cls, value in sorted(by_class.items())],
        "",
        "## Базовая линия латентности",
        "",
        f"Медиана {lat['median']} с, максимум {lat['max']} с на вопрос. "
        "Сравнивать не с чем, пока нет движка; число фиксируется здесь, чтобы "
        "приёмке шага 2 было с чем сравнивать.",
        "",
        "## Как читать класс вопроса",
        "",
        "- `traversal` - ответ требует обойти много файлов. Потолок ниже 1.0 это "
        "доказательство, что топ-K не доносит ответ.",
        "- `cross_source` - ответ это отношение между двумя небольшими плотными "
        "файлами. Потолок будет около 1.0 и говорит лишь то, что узкое место не "
        "в извлечении. Неверный ответ при потолке 1.0 локализует отказ в "
        "рассуждении, а там SRLM предупреждает, что примитив обхода ВРЕДИТ.",
        "",
    ]
    return "\n".join(lines)


# ============================================================
# Modes
# ============================================================

def mode_show_truth(questions: list[dict], corpus: CorpusPaths, today: date) -> int:
    truth = load_truth(questions, corpus, today)
    print(f"{BOLD}{'id':<8} {'группа':<10} {'вид':<7} {'|paths|':>8} {'value':>7}  пример{RESET}")
    for question in questions:
        answer = truth[question["id"]]
        sample = sorted(answer.paths)[0] if answer.paths else ""
        if len(sample) > 52:
            sample = "..." + sample[-49:]
        print(f"{question['id']:<8} {question['group']:<10} {answer.kind:<7} "
              f"{answer.cardinality:>8} {str(answer.value):>7}  {GRAY}{sample}{RESET}")
    print(f"\n{GREEN}Все {len(questions)} истин непустые.{RESET}")
    return 0


def mode_baseline(questions: list[dict], corpus: CorpusPaths, root: Path,
                  today: date, write: bool) -> int:
    truth = load_truth(questions, corpus, today)
    state = _run_state(corpus, root, today)

    index_db = corpus.root / ".memory-index" / "index.db"
    if not index_db.exists():
        print(f"{RED}Индекс не собран:{RESET} {index_db} отсутствует. "
              f"Запусти `python3 scripts/memory-index.py build`.", file=sys.stderr)
        return 2

    results: list[QuestionResult] = []
    for question in questions:
        try:
            results.append(measure_question(root, question, truth[question["id"]]))
        except QUERY_FAILURES as exc:
            print(f"{RED}Запрос не удался на {question['id']}:{RESET} {exc!r}", file=sys.stderr)
            return 3

    print_table(results)

    agg = [r for r in results if r.group == "aggregate" and r.measurable]
    ctl = [r for r in results if r.group == "control" and r.measurable]
    n_unmeasurable = sum(1 for r in results if not r.measurable)
    agg_mean = _mean([r.recall_ceiling for r in agg])
    ctl_mean = _mean([r.recall_ceiling for r in ctl])

    verdict_name, rule = verdict(agg_mean, ctl_mean, len(agg))

    print(f"\n{BOLD}Среднее по измеримым агрегирующим:{RESET} "
          f"{agg_mean if agg_mean is None else f'{agg_mean:.3f}'} "
          f"{GRAY}({len(agg)} из {sum(1 for r in results if r.group == 'aggregate')}){RESET}")
    print(f"{BOLD}Среднее по контрольным:{RESET} "
          f"{ctl_mean if ctl_mean is None else f'{ctl_mean:.3f}'}")
    print(f"{BOLD}Исключено как UNMEASURABLE:{RESET} {n_unmeasurable}")
    print(f"\n{BOLD}ВЕРДИКТ: {CYAN}{verdict_name}{RESET}")
    print(f"{GRAY}{rule}{RESET}")

    report = build_report(results, state, verdict_name, rule, agg_mean, ctl_mean, n_unmeasurable)
    if write:
        out_dir = get_outputs_dir() / "operations" / "census-bench"
        stem = f"{today.isoformat()}_bench_census-baseline"
        atomic_write_text(out_dir / f"{stem}.json",
                          json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(out_dir / f"{stem}.md", render_markdown(report))
        print(f"\n{GREEN}Отчёт:{RESET} {out_dir / (stem + '.md')}")

    if verdict_name in (VERDICT_FIX_RECALL, FLAG_RECALL_BROKEN):
        return 1
    if verdict_name == "NO-VERDICT":
        return 2
    return 0


# ============================================================
# Scoring /census answers against the same truth
# ============================================================

STATUS_CORRECT = "correct"
STATUS_WRONG = "wrong"
STATUS_CONFIDENTLY_WRONG = "confidently-wrong"
STATUS_REFUSED = "refused"
# A question the BASELINE measures but /census is deliberately not graded on.
# Distinct from "wrong" on purpose: a zero that measures an underspecified
# question reads, in a report six months from now, exactly like a zero that
# measures a weak primitive. Naming the difference is the whole point.
STATUS_NOT_SCORED = "not-scored"


def _corpus_files(corpus: CorpusPaths) -> set[str]:
    """Every data-root-relative path that exists, for the fabrication check."""
    root = corpus.root.resolve()
    return {p.resolve().relative_to(root).as_posix()
            for p in root.rglob("*") if p.is_file()}


def grade_one(answer_record: dict, truth: OracleAnswer,
              existing: set[str]) -> tuple[str, str]:
    """(status, why) for one answer.

    A REFUSAL is not a wrong answer and is never counted as one. That asymmetry
    is the whole point of the fabrication column: a primitive that says "I could
    not do this" is usable, and one that says "thirteen" when the answer is four
    is not, even though both are un-correct.
    """
    if answer_record.get("answer") is None:
        return STATUS_REFUSED, (answer_record.get("error") or "no answer given")

    answer = answer_record["answer"]
    if not isinstance(answer, dict):
        # An LLM-emitted answers file can carry anything here. This used to
        # reach `.get` on a string or a list and die with AttributeError, on
        # the acceptance path, with a traceback and exit 1.
        return STATUS_WRONG, f"answer is a {type(answer).__name__}, not an object"
    kind = answer.get("kind")

    # `_seq` and not `answer.get("sources", [])`: the default applies only when
    # the key is ABSENT. `"sources": null` is a realistic LLM-emitted shape, and
    # it returned None, which `set()` and iteration both refuse with TypeError.
    def _seq(key: str) -> list:
        value = answer.get(key)
        return value if isinstance(value, list) else []

    cited = [s for s in _seq("sources") if isinstance(s, str)]
    invented = [s for s in cited if s not in existing]
    if kind == "paths":
        invented += [p for p in _seq("paths") if p not in existing]

    if kind == "count" and truth.kind == "count":
        correct = answer.get("value") == truth.value
    elif kind == "count" and truth.kind == "paths":
        # A cardinality answer to a "which ones" question is answering half the
        # question; graded on the half it answered.
        correct = answer.get("value") == len(truth.paths)
    elif kind == "paths" and truth.kind == "paths":
        correct = set(_seq("paths")) == set(truth.paths)
    elif kind == "pairs" and truth.kind == "pairs":
        correct = ({tuple(p) for p in _seq("pairs") if isinstance(p, (list, tuple))}
                   == {tuple(p) for p in (truth.value or [])})
    else:
        return STATUS_WRONG, f"answer kind {kind!r} does not answer a {truth.kind!r} question"

    if correct and invented:
        # The number is right and a cited file does not exist. Treat it as
        # fabrication rather than as correct: the citation is what makes an
        # answer checkable, and an uncheckable right answer is luck.
        return STATUS_CONFIDENTLY_WRONG, f"cites files that do not exist: {invented[:3]}"
    if correct:
        return STATUS_CORRECT, ""
    if invented:
        return STATUS_CONFIDENTLY_WRONG, (
            f"wrong, and cites files that do not exist: {invented[:3]}")
    return STATUS_WRONG, "does not match the oracle"


def score_answers(answers_path: str, today: date | None = None) -> dict:
    """Grade an answers file per question class. Returns the report dict."""
    root = get_workspace_root()
    corpus = CorpusPaths.from_workspace()
    payload = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    # ValueError, so `main` reports it and returns the documented exit 2. Only
    # the per-answer records were hardened when `--score` moved inside that try
    # (`"sources": null`, a non-dict `answer`); the two levels ABOVE them were
    # left as they were, and `json.loads` accepts any JSON value. A file that is
    # a bare list, or an `answers` object instead of a list, or one bad record
    # among good ones, reached `.get` on a non-mapping and died with an
    # AttributeError traceback and exit 1 — which the docstring assigns to a
    # real benchmark verdict, so a harness reading exit codes scores the crash
    # as a result. This is the acceptance gate; it refuses, it does not guess.
    if not isinstance(payload, dict):
        raise ValueError(f"файл ответов должен быть объектом, "
                         f"получено {type(payload).__name__}")
    records = payload.get("answers", [])
    if not isinstance(records, list):
        raise ValueError(f"поле answers должно быть списком, "
                         f"получено {type(records).__name__}")
    malformed = [i for i, a in enumerate(records) if not isinstance(a, dict)]
    if malformed:
        raise ValueError(f"записи ответов должны быть объектами; не объекты "
                         f"на позициях {malformed}")
    # A dict comprehension keeps the LAST record per id, so an answers file
    # assembled by merging two runs silently discarded one of them and the
    # verdict turned on file order. This module's own principle is that a
    # dropped measurement is named, never silent, and it applies that rule to
    # `not_scored` rows and to operator input elsewhere. The graded denominator
    # comes from the question list, so nothing downstream would ever have
    # noticed. A record with no usable id is the same defect wearing a
    # different hat: it lands under the key None, matches no question, and is
    # scored as absent.
    ids = [a.get("question_id") for a in records]
    unusable = sorted(i for i, qid in enumerate(ids)
                      if not isinstance(qid, str) or not qid)
    if unusable:
        raise ValueError(f"у записей ответов нет question_id "
                         f"на позициях {unusable}")
    duplicated = sorted({qid for qid in ids if ids.count(qid) > 1})
    if duplicated:
        raise ValueError(f"question_id повторяется: {', '.join(duplicated)}; "
                         f"один вопрос - одна запись")
    answers = {a["question_id"]: a for a in records}

    stated = payload.get("run_state") or {}
    if not isinstance(stated, dict):
        raise ValueError(f"поле run_state должно быть объектом, "
                         f"получено {type(stated).__name__}")
    graded_today = today or _today_from(stated)
    question_list = load_questions(root)
    truth = load_truth(question_list, corpus, graded_today)
    questions = {q["id"]: q for q in question_list}
    existing = _corpus_files(corpus)

    baseline, comparable, diverged = _baseline_comparison(stated)

    per_question = []
    for qid, question in questions.items():
        record = answers.get(qid)
        if question.get("census_scored") is False:
            # The ceiling for this question is still measured by --baseline; only
            # the /census grade is withheld, and the reason travels with the row.
            per_question.append({"id": qid, "group": question["group"],
                                 "question_class": question.get("question_class", ""),
                                 "status": STATUS_NOT_SCORED,
                                 "why": question.get("not_scored_because", ""),
                                 "elapsed_s": (record or {}).get("elapsed_s")})
            continue
        if record is None:
            per_question.append({"id": qid, "group": question["group"],
                                 "question_class": question.get("question_class", ""),
                                 "status": STATUS_REFUSED, "why": "not answered",
                                 "elapsed_s": None})
            continue
        status, why = grade_one(record, truth[qid], existing)
        per_question.append({
            "id": qid,
            "group": question["group"],
            "question_class": question.get("question_class", ""),
            "status": status,
            "why": why,
            "elapsed_s": record.get("elapsed_s"),
        })

    per_class: dict[str, dict] = {}
    not_scored = [r["id"] for r in per_question if r["status"] == STATUS_NOT_SCORED]
    for row in per_question:
        if row["status"] == STATUS_NOT_SCORED:
            continue
        # Controls carry a `question_class` too, and on this set it is
        # "traversal". Bucketing on that field alone merged them into the gated
        # class and reported traversal 10/12 - five control wins carrying the
        # gate, which is the exact failure the class split exists to prevent.
        # The group decides first; the class only subdivides the aggregates.
        key = "control" if row["group"] == "control" else (
            row["question_class"] or row["group"])
        bucket = per_class.setdefault(key, {"n": 0, "correct": 0,
                                            "confidently_wrong": 0, "refused": 0})
        bucket["n"] += 1
        if row["status"] == STATUS_CORRECT:
            bucket["correct"] += 1
        elif row["status"] == STATUS_CONFIDENTLY_WRONG:
            bucket["confidently_wrong"] += 1
        elif row["status"] == STATUS_REFUSED:
            bucket["refused"] += 1
    for bucket in per_class.values():
        bucket["accuracy"] = (bucket["correct"] / bucket["n"]) if bucket["n"] else None

    confidently_wrong = sum(1 for r in per_question
                            if r["status"] == STATUS_CONFIDENTLY_WRONG)
    elapsed = [r["elapsed_s"] for r in per_question if r["elapsed_s"] is not None]

    verdict_name, verdict_why = acceptance_verdict(
        per_class, confidently_wrong, comparable, diverged)

    return {
        "schema_version": 1,
        "mode": "score",
        "generated": datetime.now(tz=get_default_tz()).isoformat(),
        "answers_file": str(answers_path),
        "run_state": stated,
        "baseline_run_state": (baseline or {}).get("run_state"),
        "states_comparable": comparable,
        "diverged_keys": diverged,
        "retrieval_pins_diverged": states_comparable(
            stated, (baseline or {}).get("run_state") or {}, RETRIEVAL_PINS)[1],
        "per_class": per_class,
        "not_scored": not_scored,
        "questions": per_question,
        "confidently_wrong": confidently_wrong,
        "latency_median_s": statistics.median(elapsed) if elapsed else None,
        "baseline_latency_median_s": (baseline or {}).get("latency_baseline_s"),
        "baseline_mean_by_class": (baseline or {}).get("aggregate_mean_by_class"),
        "verdict": verdict_name,
        "verdict_rule": (
            f">= {ACCEPT_TRAVERSAL_AT_LEAST} of {ACCEPT_TRAVERSAL_OF} on the "
            f"{GATED_CLASS} class AND <= {ACCEPT_CONFIDENTLY_WRONG_MAX} "
            "confidently-wrong; cross_source has its ceiling measured by "
            "--baseline and is not graded here"),
        "verdict_why": verdict_why,
    }


def acceptance_verdict(per_class: dict, confidently_wrong: int,
                       comparable: bool, diverged: list[str]) -> tuple[str, str]:
    """The pre-registered rule, applied. Pure, so a test can pin it."""
    if not comparable:
        return VERDICT_NOT_COMPARABLE, (
            "the answers were produced against a different world than the "
            f"baseline ({', '.join(diverged)} diverge); grading them against the "
            "baseline's numbers would compare two corpora, not two methods")
    gated = per_class.get(GATED_CLASS) or {}
    correct = gated.get("correct", 0)
    n = gated.get("n", 0)
    # The denominator is part of the pre-registered rule, not decoration. Without
    # this check "6 of 7" quietly becomes "6 of N" the moment a question is added
    # to or dropped from the gated class, and the threshold stops meaning what was
    # written down before the run.
    if n != ACCEPT_TRAVERSAL_OF:
        return VERDICT_NOT_COMPARABLE, (
            f"the {GATED_CLASS} class holds {n} question(s), not the "
            f"{ACCEPT_TRAVERSAL_OF} the threshold was pre-registered against; "
            "the rule cannot be applied to a set it was not written for")
    if confidently_wrong > ACCEPT_CONFIDENTLY_WRONG_MAX:
        return VERDICT_REJECTED, (
            f"{confidently_wrong} confidently-wrong answer(s); a confident wrong "
            "answer is worse than an honest refusal and the rule treats it so")
    if correct < ACCEPT_TRAVERSAL_AT_LEAST:
        return VERDICT_REJECTED, (
            f"{GATED_CLASS} {correct}/{gated.get('n', 0)} correct, below the "
            f"pre-registered {ACCEPT_TRAVERSAL_AT_LEAST}/{ACCEPT_TRAVERSAL_OF}")
    return VERDICT_ACCEPTED, (
        f"{GATED_CLASS} {correct}/{gated.get('n', 0)} correct with "
        f"{confidently_wrong} confidently-wrong")


def _today_from(state: dict) -> date:
    raw = state.get("today")
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return datetime.now(tz=get_default_tz()).date()


def _baseline_comparison(stated: dict) -> tuple[dict | None, bool, list[str]]:
    """Load the baseline report and compare pinned state against it."""
    baseline_dir = get_outputs_dir() / "operations" / "census-bench"
    candidates = sorted(baseline_dir.glob("*_bench_census-baseline.json"))
    if not candidates:
        return None, False, ["baseline report absent"]
    baseline = json.loads(candidates[-1].read_text(encoding="utf-8"))
    base_state = baseline.get("run_state") or {}
    # Only the ORACLE pins gate this comparison. /census answers are produced by
    # reading files, so a rebuilt index cannot change whether they are right -
    # and on the first scored run a rebuilt index alone was enough to refuse a
    # perfectly valid report.
    comparable, diverged = states_comparable(stated, base_state, ORACLE_PINS)
    return baseline, comparable, diverged


# ============================================================
# The two obligations step 1 left open
# ============================================================

# `/recall`'s shipped defaults. The baseline measured at saturation (top_k 200,
# threshold 0.0) because it wanted the CEILING; this is the OPERATING point,
# which is a different question and, on the control group, a different answer.
OPERATING_TOP_K = 8
OPERATING_THRESHOLD = 0.55


def query_at(root: Path, text: str, depth: int, threshold: float) -> list[dict]:
    """One query at an explicit depth and threshold. Argument list, never a shell."""
    cmd = [
        sys.executable, str(root / "scripts" / "memory-index.py"), "query", text,
        "--json", "--top-k", str(depth), "--threshold", str(threshold),
        "--collection", QUERY_COLLECTION,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,  # nosec B603
                          timeout=300, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:])
    return json.loads(proc.stdout).get("hits", [])


def best_ceiling_at(root: Path, question: dict, expected: set[str],
                    depth: int, threshold: float) -> tuple[float, int]:
    """Ceiling over BOTH question languages, keeping the better.

    The index is cross-lingual and the baseline measures it this way. Measuring
    one language here would compare a different quantity to the baseline's and
    call the difference a finding.
    """
    denom = len(expected) or 1
    best = (0.0, 0)
    for key in ("question_ru", "question_en"):
        text = question.get(key)
        if not text:
            continue
        hits = query_at(root, text, depth, threshold)
        found = len(expected & {h.get("path") for h in hits}) / denom
        if found >= best[0]:
            best = (found, len(hits))
    return best


def mode_operating_point(questions: list[dict], corpus: CorpusPaths,
                         root: Path, today: date, write: bool = True) -> int:
    """Obligation 2: what the ceiling is where /recall actually runs.

    The baseline reported the control group 5 of 5 at 1.00 and concluded the
    RECALL-BROKEN flag stayed down. True, and narrower than it reads. That
    measurement was taken at saturation depth with the salience gate disabled;
    at the shipped defaults, ctl-02 sits at rank 87 of a 102-hit pool and ctl-04
    at rank 97 of 109, so neither is returned at all. A guard that cannot tell a
    healthy index from an unreachable answer is not measuring what its name says,
    and this mode is what makes the difference visible instead of implied.
    """
    truth = load_truth(questions, corpus, today)
    rows = []
    print(f"{BOLD}{'id':<8} {'группа':<10} {'класс':<13} "
          f"{'потолок@насыщ':>14} {'потолок@раб':>12}{RESET}")
    for question in questions:
        qid = question["id"]
        expected = truth[qid].paths
        try:
            sat, sat_pool = best_ceiling_at(root, question, expected,
                                            QUERY_DEPTH, QUERY_THRESHOLD)
            op, op_pool = best_ceiling_at(root, question, expected,
                                          OPERATING_TOP_K, OPERATING_THRESHOLD)
        except QUERY_FAILURES as exc:
            print(f"{RED}{qid}: запрос не выполнен: {exc!r}{RESET}", file=sys.stderr)
            return 3
        rows.append({"id": qid, "group": question["group"],
                     "question_class": question.get("question_class", ""),
                     "ceiling_saturated": round(sat, 3),
                     "ceiling_operating": round(op, 3),
                     "pool_saturated": sat_pool, "pool_operating": op_pool})
        colour = RED if (question["group"] == "control" and op < 1.0) else GRAY
        print(f"{qid:<8} {question['group']:<10} "
              f"{question.get('question_class', ''):<13} {sat:>14.3f} "
              f"{colour}{op:>12.3f}{RESET}")

    controls = [r for r in rows if r["group"] == "control"]
    full_at_operating = sum(1 for r in controls if r["ceiling_operating"] >= 1.0)
    print(f"\n  контроль на глубине насыщения: "
          f"{sum(1 for r in controls if r['ceiling_saturated'] >= 1.0)}/{len(controls)}")
    print(f"  контроль на рабочей точке "
          f"(top_k {OPERATING_TOP_K}, threshold {OPERATING_THRESHOLD}): "
          f"{full_at_operating}/{len(controls)}")
    if full_at_operating < len(controls):
        print(f"{YELLOW}Разрыв реален: часть контрольных недосягаема там, где "
              f"/recall работает. «Индекс исправен» этим замером НЕ установлено.{RESET}")

    out_dir = get_outputs_dir() / "operations" / "census-bench"
    stem = (f"{datetime.now(tz=get_default_tz()).date().isoformat()}"
            "_operating-point_census")
    # `--no-write` reached only `--baseline`; this mode wrote its report anyway.
    if not write:
        print(f"{GRAY}--no-write: отчёт не записан{RESET}")
        return 0
    atomic_write_text(out_dir / f"{stem}.json", json.dumps({
        "schema_version": 1, "mode": "operating-point",
        "generated": datetime.now(tz=get_default_tz()).isoformat(),
        "top_k": OPERATING_TOP_K, "threshold": OPERATING_THRESHOLD,
        "run_state": _run_state(corpus, root, today),
        "questions": rows,
        "controls_full_at_saturation":
            sum(1 for r in controls if r["ceiling_saturated"] >= 1.0),
        "controls_full_at_operating": full_at_operating,
        "controls_total": len(controls),
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"\n{GREEN}Отчёт:{RESET} {out_dir / (stem + '.json')}")
    return 0


CROSSCHECK_QUESTIONS = ("agg-05", "agg-03", "ctl-02")

# The hits the print pass showed, carried to the grading pass. Undated on
# purpose: it is a handoff between two invocations of one measurement, not a
# report, and the operator may answer the next morning.
CROSSCHECK_SHOWN_FILE = "recall-crosscheck-shown.json"


def _crosscheck_shown_path() -> Path:
    return get_outputs_dir() / "operations" / "census-bench" / CROSSCHECK_SHOWN_FILE


def mode_recall_crosscheck(questions: list[dict], corpus: CorpusPaths,
                           root: Path, today: date,
                           answers_path: str | None, write: bool = True) -> int:
    """Obligation 1: does the ceiling predict what the MODEL-composed path does?

    Step 1 claimed to have checked this and had not. It queried the raw index at
    /recall's defaults, and `memory-index.py:1118` applies `top_k` as the last
    truncation of an already-fused list, so that query is a strict SUBSET of the
    baseline query: "the real path stays under the ceiling" was true by
    construction, at any index quality. The falsifiable half - at ceiling 0.00
    the answer must be wrong or a refusal - needs a model to answer, and no
    script can supply one.

    So this mode does the half a script can do. It prints each question with the
    material `/recall` would compose over, and the SESSION answers from that
    material and nothing else, exactly as `/recall` does. Given those answers
    back, it grades them against the oracle and records whether the outcome
    matched the ceiling's prediction. The model is in the loop by construction,
    which is the whole point; what is removed is the pretence that it was.

    The two invocations are joined by `recall-crosscheck-shown.json`, written by
    the print pass and read by the grading pass. That file IS the measurement:
    the ceiling must describe the hits the answers came from, not a re-query run
    after the operator went to bed.
    """
    truth = load_truth(questions, corpus, today)
    by_id = {q["id"]: q for q in questions}
    shown_path = _crosscheck_shown_path()

    if answers_path is None:
        shown: dict[str, list[dict]] = {}
        for qid in CROSSCHECK_QUESTIONS:
            question = by_id[qid]
            text = question.get("question_ru") or question.get("question_en")
            if not text:
                # `measure_question` already refuses this; here `None` went
                # straight into a subprocess argv and raised TypeError.
                raise ValueError(f"{qid}: no question text in any language")
            # Guarded like the other two query sites. This one had no try at
            # all, so a non-zero exit from `memory-index.py` (RuntimeError) or
            # a 300-second timeout produced a traceback instead of the exit 3
            # the docstring documents for "the retrieval layer could not be
            # called".
            try:
                hits = query_at(root, text, OPERATING_TOP_K, OPERATING_THRESHOLD)
            except QUERY_FAILURES as exc:
                print(f"{RED}{qid}: запрос не выполнен: {exc!r}{RESET}", file=sys.stderr)
                return 3
            shown[qid] = [{"path": h.get("path"), "score": h.get("score", 0)}
                          for h in hits]
            print(f"\n{BOLD}{qid}{RESET} ({question['group']}, "
                  f"{question.get('question_class', '')}) - {text}")
            print(f"{GRAY}истина: {len(truth[qid].paths)} путь(ей); "
                  f"выдача /recall на рабочей точке: {len(hits)} хит(ов){RESET}")
            for hit in hits:
                print(f"  {hit.get('score', 0):.3f}  {hit.get('path')}")
        # Persisted so the grading pass grades THIS material. See the note on
        # the grading branch below for what re-querying there cost.
        atomic_write_text(shown_path, json.dumps({
            "schema_version": 1,
            "generated": datetime.now(tz=get_default_tz()).isoformat(),
            "run_state": _run_state(corpus, root, today),
            "top_k": OPERATING_TOP_K, "threshold": OPERATING_THRESHOLD,
            "shown": shown,
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"\n{YELLOW}Ответьте на эти три вопроса ТОЛЬКО по показанной "
              f"выдаче, как это делает /recall, и подайте ответы обратно:{RESET}")
        print(f"{GRAY}  --recall-crosscheck --crosscheck-answers FILE{RESET}")
        print(f'{GRAY}  формат: {{"agg-05": {{"kind": "paths", "paths": [...]}}, ...}}'
              f' либо {{"agg-05": {{"refused": true}}}}{RESET}')
        print(f"{GRAY}показанная выдача сохранена: {shown_path}{RESET}")
        return 0

    # The ceiling is computed from the hits the PRINT pass showed, never from a
    # fresh query. Until 2026-08-23 this branch called `query_at` again, minutes
    # or hours later, against a live corpus whose index rebuilds on file change
    # and on a nightly timer. The mode's own falsification rule - at ceiling 0.00
    # the answer must be wrong or a refusal - was then decided by whichever
    # version of the index answered second: a rebuild could manufacture a
    # РАСХОЖДЕНИЕ out of a correct answer composed from three real hits, or bury
    # a real one. A harness that exists to falsify an assumption must not itself
    # be falsifiable by an unrelated background job.
    if not shown_path.is_file():
        print(f"{RED}нет показанной выдачи: {shown_path}{RESET}", file=sys.stderr)
        print(f"{RED}Сначала запустите --recall-crosscheck без --crosscheck-answers, "
              f"ответьте по ТОЙ выдаче, затем подайте ответы.{RESET}", file=sys.stderr)
        return 2
    record = json.loads(shown_path.read_text(encoding="utf-8"))
    # `json.loads` accepts any JSON value, and `score_answers` says exactly this
    # when it justifies its own shape checks. This file is the one the docstring
    # calls THE MEASUREMENT and it sits on disk overnight between the two
    # passes, so a truncated re-save as `[]` is a live case: `record.get` on a
    # list is an AttributeError nothing catches, and exit 1 is the code this
    # mode documents as "the ceiling was contradicted". A corrupt input would
    # have been filed as a falsified benchmark.
    if not isinstance(record, dict):
        print(f"{RED}показанная выдача повреждена: ожидался объект, получено "
              f"{type(record).__name__}: {shown_path}{RESET}", file=sys.stderr)
        return 2
    shown = record.get("shown")
    if shown is None:
        # Absent is INCOMPLETE, not corrupt: fall through to the coverage check
        # below, whose message names the pass to re-run.
        shown = {}
    if not isinstance(shown, dict):
        print(f"{RED}поле shown должно быть объектом, получено "
              f"{type(shown).__name__}: {shown_path}{RESET}", file=sys.stderr)
        return 2
    missing = [q for q in CROSSCHECK_QUESTIONS if q not in shown]
    if missing:
        print(f"{RED}показанная выдача не покрывает {', '.join(missing)}. "
              f"Перезапустите печатающий проход.{RESET}", file=sys.stderr)
        return 2

    comparable, diverged = states_comparable(
        record.get("run_state") or {}, _run_state(corpus, root, today))
    if not comparable:
        # Not fatal: the shown hits are still exactly what the answers came
        # from, so the grade stands. But the reader must know the corpus moved.
        print(f"{YELLOW}Корпус изменился между проходами ({', '.join(diverged)}). "
              f"Оценка идёт по показанной выдаче, отчёт помечен.{RESET}")

    # Validated like `mode_score` does. A typo'd path used to raise
    # FileNotFoundError -- an OSError, which main() did not catch -- and exit 1
    # on a traceback.
    if not Path(answers_path).is_file():
        print(f"{RED}файл ответов не найден: {answers_path}{RESET}", file=sys.stderr)
        return 2
    given = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    if not isinstance(given, dict):
        print(f"{RED}файл ответов должен быть объектом "
              f"{{qid: ответ}}, получено {type(given).__name__}{RESET}", file=sys.stderr)
        return 2
    # The per-question values, checked like the top level above them. This mode
    # is the interactive one: the operator writes this file by hand, hours after
    # the print pass, so `{"agg-03": "see notes.txt"}` is the EXPECTED mistake,
    # not an exotic one. Every other input error here exits 2 with a message;
    # this one fell through `answer.get("refused")` as an AttributeError nothing
    # in the call chain catches, so it exited 1 with a traceback and no report.
    wrong_shape = sorted(qid for qid in CROSSCHECK_QUESTIONS
                         if qid in given and not isinstance(given[qid], dict))
    if wrong_shape:
        print(f"{RED}ответы должны быть объектами; не объекты для: "
              f"{', '.join(wrong_shape)}{RESET}", file=sys.stderr)
        return 2
    # The VALUE inside the container, checked for the same reason. The grading
    # branch does `set(answer.get("paths", []))`, and the default only applies
    # when the key is ABSENT - so `"paths": null` reached `set(None)`, a
    # TypeError that `main`'s except chain does not list, and the run exited 1
    # on a traceback with no report. Exit 1 in this mode means "the ceiling's
    # meaning as an upper bound was contradicted", so a typo in a hand-written
    # file was recorded by any exit-code-reading harness as a falsified
    # benchmark. Refused rather than coerced to []: coercion would grade the
    # question "wrong" and manufacture a measurement out of the typo.
    bad_paths = sorted(qid for qid in CROSSCHECK_QUESTIONS
                       if isinstance(given.get(qid), dict)
                       and "paths" in given[qid]
                       and not isinstance(given[qid]["paths"], list))
    if bad_paths:
        print(f"{RED}поле paths должно быть списком; не список для: "
              f"{', '.join(bad_paths)}{RESET}", file=sys.stderr)
        return 2
    rows = []
    for qid in CROSSCHECK_QUESTIONS:
        expected = truth[qid]
        answer = given.get(qid) or {}
        got = {h.get("path") for h in shown[qid]}
        denom = len(expected.paths) or 1
        ceiling = len(expected.paths & got) / denom

        if answer.get("refused"):
            outcome = "refused"
        elif expected.kind == "paths":
            outcome = ("correct" if set(answer.get("paths", [])) == expected.paths
                       else "wrong")
        else:
            outcome = "correct" if answer.get("value") == expected.value else "wrong"

        # The prediction: at ceiling 0.00 the model cannot cite what it never
        # saw, so anything but wrong-or-refused would falsify the ceiling's
        # meaning as an upper bound.
        predicted = "wrong-or-refused" if ceiling == 0.0 else "unconstrained"
        matched = (predicted == "unconstrained"
                   or outcome in ("wrong", "refused"))
        rows.append({"id": qid, "ceiling_operating": round(ceiling, 3),
                     "outcome": outcome, "prediction": predicted,
                     "matched": matched})
        colour = GREEN if matched else RED
        print(f"{qid:<8} потолок {ceiling:>5.3f}  исход {outcome:<9} "
              f"{colour}{'совпало' if matched else 'РАСХОЖДЕНИЕ'}{RESET}")

    contradicted = [r for r in rows if not r["matched"]]
    # How many questions could have FALSIFIED the assumption. A question whose
    # ceiling is 1.0 predicts nothing, so a run of three such questions would
    # report "held" while testing nothing at all - the same shape of defect the
    # step-1 manual check had, and worth refusing to repeat.
    constrained = [r for r in rows if r["prediction"] == "wrong-or-refused"]
    if contradicted:
        print(f"\n{RED}Потолок опровергнут на {len(contradicted)} вопросе(ах): "
              f"модель дала верный ответ там, где выдача его не содержала. "
              f"Это находка о бенчмарке, а не о примитиве.{RESET}")
    elif not constrained:
        print(f"\n{YELLOW}Ничего не проверено: ни у одного из вопросов потолок не "
              f"равен нулю, поэтому опровергнуть допущение было нечем. "
              f"Возьмите вопросы с нулевым потолком.{RESET}")
    else:
        print(f"\n{GREEN}Расхождений нет на {len(constrained)} из {len(rows)} "
              f"вопросов, которые МОГЛИ опровергнуть допущение "
              f"(потолок 0.000). Остальные его не проверяют.{RESET}")

    out_dir = get_outputs_dir() / "operations" / "census-bench"
    stem = (f"{datetime.now(tz=get_default_tz()).date().isoformat()}"
            "_recall-crosscheck_census")
    # Only the REPORT is suppressed. The `shown_path` write above is state the
    # grading pass reads back, not a report, so `--no-write` must not skip it.
    if not write:
        print(f"{GRAY}--no-write: отчёт не записан{RESET}")
        return 1 if contradicted else 0
    atomic_write_text(out_dir / f"{stem}.json", json.dumps({
        "schema_version": 1, "mode": "recall-crosscheck",
        "generated": datetime.now(tz=get_default_tz()).isoformat(),
        "run_state": _run_state(corpus, root, today),
        "shown_run_state": record.get("run_state"),
        "shown_generated": record.get("generated"),
        "corpus_moved_between_passes": diverged,
        "top_k": OPERATING_TOP_K, "threshold": OPERATING_THRESHOLD,
        "questions": rows,
        "contradictions": len(contradicted),
        "falsifiable_questions": len(constrained),
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"{GREEN}Отчёт:{RESET} {out_dir / (stem + '.json')}")
    return 1 if contradicted else 0


def mode_score(path: str, today: date | None = None, write: bool = True) -> int:
    """Grade /census answers against the oracles and print the verdict.

    `today` is threaded through because the oracles are date-sensitive. It used
    not to be: `--score answers.json --today 2026-08-01` graded against a
    different date, silently, on an acceptance gate.
    """
    if not Path(path).is_file():
        print(f"{RED}answers file not found: {path}{RESET}", file=sys.stderr)
        return 2

    report = score_answers(path, today)

    print(f"{BOLD}{'id':<8} {'класс':<13} {'статус':<18} причина{RESET}")
    for row in report["questions"]:
        colour = {STATUS_CORRECT: GREEN, STATUS_CONFIDENTLY_WRONG: RED}.get(
            row["status"], YELLOW)
        print(f"{row['id']:<8} {row['question_class']:<13} "
              f"{colour}{row['status']:<18}{RESET} {GRAY}{row['why'][:60]}{RESET}")

    print()
    for name, bucket in sorted(report["per_class"].items()):
        acc = bucket["accuracy"]
        print(f"  {name:<13} {bucket['correct']}/{bucket['n']} верных"
              f"{'' if acc is None else f' ({acc:.3f})'}"
              f"{GRAY}, отказов {bucket['refused']}, "
              f"уверенно неверных {bucket['confidently_wrong']}{RESET}")

    # Named, never silent. A question dropped from the grade without a line here
    # is a narrowed measurement that reads like a complete one.
    if report.get("not_scored"):
        print(f"  {GRAY}не оценивались ({len(report['not_scored'])}): "
              f"{', '.join(report['not_scored'])} — потолок мерится --baseline, "
              f"грейд /census не выносится{RESET}")

    if report["latency_median_s"] is not None:
        base = report["baseline_latency_median_s"]
        # An explicit key and an explicit None test. This was
        # `base.get("median_s") or base.get("median")`: `or` treats 0.0 as
        # absent, and `build_report` writes `median`, never `median_s`, so the
        # first half of that expression could only ever be dead weight holding
        # the trap open. Worse, the comparison then vanished with no note —
        # in a file whose stated principle is that a dropped measurement is
        # named, never silent. `median` is legitimately None when the baseline
        # run measured no latencies, which is the common way this happens.
        if isinstance(base, dict):
            base = base.get("median")
        if isinstance(base, (int, float)):
            against = f" против {base:.2f} с базовой линии"
        elif base is None:
            against = f" {GRAY}(базовой линии для сравнения нет){RESET}"
        else:
            against = f" {GRAY}(базовая линия непригодна: {base!r}){RESET}"
        print(f"\n  медиана времени ответа "
              f"{report['latency_median_s']:.2f} с{against}")

    verdict_name = report["verdict"]
    colour = {VERDICT_ACCEPTED: GREEN, VERDICT_REJECTED: RED}.get(verdict_name, YELLOW)
    print(f"\n{colour}{BOLD}{verdict_name}{RESET} {report['verdict_why']}")
    print(f"{GRAY}Правило: {report['verdict_rule']}{RESET}")

    out_dir = get_outputs_dir() / "operations" / "census-bench"
    stem = (f"{datetime.now(tz=get_default_tz()).date().isoformat()}"
        "_acceptance_census-primitive")
    # `--no-write` reached only `--baseline`; every other mode wrote its report
    # regardless of the flag that says not to.
    if write:
        atomic_write_text(out_dir / f"{stem}.json",
                          json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"\n{GREEN}Отчёт:{RESET} {out_dir / (stem + '.json')}")

    return 0 if verdict_name == VERDICT_ACCEPTED else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acceptance benchmark for /census: the reachable ceiling of the "
                    "incumbent retrieval path, measured against code-computed truth.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show-truth", action="store_true",
                       help="print each question's ground truth and its cardinality")
    group.add_argument("--baseline", action="store_true",
                       help="measure the retrieval ceiling and print the verdict")
    group.add_argument("--score", metavar="ANSWERS.JSON",
                       help="grade /census answers per question class")
    group.add_argument("--operating-point", action="store_true",
                       help="ceiling at /recall's shipped defaults, not at saturation")
    group.add_argument("--recall-crosscheck", action="store_true",
                       help="print three questions for the MODEL-composed path, "
                            "or grade the answers given back")
    parser.add_argument("--crosscheck-answers", metavar="FILE",
                        help="answers to grade, with --recall-crosscheck")
    parser.add_argument("--today", help="ISO date override; defaults to today in the operator zone")
    parser.add_argument("--no-write", action="store_true", help="do not write the report files")
    args = parser.parse_args()

    # `--crosscheck-answers` only means something with `--recall-crosscheck`.
    # It parsed happily beside `--baseline` and was then never read: operator
    # input accepted and silently discarded.
    if args.crosscheck_answers and not args.recall_crosscheck:
        print(f"{RED}--crosscheck-answers only applies with --recall-crosscheck{RESET}",
              file=sys.stderr)
        return 2

    root = get_workspace_root()
    # Parsed inside the guard: a malformed --today used to raise ValueError here,
    # OUTSIDE any try, and exit 1 on a traceback instead of the documented 2.
    try:
        today = date.fromisoformat(args.today) if args.today else datetime.now(get_default_tz()).date()
    except ValueError as exc:
        print(f"{RED}--today is not an ISO date:{RESET} {exc}", file=sys.stderr)
        return 2
    corpus = CorpusPaths.from_workspace()

    try:
        questions = load_questions(root)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"{RED}Набор вопросов не читается:{RESET} {exc}", file=sys.stderr)
        return 2

    # INSIDE the try, and carrying `today` and `write`. `--score` used to run
    # above it, so a malformed answers file, a corrupt baseline report, or an
    # answer record with `"sources": null` each produced a traceback and exit 1
    # rather than the documented exit 2 — on the acceptance path this file
    # exists to protect. It also never received `--today`, so pinning the
    # oracle date changed nothing and the grade was computed against a
    # different day with no warning.
    try:
        if args.score:
            return mode_score(args.score, today, write=not args.no_write)
        if args.show_truth:
            return mode_show_truth(questions, corpus, today)
        if args.operating_point:
            return mode_operating_point(questions, corpus, root, today,
                                        write=not args.no_write)
        if args.recall_crosscheck:
            return mode_recall_crosscheck(questions, corpus, root, today,
                                          args.crosscheck_answers,
                                          write=not args.no_write)
        return mode_baseline(questions, corpus, root, today, write=not args.no_write)
    # `UnreadableCorpus` is a RuntimeError, and RuntimeError was in no branch
    # below, so the one condition the oracles raise BY DESIGN (a corpus file
    # whose frontmatter does not parse, refused rather than silently skipped)
    # left this gate as a traceback and exit 1. It reaches every mode, because
    # every mode calls `load_truth`: one stray non-thread file under
    # `threads/` was enough, and a bare clone grading against the bundled
    # `examples/` corpus hits it on the first question.
    except UnreadableCorpus as exc:
        print(f"{RED}Истину по этому корпусу вычислить нельзя:{RESET} {exc}",
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{RED}Прибор отказал:{RESET} {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"{RED}Не найден ключ:{RESET} {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{RED}Файл не читается как JSON:{RESET} {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{RED}Файл недоступен:{RESET} {exc}", file=sys.stderr)
        return 2
    except subprocess.SubprocessError as exc:
        print(f"{RED}Слой поиска не удалось вызвать:{RESET} {exc!r}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
