#!/usr/bin/env python3
"""Shard scripts-10-p2: work abandoned, and diagnostics that named the wrong thing.

Two families here.

Abandoned work:
  - `regenerate-docs-html --all` fed a GENERATOR to `all()`, which short-circuits.
    One unreadable page stopped every later page from being regenerated, and the
    search index was then rebuilt from that partially-stale HTML.
  - `reminders-notify` called Telegram with an empty recipient on an
    unconfigured box, so every due reminder failed to send, every tick, forever,
    and none was ever marked fired -- while the docstring promised no send.

Diagnostics pointing the wrong way:
  - `run-integration-tests` caught `FileNotFoundError` around a subprocess whose
    executable is `sys.executable`. That can never fire, so a box without pytest
    got exit 1 and "One or more tests failed" -- triage aimed at the tests
    instead of the environment.
  - `router-accuracy-nightly --dry-run` printed `is_sensitive()` while the run
    consults `sensitivity_is_declared()`.
  - `resolve_entity` reported one `backend_used` for sources that came from two
    backends, and classified "Asiana Airlines" as a market because "asia" is a
    substring of it.
  - `resolve_customization --key` dropped an unresolvable key silently, so a
    typo and a legitimately-absent key produced the same `{}`.
  - `rule_split_check` with no flags died on `NoneType.partition` instead of
    printing a usage error.

Run: .venv/bin/python -m pytest tests/test_a_gate_that_stops_at_the_first_stumble.py -q
"""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rdh = _load("regenerate_docs_html_p10b", "scripts/regenerate-docs-html.py")
rn = _load("reminders_notify_p10b", "scripts/reminders-notify.py")
re_mod = _load("resolve_entity_p10b", "scripts/resolve_entity.py")
rse = _load("run_skill_eval_p10b", "scripts/run-skill-eval.py")


# ============================================================
# 1 - one bad page does not stop the rest
# ============================================================
def test_every_page_is_attempted_even_after_a_failure(monkeypatch, capsys):
    """`all(generator)` short-circuits; `all(list)` does not. The first failing
    page used to silently leave every later one stale.

    This drives `main()` with `--all`, which is where the list comprehension
    lives. It used to patch three module names and then call its own fake in a
    list comprehension of its own, so what it measured was a property of Python,
    not of this script: it would have stayed green with the generator back. One
    of the three names, `tracked_pairs`, did not exist on the module at all, and
    `raising=False` bound a new attribute nobody reads while the real
    `find_tracked_pairs` ran untouched. Nothing here passes `raising=False` now,
    so renaming any of these four functions fails loudly instead of quietly
    patching a stranger.
    """
    pairs = [Path(f"docs/p{i}.md") for i in range(5)]
    attempted = []

    def fake_regenerate(md, quiet=False):
        attempted.append(md)
        return md.name != "p1.md"

    monkeypatch.setattr(rdh, "regenerate", fake_regenerate)
    monkeypatch.setattr(rdh, "find_tracked_pairs", lambda: pairs)
    monkeypatch.setattr(rdh, "sync_all_navs", lambda **k: True)
    monkeypatch.setattr(rdh, "build_search_index", lambda **k: None)
    monkeypatch.setattr(sys, "argv",
                        ["regenerate-docs-html.py", "--all", "--quiet"])

    with pytest.raises(SystemExit) as exited:
        rdh.main()

    assert attempted == pairs, (
        f"only {len(attempted)} of {len(pairs)} page(s) were attempted after the "
        f"failure at p1.md: {[p.name for p in attempted]}")
    assert exited.value.code == 1, "one failed page must still fail the run"


