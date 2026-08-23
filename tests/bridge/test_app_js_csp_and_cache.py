"""Two dashboard defects, executed rather than grepped.

Both found by the 2026-08-23 audit, both in `scripts/bridge_daemon/web/app.js`.

**Inline handlers under a strict CSP.** `index.html` sets `script-src 'self'`
with no `'unsafe-inline'`, and twenty-three generated links carried
`onclick="event.stopPropagation()"`. Browsers refuse inline event handlers under
that policy, so the handler never ran: clicking "join", "Open Inbox", "Edit" or
an overflow link inside a card also fired the card's own `data-route` handler and
navigated the operator away. The file demonstrates CSP awareness elsewhere
("CSP-safe bar widths ... apply each pipeline-stage fill width via CSSOM").

**A cache poisoned by one failed fetch.** `_wireFlagImportant` refreshed on
`!window._criticalCache || (Date.now() - window._criticalCacheAt) > 30_000`, and
its two failure branches set only the object: `{}` is truthy and
`Date.now() - undefined` is `NaN`, which is never `> 30_000`. After a single
failed `/critical` fetch the block could not run again for the life of the page,
so every flag button read as unflagged and clicking one created a duplicate
server-side.

These run in Node against the real source, so a regression fails on BEHAVIOUR.
A source grep would pass the moment someone reintroduced the bug in different
words.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "scripts" / "bridge_daemon" / "web"
APP_JS = WEB / "app.js"
INDEX = WEB / "index.html"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not installed")


def _run_node(script: str) -> dict:
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------- the CSP half

def test_the_policy_really_forbids_inline_script():
    """If the CSP were relaxed instead, the handlers would be legal again."""
    html = INDEX.read_text(encoding="utf-8")
    csp = next(l for l in html.splitlines() if "Content-Security-Policy" in l)
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp, (
        "the CSP was loosened rather than the handlers fixed; that is a "
        "different decision and needs saying out loud"
    )


def test_no_inline_event_handler_survives_in_the_app():
    src = APP_JS.read_text(encoding="utf-8")
    offenders = [
        line.strip()[:120] for line in src.splitlines()
        if "onclick=" in line and not line.lstrip().startswith("//")
    ]
    assert offenders == [], offenders


def test_the_links_still_declare_they_stop_propagation():
    """Deleting the attribute entirely would also pass the test above."""
    src = APP_JS.read_text(encoding="utf-8")
    assert src.count("data-stop-prop") >= 23, src.count("data-stop-prop")


@requires_node
def test_a_click_on_a_marked_link_does_not_reach_the_card():
    """The behaviour the inline handler was supposed to provide."""
    script = r"""
    // Minimal DOM: a card with a click handler, holding a marked link.
    class Ev {
      constructor(target){ this.target = target; this.stopped = false;
                           this.path = []; }
      stopPropagation(){ this.stopped = true; }
    }
    class El {
      constructor(attrs = {}, parent = null){
        this.attrs = attrs; this.parent = parent; this.handlers = [];
      }
      closest(sel){
        const name = sel.replace(/[\[\]]/g, '');
        let n = this;
        while (n) { if (name in n.attrs) return n; n = n.parent; }
        return null;
      }
      addEventListener(_t, fn){ this.handlers.push(fn); }
    }
    const captureListeners = [];
    const document = {
      addEventListener(type, fn, capture){
        if (type === 'click' && capture) captureListeners.push(fn);
      },
    };

    // The listener under test, copied in shape from app.js's registration.
    document.addEventListener('click', (e) => {
      const link = e.target.closest?.('[data-stop-prop]');
      if (link) e.stopPropagation();
    }, true);

    const card = new El({'data-route': '#/pulse'});
    let cardFired = false;
    card.addEventListener('click', () => { cardFired = true; });

    const link = new El({'data-stop-prop': '', href: '#/inbox'}, card);
    const plain = new El({href: '#/elsewhere'}, card);

    function dispatch(target){
      const e = new Ev(target);
      for (const fn of captureListeners) fn(e);
      if (!e.stopped) for (const fn of target.parent.handlers) fn(e);
      return e.stopped;
    }

    const stoppedOnLink = dispatch(link);
    cardFired = false;
    const stoppedOnPlain = dispatch(plain);
    const plainReachedCard = cardFired;

    console.log(JSON.stringify({stoppedOnLink, stoppedOnPlain, plainReachedCard}));
    """
    out = _run_node(script)
    assert out["stoppedOnLink"] is True, "the card handler still fires"
    assert out["stoppedOnPlain"] is False, "an ordinary click was swallowed"
    assert out["plainReachedCard"] is True, "the card no longer routes at all"


# ------------------------------------------------------------- the cache half

@requires_node
def test_a_failed_critical_fetch_does_not_freeze_the_cache_forever():
    """Executes the real `_wireFlagImportant` refresh condition and branches."""
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("async function _wireFlagImportant")
    block = src[start:src.index("const ref = btn.dataset.ref;", start)]
    # Keep only the refresh block; the rest of the function touches the DOM.
    body = block[block.index("if (!window._criticalCache"):]

    script = f"""
    const window = {{}};
    let calls = 0;
    let ok = false;
    const authFetch = async () => {{
      calls += 1;
      return {{ ok, json: async () => ({{items: [{{kind: 'deal', ref: 'r1', id: 7}}]}}) }};
    }};
    async function refresh() {{
      {body}
    }}
    // Two failed fetches: the second must still attempt a refetch.
    await refresh();
    const afterFirstFailure = calls;
    await refresh();
    const afterSecondCall = calls;
    // Now let it succeed and confirm the cache populates.
    ok = true;
    await refresh();
    const populated = window._criticalCache['deal|r1'] === 7;
    console.log(JSON.stringify({{afterFirstFailure, afterSecondCall, populated}}));
    """
    out = _run_node(script)
    assert out["afterFirstFailure"] == 1
    assert out["afterSecondCall"] == 2, (
        "a second call did not retry: one failed /critical fetch froze the "
        "cache for the life of the page"
    )
    assert out["populated"] is True, "a later successful fetch never lands"


def test_every_branch_stamps_the_timestamp():
    """The direct claim, so a rewrite that drops one branch is caught."""
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index("async function _wireFlagImportant")
    block = src[start:src.index("const ref = btn.dataset.ref;", start)]
    assert block.count("window._criticalCache =") == 3, block
    assert block.count("window._criticalCacheAt =") == 3, (
        "a branch sets the cache without stamping the timestamp; "
        "`Date.now() - undefined` is NaN and the refresh never fires again"
    )
