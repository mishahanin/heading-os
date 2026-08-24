"""The /census benchmark oracles, against a fixture whose answers are known.

Every expected answer in this file was worked out BY HAND from
`tests/fixtures/census_corpus/` before the oracles were run against it, and each
fixture file carries an HTML comment naming the oracles it serves. That ordering
is the point: an oracle checked only against its own output tests nothing, and an
oracle that is wrong makes the benchmark's verdict wrong silently -- which would
decide the fate of a whole primitive on a bad number.

Fixture date is pinned at 2026-06-15. Several oracles consume `today` directly,
so a floating clock would make these assertions rot within a month.
"""
from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import census_oracles
from scripts.utils.census_oracles import ORACLES, CorpusPaths, resolve

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "census_corpus"
TODAY = date(2026, 6, 15)

T = "threads/business"
C = "crm/contacts"
M = "auto-memory"


@pytest.fixture
def corpus() -> CorpusPaths:
    return CorpusPaths.from_fixture(FIXTURE)


def answer(corpus: CorpusPaths, qid: str):
    return resolve(qid)(corpus, TODAY)


# ============================================================
# Aggregating oracles
# ============================================================

def test_agg_01_stale_active_threads_excludes_the_quiet_one(corpus):
    """Only the Northwind thread is both active and untouched for over 30 days.

    Contoso is older still but carries `quiet_until: 2026-12-31`; counting it
    would contradict the operator's own instruction not to surface it.
    """
    a = answer(corpus, "agg-01")
    assert a.paths == {f"{T}/2026-01-10-northwind-core-upgrade.md"}
    assert a.value == 1


def test_agg_02_pipeline_companies_named_in_no_active_thread(corpus):
    """Vantage Systems and Kestrel Mobile: 2 of the 4 pipeline rows."""
    a = answer(corpus, "agg-02")
    assert a.value == 2
    assert a.detail["companies"] == ["kestrel mobile", "vantage systems"]
    assert a.detail["pipeline_total"] == 4
    assert a.paths == {"context/pipeline.md"}


def test_agg_03_people_without_a_crm_card(corpus):
    """James Bond alone; Alba Karimova and Henrik Vale both hold cards."""
    a = answer(corpus, "agg-03")
    assert a.value == 1
    assert a.detail["names"] == ["James Bond"]
    assert a.detail["people_total"] == 3


def test_agg_04_pipeline_rows_with_no_matching_crm_card(corpus):
    """Kestrel Mobile alone; the other three are each covered by a card."""
    a = answer(corpus, "agg-04")
    assert a.value == 1
    assert a.detail["companies"] == ["kestrel mobile"]


def test_agg_05_stale_prospects(corpus):
    """Alba, Mira and Sofia are prospects past 60 days.

    Piotr is a prospect touched 5 days ago; Henrik is 7 months stale but is a
    partner, not a prospect. Both are deliberate negatives.
    """
    a = answer(corpus, "agg-05")
    assert a.paths == {
        f"{C}/alba-karimova.md", f"{C}/mira-okafor.md", f"{C}/sofia-reyes.md",
    }


def test_agg_06_threads_naming_a_counterparty_with_no_card(corpus):
    """Contoso names James Bond and Quarterly names Dmitri Voll; neither has a card."""
    a = answer(corpus, "agg-06")
    assert a.paths == {
        f"{T}/2026-02-05-contoso-capital-raise.md",
        f"{T}/2026-06-15-quarterly-planning.md",
    }


def test_agg_07_memory_files_with_a_dangling_link(corpus):
    """Two files link to a note that does not exist; the link targets are reported."""
    a = answer(corpus, "agg-07")
    assert a.paths == {f"{M}/northwind-cadence.md", f"{M}/vantage-integration.md"}
    assert a.detail[f"{M}/northwind-cadence.md"] == ["missing-note-one"]
    assert a.detail[f"{M}/vantage-integration.md"] == ["missing-note-two"]


def test_agg_08_active_threads_with_no_open_follow_up(corpus):
    """Quarterly planning has no `## Open follow-ups` section at all."""
    a = answer(corpus, "agg-08")
    assert a.paths == {f"{T}/2026-06-15-quarterly-planning.md"}


