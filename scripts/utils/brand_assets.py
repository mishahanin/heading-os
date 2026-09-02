"""Resolve a brand asset by logical key, so the engine never spells its filename.

MEASURED 2026-09-02 by `tests/test_a_public_engine_that_named_a_private_competitor.py`:
twenty-seven places in this public repository quoted a filename that exists only
in the private data overlay. A logo, the Word master template, ten GT Standard
faces, a product document and four output examples, written out verbatim in
renderers, marp themes, prose and two test files. The operator's directive that
day was given in capitals and without qualification: everything under
`datastore/` is private, and that includes the FILENAMES, not only the contents.

The names were there for an honest reason. The code loads those files, so it had
to say which ones. This module is the seam that lets it stop: the engine asks for
a stable invented key (`logo_primary`, `word_master_template`,
`font_gt_l_medium_oblique`), and the map from key to real filename lives in the
private overlay at `<data-root>/config/brand-assets.json`, where a real filename
belongs.

Three properties, each of which cost something to learn:

*Resolved at CALL time, never at import.* `get_datastore_dir()` reads
`HEADING_OS_DATA` on every call, so it follows a caller that asks after the
environment moved. Frozen into a module-level constant it asks once, during its
own import, and stores the answer, so a test that imported the module and then
repointed the data root still read the operator's real overlay. The same
reasoning, with the same wording, guards `datastore_dir()` in
`scripts/datastore-extract.py`; it is not a style preference.

*Resolved through `get_corporate_root()`, never `get_reference_dir()`.*
`get_reference_dir()` returns the ENGINE root on the operator's own workspace,
because `reference/` is engine content. Reaching for it here would put the
manifest path inside the public repository, and calling it by mistake on
2026-09-02 wrote a private file straight into the public tree.

*A missing manifest or a missing key RAISES, named.* There is deliberately no
`*.example.json` beside this module and no hardcoded default, which breaks the
`resolve_config_with_example` convention its neighbours in `config/` follow. The
convention is right for a roster or a schedule, where a plausible stand-in lets a
public clone run. It is wrong here: a stand-in filename resolves to a file that
does not exist, `_embed_asset` reports a miss and returns "", and the renderer
exits 0 having produced a complete, plausible, entirely unbranded document. A
public clone has no brand assets to load at all, so refusing loudly is the honest
answer and the error names both the key and the manifest path that would supply
it.

Public API:
    BrandAssetError
    manifest_path() -> Path
    load_manifest() -> dict[str, str]
    brand_asset_path(key, manifest=None) -> Path
    brand_asset_name(key, manifest=None) -> str
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_NAME = "brand-assets.json"


class BrandAssetError(RuntimeError):
    """The manifest is absent, unreadable, malformed, or lacks the key asked for.

    A distinct type, not `FileNotFoundError` or `KeyError`: callers in this
    workspace routinely wrap asset work in `except OSError` or `except KeyError`
    for a genuinely missing file, and a configuration fault swallowed by one of
    those handlers reappears downstream as "brand asset not found", which sends
    the reader to the wrong question entirely.
    """


def manifest_path() -> Path:
    """Where the key-to-filename map lives, resolved fresh on every call."""
    from scripts.utils.workspace import get_corporate_root

    return get_corporate_root() / "config" / MANIFEST_NAME


def load_manifest() -> dict[str, str]:
    """The manifest as a plain key-to-relative-path dict.

    Keys beginning with an underscore are documentation inside the JSON and are
    dropped here, so a comment block can never be mistaken for an asset.

    Callers that need several assets in one pass (the marp theme substitution
    needs ten) should call this once and hand the result to `brand_asset_name`,
    rather than re-reading the file per key. It is NOT cached: a cache keyed on
    nothing freezes the first data root this process happened to see, which is
    the exact defect the call-time rule above exists to prevent.
    """
    path = manifest_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrandAssetError(
            f"the brand-asset manifest could not be read from {path}: {exc}. "
            "It lives in the private data overlay and is absent on a public "
            "clone, which has no brand assets to load either. Refusing to guess "
            "a filename."
        ) from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BrandAssetError(
            f"the brand-asset manifest at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise BrandAssetError(
            f"the brand-asset manifest at {path} must be a JSON object mapping "
            f"a logical key to a datastore-relative path, not a "
            f"{type(data).__name__}"
        )

    return {
        key: value
        for key, value in data.items()
        if not key.startswith("_") and isinstance(value, str)
    }


def _relpath(key: str, manifest: dict[str, str] | None) -> str:
    entries = load_manifest() if manifest is None else manifest
    try:
        return entries[key]
    except KeyError as exc:
        raise BrandAssetError(
            f"no brand asset is registered under the key {key!r} in "
            f"{manifest_path()}. Known keys: "
            f"{', '.join(sorted(entries)) or '(none)'}. Add the key there with "
            "the real relative path; never put the filename back in the engine."
        ) from exc


def brand_asset_path(key: str, manifest: dict[str, str] | None = None) -> Path:
    """The absolute path of the asset registered under `key`.

    Existence is NOT checked here. Callers differ on what a missing file means:
    `_embed_asset` in the doctype renderer degrades and says so, the marp theme
    falls back to a system face, and `brand_master_template` refuses. Answering
    "where would it be" separately from "is it there" keeps those decisions with
    the caller that has to make them.
    """
    from scripts.utils.workspace import get_datastore_dir

    return get_datastore_dir() / _relpath(key, manifest)


def brand_asset_name(key: str, manifest: dict[str, str] | None = None) -> str:
    """Just the filename of the asset registered under `key`.

    Wanted by callers that hold their own copy of a file the datastore also
    carries. The marp themes are the case: the licensed web fonts are dropped
    into the skill's own gitignored `themes/fonts/` directory under the names the
    manifest records, so the theme needs the basename and not the datastore path.
    """
    return Path(_relpath(key, manifest)).name
