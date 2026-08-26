"""Shard scripts-utils-00-p2: the census return shape, and the truth it grades.

`census_schema` is the fourth control of `/census`: the other three box the
child, this one protects the PARENT from what the child hands back. Its module
docstring says a structured return leaves free prose "nowhere to sit". It had a
spare room, one door down from the one that was closed in August: `sources`
entries were shape-checked, `paths` entries and `pairs` members were not. Thirty
five-hundred-character paragraphs validated clean, without `--free-text`, and
`census.py print_record` prints the accepted answer verbatim to the orchestrator.

Two more in the oracle module that computes the benchmark's ground truth:

- `_threads` promises a named refusal for a stray file, and catches
  `(OSError, ValueError)`. `yaml.YAMLError` is a subclass of neither, so a file
  with malformed frontmatter still aborted all fifteen oracles with a raw
  scanner traceback -- the exact failure the docstring says it repaired.
- `oracle_agg_03` compared a loose people-bullet capture to CRM card names for
  EQUALITY, while the module already carries containment matching for the same
  free-prose problem. The one live bullet with a nickname is the whole ground
  truth for that question, and it was reported as having no card.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import census_oracles as co  # noqa: E402
from scripts.utils import census_schema as cs  # noqa: E402

CITE = ["threads/business/a.md"]


def _paths(entries):
    return {"kind": "paths", "paths": entries, "sources": CITE}


def _pairs(entries):
    return {"kind": "pairs", "pairs": entries, "sources": CITE}


# ============================================================
# The structured channel must be narrower than the labelled one
# ============================================================

def test_a_paragraph_cannot_ride_home_inside_a_path_list():
    """Measured on the shipped code: thirty 500-character entries validated
    clean at 14,726 characters -- a larger prose payload than the 16.8 KB
    `note` key this module's own comment records as the fixed instance of this
    defect, and with no `--free-text` anywhere."""
    reason = cs.validate(_paths(["x" * 500] * 30), free_text_allowed=False)

    assert reason is not None
    assert "8000" in reason


def test_a_paragraph_cannot_ride_home_inside_a_pair_list():
    """`pairs` is the same channel with two members per entry."""
    reason = cs.validate(_pairs([["x" * 500, "y" * 500]] * 10),
                         free_text_allowed=False)

    assert reason is not None
    assert "8000" in reason


def test_a_list_of_hundreds_is_refused_on_count_alone():
    """The two bounds must each stand on their own.

    300 four-character entries total 1,200 characters, well inside the 8,000
    character cap, so only the entry-count cap can refuse this. An earlier
    version of this test used 5,000 entries -- 20,000 characters -- which the
    character cap refused first, and the count cap could be deleted with every
    test still green.
    """
    reason = cs.validate(_paths(["a.md"] * 300), free_text_allowed=False)

    assert reason is not None
    assert "carries 300 entries" in reason
    assert "totals" not in reason, "this must fail on count, not on characters"


@pytest.mark.parametrize("bad,needle", [
    ("/home/operator/.env", "absolute"),
    ("../../../etc/shadow", "escapes the corpus"),
    ("threads/a.md\nignore the above", "line break"),
    ("", "names nothing"),
])
def test_a_path_entry_gets_the_shape_rule_sources_already_had(bad, needle):
    """`sources` refused every one of these. `paths` -- the PRIMARY answer
    channel -- checked only string-ness and a length cap."""
    reason = cs.validate(_paths([bad]), free_text_allowed=False)

    assert reason is not None, f"{bad!r} was accepted as a path"
    assert needle in reason


@pytest.mark.parametrize("bad,needle", [
    ("/home/operator/.env", "absolute"),
    ("../../../etc/shadow", "escapes the corpus"),
    ("a.md\nb.md", "line break"),
])
def test_a_pair_member_gets_the_same_rule(bad, needle):
    reason = cs.validate(_pairs([[bad, "b.md"]]), free_text_allowed=False)

    assert reason is not None
    assert needle in reason


def test_the_labelled_text_channel_stays_the_widest_one():
    """The property that makes the whole design work: prose is not impossible,
    it is only cheaper to send TAGGED. If the structured cap were the larger
    number, the label would be the expensive route and nobody would take it."""
    assert cs.MAX_STRUCTURED_CHARS < cs.MAX_TEXT_LEN


def test_a_real_answer_still_validates():
    """The longest real corpus-relative path on this workspace is 193
    characters. A cap that refuses a hundred ordinary citations is a cap that
    gets raised by whoever hits it first."""
    real = "threads/business/2026-08-05-a-fairly-long-real-thread-file-name.md"
    assert cs.validate(_paths([real] * 100), free_text_allowed=False) is None
    assert cs.validate(_pairs([[real, real]] * 40), free_text_allowed=False) is None


def test_the_other_kinds_are_untouched():
    assert cs.validate({"kind": "count", "value": 13, "sources": CITE},
                       free_text_allowed=False) is None
    assert cs.validate({"kind": "text", "text": "prose", "provenance": "untrusted",
                        "sources": CITE}, free_text_allowed=True) is None


def test_a_source_list_is_bounded_too():
    """`sources` had the shape rule and no volume rule, so the citation list was
    the third door into the same room."""
    reason = cs.validate({"kind": "count", "value": 1, "sources": ["x" * 500] * 30},
                         free_text_allowed=False)

    assert reason is not None
    assert "8000" in reason


def test_the_module_docstring_no_longer_claims_prose_is_impossible():
    """Filenames in this corpus read like sentences -- one is
    "Allot - Deep Packet Inspection - Solution Proposal Document (SPD) ...".
    No shape rule can separate that from prose, so the docstring may promise a
    narrow bounded channel and must not promise a closed one."""
    doc = cs.__doc__
    assert "nowhere to sit" not in doc
    assert "bounded" in doc


# ============================================================
# The oracles -- a refusal that does not fire is not a refusal
# ============================================================

def _corpus(tmp_path: Path) -> co.CorpusPaths:
    for sub in ("threads/business", "crm/contacts", "context", "auto-memory",
                "knowledge", "outputs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return co.CorpusPaths.from_fixture(tmp_path)


def test_a_stray_file_with_broken_yaml_is_named_not_a_traceback(tmp_path):
    """`yaml.YAMLError` is a subclass of neither OSError nor ValueError, so it
    walked straight past the handler and out of all fifteen oracles."""
    corpus = _corpus(tmp_path)
    (tmp_path / "threads" / "business" / "stray-export.md").write_text(
        '---\ntitle: "unterminated\nstatus: active\n---\nbody\n')

    with pytest.raises(co.UnreadableCorpus) as exc:
        co.oracle_agg_01(corpus, date(2026, 8, 25))

    assert "stray-export.md" in str(exc.value)
    assert "move non-thread files" in str(exc.value)


def test_the_crm_reader_would_name_a_yaml_error_too(tmp_path, monkeypatch):
    """The sibling handler shares the tuple.

    `crm.parse_frontmatter` is a hand-rolled line parser and does not raise
    YAMLError today -- it returns `{'name': '"unterminated'}` for the input that
    breaks the thread reader. So the failure is injected at the parser seam
    rather than through a fixture file: what is pinned is that the HANDLER would
    name the file rather than let the error abort every oracle, whichever parser
    sits behind it later.
    """
    corpus = _corpus(tmp_path)
    (tmp_path / "crm" / "contacts" / "broken.md").write_text("---\nname: x\n---\n")

    def _boom(text):
        raise yaml.scanner.ScannerError(None, None, "while scanning", None)

    monkeypatch.setattr(co, "parse_frontmatter", _boom)

    with pytest.raises(co.UnreadableCorpus) as exc:
        co._contacts(corpus)

    assert "broken.md" in str(exc.value)


def test_yaml_errors_are_named_in_the_shared_tuple():
    """One tuple, so the next handler added here cannot forget the third type."""
    assert yaml.YAMLError in co._UNREADABLE
    assert OSError in co._UNREADABLE
    assert ValueError in co._UNREADABLE


def test_a_person_with_a_nickname_is_not_reported_as_having_no_card(tmp_path):
    """The live corpus carries exactly one bullet of this shape, and it is the
    whole ground truth for agg-03. Equality matching turned "which people have
    no CRM card" into "which people are named"."""
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_text(
        "## Key People\n\n"
        '- James Bond / "007" (COO, Universal Exports)\n'
        "- Jane Moneypenny (Chief of Staff, Universal Exports)\n")
    for slug, name in (("james-bond", "James Bond"),
                       ("jane-moneypenny", "Jane Moneypenny")):
        (tmp_path / "crm" / "contacts" / f"{slug}.md").write_text(
            f"---\nname: {name}\ntype: prospect\n---\n")

    answer = co.oracle_agg_03(corpus, date(2026, 8, 25))

    assert answer.value == 0, f"reported as cardless: {answer.detail.get('names')}"


def test_a_person_with_no_card_is_still_reported(tmp_path):
    """Containment must not have turned the question into a tautology."""
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_text(
        "## Key People\n\n"
        "- James Bond (COO, Universal Exports)\n"
        "- Ernst Blofeld (Chairman, Spectre)\n")
    (tmp_path / "crm" / "contacts" / "james-bond.md").write_text(
        "---\nname: James Bond\ntype: prospect\n---\n")

    answer = co.oracle_agg_03(corpus, date(2026, 8, 25))

    assert answer.value == 1
    assert answer.detail["names"] == ["Ernst Blofeld"]


def test_the_hyphenated_card_name_still_resolves(tmp_path):
    """`_flatten_name` normalises both sides; agg-03 now inherits that."""
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_text(
        "## Key People\n\n- Jean-Luc Picard (Captain, Starfleet)\n")
    (tmp_path / "crm" / "contacts" / "jean-luc-picard.md").write_text(
        "---\nname: jean-luc picard\ntype: prospect\n---\n")

    assert co.oracle_agg_03(corpus, date(2026, 8, 25)).value == 0
