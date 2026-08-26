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


def test_observability_safe_asks_a_resolver_for_its_paths():
    """F-M12 widened 2026-08-26.

    It read `"get_workspace_root" in src`, which pinned one particular resolver
    rather than the invariant: the module must ASK for a path, never walk to one
    by hand. That naming became wrong when the module stopped needing the engine
    root at all. `_debug_trace_path` wrote raw e-mail bodies (args, kwargs and
    return values) to `<engine>/state/email-triage/`, a path that routes `engine`
    and is not gitignored, so it now delegates to `inbox_pulse.paths.get_state_dir
    ()` and lands in the DATA overlay. Asserting the old name would have forced
    the engine root back into a module that must not use it.

    The companion test above still forbids the hardcoded `parent.parent.parent`.
    """
    src = (ENGINE / "scripts/utils/observability_safe.py").read_text(encoding="utf-8")
    assert "get_state_dir" in src or "get_workspace_root" in src, \
        "observability_safe.py must resolve paths through a shared resolver (F-M12)"
