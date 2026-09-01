#!/usr/bin/env python3
"""Shard 54: the digest that read a card the schema had moved out from under it.

`scripts/email-intelligence.py::load_crm_contacts` held a FIFTH private
frontmatter parser -- a hand-rolled line splitter with the "three characters,
not a line" fence defect that shard 52 fixed in three other copies. Its name is
not a `parse_frontmatter` spelling, so the anti-duplication sweep in
tests/test_markdown_frontmatter_single_source.py had never seen it. That is the
second time a name-keyed detector missed the copy carrying the defect, so this
file adds a detector keyed on the SHAPE instead.

The parser was not the biggest thing wrong with it. Reading the relationship
card's own frontmatter skips the ENTITY MERGE, and the CRM moved to that model:
`company`, `type` and often `email` live on the address-book entity. MEASURED
2026-08-28 over the live 169 cards, the old reader against `scan_contacts`:

    contacts found by email      89   ->  144
    blank `company`              87   ->    0
    blank `type`                 89   ->    0

Live on every run, not a latent shape.

This file also finishes the date-reader family shard 53 opened: the last three
readers that could not read their own input, and the ratchet that keeps the
count honest.

2026-09-01 adds the dashboard's own reads, in section C. `collect_capture_payoff`
dropped a note it could not open through `except Exception: return False` with
nothing printed, the viraid panel's handler named only JSONDecodeError and
OSError, and `collect_freshness` read four context files inside a loop with no
`try` at all -- so one undecodable file killed the whole render, which is
verbatim what the impossible-date branch four lines below it exists to prevent.
All three were reproduced before the fix.

Example data is invented throughout. No real entity appears in this file.
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.markdown import frontmatter_date  # noqa: E402
from tests.repo_files import tracked_python_files  # noqa: E402

PY = sys.executable


# ============================================================
# A fixture data overlay -- invented content only
# ============================================================

ENTITY = """---
slug: {slug}
name: {name}
canonical_email: {email}
employer: {employer}
created: 2020-01-01
---

# {name}
"""

RELATIONSHIP = """---
entity_ref: {slug}
relationship_type: {rtype}
type: {rtype}
last_touch: {last_touch}
created: 2020-01-01
cadence: 30
owner: operator
---

## Active Commitments

## Interaction Log
"""

INLINE_CARD = """---
name: {name}
email: {email}
company: {company}
type: {rtype}
last_touch: {last_touch}
---

# {name}
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def overlay(tmp_path):
    """A data overlay laid out like the real one, holding invented records."""
    data = tmp_path / "data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "crm" / "address-book").mkdir(parents=True)
    (data / "context").mkdir()
    (data / "knowledge").mkdir()
    _write(data / "crm" / "config.md", "## Config\n\npartner: 30\n")
    _write(data / "crm" / "aliases.md", "# Aliases\n")
    _write(data / "context" / "pipeline.md", "# Pipeline\n")
    return data


def _run_driver(data: Path, code: str) -> subprocess.CompletedProcess:
    """Run `code` in a child that resolves the fixture overlay, not the real one."""
    env = dict(os.environ, HEADING_OS_DATA=str(data), PYTHONPATH=str(ROOT))
    env.pop("HEADING_OS_TZ", None)
    env.pop("CRM_WORKSPACE_ROOT", None)
    return subprocess.run([PY, "-c", code], capture_output=True, text=True,
                          env=env, cwd=str(ROOT), timeout=180)


_LOAD_DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ei", "scripts/email-intelligence.py")
EI = importlib.util.module_from_spec(spec); spec.loader.exec_module(EI)
m = EI.load_crm_contacts()
print(json.dumps({k: {kk: str(vv) for kk, vv in v.items()
                      if kk in ("name", "company", "type", "last_touch", "slug")}
                  for k, v in m.items()}))
