#!/usr/bin/env python3
"""Speed and accuracy of the sub-model that /census would call per slice.

An RLM run makes a handful of root calls and hundreds of sub-calls. The sub-calls
are the cost and the risk: they are cheap individually, and they are where a small
model quietly degrades as the slice grows. This measures both, on the real shape
of a sub-call -- a slice of a real document plus one pointwise question, answered
as strict JSON.

Ground truth is computed by code, never by a judge model. Three tasks per
document, each mechanically decidable: read a frontmatter field, count checkbox
lines in a named section, test for a substring. A model that scores badly here
would score badly inside /census for the same reason.

Two properties this file exists to protect, both learned the hard way on
2026-08-13:

* **A slice must actually be a slice.** `text[:50000]` on a corpus whose files
  are 5k long returns the whole file, and three "slice widths" collapse into one
  measurement wearing three hats. Long slices are therefore drawn from trees that
  hold long documents, and every case records the length it ACTUALLY got beside
  the width it asked for. `--dry-run` fails a width whose cases are mostly short.
* **The corpus is private.** Every call ships live workspace text to a third
  party, so the runner list is explicit, one-entry, and disabled-by-default for
  everything else, and a declared-sensitive session refuses the whole run rather
  than silently skipping a model.

Usage:
    python scripts/census-submodel-bench.py all --dry-run     # plan only, no network
    python scripts/census-submodel-bench.py accuracy
    python scripts/census-submodel-bench.py speed
    python scripts/census-submodel-bench.py all

Exit codes: 0 ok, 2 setup error (no documents, degenerate width, sensitive
session), 3 every enabled runner was unreachable.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.api import load_api_key
from scripts.utils.atomic import atomic_write_text
from scripts.utils.claude_models import latest
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.sensitive import is_sensitive, sensitivity_is_declared
from scripts.utils.workspace import (
    get_default_tz,
    get_knowledge_dir,
    get_outputs_dir,
    get_threads_dir,
)

# ============================================================
# Configuration
# ============================================================

OLLAMA_URL = "http://localhost:11434/api/chat"
PROXY_URL = "http://localhost:8317/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

DOC_COUNT = 30
SLICE_WIDTHS = (4_000, 20_000, 50_000)
# A width is degenerate when most of its cases could not be filled to it: the
# slice then equals a shorter one and measures nothing new.
MIN_FILLED_FRACTION = 0.6

# A width whose cases share too few distinct truths cannot measure a model.
# Measured 2026-08-13 on the shipped builder: at width 50000 all 30 cases had the
# SAME truth, so the constant answer {"field": null, "checkboxes": 0,
# "mentions": false} scored 90/90 - and its score ROSE with width, inverting the
# drift signal the harness exists to detect. The filled-fraction guard saw 28/30
# filled and printed "годен".
MIN_DISTINCT_TRUTH_FRACTION = 0.4
PARALLELISM = 8
SPEED_SAMPLE = 5


@dataclass
class Runner:
    """One model under test.

    `enabled` is the egress switch. Everything but the chosen sub-model ships
    disabled with its reason recorded, so the history of what was ruled out
    survives in the file instead of in someone's memory, and so turning a model
    back on is a deliberate edit rather than a side effect of running the script.
    """

    label: str
    transport: str          # "proxy" | "ollama" | "anthropic"
    model: str
    enabled: bool
    reason: str


RUNNERS: tuple[Runner, ...] = (
    Runner("proxy k3", "proxy", "k3", True,
           "chosen sub-model: 18/18 at both widths measured 2026-08-13, and "
           "non-Anthropic, so a run never eats the Claude Code subscription quota"),
    Runner("proxy gemini-3.5-flash-extra-low", "proxy", "gemini-3.5-flash-extra-low", False,
           "reserve only; enabling it is a fresh decision about shipping private "
           "text to another provider, not an automatic fallback"),
    Runner("proxy kimi-for-coding-highspeed", "proxy", "kimi-for-coding-highspeed", False,
           "ruled out 2026-08-13: 18/18 at 4k collapsing to 12/18 at 20k -- the "
           "context-growth drift this benchmark exists to catch"),
    Runner("ollama gemma3:4b", "ollama", "gemma3:4b", False,
           "ruled out 2026-08-13 on both axes: 52 minutes for 200 calls, and "
           "11/18 at 4k falling to 7/18 at 20k with the JSON format itself breaking"),
    # Family name, never a release id: `scripts/utils/claude_models.latest`
    # resolves it at call time, so a new flagship reaches this bench with no edit.
    # `tests/test_no_claude_model_pins.py` fails any engine script that pins one.
    Runner("api haiku", "anthropic", "haiku", False,
           "paid API path; only worth enabling when speed matters more than cost"),
)

TASK_PROMPT = (
    "Ответь СТРОГО одним JSON-объектом, без пояснений и без markdown-ограждения:\n"
    '{{"field": "YYYY-MM-DD" | null, "checkboxes": <целое>, "mentions": true|false}}\n\n'
    "field - значение поля {field_key} из заголовка документа, либо null если его нет.\n"
    "checkboxes - сколько строк вида '- [ ]' в разделе '{section}'. "
    "Если раздела нет или строк нет, 0.\n"
    'mentions - встречается ли в тексте подстрока "{marker}" (регистр не важен).\n\n'
    "ДОКУМЕНТ:\n"
)

FIELD_KEY = "last_touched"
SECTION = "Open follow-ups"
# The substring probe. Deliberately a token that does not occur in the corpus, so
# no real name has to be embedded here or passed on the command line. --marker
# overrides it.
#
# It is PLANTED into every other case (`MARKER_PLANT_EVERY`), which the 2026-08-13
# audit showed is the difference between a probe and a gift: with the token absent
# from the whole corpus the honest answer was `false` on all 90 cases at all
# widths, so any model biased toward `false` collected a third of every accuracy
# cell without reading anything. Planting it keeps the token synthetic and makes
# the answer depend on the document in front of the model.
DEFAULT_MARKER = "zzq-census-probe-token"
MARKER_PLANT_EVERY = 2


# ============================================================
# Corpus sampling
# ============================================================

@dataclass
class Case:
    path: Path
    text: str
    width: int
    actual_len: int
    truth: dict

    @property
    def filled(self) -> bool:
        """True when the document was long enough to fill the requested width."""
        return self.actual_len >= self.width


def _truth(text: str, marker: str) -> dict:
    """Ground truth for one slice, computed by code.

    Deliberately mirrors the question wording rather than the corpus schema: what
    is being measured is whether the model can read what is in front of it.
    """
    field_match = re.search(rf"^{re.escape(FIELD_KEY)}:\s*'?(\d{{4}}-\d{{2}}-\d{{2}})", text, re.M)
    section_match = re.search(
        rf"^## {re.escape(SECTION)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S,
    )
    return {
        "field": field_match.group(1) if field_match else None,
        "checkboxes": len(re.findall(r"^- \[ \]", section_match.group(1), re.M)) if section_match else 0,
        "mentions": marker.lower() in text.lower(),
    }


def _candidate_docs(min_len: int, want: int) -> list[Path]:
    """Documents of at least `min_len` characters, threads first.

    Threads are the shape a sub-call actually sees, so they are preferred. They
    top out around 38k characters, so the long widths fall through to knowledge
    and outputs, which do hold documents past 50k.
    """
    picked: list[Path] = []
    seen: set[Path] = set()
    for root, pattern in (
        (get_threads_dir() / "business", "*.md"),
        (get_knowledge_dir(), "**/*.md"),
        (get_outputs_dir(), "**/*.md"),
    ):
        if not root.exists():
            continue
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < min_len:
                continue
            seen.add(path)
            picked.append(path)
            if len(picked) >= want:
                return picked
    return picked


def constant_baseline(cases: list["Case"]) -> tuple[int, int]:
    """(hits, total) a model scores by answering the modal value to everything.

    Printed beside the real score so a reader can see how much of it was earned.
    A cell that beats nothing is not a measurement.
    """
    if not cases:
        return 0, 0
    fields = [c.truth["field"] for c in cases]
    modal = {
        "field": max(set(fields), key=fields.count),
        "checkboxes": max({c.truth["checkboxes"] for c in cases},
                          key=[c.truth["checkboxes"] for c in cases].count),
        "mentions": sum(1 for c in cases if c.truth["mentions"]) * 2 >= len(cases),
    }
    hits = sum(1 for c in cases for k, v in c.truth.items() if modal[k] == v)
    return hits, len(cases) * 3


def distinct_truth_fraction(cases: list["Case"]) -> float:
    if not cases:
        return 0.0
    distinct = {tuple(sorted(c.truth.items())) for c in cases}
    return len(distinct) / len(cases)


def _plant(text: str, marker: str, index: int) -> str:
    """Plant the probe token in every Nth case, so `mentions` has two answers."""
    if index % MARKER_PLANT_EVERY:
        return text
    return f"{text}\n\n<!-- {marker} -->\n"


def build_cases(width: int, marker: str, doc_count: int = DOC_COUNT) -> list[Case]:
    cases: list[Case] = []
    for path in _candidate_docs(min_len=width, want=doc_count):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:width]
        except OSError:
            continue
        text = _plant(text, marker, len(cases))
        cases.append(Case(path, text, width, len(text), _truth(text, marker)))
    if len(cases) < doc_count:
        # Fall back to the longest available rather than silently under-sampling.
        for path in _candidate_docs(min_len=0, want=doc_count * 4):
            if len(cases) >= doc_count:
                break
            if any(c.path == path for c in cases):
                continue
            text = _plant(path.read_text(encoding="utf-8", errors="ignore")[:width],
                          marker, len(cases))
            cases.append(Case(path, text, width, len(text), _truth(text, marker)))
    return cases[:doc_count]


# ============================================================
# Transports
# ============================================================

def _post(url: str, payload: dict, headers: dict, timeout: int = 300) -> dict:
    # Assert the destination rather than suppressing the warning about it. Every
    # caller passes one of the three module constants, but this payload is real
    # workspace text and the guard has to hold for whoever adds the fourth.
    if url not in (OLLAMA_URL, PROXY_URL, ANTHROPIC_URL):
        raise ValueError(f"refusing an unregistered destination: {url!r}")
    request = urllib.request.Request(  # noqa: S310 - destination asserted above
        url, data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


def call_model(runner: Runner, prompt: str) -> str:
    """One sub-call. Raises on transport failure so the caller can name the cause."""
    if runner.transport == "ollama":
        data = _post(OLLAMA_URL, {
            "model": runner.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 300},
        }, {"Content-Type": "application/json"})
        return data["message"]["content"]

    if runner.transport == "proxy":
        key = load_api_key("CLIPROXY_API_KEY", required=False)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = _post(PROXY_URL, {
            "model": runner.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 400,
        }, headers)
        return data["choices"][0]["message"]["content"]

    if runner.transport == "anthropic":
        key = load_api_key("ANTHROPIC_API_KEY", required=True)
        data = _post(ANTHROPIC_URL, {
            # Resolved from the family at call time, not pinned in the source.
            "model": latest(runner.model),
            "max_tokens": 400,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }, {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        })
        return "".join(block.get("text", "") for block in data.get("content", []))

    raise ValueError(f"unknown transport {runner.transport!r}")


def parse_answer(raw: str) -> dict | None:
    """Extract the JSON object, tolerating a markdown fence. None when unparseable."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _describe_failure(exc: BaseException) -> str:
    """Say WHY a call failed, not just that it did.

    The /tmp drafts printed the exception type alone, which made "the model is not
    reachable" indistinguishable from "the model returned something unexpected" --
    two failures with opposite responses.
    """
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode(errors="replace")[:200]
        except Exception:  # noqa: BLE001 - diagnostic only; the real error is exc
            body = "<body unreadable>"
        return f"HTTP {exc.code} {exc.reason}: {body}"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable: {exc.reason}"
    if isinstance(exc, KeyError):
        return f"response shape unexpected, missing key {exc}"
    if isinstance(exc, (TimeoutError, OSError)):
        return f"{type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"


