"""Shard tests-skips-01: four checks this machine reported as "skipped" while
the thing under them was broken, dead, or never once measured.

The suite ran 13290 tests and skipped 12. A skip is not a pass and not a
failure; it is silence, and nobody reads it. Each of these four had a reason
string that sounded like an environment fact and was actually a defect.

* `scripts/generate-usecases-docx.py` named its brand template by full
  filename, version included, and said `v1.00` after the master became
  `v1.01`. Every run of it died on the first `shutil.copy2`. The one golden
  case that would have said so copied the same dead name, found nothing, and
  SKIPPED. Both generators now call `docx_helpers.brand_master_template`,
  which resolves the newest by parsed version.

* `scripts/marp_render.get_workspace_defaults` resolved its source path against
  the ENGINE root alone. Four of its five prefix keys -- `context/`,
  `knowledge/`, `outputs/intel/`, `outputs/operations/` -- exist only in the
  private DATA overlay, so on the two-part topology the whole table was dead
  and every real `/marp from` fell through to "mixed" with the bare filename as
  its subtitle. The two integration tests that cover it looked for `context/`
  under the engine root too, so both skipped.

* `tests/test_recall_cross_lingual.py` resolved its embedder with a hardcoded
  `auto:11436` and the DEGRADING resolver, which falls back to a WSL-local
  daemon that does not exist here. `config/ollama-hosts.yaml` lists
  `auto:11434` first and the Windows daemon answers there. So all four tests
  skipped, and the bge-m3 cross-lingual claim they exist to falsify had never
  been measured on the only machine that can measure it.

* `tests/test_import_purity.py` excluded four skill-creator scripts over a
  `scripts` package collision. Each of the four fixed that collision itself on
  2026-08-23 with a `sys.path.insert` above its import. The exclusion outlived
  its cause and hid four scripts from the gate.

Run: python3 -m pytest tests/test_a_pin_that_outlived_the_file_it_named.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.docx_helpers import brand_master_template  # noqa: E402


# ============================================================
# The pin that outlived the file it named
# ============================================================

def _templates(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    for name in names:
        (d / name).write_bytes(b"PK\x03\x04")
    return d


def test_the_newest_version_wins(tmp_path):
    d = _templates(tmp_path,
                   "31C - Master Template (New Identity 2026 v1.00).dotx",
                   "31C - Master Template (New Identity 2026 v1.01).dotx")

    got = brand_master_template(".dotx", templates_dir=d)

    assert got.name.endswith("v1.01).dotx")


def test_the_sort_is_on_the_parsed_version_not_the_string(tmp_path):
    """`v1.9` sorts above `v1.10` as text. The whole point is that it must not."""
    d = _templates(tmp_path,
                   "31C - Master Template (New Identity 2026 v1.9).dotx",
                   "31C - Master Template (New Identity 2026 v1.10).dotx")

    assert brand_master_template(".dotx", templates_dir=d).name.endswith("v1.10).dotx")


def test_the_suffix_selects_which_master(tmp_path):
    """The two generators want different ones out of the same directory."""
    d = _templates(tmp_path,
                   "31C - Master Template (New Identity 2026 v1.01).docx",
                   "31C - Master Template (New Identity 2026 v1.01).dotx")

    assert brand_master_template(".docx", templates_dir=d).suffix == ".docx"
    assert brand_master_template(".dotx", templates_dir=d).suffix == ".dotx"


def test_an_unrelated_file_is_not_mistaken_for_a_master(tmp_path):
    d = _templates(tmp_path, "31C - Generic PP template.pptx",
                   "31c-deck-design-master.pen")

    with pytest.raises(FileNotFoundError):
        brand_master_template(".dotx", templates_dir=d)


def test_the_refusal_lists_what_the_directory_does_hold(tmp_path):
    """"Template not found" sends the reader to the wrong question."""
    d = _templates(tmp_path, "31C - Generic PP template.pptx")

    with pytest.raises(FileNotFoundError) as exc:
        brand_master_template(".dotx", templates_dir=d)

    assert "31C - Generic PP template.pptx" in str(exc.value)


def test_a_missing_directory_is_refused_and_says_so(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        brand_master_template(".dotx", templates_dir=tmp_path / "nope")

    assert "nope" in str(exc.value)


@pytest.mark.parametrize("script", ["scripts/generate-usecases-docx.py",
                                    "scripts/generate-odunone-docx.py"])
def test_no_generator_carries_a_template_version_literal(script):
    """The defect was the literal, not the number in it."""
    text = (ROOT / script).read_text(encoding="utf-8")

    assert "brand_master_template" in text, f"{script} must resolve, not name"
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue                       # the comments explain the old literal
        assert "New Identity 2026 v" not in line, (
            f"{script} still pins a template version in code: {line.strip()}")


@pytest.mark.parametrize("script", ["scripts/generate-usecases-docx.py",
                                    "scripts/generate-odunone-docx.py"])
def test_the_lookup_is_not_run_at_import(script):
    """It touches the datastore and raises; at module scope that breaks
    collection on any clone without the overlay, which is what
    tests/test_import_purity.py exists to stop."""
    tree = ast.parse((ROOT / script).read_text(encoding="utf-8"))
    for node in tree.body:
        # A def at module scope is not module-scope EXECUTION, and walking into
        # one is how this test first failed on the very call sites it is meant
        # to allow.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            assert name != "brand_master_template", (
                f"{script} resolves the template at import time")


# ============================================================
# The table that was dead on the two-part topology
# ============================================================

@pytest.fixture()
def two_roots(tmp_path, monkeypatch):
    """An engine root and a separate data root, neither of them the real ones.

    Isolated on purpose: `workspace_relative` reads a module global and a
    resolver, and a test that hands it the live roots is decided by whatever
    else was edited that hour.
    """
    from scripts import marp_render

    engine = tmp_path / "engine"
    data = tmp_path / "data"
    engine.mkdir()
    data.mkdir()
    monkeypatch.setattr(marp_render, "WORKSPACE_ROOT", engine)
    monkeypatch.setattr(marp_render, "get_data_root", lambda: data)
    return marp_render, engine, data


@pytest.mark.parametrize("rel,mode", [
    ("context/strategy.md", "light"),
    ("knowledge/odin-brain/a.md", "light"),
    ("outputs/intel/brief.md", "dark"),
    ("outputs/operations/run.md", "dark"),
    ("outputs/proposals/p.md", "mixed"),
])
def test_every_prefix_is_reachable_from_the_data_overlay(two_roots, rel, mode):
    """The finding. All five keys live in the overlay; before the fix none of
    them could ever match, because `relative_to(engine)` raised first."""
    marp, _engine, data = two_roots
    source = data / rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# t\n", encoding="utf-8")

    assert marp.workspace_relative(source) == rel
    assert marp.get_workspace_defaults(source)["mode"] == mode


def test_the_engine_root_still_wins_when_both_hold_the_path(two_roots):
    """`reference/` is the one key present in both trees."""
    marp, engine, data = two_roots
    for root in (engine, data):
        (root / "reference").mkdir(parents=True, exist_ok=True)
        (root / "reference" / "x.md").write_text("# t\n", encoding="utf-8")

    assert marp.workspace_relative(engine / "reference" / "x.md") == "reference/x.md"
    assert marp.workspace_relative(data / "reference" / "x.md") == "reference/x.md"


def test_a_path_under_neither_root_falls_through_unchanged(two_roots, tmp_path):
    marp, _engine, _data = two_roots
    stray = tmp_path / "elsewhere" / "x.md"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("# t\n", encoding="utf-8")

    assert marp.get_workspace_defaults(stray)["mode"] == "mixed"


def test_an_unresolvable_data_root_is_named_out_loud(tmp_path, monkeypatch, capsys):
    """Silently halving the lookup is the failure this function exists to end."""
    from scripts import marp_render
    from scripts.utils.paths import DataRootError

    engine = tmp_path / "engine"
    engine.mkdir()
    monkeypatch.setattr(marp_render, "WORKSPACE_ROOT", engine)

    def _boom():
        raise DataRootError("HEADING_OS_DATA names a path that does not exist")

    monkeypatch.setattr(marp_render, "get_data_root", _boom)
    (engine / "reference").mkdir()
    (engine / "reference" / "x.md").write_text("# t\n", encoding="utf-8")

    assert marp_render.workspace_relative(engine / "reference" / "x.md") == "reference/x.md"
    assert "cannot resolve the data root" in capsys.readouterr().err


# ============================================================
# The claim no machine ever measured
# ============================================================

CROSS_LINGUAL = ROOT / "tests" / "test_recall_cross_lingual.py"


def test_the_cross_lingual_test_resolves_its_embedder_the_shared_way():
    """A private copy of the host lookup is what made all four skip here.

    Read through the AST, not with `in` over the text: the file's own
    docstrings QUOTE the old resolver and the old literal port on purpose, so a
    substring search fails on the explanation of the fix.
    """
    tree = ast.parse(CROSS_LINGUAL.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = {a.name for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) for a in n.names}
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    assert "index_embed_target" in imported | names
    assert "resolve_ollama_host" not in imported | names, (
        "the degrading resolver falls back to a local daemon this workspace "
        "does not run")
    assert "auto:11436" not in literals, "no literal port; the pin file names them"


# ============================================================
# The exclusion that outlived its cause
# ============================================================

def test_import_purity_excludes_nothing():
    """Four scripts sat behind an exclusion for three days after the collision
    it named was fixed inside each of them."""
    text = (ROOT / "tests" / "test_import_purity.py").read_text(encoding="utf-8")

    assert "SKIP = {" not in text
    assert "pytest.mark.skip(" not in text


@pytest.mark.parametrize("name", ["improve_description", "package_skill",
                                  "run_eval", "run_loop"])
def test_each_formerly_excluded_script_still_pins_its_own_package(name):
    """What actually fixed them, so a revert cannot quietly re-break the four
    while this file reports the exclusion is gone."""
    path = ROOT / ".claude" / "skills" / "skill-creator" / "scripts" / f"{name}.py"
    text = path.read_text(encoding="utf-8")

    assert "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))" in text
