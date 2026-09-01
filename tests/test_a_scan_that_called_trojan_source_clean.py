"""Shard scripts-utils-02-p2: six tools that reported a state they had not read.

* ``sanitize_text.scan`` iterated CHAR_NAMES while ``sanitize`` acts on
  INVISIBLE_CHARS. Both tables are hand-maintained and they drifted: the four
  Trojan Source isolates U+2066-U+2069 were in one and not the other, so the
  scan printed "Clean - no hidden characters found." over precisely the
  characters designed to make a reviewer read a line differently from the
  parser. `.claude/rules/hidden-chars.md` makes that sentence the validation
  line on every deliverable.

* ``sandbox.run_sandboxed`` refused an ``out_dir`` INSIDE a corpus path and not
  one that CONTAINS it. The writable ``/out`` mount then held the read-only
  corpus as a subtree, and a traversal wrote straight through it onto the host.

* ``search._get_json`` decoded every body as UTF-8 while ``brave_search`` asks
  for gzip, so a compressing endpoint raised UnicodeDecodeError - not a
  ``SearchBackendError``, so ``search_with_fallback`` could not fall back.

* ``scrutinize_record._judged_count`` returned 0 for an ABSENT ``Findings:``
  line, and ``len(verdict_rows) < 0`` is false for every row count, so a report
  claiming a complete refutation pass over zero verdict rows validated clean.

* ``_install_systemd_user_timer`` printed its green "enabled" line and returned
  True one line under its own warning that ``is-active`` reported inactive.

* ``schedule._run`` promised "never raises" and raised FileNotFoundError on a
  command that is not on PATH, which is every ``schtasks`` call off Windows.

Run: python3 -m pytest tests/test_a_scan_that_called_trojan_source_clean.py
"""
from __future__ import annotations

import ast
import builtins
import email.message
import gzip
import io
import json
import subprocess
import sys
import threading
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import sanitize_text as st  # noqa: E402
from scripts.utils import schedule as sched  # noqa: E402
from scripts.utils import scrutinize_record as sr  # noqa: E402
from scripts.utils import search as search_mod  # noqa: E402
from scripts.utils.sandbox import run_sandboxed  # noqa: E402

# The Trojan Source family, by codepoint. Never typed literally in this file:
# the workspace bans invisible characters in generated text, and an editor that
# normalises them would quietly disarm the test.
LRI, RLI, FSI, PDI = (chr(cp) for cp in (0x2066, 0x2067, 0x2068, 0x2069))


# ============================================================
# The scan that called Trojan Source clean
# ============================================================

@pytest.mark.parametrize("char,label", [
    (LRI, "U+2066"), (RLI, "U+2067"), (FSI, "U+2068"), (PDI, "U+2069"),
])
def test_each_isolate_is_reported_not_called_clean(char, label):
    buf = io.StringIO()
    count = st.scan(f"hello {char}world", "sample.md", out=buf)
    report = buf.getvalue()

    assert count == 1, f"{label} was scanned as clean"
    assert "Clean" not in report
    assert label in report


def test_the_scan_covers_everything_the_sanitizer_strips():
    """The invariant, not the four characters: one table drove both answers."""
    for char in st.INVISIBLE_CHARS:
        buf = io.StringIO()
        assert st.scan(f"a{char}b", "x.md", out=buf) == 1, (
            f"sanitize() strips U+{ord(char):04X} and scan() reports it clean")


def test_the_scanned_set_is_derived_from_the_sanitizer():
    assert frozenset(st.INVISIBLE_CHARS) <= st.SCANNED_CHARS
    assert frozenset(st.REPLACE_MAP) <= st.SCANNED_CHARS


def test_an_unnamed_character_is_still_reported(monkeypatch):
    """A future addition must not need a name to be caught."""
    monkeypatch.setitem(st.CHAR_NAMES, LRI, None)
    monkeypatch.delitem(st.CHAR_NAMES, LRI)
    buf = io.StringIO()

    assert st.scan(f"a{LRI}b", "x.md", out=buf) == 1
    assert unicodedata.name(LRI).title() in buf.getvalue()


def test_every_scanned_character_has_a_name_today():
    assert st.SCANNED_CHARS - set(st.CHAR_NAMES) == set()


