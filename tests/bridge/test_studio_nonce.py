"""F-M1/F-L5: /studio/image must accept a short-lived, single-use nonce, not the
bearer token in a query param. The nonce mint endpoint is bearer-authed; the image
endpoint validates and consumes the nonce. The bearer token must never appear in the
image URL the frontend constructs.

## The frontend half could not see the defect it is named for (2026-08-31)

`test_image_url_in_frontend_carries_nonce_not_bearer` was two substring checks
over `app.js`: one exact literal that must be ABSENT, `&t=${encodeURIComponent(
state.token`, and the word `image-nonce` that must be PRESENT. Neither
establishes what the name claims.

Measured by deleting the mint entirely and putting the bearer back in the URL
under a different spelling, which is the whole point - a defect is written in
whatever the author reached for, not in a fixed phrase:

    -  const r = await authFetch('/studio/image-nonce', { method: 'POST' });
    -  if (!r.ok) return '';
    -  const { nonce } = await r.json();
    -  return `/studio/image?path=${encodeURIComponent(path)}&n=${encodeURIComponent(nonce)}`;
    +  return `/studio/image?path=${encodeURIComponent(path)}&t=${state.token}`;

    tests/bridge tests/inbox_pulse tests/contract  ->  1706 passed, 1 skipped

Byte-identical to the baseline. The absent-literal check walked past
`${state.token}` because it demanded `${encodeURIComponent(state.token`, and
the present-check was satisfied by the COMMENT above the function, which names
`POST /studio/image-nonce` in prose. The mutation leaves that comment in place,
so the one positive assertion in the test was reading documentation.

Two replacements, and the split is deliberate. The structural one strips
comments and asks about the function body, so it holds on a clone with no node.
The behavioural one runs `_studioImgUrl` under node with a stubbed `authFetch`
and reads the URL it returns, which is the only check that can see a spelling
nobody has thought of yet.
"""
import json
import re
import shutil
import subprocess  # nosec B404 - fixed argv, never shell=True
import time
from pathlib import Path

import pytest
pytest.importorskip("fastapi")  # F-7.1: skip on a core-only clone (needs the dashboard extra)

from fastapi.testclient import TestClient

from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State

WEB = Path(__file__).resolve().parent.parent.parent / "scripts" / "bridge_daemon" / "web"
APP_JS = WEB / "app.js"


def _js_function(name: str, source: str) -> str:
    """The source of one top-level `async function name(...)`, by brace matching.

    The parameter list is matched first, so a destructured parameter or a `= {}`
    default cannot be mistaken for the body's opening brace. Same shape as the
    helper in `test_the_dashboard_script_says_what_it_does.py`; kept local
    because importing across two test modules to share fifteen lines couples
    them for no gain.
    """
    m = re.search(rf"(?m)^(?:async\s+)?function {re.escape(name)}\s*\(", source)
    assert m, f"{name} is gone from app.js; this test no longer checks anything"
    k, depth = m.end() - 1, 0
    while True:
        if source[k] == "(":
            depth += 1
        elif source[k] == ")":
            depth -= 1
            if depth == 0:
                break
        k += 1
    i = source.index("{", k)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[m.start():j + 1]
        j += 1


def _make_client(workspace_root, token="testtoken"):  # noqa: S107  test fixture default, not a real secret
    state = State()
    app = build_app(workspace_root=workspace_root, state=state, token=token,
                    user_slug="misha", data_root=workspace_root)
    # base_url loopback: the F-9.2 host-origin guard rejects a non-loopback Host.
    return TestClient(app, base_url="http://127.0.0.1"), state


# --- module-level nonce store + helpers (plan Step 11.1) ---

def test_image_nonce_endpoint_exists_in_app_source():
    src = (Path(__file__).resolve().parent.parent.parent
           / "scripts/bridge_daemon/app.py").read_text(encoding="utf-8")
    assert "/studio/image-nonce" in src, \
        "POST /studio/image-nonce endpoint must exist (F-M1)"


def test_studio_image_nonce_module_attributes():
    """The nonce store and mint function must be importable from bridge_daemon.app."""
    from scripts.bridge_daemon.app import _nonces, _mint_image_nonce  # noqa: F401
    assert isinstance(_nonces, dict)


def test_mint_image_nonce_returns_string():
    from scripts.bridge_daemon.app import _mint_image_nonce
    nonce = _mint_image_nonce()
    assert isinstance(nonce, str) and len(nonce) >= 32


def test_mint_image_nonce_is_single_use():
    from scripts.bridge_daemon.app import _mint_image_nonce, _consume_image_nonce
    nonce = _mint_image_nonce()
    assert _consume_image_nonce(nonce) is True   # first use: valid
    assert _consume_image_nonce(nonce) is False  # second use: invalid (consumed)


def test_consume_image_nonce_rejects_expired():
    from scripts.bridge_daemon import app as app_mod
    nonce = app_mod._mint_image_nonce()
    # Expire the nonce by backdating its expiry past the TTL.
    app_mod._nonces[nonce] = time.monotonic() - 31.0
    assert app_mod._consume_image_nonce(nonce) is False


def test_consume_unknown_nonce_returns_false():
    from scripts.bridge_daemon.app import _consume_image_nonce
    assert _consume_image_nonce("nonexistent-nonce-value") is False


