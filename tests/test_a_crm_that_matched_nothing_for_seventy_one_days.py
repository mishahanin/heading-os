"""Shard scripts-utils-01-p1: two modules that resolved private content under the
engine clone, and four promises the code did not keep.

* ``crm_autolog`` resolved the address book and the contacts tree under
  ``get_workspace_root()`` - the ENGINE clone - while the CRM lives in the private
  DATA overlay. `resolve_recipient` returns None for a missing directory exactly as
  it does for an unknown address, so the audit trail recorded every call as
  ``"matched": false``. Measured on the live tree on 2026-08-25: 71 daily log files
  spanning 2026-06-14 to 2026-08-25, 19 290 entries, 0 matched. Of the 422 distinct
  addresses in those logs, 62 resolve to a real address-book entity once the seam
  is right.

* ``doctype_renderer`` resolved ``datastore/brand/`` the same way, so all five
  locked corporate doctypes died with FileNotFoundError before rendering a byte.

* ``bump_last_touch_in_text`` located the frontmatter fence with
  ``text.find("---", 3)``, which matches any three hyphens - including a markdown
  horizontal rule in a record that has no frontmatter at all.

* ``resolve_recipient``'s docstring sent the operator to
  ``crm-autolog-conflicts-{date}.jsonl``, a file nothing writes and nothing reads.

* ``dead_letter.record`` promised "Never raises" and caught only OSError, while
  ``json.dumps`` raises TypeError on an ordinary un-encodable payload value.

* ``docx_font_embed._build_font_rels`` stripped prior font relationships with a
  pattern that cannot cross a slash, and every relationship it writes carries
  ``Target="fonts/..."``.

* ``draft_critique.critique_draft`` swallowed every exception with no record, so a
  broken critic and a clean draft produced the same output.

* ``daemon_heartbeat``'s field list called ``version`` a build version; ``beat``
  has no build-version parameter and copies the config version into both fields.

Run: python3 -m pytest tests/test_a_crm_that_matched_nothing_for_seventy_one_days.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import crm as crm_lib  # noqa: E402
from scripts.utils import crm_autolog as ca  # noqa: E402
from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import dead_letter as dl  # noqa: E402
from scripts.utils import doctype_renderer as dr  # noqa: E402
from scripts.utils import docx_font_embed as dfe  # noqa: E402
from scripts.utils import draft_critique  # noqa: E402
from scripts.utils.workspace import get_crm_contacts_dir, get_workspace_root  # noqa: E402


# ============================================================
# The CRM tree the engine clone does not hold
# ============================================================

def test_the_address_book_does_not_resolve_under_the_engine_clone():
    """The engine repo is public and carries no CRM; resolving there is the bug."""
    resolved = ca._address_book_dir()
    assert get_workspace_root() not in resolved.parents, (
        f"the address book resolved inside the engine clone: {resolved}"
    )


def test_the_contacts_tree_does_not_resolve_under_the_engine_clone():
    resolved = ca._contacts_dir()
    assert get_workspace_root() not in resolved.parents, (
        f"the contacts tree resolved inside the engine clone: {resolved}"
    )


def test_the_address_book_seam_is_delegated_not_copied(monkeypatch):
    """A second copy of a seam is the copy that stops being fixed.

    Proven by moving the ONE implementation and watching the caller follow it.
    """
    sentinel = Path("/nowhere/address-book-sentinel")
    monkeypatch.setattr(crm_lib, "_address_book_dir", lambda *a, **k: sentinel)
    assert ca._address_book_dir() == sentinel


def test_the_contacts_seam_is_the_shared_workspace_resolver():
    assert ca._contacts_dir() == get_crm_contacts_dir()


def test_an_explicit_root_still_means_that_exact_tree(tmp_path):
    """The fixtures depend on this; the seam must not take the choice away."""
    (tmp_path / "crm" / "address-book").mkdir(parents=True)
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    assert ca._address_book_dir(tmp_path) == tmp_path / "crm" / "address-book"
    assert ca._contacts_dir(tmp_path) == tmp_path / "crm" / "contacts"


def test_an_explicit_exec_layout_still_resolves(tmp_path):
    (tmp_path / "corporate" / "crm" / "address-book").mkdir(parents=True)
    (tmp_path / "personal" / "crm" / "contacts").mkdir(parents=True)
    assert ca._address_book_dir(tmp_path).parts[-3:] == ("corporate", "crm", "address-book")
    assert ca._contacts_dir(tmp_path).parts[-3:] == ("personal", "crm", "contacts")


def test_the_audit_trail_does_not_live_in_the_engine_clone():
    """Every entry carries an e-mail address, so the trail is private data.

    71 days of it sat under `<engine>/.sync/logs/`. Nothing leaked - `.sync/` is
    gitignored on both sides - but the routing rule is about where a record
    lives, and the operator moved the 71 files on 2026-08-25.
    """
    resolved = ca._logs_dir()
    assert get_workspace_root() not in resolved.parents, (
        f"the CRM audit trail resolved inside the engine clone: {resolved}"
    )


def test_the_audit_trail_resolves_under_the_data_root():
    from scripts.utils.workspace import get_data_root
    assert ca._logs_dir() == get_data_root() / ".sync" / "logs"


def test_an_explicit_root_still_keeps_the_trail_in_that_tree(tmp_path):
    assert ca._logs_dir(tmp_path) == tmp_path / ".sync" / "logs"


def _fixture_workspace(tmp_path: Path, email: str, slug: str) -> Path:
    ab = tmp_path / "crm" / "address-book"
    ab.mkdir(parents=True)
    (ab / f"{slug}.md").write_text(
        f"---\nslug: {slug}\ncanonical_email: {email}\n---\n# {slug}\n",
        encoding="utf-8")
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / f"{slug}.md").write_text(
        f"---\nslug: {slug}\ntype: partner\n---\n\n## Interaction Log\n",
        encoding="utf-8")
    return tmp_path


def test_a_known_address_resolves_to_its_contact_file(tmp_path):
    ws = _fixture_workspace(tmp_path, "someone@example.invalid", "some-one")
    resolved = ca.resolve_recipient("SomeOne@Example.Invalid", workspace_root=ws)
    assert resolved is not None
    assert resolved.name == "some-one.md"


def test_an_unknown_address_resolves_to_nothing(tmp_path):
    ws = _fixture_workspace(tmp_path, "someone@example.invalid", "some-one")
    assert ca.resolve_recipient("nobody@example.invalid", workspace_root=ws) is None


# ============================================================
# The conflict log the docstring invented
# ============================================================

def test_the_docstring_no_longer_names_a_file_nothing_writes():
    """Anchor on position, not on absence.

    The correction quotes the wrong filename in order to explain it, so a bare
    `not in doc` can never pass. What must hold is that the REAL destination is
    named first, and the invented one appears only in the note about the old
    wording - a reader who stops at the first path gets the right file.
    """
    doc = ca.resolve_recipient.__doc__
    real = doc.index("crm-autolog-{date}.jsonl")
    invented = doc.index("crm-autolog-conflicts-")
    assert real < invented, (
        "the invented filename is still the first path an operator reads"
    )
    assert "nothing writes" in doc[invented:invented + 200]


def test_a_conflict_lands_in_the_daily_log_the_docstring_names(tmp_path):
    ab = tmp_path / "crm" / "address-book"
    ab.mkdir(parents=True)
    for slug in ("one-person", "two-person"):
        (ab / f"{slug}.md").write_text(
            f"---\nslug: {slug}\ncanonical_email: shared@example.invalid\n---\n",
            encoding="utf-8")

    assert ca.resolve_recipient("shared@example.invalid", workspace_root=tmp_path) is None

    logs = sorted((tmp_path / ".sync" / "logs").glob("crm-autolog-*.jsonl"))
    assert [p.name.startswith("crm-autolog-conflicts") for p in logs] == [False] * len(logs)
    kinds = [json.loads(ln)["kind"]
             for p in logs for ln in p.read_text(encoding="utf-8").splitlines() if ln]
    assert "conflict" in kinds


# ============================================================
# The insert that landed in the prose
# ============================================================

def test_a_record_with_frontmatter_gets_the_key_inside_it():
    out = ca.bump_last_touch_in_text("---\nname: X\ntype: partner\n---\n# Body\n",
                                     "2026-08-25")
    assert out == "---\nname: X\ntype: partner\nlast_touch: 2026-08-25\n---\n# Body\n"


def test_an_existing_key_is_replaced_not_duplicated():
    out = ca.bump_last_touch_in_text("---\nname: X\nlast_touch: 2020-01-01\n---\nbody\n",
                                     "2026-08-25")
    assert out.count("last_touch:") == 1
    assert "2026-08-25" in out


def test_a_record_without_frontmatter_is_left_alone():
    """`find("---", 3)` matched a markdown horizontal rule in the BODY.

    The key then sat mid-prose, where nothing reads it and a human reader meets
    a stray YAML line in the middle of a sentence.
    """
    text = "# Notes\n\nsome prose\n\n---\n\nmore prose\n"
    assert ca.bump_last_touch_in_text(text, "2026-08-25") == text


def test_a_dash_run_inside_a_value_does_not_move_the_fence():
    out = ca.bump_last_touch_in_text("---\nname: A --- B\n---\nbody\n", "2026-08-25")
    assert out == "---\nname: A --- B\nlast_touch: 2026-08-25\n---\nbody\n"


def test_a_line_that_only_starts_with_dashes_is_not_the_fence():
    """The fence is a line that is EXACTLY three dashes, not one that begins with them.

    A block scalar carrying `--- something` sits at line start inside the
    frontmatter, so a pattern without the end anchor stops there and writes the
    key into the middle of a value.
    """
    text = "---\nname: X\nnotes: |\n  --- draft heading\n---\nbody\n"
    out = ca.bump_last_touch_in_text(text, "2026-08-25")
    assert out == "---\nname: X\nnotes: |\n  --- draft heading\nlast_touch: 2026-08-25\n---\nbody\n"

    unindented = "---\nname: X\n--- draft heading\n---\nbody\n"
    assert ca.bump_last_touch_in_text(unindented, "2026-08-25") == (
        "---\nname: X\n--- draft heading\nlast_touch: 2026-08-25\n---\nbody\n"
    )


def test_an_unclosed_frontmatter_block_is_left_alone():
    text = "---\nname: X\nbody with no fence\n"
    assert ca.bump_last_touch_in_text(text, "2026-08-25") == text


def test_an_empty_record_is_left_alone():
    assert ca.bump_last_touch_in_text("", "2026-08-25") == ""


# ============================================================
# The five locked doctypes that could not find their templates
# ============================================================

def test_the_template_directory_does_not_resolve_under_the_engine_clone():
    resolved = dr._templates_dir(get_workspace_root())
    assert get_workspace_root() not in resolved.parents, (
        f"brand templates resolved inside the engine clone: {resolved}"
    )


@pytest.mark.parametrize("resolver", [dr._templates_dir, dr._assets_dir, dr._fonts_dir])
def test_every_brand_directory_exists_on_this_workspace(resolver):
    assert resolver(get_workspace_root()).is_dir()


@pytest.mark.parametrize("doctype", sorted(dr.TEMPLATE_REGISTRY))
def test_every_locked_doctype_has_its_template_on_disk(doctype):
    template = dr.TEMPLATE_REGISTRY[doctype]["template"]
    assert (dr._templates_dir(get_workspace_root()) / template).is_file()


def test_a_letter_renders_end_to_end():
    """It raised FileNotFoundError before the seam was fixed."""
    data = dict.fromkeys(dr.TEMPLATE_REGISTRY["letter"]["required"], "PLACEHOLDER")
    data["BODY_HTML"] = "<p>placeholder body</p>"
    assert dr.validate_required_fields("letter", data) == []
    html = dr.render_html("letter", data, get_workspace_root())
    assert "PLACEHOLDER" in html
    assert "<style" in html, "brand css was not inlined"


def test_an_explicit_root_that_holds_the_tree_wins_over_the_seam(tmp_path):
    target = tmp_path / "datastore" / "brand" / "assets"
    target.mkdir(parents=True)
    assert dr._assets_dir(tmp_path) == target


def test_a_path_outside_the_datastore_is_not_routed_through_the_seam():
    assert dr._under_datastore("reference/brand") is None
    assert dr._under_datastore("datastore/brand") is not None


def test_a_missing_directory_still_reports_the_explicit_root(tmp_path):
    """The fallback names the path the caller asked for, not the seam."""
    assert dr._resolve_under_corporate(tmp_path, "no/such/tree") == tmp_path / "no/such/tree"


def test_a_seam_that_points_at_nothing_is_not_returned(tmp_path, monkeypatch):
    """An absent seam must fall back, not hand back a path that does not exist.

    Returning it would move the eventual error message onto the DATA overlay and
    blame a tree the caller never named.
    """
    monkeypatch.setattr("scripts.utils.workspace.get_datastore_dir",
                        lambda: tmp_path / "no-datastore-here")
    resolved = dr._resolve_under_corporate(tmp_path, "datastore/brand/assets")
    assert resolved == tmp_path / "datastore/brand/assets"


def test_a_seam_that_cannot_resolve_says_so(tmp_path, monkeypatch, capsys):
    """A silent None here reappears downstream as a wrongly-blamed cause."""
    def _boom():
        raise RuntimeError("no overlay on this clone")

    monkeypatch.setattr("scripts.utils.workspace.get_datastore_dir", _boom)
    assert dr._under_datastore("datastore/brand/assets") is None
    err = capsys.readouterr().err
    assert "cannot resolve the datastore root" in err
    assert "RuntimeError" in err
    assert "no overlay on this clone" in err


# ============================================================
# The finalizer that raised through "Never raises"
# ============================================================

def test_a_payload_python_cannot_encode_is_written_not_raised(tmp_path):
    """`json.dumps` raises TypeError, and only OSError was caught."""
    payload = {"when": datetime.now(timezone.utc), "path": Path("/srv/data/x")}
    written = dl.record("t1", "send", payload, "permanent", "boom",
                        workspace_root=tmp_path)
    assert written is not None
    assert json.loads(written.read_text(encoding="utf-8"))["classification"] == "permanent"


def test_a_circular_payload_costs_the_payload_not_the_record(tmp_path):
    circular: dict = {}
    circular["self"] = circular
    written = dl.record("t2", "send", circular, "transient", "boom",
                        workspace_root=tmp_path)
    assert written is not None
    entry = json.loads(written.read_text(encoding="utf-8"))
    assert entry["payload"] is None
    assert "payload_error" in entry
    assert entry["error"] == "boom"
    assert entry["classification"] == "transient"


def test_an_encodable_stand_in_keeps_the_payload_rather_than_dropping_it():
    """`default=str` is what keeps the ordinary case whole.

    A Path or a datetime is not a corrupt payload; it is a value json does not
    know. Nulling it would throw away the recipient and subject an operator needs
    to decide on a retry, so the fallback is reserved for what `default=str`
    cannot rescue.
    """
    blob = json.loads(dl._serialize({
        "payload": {"path": Path("/srv/data/x"), "when": datetime(2026, 8, 25, tzinfo=timezone.utc)},
    }))
    assert blob["payload"]["path"] == "/srv/data/x"
    assert blob["payload"]["when"].startswith("2026-08-25")
    assert "payload_error" not in blob


def test_a_serializer_that_fails_outright_still_returns_none(tmp_path, monkeypatch):
    """The outer promise is "Never raises", so it must hold above `_serialize` too."""
    def _boom(_entry):
        raise TypeError("nothing survives this")

    monkeypatch.setattr(dl, "_serialize", _boom)
    assert dl.record("t5", "send", {}, "permanent", "boom",
                     workspace_root=tmp_path) is None


def test_an_ordinary_payload_is_unchanged(tmp_path):
    written = dl.record("t3", "send", {"to": "x@example.invalid"}, "permanent",
                        "boom", workspace_root=tmp_path)
    entry = json.loads(written.read_text(encoding="utf-8"))
    assert entry["payload"] == {"to": "x@example.invalid"}
    assert "payload_error" not in entry


def test_a_write_failure_still_returns_none(tmp_path, monkeypatch):
    def _deny(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(dl, "_atomic_write", _deny)
    assert dl.record("t4", "send", {}, "permanent", "boom",
                     workspace_root=tmp_path) is None


# ============================================================
# The strip that could not cross a slash
# ============================================================

def _rels(*inner: str) -> str:
    return ('<?xml version="1.0"?><Relationships xmlns="x">'
            + "".join(inner) + "</Relationships>")


def _font_rel(rel_id: str, target: str) -> str:
    return f'<Relationship Id="{rel_id}" Type="{dfe.FONT_REL_TYPE}" Target="{target}"/>'


def test_a_prior_font_relationship_is_stripped():
    """Every font Target this module writes carries a slash, so the old pattern
    matched nothing it had ever produced."""
    existing = _rels(_font_rel("rId99", "fonts/GTStandard.odttf"))
    out = dfe._build_font_rels([(None,) * 4 + ("rId100", "GTStandard.odttf")], existing)
    assert "rId99" not in out
    assert out.count(dfe.FONT_REL_TYPE) == 1


def test_a_non_font_relationship_is_preserved():
    existing = _rels('<Relationship Id="rIdOther" Type="http://other" Target="a/b.xml"/>',
                     _font_rel("rId99", "fonts/GTStandard.odttf"))
    out = dfe._build_font_rels([(None,) * 4 + ("rId100", "GTStandard.odttf")], existing)
    assert "rIdOther" in out
    assert "a/b.xml" in out


def test_repeated_embedding_does_not_grow_the_part():
    plan = [(None,) * 4 + ("rId100", "GTStandard.odttf")]
    once = dfe._build_font_rels(plan, _rels())
    twice = dfe._build_font_rels(plan, once)
    assert twice.count(dfe.FONT_REL_TYPE) == once.count(dfe.FONT_REL_TYPE) == 1


def test_a_fresh_part_is_built_when_none_exists():
    out = dfe._build_font_rels([(None,) * 4 + ("rId1", "F.odttf")], None)
    assert out.startswith("<?xml")
    assert 'Target="fonts/F.odttf"' in out


def test_a_malformed_part_is_still_refused():
    with pytest.raises(ValueError):
        dfe._build_font_rels([], '<Relationships xmlns="x">')


# ============================================================
# The critic that failed the same way it passed
# ============================================================

def test_a_failing_critique_says_so_instead_of_looking_clean(monkeypatch, capsys):
    """An absent critique reads as "no concerns"; a broken critic must not."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "NOT-A-REAL-KEY-FOR-THIS-TEST")

    def _boom(_model):
        raise RuntimeError("model pin retired")

    monkeypatch.setattr(draft_critique, "_resolve_model", _boom)
    assert draft_critique.critique_draft("subj", "body text") is None
    err = capsys.readouterr().err
    assert "UNCRITIQUED" in err
    assert "RuntimeError" in err
    assert "model pin retired" in err


def test_an_empty_body_is_still_a_quiet_skip(capsys):
    """Nothing failed there, so nothing should be reported."""
    assert draft_critique.critique_draft("subj", "   ") is None
    assert capsys.readouterr().err == ""


# ============================================================
# The heartbeat field that was never a build version
# ============================================================

def test_the_two_version_fields_are_identical_by_construction(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_heartbeat, "get_workspace_root", lambda: tmp_path)
    daemon_heartbeat.beat("probe", config_version="17")
    written = json.loads(
        (tmp_path / ".daemon-state" / daemon_heartbeat.HEARTBEATS_DIR / "probe.json")
        .read_text(encoding="utf-8"))
    assert written["version"] == written["config_loaded_version"] == "17"


def test_the_field_list_no_longer_calls_it_a_build_version():
    doc = " ".join(daemon_heartbeat.__doc__.split())
    assert "caller-supplied build version" not in doc
    assert "not a build version" in doc


def test_beat_has_no_build_version_parameter():
    """The docstring claimed one for as long as it did not exist."""
    import inspect
    assert "build_version" not in inspect.signature(daemon_heartbeat.beat).parameters