def test_the_scanned_set_is_built_from_the_sanitizer_tables_in_source():
    """The derivation, asked of the AST, because today the two sets are equal.

    `CHAR_NAMES` names every scanned character right now, so
    `SCANNED_CHARS = frozenset(CHAR_NAMES)` -- the hand-maintained shape whose
    drift produced the Trojan Source hole -- passes every behavioural test in
    this section. Measured: that substitution left all 37 cases green. What the
    fix actually bought is that a character added to `INVISIBLE_CHARS` is
    scanned WITHOUT anyone remembering to name it, and only the expression can
    say whether that still holds.
    """
    module = ast.parse((ROOT / "scripts" / "utils" / "sanitize_text.py")
                       .read_text(encoding="utf-8"))
    assigns = [node for node in module.body
               if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "SCANNED_CHARS"
                       for t in node.targets)]
    assert len(assigns) == 1, f"expected one SCANNED_CHARS assignment, got {len(assigns)}"

    names = {n.id for n in ast.walk(assigns[0].value) if isinstance(n, ast.Name)}
    sources = names - set(dir(builtins))   # `frozenset` is a Name node too
    assert sources == {"INVISIBLE_CHARS", "REPLACE_MAP"}, (
        f"SCANNED_CHARS is built from {sorted(sources)}; it must be derived from "
        "the two tables `sanitize()` acts on, never from the name table")


def test_the_cli_exit_code_follows_the_finding(tmp_path):
    """The hook and the rule both read the count, not the prose."""
    target = tmp_path / "doc.md"
    target.write_text(f"a{PDI}b\n", encoding="utf-8")
    count, report = st.scan_file(target)

    assert count == 1
    assert "U+2069" in report


def test_a_genuinely_clean_file_still_reads_clean(tmp_path):
    target = tmp_path / "ok.md"
    target.write_text("plain ascii\n", encoding="utf-8")
    count, report = st.scan_file(target)

    assert count == 0
    assert "Clean" in report


# ============================================================
# The writable mount that reached into the read-only corpus
# ============================================================

@pytest.fixture
def box(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.md").write_text("original", encoding="utf-8")
    program = tmp_path / "trav.py"
    program.write_text(
        "from pathlib import Path\n"
        "Path('/out/corpus/note.md').write_text('overwritten')\n"
        "Path('/out/corpus/planted.md').write_text('planted')\n",
        encoding="utf-8")
    return tmp_path, corpus, program


def test_an_out_dir_that_contains_the_corpus_is_refused(box):
    """This wrote through onto the host: `/out` held the corpus as a subtree."""
    parent, corpus, program = box
    result = run_sandboxed(program=program, corpus_paths=[corpus], out_dir=parent)

    assert result.refused is not None
    assert "contains the corpus path" in result.refused
    assert (corpus / "note.md").read_text(encoding="utf-8") == "original"
    assert not (corpus / "planted.md").exists()


def test_the_refusal_happens_before_any_process_starts(box):
    parent, corpus, program = box
    result = run_sandboxed(program=program, corpus_paths=[corpus], out_dir=parent)

    assert result.exit_code is None, "a process ran before the refusal"


def test_an_out_dir_inside_the_corpus_is_still_refused(box):
    """The direction that was already covered must not regress."""
    _parent, corpus, program = box
    inside = corpus / "out"
    result = run_sandboxed(program=program, corpus_paths=[corpus], out_dir=inside)

    assert result.refused is not None
    assert "lies inside the corpus path" in result.refused


def test_a_non_normalised_out_dir_is_resolved_before_the_test(box):
    """`<corpus>/..` IS the containing directory; only resolve() can see that.

    Comparing the caller's path as given would answer "not nested" and let the
    writable mount cover the corpus anyway.
    """
    _parent, corpus, program = box
    sneaky = corpus / ".."
    result = run_sandboxed(program=program, corpus_paths=[corpus], out_dir=sneaky)

    assert result.refused is not None
    assert "contains the corpus path" in result.refused


def test_an_out_dir_beside_the_corpus_is_not_refused_for_nesting(box):
    """The guard must refuse nesting, not every out_dir."""
    parent, corpus, program = box
    beside = parent / "out"
    result = run_sandboxed(program=program, corpus_paths=[corpus], out_dir=beside)

    if result.refused is not None:
        assert "corpus path" not in result.refused, (
            f"a sibling out_dir was refused as nested: {result.refused}")


# ============================================================
# The compressed reply nobody could read
# ============================================================

def _response(body: bytes, encoding: str | None):
    headers = email.message.Message()
    if encoding:
        headers["Content-Encoding"] = encoding

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    resp = _Resp()
    resp.headers = headers
    return resp


def _patched(monkeypatch, resp):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: resp)


