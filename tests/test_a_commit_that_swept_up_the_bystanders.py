#!/usr/bin/env python3
"""Shard 09-p3: a commit that took work it was never shown.

`linkedin-archive.py --commit` ran a bare `git commit -m`, which commits
EVERYTHING currently staged. Any unrelated work the operator had staged went
into a commit labelled as a LinkedIn archive move, silently. It now names the
two paths this run touched and nothing else.

Its tracked-check had the mirror problem: `git ls-files --error-unmatch` failing
for a reason the stderr parser cannot read -- not a repository, a corrupt index,
the outside-repository error the call site's own comment describes -- returned
`[]`, which reads as "everything is tracked". The run then died at `git mv` and
blamed the wrong thing.

`marp_render.py` carried eight. The three that reach a rendered deck: slide
splitting was blind to code fences, so a deck ABOUT markdown got `---` breaks
pushed into its own examples and had its fences torn across slides; the `from`
mode wrote its intermediate file to the system temp dir, so every relative image
path in a workspace document resolved nowhere, which is the exact failure the
comment in `render` says must not happen; and the sanitizer DELETED U+00A0
instead of replacing it, joining the words around it.

Three more that hide a failure or crash on one: a frontmatter title containing a
quote produced malformed YAML; a failed `--images png` run appended nothing to
`errors`, so "Render successful" printed with no PNGs; and a marp-cli hang
raised `TimeoutExpired` straight through the CLI, in a function whose every
other failure is a structured result.

Two about watch mode: the Windows liveness check matched the PID as a SUBSTRING
of `tasklist` output, so 808 matched 8080; and `watch_stop` signalled a recorded
PID with no liveness check at all, which after PID reuse is a SIGTERM at a
stranger.

`llm-fit-report.py` divided its downgrade rate by a denominator that counts
traces which can never be flagged.

One finding is REFUTED, with evidence below: `lint-ratchet.py` was said to crash
when run from another directory. ruff emits ABSOLUTE paths, so it does not. The
line is hardened anyway, since nothing here pinned that ruff detail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


la = _load("linkedin-archive", "linkedin_archive_09p3")
lr = _load("lint-ratchet", "lint_ratchet_09p3")
lf = _load("llm-fit-report", "llm_fit_report_09p3")

from scripts import marp_render as m  # noqa: E402


# ============================================================
# F1 -- the commit that swept up the bystanders
# ============================================================
def test_the_commit_names_a_pathspec():
    src = (ROOT / "scripts" / "linkedin-archive.py").read_text(encoding="utf-8")
    assert '"git", "commit", "-m", msg, "--", str(md), str(dest_folder)' in src


def test_a_bare_commit_is_gone():
    src = (ROOT / "scripts" / "linkedin-archive.py").read_text(encoding="utf-8")
    assert '["git", "commit", "-m", msg]' not in src


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway repo with one committed file, so `git mv` has history."""
    def run(*args, **kw):
        return subprocess.run(["git", *args], cwd=str(tmp_path), capture_output=True,
                              text=True, timeout=60, **kw)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "T")
    run("config", "commit.gpgsign", "false")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    run("add", "seed.txt")
    run("commit", "-q", "-m", "seed", "--no-verify")
    return tmp_path, run


def test_an_unrelated_staged_file_is_not_swept_into_the_archive_commit(git_repo):
    """End to end, with a real git: the defect this finding is about."""
    repo, run = git_repo
    (repo / "moved.md").write_text("post\n", encoding="utf-8")
    run("add", "moved.md")
    run("commit", "-q", "-m", "add post", "--no-verify")

    (repo / "BYSTANDER.txt").write_text("the operator's own work\n", encoding="utf-8")
    run("add", "BYSTANDER.txt")

    dest = repo / "archive" / "post"
    dest.mkdir(parents=True)
    run("mv", "--", "moved.md", str(dest / "moved.md"))
    out = run("commit", "-m", "chore(linkedin-archive)", "--",
              str(repo / "moved.md"), str(dest))
    assert out.returncode == 0, out.stderr

    named = run("show", "--name-only", "--format=", "HEAD").stdout
    assert "BYSTANDER.txt" not in named, f"the bystander was committed:\n{named}"
    assert "archive/post/moved.md" in named
    assert run("diff", "--cached", "--name-only").stdout.strip() == "BYSTANDER.txt"


