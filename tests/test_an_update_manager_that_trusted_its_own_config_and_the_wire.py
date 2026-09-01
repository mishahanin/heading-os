#!/usr/bin/env python3
"""Four places the update manager believed unverified input.

The registry is hand-edited YAML and the sources are the public internet, so
both are data, not trusted structure. Measured 2026-08-30:

* `update_registry.load_registry` guarded EVERY `apply` invariant behind
  `isinstance(apply_block, dict)`, so `apply: "echo hi"` passed all of them by
  firing none and was stored into `Component.apply`, annotated
  `dict[str, Any] | None`. `health`, `display` and `pin` had no check at all;
  an unquoted `pin: 1.5` arrives from YAML as a float.
* The same loader wrapped read failures in `RegistryError`, except
  `UnicodeDecodeError`, which is a `ValueError`: `b"\\xff\\xfe"` in the file
  escaped raw past every caller that catches `RegistryError`.
* `update_common.resolve_current` documents "" for unknown and caught only
  `TimeoutExpired`. A `current.regex` of `v(\\d+` raised
  `re.error: missing ), unterminated subpattern`.
* `update_sources._get_json` is annotated `-> dict` and returned whatever
  `json.loads` produced. A 200 whose body is `[]` made `latest_version` raise
  `AttributeError`, which the manager does not catch, so ONE bad row took down
  the whole check.
"""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.update_common import resolve_current  # noqa: E402
from scripts.utils.update_registry import (  # noqa: E402
    Component,
    RegistryError,
    load_registry,
)
from scripts.utils import update_sources  # noqa: E402