def test_the_all_call_is_over_a_materialised_list():
    """A generator here silently drops work that a list does not.

    Asserted structurally, not textually. This used to require one exact source
    LINE, so renaming `pairs`, reflowing the comprehension, or dropping the
    intermediate name broke the test over a change that kept the property. What
    matters is the NODE TYPE: `all(<GeneratorExp>)` short-circuits and
    `all(<list>)` cannot, whatever the expression is spelled like.
    """
    tree = ast.parse(
        (ROOT / "scripts" / "regenerate-docs-html.py").read_text(encoding="utf-8"))
    all_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "all"]
    assert all_calls, "no all() call left to check; this test is measuring nothing"

    regenerating = [n for n in ast.walk(tree)
                    if isinstance(n, (ast.GeneratorExp, ast.ListComp))
                    and isinstance(n.elt, ast.Call)
                    and isinstance(n.elt.func, ast.Name)
                    and n.elt.func.id == "regenerate"]
    assert regenerating, "the per-page comprehension is gone; re-point this test"

    # The claim is narrow on purpose: a generator handed STRAIGHT to `all()` is
    # the defect. `list(<genexpr>)` materialises just as a list comprehension
    # does, so flagging every generator would fail a refactor that keeps the
    # property, and a test that cries over a safe change gets edited away.
    lazy = [c for c in all_calls
            if c.args and isinstance(c.args[0], ast.GeneratorExp)
            and isinstance(c.args[0].elt, ast.Call)
            and isinstance(c.args[0].elt.func, ast.Name)
            and c.args[0].elt.func.id == "regenerate"]
    assert not lazy, (
        "all() is fed regenerate() lazily, so the first page that fails stops "
        "every later page from being attempted")


# ============================================================
# 2 - no recipient means no send attempt
# ============================================================
def test_an_unconfigured_recipient_never_reaches_the_transport(monkeypatch, capsys):
    monkeypatch.delenv("REMINDERS_TELEGRAM_TARGET", raising=False)
    monkeypatch.delenv("ODIN_CADENCE_TELEGRAM_TARGET", raising=False)
    calls = []
    monkeypatch.setattr(rn.telegram_notify, "notify",
                        lambda *a, **k: calls.append(a) or True)
    send = rn._telegram_sender()
    assert send("anything") is False
    assert calls == [], "notify was called with an empty target"
    assert "no REMINDERS_TELEGRAM_TARGET" in capsys.readouterr().err


def test_a_configured_recipient_still_reaches_the_transport(monkeypatch):
    monkeypatch.setenv("REMINDERS_TELEGRAM_TARGET", "12345")
    calls = []
    monkeypatch.setattr(rn.telegram_notify, "notify",
                        lambda target, msg: calls.append((target, msg)) or True)
    send = rn._telegram_sender()
    assert send("hello") is True
    assert calls == [("12345", "hello")]


# ============================================================
# 3 - the store is read once, and the read list is what gets sent
# ============================================================
def test_send_due_uses_the_list_it_was_given(monkeypatch):
    """Re-reading the store inside send_due meant the count main() logged could
    disagree with the set actually attempted, and a store that went corrupt in
    between raised past main()'s handler."""
    reads = []
    monkeypatch.setattr(rn.rs, "due_records",
                        lambda today: reads.append(today) or [{"id": "z"}])
    monkeypatch.setattr(rn.rs, "mark_fired", lambda *a: None)
    monkeypatch.setattr(rn, "_format", lambda rec: "msg")
    due = [{"id": "a"}, {"id": "b"}]
    sent = rn.send_due("2026-08-24", lambda m: True, due)
    assert sent == ["a", "b"]
    assert reads == [], "send_due re-read the store"


def test_send_due_still_reads_the_store_when_given_nothing(monkeypatch):
    """The `due=None` path stays, so a direct caller is not broken."""
    monkeypatch.setattr(rn.rs, "due_records", lambda today: [{"id": "x"}])
    monkeypatch.setattr(rn.rs, "mark_fired", lambda *a: None)
    monkeypatch.setattr(rn, "_format", lambda rec: "msg")
    assert rn.send_due("2026-08-24", lambda m: True) == ["x"]


