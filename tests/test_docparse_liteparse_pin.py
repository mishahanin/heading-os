"""The installer must install the version the parser was written against.

Found by the 2026-08-23 audit. `scripts/docparse.py` named three liteparse
versions and one of them was wrong:

* the module docstring and `setup --check` told the operator `2.0.0`;
* `parse_document` was written against the 2.0 API, where `dpi`,
  `target_pages` and `password` moved into the `LiteParse(...)` constructor;
* `setup --install` — the command the failure message points at — ran
  `pip install liteparse==1.2.1`.

So the documented repair path installed a package whose constructor rejects
every keyword the parser passes. Setup printed "Setup complete", and the first
document raised a TypeError with nothing connecting the two.

Version literals in prose drift because nothing reads them. These tests read
them.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_DOCPARSE = ROOT / "scripts" / "docparse.py"


def _load():
    spec = importlib.util.spec_from_file_location("docparse_pin", _DOCPARSE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["docparse_pin"] = module
    spec.loader.exec_module(module)
    return module


docparse = _load()
SOURCE = _DOCPARSE.read_text(encoding="utf-8")


def test_every_liteparse_pin_in_the_file_is_the_same_version():
    """Includes the docstring, which is what the operator reads first."""
    found = set(re.findall(r"liteparse==([0-9][0-9.]*)", SOURCE))
    assert found == {docparse.LITEPARSE_VERSION}, (
        f"docparse.py names liteparse versions {sorted(found)}; "
        f"LITEPARSE_VERSION is {docparse.LITEPARSE_VERSION}"
    )


def _parse_document() -> ast.FunctionDef:
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == "parse_document":
            return node
    raise AssertionError("parse_document not found in scripts/docparse.py")


def _liteparse_call(func: ast.FunctionDef) -> ast.Call:
    calls = [n for n in ast.walk(func)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "LiteParse"]
    assert len(calls) == 1, (
        f"expected exactly one LiteParse(...) construction inside parse_document, "
        f"found {len(calls)}; re-scope this guard rather than let it read the wrong one"
    )
    return calls[0]


def _constructor_keywords(func: ast.FunctionDef) -> set[str]:
    """Every keyword name that reaches the `LiteParse(...)` constructor.

    Direct keywords, plus the string keys of a mapping splatted in with `**` and
    built inside the same function, which is the shape the code actually uses
    (`parser_kwargs = {...}`, then `parser_kwargs["target_pages"] = pages`).
    """
    call = _liteparse_call(func)
    names: set[str] = set()
    splatted: set[str] = set()
    for kw in call.keywords:
        if kw.arg is None:
            if isinstance(kw.value, ast.Name):
                splatted.add(kw.value.id)
        else:
            names.add(kw.arg)
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Name) and target.id in splatted
                    and isinstance(node.value, ast.Dict)):
                names |= {k.value for k in node.value.keys
                          if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in splatted
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                names.add(target.slice.value)
    return names


# Written out here rather than read back off the call, because a set derived
# from the thing under test shrinks with it and the mutant deletes its own
# coverage. Three keywords, and the count is pinned below.
CONSTRUCTOR_KEYWORDS_2X = {"dpi", "target_pages", "password"}


def test_the_pin_is_the_major_version_the_parser_calls():
    """`parse_document` passes 2.0-only constructor keywords. Pin 2.x or fix it.

    Asked of the AST, scoped to `parse_document`. The first version of this test
    asked `f'"{kw}"' in SOURCE or f"{kw}=" in SOURCE` over the WHOLE file.
    MEASURED 2026-09-01: rewriting the constructor to the 1.x shape
    (`LiteParse()` with `parser.parse(path, dpi=...)`) left all three tests in
    this file green, because `"dpi"`, `"target_pages"` and `"password"` all
    still occur elsewhere in `scripts/docparse.py` (the result dict, the cache
    key, the CLI). The guard could not tell a constructor call from a mention.
    """
    assert docparse.LITEPARSE_VERSION.startswith("2."), (
        "parse_document builds LiteParse(dpi=..., target_pages=..., password=...), "
        "which is the 2.0 constructor; a 1.x pin cannot run it"
    )
    assert len(CONSTRUCTOR_KEYWORDS_2X) == 3
    reaching = _constructor_keywords(_parse_document())
    missing = sorted(CONSTRUCTOR_KEYWORDS_2X - reaching)
    assert not missing, (
        f"LITEPARSE_VERSION pins {docparse.LITEPARSE_VERSION}, but "
        f"parse_document no longer passes {missing} to the LiteParse "
        f"constructor. In 2.0 those moved OUT of parse() and INTO the "
        f"constructor; a call site that stopped passing them is either running "
        f"the 1.x API against a 2.x pin, or the pin needs to move with it. "
        f"Keywords found reaching the constructor: {sorted(reaching)}"
    )


def test_the_parse_call_takes_only_the_path():
    """The other half of the same API break, and the half a keyword scan cannot
    see: in liteparse 2.0 `parse()` takes the file path and nothing else. A
    reverted call site puts `dpi` back on `parse(...)`, where 2.x rejects it."""
    func = _parse_document()
    parse_calls = [n for n in ast.walk(func)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "parse"]
    assert len(parse_calls) == 1, (
        f"expected exactly one .parse(...) call inside parse_document, "
        f"found {len(parse_calls)}"
    )
    call = parse_calls[0]
    assert call.keywords == [], (
        "parse() is being handed keywords: "
        f"{[k.arg for k in call.keywords]}. Under liteparse 2.0 those belong on "
        "the LiteParse(...) constructor."
    )
    assert len(call.args) == 1, (
        f"parse() takes the path alone under liteparse 2.0; this call passes "
        f"{len(call.args)} positional arguments"
    )


def test_the_installer_uses_the_constant_rather_than_a_literal():
    """The structural half: no fourth copy to drift."""
    install_line = next(
        line for line in SOURCE.splitlines() if '"pip", "install"' in line
    )
    assert "LITEPARSE_VERSION" in install_line, (
        "setup --install hardcodes a version again: " + install_line.strip()
    )


# ==========================================================================
# `report` must not need liteparse when it has nothing to screenshot
# ==========================================================================
#
# Found by the 2026-08-24 audit, shard scripts-05-p3 finding 4. Two couplings
# in one function. `from liteparse import LiteParse` sat at the TOP of
# `cmd_report`, above the "Parse JSON not found" / "Citations JSON not found"
# checks, so on a machine without the package the command died with a raw
# ImportError instead of its own diagnostics, even when the inputs were
# invalid anyway. And `LiteParse()` was then constructed unconditionally,
# including for zero citations, every citation ambiguous, or every cited page
# <= 0, where no screenshot can be taken and the HTML report renders its
# explicit "no page image" notes. A broken install killed a report that needed
# nothing from it.


class _NoLiteparse:
    """Make `import liteparse` raise, without uninstalling anything.

    A `None` entry in `sys.modules` is the documented way to make an import of
    that name fail. Restoring the previous entry, rather than deleting the key,
    keeps a real installed package available to every later test.
    """

    def __init__(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "liteparse", None)


def _report_args(tmp_path, *, citations, parse_files=None, missing_parse=False):
    import json
    import types

    parse_path = tmp_path / "parsed.json"
    cit_path = tmp_path / "citations.json"
    if not missing_parse:
        parse_path.write_text(json.dumps({"files": parse_files or []}),
                              encoding="utf-8")
    cit_path.write_text(
        json.dumps({"question": "q", "answer_md": "a", "citations": citations}),
        encoding="utf-8")
    return types.SimpleNamespace(
        parse_json=str(parse_path), citations=str(cit_path),
        output_dir=str(tmp_path / "out"), title="T", no_pdf=True,
        max_pages=docparse.MAX_REPORT_PAGES)


def test_a_missing_input_is_reported_before_liteparse_is_imported(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """The diagnostics this command owns must outrank an optional dependency."""
    _NoLiteparse(monkeypatch)
    args = _report_args(tmp_path, citations=[], missing_parse=True)
    try:
        docparse.cmd_report(args)
    except ImportError as exc:  # pragma: no cover - this is the defect
        raise AssertionError(
            "cmd_report imported liteparse before checking its inputs, so a "
            f"missing parse JSON surfaced as {exc!r} instead of the tool's own "
            "'Parse JSON not found' message") from exc
    except SystemExit as exc:
        assert exc.code == 2, f"expected the input-not-found code 2, got {exc.code!r}"
    else:
        raise AssertionError("a missing parse JSON did not exit at all")
    assert "Parse JSON not found" in capsys.readouterr().err


def test_a_report_with_no_page_to_capture_never_touches_liteparse(tmp_path,
                                                                  monkeypatch):
    """Zero citations means zero screenshots, so the parser is not needed."""
    _NoLiteparse(monkeypatch)
    args = _report_args(tmp_path, citations=[])
    docparse.cmd_report(args)
    html = Path(args.output_dir) / "docparse-report.html"
    assert html.is_file(), "the report was not written"
    assert html.stat().st_size > 0


def test_a_citation_on_page_zero_also_needs_no_parser(tmp_path, monkeypatch):
    """The other empty-screenshot route: every cited page is filtered out.

    Distinct from "no citations at all", because the `page <= 0` filter sits
    inside the loop and an empty `pages_to_screenshot` is reached with a
    non-empty citation list. A guard written only against `not citations`
    would pass the test above and still construct the parser here.
    """
    _NoLiteparse(monkeypatch)
    args = _report_args(tmp_path,
                        citations=[{"file": "a.pdf", "page": 0, "quote": "q"}])
    docparse.cmd_report(args)
    assert (Path(args.output_dir) / "docparse-report.html").is_file()


# ==========================================================================
# `_setup_check`'s comment must not deny the version checks above it
# ==========================================================================

_SETUP_CHECK_DENIALS = (
    "never compared a single version",
    "never compares a single version",
    "compared no version",
)


def _setup_check() -> ast.FunctionDef:
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == "_setup_check":
            return node
    raise AssertionError("_setup_check not found in scripts/docparse.py")


def _setup_check_source() -> str:
    func = _setup_check()
    lines = SOURCE.splitlines()
    return "\n".join(lines[func.lineno - 1:func.end_lineno])


def test_setup_check_really_does_compare_both_versions():
    """The fact the comment has to agree with, established first.

    Asked of the AST rather than of the prose, so this half cannot be satisfied
    by the same text the next test reads.
    """
    body = _setup_check_source()
    assert "major >= 18" in body, (
        "_setup_check no longer compares the Node major version")
    assert "!= LITEPARSE_VERSION" in body, (
        "_setup_check no longer compares the installed liteparse version")
    assert "version_unknown" in body, (
        "_setup_check no longer tracks whether a version could be confirmed")


def test_the_setup_check_comment_does_not_deny_the_checks_beside_it():
    """A comment that describes the code as it was, in the tense of now.

    Found by the 2026-08-24 audit, shard scripts-05-p3 finding 3. The closing
    comment read "over a method that never compared a single version" while the
    two blocks directly above it compared two, and a reader auditing why the
    summary sentence is worded as it is would conclude version drift was still
    unchecked here. It is checked, and downgraded to a WARN that sets
    `version_unknown`, which the summary is then gated on.
    """
    body = _setup_check_source()
    present = [claim for claim in _SETUP_CHECK_DENIALS if claim in body]
    assert not present, (
        "_setup_check carries a comment claiming it compares no version, while "
        f"the test above proves it compares two: {present}")
