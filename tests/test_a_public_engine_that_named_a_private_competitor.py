"""The public engine carried the title of a private competitor document.

MEASURED 2026-09-02. `tests/test_a_citation_list_that_carried_a_paragraph.py`
quoted, verbatim in a docstring, the filename of a real document that lives at
a `private` path in the data overlay. The engine repository is public, so that
one line disclosed a named competitor and the fact that the operator holds its
commercial proposal. The quote was there because a real filename was the most
convenient example of "a filename that reads like a sentence"; an invented one
of the same shape does the job identically, and now does.

Nothing in the workspace could have caught it. `scripts/leak-guard.py` grades
PATHS against `config/routing-map.yaml`, so it asks where a file belongs, never
what a file says. The push-time content scan looks for secrets. Neither asks the
question this file asks: does a public file spell something that exists only
inside the private tree?

## Why this matches PHRASES and not names

The first version of this guard harvested every directory and file name from the
datastore and searched for each one. MEASURED: it produced hundreds of findings,
almost all of them the words `source`, `errors`, `index`, `probe`, `verify`,
`readme`, `fonts`, `assets`, `profile` and `dashboard`. Those are ordinary
engineering vocabulary that also happen to be filenames in the overlay. A guard
that noisy gets switched off within a week, and the real check goes with it.

So the unit of comparison is a PHRASE: four or more words taken verbatim from a
private filename. A document title that long does not appear in engine prose by
coincidence, which is what makes the signal clean. It is also the exact shape of
the leak that happened, and of the way these leaks happen at all: somebody
copies a real filename because it is the handiest example.

## What this test does NOT cover, stated so nobody assumes otherwise

- **A one-word entity name.** A competitor folder named with a single lowercase
  word is invisible here, because that word cannot be told apart from ordinary
  prose by any rule this file can apply offline. The leak that prompted this
  guard was caught by hand, not by a rule.
- **A private fact paraphrased.** This reads names, never meaning. Rewriting a
  private figure into engine prose without naming anything passes.
- **A public clone.** The comparison needs the private tree. The skip below
  states the mechanical condition, because a vague skip reason hides a guard
  that quietly stopped running.

Both halves are derived at run time and neither is written down here. A
hand-maintained list of forbidden names is the defect shape this repository
keeps finding: it falls behind the thing it describes, silently, and a guard
that has fallen behind is green. `.claude/rules/datastore.md` is the local
proof, having drifted until it omitted three whole top-level trees.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import (  # noqa: E402
    get_datastore_dir,
    get_routing_destination,
)

# The unit of comparison is a WINDOW of consecutive words, not the whole
# filename, and that is the second thing this guard got wrong.
#
# MEASURED 2026-09-02: the leak that prompted this file was a TRUNCATED title.
# The shape, with every word invented: the public docstring carried "Northwind
# Systems - Flow Telemetry - Commercial Offer" while the private file is named
# "... Summary (COS) for a regional carrier, rev 4". A whole-filename
# comparison finds nothing there, so the first version of this guard would have
# reported the tree clean on the exact defect it was written for. Sliding a
# window catches the head of a title, its tail, or any span in the middle.
#
# The example is invented because the first draft of this comment was not. It
# quoted the real title, so the file explaining the leak REPRODUCED it, and the
# guard flagged its own source the moment that source became tracked. Writing
# about a disclosure by restating it is the same mistake wearing an apology.
#
# Five words, not four. 31C is a deep packet inspection company, so a four-word
# window like "deep packet inspection solution" is ordinary engine vocabulary
# here; five consecutive words of a document title are not.
WINDOW_WORDS = 5

# A word shorter than this carries no identifying weight inside a phrase, and
# dropping it lets "Acme - PRODUCT.ONE Capability Deck" and
# "Acme PRODUCT ONE Capability Deck" compare equal. Invented, for the reason
# given above the previous constant.
MIN_WORD_LEN = 2

# Reviewed and accepted matches. Each entry states why the phrase is safe in
# public, so the next reader re-judges it instead of trusting it. Keep this
# SHORT: a long allowlist is a guard being switched off one line at a time.
ACCEPTED: dict[str, str] = {
    "31c deck design system": (
        "A cross-repository POINTER, not a disclosure. Four engine files name "
        "`datastore/brand/design-system/31c-deck-design-system.md` so a reader "
        "can find the spec the theme was built from. The filename discloses "
        "only that 31C has a deck design system, and 31C is one of the "
        "identities the engine is explicitly allowed to carry. The file itself "
        "routes `private` because of the investor-deck renders embedded IN it, "
        "which is a fact about its contents, not about its name."
    ),
    "odun one ai monetization use": (
        "Two DIFFERENT documents that share a subject, not a copied filename. "
        "`scripts/generate-usecases-docx.py` writes its own deliverable, "
        "`outputs/deliverables/documents/ODUN.ONE - AI Monetization Use Cases "
        "for Telco Operators v2.docx`; the datastore file it collides with is "
        "`datastore/products/odun-one/sales/... (Telco) v3.docx`. MEASURED "
        "2026-09-02: v2 and v3, two documents in one product-document lineage, "
        "neither generated from the other. The shared words are 31C's own "
        "product-marketing description of the subject, and ODUN.ONE is one of "
        "the identities the engine is explicitly permitted to carry. Renaming a "
        "business deliverable to satisfy a lint guard changes an artefact the "
        "operator owns, and the operator declined that trade on 2026-09-02 and "
        "chose this exception instead. Cost of the entry: a future leak that "
        "happened to reuse these exact five words would be missed."
    ),
    "one ai monetization use cases": (
        "The adjacent window of the same phrase, from the same two files, "
        "accepted for the same reason as the entry above: the engine's v2 "
        "deliverable and the datastore's v3 document are separate documents in "
        "one lineage, and the words they share are 31C's public description of "
        "the subject rather than anything copied out of the private tree. Both "
        "windows have to be listed because the guard slides a window over the "
        "text and a single collision yields more than one. Same cost: these "
        "exact five words are now invisible to it."
    ),
    "built from zero lines of": (
        "The direction of copying runs the other way. This is 31C's own public "
        "positioning line, quoted in the pptx brand voice guide; the LinkedIn "
        "post whose archive filename collides with it was NAMED after the "
        "slogan, and was published on LinkedIn. A public slogan is not a "
        "disclosure, and 31C is an identity the engine is allowed to carry. "
        "Cost of this entry: a future leak that happened to reuse these exact "
        "five words would be missed."
    ),
}

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".xlsx", ".pptx", ".docx",
    ".db", ".sqlite", ".sqlite3", ".pyc", ".so", ".dylib", ".mp4", ".mp3",
}

# Generated or vendored assets. Their bytes are not authored here, so a hit in
# one is a finding about its generator, not about this repository's prose.
EXCLUDED_PREFIXES = ("docs/assets/", ".git/")


def _normalise(text: str) -> str:
    """Collapse to lowercase words separated by single spaces.

    Both sides go through this, so punctuation, case, underscores and repeated
    separators cannot hide a match. A filename written with hyphens and the
    same title written with spaces normalise identically.
    """
    return " ".join(
        w for w in re.split(r"[^A-Za-z0-9]+", text.casefold())
        if len(w) >= MIN_WORD_LEN
    )


def _private_phrases(datastore: Path) -> dict[str, Path]:
    """Normalised filename phrases from EVERY file in the datastore.

    Operator directive, 2026-09-02, given in capitals and without qualification:
    everything in `datastore/` is private and must never be public.

    So this walk applies no routing filter. An earlier version restricted
    itself to paths routing `private`, which excluded the `corporate` majority
    of the tree. `corporate` means shared down to executives, which is not
    public, but the directive is about this repository, and this repository is
    public. Routing decides who among the operator's own people receives a
    file; it does not license quoting that file's name in a public clone.

    The functional exception, and it is narrow: engine code that EMBEDS a brand
    asset has to name the file it loads. Those names sit in `ACCEPTED` with the
    reason, one per phrase, rather than being waved through by a whole
    destination.
    """
    phrases: dict[str, Path] = {}
    for path in datastore.rglob("*"):
        if ".git" in path.parts or path.is_dir():
            continue
        words = _normalise(path.stem).split()
        if len(words) < WINDOW_WORDS:
            continue
        for start in range(len(words) - WINDOW_WORDS + 1):
            window = " ".join(words[start:start + WINDOW_WORDS])
            if window in ACCEPTED:
                continue
            phrases.setdefault(window, path)
    return phrases


def _windows(text: str) -> set[str]:
    """Every window of consecutive words in `text`, normalised.

    A set of windows, looked up against the harvested set, is what makes this
    affordable. Substring-searching each of a few thousand windows across
    every engine file is quadratic; two sets and an intersection is linear.
    """
    words = _normalise(text).split()
    return {
        " ".join(words[i:i + WINDOW_WORDS])
        for i in range(max(0, len(words) - WINDOW_WORDS + 1))
    }


def _engine_prose_files() -> list[Path]:
    """Tracked engine files whose bytes are prose a human wrote."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(ROOT), capture_output=True, check=True,
    ).stdout
    files = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if rel.startswith(EXCLUDED_PREFIXES):
            continue
        path = ROOT / rel
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        if get_routing_destination(rel) != "engine":
            continue
        files.append(path)
    return files