# --- endpoint behaviour (the curl-drivable contract) ---

def test_mint_endpoint_requires_auth(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/studio/image-nonce")
    assert r.status_code == 401
    r = client.post("/studio/image-nonce", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_mint_endpoint_returns_nonce_shape(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/studio/image-nonce", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert "nonce" in body
    assert isinstance(body["nonce"], str) and len(body["nonce"]) >= 32


def test_image_endpoint_rejects_missing_nonce(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    # No n= and no t= -> 401 (nonce required).
    r = client.get("/studio/image", params={"path": "anything.png"})
    assert r.status_code == 401


def test_image_endpoint_rejects_unknown_nonce(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/studio/image", params={"path": "x.png", "n": "bogus-nonce"})
    assert r.status_code == 401


def test_image_endpoint_rejects_bearer_in_query_param(workspace_root):
    """F-M1 (fully closed): the deprecated ?t=<bearer> path is removed outright.
    A VALID bearer token passed as a query param must NOT authenticate the image
    endpoint — only a minted nonce does. This is the regression guard against the
    insecure token-in-URL path ever being reinstated."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/studio/image", params={"path": "x.png", "t": "t1"})
    assert r.status_code == 401, (
        "bearer-in-query (?t=) must be rejected — the insecure path is removed (F-M1)"
    )


def test_image_endpoint_nonce_is_single_use(workspace_root):
    """A minted nonce authenticates exactly one image request, then is rejected."""
    client, _ = _make_client(workspace_root, token="t1")
    nonce = client.post(
        "/studio/image-nonce", headers={"Authorization": "Bearer t1"}
    ).json()["nonce"]
    # First use consumes the nonce; the resolver returns 404 for a non-existent
    # image, which proves the nonce passed auth (auth failure would be 401).
    r1 = client.get("/studio/image", params={"path": "no-such-image.png", "n": nonce})
    assert r1.status_code == 404, "nonce should authenticate (404 = past auth, image missing)"
    # Second use of the same nonce must fail auth.
    r2 = client.get("/studio/image", params={"path": "no-such-image.png", "n": nonce})
    assert r2.status_code == 401, "nonce must be single-use"


def test_the_image_url_builder_never_reads_the_bearer_token():
    """F-L5, structural: no node needed, so this holds on any clone.

    The question is asked of the function BODY with comments stripped, and it
    is asked about the token rather than about one spelling of it. `state.token`
    is the only name in `app.js` that holds the bearer, so a builder that does
    not mention it cannot put it in a URL, however it is written.
    """
    src = APP_JS.read_text(encoding="utf-8")
    body = _js_function("_studioImgUrl", src)
    code = re.sub(r"(?m)//.*$", "", re.sub(r"/\*.*?\*/", "", body, flags=re.S))

    assert "state.token" not in code and "state['token']" not in code, (
        "the studio image-URL builder reads the bearer token; an <img> src ends "
        "up in HTTP logs, the Referer header and browser history (F-L5):\n"
        + code)
    assert "'/studio/image-nonce'" in code or '"/studio/image-nonce"' in code, (
        "the builder no longer mints a nonce. The previous version of this "
        "assertion looked for `image-nonce` anywhere in the whole file, which "
        "the explanatory comment above this function satisfies on its own")
    assert re.search(r"\bn=\$\{", code), (
        "the built URL carries no `n=` nonce parameter")


def test_the_image_url_builder_returns_a_nonce_url_and_no_token():
    """F-L5, behavioural: `_studioImgUrl` is EXECUTED and its output read.

    The structural test above still asks about source text, so it can only
    refuse the shapes someone thought to describe. This one stubs `authFetch`,
    plants a recognisable bearer in `state`, runs the real function under node
    and inspects the string it actually returns. A token reaching the URL by
    any route fails here.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    body = _js_function("_studioImgUrl", APP_JS.read_text(encoding="utf-8"))
    bearer = "BEARER-must-not-reach-the-url"  # noqa: S105  a canary, not a secret
    harness = f"""
    const state = {{ token: {json.dumps(bearer)} }};
    const calls = [];
    async function authFetch(path, opts) {{
      calls.push({{ path, method: (opts || {{}}).method || 'GET' }});
      return {{ ok: true, json: async () => ({{ nonce: 'NONCE-0123456789' }}) }};
    }}
    {body}
    _studioImgUrl('linkedin/2026-08-31/cover.png')
      .then(url => console.log(JSON.stringify({{ url, calls }})))
      .catch(e => {{ console.log(JSON.stringify({{ error: String(e) }})); }});
    """
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [node, "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "error" not in got, got

    assert bearer not in got["url"], (
        f"the bearer token rode into the studio image URL: {got['url']!r}")
    params = dict(p.split("=", 1) for p in got["url"].split("?", 1)[1].split("&"))
    assert params.get("n") == "NONCE-0123456789", params
    # The second axis: a `t=` carrying a token from somewhere other than
    # `state.token` (localStorage, a cookie read, a second global) would slip
    # past the canary above, and the deprecated parameter name is the one the
    # backend used to accept.
    assert "t" not in params, (
        f"the removed ?t= parameter is back in the image URL: {params}")
    assert got["calls"] == [{"path": "/studio/image-nonce", "method": "POST"}], (
        f"the nonce was not minted through the bearer-authed endpoint: "
        f"{got['calls']}")
