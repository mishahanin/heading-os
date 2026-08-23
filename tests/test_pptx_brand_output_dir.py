"""A brand's output directory must not resolve into the engine repo.

`references/generation-workflow.md` warns by name: "A bare `output/{brand}`
relative path would resolve into the engine root". Until 2026-08-23 both brand
configs carried exactly such a path -- `output/{brand}` in the template and
`outputs/deliverables/presentations` in 31c -- so an agent that honoured the
config wrote generated PPTX artifacts into the public engine tree.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BRANDS = ROOT / ".claude" / "skills" / "pptx-generator" / "brands"


def test_no_brand_config_carries_a_repo_relative_output_path():
    from scripts.utils.workspace import get_routing_destination

    offenders = []
    for cfg in sorted(BRANDS.glob("*/config.json")):
        directory = (json.loads(cfg.read_text(encoding="utf-8"))
                     .get("output", {}).get("directory", ""))
        if not directory:
            continue
        # A caller-resolved marker or an absolute path is fine. A bare relative
        # path is what lands in the engine root.
        if directory.startswith(("$", "/", "~")):
            continue
        offenders.append((cfg.parent.name, directory,
                          get_routing_destination(directory)))
    assert not offenders, (
        "brand config(s) name a repo-relative output directory, which resolves "
        f"into the engine tree: {offenders}")


def test_every_brand_declares_an_output_directory():
    """An absent value is not a pass -- it just moves the guess to the caller."""
    for cfg in sorted(BRANDS.glob("*/config.json")):
        d = json.loads(cfg.read_text(encoding="utf-8"))
        assert d.get("output", {}).get("directory"), f"{cfg.parent.name} declares none"