PAYLOAD = {"web": {"results": [{"title": "t", "url": "u", "description": "d"}]}}


def test_a_gzip_reply_is_read_rather_than_raising(monkeypatch):
    """brave_search asks for gzip; the reader decoded UTF-8 regardless."""
    body = gzip.compress(json.dumps(PAYLOAD).encode())
    _patched(monkeypatch, _response(body, "gzip"))

    assert search_mod._get_json("http://x/y", {})["web"]["results"][0]["title"] == "t"


def test_an_uncompressed_reply_still_reads(monkeypatch):
    _patched(monkeypatch, _response(json.dumps(PAYLOAD).encode(), None))
    assert search_mod._get_json("http://x/y", {}) == PAYLOAD


def test_brave_search_asks_for_the_encoding_the_reader_handles():
    """The two used to disagree; that disagreement WAS the defect."""
    source = (ROOT / "scripts" / "utils" / "search.py").read_text(encoding="utf-8")
    assert '"Accept-Encoding": "gzip"' in source
    assert 'gzip.decompress' in source


@pytest.mark.parametrize("body,encoding", [
    (b"not json at all", None),
    (b"\x1f\x8b broken gzip", "gzip"),
    (json.dumps(PAYLOAD).encode(), "br"),
    # The one case that reaches `body.decode("utf-8")` itself. The three above
    # raise JSONDecodeError, gzip.BadGzipFile (an OSError) and
    # SearchBackendError, so dropping `UnicodeDecodeError` from the handler's
    # tuple left every one of them green while a latin-1 reply from an
    # uncompressing endpoint escaped `search_with_fallback` as a bare
    # UnicodeDecodeError -- the very shape the gzip half of this section was
    # written for.
    (b"caf\xe9 not utf-8", None),
    # And the same byte through the gzip branch: decompression succeeds and the
    # decode after it is what fails.
    #
    # `mtime=0` is NOT cosmetic. This call sits at MODULE scope, inside the
    # decorator, so it runs once per process at import. `gzip.compress` writes
    # the current epoch second into the header, at byte offset 4, and pytest
    # derives a parametrize id from the bytes. Under `-n auto` each xdist worker
    # imports this module a moment apart, so the workers built DIFFERENT ids for
    # this one case and xdist aborted the entire run with "Different tests were
    # collected between gw0 and gwN".
    #
    # MEASURED 2026-09-01: `gzip.compress(x) != gzip.compress(x)` across 1.1s,
    # first differing byte at offset 4; with `mtime=0` the two are identical.
    # The whole parallel gate went down on every run, and the error names a
    # collection mismatch, which reads as a nondeterministic conftest rather
    # than as one timestamp in one fixture.
    #
    # The two other `gzip.compress` calls in this tree are FINE and were left
    # alone: `test_resolve_entity.py:212` and line 257 above both run inside a
    # function body, at test time, where the bytes never reach an id.
    (gzip.compress(b"caf\xe9 not utf-8", mtime=0), "gzip"),
])
def test_an_unreadable_body_is_a_backend_error(monkeypatch, body, encoding):
    """Anything else escapes `search_with_fallback` and kills the whole call."""
    _patched(monkeypatch, _response(body, encoding))
    with pytest.raises(search_mod.SearchBackendError):
        search_mod._get_json("http://x/y", {})


def test_an_http_error_is_not_shadowed_by_the_body_clause(monkeypatch):
    """HTTPError is an OSError; clause order is load-bearing."""
    def _raise(*a, **k):
        raise urllib.error.HTTPError("http://x/y", 404, "gone", {},
                                     io.BytesIO(b"missing"))

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(search_mod.SearchBackendError) as exc:
        search_mod._get_json("http://x/y", {})
    assert "HTTP 404" in str(exc.value)


def test_a_retryable_status_still_retries(monkeypatch):
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("http://x/y", 503, "busy", {},
                                         io.BytesIO(b"busy"))
        return _response(json.dumps(PAYLOAD).encode(), None)

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    monkeypatch.setattr(search_mod.time, "sleep", lambda *_a: None)

    assert search_mod._get_json("http://x/y", {}) == PAYLOAD
    assert calls["n"] == 2