def test_agg_09_threads_naming_two_or_more_countries(corpus):
    """Northwind (Portugal, Kenya), Vendor (Poland, Spain), Quarterly (Japan, Brazil, Kenya).

    Counts every thread regardless of status, and Contoso names only Germany, so
    a single-country thread is a live negative rather than an assumed one.
    """
    a = answer(corpus, "agg-09")
    assert a.paths == {
        f"{T}/2026-01-10-northwind-core-upgrade.md",
        f"{T}/2026-04-01-vendor-dispute.md",
        f"{T}/2026-06-15-quarterly-planning.md",
    }
    assert a.detail[f"{T}/2026-06-15-quarterly-planning.md"] == ["Brazil", "Japan", "Kenya"]


def test_agg_10_cards_in_a_non_active_status(corpus):
    """Mira (blocked), Sofia (lost), Henrik (dormant)."""
    a = answer(corpus, "agg-10")
    assert a.paths == {
        f"{C}/henrik-vale.md", f"{C}/mira-okafor.md", f"{C}/sofia-reyes.md",
    }


# ============================================================
# Control oracles -- a single STATED fact
# ============================================================

def test_ctl_01_the_on_hold_thread(corpus):
    """Stated fact: `status: on-hold` sits in the file's frontmatter."""
    a = answer(corpus, "ctl-01")
    assert a.paths == {f"{T}/2026-03-01-legacy-migration.md"}


def test_ctl_02_cards_in_status_dormant(corpus):
    """Stated fact: `status: dormant`."""
    a = answer(corpus, "ctl-02")
    assert a.paths == {f"{C}/henrik-vale.md"}


def test_ctl_03_the_thread_with_a_dated_quiet_marker(corpus):
    """Stated fact: `quiet_until: 2026-12-31`."""
    a = answer(corpus, "ctl-03")
    assert a.paths == {f"{T}/2026-02-05-contoso-capital-raise.md"}


def test_ctl_04_the_contact_typed_customer(corpus):
    """Stated fact: `relationship_type: customer`, on exactly one card."""
    a = answer(corpus, "ctl-04")
    assert a.paths == {f"{C}/ilya-vetrov.md"}


def test_ctl_05_the_memory_index_file(corpus):
    """Stated fact: the file is titled "Memory index" and names the others."""
    a = answer(corpus, "ctl-05")
    assert a.paths == {f"{M}/MEMORY.md"}


def test_every_control_answer_is_a_fact_stated_in_its_own_file(corpus):
    """The property that makes a control a control.

    The first version of this group asked for set extrema -- newest thread,
    longest-untouched card, most linked-to memory. No file states that it holds
    an extremum, so those were aggregation questions wearing a control's label,
    and the group scored 0.500 on the live corpus while the two genuine controls
    scored 1.00 each. A control group that retrieval cannot answer cannot certify
    retrieval. This test pins the repair: every control answer file must contain
    the literal token its question asks about.
    """
    expected = {
        "ctl-01": "status: on-hold",
        "ctl-02": "status: dormant",
        "ctl-03": "quiet_until:",
        "ctl-04": "relationship_type: customer",
        "ctl-05": "Memory index",
    }
    for qid, token in expected.items():
        for rel in answer(corpus, qid).paths:
            text = (FIXTURE / rel).read_text(encoding="utf-8")
            assert token in text, f"{qid}: {rel} does not state {token!r}"


def test_every_control_answer_has_cardinality_exactly_one(corpus):
    """The second property that makes a control a control.

    "Find every file carrying marker X" becomes a traversal question the moment X
    sits in more than one file, and the live corpus said so: the three
    cardinality-1 controls scored 1.00 each, the two that returned sets scored
    0.50 and 0.17. A control group must certify retrieval, not re-test the very
    thesis the aggregating group exists to measure.
    """
    for qid in ("ctl-01", "ctl-02", "ctl-03", "ctl-04", "ctl-05"):
        assert answer(corpus, qid).cardinality == 1, qid


def test_controls_span_more_than_one_corpus_layer(corpus):
    """A group drawn from one layer cannot notice that a different layer is sick."""
    layers = set()
    for qid in ("ctl-01", "ctl-02", "ctl-03", "ctl-04", "ctl-05"):
        for rel in answer(corpus, qid).paths:
            layers.add(rel.split("/")[0])
    assert layers == {"threads", "crm", "auto-memory"}


