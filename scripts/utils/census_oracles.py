#!/usr/bin/env python3
"""Mechanically-computed ground truth for the /census acceptance benchmark.

Every oracle here answers one benchmark question by READING THE CORPUS AND
COUNTING. No model is consulted at any point, by design: the benchmark exists to
measure whether an answer path fabricates, and a model-judge would inject the
very error the instrument is meant to detect.

Two invariants make the answers usable:

1. **Purity.** An oracle takes its corpus paths and `today` as arguments and
   reads nothing else. `datetime.now()` inside an oracle would make the truth
   depend on when the test ran, so it is banned (and `tests/test_census_oracles.py`
   enforces the ban by inspecting source).
2. **Path form.** Answer paths are POSIX strings relative to the data root, which
   is exactly the form `scripts/memory-index.py --json` reports in `hits[].path`.
   That is what makes `|truth & retrieved| / |truth|` computable without a
   normalisation step that could silently drop matches.

What an answer set MEANS, and the limit that follows
----------------------------------------------------
`OracleAnswer.paths` is the set of files that CONSTITUTE the answer -- what the
question asks to be listed -- not the wider set a reader would need to verify it.
That is deliberately the reading most generous to the incumbent path: `/recall`
only has to surface the answer items, not the corroborating ones. A path that
loses on its best terms has lost.

The consequence is that the retrieval ceiling bounds RETRIEVAL, not reasoning.
For a question whose evidence is one or two files (a set difference between two
context files, say), the ceiling is 1.0 and says nothing about whether the answer
would be right -- it says only that retrieval is not the bottleneck. Those
questions are kept on purpose: a ceiling of 1.0 beside a wrong answer localises
the failure to reasoning, and per SRLM (arXiv:2603.15653) a corpus that fits the
window is the case where a traversal primitive HURTS. `question_class` marks
which is which so a report can never present the two as the same measurement.

Consumed by: `scripts/census-bench.py`, `tests/test_census_oracles.py`.
Question set: `config/census-bench-questions.json`.
Plan: `plans/2026-08-13-census-acceptance-benchmark.md`.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

# The exception types a corpus file can raise on the way in.
#
# `yaml.YAMLError` is here because `threads_lib.parse_thread_file` calls
# `yaml.safe_load`, and YAMLError subclasses neither OSError nor ValueError: a
# stray `.md` with malformed frontmatter escaped the handler below as a raw
# `yaml.scanner.ScannerError` and aborted all fifteen oracles with a traceback
# naming neither the benchmark nor the fix -- the exact failure the refusal in
# `_threads` says it repaired.
#
# `_contacts` reads through `crm.parse_frontmatter`, which is a hand-rolled line
# parser and does not raise YAMLError today. It shares this tuple anyway: the two
# readers make the same promise to the same caller, and a tuple that is right for
# one of them is the kind of asymmetry this whole finding came from.
_UNREADABLE = (OSError, ValueError, yaml.YAMLError)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.crm import (
    is_radar_frozen,
    parse_config,
    parse_frontmatter,
    parse_pipeline_stages,
    scan_contacts,
)
from scripts.utils.markdown import frontmatter_date
from scripts.utils.threads_lib import is_quiet, parse_thread_file

# ============================================================
# Corpus addressing
# ============================================================

# Thresholds the questions are phrased against. Named here rather than inlined so
# a question's wording and its oracle cannot drift apart unnoticed.
STALE_THREAD_DAYS = 30
STALE_PROSPECT_DAYS = 60

# Countries used by agg-09. Generic reference data on purpose: a list narrowed to
# the operator's actual markets would encode private deal geography into a file
# that routes to the public engine.
COUNTRY_NAMES: tuple[str, ...] = (
    "Azerbaijan", "Bangladesh", "Brazil", "China", "Egypt", "France", "Gabon",
    "Georgia", "Germany", "Ghana", "India", "Indonesia", "Israel", "Italy",
    "Japan", "Kazakhstan", "Kenya", "Kyrgyzstan", "Madagascar", "Malaysia",
    "Mexico", "Mongolia", "Morocco", "Netherlands", "Nigeria", "Oman",
    "Pakistan", "Philippines", "Poland", "Portugal", "Qatar", "Romania",
    "Russia", "Rwanda", "Saudi Arabia", "Senegal", "Serbia", "Singapore",
    "Spain", "Switzerland", "Tanzania", "Thailand", "Turkey", "Uganda",
    "Ukraine", "Uzbekistan", "Vietnam", "Zambia",
)
MIN_COUNTRIES_FOR_MULTI = 2


@dataclass(frozen=True)
class CorpusPaths:
    """Where the corpus lives, and what answer paths are relative to.

    Passed in rather than resolved internally so a test can point every oracle at
    a fixture tree. `root` is the anchor for the relative POSIX paths oracles
    return; the other fields must all sit underneath it.
    """

    root: Path
    threads: Path
    crm: Path
    context: Path
    auto_memory: Path
    knowledge: Path
    outputs: Path

    @classmethod
    def from_workspace(cls) -> "CorpusPaths":
        """Resolve against the live data overlay via the data-root seam."""
        from scripts.utils.workspace import (
            get_auto_memory_dir,
            get_context_dir,
            get_crm_contacts_dir,
            get_knowledge_dir,
            get_outputs_dir,
            get_threads_dir,
        )

        threads = get_threads_dir()
        return cls(
            root=threads.parent,
            threads=threads / "business",
            crm=get_crm_contacts_dir(),
            context=get_context_dir(),
            auto_memory=get_auto_memory_dir(),
            knowledge=get_knowledge_dir(),
            outputs=get_outputs_dir(),
        )

    @classmethod
    def from_fixture(cls, root: Path) -> "CorpusPaths":
        """Resolve against a synthetic fixture laid out like the real overlay."""
        return cls(
            root=root,
            threads=root / "threads" / "business",
            crm=root / "crm" / "contacts",
            context=root / "context",
            auto_memory=root / "auto-memory",
            knowledge=root / "knowledge",
            outputs=root / "outputs",
        )

    def rel(self, path: Path) -> str:
        """Path relative to the corpus root, POSIX form -- the memory-index form."""
        return path.resolve().relative_to(self.root.resolve()).as_posix()


@dataclass
class OracleAnswer:
    """One question's ground truth.

    `paths` is always the scoring surface, including for a counting question:
    a count is only answerable by whoever can see the files it counts, so the
    evidence set is what a retrieval ceiling must be measured against.
    """

    kind: str                                   # "paths" | "count" | "pairs"
    paths: set[str] = field(default_factory=set)
    value: Any = None                           # count, or the list of pairs
    detail: dict[str, Any] = field(default_factory=dict)
    # How many candidates the oracle examined before selecting `paths`. Declared
    # by the oracle because only the oracle knows what it drew from. None means
    # the oracle did not declare one, and the saturation guard cannot run.
    population: int | None = None

    @property
    def cardinality(self) -> int:
        return len(self.paths)

    @property
    def selected(self) -> int:
        """How many things the predicate selected, whatever the answer's shape.

        A `count` oracle scores on `value` and cites one file, so its
        `cardinality` is 1 and says nothing about the predicate. A `paths` oracle
        scores on the set itself. The saturation guard needs the predicate's
        yield, so it reads this rather than either field directly.
        """
        if self.kind == "count" and isinstance(self.value, int):
            return self.value
        return self.cardinality

    def is_empty(self) -> bool:
        return not self.paths

    def is_saturated(self) -> bool:
        """True when every candidate was selected, so the predicate never fired negative.

        The mirror of `is_empty`, and it exists because the empty case was the
        only one guarded until 2026-08-13. On that date `oracle_agg_06` was found
        returning all seven of its seven candidates: not one of the 17
        counterparty strings in the live corpus matched a CRM card verbatim, so
        the "has no card" test was constant-true and the oracle was really
        answering "which active threads name a counterparty at all". A truth set
        that selects everything measures the population, not the predicate,
        exactly as an empty one measures nothing.
        """
        return self.population is not None and self.population > 0 and \
            self.selected >= self.population


# ============================================================
# Shared corpus readers
# ============================================================

class UnreadableCorpus(RuntimeError):
    """A corpus file the oracles cannot parse, so no truth can be computed.

    Raised rather than skipped. A card whose frontmatter does not parse used to
    yield `{}`: it entered every population, could never enter a hit set, and its
    person was reported as having no CRM card. A ground-truth oracle failing
    silently toward a SMALLER truth set is the worst available behaviour, because
    the number it produces still looks like an answer.
    """



def _iso(value: Any) -> date | None:
    """Parse an ISO date or datetime prefix; None when unparseable or absent.

    A None here removes the record from every stale-set oracle, so a mistyped
    date is indistinguishable from a fresh one - the truth shrinks and nothing
    says so. ABSENT is legitimate and stays silent; UNPARSEABLE is a corpus
    defect and raises, because a ground-truth oracle that quietly answers a
    smaller question still produces a number that looks like an answer.

    Through the shared coercion since 2026-08-28. This was
    `date.fromisoformat(str(value)[:10])`, and a blind ten-character slice does
    not raise on a broken date - it INVENTS one. MEASURED over ten value shapes
    against `frontmatter_date`: the two agreed on nine and diverged on
    `"2026-01-02garbage"`, which the slice read as 2026-01-02. That is the one
    outcome the docstring above rules out, since a wrong date is worse for a
    ground-truth oracle than a named refusal.
    """
    if not value:
        return None
    try:
        return frontmatter_date(value)
    except ValueError as exc:
        raise UnreadableCorpus(
            f"unparseable date {value!r}: a record with a broken date drops out "
            "of every stale set, so the truth would shrink silently") from exc


def _threads(corpus: CorpusPaths) -> list:
    """Every thread file, or a named refusal.

    `parse_thread_file` raises on a file without thread frontmatter, and one
    stray `.md` dropped into the directory - a scratch note, an export - aborted
    all fifteen oracles with a traceback that named neither the benchmark nor
    the fix. The refusal now names the file and says what to do with it.
    """
    if not corpus.threads.exists():
        return []
    threads, unreadable = [], []
    for p in sorted(corpus.threads.glob("*.md")):
        try:
            threads.append(parse_thread_file(p))
        except _UNREADABLE as exc:
            unreadable.append(f"{p.name}: {exc}")
    if unreadable:
        raise UnreadableCorpus(
            "cannot compute truth over unparseable thread file(s): "
            + "; ".join(unreadable[:5])
            + (f" (+{len(unreadable) - 5} more)" if len(unreadable) > 5 else "")
            + " -- move non-thread files out of the thread directory")
    return threads


def _active(corpus: CorpusPaths) -> list:
    return [t for t in _threads(corpus) if t.status == "active"]


def _contacts(corpus: CorpusPaths) -> list[tuple[Path, dict]]:
    if not corpus.crm.exists():
        return []
    contacts, unreadable = [], []
    for p in sorted(corpus.crm.glob("*.md")):
        try:
            frontmatter = parse_frontmatter(p.read_text(encoding="utf-8"))
        except _UNREADABLE as exc:
            unreadable.append(f"{p.name}: {exc}")
            continue
        if not frontmatter:
            unreadable.append(f"{p.name}: no parseable frontmatter")
            continue
        contacts.append((p, frontmatter))
    if unreadable:
        raise UnreadableCorpus(
            "cannot compute truth over unparseable CRM card(s): "
            + "; ".join(unreadable[:5])
            + (f" (+{len(unreadable) - 5} more)" if len(unreadable) > 5 else ""))
    return contacts


def _contact_name(corpus: CorpusPaths, path: Path, fm: dict) -> str:
    """The person a CRM card is about, in either shape the tree may hold.

    A legacy card carries `name:` inline. A card migrated by
    `scripts/crm_migrate_to_entity_model.py` is a RELATIONSHIP record: its
    frontmatter is `entity_ref` / `relationship_type` / `last_touch` / `created`
    with no `name:` at all, because the name lives in the address-book entity.
    `config/schemas/crm-relationship.schema.json` declares exactly that, and
    `scripts/utils/crm.py` has read both shapes since the migration landed.

    This function did not: it read `fm.get("name")` only. Every migrated card
    therefore dropped out of the truth set, so `oracle_agg_03` ("people named in
    context/people.md with no CRM card") and `oracle_agg_06` ("active threads
    naming a counterparty who has no CRM card") counted those people as
    cardless. Migrate them all and both oracles report 100% missing. The
    corpus held six cards and all six were the legacy shape, so nothing said so.

    Resolved against the corpus rather than through the workspace seam, because
    `CorpusPaths` exists so every oracle can be pointed at a fixture tree.
    """
    inline = (fm.get("name") or "").strip()
    if inline:
        return inline
    ref = (fm.get("entity_ref") or "").strip()
    if not ref:
        return ""
    entity_file = corpus.crm.parent / "address-book" / f"{ref}.md"
    if not entity_file.exists():
        raise UnreadableCorpus(
            f"cannot compute truth over CRM card {path.name}: it points at "
            f"entity '{ref}', which is not in {entity_file.parent}. A relationship "
            f"record whose entity is missing has no name, and counting it as a "
            f"person with no card is the wrong answer, not a smaller one."
        )
    entity = parse_frontmatter(entity_file.read_text(encoding="utf-8"))
    name = (entity.get("name") or "").strip() if entity else ""
    if not name:
        raise UnreadableCorpus(
            f"cannot compute truth over CRM card {path.name}: entity "
            f"'{ref}' carries no name. An entity that exists and says nothing "
            f"is as dangling as one that is gone."
        )
    return name


def _contact_names(corpus: CorpusPaths) -> set[str]:
    names = {_contact_name(corpus, path, fm) for path, fm in _contacts(corpus)}
    return {n.lower() for n in names if n}


_OPEN_FOLLOWUPS_RE = re.compile(r"^## Open follow-ups\s*\n(.*?)(?=^## |\Z)", re.M | re.S)
_UNCHECKED_RE = re.compile(r"^- \[ \]", re.M)
# A wikilink target: [[name]], [[name|label]], [[name#anchor]].
#
# The captured text is normalised by `_link_stem` before it meets the file
# stems: a link written [[note.md]] or [[sub/note]] is the same target as
# [[note]], and comparing the raw capture reported both as dangling.
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def _link_stem(target: str) -> str:
    """The stem a wikilink points at, however the author spelled it."""
    tail = target.strip().replace("\\", "/").rsplit("/", 1)[-1]
    return tail[:-3] if tail.lower().endswith(".md") else tail
# The hand-authored summary bullets at the top of context/people.md:
# "- Alba Karimova (CTO, Northwind) - ...". The Contact Radar table further down is
# GENERATED FROM the CRM by crm-health.py, so differencing it against the CRM
# would be circular and would answer "nobody" by construction.
#
# Narrowed 2026-08-13 by the audit: the scan is anchored to the FIRST section,
# because the pattern applied to the whole file also matched the bullets of every
# later section, and any capitalised phrase before a parenthesis ("- Reviewed Q3
# plan (done)") counted as a person with no CRM card.
#
# The BULLET pattern is deliberately left loose. Tightening it to a name shape
# (two to four capitalised words) was tried the same day and dropped a real
# person whose bullet carries a nickname - `Name Surname / "Nick" (COO)` - which
# emptied this question's truth entirely. A loose pattern inside the right
# section beats a strict pattern over the wrong one.
# `[^\n]*` for the heading line, not `.*`: under re.S the dot eats newlines,
# so `^## .*\n` swallowed the file and captured an empty section.
_PEOPLE_SECTION_RE = re.compile(r"^## [^\n]*\n(.*?)(?=^## |\Z)", re.M | re.S)
_PEOPLE_BULLET_RE = re.compile(r"^- ([A-Z][^(\n]{2,60}?) \(", re.M)


def _counterparty_resolves(entry: str, known: set[str]) -> bool:
    """True when some CRM card name occurs inside a `counterparties:` entry.

    The entries are free prose, not keys: 'Alba Karimova (Northwind Telecom)',
    'Vantage - Mira Okafor (Deputy CEO)', 'sofia-reyes'. Comparing an
    entry to a card name for EQUALITY, which this oracle did until 2026-08-13,
    matched none of the 17 entries in the live corpus, so the question "which
    threads name a counterparty who has no card" silently became "which threads
    name a counterparty".

    Containment in one direction is the whole rule, and the question states it,
    so a traversal program can implement the same thing rather than guess at it.
    Hyphens collapse to spaces first, which is what makes the slug form
    ('sofia-reyes') meet the card form ('Sofia Reyes').
    """
    haystack = _flatten_name(entry)
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(_flatten_name(name)) + r"(?![a-z0-9])",
                  haystack)
        for name in known if _flatten_name(name)
    )


def _flatten_name(value: str) -> str:
    """Hyphens to spaces, whitespace collapsed, casefolded - on BOTH sides.

    Normalising only the entry meant a hyphenated CARD name could never match:
    `_counterparty_resolves("Jean-Luc Picard (Contoso)", {"jean-luc picard"})`
    returned False, the exact case the docstring above offers as working.
    """
    return re.sub(r"\s+", " ", value.replace("-", " ")).strip().lower()


def _country_hits(text: str) -> list[str]:
    """Reference-list countries named in `text`, matched on whole words.

    Plain substring matching, which this used until 2026-08-13, found 'Russia'
    inside 'Russian' and credited three Uzbekistan threads with a country none of
    them mentions. The language is not the country, and an oracle that cannot
    tell them apart marks a correct traversal wrong.
    """
    lowered = text.lower()
    return sorted(
        c for c in COUNTRY_NAMES
        if re.search(r"(?<![a-z])" + re.escape(c.lower()) + r"(?![a-z])", lowered)
    )


def _open_followup_count(body: str) -> int:
    match = _OPEN_FOLLOWUPS_RE.search(body)
    return len(_UNCHECKED_RE.findall(match.group(1))) if match else 0


def _people_file(corpus: CorpusPaths) -> Path:
    return corpus.context / "people.md"


def _pipeline_file(corpus: CorpusPaths) -> Path:
    return corpus.context / "pipeline.md"


# ============================================================
# Aggregating oracles -- the class /census is proposed for
# ============================================================

def oracle_agg_01(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Active threads not touched in over 30 days, excluding quiet ones.

    Quiet threads are excluded because the operator has asked not to be shown
    them; counting them as forgotten would contradict `/thread quiet`.
    """
    active = _active(corpus)
    hits = [
        t for t in active
        if not is_quiet(t, today)
        and (d := _iso(t.last_touched)) is not None
        and (today - d).days > STALE_THREAD_DAYS
    ]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t in hits},
        value=len(hits),
        population=len(active),
    )


