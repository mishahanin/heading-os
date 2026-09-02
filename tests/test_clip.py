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


def _only_wlpaste(monkeypatch, payload=PNG_BYTES, returncode=0):
    """wl-paste is on PATH and produces `payload`; xclip is absent.

    The Wayland half of the pair, and nothing exercised it until 2026-09-01.
    MEASURED that day, with `_grab_via_wlpaste` mutated three ways in turn -
    an unconditional `return False`, an unconditional `return True` that skips
    the return-code and size checks, and dropping the function from `main()`'s
    fallback tuple altogether - all three left the whole tree green, because
    both existing helpers make `shutil.which("wl-paste")` answer None and the
    body returns at its first line.
    """
    monkeypatch.setattr(
        clip.shutil,
        "which",
        lambda name: "/usr/bin/wl-paste" if name == "wl-paste" else None,
    )

    def fake_run(args, **kwargs):
        assert args[0] == "wl-paste", f"expected the wl-paste fallback, got {args!r}"
        kwargs["stdout"].write(payload)
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(clip.subprocess, "run", fake_run)


def _both_grabbers(monkeypatch, calls):
    """Both binaries present. Records which one main() reached, in order."""
    monkeypatch.setattr(
        clip.shutil, "which", lambda name: f"/usr/bin/{name}"
    )

    def fake_run(args, **kwargs):
        calls.append(args[0])
        kwargs["stdout"].write(PNG_BYTES)
        return subprocess.CompletedProcess(args, 0)

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


def test_the_wayland_fallback_runs_when_pil_refuses(out_dir, monkeypatch, capsys):
    """The other half of the pair. PIL takes wl-paste only for a wayland session,
    so an x11 session with only wl-paste installed lands here and wl-paste works."""
    _raises_no_grabber(monkeypatch)
    _only_wlpaste(monkeypatch)

    rc = clip.main()

    saved = out_dir / "clipboard" / "clip.png"
    assert rc == 0
    assert saved.read_bytes() == PNG_BYTES
    assert capsys.readouterr().out.strip() == str(saved)


def test_a_wayland_fallback_that_produces_nothing_cleans_up_and_reports(
    out_dir, monkeypatch, capsys
):
    """wl-paste runs but yields an empty file: no success, no zero-byte litter.

    The case ON the line for `_grab_via_wlpaste`'s `st_size > 0`. Without it the
    hook returns success over an empty PNG and prints its path.
    """
    _raises_no_grabber(monkeypatch)
    _only_wlpaste(monkeypatch, payload=b"")

    rc = clip.main()

    assert rc != 0
    assert not (out_dir / "clipboard" / "clip.png").exists()
    assert PIL_NO_GRABBER in capsys.readouterr().err


def test_a_wayland_fallback_that_exits_nonzero_is_not_taken_as_success(
    out_dir, monkeypatch, capsys
):
    """wl-paste writes bytes and still exits non-zero (it prints its refusal on
    stderr and can leave partial output). The return code has to bind."""
    _raises_no_grabber(monkeypatch)
    _only_wlpaste(monkeypatch, payload=b"partial", returncode=1)

    rc = clip.main()

    assert rc != 0
    assert capsys.readouterr().out.strip() == "", (
        "a failed grab printed a path as though it had saved something"
    )


def test_wl_paste_is_tried_before_xclip(out_dir, monkeypatch, capsys):
    """Order, asserted rather than assumed.

    On a host carrying both, wl-paste is the one that talks to the compositor
    actually holding the selection. `main()` names the tuple
    `(_grab_via_wlpaste, _grab_via_xclip)`, and dropping the first element left
    every other test in this file green.
    """
    _raises_no_grabber(monkeypatch)
    calls: list[str] = []
    _both_grabbers(monkeypatch, calls)

    rc = clip.main()

    assert rc == 0
    assert calls[:1] == ["wl-paste"], f"the fallbacks ran in the order {calls!r}"


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


