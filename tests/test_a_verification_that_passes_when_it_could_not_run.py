#!/usr/bin/env python3
"""Shard `scripts-05-p2`: publishing, splitting, parsing — and what they claimed.

The worst of these is `publish-marketplace.py`. Its push verification ran
`git rev-list` with `check=False` and compared stdout against `("0\\t0", "")` —
so when the command ERRORED (bad ref, no upstream, damaged clone) its empty
stdout matched, and the script printed "Pushed to ... (verified in sync)". On
the one path whose entire purpose is reproducible external distribution, a
verification that passes when it could not run is worse than no verification:
it is a claim nothing established. The same block compared against
`origin/main` while the push is `HEAD`, so on any other branch a fully
successful push reported failure.

`split-skills-catalog.py` is a one-shot tool that cannot be safely re-run, and
its divider regex matched only the OPENING `<h3 class="cat" ...>` tag. `m.end()`
therefore left the category name and an orphan `</h3>` at the top of every
generated page — invalid HTML, from a splitter whose docstring promises the
cards are preserved verbatim and whose inline comment says the divider line is
dropped. Its guard counted dividers without checking their ids, so a renamed
category crashed on an uncaught KeyError half-way through; its id-move
substitution needed byte-exact adjacency and skipped cards silently when the
source drifted, leaving broken same-page anchors on the rebuilt index; and its
"further down this page" fix-up was an exact-literal replace that no-oped
without a word.

`docparse.py` passed `cli_path=` to `LiteParse` — a keyword its own comment,
sixty lines up, says liteparse 2.0 removed — but only in `report`, so `parse`
worked and `report` raised TypeError before taking a screenshot. Its
corrupt-cache handler caught neither of the two exceptions a corrupt
`_cached_at` actually raises. `clear-cache --file` matched by BASENAME and
deleted other directories' entries. `--password` put a secret in the process
table. A PNG fallback shipped under a `data:image/jpeg` label. A failed PDF
conversion was fully silent. `norm_concat` was computed and never used, next to
an inline re-implementation of the very function that produced it.

One audit finding is REFUTED: the installer does not pin `liteparse==1.2.1`.
`LITEPARSE_VERSION = "2.0.0"` is a single constant used by the installer, the
remediation text and the docstring, and the comment above it records that the
three copies were unified on 2026-08-23 — before this shard's audit output was
written. `tests/test_docparse_liteparse_pin.py` already holds it.

Also here: `extract-router-rows.py` split frontmatter on the SUBSTRING `---`
and silently overwrote duplicate rows; `wizard-simulate.py` documented a
workaround its own guard refuses, and crashed on a missing `--answers` file;
`draft-critique.py` let `json.loads` raise past both its handlers.

Fixed 2026-08-24.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv" / "bin" / "python")


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _code_only(src: str) -> str:
    """Source with `#` comments stripped; the fixes quote what they removed."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "  # " in line:
            line = line.split("  # ", 1)[0]
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# publish-marketplace.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pm():
    return _load("publish_mkt_mod", "scripts/dev/publish-marketplace.py")


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _wire(pm, monkeypatch, rev_list: _Proc, status="M file"):
    """Drive commit_and_push with every git call faked."""
    seen = []

    def fake_run(cmd, cwd=None, check=True, **kw):
        seen.append(cmd)
        if cmd[:2] == ["git", "config"]:
            return _Proc(0, "someone\n")
        if cmd[:2] == ["git", "status"]:
            return _Proc(0, status)
        if cmd[:2] == ["git", "rev-list"]:
            return rev_list
        return _Proc(0, "")

    monkeypatch.setattr(pm, "_run", fake_run)
    return seen


def test_a_verification_that_could_not_run_is_a_failure(pm, tmp_path, monkeypatch,
                                                        capsys):
    _wire(pm, monkeypatch, _Proc(returncode=128, stdout="", stderr="bad revision"))
    rc = pm.commit_and_push(tmp_path, tmp_path, "msg", push=True)
    out = capsys.readouterr().out
    assert rc == 1, (
        "`rev-list` errored, its empty stdout matched the accepted `\"\"`, and "
        "the script printed 'verified in sync' on a distribution path"
    )
    assert "could not run" in out
    assert "verified in sync" not in out


