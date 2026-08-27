import pytest
from scripts.utils import update_sources as us


def _recorder(monkeypatch, payload):
    """Stub `_get_json` and KEEP the url it was called with.

    The lambdas here used to be `lambda url: <payload>`, discarding `url`, and
    the payload's own literal was then the value asserted equal. Building that
    url is the entire per-branch behaviour of `latest_version`, so the tests
    measured the stub and nothing else: dropping the trailing path segment from
    the npm and pypi endpoints left every one of them green.
    """
    seen = []

    def _get_json(url):
        seen.append(url)
        return payload

    monkeypatch.setattr(us, "_get_json", _get_json)
    return seen


def test_github_release_latest(monkeypatch):
    seen = _recorder(monkeypatch, {"tag_name": "v7.2.92"})
    assert us.latest_version({"via": "github_release", "repo": "x/y"}) == "7.2.92"
    assert seen == ["https://api.github.com/repos/x/y/releases/latest"]


def test_pypi_latest(monkeypatch):
    seen = _recorder(monkeypatch, {"info": {"version": "2026.7.20"}})
    assert us.latest_version({"via": "pypi", "package": "yt-dlp"}) == "2026.7.20"
    assert seen == ["https://pypi.org/pypi/yt-dlp/json"]


def test_npm_latest(monkeypatch):
    seen = _recorder(monkeypatch, {"version": "1.2.3"})
    assert us.latest_version({"via": "npm", "package": "@anthropic-ai/claude-code"}) == "1.2.3"
    assert seen == ["https://registry.npmjs.org/@anthropic-ai/claude-code/latest"]


def test_github_asset_url_selects_by_arch(monkeypatch):
    payload = {"tag_name": "v7.2.92", "assets": [
        {"name": "CLIProxyAPI_7.2.92_linux_amd64.tar.gz",
         "browser_download_url": "https://x/amd64.tar.gz"},
        {"name": "CLIProxyAPI_7.2.92_linux_aarch64.tar.gz",
         "browser_download_url": "https://x/arm64.tar.gz"},
    ]}
    seen = _recorder(monkeypatch, payload)
    url = us.github_asset_url({"repo": "x/y"}, "amd64")
    assert url == "https://x/amd64.tar.gz"
    assert seen == ["https://api.github.com/repos/x/y/releases/latest"]


def test_unknown_via_raises():
    with pytest.raises(us.SourceError, match="via"):
        us.latest_version({"via": "carrier-pigeon"})