# ============================================================
# Measurement
# ============================================================

def score_accuracy(runner: Runner, cases: list[Case], marker: str) -> dict | None:
    prompt_head = TASK_PROMPT.format(field_key=FIELD_KEY, section=SECTION, marker=marker)
    prompts = [prompt_head + case.text for case in cases]
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=PARALLELISM) as pool:
            raws = list(pool.map(lambda p: call_model(runner, p), prompts))
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            TimeoutError, OSError, ValueError) as exc:
        print(f"  {runner.label:<34} {RED}пропущена{RESET} {GRAY}{_describe_failure(exc)}{RESET}")
        return None
    wall = time.perf_counter() - started

    hits = {"field": 0, "checkboxes": 0, "mentions": 0}
    parsed_ok = 0
    # strict: `raws` is `pool.map` over prompts built one-per-case, so a length
    # mismatch would mean a silently dropped answer scored against the wrong case.
    for case, raw in zip(cases, raws, strict=True):
        answer = parse_answer(raw)
        if answer is None:
            continue
        parsed_ok += 1
        for key in hits:
            if answer.get(key) == case.truth[key]:
                hits[key] += 1
    total = sum(hits.values())
    maximum = len(cases) * 3
    return {
        "runner": runner.label, "model": runner.model,
        "width": cases[0].width if cases else 0,
        "n": len(cases), "hits": hits, "total": total, "max": maximum,
        "parsed_ok": parsed_ok, "wall_s": round(wall, 2),
    }


