import json
from unittest.mock import patch

# Note: wt.exe paths in launch tests below use Windows-shaped strings
# (e.g. r"C:\Windows\System32\wt.exe") but the values flow only through
# mocked shutil.which calls - tests run cross-platform.
from fastapi.testclient import TestClient
from scripts.bridge_daemon.app import build_app
from scripts.bridge_daemon.state import State

def _make_client(workspace_root, token="testtoken"):  # noqa: S107  test fixture default, not a real secret
    state = State()
    app = build_app(workspace_root=workspace_root, state=state, token=token,
                    user_slug="misha", data_root=workspace_root)
    return TestClient(app), state

def test_health_no_auth(workspace_root):
    client, _ = _make_client(workspace_root)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert "pid" in body
    assert "uptime_s" in body

def test_bootstrap_returns_token(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/_bootstrap")
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "t1"
    assert body["user"] == "misha"

def test_authed_endpoint_rejects_no_token(workspace_root):
    client, _ = _make_client(workspace_root)
    r = client.get("/version")
    assert r.status_code == 401

def test_authed_endpoint_accepts_token(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/version", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200

def test_version_304_short_circuit(workspace_root):
    """If-None-Match matching the current ETag returns 304 with no body."""
    client, _ = _make_client(workspace_root, token="t1")
    headers = {"Authorization": "Bearer t1"}
    # First request: capture ETag
    r = client.get("/version", headers=headers)
    assert r.status_code == 200
    etag = r.headers["ETag"]
    assert etag.startswith('"g')
    # Second request: send If-None-Match, expect 304
    r2 = client.get("/version", headers={**headers, "If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.content == b""

def test_bootstrap_shape_includes_refresh_dict(workspace_root):
    """/_bootstrap must return workspace path + refresh dict so JS polls correctly."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/_bootstrap")
    assert r.status_code == 200
    body = r.json()
    assert "workspace" in body
    assert isinstance(body.get("refresh"), dict)
    # Cache-Control: no-store must be present (token-carrying response)
    assert r.headers.get("Cache-Control") == "no-store"

def test_malformed_authorization_headers_rejected(workspace_root):
    """Defensive parsing must handle edge cases without IndexError or accepting bad input."""
    client, _ = _make_client(workspace_root, token="t1")
    # Bearer without space (caught by startswith check)
    r = client.get("/version", headers={"Authorization": "Bearer"})
    assert r.status_code == 401
    # Wrong scheme
    r = client.get("/version", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    # Empty token after Bearer
    r = client.get("/version", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    # Lowercase 'bearer ' should still work
    r = client.get("/version", headers={"Authorization": "bearer t1"})
    assert r.status_code == 200

def test_pulse_endpoint_shape(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/pulse", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    # Phase 1: top-level shape only; nested fields pinned in Phase 2
    assert "kpi" in body          # bento KPI strip data
    assert "now" in body          # Now section (current meeting / focus)
    assert "next" in body         # Imminent items
    assert "watch" in body        # Watchpoints
    assert "data_time" in body

def test_pulse_requires_auth(workspace_root):
    """Without a bearer token, /pulse must 401 — guards against accidental
    auth removal in future refactors."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/pulse")
    assert r.status_code == 401

def test_inbox_endpoint_returns_bands(workspace_root):
    """Phase 1.32: /inbox returns priority bands from _latest-fetch.json."""
    fetch = workspace_root / "outputs/operations/email-intelligence/_latest-fetch.json"
    fetch.write_text(json.dumps({
        "run_info": {"timestamp": "2026-05-20T10:00:00+00:00"},
        "conversations": [
            {"id": "conv-1", "topic": "Reply to Victor", "priority": "P1",
             "latest_datetime": "2026-05-20T09:00:00+00:00",
             "analysis": {"priority": "P1", "summary": "s",
                          "proposed_actions": ["draft a reply"]}},
        ],
    }))
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/inbox", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["bands"]["needs-you"]) == 1
    assert body["bands"]["needs-you"][0]["subject"] == "Reply to Victor"
    assert body["counts"]["needs-you"] == 1
    assert "data_time" in body

def test_inbox_requires_auth(workspace_root):
    """Without a bearer token, /inbox must 401 - guards against accidental
    auth removal in future refactors."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/inbox")
    assert r.status_code == 401

def test_inbox_empty_state_returns_zero(workspace_root):
    """With no _latest-fetch.json on disk, /inbox returns empty bands."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/inbox", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["needs-you"] == 0
    assert body["bands"]["needs-you"] == []


def test_inbox_defer_and_undo(workspace_root):
    """Phase 1.33: POST /inbox/defer hides the conversation; undo-defer restores it."""
    from datetime import date, timedelta
    fetch = workspace_root / "outputs/operations/email-intelligence/_latest-fetch.json"
    fetch.write_text(json.dumps({
        "run_info": {"timestamp": "2026-05-20T10:00:00+00:00"},
        "conversations": [
            {"id": "c1", "topic": "Defer me", "priority": "P1",
             "latest_datetime": "2026-05-20T09:00:00+00:00", "analysis": {}},
        ],
    }))
    client, _ = _make_client(workspace_root, token="t1")
    h = {"Authorization": "Bearer t1"}
    future = (date.today() + timedelta(days=3)).isoformat()
    r = client.post("/inbox/defer", headers=h, json={"conv_id": "c1", "defer_until": future})
    assert r.status_code == 200
    assert client.get("/inbox", headers=h).json()["counts"]["needs-you"] == 0
    log = client.get("/inbox/defer-log", headers=h).json()["items"]
    assert any(it["conv_id"] == "c1" for it in log)
    r = client.post("/inbox/undo-defer", headers=h, json={"conv_id": "c1"})
    assert r.status_code == 200
    assert client.get("/inbox", headers=h).json()["counts"]["needs-you"] == 1


def test_inbox_defer_rejects_past_date(workspace_root):
    """A past defer_until -> 400."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/inbox/defer", headers={"Authorization": "Bearer t1"},
                     json={"conv_id": "c1", "defer_until": "2020-01-01"})
    assert r.status_code == 400


def test_inbox_defer_requires_auth(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/inbox/defer", json={"conv_id": "c1", "defer_until": "2099-01-01"})
    assert r.status_code == 401


def test_inbox_crm_log_no_contact(workspace_root):
    """crm-log on a conversation with no linked contact -> 400."""
    fetch = workspace_root / "outputs/operations/email-intelligence/_latest-fetch.json"
    fetch.write_text(json.dumps({
        "run_info": {},
        "conversations": [{"id": "c1", "topic": "No CRM link",
                           "latest_datetime": "2026-05-20T09:00:00+00:00", "analysis": {}}],
    }))
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/inbox/crm-log", headers={"Authorization": "Bearer t1"},
                     json={"conv_id": "c1"})
    assert r.status_code == 400


def test_inbox_dismiss_marks_read_in_exchange(workspace_root):
    """Phase 1.34: /inbox/dismiss ('Done') marks the conversation read in Exchange."""
    fetch = workspace_root / "outputs/operations/email-intelligence/_latest-fetch.json"
    fetch.write_text(json.dumps({"run_info": {}, "conversations": [
        {"id": "c1", "topic": "Done me", "priority": "P1",
         "latest_datetime": "2026-05-20T09:00:00+00:00", "analysis": {}}]}))
    with patch("scripts.bridge_daemon.finalizers.mark_read.mark_conversation_read",
               return_value={"ok": True, "messages_changed": 2}) as mock_mr:
        client, _ = _make_client(workspace_root, token="t1")
        r = client.post("/inbox/dismiss", headers={"Authorization": "Bearer t1"},
                         json={"conv_id": "c1"})
    assert r.status_code == 200
    assert r.json()["messages_changed"] == 2
    mock_mr.assert_called_once()
    assert mock_mr.call_args.kwargs.get("mark_read") is True


def test_inbox_dismiss_502_when_exchange_fails(workspace_root):
    """If Exchange cannot be updated, /inbox/dismiss returns 502 - the email
    is still unread in Outlook, so the dismiss must not silently succeed."""
    with patch("scripts.bridge_daemon.finalizers.mark_read.mark_conversation_read",
               return_value={"ok": False, "error": "Exchange unreachable"}):
        client, _ = _make_client(workspace_root, token="t1")
        r = client.post("/inbox/dismiss", headers={"Authorization": "Bearer t1"},
                         json={"conv_id": "c1"})
    assert r.status_code == 502


def test_contacts_endpoint_lists_ceo_contacts(workspace_root):
    """Phase 1.35: /contacts returns the CEO's CRM contacts."""
    d = workspace_root / "crm" / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "alice.md").write_text(
        "---\nrelationship_type: prospect\n---\n\n# Alice Smith\n", encoding="utf-8")
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/contacts", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["contacts"][0]["name"] == "Alice Smith"


def test_contacts_contact_drilldown(workspace_root):
    d = workspace_root / "crm" / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "alice.md").write_text(
        "---\nrelationship_type: prospect\n---\n\n# Alice Smith\n\n## Interaction Log\n### 2026-05-01 note\n",
        encoding="utf-8")
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/contacts/contact?owner=ceo&slug=alice",
                    headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    assert r.json()["name"] == "Alice Smith"


def test_contacts_contact_404_unknown(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/contacts/contact?owner=ceo&slug=ghost",
                    headers={"Authorization": "Bearer t1"})
    assert r.status_code == 404


def test_contacts_requires_auth(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    assert client.get("/contacts").status_code == 401


def _make_archive_post(workspace_root, slug, body="Prose.", with_image=False):
    folder = (workspace_root / "datastore/content/linkedin-archive/posts" / slug)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(
        f"---\ntitle: {slug}\ndate: 2026-05-01\n---\n\n# Caption\n\n{body}\n",
        encoding="utf-8")
    if with_image:
        (folder / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png")
    return folder


def test_studio_endpoint_lists_artifacts(workspace_root):
    """Phase 1.38: /studio returns LinkedIn artifacts."""
    _make_archive_post(workspace_root, "2026-05-01-ep-post", body="The body.")
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/studio", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["artifacts"][0]["kind"] == "post"


def test_studio_artifact_drilldown(workspace_root):
    _make_archive_post(workspace_root, "2026-05-01-dd", body="Drilldown body.")
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/studio/artifact?kind=post&slug=2026-05-01-dd",
                    headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    assert "Drilldown body." in r.json()["content"]


def test_studio_artifact_404_unknown(workspace_root):
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/studio/artifact?kind=post&slug=ghost",
                    headers={"Authorization": "Bearer t1"})
    assert r.status_code == 404


def test_studio_image_serves_with_nonce(workspace_root):
    """The image endpoint authenticates via a short-lived minted nonce (F-M1/F-L5).
    The old ?t=<bearer> query-token path is removed outright and must be rejected."""
    _make_archive_post(workspace_root, "2026-05-01-im", with_image=True)
    client, _ = _make_client(workspace_root, token="t1")
    rel = "datastore/content/linkedin-archive/posts/2026-05-01-im/shot.png"
    nonce = client.post(
        "/studio/image-nonce", headers={"Authorization": "Bearer t1"}
    ).json()["nonce"]
    ok = client.get(f"/studio/image?path={rel}&n={nonce}")
    assert ok.status_code == 200
    # The removed bearer-in-query path must now be rejected (no ?t= fallback).
    bad = client.get(f"/studio/image?path={rel}&t=t1")
    assert bad.status_code == 401

def test_launch_requires_auth(workspace_root):
    """Without a bearer token, /launch must 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/launch", json={"action": "email-respond"})
    assert r.status_code == 401


def test_launch_with_session_id(workspace_root):
    """When body supplies session_id, /launch uses it directly and spawns wt.exe."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.subprocess.Popen") as mock_popen, \
         patch("scripts.bridge_daemon.terminal.shutil.which",
               return_value=r"C:\Windows\System32\wt.exe"):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "email-respond", "session_id": "abc123",
                  "cwd": str(workspace_root), "title": "Victor reply"})
    assert r.status_code == 200
    body = r.json()
    assert body["launched"] is True
    # POSIX spawn_or_focus issues two Popen calls (tmux session create, then GUI
    # attach); the session_id rides the first. Search across all calls so the
    # assertion is OS-portable (Windows = 1 call, macOS/Linux = 2).
    all_args = " ".join(a for call in mock_popen.call_args_list for a in call[0][0])
    assert "abc123" in all_args


