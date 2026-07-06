import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.memory_health import scan_redundancy


def test_flags_near_duplicates_not_distinct(tmp_path):
    (tmp_path / "a.md").write_text("Misha prefers no em-dash in prose.", encoding="utf-8")
    (tmp_path / "b.md").write_text("No em-dash in Misha's authored prose.", encoding="utf-8")
    (tmp_path / "c.md").write_text("The bridge daemon binds to loopback.", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- index line", encoding="utf-8")

    table = {
        "Misha prefers no em-dash in prose.": [1.0, 0.0, 0.0],
        "No em-dash in Misha's authored prose.": [0.98, 0.02, 0.0],
        "The bridge daemon binds to loopback.": [0.0, 0.0, 1.0],
    }
    res = scan_redundancy(tmp_path, threshold=0.86, embedder=lambda ts: [table[t] for t in ts])
    assert res["ok"] is True
    names = {(p["a"], p["b"]) for p in res["pairs"]}
    assert ("a.md", "b.md") in names
    assert all("c.md" not in (p["a"], p["b"]) for p in res["pairs"])
    allnames = {p["a"] for p in res["pairs"]} | {p["b"] for p in res["pairs"]}
    assert "MEMORY.md" not in allnames


def test_degrades_when_embedder_unavailable(tmp_path):
    (tmp_path / "a.md").write_text("one", encoding="utf-8")
    (tmp_path / "b.md").write_text("two", encoding="utf-8")

    def boom(ts):
        raise RuntimeError("ollama down")

    res = scan_redundancy(tmp_path, threshold=0.86, embedder=boom)
    assert res["ok"] is False
    assert res["pairs"] == []
    assert "unavailable" in res["note"]
