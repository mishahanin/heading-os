"""Shard 06-p3: the two CRM/document generators, and the seam under them.

Six findings came in from the k3 read of `scripts/generate-client-docx.py` and
`scripts/generate-crm-dashboard.py`. Fixing the smallest of them -- a `doc.save`
with no `mkdir` -- exposed a seventh that is larger than the six put together.

1. The contents page promised four appendices and the builder emitted two.
   There is no Glossary and no Compliance & Certification Matrix anywhere in
   `generate-client-docx.py`; a partner forwarding the template shipped a TOC
   pointing at sections that do not exist, under a note telling them to update
   the page numbers. The TOC and both body headings now read one `APPENDICES`
   tuple, so they cannot drift again.

2. `Document.save()` does not create missing parents, and SEVEN generators
   under scripts/ called it on a path built from `get_outputs_dir()` with no
   `mkdir` anywhere. All seven now go through `docx_helpers.save_docx`.

3. THE ONE THAT MATTERED. `tests/test_docx_helpers.py` promised "a throwaway
   data root so a generator writes nowhere real" by setting `HEADING_OS_DATA`
   to `tmp_path / "data"` -- a directory it never created. `get_data_root()`
   honours the override only when it names a real directory and OTHERWISE FALLS
   THROUGH IN SILENCE, on this machine to the operator's live overlay. The
   sandbox had only ever worked because pre-creating the output leaves created
   the root as a side effect. Removing that scaffolding (finding 2 made it
   unnecessary) sent three generators into `.heading-os-data/outputs/` for real
   and overwrote three tracked exec-meeting documents, which were restored
   byte-identical to HEAD. The fallback is kept -- an `.env` naming a moved path
   should not brick a session -- but it now warns, and both test harnesses that
   depended on the silence create their root.

4. The console summary in `main()` carried a private copy of the exact counting
   the `_health_counts` docstring calls the old defect, so the rendered page and
   the console line disagreed by one for any unrecognised Health value.

5. The header rendered the literal words "(the configured timezone)" in every
   dashboard and PDF, on a page headed "Internal - CEO Eyes Only".

6. Overdue contacts were attributed to an executive by testing whether the
   exec's surname appeared ANYWHERE in the radar's free-text Owner cell. Both
   strings are written by one function from one slug, so the comparison is an
   equality, and the substring version put wrong names under wrong executives.

7. A corrupt `exec-registry.json` printed "Registry: 0 active executives" and
   nothing else, which is indistinguishable from a company with no executives.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import paths as paths_mod  # noqa: E402
from scripts.utils.docx_helpers import save_docx  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cd():
    return _load("crm_dashboard_k3", "scripts/generate-crm-dashboard.py")


@pytest.fixture(scope="module")
def gcd():
    return _load("client_docx_k3", "scripts/generate-client-docx.py")


# ============================================================
# 1. The data-root override that was ignored in silence
# ============================================================

def test_an_override_naming_a_real_directory_is_honoured(tmp_path, monkeypatch):
    real = tmp_path / "sandbox"
    real.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(real))
    assert paths_mod.env_data_root() == real.resolve()


def test_an_override_naming_a_missing_directory_is_refused(tmp_path, monkeypatch):
    """The whole finding, and it now RAISES rather than falling through.

    A silent None here WAS the data leak: `get_data_root()` falls through to
    the live overlay, so a harness that set the variable precisely to keep a
    write away from real data got real data, and learned about it from `git
    status` afterwards, if at all. The first fix warned and kept the fallback;
    the operator replaced that with a refusal on 2026-08-25, because a warning
    in a daemon or a scheduled run is read by nobody.
    """
    missing = tmp_path / "never-created"
    monkeypatch.setenv("HEADING_OS_DATA", str(missing))
    with pytest.raises(RuntimeError) as exc:
        paths_mod.env_data_root()
    assert str(missing) in str(exc.value), "the refusal must name the path"


def test_the_refusal_names_the_consequence_not_just_the_fault(tmp_path, monkeypatch):
    """"Not a directory" alone would not tell anyone data is now at risk."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "gone"))
    with pytest.raises(RuntimeError) as exc:
        paths_mod.env_data_root()
    message = str(exc.value).lower()
    assert "fall" in message, "the refusal never says what it refused to do"
    assert "overlay" in message or "real data" in message