# ============================================================
# 4 - the mode heuristic reads words, not substrings
# ============================================================
@pytest.fixture
def no_crossref(tmp_path, monkeypatch):
    """Isolate the HEURISTIC layer.

    `detect_mode` consults crm/contacts, people.md and pipeline.md first, and
    those are the operator's private files -- a test that reads them asserts
    against data that is absent on a public clone and changes under it here.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: empty)
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: empty)


@pytest.mark.parametrize("target,expected_market", [
    ("Asiana Airlines", False),
    ("Marketo", False),
    ("Asiaticorp", False),
    ("Africa telecom market", True),
    ("GCC region", True),
    ("Middle East market", True),
    ("asia", True),
])
def test_market_detection_matches_whole_words(no_crossref, target, expected_market):
    mode, _reason = re_mod.detect_mode(target)
    assert (mode == "market") is expected_market, (target, mode)


def test_a_short_target_is_not_a_person_by_coincidence(tmp_path, monkeypatch):
    """`target.lower() in text` matched anywhere in the whole file, so any short
    target inherited a classification from unrelated prose."""
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "people.md").write_text("Jane Malasia runs the Riverbend office.\n",
                                   encoding="utf-8")
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: ctx)
    mode, reason = re_mod.detect_mode("asia")
    assert reason != "matched context/people.md", (mode, reason)


def test_a_real_name_still_matches_the_people_file(tmp_path, monkeypatch):
    """The guard must not silence the cross-reference it protects."""
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "people.md").write_text("- Jane Malasia, Riverbend office lead\n",
                                   encoding="utf-8")
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: ctx)
    assert re_mod.detect_mode("Jane Malasia") == ("person", "matched context/people.md")


# ============================================================
# 5 - every backend that served a query is named
# ============================================================
def test_both_backends_are_reported_when_the_search_falls_back():
    """One overwritten `backend_used` claimed a single provenance for sources
    that came from Tavily AND Brave."""
    src = (ROOT / "scripts" / "resolve_entity.py").read_text(encoding="utf-8")
    assert "backends_used" in src
    assert '"backends_used": backends_used,' in src
    # The single-value key stays for existing consumers, derived not overwritten.
    # It names the PRIMARY -- `[0]`, the backend that served before any fallback.
    # Shard 12-p1 moved it off `[-1]`: the LAST backend is a property of how many
    # queries the mode built, not of the run, so "brave" could not be told apart
    # from a configured primary. The per-source `backend` this pairs with is
    # pinned in tests/test_a_dry_run_that_said_the_case_did_not_exist.py.
    assert '"backend_used": backends_used[0] if backends_used else "",' in src


# ============================================================
# 6 - a missing config key is named, not dropped
# ============================================================
def test_an_unresolvable_key_is_reported_on_stderr(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "resolve_customization.py"),
         "--skill", ".claude/skills/osint",
         "--key", "workflow.no_such_key_at_all"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert "key not found: workflow.no_such_key_at_all" in proc.stderr, proc.stderr


# ============================================================
# 7 - a bare invocation is a usage error, not a traceback
# ============================================================
def test_rule_split_check_with_no_flags_prints_usage():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "rule_split_check.py")],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert proc.returncode == 2, proc
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "--original is required" in proc.stderr


# ============================================================
# 8 - missing pytest is an environment problem, said so
# ============================================================
def test_the_dead_filenotfound_catch_is_gone():
    """`sys.executable` always exists, so that catch could never fire; the real
    failure -- no pytest -- came out as exit 1 and 'tests failed'."""
    src = (ROOT / "scripts" / "run-integration-tests.py").read_text(encoding="utf-8")
    # Comments stripped first: the fix's own comment QUOTES the removed clause,
    # so a plain substring search finds its own tombstone and passes forever.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "except FileNotFoundError" not in code, code
    assert 'find_spec("pytest")' in code


# ============================================================
# 9 - the dry-run reports the predicate the runner consults
# ============================================================
@pytest.mark.parametrize("declared", [True, False])
def test_the_dry_run_prints_the_declared_sensitivity(tmp_path, monkeypatch,
                                                     capsys, declared):
    """Run the dry run and read what it printed.

    It used to compute `dry = src.split("--dry-run", 1)[-1]` and then assert
    against `src`, the whole file, so the slice was dead and the phrase
    "sensitivity declared:" satisfied the test from a docstring or the
    normal-run path while the dry-run branch said nothing at all. `split` also
    degraded silently: lose the literal and `dry` becomes the entire file.

    Parametrized on both values, because a branch that printed a hardcoded
    `True` would satisfy a single-value check.
    """
    ran = _load("router_accuracy_nightly_p10b", "scripts/router-accuracy-nightly.py")
    monkeypatch.setattr(ran, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(ran, "sensitivity_is_declared", lambda: declared)
    monkeypatch.setattr(ran, "run", lambda *a, **k: pytest.fail(
        "the dry run executed the harness"))

    assert ran.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"sensitivity declared: {declared}" in out, out
    assert "sensitive now:" not in out, (
        "the dry run reports is_sensitive() again; run() stopped consulting it, "
        "so that line predicts a mechanism that decides nothing")

    src = (ROOT / "scripts" / "router-accuracy-nightly.py").read_text(encoding="utf-8")
    assert 'print(f"  sensitive now:  {is_sensitive()}")' not in src


# ============================================================
# 10 - a corrupt baseline is kept, not silently discarded
# ============================================================
def _eval_calls_on(name: str) -> list:
    """Every call in run-skill-eval.py whose first argument is `name`.

    Both tests below used to split the SOURCE on two literals and search the
    slice for a phrase. That is fragile twice over: it breaks when the code is
    reworded, and it breaks when an unrelated COMMENT happens to contain the
    closing literal, which is how it was found (an added comment carrying
    `existing[` truncated the window and turned the test red over a change it
    did not touch). A rule that punishes a file for documenting itself teaches
    people to stop documenting it. The claim is structural, so it is asserted
    structurally.
    """
    tree = ast.parse((ROOT / "scripts" / "run-skill-eval.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == name:
            out.append(node)
    return out


def _eval_method_calls_on(name: str) -> set[str]:
    """Every `name.<attr>(...)` method call in run-skill-eval.py."""
    tree = ast.parse((ROOT / "scripts" / "run-skill-eval.py").read_text(encoding="utf-8"))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name}


def test_an_unparseable_benchmark_is_preserved_and_announced():
    """The wreck goes through the SHARED quarantine writer, which puts it in a
    `.quarantine/` sibling. The old spelling built the name at the call site,
    and `benchmark.json.corrupt` landed inside a TRACKED skill directory in the
    public engine, matched by no ignore rule (measured 2026-08-29). Pinning the
    shared call is the stronger claim: the caller no longer picks a name.

    The end-to-end behaviour is driven in
    tests/test_a_broken_fixture_that_billed_itself_as_an_api_error.py; this pins
    that no second, self-named path grows back beside it.
    """
    callees = {c.func.id for c in _eval_calls_on("benchmark_path")
               if isinstance(c.func, ast.Name)}
    assert "quarantine_file" in callees, callees
    assert "replace" not in _eval_method_calls_on("benchmark_path"), (
        "a wreck named beside the live file is the defect this fixed")


def test_the_benchmark_is_written_atomically():
    """An interrupt mid-write left unparseable JSON, which the quarantine branch
    above then had to deal with."""
    callees = {c.func.id for c in _eval_calls_on("benchmark_path")
               if isinstance(c.func, ast.Name)}
    assert "atomic_write_text" in callees, callees
    assert "write_text" not in _eval_method_calls_on("benchmark_path")


# ============================================================
# 11 - run_checks no longer takes a parameter it never reads
# ============================================================
def test_run_checks_has_no_dead_parameter():
    import inspect
    assert list(inspect.signature(rse.run_checks).parameters) == ["output", "checks"]


def test_run_checks_still_evaluates_a_must_mention():
    results = rse.run_checks("the answer mentions ODUN.ONE",
                             {"must_mention": ["ODUN.ONE"]})
    assert results and all(r["passed"] for r in results)


# ============================================================
# 12 - a markdown link does not reach the published header
# ============================================================
def test_a_link_in_the_subtitle_is_unwrapped(tmp_path):
    md = tmp_path / "page.md"
    md.write_text("# Title\n\n[See the guide](x.md) then read on\n", encoding="utf-8")
    title, subtitle = rdh.extract_title(md.read_text(encoding="utf-8"), "page")
    assert "](" not in subtitle, subtitle
    assert "See the guide" in subtitle


# ============================================================
# 13 - the docs artifacts are written atomically
# ============================================================
def test_no_bare_write_text_remains_in_the_docs_generator():
    src = (ROOT / "scripts" / "regenerate-docs-html.py").read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines()
                 if ".write_text(" in ln and "atomic_write_text" not in ln]
    assert offenders == [], offenders