def score_speed(runner: Runner, cases: list[Case], marker: str) -> dict | None:
    prompt_head = TASK_PROMPT.format(field_key=FIELD_KEY, section=SECTION, marker=marker)
    prompts = [prompt_head + case.text for case in cases]
    started = time.perf_counter()
    try:
        call_model(runner, prompts[0])
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            TimeoutError, OSError, ValueError) as exc:
        print(f"  {runner.label:<34} {RED}пропущена{RESET} {GRAY}{_describe_failure(exc)}{RESET}")
        return None
    cold = time.perf_counter() - started

    latencies = []
    for prompt in prompts[:SPEED_SAMPLE]:
        mark = time.perf_counter()
        try:
            call_model(runner, prompt)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
                TimeoutError, OSError, ValueError) as exc:
            print(f"  {runner.label:<34} {RED}сбой в прогоне{RESET} {GRAY}{_describe_failure(exc)}{RESET}")
            return None
        latencies.append(time.perf_counter() - mark)

    started = time.perf_counter()
    batch = prompts[:PARALLELISM]
    with ThreadPoolExecutor(max_workers=PARALLELISM) as pool:
        list(pool.map(lambda p: call_model(runner, p), batch))
    parallel_wall = time.perf_counter() - started
    # Divide by what was actually submitted. Dividing by PARALLELISM while
    # submitting fewer understated per-call time by up to 8x on a small --docs run.
    per_call = parallel_wall / max(1, len(batch))

    return {
        "runner": runner.label, "model": runner.model,
        "cold_s": round(cold, 2),
        "median_s": round(statistics.median(latencies), 2),
        "parallel_wall_s": round(parallel_wall, 2),
        "per_call_parallel_s": round(per_call, 3),
        "projected_200_s": round(per_call * 200, 1),
    }