# ============================================================
# F2 -- an unreadable git failure is not "everything is tracked"
# ============================================================
class _Result:
    def __init__(self, rc, stderr=""):
        self.returncode, self.stdout, self.stderr = rc, "", stderr


def test_an_unparsable_git_failure_raises_instead_of_reporting_tracked(monkeypatch):
    monkeypatch.setattr(la, "_run_git",
                        lambda *a, **k: _Result(128, "fatal: not a git repository"))
    with pytest.raises(la.GitProbeError) as exc:
        la.find_untracked([Path("a.md")], Path("/nowhere"))
    assert "not a git repository" in str(exc.value)
    assert "unknown" in str(exc.value)


def test_a_parsable_failure_still_names_the_untracked_file(monkeypatch):
    monkeypatch.setattr(
        la, "_run_git",
        lambda *a, **k: _Result(1, "error: pathspec 'a.md' did not match any file(s)"))
    assert la.find_untracked([Path("a.md")], Path("/x")) == [Path("a.md")]


def test_a_clean_exit_still_means_tracked(monkeypatch):
    monkeypatch.setattr(la, "_run_git", lambda *a, **k: _Result(0))
    assert la.find_untracked([Path("a.md")], Path("/x")) == []


def test_no_paths_short_circuits_without_calling_git(monkeypatch):
    monkeypatch.setattr(la, "_run_git",
                        lambda *a, **k: pytest.fail("git must not be called"))
    assert la.find_untracked([], Path("/x")) == []


@pytest.fixture
def archive_layout(tmp_path, monkeypatch):
    """The minimum tree `main()` needs to reach the tracked-check."""
    src = tmp_path / "outputs" / "content" / "linkedin"
    src.mkdir(parents=True)
    (src / "2026-08-25-thing_linkedin-post_.md").write_text("post\n", encoding="utf-8")
    arch = tmp_path / "datastore" / "content" / "linkedin-archive"
    arch.mkdir(parents=True)
    monkeypatch.setattr(la, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(la, "get_outputs_dir", lambda: tmp_path / "outputs")
    monkeypatch.setattr(la, "get_datastore_dir", lambda: tmp_path / "datastore")
    return tmp_path


def test_an_unreadable_git_answer_aborts_the_run(archive_layout, monkeypatch, capsys):
    """`main()` must not walk past a tracked-check it could not answer."""
    monkeypatch.setattr(la, "_run_git",
                        lambda *a, **k: _Result(128, "fatal: not a git repository"))
    assert la.main(["--execute"]) == 11
    assert "ABORT" in capsys.readouterr().err


def test_the_abort_names_the_reason_git_gave(archive_layout, monkeypatch, capsys):
    monkeypatch.setattr(la, "_run_git",
                        lambda *a, **k: _Result(128, "fatal: not a git repository"))
    la.main(["--execute"])
    assert "not a git repository" in capsys.readouterr().err


def test_nothing_is_moved_when_the_tracked_check_could_not_be_answered(
        archive_layout, monkeypatch):
    monkeypatch.setattr(la, "_run_git", lambda *a, **k: _Result(128, "fatal: broken index"))
    la.main(["--execute"])
    assert (archive_layout / "outputs" / "content" / "linkedin"
            / "2026-08-25-thing_linkedin-post_.md").exists()
    assert not (archive_layout / "datastore" / "content" / "linkedin-archive"
                / "posts").exists()


def test_a_genuinely_untracked_file_still_gets_its_own_exit_code(
        archive_layout, monkeypatch, capsys):
    """Exit 7 and exit 11 are different answers and must stay apart."""
    monkeypatch.setattr(
        la, "_run_git",
        lambda *a, **k: _Result(1, "error: pathspec 'x.md' did not match any file(s)"))
    monkeypatch.setattr(la, "find_untracked", lambda paths, ws: list(paths))
    assert la.main(["--execute"]) == 7


def test_the_new_exit_code_is_documented():
    doc = la.__doc__ or ""
    assert "11 the tracked-check could not be answered" in " ".join(doc.split())


# ============================================================
# F3 -- REFUTED, then hardened anyway
# ============================================================
def test_ruff_emits_absolute_paths_which_is_why_the_finding_is_refuted():
    """The audit assumed cwd-relative paths and predicted a crash from /tmp."""
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    items = json.loads(out.stdout or "[]")
    if not items:
        pytest.skip("ruff reported a clean tree; nothing to inspect")
    assert Path(items[0]["filename"]).is_absolute()


def test_the_gate_produces_a_verdict_from_another_directory(tmp_path):
    out = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "scripts" / "lint-ratchet.py"),
         "check"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=600)
    combined = out.stdout + out.stderr
    assert "lint-ratchet:" in combined, combined
    assert "Traceback" not in combined
    assert "is not in the subpath of" not in combined


