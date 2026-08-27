"""Six checks that admitted the thing each was written to refuse.

Every one of them was CONFIRMED by running it before a line was changed, which
matters here: the workflow panel that surfaced them refuted 0 of 16 findings,
and a verifier that never refutes is a rubber stamp, not evidence. Two of the
six turned out different from the report once reproduced, and one of my own
first fixes was refuted by the corpus (see the Odin section).

1. `optdeps.available()` / `require()` accepted a PEP-420 namespace package - an
   EMPTY DIRECTORY on sys.path - as an installed dependency.
2. `odin_skill_proposal`'s reflection gate matched "matured from" and "reflect"
   anywhere in a document, across sections.
3. `docx_font_embed._patch_content_types`'s replace branch could never match, so
   a second `<Default Extension="ttf">` was inserted beside the first.
4. `deep_research_prompts._remap_inline_citations` returned an unsourced angle's
   local `[1]` unchanged into a corpus the prompt declares GLOBAL.
5. `llm-fit-report.fetch_traces` returned a truncated page walk with no signal,
   and the report printed "Window: last N days" over it.
6. `compression-candidates.py` resolved `datastore` against the ENGINE root, so
   the default invocation exited 1.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    """Import a kebab-case CLI script under a module name."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# One: an empty directory is not an installed dependency
# ============================================================

@pytest.fixture()
def namespace_dir(tmp_path, monkeypatch):
    """A bare directory on sys.path, which PEP 420 makes importable."""
    pkg = tmp_path / "phantomdep"
    pkg.mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("phantomdep", None)
    importlib.invalidate_caches()
    yield "phantomdep"
    sys.modules.pop("phantomdep", None)


def test_a_bare_directory_is_reported_absent_by_available(namespace_dir):
    """`find_spec` answers yes for a directory holding nothing."""
    from scripts.utils import optdeps

    assert optdeps.available(namespace_dir) is False


def test_require_refuses_a_bare_directory_with_the_actionable_message(
        namespace_dir, capsys):
    """The whole point of this module: a message, never a stack trace.

    `import_module` on a namespace package SUCCEEDS and returns a module whose
    every attribute access raises AttributeError. So the caller got its
    traceback from deep inside its own code, which is what `require` exists to
    prevent, and the operator was never told to run `uv sync`.
    """
    from scripts.utils import optdeps

    with pytest.raises(SystemExit) as exc:
        optdeps.require(namespace_dir, extra="email")
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().err.strip())
    assert "not installed" in payload["error"]
    assert "uv sync --extra email" in payload["error"]


def test_the_namespace_check_is_not_vacuous():
    """A real module must still pass, or the guard refuses everything.

    `json` is stdlib and always importable, so this cannot flake on a machine
    without the optional extras installed.
    """
    from scripts.utils import optdeps

    assert optdeps.available("json") is True
    assert optdeps.require("json", extra="anything").__name__ == "json"


def test_a_genuinely_missing_module_is_still_absent():
    from scripts.utils import optdeps

    assert optdeps.available("no_such_module_9d3f") is False