def test_the_refusal_is_not_swallowed_by_an_oserror_handler(tmp_path, monkeypatch):
    """Several callers wrap filesystem work in `except OSError`.

    `NotADirectoryError` would read as the natural type here and would be
    caught by every one of them, turning the refusal back into the silence it
    replaced.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "gone"))
    with pytest.raises(RuntimeError):
        try:
            paths_mod.env_data_root()
        except OSError:                      # pragma: no cover - must not fire
            raise AssertionError(
                "an OSError handler swallowed the refusal") from None


def test_the_refusal_repeats_every_time(tmp_path, monkeypatch):
    """A once-per-process warning was fine; a once-per-process REFUSAL is not.

    The second caller is a different write to the same wrong place.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "gone"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            paths_mod.env_data_root()


def test_an_unset_override_says_nothing(monkeypatch, caplog):
    """Absent is not misconfigured; the sibling overlay is the normal path."""
    monkeypatch.delenv("HEADING_OS_DATA", raising=False)
    with caplog.at_level(logging.WARNING, logger=paths_mod.__name__):
        assert paths_mod.env_data_root() is None
    assert not caplog.records


def test_an_empty_override_says_nothing(monkeypatch, caplog):
    monkeypatch.setenv("HEADING_OS_DATA", "")
    with caplog.at_level(logging.WARNING, logger=paths_mod.__name__):
        assert paths_mod.env_data_root() is None
    assert not caplog.records


def test_a_file_is_not_a_data_root(tmp_path, monkeypatch):
    f = tmp_path / "notadir"
    f.write_text("", encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(f))
    with pytest.raises(RuntimeError):
        paths_mod.env_data_root()


def test_a_tilde_override_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "od").mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", "~/od")
    assert paths_mod.env_data_root() == (tmp_path / "od").resolve()


def test_get_data_root_uses_the_honoured_override(tmp_path, monkeypatch):
    real = tmp_path / "sandbox"
    real.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(real))
    assert paths_mod.get_data_root() == real.resolve()


def test_both_resolvers_read_the_same_helper():
    """Two copies of this check is how one of them stops being fixed."""
    src = (ROOT / "scripts" / "utils" / "workspace.py").read_text(encoding="utf-8")
    assert "env_data_root()" in src
    assert 'os.environ.get("HEADING_OS_DATA")' not in src, (
        "get_exec_data_root must not re-implement the override check")


def test_the_docx_sandbox_creates_its_own_root():
    """The harness whose isolation claim was false must not regress.

    `_sandbox` builds `tmp_path / "data"` and hands it to HEADING_OS_DATA. If
    nothing creates it, the override is refused and the generators write to
    the live overlay -- which is exactly what happened on 2026-08-24.
    """
    src = (ROOT / "tests" / "test_docx_helpers.py").read_text(encoding="utf-8")
    at = src.index('data_root = tmp_path / "data"')
    after = src[at:src.index("return data_root", at)]
    assert "data_root.mkdir(" in after


def test_the_checkpoint_harness_creates_its_own_root():
    src = (ROOT / "tests"
           / "test_checkpoint_autonomy_visibility.py").read_text(encoding="utf-8")
    at = src.index('env["HEADING_OS_DATA"]')
    assert "mkdir(" in src[max(0, at - 400):at]


# ============================================================
# 2. The save that did not create its own directory
# ============================================================

class _FakeDoc:
    def __init__(self):
        self.saved_to = None

    def save(self, path):
        # The real python-docx behaviour: no parent creation, and a missing
        # directory raises. Reproducing it is the point of the fake.
        Path(path).write_bytes(b"PK\x03\x04")
        self.saved_to = path


def test_save_docx_creates_a_missing_parent(tmp_path):
    doc = _FakeDoc()
    target = tmp_path / "outputs" / "documents" / "x.docx"
    assert not target.parent.exists()
    save_docx(doc, target)
    assert target.is_file()


def test_save_docx_creates_a_whole_missing_chain(tmp_path):
    doc = _FakeDoc()
    target = tmp_path / "a" / "b" / "c" / "d" / "x.docx"
    save_docx(doc, target)
    assert target.is_file()