def test_launch_falls_back_to_registry_when_session_id_missing(workspace_root, tmp_path, monkeypatch):
    """Registry fallback: when /launch body omits session_id but provides
    cwd, the daemon consults ~/.claude/state/active-sessions.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    reg = tmp_path / ".claude" / "state" / "active-sessions.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({str(workspace_root): {"session_id": "registry-sid-xyz"}}))
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.subprocess.Popen") as mock_popen, \
         patch("scripts.bridge_daemon.terminal.shutil.which",
               return_value=r"C:\Windows\System32\wt.exe"):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "osint", "cwd": str(workspace_root)})  # no session_id
    assert r.status_code == 200
    all_args = " ".join(a for call in mock_popen.call_args_list for a in call[0][0])
    assert "registry-sid-xyz" in all_args


def test_launch_503_when_wt_missing(workspace_root):
    """When wt.exe is not on PATH, /launch returns 503 (service unavailable)."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.shutil.which", return_value=None):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "email-respond", "cwd": str(workspace_root)})
    assert r.status_code == 503


def test_launch_rejects_malformed_action(workspace_root):
    """A malformed action (e.g., contains spaces) fails terminal allowlist
    and surfaces as 400, not 500."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.subprocess.Popen") as _, \
         patch("scripts.bridge_daemon.terminal.shutil.which",
               return_value=r"C:\Windows\System32\wt.exe"):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "email respond", "cwd": str(workspace_root)})  # space in action
    assert r.status_code == 400


def test_launch_rejects_nonexistent_cwd(workspace_root):
    """A cwd that does not exist as a directory surfaces as 400."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/launch",
        headers={"Authorization": "Bearer t1"},
        json={"action": "email-respond", "cwd": str(workspace_root / "does-not-exist")})
    assert r.status_code == 400


