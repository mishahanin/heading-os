"""Golden + unit coverage for the shared DOCX bootstrap in scripts/utils/docx_helpers.py.

Two jobs in one module, because they guard the same seam.

1. ``load_docx()`` — the lazy python-docx bootstrap that eight generator scripts
   used to carry as eight copies of ``_ensure_docx()`` (143 duplicated lines,
   measured 2026-08-20). The unit tests below pin the two properties that made
   those copies necessary in the first place: the helper module must import
   WITHOUT python-docx installed (it is the optional ``documents`` extra), and
   the namespace it returns must carry every symbol the callers bind.

2. The golden artifacts. None of the eight scripts had a test before this
   module, so the de-duplication had nothing to prove itself against. Each test
   below runs one script in a sandboxed data root and compares the produced
   .docx against a committed fixture.

   The fixture is the extracted ``word/document.xml`` plus ``[Content_Types].xml``
   — NEVER the zip bytes. A .docx zip carries per-file mtimes and a
   docProps/core.xml timestamp, so its bytes differ on every run while the
   document itself is identical.

Regenerate the fixtures deliberately (and read the diff) with:

    HEADING_OS_GOLDEN_UPDATE=1 .venv/bin/python -m pytest tests/test_docx_helpers.py

Scripts whose brand template or operator input lives outside the engine skip
rather than fail: a public clone has neither.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.utils.workspace import get_datastore_dir

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "docx"
INPUTS = GOLDEN / "inputs"
UPDATE = os.environ.get("HEADING_OS_GOLDEN_UPDATE") == "1"

# The two brand master templates the two template-driven generators open. They
# live in the private datastore overlay, so they are absent on a public clone.
TEMPLATE_DIR = ("datastore", "brand", "templates")


# --------------------------------------------------------------------------
# Golden case registry
# --------------------------------------------------------------------------
# name -> (script, argv, seed files as {sandbox-relative dest: fixture input},
#          brand templates the script needs by filename)
CASES = {
    "generate-odunone-docx": (
        "scripts/generate-odunone-docx.py", [], {},
        ["31C - Master Template (New Identity 2026 v1.01).docx"],
    ),
    "generate-client-docx": (
        "scripts/generate-client-docx.py", [], {}, [],
    ),
    "generate-usecases-docx": (
        "scripts/generate-usecases-docx.py", [], {},
        ["31C - Master Template (New Identity 2026 v1.00).dotx"],
    ),
    "md-to-docx-proposal": (
        "scripts/md-to-docx-proposal.py", [],
        {"outputs/proposals/31C-National-Programme-DPI-Proposal-v1.md": "proposal.md"},
        [],
    ),
    "md-to-docx-competitive": (
        "scripts/md-to-docx-competitive.py", [],
        {"outputs/documents/competitive-analysis-example.md": "competitive.md"},
        [],
    ),
    "md-to-docx-letter": (
        "scripts/md-to-docx-letter.py", [],
        {"outputs/documents/example-support-letter.md": "letter.md"},
        [],
    ),
    "md-to-docx-charter": (
        # A fixed-template generator: the letter is hardcoded and no markdown
        # is read, so there is no seed input. It used to advertise an `md_path`
        # it ignored; corrected 2026-08-23.
        "scripts/md-to-docx-charter.py", [], {}, [],
    ),
    "gen-exec-meeting-docx": (
        "scripts/gen-exec-meeting-docx.py",
        ["--start", "2026-06-15", "--weeks", "2"], {}, [],
    ),
}


def _sandbox(tmp_path: Path, seeds: dict[str, str], templates: list[str]) -> Path:
    """Build a throwaway data root so a generator writes nowhere real.

    HEADING_OS_DATA wins over every other data-root rule (scripts/utils/paths.py),
    and on a CEO workspace the datastore resolves through the same root, so one
    env var redirects both the template read and the artifact write.
    """
    data_root = tmp_path / "data"
    real_templates = get_datastore_dir() / "brand" / "templates"
    if templates:
        dest = data_root.joinpath(*TEMPLATE_DIR)
        dest.mkdir(parents=True, exist_ok=True)
        for name in templates:
            src = real_templates / name
            if not src.is_file():
                pytest.skip(f"brand template not in this clone: {name}")
            shutil.copy2(src, dest / name)
    for rel, fixture in seeds.items():
        target = data_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INPUTS / fixture, target)
    # Four of the eight save straight to a path without mkdir-ing its parent —
    # they were written against a workspace where outputs/documents already
    # existed. Pre-create the standard leaves so the sandbox matches that
    # assumption; this is scaffolding, not a behaviour change.
    for leaf in ("documents", "proposals", "deliverables/documents"):
        (data_root / "outputs" / leaf).mkdir(parents=True, exist_ok=True)
    return data_root


def _extract(data_root: Path) -> dict[str, bytes]:
    """Map <docx relative path>::<part> -> bytes for the two comparable parts.

    Scoped to outputs/ on purpose: the seeded brand master template is also a
    .docx sitting in the sandbox, and it is private datastore content that must
    never land in a committed engine fixture.
    """
    out: dict[str, bytes] = {}
    for docx in sorted((data_root / "outputs").rglob("*.docx")):
        rel = docx.relative_to(data_root).as_posix()
        with zipfile.ZipFile(docx) as zf:
            for part in ("word/document.xml", "[Content_Types].xml"):
                out[f"{rel}::{part}"] = zf.read(part)
    return out


def _fixture_name(key: str) -> str:
    """Flatten a '<relpath>::<part>' key into one shell-safe fixture filename.

    Spaces and brackets go too: several repo gates iterate file lists through a
    shell, and a fixture whose name needs quoting is a fixture that silently
    stops being scanned.
    """
    flat = key.replace("::", "__").replace("/", "_")
    for ch in " []":
        flat = flat.replace(ch, "_" if ch == " " else "")
    return flat


def _run_case(name: str, tmp_path: Path) -> None:
    script, argv, seeds, templates = CASES[name]
    pytest.importorskip("docx", reason="python-docx is the optional 'documents' extra")
    data_root = _sandbox(tmp_path, seeds, templates)

    env = dict(os.environ, HEADING_OS_DATA=str(data_root))
    proc = subprocess.run(
        [sys.executable, str(REPO / script), *argv],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"

    produced = _extract(data_root)
    assert produced, f"{script} produced no .docx under the sandbox data root"

    case_dir = GOLDEN / name
    if UPDATE:
        if case_dir.exists():
            shutil.rmtree(case_dir)
        case_dir.mkdir(parents=True)
        for key, blob in produced.items():
            (case_dir / _fixture_name(key)).write_bytes(blob)
        pytest.skip(f"golden fixtures rewritten for {name}")

    assert case_dir.is_dir(), f"no golden fixture for {name}; run with HEADING_OS_GOLDEN_UPDATE=1"
    expected = {p.name: p.read_bytes() for p in sorted(case_dir.iterdir())}
    actual = {_fixture_name(k): v for k, v in produced.items()}
    assert sorted(actual) == sorted(expected), "the set of produced document parts changed"
    for part in sorted(expected):
        assert actual[part] == expected[part], f"{name}: {part} differs from the golden fixture"


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_docx(name: str, tmp_path: Path) -> None:
    _run_case(name, tmp_path)


# --------------------------------------------------------------------------
# load_docx() unit coverage
# --------------------------------------------------------------------------

def test_helpers_import_without_docx() -> None:
    """The module must import pure: python-docx is an optional extra, and a
    clone without it still has to collect every caller (F-2.1)."""
    src = (REPO / "scripts" / "utils" / "docx_helpers.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        assert not line.startswith(("import docx", "from docx")), (
            f"module-scope docx import would break a clone without the extra: {line}"
        )


def test_load_docx_binds_every_symbol_callers_use() -> None:
    pytest.importorskip("docx", reason="python-docx is the optional 'documents' extra")
    from scripts.utils.docx_helpers import load_docx

    ns = load_docx()
    for attr in (
        "Document", "Pt", "Cm", "Inches", "Emu", "RGBColor",
        "WD_ALIGN_PARAGRAPH", "WD_TABLE_ALIGNMENT", "WD_ORIENT",
        "qn", "nsdecls", "parse_xml", "OxmlElement",
    ):
        assert getattr(ns, attr) is not None, f"load_docx() did not bind {attr}"


def test_load_docx_is_cached() -> None:
    """Callers call it per build; rebuilding the namespace each time would be
    pure waste, and the eight _ensure_docx() copies it replaced all short-circuited."""
    pytest.importorskip("docx", reason="python-docx is the optional 'documents' extra")
    from scripts.utils.docx_helpers import load_docx

    assert load_docx() is load_docx()
