#!/usr/bin/env python3
"""Shard 10-p3: six findings in the Odin brain tooling, four of them claims.

The headline is measurable on the live brain. `collect_brain_files` orders each
subdirectory by FILENAME descending. For a slug-named note that is
reverse-alphabetical and carries no time information at all, and INDEX.md's
"Recent" section sliced THAT order before sorting by date, so a note the slice
had already dropped could never come back. Measured 2026-08-25 over the real
brain: the three positions printed under "Recent" were created 2026-07-29,
2026-04-10 and 2026-05-28, while the newest position (2026-08-06) was not shown
at all. 63 of 67 positions are slug-named, so this was the normal case.

Three of the remaining five are the repo's "a tool says only what its method
established" class:

  - `find_stale_positions` tested a free-text field for truthiness and called
    the result stale. 67 of 67 live positions carry one, and
    `memory-hygiene.py` printed "Odin stale positions: 67" to a human. The
    evaluation is real, but it belongs to the /odin skill downstream; this
    report is not that skill.
  - `find_stale_seeds` swallowed a parse failure with a bare `pass`, so a seed
    it could not age vanished from a count that then asserted completeness.
  - `odin_brain_lint`'s `rel()` fell back to the last path component when a seam
    resolved outside the personal root. That globbed to nothing, which
    `_external_entities` documents as "the tree is absent on this machine" -- so
    a THREADS_ROOT pointing at a real directory was reported as a directory that
    does not exist, and every namespaced link into it stopped being checked.

And two ordinary defects: `_fm_list` read only the inline list form while its
sibling tool read both, and `_run_headless_propose` stat'd files twice without a
guard inside a function whose contract says "NEVER raises".

Run: .venv/bin/python -m pytest tests/test_a_recent_list_that_was_only_alphabetical.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


obh = _load("scripts/odin-brain-health.py", "odin_brain_health_10p3")
oc = _load("scripts/odin-cadence.py", "odin_cadence_10p3")
ocn = _load("scripts/odin-cadence-notify.py", "odin_cadence_notify_10p3")
obl = _load("scripts/odin_brain_lint.py", "odin_brain_lint_10p3")
mh = _load("scripts/memory-hygiene.py", "memory_hygiene_10p3")


def write(path: Path, fm: dict, body: str = "body\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n" + body,
                    encoding="utf-8")
    return path


# ============================================================
# Finding 2 -- "Recent" was reverse-alphabetical
# ============================================================

@pytest.fixture
def positions(tmp_path):
    """Three slug-named positions whose ALPHABET order inverts their date order."""
    d = tmp_path / "positions"
    write(d / "aaa-newest.md", {"title": "Newest", "created": "2026-08-06"})
    write(d / "mmm-middle.md", {"title": "Middle", "created": "2026-05-28"})
    write(d / "zzz-oldest.md", {"title": "Oldest", "created": "2026-04-10"})
    write(d / "qqq-ancient.md", {"title": "Ancient", "created": "2025-01-01"})
    files = {"positions": sorted(d.glob("*.md"), key=lambda f: f.name, reverse=True)}
    return files


def _titles(rows):
    return [label for _d, label in rows]


def test_recent_picks_by_date_not_by_filename(positions):
    rows = obh._recent_rows(positions, "positions", ("created",), 3,
                            lambda fm, f: fm["title"])
    assert _titles(rows) == ["Newest", "Middle", "Oldest"]


def test_the_newest_note_is_not_dropped_by_an_alphabetical_slice(positions):
    """The exact live failure: `aaa-newest.md` sorts LAST under reverse-name
    order, so the [:3] slice never saw it and the later date sort could not
    bring it back."""
    by_name = positions["positions"][:3]
    assert "aaa-newest.md" not in [f.name for f in by_name], (
        "the fixture must reproduce the drop, or it proves nothing")
    rows = obh._recent_rows(positions, "positions", ("created",), 3,
                            lambda fm, f: fm["title"])
    assert "Newest" in _titles(rows)


def test_the_limit_is_still_respected(positions):
    rows = obh._recent_rows(positions, "positions", ("created",), 2,
                            lambda fm, f: fm["title"])
    assert len(rows) == 2


def test_a_note_with_no_date_sorts_to_the_bottom_instead_of_raising(tmp_path):
    d = tmp_path / "sources"
    write(d / "dated.md", {"title": "Dated", "ingested": "2026-01-01"})
    write(d / "undated.md", {"title": "Undated"})
    files = {"sources": sorted(d.glob("*.md"))}
    rows = obh._recent_rows(files, "sources", ("ingested", "created"), 5,
                            lambda fm, f: fm["title"])
    assert _titles(rows) == ["Dated", "Undated"]


def test_a_timestamp_and_a_bare_date_order_against_each_other(tmp_path):
    """`yaml.safe_load` gives a datetime for one and a date for the other, and
    Python 3 refuses to compare them. `_date_key` is why this does not raise."""
    d = tmp_path / "episodes"
    write(d / "a.md",
          {"title": "WithTime", "date": dt.datetime(2026, 3, 1, 9, 30)})  # noqa: DTZ001 - naive on purpose: this is exactly what yaml.safe_load returns
    write(d / "b.md", {"title": "BareDate", "date": dt.date(2026, 6, 1)})
    files = {"episodes": sorted(d.glob("*.md"))}
    rows = obh._recent_rows(files, "episodes", ("date", "created"), 5,
                            lambda fm, f: fm["title"])
    assert _titles(rows) == ["BareDate", "WithTime"]


def test_the_first_date_field_that_carries_a_value_wins(tmp_path):
    d = tmp_path / "sources"
    write(d / "a.md", {"title": "A", "ingested": "2026-08-01", "created": "2020-01-01"})
    files = {"sources": sorted(d.glob("*.md"))}
    rows = obh._recent_rows(files, "sources", ("ingested", "created"), 5,
                            lambda fm, f: fm["title"])
    assert rows[0][0] == "2026-08-01"


def test_an_empty_first_field_falls_through_to_the_next(tmp_path):
    """`fm.get("ingested", fm.get("created"))` only fell through on ABSENCE;
    a present-but-empty field won and sorted the note to the bottom."""
    d = tmp_path / "sources"
    write(d / "a.md", {"title": "A", "ingested": "", "created": "2026-08-01"})
    files = {"sources": sorted(d.glob("*.md"))}
    rows = obh._recent_rows(files, "sources", ("ingested", "created"), 5,
                            lambda fm, f: fm["title"])
    assert rows[0][0] == "2026-08-01"


def test_the_index_page_itself_shows_the_newest_position(positions, tmp_path):
    """End to end through `generate_index`, not just the helper. The call site
    is where the old slice lived, so a helper-only test leaves it unpinned."""
    files = {k: [] for k in ("sources", "principles", "positions", "episodes",
                             "conflicts", "reference")}
    files["positions"] = positions["positions"]
    page = obh.generate_index(files)
    recent = page.split("## Recent", 1)[1]
    assert "Position formed: Newest" in recent
    assert "Position formed: Ancient" not in recent, "the limit still applies"
    assert recent.index("Newest") < recent.index("Middle") < recent.index("Oldest")


# ============================================================
# Finding 3 -- staleness nobody measured
# ============================================================

def test_the_collector_does_not_claim_to_evaluate_the_condition():
    summary = obh.find_stale_positions.__doc__.splitlines()[0]
    assert "Nothing is evaluated here" in summary
    assert "might be met" not in summary, (
        "the summary line is what a reader takes as the contract")


def test_every_position_carrying_a_condition_is_returned(tmp_path):
    """Pinning the actual behaviour: it is a truthiness test, nothing more. On
    the live brain that means 67 of 67."""
    d = tmp_path / "positions"
    write(d / "a.md", {"title": "A", "revisit_when": "if the market moves"})
    write(d / "b.md", {"title": "B", "revisit_when": "when Q3 lands"})
    write(d / "c.md", {"title": "C"})
    files = {"positions": sorted(d.glob("*.md"))}
    out = obh.find_stale_positions(files)
    assert {r["title"] for r in out} == {"A", "B"}


def test_the_human_facing_report_no_longer_calls_them_stale():
    src = (ROOT / "scripts/memory-hygiene.py").read_text(encoding="utf-8")
    assert '("stale_positions", "Odin stale positions")' not in src
    assert "not evaluated" in src


def test_the_json_key_is_unchanged_because_a_skill_documents_it():
    """The key is the contract with `/odin`, whose own reference describes it
    correctly as "conditions to evaluate". Renaming it would break a consumer
    to fix a sentence."""
    ref = (ROOT / ".claude/skills/odin/references/compile-pipeline.md").read_text(
        encoding="utf-8")
    assert "`stale_positions`" in ref
    src = (ROOT / "scripts/odin-brain-health.py").read_text(encoding="utf-8")
    assert '"stale_positions": find_stale_positions(files)' in src


# ============================================================
# Finding 5 -- the date with a time on it
# ============================================================

def test_a_yaml_timestamp_is_a_datetime_that_fromisoformat_rejects():
    """The premise, pinned on this repo's interpreter."""
    value = yaml.safe_load("created: 2026-01-01 09:30:00")["created"]
    assert isinstance(value, dt.datetime)
    with pytest.raises(ValueError):
        dt.date.fromisoformat(str(value))


