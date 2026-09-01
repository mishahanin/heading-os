"""The dashboard script: races nobody serialised, and metrics that measured
something other than what their comments claimed.

Found by the 2026-08-23 engine audit, shard `scripts-03-p1`, twenty-five
findings across a 257 KB file. Every one was read against the source before it
was touched, and one was refuted on measurement rather than taken on trust.

There is no JS test runner in this repo, so these are structural checks over the
file plus `node --check`. That is narrower than executing the code and the
docstrings say which of the two each test is; a structural check that reads like
a behavioural one is the defect this workspace calls a scope claim.

**The two classes that mattered.**

*Concurrency, unserialised everywhere.* `renderCurrentPage` is entered from four
places, and each captured its route, awaited a fetch, then wrote the canvas -
so whichever fetch resolved LAST won. Navigate while a slow page is loading and
the URL says B while the canvas shows A, with the wrong nav item carrying
`aria-current`, self-correcting only on the next poll. `checkVersion` sat on a
`setInterval` and could overlap itself, letting two runs both pass the
`prevVersion` test and double-toast. `_wireFlagImportant` was called per row
without awaiting, so fifteen tasks meant fifteen identical `GET /critical` in
flight. And the command palette re-rendered its whole result list on
`mouseenter`, which destroys the node under the pointer and inserts a fresh one
at the same coordinates - the browser then fires `mouseenter` on THAT. A
stationary cursor churned the DOM continuously.

*Telemetry that did not measure what it said.* `tab_time_minutes` feeds the
Phase 1 to Phase 2 adoption gate. The `beforeunload` flush used a plain fetch
with no `keepalive`, which browsers cancel during unload, while the comment
above it promised the last page of every session gets a duration record. And
after the hidden-tab flush nulled the page, the polling loop kept running: the
next component bump re-rendered, called `trackPageView`, found no page in
flight, and started a fresh timer WHILE THE TAB WAS STILL HIDDEN. Background
time flowed straight back into the metric this machinery exists to deflate.

**The rest, each confirmed in the source.** A task with no `priority` threw and
took the whole Tasks page down, on the line directly above one that already
guards the same field; that class name was also the only server value in the
file interpolated into an attribute without `escapeHtml`. No caller anywhere
handled 401, so a daemon restart that rotates the token bricked the dashboard
until a manual reload. Four "Recently X" panels cached their own failure state
permanently. `formatRelative` printed the literal "NaNh ago". Two breadcrumb
callers pre-escaped a value the callee escapes again. A missing `source_page`
round-tripped the string "undefined" back to the server. Both failure paths of
"Continue in session" left the button disabled. Five renders died mid-page if
the server omitted `counts`, three lines from a sibling that guards it. Esc
closed every open layer at once. A failing drill-down was re-requested every
poll forever. Activity times printed in UTC on a dashboard that renders
everything else in `state.tz`. Unknown pipeline stages sorted first as chips and
last as sections. Two links took a server-supplied URL with no scheme gate. The
palette offered a Signals destination that `ROUTES` has no key for. Six
functions and one whole page were defined and never reached, one of them still
rendering functional-looking Mark-sent buttons wired to nothing.

**Refuted.** The new-email toast was reported as keying off the top id, which is
true, but the fix is not the one implied: the toast now fires only for a
conversation id this session has never seen, so clicking Done and promoting the
row beneath it stays silent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
JS_PATH = ROOT / "scripts" / "bridge_daemon" / "web" / "app.js"
JS = JS_PATH.read_text(encoding="utf-8")
# Comments legitimately name the patterns this file forbids.
CODE = re.sub(r"(?m)//.*$", "", re.sub(r"/\*.*?\*/", "", JS, flags=re.S))


def _fn(name: str, source: str = None) -> str:
    """The source of one top-level function, by brace matching.

    The parameter list is skipped by matching ITS parens first. A naive
    "first { after the name" lands inside a destructured parameter
    (`_continueInSession({action, ...})`) or a default (`opts = {}`), and the
    extraction then returns the signature line alone - a test that reads a
    one-line string and finds nothing in it passes or fails for the wrong
    reason. Both happened here on the first run.
    """
    src = JS if source is None else source
    m = re.search(rf"(?m)^(?:async\s+)?function {re.escape(name)}\s*\(", src)
    assert m, f"{name} is gone; this test no longer checks anything"
    k, depth = m.end() - 1, 0
    while True:                       # walk the parameter list to its close
        if src[k] == "(":
            depth += 1
        elif src[k] == ")":
            depth -= 1
            if depth == 0:
                break
        k += 1
    i = src.index("{", k)
    depth, j = 0, i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1


# --- the file parses ---------------------------------------------------------

def test_the_script_parses():
    """No JS runner here, so `node --check` is the whole syntax gate. Six dead
    functions and a page were removed by brace matching on 2026-08-24; a
    mismatched brace would ship silently without this."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--check", str(JS_PATH)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