def test_save_docx_returns_the_path_it_wrote(tmp_path):
    doc = _FakeDoc()
    target = tmp_path / "out" / "x.docx"
    assert save_docx(doc, target) == target


def test_save_docx_accepts_a_string_path(tmp_path):
    """Every one of the seven call sites passes a str, not a Path."""
    doc = _FakeDoc()
    target = tmp_path / "out" / "x.docx"
    save_docx(doc, str(target))
    assert target.is_file()


def test_an_existing_directory_is_not_an_error(tmp_path):
    doc = _FakeDoc()
    (tmp_path / "out").mkdir()
    save_docx(doc, tmp_path / "out" / "x.docx")
    assert (tmp_path / "out" / "x.docx").is_file()


@pytest.mark.parametrize("script", [
    "generate-odunone-docx.py",
    "generate-client-docx.py",
    "generate-usecases-docx.py",
    "md-to-docx-proposal.py",
    "md-to-docx-competitive.py",
    "md-to-docx-letter.py",
    "md-to-docx-charter.py",
])
def test_no_generator_calls_save_directly(script):
    """A new `doc.save(...)` is a new FileNotFoundError on a fresh data root."""
    src = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "doc.save(" not in src, f"{script} must save through save_docx"
    assert "save_docx(" in src


# ============================================================
# 3. The contents page that promised sections nobody wrote
# ============================================================

def test_the_toc_names_only_appendices_that_exist(gcd):
    listed = {f"Appendix {letter}" for letter, _ in gcd.APPENDICES}
    assert listed == {"Appendix A", "Appendix D"}


def test_the_glossary_is_not_promised_anywhere(gcd):
    src = (ROOT / "scripts" / "generate-client-docx.py").read_text(encoding="utf-8")
    assert "Appendix B: Glossary" not in src
    assert "Appendix C: Compliance" not in src


def test_a_body_heading_reads_the_same_list(gcd):
    assert gcd.appendix_heading("A") == "Appendix A: Technical Specifications"
    assert gcd.appendix_heading("D") == "Appendix D: Competitive Advantage Summary"


def test_an_unknown_appendix_raises_rather_than_half_rendering(gcd):
    with pytest.raises(KeyError):
        gcd.appendix_heading("B")


def test_no_appendix_heading_is_typed_by_hand(gcd):
    """The drift is only impossible while both sides read APPENDICES."""
    src = (ROOT / "scripts" / "generate-client-docx.py").read_text(encoding="utf-8")
    body = src[src.index("def build_document"):]
    assert "'Appendix A:" not in body
    assert "'Appendix D:" not in body


# ============================================================
# 4. The console summary that disagreed with the page
# ============================================================

def test_an_unrecognised_health_lands_in_gray(cd, monkeypatch):
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    counts = cd._health_counts([{"name": "A", "health": "amber"}])
    assert counts["GRAY"] == 1


def test_the_counts_sum_to_the_contact_total(cd, monkeypatch):
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    contacts = [{"name": "A", "health": "amber"}, {"name": "B", "health": ""},
                {"name": "C", "health": "RED"}, {"name": "D", "health": "green"}]
    counts = cd._health_counts(contacts)
    assert sum(counts.values()) == len(contacts)


def test_the_console_summary_does_not_count_for_itself(cd):
    """`main()` carried a private copy of the pattern the helper replaced."""
    src = (ROOT / "scripts" / "generate-crm-dashboard.py").read_text(encoding="utf-8")
    main_body = src[src.index("radar_contacts = collect_radar()"):]
    assert "health_counts = _health_counts(radar_contacts)" in main_body
    assert "if h in health_counts:" not in main_body


def test_one_bad_row_warns_once_however_many_callers_ask(cd, monkeypatch, capsys):
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    rows = [{"name": "Vesper Lynd", "health": "amber"}]
    for _ in range(3):
        cd._health_counts(rows)
    assert capsys.readouterr().err.count("counted as GRAY") == 1