# ============================================================
# The refutation that reconciled against nothing
# ============================================================

@pytest.fixture
def run_rows(monkeypatch, tmp_path):
    rows = [{"run_id": "r1", "kind": "pass_start"}]
    monkeypatch.setattr(sr, "iter_rows", lambda: list(rows))
    return rows


REPORT_HEAD = "Refutation: pass complete, all findings judged\n"


def test_a_report_without_a_findings_line_is_a_defect(run_rows, tmp_path):
    """It validated clean: absent counted as zero, and zero can never fail."""
    report = tmp_path / "r.md"
    report.write_text(REPORT_HEAD + "Summary: 3 BLOCKER, 5 HIGH, 2 LOW\n",
                      encoding="utf-8")

    defects = sr.validate(run_id="r1", report_path=report)

    assert defects, "a refutation claim over zero verdict rows validated clean"
    assert "no 'Findings:' line" in defects[0]


def test_a_findings_line_with_zero_counts_is_not_a_defect(run_rows, tmp_path):
    """Absent is not zero, and a real zero must still pass."""
    report = tmp_path / "r.md"
    report.write_text(REPORT_HEAD + "Findings: 0 BLOCKER, 0 HIGH, 0 MEDIUM\n",
                      encoding="utf-8")

    assert sr.validate(run_id="r1", report_path=report) == []


def test_a_short_verdict_count_is_still_caught(run_rows, tmp_path):
    report = tmp_path / "r.md"
    report.write_text(REPORT_HEAD + "Findings: 3 BLOCKER, 5 HIGH, 2 MEDIUM\n",
                      encoding="utf-8")

    defects = sr.validate(run_id="r1", report_path=report)

    assert any("10 judged finding(s)" in d for d in defects)


def test_the_absent_line_reads_as_absent_not_zero(tmp_path):
    assert sr._judged_count("Summary: 3 BLOCKER\n") is None
    assert sr._judged_count("Findings: 0 BLOCKER\n") == 0
    assert sr._judged_count("Findings: 3 BLOCKER, 5 HIGH, 2 MEDIUM\n") == 10


def test_a_declared_skip_is_untouched_by_the_change(run_rows, tmp_path):
    """A skip is reconciled against a degraded row, never a count."""
    run_rows.append({"run_id": "r1", "kind": "degraded", "reason": "no quota"})
    report = tmp_path / "r.md"
    report.write_text("Refutation: skipped\nSummary: nothing judged\n",
                      encoding="utf-8")

    assert sr.validate(run_id="r1", report_path=report) == []


# ============================================================
# The installer that reported a timer it had measured inactive
# ============================================================

def _stub_run(monkeypatch, is_active_rc: int, stdout: str = ""):
    seen = []

    def _fake(cmd, check=False):
        seen.append(cmd)
        if cmd[:3] == ["systemctl", "--user"] and cmd[2:3] == ["is-active"]:
            pass
        if "is-active" in cmd:
            return subprocess.CompletedProcess(cmd, is_active_rc, stdout, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sched, "_run", _fake)
    return seen