def test_as_date_reads_every_shape_the_frontmatter_produces():
    assert obh._as_date(dt.datetime(2026, 1, 1, 9, 30)) == dt.date(2026, 1, 1)  # noqa: DTZ001 - naive on purpose: this is exactly what yaml.safe_load returns
    assert obh._as_date(dt.date(2026, 1, 1)) == dt.date(2026, 1, 1)
    assert obh._as_date("2026-02-03") == dt.date(2026, 2, 3)
    assert obh._as_date(" 2026-02-03 ") == dt.date(2026, 2, 3)


def test_a_seed_dated_with_a_timestamp_is_now_aged(tmp_path):
    d = tmp_path / "sources"
    write(d / "seed.md", {"title": "Seed", "status": "seed",
                          "created": dt.datetime(2020, 1, 1, 9, 30)})  # noqa: DTZ001 - naive on purpose: this is exactly what yaml.safe_load returns
    files = {k: [] for k in ("sources", "principles", "positions", "reference")}
    files["sources"] = sorted(d.glob("*.md"))
    out = obh.find_stale_seeds(files)
    assert [r["title"] for r in out] == ["Seed"]


def test_a_genuinely_unreadable_date_is_reported_not_swallowed(tmp_path, capsys):
    """`except (ValueError, TypeError): pass` made the hole invisible from the
    tool's own output: the count shrank and nothing said why."""
    d = tmp_path / "sources"
    write(d / "seed.md", {"title": "Seed", "status": "seed", "created": "soon-ish"})
    files = {k: [] for k in ("sources", "principles", "positions", "reference")}
    files["sources"] = sorted(d.glob("*.md"))
    assert obh.find_stale_seeds(files) == []
    err = capsys.readouterr().err
    assert "unreadable" in err and "seed.md" in err


