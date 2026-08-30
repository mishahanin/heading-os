"""Shard scripts-utils-00-p4: ten guards, mostly guarding a public repository.

Two of these decide whether private data reaches a public git history.

- `content_denylist._add` refused any bare word that was not PURE alphabetic, so
  every handle carrying an underscore or a digit never became a token: 19 of 58
  on the live roster. The content gate could not detect them in an
  engine-routed file, which is the exact leak class the roster harvest was
  added to close.
- `commit_source._run` let git quote non-ASCII paths (`core.quotePath` defaults
  on), and `air_gap.is_denied` matches deny PREFIXES with `startswith`. The
  leading double-quote defeated the hard-coded vault prefix and every prefix a
  caller passes, so those commits were indexed, message and all.

The rest are the same family: a scan that dies on one bad record, a contact that
vanishes with no diagnostic, a nudge that says nothing when it checked nothing,
a writer that erases pins it could not read, and three comments that describe
coverage their code does not have.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import commit_source as CS  # noqa: E402
from scripts.utils import content_denylist as CD  # noqa: E402
from scripts.utils import council_freshness as CF  # noqa: E402
from scripts.utils import council_models as CM  # noqa: E402
from scripts.utils import crm  # noqa: E402
from scripts.utils.air_gap import is_denied  # noqa: E402


# ============================================================
# The denylist token gate -- a handle is not noise
# ============================================================

@pytest.mark.parametrize("handle", [
    "quill_vantage",     # underscore
    "vantage95",         # trailing digits
    "v2_marlow",         # both
    "tamsin_okon",
])
def test_a_handle_with_an_underscore_or_a_digit_becomes_a_token(handle):
    """`not v.isalpha()` dropped these. Measured against the live roster,
    19 of 58 real handles never entered the denylist at all."""
    tokens: dict[str, str] = {}
    CD._add(tokens, handle, "handle")
    assert handle in tokens


@pytest.mark.parametrize("noise", [
    "12345", "2026", "9999999", "ab", "x_1", "42",
    # Long enough to clear the length floor and holding no letter at all. Only
    # the "contains a letter" half of the gate refuses these, so without a case
    # of this shape that half could be deleted with every test still green.
    "12_34", "1_2_3_4", "999_888",
])
def test_numeric_and_short_noise_is_still_refused(noise):
    """The gate exists to keep the denylist precise. A gate that starts matching
    bare years and short fragments gets switched off by whoever hits it."""
    tokens: dict[str, str] = {}
    CD._add(tokens, noise, "handle")
    assert tokens == {}


def test_a_handle_that_is_a_token_is_actually_detected():
    """Through the public scanner, not just the token dict: the point of a token
    is that `scan_text` finds it."""
    dl = CD.Denylist()
    for handle in ("quill_vantage", "vantage95"):
        CD._add(dl.tokens, handle, "handle")
    dl._compile()

    assert dl.scan_text("ping @quill_vantage about it")
    assert dl.scan_text("and @vantage95 too")


# ============================================================
# The air gap -- git may not hide a path behind a quote
# ============================================================

# The operator's ~/.gitconfig is not part of these fixtures.
#
# Added 2026-08-30. Both air-gap tests below hardened their throwaway repo
# against a MISSING identity (`user.email`, `user.name`) but not against an
# INHERITED global setting. On a machine with `commit.gpgsign = true` and no
# usable signing key, the `commit` step dies with "gpg failed to sign the data",
# `check=True` raises, and both tests error out for a reason with nothing to do
# with the quoting behaviour under test. These two guard the highest-severity
# behaviour in this shard -- a private path reaching a public index -- and an
# environmental false-red on a leak-gate test is how a leak-gate test ends up
# switched off.
_GIT_FIXTURE_SETUP = (
    ["init", "-q", "."],
    ["config", "user.email", "t@example.invalid"],
    ["config", "user.name", "t"],
    ["config", "commit.gpgsign", "false"],
    ["config", "tag.gpgsign", "false"],
)


def _init_repo(repo, message):
    """Init `repo`, stage everything, and make one commit that cannot be signed."""
    for args in (*_GIT_FIXTURE_SETUP, ["add", "-A"], ["commit", "-qm", message]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_a_vault_path_with_non_ascii_still_trips_the_air_gap(tmp_path):
    """`core.quotePath` defaults on, so git emits
    `"_secure/x/\\321\\204.md"` -- and `is_denied` matches prefixes with
    `startswith`, which that leading quote defeats. The commit was indexed."""
    repo = tmp_path / "gr"
    (repo / "_secure" / "x").mkdir(parents=True)
    (repo / "ok").mkdir()
    (repo / "_secure" / "x" / "файл.md").write_text("private")
    (repo / "ok" / "plain.md").write_text("public")
    _init_repo(repo, "feat: touch the vault")

    sha = CS._run(repo, ["rev-parse", "HEAD"]).strip()
    paths = CS._changed_paths(repo, [sha])[sha]

    vault = [p for p in paths if "_secure" in p]
    assert vault, f"the vault path is missing from {paths}"
    assert all(is_denied(p) for p in vault), f"air gap missed {vault}"
    assert not any(p.startswith('"') for p in paths), "git is still quoting"


def test_an_ordinary_path_is_still_not_denied(tmp_path):
    repo = tmp_path / "gr2"
    (repo / "ok").mkdir(parents=True)
    (repo / "ok" / "plain.md").write_text("public")
    _init_repo(repo, "feat: ok")

    sha = CS._run(repo, ["rev-parse", "HEAD"]).strip()
    paths = CS._changed_paths(repo, [sha])[sha]

    assert paths == ["ok/plain.md"]
    assert not is_denied(paths[0])


# ============================================================
# The CRM scan -- one bad record may not take the rest with it
# ============================================================

def _contacts(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return d


CONFIG = {"partner": {"cadence": 30, "yellow": 20, "red": 30}}
TODAY = date(2026, 8, 25)


def test_a_non_integer_cadence_does_not_abort_the_whole_scan(tmp_path, capsys):
    """`int(cadence_override)` sat inline in two branches, so `cadence: 7 days`
    in ONE contact raised out of `scan_contacts` and took down crm-health,
    generate-dashboard and aggregate-crm together. Everything else in this
    module degrades on bad input."""
    d = _contacts(tmp_path, {
        "bad.md": "---\ntype: partner\ncadence: 7 days\nlast_touch: 2026-08-24\n"
                  "name: Beta Tester\n---\nbody\n",
        "good.md": "---\ntype: partner\ncadence: 5\nlast_touch: 2026-08-01\n"
                   "name: Delta Tester\n---\nbody\n",
    })

    contacts, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    names = sorted(c["name"] for c in contacts)
    assert names == ["Beta Tester", "Delta Tester"]
    assert "not a whole number of days" in capsys.readouterr().err


def test_the_bad_record_falls_back_to_its_type_default(tmp_path):
    """Degrading is not the same as dropping: the contact keeps being tracked,
    on the cadence its type says."""
    d = _contacts(tmp_path, {
        "bad.md": "---\ntype: partner\ncadence: soon\nlast_touch: 2026-08-24\n"
                  "name: Beta Tester\n---\nbody\n",
    })

    contacts, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert contacts[0]["cadence"] == 30


def test_a_zero_cadence_override_is_gray_not_red(tmp_path):
    """`cadence: 0` is this module's "no time-based tracking" sentinel, honoured
    in two of the three branches. The third set cadence=0, yellow=0, red=0, and
    `calculate_health` returns red for `days_since >= 0` -- a red for someone
    touched yesterday, feeding the radar and /cold-sweep's outreach drafting."""
    d = _contacts(tmp_path, {
        "a.md": "---\ntype: advisor\ncadence: 0\nlast_touch: 2026-08-24\n"
                "name: Alpha Tester\n---\nbody\n",
    })

    contacts, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert contacts[0]["health"] == "gray"
    assert contacts[0]["days_since"] is None
    assert contacts[0]["cadence"] == 0


