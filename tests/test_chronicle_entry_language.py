"""A Chronicle entry is stored in the language it was spoken in. Never translated.

Operator directive, 2026-08-22: "не делай переводы, пусть будет как в реале. если
я писал на русском, храни на русском, если ты писал мне на русском, храни на
русском. Если мы общались на английском, храни на английском, если был микс
языков, храни в миксе."

A first cut of this rule asked for English prose with quotations left in their
own language, and the operator replaced it the same hour with the simpler and
stronger form above: no translation anywhere, in either direction. A translated
line stops being evidence of what was said, and which language something was said
in is itself part of the record.

The prompt carries the rule to the model. These tests hold the mechanical half:
the render path must not mangle Cyrillic on its way to disk, and the rule must
actually be present in the prompt the model receives — a rule the model never
sees is a rule that does not exist.

The section headings stay English on purpose, and they are the one deliberate
exception. They are the entry's structure rather than anything anyone said, and a
fixed set keeps every entry greppable by one string regardless of the language
inside it.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]

# Rendered into the entry as the `Full transcript:` pointer and never opened, so
# no file needs to exist. Deliberately not under /tmp: a literal temp path in a
# test reads to ruff (S108) as a real insecure write.
SESSION_PATH = "sessions/c9bbd8dc.jsonl"


@pytest.fixture(scope="module")
def chronicle():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location(
        "chronicle_lang_mod", WORKSPACE / "scripts" / "chronicle.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["chronicle_lang_mod"] = module
    spec.loader.exec_module(module)
    return module


def _summary(**over):
    base = {
        "gist": "The operator raised the transcript retention window.",
        "reasoning": 'They asked for it in these words: "срок побольше и в бэкап".',
        "considered": ['Leaving the 30-day default - rejected, "кровь течёт прямо '
                       'сейчас" was the operator\'s reading of the loss rate.'],
        "open": ["Whether the archive should move to zstd if the repo grows."],
        "topics": ["transcripts", "retention"],
        "personal": False,
    }
    base.update(over)
    return base


def test_a_fully_russian_entry_renders_untouched(chronicle):
    """The whole record in Russian, because the session was. No English crept in."""
    summary = _summary(
        gist="Подняли окно хранения транскриптов с 30 до 365 дней.",
        reasoning="Мера была срочной: 177 записей из 258 уже указывали в пустоту.",
        considered=["Оставить 30 дней - отклонено, потери шли каждый день."],
        open=["Перейти ли на zstd, если репозиторий раздуется."],
        topics=["транскрипты", "хранение"],
    )
    entry = chronicle.render_entry("sid", "2026-08-22", SESSION_PATH, summary)
    assert "Подняли окно хранения транскриптов с 30 до 365 дней." in entry
    assert "177 записей из 258 уже указывали в пустоту" in entry
    assert "Оставить 30 дней" in entry
    assert "транскрипты" in entry, "a Russian topic tag was lost in the frontmatter"


def test_the_entry_furniture_is_english(chronicle):
    entry = chronicle.render_entry("sid", "2026-08-22", SESSION_PATH, _summary())
    assert "## How this was reached" in entry
    assert "## Considered and dropped" in entry
    assert "## Left open" in entry


def test_russian_inside_a_quotation_survives_verbatim(chronicle):
    """The render path must not transliterate, strip, or escape Cyrillic."""
    entry = chronicle.render_entry("sid", "2026-08-22", SESSION_PATH, _summary())
    assert "срок побольше и в бэкап" in entry
    assert "кровь течёт прямо сейчас" in entry
    assert "ё" in entry, "a Cyrillic ё was lost or normalised away"


def test_russian_survives_a_round_trip_to_disk(chronicle, tmp_path):
    """Write and read back: an encoding slip shows up here, not months later."""
    entry = chronicle.render_entry("sid", "2026-08-22", SESSION_PATH, _summary())
    path = tmp_path / "entry.md"
    chronicle.write_entry(path, entry)
    assert "срок побольше и в бэкап" in path.read_text(encoding="utf-8")


def test_the_prompt_forbids_translation_outright(chronicle):
    """A rule the model never sees is a rule that does not exist."""
    lowered = chronicle.PROMPT.lower()
    assert "never translate" in lowered, "the prompt does not forbid translation"
    assert "word for word" in lowered, "the prompt does not pin quotations"


def test_the_prompt_names_every_case_the_operator_named(chronicle):
    """Russian, English, and the mix. The mix is the one a shorter rule loses."""
    lowered = chronicle.PROMPT.lower()
    assert "russian" in lowered and "english" in lowered
    assert "mix" in lowered, (
        "the prompt does not say what to do with a bilingual session, which is "
        "the common shape here and the case a 'pick one language' rule destroys"
    )


def test_the_prompt_covers_the_list_fields_too(chronicle):
    """gist alone is not the record; considered/open carry the reasoning."""
    lowered = chronicle.PROMPT.lower()
    assert "every field" in lowered or "considered" in lowered


def test_an_entry_with_no_reasoning_grows_no_empty_headings(chronicle):
    """A session that decided nothing must not render three bare headings."""
    entry = chronicle.render_entry(
        "sid", "2026-08-22", SESSION_PATH,
        _summary(reasoning="", considered=[], open=[]),
    )
    assert "## How this was reached" not in entry
    assert "## Considered and dropped" not in entry
    assert "## Left open" not in entry