def oracle_agg_02(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Pipeline companies mentioned in no active thread.

    Cross-source: the answer exists in neither file alone. Scored against the
    pipeline file itself, since that is where the unmatched rows live -- the
    absent threads have no path to name.

    Replaced the original "red-health contacts with an open thread" on
    2026-08-13: the live CRM holds zero red contacts, so that question's truth
    was empty by construction.
    """
    stages = parse_pipeline_stages(_pipeline_file(corpus))
    corpus_text = " ".join(
        (t.title + " " + " ".join(t.counterparties or []) + " " + t.body).lower()
        for t in _active(corpus)
    )
    # Whole-word, for the reason `_country_hits` is whole-word: a substring test
    # here is the same latent defect wearing a company's name. Four of the 28
    # live rows are six characters or fewer, and a two-letter row name inside an
    # ordinary word would silently mark a pipeline row as
    # "mentioned" and shrink this truth. Measured 2026-08-13: on today's corpus
    # substring and whole-word agree on all 28, so this changes no number now -
    # it removes a hazard from the one measurement the cross_source Non-Goal
    # rests on, before a shorter row makes it fire.
    unmatched = sorted(
        c for c in stages
        if c and not re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])",
                               corpus_text)
    )
    pipeline_rel = corpus.rel(_pipeline_file(corpus)) if _pipeline_file(corpus).exists() else None
    return OracleAnswer(
        kind="count",
        paths={pipeline_rel} if pipeline_rel else set(),
        value=len(unmatched),
        detail={"companies": unmatched, "pipeline_total": len(stages)},
        population=len(stages),
    )


def oracle_agg_03(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """People named in context/people.md with no CRM card.

    Reads the hand-authored summary bullets only, never the generated Contact
    Radar table -- see `_PEOPLE_BULLET_RE`.
    """
    people_file = _people_file(corpus)
    if not people_file.exists():
        return OracleAnswer(kind="count", value=0)
    section = _PEOPLE_SECTION_RE.search(people_file.read_text(encoding="utf-8"))
    names = _PEOPLE_BULLET_RE.findall(section.group(1) if section else "")
    known = _contact_names(corpus)
    # Containment, not equality -- the same rule `_counterparty_resolves` already
    # applies to the other free-prose name field in this module. The bullet
    # pattern is deliberately loose (see the comment above `_PEOPLE_BULLET_RE`)
    # precisely because the live corpus carries a bullet of the form
    # `Name Surname / "Nick" (COO)`, and comparing that whole capture to a card
    # name for EQUALITY reported the one person this question has ground truth
    # for as having no card. The question became "which people are named" rather
    # than "which people have no card".
    missing = sorted(n.strip() for n in names if not _counterparty_resolves(n, known))
    return OracleAnswer(
        kind="count",
        paths={corpus.rel(people_file)},
        value=len(missing),
        detail={"names": missing, "people_total": len(names)},
        population=len(names),
    )


def oracle_agg_04(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Pipeline rows with no CRM card carrying that `pipeline_company`."""
    stages = parse_pipeline_stages(_pipeline_file(corpus))
    covered = {
        (fm.get("pipeline_company") or "").strip().lower()
        for _, fm in _contacts(corpus)
        if (fm.get("pipeline_company") or "").strip()
    }
    orphans = sorted(c for c in stages if c not in covered)
    pipeline_file = _pipeline_file(corpus)
    return OracleAnswer(
        kind="count",
        paths={corpus.rel(pipeline_file)} if pipeline_file.exists() else set(),
        value=len(orphans),
        detail={"companies": orphans, "pipeline_total": len(stages)},
        population=len(stages),
    )


def oracle_agg_05(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Prospect cards not touched in over 60 days."""
    prospects = [
        (p, fm) for p, fm in _contacts(corpus)
        if fm.get("relationship_type") == "prospect"
    ]
    hits = [
        p for p, fm in prospects
        if (d := _iso(fm.get("last_touch"))) is not None
        and (today - d).days > STALE_PROSPECT_DAYS
    ]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(p) for p in hits},
        value=len(hits),
        population=len(prospects),
    )


def oracle_agg_06(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Active threads naming a counterparty who has no CRM card.

    Resolution rule: `_counterparty_resolves`. The population is the active
    threads that carry a non-empty `counterparties:` list, because a thread with
    no counterparty cannot name an unknown one and is not a candidate.
    """
    known = _contact_names(corpus)
    candidates = [t for t in _active(corpus) if (t.counterparties or [])]
    hits = []
    for t in candidates:
        missing = [c for c in t.counterparties if not _counterparty_resolves(c, known)]
        if missing:
            hits.append((t, missing))
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t, _ in hits},
        value=len(hits),
        detail={corpus.rel(t.path): m for t, m in hits},
        population=len(candidates),
    )


