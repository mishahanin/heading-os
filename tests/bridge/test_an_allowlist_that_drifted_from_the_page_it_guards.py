"""The server's page allowlist must equal the dashboard's route table.

`ALLOWED_RETURN_PAGES` in `scripts/bridge_daemon/app.py` gates two endpoints:
`/return`, which opens a browser window at `#/<page>`, and
`/telemetry/page-view`, which records how long the operator spent on a page.
Both names come from the same place, the `ROUTES` table in `web/app.js`, and
neither endpoint can be right if the two lists disagree.

They disagreed in both directions, measured 2026-08-29 against the shipped tree:

    reported by the UI but REFUSED by the server: ['critical']
    allowed by the server with NO renderer      : ['signals', 'spaces']

`critical` is the worse half. The page shipped in Phase 1.140 with a renderer, a
Pulse KPI tile that links to it and the `m` keyboard shortcut, and it was never
added to the allowlist. So `POST /telemetry/page-view {"page": "critical"}`
answered 422; `_bridgeFlushLastPage` catches the failure and returns, by design,
because telemetry must not block the UI; and the event reached neither
`usage.jsonl` nor `adoption.summarize`. Eighteen half-hour page visits produced
seventeen events.

That is not just a shortfall in a number. `browser_first` is decided by the FIRST
event of the local day, so a morning that opened on the Critical page recorded
the later terminal launch as the day's first event, and the day counted AGAINST
`browser_first_pct_weekdays`. Measured on one such morning:

    with the page-view refused: browser_first_mornings=0  tab_time=0.0
    with the page-view kept   : browser_first_mornings=1  tab_time=25.0

`browser_first_pass` is one of the three booleans in `gate.all_pass`, which is
what `/bridge-health --gate` reads to decide Phase 1 to Phase 2. A gate fed by a
silently truncated signal reports a decision it did not measure.

The other direction is quieter. The `/signals` page was folded into Pulse on
2026-06-22 and `spaces` never had a renderer in any shipped version, yet both
stayed on the allowlist, so `POST /return {"target_page": "signals"}` answered
200 and opened a window rendering Pulse under a `#/signals` address.

Why the six existing `/telemetry/page-view` tests in `test_endpoints.py` did not
catch it: every positive case used `"pulse"` or `"inbox"`, so the guard was
exercised on the passing side for two of nineteen names, and the single negative
case fed `"../../etc/passwd"`. A path-traversal string is a straw man. No
renderer would ever send it, so refusing it proves the allowlist rejects
SOMETHING while proving nothing about whether it matches the shipped route
table. `test_return_rejects_unknown_target_page` has the identical shape. Nothing
in `tests/bridge/` derived the expected set from `web/app.js`, so the two lists
were free to drift, and did.

This file derives it. The negative cases below use `signals` and `spaces`, names
that really were accepted and really are dead, rather than a string no client
sends.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")  # F-7.1: skip on a core-only clone

from fastapi.testclient import TestClient

from scripts.bridge_daemon import adoption
from scripts.bridge_daemon.app import ALLOWED_RETURN_PAGES, build_app
from scripts.bridge_daemon.state import State
from scripts.utils.workspace import get_default_tz

APP_JS = Path(__file__).resolve().parents[2] / "scripts" / "bridge_daemon" / "web" / "app.js"

# A page with no renderer. `#/spaces` resolves through `ROUTES[route] ||
# renderPulse`, so the window opens on Pulse with the wrong address showing.
DEAD_PAGES = ("signals", "spaces")


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _routes() -> set[str]:
    """The key set of the shipped `ROUTES` table: every page with a renderer.

    Keys are bare identifiers except `'action-queue'`, which is quoted because
    of the hyphen, so the optional quote in the pattern is load-bearing.
    """
    block = re.search(r"^const ROUTES = \{(.*?)^\};", _app_js(), re.S | re.M)
    assert block, "the ROUTES table is no longer a `const ROUTES = {...};` block"
    return {m.group("key") for m in re.finditer(
        r"^\s*'?(?P<key>[A-Za-z][\w-]*)'?\s*:", block.group(1), re.M)}


def _reported_pages() -> set[str]:
    """Every page the frontend posts a page-view for.

    Every call site passes a single-quoted literal. `_bridgeFlushLastPage`
    forwards whatever `trackPageView` last stored, so this literal set is the
    complete set of names that can reach the endpoint.
    """
    return set(re.findall(r"trackPageView\('([^']+)'\)", _app_js()))


def _client(workspace_root, token="t1"):  # noqa: S107  test fixture default, not a secret
    app = build_app(workspace_root=workspace_root, state=State(), token=token,
                    user_slug="misha", data_root=workspace_root)
    return TestClient(app, base_url="http://127.0.0.1")


def _head(token="t1"):  # noqa: S107  test fixture default, not a secret
    return {"Authorization": f"Bearer {token}"}


def _events(workspace_root) -> list[dict]:
    path = workspace_root / ".daemon-state" / "usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# The parsers must not silently match nothing.
# --------------------------------------------------------------------------

def test_the_route_parser_finds_the_shipped_table():
    """A regex that matched nothing would make every set comparison below vacuous.

    Two independent anti-vacuity checks: a floor on the count, and three names
    of very different shape. `action-queue` is the quoted, hyphenated key.
    """
    routes = _routes()
    assert len(routes) >= 15, f"only {len(routes)} routes parsed out of app.js: {sorted(routes)}"
    for name in ("pulse", "critical", "action-queue"):
        assert name in routes, f"{name!r} missing from the parsed ROUTES table"


def test_the_reported_page_parser_finds_the_call_sites():
    """Same guard for the `trackPageView` scan."""
    reported = _reported_pages()
    assert len(reported) >= 15, f"only {len(reported)} reported pages parsed: {sorted(reported)}"
    assert "critical" in reported, "the Critical page no longer reports a page-view"


def test_the_dead_pages_really_have_no_renderer():
    """`signals` and `spaces` are the negative cases, so their deadness is a fact
    this file must check rather than remember. Restoring either renderer must
    turn the negative tests red, not leave them asserting against a live page."""
    routes = _routes()
    for page in DEAD_PAGES:
        assert page not in routes, (
            f"{page!r} has a renderer again. Add it to ALLOWED_RETURN_PAGES and "
            f"remove it from DEAD_PAGES in this file, in the same change.")


# --------------------------------------------------------------------------
# The invariant, both directions.
# --------------------------------------------------------------------------

def test_every_renderable_page_is_on_the_server_allowlist():
    """The `critical` half of the drift. A page the operator can open must be a
    page the server will record and return to."""
    missing = sorted(_routes() - set(ALLOWED_RETURN_PAGES))
    assert not missing, (
        f"{len(missing)} page(s) render in the dashboard but are refused by the "
        f"server: {missing}. /telemetry/page-view answers 422 for each, the "
        f"browser swallows it, and the time spent there never reaches the "
        f"adoption gate. Add them to ALLOWED_RETURN_PAGES in "
        f"scripts/bridge_daemon/app.py.")


def test_every_allowed_page_has_a_renderer():
    """The `signals` / `spaces` half. Allowing a name with no renderer means
    /return opens a window showing Pulse under someone else's address."""
    orphans = sorted(set(ALLOWED_RETURN_PAGES) - _routes())
    assert not orphans, (
        f"{len(orphans)} allowed page(s) have no renderer in web/app.js: "
        f"{orphans}. /return answers 200 and opens a window that falls through "
        f"to Pulse. Remove them from ALLOWED_RETURN_PAGES in the same change "
        f"that removed the renderer.")