# --- concurrency -------------------------------------------------------------

def test_a_stale_render_cannot_overwrite_the_page_you_navigated_to():
    src = _fn("renderCurrentPage")
    assert "_renderGeneration" in src, "the render generation counter is gone"
    assert re.search(r"myGeneration !== _renderGeneration", src), (
        "renderCurrentPage awaits a fetch and then writes the canvas without "
        "checking whether a newer render started; the slower one wins"
    )
    # The guard must sit AFTER the await it protects.
    assert src.index("await fn(params)") < src.index("myGeneration !== _renderGeneration")


def test_the_poll_cannot_overlap_itself():
    src = _fn("checkVersion")
    # Both halves, named separately. Asserting only that the flag APPEARS was
    # satisfied by the `finally` that clears it, so deleting the guard itself
    # left the test green - caught by mutation on 2026-08-24.
    assert re.search(r"if \(_checkVersionInFlight\) return;", src), (
        "the early return is gone; two polls can run at once and both act on "
        "the same version bump"
    )
    assert re.search(r"finally \{[^}]*_checkVersionInFlight = false", src), (
        "the in-flight flag is never released, so the poll runs exactly once"
    )


def test_the_command_palette_does_not_re_render_under_the_cursor():
    src = _fn("_cmdkRender", CODE)
    hover = src[src.index("mouseenter"):]
    assert "_cmdkRender(" not in hover[:400], (
        "hovering re-renders the list, which replaces the node under the "
        "pointer and re-fires mouseenter on its replacement"
    )
    assert "_cmdkSetActive" in hover[:400]


def test_the_critical_cache_is_fetched_once_per_render_not_once_per_row():
    src = _fn("_wireFlagImportant")
    # The SHARING idiom, not the name. Asserting the identifier appears was
    # satisfied by the two later references even after the assignment that
    # creates the shared promise was mutated away - caught on 2026-08-24.
    assert re.search(r"window\._criticalFetch = window\._criticalFetch \|\|", src), (
        "each row fires its own GET /critical; callers do not await this "
        "function, so N rows mean N identical requests in flight"
    )
    assert "await window._criticalFetch" in src, "the shared promise is never awaited"


def test_unflagging_never_silently_does_nothing():
    src = _fn("_wireFlagImportant")
    assert "if (!currentId)" in src, (
        "when the cache has lost the id the unflag branch is skipped entirely: "
        "no request, no toast, and the button is re-enabled by `finally`"
    )


# --- telemetry measures what its comments claim ------------------------------

def test_the_unload_flush_can_outlive_the_document():
    src = _fn("_bridgeFlushLastPage")
    assert "keepalive: true" in src, (
        "the beforeunload flush is a plain fetch; browsers cancel those during "
        "unload, so the comment's promise about the last page of every session "
        "does not hold"
    )


def test_a_hidden_tab_does_not_accumulate_tab_time():
    src = _fn("trackPageView")
    assert "visibilityState === 'hidden'" in src, (
        "a poll-triggered re-render while hidden starts a fresh page timer, so "
        "background time counts as tab time"
    )
    # The check must precede the timestamp it prevents.
    assert src.index("visibilityState") < src.index("_bridgeLastPageStartTs = Date.now()")


# --- shape assumptions --------------------------------------------------------

def test_no_render_indexes_counts_without_a_default():
    """Five renders died mid-page on a payload without `counts`, three lines
    from a sibling that already guarded it."""
    bad = [ln.strip() for ln in CODE.splitlines()
           if ("d.counts || {}" not in ln
               and (re.search(r"\bd\.counts\s*\[", ln)
                    or re.search(r"Object\.(entries|keys|values)\(d\.counts\)", ln)))]
    assert not bad, bad