def oracle_agg_07(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Auto-memory files carrying a `[[wikilink]]` to a file that does not exist.

    A dangling link is legitimate by convention (it marks a memory worth writing),
    so this counts a graph property, not a defect.
    """
    if not corpus.auto_memory.exists():
        return OracleAnswer(kind="paths")
    files = sorted(corpus.auto_memory.glob("*.md"))
    stems = {p.stem for p in files}
    hits, broken = [], {}
    for p in files:
        targets = [_link_stem(t) for t in _WIKILINK_RE.findall(p.read_text(encoding="utf-8"))]
        dangling = sorted({t for t in targets if t and t not in stems})
        if dangling:
            hits.append(p)
            broken[corpus.rel(p)] = dangling
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(p) for p in hits},
        value=len(hits),
        detail=broken,
        population=len(files),
    )


def oracle_agg_08(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Active threads with no open follow-up."""
    active = _active(corpus)
    hits = [t for t in active if _open_followup_count(t.body) == 0]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t in hits},
        value=len(hits),
        population=len(active),
    )


def oracle_agg_09(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """Threads naming two or more countries from the reference list.

    Matching rule: `_country_hits`, whole-word and English-only. The list is the
    reference list, not the operator's markets, so the question names it.
    """
    threads = _threads(corpus)
    hits, found = [], {}
    for t in threads:
        names = _country_hits(t.title + " " + t.body)
        if len(names) >= MIN_COUNTRIES_FOR_MULTI:
            hits.append(t)
            found[corpus.rel(t.path)] = names
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t in hits},
        value=len(hits),
        detail=found,
        population=len(threads),
    )