def test_a_populated_namespace_package_is_present(tmp_path, monkeypatch):
    """The refutation, kept as a test because the suite found it, not I did.

    The first fix here refused EVERY PEP-420 namespace package, and the suite
    killed it in one run: `google` is a legitimate namespace package - that
    shape exists so `google.auth`, `google.oauth2` and `google.protobuf` can
    ship as separate distributions into one directory - and
    `gmail_auth.get_service()` calls `require("google", ...)`. Refusing it broke
    Gmail authentication outright. Emptiness is the test, not the shape.
    """
    from scripts.utils import optdeps

    pkg = tmp_path / "populated_ns"
    (pkg / "child").mkdir(parents=True)
    (pkg / "child" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("populated_ns", None)
    importlib.invalidate_caches()
    try:
        assert optdeps.available("populated_ns") is True
        assert optdeps.require("populated_ns", extra="demo") is not None
    finally:
        sys.modules.pop("populated_ns", None)


def test_the_real_google_namespace_package_is_not_refused():
    """The exact case the suite caught, asserted against the installed tree.

    Skipped rather than faked when `google` is absent: a test that invents the
    package would assert its own fixture, not the venv this workspace runs on.
    """
    from scripts.utils import optdeps

    spec = importlib.util.find_spec("google")
    if spec is None:
        pytest.skip("google is not installed in this environment")
    assert spec.origin is None, (
        "google is no longer a namespace package here; this test guarded that "
        "shape and now guards nothing")
    assert optdeps.available("google") is True


def test_a_spec_with_no_origin_and_no_locations_is_not_a_namespace_package():
    """The narrow half, which nothing else reaches.

    `find_spec` on this machine never produces a spec with neither an origin
    nor a search-location list, so no corpus-driven test can tell a correct
    implementation from one that answers True there. Asserted directly on a
    hand-built `ModuleSpec`: no origin ALONE does not make something an empty
    directory. Erring toward "available" is deliberate; refusing a module that
    might work is the worse mistake for a probe that gates a capability.
    """
    from importlib.machinery import ModuleSpec

    from scripts.utils import optdeps

    bare = ModuleSpec("phantom", loader=None)
    assert bare.origin is None
    assert bare.submodule_search_locations is None
    assert optdeps._is_empty_namespace(bare) is False


def test_an_unreadable_search_location_is_not_read_as_empty(tmp_path, monkeypatch):
    """"I could not look" must not become "there is nothing there".

    The same fail-toward-over-reporting rule `.claude/rules/scope-claims.md`
    states for coverage claims: an unavailable measurement widens back, it does
    not silently answer the strict way.
    """
    from importlib.machinery import ModuleSpec

    from scripts.utils import optdeps

    spec = ModuleSpec("phantom", loader=None, is_package=True)
    spec.origin = None
    spec.submodule_search_locations = [str(tmp_path / "does-not-exist")]
    assert optdeps._is_empty_namespace(spec) is False


def test_available_answers_rather_than_raising_on_a_missing_parent():
    """`find_spec("a.b")` RAISES when `a` is absent; a probe must answer.

    A helper whose contract is "can I use this" propagating ImportError to its
    caller defeats the caller's own degradation path.
    """
    from scripts.utils import optdeps

    assert optdeps.available("no_such_parent_9d3f.child") is False


# ============================================================
# Two: the reflection gate, and the corpus that refuted my first fix
# ============================================================

BOOK_PRINCIPLE = (
    "## Evidence\n"
    "It now has 31C's own controlled comparison, matured from a 2019 study.\n"
    "\n"
    "## Notes\n"
    "Budget allocation should reflect this.\n"
)

WRAPPED_ATTRIBUTION = (
    "## Evidence\n"
    "Matured from three lived episodes of 2026-08-10, all within the same day\n"
    "and each showing a different face, CEO-confirmed in `reflect` on 2026-08-11.\n"
)


def test_a_book_principle_is_refused_by_the_reflection_gate():
    """The case the module docstring says it "correctly refuses".

    The old pattern was DOTALL with a lazy `.*?`, so "matured from" in one
    section and "reflect" in another satisfied it. Measured on the live corpus:
    2,280 characters and several sections apart.
    """
    from scripts.utils.odin_skill_proposal import _is_reflection_derived

    assert _is_reflection_derived(BOOK_PRINCIPLE) is False


def test_an_attribution_wrapped_over_a_line_break_is_still_accepted():
    """My first fix bounded the gate to ONE line and the corpus refuted it.

    Two genuine principles wrap the attribution across a newline
    (`proximity-to-the-counterpart-decides-who-acts` and
    `split-the-irreversible-stage-from-the-replayable-one`), so a same-line rule
    would have refused two real ones to catch one false one. Written down here
    because the next author will reach for the same tightening.
    """
    from scripts.utils.odin_skill_proposal import _is_reflection_derived

    assert _is_reflection_derived(WRAPPED_ATTRIBUTION) is True


@pytest.mark.parametrize("body,expected", [
    ("Matured from 4 episodes, CEO-confirmed in `reflect` on 2026-05-01.", True),
    ("**Matured from** two lived episodes. CEO-confirmed in `reflect`.", True),
    ("  Matured from two episodes\nCEO-confirmed in `reflect`.", True),
    # No `reflect` anywhere: the second signal is genuinely required.
    ("Matured from two lived episodes on 2026-01-01.", False),
    # `reflect` present but the provenance claim is mid-sentence prose.
    ("The figure was matured from a book, and the data reflect it.", False),
    ("", False),
])
def test_the_two_signals_are_both_required(body, expected):
    from scripts.utils.odin_skill_proposal import _is_reflection_derived

    assert _is_reflection_derived(body) is expected


def test_the_gate_is_anchored_not_a_word_presence_test():
    """The property, stated so a future rewrite cannot lose it.

    Same two words, same document, differing only in whether the provenance
    claim OPENS a line. If both answers were equal the gate would be measuring
    vocabulary rather than structure.
    """
    from scripts.utils.odin_skill_proposal import _is_reflection_derived

    anchored = "Matured from two episodes.\nCEO-confirmed in `reflect`."
    inline = "It was matured from two episodes.\nCEO-confirmed in `reflect`."
    assert _is_reflection_derived(anchored) is True
    assert _is_reflection_derived(inline) is False


# ============================================================
# Three: one ttf Default in, one out
# ============================================================

REAL_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.obfuscatedFont"
)


