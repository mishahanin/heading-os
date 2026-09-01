"""Four more readers whose documented fallback a non-UTF-8 byte walked past.

Found by sweeping the production modules this shard's twelve test files guard,
with the same question `tests/test_a_decode_fix_that_landed_in_one_reader_of_four.py`
asked of `impeccable_engine`: does the handler around this `read_text` see a
`UnicodeDecodeError`? It subclasses `ValueError`, so it is a SIBLING of
`json.JSONDecodeError` and no relation to `OSError`, and none of these four
handlers named anything that covers it.

Each function below states a fallback in its own docstring or in the seam its
caller depends on, and each raised instead of taking it. MEASURED 2026-09-01
against the unfixed tree, every function driven directly with a real `0xff` byte
on disk:

    generate-dashboard.read_file            RAISED UnicodeDecodeError
    chronicle._personal_keywords            RAISED UnicodeDecodeError
    crm_next.last_interaction_excerpt       RAISED UnicodeDecodeError
    datastore-extract.update_index          RAISED UnicodeDecodeError

What each one costs:

  * `read_file` is the reader behind every dashboard panel. `collect_calendar`
    and `collect_emails` already distinguish "the source was not read"
    (`source_read: False`, rendered NOT SYNCED) from "the source was empty", and
    `tests/test_eleven_panels_that_claimed_more_than_they_read.py` exists to keep
    that distinction. An undecodable file produced NEITHER: it produced a
    traceback and no dashboard at all.
  * `_personal_keywords` documents that the private keyword file is absent
    on a public clone and "just the generic defaults apply". A corrupt one killed
    `chronicle build`, which runs on a timer.
  * `last_interaction_excerpt` documents the string it falls back to. It runs
    per contact under `/cold-sweep`, so one byte-corrupt CRM record took the
    whole sweep down rather than one card.
  * `update_index` already carries a comment about a failure that arrived "AFTER
    every file had already been extracted, so the work was done and the index
    update died". An undecodable INDEX.md is that same failure, one line up.

Every case below is paired with the direction that keeps the fix from being
"swallow everything": a good file on the same path is still read, and the value
read is asserted rather than merely its truthiness.

Run: .venv/bin/python -m pytest
     tests/test_four_documented_fallbacks_no_undecodable_byte_could_reach.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UNDECODABLE = b"\xff\xfe\x00bad"


def _load(stem: str, relpath: str):
    spec = importlib.util.spec_from_file_location(stem, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[stem] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1 - the dashboard's shared reader, and the seam it bypassed
# ============================================================
@pytest.fixture(scope="module")
def gd():
    return _load("dashboard_decode_sweep", "scripts/generate-dashboard.py")


def test_an_undecodable_source_reads_as_unread_not_as_a_traceback(gd, tmp_path):
    path = tmp_path / "upcoming.md"
    path.write_bytes(UNDECODABLE)

    assert gd.read_file(path) == ""


def test_an_undecodable_source_says_so_on_stderr(gd, tmp_path, capsys):
    """Silence here is the worse half: "" is also what an empty file gives, and
    the panel would then report NOT SYNCED with no reason anywhere."""
    path = tmp_path / "upcoming.md"
    path.write_bytes(UNDECODABLE)

    gd.read_file(path)

    assert str(path) in capsys.readouterr().err


def test_the_calendar_panel_reports_the_source_as_unread(gd, tmp_path,
                                                         monkeypatch):
    """The seam this defect bypassed, driven end to end.

    `source_read` False is what `_sync_label` turns into NOT SYNCED. An
    undecodable calendar has to arrive there, not as an exception out of
    `collect_calendar`.
    """
    path = tmp_path / "upcoming.md"
    path.write_bytes(UNDECODABLE)
    monkeypatch.setattr(gd, "calendar_file", lambda p=path: p)

    result = gd.collect_calendar()

    assert result["source_read"] is False
    assert result["meetings"] == []


def test_a_readable_source_is_still_returned_verbatim(gd, tmp_path):
    """The control. A `read_file` that answered "" on everything would pass both
    tests above and blank every panel on the page."""
    path = tmp_path / "upcoming.md"
    path.write_text("# Calendar\n\nbody\n", encoding="utf-8")

    assert gd.read_file(path) == "# Calendar\n\nbody\n"


def test_an_absent_source_is_still_the_empty_string(gd, tmp_path):
    assert gd.read_file(tmp_path / "nothing.md") == ""


# ============================================================
# 2 - chronicle's personal keywords, documented to fall back to defaults
# ============================================================
@pytest.fixture(scope="module")
def ch():
    return _load("chronicle_decode_sweep", "scripts/chronicle.py")


@pytest.fixture()
def keyword_root(ch, monkeypatch, tmp_path):
    """A data root whose keyword file is the only thing that exists, with the
    module-level cache cleared so each case really reads from disk."""
    monkeypatch.setattr(ch, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(ch, "_PERSONAL_KEYWORDS_CACHE", None)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return tmp_path / "config" / "chronicle-personal-keywords.txt"


def test_an_undecodable_keyword_file_falls_back_to_the_defaults(ch, keyword_root):
    keyword_root.write_bytes(UNDECODABLE)

    assert ch._personal_keywords() == tuple(ch._DEFAULT_PERSONAL_KEYWORDS)


def test_an_undecodable_keyword_file_names_itself_on_stderr(ch, keyword_root,
                                                            capsys):
    keyword_root.write_bytes(UNDECODABLE)

    ch._personal_keywords()

    assert "chronicle-personal-keywords" in capsys.readouterr().err


def test_a_readable_keyword_file_still_adds_its_keywords(ch, keyword_root):
    """The control, and the one that binds: a loader that always returned the
    defaults would pass both tests above and silently ignore the operator's
    whole private list."""
    keyword_root.write_text("# a comment\nmoneypenny\n\nSKYFALL\n",
                            encoding="utf-8")

    loaded = ch._personal_keywords()

    assert "moneypenny" in loaded
    assert "skyfall" in loaded
    assert set(ch._DEFAULT_PERSONAL_KEYWORDS) <= set(loaded)


# ============================================================
# 3 - the CRM excerpt, which runs once per contact under /cold-sweep
# ============================================================
@pytest.fixture(scope="module")
def crm_next():
    return _load("crm_next_decode_sweep", "scripts/crm_next.py")


def test_an_undecodable_contact_record_falls_back_to_the_documented_string(
    crm_next, tmp_path
):
    record = tmp_path / "vesper-lynd.md"
    record.write_bytes(b"## Interaction Log\n" + UNDECODABLE)

    assert crm_next.last_interaction_excerpt(record) == "(no prior interaction)"


def test_an_undecodable_contact_record_is_named_on_stderr(crm_next, tmp_path,
                                                          capsys):
    """Under a sweep of N contacts, the operator has to be able to tell WHICH
    record produced an empty excerpt."""
    record = tmp_path / "vesper-lynd.md"
    record.write_bytes(UNDECODABLE)

    crm_next.last_interaction_excerpt(record)

    assert "vesper-lynd.md" in capsys.readouterr().err


def test_a_readable_contact_record_still_yields_its_newest_entry(crm_next,
                                                                 tmp_path):
    """The control. An excerpt reader that always answered the fallback would
    pass both tests above and strip every draft of its context."""
    record = tmp_path / "vesper-lynd.md"
    record.write_text(
        "# Vesper Lynd\n\n## Interaction Log\n\n"
        "### 2026-08-30 | call\nAgreed to revisit the terms in September.\n\n"
        "### 2026-01-04 | email\nOlder note.\n",
        encoding="utf-8",
    )

    excerpt = crm_next.last_interaction_excerpt(record)

    assert "2026-08-30" in excerpt
    assert "revisit the terms" in excerpt
    assert "Older note" not in excerpt


def test_an_absent_contact_record_is_still_the_documented_string(crm_next,
                                                                 tmp_path):
    assert crm_next.last_interaction_excerpt(
        tmp_path / "nobody.md") == "(no prior interaction)"


# ============================================================
# 4 - the datastore index, updated after the extraction work is already done
# ============================================================
@pytest.fixture(scope="module")
def extract():
    return _load("datastore_extract_decode_sweep", "scripts/datastore-extract.py")


def test_an_undecodable_index_does_not_undo_a_completed_extraction(extract,
                                                                   tmp_path,
                                                                   monkeypatch):
    """`update_index` runs LAST. Raising here reports failure over work that
    succeeded, which is the outcome the comment inside it already names."""
    index = tmp_path / "INDEX.md"
    index.write_bytes(UNDECODABLE)
    monkeypatch.setattr(extract, "index_file", lambda p=index: p)
    monkeypatch.setattr(extract, "datastore_dir", lambda p=tmp_path: p)

    extract.update_index([(tmp_path / "pitch.pptx", tmp_path / "pitch-extract.md")])


def test_an_undecodable_index_says_which_file_it_could_not_read(extract,
                                                                tmp_path,
                                                                monkeypatch,
                                                                capsys):
    index = tmp_path / "INDEX.md"
    index.write_bytes(UNDECODABLE)
    monkeypatch.setattr(extract, "index_file", lambda p=index: p)
    monkeypatch.setattr(extract, "datastore_dir", lambda p=tmp_path: p)

    extract.update_index([(tmp_path / "pitch.pptx", tmp_path / "pitch-extract.md")])

    out = capsys.readouterr()
    assert "INDEX.md" in (out.out + out.err)


def test_a_readable_index_still_gains_its_row(extract, tmp_path, monkeypatch):
    """The control. An `update_index` that returned early on every file would
    pass both tests above and stop maintaining the index entirely."""
    index = tmp_path / "INDEX.md"
    index.write_text("# Datastore index\n\n| File | Companion | Added |\n",
                     encoding="utf-8")
    monkeypatch.setattr(extract, "index_file", lambda p=index: p)
    monkeypatch.setattr(extract, "datastore_dir", lambda p=tmp_path: p)

    extract.update_index([(tmp_path / "pitch.pptx", tmp_path / "pitch-extract.md")])

    assert "pitch.pptx" in index.read_text(encoding="utf-8")
