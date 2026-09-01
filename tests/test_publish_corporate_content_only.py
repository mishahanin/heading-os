#!/usr/bin/env python3
"""Cutover invariant: the corporate publish set is content-only, never code.

HEADING OS step 8 (cutover, 2026-06-14): publish-corporate ships ONLY files
whose three-value routing destination is 'corporate'. Engine code is NOT
published through 31c-corporate anymore — execs receive it by cloning the engine
repo (.heading-os). This test pins that boundary against the LIVE routing map:
no git-tracked file routed 'corporate' may live under a code-ish prefix
(scripts/, .claude/, tests/, docs/, config/, reference/, templates/).

A future routing-map.yaml edit that accidentally routed a script or rule
'corporate' would ship code through the content channel — this test catches it.

Resilient across layouts: in a data-less engine clone the corporate-routed set
is EXACTLY empty (measured 2026-09-01: 2126 tracked files, all `engine`), so the
assertion below holds vacuously there; in the operator's workspace it guards the
real content set (datastore/, knowledge/shared/, the context carve-outs, crm
config).

That vacuity was the whole defect until 2026-09-01, and it is the shape the
coordinator named on `scripts/leak-guard.py::check_paths` the same day: a filter
whose EMPTY result is reported as success. `corporate` is built by asking the
resolver a question, and every way of breaking the resolver produces the empty
list that reads as a clean tree. MEASURED here, by substituting the resolver in
this module's namespace and calling the test directly:

    resolver returns "private" for everything   (a broken routing-map.yaml,
      which `load_routing_map` deliberately fails CLOSED to)   -> PASSED
    resolver returns "engine" for everything    (a resolver stuck on one answer) -> PASSED
    `_tracked_files()` returns []               (git answered with nothing)      -> PASSED

Three switches, no witness, and the third is the one the engine repo runs on
every push. So the corpus floor and the resolver canary below are not decoration:
they are what makes "no corporate-routed code path" mean anything at all. The
canary asks the resolver a question whose answer is fixed by
`config/routing-map.yaml` and checkable here, so a resolver that can no longer
say "corporate" fails LOUDLY instead of certifying an empty list.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_routing_destination, get_workspace_root  # noqa: E402

# Every top-level tracked directory that carries code or engine configuration.
# `.github/`, `.githooks/`, `.devcontainer/`, `.vscode/` and `examples/` joined
# the list on 2026-09-01: they were tracked all along (measured: 9, 2, 2, 1 and
# 7 files) and a routing-map edit that sent a workflow or a git hook down the
# content channel would have passed a guard that never looked at them.
CODE_PREFIXES = (
    "scripts/", ".claude/", "tests/", "docs/", "config/", "reference/", "templates/",
    ".github/", ".githooks/", ".devcontainer/", ".vscode/", "examples/",
)

# Paths whose routing destination is fixed by `config/routing-map.yaml` and is
# the thing this guard asks about. Not a sample of the tree: these are asked of
# the resolver directly, so the canary holds on a data-less clone where none of
# them is tracked.
_CANARY = {
    "datastore/brand/templates/doctypes/letter.html": "corporate",
    "knowledge/shared/a-shared-note.md": "corporate",
    "scripts/push-all.py": "engine",
    ".claude/rules/voice.md": "engine",
}


def _tracked_files() -> list[str]:
    root = get_workspace_root()
    # `-z`: git C-quotes any path with a byte outside printable ASCII, and a
    # quoted name matches no routing rule, so it falls to the map default and
    # this boundary guard never sees it. The publisher had the same defect until
    # 2026-08-27; a guard blind to the same files is not a guard.
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(root),
        capture_output=True, text=True, check=True,
    )
    return [rel for rel in result.stdout.split("\0") if rel]


def test_the_resolver_can_still_answer_corporate():
    """The canary. Without it, a resolver that has stopped saying 'corporate'
    certifies the tree by producing nothing to object to.

    Both directions, because a resolver stuck on ONE answer passes a one-sided
    check: 'corporate' must still be reachable, and a code path must still come
    back 'engine' rather than being swept into the same answer.
    """
    answers = {path: get_routing_destination(path) for path in _CANARY}
    wrong = {p: got for p, got in answers.items() if got != _CANARY[p]}
    assert not wrong, (
        "the routing resolver no longer answers as config/routing-map.yaml says, "
        "so an empty corporate set below proves nothing about the tree: "
        f"{wrong} (expected {_CANARY})"
    )


def test_the_tracked_corpus_is_not_empty():
    """A guard is green over an empty corpus. `_tracked_files()` is the only
    input to the boundary check, so its size is the floor that check rests on."""
    files = _tracked_files()
    assert len(files) >= 500, (
        f"only {len(files)} tracked file(s); the boundary guard below measured "
        "almost nothing"
    )


def test_the_boundary_predicate_flags_a_code_path_routed_corporate():
    """The positive control: prove the `leaked` filter can still fire.

    The real assertion is 'no offenders found', which stays true when the filter
    itself is broken (an empty CODE_PREFIXES, a `startswith` that stopped
    matching). This runs the same predicate over an INVENTED path that is
    exactly the defect the guard is named for.
    """
    invented = ["scripts/some-new-tool.py", ".github/workflows/ci.yml",
                "datastore/brand/logo.svg"]
    leaked = [p for p in invented if p.startswith(CODE_PREFIXES)]
    assert leaked == ["scripts/some-new-tool.py", ".github/workflows/ci.yml"], leaked


def test_corporate_publish_set_has_no_code_paths():
    corporate = [p for p in _tracked_files() if get_routing_destination(p) == "corporate"]
    leaked = [p for p in corporate if p.startswith(CODE_PREFIXES)]
    assert not leaked, (
        "code-ish paths routed 'corporate' would ship through 31c-corporate "
        f"post-cutover: {leaked}"
    )