def test_a_fresh_seed_is_still_not_reported(tmp_path):
    d = tmp_path / "sources"
    today = dt.datetime.now(dt.timezone.utc).date()
    write(d / "seed.md", {"title": "Seed", "status": "seed", "created": str(today)})
    files = {k: [] for k in ("sources", "principles", "positions", "reference")}
    files["sources"] = sorted(d.glob("*.md"))
    assert obh.find_stale_seeds(files) == []


# ============================================================
# Finding 1 -- one list form read, two written
# ============================================================

def test_an_inline_list_still_parses():
    assert oc._fm_list("keywords: [a, B, c]", "keywords") == ["a", "b", "c"]


def test_a_block_list_parses_too():
    block = "status: raw\nkeywords:\n  - a\n  - B\n  - c\nother: x\n"
    assert oc._fm_list(block, "keywords") == ["a", "b", "c"]


def test_a_block_list_at_its_keys_own_column_parses():
    """YAML allows it, and the sibling parser accepts it."""
    assert oc._fm_list("keywords:\n- a\n- b\n", "keywords") == ["a", "b"]


def test_a_block_list_stops_at_the_next_key():
    block = "keywords:\n  - a\nentities:\n  - zzz\n"
    assert oc._fm_list(block, "keywords") == ["a"]
    assert oc._fm_list(block, "entities") == ["zzz"]