def test_a_task_without_a_priority_does_not_take_the_page_down():
    # `\b` matters: `it.priority.toLowerCase()` in _taskDoneToggle is correct
    # and CONTAINS the bad string as a substring.
    assert not re.search(r"\bt\.priority\.toLowerCase\(\)", CODE), (
        "the unguarded call is back; the line below it already knows the field "
        "can be absent"
    )


def test_the_priority_class_is_escaped_like_every_other_attribute():
    m = re.search(r"const priClass = `task-pri-\$\{([^}]+)\}`", CODE)
    assert m, "the priority class construction moved; re-point this test"
    assert "escapeHtml" in m.group(1), (
        "a server value reaches a class attribute unescaped; a priority of "
        '`P2\" onmouseover=...` is attribute injection'
    )


# --- recovery, not permanent failure -----------------------------------------

def test_a_rotated_token_does_not_brick_the_dashboard():
    """Structural, and narrowed after it was caught measuring nothing.

    This was `assert "401" in src and "_bootstrap" in src`, which asks whether
    two strings EXIST inside `authFetch`, never whether the branch they belong
    to can run. Measured 2026-08-31 by making the recovery unreachable while
    leaving both strings in place:

        -  if (r.status !== 401) return r;
        +  if (r.status !== 401 || true) return r;  // MUTATION

        tests/bridge tests/inbox_pulse tests/contract -> 1706 passed, 1 skipped

    Byte-identical to the baseline. Every 401 now returns straight to the
    caller, which is the exact "fails forever with no re-bootstrap and no
    reload" the old failure message described.

    The gate expression is pinned literally here, and the behaviour is executed
    in the sibling below. This half is what holds on a clone with no node.
    """
    src = _fn("authFetch", CODE)
    assert re.search(r"if \(r\.status !== 401\) return r;", src), (
        "the 401 gate is not the plain status test any more. Anything wider "
        "makes the re-bootstrap below it unreachable while leaving it in the "
        f"source: {src}"
    )
    gate = src.index("if (r.status !== 401) return r;")
    assert gate < src.index("_bootstrap"), (
        "the re-bootstrap runs before the status is even checked")
    assert src.index("_bootstrap") < src.rindex("_rawAuthFetch"), (
        "nothing retries the request after the token is refreshed, so the "
        "caller still gets the 401 it started with")


