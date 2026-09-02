"""Six findings in the CRM Command Center, three of which printed a wrong fact.

`scripts/generate-crm-dashboard.py` writes a page footed "Internal - CEO Eyes
Only" and a JSON sidecar beside it. A generator that crashes is loud. A
generator that renders a confident number nobody measured is not, and three of
the six below are that shape.

1. *A function-local import outside its own handler.* `collect_heartbeat` opened
   with `from scripts.utils.workspace import get_all_active_exec_slugs,
   get_per_exec_contacts_dir` on the line ABOVE the `try` whose `except` names
   `ImportError`. The only statement in that function that can raise the error
   the handler was written for was the one statement the handler could not see,
   so a renamed or missing helper took the whole dashboard down while every
   other collector in the file degrades and says so.

2. *One contact, two health classes, one page.* `collect_radar` upper-cased the
   Health cell and stopped there. A row reading "amber" was counted GRAY by the
   cards, sorted BELOW every GRAY in the table (`order.get(..., 4)` against a
   GRAY of 3), and badged with its own raw text. The page disagreed with itself
   about a single contact. Health is now resolved once, at the collector, and
   the table asks the same helper the cards do.

3. *Substring company matching invented correlations.* `"Meridian"` and
   `"Meridian Dental Group"` were reported as a CRM-to-pipeline match, with a
   stage and a deal value attached, indistinguishable from a match on the same
   company. Exact is too strict for real pipeline text (a deal row carrying a
   legal suffix would drop out), so the weaker matches survive and are now
   labelled: every correlation carries `match: exact | partial`, and the table
   renders the partial ones as visibly weaker.

4. *Two keys initialised, never populated, exported anyway.* Every exec object
   in `crm-command-center.json` shipped `"types": {}, "contacts": []`. Grep
   found `"types"` exactly once in the file, in the initialiser, and no reader
   anywhere in the repository. An empty structure that reads as a measurement is
   worse than an absent one, so both are gone. The underlying numbers do exist
   in `ownership-map.md`, which `aggregate-crm.generate_ownership_map` writes as
   a per-exec Type table and contact list, so populating them later is a parser
   away.

5. *A hardcoded "Top 15" beside a `limit` argument.* The populated branch of
   `build_top_overdue` honoured `limit`; the empty branch printed 15 whatever it
   was given. Latent at the single `limit=15` call site, and wrong the first
   time anyone reuses the function.

6. *A preflight warning that was wrong on the ordinary path.* Every cold start
   printed "Run aggregate-crm.py first" to stderr, then ran aggregate-crm.py,
   then succeeded. That teaches the operator to skip the channel that also
   carries the real data-integrity warnings from `_health_counts` and
   `collect_exec_registry`. The check now runs AFTER the refresh that would
   satisfy it, and fires only when the data is still missing.

No dashboard is rendered from real data here. Every test supplies its own text
or its own fixture tree under `tmp_path`; the two that drive `main()` stub every
collector and pass `--output-dir`, so no data-root helper is reached.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "generate-crm-dashboard.py"


def _load(name: str = "crm_dashboard_stated"):
    spec = importlib.util.spec_from_file_location(name, str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cd():
    return _load()


@pytest.fixture(autouse=True)
def _quiet_health_dedupe(cd, monkeypatch):
    """The warn-once set is process-global, so tests must not inherit it."""
    monkeypatch.setattr(cd, "_HEALTH_WARNED", set())


def _radar(name, health="RED", company="", owner="", days_since=1):
    return {"name": name, "company": company, "type": "", "owner": owner,
            "last_touch": "", "days_since": days_since, "health": health,
            "cadence": ""}


def _deal(company, stage="Proposal", value="$1", owner="Bo Kessler"):
    return {"company": company, "country": "", "stage": stage,
            "value": value, "owner": owner}


def _tree():
    return ast.parse(SCRIPT.read_text(encoding="utf-8"))


def _func(name):
    fns = [n for n in _tree().body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and n.name == name]
    assert len(fns) == 1, f"expected exactly one {name} in the generator"
    return fns[0]


# ============================================================
# 1. The import the handler could not see
# ============================================================

def _names_import_error(handler):
    node = handler.type
    if node is None:
        return True
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(isinstance(p, ast.Name) and p.id == "ImportError" for p in parts)


def test_a_missing_contacts_helper_does_not_end_the_whole_run(cd, monkeypatch,
                                                              capsys):
    """The exact failure the `except ImportError` was written for.

    `get_per_exec_contacts_dir` is read one line above the try, so deleting it
    raises out of `collect_heartbeat`, out of `main`, and the CEO gets no page
    at all rather than a page with an empty heartbeat row.
    """
    import scripts.utils.workspace as ws
    monkeypatch.delattr(ws, "get_per_exec_contacts_dir")
    assert cd.collect_heartbeat() == {}
    assert "heartbeat" in capsys.readouterr().err


def test_a_missing_roster_helper_does_not_end_the_whole_run(cd, monkeypatch,
                                                            capsys):
    import scripts.utils.workspace as ws
    monkeypatch.delattr(ws, "get_all_active_exec_slugs")
    assert cd.collect_heartbeat() == {}
    assert "heartbeat" in capsys.readouterr().err


def test_the_import_sits_inside_the_try_that_names_importerror(cd):
    """Behaviour is pinned above; this pins the shape that produced it.

    A later edit that hoists the import back out of the try restores the exact
    defect while both behavioural tests keep passing only for as long as the
    handler happens to catch it somewhere else.
    """
    fn = _func("collect_heartbeat")
    guarded: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and any(
                _names_import_error(h) for h in node.handlers):
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))

    imports = [n for n in ast.walk(fn)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports, (
        "collect_heartbeat imports nothing, so this guard measures nothing")
    loose = [f"line {n.lineno}: {ast.unparse(n)}"
             for n in imports if id(n) not in guarded]
    assert not loose, (
        f"an import sits outside the handler written to catch it: {loose}")


# ============================================================
# 2. One contact, one health class, whichever surface asks
# ============================================================

def test_an_unrecognised_health_sorts_with_the_grays(cd):
    """`order.get(h, 4)` put it below GRAY, which is a fifth class in a table
    that has four."""
    rows = [
        _radar("Ada Prewitt", health="GRAY", days_since=1),
        _radar("Bo Kessler", health="amber", days_since=99),
        _radar("Cyd Marlowe", health="GREEN", days_since=5),
    ]
    html = cd.build_radar_table(rows)
    assert html.index("Cyd Marlowe") < html.index("Bo Kessler")
    assert html.index("Bo Kessler") < html.index("Ada Prewitt"), (
        "the unrecognised row used to sort below every GRAY instead of among "
        "them, so the oldest overdue contact landed last")


def test_an_unrecognised_health_is_labelled_with_the_class_it_was_given(cd):
    html = cd.build_radar_table([_radar("Ada Prewitt", health="amber")])
    assert "badge-gray" in html
    assert ">amber<" not in html and ">AMBER<" not in html, (
        "the badge carried the raw text while the cards counted it as GRAY")
    assert ">GRAY<" in html


def test_the_cards_and_the_table_agree_about_one_contact(cd):
    rows = [_radar("Ada Prewitt", health="amber")]
    assert cd._health_counts(rows)["GRAY"] == 1
    assert cd.build_radar_table(rows).count("badge-gray") == 1


def test_the_collector_resolves_health_before_anything_renders_it(cd, tmp_path,
                                                                  monkeypatch):
    radar = tmp_path / "company-radar.md"
    radar.write_text(
        "| Name | Company | Type | Owner | Last Touch | Cadence | Health |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Ada Prewitt | Northwind Freight | partner | Bo Kessler "
        "| 2026-08-01 | 30 | amber |\n",
        encoding="utf-8")
    monkeypatch.setattr(cd, "company_radar_file", lambda p=radar: p)
    rows = cd.collect_radar()
    assert rows[0]["health"] == "GRAY", (
        "upper() alone is not normalisation; every consumer downstream then "
        "guessed for itself")


def test_a_known_health_is_carried_through_untouched(cd, tmp_path, monkeypatch):
    radar = tmp_path / "company-radar.md"
    radar.write_text(
        "| Name | Company | Type | Owner | Last Touch | Cadence | Health |\n"
        "|---|---|---|---|---|---|---|\n"
        "| Ada Prewitt | Northwind Freight | partner | Bo Kessler "
        "| 2026-08-01 | 30 | red |\n",
        encoding="utf-8")
    monkeypatch.setattr(cd, "company_radar_file", lambda p=radar: p)
    assert cd.collect_radar()[0]["health"] == "RED"


def test_the_unrecognised_value_is_still_named_on_stderr(cd, capsys):
    """Resolving to GRAY must not become hiding the bad cell."""
    cd.build_radar_table([_radar("Ada Prewitt", health="amber")])
    err = capsys.readouterr().err
    assert "Ada Prewitt" in err and "amber" in err


# ============================================================
# 3. A match that is not the same company says so
# ============================================================

def test_a_company_that_merely_contains_another_is_not_reported_as_equal(cd):
    matches = cd.correlate_pipeline_crm(
        [_radar("Ada Prewitt", company="Meridian")],
        [_deal("Meridian Dental Group")])
    assert len(matches) == 1
    assert matches[0]["match"] == "partial", (
        "a substring hit was shipped with a stage and a deal value attached, "
        "on a page the CEO reads as fact")


def test_the_same_company_is_reported_as_exact(cd):
    matches = cd.correlate_pipeline_crm(
        [_radar("Ada Prewitt", company="Meridian Dental Group")],
        [_deal("Meridian Dental Group")])
    assert matches[0]["match"] == "exact"


def test_exactness_survives_case_and_surrounding_space(cd):
    matches = cd.correlate_pipeline_crm(
        [_radar("Ada Prewitt", company="  meridian dental group ")],
        [_deal("Meridian Dental Group")])
    assert matches[0]["match"] == "exact"


def test_an_unrelated_company_still_matches_nothing(cd):
    assert cd.correlate_pipeline_crm(
        [_radar("Ada Prewitt", company="Northwind Freight")],
        [_deal("Meridian Dental Group")]) == []


def test_a_partial_match_renders_visibly_weaker_than_an_exact_one(cd):
    partial = cd.build_pipeline_correlation([{
        "deal_company": "Meridian Dental Group", "contact_name": "Ada Prewitt",
        "contact_company": "Meridian", "stage": "Proposal", "value": "$1",
        "crm-health": "RED", "crm_owner": "", "deal_owner": "",
        "match": "partial"}])
    exact = cd.build_pipeline_correlation([{
        "deal_company": "Meridian Dental Group", "contact_name": "Ada Prewitt",
        "contact_company": "Meridian Dental Group", "stage": "Proposal",
        "value": "$1", "crm-health": "RED", "crm_owner": "", "deal_owner": "",
        "match": "exact"}])
    assert "partial" in partial.lower()
    assert "partial" not in exact.lower()
    assert partial != exact, "the two used to render identically"


def test_a_correlation_with_no_match_field_renders_as_the_weaker_one(cd):
    """An unlabelled row is not evidence of an exact match."""
    html = cd.build_pipeline_correlation([{
        "deal_company": "Meridian Dental Group", "contact_name": "Ada Prewitt",
        "contact_company": "Meridian", "stage": "Proposal", "value": "$1",
        "crm-health": "RED", "crm_owner": "", "deal_owner": ""}])
    assert "partial" in html.lower()


# ============================================================
# 4. Nothing ships as measured that was never measured
# ============================================================

def _ownership(tmp_path, monkeypatch, cd):
    f = tmp_path / "ownership-map.md"
    f.write_text(
        "## Ada Prewitt (`ada-prewitt`)\n"
        "- **Total contacts:** 4\n"
        "- **Health:** 1 red, 0 yellow, 3 green, 0 gray\n",
        encoding="utf-8")
    monkeypatch.setattr(cd, "ownership_map_file", lambda p=f: f)
    return cd.collect_ownership({"executives": []})


def test_no_exec_object_carries_an_empty_structure_it_never_filled(
        cd, tmp_path, monkeypatch):
    execs = _ownership(tmp_path, monkeypatch, cd)
    assert len(execs) == 1
    assert "types" not in execs[0]
    assert "contacts" not in execs[0]


def test_the_counts_that_were_measured_are_still_there(cd, tmp_path, monkeypatch):
    ex = _ownership(tmp_path, monkeypatch, cd)[0]
    assert ex["total"] == 4
    assert (ex["red"], ex["yellow"], ex["green"], ex["gray"]) == (1, 0, 3, 0)


def test_the_json_sidecar_ships_no_empty_measurement(cd, tmp_path, monkeypatch):
    execs = _ownership(tmp_path, monkeypatch, cd)
    data = cd.build_json_export([], execs, [], {}, [])
    assert "types" not in data["executives"][0]
    assert "contacts" not in data["executives"][0]
    assert data["contacts"] == [], (
        "the top-level contacts key IS populated from radar and stays")


def test_the_scorecards_still_render_without_the_removed_keys(cd, tmp_path,
                                                              monkeypatch):
    execs = _ownership(tmp_path, monkeypatch, cd)
    html = cd.build_exec_scorecards(execs, [], {})
    assert "Ada Prewitt" in html


# ============================================================
# 5. The heading reads the argument it was given
# ============================================================

def test_the_empty_overdue_heading_honours_the_limit(cd):
    html = cd.build_top_overdue([], limit=5)
    assert "Top 5 Overdue" in html
    assert "Top 15" not in html


def test_the_default_overdue_heading_still_reads_fifteen(cd):
    assert "Top 15 Overdue" in cd.build_top_overdue([])


def test_the_populated_heading_is_unchanged(cd):
    html = cd.build_top_overdue(
        [_radar("Ada Prewitt", health="RED", days_since=40)], limit=5)
    assert "Top 1 Overdue" in html


# ============================================================
# 6. A warning that is true when it fires
# ============================================================

def _main_argv(monkeypatch, out_dir):
    monkeypatch.setattr(sys, "argv", ["generate-crm-dashboard.py", "--json",
                                      "--output-dir", str(out_dir)])


def _stub_collectors(cd, monkeypatch):
    monkeypatch.setattr(cd, "collect_exec_registry",
                        lambda: {"version": "1.0", "executives": []})
    monkeypatch.setattr(cd, "collect_radar", list)
    monkeypatch.setattr(cd, "collect_ownership", lambda _r: [])
    monkeypatch.setattr(cd, "collect_shared_contacts", list)
    monkeypatch.setattr(cd, "collect_heartbeat", dict)
    monkeypatch.setattr(cd, "collect_pipeline_companies", list)


def test_a_cold_start_does_not_tell_the_operator_to_run_what_it_just_ran(
        cd, tmp_path, monkeypatch, capsys):
    """The ordinary path, and the one the warning fired on every time.

    The aggregate directory does not exist, `aggregate-crm.py` creates it, the
    run succeeds. Anything on stderr here is noise on the channel that also
    carries the real data-integrity warnings.
    """
    aggregated = tmp_path / "aggregated"

    def _refresh():
        aggregated.mkdir(parents=True, exist_ok=True)
        return True

    monkeypatch.setattr(cd, "aggregated_dir", lambda p=aggregated: p)
    monkeypatch.setattr(cd, "refresh_aggregated_data", _refresh)
    _stub_collectors(cd, monkeypatch)
    _main_argv(monkeypatch, tmp_path / "out")

    cd.main()

    err = capsys.readouterr().err
    assert "Run aggregate-crm.py first" not in err
    assert err.strip() == "", f"a clean cold start still wrote to stderr: {err!r}"
    assert (tmp_path / "out" / "crm-command-center.json").is_file()


def test_an_aggregate_still_missing_after_the_refresh_is_reported(
        cd, tmp_path, monkeypatch, capsys):
    """Moving the check must not delete it. This is the case it exists for."""
    aggregated = tmp_path / "aggregated"
    monkeypatch.setattr(cd, "aggregated_dir", lambda p=aggregated: p)
    monkeypatch.setattr(cd, "refresh_aggregated_data", lambda: False)
    _stub_collectors(cd, monkeypatch)
    _main_argv(monkeypatch, tmp_path / "out")

    cd.main()

    err = capsys.readouterr().err
    assert str(aggregated) in err
    assert "Run aggregate-crm.py first" not in err, (
        "the run already tried; repeating the instruction is the defect")


def test_the_preflight_runs_after_the_refresh_that_would_satisfy_it(cd):
    main_fn = _func("main")
    first: dict[str, int] = {}
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            first.setdefault(node.func.id, node.lineno)
    assert "refresh_aggregated_data" in first
    assert "warn_if_aggregate_missing" in first
    assert first["refresh_aggregated_data"] < first["warn_if_aggregate_missing"]