# ============================================================
# Dry run -- the acceptance surface for this step
# ============================================================

def dry_run(marker: str, doc_count: int) -> int:
    enabled = [r for r in RUNNERS if r.enabled]
    print(f"{BOLD}План прогона{RESET} {GRAY}(ни одного сетевого вызова){RESET}\n")
    print(f"{BOLD}Модели{RESET}")
    for runner in RUNNERS:
        flag = f"{GREEN}включена{RESET}" if runner.enabled else f"{GRAY}выключена{RESET}"
        print(f"  {runner.label:<34} {flag}  {GRAY}{runner.reason}{RESET}")
    if len(enabled) != 1:
        print(f"\n{YELLOW}Включено моделей: {len(enabled)}.{RESET} "
              f"Решение CEO 2026-08-13 разрешает ровно одну.")

    print(f"\n{BOLD}Срезы{RESET}")
    degenerate = []
    total_calls = 0
    for width in SLICE_WIDTHS:
        cases = build_cases(width, marker, doc_count)
        if not cases:
            print(f"  {width:>6}  {RED}нет документов{RESET}")
            degenerate.append(width)
            continue
        filled = sum(1 for c in cases if c.filled)
        lengths = [c.actual_len for c in cases]
        fraction = filled / len(cases)
        distinct = distinct_truth_fraction(cases)
        floor_hits, floor_max = constant_baseline(cases)
        ok = fraction >= MIN_FILLED_FRACTION and distinct >= MIN_DISTINCT_TRUTH_FRACTION
        mark = f"{GREEN}годен{RESET}" if ok else f"{RED}ВЫРОЖДЕН{RESET}"
        if not ok:
            degenerate.append(width)
        print(f"  {width:>6}  {mark}  документов {len(cases):>2}, "
              f"заполнено до ширины {filled}/{len(cases)} ({fraction:.0%}), "
              f"фактическая длина медиана {int(statistics.median(lengths))}, "
              f"мин {min(lengths)}, макс {max(lengths)}")
        total_calls += len(cases)

    print(f"\n{BOLD}Итого{RESET} вызовов точности: {total_calls * len(enabled)} "
          f"({total_calls} кейсов x {len(enabled)} модель)")
    print(f"{GRAY}Маркер подстроки: {marker!r} "
          f"(вживляется в каждый второй кейс, так что истина mentions разделена примерно пополам){RESET}")

    if degenerate:
        print(f"\n{RED}Вырожденные срезы: {degenerate}.{RESET} "
              f"Ячейка, которую нельзя цитировать как измерение.")
        return 2
    print(f"\n{GREEN}Все срезы непустые. Готов к прогону.{RESET}")
    return 0