def _reads_as_text(path: Path) -> str | None:
    """Return the text, or None when the bytes are not text.

    A NUL byte in the first block is the cheap and reliable signal. Suffix
    alone missed a binary carrying no extension this file knows about.
    """
    try:
        blob = path.read_bytes()
    except OSError as exc:
        pytest.fail(f"cannot read tracked engine file {path}: {exc}")
    if b"\0" in blob[:8192]:
        return None
    return blob.decode("utf-8", "ignore")


@pytest.fixture(scope="module")
def datastore() -> Path:
    ds = get_datastore_dir()
    if not ds.is_dir():
        pytest.skip(
            f"no data overlay on this machine: {ds} is not a directory. "
            "This guard compares public engine prose against phrases that "
            "exist only in the private datastore, so it can run only where "
            "that tree is checked out. On a public clone there is nothing to "
            "compare against, and the skip is the correct outcome."
        )
    return ds


@pytest.fixture(scope="module")
def phrases(datastore) -> dict[str, Path]:
    return _private_phrases(datastore)


def test_the_guard_has_a_corpus_to_check(phrases):
    """Zero phrases or zero files is a pass over an empty corpus, not a pass.

    A broken walk, a broken routing lookup, or a data root pointed at the wrong
    directory each produce an empty set, and an empty set reports the tree
    clean on any input.
    """
    files = _engine_prose_files()
    assert len(phrases) > 50, (
        f"only {len(phrases)} private phrases harvested; the datastore walk "
        "or the routing lookup is broken, and this guard would pass on "
        "anything"
    )
    assert len(files) > 100, (
        f"only {len(files)} engine prose files found; `git ls-files` or the "
        "routing filter is broken"
    )


