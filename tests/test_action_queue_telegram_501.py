"""Regression: approved telegram_send cards must return 501, not be silently skipped (F-L6)."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "action_queue_execute", str(ROOT / "scripts" / "action-queue-execute.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_queue(tmp_path, card):
    qdir = tmp_path / "operations" / "action-queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "queue.json").write_text(json.dumps({"actions": [card]}), encoding="utf-8")


def _run_main(tmp_path, capsys):
    with patch.object(_mod, "get_outputs_dir", return_value=tmp_path), \
         patch.object(_mod, "get_workspace_root", return_value=ROOT):
        rc = _mod.main()
    return rc, json.loads(capsys.readouterr().out)


def test_telegram_send_returns_501(tmp_path, capsys):
    """An approved telegram_send card yields a send_failed/501/permanent result."""
    _write_queue(tmp_path, {
        "id": "tg-001", "status": "approved", "action_type": "telegram_send",
        "to": "12345", "draft_body": "hello",
    })
    rc, results = _run_main(tmp_path, capsys)
    assert rc == 0
    assert len(results) == 1, "telegram_send card must produce a result, not be silently skipped"
    assert results[0]["result"] == "send_failed"
    assert "501" in results[0]["error"]
    assert results[0]["classification"] == "permanent"
    assert results[0]["action_id"] == "tg-001"


def test_telegram_send_not_silently_skipped(tmp_path, capsys):
    """The telegram card's id must appear in the output (proves it was not dropped)."""
    _write_queue(tmp_path, {
        "id": "tg-002", "status": "approved", "action_type": "telegram_send",
        "to": "999", "draft_body": "x",
    })
    _, results = _run_main(tmp_path, capsys)
    assert any(r.get("action_id") == "tg-002" for r in results)


def test_telegram_send_never_dispatches_a_send(tmp_path, capsys):
    """The 501 branch must not spawn any subprocess (no actual send)."""
    _write_queue(tmp_path, {
        "id": "tg-003", "status": "approved", "action_type": "telegram_send",
        "to": "1", "draft_body": "y",
    })
    with patch.object(_mod.subprocess, "run") as mock_run:
        _, results = _run_main(tmp_path, capsys)
    mock_run.assert_not_called()
    assert results[0]["result"] == "send_failed"
