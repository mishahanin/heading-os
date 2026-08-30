#!/usr/bin/env python3
"""Shard scripts-02-p3: the capture pair's cleanup, and canopus's field guard.

Three behaviours and three claims.

`capture-design-exemplars.py` and its `-retry.py` sibling each guard every
context close with `close_quietly`, whose docstring names the scenario it
exists for: a browser that died mid-run, whose close raises and REPLACES the
result that was built. One level up, `await browser.close()` sat bare — and it
sits ABOVE the manifest write. So the dead browser the guards survive raised
there instead, `main` exited with a traceback, and every row those guards had
just salvaged was discarded. The comments in both files credit
`return_exceptions=True` with removing exactly that outcome.

The retry script's merge counted a result as a success on `error is None`. But
`capture_one` also finishes with `full_page_error` set and `full_page` left
None — the row it prints as `PART` — and such a row DELETED the complete old
one, dropping the manifest's only reference to a full-page screenshot still on
disk while `ok`/`total` were recomputed as if nothing had gone.

`canopus_check._unreadable` exists so that a hand-edited note is REPORTED
rather than crashing the run before the other notes are checked. It tested
fields with `str(note.get(name, "")).strip()`, and `str(None)` is the non-empty
`"None"`, so a YAML null passed as checkable. C1 then put that None into an
argv list; `subprocess.run` raises TypeError, which is not in `_git`'s except
tuple and is not caught by the clause loop in `main`. The guard's own failure
mode, arriving through a different exception.

Plus three claims that contradicted the code they describe: canopus's module
docstring said `--after-build` "never refuses" while `_after_build` returns 1,
attributed `probe` to step 4 in its header and to step 3 one line below, and
`canopus_check` stated a per-note cost of "two git commands each" that matches
no note that exists.

Found by the 2026-08-23 engine audit, shard `scripts-02-p3`. Fixed 2026-08-24.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import canopus_check as cc  # noqa: E402


def _load(name: str, filename: str):
    """Import a kebab-case script by path.

    Both used to create OUTPUT_DIR at import, which wrote into the engine clone
    on a checkout with no private data overlay. They now do it from `main()`.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def main_mod():
    return _load("p03_cap_main", "capture-design-exemplars.py")


@pytest.fixture(scope="module")
def retry_mod():
    return _load("p03_cap_retry", "capture-design-exemplars-retry.py")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _DeadBrowser:
    """Closes like a browser whose process is already gone."""

    def __init__(self):
        self.close_attempted = False

    async def close(self):
        self.close_attempted = True
        raise RuntimeError("Target page, context or browser has been closed")


class _FakePlaywright:
    def __init__(self, browser):
        self.browser = browser

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def chromium(self):
        outer = self

        class _C:
            async def launch(self, **k):
                return outer.browser
        return _C()


def _ok_row(slug, full_page="p-full.png"):
    return {"slug": slug, "url": f"https://{slug}", "category": "cat",
            "above_fold": f"{slug}-above.png", "full_page": full_page,
            "title": slug, "error": None}


# ---------------------------------------------------------------------------
# Finding 1 -- the close that discarded the run it was cleaning up after
# ---------------------------------------------------------------------------

def test_a_browser_that_dies_before_close_still_leaves_a_manifest(
        main_mod, tmp_path, monkeypatch):
    """The whole point of `return_exceptions=True` two lines above: every
    target degraded to a row, and then the cleanup threw all of them away."""
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(main_mod, "TARGETS", [("a", "https://a", "cat", 0)])
    browser = _DeadBrowser()
    monkeypatch.setattr(main_mod, "async_playwright", _FakePlaywright(browser))

    async def one(browser_, sem, slug, url, cat, settle):
        return _ok_row(slug)

    monkeypatch.setattr(main_mod, "capture_one", one)
    asyncio.run(main_mod.main())

    assert browser.close_attempted, "the close was skipped, not guarded"
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert [r["slug"] for r in manifest["results"]] == ["a"], (
        "the browser's close raised past main and the run's whole output was "
        "lost -- the outcome the comments above the gather say was removed"
    )


