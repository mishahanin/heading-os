"""Tests for the volatile-pointer guard (memory-discipline enforcement).

`scan_volatile_hooks` is HIGH-PRECISION and ADVISORY. It must flag MEMORY.md
index hooks AND memory-file `description:` frontmatter that quote live money state
(the stale-money-hook failure class), and must NOT flag stable descriptors —
including the tricky k/M-suffix SPEC class (`128k context`, `5K display`,
`i9-13900K`, `1M-context`) that carries a magnitude token but no money context.
These tests pin both directions on a synthetic corpus so a future regex tweak
cannot silently regress the false-positive floor. Recall is deliberately partial
(money class only); non-money volatile prose is the principle's job, not this
guard's.

All fixtures are fully fictional — no operator-private entities or figures.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.memory_health import scan_volatile_hooks  # noqa: E402

# MUST flag — live money state (currency, or a magnitude WITH a money-context word).
VOLATILE_HOOKS = [
    "- [Cottage purchase](cottage-purchase.md) — live offer 640k, true max ~660k, seller 700k firm.",
    "- [Workshop loan — Riverside Bank](workshop-loan-riverside.md) — €480k/85% LTV/22y via banker.",
    "- [Series-B pipeline](series-b-pipeline.md) — 2M pipeline value under discussion.",
    "- [Retainer terms](retainer-terms.md) — USD 500 monthly retainer agreed.",
]

# MUST NOT flag — stable descriptors, INCLUDING k/M-suffix spec magnitudes that
# have no money-context word.
STABLE_HOOKS = [
    "- [Repo is now PUBLIC](repo-now-public.md) — example/project PUBLIC; v0.6.0.",
    "- [MCP node path](mcp-node-path.md) — CLI 2.1.160; absolute node path.",
    "- [Vendor review — remind late August](vendor-review-remind.md) — silent until ~2026-08-25.",
    "- [Doc versions](doc-versions.md) — marker date not older than 90 days; bump on edit.",
    "- [Multi-user ecosystem](multi-user-ecosystem.md) — hub-and-spoke for 10-15 users.",
    "- [Push gate parallelized](push-gate.md) — pre-push gate ~240s to ~92s via xdist.",
    "- [Laptop hardware](laptop-hardware.md) — 8-core CPU, no GPU, local ceiling ~7-8B.",
    "- [Wide mode](wide-mode.md) — opt-in 1M-context wide mode for the review voice.",
    "- [LLM context window](llm-context.md) — 128k context window; i9-13900K test box; 5K display; 10k RPM disk.",
]

# threads/ path pointers are OUT of scope even when they carry money.
THREAD_HOOKS = [
    "- [Vendor demo](threads/business/2026-06-20-vendor-demo.md) - €50k pilot budget offer discussed.",
]


def _write_memory(tmp_path: Path, hook_lines) -> Path:
    mem_dir = tmp_path / "auto-memory"
    mem_dir.mkdir()
    body = "# Memory index\n\n" + "\n".join(hook_lines) + "\n"
    (mem_dir / "MEMORY.md").write_text(body, encoding="utf-8")
    return mem_dir


def _write_memory_file(mem_dir: Path, name: str, description: str) -> None:
    (mem_dir / name).write_text(
        f'---\nname: {name[:-3]}\ndescription: "{description}"\nmetadata:\n  type: project\n---\n\nbody.\n',
        encoding="utf-8",
    )


def test_flags_volatile_money_hooks(tmp_path):
    mem_dir = _write_memory(tmp_path, VOLATILE_HOOKS)
    result = scan_volatile_hooks(mem_dir)
    assert result["ok"] is True
    targets = {f["target"] for f in result["flagged"]}
    assert targets == {
        "cottage-purchase.md",
        "workshop-loan-riverside.md",
        "series-b-pipeline.md",
        "retainer-terms.md",
    }
    for f in result["flagged"]:
        assert f["signals"], "a flagged hook must record why it flagged"


def test_does_not_flag_stable_descriptors(tmp_path):
    mem_dir = _write_memory(tmp_path, STABLE_HOOKS)
    result = scan_volatile_hooks(mem_dir)
    assert result["flagged"] == [], (
        "stable descriptors incl. k/M-suffix spec magnitudes (128k context, 5K "
        f"display, i9-13900K, 1M-context) must not flag; got {result['flagged']}"
    )


def test_ignores_thread_path_pointers(tmp_path):
    mem_dir = _write_memory(tmp_path, THREAD_HOOKS)
    result = scan_volatile_hooks(mem_dir)
    assert result["flagged"] == [], "threads/ pointers are out of scope"


def test_mixed_corpus_precision(tmp_path):
    mem_dir = _write_memory(tmp_path, VOLATILE_HOOKS + STABLE_HOOKS + THREAD_HOOKS)
    result = scan_volatile_hooks(mem_dir)
    assert len(result["flagged"]) == 4, [f["line"] for f in result["flagged"]]


def test_scans_frontmatter_descriptions(tmp_path):
    mem_dir = _write_memory(tmp_path, ["- [Clean](clean.md) — a clean topic hook."])
    _write_memory_file(mem_dir, "deal.md", "live offer 640k, seller 700k firm - lakeside cottage")
    _write_memory_file(mem_dir, "clean.md", "topic-only description, numbers live in the body")
    result = scan_volatile_hooks(mem_dir)
    flagged_files = {f["file"] for f in result["flagged_descriptions"]}
    assert flagged_files == {"deal.md"}, result["flagged_descriptions"]


def test_a_description_line_outside_frontmatter_is_not_a_description(tmp_path):
    """`_extract_description` reads a FRONTMATTER value, not any matching line.

    The reader returns "" unless the file opens with `---`, so prose that
    happens to start a line with `description:` in the body is not a
    pointer-layer summary and must not be scanned as one. Nothing measured that
    until 2026-09-01: with the header check removed, this file stayed green,
    because every fixture it wrote carried frontmatter. The guard is what keeps
    an advisory, precision-first scanner from flagging body text -- which is the
    place the rule says a live number BELONGS.
    """
    mem_dir = _write_memory(tmp_path, ["- [Clean](clean.md) — a clean topic hook."])
    (mem_dir / "prose.md").write_text(
        "# A note with no frontmatter\n\n"
        "description: live offer 640k, seller 700k firm\n",
        encoding="utf-8",
    )
    _write_memory_file(mem_dir, "real.md", "live offer 640k, seller 700k firm")

    result = scan_volatile_hooks(mem_dir)

    flagged_files = {f["file"] for f in result["flagged_descriptions"]}
    assert flagged_files == {"real.md"}, result["flagged_descriptions"]


def test_missing_memory_md_is_clean(tmp_path):
    mem_dir = tmp_path / "auto-memory"
    mem_dir.mkdir()
    result = scan_volatile_hooks(mem_dir)
    assert result["ok"] is True
    assert result["flagged"] == []
    assert result["flagged_descriptions"] == []