def test_launch_no_session_no_cwd_omits_resume(workspace_root):
    """When both session_id and cwd are absent, /launch falls back to
    workspace_root and spawns Claude WITHOUT --resume (fresh session)."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.subprocess.Popen") as mock_popen, \
         patch("scripts.bridge_daemon.terminal.shutil.which",
               return_value=r"C:\Windows\System32\wt.exe"):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "osint"})
    assert r.status_code == 200
    joined = " ".join(mock_popen.call_args[0][0])
    assert "--resume" not in joined


def test_return_opens_browser(workspace_root):
    """POST /return opens the daemon's own root URL with target_page hash route."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("webbrowser.open") as mock_open:
        r = client.post("/return",
            headers={"Authorization": "Bearer t1"},
            json={"session_id": "abc123", "target_page": "inbox"})
    assert r.status_code == 200
    mock_open.assert_called_once()
    url = mock_open.call_args[0][0]
    assert url == "http://127.0.0.1:31415/#/inbox"


def test_return_requires_auth(workspace_root):
    """Without a bearer token, /return must 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/return", json={"session_id": "abc123", "target_page": "inbox"})
    assert r.status_code == 401


def test_return_defaults_to_pulse_when_target_page_omitted(workspace_root):
    """When body omits target_page, /return defaults to /#/pulse."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("webbrowser.open") as mock_open:
        r = client.post("/return",
            headers={"Authorization": "Bearer t1"},
            json={"session_id": "abc123"})
    assert r.status_code == 200
    url = mock_open.call_args[0][0]
    assert url.endswith("/#/pulse")