def oracle_agg_10(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """CRM cards whose `status` is anything other than `active`.

    Replaced the original "cards with an expired `radar_freeze_until`" on
    2026-08-13: all 135 live freezes are future-dated, so that truth was empty.
    """
    contacts = _contacts(corpus)
    hits = [
        p for p, fm in contacts
        if (fm.get("status") or "").strip() and (fm.get("status") or "").strip() != "active"
    ]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(p) for p in hits},
        value=len(hits),
        population=len(contacts),
    )


# ============================================================
# Control oracles -- single STATED fact
#
# These test the INDEX, not the primitive. `/recall` is expected to be strong
# here; a low control mean means the index cannot reach a fact it should reach,
# and the aggregating verdict beside it would be a comparison against a broken
# index rather than a measurement.
#
# The defining property, learned by getting it wrong on 2026-08-13: a control's
# answer must be LITERALLY WRITTEN in the file that constitutes it. The first
# version of this group asked for set extrema -- the most recently touched
# thread, the longest-untouched card, the most linked-to memory. No file states
# that it holds an extremum, so those were aggregating questions wearing a
# control's label, and the group scored 0.500 while the two genuine controls
# (ctl-01, ctl-03) both scored 1.00. A control group that cannot be answered by
# retrieval cannot certify retrieval.
#
# The second defining property, learned on the same day: a control's truth must
# have cardinality EXACTLY 1. "Find every file carrying marker X" is a traversal
# question again whenever X sits in more than one file, and the measurement said
# so plainly -- all three cardinality-1 controls scored 1.00, while the two that
# returned sets scored 0.50 and 0.17. Retrieval reaches a stated fact; it does
# not enumerate a set. That is the whole thesis of the benchmark, and a control
# group must not accidentally test it.
#
# All five stay structural and name no entity, and between them they span three
# layers (threads, crm, auto-memory) so a single sick layer cannot pass unseen.
# ============================================================