GOOD = """\
components:
  yt-dlp:
    tier: auto
    display: yt-dlp
    current: {via: shell, cmd: "yt-dlp --version"}
    latest: {via: pypi, package: yt-dlp}
    apply:
      cmd: "uv tool upgrade yt-dlp"
      rollback_cmd: "uv tool install 'yt-dlp=={prev}'"
    health: {cmd: "yt-dlp --version"}
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- registry


def test_a_known_good_registry_still_loads(tmp_path):
    """The control. Every refusal below is meaningless without this."""
    comps = load_registry(_write(tmp_path, GOOD))
    assert len(comps) == 1
    assert comps[0].apply["cmd"] == "uv tool upgrade yt-dlp"


def test_a_string_apply_block_is_refused_at_load_time(tmp_path):
    """The measured case: `apply: "echo hi"` passed every dict-guarded check."""
    text = GOOD.replace(
        '    apply:\n'
        '      cmd: "uv tool upgrade yt-dlp"\n'
        '      rollback_cmd: "uv tool install \'yt-dlp=={prev}\'"\n',
        '    apply: "echo hi"\n')
    assert "apply: \"echo hi\"" in text, "the fixture edit did not apply"
    with pytest.raises(RegistryError, match="`apply` must be a mapping"):
        load_registry(_write(tmp_path, text))


def test_a_list_apply_block_is_refused_too(tmp_path):
    text = GOOD.replace(
        '    apply:\n'
        '      cmd: "uv tool upgrade yt-dlp"\n'
        '      rollback_cmd: "uv tool install \'yt-dlp=={prev}\'"\n',
        '    apply: [one, two]\n')
    with pytest.raises(RegistryError, match="`apply` must be a mapping"):
        load_registry(_write(tmp_path, text))


@pytest.mark.parametrize("line,field", [
    ("    health: not-a-mapping\n", "health"),
    ("    display: [a, b]\n", "display"),
    ("    pin: 1.5\n", "pin"),
])
def test_a_wrong_typed_scalar_field_is_refused_at_load_time(tmp_path, line, field):
    """The three fields stored into typed slots with no check at all."""
    text = GOOD.replace('    health: {cmd: "yt-dlp --version"}\n',
                        '    health: {cmd: "yt-dlp --version"}\n' + line) \
        if field != "health" else GOOD.replace(
            '    health: {cmd: "yt-dlp --version"}\n', line)
    with pytest.raises(RegistryError, match=f"`{field}` must be"):
        load_registry(_write(tmp_path, text))


def test_an_undecodable_registry_is_a_registryerror_not_a_unicodedecodeerror(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_bytes(b"\xff\xfe components:\n")
    with pytest.raises(RegistryError, match="cannot read registry"):
        load_registry(path)


def test_the_observed_apply_invariant_still_holds(tmp_path):
    """The pre-existing hard invariant must survive the new type check."""
    text = GOOD.replace("    tier: auto\n", "    tier: observed\n")
    with pytest.raises(RegistryError, match="observed entries may not carry"):
        load_registry(_write(tmp_path, text))


# ------------------------------------------------------------ resolve_current


def test_a_malformed_regex_in_the_registry_resolves_to_unknown():
    """The measured case: `v(\\d+` raised re.error out of a "" -or-nothing API."""
    comp = Component(name="x", tier="auto", latest={},
                     current={"cmd": "echo v1.2", "regex": r"v(\d+"})
    assert resolve_current(comp) == ""


def test_a_command_that_cannot_be_spawned_resolves_to_unknown(monkeypatch):
    """OSError from the spawn is "unknown", not an exception past the caller."""
    import subprocess

    def boom(*a, **kw):
        raise OSError("no bash on PATH")

    monkeypatch.setattr(subprocess, "run", boom)
    comp = Component(name="x", tier="auto", latest={}, current={"cmd": "true"})
    assert resolve_current(comp) == ""


def test_a_version_banner_that_is_not_utf8_still_resolves():
    """`text=True` with no `errors=` decodes the child's stdout STRICTLY.

    Third fault of the same shape as the two this function's comment already
    names, and the one it missed. `resolve_current` catches
    `(subprocess.SubprocessError, OSError)`; `UnicodeDecodeError` is a
    `ValueError`, so it is neither. A tool whose version banner carries one
    non-UTF-8 byte - a Latin-1 copyright sign or an accented word, ordinary in a
    vendor CLI - therefore raised straight out of a function documented to
    answer "" rather than raise, and ONE such component took the whole `check`
    run down. MEASURED 2026-09-01 with `printf 'v1.2.3 \\xe9dition\\n'`:
    `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 7`,
    past every caller.

    Decoding with `errors="replace"` is the fix rather than a wider `except`,
    because the version here is RECOVERABLE: returning "" would report a
    perfectly healthy tool as unknown, which is the misleading answer the
    docstring's "" is meant to avoid. `update_sources._get_json` made the other
    choice for the same exception one module over, correctly - a JSON body with
    an undecodable byte is not recoverable, so it raises SourceError.
    """
    comp = Component(name="x", tier="auto", latest={},
                     current={"cmd": r"printf 'v1.2.3 \xe9dition\n'",
                              "regex": r"v([0-9.]+)"})
    assert resolve_current(comp) == "1.2.3"


def test_an_undecodable_banner_with_no_regex_still_returns_its_first_line():
    """The no-regex arm of the same read; both reach the strict decode."""
    comp = Component(name="x", tier="auto", latest={},
                     current={"cmd": r"printf 'v1.2.3 \xe9dition\nsecond line\n'"})
    out = resolve_current(comp)
    assert out.startswith("v1.2.3 "), out
    assert "second line" not in out


def test_a_working_regex_still_returns_its_capture_group():
    """The control: the guards must not turn every resolution into unknown."""
    comp = Component(name="x", tier="auto", latest={},
                     current={"cmd": "echo v1.2.3", "regex": r"v([0-9.]+)"})
    assert resolve_current(comp) == "1.2.3"


# ------------------------------------------------------------- update_sources


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def wire(monkeypatch):
    """Answer urlopen from a scripted body. Nothing here reaches the network."""
    box = {"body": b"{}"}

    def fake_urlopen(req, timeout=None):
        return _Resp(box["body"])

    monkeypatch.setattr(update_sources.urllib.request, "urlopen", fake_urlopen)
    return box


def test_a_json_array_body_is_a_sourceerror_not_an_attributeerror(wire):
    """The measured case: one bad row took the whole check down."""
    wire["body"] = b"[]"
    with pytest.raises(update_sources.SourceError, match="not a JSON object"):
        update_sources.latest_version({"via": "pypi", "package": "yt-dlp"})


def test_a_json_scalar_body_is_a_sourceerror_too(wire):
    wire["body"] = b'"an error page that happens to be json"'
    with pytest.raises(update_sources.SourceError, match="not a JSON object"):
        update_sources.latest_version({"via": "npm", "package": "x"})


def test_an_asset_entry_without_a_name_does_not_raise(wire):
    """`asset["name"]` raised KeyError out of a SourceError-only contract."""
    wire["body"] = json.dumps({"assets": [
        {"browser_download_url": "https://example.invalid/a"},
        "not even a mapping",
        {"name": "tool-linux-amd64.tar.gz",
         "browser_download_url": "https://example.invalid/good"},
    ]}).encode()
    assert update_sources.github_asset_url({"repo": "acme/tool"}) == (
        "https://example.invalid/good")


def test_a_non_https_source_url_is_refused(wire):
    """The `noqa: S310 - https literal` justification is now enforced, not asserted."""
    with pytest.raises(update_sources.SourceError, match="non-https"):
        update_sources._get_json("http://example.invalid/x")
    with pytest.raises(update_sources.SourceError, match="non-https"):
        update_sources._get_json("file:///etc/passwd")


def test_a_well_formed_body_still_resolves(wire):
    """The control: the shape check must not refuse a real answer."""
    wire["body"] = json.dumps({"info": {"version": "2026.8.30"}}).encode()
    assert update_sources.latest_version(
        {"via": "pypi", "package": "yt-dlp"}) == "2026.8.30"
