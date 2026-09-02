"""Shard 48: the failures two tools absorbed, and the totals they printed after.

Nine defects across `scripts/docparse.py` and
`scripts/generate-newsletter-html.py`, every one the same shape: something went
wrong, the tool carried on, and the number or sentence it printed afterwards did
not mention it. Each was reproduced by running the code before a line changed.

docparse:

1. `cmd_clear_cache --file` wrapped BOTH the cache read and the unlink in one
   `except (json.JSONDecodeError, OSError): pass`. MEASURED with one matching
   entry that raises EACCES on unlink: it printed `Removed 0 cache entries for
   q3.pdf` and stopped, which an operator reads as "there was no cache for that
   file" - the opposite of the truth. The entry is still on disk and the next
   parse still reads it. A corrupt entry was skipped equally silently, and a
   corrupt entry is one the loop cannot rule out.
2. The `--force` branch is the second copy of the same defect: it counted the
   entries it FOUND and called them cleared, and an OSError anywhere in the
   sweep aborted it with a traceback and no summary.
3. `cmd_parse` wrote `"total_files": len(results["files"])`, counting successes
   and nothing else. MEASURED on five documents of which two raised: the
   archived JSON said `total_files: 3`, carried no record of the other two, and
   printed `3 files`. The errors went to stderr, where they scroll away.
4. `cmd_report` then read that JSON and had no way to know the sweep was
   partial, so a report over three fifths of a corpus looked exactly like one
   over all of it - with an answer written against the three.
5. `_setup_check` printed "All prerequisites met" over a method that checked
   three things are PRESENT and never compared a version, while
   `setup --install` pins `liteparse=={LITEPARSE_VERSION}`. MEASURED on the
   operator's machine 2026-08-28: LITEPARSE_VERSION is "2.0.0", the installed
   package is 2.9.0, and the check reported all prerequisites met.

generate-newsletter-html:

6. `esc()` guarded on `if not text`, which is true of `0`, `0.0` and `False`.
   MEASURED: `esc(0)` returned `""`, so `{"value": 0, "label": "deals closed"}`
   rendered a label with an empty value beside it. Zero is the one number a
   reader cannot reconstruct from the absence of a number.
7. `build_market_depth` built the stats overlay and the market caption INSIDE
   `if bars:`. MEASURED with stats and a caption present and `bars` absent:
   "$347,850", "median" and the caption were all missing. The bars are
   decoration; the stats are the operator's figures.
8. `build_navigation_chart` looped a fixed list of four keys. MEASURED: a
   chart carrying "apac" and "eu" beside "gcc" rendered the Gulf row alone.
9. `--images` dropped any mapping without `=`, and the shape that produces one
   is a typo: `--images sea_state /path/img.png` is two bare words under
   `nargs="*"`. Plus `embed_image` returned "" for a missing path in silence,
   and `issue_number` went into the page unescaped in BOTH places it renders.

Tests: this file.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dp = _load("docparse", "docparse_s48")
nl = _load("generate-newsletter-html", "newsletter_s48")


# ==========================================================================
# 1 - clear-cache, which swallowed both halves of its own job
# ==========================================================================

def _cache(tmp_path: Path, monkeypatch) -> Path:
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(dp, "cache_dir", lambda: cache)
    return cache


def _entry(cache: Path, name: str, for_file: Path) -> Path:
    p = cache / name
    p.write_text(json.dumps({"file": str(for_file.resolve())}), encoding="utf-8")
    return p


def _clear(target: Path, force=False):
    """Run cmd_clear_cache, returning (stdout, stderr, exit_code_or_None)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    args = types.SimpleNamespace(file=str(target) if target else None, force=force)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            dp.cmd_clear_cache(args)
        except SystemExit as e:
            code = e.code
    return out.getvalue(), err.getvalue(), code


def test_an_unreadable_cache_entry_is_named(tmp_path, monkeypatch):
    """A corrupt entry is one the loop cannot rule out: it may be the cache of
    exactly the file the operator asked to clear."""
    cache = _cache(tmp_path, monkeypatch)
    target = tmp_path / "q3.pdf"
    _entry(cache, "good.json", target)
    (cache / "corrupt.json").write_text("{not json at all", encoding="utf-8")

    out, err, code = _clear(target)

    assert "Removed 1" in out
    assert "corrupt.json" in err
    assert "unreadable" in err
    assert code is None, "an unreadable stranger is not a failure of the command"