# ============================================================
# Entry point
# ============================================================

def _refuse_if_sensitive() -> None:
    """Refuse the run unless SENSITIVE_MODE has been explicitly cleared.

    The guard is `is_sensitive()`, which is fail-closed: unset, empty and
    unrecognised all resolve sensitive. That is the right default HERE - unlike
    the judge gate in `bias-mitigation.md`, which must ask
    `sensitivity_is_declared()` - because this payload is real workspace text
    leaving for a third party, and a caller may treat the unset default as the
    machine's default only when it can prove its payload carries nothing
    private. This one cannot.

    What the guard cannot establish is WHY it fired, so the message names which
    of the two it is rather than asserting a declaration nobody made
    (`.claude/rules/scope-claims.md`). There is no local model in the enabled
    set to degrade to, so a silent skip would look like a completed run that
    measured nothing.
    """
    if not is_sensitive():
        return
    if sensitivity_is_declared():
        cause = "сессия объявлена чувствительной (SENSITIVE_MODE задан явно)"
    else:
        cause = ("SENSITIVE_MODE не очищен - это умолчание машины, а не "
                 "объявление оператора; очистите его осознанно "
                 "(SENSITIVE_MODE=off), чтобы разрешить выгрузку")
    print(f"{RED}Прогон отменён:{RESET} {cause}. Каждый вызов отправляет текст "
          f"рабочих документов третьей стороне, а локальной модели во "
          f"включённом наборе нет, деградировать не во что.", file=sys.stderr)
    sys.exit(2)