# ============================================================
# Properties that hold for the whole registry
# ============================================================

def test_no_oracle_returns_empty_truth_on_the_fixture(corpus):
    """Empty truth means the question measures nothing, and a 0.0 ceiling that
    means 'bad question' is indistinguishable in a report from one that means
    'retrieval is blind'. This is the guard that caught agg-02 and agg-10 on the
    live corpus before either was written."""
    empty = [qid for qid in ORACLES if answer(corpus, qid).is_empty()]
    assert empty == [], f"empty truth: {empty}"


def test_every_question_in_the_shipped_set_resolves_to_an_oracle():
    import json
    root = Path(__file__).resolve().parent.parent
    data = json.loads((root / "config" / "census-bench-questions.json").read_text(encoding="utf-8"))
    ids = [q["id"] for q in data["questions"]]
    assert len(ids) == len(set(ids)) == 15
    assert set(ids) == set(ORACLES)
    assert sum(1 for q in data["questions"] if q["group"] == "aggregate") == 10
    assert sum(1 for q in data["questions"] if q["group"] == "control") == 5


def test_oracles_never_read_the_clock():
    """`today` is an argument, so a `datetime.now()` inside an oracle would make
    the truth depend on when the test ran and would silently break reproducibility
    between the baseline run and the acceptance run weeks later."""
    source = Path(census_oracles.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("oracle_"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = ast.unparse(inner.func)
                if "now" in name or "today" in name.split(".")[-1:]:
                    offenders.append(f"{node.name}: {name}")
    assert offenders == [], f"oracle reads the clock: {offenders}"


def test_answer_paths_are_relative_posix_inside_the_corpus(corpus):
    """The ceiling is computed by intersecting these with `memory-index --json`
    output, which reports data-root-relative POSIX paths. An absolute path here
    would intersect to nothing and read as a ceiling of 0.0."""
    for qid in ORACLES:
        for path in answer(corpus, qid).paths:
            assert not path.startswith("/"), (qid, path)
            assert "\\" not in path, (qid, path)
            assert (FIXTURE / path).exists(), (qid, path)


def test_resolve_names_the_known_ids_when_given_an_unknown_one():
    with pytest.raises(KeyError) as excinfo:
        resolve("agg-99")
    assert "agg-01" in str(excinfo.value)


# ============================================================
# The two oracle defects found on 2026-08-13, and the guard that catches the class
# ============================================================
#
# Both were found by diffing a REJECTED acceptance run, and both had marked a
# correct traversal wrong. Neither is a hypothetical: each assertion below fails
# against the oracle as it stood that morning.


def _corpus_from(tmp_path: Path, *, threads: dict[str, str],
                 contacts: dict[str, str] | None = None) -> CorpusPaths:
    """A throwaway corpus holding only what one assertion needs."""
    tdir = tmp_path / "threads" / "business"
    tdir.mkdir(parents=True)
    for name, text in threads.items():
        (tdir / name).write_text(text, encoding="utf-8")
    cdir = tmp_path / "crm" / "contacts"
    cdir.mkdir(parents=True)
    for name, text in (contacts or {}).items():
        (cdir / name).write_text(text, encoding="utf-8")
    return CorpusPaths.from_fixture(tmp_path)


def _thread(title: str, *, counterparties: list[str] = (), body: str = "",
            status: str = "active") -> str:
    lines = ["---", f"id: {title.lower()}", f"title: {title}", f"status: {status}",
             "type: business", "classification: ceo-only", "opened: '2026-01-01'",
             "last_touched: '2026-06-14'", "links: {}", "tags: []"]
    if counterparties:
        lines.append("counterparties:")
        lines += [f"- {c}" for c in counterparties]
    lines += ["---", "", body]
    return "\n".join(lines) + "\n"


def _card(name: str) -> str:
    return (f"---\nname: {name}\nrelationship_type: prospect\n"
            "status: active\nlast_touch: '2026-06-01'\n---\n")


def test_agg_06_resolves_a_counterparty_written_with_a_role_suffix(tmp_path):
    """'Alba Karimova (Northwind, CTO)' names a person who HAS a card.

    Until 2026-08-13 the oracle compared the whole entry to a card name for
    equality, so every entry carrying a parenthetical or a slug missed. On the
    live corpus that was all 17 of 17, which made the answer "every active thread
    with a counterparty" and cost a correct traversal two marks.
    """
    corpus = _corpus_from(
        tmp_path,
        threads={"a.md": _thread("A", counterparties=["Alba Karimova (Northwind, CTO)"]),
                 "b.md": _thread("B", counterparties=["alba-karimova"]),
                 "c.md": _thread("C", counterparties=["James Bond (no card)"])},
        contacts={"alba.md": _card("Alba Karimova")},
    )
    a = resolve("agg-06")(corpus, TODAY)
    assert a.paths == {f"{T}/c.md"}
    assert a.population == 3


def test_agg_09_does_not_read_a_language_as_its_country(tmp_path):
    """'Russian' is not 'Russia'.

    A plain substring match credited three Uzbekistan threads with Russia on the
    live corpus, none of which mention the country, and marked a traversal that
    matched on word boundaries wrong for being right.
    """
    corpus = _corpus_from(tmp_path, threads={
        "a.md": _thread("A", body="The letter went out in Russian. Uzbekistan only."),
        "b.md": _thread("B", body="Split between Portugal and Kenya."),
    })
    a = resolve("agg-09")(corpus, TODAY)
    assert a.paths == {f"{T}/b.md"}
    assert a.detail[f"{T}/b.md"] == ["Kenya", "Portugal"]


def test_a_saturated_oracle_is_reported_as_saturated(tmp_path):
    """Every candidate selected means the predicate never fired negative."""
    corpus = _corpus_from(tmp_path, threads={
        "a.md": _thread("A", counterparties=["Nobody At All"]),
        "b.md": _thread("B", counterparties=["Also Nobody"]),
    })
    a = resolve("agg-06")(corpus, TODAY)
    assert a.selected == a.population == 2
    assert a.is_saturated()
    assert not a.is_empty()


def test_every_live_oracle_is_non_degenerate_at_both_ends(corpus):
    """The fixture exists to be a corpus where every question discriminates.

    An oracle that selects nothing, or selects everything, tests the population
    rather than its own predicate.
    """
    degenerate = []
    for qid in ORACLES:
        a = answer(corpus, qid)
        if a.is_empty():
            degenerate.append(f"{qid}: empty")
        elif a.is_saturated():
            degenerate.append(f"{qid}: saturated {a.selected}/{a.population}")
    assert degenerate == [], f"degenerate oracle(s): {degenerate}"


def test_an_unparseable_date_refuses_rather_than_shrinking_the_truth(tmp_path):
    """A broken date used to drop its record out of every stale set in silence."""
    corpus = _corpus_from(tmp_path, threads={
        "a.md": _thread("A").replace("last_touched: '2026-06-14'",
                                     "last_touched: 'not-a-date'"),
    })
    with pytest.raises(census_oracles.UnreadableCorpus) as excinfo:
        resolve("agg-01")(corpus, TODAY)
    assert "unparseable date" in str(excinfo.value)


def test_a_stray_file_in_the_thread_directory_is_named_not_a_traceback(tmp_path):
    """One scratch note used to abort all fifteen oracles with an opaque error."""
    corpus = _corpus_from(tmp_path, threads={
        "a.md": _thread("A"),
        "scratch.md": "just a note, no frontmatter\n",
    })
    with pytest.raises(census_oracles.UnreadableCorpus) as excinfo:
        resolve("agg-01")(corpus, TODAY)
    message = str(excinfo.value)
    assert "scratch.md" in message
    assert "move non-thread files" in message


def test_a_wikilink_written_with_an_extension_or_a_folder_is_not_dangling(tmp_path):
    """[[note.md]] and [[sub/note]] point at the same file as [[note]]."""
    memory = tmp_path / "auto-memory"
    memory.mkdir(parents=True)
    (memory / "note.md").write_text("target\n", encoding="utf-8")
    (memory / "linker.md").write_text(
        "see [[note.md]] and [[sub/note]] and [[missing-one]]\n", encoding="utf-8")
    (tmp_path / "threads" / "business").mkdir(parents=True)
    (tmp_path / "crm" / "contacts").mkdir(parents=True)

    a = resolve("agg-07")(CorpusPaths.from_fixture(tmp_path), TODAY)
    assert a.detail == {f"{M}/linker.md": ["missing-one"]}
