"""The cliproxyapi apply script fetches a URL it did not author.

`browser_download_url` arrives inside the GitHub API response, so unlike every
URL in `update_sources` it is remote-controlled data rather than an https
literal in the source. `urllib.request.urlopen` honours `file:` and custom
schemes, which would turn a hijacked or spoofed API response into a local-file
read staged as the replacement binary.
"""
import pytest

from scripts.updaters import cliproxyapi_update


@pytest.mark.parametrize("url", [
    "file:///etc/hostname",
    "http://example.invalid/cli-proxy-api.tar.gz",
    "ftp://example.invalid/cli-proxy-api.tar.gz",
    "gopher://example.invalid/x",
])
def test_download_refuses_a_scheme_it_did_not_choose(url, tmp_path):
    dest = tmp_path / "asset.bin"

    with pytest.raises(ValueError, match="non-https"):
        cliproxyapi_update._download(url, dest)

    assert not dest.exists(), "the refusal lands before anything is written"


def test_the_refusal_names_the_url_it_refused(tmp_path):
    """A silent refusal reads as a network failure; the operator gets the URL."""
    with pytest.raises(ValueError) as exc:
        cliproxyapi_update._download("file:///etc/hostname", tmp_path / "a.bin")

    assert "file:///etc/hostname" in str(exc.value)