def test_two_different_bad_rows_each_get_a_warning(cd, monkeypatch, capsys):
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    cd._health_counts([{"name": "Vesper Lynd", "health": "amber"},
                       {"name": "Felix Leiter", "health": "BLUE"}])
    assert capsys.readouterr().err.count("counted as GRAY") == 2


def test_one_contact_with_two_bad_values_is_warned_about_twice(cd, monkeypatch,
                                                                capsys):
    """The dedupe key is (name, value), and the value half earns its place.

    Keying on the name alone would report the first bad Health cell a
    contact ever had and stay quiet about the next one. The operator fixes
    "amber", re-runs, sees silence, and never learns the row now reads
    "BLUE". Deduping must not turn into forgetting.
    """
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    cd._health_counts([{"name": "Vesper Lynd", "health": "amber"},
                       {"name": "Vesper Lynd", "health": "BLUE"}])
    err = capsys.readouterr().err
    assert err.count("counted as GRAY") == 2
    assert "amber" in err and "BLUE" in err


def test_the_warning_names_the_row_so_it_can_be_fixed(cd, monkeypatch, capsys):
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())
    cd._health_counts([{"name": "Rene Mathis", "health": "teal"}])
    err = capsys.readouterr().err
    assert "Rene Mathis" in err and "teal" in err


# ============================================================
# 5. The header that shipped its own placeholder
# ============================================================

def test_the_header_does_not_render_the_placeholder_words(cd):
    html = cd.build_header("", 3, 40)
    assert "the configured timezone" not in html


def test_the_header_names_the_real_zone(cd):
    zone = cd.NOW.tzname()
    html = cd.build_header("", 3, 40)
    if zone:
        assert zone in html


def test_a_nameless_zone_leaves_no_empty_brackets(cd, monkeypatch):
    """`tzname()` returns None for a zone that has no abbreviation.

    Asserting against the live `NOW` proves nothing: the configured zone
    always names itself, so the guarded branch is never entered and a header
    hard-coded to print the parenthetical would pass anyway. A naive datetime
    is the shape that answers None.

    And the symptom is "14:32 ()", NOT "14:32 (None)" -- `esc()` maps a falsy
    value to the empty string. Asserting the absence of the word "None" was
    therefore asserting something `esc` already guaranteed, and it let an
    unguarded f-string through. The bare brackets are what a reader sees.
    """
    from datetime import datetime as _dt
    # DTZ001 is exactly right in general and exactly wrong here: the NAIVE
    # datetime is the fixture. It is the only construction whose `tzname()`
    # answers None, which is the branch under test.
    monkeypatch.setattr(cd, "NOW", _dt(2026, 8, 24, 14, 32))  # noqa: DTZ001
    assert cd.NOW.tzname() is None, "fixture must actually reach the None branch"
    html = cd.build_header("", 1, 1)
    assert "14:32" in html
    assert "()" not in html
    assert "None" not in html


# ============================================================
# 6. Overdue names under the wrong executive
# ============================================================

def _exec(name, slug, **kw):
    base = {"name": name, "slug": slug, "title": "", "total": 0,
            "red": 0, "yellow": 0, "green": 0, "gray": 0}
    base.update(kw)
    return base


def _radar(name, owner, health="RED"):
    return {"name": name, "company": "", "type": "", "owner": owner,
            "last_touch": "", "days_since": 1, "health": health, "cadence": ""}


def test_a_different_person_sharing_a_surname_is_not_claimed(cd):
    """"Ann Li" used to claim every contact owned by "Julia Li"."""
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Vesper Lynd", "J. Li")], {})
    assert "Vesper Lynd" not in html


def test_a_surname_buried_in_another_word_is_not_claimed(cd):
    html = cd.build_exec_scorecards(
        [_exec("R. Orr", "rob-orr", red=1)],
        [_radar("Felix Leiter", "M. Torres")], {})
    assert "Felix Leiter" not in html


def test_a_team_owner_is_not_claimed_by_a_matching_surname(cd):
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Rene Mathis", "Compliance Team")], {})
    assert "Rene Mathis" not in html


def test_the_real_owner_is_still_matched(cd):
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Vesper Lynd", "A. Li")], {})
    assert "Vesper Lynd" in html


