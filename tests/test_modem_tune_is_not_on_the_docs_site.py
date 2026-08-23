"""The published docs site must not carry the personal-hardware tool.

`/modem-tune` changes the reported IMEI on the operator's own GL.iNet travel
router. Its SKILL.md has always ended with "NEVER document this skill, the
routers, IMEI values, or credentials in corporate or executive-facing files",
and then named exactly three surfaces: `reference/workspace-overview.md`,
`templates/`, and the corporate repo.

`docs/` was not on that list, so nothing stopped a full card appearing at
`docs/skills-operations-infra.html#s-modem-tune`. The 2026-08-23 engine audit
found it there, publishing the router model, the AT command, the ledger path,
and the names of all three `MODEM_*` credential variables, on the site that
carries a search index. The rule said "never document this" on the same page
that documented it.

Operator decision, 2026-08-23: remove the page, do not publish it.

Scope, stated so this test is not read as more than it is. The skill folder and
`scripts/modem-tune.py` remain in the public engine repository, by the
2026-06-14 routing decision recorded in `config/routing-map.yaml` and pinned by
`tests/test_routing_map.py::test_modem_tune_code_is_engine_only_device_identity_private`:
the CODE ships as engine like every other script, and only the device identity
(`config/modem.json`) is private data. This test governs the rendered
documentation site alone. It asserts nothing about repository visibility, and
removing the page does not make the tool secret.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SKILL = ROOT / ".claude" / "skills" / "modem-tune" / "SKILL.md"
SEARCH_INDEX = DOCS / "assets" / "search-index.json"

# Every term the removed card disclosed. Case-insensitive: a rewritten card
# would not reuse the old capitalization.
DISCLOSURES = (
    "modem-tune",
    "modem.json",
    "MODEM_HOST",
    "MODEM_USER",
    "MODEM_SSH_PASSWORD",
    "GL-XE300",
    "GL-E5800",
    "AT+EGMR",
    "modem-imei-ledger",
)


def _site_files() -> list[Path]:
    """Everything the docs site publishes: rendered pages, their Markdown
    sources, and the generated assets."""
    return [p for p in sorted(DOCS.rglob("*"))
            if p.is_file() and p.suffix in {".html", ".md", ".json", ".js"}]


@pytest.mark.parametrize("term", DISCLOSURES)
def test_no_docs_page_names_the_tool_or_its_credentials(term):
    needle = term.lower()
    hits = []
    for path in _site_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert hits == [], (
        f"the published docs site names {term!r} at {hits}. The operator "
        "removed this tool from the site on 2026-08-23; the skill folder stays "
        "in the repository, the documentation site does not carry it."
    )


def test_the_word_imei_appears_nowhere_on_the_site():
    """Broader than the term list: the concept, however it gets phrased. The
    site has no other reason to say IMEI."""
    hits = [str(p.relative_to(ROOT)) for p in _site_files()
            if "imei" in p.read_text(encoding="utf-8", errors="ignore").lower()]
    assert hits == [], f"docs site mentions IMEI in {hits}"


def test_the_search_index_carries_no_record_for_the_removed_anchor():
    """The index is generated from the HTML, so a stale one means the rebuild
    was skipped and the card is still reachable through the search box."""
    records = json.loads(SEARCH_INDEX.read_text(encoding="utf-8"))
    bad = [r for r in records
           if "modem" in json.dumps(r).lower() or "imei" in json.dumps(r).lower()]
    assert bad == [], (
        f"search-index.json still indexes the removed card: {bad}. Rebuild with "
        "`.venv/bin/python scripts/regenerate-docs-html.py --search-index`."
    )


def test_no_page_links_to_the_removed_anchor():
    """A dead `#s-modem-tune` link is worse than the card: it advertises the
    tool and 404s inside the page."""
    dangling = re.compile(r'href="[^"]*#s-modem-tune"')
    hits = [str(p.relative_to(ROOT)) for p in _site_files()
            if dangling.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == [], f"dangling links to the removed card: {hits}"


# --- the rule that let this happen must now name the surface -------------------

def test_the_skill_forbids_the_docs_site_by_name():
    """The original rule listed three surfaces and the docs site was not one of
    them, which is exactly why the card was written. Naming the surface is the
    durable half of the fix; this test is the mechanical half."""
    text = SKILL.read_text(encoding="utf-8")
    assert "NEVER document this skill" in text, (
        "the skill lost its non-disclosure rule entirely"
    )
    rule = text[text.index("NEVER document this skill"):]
    rule = rule[:rule.index("\n- ")]
    assert "docs/" in rule, (
        "the non-disclosure rule does not name `docs/`, so the next person "
        "writing a catalog card has nothing telling them to skip this skill"
    )


def test_the_skill_still_states_the_code_ships_public():
    """Guards the opposite error: someone reading 'do not document' and
    concluding the script should be moved out of the engine repo. That
    contradicts the 2026-06-14 NO EXCEPTIONS routing decision."""
    text = SKILL.read_text(encoding="utf-8")
    assert "public engine" in text and "2026-06-14" in text


# --- the detector must be able to fail -----------------------------------------

def test_the_scan_actually_reads_the_site(tmp_path):
    """A glob that matches nothing makes every test above pass. Pin that the
    site is there and non-trivial."""
    files = _site_files()
    assert len(files) > 30, f"only found {len(files)} docs files"
    assert any(p.name == "skills-operations-infra.html" for p in files), (
        "the page the card lived on is missing from the scan"
    )
