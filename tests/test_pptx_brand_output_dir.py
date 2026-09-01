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


def _read_config_or_fail(cfg: Path) -> str:
    """Read one brand config, or fail naming it. Not a skip.

    The glob and the reads are two moments, and in a checkout several workers
    share a file can go between them. A sweep hunting offenders across hundreds
    of files can skip such a file with a warning, because a file that is gone
    violates nothing.

    This corpus is two permanent, tracked configs and the floor below is one, so
    a silent skip leaves both guards green over HALF the brands while their
    messages still say "no brand config" and "every brand". A miss here is not a
    scratch-file race either; it is an anomaly about a file that belongs in the
    tree. Retried once for a rewrite window, then named.
    """
    try:
        return cfg.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    try:
        return cfg.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AssertionError(
            f"{cfg} vanished between the glob and the read. With two brands in "
            f"the corpus, skipping one would leave this guard reporting on half "
            f"of it in the words of the whole."
        ) from None


def test_no_brand_config_carries_a_repo_relative_output_path():
    from scripts.utils.workspace import get_routing_destination

    configs = sorted(BRANDS.glob("*/config.json"))
    # An empty offenders list is also what zero scanned configs produces, so a
    # renamed brands directory or a moved skill would turn this guard off while
    # it still reported green. Measured 2 configs on 2026-08-26: 31c, template.
    assert len(configs) >= 1, f"the scan collapsed to {len(configs)} files"
    offenders = []
    for cfg in configs:
        directory = (json.loads(_read_config_or_fail(cfg))
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
    configs = sorted(BRANDS.glob("*/config.json"))
    # The assertion below sits inside the loop, so over an empty corpus it never
    # runs and the test reports a pass precisely when the brands directory has
    # gone missing. Measured 2 configs on 2026-08-26: 31c, template.
    assert len(configs) >= 1, f"the scan collapsed to {len(configs)} files"
    for cfg in configs:
        d = json.loads(_read_config_or_fail(cfg))
        assert d.get("output", {}).get("directory"), f"{cfg.parent.name} declares none"