"""


def _load_contacts(data: Path) -> dict:
    proc = _run_driver(data, _LOAD_DRIVER)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ============================================================
# A -- the digest sees the entity, not just the card
# ============================================================

def test_a_contact_whose_email_lives_on_the_entity_is_found(overlay):
    """The 55 the old reader could not see.

    A relationship record carries `entity_ref` and no `email:` of its own. The
    hand-rolled parser read only the card, so the contact was absent from the
    map and every conversation with them was enriched with nothing.
    """
    _write(overlay / "crm" / "address-book" / "jane-bond.md",
           ENTITY.format(slug="jane-bond", name="Jane Bond",
                         email="jane.bond@universal-exports.invalid",
                         employer="Universal Exports"))
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           RELATIONSHIP.format(slug="jane-bond", rtype="partner",
                               last_touch="2020-01-05"))
    got = _load_contacts(overlay)
    assert "jane.bond@universal-exports.invalid" in got, got


def test_company_and_type_come_through_the_entity_merge(overlay):
    """Blank on 87 and 89 of the live cards respectively, before this change."""
    _write(overlay / "crm" / "address-book" / "jane-bond.md",
           ENTITY.format(slug="jane-bond", name="Jane Bond",
                         email="jane.bond@universal-exports.invalid",
                         employer="Universal Exports"))
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           RELATIONSHIP.format(slug="jane-bond", rtype="partner",
                               last_touch="2020-01-05"))
    rec = _load_contacts(overlay)["jane.bond@universal-exports.invalid"]
    assert rec["name"] == "Jane Bond"
    assert rec["company"] == "Universal Exports"
    assert rec["type"] == "partner"


def test_a_readme_carrying_frontmatter_is_not_a_contact(overlay):
    """The glob excluded nothing at all -- a fourth copy of that question."""
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           INLINE_CARD.format(name="Jane Bond", email="jane@universal-exports.invalid",
                              company="Universal Exports", rtype="partner",
                              last_touch="2020-01-05"))
    _write(overlay / "crm" / "contacts" / "README.md",
           INLINE_CARD.format(name="How to use this folder",
                              email="readme@universal-exports.invalid",
                              company="n/a", rtype="partner", last_touch="2020-01-05"))
    got = _load_contacts(overlay)
    assert "jane@universal-exports.invalid" in got
    assert "readme@universal-exports.invalid" not in got, "the README was loaded as a contact"


def test_dashes_inside_a_value_no_longer_truncate_the_card(overlay):
    """`text.find("---", 3)` cut the block at the dashes and lost every key after.

    The digest then had no company to look up in the pipeline and no date to age
    the relationship against, on a card that is valid YAML.
    """
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           INLINE_CARD.format(name="Jane --- Bond",
                              email="jane@universal-exports.invalid",
                              company="Universal Exports", rtype="partner",
                              last_touch="2020-01-05"))
    rec = _load_contacts(overlay)["jane@universal-exports.invalid"]
    assert rec["name"] == "Jane --- Bond"
    assert rec["company"] == "Universal Exports"
    assert rec["last_touch"] == "2020-01-05"


def test_the_email_key_is_normalised_before_it_becomes_a_lookup_key(overlay):
    """The map is keyed by address, and the lookup side lower-cases nothing.

    `enrich_conversation` does `crm_map.get(p["email"])` with the address as the
    mail server gave it. A card written `Jane.Bond@Example.invalid` (or with a
    trailing space) would key the map under a string no lookup ever produces, so
    the contact silently has no CRM context at all.
    """
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           INLINE_CARD.format(name="Jane Bond", email="  Jane.Bond@Example.INVALID  ",
                              company="Universal Exports", rtype="partner",
                              last_touch="2020-01-05"))
    got = _load_contacts(overlay)
    assert list(got) == ["jane.bond@example.invalid"], got


def test_the_private_parser_is_gone_from_the_loader():
    """No fence splitting, no line splitting, inside this function any more."""
    src = (ROOT / "scripts" / "email-intelligence.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "load_crm_contacts")
    fence_calls = [
        ast.unparse(n) for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("find", "startswith", "split")
        and any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                and a.value.startswith("---") for a in n.args)
    ]
    assert fence_calls == [], f"the private parser is back: {fence_calls}"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "scan_contacts" in called, "the loader stopped going through the CRM scan"


# ============================================================
# B -- the digest's own date reader
# ============================================================

_ENRICH_DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ei", "scripts/email-intelligence.py")
EI = importlib.util.module_from_spec(spec); spec.loader.exec_module(EI)
m = EI.load_crm_contacts()
addr = sorted(m)[0]
conv = {"participants": [{"email": addr}], "topic": "quarterly review"}
out = EI.enrich_conversation(conv, m, "", {})
print(json.dumps(out["crm_context"], default=str))
"""


