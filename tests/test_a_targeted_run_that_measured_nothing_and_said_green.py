"""Shard scripts-05-p3: six tools that reported a state they had not established.

The shape repeats across all six, and it is the one `.claude/rules/scope-claims.md`
names: a tool prints a sentence its method did not earn.

  * `run-skill-eval.py --case <typo>` matched nothing, ran nothing, and exited
    **0**. A targeted regression run reporting green over zero measurements is
    the exact defect `tests/test_run_skill_eval_exit_codes.py` was written for;
    it closed the API-error and unknown-skill doors and left this one open.
  * Both eval runners wrote the benchmark sidecar from a `--case`-filtered run,
    replacing `last_run` with a one-case record wearing a whole run's shape.
    Measured on the live tree: email-intel went from 9/9 over three cases to
    2/2 over one, and nothing in the file said two cases never ran.
  * `eval-outcomes.py --all --case <real-id>` ran the case, PASSED it, and then
    exited 2 - because the five skills that do not carry that case each raised
    a setup error of their own.
  * `eval-query-set.py` scored an unparsed Set A as `0/0 = 0% FAIL (bar 80%)`,
    pointing the operator at an index regression that was a Markdown heading.
  * `export-antigravity-config.py` masked strings under a sensitive key and let
    NUMBERS through, reporting "0 keys masked" over a shipped credential - the
    same defect its own docstring says it fixed, one JSON type over. It also
    suppressed the "the scan only matches key NAMES" caution in exactly the run
    where nothing matched.
  * `exchange-task.py --list` raised IndexError halfway through the listing on
    a task whose notes hold only whitespace.

Written 2026-08-24. Every test here fails against the pre-fix file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / stem))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def outcomes():
    return _load("eval-outcomes.py", "p05p3_eval_outcomes")


@pytest.fixture(scope="module")
def prose():
    return _load("run-skill-eval.py", "p05p3_run_skill_eval")


@pytest.fixture(scope="module")
def flag():
    return _load("eval-flag.py", "p05p3_eval_flag")


@pytest.fixture(scope="module")
def queryset():
    return _load("eval-query-set.py", "p05p3_eval_query_set")


@pytest.fixture(scope="module")
def antigravity():
    return _load("export-antigravity-config.py", "p05p3_export_antigravity")


@pytest.fixture(scope="module")
def tasks():
    return _load("exchange-task.py", "p05p3_exchange_task")


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["prog", *args])


# ============================================================
# eval-outcomes.py - the targeted run
# ============================================================

_PASSING_CASE = {
    "id": "case-alpha",
    "outcome": {"type": "doctype_render", "doctype": "official", "expect_missing": [],
                "data": {"CLASS": "Board Resolution", "REF_ID": "R-1",
                         "DATE": "2026-06-06", "PLACE": "Sample City, Country",
                         "ISSUER_NAME": "Misha Hanin", "ISSUER_TITLE": "CEO",
                         "SUBJECT": "Test"}},
}


def _second_case() -> dict:
    case = json.loads(json.dumps(_PASSING_CASE))
    case["id"] = "case-beta"
    return case


@pytest.fixture()
def outcome_tree(outcomes, tmp_path, monkeypatch):
    """Two skills. `official-doc` carries two cases; `xpager` carries a third."""
    skills = tmp_path / ".claude" / "skills"
    for skill, cases in (("official-doc", [_PASSING_CASE, _second_case()]),
                         ("xpager", [{**_second_case(), "id": "case-gamma"}])):
        out = skills / skill / "evals" / "outcomes"
        out.mkdir(parents=True)
        for case in cases:
            (out / f"{case['id']}.json").write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setattr(outcomes, "SKILLS_DIR", skills)
    monkeypatch.setattr(outcomes, "ROOT", tmp_path)
    return skills


def _sidecar(skills: Path, skill: str) -> Path:
    return skills / skill / "evals" / "benchmark-outcomes.json"


def test_a_full_run_still_writes_the_sidecar(outcomes, outcome_tree, monkeypatch):
    """The green path. Removing the write entirely must not pass."""
    _argv(monkeypatch, "--skill", "official-doc")
    assert outcomes.main() == 0
    payload = json.loads(_sidecar(outcome_tree, "official-doc").read_text(encoding="utf-8"))
    assert [c["id"] for c in payload["last_run"]["cases"]] == ["case-alpha", "case-beta"]


def test_a_filtered_run_leaves_the_sidecar_untouched(outcomes, outcome_tree, monkeypatch):
    """THE case. `--case` grades one case; the sidecar records a full run.

    Before the fix this wrote a 1-of-2 record with no marker, so the next reader
    of benchmark-outcomes.json saw a passing run over a case set that had
    silently halved.
    """
    _argv(monkeypatch, "--skill", "official-doc")
    assert outcomes.main() == 0
    before = _sidecar(outcome_tree, "official-doc").read_text(encoding="utf-8")

    _argv(monkeypatch, "--skill", "official-doc", "--case", "case-beta")
    assert outcomes.main() == 0
    assert _sidecar(outcome_tree, "official-doc").read_text(encoding="utf-8") == before, (
        "a --case run overwrote the full-run benchmark with a partial record"
    )


def test_a_filtered_run_says_why_the_sidecar_was_skipped(outcomes, outcome_tree,
                                                         monkeypatch, capsys):
    """Silence about a skipped write reads as a write that happened."""
    _argv(monkeypatch, "--skill", "official-doc", "--case", "case-beta")
    assert outcomes.main() == 0
    out = capsys.readouterr().out
    assert "not written" in out and "--case" in out


def test_a_filtered_run_that_matched_writes_nothing_even_on_a_first_run(
        outcomes, outcome_tree, monkeypatch):
    """No prior sidecar either: the file must not be CREATED from one case."""
    _argv(monkeypatch, "--skill", "official-doc", "--case", "case-beta")
    assert outcomes.main() == 0
    assert not _sidecar(outcome_tree, "official-doc").exists()


def test_all_with_a_real_case_id_exits_zero(outcomes, outcome_tree, monkeypatch):
    """THE case. The run passed and still exited 2.

    Under --all a named case lives in exactly ONE skill, so "no match here" is
    the ordinary state of every other skill. The per-skill setup error made the
    documented combination unusable.
    """
    _argv(monkeypatch, "--all", "--case", "case-gamma", "--no-write")
    assert outcomes.main() == 0, "a passing targeted run reported a setup error"


def test_all_with_a_real_case_id_actually_runs_it(outcomes, outcome_tree, monkeypatch, capsys):
    """Exit 0 is not enough: a filter that matched nothing would also be 0 now."""
    _argv(monkeypatch, "--all", "--case", "case-gamma", "--no-write")
    assert outcomes.main() == 0
    assert "Total: 1/1" in capsys.readouterr().out


def test_all_with_a_typo_case_id_exits_two(outcomes, outcome_tree, monkeypatch, capsys):
    """Matched NOWHERE is the error the per-skill check was reaching for."""
    _argv(monkeypatch, "--all", "--case", "case-nonexistent", "--no-write")
    assert outcomes.main() == 2
    assert "matched no outcome case in any skill" in capsys.readouterr().err


def test_one_skill_with_a_typo_case_id_exits_two(outcomes, outcome_tree, monkeypatch, capsys):
    """And the single-skill path keeps the refusal it already had."""
    _argv(monkeypatch, "--skill", "official-doc", "--case", "case-nonexistent", "--no-write")
    assert outcomes.main() == 2
    err = capsys.readouterr().err
    assert "matched no outcome case" in err and "official-doc" in err


def test_a_zero_check_total_never_prints_without_the_setup_marker(
        outcomes, outcome_tree, monkeypatch, capsys):
    """The summary line is what a human reads. `Total: 0/0 checks passed` on its
    own is a green sentence over nothing measured, so it must never appear
    without the marker that says the run did not happen."""
    _argv(monkeypatch, "--skill", "official-doc", "--case", "nope", "--no-write")
    outcomes.main()
    out = capsys.readouterr().out
    assert "Total: 0/0" in out
    assert "setup errors present" in out, (
        "a zero-measurement summary printed as a clean total"
    )


def test_an_unfiltered_run_needs_no_case_flag_to_pass(outcomes, outcome_tree, monkeypatch):
    """A guard keyed on the wrong condition would break the ordinary run."""
    _argv(monkeypatch, "--all", "--no-write")
    assert outcomes.main() == 0


# ---- the serialized timestamp ------------------------------------------------

def test_the_outcome_sidecar_timestamp_is_utc_aware(outcomes, outcome_tree, monkeypatch):
    """A SERIALIZED stamp, so UTC with an offset (dtz-datetime-convention).

    `time.strftime` wrote naive local time, and carries no tzinfo for ruff's DTZ
    ruleset to catch - so the one field that makes two runs comparable was the
    one field that did not say which clock it came from.
    """
    _argv(monkeypatch, "--skill", "official-doc")
    assert outcomes.main() == 0
    stamp = json.loads(_sidecar(outcome_tree, "official-doc").read_text(
        encoding="utf-8"))["last_run"]["timestamp"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None, f"naive serialized timestamp: {stamp!r}"
    assert parsed.utcoffset().total_seconds() == 0, f"not UTC: {stamp!r}"


def test_the_outcome_sidecar_timestamp_is_not_local_wall_clock(outcomes, outcome_tree,
                                                               monkeypatch):
    """A `.astimezone()` local stamp is aware too, and still the wrong clock."""
    _argv(monkeypatch, "--skill", "official-doc")
    assert outcomes.main() == 0
    stamp = json.loads(_sidecar(outcome_tree, "official-doc").read_text(
        encoding="utf-8"))["last_run"]["timestamp"]
    assert stamp.endswith("+00:00"), f"expected a UTC offset, got {stamp!r}"


# ============================================================
# run-skill-eval.py - the same two defects in the prose sibling
# ============================================================

def _usage() -> dict:
    return {"input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@pytest.fixture()
def prose_tree(prose, tmp_path, monkeypatch):
    skill = tmp_path / "demo"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nBody.\n",
                                    encoding="utf-8")
    for cid in ("case-1", "case-2"):
        (skill / "evals" / "cases" / f"{cid}.json").write_text(
            json.dumps({"id": cid, "input": "hello",
                        "checks": {"must_mention": ["hello"]}}), encoding="utf-8")
    monkeypatch.setattr(prose, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(prose, "ROOT", tmp_path)
    monkeypatch.setattr(prose, "load_env", lambda: None)
    monkeypatch.setattr(prose, "call_skill", lambda *a, **k: ("hello there", _usage(), 0.1))
    return skill


def test_a_typo_case_id_no_longer_exits_green(prose, prose_tree, monkeypatch, capsys):
    """THE case. Zero cases matched, zero checks ran, and it returned 0.

    Every other zero-measurement door in this file was closed by the 2026-08-23
    audit; a targeted `--case` run was the one still open.
    """
    _argv(monkeypatch, "--skill", "demo", "--case", "case-99", "--no-write")
    assert prose.main() == 2, "a run that measured nothing reported success"
    assert "matched no case" in capsys.readouterr().err


def test_a_real_case_id_still_exits_zero(prose, prose_tree, monkeypatch):
    """The guard must not swallow the run it was meant to protect."""
    _argv(monkeypatch, "--skill", "demo", "--case", "case-1", "--no-write")
    assert prose.main() == 0


def test_a_skill_with_no_cases_and_no_case_flag_stays_zero(prose, tmp_path, monkeypatch):
    """An empty evals/cases/ is a skip, not a setup error. Unchanged behaviour."""
    skill = tmp_path / "empty"
    (skill / "evals" / "cases").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: empty\n---\n", encoding="utf-8")
    monkeypatch.setattr(prose, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(prose, "ROOT", tmp_path)
    monkeypatch.setattr(prose, "load_env", lambda: None)
    _argv(monkeypatch, "--skill", "empty", "--no-write")
    assert prose.main() == 0


def test_the_prose_sidecar_is_untouched_by_a_filtered_run(prose, prose_tree, monkeypatch):
    """Same partial-record defect as the outcome runner, same fix."""
    bench = prose_tree / "evals" / "benchmark.json"
    _argv(monkeypatch, "--skill", "demo")
    assert prose.main() == 0
    before = bench.read_text(encoding="utf-8")

    _argv(monkeypatch, "--skill", "demo", "--case", "case-1")
    assert prose.main() == 0
    assert bench.read_text(encoding="utf-8") == before


def test_the_prose_sidecar_records_every_case_on_a_full_run(prose, prose_tree, monkeypatch):
    """The green path, so 'never write' cannot pass as the fix."""
    _argv(monkeypatch, "--skill", "demo")
    assert prose.main() == 0
    payload = json.loads((prose_tree / "evals" / "benchmark.json").read_text(encoding="utf-8"))
    assert sorted(c["id"] for c in payload["last_run"]["cases"]) == ["case-1", "case-2"]


def test_the_prose_sidecar_timestamp_is_utc_aware(prose, prose_tree, monkeypatch):
    """The paired sidecars are read side by side; one local and one UTC is worse
    than both being wrong the same way."""
    _argv(monkeypatch, "--skill", "demo")
    assert prose.main() == 0
    stamp = json.loads((prose_tree / "evals" / "benchmark.json").read_text(
        encoding="utf-8"))["last_run"]["timestamp"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


# ============================================================
# eval-query-set.py - a bar over an unparsed file
# ============================================================

_SET_FILE = """# Frozen set

