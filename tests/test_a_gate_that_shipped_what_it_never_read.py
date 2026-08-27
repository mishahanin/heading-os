"""Shard 03-p4: a gate that exited 0 over files it could not read, a regex that
needed a key after it, and two crashes on ordinary inputs.

* ``content-guard`` warned on stderr about an engine-routed file it could not
  decode, and then returned 0. The exit code is what CI consumes, so "clean"
  shipped over a file nobody had looked at - the one outcome the gate exists to
  prevent, and the one its own comment says was already fixed. Making it refuse
  immediately surfaced a real case: ``.bin`` was missing from the binary-suffix
  list, so a committed test fixture had been silently unscanned on every sweep.

* ``context-floor-audit.DESCRIPTION_RE`` required a top-level key AFTER the
  description. YAML key order is free, so a description written last matched
  nothing: that skill measured 0 description bytes and its whole frontmatter
  landed in the "other" row - moving both of the two totals the script exists
  to separate.

* ``context-freshness.get_freshness`` matched the SHAPE of a date and then let
  ``date.fromisoformat`` validate the value with nothing catching it. One
  hand-typed ``2026-13-40`` aborted the listing partway and made every other
  file's freshness unreportable.

* ``composite-logo`` flattened only mode RGBA before writing a JPEG, so a GIF
  base (mode P) or a grey-plus-alpha PNG (mode LA) raised. It also let
  ``int()`` truncate a scaled logo to zero pixels, which ``Image.resize``
  refuses.

Run: python3 -m pytest tests/test_a_gate_that_shipped_what_it_never_read.py
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import data_root_is_demo  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


floor = _load("context_floor_under_test", "scripts/context-floor-audit.py")
fresh = _load("context_freshness_under_test", "scripts/context-freshness.py")
GUARD = ROOT / "scripts" / "content-guard.py"


# ============================================================
# The gate that passed over what it could not read
# ============================================================

def _guard(*args, cwd=None):
    return subprocess.run([sys.executable, str(GUARD), *args],
                          capture_output=True, text=True, timeout=300,
                          check=False, cwd=cwd)


def test_an_undecodable_engine_file_fails_the_gate(tmp_path):
    """Unverified is not clean, and the exit code is what a gate IS."""
    bad = ROOT / "_content_guard_probe.md"
    bad.write_bytes(b"caf\xe9\n")
    try:
        proc = _guard("--files", "_content_guard_probe.md")
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "REFUSED" in proc.stderr
    finally:
        bad.unlink()


def test_the_refusal_names_the_file(tmp_path):
    bad = ROOT / "_content_guard_probe2.md"
    bad.write_bytes(b"\xff\xfe not utf-8\n")
    try:
        proc = _guard("--files", "_content_guard_probe2.md")
        assert "_content_guard_probe2.md" in proc.stderr
    finally:
        bad.unlink()


def test_a_readable_file_is_still_clean(tmp_path):
    ok = ROOT / "_content_guard_probe3.md"
    ok.write_text("Ordinary prose with nothing private in it.\n", encoding="utf-8")
    try:
        proc = _guard("--files", "_content_guard_probe3.md")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "clean" in proc.stdout
    finally:
        ok.unlink()


@pytest.mark.skipif(
    data_root_is_demo(),
    reason=(
        "demo data root: get_data_root() falls back to <engine>/examples, so "
        "build_denylist harvests the shipped fictional CRM contact and the gate "
        "flags the engine against the demo corpus the engine itself ships. What "
        "is NOT measured here is the real-entity sweep of the whole engine "
        "surface; it needs a private overlay to have any real entity in it."
    ),
)
def test_the_whole_engine_surface_passes():
    """The gate must be green on this repository, or the refusal is unusable.

    This is the test that found `.bin` missing from the binary-suffix list:
    with the refusal in place, a committed fixture the sweep had always been
    skipping stopped being invisible.
    """
    proc = _guard("--all")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_binary_suffix_list_covers_the_committed_fixture():
    """Asked of the selector, not of the CLI's source text.

    This grepped `content-guard.py` for the literal `".bin"` until the suffix
    list moved into `scripts/utils/engine_guard.py`, where both content gates now
    share it. The grep then failed over a move that changed no behaviour, which
    is the giveaway: it was measuring where the characters sat, not what the gate
    does with the file.
    """
    from scripts.utils.engine_guard import BINARY_SUFFIXES, engine_text_files

    assert ".bin" in BINARY_SUFFIXES
    fixture = ROOT / "tests" / "integration" / "fixtures" / "unsupported.bin"
    assert fixture.is_file(), "the fixture this covers must still exist"
    rel = "tests/integration/fixtures/unsupported.bin"
    assert engine_text_files(ROOT, [rel]) == []


def test_the_clean_line_no_longer_carries_an_unscanned_count():
    """The two states are now exclusive: it refuses, or it is clean."""
    src = GUARD.read_text(encoding="utf-8")
    assert "unreadable and NOT scanned" not in src


# ============================================================
# The description that had to be followed by another key
# ============================================================

def _skill(tmp_path: Path, frontmatter: str) -> Path:
    d = tmp_path / ".claude" / "skills" / "demo"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text(f"---\n{frontmatter}\n---\n\nBody text.\n", encoding="utf-8")
    return p


def test_a_description_written_last_is_still_measured():
    """The reported reproduction: no following key, so no match at all."""
    fm = "name: demo\ndescription: a long folded description of the skill"
    found = floor.DESCRIPTION_RE.search(fm)
    assert found is not None
    assert "long folded description" in found.group(1)


def test_a_description_in_the_middle_is_unchanged():
    fm = "name: demo\ndescription: the description\nversion: 1.0"
    found = floor.DESCRIPTION_RE.search(fm)
    assert found is not None
    assert found.group(1).strip() == "the description"


def test_a_multi_line_description_written_last_is_captured_whole():
    fm = ("name: demo\ndescription: >\n  first line\n  second line")
    found = floor.DESCRIPTION_RE.search(fm)
    assert found is not None
    assert "first line" in found.group(1) and "second line" in found.group(1)


def test_the_capture_still_stops_at_the_next_key():
    """The lookahead must not swallow the rest of the frontmatter."""
    fm = "description: only this\nname: demo\nversion: 1.0"
    found = floor.DESCRIPTION_RE.search(fm)
    assert "demo" not in found.group(1)
    assert "1.0" not in found.group(1)


def test_a_frontmatter_with_no_description_still_matches_nothing():
    assert floor.DESCRIPTION_RE.search("name: demo\nversion: 1.0") is None


# ============================================================
# The date that matched the shape and not the calendar
# ============================================================

@pytest.mark.parametrize("marker", [
    "2026-13-40",   # the reported reproduction
    "2026-00-01",
    "2026-02-30",
    "9999-99-99",
])
def test_an_impossible_date_does_not_abort_the_listing(tmp_path, marker):
    f = tmp_path / "note.md"
    f.write_text(f"> Last verified: {marker}\n\nBody.\n", encoding="utf-8")
    date_str, age = fresh.get_freshness(f)
    assert date_str == marker
    assert age is None, "unusable, and said so, rather than raising"


def test_a_real_date_still_reads(tmp_path):
    f = tmp_path / "note.md"
    # The module's own clock source, not libc's: DTZ011 bans a bare
    # today-in-local-time call here for the same reason the scripts avoid it.
    today = datetime.now(fresh.get_default_tz()).date().isoformat()
    f.write_text(f"> Last verified: {today}\n\nBody.\n", encoding="utf-8")
    date_str, age = fresh.get_freshness(f)
    assert date_str == today and age == 0


def test_a_file_with_no_marker_is_still_the_none_case(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# Heading\n\nNo marker here.\n", encoding="utf-8")
    assert fresh.get_freshness(f) == (None, None)


def test_the_listing_names_the_bad_marker_and_keeps_going(tmp_path, monkeypatch,
                                                          capsys):
    """Through `check_all`, not through the parser.

    A mutation removing the bad-marker row survived the first pass, because
    nothing asserted the LISTING behaviour - only the return shape. The row
    exists so the operator learns which file to fix.
    """
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "broken.md").write_text("> Last verified: 2026-13-40\n", encoding="utf-8")
    today = datetime.now(fresh.get_default_tz()).date().isoformat()
    (ctx / "good.md").write_text(f"> Last verified: {today}\n", encoding="utf-8")
    monkeypatch.setattr(fresh, "CONTEXT_DIR", ctx)

    fresh.check_all()
    out = capsys.readouterr().out
    assert "broken.md" in out and "2026-13-40" in out
    assert "good.md" in out, "one bad marker must not end the listing"
    assert "Unusable marker" in out


def test_a_bad_marker_is_not_folded_into_no_marker(tmp_path, monkeypatch, capsys):
    """There IS a marker; calling it absent would hide the thing to repair."""
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "broken.md").write_text("> Last verified: 2026-02-30\n", encoding="utf-8")
    monkeypatch.setattr(fresh, "CONTEXT_DIR", ctx)

    fresh.check_all()
    out = capsys.readouterr().out
    assert "No marker" not in out


def test_the_three_return_shapes_are_documented():
    doc = fresh.get_freshness.__doc__
    assert "(date_str, None)" in doc
    assert "(None, None)" in doc


# ============================================================
# The image modes JPEG cannot write, and the zero-pixel resize
# ============================================================

@pytest.fixture()
def pil():
    return pytest.importorskip("PIL.Image")


def _run_composite(tmp_path, base, logo, out):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "composite-logo.py"),
         str(base), str(logo), str(out)],
        capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.parametrize("base_mode,base_name", [
    ("P", "base.gif"),      # a GIF opens in palette mode
    ("LA", "base.png"),     # grey plus alpha
    ("RGBA", "base2.png"),  # the one case that always worked
])
def test_a_base_image_jpeg_cannot_write_is_flattened(pil, tmp_path, base_mode,
                                                     base_name):
    base = tmp_path / base_name
    pil.new(base_mode, (400, 300), 0).save(base)
    logo = tmp_path / "logo.png"
    pil.new("RGBA", (100, 40), (255, 0, 0, 255)).save(logo)
    out = tmp_path / "out.jpg"

    proc = _run_composite(tmp_path, base, logo, out)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    assert pil.open(out).mode == "RGB"


def test_a_png_output_keeps_its_mode(pil, tmp_path):
    """The flatten is scoped to JPEG; a PNG must not be converted."""
    base = tmp_path / "base.png"
    pil.new("RGBA", (400, 300), (0, 0, 0, 255)).save(base)
    logo = tmp_path / "logo.png"
    pil.new("RGBA", (100, 40), (255, 0, 0, 255)).save(logo)
    out = tmp_path / "out.png"

    proc = _run_composite(tmp_path, base, logo, out)
    assert proc.returncode == 0, proc.stderr
    assert pil.open(out).mode == "RGBA"


def test_a_base_narrower_than_seven_pixels_does_not_crash(pil, tmp_path):
    """`int(6 * 0.15)` is 0, and `resize` refuses a zero dimension."""
    base = tmp_path / "base.png"
    pil.new("RGBA", (6, 400), (0, 0, 0, 255)).save(base)
    logo = tmp_path / "logo.png"
    pil.new("RGBA", (100, 40), (255, 0, 0, 255)).save(logo)
    out = tmp_path / "out.png"

    proc = _run_composite(tmp_path, base, logo, out)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()


def test_a_banner_logo_on_a_small_base_does_not_crash(pil, tmp_path):
    """The other zero: a wide logo scaled down to under one pixel tall."""
    base = tmp_path / "base.png"
    pil.new("RGBA", (100, 100), (0, 0, 0, 255)).save(base)
    logo = tmp_path / "logo.png"
    pil.new("RGBA", (2000, 5), (255, 0, 0, 255)).save(logo)
    out = tmp_path / "out.png"

    proc = _run_composite(tmp_path, base, logo, out)
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()


def test_an_ordinary_composite_still_places_the_logo(pil, tmp_path):
    base = tmp_path / "base.png"
    pil.new("RGBA", (800, 600), (0, 0, 0, 255)).save(base)
    logo = tmp_path / "logo.png"
    pil.new("RGBA", (200, 80), (255, 0, 0, 255)).save(logo)
    out = tmp_path / "out.png"

    proc = _run_composite(tmp_path, base, logo, out)
    assert proc.returncode == 0, proc.stderr
    assert "size: 120x48" in proc.stdout, proc.stdout