def test_a_clean_verification_still_passes(pm, tmp_path, monkeypatch, capsys):
    """Anchor: reading the return code must not break the working case."""
    _wire(pm, monkeypatch, _Proc(returncode=0, stdout="0\t0\n"))
    rc = pm.commit_and_push(tmp_path, tmp_path, "msg", push=True)
    assert rc == 0
    assert "verified in sync" in capsys.readouterr().out


def test_a_genuine_divergence_still_fails(pm, tmp_path, monkeypatch, capsys):
    _wire(pm, monkeypatch, _Proc(returncode=0, stdout="0\t3\n"))
    assert pm.commit_and_push(tmp_path, tmp_path, "msg", push=True) == 1
    assert "verification failed" in capsys.readouterr().out


def test_an_empty_stdout_with_a_zero_exit_is_not_in_sync(pm, tmp_path,
                                                         monkeypatch, capsys):
    """`""` was an accepted value. It never meant anything good."""
    _wire(pm, monkeypatch, _Proc(returncode=0, stdout=""))
    assert pm.commit_and_push(tmp_path, tmp_path, "msg", push=True) == 1


def test_the_verification_follows_the_pushed_branch(pm, tmp_path, monkeypatch):
    seen = _wire(pm, monkeypatch, _Proc(returncode=0, stdout="0\t0\n"))
    pm.commit_and_push(tmp_path, tmp_path, "msg", push=True)
    rev = next(c for c in seen if c[:2] == ["git", "rev-list"])
    assert "@{upstream}...HEAD" in rev, (
        f"{rev}: the push is `HEAD`, so comparing against a hardcoded "
        "origin/main reported failure for a successful push on any other branch"
    )
    assert "origin/main...HEAD" not in rev


def test_the_identity_mirror_checks_both_keys(pm, tmp_path, monkeypatch):
    """git needs name AND email; the early return asked only about email."""
    repo_cfg = {"user.email": "someone@example.com"}
    engine_cfg = {"user.name": "Someone", "user.email": "someone@example.com"}
    written = []

    def fake_run(cmd, cwd=None, check=True, **kw):
        if cmd[:2] == ["git", "config"] and len(cmd) == 3:
            src = repo_cfg if cwd == tmp_path / "repo" else engine_cfg
            return _Proc(0, src.get(cmd[2], "") + "\n" if src.get(cmd[2]) else "")
        if cmd[:2] == ["git", "config"] and len(cmd) == 4:
            written.append(cmd[2])
            return _Proc(0, "")
        return _Proc(0, "")

    monkeypatch.setattr(pm, "_run", fake_run)
    pm.ensure_identity(tmp_path / "repo", tmp_path / "engine")
    assert "user.name" in written, (
        "the repo had an email and no name, so the function returned early and "
        "the commit below died on a raw CalledProcessError"
    )


# ---------------------------------------------------------------------------
# split-skills-catalog.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def splitter():
    return _load("split_catalog_mod", "scripts/dev/split-skills-catalog.py")


def test_the_divider_regex_consumes_the_whole_element(splitter):
    html = '<h3 class="cat" id="cat-intel">Intel</h3>\n<section class="skill">'
    m = splitter.CAT_DIVIDER_RE.search(html)
    assert m is not None
    rest = html[m.end():]
    assert not rest.startswith("Intel"), (
        "the regex matched only the opening tag, so every category page began "
        "with the category name and an orphan </h3>"
    )
    assert "</h3>" not in rest
    assert rest.startswith("<section")


def test_the_divider_regex_still_captures_the_id(splitter):
    m = splitter.CAT_DIVIDER_RE.search('<h3 class="cat" id="cat-intel">Intel</h3>\n')
    assert m.group(1) == "cat-intel"


def test_the_divider_regex_does_not_swallow_the_next_divider(splitter):
    """`.*?` with DOTALL is lazy, but say so out loud: two dividers must stay two."""
    html = ('<h3 class="cat" id="cat-a">A</h3>\nbody a\n'
            '<h3 class="cat" id="cat-b">B</h3>\nbody b\n')
    ids = [m.group(1) for m in splitter.CAT_DIVIDER_RE.finditer(html)]
    assert ids == ["cat-a", "cat-b"]