def test_the_path_join_is_anchored_to_the_root_not_the_cwd():
    src = (ROOT / "scripts" / "lint-ratchet.py").read_text(encoding="utf-8")
    assert 'rel = (root / it["filename"]).resolve().relative_to(root).as_posix()' in src


def test_a_relative_ruff_path_would_also_resolve_correctly(monkeypatch, tmp_path):
    """The hardening, checked rather than asserted: pathlib discards the left
    side when the right one is absolute, so one expression covers both shapes."""
    root = tmp_path
    assert (root / "/abs/x.py") == Path("/abs/x.py")
    assert (root / "scripts/x.py") == root / "scripts" / "x.py"


# ============================================================
# F4 -- frontmatter with a quote in it
# ============================================================
def _fm_of(text: str) -> dict:
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---", 2)[1])


def test_a_title_containing_a_quote_still_parses():
    out = m.inject_frontmatter("body", title='Says "hello"')
    assert _fm_of(out)["title"] == 'Says "hello"'


def test_a_title_containing_a_colon_still_parses():
    out = m.inject_frontmatter("body", title="Q3: the reckoning")
    assert _fm_of(out)["title"] == "Q3: the reckoning"


def test_a_title_containing_a_newline_still_parses():
    out = m.inject_frontmatter("body", title="line one\nline two")
    assert _fm_of(out)["title"] == "line one\nline two"


def test_a_plain_title_is_unchanged():
    out = m.inject_frontmatter("body", title="Plain Title")
    assert _fm_of(out)["title"] == "Plain Title"


def test_booleans_are_still_emitted_unquoted():
    out = m.inject_frontmatter("body", title="x")
    assert "marp: true" in out
    assert _fm_of(out)["marp"] is True


def test_the_paginate_reconstruction_escapes_the_same_way():
    heavy = "---\ntitle: \"a\"\n---\n\n" + ("word " * 200)
    src = heavy.replace('title: "a"', 'title: "Says \\"hi\\""')
    out = m.paginate_heavy(src)
    assert _fm_of(out)["title"] == 'Says "hi"'


# ============================================================
# F5 -- the temp file that moved the source directory
# ============================================================
def test_the_from_mode_temp_file_is_written_beside_the_source():
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    block = src.split('prefix=f".{source.stem}.marp-from-"')[1][:120]
    assert "dir=str(source.parent)" in block


def test_the_render_temp_file_still_sits_beside_its_source():
    """Regression: `render` already did this; the fix copies it, not replaces it."""
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    block = src.split('prefix=f".{source.stem}.marp-src-"')[1][:120]
    assert "dir=str(source.parent)" in block