def test_return_honors_bridge_port_env(workspace_root, monkeypatch):
    """When BRIDGE_PORT is set, /return uses it in the URL instead of 31415."""
    monkeypatch.setenv("BRIDGE_PORT", "54321")
    client, _ = _make_client(workspace_root, token="t1")
    with patch("webbrowser.open") as mock_open:
        r = client.post("/return",
            headers={"Authorization": "Bearer t1"},
            json={"session_id": "abc123", "target_page": "inbox"})
    assert r.status_code == 200
    url = mock_open.call_args[0][0]
    assert url == "http://127.0.0.1:54321/#/inbox"


def test_return_rejects_unknown_target_page(workspace_root):
    """A target_page not on the allowlist is rejected with 422."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/return",
        headers={"Authorization": "Bearer t1"},
        json={"session_id": "abc123", "target_page": "../etc/passwd"})
    assert r.status_code == 422


def test_refresh_bumps_component(workspace_root):
    """POST /refresh bumps the named component's version counter."""
    client, state = _make_client(workspace_root, token="t1")
    before = state.version("inflight")
    r = client.post("/refresh",
        headers={"Authorization": "Bearer t1"},
        json={"component": "inflight"})
    assert r.status_code == 200
    assert state.version("inflight") == before + 1


def test_refresh_requires_auth(workspace_root):
    """Without a bearer token, /refresh must 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/refresh", json={"component": "inflight"})
    assert r.status_code == 401


def test_finalize_send_email_rejects_unknown_action(workspace_root):
    """A finalize action not in the allowlist is rejected with 400."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "bogus", "artifact_id": "1"})
    assert r.status_code == 400


