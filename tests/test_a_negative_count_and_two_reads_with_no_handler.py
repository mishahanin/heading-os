#!/usr/bin/env python3
"""Shard 10: an exclusion that printed a negative count, and reads with no try.

Three defects, one shape between them: a tool saying something its method never
established (`.claude/rules/scope-claims.md`).

THE STOP HOOK PRINTED "-1 SLOW TEST(S) NOT RUN HERE". `scripts/turn-check.py`
returns `DESELECTED_UNKNOWN = -1` from `_deselected` when the lane ran under
`-n auto`, because xdist prints no deselection summary at all and zero would be
a claim that nothing was excluded. The checker's own renderer, `_slow_note`,
has always spelled that state out in words. `.claude/hooks/turn-check.py` read
the same value as a count.

    MEASURED 2026-09-01 by driving the real hook, with a stub checker emitting
    a failing result carrying `deselected_slow: -1`:

      before   "Not covered by this check: -1 slow test(s) not run here: ..."
      after    "Not covered by this check: an unknown number of slow test(s)
                not run here: the parallel lane reports no deselection count..."

    Reachable on any campaign-sized failure: the parallel lane starts at 20
    matched files (`PARALLEL_FILE_THRESHOLD`) and the run this comment is about
    went 74 -> 111. The hook's message is the one an operator reads.

    Nothing saw it. `test_the_widening_note_sits_beside_the_other_exclusions`
    in `tests/test_a_checker_whose_crash_looked_like_a_clean_tree.py` drives the
    same branch with `deselected_slow=4`, so the count is exercised and only its
    POSITIVE case ever was.

TWO READS WITH NO HANDLER AT ALL. `UnicodeDecodeError` is a `ValueError`, and
the 2026-09-01 AST sweep behind `tests/test_four_readers_that_died_on_one_bad_byte.py`
looked for try-blocks whose handler names cannot catch one. A read with no try
around it answers that question by not being asked, so two of them survived:

    MEASURED the same day on a two-file corpus, one clean and one carrying a
    lone 0xe9:

      census_oracles.oracle_agg_07   RAISED UnicodeDecodeError  -> UnreadableCorpus
      census_oracles.oracle_agg_03   RAISED UnicodeDecodeError  -> UnreadableCorpus
      crm.scan_contacts              RAISED UnicodeDecodeError  -> the clean card

The two oracles are the module's ground truth, and `_threads` and `_contacts`
already route their reads through `_UNREADABLE`, which holds `ValueError`. So
one stray note aborted all fifteen oracles with a bare traceback naming a codec,
a byte and an offset and no path, which is the outcome `_threads`' own docstring
says was repaired. `_entity_name` had the same gap and is covered here too.

`scan_contacts` is the CRM health walk. It is an advisory reader, so it takes
the other degradation, the one `contact_index_by_email` took the same day: skip
the card, log the path, keep the rest. Losing every card to one is what it did
before.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import census_oracles as co  # noqa: E402
from scripts.utils.crm import scan_contacts  # noqa: E402

HOOK = ROOT / ".claude" / "hooks" / "turn-check.py"

# A lone 0xe9: valid latin-1, invalid UTF-8 on its own, and what a note pasted
# out of an older editor arrives as.
BAD_BYTE = b"\xe9"


# ============================================================
# The Stop hook and the count it could not have
# ============================================================

def _tree(tmp_path: Path, result: dict) -> Path:
    """A scratch workspace holding a COPY of the real hook and a stub checker.

    The hook derives `WORKSPACE` from its own `__file__` and `CHECKER` from
    that, so a tree of the same shape runs the real bytes in their own process.
    Same construction as `tests/test_a_checker_whose_crash_looked_like_a_clean_tree.py`.
    """
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(HOOK, hooks / "turn-check.py")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "turn-check.py").write_text(
        "import sys\n"
        f"print({json.dumps(json.dumps(result))})\n"
        "sys.exit(1)\n",
        encoding="utf-8")
    return tmp_path


_FAILING = {"status": "fail", "lane": "tests", "files": 30, "tests_run": 25,
            "failures": ["assert False"], "skipped_foreign": 0,
            "skipped_contract": 0, "unmeasured": 0, "scope_unknown": False}


def _reason(tmp_path: Path, **overrides) -> str:
    proc = subprocess.run(
        [sys.executable, str(_tree(tmp_path, dict(_FAILING, **overrides))
                             / ".claude" / "hooks" / "turn-check.py")],
        input="{}", capture_output=True, text=True, errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())["reason"]


def test_the_unknown_deselection_count_is_never_printed_as_a_number(tmp_path):
    """The measured defect, verbatim."""
    reason = _reason(tmp_path, deselected_slow=-1)

    assert "-1 slow test(s)" not in reason, reason
    assert "-1" not in reason, f"a negative count reached the operator: {reason}"


def test_the_unknown_deselection_count_is_named_as_unknown(tmp_path):
    """Saying nothing would be the same defect one level quieter: the exclusion
    is still happening, only its size is unreadable."""
    reason = _reason(tmp_path, deselected_slow=-1)

    assert "Not covered by this check:" in reason, reason
    assert "unknown number of slow test(s)" in reason, reason
    assert "run-tests.py" in reason, reason


def test_a_real_deselection_count_is_still_printed_as_a_number(tmp_path):
    """The other side. A hook that answered "unknown" for every value would
    satisfy both assertions above and throw away a number it really has."""
    reason = _reason(tmp_path, deselected_slow=4)

    assert "4 slow test(s) not run here" in reason, reason
    assert "unknown number" not in reason, reason


def test_no_deselection_at_all_still_says_nothing(tmp_path):
    """Zero is a real answer and prints no exclusion, which is what separates
    the sentinel from it."""
    reason = _reason(tmp_path, deselected_slow=0)

    assert "slow test(s)" not in reason, reason
    assert "Not covered by this check" not in reason, reason


def test_the_sentinel_the_hook_reads_is_the_one_the_checker_emits(tmp_path):
    """The two files agree by construction today because both call it negative.
    If the checker ever moves its sentinel to another value, this fails rather
    than letting the hook print it as a count again.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "turn_check_for_sentinel_check", ROOT / "scripts" / "turn-check.py")
    tc = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_for_sentinel_check"] = tc
    spec.loader.exec_module(tc)

    assert tc.DESELECTED_UNKNOWN < 0, (
        f"the checker's unknown-count sentinel is {tc.DESELECTED_UNKNOWN}, which "
        f"the hook's `slow < 0` test cannot recognise")
    reason = _reason(tmp_path, deselected_slow=tc.DESELECTED_UNKNOWN)
    assert "unknown number of slow test(s)" in reason, reason