def test_a_rotated_token_is_re_bootstrapped_and_the_request_retried():
    """The same claim, executed. `authFetch` runs under node against a stub
    that answers 401 once and 200 after, so the retry, the token refresh and
    the absence of a page reload are read off real behaviour rather than
    inferred from two substrings."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    harness = f"""
    let _reauthInFlight = null;
    const state = {{ token: 'stale' }};
    const seen = [];
    let reloads = 0;
    const location = {{ reload: () => {{ reloads += 1; }} }};
    const console_ = console;
    async function _rawAuthFetch(path, opts) {{
      seen.push(state.token);
      return {{ status: seen.length === 1 ? 401 : 200 }};
    }}
    async function fetch(url) {{
      return {{ json: async () => ({{ token: 'rotated' }}) }};
    }}
    {_fn("authFetch")}
    authFetch('/pulse').then(r => console_.log(JSON.stringify({{
      status: r.status, seen, reloads, token: state.token,
    }})));
    """
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [node, "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    assert got["status"] == 200, (
        f"a 401 reached the caller instead of being recovered: {got}")
    assert got["seen"] == ["stale", "rotated"], (
        f"the request was not retried with the refreshed token: {got['seen']}")
    assert got["token"] == "rotated", got  # noqa: S105 - the fixture's marker value, not a credential
    assert got["reloads"] == 0, (
        "the page was reloaded even though the retry succeeded; a reload throws "
        "away whatever the operator was doing")


def test_a_failed_panel_load_is_not_cached_forever():
    """`dataset.loaded` was stamped after the try/catch, so one transient
    failure froze the panel on its error message until a full re-render."""
    for name in ("_inboxDismissedToggle", "_inboxDeferredToggle",
                 "_apprSentToggle", "_taskDoneToggle"):
        src = _fn(name)
        stamp = src.index("dataset.loaded = '1'")
        catch = src.index("} catch (e) {")
        assert stamp < catch, f"{name} still caches its own failure state"


def test_a_failed_drill_down_is_not_re_requested_on_every_poll():
    src = _fn("_inboxToggleExpand")
    after_fetch = src[src.index("/inbox/conversation"):]
    assert "_inboxExpandedId = null" in after_fetch[:600], (
        "the expanded id survives a failed fetch, so renderInbox re-invokes "
        "this toggle every poll and re-inserts the error panel forever"
    )


def test_the_launch_button_is_re_enabled_on_failure():
    src = _fn("_continueInSession")
    assert src.count("btn.disabled = false") >= 3, (
        "only the success path re-enables; a failed click on the human "
        "send-gate control needs a page re-render before it can be retried"
    )


# --- honest output ------------------------------------------------------------

def test_a_bad_timestamp_does_not_render_the_word_nan():
    src = _fn("formatRelative")
    assert "isNaN" in src and "Math.max(0" in src, (
        "an unparseable stamp prints the literal 'NaNh ago' and a server clock "
        "running ahead prints a negative age"
    )


def test_nothing_pre_escapes_a_value_the_breadcrumb_escapes_again():
    """Both forms: escaped inline at the call, and escaped into a variable that
    is then passed. Only the first was checked at first, and the live defect was
    the second - `raiseLabel` on the Investors page."""
    assert "escapeHtml(_breadcrumb" not in CODE
    for m in re.finditer(r"_breadcrumb\(([^\n]*)\)", CODE):
        args = m.group(1)
        assert "escapeHtml" not in args, (
            f"_breadcrumb escapes its own arguments; `R&D` becomes `R&amp;D`: "
            f"{m.group(0)[:110]}"
        )
        # Resolve any bare identifier argument back to where it was built.
        for ident in re.findall(r"(?<![.\w])([a-z][A-Za-z0-9_]*)\s*(?:,|\)|$)", args):
            decl = re.search(rf"(?m)^\s*const {re.escape(ident)} = .*$", CODE)
            if decl and "escapeHtml" in decl.group(0):
                raise AssertionError(
                    f"`{ident}` is pre-escaped and then handed to _breadcrumb, "
                    f"which escapes it again:\n  {decl.group(0).strip()[:120]}"
                )


def test_a_missing_source_page_is_not_round_tripped_as_the_word_undefined():
    m = re.search(r'data-source-page="\$\{([^}]+)\}"', CODE)
    assert m and "||" in m.group(1), (
        "an absent source_page becomes the truthy string 'undefined', which "
        "Re-flag then POSTs back to /critical/mark"
    )


def test_the_sea_state_tooltip_has_no_undefined_in_it():
    assert "${d.overdue_total}" not in CODE and "${d.events_today}" not in CODE


def test_activity_times_use_the_operator_timezone():
    assert "iso.match(/T(\\d{2}):(\\d{2})/)" not in CODE, (
        "activity entries slice HH:MM out of an ISO string, which prints UTC "
        "on a dashboard that renders everything else in state.tz"
    )


# --- ordering and links -------------------------------------------------------

def test_unknown_pipeline_stages_sort_the_same_way_twice():
    assert "order.indexOf(a[0]) - order.indexOf(b[0])" not in CODE, (
        "indexOf returns -1 for an unknown stage, sorting it FIRST as a chip "
        "while the grouping below appends it LAST"
    )


def _href_interpolations(source: str) -> list[str]:
    """Every `href="${ ... }"` expression, by brace matching.

    A regex cannot do this. The previous version of the test below used one, and
    it required the literal shape `href="${escapeHtml( ... )}"` with at most one
    nested call level, so a link written without `escapeHtml`, or with two
    levels of nesting, matched nothing and passed a test whose name says
    "every".
    """
    found = []
    marker = 'href="${'
    at = source.find(marker)
    while at != -1:
        i = at + len(marker)
        depth = 1
        while i < len(source) and depth:
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
            i += 1
        found.append(source[at + len(marker):i - 1])
        at = source.find(marker, i)
    return found


# Every `href="${...}"` in app.js, and the gate that makes it safe. `escapeHtml`
# is NOT a gate: it neutralises markup, not a `javascript:` scheme, in a page
# that holds the bearer token in memory.
#
#   "_safeHref"  -- the scheme allow-list at the top of app.js
#   "scheme-if"  -- the expression is only reached inside a `startsWith('http')`
#                   conditional, so the value cannot carry another scheme
#   "client"     -- the value never came from the server: a `#/...` literal, or
#                   a local table this file owns
#
# A new href site fails here until its author classifies it, the way
# `tests/test_scope_claims.py` makes a new coverage claim fail until answered.
#
# The key is the EXPRESSION, so sites that share one share a classification:
# `escapeHtml(href)` covers three. The companion test below pins what every
# local named `href` is built from, so that shared key cannot quietly start
# meaning a server value.
HREF_GATES = {
    "escapeHtml(baseRoute)": "client",
    "escapeHtml(link)": "client",
    "escapeHtml(item.location)": "scheme-if",
    "escapeHtml(_safeHref(it.link, '#/pulse'))": "_safeHref",
    "escapeHtml(href)": "client",
    "escapeHtml(loc)": "scheme-if",
    "escapeHtml(_safeHref(_openHref(it), '#/critical'))": "_safeHref",
    "escapeHtml(e.location)": "scheme-if",
    "escapeHtml(location)": "scheme-if",
}


def _unclassified_hrefs(source: str) -> list[str]:
    """Href sites in `source` that HREF_GATES does not name a gate for."""
    return sorted({s for s in _href_interpolations(source)
                   if s not in HREF_GATES})


def _stale_href_gates(source: str) -> list[str]:
    """HREF_GATES keys that `source` no longer contains."""
    sites = set(_href_interpolations(source))
    return sorted(k for k in HREF_GATES if k not in sites)


def test_every_server_supplied_link_passes_a_scheme_gate():
    """Structural, not behavioural: this reads app.js, it does not run it.

    Measured 2026-08-28 against the file: 11 sites, 9 distinct expressions, and
    every one of them gated. The defect was that the check could not have seen a
    twelfth written without `escapeHtml`.
    """
    assert "function _safeHref" in CODE
    sites = _href_interpolations(CODE)
    assert len(sites) >= 9, (
        f"the href sites moved or the brace matcher broke; found {len(sites)}")
    assert not _unclassified_hrefs(CODE), (
        f"new href interpolation(s) with no declared scheme gate. Route the "
        f"value through _safeHref, or add it to HREF_GATES naming the gate that "
        f"already protects it: {_unclassified_hrefs(CODE)}")
    assert not _stale_href_gates(CODE), (
        f"HREF_GATES still classifies expressions app.js no longer has, so the "
        f"registry has stopped describing the file: {_stale_href_gates(CODE)}")
    for site, gate in HREF_GATES.items():
        if gate == "_safeHref":
            assert "_safeHref" in site


def test_every_local_named_href_is_a_client_built_route():
    """The shared `escapeHtml(href)` key above is classified `client`. Three
    sites lean on that one word, so pin what the name is actually assigned."""
    assigned = re.findall(r"(?m)^\s*const href = (.)", CODE)
    assert len(assigned) >= 2, "the href locals moved; re-point this test"
    assert set(assigned) == {"`"}, (
        "a local named `href` is assigned something other than a template "
        "literal, so `escapeHtml(href)` may no longer be a client-built route")
    for literal in re.findall(r"(?m)^\s*const href = `([^`]*)`", CODE):
        assert literal.startswith("#/"), (
            f"a local named `href` no longer starts at a hash route: {literal}")


def test_only_one_scheme_gate_exists():
    """A second copy is the one that stops being fixed."""
    assert CODE.count("_safeHref = ") + CODE.count("function _safeHref") == 1


# --- nothing offered that does not exist --------------------------------------

def test_every_palette_destination_is_a_real_route():
    routes = set(re.findall(r"(?m)^  '?([a-z-]+)'?: render", CODE))
    assert routes, "the ROUTES table moved; re-point this test"
    offered = re.findall(r"hash: '#/([a-z-]+)'", CODE)
    assert offered, "the palette item list moved; re-point this test"
    missing = sorted({h for h in offered if h not in routes})
    assert not missing, (
        f"the command palette offers destinations ROUTES has no key for, so "
        f"selecting one silently renders Pulse: {missing}"
    )


def test_no_dead_renderer_is_left_looking_live():
    """Each of these was defined and never called; one still rendered
    functional-looking Mark-sent buttons that were wired to nothing."""
    # _readTweaks is not from the audit: the reachability test below found it.
    for name in ("pulseApprovalsHtml", "_pulseApprovalToggle",
                 "_pulseApprovalInlineMarkSent", "pulseWatchHtml",
                 "launchAction", "nextMeetingHtml", "renderStub",
                 "_readTweaks"):
        assert name not in JS, f"{name} is back"


def test_every_declared_function_is_reachable():
    """The general form of the test above, so the next dead renderer is caught
    without being named. A function whose identifier appears exactly once in
    the file is defined and never used."""
    declared = re.findall(r"(?m)^(?:async\s+)?function ([A-Za-z_][A-Za-z0-9_]*)", CODE)
    orphans = [n for n in declared if len(re.findall(rf"\b{re.escape(n)}\b", CODE)) == 1]
    assert not orphans, f"defined and never called: {sorted(orphans)}"


# --- the keyboard help is derived, not a second hand-kept list ----------------
#
# Shard `scripts-03-p2` reported the help overlay advertising six screens that
# "exist nowhere in this app" (`g m`, `g a`, `g o`, `g s`, `g h`, `g l`). That
# is REFUTED: it was read from index.html's sidebar alone, and ROUTES defines
# critical, approvals, conversations, studio, threads and library. The audit
# flagged its own gap ("verify against app.js before fixing") and was right to.
#
# Measuring it turned up the inverse defect. KBD_NAV carried fifteen shortcuts
# and the hand-written table listed fourteen: `g e` (Contacts) worked and was
# documented nowhere, so the only way to find it was to read the source. The
# rows are now built from KBD_NAV, which is the single place a g-shortcut is
# defined, so the two lists can no longer disagree.

def _kbd_nav_pairs():
    block = re.search(r"const KBD_NAV = \{(.*?)\n\};", CODE, re.S)
    assert block, "KBD_NAV moved; re-point this test"
    return re.findall(r"(?m)^\s*(\w+):\s*'#/([a-z-]+)'", block.group(1))


def test_every_keyboard_shortcut_lands_on_a_real_route():
    routes = set(re.findall(r"(?m)^  '?([a-z-]+)'?: render", CODE))
    assert routes, "the ROUTES table moved; re-point this test"
    dead = sorted({page for _key, page in _kbd_nav_pairs() if page not in routes})
    assert not dead, (
        f"these g-shortcuts navigate to a hash ROUTES has no key for, so "
        f"pressing them silently renders Pulse: {dead}"
    )


def test_the_help_overlay_is_generated_from_the_shortcut_table():
    """A hand-written copy of KBD_NAV is a list that drifts, and it had."""
    html = (Path(__file__).resolve().parents[2] / "scripts" / "bridge_daemon"
            / "web" / "index.html").read_text(encoding="utf-8")
    assert "<kbd>g</kbd> <kbd>" not in html, (
        "the navigation rows are hand-written again; build them from KBD_NAV "
        "so a new shortcut cannot ship undocumented"
    )
    assert 'id="kbd-help-table"' in html, "the generator has no anchor to fill"
    assert "_buildKbdHelpNav" in CODE
    assert "Object.entries(KBD_NAV)" in CODE, (
        "the builder must read KBD_NAV itself; a copy of the pairs would be the "
        "same defect one file over"
    )


def test_the_help_overlay_is_built_before_it_is_shown():
    """A builder nothing calls documents nothing."""
    fn = _fn("_toggleKbdHelp")
    assert "_buildKbdHelpNav()" in fn, (
        "the rows are only inserted on first open; if the toggle stops calling "
        "the builder the panel shows a heading over an empty table"
    )


def test_every_shortcut_gets_a_readable_label():
    """The label lookup is CMDK_DEFAULT_ITEMS; a shortcut missing from it must
    still get a row rather than vanish, which is the defect being fixed."""
    labelled = set(re.findall(r"hash: '#/([a-z-]+)'", CODE))
    pages = {page for _key, page in _kbd_nav_pairs()}
    unlabelled = sorted(pages - labelled)
    assert not unlabelled, (
        f"no palette label for {unlabelled}; the row falls back to the raw hash"
    )
    assert "labelFor.get(hash) ||" in CODE, (
        "the fallback is what stops a missing label from hiding a live shortcut"
    )


def test_the_generated_rows_escape_their_own_values():
    fn = _fn("_buildKbdHelpNav")
    assert fn.count("escapeHtml(") == 2, (
        "both the key and the label are interpolated into HTML"
    )