@pytest.mark.parametrize("last_touch,expect_age", [
    ("2020-01-05", True),
    # A value carrying a time. `date.fromisoformat(str(...))` refused it, and the
    # bare `pass` left the digest showing the raw value beside no age at all.
    ('"2020-01-05 09:30:00"', True),
    ("not-a-date", False),
])
def test_the_digest_ages_a_contact_whatever_shape_the_date_is(overlay, last_touch, expect_age):
    _write(overlay / "crm" / "contacts" / "jane-bond.md",
           INLINE_CARD.format(name="Jane Bond", email="jane@universal-exports.invalid",
                              company="Universal Exports", rtype="partner",
                              last_touch=last_touch))
    proc = _run_driver(overlay, _ENRICH_DRIVER)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)
    if expect_age:
        assert isinstance(ctx["days_since"], int), ctx
    else:
        assert ctx["days_since"] is None
        assert "unreadable `last_touch`" in proc.stderr, "the failure was swallowed"
        assert "not-a-date" in proc.stderr, "the warning did not name the value"


def test_a_tribe_contact_still_gets_an_age(overlay):
    """The digest asks a different question from the radar.

    `scan_contacts` returns `days_since: None` for a no-cadence type, because the
    radar does not track them. "How long since we spoke" still has an answer, so
    the digest computes its own rather than taking the scan's.
    """
    _write(overlay / "crm" / "contacts" / "sam-tribe.md",
           INLINE_CARD.format(name="Sam Tribe", email="sam@example.invalid",
                              company="Example Co", rtype="tribe",
                              last_touch="2020-01-05"))
    proc = _run_driver(overlay, _ENRICH_DRIVER)
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)
    assert ctx["type"] == "tribe"
    assert isinstance(ctx["days_since"], int), "the digest lost the age for a tribe contact"


# ============================================================
# C -- the dashboard's capture panel
# ============================================================

ZK_BRAIN_NOTE = """---
id: "{nid}"
title: {title}
type: source
format: fleeting
author: J. Bond
ingested: {ingested}
confidence: medium
keywords: [gadget]
status: seed
created: {created}
---

# {title}
"""

_PAYOFF_DRIVER = """
import importlib.util, json
spec = importlib.util.spec_from_file_location("gd", "scripts/generate-dashboard.py")
GD = importlib.util.module_from_spec(spec); spec.loader.exec_module(GD)
print(json.dumps(GD.collect_capture_payoff(), default=str))
"""


def _payoff_overlay(overlay: Path, notes: dict) -> Path:
    brain = overlay / "knowledge" / "odin-brain"
    for sub in ("sources", "principles", "positions", "episodes", "conflicts", "reference"):
        (brain / sub).mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        _write(brain / "sources" / name, text)
    return overlay


def test_a_broken_date_is_not_counted_as_a_captured_signal(overlay):
    """`str(val)[:10]` read "2026-08-25garbage" as a date and counted the note.

    MEASURED 2026-08-28: that is the only input on which the slice and the
    shared coercion disagree, and it disagrees in the direction that INFLATES
    the number the panel exists to report.
    """
    _payoff_overlay(overlay, {
        "broken.md": ZK_BRAIN_NOTE.format(nid="1", title="Broken",
                                          ingested="2020-01-01",
                                          created='"2999-01-01garbage"'),
    })
    proc = _run_driver(overlay, _PAYOFF_DRIVER)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["available"] is True
    assert out["signals_week"] == 0, "a note with no readable date was counted"
    assert "no readable date" in proc.stderr, "the drop was silent"
    assert "broken.md" in proc.stderr, "the warning did not name the note"


