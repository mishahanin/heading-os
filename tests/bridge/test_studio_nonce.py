"""F-M1/F-L5: /studio/image must accept a short-lived, single-use nonce, not the
bearer token in a query param. The nonce mint endpoint is bearer-authed; the image
endpoint validates and consumes the nonce. The bearer token must never appear in the
image URL the frontend constructs."""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State


def _make_client(workspace_root, token="testtoken"):  # noqa: S107  test fixture default, not a real secret
    state = State()
    app = build_app(workspace_root=workspace_root, state=state, token=token,
                    user_slug="misha", data_root=workspace_root)
    return TestClient(app), state


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


def test_image_url_in_frontend_carries_nonce_not_bearer():
    """F-L5: the frontend image-URL builder must not embed the bearer token."""
    app_js = (Path(__file__).resolve().parent.parent.parent
              / "scripts/bridge_daemon/web/app.js").read_text(encoding="utf-8")
    # The token must no longer ride in the studio image URL.
    assert "&t=${encodeURIComponent(state.token" not in app_js, \
        "bearer token must not appear in the studio image URL (F-L5)"
    assert "image-nonce" in app_js, \
        "frontend must mint a nonce for the studio image URL (F-L5)"
