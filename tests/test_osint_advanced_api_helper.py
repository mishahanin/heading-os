"""The mandatory sanctions stream must be reachable with the tools the skill declares.

Found by the 2026-08-23 audit. `/osint-advanced` declares
`allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"`, and its reference
files prescribed API access that no listed tool can perform:

- The MANDATORY Sanctions/Compliance stream told the assistant to reach
  `POST https://api.opensanctions.org/match/default` with a JSON body and an
  `Authorization` header **via WebFetch**. WebFetch issues a GET and controls no
  headers, so the primary sanctions query could not run at all. The skill's
  NEVER-list forbids "a sanctions CLEAR without actually querying the
  databases" - the documented primary path produced exactly that.
- Six further calls were raw `curl`, which is not in `allowed-tools`.
- Each of those six interpolated a live API key into a shell command line via
  `$(python3 -c '...load_api_key...')`, putting the credential in the process
  table and the session transcript.

All three are answered by one stdlib helper reached through the already-granted
`Bash(python3:*)`, with the key read in-process.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "osint-advanced"
HELPER = SKILL / "scripts" / "osint_api.py"

_spec = importlib.util.spec_from_file_location("_osint_api_under_test", HELPER)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


class _FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def captured(monkeypatch):
    """Intercept the outgoing request instead of touching the network."""
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data.decode()) if req.data else None
        return _FakeResponse('{"responses": {"q1": {"results": []}}}')

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(api, "load_api_key", lambda name: f"test-{name}")
    return seen


# ------------------------------------------------------------------ the POST

def test_sanctions_issues_a_post_with_an_auth_header(captured):
    payload, code = api.cmd_sanctions(
        api.build_parser().parse_args(["sanctions", "--name", "Jane Roe"])
    )
    assert code == 0
    assert captured["method"] == "POST", "WebFetch could only ever GET this"
    assert captured["url"] == "https://api.opensanctions.org/match/default"
    assert captured["headers"]["authorization"] == "ApiKey test-OPENSANCTIONS_API_KEY"
    assert captured["body"]["queries"]["q1"]["properties"]["name"] == ["Jane Roe"]
    assert captured["body"]["queries"]["q1"]["schema"] == "Person"
    assert payload["ok"] is True


def test_sanctions_accepts_a_company_schema(captured):
    api.cmd_sanctions(api.build_parser().parse_args(
        ["sanctions", "--name", "Acme Ltd", "--schema", "Company"]))
    assert captured["body"]["queries"]["q1"]["schema"] == "Company"


def test_dehashed_also_posts(captured):
    api.cmd_dehashed(api.build_parser().parse_args(
        ["dehashed", "--query", "email:a@b.test"]))
    assert captured["method"] == "POST"
    assert captured["headers"]["dehashed-api-key"] == "test-DEHASHED_API_KEY"
    assert captured["body"]["query"] == "email:a@b.test"


# ------------------------------------------------- keys stay out of argv

def test_the_key_is_never_passed_on_a_command_line(captured):
    """The old pattern put a live credential in the process table."""
    api.cmd_virustotal(api.build_parser().parse_args(
        ["virustotal", "--domain", "example.com"]))
    assert captured["headers"]["x-apikey"] == "test-VIRUSTOTAL_API_KEY"  # pragma: allowlist secret - a fixture value the conftest injects, not a key
    assert "test-VIRUSTOTAL_API_KEY" not in captured["url"]


def test_hibp_sends_the_required_user_agent(captured):
    """HIBP answers 403 without one."""
    api.cmd_hibp(api.build_parser().parse_args(["hibp", "--account", "a@b.test"]))
    assert captured["headers"]["user-agent"] == "31C-OSINT"
    assert captured["headers"]["hibp-api-key"] == "test-HIBP_API_KEY"
    assert captured["url"].endswith("/breachedaccount/a%40b.test?truncateResponse=false")


@pytest.mark.parametrize("kind", api.HIBP_KINDS)
def test_every_documented_hibp_kind_is_reachable(captured, kind: str):
    api.cmd_hibp(api.build_parser().parse_args(
        ["hibp", "--account", "a@b.test", "--kind", kind]))
    assert f"/api/v3/{kind}/" in captured["url"]


# ------------------------------------------ a failed call is reported, not hidden

def test_an_http_error_is_a_reportable_failure_not_an_empty_clear(monkeypatch):
    """A stream that did not run must never render as CLEAR."""
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    monkeypatch.setattr(api, "load_api_key", lambda name: "k")
    payload, code = api.cmd_sanctions(
        api.build_parser().parse_args(["sanctions", "--name", "Jane Roe"]))
    assert code == 2
    assert payload["ok"] is False
    assert payload["status"] == 401


def test_a_missing_credential_is_reported_not_raised(monkeypatch):
    def missing(name):
        raise RuntimeError(f"{name} not found in .env")

    monkeypatch.setattr(api, "load_api_key", missing)
    payload, code = api.cmd_sanctions(
        api.build_parser().parse_args(["sanctions", "--name", "Jane Roe"]))
    assert code == 2
    assert payload["ok"] is False
    assert "OPENSANCTIONS_API_KEY" in payload["error"]


# --------------------------------------------------- the docs match the tools

def test_no_reference_file_prescribes_an_ungranted_tool():
    paths = sorted((SKILL / "references").glob("*.md"))
    # "no file prescribes curl" is green over zero files, so a renamed
    # references/ directory would switch this scan off in silence.
    # 3 files matched on 2026-08-26.
    assert len(paths) >= 2, f"the scan collapsed to {len(paths)} files"
    offenders = [p.name for p in paths if "curl" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"{offenders} still prescribe curl"


def test_no_reference_file_puts_a_credential_on_a_command_line():
    paths = sorted((SKILL / "references").glob("*.md"))
    # Same reason as above: an empty corpus makes this credential check pass
    # while reading nothing. 3 files matched on 2026-08-26.
    assert len(paths) >= 2, f"the scan collapsed to {len(paths)} files"
    offenders = [
        p.name for p in paths if "load_api_key(" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} still interpolate a key into a shell command"
    )


def test_the_sanctions_line_no_longer_claims_webfetch_can_post():
    text = (SKILL / "references" / "streams-deep-osint.md").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "OpenSanctions API" in l)
    assert "WebFetch" not in line.split("This is a POST")[0], line
    assert "osint_api.py sanctions" in line


def test_the_helper_runs_under_the_granted_tool_pattern():
    """`Bash(python3:*)` is the grant; the helper must work as a plain script."""
    proc = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    for sub in ("sanctions", "hunter", "hibp", "virustotal", "dehashed"):
        assert sub in proc.stdout


def test_the_skill_grants_the_interpreter_the_helper_needs():
    frontmatter = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Bash(python3:*)" in frontmatter
