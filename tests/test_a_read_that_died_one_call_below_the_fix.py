#!/usr/bin/env python3
"""Shard 16: fifteen reads that still raised after the read beside them was fixed.

`UnicodeDecodeError` is a `ValueError`. It is raised inside `read_text` before
any parser runs, so `except OSError` cannot catch it and a read with no `try` at
all is invisible to an AST sweep that asks which handler names appear. On
2026-09-01 that defect was measured and fixed in three places. It was left
standing in fifteen more, every one of them either one call below a fixed site
or a sibling in the same file, and each fix carries a comment claiming the
failure is closed.

The fifteen, by file: `scripts/utils/crm.py` 4, `scripts/workspace-health.py` 5,
`scripts/knowledge-health.py` 2, `scripts/odin_brain_lint.py` 2,
`scripts/odin-brain-health.py` 1, `scripts/validate-crm-schema.py` 1. This
sentence said "nine" while the file tested fifteen, which is the stale-measured-
claim shape this campaign exists to catch, so the split is spelled out rather
than summarised.

MEASURED 2026-09-01 against the tree as it then stood. Every line below is a
probe result, not a reading.

  scripts/utils/crm.py
    `scan_contacts` and `contact_index_by_email` were both hardened that day
    against a contact card that is not valid UTF-8, and both say so in a
    comment. Both then call `load_entity`, which read the address-book entity
    with no `try` at all. With one clean entity and one carrying a lone 0xe9:

        load_entity("broken")        RAISED UnicodeDecodeError
        scan_contacts(...)           RAISED UnicodeDecodeError, 0 of 2 contacts
        contact_index_by_email(...)  RAISED UnicodeDecodeError, 0 of 2 addresses

    `contact_index_by_email`'s own docstring calls it THE ONE PLACE that answers
    which contact owns an address, and records that 80 of 169 cards on the
    operator's tree resolve their address ONLY through an entity. So the path
    that was left raising is the majority path for that question, not an edge.
    `parse_config`, `parse_pipeline_stages` and `parse_aliases` are the same
    shape: `scan_contacts` calls the last two unconditionally on every run.

        parse_pipeline_stages(...)   RAISED UnicodeDecodeError
        parse_aliases(...)           RAISED UnicodeDecodeError
        parse_config(...)            RAISED UnicodeDecodeError

  scripts/workspace-health.py
    `check_context_freshness` was hardened the same day, with a comment
    recording that it "reported on the clean file, then died on the next one
    ... so the run produced no verdict at all". Five sibling sections in the
    same file read their input with no `try`, and `main` runs all thirteen in an
    unguarded loop, so any one of them ends the run the same way:

        check_reference_validation   RAISED UnicodeDecodeError
        check_pipeline_health        RAISED UnicodeDecodeError
        check_people_completeness    RAISED UnicodeDecodeError
        check_skill_router_coverage  RAISED UnicodeDecodeError
        check_doc_versions           RAISED UnicodeDecodeError

  scripts/knowledge-health.py and scripts/odin-brain-health.py
    The two engines over one knowledge root, which
    `tests/test_a_health_engine_that_scanned_a_name_list.py` exists because they
    drifted apart. They had drifted here too, in opposite directions and to the
    same outcome: `knowledge-health.scan_notes` had no handler, and
    `odin-brain-health.load_frontmatter` had `except OSError`, which cannot
    catch this. Neither degraded. Hardening the engine was also not enough:
    `--compile` then died one level further down, in
    `scripts/odin_brain_lint.py`, whose two reads carry the same narrow handler.
    That second traceback arrived AFTER the engine had printed its new warning
    line, which is the most misleading shape available: the warning looks like
    the handling worked.

  scripts/validate-crm-schema.py
    A gate, and its handler's own comment says an unreadable FILE and a
    malformed BLOCK are different findings that must be told apart. It caught
    `OSError` only, so the likeliest failure for a file that holds names went
    straight past it. Measured over one clean card and one carrying a lone
    0xe9: exit 1 with a raw traceback naming a codec, a byte and an offset,
    instead of the FAIL line the handler was written to print.

A health check that cannot read its input has not found that input healthy, so
every fix here reports the file by name and counts it, and none of them
swallows. The negative cases below are what hold that line: a readable corpus
must still resolve, still be scored, and still say nothing.

Example data is invented. The undecodable byte is written as an explicit
`\\xe9` in a bytes literal so this file stays pure ASCII on disk.

Run: .venv/bin/python -m pytest tests/test_a_read_that_died_one_call_below_the_fix.py -q
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import crm  # noqa: E402

# A single Latin-1 byte, which is what a paste out of a word processor or a
# name typed on a non-UTF-8 host actually leaves behind. Written as an escape:
# the engine repo forbids a stray non-ASCII byte in a tracked file, and this
# test would otherwise be the thing it forbids.
BAD_BYTE = b"\xe9"

CONFIG = {"partner": {"cadence": 30, "yellow": 20, "red": 30}}
TODAY = date(2026, 8, 25)


# ============================================================
# 1. crm: the reader both hardened callers funnel through
# ============================================================

@pytest.fixture
def book(tmp_path, monkeypatch):
    """An address book holding one readable entity and one undecodable one."""
    d = tmp_path / "address-book"
    d.mkdir()
    (d / "clean.md").write_bytes(
        b"---\nname: James Bond\ncanonical_email: bond@example.invalid\n---\n")
    (d / "broken.md").write_bytes(
        b"---\nname: Ren" + BAD_BYTE + b" Marlow\n"
        b"canonical_email: marlow@example.invalid\n---\n")
    monkeypatch.setattr(crm, "_address_book_dir", lambda workspace_root=None: d)
    return d


def _cards(tmp_path: Path, **refs: str) -> Path:
    d = tmp_path / "contacts"
    d.mkdir()
    for name, slug in refs.items():
        (d / f"{name}.md").write_text(
            f"---\ntype: partner\nentity_ref: {slug}\nlast_touch: 2026-08-01\n---\n",
            encoding="utf-8")
    return d


def test_an_undecodable_entity_reads_as_absent_rather_than_raising(book):
    """The docstring says "Returns parsed frontmatter or None". It raised."""
    assert crm.load_entity("broken") is None


def test_a_readable_entity_beside_it_still_resolves(book):
    """The anchor. A handler that returned None unconditionally would satisfy
    the test above and destroy the function, so both directions are required."""
    entity = crm.load_entity("clean")
    assert entity is not None and entity["name"] == "James Bond"


def test_the_scan_keeps_every_card_whose_entity_it_could_read(tmp_path, book):
    """`scan_contacts` returned NEITHER card. The fix beside it, in the card
    read one level up, could not see this because the byte is in the entity."""
    contacts, _tribe, dangling, *_ = crm.scan_contacts(
        CONFIG, today=TODAY, contacts_dir=_cards(tmp_path, a="clean", b="broken"))

    assert [c["name"] for c in contacts] == ["James Bond"]
    assert dangling == [{"file": "b.md", "entity_ref": "broken"}], (
        "an unreadable entity must be REPORTED, not dropped: dangling_refs is "
        "the diagnostic the operator already reads for a card resolving to "
        "nothing, and silence here is a person who stops accruing red debt")


def test_the_email_index_keeps_the_address_it_could_read(tmp_path, book):
    """`contact_index_by_email` calls itself THE ONE PLACE that answers which
    contact owns an address. It answered with an exception."""
    index = crm.contact_index_by_email(
        contacts_dir=_cards(tmp_path, a="clean", b="broken"))
    assert sorted(index) == ["bond@example.invalid"]


def test_an_all_readable_tree_reports_nothing_dangling(tmp_path, book):
    """The other anchor: the tolerance must not have made every card dangle."""
    contacts, _tribe, dangling, *_ = crm.scan_contacts(
        CONFIG, today=TODAY, contacts_dir=_cards(tmp_path, a="clean"))
    assert dangling == []
    assert [c["name"] for c in contacts] == ["James Bond"]


@pytest.mark.parametrize("reader, payload", [
    ("parse_pipeline_stages", b"| Company | Stage |\n|---|---|\n| Ren"
                              + BAD_BYTE + b" Ltd | Lead |\n"),
    ("parse_aliases", b"## Aliases\n### acme\n- Ren" + BAD_BYTE + b" Co\n"),
    ("parse_config", b"| Type | Cadence | Yellow | Red |\n|---|---|---|---|\n"
                     b"| Ren" + BAD_BYTE + b" | 30 | 20 | 30 |\n"),
])
def test_an_undecodable_table_degrades_to_empty(tmp_path, reader, payload):
    """All three already answer `{}` for an absent file, which is the same
    degradation with the same consequence. `scan_contacts` calls the first two
    unconditionally, so either one raising ended the whole scan."""
    path = tmp_path / "table.md"
    path.write_bytes(payload)
    assert getattr(crm, reader)(path) == {}


def test_a_readable_pipeline_still_yields_its_stages(tmp_path):
    """The anchor for the three above: `{}` must mean "could not read", not
    "does not read"."""
    path = tmp_path / "pipeline.md"
    path.write_text("| Company | Stage |\n|---|---|\n| Universal Exports | Lead |\n",
                    encoding="utf-8")
    assert crm.parse_pipeline_stages(path) == {"universal exports": "Lead"}


# ============================================================
# 2. workspace-health: five sections beside the one that was fixed
# ============================================================

@pytest.fixture
def wh():
    """A fresh module object: it binds directories at import time."""
    spec = importlib.util.spec_from_file_location(
        "workspace_health_shard16", ROOT / "scripts" / "workspace-health.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["workspace_health_shard16"] = mod
    spec.loader.exec_module(mod)
    return mod


def _undecodable(path: Path, prefix: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(prefix + b"Ren" + BAD_BYTE + b" Marlow\n")
    return path


def test_an_unreadable_pipeline_is_an_issue_not_a_traceback(wh, tmp_path, monkeypatch):
    ctx = tmp_path / "context"
    _undecodable(ctx / "pipeline.md", b"| Company | Stage |\n|---|---|\n| ")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)
    assert wh.check_pipeline_health() == 1


def test_an_unreadable_people_file_is_an_issue_not_a_traceback(wh, tmp_path, monkeypatch):
    ctx = tmp_path / "context"
    _undecodable(ctx / "people.md", b"| Name |\n|---|\n| ")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)
    assert wh.check_people_completeness() == 1


def test_an_unreadable_reference_index_is_an_issue_not_a_traceback(
        wh, tmp_path, monkeypatch):
    data = tmp_path / "data"
    _undecodable(data / wh.REFERENCE_INDEX_RELPATH, b"- `scripts/x.py` ")
    monkeypatch.setattr(wh, "get_data_root", lambda: data)
    assert wh.check_reference_validation() == 1


def test_an_unreadable_router_is_an_issue_not_a_traceback(wh, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _undecodable(ws / ".claude" / "rules" / "skill-router.md", b"| `/osint` | ")
    (ws / ".claude" / "skills" / "osint").mkdir(parents=True)
    monkeypatch.setattr(wh, "WORKSPACE", ws)
    assert wh.check_skill_router_coverage() == 1


def test_an_unreadable_template_is_an_issue_not_a_traceback(wh, tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    for n in ("GETTING-STARTED.md", "CEO-ADMIN-GUIDE.md",
              "EMERGENCY-PROCEDURES.md", "CLAUDE.md.template"):
        _undecodable(templates / n, b"<!-- version: 1.0.0 | last-updated: 2026-08-20 --> ")
    monkeypatch.setattr(wh, "get_templates_dir", lambda: templates)
    assert wh.check_doc_versions() == 4, "each unverifiable template counts once"


def test_every_unreadable_section_names_the_reason_it_could_not_check(
        wh, tmp_path, monkeypatch, capsys):
    """"NOT checked" is the wording the fixed sibling uses. A section that
    counted an issue while printing nothing about WHY would be a number the
    operator cannot act on."""
    ctx = tmp_path / "context"
    _undecodable(ctx / "pipeline.md", b"| Company |\n|---|\n| ")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)
    wh.check_pipeline_health()
    out = capsys.readouterr().out
    assert "pipeline.md could not be read" in out
    assert "UnicodeDecodeError" in out
    assert "NOT checked" in out


def test_a_readable_section_is_still_silent_about_reading(wh, tmp_path, monkeypatch, capsys):
    """The anchor. A handler that fired on every run would print this forever."""
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "people.md").write_text(
        "| Name | Role | Email |\n|---|---|---|\n| J Bond | Agent | b@example.invalid |\n",
        encoding="utf-8")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)
    wh.check_people_completeness()
    assert "could not be read" not in capsys.readouterr().out


def test_the_run_still_reaches_a_verdict_over_an_unreadable_context_file(
        wh, tmp_path, monkeypatch, capsys):
    """The consequence, through `main`, which is where it was measured.

    `main` sums thirteen sections in an unguarded loop, so one raising section
    is not one failed section: it is a traceback and NO summary line. This
    drives the real loop with every other section stubbed, so the assertion is
    about the surviving verdict rather than about the other twelve.
    """
    ctx = tmp_path / "context"
    _undecodable(ctx / "pipeline.md", b"| Company |\n|---|\n| ")
    monkeypatch.setattr(wh, "context_dir", lambda p=ctx: p)
    for name in list(wh.__dict__):
        if name.startswith("check_") and name != "check_pipeline_health":
            monkeypatch.setitem(wh.__dict__, name, lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["workspace-health.py"])

    with pytest.raises(SystemExit) as exc:
        wh.main()

    out = capsys.readouterr().out
    assert exc.value.code == 1, "an unreadable input must not read as a pass"
    assert "1 issue(s) found." in out, (
        "the run ended without a summary, which is the defect: the operator "
        "sees a codec error and no verdict")


# ============================================================
# 3. The two health engines, which must degrade the same way
# ============================================================

def _knowledge_overlay(tmp_path: Path, *, broken: bool) -> Path:
    """A data overlay with one readable note per engine, plus optionally one
    undecodable note per engine."""
    data = tmp_path / "data"
    kd = data / "knowledge"
    (kd / "research").mkdir(parents=True)
    (data / "crm").mkdir()
    for sub in ("sources", "principles", "positions", "episodes", "conflicts",
                "reference"):
        (kd / "odin-brain" / sub).mkdir(parents=True, exist_ok=True)

    zk = (b"---\nid: \"20200101000001\"\ntitle: TITLE\ntype: research\n"
          b"status: seed\ncreated: 2020-01-01\nkeywords: [gadget]\n"
          b"confidence: medium\n---\n\n# TITLE\n")
    # The brain body carries a deliberately dangling wiki-link. It is the anchor
    # for the lint fix: `odin_brain_lint`'s wikilink sweep is what raised, and a
    # handler that skipped EVERY file would silence the traceback while also
    # silencing the lint. Measured: without this link in the corpus, forcing
    # that sweep to skip every file was undetectable by this whole file.
    brain = (b"---\nid: \"1\"\ntitle: TITLE\ntype: source\nformat: fleeting\n"
             b"author: J. Bond\ningested: 2020-01-01\nconfidence: medium\n"
             b"keywords: [gadget]\nstatus: seed\ncreated: 2020-01-01\n---\n"
             b"\nSee [[no-such-note-anywhere]] for the rest.\n")
    (kd / "research" / "20200101000001-ok.md").write_bytes(
        zk.replace(b"TITLE", b"Readable note"))
    (kd / "odin-brain" / "sources" / "1-ok.md").write_bytes(
        brain.replace(b"TITLE", b"Readable source"))
    if broken:
        (kd / "research" / "20200101000002-bad.md").write_bytes(
            zk.replace(b"TITLE", b"Ren" + BAD_BYTE + b" note"))
        (kd / "odin-brain" / "sources" / "2-bad.md").write_bytes(
            brain.replace(b"TITLE", b"Ren" + BAD_BYTE + b" source"))
    return data


def _engine(script: str, data: Path, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ, HEADING_OS_DATA=str(data))
    env.pop("HEADING_OS_TZ", None)
    # `errors="replace"`: the fixtures below deliberately plant an undecodable
    # byte for the child to read, and the child names the offending file in its
    # report. Decoding that report strictly raises out of `subprocess.run`
    # before this function returns.
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, errors="replace", env=env,
        cwd=str(ROOT), timeout=300, check=False)


ENGINES = (("knowledge-health.py", "--json"), ("odin-brain-health.py", "--compile"))


@pytest.mark.parametrize("script, flag", ENGINES)
def test_an_engine_finishes_over_an_undecodable_note(tmp_path, script, flag):
    """Both died: one with no handler, one with an `except OSError` that cannot
    catch a `UnicodeDecodeError`. Different code, same outcome, no report."""
    proc = _engine(script, _knowledge_overlay(tmp_path, broken=True), flag)
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.mark.parametrize("script, flag", ENGINES)
def test_an_engine_names_the_note_it_could_not_read(tmp_path, script, flag):
    """Skipping is right and silence is not: this engine already prints a warn
    line for an unreadable `created:` value, for exactly this reason."""
    proc = _engine(script, _knowledge_overlay(tmp_path, broken=True), flag)
    assert "could not be read" in proc.stderr, proc.stderr[-2000:]
    assert "bad.md" in proc.stderr, proc.stderr[-2000:]


@pytest.mark.parametrize("script, flag", ENGINES)
def test_an_engine_says_nothing_over_a_clean_corpus(tmp_path, script, flag):
    """The anchor. A warn line on every healthy run stops being read, and this
    one would then be indistinguishable from the real thing."""
    proc = _engine(script, _knowledge_overlay(tmp_path, broken=False), flag)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "could not be read" not in proc.stderr


def test_both_engines_degrade_the_same_way_over_the_same_byte(tmp_path):
    """The pairing this shard's neighbour exists to hold.

    `tests/test_a_health_engine_that_scanned_a_name_list.py` was written because
    a fix landed in one of these two engines and not the other. That had
    happened again here, in both directions at once: `knowledge-health` had no
    handler and `odin-brain-health` had one too narrow to catch this. A
    per-engine test passes while the two disagree, so one fixture drives both.
    """
    data = _knowledge_overlay(tmp_path, broken=True)
    outcomes = {}
    for script, flag in ENGINES:
        proc = _engine(script, data, flag)
        outcomes[script] = (proc.returncode,
                            "could not be read" in proc.stderr,
                            "bad.md" in proc.stderr)
    assert len(set(outcomes.values())) == 1, (
        f"the two engines disagree about an undecodable note: {outcomes}")
    assert set(outcomes.values()) == {(0, True, True)}


def test_the_compile_path_survives_its_own_lint(tmp_path):
    """`odin-brain-health.py --compile` still died one level below the engine.

    Hardening `load_frontmatter` moved the traceback rather than removing it:
    `run_compile` calls `scripts/odin_brain_lint.py`, whose two reads carried
    the same `except OSError`. The engine printed its warn line and then died,
    which is the most misleading shape of all, because the warning looks like
    the handling worked.
    """
    proc = _engine("odin-brain-health.py",
                   _knowledge_overlay(tmp_path, broken=True), "--compile")
    assert "odin_brain_lint" not in proc.stderr, proc.stderr[-2000:]
    assert "Traceback" not in proc.stderr, proc.stderr[-2000:]
    assert proc.returncode == 0


# ============================================================
# 4. The CRM schema gate, which crashed past its own handler
# ============================================================

def _crm_tree(tmp_path: Path, *, broken: bool) -> Path:
    """A `--dir` layout: one readable card, optionally one undecodable one.

    A sibling `data/` is created beside it purely so `_validate` has a real
    directory to pin `HEADING_OS_DATA` at; see that helper for why.
    """
    (tmp_path / "data").mkdir(exist_ok=True)
    root = tmp_path / "crm"
    (root / "contacts").mkdir(parents=True)
    (root / "address-book").mkdir()
    card = (b"---\ntype: partner\nname: NAME\nlast_touch: 2026-08-01\n"
            b"---\nbody\n")
    (root / "contacts" / "readable.md").write_bytes(
        card.replace(b"NAME", b"James Bond"))
    if broken:
        (root / "contacts" / "undecodable.md").write_bytes(
            card.replace(b"NAME", b"Ren" + BAD_BYTE + b" Marlow"))
    return root


def _validate(root: Path) -> subprocess.CompletedProcess:
    """Run the gate against `root`, with the operator's overlay pinned away.

    `--dir` already names the corpus, so the data root is not consulted for the
    records. Pinning it anyway is the workspace's standing rule for a spawned
    child: an unpinned child inherits `HEADING_OS_DATA` from the ambient
    environment and resolves the operator's live private overlay, which makes it
    a suspect in the session guard's whole-tree report even when it only reads.
    The first draft of this helper omitted `env=`, and the guard duly listed it
    among the child processes that could have written. A suspect list that names
    everything names nothing.
    """
    env = dict(os.environ, HEADING_OS_DATA=str(root.parent / "data"))
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-crm-schema.py"),
         "--dir", str(root)],
        capture_output=True, text=True, errors="replace", cwd=str(ROOT),
        timeout=300, check=False, env=env)


def test_the_schema_gate_reports_rather_than_crashing(tmp_path):
    """Its handler's own comment says an unreadable FILE and a malformed BLOCK
    are different findings and must be told apart. `except OSError` could not
    catch the likeliest one, and CRM cards are the files that hold names."""
    proc = _validate(_crm_tree(tmp_path, broken=True))
    assert "Traceback" not in proc.stderr, proc.stderr[-2000:]
    assert "UnicodeDecodeError:" not in proc.stderr.replace(
        "(UnicodeDecodeError)", ""), proc.stderr[-2000:]


def test_the_schema_gate_names_the_card_it_could_not_read(tmp_path):
    proc = _validate(_crm_tree(tmp_path, broken=True))
    assert "undecodable.md: could not be read" in proc.stderr, proc.stderr[-2000:]


def test_an_unreadable_card_still_fails_the_gate(tmp_path):
    """The verdict must not soften. A card nobody could read has not been shown
    to satisfy the schema, so it FAILS; only the crash goes. A handler that
    skipped it instead would turn a crash into a silent pass, which is the
    worse of the two.
    """
    proc = _validate(_crm_tree(tmp_path, broken=True))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 of 2 records fail schema." in proc.stdout, proc.stdout


def test_the_readable_card_beside_it_still_validates(tmp_path):
    """The anchor: one bad card must cost itself and nothing else."""
    proc = _validate(_crm_tree(tmp_path, broken=True))
    assert "OK" in proc.stdout and "readable" in proc.stdout, proc.stdout


def test_an_all_readable_crm_tree_still_passes_silently(tmp_path):
    """The other anchor. A warn line on a clean corpus is noise, and this gate
    runs before a push."""
    proc = _validate(_crm_tree(tmp_path, broken=False))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not be read" not in proc.stderr


@pytest.mark.parametrize("broken", [True, False])
def test_the_lint_still_finds_a_dangling_wikilink_it_could_read(tmp_path, broken):
    """The anchor for the lint fix, in both corpora.

    MEASURED: forcing that sweep to skip every file SURVIVED this whole file
    until this test existed. Silencing a traceback by declining to read
    anything is not a fix, it is the same outage with the log line removed, and
    a corpus holding no wiki-link at all cannot tell the two apart.

    Run over the broken corpus AND the clean one, because the claim is that the
    undecodable note costs its own findings and nobody else's.
    """
    proc = _engine("odin-brain-health.py",
                   _knowledge_overlay(tmp_path, broken=broken), "--compile")
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout)
    warnings = report["temporal_validity"]["warnings"]
    dangling = [w for w in warnings if w.get("check") == "dangling_wikilink"]
    assert dangling, (
        "the readable brain source names [[no-such-note-anywhere]] and the "
        f"lint reported no dangling wiki-link: {warnings}")
    assert any("no-such-note-anywhere" in w.get("target", "") for w in dangling)