def test_a_failing_browser_close_is_printed_not_swallowed(
        main_mod, tmp_path, monkeypatch, capsys):
    """`suppress(Exception)` would pass the test above. Never silent."""
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(main_mod, "TARGETS", [])
    monkeypatch.setattr(main_mod, "async_playwright",
                        _FakePlaywright(_DeadBrowser()))
    asyncio.run(main_mod.main())
    assert "has been closed" in capsys.readouterr().err


def test_the_retry_script_survives_the_same_dead_browser(
        retry_mod, tmp_path, monkeypatch):
    """Same defect, same file pair. Here the close sat above the merge."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"results": [_ok_row("raycast")], "total": 1, "ok": 1, "errors": 0}))
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(retry_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(retry_mod, "RETRIES", [])
    monkeypatch.setattr(retry_mod, "async_playwright",
                        _FakePlaywright(_DeadBrowser()))
    asyncio.run(retry_mod.main())

    saved = json.loads(manifest_path.read_text())
    assert saved["retried_at_utc"], (
        "the close raised before the merge, so the retry ran and recorded "
        "nothing"
    )


def test_closing_nothing_is_silent_not_just_survivable(main_mod, retry_mod,
                                                       capsys):
    """The None guard survived the widening from contexts to any closeable.

    Checking only that this does not raise is not enough: the blanket
    `except Exception` below swallows `None.close()` too, so dropping the guard
    still "works" — it just reports a failed close in a run where there was
    simply nothing to close. `capture_one` reaches here with `ctx = None`
    whenever `new_context` itself failed, which is the run already carrying a
    real error; a second invented one buries it.
    """
    asyncio.run(main_mod.close_quietly(None, "the browser"))
    asyncio.run(retry_mod.close_quietly(None, "the context for x"))
    captured = capsys.readouterr()
    assert captured.err == "", captured.err
    assert captured.out == "", captured.out


def test_a_close_that_works_is_awaited(main_mod):
    """Anchor: a helper that returned early would pass every test above."""
    class _Live:
        closed = False

        async def close(self):
            self.closed = True

    live = _Live()
    asyncio.run(main_mod.close_quietly(live, "the browser"))
    assert live.closed


# ---------------------------------------------------------------------------
# Finding 3 -- a partial retry replacing a complete row
# ---------------------------------------------------------------------------

def test_a_partial_retry_does_not_evict_a_complete_row(retry_mod):
    old = _ok_row("mercury", full_page="mercury-full.png")
    partial = {"slug": "mercury", "error": None, "above_fold": "a.png",
               "full_page": None, "full_page_error": "timeout 25000ms"}
    assert retry_mod._replaces(partial, old) is False, (
        "the complete row is deleted and the manifest loses its only pointer "
        "to a full-page screenshot that is still on disk"
    )


def test_a_partial_retry_is_still_kept_when_there_was_nothing_better(retry_mod):
    """Refusing every partial would lose the above-fold capture too."""
    old = {"slug": "mercury", "error": "dead", "above_fold": None,
           "full_page": None}
    partial = {"slug": "mercury", "error": None, "above_fold": "a.png",
               "full_page": None, "full_page_error": "timeout"}
    assert retry_mod._replaces(partial, old) is True
    assert retry_mod._replaces(partial, None) is True


def test_a_complete_retry_replaces_whatever_was_there(retry_mod):
    assert retry_mod._replaces(_ok_row("mercury"), _ok_row("mercury")) is True


def test_a_failed_retry_never_replaces_anything(retry_mod):
    failed = {"slug": "mercury", "error": "boom", "full_page": None}
    assert retry_mod._replaces(failed, None) is False


def test_the_merge_keeps_the_full_page_row_and_says_so(retry_mod, tmp_path,
                                                       monkeypatch, capsys):
    """End to end, through `main`: the manifest is what the operator reads."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"results": [_ok_row("mercury", full_page="mercury-full.png")],
         "total": 1, "ok": 1, "errors": 0}))
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(retry_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(retry_mod, "RETRIES",
                        [("mercury", "https://mercury.com", "p", 0, True)])

    class _LiveBrowser:
        async def close(self):
            return None

    monkeypatch.setattr(retry_mod, "async_playwright",
                        _FakePlaywright(_LiveBrowser()))

    async def partial(browser, slug, url, cat, settle, full):
        return {"slug": slug, "url": url, "category": cat,
                "above_fold": f"{slug}-above.png", "full_page": None,
                "full_page_error": "timeout 25000ms", "title": slug,
                "error": None}

    monkeypatch.setattr(retry_mod, "capture_one", partial)
    asyncio.run(retry_mod.main())

    rows = json.loads(manifest_path.read_text())["results"]
    assert len(rows) == 1
    assert rows[0]["full_page"] == "mercury-full.png", (
        "the partial retry overwrote the complete row"
    )
    assert "Kept the earlier" in capsys.readouterr().out, (
        "a discarded retry result must not be silent"
    )


# ---------------------------------------------------------------------------
# Finding 2 -- a null field that passed the guard and killed the run
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, 5, [], ["a"], {}, True])
def test_a_non_string_field_is_not_a_usable_value(value):
    assert cc._text(value) == ""