def _write_report(payload: dict) -> Path:
    today = datetime.now(get_default_tz()).date().isoformat()
    out_dir = get_outputs_dir() / "operations" / "census-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today}_bench_census-submodel.json"
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Speed and accuracy of the /census sub-model, on real slices.",
    )
    parser.add_argument("mode", choices=("speed", "accuracy", "all"))
    parser.add_argument("--dry-run", action="store_true",
                        help="print the run plan and exit; makes no network call")
    parser.add_argument("--marker", default=DEFAULT_MARKER,
                        help="substring probe for the third task "
                             f"(default: {DEFAULT_MARKER!r}, absent from the corpus)")
    parser.add_argument("--docs", type=int, default=DOC_COUNT,
                        help=f"documents per slice width (default {DOC_COUNT})")
    args = parser.parse_args()

    if args.dry_run:
        return dry_run(args.marker, args.docs)

    _refuse_if_sensitive()
    enabled = [r for r in RUNNERS if r.enabled]
    if not enabled:
        print(f"{RED}Ни одной включённой модели.{RESET}", file=sys.stderr)
        return 2

    report: dict = {
        "generated": datetime.now(get_default_tz()).isoformat(),
        "runners_enabled": [r.model for r in enabled],
        "docs_per_width": args.docs,
        "marker": args.marker,
        "speed": [],
        "accuracy": [],
        "note": (
            "gemma3:4b and kimi-for-coding-highspeed were ruled out on 2026-08-13 "
            "at 18 checks per cell and are NOT re-confirmed here; they remain "
            "disabled in RUNNERS with their reasons."
        ),
    }
    reached = 0

    if args.mode in ("speed", "all"):
        print(f"\n{BOLD}Скорость{RESET} {GRAY}(срез 4k){RESET}")
        cases = build_cases(SLICE_WIDTHS[0], args.marker, args.docs)
        for runner in enabled:
            result = score_speed(runner, cases, args.marker)
            if result:
                reached += 1
                report["speed"].append(result)
                print(f"  {runner.label:<34} медиана {result['median_s']:>6.2f}s | "
                      f"на вызов при {PARALLELISM} параллельно {result['per_call_parallel_s']:>5.2f}s | "
                      f"200 вызовов ~{result['projected_200_s'] / 60:.1f} мин")

    if args.mode in ("accuracy", "all"):
        for width in SLICE_WIDTHS:
            cases = build_cases(width, args.marker, args.docs)
            filled = sum(1 for c in cases if c.filled)
            distinct = distinct_truth_fraction(cases)
            floor_hits, floor_max = constant_baseline(cases)
            degenerate = (not cases
                          or filled / len(cases) < MIN_FILLED_FRACTION
                          or distinct < MIN_DISTINCT_TRUTH_FRACTION)
            tag = f" {RED}[ВЫРОЖДЕН]{RESET}" if degenerate else ""
            print(f"\n{BOLD}Точность{RESET} {CYAN}срез {width}{RESET}{tag} "
                  f"{GRAY}заполнено {filled}/{len(cases)}, "
                  f"различных истин {distinct:.0%}, "
                  f"пол константного ответа {floor_hits}/{floor_max}{RESET}")
            for runner in enabled:
                result = score_accuracy(runner, cases, args.marker)
                if result:
                    reached += 1
                    result["degenerate"] = degenerate
                    result["filled"] = filled
                    result["distinct_truth_fraction"] = round(distinct, 3)
                    result["constant_baseline"] = [floor_hits, floor_max]
                    report["accuracy"].append(result)
                    hits = result["hits"]
                    print(f"  {runner.label:<34} поле {hits['field']}/{result['n']}  "
                          f"счёт {hits['checkboxes']}/{result['n']}  "
                          f"поиск {hits['mentions']}/{result['n']}  "
                          f"= {result['total']}/{result['max']}  "
                          f"JSON {result['parsed_ok']}/{result['n']}  "
                          f"{result['wall_s']:.1f}s")

    if reached == 0:
        print(f"{RED}Ни одна включённая модель не ответила.{RESET}", file=sys.stderr)
        return 3

    path = _write_report(report)
    print(f"\n{GREEN}Отчёт:{RESET} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
