"""Four readers that trusted a shape nobody promised them.

Each of these takes input from outside itself and reads a shape out of it
without asking whether the shape is there. All four fail in the same direction:
a wrong answer or a raw traceback where the function's own contract promises
something specific.

**`odin_principles._load_principles`.** `fm.get("title", f.stem)` returns None,
not the default, for a principle whose frontmatter carries a bare `title:` - the
key is PRESENT and YAML types it as None. The comment two lines above explains
this exact trap for `keywords` and the fix was never applied to `title`. A None
title renders as the literal "None" in a /meeting-prep or /deal-strategy
citation. `confidence` carried the identical trap and feeds a ranking sort, so it
is fixed beside it.

**`perplexity_client.research`.** The docstring tells callers that transport
failures arrive as `RuntimeError`. An HTTP 200 carrying a non-JSON body raised
`json.JSONDecodeError`, and one carrying an error payload, an empty `choices`,
or a `message` with no `content` raised `KeyError`/`IndexError` - none of them a
`RuntimeError`, so a caller written to the published contract got a raw
traceback on an anomalous-but-successful exchange.

**`rmtree_force`.** `Path.exists()` follows symlinks, so a BROKEN symlink reads
as absent and the function returned having removed nothing and raised nothing.
`Path.unlink(missing_ok=True)`, the semantics the docstring claims to mirror,
removes the link itself.

**`router_payload.dirty_sources`.** The refusal it feeds has to NAME a file the
operator can open. `git status --porcelain` reports a renamed path as
`R  old -> new`, and `line[3:]` yielded the literal string "old.md -> new.md":
a dirty source naming no file, with neither half identified. `--no-renames` plus
`-z` gives plain, verbatim paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import perplexity_client, router_payload  # noqa: E402
from scripts.utils.odin_principles import principles_for_domains  # noqa: E402
from scripts.utils.rmtree import rmtree_force  # noqa: E402


# ============================================================
# odin_principles: a key with no value is not an absent key
# ============================================================

def _brain(tmp_path: Path, name: str, frontmatter: str) -> Path:
    pdir = tmp_path / "principles"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / name).write_text(f"---\n{frontmatter}---\n\nbody\n", encoding="utf-8")
    return tmp_path


def test_a_bare_title_key_falls_back_to_the_filename(tmp_path):
    root = _brain(tmp_path, "never-split-the-difference.md",
                  "title:\nkeywords: [negotiation]\nconfidence: high\n")
    got = principles_for_domains(["negotiation"], brain_root=root)
    assert len(got) == 1
    assert got[0]["title"] == "never-split-the-difference"


def test_a_bare_confidence_key_falls_back_to_the_empty_string(tmp_path):
    root = _brain(tmp_path, "hold-the-heading.md",
                  "title: Hold the heading\nkeywords: [navigation]\nconfidence:\n")
    got = principles_for_domains(["navigation"], brain_root=root)
    assert got[0]["confidence"] == ""


def test_a_real_title_is_still_used(tmp_path):
    root = _brain(tmp_path, "hold-the-heading.md",
                  "title: Hold the heading\nkeywords: [navigation]\n")
    got = principles_for_domains(["navigation"], brain_root=root)
    assert got[0]["title"] == "Hold the heading"


def test_an_absent_title_key_also_falls_back(tmp_path):
    root = _brain(tmp_path, "hold-the-heading.md", "keywords: [navigation]\n")
    got = principles_for_domains(["navigation"], brain_root=root)
    assert got[0]["title"] == "hold-the-heading"


def test_ranking_still_sorts_with_a_bare_confidence_present(tmp_path):
    root = _brain(tmp_path, "alpha.md", "keywords: [navigation]\nconfidence:\n")
    _brain(tmp_path, "bravo.md",
           "keywords: [navigation]\nconfidence: high\n")
    got = principles_for_domains(["navigation"], brain_root=root)
    assert {p["slug"] for p in got} == {"alpha", "bravo"}


# ============================================================
# perplexity_client: an HTTP 200 is not a promise about the body
# ============================================================

class _Body:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def stub_perplexity(monkeypatch):
    monkeypatch.setattr(perplexity_client, "load_api_key",
                        lambda name: "not-a-real-key")

    def install(raw: bytes):
        monkeypatch.setattr(perplexity_client.urllib.request, "urlopen",
                            lambda req, timeout=None: _Body(raw))

    return install


@pytest.mark.parametrize("raw", [
    b"{}",
    b'{"choices": []}',
    b'{"choices": [{"message": {}}]}',
    b"not json at all",
    b'"a bare string"',
    b'{"choices": "not a list"}',
])
def test_an_unreadable_two_hundred_arrives_as_a_runtime_error(stub_perplexity, raw):
    stub_perplexity(raw)
    with pytest.raises(RuntimeError) as excinfo:
        perplexity_client.research("who owns the Bellweather yard?")
    assert "not in the shape" in str(excinfo.value)


def test_a_well_shaped_two_hundred_still_returns_content_and_citations(stub_perplexity):
    stub_perplexity(b'{"choices": [{"message": {"content": "an answer"}}], '
                    b'"citations": ["https://example.invalid/a"]}')
    content, citations = perplexity_client.research("q")
    assert content == "an answer"
    assert citations == ["https://example.invalid/a"]


def test_a_missing_citations_key_is_not_an_error(stub_perplexity):
    stub_perplexity(b'{"choices": [{"message": {"content": "an answer"}}]}')
    content, citations = perplexity_client.research("q")
    assert content == "an answer"
    assert citations == []


# ============================================================
# rmtree_force: a broken symlink is not an absent path
# ============================================================

def test_a_broken_symlink_is_removed(tmp_path):
    link = tmp_path / "dangling"
    os.symlink(tmp_path / "nowhere", link)
    assert link.is_symlink() and not link.exists()
    rmtree_force(link)
    assert not link.is_symlink()
    assert not link.exists()


def test_a_genuinely_absent_path_is_still_not_an_error(tmp_path):
    rmtree_force(tmp_path / "never-existed")


def test_a_real_tree_is_still_removed(tmp_path):
    tree = tmp_path / "tree" / "inner"
    tree.mkdir(parents=True)
    (tree / "file.txt").write_text("x", encoding="utf-8")
    rmtree_force(tmp_path / "tree")
    assert not (tmp_path / "tree").exists()


def test_a_symlink_removal_does_not_follow_the_link(tmp_path):
    """Only the entry named goes; whatever it pointed at is untouched."""
    victim = tmp_path / "real"
    victim.mkdir()
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "later-broken"
    os.symlink(victim / "gone-now", link)
    rmtree_force(link)
    assert not link.is_symlink()
    assert (victim / "keep.txt").exists()


# ============================================================
# router_payload.dirty_sources: the refusal names a file
# ============================================================

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def payload_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reference" / "skill-router").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "builder@example.invalid")
    _git(repo, "config", "user.name", "Builder")
    sources = []
    for name in ("intel.md", "operations.md"):
        path = repo / "reference" / "skill-router" / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        sources.append(path)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    monkeypatch.setattr(router_payload, "ROOT", repo)
    monkeypatch.setattr(router_payload, "payload_sources",
                        lambda: list(repo.rglob("reference/skill-router/*.md")))
    return repo


def test_a_clean_tree_reports_nothing_dirty(payload_repo):
    assert router_payload.dirty_sources() == []


def test_a_quoted_path_is_named_as_it_is_on_disk(payload_repo):
    """The reachable half of the defect: a path git C-quotes.

    Without `-z` the record is `?? "reference/skill-router/od\\"un.md"` and the
    old unquoting left the backslash in, naming a path that does not exist.
    """
    odd = payload_repo / "reference" / "skill-router" / 'od"un.md'
    odd.write_text("# odd\n", encoding="utf-8")
    dirty = router_payload.dirty_sources()
    assert 'reference/skill-router/od"un.md' in dirty, dirty
    assert all(entry == str(payload_repo / entry).replace(f"{payload_repo}/", "")
               for entry in dirty)
    for entry in dirty:
        assert (payload_repo / entry).exists(), f"named a path that is not there: {entry}"


def test_an_edited_source_is_still_reported(payload_repo):
    target = payload_repo / "reference" / "skill-router" / "intel.md"
    target.write_text("# edited\n", encoding="utf-8")
    assert router_payload.dirty_sources() == ["reference/skill-router/intel.md"]


def test_a_renamed_source_never_reports_an_arrow_pair(payload_repo):
    """`--no-renames` holds the guarantee even if the pathspec ever widens."""
    _git(payload_repo, "mv",
         "reference/skill-router/intel.md",
         "reference/skill-router/intelligence.md")
    dirty = router_payload.dirty_sources()
    assert dirty, "a renamed payload source must still refuse"
    assert all("->" not in entry for entry in dirty), dirty