def test_an_empty_inline_list_is_empty_not_a_fallthrough():
    assert oc._fm_list("keywords: []\n", "keywords") == []


def test_an_absent_key_is_empty():
    assert oc._fm_list("status: raw\n", "keywords") == []


def test_a_scalar_value_is_not_read_as_a_list():
    """`keywords: alpha` is not a list, and must not be mistaken for a block
    header whose items happen to follow."""
    assert oc._fm_list("keywords: alpha\n- b\n", "keywords") == []


def test_quoted_block_items_lose_their_quotes():
    assert oc._fm_list('keywords:\n  - "Alpha"\n  - \'Beta\'\n', "keywords") == [
        "alpha", "beta"]


# ============================================================
# Finding 4 -- two unguarded stats in a "NEVER raises" function
# ============================================================

@pytest.fixture
def propose_env(tmp_path, monkeypatch):
    """`_run_headless_propose` driven to its same-run fallback, no subprocess."""
    monkeypatch.setenv("ODIN_REFLECT_PROPOSE_ENABLED", "1")
    monkeypatch.setattr(ocn, "get_data_root", lambda: tmp_path)

    fake_cadence = types.SimpleNamespace(
        DEFAULT_MIN_ENTRIES=1,
        compute=lambda *a, **k: {"cluster_detail": [{"tag": "x"}]},
    )
    monkeypatch.setattr(ocn, "_load_cadence_module", lambda p: fake_cadence)
    monkeypatch.setattr(ocn, "_brain_snapshot", lambda d: {})
    monkeypatch.setattr(
        ocn.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))

    props = tmp_path / "outputs" / "operations" / "odin-reflect-proposals"
    props.mkdir(parents=True)
    return props


def test_a_proposal_removed_between_glob_and_stat_does_not_raise(
        propose_env, monkeypatch):
    """The contract at the top of `_run_headless_propose` is "NEVER raises".
    An unguarded `p.stat()` broke it, took `main` down with it, and threw away
    the counts nudge that had already been computed."""
    good = propose_env / "2020-01-01_odin-reflect-proposal.md"
    good.write_text("keep", encoding="utf-8")
    ghost = propose_env / "2020-01-02_odin-reflect-proposal.md"
    ghost.write_text("vanishes", encoding="utf-8")

    real_stat = Path.stat

    def flaky(self, *a, **k):
        if self.name.startswith("2020-01-02"):
            raise FileNotFoundError(2, "No such file or directory")
        return real_stat(self, *a, **k)
    monkeypatch.setattr(Path, "stat", flaky)

    assert ocn._run_headless_propose(ROOT) == good


def test_the_newest_fresh_proposal_is_the_one_returned(propose_env):
    import os
    older = propose_env / "2020-01-01_odin-reflect-proposal.md"
    newer = propose_env / "2020-01-02_odin-reflect-proposal.md"
    older.write_text("a", encoding="utf-8")
    newer.write_text("b", encoding="utf-8")
    # Both must sit INSIDE the 2s freshness window, or the filter removes one
    # and "newest wins" is never actually exercised.
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    os.utime(older, (now - 1.0, now - 1.0))
    os.utime(newer, (now, now))
    assert ocn._run_headless_propose(ROOT) == newer


def test_no_proposal_at_all_returns_none_rather_than_raising(propose_env):
    assert ocn._run_headless_propose(ROOT) is None


