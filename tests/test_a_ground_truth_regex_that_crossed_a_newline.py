"""An empty frontmatter field must not absorb a date from a later line.

Found by the 2026-08-29 audit of `scripts/census-submodel-bench.py`.

`_truth` is the whole reason that file exists: "Ground truth is computed by code,
never by a judge model." The `field` third of that truth was computed with

    rf"^{re.escape(FIELD_KEY)}:\\s*'?(\\d{{4}}-\\d{{2}}-\\d{{2}})"

under `re.M`. `\\s` matches a newline. So the anchor sat on `^last_touched:` at a
line start, the gap then ate the line break and any blank lines after it, and
the date group matched a date that BEGAN A LATER LINE. For a document whose
`last_touched` is empty or absent, the oracle recorded that later date as the
field's value.

The consequence is worse than a wrong number. A model answering `null`, which is
correct, lost the point; a model hallucinating the same way the regex did won
it. The instrument scored in favour of the failure it was built to detect.

`[^\\S\\n]*` keeps the value on the key's own line.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "census_submodel_bench_truth", ROOT / "scripts" / "census-submodel-bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["census_submodel_bench_truth"] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
MARKER = "zzq-census-probe-token"


@pytest.mark.parametrize("document", [
    # The audit's reproduction, verbatim in shape.
    "last_touched:\n\n2026-01-15 was a busy day\n",
    # No blank line between them.
    "last_touched:\n2026-01-15 was a busy day\n",
    # Trailing spaces on the key line, then the date.
    "last_touched:   \n\n\n2026-01-15 shipped\n",
    # Inside plausible frontmatter, with the date arriving in the body.
    "---\nid: t-001\nlast_touched:\nstatus: active\n---\n\n2026-01-15 notes\n",
])
def test_an_empty_field_reads_as_absent_not_as_the_next_line_s_date(document):
    assert bench._truth(document, MARKER)["field"] is None


def test_a_value_on_the_key_s_own_line_is_still_read():
    """The negative cases above prove nothing if the regex now matches nothing."""
    assert bench._truth("last_touched: '2026-01-20'\n", MARKER)["field"] == "2026-01-20"
    assert bench._truth("last_touched: 2026-01-20\n", MARKER)["field"] == "2026-01-20"
    assert bench._truth("last_touched:\t2026-01-20\n", MARKER)["field"] == "2026-01-20"


def test_the_key_must_still_start_its_line():
    """`^` under re.M, unchanged: a mention mid-line is not the field."""
    assert bench._truth("see last_touched: 2026-01-20\n", MARKER)["field"] is None


def test_the_document_that_used_to_score_a_correct_answer_wrong():
    """The full case, stated as the benchmark experiences it.

    A model reading this document and answering `{"field": null, ...}` is right.
    Before the fix the oracle held `"2026-01-15"` and marked it wrong.
    """
    document = (
        "---\n"
        "id: 2026-06-01-acme-renewal\n"
        "title: Acme Telecom renewal\n"
        "status: active\n"
        "last_touched:\n"
        "---\n"
        "\n"
        "## Open follow-ups\n"
        "\n"
        "- [ ] send the revised schedule\n"
        "\n"
        "2026-01-15 was when the first call happened.\n"
    )
    truth = bench._truth(document, MARKER)
    assert truth == {"field": None, "checkboxes": 1, "mentions": False}