def test_a_matching_entry_that_cannot_be_deleted_is_reported(tmp_path, monkeypatch):
    """MEASURED before the fix: `Removed 0 cache entries for q3.pdf`, nothing
    else, and the entry still on disk."""
    cache = _cache(tmp_path, monkeypatch)
    target = tmp_path / "q3.pdf"
    entry = _entry(cache, "a.json", target)
    real_unlink = Path.unlink

    def _boom(self, *a, **k):
        if self.name == "a.json":
            raise OSError(13, "Permission denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _boom)
    out, err, code = _clear(target)

    assert code == 1, "the command did not do what it was asked and reported success"
    assert "a.json" in err
    assert "Permission denied" in err
    assert entry.exists()


def test_a_clean_clear_says_nothing_extra(tmp_path, monkeypatch):
    """A warning that fires on the healthy path is a warning nobody reads."""
    cache = _cache(tmp_path, monkeypatch)
    target = tmp_path / "q3.pdf"
    _entry(cache, "a.json", target)
    _entry(cache, "b.json", tmp_path / "other.pdf")

    out, err, code = _clear(target)

    assert "Removed 1" in out
    assert err.strip() == ""
    assert code is None
    assert (cache / "b.json").exists(), "a non-matching entry must survive"


def test_the_force_branch_counts_what_it_removed_not_what_it_found(tmp_path, monkeypatch):
    """The second copy. It printed `Cleared {len(entries)}` before knowing."""
    cache = _cache(tmp_path, monkeypatch)
    for n in ("a.json", "b.json", "c.json"):
        _entry(cache, n, tmp_path / "x.pdf")
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (
        (_ for _ in ()).throw(OSError(13, "Permission denied"))
        if self.name == "b.json" else real_unlink(self, *a, **k)))

    out, err, code = _clear(None, force=True)

    assert "Cleared 2 of 3" in out, out
    assert "b.json" in err
    assert code == 1


def test_the_force_branch_does_not_abort_the_sweep_on_one_failure(tmp_path, monkeypatch):
    """It raised out of the loop, so entries after the failure were never
    touched and no summary printed at all."""
    cache = _cache(tmp_path, monkeypatch)
    for n in ("a.json", "b.json", "c.json"):
        _entry(cache, n, tmp_path / "x.pdf")
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (
        (_ for _ in ()).throw(OSError(13, "nope"))
        if self.name == "a.json" else real_unlink(self, *a, **k)))

    _out, _err, _code = _clear(None, force=True)

    assert not (cache / "b.json").exists(), "the sweep stopped at the first failure"
    assert not (cache / "c.json").exists()


def test_a_clean_force_clear_exits_normally(tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch)
    for n in ("a.json", "b.json"):
        _entry(cache, n, tmp_path / "x.pdf")

    out, err, code = _clear(None, force=True)

    assert "Cleared 2 of 2" in out
    assert code is None
    assert list(cache.iterdir()) == []


# ==========================================================================
# 2 - a partial sweep archived as a complete one
# ==========================================================================

def _parse(tmp_path, monkeypatch, failing=("b.pdf", "d.pdf"), names=None,
           extra_paths=()):
    names = names or ["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"]
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    for n in names:
        (docs / n).write_bytes(b"%PDF-1.4\n")

    def _fake(f, **kw):
        if f.name in failing:
            raise RuntimeError("liteparse crashed on this document")
        return {"file": str(f), "file_name": f.name,
                "pages": [{"page_num": 1, "text_items": []}]}

    monkeypatch.setattr(dp, "parse_document", _fake)
    out_json = tmp_path / "parsed.json"
    args = types.SimpleNamespace(
        files=[str(docs), *extra_paths], pages=None, dpi=100, no_cache=True,
        output_json=str(out_json), password=None)
    err = io.StringIO()
    code = None
    with contextlib.redirect_stderr(err):
        try:
            dp.cmd_parse(args)
        except SystemExit as exc:
            code = exc.code
    return json.loads(out_json.read_text(encoding="utf-8")), err.getvalue(), code


def test_the_archived_json_records_every_failure(tmp_path, monkeypatch):
    data, _err, _code = _parse(tmp_path, monkeypatch)
    assert [Path(f["file"]).name for f in data["failures"]] == ["b.pdf", "d.pdf"]
    assert all(f["error"] for f in data["failures"])


def test_the_summary_carries_the_number_asked_for(tmp_path, monkeypatch):
    """`total_files` alone reads as "this is what there was"."""
    data, _err, _code = _parse(tmp_path, monkeypatch)
    s = data["summary"]
    assert s["total_files"] == 3
    assert s["total_failed"] == 2
    assert s["total_requested"] == 5