def test_divider_identity_is_checked_not_just_the_count():
    src = _code_only(
        (ROOT / "scripts" / "dev" / "split-skills-catalog.py").read_text(encoding="utf-8"))
    assert "found_ids != expected_ids" in src, (
        "eight dividers with one id renamed passed the count guard and then "
        "died on an uncaught KeyError, half-way through a one-shot tool"
    )
    assert "unexpected:" in src and "missing:" in src, "abort without a diff is not an abort"


def test_a_skipped_id_move_aborts_instead_of_shipping_broken_anchors():
    """`moved` must come FROM the substitution, and be compared to the total.

    Checked through the AST, because the string check this replaced could not
    tell the fix from a revert: prefixing `moved = section_total` before the
    `subn` call leaves every asserted substring in place while restoring the
    silent skip exactly. `sub` discards the count, and a card whose formatting
    drifted was then skipped without a word, leaving a dead same-page anchor on
    the rebuilt index — which the docstring promises still resolves.
    """
    import ast
    tree = ast.parse(
        (ROOT / "scripts" / "dev" / "split-skills-catalog.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and "moved" in ast.dump(n))
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "moved"
                       or (isinstance(t, ast.Tuple)
                           and any(getattr(e, "id", None) == "moved" for e in t.elts))
                       for t in n.targets)]
    assert len(assigns) == 1, (
        f"`moved` is assigned {len(assigns)} times; a second assignment can "
        "overwrite the substitution count and restore the silent skip"
    )
    value = assigns[0].value
    assert isinstance(value, ast.Call), "`moved` is not taken from a call"
    assert getattr(value.func, "attr", None) == "subn", (
        "`moved` must come from SECTION_ID_RE.subn; `sub` discards the count"
    )
    src = (ROOT / "scripts" / "dev" / "split-skills-catalog.py").read_text(encoding="utf-8")
    assert "if moved != section_total:" in src
    assert "did not match" in src


def test_the_intro_fixup_reports_a_no_op():
    src = _code_only(
        (ROOT / "scripts" / "dev" / "split-skills-catalog.py").read_text(encoding="utf-8"))
    assert "_stale not in intro" in src, (
        "an exact-literal replace that quietly does nothing leaves readers "
        "pointed at a section this split removes from the page"
    )


# ---------------------------------------------------------------------------
# docparse.py
# ---------------------------------------------------------------------------

DOCPARSE_SRC = (ROOT / "scripts" / "docparse.py").read_text(encoding="utf-8")
DOCPARSE_CODE = _code_only(DOCPARSE_SRC)


@pytest.fixture(scope="module")
def dp():
    return _load("docparse_mod", "scripts/docparse.py")


def test_the_report_parser_does_not_pass_a_removed_keyword():
    assert "LiteParse(cli_path=" not in DOCPARSE_CODE, (
        "the note at the top of parse_document says liteparse 2.0 removed "
        "cli_path; `report` passed it whenever the CLI was on PATH, which is "
        "the documented setup, so report raised TypeError while parse worked"
    )


def test_the_liteparse_pin_is_two_point_zero():
    """REFUTED finding: the audit said the installer pins 1.2.1. It does not."""
    assert 'LITEPARSE_VERSION = "2.0.0"' in DOCPARSE_CODE
    assert "liteparse==1.2.1" not in DOCPARSE_SRC
    assert 'f"liteparse=={LITEPARSE_VERSION}"' in DOCPARSE_CODE, (
        "the installer must derive its pin from the one constant"
    )


@pytest.mark.parametrize("cached_at", ["not-a-date", 123, None, 4.5, [], "2026-13-45"])
def test_a_corrupt_cache_timestamp_regenerates(dp, tmp_path, monkeypatch, cached_at,
                                               capsys):
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path)
    (tmp_path / "k.json").write_text(
        json.dumps({"_cached_at": cached_at, "pages": []}), encoding="utf-8")
    assert dp._cache_get("k") is None, (
        "the handler exists so a corrupt entry regenerates; fromisoformat "
        "raises ValueError on a bad string and TypeError on a non-string, and "
        "neither was in the except tuple"
    )
    assert "corrupt" in capsys.readouterr().err
    assert not (tmp_path / "k.json").exists(), "the bad entry was not cleared"