def test_the_fallback_chain_still_reaches_a_later_field(overlay):
    """An old `created` must not stop `ingested` from being consulted."""
    today = dt.date.today()          # noqa: DTZ011 - the panel's own window is host-relative
    _payoff_overlay(overlay, {
        "fresh.md": ZK_BRAIN_NOTE.format(nid="2", title="Fresh via ingested",
                                         ingested=today.isoformat(),
                                         created="2020-01-01"),
    })
    proc = _run_driver(overlay, _PAYOFF_DRIVER)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["signals_week"] == 1
    assert "no readable date" not in proc.stderr


def test_an_unreadable_field_does_not_stop_a_later_readable_one(overlay):
    """The other half of the chain: `continue`, not `break`.

    A note whose `created` is garbage but whose `ingested` is this week IS a
    signal captured this week. Stopping at the first unreadable field loses it,
    and the loss is indistinguishable from a quiet week.
    """
    today = dt.date.today()          # noqa: DTZ011 - the panel's own window is host-relative
    _payoff_overlay(overlay, {
        "mixed.md": ZK_BRAIN_NOTE.format(nid="3", title="Broken created, fresh ingested",
                                         ingested=today.isoformat(),
                                         created='"not-a-date"'),
    })
    proc = _run_driver(overlay, _PAYOFF_DRIVER)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["signals_week"] == 1, (
        "an unreadable earlier field stopped a readable later one")
    assert "no readable date" not in proc.stderr, (
        "a note that WAS dated must not be reported as undated")


def test_a_note_the_reader_cannot_decode_is_reported_not_silently_dropped(overlay):
    """`except Exception: return False`, with nothing printed.

    The date branch fifteen lines below it says "no readable date" and names the
    file. The read branch above it said nothing at all, so a cp1251 note left
    "Signals Captured (7d)" one lower with no trace anywhere. Measured
    2026-09-01: `UnicodeDecodeError` took that silent path, which is the same
    undercount the noisy branch exists to prevent, by the other door.

    The second note is the anchor: a reader that gave up on the whole tree at
    the first bad file would report 0 here, and the point is that it reports 1.
    """
    today = dt.date.today()          # noqa: DTZ011 - the panel's own window is host-relative
    _payoff_overlay(overlay, {
        "fresh.md": ZK_BRAIN_NOTE.format(nid="4", title="Fresh",
                                         ingested=today.isoformat(),
                                         created=today.isoformat()),
    })
    bad = overlay / "knowledge" / "odin-brain" / "sources" / "cp1251.md"
    bad.write_bytes(b"---\ntitle: \xcf\xf0\xe8\xed\xf6\xe8\xef\ncreated: 2020-01-01\n---\n")
    with pytest.raises(UnicodeDecodeError):
        bad.read_text(encoding="utf-8")

    proc = _run_driver(overlay, _PAYOFF_DRIVER)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["signals_week"] == 1
    assert "cp1251.md" in proc.stderr, "the dropped note was not named"
    assert "unreadable" in proc.stderr, proc.stderr


_FRESHNESS_DRIVER = """
import importlib.util, json
spec = importlib.util.spec_from_file_location("gd", "scripts/generate-dashboard.py")
GD = importlib.util.module_from_spec(spec); spec.loader.exec_module(GD)
print(json.dumps(GD.collect_freshness(), default=str))
"""


