#!/usr/bin/env python3
"""R11 -- Odin brain temporal-validity lint.

Guards the ``superseded_by`` convention: when a teaching overrides a principle
or position, the old note is marked ``superseded_by: <slug>`` + ``superseded_date``
(optionally ``valid_until``) and KEPT, never deleted -- so "what did we believe
about X before date Y" stays answerable. This lint validates the convention:

  - dangling          : superseded_by points to a non-existent note  (error)
  - circular_chain    : A -> B -> ... -> A supersession cycle          (error)
  - orphan_superseded : a superseded principle neither referenced by a
                        position nor cited by its successor -- a candidate
                        for archival                                   (warn)
  - dangling_wikilink : a free [[wiki-link]] in a note body resolves to no
                        brain note (or to a missing crm:/thread: entity). A
                        [[name]] with no target yet is a legitimate "write this
                        later" marker, so this is a WARN -- but surfacing it
                        stops the backlog growing silently, the gap that let 28
                        dangling links accumulate before the 2026-06-16 sweep. (warn)

Usage:
    python3 scripts/odin_brain_lint.py [--json] [--brain-root PATH]

Exit 1 if any error-severity issue is found, else 0. Snake_case because it is
imported by tests and by ``/odin compile`` (odin-brain-health.py). Read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.markdown import parse_frontmatter as _parse_fm  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_crm_contacts_dir,
    get_knowledge_dir,
    get_personal_root,
    get_plans_dir,
    get_threads_dir,
)
# Reuse the PageRank resolver primitives so "what counts as resolvable" is
# defined in exactly one place -- a wiki-link the lint flags is precisely one
# the recall graph (scripts/odin_pagerank.py) would also fail to wire an edge for.
from scripts.odin_pagerank import (  # noqa: E402
    FRONTMATTER_RE,
    _slug,
    parse_wikilinks,
)

# DATA-root relative (get_knowledge_dir -> get_data_root): resolves to the data
# sibling when run from the engine clone, not a fixed engine-relative path.
BRAIN_ROOT = get_knowledge_dir() / "odin-brain"
SUBDIRS = ["sources", "principles", "positions", "episodes", "conflicts", "reference"]

# Cross-namespace wiki-link targets that point OUT of the brain (the recall
# graph ignores them; they are documentary). Verified against these subtrees of
# the brain's own data root when present (rglob, so plans/archive/ is covered).


def _namespace_rels():
    """ns -> data-root-relative subpath for each cross-tree target.

    Subpaths are derived from the seam helpers (relative to the personal/data
    root) so no hardcoded data-path literal lives in engine code (leak-guard).
    They stay *relative* on purpose: _external_entities joins them onto a root
    derived from the passed brain_root, which keeps the lint hermetic under a
    temp brain in tests. Thread refs scope to the business/ subtree -- personal
    threads are CEO-only and are never wiki-link targets."""
    base = get_personal_root()

    def rel(p):
        try:
            return p.relative_to(base)
        except ValueError:  # e.g. THREADS_ROOT override points outside base
            return Path(p.name)

    return {
        "crm": rel(get_crm_contacts_dir()),
        "thread": rel(get_threads_dir()) / "business",
        "plan": rel(get_plans_dir()),
    }

# Intra-brain TYPE prefixes: [[source:slug]] / [[principle:slug]] etc. are just a
# type hint -- the target is a normal brain note, resolved by the slug after the
# colon (matching the brain's own note `type` values).
BRAIN_TYPE_PREFIXES = {"source", "principle", "position", "episode", "conflict", "reference"}


def _frontmatter(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    data, _ = _parse_fm(text)
    return data or None


def collect_brain_files(brain_root=None):
    """Return (files_by_subdir, id_to_file, slug_to_file). ``brain_root`` defaults
    to the real brain; tests pass a temp dir."""
    root = Path(brain_root) if brain_root else BRAIN_ROOT
    files_by_subdir = {d: {} for d in SUBDIRS}
    id_to_file: dict[str, Path] = {}
    slug_to_file: dict[str, Path] = {}
    for subdir in SUBDIRS:
        dirpath = root / subdir
        if not dirpath.exists():
            continue
        for f in sorted(dirpath.glob("*.md")):
            fm = _frontmatter(f)
            if not fm:
                continue
            files_by_subdir[subdir][f.stem] = {"path": f, "frontmatter": fm}
            fid = fm.get("id")
            if fid:
                id_to_file[str(fid)] = f
            slug_to_file[f.stem] = f
    return files_by_subdir, id_to_file, slug_to_file


def _iter_files(files_by_subdir):
    for subdir, files in files_by_subdir.items():
        for slug, info in files.items():
            yield subdir, slug, info


def check_dangling_references(files_by_subdir, slug_to_file):
    """Every superseded_by must point to an existing brain note."""
    issues = []
    for subdir, slug, info in _iter_files(files_by_subdir):
        tgt = info["frontmatter"].get("superseded_by")
        if tgt and str(tgt) not in slug_to_file:
            issues.append({
                "check": "dangling_reference",
                "severity": "error",
                "file": f"{subdir}/{info['path'].name}",
                "slug": slug,
                "message": f"superseded_by '{tgt}' does not point to an existing brain note",
            })
    return issues


def check_circular_chains(files_by_subdir):
    """No A -> B -> ... -> A. Each note has at most one superseded_by, so the
    supersession relation is a function; a cycle is a node reachable from itself."""
    graph = {}
    for _subdir, slug, info in _iter_files(files_by_subdir):
        tgt = info["frontmatter"].get("superseded_by")
        if tgt:
            graph[slug] = str(tgt)
    issues, reported = [], set()
    for start in graph:
        on_path, path, node = set(), [], start
        while node in graph and node not in on_path:
            on_path.add(node)
            path.append(node)
            node = graph[node]
        if node in on_path:  # the chain closed back onto itself
            cyc = path[path.index(node):] + [node]
            sig = frozenset(cyc)
            if sig not in reported:
                reported.add(sig)
                issues.append({
                    "check": "circular_chain",
                    "severity": "error",
                    "cycle": " -> ".join(cyc),
                    "message": "circular supersession chain (a superseded note eventually supersedes itself)",
                })
    return issues


def check_orphan_superseded(files_by_subdir):
    """A superseded principle that no position references and that its successor
    does not cite is a candidate for archival -- flag it (advisory)."""
    issues = []
    position_refs = set()
    for info in files_by_subdir.get("positions", {}).values():
        refs = info["frontmatter"].get("principles", [])
        if isinstance(refs, str):
            refs = [refs]
        position_refs.update(str(r) for r in refs)

    by_slug = {slug: info for _subdir, slug, info in _iter_files(files_by_subdir)}

    for slug, info in files_by_subdir.get("principles", {}).items():
        fm = info["frontmatter"]
        tgt = fm.get("superseded_by")
        if not tgt:
            continue
        fid = str(fm.get("id") or "")
        in_position = fid in position_refs or slug in position_refs
        successor = by_slug.get(str(tgt))
        cites_old = False
        if successor:
            srcs = successor["frontmatter"].get("sources", [])
            if isinstance(srcs, str):
                srcs = [srcs]
            srcs = {str(s) for s in srcs}
            cites_old = fid in srcs or slug in srcs
        if not in_position and not cites_old:
            issues.append({
                "check": "orphan_superseded",
                "severity": "warn",
                "file": f"principles/{info['path'].name}",
                "slug": slug,
                "superseded_by": str(tgt),
                "message": (
                    f"superseded principle '{slug}' is neither referenced by a position "
                    f"nor cited by its successor '{tgt}' -- candidate for archival"
                ),
            })
    return issues


def _external_entities(brain_root):
    """Return {ns: set(stems) | None} for each cross-namespace target tree.

    ``brain_root`` is ``<data_root>/knowledge/odin-brain``, so the DATA root is
    two levels up. ``None`` means the tree is absent on this machine (e.g. an
    exec workspace, or a temp brain in tests) -- namespaced refs into it then
    cannot be verified and are NOT flagged."""
    data_root = Path(brain_root).parent.parent
    out = {}
    for ns, rel in _namespace_rels().items():
        d = data_root / rel
        out[ns] = {p.stem for p in d.rglob("*.md")} if d.is_dir() else None
    return out


def check_dangling_wikilinks(files_by_subdir, brain_root):
    """Every free [[wiki-link]] in a note body must resolve -- to a brain note
    (by id, stem, or slugified stem/title, matching the PageRank resolver) or,
    when namespaced, to an existing crm:/thread: entity. Unresolved links are
    warnings, not errors: a [[name]] with no target yet is a legitimate marker,
    but surfacing it keeps the backlog from accumulating unseen."""
    resolver = set()
    for _subdir, slug, info in _iter_files(files_by_subdir):
        fm = info["frontmatter"]
        for tok in (str(fm.get("id") or "").strip(), slug,
                    _slug(slug), _slug(str(fm.get("title") or ""))):
            if tok:
                resolver.add(tok)

    ext = _external_entities(brain_root)
    issues = []
    for subdir, slug, info in _iter_files(files_by_subdir):
        try:
            text = info["path"].read_text(encoding="utf-8")
        except OSError:
            continue
        body = FRONTMATTER_RE.sub("", text, count=1)
        for target in parse_wikilinks(body):
            ns, sep, rest = target.partition(":")
            # Intra-brain type hint ([[source:slug]]): resolve the slug in-brain.
            resolve_key = rest if (sep and ns in BRAIN_TYPE_PREFIXES) else target
            # Cross-tree ref ([[crm:slug]] / [[thread:]] / [[plan:]]): verify the
            # entity exists in its DATA-root tree (skipped when the tree is absent).
            if sep and ns in ext:
                entities = ext[ns]
                if entities is None or rest in entities:
                    continue  # verified, or tree absent (cannot verify)
                issues.append({
                    "check": "dangling_wikilink",
                    "severity": "warn",
                    "file": f"{subdir}/{info['path'].name}",
                    "target": target,
                    "message": f"wiki-link [[{target}]] points to no {ns} entity '{rest}'",
                })
                continue
            if resolve_key in resolver or _slug(resolve_key) in resolver:
                continue
            hint = ""
            for ns2, entities in ext.items():
                if entities and target in entities:
                    hint = f" -- matches a {ns2} entity, did you mean [[{ns2}:{target}]]?"
                    break
            issues.append({
                "check": "dangling_wikilink",
                "severity": "warn",
                "file": f"{subdir}/{info['path'].name}",
                "target": target,
                "message": f"wiki-link [[{target}]] resolves to no brain note{hint}",
            })
    return issues


def lint(brain_root=None):
    """Run all checks and return the list of issues."""
    files_by_subdir, _id, slug_to_file = collect_brain_files(brain_root)
    root = Path(brain_root) if brain_root else BRAIN_ROOT
    issues = []
    issues += check_dangling_references(files_by_subdir, slug_to_file)
    issues += check_circular_chains(files_by_subdir)
    issues += check_orphan_superseded(files_by_subdir)
    issues += check_dangling_wikilinks(files_by_subdir, root)
    return issues


def run_all_checks(brain_root=None, json_output=False):
    root = Path(brain_root) if brain_root else BRAIN_ROOT
    if not root.exists():
        # No brain on this machine (e.g. an exec workspace) -- nothing to lint.
        if json_output:
            print(json.dumps({"total_issues": 0, "errors": [], "warnings": []}))
        else:
            print("No Odin brain on this machine -- nothing to lint.")
        return 0
    issues = lint(root)
    errors = [i for i in issues if i.get("severity") == "error"]
    warns = [i for i in issues if i.get("severity") == "warn"]
    if json_output:
        print(json.dumps(
            {"total_issues": len(issues), "errors": errors, "warnings": warns},
            indent=2, ensure_ascii=False,
        ))
    else:
        print(f"Odin brain temporal-validity lint\nTotal issues: {len(issues)} "
              f"({len(errors)} error, {len(warns)} warn)\n")
        for i in issues:
            print(f"[{i['severity'].upper()}] {i['message']}")
            if "file" in i:
                print(f"  file: {i['file']}")
            if "cycle" in i:
                print(f"  cycle: {i['cycle']}")
        if not issues:
            print("clean.")
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Odin brain temporal-validity lint (R11)")
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    ap.add_argument("--brain-root", default=None, help="override brain root (testing)")
    args = ap.parse_args(argv)
    return run_all_checks(brain_root=args.brain_root, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