def _ctypes(*defaults: str) -> str:
    inner = "".join(defaults)
    return f'<?xml version="1.0"?><Types xmlns="urn:x">{inner}</Types>'


def test_an_existing_ttf_default_is_replaced_not_duplicated():
    """Reproduced 2026-08-27: one Default in, two out.

    `[^/>]*` cannot cross a slash and EVERY real ContentType value carries one,
    so the replace branch matched nothing that Word or python-docx writes. The
    insert branch ran in its place. Duplicate Default extensions make the OPC
    package invalid, and the stale ContentType survived underneath.
    """
    from scripts.utils.docx_font_embed import _patch_content_types

    xml = _ctypes(
        '<Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="ttf" ContentType="application/x-fontdata"/>',
    )
    out = _patch_content_types(xml)
    assert out.count('Extension="ttf"') == 1
    assert "application/x-fontdata" not in out
    assert REAL_CONTENT_TYPE in out


def test_the_replacement_leaves_other_defaults_alone():
    from scripts.utils.docx_font_embed import _patch_content_types

    xml = _ctypes(
        '<Default Extension="rels" ContentType="application/x-rels"/>',
        '<Default Extension="ttf" ContentType="application/x-fontdata"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    )
    out = _patch_content_types(xml)
    assert 'Extension="rels" ContentType="application/x-rels"' in out
    assert 'Extension="xml" ContentType="application/xml"' in out


def test_patching_twice_is_idempotent():
    """The real trigger: re-embedding fonts into an already-embedded .docx.

    Each run added one more duplicate, so the defect grew with use rather than
    announcing itself once.
    """
    from scripts.utils.docx_font_embed import _patch_content_types

    xml = _ctypes('<Default Extension="ttf" ContentType="application/x-old"/>')
    once = _patch_content_types(xml)
    twice = _patch_content_types(once)
    assert twice.count('Extension="ttf"') == 1
    assert twice == once


def test_the_insert_branch_still_covers_a_document_with_no_ttf_default():
    from scripts.utils.docx_font_embed import _patch_content_types

    out = _patch_content_types(
        _ctypes('<Default Extension="rels" ContentType="a/b"/>'))
    assert out.count('Extension="ttf"') == 1
    assert REAL_CONTENT_TYPE in out


def test_a_content_types_without_a_types_element_is_refused():
    from scripts.utils.docx_font_embed import _patch_content_types

    with pytest.raises(ValueError, match="malformed"):
        _patch_content_types("<NotTypes/>")


# ============================================================
# Four: an unsourced angle cannot cite a global id
# ============================================================

def test_an_unsourced_angle_has_its_markers_stripped():
    """The early return handed local `[1]` to a prompt that declares ids GLOBAL.

    The model then read it as global source 1, which belongs to a different
    angle, and attributed a claim to a source that never supported it.
    """
    from scripts.utils.deep_research_prompts import _remap_inline_citations

    assert _remap_inline_citations("Claim A [1] and B [2].", []) == "Claim A and B."


def test_stripping_takes_the_space_with_the_marker():
    """Otherwise the model reads "A  and B ." as damage, not as a removal."""
    from scripts.utils.deep_research_prompts import _remap_inline_citations

    assert _remap_inline_citations("Multi [1, 2] group.", []) == "Multi group."
    assert _remap_inline_citations("No markers here.", []) == "No markers here."


def test_a_sourced_angle_is_still_remapped_to_its_global_ids():
    from scripts.utils.deep_research_prompts import _remap_inline_citations

    assert _remap_inline_citations("A [1], B [2].", [41, 42]) == "A [41], B [42]."


def test_a_marker_outside_the_range_is_still_left_alone():
    """A footnote or a version number in brackets is not ours to renumber."""
    from scripts.utils.deep_research_prompts import _remap_inline_citations

    assert _remap_inline_citations("See [9].", [41]) == "See [9]."


def test_the_corpus_says_in_words_that_an_angle_had_no_sources():
    """An empty Sources block rendered as a bare blank line.

    Nothing in the prompt let the model tell "no sources" from a formatting
    accident, and the instruction below it says to cite the id printed in the
    corpus.
    """
    from scripts.utils.deep_research_prompts import build_reason_prompt

    prompt = build_reason_prompt("Q", [
        {"angle": "dry", "content": "Claim A [1].", "source_ids": []},
        {"angle": "wet", "content": "Claim B [1].", "source_ids": [41]},
    ])
    corpus = prompt.split("CORPUS:")[1].split("Tasks:")[0]
    assert "no sources" in corpus
    assert "Claim A ." not in corpus and "Claim A [1]" not in corpus
    assert "Claim B [41]." in corpus


# ============================================================
# Five: a window the fetch did not cover
# ============================================================

def _llm_fit():
    return _load("llm_fit_report_under_test", "scripts/llm-fit-report.py")


def test_a_truncated_fetch_is_named_in_the_report_body():
    """stderr is not in the file, and the file is the deliverable.

    Both early exits warned on stderr and returned a partial list. The report
    then said "Window: last 30 days" and every count, percentage and percentile
    under it was computed on the fragment.
    """
    mod = _llm_fit()
    agg = {"skill-a": {"total": 3, "by_vendor": {"anthropic": 3},
                       "fallback_count": 0, "downgrade_candidates": 0,
                       "flag_eligible": 0, "downgrade_pct": 0.0,
                       "with_tool_use": 0, "median_output_tokens": 10,
                       "p90_output_tokens": None}}
    md = mod.render_markdown(agg, 30, "2026-08-27T00:00:00+00:00", 3,
                             "stopped at the 50-page safety cap with 5000 trace(s)")
    assert "INCOMPLETE FETCH" in md
    assert "50-page safety cap" in md
    assert "Window: last 30 days." not in md, (
        "the report still claims the whole window")


def test_a_complete_fetch_still_claims_the_whole_window():
    """The guard must not make every report hedge; that teaches nothing."""
    mod = _llm_fit()
    md = mod.render_markdown({}, 30, "2026-08-27T00:00:00+00:00", 0, None)
    assert "Window: last 30 days." in md
    assert "INCOMPLETE" not in md


def test_the_truncation_notice_survives_the_empty_aggregate_branch():
    """`if not agg` returns early, and "nothing was wired" is the wrong story
    to tell an operator whose fetch actually failed on page 2."""
    mod = _llm_fit()
    md = mod.render_markdown({}, 7, "2026-08-27T00:00:00+00:00", 0,
                             "the Langfuse query failed at page 2")
    assert "INCOMPLETE FETCH" in md
    assert "failed at page 2" in md


def test_fetch_traces_returns_the_reason_beside_the_traces():
    """The signature is the contract: a bare list cannot carry the caveat."""
    mod = _llm_fit()
    import inspect

    sig = inspect.signature(mod.fetch_traces)
    assert "tuple" in str(sig.return_annotation), (
        f"fetch_traces returns {sig.return_annotation}; a caller cannot learn "
        f"the walk was cut short from a bare list")


class _StubTrace:
    """One trace object; only its identity matters to the pager."""


def _install_stub_langfuse(monkeypatch, pages):
    """Put a fake `langfuse` module in sys.modules for the in-function import.

    `pages` is a callable taking the 1-based page number and returning either a
    list of traces or raising. `fetch_traces` imports `langfuse` INSIDE the
    function, so replacing the module entry is enough and nothing real is
    contacted.
    """
    import types

    class _Resp:
        def __init__(self, data):
            self.data = data

    class _Api:
        class trace:  # noqa: N801 - mirrors the vendor's attribute shape
            @staticmethod
            def list(from_timestamp, page, limit):
                return _Resp(pages(page))

    class _Client:
        api = _Api()

    stub = types.ModuleType("langfuse")
    stub.get_client = lambda: _Client()
    monkeypatch.setitem(sys.modules, "langfuse", stub)


def test_the_page_cap_is_reported_by_the_fetch_itself(monkeypatch, capsys):
    """Driven, not inspected. The signature test above passes on a function
    that returns `(out, None)` from every branch, which is exactly the defect.

    Every page comes back full, so the walk never ends on its own and the
    50-page safety cap is what stops it: a truncation, and the caller must be
    told.
    """
    mod = _llm_fit()
    _install_stub_langfuse(monkeypatch, lambda page: [_StubTrace()] * 100)

    traces, truncated = mod.fetch_traces(days=30, page_size=100)
    assert truncated, "the 50-page cap returned no reason"
    assert "50-page" in truncated
    assert len(traces) == 5000
    assert "WARN" in capsys.readouterr().err


def test_a_failed_page_is_reported_by_the_fetch_itself(monkeypatch, capsys):
    """The other early exit, and the more likely one in production."""
    mod = _llm_fit()

    def pages(page):
        if page == 1:
            return [_StubTrace()] * 100
        raise RuntimeError("connection reset")

    _install_stub_langfuse(monkeypatch, pages)
    traces, truncated = mod.fetch_traces(days=7, page_size=100)
    assert truncated, "a failed page returned no reason"
    assert "page 2" in truncated and "connection reset" in truncated
    assert len(traces) == 100
    assert "WARN" in capsys.readouterr().err


@pytest.mark.parametrize("last_page_size", [0, 42])
def test_a_walk_that_reaches_the_end_reports_no_truncation(
        monkeypatch, last_page_size):
    """Both natural endings: an empty page, and a short final page.

    Without this the guard could be satisfied by returning a reason always,
    which would make every report hedge and teach the operator to ignore it.
    """
    mod = _llm_fit()

    def pages(page):
        if page == 1:
            return [_StubTrace()] * 100
        return [_StubTrace()] * last_page_size

    _install_stub_langfuse(monkeypatch, pages)
    traces, truncated = mod.fetch_traces(days=7, page_size=100)
    assert truncated is None
    assert len(traces) == 100 + last_page_size


def test_render_markdown_accepts_the_truncation_argument():
    mod = _llm_fit()
    import inspect

    assert "truncated" in inspect.signature(mod.render_markdown).parameters


# ============================================================
# Six: datastore lives in the overlay, not in the engine
# ============================================================

def _compression():
    return _load("compression_candidates_under_test",
                 "scripts/compression-candidates.py")


def test_the_default_path_resolves_into_the_data_overlay():
    """It joined `datastore` onto the ENGINE root, which never holds it.

    So the default invocation - and every example in the script's own usage
    block - printed "Path not found" and exited 1.
    """
    from scripts.utils.workspace import get_datastore_dir, get_workspace_root

    mod = _compression()
    resolved = mod._resolve_scan_root("datastore")
    assert resolved == get_datastore_dir()
    assert resolved != get_workspace_root() / "datastore"


def test_a_datastore_subfolder_resolves_through_the_same_seam():
    from scripts.utils.workspace import get_datastore_dir

    mod = _compression()
    assert (mod._resolve_scan_root("datastore/corporate")
            == get_datastore_dir() / "corporate")


def test_an_engine_path_stays_engine_relative():
    """`--path docs` must keep meaning what it says."""
    from scripts.utils.workspace import get_workspace_root

    mod = _compression()
    assert mod._resolve_scan_root("docs") == get_workspace_root() / "docs"


def test_every_usage_example_in_the_docstring_names_a_resolvable_path():
    """The defect was documented invocations that could not run.

    Checks the `--path` values the docstring shows, against the resolver rather
    than against the filesystem, so this stays true on a clone with no overlay.
    """
    mod = _compression()
    doc = mod.__doc__ or ""
    shown = re.findall(r"--path\s+(\S+)", doc)
    assert shown, "the usage block no longer shows a --path example"
    for rel in shown:
        resolved = mod._resolve_scan_root(rel)
        assert "datastore" not in resolved.parts or "-data" in str(resolved), (
            f"--path {rel} resolves to {resolved}, inside the engine clone")


@pytest.fixture()
def doc_tree(tmp_path):
    """A scan root with one document per type, each over its own floor."""
    root = tmp_path / "store"
    (root / "sub").mkdir(parents=True)
    (root / "_archive").mkdir()
    (root / "big.PDF").write_bytes(b"0" * 2_000_000)      # pdf floor 1.5 MB
    (root / "sub" / "deck.PPTX").write_bytes(b"0" * 6_000_000)   # pptx 5.0 MB
    (root / "sheet.XlSx").write_bytes(b"0" * 3_000_000)   # xlsx 2.0 MB
    (root / "plain.pdf").write_bytes(b"0" * 2_000_000)
    (root / "_archive" / "old.pdf").write_bytes(b"0" * 2_000_000)
    (root / "notes.txt").write_bytes(b"0" * 9_000_000)
    return root


def test_an_uppercase_extension_is_still_a_document(doc_tree):
    """`rglob("*.pdf")` is case-SENSITIVE on Linux.

    So `REPORT.PDF` was invisible to the scan at any size, and the report said
    nothing about having skipped it. One such file exists in the live datastore
    today and happens to sit under its threshold, which is why this never
    surfaced: the walk that misses a 30 MB `.PDF` looks identical to a correct
    one until the day it matters.
    """
    mod = _compression()
    found = {c["name"]: c["ext"] for c in
             mod.scan(doc_tree, include_archive=False, min_mb=0)}
    assert found == {"big.PDF": ".pdf", "deck.PPTX": ".pptx",
                     "sheet.XlSx": ".xlsx", "plain.pdf": ".pdf"}


def test_an_unlisted_extension_is_still_skipped(doc_tree):
    """The guard must not have widened into "every large file"."""
    mod = _compression()
    names = {c["name"] for c in mod.scan(doc_tree, include_archive=False, min_mb=0)}
    assert "notes.txt" not in names


def test_the_archive_filter_is_relative_to_the_scan_root(tmp_path):
    """`f.parts` is the ABSOLUTE path, so one `_archive` in the tree's ancestry
    excluded every file beneath it and the scan reported zero candidates.

    The sibling `output-organizer.py` carries a fix-comment for this exact
    defect; this copy still had it. Found while editing these lines, so the diff
    is wider than the case fix alone.
    """
    mod = _compression()
    root = tmp_path / "_archive" / "store"
    root.mkdir(parents=True)
    (root / "live.pdf").write_bytes(b"0" * 2_000_000)
    (root / "_archive").mkdir()
    (root / "_archive" / "old.pdf").write_bytes(b"0" * 2_000_000)

    names = {c["name"] for c in mod.scan(root, include_archive=False, min_mb=0)}
    assert names == {"live.pdf"}, (
        "an ancestor directory named _archive swallowed the whole tree")

    with_archive = {c["name"] for c in
                    mod.scan(root, include_archive=True, min_mb=0)}
    assert with_archive == {"live.pdf", "old.pdf"}


def test_the_per_type_floor_still_applies(doc_tree):
    """A 3 MB pptx is under the 5 MB pptx floor even though it clears the pdf
    one; the profile is per extension, not global."""
    mod = _compression()
    small = doc_tree / "tiny.pptx"
    small.write_bytes(b"0" * 3_000_000)
    names = {c["name"] for c in mod.scan(doc_tree, include_archive=False, min_mb=0)}
    assert "tiny.pptx" not in names


def test_the_cli_runs_to_completion_on_the_real_tree():
    """End to end, because the exit code was the whole symptom."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compression-candidates.py"),
         "--format", "json"],
        cwd=ROOT, capture_output=True, text=True)
    if "Path not found" in result.stderr and "-data" in result.stderr:
        pytest.skip("no data overlay on this machine (CI); the resolver is "
                    "asserted directly by the tests above")
    assert result.returncode == 0, result.stderr[-800:]
    json.loads(result.stdout)
