#!/usr/bin/env python3
"""Three slug builders threw away every non-Latin letter, then named a file.

Each of them cleans a title down to `[a-z0-9-]` and uses the result as a
filename stem. A Cyrillic title has no character in that set, so the result is
the empty string. Verified on 2026-08-27:

    render-doctype.slugify('Партнёрское предложение')  -> ''
    marp_render.generate_slug('Стратегия развития')     -> ''
    threads_lib.slugify('Договор')                      -> ''

The consequences differ by caller, and one of them loses work:

* `scripts/render-doctype.py` builds
  `{date}_{doctype}_{recipient}_{subject}.{ext}`. Two letters to two different
  Russian-named recipients on one day both render to `2026-08-27_letter__.pdf`,
  and the second silently overwrites the first, in PDF, DOCX and HTML.
* `scripts/marp_render.py` builds `31C-{title}-{date}`, so every Russian-titled
  deck rendered on one day collapses to `31C--27-Aug-2026`.
* `scripts/utils/threads_lib.py` fails safe: `new_thread_path` RAISES on an
  empty slug. It still loses information on a MIXED title, which is the common
  case. A thread titled "Миграция CRM на новый сервер" took the id `crm`:
  five words in, one word out.

The operator writes in Russian daily, so this is not a hypothetical script.

The fix is a transliteration PRE-PASS shared by all three, plus a stable
fallback for the two that name output files. It is a pre-pass on purpose: each
builder keeps its own ASCII rules, so every slug that worked before is
byte-identical afterwards. Unifying the three rule sets would rename existing
outputs and existing thread ids, which is a different change with a different
risk.

Found by the engine defect hunt, 2026-08-27.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.marp_render import generate_slug  # noqa: E402
from scripts.utils.slugs import stable_suffix, transliterate  # noqa: E402
from scripts.utils.threads_lib import new_thread_path  # noqa: E402
from scripts.utils.threads_lib import slugify as thread_slug  # noqa: E402


@pytest.fixture(scope="module")
def doctype():
    spec = importlib.util.spec_from_file_location(
        "render_doctype_under_test", ROOT / "scripts" / "render-doctype.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_doctype_under_test"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The shared pre-pass
# ============================================================

@pytest.mark.parametrize("text,expected", [
    ("Договор", "dogovor"),
    ("Стратегия", "strategiya"),
    ("ёлка", "elka"),
    ("Щука", "shchuka"),
    ("объезд", "obezd"),
    ("МЫС", "mys"),
])
def test_a_russian_word_becomes_readable_latin(text, expected):
    assert transliterate(text).lower() == expected


def test_ascii_text_passes_through_unchanged():
    """Every slug that worked before must be byte-identical afterwards."""
    for text in ("ODUN.ONE proposal", "31c.io", "A-B_C 123", ""):
        assert transliterate(text) == text


def test_punctuation_and_spacing_survive_the_pass():
    """The pre-pass replaces letters only; the caller's own rules do the rest."""
    assert transliterate("Отчёт: 31c.io, 2026") == "Otchet: 31c.io, 2026"


def test_a_ukrainian_letter_is_covered():
    """One table, not one per language.

    "Київ" comes out "kiyiv" rather than the Ukrainian-standard "Kyiv", because
    `и` maps the Russian way and nothing here knows which language a title is
    written in. Readable and stable beats standards-correct for a filename, and
    guessing the language from the characters would be a new failure mode.
    """
    assert transliterate("Київ").lower() == "kiyiv"
    assert transliterate("Ґанок").lower() == "ganok"


def test_a_decomposed_letter_is_normalised_before_lookup():
    """The same "ё" arrives two ways, and only one of them is a single character.

    A title pasted from a Mac, or out of some PDF text layers, carries `е` plus
    a combining diaeresis. Without normalising first, the combining mark is not
    in the table, survives the pass, and the caller's ASCII rules then turn it
    into a hyphen: `elka` becomes `e-lka`.
    """
    composed = "ёлка"
    decomposed = "ёлка"
    assert composed != decomposed
    assert transliterate(decomposed) == transliterate(composed) == "elka"


def test_a_script_with_no_entry_is_left_for_the_fallback():
    """The table is Cyrillic. Arabic and CJK are not silently mangled here."""
    assert transliterate("文書") == "文書"


# ============================================================
# The fallback that keeps two names apart
# ============================================================

