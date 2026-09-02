#!/usr/bin/env python3
"""Shard scripts-05-p1 finding 2: `scripts/design-engine.py` overwrote its own output.

`_unique_path` exists, and its docstring says why: `_timestamp()` has one-second
resolution, so two runs inside the same second produced the same default name and
the second silently overwrote the first while printing "Saved" over a path whose
earlier bytes were gone.

The guard ran against the UN-NUMBERED base name. `cmd_generate` built
`design-<ts>.png`, asked `_unique_path` whether it was free, and handed the answer
to `_save_outputs` -- which, whenever more than one URL came back, wrote only
`design-<ts>_1.png` and `design-<ts>_2.png` and never touched the name that had
been checked. So a second `--count 2` run in the same second found the base name
free, allocated no `-2` suffix, and wrote over the first run's two images: the
exact data loss the guard was written to prevent, reachable through the gap
between the name checked and the names written.

The single-output path was never affected, and a path the operator typed with
`-o` is still theirs to overwrite. Both are asked below, because a fix that
uniquified everything would be a different bug.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENGINE = ROOT / "scripts" / "design-engine.py"

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def de():
    """Load design-engine.py as a module (hyphen in filename)."""
    spec = importlib.util.spec_from_file_location("design_engine_shard", str(ENGINE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def download(monkeypatch, de):
    """Replace the network with bytes keyed on the URL.

    `_save_outputs` is the whole subject here, so the transport is stubbed and
    the PNG magic is real: `_sniff_ext` reads it, and a stub returning arbitrary
    bytes would send every destination down the rename branch instead.
    """
    def _stub(url, dest):
        body = PNG + url.encode()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return body

    monkeypatch.setattr(de, "_download", _stub)
    return _stub


def _saved_bodies(paths):
    return {p.name: p.read_bytes() for p in paths}


def test_a_second_same_second_multi_output_run_does_not_overwrite_the_first(
        tmp_path, de, download):
    """The defect, at the size it actually occurred: two images, twice.

    Both runs are handed the SAME base path, which is what two invocations
    inside one wall-clock second get from `_timestamp()` after `_unique_path`
    has found the un-numbered name free. Nothing is monkeypatched to force the
    collision; the base name is simply reused, exactly as the clock reuses it.
    """
    base = tmp_path / "design-20260902-120000.png"

    first = de._save_outputs(["https://a/1", "https://a/2"], base,
                             name_from_bytes=True)
    first_bodies = _saved_bodies(first)
    assert len(first) == 2

    second = de._save_outputs(["https://b/1", "https://b/2"], base,
                              name_from_bytes=True)
    assert len(second) == 2

    assert set(first) & set(second) == set(), (
        "the second run reused a path the first run wrote: "
        f"{sorted(p.name for p in set(first) & set(second))}")
    for path, body in first_bodies.items():
        assert (tmp_path / path).read_bytes() == body, (
            f"{path} no longer holds the bytes the first run saved")
    assert len({p.name for p in first + second}) == 4, (
        "four images were downloaded and fewer than four files survive")


def test_every_returned_path_is_the_path_that_was_written(tmp_path, de, download):
    """The half a name-collision check alone would miss.

    `_save_outputs` returns what it claims to have saved, and the caller prints
    that list and bills against it. A uniquifier applied to the name but not to
    the write would report `_1-2.png` while the bytes went to `_1.png`.
    """
    base = tmp_path / "design-20260902-120000.png"
    de._save_outputs(["https://a/1", "https://a/2"], base, name_from_bytes=True)
    second = de._save_outputs(["https://b/1", "https://b/2"], base,
                              name_from_bytes=True)
    for path in second:
        assert path.is_file(), f"{path} was reported as saved and is not on disk"
        assert path.read_bytes().startswith(PNG)


def test_an_operator_named_output_is_still_theirs_to_overwrite(tmp_path, de,
                                                               download):
    """`-o` sets `name_from_bytes=False`, and `_unique_path`'s docstring says a
    path the operator typed is not renamed behind their back. A fix that
    uniquified unconditionally would break the one guarantee `-o` carries.
    """
    base = tmp_path / "mine.png"
    first = de._save_outputs(["https://a/1", "https://a/2"], base,
                             name_from_bytes=False)
    second = de._save_outputs(["https://b/1", "https://b/2"], base,
                              name_from_bytes=False)
    assert [p.name for p in first] == ["mine_1.png", "mine_2.png"]
    assert [p.name for p in second] == ["mine_1.png", "mine_2.png"]


def test_a_single_output_run_keeps_the_name_the_caller_uniquified(tmp_path, de,
                                                                  download):
    """One URL writes the un-numbered base name, which `cmd_generate` already
    put through `_unique_path`. Numbering it, or uniquifying it twice, would
    move the artifact away from the path the caller announced.
    """
    base = tmp_path / "design-20260902-120000.png"
    saved = de._save_outputs(["https://a/only"], base, name_from_bytes=True)
    assert saved == [base]


def test_a_first_multi_output_run_uses_the_plain_numbered_names(tmp_path, de,
                                                                download):
    """On a free directory nothing is suffixed, or every default name grows one."""
    base = tmp_path / "design-20260902-120000.png"
    saved = de._save_outputs(["https://a/1", "https://a/2"], base,
                             name_from_bytes=True)
    assert [p.name for p in saved] == ["design-20260902-120000_1.png",
                                       "design-20260902-120000_2.png"]
