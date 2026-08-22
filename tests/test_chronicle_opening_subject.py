"""The gist must account for the subject the conversation OPENED on.

Measured 2026-08-22 on one real session. Its opening subject — an investor the
operator asked about in the very first turn — was named 21 times in the body and
was the single most-mentioned thing in it. Both gemma3:4b and gemma3:12b led
their gist with the SECOND subject, a broken Ollama instance, and never named the
first at all. Neither run was truncated: 24,035 chars against a 120,000 budget.

(The real name stays out of this repo, per the engine's no-real-data rule. The
fixtures below use a placeholder. The pre-commit content guard caught the first
draft of this file, which did not.)

The failure is not parameter count. It is that a session here routinely holds
several unrelated subjects, and "what this conversation was about" invites a
model to answer with whichever one ran longest. The opening subject is usually
the one the operator came for.

So the opening turn is EXTRACTED here and stated to the model as a fact, in the
target language, rather than left to its judgement — the same shape that fixed
the language defect the same day, and for the same reason: a 4B model obeys a
short instruction at the end far better than a paragraph in the middle.
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
        "chronicle_open_mod", WORKSPACE / "scripts" / "chronicle.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["chronicle_open_mod"] = module
    spec.loader.exec_module(module)
    return module


# --- extracting the opening turn --------------------------------------------

def test_the_first_user_turn_is_extracted(chronicle):
    body = ("USER: у нас были взаимоотношения с example capital?\n"
            "ASSISTANT: да, живой контакт\n"
            "USER: теперь почини ollama на windows\n")
    assert "example capital" in chronicle.opening_subject(body).lower()


def test_an_assistant_turn_is_never_taken_as_the_opening(chronicle):
    """The operator's question is the subject; the answer is not."""
    body = ("ASSISTANT: продолжаю с сохранённого хендоффа\n"
            "USER: почини ollama на windows\n")
    opening = chronicle.opening_subject(body)
    assert "ollama" in opening.lower()
    assert "хендоффа" not in opening


def test_a_body_with_no_user_turn_yields_nothing(chronicle):
    assert chronicle.opening_subject("ASSISTANT: only me here") == ""


def test_an_empty_body_yields_nothing(chronicle):
    assert chronicle.opening_subject("") == ""


def test_a_long_opening_turn_is_trimmed(chronicle):
    body = "USER: " + ("очень длинный вопрос " * 200) + "\nASSISTANT: ок"
    opening = chronicle.opening_subject(body)
    assert 0 < len(opening) <= chronicle.OPENING_CHARS


# --- the directive it produces ----------------------------------------------

def test_the_directive_quotes_the_opening_subject(chronicle):
    d = chronicle.opening_directive("example capital", "ru")
    assert "example capital" in d


def test_the_directive_is_written_in_the_target_language(chronicle):
    """Same lesson as the language directive: for a 4B model this IS the example."""
    ru = chronicle.opening_directive("что там с example capital", "ru")
    cyrillic = sum(1 for c in ru.lower() if "а" <= c <= "я" or c == "ё")
    assert cyrillic > 20, f"a Russian directive with no Russian in it: {ru!r}"

    en = chronicle.opening_directive("what about example capital", "en")
    assert sum(1 for c in en.lower() if "а" <= c <= "я" or c == "ё") == 0


def test_no_opening_means_no_directive(chronicle):
    assert chronicle.opening_directive("", "ru") == ""


# --- how it composes into the prompt ----------------------------------------

def test_the_prompt_carries_the_opening_directive(chronicle):
    body = ("USER: у нас были взаимоотношения с example capital?\n"
            "ASSISTANT: да\n"
            "USER: почини ollama\n")
    assert "example capital" in chronicle.build_prompt(body).lower()


def test_the_language_directive_is_still_the_very_last_thing(chronicle):
    """Regression. The language fix depends on position; do not displace it."""
    body = ("USER: у нас были взаимоотношения с example capital?\n"
            "ASSISTANT: да, живой контакт, встреча вчера\n")
    prompt = chronicle.build_prompt(body)
    assert prompt.rstrip().endswith(chronicle.language_directive("ru")), (
        "the opening directive displaced the language directive from the end"
    )


def test_a_body_with_no_user_turn_still_builds_a_prompt(chronicle):
    prompt = chronicle.build_prompt("ASSISTANT: only me here")
    assert "only me here" in prompt


# --- the standing instruction in PROMPT -------------------------------------

def test_the_prompt_asks_for_every_subject_in_order_of_first_appearance(chronicle):
    """The gist field must not license picking the longest stretch."""
    text = chronicle.PROMPT.lower()
    assert "first appear" in text or "order they first" in text
    assert "longest" in text
