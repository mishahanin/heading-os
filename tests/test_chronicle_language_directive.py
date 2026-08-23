"""The language of the record is measured, not left to the model's judgement.

The prompt asked, in English prose, for the entry to be written in whatever
language the conversation used. Tested 2026-08-22 against this workspace's own
session — 23,773 characters of body, 56% of the letters Cyrillic — gemma3:4b
returned an entry written entirely in English. A 4B model defaults to English
whatever a paragraph in the middle of its prompt says.

So the share is computed here and the directive is stated as a fact the model is
told, not a decision it makes: the last line of the prompt, short, and written IN
the target language. Small models follow a short instruction at the end of a
prompt far more reliably than a paragraph in the middle, and one written in the
target language doubles as an example of it.

This is the same shape as every other guard here: measure the thing, then act on
the measurement, rather than asking a model to be disciplined.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def chronicle():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location(
        "chronicle_dir_mod", WORKSPACE / "scripts" / "chronicle.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["chronicle_dir_mod"] = module
    spec.loader.exec_module(module)
    return module


def test_a_russian_body_is_measured_as_russian(chronicle):
    body = "USER: подними окно хранения транскриптов\nASSISTANT: поднял до 365 дней"
    assert chronicle.dominant_language(body) == "ru"


def test_an_english_body_is_measured_as_english(chronicle):
    body = "USER: raise the retention window\nASSISTANT: raised it to 365 days"
    assert chronicle.dominant_language(body) == "en"


def test_a_genuinely_mixed_body_is_measured_as_mixed(chronicle):
    """Half and half is the common shape here, and the case a two-way test loses."""
    body = ("USER: подними окно хранения транскриптов до года, это важно\n"
            "ASSISTANT: raised cleanupPeriodDays to 365 and archived the files\n"
            "USER: а что с этим делает архиватор, покажи цифры\n"
            "ASSISTANT: gzip gives 3.8x, zstd 6.3x on the same transcript file")
    assert chronicle.dominant_language(body) == "mixed"


def test_code_and_paths_do_not_make_a_russian_session_english(chronicle):
    """Every session here quotes filenames and commands. Latin noise is not prose."""
    body = ("USER: почини пересборку индексов, она тормозит\n"
            "ASSISTANT: правлю scripts/memory-index.py и config/memory-index.yaml, "
            "запускаю .venv/bin/python -m pytest tests/ -q -n auto\n"
            "USER: покажи замер, сколько вышло по времени\n"
            "ASSISTANT: холодный запрос 7.00 с, тёплый 0.87 с, разница на загрузке")
    assert chronicle.dominant_language(body) == "ru"


def test_an_empty_body_does_not_crash(chronicle):
    assert chronicle.dominant_language("") in ("en", "mixed", "ru")


def test_the_directive_for_a_russian_session_is_written_in_russian(chronicle):
    """Written in the target language: for a small model that IS the example."""
    directive = chronicle.language_directive("ru")
    cyrillic = sum(1 for c in directive.lower() if "а" <= c <= "я" or c == "ё")
    assert cyrillic > 20, f"a Russian directive with no Russian in it: {directive!r}"


def test_the_directive_for_an_english_session_is_english(chronicle):
    directive = chronicle.language_directive("en")
    assert "English" in directive
    cyrillic = sum(1 for c in directive.lower() if "а" <= c <= "я" or c == "ё")
    assert cyrillic == 0


def test_the_mixed_directive_asks_for_both_and_forbids_picking_one(chronicle):
    directive = chronicle.language_directive("mixed").lower()
    assert "mix" in directive or "микс" in directive
    assert "translat" in directive or "перевод" in directive


def test_the_directive_lands_at_the_very_end_of_the_prompt(chronicle):
    """Position is the whole point: a 4B model obeyed nothing in the middle."""
    body = "USER: подними окно хранения\nASSISTANT: поднял до 365 дней"
    prompt = chronicle.build_prompt(body)
    tail = prompt[-len(chronicle.language_directive("ru")) - 5:]
    assert chronicle.language_directive("ru") in tail, (
        "the directive is not the last thing the model reads"
    )


def test_the_prompt_still_carries_the_body(chronicle):
    body = "USER: подними окно хранения\nASSISTANT: поднял до 365 дней"
    assert "подними окно хранения" in chronicle.build_prompt(body)


def test_summarize_sends_the_directive(chronicle, monkeypatch):
    """End to end through the real call path, not just the builder."""
    import json

    sent = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"response": '{"gist":"г","topics":["т"],'
                                           '"class":"business"}'}).encode()

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return FakeResponse()

    # The endpoint is resolved lazily since 2026-08-23 and its probe would go
    # through the very `urlopen` this test replaces, so pin it instead of
    # letting a fake summarizer response answer a version probe.
    monkeypatch.setattr(chronicle, "ollama_url", lambda: "http://pinned.test:11434/api/generate")
    monkeypatch.setattr(chronicle.urllib.request, "urlopen", fake_urlopen)
    chronicle.summarize("USER: подними окно хранения транскриптов до года")

    assert chronicle.language_directive("ru") in sent["prompt"]