def test_a_good_cache_entry_is_still_returned(dp, tmp_path, monkeypatch):
    """Anchor: widening the except must not discard working cache entries."""
    from datetime import datetime, timezone
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path)
    fresh = datetime.now(timezone.utc).isoformat()
    (tmp_path / "k.json").write_text(
        json.dumps({"_cached_at": fresh, "pages": [1]}), encoding="utf-8")
    got = dp._cache_get("k")
    assert got is not None and got["pages"] == [1]


def test_clear_cache_matches_the_exact_path_only():
    assert 'Path(data.get("file", "")).name == fp.name' not in DOCPARSE_CODE, (
        "the basename fallback deleted the cache of every document sharing a "
        "filename across directories"
    )
    assert 'if data.get("file") == str(fp):' in DOCPARSE_CODE


def test_the_password_prefers_the_environment(dp, monkeypatch, capsys):
    import argparse
    monkeypatch.setenv("DOCPARSE_PASSWORD", "from-env")
    args = argparse.Namespace(password="from-argv")
    assert dp._password(args) == "from-env"
    assert "using the environment" in capsys.readouterr().err


def test_the_password_flag_still_works_and_warns(dp, monkeypatch, capsys):
    import argparse
    monkeypatch.delenv("DOCPARSE_PASSWORD", raising=False)
    fixture = "hunter2"  # pragma: allowlist secret - a joke literal, not a credential
    assert dp._password(argparse.Namespace(password=fixture)) == fixture
    err = capsys.readouterr().err
    assert "ps" in err and "shell history" in err, (
        "an argv secret is readable by any local account; the flag has to say so"
    )


def test_no_password_is_no_warning(dp, monkeypatch, capsys):
    import argparse
    monkeypatch.delenv("DOCPARSE_PASSWORD", raising=False)
    assert dp._password(argparse.Namespace(password=None)) is None
    assert capsys.readouterr().err == ""


def test_png_bytes_are_labelled_png(dp):
    png = b"\x89PNG\r\n\x1a\n" + b"rest"
    assert dp._image_mime(png) == "image/png", (
        "_png_to_jpeg returns the ORIGINAL PNG when Pillow is missing, and the "
        "report embedded it under data:image/jpeg — a false label a strict "
        "PDF pipeline can drop"
    )


def test_jpeg_bytes_are_labelled_jpeg(dp):
    assert dp._image_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"


def test_the_report_uses_the_measured_mime():
    assert 'f"data:{_image_mime(img_bytes)};base64,{img_b64}"' in DOCPARSE_CODE
    assert '"data:image/jpeg;base64,{img_b64}"' not in DOCPARSE_CODE


def test_a_failed_pdf_conversion_is_reported():
    assert "PDF conversion produced no file" in DOCPARSE_CODE, (
        "the return code was never read and there was no else, so a converter "
        "that exited non-zero left no PDF and no message"
    )


def test_the_citation_id_and_page_number_are_escaped():
    assert 'id="cite-{html.escape(str(cit_id), quote=True)}"' in DOCPARSE_CODE, (
        "every other citation field goes through html.escape; cit_id went raw "
        "into an id attribute"
    )
    assert "Page {page_num}" not in DOCPARSE_CODE
    assert 'alt="Page {html.escape(str(page_num))}"' in DOCPARSE_CODE


def test_the_dead_normalisation_variable_became_a_drift_check():
    """The comparison moved off the lowered text, and had to.

    This asserted `if concat != norm_concat:`, where `concat` was the LOWERED
    join. That worked only while lowering preserved length. It does not:
    `"İ".lower()` is two code points, so the lowered join legitimately differs
    from `_normalize_text`'s output and the guard fired on correct input. The
    check now compares the pre-lowering sequences, which is what
    `_normalize_text` actually produces. The invariant is unchanged and the
    reference value is still read.
    """
    assert 'if "".join(norm_chars) != norm_concat:' in DOCPARSE_CODE, (
        "the loop re-implements _normalize_text inline to carry the "
        "char-to-item map; with the reference value unused, nothing compared "
        "them and an edit to _normalize_text would desync the matcher silently"
    )
    assert "norm_concat = _normalize_text(raw_concat)" in DOCPARSE_CODE, (
        "the reference must come from _normalize_text itself, or the guard "
        "compares the inline copy against another inline copy"
    )