def test_an_inactive_timer_is_not_reported_as_enabled(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _stub_run(monkeypatch, is_active_rc=3, stdout="inactive")

    ok = sched._install_systemd_user_timer(
        unit_name="audit-probe", workspace_dir=tmp_path,
        script_rel_path="scripts/noop.py", script_args=[], cadence_min=30,
        description="probe")
    out = capsys.readouterr().out

    assert ok is False, "the installer returned success over an inactive timer"
    assert "inactive" in out
    assert "enabled and active" not in out


def test_an_active_timer_is_reported_as_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    _stub_run(monkeypatch, is_active_rc=0, stdout="active")

    ok = sched._install_systemd_user_timer(
        unit_name="audit-probe", workspace_dir=tmp_path,
        script_rel_path="scripts/noop.py", script_args=[], cadence_min=30,
        description="probe")

    assert ok is True
    assert "enabled and active" in capsys.readouterr().out


# ============================================================
# The runner that promised never to raise
# ============================================================

def test_a_missing_binary_is_reported_not_raised():
    """`schtasks` is absent on every non-Windows host, and this raised there."""
    result = sched._run(["definitely-not-a-command-zzz", "/query"])

    assert result.returncode == 127
    assert "definitely-not-a-command-zzz" in result.stderr


def test_a_real_command_still_runs():
    result = sched._run(["/bin/echo", "hello"])

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_an_empty_command_does_not_raise_either():
    assert sched._run([]).returncode == 127


# ============================================================
# The harness that patched whichever function came first
# ============================================================

def _harness_tree(tmp_path: Path, anchor_twice: bool) -> tuple:
    """A tiny repo with a passing contract and, optionally, a duplicated line."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    second = "    if flag != 0:\n        return 'other'\n" if anchor_twice else ""
    (root / "src.py").write_text(
        "def other(flag):\n" + (second or "    return 'other'\n") +
        "\n\ndef answer(flag):\n    if flag != 0:\n        return 'guarded'\n"
        "    return 'open'\n", encoding="utf-8")
    (root / "tests" / "test_contract.py").write_text(
        "import sys; sys.path.insert(0, '.')\n"
        "from src import answer\n"
        "def test_guard():\n    assert answer(1) == 'guarded'\n",
        encoding="utf-8")
    return root, ("tests/test_contract.py",)


def test_an_ambiguous_anchor_is_named_not_silently_applied(tmp_path, capsys):
    """It applied to the first match and reported the result as SURVIVED."""
    from scripts.utils.mutation_harness import run_mutations as harness

    root, tests = _harness_tree(tmp_path, anchor_twice=True)
    rc = harness(root, tests,
                 [("Z1", "src.py", "    if flag != 0:", "    if False:")],
                 python=sys.executable)
    out = capsys.readouterr().out

    assert "ANCHOR AMBIGUOUS (2x)" in out
    assert rc != 0, "an unapplied mutation must not read as a clean sweep"
    assert (root / "src.py").read_text(encoding="utf-8").count("if flag != 0") == 2


def test_a_unique_anchor_still_mutates_and_is_caught(tmp_path, capsys):
    from scripts.utils.mutation_harness import run_mutations as harness

    root, tests = _harness_tree(tmp_path, anchor_twice=False)
    rc = harness(root, tests,
                 [("Z1", "src.py", "    if flag != 0:", "    if False:")],
                 python=sys.executable)
    out = capsys.readouterr().out

    assert "caught" in out
    assert rc == 0


def test_a_missing_anchor_is_still_named(tmp_path, capsys):
    from scripts.utils.mutation_harness import run_mutations as harness

    root, tests = _harness_tree(tmp_path, anchor_twice=False)
    rc = harness(root, tests, [("Z1", "src.py", "nowhere at all", "x")],
                 python=sys.executable)

    assert "ANCHOR MISSING" in capsys.readouterr().out
    assert rc != 0


def test_the_tree_is_restored_after_an_ambiguous_anchor(tmp_path):
    from scripts.utils.mutation_harness import run_mutations as harness

    root, tests = _harness_tree(tmp_path, anchor_twice=True)
    before = (root / "src.py").read_text(encoding="utf-8")
    harness(root, tests, [("Z1", "src.py", "    if flag != 0:", "    if False:")],
            python=sys.executable)

    assert (root / "src.py").read_text(encoding="utf-8") == before
    assert list(root.rglob("*.mutbak")) == []


def test_the_tree_is_restored_after_a_mutation_that_actually_applied(tmp_path):
    """The restore, with a case on the line that exercises it.

    The ambiguous-anchor case above never writes the file, so it holds whether
    or not the `finally` puts the backup back: replacing
    `shutil.move(backup, target)` with `backup.unlink()` left this whole file
    green. That `finally` is the only thing standing between a killed harness
    and mutated production code left in a working tree, which is the accident
    this campaign has already had once, so it needs a case where the mutation
    landed.
    """
    from scripts.utils.mutation_harness import run_mutations as harness

    root, tests = _harness_tree(tmp_path, anchor_twice=False)
    before = (root / "src.py").read_text(encoding="utf-8")
    assert "if flag != 0:" in before, "the anchor must be present or nothing applied"

    rc = harness(root, tests,
                 [("Z1", "src.py", "    if flag != 0:", "    if False:")],
                 python=sys.executable)

    assert rc == 0, "the contract must have caught the mutation, or it never applied"
    assert (root / "src.py").read_text(encoding="utf-8") == before, (
        "the harness left its mutation in the tree")
    assert list(root.rglob("*.mutbak")) == []
