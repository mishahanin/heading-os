"""The seam that took private filenames out of the public engine.

MEASURED 2026-09-02 by `tests/test_a_public_engine_that_named_a_private_competitor.py`:
this repository is public, and twenty-seven of its files quoted a filename that
exists only in the private data overlay. A logo, the Word master template, ten GT
Standard faces, a product document and four rendered examples, spelled verbatim
in renderers, marp themes, prose and two test files. The operator's directive
that day was unqualified: everything under `datastore/` is private, filenames
included.

`scripts/utils/brand_assets.py` is the answer. Engine code asks for an invented
key and the map from key to real filename lives in the overlay. That guard proves
the names are GONE; this file proves the replacement WORKS, which is the half a
grep cannot establish.

Four properties, each with a specific failure behind it:

* **A missing manifest refuses, by name.** Every neighbour of this manifest in
  `config/` ships a `scripts/*.example.json` twin so a public clone can run on a
  plausible stand-in. Here that would be a disaster in slow motion: a stand-in
  filename resolves to a file that is not there, `_embed_asset` returns "" and
  says so, and the renderer exits 0 having produced a complete, plausible,
  entirely unbranded document. That exact shape already happened once, with the
  Inter fallback faces, and went unnoticed across every Russian render.

* **A missing key refuses, by name, and lists what it does know.** "Brand asset
  not found" sends the reader to the datastore; the fault is in the manifest.

* **A lookup against a scratch manifest resolves.** Without this the two refusals
  above are satisfied by a function that only ever refuses.

* **Resolution happens at CALL time, not at import.** A module-level constant
  asks `get_datastore_dir()` once, during its own import, and stores the answer,
  so a test that imported the module and then repointed `HEADING_OS_DATA` still
  read the operator's real overlay. `datastore_dir()` in
  `scripts/datastore-extract.py` carries the same warning for the same reason.

Run: .venv/bin/python -m pytest tests/test_a_public_renderer_that_spelled_a_private_filename.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.brand_assets import (  # noqa: E402
    BrandAssetError,
    brand_asset_name,
    brand_asset_path,
    load_manifest,
    manifest_path,
)

# Invented, and it has to stay that way: this file is in the public repository
# and is exactly the kind of place a real filename gets copied into as "the
# handiest example".
SCRATCH = {
    "logo_primary": "brand/assets/logos/Northwind_Mark_Indigo.png",
    "font_gt_m_medium": "brand/fonts/Kestrel/Kestrel-Text-Medium.woff2",
}


def _overlay(tmp_path: Path, manifest) -> Path:
    """A scratch data root, with `manifest` written into it when not None."""
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    if manifest is not None:
        (data / "config" / "brand-assets.json").write_text(
            json.dumps(manifest) if not isinstance(manifest, str) else manifest,
            encoding="utf-8")
    return data


@pytest.fixture()
def overlay(tmp_path, monkeypatch):
    data = _overlay(tmp_path, SCRATCH)
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    return data


# ============================================================
# It refuses when the manifest is absent
# ============================================================

def test_a_missing_manifest_refuses_and_names_the_path(tmp_path, monkeypatch):
    """The state of every public clone. It must not resolve to anything."""
    data = _overlay(tmp_path, None)
    monkeypatch.setenv("HEADING_OS_DATA", str(data))

    with pytest.raises(BrandAssetError) as exc:
        load_manifest()

    assert str(data / "config" / "brand-assets.json") in str(exc.value)


def test_a_missing_manifest_refuses_the_lookups_too(tmp_path, monkeypatch):
    """The refusal has to reach the functions callers actually use.

    A `load_manifest` that raised while `brand_asset_path` quietly returned
    something would leave the hole exactly where it was.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(_overlay(tmp_path, None)))

    for call in (lambda: brand_asset_path("logo_primary"),
                 lambda: brand_asset_name("logo_primary")):
        with pytest.raises(BrandAssetError):
            call()


def test_a_malformed_manifest_refuses_rather_than_reading_as_empty(tmp_path,
                                                                  monkeypatch):
    """A truncated write must not present as "no assets registered"."""
    monkeypatch.setenv("HEADING_OS_DATA", str(_overlay(tmp_path, '{"logo_primary":')))

    with pytest.raises(BrandAssetError) as exc:
        load_manifest()

    assert "not valid JSON" in str(exc.value)


