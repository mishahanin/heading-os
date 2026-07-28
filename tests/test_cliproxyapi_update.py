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


def test_a_service_that_binds_late_is_not_read_as_a_dead_one(monkeypatch):
    """`systemctl start` returns on spawn, not on listen.

    The 7.2.92 -> 7.2.104 bump bound its port a fraction of a second after
    start, and the single immediate probe rolled a healthy binary back.
    """
    answers = iter([False, False, True])
    monkeypatch.setattr(cliproxyapi_update, "_health_ok", lambda: next(answers))
    monkeypatch.setattr(cliproxyapi_update.time, "sleep", lambda _s: None)

    assert cliproxyapi_update._wait_healthy(timeout_s=10.0, interval_s=0.0)


def test_a_service_that_never_answers_still_fails(monkeypatch):
    """The retry must not turn a genuinely broken binary into a silent pass."""
    calls = []
    monkeypatch.setattr(cliproxyapi_update, "_health_ok",
                        lambda: calls.append(1) or False)
    monkeypatch.setattr(cliproxyapi_update.time, "sleep", lambda _s: None)

    assert not cliproxyapi_update._wait_healthy(timeout_s=0.0, interval_s=0.0)
    assert calls, "the gate is probed at least once before giving up"