def test_a_real_string_survives_and_is_trimmed():
    """Anchor: a helper that refused everything would pass the test above."""
    assert cc._text("  abc  ") == "abc"
    assert cc._text("   ") == ""


@pytest.mark.parametrize("bad", [None, 12345, []])
def test_a_null_approval_sha_is_reported_not_run(bad):
    """`str(None)` is "None", which is non-empty: the note used to pass here
    and reach `subprocess.run` as an argv element."""
    note = {"slug": "s", "approval_sha": bad, "contract": "tests/contract/x"}
    assert "approval_sha" in cc._unreadable(note)


def test_a_retired_note_with_a_null_promotion_is_reported():
    note = {"slug": "s", "approval_sha": "abc", "contract": "c",
            "retired_sha": "def", "promoted_to": None}
    assert "promoted_to" in cc._unreadable(note)


def test_a_complete_note_is_still_checkable():
    """Anchor: a guard that refused every note would pass the four above."""
    assert cc._unreadable({"slug": "s", "approval_sha": "abc",
                           "contract": "tests/contract/x"}) == ""


def test_the_crash_this_guard_stands_in_front_of_is_real(tmp_path):
    """Why the guard has to be at the FIELD, not in `_git`'s except tuple.

    TypeError is not in that tuple, so it never becomes a CheckError, and the
    clause loop in `main` has no try around it: one bad note ends the run
    before any other note is checked. And widening the tuple is not enough on
    its own -- the handler's own `' '.join(argv)` raises the same TypeError on
    the same None.
    """
    with pytest.raises(TypeError):
        cc._git(tmp_path, "rev-parse", None)
    assert TypeError not in (OSError, subprocess.SubprocessError, ValueError)


def _write_raw_note(root, slug, **fields):
    """Write `records/slices/{slug}.md` directly, bypassing write_note's schema.

    A note that is MISSING a required field is the whole point here, and
    `write_note` validates, so it cannot produce the fixture.
    """
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    path = root / "records" / "slices" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{body}\n---\n\nBody.\n", encoding="utf-8")
    return path


def test_a_bad_note_does_not_stop_the_notes_after_it(tmp_path, monkeypatch, capsys):
    """The guarantee itself, through `main`: report, then keep going.

    Rewritten 2026-08-30. The docstring said "through `main`" and `main` was
    never called. The body reimplemented a per-note loop of its own -- read
    `_unreadable`, append a `_row`, `continue` -- and then asserted against
    that. So the thing under test was three lines of this test file: monkeypatch
    `cc.main` to raise on sight and the test still passed, and mutating the real
    `main()` to stop at the first unreadable note, or to raise before reaching
    the later ones, left it green while breaking exactly the guarantee it
    names. `main` is now driven, and the two notes are read off disk.
    """
    # `aaa-bad` sorts before `zzz-good`: note_paths() is sorted, so this fixes
    # the bad note as the FIRST one processed. Without that ordering the test
    # could pass on a run that never had to recover.
    _write_raw_note(tmp_path, "aaa-bad", value="v", contract="tests/contract/bad")
    _write_raw_note(
        tmp_path, "zzz-good", value="v", approval_sha="abc",
        contract="tests/contract/good", plan_digest="d", scrutinize_plan="s",
        scrutinize_built="s", undo="u")

    monkeypatch.setattr(cc, "_git", lambda root, *argv: subprocess.CompletedProcess(
        argv, 0, "", ""))

    rc = cc.main(["--root", str(tmp_path), "--json"])

    rows = json.loads(capsys.readouterr().out)
    slugs = [r["slug"] for r in rows]

    assert "aaa-bad" in slugs, "the unreadable note was not reported at all"
    assert "zzz-good" in slugs, (
        "the note AFTER the bad one was never reached; main stopped at the "
        "first unreadable note instead of reporting and continuing")
    assert slugs.index("aaa-bad") < slugs.index("zzz-good")

    bad_rows = [r for r in rows if r["slug"] == "aaa-bad"]
    assert any(r["ok"] is False for r in bad_rows), bad_rows
    assert rc != 0, "an unreadable note must not exit clean"