def test_an_ordinary_contact_is_still_scored(tmp_path):
    """The tolerance must not have turned every contact gray."""
    d = _contacts(tmp_path, {
        "a.md": "---\ntype: partner\ncadence: 5\nlast_touch: 2026-08-01\n"
                "name: Delta Tester\n---\nbody\n",
    })

    contacts, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert contacts[0]["health"] == "red"
    assert contacts[0]["days_since"] == 24


def test_a_contact_with_no_cadence_field_is_scored_on_its_type(tmp_path):
    """"No override" and "an override of zero" must stay distinguishable.

    `_cadence_override` returning 0 rather than None for an absent field would
    send EVERY contact without an explicit cadence down the gray path, and no
    test with a `cadence:` line in it can see that.
    """
    d = _contacts(tmp_path, {
        "a.md": "---\ntype: partner\nlast_touch: 2026-08-01\n"
                "name: Delta Tester\n---\nbody\n",
    })

    contacts, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert contacts[0]["cadence"] == 30, "the type default, not the zero sentinel"
    assert contacts[0]["health"] != "gray"
    assert contacts[0]["days_since"] == 24


def test_an_entity_ref_to_a_file_without_frontmatter_is_reported(tmp_path, monkeypatch):
    """`load_entity` returned `{}` -- not None -- for a file that exists and has
    no parseable frontmatter, so the dangling-ref branch never fired. The
    contact then failed the `if not fm.get("name")` check and vanished from CRM
    health, the radar and the dashboard, with no diagnostic anywhere."""
    book = tmp_path / "address-book"
    book.mkdir()
    (book / "ghost.md").write_text("# Heading only, no frontmatter\n")
    monkeypatch.setattr(crm, "_address_book_dir", lambda workspace_root=None: book)
    d = _contacts(tmp_path, {
        "rel.md": "---\ntype: partner\nentity_ref: ghost\nlast_touch: 2026-08-24\n---\n",
    })

    contacts, _tribe, dangling, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert contacts == []
    assert dangling == [{"file": "rel.md", "entity_ref": "ghost"}]