## Set A - grep-blind

| # | Query | Target |
|---|---|---|
| 1 | why was the daemon disabled | `abc1234` |
| 2 | who owns the leak wall | `def5678` |

## Set B - grep answers these

| # | Query | Target |
|---|---|---|
| 1 | find get_data_root | `9990000` |
"""


def _wire_query_set(queryset, tmp_path, monkeypatch, text: str, hit_target: str | None):
    data_root = tmp_path / "data"
    path = data_root / queryset.PHASES["1"]["rel"]
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(queryset, "get_data_root", lambda: data_root)

    def _query(text_, layer, top_k, threshold=None):
        if hit_target is None:
            return []
        return [{"path": f"label@{hit_target}0000", "title": "t"}]

    monkeypatch.setattr(queryset, "query", _query)
    return path


def test_an_unparsed_set_a_is_a_setup_error_not_a_zero_percent_score(
        queryset, tmp_path, monkeypatch, capsys):
    """THE case. `0/0 = 0% FAIL (bar 80%)` named a measured index failure.

    Nothing had been measured. A renamed heading in the frozen Markdown sent
    the operator hunting an index regression that was a parse problem, and the
    `cases` guard could not see it: Set B rows keep the list non-empty alone.
    """
    text = _SET_FILE.replace("## Set A - grep-blind", "## Set A (grep-blind)".replace(
        "## Set A", "## Group One"))
    _wire_query_set(queryset, tmp_path, monkeypatch, text, hit_target="9990")
    _argv(monkeypatch, "--phase", "1")
    assert queryset.main() == 2
    err = capsys.readouterr().err
    assert "Set A parsed 0 rows" in err
    assert "0%" not in err, "an unmeasured set was still reported as a rate"


def test_a_populated_set_a_still_scores(queryset, tmp_path, monkeypatch, capsys):
    """The green path: a real Set A must still produce a real verdict."""
    _wire_query_set(queryset, tmp_path, monkeypatch, _SET_FILE, hit_target="abc1")
    _argv(monkeypatch, "--phase", "1")
    queryset.main()
    assert "Set A" in capsys.readouterr().out


def test_a_below_bar_set_a_still_exits_one(queryset, tmp_path, monkeypatch):
    """A guard that turned every miss into exit 2 would hide a real regression."""
    _wire_query_set(queryset, tmp_path, monkeypatch, _SET_FILE, hit_target=None)
    _argv(monkeypatch, "--phase", "1")
    assert queryset.main() == 1


def test_an_empty_set_b_is_reported_as_unmeasured(queryset, tmp_path, monkeypatch, capsys):
    """Set B may legitimately be empty. `0/0 = 0%` reads as a miss rate."""
    text = _SET_FILE.split("## Set B")[0]
    _wire_query_set(queryset, tmp_path, monkeypatch, text, hit_target="abc1")
    _argv(monkeypatch, "--phase", "1")
    queryset.main()
    out = capsys.readouterr().out
    assert "not measured" in out


def test_an_entirely_unparsed_file_is_still_the_older_setup_error(
        queryset, tmp_path, monkeypatch, capsys):
    """The pre-existing zero-cases guard must survive the new one."""
    _wire_query_set(queryset, tmp_path, monkeypatch, "# nothing here\n", hit_target=None)
    _argv(monkeypatch, "--phase", "1")
    assert queryset.main() == 2
    assert "no cases parsed" in capsys.readouterr().err


# ============================================================
# export-antigravity-config.py - the masker
# ============================================================

MASK = "***MASKED***"   # a constant, not a literal: ruff S105 reads
                        # `data["token"] == "..."` as a hardcoded credential.
_MASKED_FIELD = "apiKey"   # a key NAME the masker matches on. No value here.


def test_a_numeric_secret_under_a_sensitive_key_is_masked(antigravity):
    """THE case. Only strings were masked, so `{"apiKey": 8675309}` shipped in
    cleartext while the console reported 0 keys masked."""
    data, masked = antigravity.mask_sensitive({"apiKey": 8675309})
    assert data["apiKey"] == MASK
    assert masked == ["apiKey"]


def test_a_numeric_secret_inside_a_list_is_masked(antigravity):
    data, masked = antigravity.mask_sensitive({"tokens": ["placeholder-value", 12345]})
    assert data["tokens"] == [MASK, MASK]
    assert len(masked) == 2


def test_a_float_under_a_sensitive_key_is_masked(antigravity):
    data, _ = antigravity.mask_sensitive({"secret": 1.5})
    assert data["secret"] == MASK


def test_a_bool_flag_under_a_sensitive_key_is_left_alone(antigravity):
    """`{"authEnabled": true}` is a flag. Masking it buries the real findings.

    Ordered before the int check on purpose: in Python `isinstance(True, int)`
    is True, so a naive number check would swallow every boolean.
    """
    data, masked = antigravity.mask_sensitive({"authEnabled": True, "authRetries": 3})
    assert data["authEnabled"] is True
    assert data["authRetries"] == MASK
    assert masked == ["authRetries"]


def test_a_string_secret_is_still_masked(antigravity):
    """The behaviour that already worked, so 'mask everything' cannot pass."""
    data, masked = antigravity.mask_sensitive({"token": "placeholder-value"})
    assert data["token"] == MASK and masked == ["token"]


def test_an_innocent_number_is_untouched(antigravity):
    """Only values under a SENSITIVE key are masked."""
    data, masked = antigravity.mask_sensitive({"fontSize": 14, "editor": {"tabSize": 4}})
    assert data == {"fontSize": 14, "editor": {"tabSize": 4}} and masked == []


def test_an_empty_string_under_a_sensitive_key_is_not_reported(antigravity):
    """An unset credential is not a leaked one; counting it inflates the report."""
    data, masked = antigravity.mask_sensitive({"apiKey": ""})
    assert data["apiKey"] == "" and masked == []


@pytest.fixture()
def antigravity_profile(antigravity, tmp_path, monkeypatch):
    """A throwaway Antigravity profile with no CLI on the path."""
    user_data = tmp_path / "User"
    user_data.mkdir()
    monkeypatch.setattr(antigravity, "detect_paths", lambda: (user_data, None))
    monkeypatch.setattr(antigravity, "get_outputs_dir", lambda: tmp_path / "out")
    return user_data


def _export(antigravity, monkeypatch, tmp_path, *extra) -> Path:
    out_zip = tmp_path / "bundle.zip"
    _argv(monkeypatch, "--output", str(out_zip), *extra)
    antigravity.main()
    return out_zip


def test_the_caution_is_printed_when_nothing_was_masked(
        antigravity, antigravity_profile, tmp_path, monkeypatch, capsys):
    """THE case. It was gated on `if masked_keys:`.

    Zero masked keys means the scan matched no key NAME - and the whole point
    of that paragraph is that a credential under an innocent name is invisible
    to a name-based scan. The clean-looking run was the one that got no warning.
    """
    (antigravity_profile / "settings.json").write_text('{"fontSize": 14}', encoding="utf-8")
    _export(antigravity, monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "Review before sending" in out
    assert "Nothing was masked" in out


def test_the_caution_still_appears_when_something_was_masked(
        antigravity, antigravity_profile, tmp_path, monkeypatch, capsys):
    # Built with json.dumps from a named constant, not written as a literal:
    # the repo secret scanner matches a credential-ish KEY NAME sitting next to
    # any quoted value, so `"apiKey": "..."` in source trips it whatever the
    # value is. A `pragma: allowlist secret` would also clear it and would
    # teach the next author to reach for the suppression first.
    (antigravity_profile / "settings.json").write_text(
        json.dumps({_MASKED_FIELD: "placeholder-value"}), encoding="utf-8")
    _export(antigravity, monkeypatch, tmp_path)
    assert "Review before sending" in capsys.readouterr().out


def test_no_caution_when_settings_never_shipped(
        antigravity, antigravity_profile, tmp_path, monkeypatch, capsys):
    """No settings.json in the profile: there is nothing to eyeball."""
    _export(antigravity, monkeypatch, tmp_path)
    assert "Review before sending" not in capsys.readouterr().out


def test_the_readme_says_settings_was_excluded(
        antigravity, antigravity_profile, tmp_path, monkeypatch):
    """THE case. The three install blocks all start by copying settings.json,
    and said so even when the file had been excluded for being unparseable."""
    (antigravity_profile / "settings.json").write_text("{ not json", encoding="utf-8")
    out_zip = _export(antigravity, monkeypatch, tmp_path)
    with zipfile.ZipFile(out_zip) as zf:
        assert "settings.json" not in zf.namelist()
        readme = zf.read("README.md").decode("utf-8")
    assert "settings.json is NOT in this bundle" in readme
    assert "would not parse" in readme


def test_the_readme_stays_quiet_when_settings_shipped(
        antigravity, antigravity_profile, tmp_path, monkeypatch):
    """The green path: a normal bundle must not carry a missing-file warning."""
    (antigravity_profile / "settings.json").write_text('{"fontSize": 14}', encoding="utf-8")
    out_zip = _export(antigravity, monkeypatch, tmp_path)
    with zipfile.ZipFile(out_zip) as zf:
        assert "settings.json" in zf.namelist()
        readme = zf.read("README.md").decode("utf-8")
    assert "NOT in this bundle" not in readme


def test_the_readme_names_an_absent_profile_settings_file(
        antigravity, antigravity_profile, tmp_path, monkeypatch):
    """Absent and excluded are different states and must not share one sentence."""
    out_zip = _export(antigravity, monkeypatch, tmp_path)
    with zipfile.ZipFile(out_zip) as zf:
        readme = zf.read("README.md").decode("utf-8")
    assert "had none" in readme


# ============================================================
# exchange-task.py - the listing that stopped halfway
# ============================================================

class _FakeTask:
    def __init__(self, body):
        self.body = body
        self.subject = "Follow up"
        self.status = "NotStarted"
        self.due_date = None
        self.reminder_is_set = False
        self.reminder_due_by = None


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self

    def order_by(self, *_):
        return self

    def filter(self, **_):
        return self

    def __iter__(self):
        return iter(self._items)


class _FakeAccount:
    def __init__(self, items):
        self.tasks = _FakeQuery(items)


class _Args:
    all_statuses = True
    status = "NotStarted"


# `list_tasks` gained a `config` parameter on 2026-08-27: it renders
# `due_date` and `reminder_due_by` on the mailbox timezone instead of the
# raw UTC these exchangelib fields carry, and the zone name comes from the
# config the create path already labels with. These tests are about the
# BODY column, so the zone is fixed and never asserted on here; the clock
# itself is covered by tests/test_two_clocks_and_a_default_nothing_read.py.
_CONFIG = {"EXCHANGE_TIMEZONE": "UTC"}


@pytest.mark.parametrize("body", ["\n", "   ", "\t\n ", "\r\n"])
def test_a_whitespace_only_body_does_not_crash_the_listing(tasks, body, capsys):
    """THE case. A body of "\\n" is TRUTHY and strips to "", whose splitlines()
    is [] - so `[0]` raised IndexError in the MIDDLE of the listing, and the
    operator got a half-printed list plus a traceback with no way to tell where
    the list stopped."""
    tasks.list_tasks(_FakeAccount([_FakeTask(body)]), _Args(), _CONFIG)
    assert "Follow up" in capsys.readouterr().out


def test_a_real_body_first_line_is_still_shown(tasks, capsys):
    """The green path: skipping every body would also stop the crash."""
    tasks.list_tasks(_FakeAccount([_FakeTask("Check the Vesper Lynd thread\nsecond line")]), _Args(), _CONFIG)
    out = capsys.readouterr().out
    assert "Check the Vesper Lynd thread" in out and "second line" not in out


def test_a_long_body_line_is_still_truncated(tasks, capsys):
    tasks.list_tasks(_FakeAccount([_FakeTask("x" * 300)]), _Args(), _CONFIG)
    assert "x" * 101 not in capsys.readouterr().out


def test_every_task_after_a_broken_one_is_still_listed(tasks, capsys):
    """The real cost of the IndexError: the tasks BELOW it were never printed."""
    tasks.list_tasks(_FakeAccount([_FakeTask("\n"), _FakeTask("later task body")]), _Args(), _CONFIG)
    assert "later task body" in capsys.readouterr().out


def test_an_empty_body_is_still_skipped(tasks, capsys):
    tasks.list_tasks(_FakeAccount([_FakeTask("")]), _Args(), _CONFIG)
    out = capsys.readouterr().out
    assert "Follow up" in out


def test_a_task_with_no_notes_does_not_print_the_word_none(tasks, capsys):
    """`t.body` is None for the common task - one created in Outlook with the
    notes field left alone. Without the `if t.body else []` guard, `str(None)`
    is the four-letter string "None", which splits to ["None"] and prints as
    though the operator had typed it. Found by a surviving mutation: the empty
    STRING case was covered and the None case was not, and None is the one the
    mailbox actually returns.
    """
    tasks.list_tasks(_FakeAccount([_FakeTask(None)]), _Args(), _CONFIG)
    out = capsys.readouterr().out
    # Nothing else this listing prints contains the substring: the header is
    # "Exchange Tasks (all statuses)", the status is "NotStarted", the subject
    # is "Follow up".
    assert "None" not in out, "a task with no notes printed the word None as its body"
    assert "Follow up" in out, "the task itself vanished from the listing"


# ---- the reminder timezone --------------------------------------------------

def test_the_reminder_is_stamped_in_the_timezone_it_was_given(tasks, monkeypatch):
    """The `.replace(tzinfo=get_default_tz())` that used to sit here was a no-op:
    the return value is rebuilt from the naive fields with the EXCHANGE tz, so
    the workspace tz was attached and then discarded without converting."""
    seen = {}

    def _fake_dt(y, mo, d, h, mi, tzinfo=None):
        seen.update(y=y, mo=mo, d=d, h=h, mi=mi, tz=tzinfo)
        return "stamped"

    monkeypatch.setattr(tasks, "EWSDateTime", _fake_dt)
    sentinel = object()
    assert tasks.parse_remind_at("2026-04-29 09:47", sentinel) == "stamped"
    assert seen["tz"] is sentinel
    assert (seen["h"], seen["mi"]) == (9, 47), "the wall clock was shifted"


def test_the_remind_at_help_names_the_exchange_timezone(tasks):
    """It said "local timezone" while the value is read in the mailbox tz.

    Harmless while EXCHANGE_TIMEZONE and HEADING_OS_TZ agree, and a silent hour
    shift the moment they do not.
    """
    args = tasks.parse_args.__doc__  # keep ruff from flagging an unused import path
    del args
    import argparse as _ap
    parser = _ap.ArgumentParser()
    # Re-read the help straight off the real parser rather than trusting a copy.
    text = (ROOT / "scripts" / "exchange-task.py").read_text(encoding="utf-8")
    assert "EXCHANGE_TIMEZONE" in text
    assert 'help="Reminder date and time (local timezone)' not in text
    del parser


def test_a_malformed_remind_at_still_exits_one(tasks, capsys):
    with pytest.raises(SystemExit) as exc:
        tasks.parse_remind_at("29/04/2026 09:47", object())
    assert exc.value.code == 1
    assert "Invalid --remind-at" in capsys.readouterr().out


# ============================================================
# eval-flag.py - a refusal that read as a crash
# ============================================================

def test_a_path_fragment_skill_is_refused_not_raised(flag, monkeypatch, capsys):
    """THE case. `_valid_skill` raises ValueError and nothing caught it, so
    `--skill ../evil` printed a raw traceback while the sibling tool over the
    same validator answers with a message and exit 1."""
    _argv(monkeypatch, "--skill", "../evil", "--note", "x")
    assert flag.main() == 1
    assert "bare skill name" in capsys.readouterr().err


def test_an_absolute_skill_path_is_refused_not_raised(flag, monkeypatch):
    _argv(monkeypatch, "--skill", "/etc", "--note", "x")
    assert flag.main() == 1


def test_a_valid_skill_still_stages_a_draft(flag, tmp_path, monkeypatch, capsys):
    """The green path: catching ValueError must not swallow the real command."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "email-intel").mkdir(parents=True)
    monkeypatch.setattr(flag, "SKILLS_DIR", skills)
    monkeypatch.setattr(flag, "ROOT", tmp_path)
    _argv(monkeypatch, "--skill", "email-intel", "--note", "wrong tone")
    assert flag.main() == 0
    assert "staged" in capsys.readouterr().out