# Frontmatter markers the controls look for. Each is a token that sits verbatim
# in the file, which is what makes the question answerable by retrieval at all.
CONTROL_CRM_STATUS = "dormant"
CONTROL_CRM_TYPE = "customer"
CONTROL_MEMORY_INDEX = "MEMORY.md"



def oracle_ctl_01(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """The thread standing in status `on-hold`. Stated: `status: on-hold`."""
    threads = _threads(corpus)
    hits = [t for t in threads if t.status == "on-hold"]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t in hits},
        value=len(hits),
        population=len(threads),
    )


def oracle_ctl_02(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """CRM cards standing in status `dormant`. Stated: `status: dormant`."""
    contacts = _contacts(corpus)
    hits = [
        p for p, fm in contacts
        if (fm.get("status") or "").strip() == CONTROL_CRM_STATUS
    ]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(p) for p in hits},
        value=len(hits),
        detail={"status": CONTROL_CRM_STATUS},
        population=len(contacts),
    )


def oracle_ctl_03(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """The thread carrying a dated quiet marker. Stated: `quiet_until: <date>`."""
    threads = _threads(corpus)
    hits = [t for t in threads if t.quiet_until]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(t.path) for t in hits},
        value=len(hits),
        detail={corpus.rel(t.path): t.quiet_until for t in hits},
        population=len(threads),
    )