def test_the_reported_page_set_matches_the_route_table():
    """Every renderer reports itself, and nothing reports a page that cannot
    render. This is what lets the two tests above stand in for the endpoint."""
    assert _reported_pages() == _routes()


# --------------------------------------------------------------------------
# Driven through the real endpoints, not set arithmetic.
# --------------------------------------------------------------------------

def test_every_page_the_frontend_reports_is_accepted_and_recorded(workspace_root):
    """Set equality could hold while the endpoint consulted some other list.

    This posts each name the shipped frontend can actually send and counts the
    events that land on disk.
    """
    client = _client(workspace_root)
    reported = sorted(_reported_pages())
    refused = []
    for page in reported:
        r = client.post("/telemetry/page-view", headers=_head(),
                        json={"page": page, "duration_s": 1800})
        if r.status_code != 200:
            refused.append((page, r.status_code, r.json().get("detail")))

    assert not refused, f"the server refused pages the dashboard reports: {refused}"

    recorded = [e["page"] for e in _events(workspace_root) if e["event"] == "page_view"]
    assert recorded == reported, (
        f"{len(recorded)} of {len(reported)} page-views reached usage.jsonl")


def test_return_opens_every_renderable_page(workspace_root):
    """`/return` must reach any page the dashboard can show.

    `webbrowser.open` is patched, and that is not optional. `/return` REALLY
    OPENS A BROWSER WINDOW: `app.py` calls `webbrowser.open(url, new=0)`. The
    first version of this test omitted the patch, so every full-suite run opened
    eighteen windows on the operator's desktop, once per page in `ROUTES`. The
    operator found it, not the suite. Every other `/return` test in
    `tests/bridge/test_endpoints.py` had the patch; this one was written without
    looking at them.

    `tests/bridge/test_no_test_opens_a_real_browser.py` now fails any test that
    posts to `/return` without it, so the next author cannot repeat this.
    """
    client = _client(workspace_root)
    with pytest.MonkeyPatch.context() as mp, patch("webbrowser.open") as opened:
        mp.setenv("BRIDGE_PORT", "31415")
        refused = [
            (page, r.status_code)
            for page in sorted(_routes())
            if (r := client.post("/return", headers=_head(),
                                 json={"session_id": "s", "target_page": page})
                ).status_code != 200
        ]
    assert not refused, f"/return refused renderable pages: {refused}"
    # The patch is load-bearing, so prove it intercepted rather than sat unused.
    assert opened.call_count == len(_routes())


