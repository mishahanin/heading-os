"""F-M12: llm_fallback.py and observability_safe.py must not hardcode parent.parent.parent."""
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent


def test_llm_fallback_no_hardcoded_root():
    src = (ENGINE / "scripts/utils/llm_fallback.py").read_text(encoding="utf-8")
    assert "parent.parent.parent" not in src, \
        "llm_fallback.py still uses hardcoded parent.parent.parent (F-M12)"


def test_llm_fallback_uses_get_workspace_root():
    src = (ENGINE / "scripts/utils/llm_fallback.py").read_text(encoding="utf-8")
    assert "get_workspace_root" in src, \
        "llm_fallback.py must import and use get_workspace_root() (F-M12)"


def test_observability_safe_no_hardcoded_root():
    src = (ENGINE / "scripts/utils/observability_safe.py").read_text(encoding="utf-8")
    assert "parent.parent.parent" not in src, \
        "observability_safe.py still uses hardcoded parent.parent.parent (F-M12)"


def test_observability_safe_uses_get_workspace_root():
    src = (ENGINE / "scripts/utils/observability_safe.py").read_text(encoding="utf-8")
    assert "get_workspace_root" in src, \
        "observability_safe.py must import and use get_workspace_root() (F-M12)"