def test_the_operator_is_told_the_sweep_was_partial(tmp_path, monkeypatch):
    _data, err, _code = _parse(tmp_path, monkeypatch)
    assert "3 of 5 files" in err
    assert "2 file(s) failed" in err
    assert "b.pdf" in err and "d.pdf" in err


def test_a_partial_sweep_does_not_exit_zero(tmp_path, monkeypatch):
    """The exit code is the one channel automation reads, and it said "fine".

    Everything the tool knew about the two failed documents went to stderr and
    into the archived JSON. A cron job, a CI step, or a parent using
    `subprocess.run(..., check=True)` reads neither: it reads the code, and a
    sweep that missed two fifths of its corpus returned 0, indistinguishable
    from a complete one.

    1, not 2, because 2 is already taken by "nothing parsed at all" below. A
    caller has to be able to tell a partial sweep from a total one.
    """
    _data, _err, code = _parse(tmp_path, monkeypatch)
    assert code == 1, f"a sweep with 2 of 5 files failed exited {code!r}"


def test_a_complete_sweep_still_exits_zero(tmp_path, monkeypatch):
    """Or the check above is just "this command always fails"."""
    data, _err, code = _parse(tmp_path, monkeypatch, failing=())
    assert data["summary"]["total_failed"] == 0
    assert code is None, f"a clean sweep exited {code!r}"


def test_a_sweep_where_nothing_parsed_keeps_its_own_code(tmp_path, monkeypatch):
    """2 is the pre-existing "no files were successfully parsed" code.

    Asked explicitly, because the partial-failure exit added above sits
    directly below it and a mistake in the ordering would collapse the two
    states onto one code.
    """
    _data, err, code = _parse(tmp_path, monkeypatch,
                              failing=("a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"))
    assert code == 2, f"a sweep where every file failed exited {code!r}"
    assert "No files were successfully parsed" in err


def test_a_complete_sweep_reports_plainly(tmp_path, monkeypatch):
    """The other half: no `of N` and no failure block when nothing failed."""
    data, err, _code = _parse(tmp_path, monkeypatch, failing=())
    assert data["summary"]["total_failed"] == 0
    assert data["failures"] == []
    assert "5 files" in err
    assert " of " not in err.split("cache hits")[0]
    assert "failed" not in err


def test_a_named_path_that_does_not_exist_is_recorded(tmp_path, monkeypatch):
    """The loudest of the three failure paths, and the one nothing recorded."""
    data, err, _code = _parse(tmp_path, monkeypatch, failing=(),
                              extra_paths=[str(tmp_path / "nowhere.pdf")])
    assert [Path(f["file"]).name for f in data["failures"]] == ["nowhere.pdf"]
    assert data["summary"]["total_requested"] == 6
    assert "not found" in err


# ==========================================================================
# 3 - the report built over a parse that was not complete
# ==========================================================================

_PAGE = {"page_num": 1, "width_pt": 612, "height_pt": 792, "text_items": []}
_CITS = [{"file": "a.pdf", "page": 1, "quote": "a quote"}]


def _report(failures):
    parse_data = {
        "files": [{"file": "/x/a.pdf", "file_name": "a.pdf", "pages": [_PAGE]}],
    }
    if failures is not None:
        parse_data["failures"] = failures
    return dp._generate_report_html("Q?", "An answer", _CITS, {}, parse_data)


def test_the_report_says_the_parse_behind_it_was_partial():
    html = _report([{"file": "/x/b.pdf", "error": "RuntimeError: crashed"},
                    {"file": "/x/d.pdf", "error": "not found"}])
    assert "did not cover every" in html
    assert "2 failed" in html
    assert "b.pdf" in html and "d.pdf" in html


def test_the_report_is_silent_when_the_parse_was_complete():
    assert "did not cover every" not in _report([])


def test_a_parse_json_written_before_this_change_does_not_break_the_report():
    """`failures` is absent from every JSON already on disk."""
    assert "did not cover every" not in _report(None)


def test_the_failure_names_are_escaped_in_the_report():
    """No slashes in the payload: `Path(...).name` keeps only the last
    segment, so a `<script>` written with a `/` in its closing tag never
    reaches the page and the test would pass on unescaped output.
    """
    payload = '<img src=x onerror=alert(1)>.pdf'
    html = _report([{"file": f"/x/{payload}", "error": "x"}])
    assert payload not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