def oracle_ctl_04(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """The contact typed `customer`. Stated: `relationship_type: customer`."""
    contacts = _contacts(corpus)
    hits = [
        p for p, fm in contacts
        if (fm.get("relationship_type") or "").strip() == CONTROL_CRM_TYPE
    ]
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(p) for p in hits},
        value=len(hits),
        detail={"relationship_type": CONTROL_CRM_TYPE},
        population=len(contacts),
    )


def oracle_ctl_05(corpus: CorpusPaths, today: date) -> OracleAnswer:
    """The memory file that indexes the others. Stated: its own title, "Memory index"."""
    index_file = corpus.auto_memory / CONTROL_MEMORY_INDEX
    if not index_file.exists():
        return OracleAnswer(kind="paths")
    return OracleAnswer(
        kind="paths",
        paths={corpus.rel(index_file)},
        value=1,
        detail={"file": CONTROL_MEMORY_INDEX},
    )


# ============================================================
# Registry
# ============================================================

ORACLES: dict[str, Callable[[CorpusPaths, date], OracleAnswer]] = {
    "agg-01": oracle_agg_01,
    "agg-02": oracle_agg_02,
    "agg-03": oracle_agg_03,
    "agg-04": oracle_agg_04,
    "agg-05": oracle_agg_05,
    "agg-06": oracle_agg_06,
    "agg-07": oracle_agg_07,
    "agg-08": oracle_agg_08,
    "agg-09": oracle_agg_09,
    "agg-10": oracle_agg_10,
    "ctl-01": oracle_ctl_01,
    "ctl-02": oracle_ctl_02,
    "ctl-03": oracle_ctl_03,
    "ctl-04": oracle_ctl_04,
    "ctl-05": oracle_ctl_05,
}


def resolve(question_id: str) -> Callable[[CorpusPaths, date], OracleAnswer]:
    """Look up an oracle by question id, failing loudly on an unknown id."""
    try:
        return ORACLES[question_id]
    except KeyError:
        raise KeyError(
            f"no oracle registered for question id {question_id!r}; "
            f"known ids: {', '.join(sorted(ORACLES))}"
        ) from None


# `scan_contacts`, `parse_config` and `is_radar_frozen` are imported for callers
# that need CRM health or freeze semantics identical to `crm-health.py`. No
# oracle uses them today: the live corpus holds zero red-health contacts and no
# expired freeze, which is exactly why the two questions that depended on them
# were replaced on 2026-08-13. Kept as the sanctioned entry points so a future
# question reaches for the shared implementation instead of writing a third one.
__all__ = [
    "COUNTRY_NAMES",
    "ORACLES",
    "STALE_PROSPECT_DAYS",
    "STALE_THREAD_DAYS",
    "CorpusPaths",
    "OracleAnswer",
    "is_radar_frozen",
    "parse_config",
    "resolve",
    "scan_contacts",
]
