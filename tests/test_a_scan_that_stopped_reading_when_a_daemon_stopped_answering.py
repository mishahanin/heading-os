#!/usr/bin/env python3
"""An outage on one machine silenced a corruption signal on another.

`memory_health.scan_redundancy` does two unrelated jobs. It embeds the memory
corpus to propose near-duplicate merges, and on the way it reports any file it
could not READ -- a corrupt fact file that will never expire, never be indexed,
and never be merged, named nowhere else in the engine.

Until 2026-09-03 it resolved the embedder FIRST. `index_embed_target()` probes
when a host is pinned, and a pin REFUSES rather than degrading (operator
directive, 2026-08-23), so on a machine whose pinned ollama is down the function
returned before opening a single file. The corruption signal was suppressed by
an outage it has nothing to do with.

HOW IT SURFACED, and the shape is worth keeping. The operator's HELM clone went
red on `test_memory_health_names_the_file_it_dropped` while this worktree ran
the same commit green. The difference was not the code and not the merge:
`config/ollama-hosts.yaml` is GITIGNORED and machine-local, so HELM carries the
pin and a fresh worktree does not. Same tree, same interpreter, opposite
verdicts, decided by an untracked file and by whether a daemon on the Windows
side happened to be up.

MEASURED 2026-09-03 in this worktree, on the three-file corpus below with
`HEADING_OS_OLLAMA_EMBED_HOST=auto:11434,11436` and nothing answering there:

    before  scan_redundancy -> {'ok': False, 'note': 'embedder unavailable: ...'}
            caplog.text == ''            the undecodable file named nowhere
    after   caplog.text names b-broken.md, and the note carries the count

THE OTHER HALF, and it is the reason this file exists rather than a one-line
edit. The test that caught it was itself defective: it called the real embedder
and so its verdict was decided by whether a foreign daemon was running. It was
green by luck. Every assertion here injects an embedder, so nothing below can
reach the network, and `test_the_scan_never_reaches_the_network` holds that
property for the whole file rather than trusting each test to remember.

Run: python3 -m pytest tests/test_a_scan_that_stopped_reading_when_a_daemon_stopped_answering.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import memory_health  # noqa: E402

LONE_CONTINUATION = b"\xe9"


class EmbedderRefused(RuntimeError):
    """Stands in for `EmbeddingError` without importing the module that probes."""


def _corpus(tmp_path: Path) -> Path:
    """One clean note, one accented-but-valid note, one undecodable note.

    Alphabetical order puts the bad file in the MIDDLE, so a reader that aborts
    loses a file it had not reached yet as well as the one it was on.
    """
    (tmp_path / "a-clean.md").write_text(
        "---\nname: a-clean\ndescription: a clean note\n---\n\nBody.\n",
        encoding="utf-8")
    (tmp_path / "b-broken.md").write_bytes(
        b"---\nname: broken\n---\n\nCaf" + LONE_CONTINUATION + b" note.\n")
    (tmp_path / "c-accented.md").write_text(
        "---\nname: c-accented\ndescription: café latté\n---\n\n"
        "Résumé.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to resolve a real embedding host fails loudly, not slowly.

    The defect this file is about was found because a test silently reached a
    daemon. A test that silently reaches one to PROVE it does not is the same
    mistake, so the resolver is replaced rather than trusted to be unreachable.
    """
    from scripts.utils import embeddings

    def refuse(*a, **k):
        raise AssertionError(
            "scan_redundancy tried to resolve a real embedding host; every "
            "test in this file must inject its own embedder")

    monkeypatch.setattr(embeddings, "index_embed_target", refuse)
    monkeypatch.setattr(embeddings, "embed", refuse)


# ============================================================
# The corpus is read before the embedder is resolved
# ============================================================

def test_a_pinned_host_that_is_down_still_names_the_unreadable_file(
        tmp_path, caplog, monkeypatch):
    """The failing half. It failed against the version before this change.

    Reproduces HELM exactly and without a network: the resolver raises the way
    a dead pin makes it raise, and the question is whether the corrupt file is
    named anyway.
    """
    from scripts.utils import embeddings

    def dead_pin(*a, **k):
        raise EmbedderRefused(
            "no pinned ollama host answered: http://172.30.48.1:11434, "
            "http://172.30.48.1:11436")

    monkeypatch.setattr(embeddings, "index_embed_target", dead_pin)

    with caplog.at_level(logging.WARNING, logger="scripts.utils.memory_health"):
        result = memory_health.scan_redundancy(_corpus(tmp_path))

    assert "b-broken.md" in caplog.text, (
        "the pinned embedding host being down suppressed the warning about an "
        f"unreadable memory file: {caplog.text!r}")
    assert result["ok"] is False
    assert "embedder unavailable" in result["note"], result["note"]
    assert "1 unreadable file(s) skipped" in result["note"], (
        "the returned note reports the outage but not the corrupt file, so a "
        f"caller that logs only the note still learns nothing: {result['note']}")