def test_the_match_ignores_case_and_surrounding_space(cd):
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Vesper Lynd", "  a. li ")], {})
    assert "Vesper Lynd" in html


def test_a_healthy_contact_is_never_listed_as_overdue(cd):
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li")],
        [_radar("Vesper Lynd", "A. Li", health="GREEN")], {})
    assert "Vesper Lynd" not in html


def test_an_owner_name_that_merely_starts_the_same_is_not_claimed(cd):
    """Equality, not "the exec's name appears in the owner cell".

    Turning the comparison around is as wrong as the surname substring it
    replaced, just less obviously: "A. Li" is a prefix of "A. Lindt", so
    exec A. Li would silently collect a different person's overdue contacts.
    """
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Vesper Lynd", "A. Lindt")], {})
    assert "Vesper Lynd" not in html


def test_an_empty_owner_matches_nobody(cd):
    html = cd.build_exec_scorecards(
        [_exec("A. Li", "ann-li", red=1)],
        [_radar("Vesper Lynd", "")], {})
    assert "Vesper Lynd" not in html


def test_a_nameless_exec_does_not_collect_every_ownerless_contact(cd):
    """Why `and c["owner"]` stays, now that the match is an equality.

    Equality already rejects an empty owner for any exec with a name. The
    guard earns its place only in the degenerate case: an exec section whose
    name parsed as blank would have `owner_key == ""`, and every radar row
    with an empty Owner cell would then equal it and land on that card. That
    is a scorecard silently claiming contacts nobody owns.
    """
    html = cd.build_exec_scorecards(
        [_exec("", "unnamed", red=2)],
        [_radar("Vesper Lynd", ""), _radar("Felix Leiter", "")], {})
    assert "Vesper Lynd" not in html
    assert "Felix Leiter" not in html


def test_at_most_three_overdue_names_are_listed(cd):
    rows = [_radar(f"Contact {i}", "A. Li") for i in range(6)]
    html = cd.build_exec_scorecards([_exec("A. Li", "ann-li", red=6)], rows, {})
    assert sum(f"Contact {i}" in html for i in range(6)) == 3


# ============================================================
# 7. The registry that failed without saying so
# ============================================================

def test_a_corrupt_registry_is_named_on_stderr(cd, tmp_path, monkeypatch, capsys):
    bad = tmp_path / "exec-registry.json"
    bad.write_text('{"executives": [],}', encoding="utf-8")
    monkeypatch.setattr(cd, "EXEC_REGISTRY_FILE", bad)
    cd.collect_exec_registry()
    assert "unreadable" in capsys.readouterr().err


def test_the_corrupt_registry_warning_names_the_file(cd, tmp_path, monkeypatch,
                                                      capsys):
    bad = tmp_path / "exec-registry.json"
    bad.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(cd, "EXEC_REGISTRY_FILE", bad)
    cd.collect_exec_registry()
    assert str(bad) in capsys.readouterr().err


def test_a_corrupt_registry_still_degrades_to_empty(cd, tmp_path, monkeypatch):
    bad = tmp_path / "exec-registry.json"
    bad.write_text("{[}", encoding="utf-8")
    monkeypatch.setattr(cd, "EXEC_REGISTRY_FILE", bad)
    assert cd.collect_exec_registry()["executives"] == []


def test_an_absent_registry_is_not_an_error(cd, tmp_path, monkeypatch, capsys):
    """Missing and corrupt are different facts; only one is a fault."""
    monkeypatch.setattr(cd, "EXEC_REGISTRY_FILE", tmp_path / "nope.json")
    assert cd.collect_exec_registry()["executives"] == []
    assert "unreadable" not in capsys.readouterr().err


def test_a_good_registry_is_returned_unchanged(cd, tmp_path, monkeypatch, capsys):
    good = tmp_path / "exec-registry.json"
    good.write_text(json.dumps(
        {"version": "1.0",
         "executives": [{"slug": "vlynd", "status": "active", "title": "CFO"}]}),
        encoding="utf-8")
    monkeypatch.setattr(cd, "EXEC_REGISTRY_FILE", good)
    assert cd.collect_exec_registry()["executives"][0]["slug"] == "vlynd"
    assert capsys.readouterr().err == ""