# ---------------------------------------------------------------------------
# extract-router-rows.py
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def extractor():
    return _load("extract_rows_mod", "scripts/dev/extract-router-rows.py")


ROUTING = {"category": "Intel", "triggers": ["x"], "exclusions": ["N/A"],
           "compound": "No", "router": "auto"}


def test_a_dash_inside_a_frontmatter_value_does_not_split_it(extractor, tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        '---\nname: demo\ndescription: "alpha --- beta"\n---\n\nBody text.\n',
        encoding="utf-8")
    extractor.apply_block(skill, ROUTING)
    text = skill.read_text(encoding="utf-8")
    assert 'description: "alpha --- beta"' in text, (
        "`text.split('---', 2)` cut inside the value, so the block landed in "
        "the middle of the YAML and the closing fence inside a string"
    )
    assert text.endswith("Body text.\n")
    assert "x-heading-routing:" in text.split("\n---\n")[0]


def test_a_file_with_no_frontmatter_is_refused(extractor, tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Title\n\npara one\n\n---\n\npara two\n\n---\n\npara three\n",
                     encoding="utf-8")
    before = skill.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter fence"):
        extractor.apply_block(skill, ROUTING)
    assert skill.read_text(encoding="utf-8") == before, (
        "two horizontal rules passed the `len(parts) < 3` check and the block "
        "was spliced into the prose"
    )


def test_unclosed_frontmatter_is_refused(extractor, tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n\nno closing fence\n", encoding="utf-8")
    with pytest.raises(ValueError, match="never closed"):
        extractor.apply_block(skill, ROUTING)


def test_an_ordinary_skill_still_gets_its_block(extractor, tmp_path):
    """Anchor: the stricter parse must not refuse the normal shape."""
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: demo\n---\n\nBody.\n", encoding="utf-8")
    extractor.apply_block(skill, ROUTING)
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\nname: demo\n")
    assert "x-heading-routing:" in text
    assert text.endswith("\nBody.\n")


def test_a_duplicate_router_row_is_warned_about():
    src = _code_only(
        (ROOT / "scripts" / "dev" / "extract-router-rows.py").read_text(encoding="utf-8"))
    assert "duplicate router row" in (ROOT / "scripts" / "dev" / "extract-router-rows.py").read_text(encoding="utf-8"), (
        "every other anomaly in this parser warns; a duplicate silently "
        "replaced the first row and lost one category's triggers"
    )
    assert "if name in rows:" in src


# ---------------------------------------------------------------------------
# wizard-simulate.py
# ---------------------------------------------------------------------------

def test_the_ceo_master_comment_does_not_promise_a_workaround_that_fails():
    src = (ROOT / "scripts" / "dev" / "wizard-simulate.py").read_text(encoding="utf-8")
    assert "copy the identity file into a fixture tmpdir" not in src, (
        "the guard reads the identity from whatever --workspace points at, so "
        "a tmpdir holding a copied ceo-master identity is refused identically"
    )
    assert "EDIT the `type` field" in src


def test_a_missing_answers_file_is_a_clean_error(tmp_path):
    proc = subprocess.run(
        [PY, str(ROOT / "scripts" / "dev" / "wizard-simulate.py"),
         "--answers", str(tmp_path / "nope.yaml"), "--workspace", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, (
        f"expected the clean ERROR path this file uses everywhere else; "
        f"got {proc.returncode}\n{proc.stderr[-500:]}"
    )
    assert "answers file not found" in proc.stderr
    assert "Traceback" not in proc.stderr


# ---------------------------------------------------------------------------
# draft-critique.py
# ---------------------------------------------------------------------------

def test_a_non_json_daemon_response_is_a_documented_exit():
    src = _code_only((ROOT / "scripts" / "draft-critique.py").read_text(encoding="utf-8"))
    assert "except ValueError as e:" in src, (
        "json.loads sits inside the `with`, and ValueError matches neither "
        "HTTPError nor URLError, so a 200 with an HTML error page died on a "
        "traceback instead of one of the documented exit codes"
    )
    assert "not JSON" in src