def test_an_embedder_that_raises_mid_call_also_names_it(tmp_path, caplog,
                                                        no_network):
    """The second failure mode, which was already after the walk.

    Kept as the anchor: if a later edit moves the walk back down, this one stays
    green while its sibling above goes red, and the pair says exactly which of
    the two orders broke.
    """
    def falls_over(texts):
        raise EmbedderRefused("connection refused")

    with caplog.at_level(logging.WARNING, logger="scripts.utils.memory_health"):
        result = memory_health.scan_redundancy(_corpus(tmp_path),
                                               embedder=falls_over)

    assert "b-broken.md" in caplog.text, caplog.text
    assert "1 unreadable file(s) skipped" in result["note"], result["note"]


def test_the_walk_runs_before_the_resolver_is_touched(tmp_path, monkeypatch):
    """The order itself, asserted rather than inferred from a consequence.

    Both assertions above could be satisfied by a version that resolved first
    and merely logged more afterwards. This one records WHEN each thing
    happened, so only the real order passes.
    """
    from scripts.utils import embeddings

    events = []

    def note_resolution(*a, **k):
        events.append("resolve")
        raise EmbedderRefused("down")

    monkeypatch.setattr(embeddings, "index_embed_target", note_resolution)

    class Recorder(logging.Handler):
        def emit(self, record):
            events.append("read")

    handler = Recorder()
    memory_health.logger.addHandler(handler)
    try:
        memory_health.scan_redundancy(_corpus(tmp_path))
    finally:
        memory_health.logger.removeHandler(handler)

    assert events == ["read", "resolve"], (
        f"expected the corpus walk before the host resolution, got {events}")


# ============================================================
# The direction that must not be lost
# ============================================================

def test_a_working_embedder_still_produces_pairs_and_reports_the_skip(
        tmp_path, no_network):
    """Reading first must not cost the scan its actual job.

    A fix that made every failure path informative while breaking the success
    path would satisfy everything above.
    """
    calls = []

    def twin_vectors(texts):
        calls.append(len(texts))
        return [[1.0, 0.0]] * len(texts)

    result = memory_health.scan_redundancy(_corpus(tmp_path),
                                           embedder=twin_vectors)

    assert result["ok"] is True, result
    assert calls == [2], (
        f"the embedder was handed {calls} texts; it must receive exactly the "
        f"two READABLE files, or every pair index names the wrong file")
    assert result["pairs"] == [
        {"a": "a-clean.md", "b": "c-accented.md", "score": 1.0}], result["pairs"]
    assert "1 unreadable file(s) skipped" in result["note"], result["note"]


def test_a_clean_corpus_says_nothing_about_unreadable_files(tmp_path,
                                                            no_network):
    """The anchor against over-reporting. A scan that always mentions a skip
    trains its reader to ignore the sentence."""
    (tmp_path / "one.md").write_text("---\nname: one\n---\n\nA.\n",
                                     encoding="utf-8")
    (tmp_path / "two.md").write_text("---\nname: two\n---\n\nB.\n",
                                     encoding="utf-8")

    result = memory_health.scan_redundancy(
        tmp_path, embedder=lambda ts: [[1.0, 0.0], [0.0, 1.0]])

    assert result["ok"] is True
    assert "unreadable" not in result["note"], result["note"]


def test_a_corpus_of_nothing_readable_never_reaches_the_embedder(tmp_path):
    """Fail-closed, and before the network.

    Two files, both undecodable. The scan must refuse on the corpus rather than
    spend a probe finding out it had nothing to embed.
    """
    for name in ("a.md", "b.md"):
        (tmp_path / name).write_bytes(b"---\nname: x\n---\n\n"
                                      + LONE_CONTINUATION)

    def never(texts):
        raise AssertionError("the embedder was called on an empty corpus")

    result = memory_health.scan_redundancy(tmp_path, embedder=never)

    assert result["ok"] is False
    assert result["note"] == "fewer than 2 readable memory files (2 unreadable)"


# ============================================================
# The property that keeps this file honest
# ============================================================

def test_the_corpus_fixture_actually_carries_an_undecodable_file(tmp_path):
    """A floor under the fixture.

    Make `b-broken.md` valid UTF-8 and every assertion above passes while
    measuring nothing at all.
    """
    corpus = _corpus(tmp_path)
    assert len(list(corpus.glob("*.md"))) == 3
    with pytest.raises(UnicodeDecodeError):
        (corpus / "b-broken.md").read_text(encoding="utf-8")
    # And the accented one must be readable, or the scan is refusing valid text.
    assert "Résumé" in (corpus / "c-accented.md").read_text(
        encoding="utf-8")


def test_the_scan_never_reaches_the_network(tmp_path, no_network, caplog):
    """The whole file's premise, stated once as its own assertion.

    `no_network` turns any real host resolution into an `AssertionError`. This
    test drives the scan through it with no `embedder=` argument at all, which
    is the exact call the defective test made, and requires that the failure be
    the LOUD one rather than a socket timeout.
    """
    with caplog.at_level(logging.WARNING, logger="scripts.utils.memory_health"):
        result = memory_health.scan_redundancy(_corpus(tmp_path))

    assert result["ok"] is False
    assert "must inject its own embedder" in result["note"], (
        "the scan resolved a host this fixture replaced, so something reaches "
        f"the network by a path this file does not control: {result['note']}")
    # And even on that path, the corrupt file is still named.
    assert "b-broken.md" in caplog.text
