"""Four functions whose docstrings promised a soft failure and delivered a crash.

Each one caught the errors its author thought of, then did the work that raises
the error they did not: a `.get` on a body that decoded to a list, a
comprehension over a YAML null. The shape is always the same. The guarded
region ends one line too early, and the crash lands in a caller that read the
docstring and wrote no handler.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`docstring-contradicts-code`. Every case below was reproduced against the
unfixed source before the fix was written.
"""
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from scripts.utils import embeddings, impeccable_engine, odin_principles, search  # noqa: E402


# ---------------------------------------------------------------------------
# search.py: "Raises SearchBackendError on API failure after retries"
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_tavily_body_that_is_not_json_is_a_backend_error(monkeypatch):
    """The POST reader must convert an undecodable body the way the GET reader does.

    `search_with_fallback` catches `(SearchBackendError, NoBackendsConfigured)`
    and nothing else, so a raw json.JSONDecodeError escaping `_post_json` meant
    Brave was never tried: the caller got a decode error instead of results.
    """
    monkeypatch.setattr(
        search.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(b"<html>502 Bad Gateway</html>"),
    )
    with pytest.raises(search.SearchBackendError) as exc:
        search._post_json("https://api.tavily.com/search", {}, {})
    assert "unreadable response body" in str(exc.value)
    assert "JSONDecodeError" in str(exc.value)


def test_a_tavily_body_that_is_not_utf8_is_a_backend_error(monkeypatch):
    monkeypatch.setattr(
        search.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(b"\x8b\x1f\xff\xfe"),
    )
    with pytest.raises(search.SearchBackendError) as exc:
        search._post_json("https://api.tavily.com/search", {}, {})
    assert "unreadable response body" in str(exc.value)


def test_the_post_reader_still_reports_a_connection_error_as_itself(monkeypatch):
    """OSError sits last in the clause list on purpose: URLError is an OSError.

    If the new clause were ordered first it would shadow the connection-error
    branch and every offline run would report an unreadable body.
    """
    def _boom(*a, **k):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(search.urllib.request, "urlopen", _boom)
    with pytest.raises(search.SearchBackendError) as exc:
        search._post_json("https://api.tavily.com/search", {}, {})
    assert "Connection error" in str(exc.value)


# ---------------------------------------------------------------------------
# impeccable_engine.py: "A missing or malformed file falls back ... and says so"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", ['["a", "b"]', '"screen"', "42", "null"])
def test_a_profile_config_that_is_not_an_object_falls_back_loudly(tmp_path, payload):
    """Valid JSON is not a valid config.

    Before the fix this raised AttributeError out of a function documented to
    make the visual gate NOISIER on an unreadable config, never quieter. A
    crash is neither: `/impeccable` died instead of degrading to screen.
    """
    p = tmp_path / "visual-check-profiles.json"
    p.write_text(payload, encoding="utf-8")
    profiles, warning = impeccable_engine.load_profiles(p)
    assert warning and "falling back to screen" in warning
    assert "screen" in profiles.get("profiles", {})


def test_a_well_formed_profile_config_still_loads(tmp_path):
    """The shape guard must not reject the good case it was added beside."""
    p = tmp_path / "visual-check-profiles.json"
    p.write_text(json.dumps({"profiles": {"screen": {}}}), encoding="utf-8")
    profiles, warning = impeccable_engine.load_profiles(p)
    assert warning is None
    assert profiles["default"] == "screen"


# ---------------------------------------------------------------------------
# embeddings.py: "Returns None rather than raising"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [b'["bge-m3"]', b'"bge-m3"', b"7"])
def test_a_tags_reply_that_is_not_an_object_returns_none(monkeypatch, payload):
    """`body.get("models")` sat OUTSIDE the try, so a list reply crashed the build.

    The docstring's whole argument is that a digest is a diagnostic and must
    never fail a build. A proxy answering /api/tags with a JSON array made it
    fail one, with an AttributeError stack instead of a message.
    """
    monkeypatch.setattr(
        embeddings.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(payload),
    )
    assert embeddings.model_digest(model="bge-m3", host="http://127.0.0.1:11434") is None


def test_a_well_formed_tags_reply_still_yields_the_digest(monkeypatch):
    body = json.dumps({"models": [{"name": "bge-m3:latest", "digest": "sha256:abc"}]})
    monkeypatch.setattr(
        embeddings.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(body.encode("utf-8")),
    )
    assert embeddings.model_digest(
        model="bge-m3", host="http://127.0.0.1:11434") == "sha256:abc"


# ---------------------------------------------------------------------------
# odin_principles.py: "Never raises"
# ---------------------------------------------------------------------------

def _principle(dirpath: Path, slug: str, frontmatter: str) -> Path:
    p = dirpath / f"{slug}.md"
    p.write_text(f"---\n{frontmatter}\n---\n\nbody\n", encoding="utf-8")
    return p


def test_a_principle_with_an_empty_keywords_key_does_not_raise(tmp_path):
    """`keywords:` with nothing after it is valid YAML and parses to None.

    `[str(k) for k in None]` raised TypeError out of a function whose docstring
    ends "Never raises", taking down every /odin consult that touched the file.
    """
    brain = tmp_path / "odin-brain"
    (brain / "principles").mkdir(parents=True)
    _principle(brain / "principles", "quiet", "title: Quiet\nkeywords:\nconfidence: high")
    loaded = odin_principles._load_principles(brain)
    assert [p["slug"] for p in loaded] == ["quiet"]
    assert loaded[0]["keywords"] == []


@pytest.mark.parametrize("value, expected", [
    ("strategy", ["strategy"]),
    ("[a, b]", ["a", "b"]),
    ("7", ["7"]),
])
def test_a_scalar_or_list_keywords_value_is_normalised(tmp_path, value, expected):
    brain = tmp_path / "odin-brain"
    (brain / "principles").mkdir(parents=True)
    _principle(brain / "principles", "p", f"title: P\nkeywords: {value}")
    assert odin_principles._load_principles(brain)[0]["keywords"] == expected


def test_a_principle_that_is_not_utf8_is_skipped_not_raised(tmp_path):
    """UnicodeDecodeError is not an OSError, so the read guard missed it."""
    brain = tmp_path / "odin-brain"
    pdir = brain / "principles"
    pdir.mkdir(parents=True)
    (pdir / "cp1251.md").write_bytes(b"---\ntitle: \xcf\xf0\xe8\xed\xf6\xe8\xef\n---\n")
    _principle(pdir, "readable", "title: Readable\nkeywords: [x]")
    assert [p["slug"] for p in odin_principles._load_principles(brain)] == ["readable"]


def test_the_intersection_query_survives_a_broken_principle(tmp_path):
    """The public entry, not the private loader: this is where the crash landed."""
    brain = tmp_path / "odin-brain"
    pdir = brain / "principles"
    pdir.mkdir(parents=True)
    _principle(pdir, "broken", "title: Broken\nkeywords:")
    _principle(pdir, "good", "title: Good\nkeywords: [deal]\nconfidence: high")
    hits = odin_principles.principles_for_domains(["deal"], brain_root=brain)
    assert [h["slug"] for h in hits] == ["good"]