@pytest.mark.parametrize("page", DEAD_PAGES)
def test_a_page_with_no_renderer_is_refused_by_both_endpoints(workspace_root, page):
    """The real negative case. Both of these answered 200 before 2026-08-29.

    A straw-man string like `../../etc/passwd` cannot tell a matching allowlist
    from a stale one; a name that was genuinely on the list and is genuinely
    dead can.
    """
    client = _client(workspace_root)

    r = client.post("/telemetry/page-view", headers=_head(),
                    json={"page": page, "duration_s": 60})
    assert r.status_code == 422, f"page-view still accepts the dead page {page!r}"

    with patch("webbrowser.open") as opened:
        r = client.post("/return", headers=_head(),
                        json={"session_id": "s", "target_page": page})
    assert r.status_code == 422, f"/return still opens the dead page {page!r}"
    # The point of the allowlist is which names reach `webbrowser.open`, so the
    # window is the thing to assert about, not only the status code.
    opened.assert_not_called()

    assert not _events(workspace_root), "a refused page must write no telemetry"


# --------------------------------------------------------------------------
# The consequence the drift had, end to end.
# --------------------------------------------------------------------------

def test_the_critical_page_records_a_page_view(workspace_root):
    """The specific regression, with the recorded event inspected."""
    client = _client(workspace_root)
    r = client.post("/telemetry/page-view", headers=_head(),
                    json={"page": "critical", "duration_s": 1500})
    assert r.status_code == 200, r.json()

    events = _events(workspace_root)
    assert len(events) == 1
    assert events[0]["event"] == "page_view"
    assert events[0]["page"] == "critical"
    assert events[0]["duration_s"] == 1500


def test_a_morning_opened_on_critical_counts_as_browser_first(workspace_root):
    """The gate consequence, through the real summarizer.

    `browser_first` reads the FIRST event of the local day. A refused page-view
    is not simply missing from the total: it hands the day's first-event slot to
    the later terminal launch, and the whole weekday counts against
    `browser_first_pct_weekdays`, one of the three booleans in `all_pass`.
    """
    today = datetime.now(get_default_tz())
    morning = (today - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0)
    usage = workspace_root / ".daemon-state" / "usage.jsonl"

    def write(rows):
        usage.write_text("".join(json.dumps(r) + "\n" for r in rows))

    launch = {"ts": (morning + timedelta(minutes=25)).isoformat(),
              "event": "launch", "session_id": "s"}
    view = {"ts": morning.isoformat(), "event": "page_view",
            "page": "critical", "duration_s": 1500}

    # The world the defect produced: the view was refused, so only the launch
    # survives and the day reads terminal-first.
    write([launch])
    lost = adoption.summarize(workspace_root, days=2, today=today.date())
    assert lost["totals"]["browser_first_mornings"] == 0
    assert lost["totals"]["tab_time_total_minutes"] == 0.0

    # The world with the fix: the same morning now reads browser-first and the
    # 25 minutes count.
    write([view, launch])
    kept = adoption.summarize(workspace_root, days=2, today=today.date())
    assert kept["totals"]["browser_first_mornings"] == 1
    assert kept["totals"]["tab_time_total_minutes"] == 25.0

    # And the endpoint really does produce that second world.
    usage.unlink()
    client = _client(workspace_root)
    assert client.post("/telemetry/page-view", headers=_head(),
                       json={"page": "critical", "duration_s": 1500}
                       ).status_code == 200
    assert [e["page"] for e in _events(workspace_root)] == ["critical"]