# ==========================================================================
# 4 - a prerequisites check that never compared a version
# ==========================================================================

def _setup_check(monkeypatch, installed_version, cli="/usr/bin/liteparse"):
    monkeypatch.setattr(dp.shutil, "which", lambda name: cli)
    monkeypatch.setattr(dp.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="v22.1.0\n", stderr="", returncode=0))
    fake = types.ModuleType("liteparse")
    if installed_version is not None:
        fake.__version__ = installed_version
    monkeypatch.setitem(sys.modules, "liteparse", fake)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = dp._setup_check()
    return ok, buf.getvalue()


def test_a_drifted_package_version_is_reported(monkeypatch):
    """MEASURED live: pin 2.0.0, installed 2.9.0, verdict "All prerequisites
    met"."""
    ok, out = _setup_check(monkeypatch, "2.9.0")
    assert ok is True, "a version warning is not a missing prerequisite"
    assert "2.9.0" in out
    assert dp.LITEPARSE_VERSION in out
    assert "WARN" in out


def test_the_verdict_no_longer_claims_all_prerequisites_met(monkeypatch):
    """The sentence is a claim about the whole setup; the method checked that
    three things exist. `.claude/rules/scope-claims.md` owns this."""
    _ok, out = _setup_check(monkeypatch, "2.9.0")
    assert "All prerequisites met" not in out
    assert "was not confirmed" in out


def test_a_matching_version_gets_the_confident_verdict(monkeypatch):
    _ok, out = _setup_check(monkeypatch, dp.LITEPARSE_VERSION)
    assert "at the versions this tool expects" in out
    assert "WARN" not in out


def test_a_package_that_reports_no_version_is_not_called_confirmed(monkeypatch):
    """Unknown is not the same as matching. Failing toward over-reporting is
    what `scope-claims.md` asks for when the evidence is unavailable.

    The message must also say the version was not REPORTED, not print `None`
    where a version goes. Dropping the `installed is None` branch falls through
    to the mismatch branch and prints "liteparse None installed", which reads
    as a version string called None and sends the operator looking for a
    version problem that is really a packaging one. A mutation that did exactly
    that survived an earlier form of this test.
    """
    _ok, out = _setup_check(monkeypatch, None)
    assert "WARN" in out
    assert "was not confirmed" in out
    assert "version not reported" in out
    assert "None installed" not in out


def test_a_missing_cli_is_still_a_failure(monkeypatch):
    ok, out = _setup_check(monkeypatch, dp.LITEPARSE_VERSION, cli=None)
    assert ok is False
    assert "Some prerequisites missing" in out


# ==========================================================================
# 5 - zero, which rendered as nothing
# ==========================================================================

@pytest.mark.parametrize("value,expect", [
    (0, "0"),
    (0.0, "0.0"),
    (False, "False"),
    (None, ""),
    ("", ""),
    ("x", "x"),
    (5, "5"),
])
def test_esc_renders_a_real_value_and_only_hides_an_absent_one(value, expect):
    assert nl.esc(value) == expect


def test_a_zero_stat_reaches_the_page():
    """The shape that produced the defect: a stat the operator wrote as 0."""
    html = nl.build_market_depth({
        "bars": [10, 70],
        "stats": [{"value": 0, "label": "deals closed"}],
    })
    assert "deals closed" in html
    assert ">0<" in html, "the label rendered with an empty value beside it"


def test_esc_still_escapes():
    assert nl.esc("<script>") == "&lt;script&gt;"


# ==========================================================================
# 6 - figures that vanished with their decoration
# ==========================================================================

_MD = {"body": "Deals slowed.",
       "stats": [{"value": "$347,850", "label": "median"}],
       "market_caption": "Q3 pipeline, measured 2026-08-01",
       "caption": "Figure 4"}


def test_stats_render_without_a_bar_chart():
    html = nl.build_market_depth(_MD)
    assert "$347,850" in html
    assert "median" in html


def test_the_market_caption_renders_without_a_bar_chart():
    assert "Q3 pipeline" in nl.build_market_depth(_MD)


def test_no_empty_chart_is_drawn_when_there_are_no_bars():
    assert 'class="chart-bar' not in nl.build_market_depth(_MD)


def test_bars_still_render_when_they_are_there():
    html = nl.build_market_depth(dict(_MD, bars=[10, 70]))
    assert 'class="chart-bar' in html
    assert "$347,850" in html
    assert "Q3 pipeline" in html


def test_a_section_with_neither_bars_nor_stats_draws_no_banner():
    html = nl.build_market_depth({"body": "Just prose."})
    assert "vis-banner" not in html
    assert "Just prose" in html


