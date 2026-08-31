"""Entry-point behaviour of `scripts/clip.py`.

The defect these bind, measured 2026-08-30 on WSL2 with Pillow 12.3.0 and
neither `wl-paste` nor `xclip` installed:

    Traceback (most recent call last):
      File "scripts/clip.py", line 102, in <module>
        sys.exit(main())
      File "scripts/clip.py", line 67, in main
        img = ImageGrab.grabclipboard()
      File ".../PIL/ImageGrab.py", line 205, in grabclipboard
        raise NotImplementedError(msg)
    NotImplementedError: wl-paste or xclip is required for
    ImageGrab.grabclipboard() on Linux

`grabclipboard()` RAISES on a grabber-less platform rather than returning None.
The raise lands before `img` is bound, so the file's whole "PIL found nothing"
recovery path was unreachable by construction. The tests below therefore cover
the entry, not the recovery: what the operator sees, and that the recovery is
now genuinely reached.

Nothing here touches the network or the operator's data overlay -- every test
redirects `get_outputs_dir` at a `tmp_path` and asserts the write landed there.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import clip  # noqa: E402

# The verbatim message Pillow 12.3.0 raises with. Kept as a literal so a Pillow
# upgrade that rewords it shows up here rather than silently weakening the
# assertion that the operator is shown PIL's own reason.
PIL_NO_GRABBER = "wl-paste or xclip is required for ImageGrab.grabclipboard() on Linux"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    """Point clip.py's only data-root call at tmp_path.

    `get_outputs_dir()` resolves through `get_data_root()`, which on this
    machine is the operator's live private overlay. Redirecting the seam (rather
    than trusting an env var to be read at the right moment) means no test here
    can name a real path even if the resolution rules change.
    """
    target = tmp_path / "outputs"
    monkeypatch.setattr(clip, "get_outputs_dir", lambda: target)
    return target


def _no_grabbers(monkeypatch):
    """Neither CLI fallback binary is on PATH, whatever this host actually has."""
    monkeypatch.setattr(clip.shutil, "which", lambda name: None)


def _only_xclip(monkeypatch, payload=PNG_BYTES, returncode=0):
    """xclip is on PATH and produces `payload`; wl-paste is absent.

    This is the case ON the line, not a hypothetical: PIL takes xclip only for
    an x11 session, so a host with WAYLAND_DISPLAY set and only xclip installed
    makes PIL raise while xclip itself works perfectly.
    """
    monkeypatch.setattr(
        clip.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None
    )

    def fake_run(args, **kwargs):
        assert args[0] == "xclip", f"expected the xclip fallback, got {args!r}"
        kwargs["stdout"].write(payload)
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(clip.subprocess, "run", fake_run)


def _raises_no_grabber(monkeypatch):
    def boom():
        raise NotImplementedError(PIL_NO_GRABBER)

    monkeypatch.setattr(clip.ImageGrab, "grabclipboard", boom)


# ============================================================
# The reported failure
# ============================================================


def test_no_grabber_exits_nonzero_without_a_traceback(out_dir, monkeypatch, capsys):
    _raises_no_grabber(monkeypatch)
    _no_grabbers(monkeypatch)

    rc = clip.main()

    assert rc != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "NotImplementedError" not in err


def test_no_grabber_prints_one_plain_sentence_naming_the_remedy(
    out_dir, monkeypatch, capsys
):
    _raises_no_grabber(monkeypatch)
    _no_grabbers(monkeypatch)

    clip.main()

    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one sentence on stderr, got {lines!r}"
    # PIL's own reason, so the sentence cannot drift from what was checked.
    assert PIL_NO_GRABBER in lines[0]
    # ...and the thing the operator can actually do about it.
    assert "xclip" in lines[0]
    assert "wl-clipboard" in lines[0]


def test_no_grabber_leaves_no_stray_file_behind(out_dir, monkeypatch, capsys):
    _raises_no_grabber(monkeypatch)
    _no_grabbers(monkeypatch)

    clip.main()

    assert not (out_dir / "clipboard" / "clip.png").exists()


# ============================================================
# The recovery path the raise used to make unreachable
# ============================================================


def test_the_linux_fallback_now_runs_when_pil_refuses(out_dir, monkeypatch, capsys):
    """The whole point of catching at the entry.

    Before the fix this could not happen at any input: `grabclipboard()` raised,
    and the fallback loop inspects a variable that the raise never bound.
    """
    _raises_no_grabber(monkeypatch)
    _only_xclip(monkeypatch)

    rc = clip.main()

    saved = out_dir / "clipboard" / "clip.png"
    assert rc == 0
    assert saved.read_bytes() == PNG_BYTES
    assert capsys.readouterr().out.strip() == str(saved)


def test_a_fallback_that_produces_nothing_cleans_up_and_reports(
    out_dir, monkeypatch, capsys
):
    """xclip runs but yields an empty file: no success, no zero-byte litter."""
    _raises_no_grabber(monkeypatch)
    _only_xclip(monkeypatch, payload=b"", returncode=0)

    rc = clip.main()

    assert rc != 0
    assert not (out_dir / "clipboard" / "clip.png").exists()
    assert PIL_NO_GRABBER in capsys.readouterr().err


# ============================================================
# Everything the fix must not have changed
# ============================================================


def test_happy_path_still_saves_the_clipboard_image(out_dir, monkeypatch, capsys):
    image = Image.new("RGB", (3, 2), "red")
    monkeypatch.setattr(clip.ImageGrab, "grabclipboard", lambda: image)

    rc = clip.main()

    saved = out_dir / "clipboard" / "clip.png"
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(saved)
    with Image.open(saved) as reopened:
        assert reopened.size == (3, 2)
        assert reopened.format == "PNG"


def test_an_empty_clipboard_keeps_its_own_distinct_message(out_dir, monkeypatch, capsys):
    """PIL answered "nothing here"; that is not the grabber-missing sentence.

    Without this, setting the no-grabber flag unconditionally would still pass
    every test above.
    """
    monkeypatch.setattr(clip.ImageGrab, "grabclipboard", lambda: None)
    _no_grabbers(monkeypatch)

    rc = clip.main()

    err = capsys.readouterr().err.strip()
    assert rc != 0
    assert err == "No image on clipboard."
    assert PIL_NO_GRABBER not in err


def test_a_clipboard_holding_file_paths_is_still_refused(out_dir, monkeypatch, capsys):
    monkeypatch.setattr(
        clip.ImageGrab, "grabclipboard", lambda: ["/some/where/a.png", "/some/where/b.png"]
    )

    rc = clip.main()

    err = capsys.readouterr().err
    assert rc != 0
    assert "2 file path(s)" in err
    assert not (out_dir / "clipboard" / "clip.png").exists()


# ============================================================
# The handler is narrow, and stays narrow
# ============================================================


def test_an_unexpected_pil_failure_still_surfaces(out_dir, monkeypatch):
    """Only NotImplementedError is absorbed. Anything else keeps its traceback."""

    def boom():
        raise OSError("the clipboard helper died mid-read")

    monkeypatch.setattr(clip.ImageGrab, "grabclipboard", boom)

    with pytest.raises(OSError, match="died mid-read"):
        clip.main()


def _handled_exception_names(node):
    for handler in (n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)):
        if handler.type is None:
            yield "<bare except>"
            continue
        parts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        for part in parts:
            yield ast.unparse(part)


def test_main_catches_only_the_specific_pillow_error(out_dir):
    """An AST binder, because a regex over the source is blind through an alias.

    `except Exception` around the grab would keep every behavioural test above
    green while swallowing failures the operator needs to see.
    """
    source = Path(clip.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )

    assert sorted(_handled_exception_names(main_fn)) == ["NotImplementedError"]


def test_the_module_has_no_bare_or_blanket_handler():
    source = Path(clip.__file__).read_text(encoding="utf-8")
    caught = sorted(set(_handled_exception_names(ast.parse(source))))

    assert "<bare except>" not in caught
    assert "Exception" not in caught
    assert "BaseException" not in caught