# ============================================================
# The oracles, which must name the file rather than the byte
# ============================================================

def _corpus(tmp_path: Path) -> co.CorpusPaths:
    for sub in ("threads/business", "crm/contacts", "crm/address-book",
                "context", "auto-memory", "knowledge", "outputs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return co.CorpusPaths.from_fixture(tmp_path)


def test_an_auto_memory_note_that_is_not_utf8_is_named(tmp_path):
    """agg-07 walks every note. One bad byte took all fifteen oracles down with
    a traceback that named no file."""
    corpus = _corpus(tmp_path)
    (tmp_path / "auto-memory" / "clean.md").write_text("[[other]]\n", encoding="utf-8")
    (tmp_path / "auto-memory" / "moneypenny-brief.md").write_bytes(
        b"[[other]]\n" + BAD_BYTE + b"\n")

    with pytest.raises(co.UnreadableCorpus) as exc:
        co.oracle_agg_07(corpus, date(2026, 8, 25))

    assert "moneypenny-brief.md" in str(exc.value), str(exc.value)


def test_a_people_file_that_is_not_utf8_is_named(tmp_path):
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_bytes(
        b"## Key People\n\n- James Bond (COO)\n" + BAD_BYTE + b"\n")

    with pytest.raises(co.UnreadableCorpus) as exc:
        co.oracle_agg_03(corpus, date(2026, 8, 25))

    assert "people.md" in str(exc.value), str(exc.value)


def test_an_address_book_entity_that_is_not_utf8_is_named(tmp_path):
    """`_entity_name` reads the entity a relationship card points at. Same gap,
    reached through a card rather than through a walk."""
    corpus = _corpus(tmp_path)
    (tmp_path / "crm" / "contacts" / "bond.md").write_text(
        "---\nentity_ref: james-bond\nrelationship_type: prospect\n---\n",
        encoding="utf-8")
    (tmp_path / "crm" / "address-book" / "james-bond.md").write_bytes(
        b"---\nname: James Bond\n---\n" + BAD_BYTE + b"\n")
    (tmp_path / "context" / "people.md").write_text(
        "## Key People\n\n- James Bond (COO, Universal Exports)\n", encoding="utf-8")

    with pytest.raises(co.UnreadableCorpus) as exc:
        co.oracle_agg_03(corpus, date(2026, 8, 25))

    assert "james-bond.md" in str(exc.value), str(exc.value)


def test_the_refusal_says_what_to_do_about_it(tmp_path):
    """A named file with no instruction is half an answer; the sibling refusals
    in this module all say where the file should go."""
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_bytes(b"## Key People\n" + BAD_BYTE)

    with pytest.raises(co.UnreadableCorpus) as exc:
        co.oracle_agg_03(corpus, date(2026, 8, 25))

    assert "encoding" in str(exc.value), str(exc.value)


def test_a_real_non_ascii_note_is_still_read(tmp_path):
    """The anchor against over-refusal. A reader that rejected every byte above
    0x7f would pass every test above and refuse half a real corpus."""
    corpus = _corpus(tmp_path)
    (tmp_path / "auto-memory" / "clean.md").write_text(
        "[[other]] caf\u00e9 na\u00efve\n", encoding="utf-8")

    answer = co.oracle_agg_07(corpus, date(2026, 8, 25))

    assert answer.value == 1, answer.detail


def test_a_readable_corpus_still_answers(tmp_path):
    """Anchor for agg-03: the refusal must not have become unconditional."""
    corpus = _corpus(tmp_path)
    (tmp_path / "context" / "people.md").write_text(
        "## Key People\n\n- James Bond (COO, Universal Exports)\n", encoding="utf-8")
    (tmp_path / "crm" / "contacts" / "james-bond.md").write_text(
        "---\nname: James Bond\ntype: prospect\n---\n", encoding="utf-8")

    assert co.oracle_agg_03(corpus, date(2026, 8, 25)).value == 0


# ============================================================
# The CRM health walk, which must lose one card and not the rest
# ============================================================

def _card(directory: Path, slug: str, name: str) -> Path:
    path = directory / f"{slug}.md"
    path.write_text(
        f"---\nname: {name}\ntype: prospect\nlast_touch: 2026-01-01\n---\n\nNotes.\n",
        encoding="utf-8")
    return path


def test_one_unreadable_card_does_not_take_the_readable_ones(tmp_path):
    """The measured defect: `scan_contacts` raised and returned nothing, so CRM
    health, the morning dashboard and the overdue set all died on one file."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    _card(contacts_dir, "jane-moneypenny", "Jane Moneypenny")
    (contacts_dir / "broken-card.md").write_bytes(
        b"---\nname: Broken Card\ntype: prospect\n---\n\nNotes " + BAD_BYTE + b"\n")

    found, _tribe, _dangling, _stages, _aliases = scan_contacts(
        {}, today=date(2026, 8, 25), contacts_dir=contacts_dir,
        workspace_root=tmp_path)

    assert [c["name"] for c in found] == ["Jane Moneypenny"], found


def test_the_skipped_card_is_named_on_the_log(tmp_path, caplog):
    """A guard that silently drops a record is the same defect one level
    quieter: the person stops accruing red debt and stops being reported."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "broken-card.md").write_bytes(
        b"---\nname: Broken Card\n---\n" + BAD_BYTE + b"\n")

    with caplog.at_level(logging.WARNING, logger="scripts.utils.crm"):
        scan_contacts({}, today=date(2026, 8, 25), contacts_dir=contacts_dir,
                      workspace_root=tmp_path)

    assert "broken-card.md" in caplog.text, caplog.text


def test_a_clean_corpus_logs_nothing(tmp_path, caplog):
    """The other side. A walk that warned on every card would satisfy the test
    above and make the warning unreadable."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    _card(contacts_dir, "jane-moneypenny", "Jane Moneypenny")
    _card(contacts_dir, "james-bond", "James Bond")

    with caplog.at_level(logging.WARNING, logger="scripts.utils.crm"):
        found, *_ = scan_contacts({}, today=date(2026, 8, 25),
                                  contacts_dir=contacts_dir,
                                  workspace_root=tmp_path)

    assert len(found) == 2, found
    assert "unreadable" not in caplog.text, caplog.text


def test_a_real_non_ascii_card_is_still_scanned(tmp_path):
    """The anchor against over-refusal, on the CRM side."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    _card(contacts_dir, "rene-dubois", "Ren\u00e9 Dubois")

    found, *_ = scan_contacts({}, today=date(2026, 8, 25),
                              contacts_dir=contacts_dir, workspace_root=tmp_path)

    assert [c["name"] for c in found] == ["Ren\u00e9 Dubois"], found