def test_a_non_numeric_bar_is_still_skipped_with_a_warning(capsys):
    """Unchanged behaviour, pinned because the branch above it moved."""
    nl.build_market_depth({"bars": ["50;position:fixed", 20]})
    assert "not a number" in capsys.readouterr().err


# ==========================================================================
# 7 - regions outside a four-key list
# ==========================================================================

def test_a_region_the_allowlist_never_heard_of_is_rendered():
    html = nl.build_navigation_chart({"gcc": "Gulf note", "apac": "Asia note"})
    assert "Gulf note" in html
    assert "Asia note" in html


def test_the_editorial_order_of_the_known_regions_is_kept():
    """gcc, cis, africa is an editorial order, not an accident of dict
    insertion, so a document listing them backwards still renders them so."""
    html = nl.build_navigation_chart({"apac": "A", "cis": "C", "gcc": "G"})
    assert html.index(">G<") < html.index(">C<") < html.index(">A<")


def test_the_africa_alias_still_renders_once():
    html = nl.build_navigation_chart({"afr": "First", "africa": "Second"})
    assert "First" in html
    assert "Second" not in html


def test_an_unknown_region_uses_its_key_as_the_code():
    assert ">APAC<" in nl.build_navigation_chart({"apac": "Asia note"})


# ==========================================================================
# 8 - an image that was not there, and an argument that was not understood
# ==========================================================================

def test_a_missing_image_is_named(tmp_path, capsys):
    assert nl.embed_image(tmp_path / "nope.png") == ""
    assert "image not found" in capsys.readouterr().err


def test_a_present_image_is_embedded_quietly(tmp_path, capsys):
    img = tmp_path / "logo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    uri = nl.embed_image(img)
    assert uri.startswith("data:image/png;base64,")
    assert capsys.readouterr().err == ""


def _run_main(monkeypatch, tmp_path, images):
    """Drive main() far enough to see the argument parsing, and no further.

    Generation itself reads the workspace stylesheet through the DATA overlay
    and raises FileNotFoundError without it, so it cannot run on CI. The
    sentinel marks the point past the argument check.
    """
    doc = tmp_path / "in.json"
    doc.write_text(json.dumps({"date": "2026-08-01", "issue_number": 7}),
                   encoding="utf-8")

    class _Reached(Exception):
        pass

    def _sentinel(*a, **k):
        raise _Reached

    monkeypatch.setattr(nl, "generate_newsletter", _sentinel)
    monkeypatch.setattr(sys, "argv",
                        ["gen", str(doc), "--output-dir", str(tmp_path / "out"),
                         "--no-pdf", "--images", *images])
    try:
        nl.main()
    except _Reached:
        return "reached-generation"
    except SystemExit as e:
        return e.code


def test_an_images_mapping_without_an_equals_sign_stops_the_run(monkeypatch, tmp_path, capsys):
    """`--images sea_state /path/img.png` is a typo, and it used to produce an
    issue with a hole in it and no line saying the argument was not read."""
    code = _run_main(monkeypatch, tmp_path,
                     ["sea_state", str(tmp_path / "img.png")])
    assert code == 2
    err = capsys.readouterr().err
    assert "section=path" in err
    assert "sea_state" in err


def test_a_well_formed_images_mapping_is_accepted(monkeypatch, tmp_path):
    """The other half. Refusing everything would satisfy the test above."""
    assert _run_main(monkeypatch, tmp_path,
                     [f"sea_state={tmp_path / 'img.png'}"]) == "reached-generation"


# ==========================================================================
# 9 - a document field that went into the page unescaped
# ==========================================================================

_XSS = '<script>alert(1)</script>'


def test_the_issue_number_is_escaped_in_the_masthead():
    html = nl.build_masthead("", "01 Sep 2026", _XSS, ["GCC"], "HIGH")
    assert _XSS not in html
    assert "&lt;script&gt;" in html


def test_the_issue_number_is_escaped_in_the_footer():
    """Two sites, and a fix in one of two is this repository's usual defect."""
    html = nl.build_footer("", _XSS, "01 Sep 2026")
    assert _XSS not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("build,expect", [
    (lambda n: nl.build_masthead("", "d", n, ["GCC"], "HIGH"), "Issue #007"),
    (lambda n: nl.build_footer("", n, "d"), "007"),
])
def test_an_integer_issue_number_is_still_zero_padded(build, expect):
    assert expect in build(7)
