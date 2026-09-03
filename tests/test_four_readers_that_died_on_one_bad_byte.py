#!/usr/bin/env python3
"""Four walkers caught OSError, and UnicodeDecodeError is a ValueError.

`UnicodeDecodeError` inherits from `ValueError`, not from `OSError`. So a
handler written as `except OSError: continue` around a `read_text` does not
skip a file that is not valid UTF-8. It does not catch anything at all: the
error raises straight out of the enclosing walk, and every readable file after
it, plus every readable file already collected, goes with the one bad one.

Found by an AST sweep of `scripts/` and `.claude/hooks/` on 2026-09-01. The
sweep started at 229 candidate try-blocks holding a decode call whose handlers
could not catch a decode error, narrowed to 162 once `json.loads` on a `str`
was excluded (a str is already decoded, so it can never raise), then to 7 once
the question became "does the enclosing function walk a directory of
OPERATOR-written files?" A file the engine itself wrote with `json.dump` is
UTF-8 by construction; a note, a card or a config the operator edits by hand is
not.

Of those 7, one was a false positive and withdrawn: `census_oracles._contacts`
catches a module constant `_UNREADABLE`, which resolves to
`(OSError, ValueError, YAMLError)` and therefore does catch it. The scanner
read handler NAMES and could not resolve a tuple constant. Two more could not
be reached with the signatures the sweep guessed and were measured separately.

MEASURED on a two-file corpus, one clean and one carrying a lone 0xe9:

    memory_expiry.find_expired          RAISED  ->  returns the clean file
    memory_health.scan_redundancy       RAISED  ->  returns, unreadable counted
    crm_autolog._build_email_index      RAISED  ->  returns the clean entities
    content_denylist._harvest_config    RAISED  ->  raises HarvestUnreadable

The fourth is deliberately still a raise, and the difference matters. The first
three are advisory readers, so dropping one unreadable file and logging its
path is the honest degradation. `_harvest_config` feeds the real-entity
denylist, the only layer of the engine leak wall that reads WHAT is inside a
file rather than where it routes. Measured the same day: a bad byte in the
second of three config files harvested the first file's token, abandoned the
third, and `push-all.py:450` then refused the push. That refusal is correct and
is not changed here. What was wrong is that `UnicodeDecodeError` carries a
codec, a byte and an offset and NO filename, so the operator was told to check
`config/content-denylist.yaml` while the file that actually broke was never
named on any stream. `HarvestUnreadable` names it.

Not fixed with `errors="replace"`, on purpose. These sources hold the real
names the wall matches on, so a byte replaced by U+FFFD would harvest a garbled
token that matches nothing and let the push proceed believing the wall was
armed. Failing closed with a named file is the safe direction.

Sibling fix the same day, from the same sweep, in a shard audit:
`scripts/utils/crm.py::contact_index_by_email`, where one bad card took 168
good ones with it.
"""
from __future__ import annotations

import ast
import datetime
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import content_denylist, crm_autolog, memory_expiry, memory_health  # noqa: E402

# A lone 0xe9. Valid latin-1, invalid UTF-8 as a standalone byte, and the exact
# shape a note pasted out of an older editor arrives in.
LONE_CONTINUATION = b"\xe9"

CLEAN_NOTE = "---\nname: clean-note\ndescription: a readable note\n---\n\nBody text.\n"
# A REAL non-ASCII note, valid UTF-8. The anchor against over-refusal: a fix
# that skipped every file with a byte above 0x7f would pass every test above
# and quietly drop half the corpus.
ACCENTED_NOTE = "---\nname: accented-note\ndescription: caf\u00e9 latt\u00e9\n---\n\nR\u00e9sum\u00e9.\n"


def _TWIN_VECTORS(texts):
    """A stub embedder, so these two tests measure the code and not the host.

    MEASURED 2026-09-03: with no `embedder=` argument, `scan_redundancy`
    resolves a real ollama host, and where one is PINNED and down it returns
    before reading a file. Both tests below were then decided by whether a
    daemon on this machine's Windows side happened to be up -- green by luck in
    a fresh worktree (no gitignored `config/ollama-hosts.yaml`, so no pin) and
    red in the operator's main clone (pin present, daemon stopped). Injecting
    the embedder is what makes their verdict a fact about the walk.

    That the walk now runs even when NO embedder can be resolved is a separate
    property, held by
    `tests/test_a_scan_that_stopped_reading_when_a_daemon_stopped_answering.py`.
    """
    return [[1.0, 0.0]] * len(texts)