def test_a_missing_entity_file_is_still_reported(tmp_path, monkeypatch):
    """The case that already worked, kept."""
    book = tmp_path / "address-book"
    book.mkdir()
    monkeypatch.setattr(crm, "_address_book_dir", lambda workspace_root=None: book)
    d = _contacts(tmp_path, {
        "rel.md": "---\ntype: partner\nentity_ref: nobody\nlast_touch: 2026-08-24\n---\n",
    })

    _contacts_out, _tribe, dangling, *_ = crm.scan_contacts(
        CONFIG, today=TODAY, contacts_dir=d)

    assert dangling == [{"file": "rel.md", "entity_ref": "nobody"}]


def test_a_good_entity_ref_still_resolves(tmp_path, monkeypatch):
    book = tmp_path / "address-book"
    book.mkdir()
    (book / "real.md").write_text("---\nname: Real Person\ncompany: Universal Exports\n---\n")
    monkeypatch.setattr(crm, "_address_book_dir", lambda workspace_root=None: book)
    d = _contacts(tmp_path, {
        "rel.md": "---\ntype: partner\nentity_ref: real\nlast_touch: 2026-08-24\n---\n",
    })

    contacts, _tribe, dangling, *_ = crm.scan_contacts(CONFIG, today=TODAY, contacts_dir=d)

    assert dangling == []
    assert contacts[0]["name"] == "Real Person"


def test_the_module_docstring_names_the_real_return_shape():
    """It advertised a 3-tuple; the function returns five. A caller written from
    the module summary unpacks wrong."""
    assert "dangling_refs, stages, aliases" in crm.__doc__


# ============================================================
# The council nudge -- silence must mean "checked and fine"
# ============================================================

def test_an_unprobed_run_does_not_report_all_pins_current():
    """`is_actionable` counts only `broken`, so a run that could not probe at
    all returned '' -- and both callers print that as good news:
    council-models-notify logs "all council pins current" and exits 0."""
    findings = CF.assess(probes={"proxy": None})

    line = CF.nudge_line(findings)

    assert line, "an unprobed run must not look like a clean one"
    assert "NOT checked" in line


def test_a_clean_run_is_still_silent():
    """The nudge is a Telegram message. If it fires on every healthy day it
    stops being read."""
    findings = [{"provider": "kimi", "pin": "k3", "status": "ok",
                 "detail": "kimi: k3 present"}]

    assert CF.nudge_line(findings) == ""


def test_a_broken_pin_still_leads():
    """A real break outranks an unknown: it is the one that acts now."""
    findings = [
        {"provider": "kimi", "pin": "k3", "status": "broken", "detail": "kimi: k3 gone"},
        {"provider": "grok", "pin": "g", "status": "unknown", "detail": "grok: unreachable"},
    ]

    line = CF.nudge_line(findings)

    assert line.startswith("Council models:")
    assert "NOT checked" not in line


# ============================================================
# The pin writer must not erase what it could not read
# ============================================================