def test_finalize_requires_auth(workspace_root):
    """Without a bearer token, /finalize must 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize", json={"action": "send-email", "artifact_id": "1"})
    assert r.status_code == 401


def test_finalize_send_email_with_existing_draft(workspace_root):
    """When the draft sidecar exists on disk, /finalize send-email returns
    {sent: True, draft: <path>}."""
    drafts_dir = workspace_root / "outputs" / "operations" / "email-intelligence" / "drafts"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "abc123.json").write_text('{"to": "alice@31c.io", "subject": "ok"}')
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "send-email", "artifact_id": "abc123"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is True
    assert body["draft"].endswith("abc123.json")


def test_finalize_send_email_missing_draft_returns_not_found_envelope(workspace_root):
    """When the draft sidecar is absent, /finalize send-email returns HTTP 200
    with {sent: False, error: '...'} (envelope-shape, not HTTP error)."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "send-email", "artifact_id": "does-not-exist"})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is False
    assert "not found" in body["error"]


def test_finalize_send_email_rejects_path_traversal_in_artifact_id(workspace_root):
    """A path-traversal payload in artifact_id is rejected with 400."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "send-email", "artifact_id": "../../etc/passwd"})
    assert r.status_code == 400


def test_refresh_rejects_unknown_component(workspace_root):
    """A component not in state.COMPONENTS is rejected with 422."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/refresh",
        headers={"Authorization": "Bearer t1"},
        json={"component": "bogus-component"})
    assert r.status_code == 422


def test_refresh_requires_component_field(workspace_root):
    """When body omits component entirely, Pydantic surfaces 422."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/refresh",
        headers={"Authorization": "Bearer t1"},
        json={})
    assert r.status_code == 422


def test_page_view_endpoint_records_event(workspace_root):
    """POST /telemetry/page-view writes a page_view event to usage.jsonl."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "inbox", "duration_s": 47})
    assert r.status_code == 200
    assert r.json() == {"recorded": True}
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert f.exists()
    last_line = f.read_text().strip().split("\n")[-1]
    import json as _json
    rec = _json.loads(last_line)
    assert rec["event"] == "page_view"
    assert rec["page"] == "inbox"
    assert rec["duration_s"] == 47


def test_page_view_requires_auth(workspace_root):
    """Without a bearer token, /telemetry/page-view must 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view", json={"page": "pulse"})
    assert r.status_code == 401


def test_launch_emits_telemetry(workspace_root):
    """Successful /launch writes a 'launch' event to usage.jsonl."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("scripts.bridge_daemon.terminal.subprocess.Popen"), \
         patch("scripts.bridge_daemon.terminal.shutil.which",
               return_value=r"C:\Windows\System32\wt.exe"):
        r = client.post("/launch",
            headers={"Authorization": "Bearer t1"},
            json={"action": "email-respond", "session_id": "abc123",
                  "cwd": str(workspace_root), "title": "Victor reply"})
    assert r.status_code == 200
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert f.exists()
    import json as _json
    rec = _json.loads(f.read_text().strip().split("\n")[-1])
    assert rec["event"] == "launch"
    assert rec["action"] == "email-respond"