def _corpus(tmp_path: Path) -> Path:
    """One clean file, one accented-but-valid file, one undecodable file.

    Written in an order that puts the bad file in the MIDDLE of the sorted
    walk, so a reader that aborts loses a file it had not reached yet as well
    as the one it was on. `find_expired` and `contact_index_by_email` both sort
    their glob, so alphabetical order is what decides this.
    """
    (tmp_path / "a-clean.md").write_text(CLEAN_NOTE, encoding="utf-8")
    (tmp_path / "b-broken.md").write_bytes(
        b"---\nname: broken\n---\n\nCaf" + LONE_CONTINUATION + b" note.\n")
    (tmp_path / "c-accented.md").write_text(ACCENTED_NOTE, encoding="utf-8")
    return tmp_path


def test_memory_expiry_returns_the_readable_files(tmp_path):
    """The walk survives, rather than raising out of the whole scan."""
    corpus = _corpus(tmp_path)
    result = memory_expiry.find_expired(corpus, datetime.date(2026, 9, 1))
    assert isinstance(result, list), (
        "find_expired raised on one undecodable note instead of skipping it")


def test_memory_expiry_names_the_file_it_dropped(tmp_path, caplog):
    """Skipping is right; silence is not.

    A fact file that drops out of this walk never expires, and without the log
    line nothing anywhere would say which one or why.
    """
    corpus = _corpus(tmp_path)
    with caplog.at_level(logging.WARNING, logger="scripts.utils.memory_expiry"):
        memory_expiry.find_expired(corpus, datetime.date(2026, 9, 1))
    assert "b-broken.md" in caplog.text, (
        f"the dropped file was not named in any warning: {caplog.text!r}")


def test_memory_expiry_still_reads_a_valid_accented_file(tmp_path):
    """Anchor against over-refusal. Valid UTF-8 above ASCII must still parse.

    Written with an expiry already in the past, so a reader that silently
    skipped it would return an empty list and be caught here.
    """
    (tmp_path / "expired.md").write_text(
        "---\nname: caf\u00e9-note\nexpires: 2020-01-01\n---\n\nR\u00e9sum\u00e9.\n",
        encoding="utf-8")
    (tmp_path / "bad.md").write_bytes(b"---\nname: x\n---\n\n" + LONE_CONTINUATION)
    names = [n for n, _ in memory_expiry.find_expired(tmp_path, datetime.date(2026, 9, 1))]
    assert "expired.md" in names, (
        "a valid UTF-8 file carrying accented characters was dropped, so the "
        "fix refuses more than the defect it was written for")


def test_memory_health_redundancy_scan_survives_and_counts(tmp_path):
    """Its own docstring promised degradation to ok=False; now it can deliver.

    The handler three lines above the fix already said an unreadable file is
    "dropped from the pair scan" and counted. That contract did not hold for
    the one input most likely to break a read.
    """
    corpus = _corpus(tmp_path)
    result = memory_health.scan_redundancy(corpus, embedder=_TWIN_VECTORS)
    assert isinstance(result, dict), (
        "scan_redundancy raised instead of degrading, which is what its own "
        "note about unreadable files says it does")
    # `"note" in result or "pairs" in result` stood here until 2026-09-03 and is
    # true on every return path this function has, including the one that never
    # opens a file. The name promises a COUNT, so the count is what is asserted.
    assert result["note"].endswith("1 unreadable file(s) skipped"), result["note"]


def test_memory_health_names_the_file_it_dropped(tmp_path, caplog):
    corpus = _corpus(tmp_path)
    with caplog.at_level(logging.WARNING, logger="scripts.utils.memory_health"):
        memory_health.scan_redundancy(corpus, embedder=_TWIN_VECTORS)
    assert "b-broken.md" in caplog.text, (
        f"the dropped file was not named in any warning: {caplog.text!r}")


def test_crm_autolog_email_index_keeps_the_readable_entities(tmp_path):
    """One bad entity must not empty the address book.

    This index answers "which contact owns this address". A raise here is not
    a missing entry, it is no index at all, and the inbox classifier's sender
    scoring reads it.
    """
    (tmp_path / "a-clean.md").write_text(
        "---\nname: Marlow Carter\ncanonical_email: marlow@example.invalid\n---\n\nNotes.\n",
        encoding="utf-8")
    (tmp_path / "b-broken.md").write_bytes(
        b"---\nname: Caf" + LONE_CONTINUATION + b"\ncanonical_email: x@example.invalid\n---\n")
    index = crm_autolog._build_email_index(tmp_path)
    assert isinstance(index, dict), (
        "_build_email_index raised on one undecodable entity file")
    assert "marlow@example.invalid" in index, (
        "the clean entity was lost along with the broken one")


def test_crm_autolog_names_the_entity_it_dropped(tmp_path, caplog):
    (tmp_path / "b-broken.md").write_bytes(
        b"---\nname: Caf" + LONE_CONTINUATION + b"\n---\n")
    with caplog.at_level(logging.WARNING, logger="scripts.utils.crm_autolog"):
        crm_autolog._build_email_index(tmp_path)
    assert "b-broken.md" in caplog.text, (
        f"the dropped entity was not named in any warning: {caplog.text!r}")


