"""Unit tests for the CRM-log finalizer (POST /inbox/crm-log)."""
import json

from scripts.bridge_daemon.finalizers.crm_log import log_to_crm


def _write_fetch(workspace_root, conversations):
    d = workspace_root / "outputs" / "operations" / "email-intelligence"
    d.mkdir(parents=True, exist_ok=True)
    (d / "_latest-fetch.json").write_text(
        json.dumps({"run_info": {}, "conversations": conversations}),
        encoding="utf-8",
    )


def _write_contact(workspace_root, slug):
    d = workspace_root / "crm" / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        "---\nlast_touch: 2026-01-01\n---\n\n# Contact\n\nNotes.\n\n## Interaction Log\n",
        encoding="utf-8",
    )


def _conv(conv_id, slug=None, topic="A thread"):
    c = {"id": conv_id, "topic": topic, "latest_datetime": "2026-05-20T09:00:00+00:00"}
    if slug:
        c["crm_context"] = {"contact_slug": slug, "name": "X"}
    return c


def test_log_to_crm_happy_path(tmp_path):
    _write_contact(tmp_path, "ada-lovelace")
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace", "Demo follow-up")])
    r = log_to_crm(tmp_path, "c1")
    assert r["ok"] is True
    assert r["slug"] == "ada-lovelace"
    text = (tmp_path / "crm" / "contacts" / "ada-lovelace.md").read_text(encoding="utf-8")
    assert "Demo follow-up" in text
    assert "last_touch: 2026-05-20" in text
    assert "Logged from the Inbox dashboard." in text


def test_log_to_crm_is_idempotent(tmp_path):
    _write_contact(tmp_path, "ada-lovelace")
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace")])
    assert log_to_crm(tmp_path, "c1")["ok"] is True
    second = log_to_crm(tmp_path, "c1")
    assert second["ok"] is False
    assert "already logged" in second["error"]
    # Exactly one entry written, not two.
    text = (tmp_path / "crm" / "contacts" / "ada-lovelace.md").read_text(encoding="utf-8")
    assert text.count("Logged from the Inbox dashboard.") == 1


def test_log_to_crm_no_contact_link(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", slug=None)])
    r = log_to_crm(tmp_path, "c1")
    assert r["ok"] is False
    assert "no CRM contact" in r["error"]


def test_log_to_crm_conv_not_in_fetch(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace")])
    r = log_to_crm(tmp_path, "ghost")
    assert r["ok"] is False
    assert "not in latest fetch" in r["error"]


def test_log_to_crm_missing_contact_file(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "no-such-contact")])
    r = log_to_crm(tmp_path, "c1")
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_log_to_crm_rejects_bad_slug(tmp_path):
    """A traversal-shaped slug is rejected before any filesystem access."""
    _write_fetch(tmp_path, [_conv("c1", "../etc/passwd")])
    r = log_to_crm(tmp_path, "c1")
    assert r["ok"] is False
    assert "invalid contact slug" in r["error"]


def test_log_to_crm_missing_conv_id(tmp_path):
    assert log_to_crm(tmp_path, "")["ok"] is False
    assert log_to_crm(tmp_path, "x" * 600)["ok"] is False
