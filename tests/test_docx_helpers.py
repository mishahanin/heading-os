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

from scripts.utils.docx_helpers import brand_master_template
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
#          brand template SUFFIXES the script needs)
#
# Suffixes, not filenames. Each entry used to name the master template in full,
# version and all, and the usecases row said v1.00 after the master became
# v1.01: the copy found nothing, this case SKIPPED, and nobody learned that
# `generate-usecases-docx.py` had been dying on its own dead literal since the
# bump. `brand_master_template` resolves the newest, which is the same function
# the generators now call, so a bump can no longer split the two apart.
CASES = {
    "generate-odunone-docx": (
        "scripts/generate-odunone-docx.py", [], {}, [".docx"],
    ),
    "generate-client-docx": (
        "scripts/generate-client-docx.py", [], {}, [],
    ),
    "generate-usecases-docx": (
        "scripts/generate-usecases-docx.py", [], {}, [".dotx"],
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
    # The ROOT itself must exist before `HEADING_OS_DATA` can point at it:
    # `env_data_root()` honours the override only when it names a real
    # directory, and a miss falls back to the live overlay. Until 2026-08-24
    # this line was absent and the root came into being only as a side effect
    # of pre-creating the output leaves below -- so the two cases with neither
    # a seed file nor a brand template silently ran against real data.
    data_root.mkdir(parents=True, exist_ok=True)
    real_templates = get_datastore_dir() / "brand" / "templates"
    if templates:
        dest = data_root.joinpath(*TEMPLATE_DIR)
        dest.mkdir(parents=True, exist_ok=True)
        for suffix in templates:
            try:
                src = brand_master_template(suffix, templates_dir=real_templates)
            except FileNotFoundError as exc:
                pytest.skip(f"brand template not in this clone: {exc}")
            shutil.copy2(src, dest / src.name)
    for rel, fixture in seeds.items():
        target = data_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INPUTS / fixture, target)
    # The output leaves are deliberately NOT pre-created. Seven of the eight
    # saved straight to a path without mkdir-ing its parent, and this sandbox
    # used to create `documents`, `proposals` and `deliverables/documents` to
    # match that assumption — scaffolding that made a real FileNotFoundError
    # on a fresh data root unreachable from the suite. The count in that
    # comment was wrong too: it said four, and a sweep on 2026-08-24 found
    # seven. All seven now go through `docx_helpers.save_docx`, which creates
    # its own parent, so leaving the sandbox bare is what proves it.
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
    # `iterdir` lists the fixtures and this reads them. Skipping one that went
    # away in between would drop a part from `expected`, and the very next
    # assertion reports the set of parts as CHANGED - a wrong verdict about the
    # renderer, stated as fact. Retry once for the race, then fail naming it.
    expected = {}
    for p in sorted(case_dir.iterdir()):
        try:
            expected[p.name] = p.read_bytes()
        except FileNotFoundError:
            try:
                expected[p.name] = p.read_bytes()
            except FileNotFoundError as gone:
                raise AssertionError(
                    f"golden fixture {p} vanished between the listing and the "
                    f"read; the part-set comparison below would blame the "
                    f"renderer for a file that simply went away") from gone
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
