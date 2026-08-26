#!/usr/bin/env python3
"""A Russian question finds an English commit. The claim, tested rather than asserted.

The design spec for the commit and symbol layers picked `bge-m3` over an
English-only code embedder for exactly one reason: Misha asks in Russian and the
corpus is written in English. `docs/superpowers/specs/2026-08-21-semantic-index-
commits-and-symbols-design.md` § Testing names this the claim that justifies the
choice, and says it "must be tested, not asserted". Until this file there was no
such test -- the 85% Set A score is measured over a set that MIXES languages, so
it could have been carried entirely by the English half.

This is the first user of the `requires_ollama` marker. It needs a live embedder
because the property under test lives in the model's weights, not in our code.
Stubbing the embedder here would test nothing at all. Where the embedder is
absent -- CI, a fresh clone -- the test SKIPS rather than fails, because absence
of Ollama is not evidence about the model.

Method: real English commit subjects from this repository, one Russian question
aimed at one of them, and the rest standing as distractors. The assertion is
comparative, never absolute: the target must beat every distractor. An absolute
cosine floor would encode today's model version as a contract.

Run: .venv/bin/python -m pytest tests/test_recall_cross_lingual.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.embeddings import EmbeddingError, embed, index_embed_target  # noqa: E402

pytestmark = pytest.mark.requires_ollama

MODEL = "bge-m3"

# Real subjects from this repository's history. Deliberately close in topic --
# all of them are about gates, memory and sends -- so a win is not the model
# separating "software" from "cooking".
CORPUS = [
    "fix: re-arm git-lfs on the engine push gate, stamp the data gate, ship commands",
    "feat(checkpoint): the compaction threshold is a per-session switch",
    "gate: a data overlay's own tests now block its push",
    "feat(recall): commit messages become searchable by meaning, not by substring",
    "test: the built-frontmatter guard covers every bundle, not only heading-core",
    "docs+rules: the stores this workspace keeps, and eight paths the prose outlived",
]

# (Russian question, index into CORPUS it should retrieve)
CASES = [
    ("почему коммиты стали искаться по смыслу", 3),
    ("что сделали с порогом сжатия сессии", 1),
    ("какие тесты теперь блокируют пуш данных", 2),
]


def _cos(a, b) -> float:
    import math
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


@pytest.fixture(scope="module")
def host() -> str:
    """The embedder this workspace ACTUALLY uses, resolved the one way it is.

    This used to be `resolve_ollama_host("auto:11436", ...)`: a literal port,
    and the DEGRADING resolver, which falls back to `http://localhost:11434`.
    Neither matches how anything else here embeds. `config/ollama-hosts.yaml`
    lists `auto:11434` first on this machine and the Windows daemon answers
    there, so the pin was live, the hardcoded 11436 probe failed, the resolver
    degraded to a WSL daemon that does not exist, and all four tests in this
    file SKIPPED. Measured 2026-08-26: the claim these tests exist to falsify
    had never once been measured on the only machine that can measure it.

    `index_embed_target` is the single reader the builder and every other embed
    caller already share, added on 2026-08-22 to end exactly this class of
    private-copy drift. This file was the caller that got missed.
    """
    try:
        target, _model = index_embed_target()
    except EmbeddingError as exc:
        pytest.skip(f"no embedder reachable, so the model claim cannot be tested: {exc}")
    return target


@pytest.fixture(scope="module")
def vectors(host):
    """Embed the corpus once, or skip the module when no embedder answers."""
    try:
        return embed(CORPUS, model=MODEL, host=host)
    except EmbeddingError as exc:
        pytest.skip(f"no embedder reachable, so the model claim cannot be tested: {exc}")


@pytest.mark.parametrize("question,target", CASES)
def test_a_russian_question_ranks_the_right_english_commit_first(question, target, host,
                                                                 vectors):
    """The justification for bge-m3, stated as a comparison the model must win."""
    qv = embed([question], model=MODEL, host=host)[0]
    scores = [_cos(qv, v) for v in vectors]
    best = max(range(len(scores)), key=scores.__getitem__)
    assert best == target, (
        f"RU question {question!r} ranked {CORPUS[best]!r} above the intended "
        f"{CORPUS[target]!r}\n"
        + "\n".join(f"  {s:.4f}  {c}" for s, c in sorted(zip(scores, CORPUS, strict=True), reverse=True))
    )


def test_the_same_question_in_english_agrees_with_the_russian_one(host, vectors):
    """Guards the interesting failure: a model that is merely CONSISTENTLY wrong.

    If the Russian and English forms of one question disagree, the win above was
    luck rather than cross-lingual alignment.
    """
    ru = embed(["почему коммиты стали искаться по смыслу"], model=MODEL, host=host)[0]
    en = embed(["why did commits become searchable by meaning"], model=MODEL, host=host)[0]
    pick = lambda v: max(range(len(vectors)), key=lambda i: _cos(v, vectors[i]))  # noqa: E731
    assert pick(ru) == pick(en) == 3
