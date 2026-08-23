"""One place names the embedding model, and one place resolves the embedder.

Three copies of `bge-m3` accumulated in this tree, each written by someone who
had no reason to think there was another:

  scripts/memory-index.py       cfg.setdefault("model", "bge-m3")   the index
  scripts/utils/memory_health.py  embed(..., model="bge-m3", ...)   the hygiene scan
  scripts/chronicle.py          EMBED_MODEL = "bge-m3"              personal recall

None of the three was WRONG. Each embeds a corpus and compares those vectors
only among themselves, so a divergent model does not produce a bad answer - it
produces a second model resident in ollama beside the first, and silence. The
host copies were worse: `memory_health` named `http://localhost:11434` outright
and `chronicle` read only an environment variable nobody sets, so both ran on the
WSL CPU while the index ran on the Windows iGPU. Measured 2026-08-22 on the real
auto-memory corpus: 267s on the CPU path against 87s on the accelerated one.

This file is the guard, written before the third copy was swept, so that a fourth
cannot be added quietly. The single source is
`scripts/utils/embeddings.index_embed_target()`, which reads
`config/memory-index.yaml` once and answers with both halves.

The detector is AST-based, not a grep, because `bge-m3` appears legitimately
dozens of times in prose and docstrings that explain the model. Only two shapes
are a defect: assigning the literal to a model-ish name, and passing it as a
`model=` keyword.
"""
from __future__ import annotations

import ast
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent

# Model tags this workspace embeds with. A new embedder is added here, not
# scattered - the point of the file.
EMBED_MODEL_TAGS = {"bge-m3", "bge-m3:latest"}

# The one place allowed to name a model literal: the single source of truth and
# its own fallback for a missing config.
#
# `scripts/memory-index.py` is deliberately NOT here. It writes the default as
# `cfg.setdefault("model", "bge-m3")`, which is neither shape the detector looks
# for, so it needs no allowance - and if that file ever passes a literal to
# `embed()` instead of `cfg["model"]`, being absent from this set is what catches
# it. The two defaults are pinned equal by tests/test_memory_health_redundancy.py.
ALLOWED = {
    "scripts/utils/embeddings.py",
}


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _target_name(target: ast.AST) -> str:
    """The written name of an assignment target, whatever its node shape.

    `getattr(target, "id", "")` alone saw only bare `ast.Name`. It returned ""
    for `Config.MODEL = "bge-m3"` and `self.MODEL = "bge-m3"`, which are
    `ast.Attribute` with no `.id` -- so a fourth model copy in either shape kept
    this guard green, which is exactly what the guard exists to stop.
    """
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _offending_nodes(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, why) for every model literal used as configuration, not as prose."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # AnnAssign as well as Assign: `EMBED_MODEL: str = "bge-m3"` is an
        # AnnAssign and slipped through entirely.
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if not (isinstance(node.value, ast.Constant)
                    and node.value.value in EMBED_MODEL_TAGS):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = _target_name(target)
                if "MODEL" in name.upper():
                    found.append((node.lineno, f"{name} = {node.value.value!r}"))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if (kw.arg == "model" and isinstance(kw.value, ast.Constant)
                        and kw.value.value in EMBED_MODEL_TAGS):
                    found.append((kw.value.lineno, f"model={kw.value.value!r}"))
    return found


def test_only_the_single_source_names_an_embedding_model():
    """A fourth copy fails here, with the file and the line that added it."""
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(ROOT.glob("scripts/**/*.py")):
        rel = _rel(path)
        if rel in ALLOWED:
            continue
        try:
            # A stray `\<` in some unrelated regex is that file's business, not
            # a reason for this guard to add a warning to every suite run.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # not ours to judge here; py_compile owns that
            continue
        hits = _offending_nodes(tree)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "these name an embedding model instead of asking "
        "scripts/utils/embeddings.index_embed_target():\n"
        + "\n".join(
            f"  {rel}:{line}  {why}"
            for rel, hits in sorted(offenders.items())
            for line, why in hits
        )
    )


def test_the_allow_list_is_not_vacuous():
    """A guard whose allow-list has drifted to cover everything passes everything.

    Every entry must still hold a literal; if one stops, it was moved and the
    entry should go, not linger as a permanent hole.
    """
    for rel in sorted(ALLOWED):
        path = ROOT / rel
        assert path.is_file(), f"{rel} is in ALLOWED but does not exist"
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        assert _offending_nodes(tree), (
            f"{rel} no longer names a model literal - remove it from ALLOWED "
            "rather than leaving a hole the detector cannot see"
        )


def test_the_detector_sees_a_planted_copy(tmp_path):
    """The detector itself, pinned. A pattern that matches nothing passes
    everything, which is how a guard rots without anyone noticing."""
    planted = ast.parse('EMBED_MODEL = "bge-m3"\nembed(x, model="bge-m3")\n')
    assert len(_offending_nodes(planted)) == 2


def test_prose_and_docstrings_are_not_a_defect():
    """`bge-m3` appears dozens of times explaining what the embedder is. Flagging
    those would make the guard noise, and a noisy guard gets an allow-list until
    it means nothing."""
    prose = ast.parse(
        '"""We embed with bge-m3, 1024-dim."""\n'
        'NOTE = "bge-m3 is multilingual"\n'
        'summarizer_model = "gemma3:4b"\n'
    )
    assert _offending_nodes(prose) == []
