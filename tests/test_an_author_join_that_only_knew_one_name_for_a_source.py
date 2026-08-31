"""The brain-health joins that knew only one of a source's two names.

`find_orphan_principles` in scripts/odin-brain-health.py documents the
convention in its own docstring: a note references another "in two
interchangeable forms, by timestamp id or by filename slug", and it checks
both. The two joins that walk a principle's `sources:` list checked the id
alone, so a slug-form link read as no link at all.

Measured read-only against the live brain 2026-08-29 (57 sources, 294
principles, 441 principle-to-source references): 13 references use the slug
form. `find_domain_clusters` suppressed one genuine multi-author cluster
outright, the `creativity` keyword with 5 principles, whose two sources
resolved to one author instead of two and so failed the `len(authors) >= 2`
gate; 86 clusters reported where 87 exist. `find_keyword_overlaps` reported 11
of its 1318 pairs as "shares 2+ keywords but isn't wiki-linked" about links
that exist.

Two smaller defects sit in the same functions. Every source with an empty `id:`
wrote to the same `""` key in the author map, later file overwriting earlier and
merging distinct authors into one. And `find_stale_seeds` required `created:`
across all four subdirs, while a source is dated by `ingested:` and does not
carry `created:` at all, so a valid source seed was skipped every time.

All fixture data here is invented (James Bond, Acme Telecom). The one test that
reads the operator's real brain is read-only and asserts counts, never
identities.

Tests: this file.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "odin-brain-health.py"


@pytest.fixture()
def bh():
    spec = importlib.util.spec_from_file_location("odin_brain_health_join", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _brain(tmp_path: Path) -> Path:
    root = tmp_path / "odin-brain"
    for sub in ("sources", "principles", "positions", "episodes", "conflicts", "reference"):
        (root / sub).mkdir(parents=True)
    return root


def _write(path: Path, **fields) -> None:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines += ["---", "", "body", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _source(root, slug, *, sid, author, keywords=("dpi",), ingested="2026-01-01", **extra):
    _write(root / "sources" / f"{slug}.md", id=sid, title=slug, type="source",
           format="pdf", author=author, ingested=ingested, confidence="high",
           keywords=list(keywords), **extra)


def _principle(root, slug, *, sid, sources, keywords):
    _write(root / "principles" / f"{slug}.md", id=sid, title=slug, type="principle",
           sources=list(sources), confidence="high", keywords=list(keywords),
           created="2026-02-01")


# ---------------------------------------------------------------- clusters


def test_a_slug_linked_source_contributes_its_author_to_the_cluster(bh, tmp_path, monkeypatch):
    """The measured defect. Three principles share a keyword; one links its
    source by slug. Both authors must count, so the cluster is reported."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000", author="James Bond")
    _source(root, "bond-dpi-review", sid="20260101100001", author="Vesper Lynd")
    _principle(root, "p-one", sid="1", sources=["20260101100000"], keywords=["creativity"])
    _principle(root, "p-two", sid="2", sources=["20260101100000"], keywords=["creativity"])
    # The slug form, which the id-only join could never resolve.
    _principle(root, "p-three", sid="3", sources=["bond-dpi-review"], keywords=["creativity"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    clusters = bh.find_domain_clusters(bh.collect_brain_files())
    assert len(clusters) == 1, (
        f"the slug-linked source's author was dropped, so the cluster was "
        f"suppressed; got {clusters!r}")
    assert clusters[0]["keyword"] == "creativity"
    assert clusters[0]["author_count"] == 2
    assert clusters[0]["authors"] == ["James Bond", "Vesper Lynd"]


def test_a_genuinely_single_author_cluster_is_still_not_reported(bh, tmp_path, monkeypatch):
    """The other direction. Resolving slugs must not turn every cluster
    multi-author: three principles, both reference forms, one author."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000", author="James Bond")
    _source(root, "bond-dpi-review", sid="20260101100001", author="James Bond")
    _principle(root, "p-one", sid="1", sources=["20260101100000"], keywords=["dpi"])
    _principle(root, "p-two", sid="2", sources=["bond-dpi-review"], keywords=["dpi"])
    _principle(root, "p-three", sid="3", sources=["bond-dpi-review"], keywords=["dpi"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    assert bh.find_domain_clusters(bh.collect_brain_files()) == []


def test_two_sources_without_ids_do_not_collide_into_one_author(bh, tmp_path, monkeypatch):
    """Every id-less source used to write the same `""` key, so their authors
    merged. Keyed by slug they stay distinct."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="''", author="James Bond")
    _source(root, "bond-dpi-review", sid="''", author="Vesper Lynd")
    _principle(root, "p-one", sid="1", sources=["acme-telecom-whitepaper"], keywords=["dpi"])
    _principle(root, "p-two", sid="2", sources=["bond-dpi-review"], keywords=["dpi"])
    _principle(root, "p-three", sid="3", sources=["bond-dpi-review"], keywords=["dpi"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    clusters = bh.find_domain_clusters(bh.collect_brain_files())
    assert len(clusters) == 1
    assert clusters[0]["author_count"] == 2
    # The empty id must never become a lookup key of its own.
    assert "" not in bh._source_ref_forms({"id": "  "}, root / "sources" / "x.md")


def test_a_reference_to_a_missing_source_is_counted_out_loud(bh, tmp_path, monkeypatch, capsys):
    """A `sources:` entry naming no file on disk has no author, so it is right
    to drop it. Dropping it silently is not: a cluster needs two authors, so
    each dropped reference can remove a real cluster from a report that then
    reads as complete."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000", author="James Bond")
    _principle(root, "p-one", sid="1", sources=["20260101100000"], keywords=["dpi"])
    _principle(root, "p-two", sid="2", sources=["20260101100000"], keywords=["dpi"])
    # Names a source that was never ingested.
    _principle(root, "p-three", sid="3", sources=["20260101109999"], keywords=["dpi"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    clusters = bh.find_domain_clusters(bh.collect_brain_files())
    # One resolvable author only, so no cluster -- and the report must say why.
    assert clusters == []
    err = capsys.readouterr().err
    assert "1 principle source reference" in err, (
        f"the unresolvable reference was dropped in silence; stderr was {err!r}")
    assert "could not be counted" in err
    # No author was invented to paper over the gap.
    assert "Unknown" not in err


def test_a_brain_with_every_source_present_says_nothing(bh, tmp_path, monkeypatch, capsys):
    """The other direction, so the fix cannot be "always warn": a brain whose
    references all resolve must produce no warning at all."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000", author="James Bond")
    _source(root, "bond-dpi-review", sid="20260101100001", author="Vesper Lynd")
    _principle(root, "p-one", sid="1", sources=["20260101100000"], keywords=["dpi"])
    _principle(root, "p-two", sid="2", sources=["bond-dpi-review"], keywords=["dpi"])
    _principle(root, "p-three", sid="3", sources=["20260101100001"], keywords=["dpi"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    assert len(bh.find_domain_clusters(bh.collect_brain_files())) == 1
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- overlaps


def test_a_slug_linked_pair_is_not_reported_as_an_unlinked_overlap(bh, tmp_path, monkeypatch):
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000",
            author="James Bond", keywords=("dpi", "sovereignty"))
    _principle(root, "p-one", sid="1", sources=["acme-telecom-whitepaper"],
               keywords=["dpi", "sovereignty"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    assert bh.find_keyword_overlaps(bh.collect_brain_files()) == []


def test_a_genuinely_unlinked_overlap_is_still_reported(bh, tmp_path, monkeypatch):
    """The other direction: the check must still find the pairs it exists for."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000",
            author="James Bond", keywords=("dpi", "sovereignty"))
    _principle(root, "p-one", sid="1", sources=["20260101100099"],
               keywords=["dpi", "sovereignty"])
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    overlaps = bh.find_keyword_overlaps(bh.collect_brain_files())
    assert len(overlaps) == 1
    assert overlaps[0]["shared_keywords"] == ["dpi", "sovereignty"]


# ---------------------------------------------------------------- stale seeds


def test_a_source_seed_dated_only_by_ingested_ages(bh, tmp_path, monkeypatch):
    """`ingested:` is what REQUIRED_FIELDS["source"] demands; `created:` is not a
    source field at all, so requiring it skipped every source seed."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000",
            author="James Bond", status="seed")
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    stale = bh.find_stale_seeds(bh.collect_brain_files())
    assert len(stale) == 1, f"an ingested-only source seed was never aged; got {stale!r}"
    assert stale[0]["file"] == "sources/acme-telecom-whitepaper.md"
    assert stale[0]["age_days"] > 7


def test_a_fresh_source_seed_is_not_stale(bh, tmp_path, monkeypatch):
    """The other direction: resolving `ingested:` must not age everything."""
    root = _brain(tmp_path)
    today = bh.datetime.now(bh.get_default_tz()).date().isoformat()
    _source(root, "acme-telecom-whitepaper", sid="20260101100000",
            author="James Bond", status="seed", ingested=today)
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    assert bh.find_stale_seeds(bh.collect_brain_files()) == []


def test_a_principle_seed_is_still_dated_by_created(bh, tmp_path, monkeypatch):
    """The per-subdir field map must not change the three subdirs that were
    already right."""
    root = _brain(tmp_path)
    _principle(root, "p-one", sid="1", sources=[], keywords=["dpi"])
    path = root / "principles" / "p-one.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "created: 2026-02-01", "created: 2026-02-01\nstatus: seed"), encoding="utf-8")
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    stale = bh.find_stale_seeds(bh.collect_brain_files())
    assert len(stale) == 1
    assert stale[0]["file"] == "principles/p-one.md"


def test_an_unreadable_ingested_value_warns_and_names_that_field(bh, tmp_path, monkeypatch, capsys):
    """The warning used to hard-code `created`, which on a source dated by
    `ingested:` raises KeyError out of the handler and turns a warning into a
    crash."""
    root = _brain(tmp_path)
    _source(root, "acme-telecom-whitepaper", sid="20260101100000",
            author="James Bond", status="seed", ingested="not-a-date")
    monkeypatch.setattr(bh, "brain_root", lambda p=root: p)

    assert bh.find_stale_seeds(bh.collect_brain_files()) == []
    err = capsys.readouterr().err
    assert "ingested" in err and "not aged" in err


# ---------------------------------------------------------------- live corpus


def test_the_live_brain_reports_no_slug_linked_false_positives(bh):
    """Read-only over the operator's real brain, counts only. Skipped where the
    corpus is absent (a bare public clone has no data overlay); where it is
    present, an empty corpus would prove nothing, so that is asserted first."""
    if not bh.brain_root().is_dir():
        pytest.skip("no data overlay on this machine; nothing to measure")
    files = bh.collect_brain_files()
    assert len(files["sources"]) > 0 and len(files["principles"]) > 0, (
        "the brain corpus is empty; this check would pass over nothing")

    from scripts.utils.markdown import frontmatter_list

    slug_by_path = {f"sources/{f.name}": f.stem for f in files["sources"]}
    links = {}
    for f in files["principles"]:
        fm = bh.parse_frontmatter(f)
        if fm:
            links[f"principles/{f.name}"] = {
                str(s) for s in frontmatter_list(fm.get("sources"))}

    false_positives = [
        o for o in bh.find_keyword_overlaps(files)
        if slug_by_path.get(o["source_file"]) in links.get(o["principle_file"], set())
    ]
    assert false_positives == [], (
        f"{len(false_positives)} reported overlaps name a link that exists in "
        f"slug form")


def test_the_usage_block_lists_every_flag_main_accepts(bh):
    """The docstring is the first thing a reader sees and its Usage block reads
    as exhaustive; it omitted `--compile`, the flag that drives the JSON compile
    report the /odin pipeline consumes.

    The flag list is DERIVED from the `add_argument` calls by walking the
    module's AST, not spelled out here. A hard-coded tuple asserts nothing about
    a flag added tomorrow: it would still pass, green, over the exact omission
    this test is named for. Deriving it means the check grows with the parser.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    flags = {
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        for arg in node.args
        if isinstance(arg, ast.Constant) and str(arg.value).startswith("--")
    }
    assert len(flags) >= 4, (
        f"only {len(flags)} flags found; an empty or near-empty derivation "
        f"would pass this check over nothing")

    doc = ast.get_docstring(tree) or ""
    missing = sorted(f for f in flags if f not in doc)
    assert missing == [], f"accepted by main() but absent from the Usage block: {missing}"


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
