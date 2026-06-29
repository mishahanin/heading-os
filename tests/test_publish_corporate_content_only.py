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
is near-empty, so the assertion holds vacuously; in ceo-main it guards the real
content set (datastore/, knowledge/shared/, the context carve-outs, crm config).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_routing_destination, get_workspace_root  # noqa: E402

CODE_PREFIXES = (
    "scripts/", ".claude/", "tests/", "docs/", "config/", "reference/", "templates/",
)


def _tracked_files() -> list[str]:
    root = get_workspace_root()
    result = subprocess.run(
        ["git", "ls-files"], cwd=str(root),
        capture_output=True, text=True, check=True,
    )
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def test_corporate_publish_set_has_no_code_paths():
    corporate = [p for p in _tracked_files() if get_routing_destination(p) == "corporate"]
    leaked = [p for p in corporate if p.startswith(CODE_PREFIXES)]
    assert not leaked, (
        "code-ish paths routed 'corporate' would ship through 31c-corporate "
        f"post-cutover: {leaked}"
    )