# ============================================================
# F6 -- slide splitting that could not see a code fence
# ============================================================
_FENCED = (
    "# Doc\n\n"
    "```markdown\n"
    "## Inside The Fence\n"
    "---\n"
    "still inside\n"
    "```\n\n"
    "## Real Heading\n\n"
    "text\n"
)


def test_a_heading_inside_a_fence_gets_no_slide_break():
    out = m.auto_slide_breaks(_FENCED)
    assert "```markdown\n## Inside The Fence\n---\nstill inside\n```" in out


def test_a_real_heading_outside_the_fence_still_gets_one():
    out = m.auto_slide_breaks(_FENCED)
    assert "\n---\n\n## Real Heading" in out


def test_a_dash_line_inside_a_fence_is_not_a_manual_break():
    """It used to look like one, so auto_slide_breaks returned the body untouched."""
    assert m.auto_slide_breaks(_FENCED) != _FENCED


def test_a_real_dash_line_is_still_a_manual_break():
    body = "# A\n\n---\n\n## B\n"
    assert m.auto_slide_breaks(body) == body


def test_split_slides_ignores_a_fenced_dash_line():
    assert len(m.split_slides(_FENCED)) == 1


def test_split_slides_still_splits_on_real_ones():
    assert len(m.split_slides("a\n\n---\n\nb\n\n---\n\nc")) == 3


def test_overflow_counting_no_longer_splits_a_fenced_slide():
    """Both halves are over threshold, so a wrong split shows up as TWO warnings."""
    body = ("---\ntitle: x\n---\n\n" + ("word " * 200)
            + "\n\n```\n" + ("code " * 200) + "\n---\n" + ("more " * 200) + "\n```\n")
    warnings = m.check_overflow(body)
    assert len(warnings) == 1, warnings


def test_paginate_heavy_does_not_re_split_on_a_fenced_dash_line():
    """The fenced block must survive VERBATIM. A regex split tears it at the
    inner `---` and rejoins the halves with blank lines around a slide break."""
    fence = "```\n" + ("code " * 200) + "\n---\n" + ("more " * 200) + "\n```"
    body = "---\ntitle: x\n---\n\n" + ("word " * 200) + "\n\n" + fence + "\n"
    out = m.paginate_heavy(body)
    assert fence in out, "the fenced block was torn across slides"


@pytest.mark.parametrize("fence", ["```", "~~~", "````"])
def test_every_fence_style_is_recognised(fence):
    lines = ["a", fence, "---", fence, "b"]
    assert m.fence_mask(lines) == [False, True, True, True, False]


def test_a_two_character_run_is_not_a_fence():
    """`` `` `` opens an inline code span, not a block. Treating it as a fence
    would swallow every line after it."""
    assert m.fence_mask(["``code``", "---", "text"]) == [False, False, False]


def test_an_indented_fence_is_recognised():
    lines = ["  ```", "  ---", "  ```"]
    assert m.fence_mask(lines) == [True, True, True]


def test_a_tilde_fence_is_not_closed_by_a_backtick_fence():
    lines = ["~~~", "```", "---", "~~~", "out"]
    assert m.fence_mask(lines) == [True, True, True, True, False]


def test_an_unclosed_fence_swallows_the_rest():
    """Failing open here would push breaks into an unterminated code block."""
    assert m.fence_mask(["```", "## H", "more"]) == [True, True, True]


# ============================================================
# F7 -- a no-break space is a space
# ============================================================
def test_a_no_break_space_becomes_a_plain_space():
    clean, count = m.run_sanitizer("10\u00a0pages")
    assert clean == "10 pages"
    assert count == 1


def test_a_zero_width_character_still_disappears():
    clean, count = m.run_sanitizer("word\u200bjoin")
    assert clean == "wordjoin"
    assert count == 1


def test_both_kinds_are_counted_together():
    clean, count = m.run_sanitizer("10\u00a0pages\u200bhere")
    assert clean == "10 pageshere"
    assert count == 2


def test_clean_text_is_returned_unchanged():
    assert m.run_sanitizer("plain text") == ("plain text", 0)