def test_the_stable_suffix_is_deterministic():
    assert stable_suffix("文書") == stable_suffix("文書")


def test_two_different_titles_get_two_different_suffixes():
    assert stable_suffix("文書") != stable_suffix("報告")


def test_the_stable_suffix_is_filename_safe():
    suffix = stable_suffix("文書")
    assert suffix and all(c in "0123456789abcdef" for c in suffix)


# ============================================================
# render-doctype: the one that overwrote a rendered document
# ============================================================

def test_a_russian_recipient_no_longer_empties_the_filename(doctype):
    name = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "Ромашка",
         "SUBJECT": "Партнёрское предложение"}, "letter", "pdf")
    assert name == "2026-08-27_letter_romashka_partnerskoe-predlozhenie.pdf"


def test_two_russian_letters_on_one_day_do_not_collide(doctype):
    first = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "Ромашка",
         "SUBJECT": "Предложение"}, "letter", "pdf")
    second = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "Василёк",
         "SUBJECT": "Договор"}, "letter", "pdf")
    assert first != second, f"both letters render to {first}"


def test_an_english_filename_is_unchanged(doctype):
    """The regression that matters: existing outputs keep their names."""
    name = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "Acme Telecom",
         "SUBJECT": "Partnership offer"}, "letter", "pdf")
    assert name == "2026-08-27_letter_acme-telecom_partnership-offer.pdf"


def test_a_title_in_an_uncovered_script_still_names_a_unique_file(doctype):
    first = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "文書",
         "SUBJECT": "報告"}, "letter", "pdf")
    second = doctype.build_filename(
        {"DATE": "2026-08-27", "RECIPIENT_ORG": "報告",
         "SUBJECT": "文書"}, "letter", "pdf")
    assert "__" not in first, f"an empty slug survived: {first}"
    assert first != second


def test_the_filename_never_ends_up_with_an_empty_field(doctype):
    for payload, dt in (
        ({"DATE": "2026-08-27", "SUBJECT": "Договор"}, "official"),
        ({"DATE": "2026-08-27", "PRODUCT_NAME": "Обзор"}, "xpager"),
        ({"DATE": "2026-08-27", "PARTY_B_SHORT": "Ромашка",
          "SUBTYPE": "мou", "SUBJECT": "Соглашение"}, "partnership"),
    ):
        name = doctype.build_filename(payload, dt, "pdf")
        stem = name.rsplit(".", 1)[0]
        assert "" not in stem.split("_"), f"empty field in {name}"


# ============================================================
# marp: the deck stem
# ============================================================

def test_a_russian_deck_title_produces_a_slug():
    assert generate_slug("Стратегия развития") == "strategiya-razvitiya"


def test_two_russian_decks_do_not_share_one_stem():
    assert generate_slug("Стратегия") != generate_slug("Тактика")


def test_an_english_deck_title_is_unchanged():
    assert generate_slug("Q3 Strategy Review") == "q3-strategy-review"


def test_an_uncovered_script_deck_still_gets_a_stem():
    assert generate_slug("文書") != ""


# ============================================================
# threads: information loss on a mixed title
# ============================================================

def test_a_mixed_title_keeps_its_russian_words(tmp_path):
    """An id of `crm` was five words in and one word out.

    The stem transliterates letter by letter, so `Миграция` becomes `migratsiya`
    and not the English word it shares a root with. That is the intended
    behaviour: a filename reproduces the title, it never translates it.
    """
    path = new_thread_path(tmp_path, "business",
                           "Миграция CRM на новый сервер", "2026-08-15")
    assert path.name == "2026-08-15-migratsiya-crm-na-novyy-server.md"


def test_a_fully_russian_title_no_longer_raises(tmp_path):
    path = new_thread_path(tmp_path, "business", "Договор аренды", "2026-08-27")
    assert path.name == "2026-08-27-dogovor-arendy.md"


def test_an_ascii_thread_id_is_unchanged():
    assert thread_slug("ODUN.ONE x TrustONE") == "odun-one-x-trustone"


def test_a_title_that_still_slugifies_to_nothing_is_refused(tmp_path):
    """Threads fail safe rather than inventing a hash. A thread id is read by a
    person, and `2026-08-27-a3f19c02` names nothing to them."""
    with pytest.raises(ValueError, match="slugifies to empty"):
        new_thread_path(tmp_path, "business", "文書", "2026-08-27")
