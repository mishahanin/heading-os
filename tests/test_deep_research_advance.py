"""The orchestrator runs Phase 0-2 headless and writes a valid intermediate.json."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deep-research-advance.py"
_spec = importlib.util.spec_from_file_location("deep_research_advance", SCRIPT)
dra = importlib.util.module_from_spec(_spec)
sys.modules["deep_research_advance"] = dra
_spec.loader.exec_module(dra)


def test_run_writes_intermediate(tmp_path):
    decompose_json = json.dumps(["sub one", "sub two"])
    reason_json = json.dumps({
        "summary": "s", "claims": [
            {"claim": "c", "status": "supported", "confidence": 0.8, "source_ids": [1]}
        ], "contradictions": []})

    with mock.patch.object(dra, "kimi_reason", side_effect=[decompose_json, reason_json]), \
         mock.patch.object(dra, "pplx_research",
                           return_value=("finding", ["https://a.com"])), \
         mock.patch.object(dra, "get_outputs_dir", return_value=tmp_path):
        run_dir = dra.run("what is X?", depth=2, critical=False)

    data = json.loads((run_dir / "intermediate.json").read_text())
    assert data["question"] == "what is X?"
    assert data["angles"] == ["sub one", "sub two"]
    assert len(data["corpus"]) == 2
    assert data["kimi_analysis"]["claims"][0]["status"] == "supported"
    assert data["degraded"] is False


def test_extract_json_tolerates_fenced_block():
    raw = "noise\n```json\n{\"a\": 1}\n```\ntrailing"
    assert dra.extract_json(raw) == {"a": 1}


def test_run_marks_degraded_when_kimi_fails(tmp_path):
    # Kimi fails on BOTH the decompose and reason calls; Perplexity still yields a corpus,
    # so the run completes with degraded=true rather than aborting (exit 3).
    with mock.patch.object(dra, "kimi_reason", side_effect=RuntimeError("kimi down")), \
         mock.patch.object(dra, "pplx_research", return_value=("finding", ["https://a.com"])), \
         mock.patch.object(dra, "get_outputs_dir", return_value=tmp_path):
        run_dir = dra.run("q", depth=2, critical=False)
    data = json.loads((run_dir / "intermediate.json").read_text())
    assert data["degraded"] is True
    assert "kimi" in data["degraded_reason"].lower()


def test_run_exits_3_when_no_corpus(tmp_path):
    with mock.patch.object(dra, "kimi_reason", return_value=json.dumps(["a", "b"])), \
         mock.patch.object(dra, "pplx_research", side_effect=RuntimeError("pplx down")), \
         mock.patch.object(dra, "get_outputs_dir", return_value=tmp_path):
        with pytest.raises(SystemExit) as exc:
            dra.run("q", depth=2)
    assert exc.value.code == 3
    jsons = list((tmp_path / "research").rglob("intermediate.json"))
    assert jsons, "intermediate.json should be written before exit(3)"
    data = json.loads(jsons[0].read_text())
    assert data["degraded"] is True


def test_main_rejects_domains_and_exclude_together():
    with pytest.raises(SystemExit) as exc:
        dra.main(["q", "--domains", "a.com", "--exclude-domains", "b.com"])
    assert exc.value.code == 2


def test_run_slug_distinguishes_same_prefix_questions():
    q1 = "What is publicly known on the open web about company A and its DPI platform"
    q2 = "What is publicly known on the open web about a person B and his career history"
    assert dra.run_slug(q1) != dra.run_slug(q2)
    # deterministic: same question -> same slug (re-runs intentionally overwrite)
    assert dra.run_slug(q1) == dra.run_slug(q1)


def test_run_retries_kimi_reason_once_before_degrading(tmp_path):
    # Phase 0 decompose ok; Phase 2 fails once then succeeds on retry -> NOT degraded.
    reason_json = '{"summary": "s", "claims": [], "contradictions": []}'
    calls = iter([
        '["angle one", "angle two"]',          # Phase 0 decompose
        RuntimeError("kimi timed out"),         # Phase 2 first attempt
        reason_json,                            # Phase 2 retry succeeds
    ])

    def fake_kimi(prompt, **kw):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    with mock.patch.object(dra, "kimi_reason", side_effect=fake_kimi), \
         mock.patch.object(dra, "pplx_research", return_value=("finding", ["https://a.com"])), \
         mock.patch.object(dra, "get_outputs_dir", return_value=tmp_path):
        run_dir = dra.run("q", depth=2, critical=False)
    data = json.loads((run_dir / "intermediate.json").read_text())
    assert data["degraded"] is False
    assert data["kimi_analysis"]["summary"] == "s"