# ============================================================
# F8 / F9 -- a failed render that reported success, and a hang that crashed
# ============================================================
def test_a_marp_timeout_returns_none_instead_of_raising(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="marp", timeout=120)

    monkeypatch.setattr(m.subprocess, "run", boom)
    assert m._run_marp(["marp"]) is None


def test_the_wrapper_passes_a_timeout(monkeypatch):
    """Without one, `TimeoutExpired` can never be raised and the hang is silent."""
    seen = {}

    def capture(cmd, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(m.subprocess, "run", capture)
    m._run_marp(["marp"])
    assert seen.get("timeout") == m.MARP_TIMEOUT_S


def test_a_normal_marp_run_is_passed_through(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: sentinel)
    assert m._run_marp(["marp"]) is sentinel


@pytest.fixture
def stub_marp(monkeypatch, tmp_path):
    """Drive `render` with marp-cli replaced, so its result dict is observable."""
    monkeypatch.setattr(m, "check_marp_installed", lambda: (True, "4.4.0"))
    monkeypatch.setattr(m, "check_version_match", lambda v: True)
    monkeypatch.setattr(m, "_resolve_marp_bin", lambda: "/bin/true")
    monkeypatch.setattr(m, "probe_browser", lambda: None)
    theme = tmp_path / "theme.css"
    theme.write_text("/* theme */", encoding="utf-8")
    monkeypatch.setattr(m, "prepare_theme", lambda: theme)

    src = tmp_path / "deck.md"
    src.write_text("---\nmarp: true\n---\n\n# One\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    def run(marp_result):
        monkeypatch.setattr(m, "_run_marp", lambda cmd: marp_result)
        return m.render(src, output_dir=out_dir, images_png=True, verbose=False)

    return run


class _Marp:
    def __init__(self, rc, stderr=""):
        self.returncode, self.stdout, self.stderr = rc, "", stderr


def test_a_zero_exit_that_wrote_no_png_is_not_success(stub_marp):
    """The defect: marp exited 0, produced nothing, and `render` said ok."""
    result = stub_marp(_Marp(0))
    assert result["ok"] is False
    assert any(e["type"] == "png" and e["error"] == "no-output"
               for e in result["errors"]), result["errors"]


def test_a_failed_png_run_is_not_success(stub_marp):
    result = stub_marp(_Marp(1, "boom"))
    assert result["ok"] is False
    assert any(e["type"] == "png" and e["error"] == "render-failed"
               for e in result["errors"]), result["errors"]


def test_a_png_timeout_is_not_success(stub_marp):
    result = stub_marp(None)
    assert result["ok"] is False
    assert any(e["type"] == "png" and e["error"] == "timeout"
               for e in result["errors"]), result["errors"]


def test_the_png_branch_records_a_failure():
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    png_block = src.split("# Render PNG images")[1]
    assert '"type": "png", "error": "render-failed"' in png_block
    assert '"type": "png", "error": "no-output"' in png_block
    assert '"type": "png", "error": "timeout"' in png_block


def test_every_marp_invocation_goes_through_the_timeout_wrapper():
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    assert "subprocess.run(cmd, capture_output=True, text=True, timeout=120)" not in src
    assert src.count("result = _run_marp(cmd)") == 3


def test_each_render_branch_handles_the_timeout_result():
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    assert src.count("if result is None:") == 3


# ============================================================
# F11 -- a PID matched by substring, and a signal fired blind
# ============================================================
def test_the_windows_liveness_check_compares_the_pid_field(monkeypatch):
    """808 must not match 8080."""
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")

    class _R:
        stdout = '"marp.exe","8080","Console","1","50,000 K"\n'

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _R())
    assert m._is_process_running(808) is False
    assert m._is_process_running(8080) is True


def test_the_posix_liveness_check_is_unchanged():
    assert m._is_process_running(os.getpid()) is True
    assert m._is_process_running(2 ** 22 - 1) in (True, False)


def test_watch_stop_does_not_signal_a_dead_pid(tmp_path, monkeypatch):
    state = tmp_path / "watch.json"
    state.write_text(json.dumps({"pid": 424242, "theme_path": ""}), encoding="utf-8")
    monkeypatch.setattr(m, "WATCH_STATE_FILE", state)
    monkeypatch.setattr(m, "_is_process_running", lambda pid: False)
    monkeypatch.setattr(m.os, "kill",
                        lambda *a: pytest.fail("signalled a PID it never checked"))
    result = m.watch_stop()
    assert result["ok"] is True
    assert result["signalled"] is False
    assert "stale watch state removed" in result["message"]
    assert not state.exists()


def test_watch_stop_still_signals_a_live_pid(tmp_path, monkeypatch):
    state = tmp_path / "watch.json"
    state.write_text(json.dumps({"pid": 999, "theme_path": ""}), encoding="utf-8")
    monkeypatch.setattr(m, "WATCH_STATE_FILE", state)
    monkeypatch.setattr(m, "_is_process_running", lambda pid: True)
    sent = []
    monkeypatch.setattr(m.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    result = m.watch_stop()
    assert result["signalled"] is True
    assert sent == [(999, m.signal.SIGTERM)]


def test_watch_stop_with_no_state_file_is_still_a_clean_no(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "WATCH_STATE_FILE", tmp_path / "absent.json")
    assert m.watch_stop()["ok"] is False


# ============================================================
# F10 -- the browser-probe comment
# ============================================================
def test_the_browser_probe_comment_matches_the_candidate_order():
    src = (ROOT / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    comment = src.split("# Candidate order:")[1].split("for name in [")[0]
    assert "Google's own packages first" in comment
    assert "no snap-specific path is probed" in comment
    order = src.split("for name in [")[1].split("]")[0]
    assert order.index("google-chrome") < order.index("chromium")


# ============================================================
# F12 -- a rate divided by traces that can never be counted
# ============================================================
class _Trace:
    def __init__(self, name, metadata, tags):
        self.name, self.metadata, self.tags = name, metadata, tags


def _anthropic(flagged: bool):
    return _Trace("skill", {"downgrade_signals": {"downgrade_candidate": flagged,
                                                  "output_tokens": 100}},
                  ["vendor:anthropic"])


def _fallback():
    """A Gemini-served trace: signals is None, so it can never be flagged."""
    return _Trace("skill", {"downgrade_signals": None, "fallback_triggered": True},
                  ["vendor:gemini"])


def test_the_rate_is_taken_over_the_flag_eligible_traces():
    traces = [_anthropic(True)] * 4 + [_anthropic(False)] * 4 + [_fallback()] * 8
    agg = lf.aggregate(traces)["skill"]
    assert agg["total"] == 16
    assert agg["flag_eligible"] == 8
    assert agg["downgrade_pct"] == pytest.approx(50.0)


def test_a_bucket_with_no_eligible_traces_reports_zero_not_a_crash():
    agg = lf.aggregate([_fallback()] * 3)["skill"]
    assert agg["flag_eligible"] == 0
    assert agg["downgrade_pct"] == 0.0


def test_a_bucket_with_no_fallback_is_unchanged():
    """Regression: where the old denominator was already right."""
    agg = lf.aggregate([_anthropic(True), _anthropic(False)])["skill"]
    assert agg["downgrade_pct"] == pytest.approx(50.0)


def test_the_column_header_says_which_population_it_measures():
    agg = lf.aggregate([_anthropic(True), _fallback()])
    out = lf.render_markdown(agg, 7, "2026-08-25T00:00:00Z", 2)
    assert "Downgrade flag % (of flag-eligible)" in out


def test_the_candidate_line_quotes_the_eligible_denominator():
    agg = lf.aggregate([_anthropic(True)] * 3 + [_fallback()] * 5)
    out = lf.render_markdown(agg, 7, "2026-08-25T00:00:00Z", 8)
    assert "3/3 flagged" in out
    assert "3/8 flagged" not in out