def test_the_refusal_is_not_an_oserror_or_a_keyerror(tmp_path, monkeypatch):
    """Callers here wrap asset work in `except OSError` and `except KeyError`.

    A configuration fault caught by a handler written for a missing file
    reappears downstream as "brand asset not found", which names the wrong cause.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(_overlay(tmp_path, None)))

    with pytest.raises(BrandAssetError) as exc:
        load_manifest()

    assert not isinstance(exc.value, (OSError, KeyError))


# ============================================================
# It refuses an unknown key
# ============================================================

def test_a_missing_key_refuses_and_names_the_key(overlay):
    with pytest.raises(BrandAssetError) as exc:
        brand_asset_path("logo_that_was_never_registered")

    assert "logo_that_was_never_registered" in str(exc.value)


def test_a_missing_key_lists_the_keys_it_does_know(overlay):
    """Otherwise the reader cannot tell a typo from an unregistered asset."""
    with pytest.raises(BrandAssetError) as exc:
        brand_asset_name("font_gt_m_medum")

    assert "font_gt_m_medium" in str(exc.value)


def test_a_documentation_key_is_not_an_asset(tmp_path, monkeypatch):
    """The real manifest opens with an `_comment` block explaining itself.

    Underscore-prefixed keys are dropped, so a comment can never be handed back
    as a path, and a JSON list value cannot either.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(_overlay(
        tmp_path, {"_comment": ["why this file exists"], "logo_primary": "a/b.png"})))

    assert load_manifest() == {"logo_primary": "a/b.png"}


# ============================================================
# It resolves what IS registered
# ============================================================

def test_a_registered_key_resolves_under_the_datastore(overlay):
    got = brand_asset_path("logo_primary")

    assert got == overlay / "datastore" / SCRATCH["logo_primary"]


def test_a_registered_key_yields_just_the_filename(overlay):
    """What the marp themes need: they hold their own copy of the file."""
    assert brand_asset_name("font_gt_m_medium") == "Kestrel-Text-Medium.woff2"


def test_the_path_is_returned_whether_or_not_the_file_is_there(overlay):
    """Deliberate. Callers disagree on what a missing file means.

    `_embed_asset` degrades and warns, the marp theme falls back to a system
    face, `brand_master_template` refuses. Answering "where would it be"
    separately from "is it there" leaves that decision with the caller.
    """
    assert not brand_asset_path("logo_primary").exists()


def test_a_caller_can_pass_the_manifest_it_already_loaded(overlay):
    """One read per render, not one per placeholder. The marp substitution
    resolves ten faces from a single load."""
    manifest = load_manifest()

    assert brand_asset_name("logo_primary", manifest) == "Northwind_Mark_Indigo.png"


# ============================================================
# It resolves at call time, not at import
# ============================================================

def test_the_manifest_path_follows_the_environment_after_import(tmp_path,
                                                                monkeypatch):
    """The property a module-level constant silently destroys.

    Both roots are asked for AFTER this module and `brand_assets` are already
    imported. A frozen constant answers the same path twice, and the second
    answer is the operator's real overlay.
    """
    first = _overlay(tmp_path / "one", SCRATCH)
    second = _overlay(tmp_path / "two", SCRATCH)

    monkeypatch.setenv("HEADING_OS_DATA", str(first))
    assert manifest_path() == first / "config" / "brand-assets.json"

    monkeypatch.setenv("HEADING_OS_DATA", str(second))
    assert manifest_path() == second / "config" / "brand-assets.json"


def test_a_second_root_with_a_different_manifest_is_actually_read(tmp_path,
                                                                 monkeypatch):
    """Stronger than the path check: the CONTENTS must follow too.

    A resolver that recomputed the path but cached the parsed manifest would
    pass the test above and still hand back the first root's filenames.
    """
    first = _overlay(tmp_path / "one", {"logo_primary": "brand/one.png"})
    second = _overlay(tmp_path / "two", {"logo_primary": "brand/two.png"})

    monkeypatch.setenv("HEADING_OS_DATA", str(first))
    assert brand_asset_name("logo_primary") == "one.png"

    monkeypatch.setenv("HEADING_OS_DATA", str(second))
    assert brand_asset_name("logo_primary") == "two.png"


def test_the_module_binds_no_resolved_path_at_import():
    """Read the module, not its behaviour: a constant added later would revive
    the defect without failing anything above, because the tests here all import
    it once and the freeze would happen before the first monkeypatch."""
    import ast

    source = (ROOT / "scripts" / "utils" / "brand_assets.py").read_text(
        encoding="utf-8")
    resolvers = {"get_corporate_root", "get_datastore_dir", "get_data_root",
                 "manifest_path", "load_manifest"}

    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                      # a def is not module-scope EXECUTION
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            assert name not in resolvers, (
                f"brand_assets.py calls {name}() at import time; the data root "
                "would freeze to whatever it was during that import")