def test_one_unreadable_context_file_degrades_one_row_not_the_whole_run(overlay):
    """The read in this loop had no `try` at all, so an AST sweep for a bad
    handler cannot see it.

    Four lines below it, the impossible-date branch exists because a single bad
    line "used to raise here and kill the whole run: no dashboard at all, from
    one bad line in one of four files". An undecodable context file did exactly
    that, by the door with no handler on it. Measured 2026-09-01: the driver
    exited non-zero with a UnicodeDecodeError and printed no JSON.

    `strategy.md` carries a valid marker and is checked in the same call, so a
    reader that gave up on the first bad file would lose it.
    """
    (overlay / "context" / "pipeline.md").write_bytes(
        b"# Pipeline\nLast verified: 2020-01-05\n\xff\xfe\x00\n")
    (overlay / "context" / "strategy.md").write_text(
        "# Strategy\nLast verified: 2020-01-05\n", encoding="utf-8")

    proc = _run_driver(overlay, _FRESHNESS_DRIVER)

    assert proc.returncode == 0, proc.stderr
    rows = {r["name"]: r for r in json.loads(proc.stdout)}
    assert rows["pipeline.md"]["health"] == "gray"
    assert rows["pipeline.md"]["date"] is None
    assert rows["strategy.md"]["date"] == "2020-01-05", (
        "a readable file after the bad one was lost")
    assert "pipeline.md is unreadable" in proc.stderr, proc.stderr


def test_a_readable_context_file_is_not_reported_as_unreadable(overlay):
    """The anchor. A branch that greyed out every row would pass above, and a
    warning on every clean render is noise rather than a signal."""
    (overlay / "context" / "pipeline.md").write_text(
        "# Pipeline\nLast verified: 2020-01-05\n", encoding="utf-8")

    proc = _run_driver(overlay, _FRESHNESS_DRIVER)

    assert proc.returncode == 0, proc.stderr
    rows = {r["name"]: r for r in json.loads(proc.stdout)}
    assert rows["pipeline.md"]["date"] == "2020-01-05"
    assert rows["pipeline.md"]["health"] != "gray"
    assert "unreadable" not in proc.stderr, proc.stderr


_VIRAID_DRIVER = """
import importlib.util, json
spec = importlib.util.spec_from_file_location("gd", "scripts/generate-dashboard.py")
GD = importlib.util.module_from_spec(spec); spec.loader.exec_module(GD)
print(json.dumps(GD.collect_viraid(), default=str))
"""


@pytest.mark.parametrize("payload, rate_known", [
    (b"\xff\xfe\x00{", False),
    (b"{not json", False),
    (b'{"stats": {"completion_rate": 0.5}}', True),
])
def test_the_viraid_panel_degrades_on_a_state_file_it_cannot_read(
        overlay, payload, rate_known):
    """`except (json.JSONDecodeError, OSError)` cannot catch a decode failure.

    `read_text(encoding="utf-8")` raises `UnicodeDecodeError`, a `ValueError`,
    before `json.loads` is handed anything, so the undecodable row escaped the
    handler this reader grew specifically to stop a corrupt state.json drawing a
    measured-looking 0%. The last row is the anchor.
    """
    state = overlay / "outputs" / "operations" / "viraid" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_bytes(payload)

    proc = _run_driver(overlay, _VIRAID_DRIVER)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["rate_known"] is rate_known
    if not rate_known:
        assert "state.json unreadable" in proc.stderr, proc.stderr


# ============================================================
# D -- the newsletter masthead
# ============================================================

def test_an_iso_datetime_renders_as_a_formatted_masthead_date(overlay):
    """`date.fromisoformat` takes date forms only, so a timestamped issue used to
    print its raw ISO string where the masthead date belongs."""
    html = _newsletter(overlay, {"date": "2020-01-05T00:00:00", "issue_number": 7})
    assert "05 January 2020" in html
    assert "2020-01-05T00:00:00" not in html


