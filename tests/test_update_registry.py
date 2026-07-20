import textwrap
from pathlib import Path
import pytest
from scripts.utils.update_registry import load_registry, RegistryError

def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "reg.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p

def test_observed_with_apply_is_rejected(tmp_path):
    reg = _write(tmp_path, """
        components:
          ollama:
            tier: observed
            current: {via: shell, cmd: "ollama --version"}
            latest: {via: github_release, repo: ollama/ollama}
            apply: {cmd: "ollama pull"}
    """)
    with pytest.raises(RegistryError, match="observed.*apply"):
        load_registry(reg)

def test_valid_registry_loads(tmp_path):
    reg = _write(tmp_path, """
        components:
          yt-dlp:
            tier: auto
            display: yt-dlp
            current: {via: shell, cmd: "yt-dlp --version"}
            latest: {via: pypi, package: yt-dlp}
            apply: {cmd: "uv tool upgrade yt-dlp", rollback_cmd: "uv tool install 'yt-dlp=={prev}'"}
            health: {cmd: "yt-dlp --version"}
    """)
    comps = load_registry(reg)
    assert len(comps) == 1
    assert comps[0].name == "yt-dlp"
    assert comps[0].tier == "auto"
    assert comps[0].apply["cmd"] == "uv tool upgrade yt-dlp"
    assert comps[0].hold is False

def test_unknown_tier_is_rejected(tmp_path):
    reg = _write(tmp_path, """
        components:
          x:
            tier: bogus
            current: {via: shell, cmd: "true"}
            latest: {via: pypi, package: x}
    """)
    with pytest.raises(RegistryError, match="tier"):
        load_registry(reg)

def test_auto_cmd_apply_requires_rollback(tmp_path):
    reg = _write(tmp_path, """
        components:
          yt-dlp:
            tier: auto
            current: {via: shell, cmd: "yt-dlp --version"}
            latest: {via: pypi, package: yt-dlp}
            apply: {cmd: "uv tool upgrade yt-dlp"}
    """)
    with pytest.raises(RegistryError, match="rollback_cmd"):
        load_registry(reg)

def test_auto_script_apply_is_exempt_from_rollback(tmp_path):
    reg = _write(tmp_path, """
        components:
          thing:
            tier: auto
            current: {via: shell, cmd: "thing --version"}
            latest: {via: github_release, repo: x/y}
            apply: {script: scripts/updaters/thing.py}
    """)
    comps = load_registry(reg)  # must not raise -- script owns its rollback
    assert comps[0].apply == {"script": "scripts/updaters/thing.py"}

def test_apply_without_cmd_or_script_is_rejected(tmp_path):
    reg = _write(tmp_path, """
        components:
          x:
            tier: notify
            current: {via: shell, cmd: "x --version"}
            latest: {via: github_release, repo: x/y}
            apply: {foo: bar}
    """)
    with pytest.raises(RegistryError, match="cmd.*script|script.*cmd"):
        load_registry(reg)