# ============================================================
# A grabber that is killed mid-write leaves no truncated PNG
#
# Shard `scripts-04-p1` F6. Both grabbers open `clip.png` for writing BEFORE
# the tool runs, and the tool is killed at `timeout=5`. `TimeoutExpired` is a
# `SubprocessError`, caught, returning False - so a slow grabber on a large
# clipboard image left a nonzero, TRUNCATED PNG at the well-known path while
# the operator was told "No image on clipboard." and given exit 1. `main` swept
# only zero-byte leftovers, which is the one shape of the mess that could not
# be mistaken for a capture.
# ============================================================


def _grabber_killed_mid_write(monkeypatch, tool: str, partial: bytes = b"\x89PNG\r\n\x1a\ntrunc"):
    """`tool` writes part of a PNG and is then killed by the timeout.

    The real sequence, in order: the parent opens the path (truncating it), the
    child emits some bytes, `subprocess.run` raises `TimeoutExpired`. Nothing
    here is a stand-in for the timeout; it is the exception the timeout raises.
    """
    monkeypatch.setattr(
        clip.shutil, "which", lambda name: f"/usr/bin/{name}" if name == tool else None
    )

    def fake_run(args, **kwargs):
        assert args[0] == tool, f"expected the {tool} fallback, got {args!r}"
        kwargs["stdout"].write(partial)
        kwargs["stdout"].flush()
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(clip.subprocess, "run", fake_run)


@pytest.mark.parametrize("tool", ["wl-paste", "xclip"])
def test_a_grabber_killed_mid_write_leaves_no_truncated_png(
    out_dir, monkeypatch, capsys, tool
):
    """Exit 1 and NO file, rather than exit 1 beside a corrupt one."""
    _raises_no_grabber(monkeypatch)
    _grabber_killed_mid_write(monkeypatch, tool)

    rc = clip.main()
    out = out_dir / "clipboard" / "clip.png"

    assert rc != 0
    assert not out.exists(), (
        f"a killed {tool} left {out.stat().st_size} bytes of truncated PNG at "
        f"the path callers read by convention"
    )
    assert capsys.readouterr().out.strip() == ""


@pytest.mark.parametrize("tool", ["wl-paste", "xclip"])
def test_a_grabber_that_exits_nonzero_leaves_no_partial_png(
    out_dir, monkeypatch, capsys, tool
):
    """The second route to the same litter: bytes written, then a refusal.

    `_only_wlpaste(payload=b"partial", returncode=1)` was already covered for
    its RETURN VALUE, and the file it left behind was asserted by nothing.
    """
    _raises_no_grabber(monkeypatch)
    helper = _only_wlpaste if tool == "wl-paste" else _only_xclip
    helper(monkeypatch, payload=b"partial", returncode=1)

    rc = clip.main()

    assert rc != 0
    assert not (out_dir / "clipboard" / "clip.png").exists()
    assert capsys.readouterr().out.strip() == ""


def test_a_failed_first_grabber_does_not_delete_the_second_ones_capture(
    out_dir, monkeypatch, capsys
):
    """Cleanup that reaches past its own run would be a worse bug than the litter.

    wl-paste is killed mid-write, xclip then succeeds. The surviving file must
    be xclip's, whole.
    """
    _raises_no_grabber(monkeypatch)
    monkeypatch.setattr(clip.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, **kwargs):
        if args[0] == "wl-paste":
            kwargs["stdout"].write(b"\x89PNG\r\n\x1a\ntrunc")
            kwargs["stdout"].flush()
            raise subprocess.TimeoutExpired(args, 5)
        kwargs["stdout"].write(PNG_BYTES)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(clip.subprocess, "run", fake_run)

    rc = clip.main()
    out = out_dir / "clipboard" / "clip.png"

    assert rc == 0
    assert out.read_bytes() == PNG_BYTES
    assert capsys.readouterr().out.strip() == str(out)