_NEWSLETTER_DRIVER = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("nl", "scripts/generate-newsletter-html.py")
NL = importlib.util.module_from_spec(spec); spec.loader.exec_module(NL)
data = json.loads(sys.argv[1])
sys.stdout.write(NL.generate_newsletter(data))
"""


def _newsletter(overlay: Path, data: dict) -> str:
    """Render through a child pointed at the fixture overlay.

    The generator loads `newsletter.css` from the DATA overlay and RAISES when it
    is absent, by design: an unstyled document that looks complete is worse than
    a refusal. A bare engine clone has no overlay, so a test that renders against
    the operator's live one passes here and fails in CI -- which is exactly what
    happened on the first run of this file. The stub below is written by the
    test, so the render is measured everywhere rather than skipped where it
    matters.
    """
    gen = overlay / "datastore" / "brand" / "templates" / "generators"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "newsletter.css").write_text("/* stub for the render path */\n", encoding="utf-8")
    env = dict(os.environ, HEADING_OS_DATA=str(overlay), PYTHONPATH=str(ROOT))
    env.pop("HEADING_OS_TZ", None)
    proc = subprocess.run([PY, "-c", _NEWSLETTER_DRIVER, json.dumps(data)],
                          capture_output=True, text=True, env=env,
                          cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_json_number_date_now_renders_formatted(overlay):
    """A behaviour change, stated because it is one.

    `20200105` is the ISO BASIC form, so the shared coercion reads it. The
    date-only parser refused it and the masthead printed the bare number. The
    guard that made a JSON number survive at all is still what stops the crash;
    it now formats the value instead of showing it raw.
    """
    html = _newsletter(overlay, {"date": 20200105, "issue_number": 7})
    assert "05 January 2020" in html
    assert ">20200105<" not in html


def test_an_unreadable_issue_date_still_renders_the_raw_value(overlay):
    """Degrade, do not crash: the guard's original purpose is preserved."""
    html = _newsletter(overlay, {"date": 12345, "issue_number": 7})
    assert "12345" in html


def test_a_missing_issue_date_does_not_crash_the_render(overlay):
    html = _newsletter(overlay, {"issue_number": 7})
    assert "<html" in html


# ============================================================
# E -- the memory-expiry coercion
# ============================================================

@pytest.mark.parametrize("value,expected", [
    ("2020-01-05", dt.date(2020, 1, 5)),
    # Both of these returned None before. The first is the sharper one: the same
    # instant UNQUOTED was read fine, because YAML typed it, so the record's fate
    # depended on the author's quotes.
    ("2020-01-05 09:30:00", dt.date(2020, 1, 5)),
    ("20200105", dt.date(2020, 1, 5)),
    (dt.date(2020, 1, 5), dt.date(2020, 1, 5)),
    ("not-a-date", None),
    ("2020-01-05garbage", None),
    (None, None),
    ([], None),
])
def test_memory_expiry_keeps_its_none_contract_on_the_shared_grammar(value, expected):
    from scripts.utils.memory_expiry import _coerce_date
    assert _coerce_date(value) == expected


def test_memory_expiry_never_raises():
    """The None contract is load-bearing: a raise would abort a sweep over the
    whole index, and a record that cannot be read must not be retired."""
    from scripts.utils.memory_expiry import _coerce_date
    for value in [None, True, 0, 2.5, "x", [], {}, object()]:
        _coerce_date(value)


# ============================================================
# F -- the ratchet, keyed on the SHAPE not the name
# ============================================================

# Every place under scripts/ and .claude/ that looks for a frontmatter fence by
# testing for the CHARACTERS `---`. MEASURED 2026-08-28 by the sweep below.
#
# The name-keyed sweep in tests/test_markdown_frontmatter_single_source.py cannot
# see these: `load_crm_contacts` parsed frontmatter inline under a name that is
# not a parser spelling, and it carried the defect. Shape beats name here.
#
# `OPEN` marks a real frontmatter reader still using the character form. Those
# are the next shard's work, listed rather than left for someone to rediscover.
DECLARED_FENCE_SITES = {
    # --- not frontmatter at all ---
    "scripts/artifact-evaluator.py": "skips a rule line while hunting the description",
    "scripts/bridge_daemon/sources/studio.py": "skips a rule line building a preview",
    "scripts/odin-skill-proposal.py": "colours a unified diff; `---` is the diff marker",
    "scripts/pipeline-summary.py": "markdown table separator and section rule",
    # --- already correct ---
    "scripts/inbox_pulse/rules.py": "guard only; the closing fence is an exact-line test",
    "scripts/marp_render.py": "guard only; delegates to the shared parser",
    "scripts/dev/extract-router-rows.py": "guard only; closing fence via ^---[ \\t]*$ MULTILINE",
    "scripts/utils/memory_health.py": "opening guard; the closing test is `line.strip() == '---'`",
    # Nine OPEN entries left on 2026-08-28 (shard 55), all migrated to
    # `split_frontmatter`: chronicle, crm_migrate_to_entity_model, odin-cadence,
    # router_payload, viraid_counterpart, run-skill-eval, validate-crm-schema,
    # threads_lib, and quick_validate. Their measurements and the divergence
    # table are in tests/test_nine_readers_that_looked_for_three_characters.py.
}


