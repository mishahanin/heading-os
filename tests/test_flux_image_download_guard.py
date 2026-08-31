"""A generated-image download must not honour a scheme the API could smuggle in.

Two defects in `.claude/skills/flux-image/scripts/generate_image.py`, found by
the 2026-08-31 review.

F8. `download_file(url, filepath)` hands `url` straight to
`urllib.request.urlopen`. The URL is not a constant: `generate_image` reads it
out of `prediction.get("output")`, the body of the Replicate API response, and
then writes whatever comes back to disk as an image. `urlopen` honours the
scheme it is given, so a `file:` URL reads a LOCAL path and this function
reports it as a generated picture, and `ftp:` reaches a second protocol nobody
asked for. The remedy already exists one skill over, in
`.claude/skills/osint-advanced/scripts/osint_api.py`, which asserts the scheme
instead of suppressing ruff's S310 warning about it.

The guard here is TIGHTER than the osint one, deliberately: osint permits
`http://` as well, and its URLs come from a fixed in-code endpoint table. This
URL comes from the response body of the very server the guard exists to
distrust, and a downgrade to cleartext is a capability the caller never needs -
Replicate serves its outputs over https.

F9. `--output` defaulted to the bare relative name `generated_image.png`, which
resolves against the current directory. Every documented invocation runs from
the engine clone, and the engine repo is PUBLIC while a generated image is DATA.
`SKILL.md` already carried the workaround in prose (resolve the data outputs dir
and pass an absolute path) while the trap stayed in the code.
"""
from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".claude" / "skills" / "flux-image" / "scripts" / "generate_image.py"


def _load():
    """Load the script by path, the way the skill runs it."""
    spec = importlib.util.spec_from_file_location("_generate_image_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test in this file may open a socket.

    `download_file` is a network function and the fixtures below drive it with
    real URLs. A guard that leaked would otherwise reach out.
    """
    def _refuse(*args, **kwargs):
        raise AssertionError("a test in this file attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)


# ---------------------------------------------------------------- F8


@pytest.mark.parametrize(
    "scheme_url",
    [
        "file://{secret}",
        "ftp://example.invalid/pic.png",
        "http://example.invalid/pic.png",
        "gopher://example.invalid/pic.png",
        "data:image/png;base64,AAAA",
    ],
)
def test_download_file_refuses_every_non_https_scheme(tmp_path, scheme_url):
    secret = tmp_path / "not-an-image.txt"
    secret.write_text("LOCAL FILE CONTENT THAT IS NOT AN IMAGE\n", encoding="utf-8")
    url = scheme_url.format(secret=secret)
    out = tmp_path / "out.png"

    with pytest.raises(ValueError) as excinfo:
        gen.download_file(url, out)

    assert "https" in str(excinfo.value).lower()
    assert not out.exists(), "a refused download must not leave a file behind"


def test_a_file_url_does_not_become_an_image(tmp_path):
    """The concrete harm, stated as behaviour rather than as a scheme list.

    Without the guard this writes the local file's bytes into out.png and the
    caller prints `[OK] Saved:`.
    """
    secret = tmp_path / "credentials.txt"
    secret.write_text("SENTINEL-LOCAL-BYTES", encoding="utf-8")
    out = tmp_path / "generated.png"

    with pytest.raises(ValueError):
        gen.download_file(f"file://{secret}", out)

    assert not out.exists()
    # And nothing anywhere under tmp_path now carries the local file's content
    # except the local file itself.
    copies = [
        p for p in tmp_path.rglob("*")
        if p.is_file() and p != secret and "SENTINEL-LOCAL-BYTES" in p.read_text(errors="replace")
    ]
    assert copies == []


def test_an_https_url_passes_the_guard_and_reaches_urlopen(tmp_path, monkeypatch):
    """The negative case's partner: the guard must not refuse the real path.

    A guard with no accepted case is indistinguishable from a function that
    always raises.
    """
    reached = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            if reached.get("done"):
                return b""
            reached["done"] = True
            return b"\x89PNG\r\n\x1a\n"

    def _fake_urlopen(req, timeout=None):
        reached["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(gen.urllib.request, "urlopen", _fake_urlopen)
    out = tmp_path / "ok.png"
    gen.download_file("https://replicate.delivery/pbxt/abc/out.png", out)

    assert reached["url"] == "https://replicate.delivery/pbxt/abc/out.png"
    assert out.read_bytes().startswith(b"\x89PNG")


# ---------------------------------------------------------------- F9


def test_the_default_output_path_is_never_relative():
    """`generated_image.png` resolved into whatever directory the caller stood in."""
    default = gen.default_output_path()
    assert Path(default).is_absolute(), f"default output {default!r} is a relative path"


def test_the_default_output_path_is_outside_the_engine_clone():
    """The engine repo is public; a generated image is DATA."""
    default = Path(gen.default_output_path()).resolve()
    assert ROOT not in default.parents and default != ROOT, (
        f"the default output {default} resolves inside the engine clone {ROOT}"
    )


def test_the_argparse_default_is_not_a_bare_filename():
    """Read the parser the CLI actually builds, rather than the source text."""
    parser = gen.build_parser()
    action = next(a for a in parser._actions if a.dest == "output")
    assert action.default is None, (
        "--output must default to None so main() can resolve a safe absolute path; "
        f"got {action.default!r}"
    )


def test_main_writes_nothing_into_the_engine_clone_when_output_is_omitted(monkeypatch, tmp_path):
    """End to end through main(): the resolved path must land outside the repo."""
    captured = {}

    def _fake_generate_image(**kwargs):
        captured.update(kwargs)
        return [kwargs["output_path"]]

    monkeypatch.setattr(gen, "generate_image", _fake_generate_image)
    monkeypatch.setattr(sys, "argv", ["generate_image.py", "--prompt", "a duck"])
    gen.main()

    resolved = Path(captured["output_path"]).resolve()
    assert resolved.is_absolute()
    assert ROOT not in resolved.parents


def test_an_explicit_output_is_still_honoured_verbatim(monkeypatch, tmp_path):
    captured = {}

    def _fake_generate_image(**kwargs):
        captured.update(kwargs)
        return [kwargs["output_path"]]

    target = tmp_path / "somewhere" / "mine.png"
    monkeypatch.setattr(gen, "generate_image", _fake_generate_image)
    monkeypatch.setattr(sys, "argv", ["generate_image.py", "--prompt", "a duck", "--output", str(target)])
    gen.main()

    assert captured["output_path"] == str(target)


def test_the_default_refuses_rather_than_falling_back_to_the_cwd(monkeypatch):
    """If the data root cannot be resolved, refuse. Never silently use cwd.

    Falling back to a cwd-relative name is the original defect wearing a
    different hat.
    """
    def _boom():
        raise RuntimeError("no data overlay on this clone")

    monkeypatch.setattr(gen, "_resolve_outputs_dir", _boom)
    with pytest.raises(SystemExit) as excinfo:
        gen.default_output_path()
    assert excinfo.value.code != 0
