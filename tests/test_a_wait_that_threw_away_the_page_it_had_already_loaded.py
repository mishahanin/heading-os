"""Five defects in `scripts/linkedin-activity.py`, plus one false comment beside it.

The headline is the crash. `main` navigates with `wait_until="domcontentloaded"`,
gets a real response back, and then calls `page.wait_for_load_state("networkidle",
timeout=args.timeout)` inside a `try/finally` that has no `except`. LinkedIn's
feed keeps long-poll and telemetry sockets open, so networkidle frequently never
settles; the `PlaywrightTimeout` walked out of `main` as a raw traceback and the
run lost a page it was already holding. `scroll_until`, earlier in the same
file, wraps the identical wait and continues on timeout with a comment saying
the feed may have rendered already, so the repaired pattern was already there.

The other four, each measured against the live functions before the change:

1. A SORT COMMENT THAT DESCRIBED A DIFFERENT SORT. The tiebreak read
   `r.get("urn", "")` under a comment saying "higher urn = more recent". A
   `urn:li:activity:<id>` id is a 64-bit value whose high bits are a creation
   time, so higher id IS more recent, but only numerically. Compared as text a
   19-digit id sorts below an 18-digit one. Records lifted from the bpr blobs
   carry no date at all, so for those the urn is the WHOLE key, and the older
   post came out first for exactly the records with nothing to fall back on.

2. A REPORT THAT ALWAYS CLAIMED A PROXY. `render_markdown` hardcoded `_Scraped
   via Playwright + Floorp auth + Decodo proxy_` while `--proxy-slot` defaults
   to 0, which is no proxy. The artifact records which IP the traffic left from
   and on the default run it recorded the wrong one.

3. A PRE-FLIGHT PROBE ON A DIFFERENT EGRESS FROM THE BROWSER. The probe issued
   an authenticated GET through `requests` from the real IP; seconds later the
   browser went out through Decodo. Two source IPs for one `li_at` inside one
   run is the IP-travel signature that this script's own `--proxy-slot` help
   warns invalidates the session.

4. A CANONICALISATION REDIRECT READ AS A DEAD SESSION. `if f"/in/{slug}" in loc`
   matched a benign 302 to the profile itself, and `main` then exited 3 telling
   the operator to log in again to a session that was fine.

And in `scripts/generate-client-docx.py`, a comment claiming `import stays pure`
sat above two module-level side effects (`sys.path.insert`, `ensure_venv()`).
The two globals that finding originally named really were fixed into call-time
functions; the comment was never narrowed to what remained true.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The script cannot run at all without playwright, so skipping here says exactly
# what it means: this dependency is a `browser`-extra install and a core clone
# does not carry it.
PlaywrightTimeout = pytest.importorskip("playwright.sync_api").TimeoutError


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, str(ROOT / "scripts" / "linkedin-activity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def la():
    return _load("linkedin_activity_wait")


# ============================================================
# Fixture HTML: what the extractor is given
# ============================================================

# Two activity ids of DIFFERENT digit length, neither carrying a date. 7.1e18
# is the larger number and therefore the newer post; "999..." is the larger
# string. Only one of the two orderings can be right.
NEWER_ID = "7100000000000000000"   # 19 digits
OLDER_ID = "999999999999999999"    # 18 digits


def _bpr(activity_id: str, text: str) -> str:
    payload = {"included": [{
        "$type": "com.linkedin.voyager.feed.render.UpdateV2",
        "updateMetadata": {"urn": f"urn:li:activity:{activity_id}"},
        "commentary": {"text": {"text": text}},
    }]}
    return f'<code id="bpr-guid-{activity_id[:4]}">{json.dumps(payload)}</code>'


UNDATED_HTML = _bpr(OLDER_ID, "the older post") + _bpr(NEWER_ID, "the newer post")


# ============================================================
# A browser that fails where the real one fails
# ============================================================

class _FakeLocator:
    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    """Enough of a Page for `main` plus `scroll_until` to run end to end.

    `card_count` is set above the scroll target so `scroll_until` returns on its
    first check. That keeps the ONLY `wait_for_load_state` call in these runs
    the one in `main`, which is the call under test; the identical wait inside
    `scroll_until` already had its handler and must not be confused with it.
    """

    def __init__(self, *, idle_raises: bool, url: str, html: str, card_count: int = 12):
        self.url = url
        self._idle_raises = idle_raises
        self._html = html
        self._card_count = card_count
        self.waits: list[tuple[str, int | None]] = []
        self.goto_url: str | None = None

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def goto(self, url, wait_until=None):
        self.goto_url = url
        self.goto_wait_until = wait_until
        return types.SimpleNamespace(status=200)

    def wait_for_load_state(self, state, timeout=None):
        self.waits.append((state, timeout))
        if self._idle_raises:
            raise PlaywrightTimeout("Timeout 45000ms exceeded.")

    def content(self) -> str:
        return self._html

    def locator(self, _selector):
        return _FakeLocator(self._card_count)

    def evaluate(self, _script):
        self.scrolled = True


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.cookies_added: list[dict] = []

    def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    def new_page(self):
        return self.page


class _FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False
        self.context_kwargs: dict = {}

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return _FakeContext(self.page)

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs: dict = {}

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class _Recorder:
    """What one stubbed run saw, so the assertions read off one object."""

    def __init__(self, page, browser, engine):
        self.page = page
        self.browser = browser
        self.engine = engine
        self.probe_calls: list[dict] = []


def _stub_run(la, monkeypatch, tmp_path, *, idle_raises=False,
              url="https://www.linkedin.com/in/mishahanin/recent-activity/all/",
              html=UNDATED_HTML, alive=(True, "status=200"), argv=()) -> _Recorder:
    """Wire `main` to a fake browser and record what it did.

    Nothing here reaches the network: `sync_playwright` is replaced on the real
    `playwright.sync_api` module (which is what `main` imports from at call
    time), and `check_session_alive` is replaced on the loaded script module.
    """
    import playwright.sync_api as pw_api

    page = _FakePage(idle_raises=idle_raises, url=url, html=html)
    browser = _FakeBrowser(page)
    engine = _FakeEngine(browser)
    rec = _Recorder(page, browser, engine)

    class _PW:
        firefox = engine
        chromium = engine

    class _Ctx:
        def __enter__(self):
            return _PW()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(pw_api, "sync_playwright", lambda: _Ctx())
    monkeypatch.setattr(la, "get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(la, "floorp_cookies_for_playwright", lambda profile: [
        {"name": "li_at", "value": "TOKEN", "domain": ".linkedin.com", "path": "/"},
    ])

    def _probe(cookies, slug, *args, **kwargs):
        rec.probe_calls.append({"cookies": cookies, "slug": slug,
                                "args": args, "kwargs": kwargs})
        return alive

    monkeypatch.setattr(la, "check_session_alive", _probe)
    monkeypatch.setattr(sys, "argv",
                        ["linkedin-activity.py", "--slug", "mishahanin", *argv])
    return rec


def _artifact(tmp_path: Path) -> str:
    return (tmp_path / "browser" / "linkedin-activity-auth.md").read_text(encoding="utf-8")


# ============================================================
# 1. The wait that discarded a page it was already holding
# ============================================================

def test_a_networkidle_timeout_does_not_abort_a_run_that_already_navigated(
        la, monkeypatch, tmp_path):
    """The headline. Before the fix this raised `PlaywrightTimeout` out of
    `main`: the browser closed in the `finally`, the traceback reached the
    operator, and the HTML that had already rendered was thrown away."""
    rec = _stub_run(la, monkeypatch, tmp_path, idle_raises=True)

    assert la.main() == 0
    assert rec.browser.closed
    assert _artifact(tmp_path).count("URL: ") == 2


def test_the_timed_out_run_still_writes_the_rendered_html(la, monkeypatch, tmp_path):
    """The page is the point. Continuing without saving it would be the same
    loss with a quieter exit code."""
    _stub_run(la, monkeypatch, tmp_path, idle_raises=True)

    assert la.main() == 0
    rendered = tmp_path / "browser" / "linkedin-activity-rendered.html"
    assert rendered.read_text(encoding="utf-8") == UNDATED_HTML


def test_the_timeout_is_reported_rather_than_swallowed(la, monkeypatch, tmp_path, capsys):
    """A wait that silently did nothing is a different defect from the one being
    fixed. The operator has to be able to tell a settled load from a partial one."""
    _stub_run(la, monkeypatch, tmp_path, idle_raises=True)

    la.main()

    assert "networkidle" in capsys.readouterr().out


def test_the_wait_is_still_attempted_with_the_configured_timeout(
        la, monkeypatch, tmp_path):
    """The other jaw: deleting the wait entirely also stops the crash, and would
    pass every test above. It must still be issued, and with `--timeout`."""
    rec = _stub_run(la, monkeypatch, tmp_path, argv=["--timeout", "12345"])

    assert la.main() == 0
    assert rec.page.waits == [("networkidle", 12345)]


def test_a_settled_load_says_nothing_about_a_timeout(la, monkeypatch, tmp_path, capsys):
    """The warning must be conditional, not printed on every run."""
    _stub_run(la, monkeypatch, tmp_path, idle_raises=False)

    assert la.main() == 0
    assert "networkidle" not in capsys.readouterr().out


def test_an_auth_wall_is_still_detected_after_a_timed_out_wait(
        la, monkeypatch, tmp_path):
    """Why continuing is safe: everything below the wait reads `page.url` and
    `page.content()`, both of which are valid on a partially loaded page. The
    checks that were previously unreachable behind the raise now run."""
    _stub_run(la, monkeypatch, tmp_path, idle_raises=True,
              url="https://www.linkedin.com/authwall?trk=x")

    assert la.main() == 3


def test_an_empty_feed_after_a_timeout_exits_on_the_no_posts_path(
        la, monkeypatch, tmp_path):
    """A feed that really did fail to render lands on exit 4 with the HTML on
    disk, which names the problem better than a traceback did."""
    _stub_run(la, monkeypatch, tmp_path, idle_raises=True, html="<html></html>")

    assert la.main() == 4
    assert (tmp_path / "browser" / "linkedin-activity-rendered.html").is_file()


# ============================================================
# 2. The tiebreak that compared a number as text
# ============================================================

def test_two_undated_posts_order_by_the_numeric_activity_id(la):
    """Measured before the fix: the 18-digit id came out first, because "9"
    sorts above "7" and the 19-digit id is the larger NUMBER, not the larger
    string. Neither record carries a date, so the urn is the whole key."""
    posts = la.extract_posts(UNDATED_HTML, limit=2)

    assert [p["urn"] for p in posts] == [
        f"urn:li:activity:{NEWER_ID}",
        f"urn:li:activity:{OLDER_ID}",
    ]


def test_the_limit_keeps_the_newest_not_the_longest_string(la):
    """The consequence at limit=1: the run reported the wrong post entirely."""
    posts = la.extract_posts(UNDATED_HTML, limit=1)

    assert [p["text"] for p in posts] == ["the newer post"]


def test_a_date_still_outranks_the_id(la):
    """The anchor. Sorting numerically must not demote the date to a tiebreak:
    a dated record keeps its place whatever its id is."""
    dated = (
        '<script type="application/ld+json">'
        + json.dumps({"@graph": [{
            "@type": "DiscussionForumPosting",
            "mainEntityOfPage":
                f"https://www.linkedin.com/posts/x_y-activity-{OLDER_ID}-ab",
            "text": "dated but low id",
            "datePublished": "2026-08-30",
        }]})
        + "</script>"
        + _bpr(NEWER_ID, "undated with a higher id")
    )

    posts = la.extract_posts(dated, limit=2)

    assert posts[0]["urn"] == f"urn:li:activity:{OLDER_ID}"


def test_a_urn_without_an_id_does_not_break_the_sort(la):
    """`posts` is keyed on a urn that matched `urn:li:activity:`, so this cannot
    arrive from the extractor today. It is asserted anyway because the key
    function must total-order whatever it is handed rather than raise."""
    assert la._activity_id("") == 0
    assert la._activity_id("urn:li:activity:42") == 42


# ============================================================
# 3. The line that named an egress the run had not used
# ============================================================

def test_the_default_run_does_not_claim_a_proxy(la, monkeypatch, tmp_path):
    """`--proxy-slot` defaults to 0, which is a direct connection. Before the
    fix the artifact said "Decodo proxy" on every single run."""
    _stub_run(la, monkeypatch, tmp_path)

    assert la.main() == 0
    header = _artifact(tmp_path)
    assert "Decodo" not in header
    assert "no proxy" in header


def test_a_proxied_run_names_the_slot_it_used(la, monkeypatch, tmp_path):
    """The other direction, so the line cannot be pinned to a constant again."""
    monkeypatch.setenv("DECODO_PROXY_2", "user:pass@gate.example.invalid:7000")
    _stub_run(la, monkeypatch, tmp_path, argv=["--proxy-slot", "2"])

    assert la.main() == 0
    assert "Decodo proxy slot 2" in _artifact(tmp_path)


def test_render_markdown_reports_the_egress_it_is_given(la):
    """The seam directly: the caller states the egress, the renderer prints it."""
    out = la.render_markdown([], "mishahanin", "direct connection, no proxy")

    assert "direct connection, no proxy" in out
    assert "Decodo" not in out


# ============================================================
# 4. The probe that left from a different IP than the browser
# ============================================================

class _FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _stub_requests(monkeypatch, response) -> list[dict]:
    """Replace `requests.get` on the real module the probe imports at call time."""
    import requests

    calls: list[dict] = []

    def _get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(requests, "get", _get)
    return calls


def test_the_probe_goes_out_through_the_proxy_it_is_given(monkeypatch, la):
    """Before the fix the function had no way to be told about the proxy at all,
    so the probe left from the real IP while the browser left through Decodo:
    two source IPs for one `li_at` within seconds of each other."""
    calls = _stub_requests(monkeypatch, _FakeResponse())

    alive, _reason = la.check_session_alive(
        {"li_at": "TOKEN"}, "mishahanin",
        proxy_url="user:pass@gate.example.invalid:7000")  # pragma: allowlist secret

    assert alive
    assert calls[0]["proxies"] == {
        "http": "http://user:pass@gate.example.invalid:7000",  # pragma: allowlist secret
        "https": "http://user:pass@gate.example.invalid:7000",  # pragma: allowlist secret
    }


def test_an_explicit_scheme_on_the_proxy_is_left_alone(monkeypatch, la):
    calls = _stub_requests(monkeypatch, _FakeResponse())

    la.check_session_alive({"li_at": "T"}, "s",
                           proxy_url="http://u:p@gate.example.invalid:7000")

    assert calls[0]["proxies"]["https"] == "http://u:p@gate.example.invalid:7000"


def test_an_unproxied_probe_sends_no_proxies_at_all(monkeypatch, la):
    """The default run must keep going out directly, not through a stale value."""
    calls = _stub_requests(monkeypatch, _FakeResponse())

    la.check_session_alive({"li_at": "T"}, "s")

    assert calls[0]["proxies"] is None


def test_main_hands_the_probe_the_same_proxy_the_browser_launches_with(
        la, monkeypatch, tmp_path):
    """The wiring, end to end. A correct `check_session_alive` still leaves the
    two egresses split if `main` forgets to pass the slot through."""
    monkeypatch.setenv("DECODO_PROXY_3", "user:pass@gate.example.invalid:7003")
    rec = _stub_run(la, monkeypatch, tmp_path, argv=["--proxy-slot", "3"])

    assert la.main() == 0
    passed = rec.probe_calls[0]
    given = passed["kwargs"].get("proxy_url") or (
        passed["args"][0] if passed["args"] else None)
    assert given == "user:pass@gate.example.invalid:7003"
    assert rec.engine.launch_kwargs["proxy"]["server"] == \
        "http://gate.example.invalid:7003"


def test_an_unproxied_run_tells_the_probe_there_is_no_proxy(
        la, monkeypatch, tmp_path):
    rec = _stub_run(la, monkeypatch, tmp_path)

    assert la.main() == 0
    passed = rec.probe_calls[0]
    given = passed["kwargs"].get("proxy_url") or (
        passed["args"][0] if passed["args"] else None)
    assert given is None
    assert "proxy" not in rec.engine.launch_kwargs


# ============================================================
# 5. The benign redirect that read as an invalidated session
# ============================================================

def test_a_canonicalising_redirect_to_the_profile_is_not_a_dead_session(
        monkeypatch, la):
    """LinkedIn 302s `/in/Slug` to `/in/slug/` as ordinary canonicalisation. The
    old test matched the profile path itself, so `main` exited 3 and told the
    operator to re-authenticate a session that was perfectly valid."""
    _stub_requests(monkeypatch, _FakeResponse(
        302, {"Location": "https://www.linkedin.com/in/mishahanin/"}))

    alive, _reason = la.check_session_alive({"li_at": "T"}, "mishahanin")

    assert alive is True


@pytest.mark.parametrize("location", [
    "https://www.linkedin.com/authwall?trk=x",
    "https://www.linkedin.com/login?session_redirect=%2Ffeed",
    "https://www.linkedin.com/uas/login-submit",
])
def test_a_redirect_to_the_wall_is_still_a_dead_session(monkeypatch, la, location):
    """The anchor. Removing the profile clause must not remove the signal that
    the probe exists for."""
    _stub_requests(monkeypatch, _FakeResponse(302, {"Location": location}))

    alive, reason = la.check_session_alive({"li_at": "T"}, "mishahanin")

    assert alive is False
    assert "invalidated" in reason


def test_the_delete_me_cookie_and_the_rate_limit_still_fail_closed(monkeypatch, la):
    """The two verdicts that never depended on the Location header."""
    _stub_requests(monkeypatch, _FakeResponse(
        200, {"Set-Cookie": 'li_at="delete me"; Max-Age=0'}))
    assert la.check_session_alive({"li_at": "T"}, "s")[0] is False

    _stub_requests(monkeypatch, _FakeResponse(429))
    assert la.check_session_alive({"li_at": "T"}, "s")[0] is False


# ============================================================
# 6. A comment that promised a purity two lines of the file break
# ============================================================

_DOCX = ROOT / "scripts" / "generate-client-docx.py"


def test_the_generator_comment_does_not_claim_the_import_is_pure():
    """The two globals the original finding named were genuinely fixed into
    `images_dir()` and `output_path()`. `sys.path.insert(...)` and
    `ensure_venv()` still run at import, so the wider claim beside them was
    false, and a false claim about the past misleads the next audit.

    Asserted on the ASSERTION, not on the phrase: the narrowed comment quotes
    the words it replaced in order to say they were wrong, which is the record
    of why the line changed and has to survive. Scanning for the bare phrase
    made this test fail on its own explanation.
    """
    src = _DOCX.read_text(encoding="utf-8")

    assert "(F-2.1: import stays pure)" not in src, (
        "the comment still asserts a purity two lines of this file break")
    assert "the import is not pure" in src, (
        "the correction that records why the line changed was removed")


def test_the_two_import_time_side_effects_are_still_there():
    """The other jaw. The comment is narrowed because these two run; a change
    that removed them instead would make the narrowed comment wrong the other
    way, and `ensure_venv()` at import is the workspace convention."""
    tree = ast.parse(_DOCX.read_text(encoding="utf-8"))
    top_level_calls = [
        node.value.func
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    names = {f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
             for f in top_level_calls}

    assert "insert" in names, "the sys.path bootstrap left module scope"
    assert "ensure_venv" in names, "the venv guard left module scope"


def test_the_two_paths_are_still_resolved_at_call_time():
    """What the narrowed comment still has to be true about."""
    tree = ast.parse(_DOCX.read_text(encoding="utf-8"))
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}

    assert {"images_dir", "output_path"} <= functions