def test_a_malformed_config_is_refused_not_rewritten(tmp_path, monkeypatch):
    """`_load_config` returns `{}` on a malformed file, which is right for a
    reader falling back and wrong for a writer: the rebuild erased every other
    operator-chosen pin and reverted them to fallbacks."""
    path = tmp_path / "council-models.json"
    path.write_text('{"gemini": "pinned-a", "grok": "pinned-b",')
    monkeypatch.setattr(CM, "config_path", lambda: path)

    with pytest.raises(RuntimeError, match="refusing to rewrite"):
        CM.set_model("kimi", "new-kimi")

    assert path.read_text() == '{"gemini": "pinned-a", "grok": "pinned-b",'


def test_a_wrong_shaped_config_is_refused_too(tmp_path, monkeypatch):
    path = tmp_path / "council-models.json"
    path.write_text('["not", "an", "object"]')
    monkeypatch.setattr(CM, "config_path", lambda: path)

    with pytest.raises(RuntimeError, match="not an object of pins"):
        CM.set_model("kimi", "new-kimi")


def test_a_good_config_still_keeps_its_other_pins(tmp_path, monkeypatch):
    """The promise the docstring makes, measured."""
    path = tmp_path / "council-models.json"
    path.write_text(json.dumps({"gemini": "pinned-a", "grok": "pinned-b"}))
    monkeypatch.setattr(CM, "config_path", lambda: path)

    CM.set_model("kimi", "new-kimi")

    assert json.loads(path.read_text()) == {
        "gemini": "pinned-a", "grok": "pinned-b", "kimi": "new-kimi"}


def test_a_first_run_with_no_config_still_writes(tmp_path, monkeypatch):
    path = tmp_path / "council-models.json"
    monkeypatch.setattr(CM, "config_path", lambda: path)

    CM.set_model("kimi", "new-kimi")

    assert json.loads(path.read_text()) == {"kimi": "new-kimi"}


# ============================================================
# A failed harvest must say so
# ============================================================

def test_a_failed_harvest_is_reported_on_stderr(tmp_path, capsys):
    """The bare `except Exception` bound nothing and logged nothing, so a
    malformed data-config switched the only content-leak layer off in silence."""
    overlay = tmp_path / "overlay"
    (overlay / "admin").mkdir(parents=True)
    (overlay / "admin" / "executives.json").write_text('{"executives": ["wrong shape"]}')

    dl = CD.build_denylist(overlay)

    assert dl.degraded is True
    assert "harvest failed" in capsys.readouterr().err


def test_a_partial_harvest_keeps_the_tokens_it_collected(tmp_path):
    """Deliberate, and pinned in tests/test_egress_proof.py: `egress_proof`
    refuses on `degraded` whatever the count, and a partial list matches more
    real entities than an empty one."""
    overlay = tmp_path / "overlay"
    (overlay / "crm" / "contacts").mkdir(parents=True)
    (overlay / "crm" / "contacts" / "quillvantage-rivera.md").write_text("---\n---\n")
    (overlay / "admin").mkdir()
    (overlay / "admin" / "executives.json").write_text('{"executives": ["wrong shape"]}')

    dl = CD.build_denylist(overlay)

    assert dl.degraded is True
    assert dl.tokens


def test_a_clean_harvest_says_nothing(tmp_path, capsys):
    """The warning must not become noise on every healthy run."""
    overlay = tmp_path / "overlay"
    (overlay / "crm" / "contacts").mkdir(parents=True)
    (overlay / "crm" / "contacts" / "marlow-rivera.md").write_text("---\nname: Marlow Rivera\n---\n")

    dl = CD.build_denylist(overlay)

    assert dl.degraded is False
    assert "harvest failed" not in capsys.readouterr().err


def test_the_content_guard_no_longer_blames_a_cause_it_did_not_check():
    """It printed "no DATA overlay" for a harvest failure too, so a malformed
    config on the operator's own machine disabled the gate under a false
    explanation."""
    src = (ROOT / "scripts" / "content-guard.py").read_text()
    assert 'denylist unavailable (no DATA overlay)' not in src
    assert "harvest failed" in src


def test_the_telegram_id_comment_matches_the_code():
    """It said ids are harvested "from the raw text of every data-config"; they
    are harvested only from files whose NAME carries fireside or roster."""
    src = (ROOT / "scripts" / "utils" / "content_denylist.py").read_text()
    assert "e-mails + Telegram-ID-shaped ints from the raw text of every" not in src
    assert "from the fireside/roster ones ONLY" in src
    assert "Telegram-ID-shaped ints from the files whose" in CD.__doc__
