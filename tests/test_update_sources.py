import pytest
from scripts.utils import update_sources as us


def test_github_release_latest(monkeypatch):
    monkeypatch.setattr(us, "_get_json", lambda url: {"tag_name": "v7.2.92"})
    assert us.latest_version({"via": "github_release", "repo": "x/y"}) == "7.2.92"


def test_pypi_latest(monkeypatch):
    monkeypatch.setattr(us, "_get_json", lambda url: {"info": {"version": "2026.7.20"}})
    assert us.latest_version({"via": "pypi", "package": "yt-dlp"}) == "2026.7.20"


def test_npm_latest(monkeypatch):
    monkeypatch.setattr(us, "_get_json", lambda url: {"version": "1.2.3"})
    assert us.latest_version({"via": "npm", "package": "@anthropic-ai/claude-code"}) == "1.2.3"


def test_github_asset_url_selects_by_arch(monkeypatch):
    payload = {"tag_name": "v7.2.92", "assets": [
        {"name": "CLIProxyAPI_7.2.92_linux_amd64.tar.gz",
         "browser_download_url": "https://x/amd64.tar.gz"},
        {"name": "CLIProxyAPI_7.2.92_linux_aarch64.tar.gz",
         "browser_download_url": "https://x/arm64.tar.gz"},
    ]}
    monkeypatch.setattr(us, "_get_json", lambda url: payload)
    url = us.github_asset_url({"repo": "x/y"}, "amd64")
    assert url == "https://x/amd64.tar.gz"


def test_unknown_via_raises():
    with pytest.raises(us.SourceError, match="via"):
        us.latest_version({"via": "carrier-pigeon"})