def test_the_leak_wall_still_fails_closed_on_an_undecodable_config(tmp_path):
    """The refusal is the point. This test exists so nobody "fixes" it away.

    A later reader may see the three skip-and-log fixes above and make this one
    match. It must not: a denylist that quietly harvests fewer tokens lets a
    real name reach a public repo, which is the exact leak class this module
    was written to close.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "b-broken.yaml").write_bytes(
        b"owner: bob.jones@example.invalid\n# caf" + LONE_CONTINUATION + b"\n")
    with pytest.raises(content_denylist.HarvestUnreadable):
        content_denylist._harvest_config(tmp_path, {}, False)


def test_the_leak_wall_refusal_names_the_file(tmp_path):
    """The whole reason HarvestUnreadable exists.

    A bare UnicodeDecodeError says "invalid continuation byte in position 38"
    and names no file. `build_denylist` prints only the exception, so the
    operator got a byte offset and a wrong suggestion.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "b-broken.yaml").write_bytes(b"owner: x@example.invalid\n# caf" + LONE_CONTINUATION)
    with pytest.raises(content_denylist.HarvestUnreadable) as caught:
        content_denylist._harvest_config(tmp_path, {}, False)
    assert "b-broken.yaml" in str(caught.value), (
        f"the refusal did not name the file that broke: {caught.value}")


def test_harvest_unreadable_is_a_value_error():
    """So `build_denylist`'s existing `except Exception` still degrades.

    Subclassing ValueError also keeps it caught by any caller that was already
    written to expect a decode failure.
    """
    assert issubclass(content_denylist.HarvestUnreadable, ValueError)


def test_the_leak_wall_reads_a_valid_accented_config(tmp_path):
    """Anchor against over-refusal on the gate that can wedge a push.

    A fix that refused every config file with a byte above ASCII would pass
    every test above and stop the operator pushing at all.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "a.yaml").write_text(
        "owner: alice.smith@example.invalid\nnote: caf\u00e9\n", encoding="utf-8")
    tokens: dict[str, str] = {}
    content_denylist._harvest_config(tmp_path, tokens, False)
    assert "alice.smith@example.invalid" in tokens, (
        "a valid UTF-8 config carrying accented characters was refused")


# ---------------------------------------------------------------------------
# The structural half: pin the handlers, so the fix cannot be reverted quietly.
# ---------------------------------------------------------------------------

FIXED_HANDLERS = [
    ("scripts/utils/memory_expiry.py", "find_expired"),
    ("scripts/utils/memory_health.py", "scan_redundancy"),
    ("scripts/utils/crm_autolog.py", "_build_email_index"),
]


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer defined in {path}")


@pytest.mark.parametrize("rel,func", FIXED_HANDLERS)
def test_the_reader_can_still_catch_a_decode_error(rel, func):
    """Asked of the AST, not of a grep.

    A grep for "UnicodeDecodeError" is satisfied by the word appearing in the
    comment that explains the fix, which would survive the handler itself being
    narrowed back to OSError.
    """
    node = _function(ROOT / rel, func)
    caught = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Try):
            continue
        for handler in sub.handlers:
            if handler.type is None:
                caught.add("<bare>")
                continue
            parts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            for p in parts:
                if isinstance(p, ast.Name):
                    caught.add(p.id)
                elif isinstance(p, ast.Attribute):
                    caught.add(p.attr)
    assert caught & {"UnicodeDecodeError", "ValueError", "UnicodeError"}, (
        f"{rel}::{func} catches {sorted(caught)}. None of those catch a "
        f"UnicodeDecodeError, which is a ValueError, so one file that is not "
        f"valid UTF-8 raises out of the whole walk again.")


def test_the_denylist_harvest_reads_through_the_naming_helper():
    """Every strict read in the harvest must name its file on failure.

    Checked by counting bare `read_text(encoding="utf-8")` calls in the module.
    Exactly two are legitimate: the one inside `_read_source` itself, and the
    `errors="replace"` read in `_harvest_contact_frontmatter`, which scans note
    bodies for ASCII e-mail addresses and therefore cannot hide one.
    """
    src = (ROOT / "scripts/utils/content_denylist.py").read_text(encoding="utf-8")
    strict = src.count('read_text(encoding="utf-8")')
    assert strict == 1, (
        f"{strict} strict read_text call(s) in content_denylist.py; expected "
        f"exactly the one inside _read_source. A new source read that bypasses "
        f"the helper raises a UnicodeDecodeError that names no file, and the "
        f"push then refuses while pointing the operator at the wrong one.")
