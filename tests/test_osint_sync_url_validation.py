"""Regression test for the CodeQL py/incomplete-url-substring-sanitization fix
in scripts/osint-advanced-sync.py (alert #3).

The old check `"github.com" in url` matched lookalike hosts (evilgithub.com,
github.com.evil.com) via substring containment and wrongly skipped them as a
"CLI" tool. The fix parses the URL and matches the host exactly (github.com or a
*.github.com subdomain). These pin that behaviour so the bypass cannot regress.
"""
import importlib.util
import sys
from pathlib import Path
from urllib.error import URLError

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location(
        "osint_advanced_sync_mod", ROOT / "scripts" / "osint-advanced-sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(monkeypatch):
    m = _load()
    # Any URL that is NOT short-circuited as "CLI" reaches urlopen; stub it so the
    # test never touches the network and such URLs deterministically come back
    # non-"CLI". Reaching this stub is itself the proof the github branch was skipped.
    def _boom(*a, **k):
        raise URLError("network disabled in test")
    monkeypatch.setattr(m, "urlopen", _boom)
    return m


@pytest.mark.parametrize("url", [
    "https://github.com/org/repo",
    "https://github.com/org/repo/releases",
    "https://api.github.com/repos/org/repo",
    "https://raw.github.com/org/repo/main/file",
])
def test_genuine_github_hosts_are_skipped_as_cli(mod, url):
    status, _ = mod.validate_url(url)
    assert status == "CLI"


@pytest.mark.parametrize("url", [
    "https://evilgithub.com/org/repo",       # substring match, different host
    "https://github.com.evil.com/x",         # github.com as a left-label, attacker domain
    "https://notgithub.com/x",
    "https://gitlab.com/org/repo",
])
def test_lookalike_and_other_hosts_are_not_skipped(mod, url):
    # Reaches the stubbed urlopen (raises) -> never returns "CLI".
    status, _ = mod.validate_url(url)
    assert status != "CLI"


def test_github_search_still_http_checked(mod):
    # /search must NOT be treated as a CLI tool even on the real github host.
    status, _ = mod.validate_url("https://github.com/search?q=x")
    assert status != "CLI"
