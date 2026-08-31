#!/usr/bin/env python3
"""Shard scripts-03-p4: a reader with a line cap, a writer without one.

`crm-backfill-exchange.py` read `last_touch` out of the first 30 lines of a
contact file. `bump_last_touch_in_text`, the writer it hands the result to,
matches `^last_touch:` over the WHOLE file with no cap. A contact whose
frontmatter runs longer — many tags, aliases, entity refs — therefore read as
unset, `""` compares below every ISO date, a bump was proposed, and `--apply`
REGRESSED the stored date to an older send. Silently, in an unattended path,
on the one field `crm-health.py` scores on: a healthy contact walks toward red.

`_stored_date`'s docstring justified returning `""` for an unreadable value by
saying "the operator sees it rather than the value being silently trusted". True
of `--dry-run`. `--apply` had no gate at all, and overwrote the unreadable value
with a stderr line as its whole notice — destroying the one signal that says
this file needs a human.

The demote flow in `crm-health.py` sliced frontmatter with `text.find("---", 3)`,
a plain substring search. A `---` inside a frontmatter VALUE ended the slice
early and the insert branch then wrote `status: dormant` INTO the middle of a
value line; a file with no frontmatter but a `---` rule in the body passed the
`fm_end == -1` guard and had a `^status:` line in an interaction-log entry
rewritten — the exact body-rewrite the comment there says the slicing prevents.
And its confirmation line reported `len(candidates)`, not what it wrote, in a
flow whose whole design is human-approved bulk mutation.

`council-aggregate.py` terminated three of its five section captures with
`(?=^## (?!Side-by-side)|\\Z)`, so a `## Side-by-side` heading did not end the
capture and the comparison table was folded into whichever model's response
preceded it — cross-attributed evidence in the file that exists to attribute.

Found by the 2026-08-23 engine audit, shard `scripts-03-p4`. Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def backfill():
    return _load("p03p4_backfill", "crm-backfill-exchange.py")


@pytest.fixture(scope="module")
def health():
    return _load("p03p4_health", "crm-health.py")


@pytest.fixture(scope="module")
def council():
    return _load("p03p4_council", "council-aggregate.py")


# ---------------------------------------------------------------------------
# Findings 2 and 3 -- the reader's line cap, and the missing gate
# ---------------------------------------------------------------------------

def _contact(tmp_path: Path, last_touch: str, padding_lines: int = 0) -> Path:
    """A contact file whose frontmatter carries `last_touch` after N filler keys."""
    filler = "\n".join(f"tag_{i}: value" for i in range(padding_lines))
    body = ["---", "name: Someone", "type: partner"]
    if filler:
        body.append(filler)
    body += [f"last_touch: {last_touch}", "---", "", "## Interaction Log", ""]
    path = tmp_path / "someone.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _bumps(backfill, monkeypatch, path: Path, send_date: str) -> dict:
    monkeypatch.setattr(backfill, "resolve_recipient", lambda email: path)
    return backfill.compute_proposed_bumps([("a@b.c", send_date)])


def test_a_long_frontmatter_does_not_hide_a_newer_stored_date(backfill,
                                                              monkeypatch,
                                                              tmp_path):
    """The regression, at the boundary that caused it. 40 filler keys put
    `last_touch` past line 30, where the reader stopped and the writer did
    not."""
    path = _contact(tmp_path, "2026-05-01", padding_lines=40)
    assert _bumps(backfill, monkeypatch, path, "2026-03-01") == {}, (
        "a bump was proposed against a NEWER stored date; --apply would rewrite "
        "2026-05-01 back to 2026-03-01"
    )


def test_a_short_frontmatter_still_reads_the_same_way(backfill, monkeypatch,
                                                      tmp_path):
    """Anchor: the cap only ever mattered past line 30, so the near case must
    keep behaving identically."""
    path = _contact(tmp_path, "2026-05-01")
    assert _bumps(backfill, monkeypatch, path, "2026-03-01") == {}


def test_a_genuinely_older_stored_date_is_still_bumped(backfill, monkeypatch,
                                                       tmp_path):
    """Anchor: refusing every bump would pass both tests above and make the
    script a no-op."""
    path = _contact(tmp_path, "2026-01-01", padding_lines=40)
    proposed = _bumps(backfill, monkeypatch, path, "2026-03-01")
    assert proposed[path][:2] == ("2026-01-01", "2026-03-01")


def test_a_send_on_the_stored_date_is_not_a_bump(backfill, monkeypatch, tmp_path):
    """`>` and not `>=`. An equal date is already recorded, so proposing it
    rewrites the file to the bytes it already holds — a no-op write that still
    counts in the "N bumps applied" line the operator reads, on every run."""
    path = _contact(tmp_path, "2026-03-01", padding_lines=40)
    assert _bumps(backfill, monkeypatch, path, "2026-03-01") == {}


def test_an_absent_last_touch_is_bumped_and_is_not_called_unreadable(
        backfill, monkeypatch, tmp_path):
    path = tmp_path / "no-touch.md"
    path.write_text("---\nname: X\n---\n", encoding="utf-8")
    proposed = _bumps(backfill, monkeypatch, path, "2026-03-01")
    current, proposed_date, unreadable = proposed[path]
    assert (current, proposed_date) == ("", "2026-03-01")
    assert unreadable is False, "an ABSENT value is not an unreadable one"


def test_an_unreadable_last_touch_is_flagged_not_trusted(backfill, monkeypatch,
                                                         tmp_path):
    path = _contact(tmp_path, "not-a-date")
    proposed = _bumps(backfill, monkeypatch, path, "2026-03-01")
    assert proposed[path][2] is True


def test_apply_refuses_to_overwrite_an_unreadable_value(backfill, monkeypatch,
                                                        tmp_path, capsys):
    """The review the docstring promises, on the path that writes."""
    path = _contact(tmp_path, "not-a-date")
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(backfill, "fetch_sent_items_recent", lambda days: [])
    monkeypatch.setattr(backfill, "compute_proposed_bumps",
                        lambda items: {path: ("", "2026-03-01", True)})
    assert backfill.cmd_apply(90) == 0
    assert path.read_text(encoding="utf-8") == before, (
        "the unreadable value was overwritten with no confirmation"
    )
    out = capsys.readouterr().out
    assert "[skipped]" in out and "Applied 0 bumps" in out


def test_apply_still_writes_a_readable_bump(backfill, monkeypatch, tmp_path,
                                            capsys):
    """Anchor: skipping everything would pass the test above."""
    path = _contact(tmp_path, "2026-01-01")
    monkeypatch.setattr(backfill, "fetch_sent_items_recent", lambda days: [])
    monkeypatch.setattr(backfill, "compute_proposed_bumps",
                        lambda items: {path: ("2026-01-01", "2026-03-01", False)})
    assert backfill.cmd_apply(90) == 0
    assert "last_touch: 2026-03-01" in path.read_text(encoding="utf-8")
    # The count too. A summary that says 0 while a file WAS rewritten is the
    # same defect as the demote flow's, pointed the other way.
    assert "Applied 1 bumps" in capsys.readouterr().out


def test_the_dry_run_names_what_apply_will_skip(backfill, monkeypatch, tmp_path,
                                                capsys):
    path = _contact(tmp_path, "not-a-date")
    monkeypatch.setattr(backfill, "fetch_sent_items_recent", lambda days: [])
    monkeypatch.setattr(backfill, "compute_proposed_bumps",
                        lambda items: {path: ("", "2026-03-01", True)})
    backfill.cmd_dry_run(90)
    out = capsys.readouterr().out
    assert "unreadable" in out
    assert "0 relationship records would be updated" in out


# ---------------------------------------------------------------------------
# Finding 5 -- the frontmatter fence, found by anchor
# ---------------------------------------------------------------------------

def test_a_dashed_value_does_not_end_the_frontmatter(health):
    text = ("---\nname: X\nnotes: 2026-01-01---draft\nstatus: active\n---\n"
            "\nbody\n")
    end = health.frontmatter_end(text)
    assert "status: active" in text[:end], (
        "the slice ended inside a value, so the status guard could not see the "
        "field it guards and the insert branch would split that value line"
    )


def test_a_body_rule_is_not_mistaken_for_frontmatter(health):
    """No opening fence, a `---` rule in the body. `text.find('---', 3)` found
    the rule, the `== -1` guard passed, and a `^status:` line in an interaction
    log was rewritten."""
    text = "# Someone\n\nnotes\n\n---\n\n## Interaction Log\nstatus: sent\n"
    assert health.frontmatter_end(text) == -1


def test_an_ordinary_contact_file_still_parses(health):
    """Anchor: returning -1 for everything would pass both tests above and turn
    the whole demote flow into a silent no-op."""
    text = "---\nname: X\nstatus: active\n---\n\nbody\n"
    end = health.frontmatter_end(text)
    assert end > 0
    assert text[:end].count("status: active") == 1
    assert "body" not in text[:end]


def test_a_frontmatter_that_never_closes_is_refused(health):
    assert health.frontmatter_end("---\nname: X\nstatus: active\n") == -1


def test_a_fence_with_trailing_spaces_still_closes(health):
    """`---   ` is a fence a hand-edited file really carries."""
    assert health.frontmatter_end("---\nname: X\n---   \n\nbody\n") > 0


# ---------------------------------------------------------------------------
# Finding 4 -- counting what was written
# ---------------------------------------------------------------------------

def test_the_demote_summary_counts_writes_not_candidates(health, monkeypatch,
                                                         tmp_path, capsys):
    """Two candidates, one unusable. The confirmation line for a human-approved
    bulk mutation said "2 contacts demoted"."""
    good = tmp_path / "good.md"
    good.write_text("---\nname: G\nstatus: active\n---\n\nbody\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("# no frontmatter here\n\nbody\n", encoding="utf-8")

    monkeypatch.setattr(health, "contacts_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(health, "parse_config", lambda path: {})
    # One contact so `main` does not take its "no contacts" early exit; the
    # report is not what is under test here, so it is stubbed too.
    monkeypatch.setattr(health, "scan_contacts",
                        lambda cfg: ([{"name": "G"}], [], [], {}, {}))
    monkeypatch.setattr(health, "format_terminal_report",
                        lambda contacts, warnings=None: "")
    monkeypatch.setattr("scripts.utils.crm.find_dormancy_candidates",
                        lambda contacts, today, threshold_days: [
                            {"file": "good.md", "name": "G", "slug": "good"},
                            {"file": "bad.md", "name": "B", "slug": "bad"},
                        ])
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    monkeypatch.setattr(sys, "argv", ["crm-health.py", "--demote-candidates"])
    health.main()

    out = capsys.readouterr().out
    assert "1 contacts demoted to dormant." in out, out
    assert "[skipped]" in out
    assert "status: dormant" in good.read_text(encoding="utf-8")
    assert "status: dormant" not in bad.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Finding 1 -- one model's answer, containing another section
# ---------------------------------------------------------------------------

TRANSCRIPT = """---
mode: full
timestamp: 2026-05-30
---
# Council Consultation - test
## Question
q
## Gemini's full response
gemini answer
## Side-by-side
comparison table
## Grok's full response
grok answer
"""


def test_the_comparison_table_is_not_folded_into_a_models_answer(council,
                                                                 tmp_path):
    path = tmp_path / "2026-05-30_council_120000_test.md"
    path.write_text(TRANSCRIPT, encoding="utf-8")
    t = council.parse_transcript(path)
    assert t is not None
    assert t.gemini_snippet == "gemini answer", (
        "the comparison section was attributed to Gemini in the file whose "
        f"whole purpose is attribution: {t.gemini_snippet!r}"
    )


def test_the_other_sections_are_still_captured(council, tmp_path):
    """Anchor: a terminator that ended every capture at once would pass the
    test above and empty the aggregate."""
    path = tmp_path / "2026-05-30_council_120000_test.md"
    path.write_text(TRANSCRIPT, encoding="utf-8")
    t = council.parse_transcript(path)
    assert t.question_snippet == "q"
    assert t.grok_snippet == "grok answer"


def test_the_canonical_layout_is_unchanged(council, tmp_path):
    """Side-by-side comes after every response in
    `.claude/skills/council/references/transcript-format.md`, so the lookahead
    never fired there. Removing it must not move the canonical readings."""
    text = ("---\nmode: full\n---\n# Council Consultation - t\n"
            "## Question\nq\n"
            "## Gemini's full response (verbatim)\ng\n"
            "## Grok's full response (verbatim)\nr\n"
            "## Kimi's full response (verbatim)\nk\n"
            "## Claude's view\nc\n"
            "## Side-by-side (as presented to user)\ntable\n")
    path = tmp_path / "2026-05-30_council_120000_t.md"
    path.write_text(text, encoding="utf-8")
    t = council.parse_transcript(path)
    assert (t.gemini_snippet, t.grok_snippet, t.kimi_snippet, t.claude_snippet) == ("g", "r", "k", "c")