def test_return_emits_telemetry(workspace_root):
    """Successful /return writes a 'return_to_browser' event to usage.jsonl."""
    client, _ = _make_client(workspace_root, token="t1")
    with patch("webbrowser.open"):
        r = client.post("/return",
            headers={"Authorization": "Bearer t1"},
            json={"session_id": "sid-xyz", "target_page": "inbox"})
    assert r.status_code == 200
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert f.exists()
    import json as _json
    rec = _json.loads(f.read_text().strip().split("\n")[-1])
    assert rec["event"] == "return_to_browser"
    assert rec["session_id"] == "sid-xyz"
    assert rec["target"] == "inbox"


def test_finalize_emits_telemetry_on_success(workspace_root):
    """Successful /finalize writes a 'finalize' event to usage.jsonl."""
    drafts_dir = workspace_root / "outputs" / "operations" / "email-intelligence" / "drafts"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "tel-test.json").write_text('{"to": "alice@31c.io"}')
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "send-email", "artifact_id": "tel-test"})
    assert r.status_code == 200
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert f.exists()
    import json as _json
    rec = _json.loads(f.read_text().strip().split("\n")[-1])
    assert rec["event"] == "finalize"
    assert rec["action"] == "send-email"
    assert rec["artifact_id"] == "tel-test"


def test_finalize_does_not_emit_telemetry_on_value_error(workspace_root):
    """When /finalize raises ValueError (e.g., bad artifact_id), NO telemetry is written."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/finalize",
        headers={"Authorization": "Bearer t1"},
        json={"action": "send-email", "artifact_id": "../bad"})
    assert r.status_code == 400
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    # File may not exist OR may be empty - either is acceptable evidence of "no event"
    assert not f.exists() or f.read_text().strip() == ""


def test_page_view_appends_multiple_events_in_order(workspace_root):
    """Two sequential /telemetry/page-view calls land as distinct lines, in order,
    proving the lock releases and append doesn't clobber."""
    client, _ = _make_client(workspace_root, token="t1")
    r1 = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "pulse"})
    r2 = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "inbox"})
    assert r1.status_code == 200 and r2.status_code == 200
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    import json as _json
    lines = [_json.loads(l) for l in f.read_text().strip().split("\n")]
    assert len(lines) == 2
    assert lines[0]["page"] == "pulse"
    assert lines[1]["page"] == "inbox"


def test_page_view_duration_s_optional_defaults_to_none(workspace_root):
    """When body omits duration_s, the recorded event has duration_s=None."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "pulse"})
    assert r.status_code == 200
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    import json as _json
    rec = _json.loads(f.read_text().strip().split("\n")[-1])
    assert rec["duration_s"] is None


def test_page_view_rejects_unknown_page(workspace_root):
    """A page not on the allowlist is rejected with 422 (no telemetry written)."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "../../etc/passwd"})
    assert r.status_code == 422
    f = workspace_root / ".daemon-state" / "usage.jsonl"
    assert not f.exists() or f.read_text().strip() == ""


def test_page_view_rejects_negative_duration(workspace_root):
    """A negative duration_s is rejected with 422."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "pulse", "duration_s": -5})
    assert r.status_code == 422


def test_page_view_rejects_oversized_duration(workspace_root):
    """A duration_s > 86400 (one day) is rejected with 422."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.post("/telemetry/page-view",
        headers={"Authorization": "Bearer t1"},
        json={"page": "pulse", "duration_s": 100_000})
    assert r.status_code == 422


def test_settings_endpoint_returns_components(workspace_root):
    """/settings returns daemon info + per-component data_times + intervals."""
    client, state = _make_client(workspace_root, token="t1")
    # Bump a component so it has a data_time.
    state.bump("inbox")
    r = client.get("/settings", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200
    body = r.json()
    assert "pid" in body
    assert "version" in body
    assert "uptime_s" in body
    assert "user" in body
    assert "workspace" in body
    assert "components" in body
    assert isinstance(body["components"], list)
    # Each component has the expected shape.
    for c in body["components"]:
        assert "name" in c
        assert "data_time" in c  # may be None
        assert "version" in c
    # The bumped 'inbox' should have a non-None data_time.
    by_name = {c["name"]: c for c in body["components"]}
    assert by_name["inbox"]["data_time"] is not None


def test_settings_requires_auth(workspace_root):
    """/settings is authed - without token returns 401."""
    client, _ = _make_client(workspace_root, token="t1")
    r = client.get("/settings")
    assert r.status_code == 401