def _fence_sites():
    """Files with a call testing a string against the literal `---`."""
    found = {}
    for path in tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("find", "startswith", "split",
                                           "index", "partition", "endswith")):
                continue
            if any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                   and a.value.startswith("---") for a in node.args):
                found.setdefault(str(path.relative_to(ROOT)), []).append(node.lineno)
    return found


def test_every_character_fence_site_is_declared():
    """A new one must be argued for, not inherited.

    Shard 52 fixed this shape in three copies and shard 54 in a fourth. A
    detector keyed on function NAMES missed the fourth entirely, because the
    parsing sat inline inside a loader.
    """
    found = _fence_sites()
    undeclared = sorted(set(found) - set(DECLARED_FENCE_SITES))
    assert undeclared == [], (
        "undeclared `---` fence test(s): "
        + ", ".join(f"{f}:{found[f]}" for f in undeclared)
        + ". Use scripts.utils.markdown.split_frontmatter, or add an entry to "
          "DECLARED_FENCE_SITES saying why the characters are the right test.")


def test_the_fence_registry_does_not_outlive_its_sites():
    """A registry naming files that no longer carry the shape passes everything."""
    found = _fence_sites()
    stale = sorted(set(DECLARED_FENCE_SITES) - set(found))
    assert stale == [], f"DECLARED_FENCE_SITES entries with no matching site: {stale}"


def test_the_digest_left_the_fence_registry():
    assert "scripts/email-intelligence.py" not in _fence_sites()


def test_no_entry_is_still_marked_open():
    """Shard 55 cleared the nine, and a new OPEN entry must not sit here quietly.

    Marking a site OPEN was the right move while nine of them waited for their
    own measurement. Leaving the mechanism in place after they are fixed is what
    turns a known defect into a documented feature, so the assertion is inverted:
    a site that needs fixing gets FIXED, not relabelled.
    """
    open_sites = [f for f, why in DECLARED_FENCE_SITES.items() if why.startswith("OPEN")]
    assert open_sites == [], (
        f"still marked OPEN: {open_sites}. Fix them, or say plainly why the "
        f"characters are the right test for that caller.")


def test_every_declared_file_still_exists():
    missing = sorted(f for f in DECLARED_FENCE_SITES if not (ROOT / f).exists())
    assert missing == [], f"registry names files that are gone: {missing}"


# ============================================================
# G -- the date-reader ratchet from shard 53 has shrunk
# ============================================================

def test_the_old_date_form_survives_in_one_place_only():
    """Three of the four files shard 53 declared are migrated.

    What is left is `.claude/hooks/checkpoint-offer.py`, and migrating it would
    be a DEFECT rather than a fix: it compares two full TIMESTAMPS, and a
    date-returning coercion would make two compactions on the same day compare
    equal.
    """
    sibling = importlib.util.spec_from_file_location(
        "shard53", ROOT / "tests" / "test_a_health_engine_that_scanned_a_name_list.py")
    mod = importlib.util.module_from_spec(sibling)
    sibling.loader.exec_module(mod)
    assert set(mod._old_form_sites()) == {".claude/hooks/checkpoint-offer.py"}


def test_the_checkpoint_hook_needs_the_time_it_would_lose():
    """The measurement behind leaving that one alone."""
    same_day_earlier = "2026-08-28T09:00:00"
    same_day_later = "2026-08-28T17:00:00"
    assert dt.datetime.fromisoformat(same_day_later) > dt.datetime.fromisoformat(same_day_earlier)
    assert frontmatter_date(same_day_later) == frontmatter_date(same_day_earlier), (
        "a date-returning coercion collapses the two timestamps the hook compares")