def test_every_candidate_is_stat_ed_once(propose_env, monkeypatch):
    """The old form stat'd twice per file -- once in the filter, once in the
    sort key -- doubling the race window it had no guard for."""
    (propose_env / "2020-01-01_odin-reflect-proposal.md").write_text("a", encoding="utf-8")
    calls = []
    real_stat = Path.stat

    def counting(self, *a, **k):
        if self.suffix == ".md":
            calls.append(self.name)
        return real_stat(self, *a, **k)
    monkeypatch.setattr(Path, "stat", counting)
    ocn._run_headless_propose(ROOT)
    assert calls.count("2020-01-01_odin-reflect-proposal.md") == 1


# ============================================================
# Finding 6 -- "the tree is absent", about a tree that exists
# ============================================================

def test_a_seam_inside_the_personal_root_stays_relative(monkeypatch, tmp_path):
    monkeypatch.delenv("THREADS_ROOT", raising=False)
    rels = obl._namespace_rels()
    assert not Path(rels["crm"][0]).is_absolute()
    assert not Path(rels["plan"][0]).is_absolute()


def test_a_threads_root_outside_the_personal_root_is_kept_absolute(
        monkeypatch, tmp_path):
    """The old fallback kept only the last component, so an override pointing
    anywhere outside the base collapsed to the bare directory name."""
    outside = tmp_path / "elsewhere" / "threads"
    outside.mkdir(parents=True)
    monkeypatch.setenv("THREADS_ROOT", str(outside))
    rels = obl._namespace_rels()
    thread_pat = Path(rels["thread"][0])
    assert thread_pat.is_absolute()
    assert thread_pat == outside / "business"


def test_an_absolute_pattern_globs_from_its_own_anchor(tmp_path):
    target = tmp_path / "a" / "b" / "business"
    target.mkdir(parents=True)
    assert obl._glob_dirs(Path("/nowhere-at-all"), target) == [target]


def test_a_relative_pattern_still_globs_from_the_given_root(tmp_path):
    target = tmp_path / "threads" / "business"
    target.mkdir(parents=True)
    assert obl._glob_dirs(tmp_path, Path("threads/business")) == [target]


def test_a_file_matching_the_pattern_is_not_mistaken_for_a_tree(tmp_path):
    """The contract is "directories matching". A plain file named `business`
    would otherwise be handed to `rglob` as if it were a subtree."""
    (tmp_path / "threads").mkdir()
    (tmp_path / "threads" / "business").write_text("not a directory", encoding="utf-8")
    assert obl._glob_dirs(tmp_path, Path("threads/business")) == []


def test_an_absolute_wildcard_pattern_still_expands(tmp_path):
    for name in ("2026", "2025"):
        (tmp_path / "threads" / "archive" / name / "business").mkdir(parents=True)
    pat = tmp_path / "threads" / "archive" / "*" / "business"
    got = sorted(p.parent.name for p in obl._glob_dirs(Path("/x"), pat))
    assert got == ["2025", "2026"]


def test_an_overridden_thread_tree_is_no_longer_reported_as_absent(
        monkeypatch, tmp_path):
    """The whole point. None means "absent on this machine" to every reader of
    `_external_entities`, and the tree was demonstrably there."""
    outside = tmp_path / "elsewhere" / "threads" / "business"
    outside.mkdir(parents=True)
    (outside / "2026-01-01-a-deal.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("THREADS_ROOT", str(tmp_path / "elsewhere" / "threads"))

    brain = tmp_path / "data" / "knowledge" / "odin-brain"
    brain.mkdir(parents=True)
    ext = obl._external_entities(brain)
    assert ext["thread"] is not None, "a real tree was reported as absent"
    assert "2026-01-01-a-deal" in ext["thread"]


def test_a_genuinely_missing_tree_still_reads_as_absent(monkeypatch, tmp_path):
    """The None must keep meaning what it says, or the fix trades one wrong
    claim for another."""
    monkeypatch.setenv("THREADS_ROOT", str(tmp_path / "does-not-exist"))
    brain = tmp_path / "data" / "knowledge" / "odin-brain"
    brain.mkdir(parents=True)
    assert obl._external_entities(brain)["thread"] is None