def test_the_guard_catches_a_repunctuated_phrase(phrases):
    """A refusal that has never fired is not known to be a refusal.

    Spells one harvested window with different punctuation and casing from the
    filename it came from, then asserts the matcher still finds it. Proves the
    search works AND that normalisation is doing its job.
    """
    victim = sorted(phrases)[0]
    disguised = f"See {victim.replace(' ', ' - ').upper()} for details."
    assert victim in _windows(disguised), (
        f"the matcher lost {victim!r} once it was re-punctuated; "
        "normalisation is not doing its job"
    )


def test_the_guard_catches_a_TRUNCATED_title(datastore, phrases):
    """The regression that made this guard useless on its own founding case.

    MEASURED: the leaked docstring stopped partway through the title while the
    private file carried several more words, a date and a revision number.
    Comparing whole filenames, the harvested phrase was a SUPERSTRING of the
    leak, so `phrase in text` was False and the guard passed on the very
    disclosure it exists to catch. The words themselves are not repeated here,
    which is the whole lesson of the incident.

    Takes a real private filename, keeps only its first two thirds, and asserts
    the matcher still finds it. Fails loudly if the window logic is ever
    replaced by a whole-name comparison again.
    """
    long_names = [
        p for p in datastore.rglob("*")
        if p.is_file() and ".git" not in p.parts
        and get_routing_destination(str(p.relative_to(datastore.parent)))
        == "private"
        and len(_normalise(p.stem).split()) >= WINDOW_WORDS + 3
    ]
    assert long_names, (
        "no private filename is long enough to truncate; this test cannot "
        "measure what it claims and must not be left silently passing"
    )

    words = _normalise(sorted(long_names)[0].stem).split()
    truncated = " ".join(words[: (len(words) * 2) // 3]) + " ..."

    assert _windows(truncated) & phrases.keys(), (
        f"a truncated private title went unnoticed: {truncated!r}. The guard "
        "is comparing whole filenames again."
    )


def test_an_invented_sentence_is_not_reported(phrases):
    """The control. Without it, a matcher that flags everything also passes.

    The two tests above prove the guard can say yes. This proves it can say
    no, so a comparison that is always true cannot satisfy all three.
    """
    clean = "Northwind Traffic Systems quarterly capability summary for review."
    assert not (_windows(clean) & phrases.keys()), (
        "an invented sentence matched a private phrase; the matcher reports "
        "findings that are not there"
    )


def test_no_public_engine_file_quotes_a_private_filename(datastore, phrases):
    """The finding this file exists for."""
    overlay = datastore.parent
    findings: list[str] = []
    for path in _engine_prose_files():
        text = _reads_as_text(path)
        if text is None:
            continue
        for window in _windows(text) & phrases.keys():
            findings.append(
                f"{path.relative_to(ROOT)} quotes {window!r}, from the "
                f"filename of {phrases[window].relative_to(overlay)}"
            )

    assert not findings, (
        "public engine files quote filenames that exist only in the private "
        "datastore. Replace each with an invented example of the same shape, "
        "or, if the phrase is genuinely public, add it to ACCEPTED in this "
        "file with the reason.\n  " + "\n  ".join(sorted(findings))
    )