# ---------------------------------------------------------------------------
# Finding 6 -- the stated per-note cost, measured
# ---------------------------------------------------------------------------

def _counting_git(monkeypatch, returncode=0):
    calls = []

    def _fake(root, *argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", "")

    monkeypatch.setattr(cc, "_git", _fake)
    return calls


def test_c2_is_exactly_one_git_command(tmp_path, monkeypatch):
    calls = _counting_git(monkeypatch)
    cc.C2(tmp_path, {"slug": "s", "approval_sha": "abc"})
    assert len(calls) == 1, f"the docstring's cost bound is wrong: {calls}"
    assert calls[0][0] == "merge-base"


def test_c1_is_one_git_command_for_a_live_note(tmp_path, monkeypatch):
    calls = _counting_git(monkeypatch)
    cc.C1(tmp_path, {"slug": "s", "approval_sha": "abc", "contract": "c"})
    assert len(calls) == 1, f"the docstring's cost bound is wrong: {calls}"
    assert calls[0][0] == "diff"


def test_c1_is_two_git_commands_for_a_retired_note(tmp_path, monkeypatch):
    """The window end costs a `cat-file -e` before the diff. Two is the CEILING
    the docstring now states, and this is the note that reaches it."""
    calls = _counting_git(monkeypatch)
    cc.C1(tmp_path, {"slug": "s", "approval_sha": "abc", "contract": "c",
                     "retired_sha": "def"})
    assert [c[0] for c in calls] == ["cat-file", "diff"]


def test_the_cost_bound_states_a_ceiling_not_a_flat_number():
    doc = cc.__doc__
    assert "at most two git commands each" in doc
    assert "_in_range" in doc, (
        "`--range` adds up to two rev-parse calls per note that the bound did "
        "not mention"
    )


# ---------------------------------------------------------------------------
# Findings 4 and 5 -- two claims canopus.py made about itself
# ---------------------------------------------------------------------------

def _canopus_doc() -> str:
    spec = importlib.util.spec_from_file_location("p03_canopus",
                                                  ROOT / "scripts" / "canopus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.__doc__


def test_the_header_and_the_usage_line_agree_on_probes_step():
    """They said 4 and 3, one line apart. `probe` is step 3: the standard
    measures vacuity BEFORE the approval commit, which is what step 4 is."""
    doc = _canopus_doc()
    header = doc.splitlines()[0]
    assert "steps 3 and 7" in header, header
    usage = next(line for line in doc.splitlines() if "canopus.py probe" in line)
    assert "step 3" in usage
    assert "step 4" not in header


def test_the_after_build_exit_contract_matches_the_code():
    """The module doc is the first thing an operator reads, and it said exit 1
    was impossible. `_after_build` returns 1 when no reading could be made."""
    doc = _canopus_doc()
    after_build = doc.split("`probe --after-build")[1]
    assert "exit is 1" in after_build or "non-zero exit is 1" in after_build
    assert "no reading could be made" in after_build


def test_the_code_still_has_the_exit_the_doc_now_admits():
    """Guard the premise: if `_after_build` stopped returning 1, the doc above
    would be the wrong one to trust."""
    src = (ROOT / "scripts" / "canopus.py").read_text(encoding="utf-8")
    body = src.split("def _after_build")[1].split("\ndef ")[0]
    assert "return 1" in body