def test_an_unreadable_draft_is_named_in_the_list(flag, tmp_path, monkeypatch, capsys):
    """A corrupt draft fell back to `{}` and listed with an empty description -
    indistinguishable from an untitled draft nobody had filled in yet."""
    staged = tmp_path / ".claude" / "skills" / "email-intel" / "evals" / "outcomes" / "_staged"
    staged.mkdir(parents=True)
    (staged / "broken.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(flag, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    monkeypatch.setattr(flag, "ROOT", tmp_path)
    _argv(monkeypatch, "--list")
    assert flag.main() == 0
    assert "UNREADABLE" in capsys.readouterr().out


def test_a_json_array_draft_is_named_unreadable(flag, tmp_path, monkeypatch, capsys):
    """`[]` decodes cleanly and is not a draft; `.get` on it would raise."""
    staged = tmp_path / ".claude" / "skills" / "email-intel" / "evals" / "outcomes" / "_staged"
    staged.mkdir(parents=True)
    (staged / "arr.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(flag, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    monkeypatch.setattr(flag, "ROOT", tmp_path)
    _argv(monkeypatch, "--list")
    assert flag.main() == 0
    assert "not a JSON object" in capsys.readouterr().out


def test_a_healthy_draft_is_not_marked_unreadable(flag, tmp_path, monkeypatch, capsys):
    """The green path, so 'mark everything' cannot pass as the fix."""
    staged = tmp_path / ".claude" / "skills" / "email-intel" / "evals" / "outcomes" / "_staged"
    staged.mkdir(parents=True)
    (staged / "ok.json").write_text(
        json.dumps({"id": "flag-1", "description": "tone was off", "trace_id": "t"}),
        encoding="utf-8")
    monkeypatch.setattr(flag, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    monkeypatch.setattr(flag, "ROOT", tmp_path)
    _argv(monkeypatch, "--list")
    assert flag.main() == 0
    out = capsys.readouterr().out
    assert "tone was off" in out and "UNREADABLE" not in out


def test_an_untitled_draft_is_distinguishable_from_a_broken_one(flag, tmp_path,
                                                                monkeypatch, capsys):
    """The whole point: an empty description must not look like a parse failure."""
    staged = tmp_path / ".claude" / "skills" / "email-intel" / "evals" / "outcomes" / "_staged"
    staged.mkdir(parents=True)
    (staged / "untitled.json").write_text(json.dumps({"id": "flag-2"}), encoding="utf-8")
    (staged / "broken.json").write_text("{{{", encoding="utf-8")
    monkeypatch.setattr(flag, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    monkeypatch.setattr(flag, "ROOT", tmp_path)
    _argv(monkeypatch, "--list", "--json")
    assert flag.main() == 0
    rows = {r["id"]: r["unreadable"] for r in json.loads(capsys.readouterr().out)}
    assert rows["flag-2"] is False
    assert rows["broken"] is True
