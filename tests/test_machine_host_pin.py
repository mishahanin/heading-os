"""Which ollama serves this machine is a MACHINE fact, not a repository fact.

Until 2026-08-23 the embedding pin lived in `config/memory-index.yaml`, which is
tracked and ships to every clone. That put `auto:11434` / `auto:11436` -- two
addresses that mean "the Windows side of this laptop, across the WSL NAT
gateway" -- into a public repository, where they name whatever sits at the
clone's default gateway and answer nothing. Since a pin REFUSES rather than
degrades, the shipped default made `memory-index build` fail on a fresh clone
that had a perfectly good local ollama.

The fix separates the two facts. `config/memory-index.yaml` keeps the tuning
every clone shares (model, threshold, layers) and pins nothing. The host pin
moves to `config/ollama-hosts.yaml`, gitignored and machine-local, with a
tracked `.example` beside it that documents the shape and enables nothing. An
absent file means "no accelerator here", which is the correct default for a
clone, for CI, and for a laptop before its Windows side is set up.

The same file answers for GENERATION (`gemma3:4b` in chronicle and the census
bench), which previously read only `HEADING_OS_OLLAMA_HOST` -- unset on this
laptop, so the summarizer ran on the WSL CPU while the iGPU idled. Same defect
shape as the embedding one fixed the same day, different path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ollama_host as oh  # noqa: E402

PIN = ["auto:11434", "auto:11436"]


def _write(root: Path, payload) -> Path:
    (root / "config").mkdir(parents=True, exist_ok=True)
    path = root / oh.MACHINE_HOSTS_FILE
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


# --- reading the machine file ------------------------------------------------

def test_no_file_means_no_pin(tmp_path):
    """The default a clone gets. Not an error, not a warning -- just no pin."""
    assert oh.machine_hosts("embed", root=tmp_path) == []
    assert oh.machine_hosts("generate", root=tmp_path) == []


def test_a_role_reads_its_own_list(tmp_path):
    _write(tmp_path, {"embed": PIN, "generate": ["auto:11434"]})
    assert oh.machine_hosts("embed", root=tmp_path) == PIN
    assert oh.machine_hosts("generate", root=tmp_path) == ["auto:11434"]


def test_a_bare_string_is_accepted_as_a_one_entry_list(tmp_path):
    _write(tmp_path, {"embed": "auto:11436"})
    assert oh.machine_hosts("embed", root=tmp_path) == ["auto:11436"]


def test_a_missing_role_is_empty_not_an_error(tmp_path):
    _write(tmp_path, {"embed": PIN})
    assert oh.machine_hosts("generate", root=tmp_path) == []


def test_non_string_entries_are_dropped(tmp_path):
    """One bad row must not disable the good ones beside it."""
    _write(tmp_path, {"embed": ["auto:11434", 11436, None]})
    assert oh.machine_hosts("embed", root=tmp_path) == ["auto:11434"]


def test_unparseable_file_is_reported_then_ignored(tmp_path, capsys):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / oh.MACHINE_HOSTS_FILE).write_text("embed: [unclosed\n", encoding="utf-8")
    assert oh.machine_hosts("embed", root=tmp_path) == []
    # Ignored, never SILENTLY ignored: a typo here unpins the machine, which is
    # the exact failure this whole arrangement exists to make visible.
    assert "ollama-hosts" in capsys.readouterr().err


def test_a_non_utf8_file_is_reported_then_ignored_too(tmp_path, capsys):
    """One bad byte must not be worse than a syntax error.

    `UnicodeDecodeError` is a `ValueError` and a SIBLING of `yaml.YAMLError`,
    and it is raised inside the READ, before any parse, so
    `except (OSError, yaml.YAMLError)` walked straight past it. MEASURED
    2026-09-01 with `embed: [\\xff'auto:11434']`: `machine_hosts("embed")`
    RAISED rather than returning [], taking down `generation_host` (the 03:00
    chronicle build) and `index_embed_target` (`memory-index build`) over a
    gitignored file that is edited by hand on one laptop.

    The sibling case above covers unparseable YAML. This is the same promise
    for the other way a text file goes bad.
    """
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / oh.MACHINE_HOSTS_FILE).write_bytes(b"embed: [\xff'auto:11434']\n")
    assert oh.machine_hosts("embed", root=tmp_path) == []
    assert "ollama-hosts" in capsys.readouterr().err


def test_an_unknown_role_is_refused(tmp_path):
    with pytest.raises(ValueError):
        oh.machine_hosts("summarise", root=tmp_path)


# --- generation ---------------------------------------------------------------

def test_generation_uses_the_machine_pin(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_OLLAMA_HOST", raising=False)
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(oh, "probe", lambda host, **kw: host.endswith(":11434"))
    _write(tmp_path, {"generate": PIN})
    assert oh.generation_host(root=tmp_path) == "http://172.30.48.1:11434"


def test_a_pinned_generation_host_that_is_down_refuses(tmp_path, monkeypatch):
    """Generation refuses too, because there is nothing left to degrade TO.

    The first draft of this let it fall back to the local daemon, on the
    argument that the CPU copy of `gemma3:4b` writes the same summary. The
    operator removed the premise the same day: the ollama inside WSL is gone and
    every model lives on the Windows side, so "fall back to local" now means
    "connect to a daemon that is not installed". A refusal that names the dead
    address beats a connection error.

    The price is real and accepted: on a night the Windows side is asleep, the
    03:00 chronicle build writes no record.
    """
    monkeypatch.delenv("HEADING_OS_OLLAMA_HOST", raising=False)
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(oh, "probe", lambda host, **kw: False)
    _write(tmp_path, {"generate": PIN})
    with pytest.raises(oh.OllamaHostUnavailable, match="11434"):
        oh.generation_host(root=tmp_path)


def test_the_environment_variable_outranks_the_machine_file(tmp_path, monkeypatch):
    """The file is this machine's DEFAULT; the variable is a one-off override."""
    monkeypatch.setenv("HEADING_OS_OLLAMA_HOST", "http://10.0.0.5:11434")
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(oh, "probe", lambda host, **kw: True)
    _write(tmp_path, {"generate": PIN})
    assert oh.generation_host(root=tmp_path) == "http://10.0.0.5:11434"


def test_no_pin_anywhere_is_the_local_daemon_unprobed(tmp_path, monkeypatch):
    monkeypatch.delenv("HEADING_OS_OLLAMA_HOST", raising=False)

    def _forbidden(*a, **k):
        raise AssertionError("an unpinned machine must not probe anything")

    monkeypatch.setattr(oh, "probe", _forbidden)
    assert oh.generation_host(root=tmp_path) == oh.LOCAL_HOST


# --- embedding reads the same file --------------------------------------------

def test_embedding_uses_the_machine_pin(tmp_path, monkeypatch):
    from scripts.utils import embeddings, workspace

    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(oh, "probe", lambda host, **kw: host.endswith(":11436"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "memory-index.yaml").write_text(
        yaml.safe_dump({"model": "bge-m3"}), encoding="utf-8")
    _write(tmp_path, {"embed": PIN})
    host, model = embeddings.index_embed_target()
    assert host == "http://172.30.48.1:11436"
    assert model == "bge-m3"


def test_a_pinned_embedder_that_is_down_still_refuses(tmp_path, monkeypatch):
    """The 2026-08-23 rule survives the move: pinned and down means STOP."""
    from scripts.utils import embeddings, workspace

    monkeypatch.delenv("HEADING_OS_OLLAMA_EMBED_HOST", raising=False)
    monkeypatch.setattr(workspace, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(oh, "read_default_gateway", lambda *a, **k: "172.30.48.1")
    monkeypatch.setattr(oh, "probe", lambda host, **kw: False)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    _write(tmp_path, {"embed": PIN})
    with pytest.raises(embeddings.EmbeddingError, match="pinned"):
        embeddings.index_embed_target()


# --- what ships ----------------------------------------------------------------

def test_the_tracked_config_pins_nothing(tmp_path):
    """A public clone must embed on its own daemon without editing anything.

    This is the regression the move exists for: with the pin in the tracked
    config, `resolve_pinned_host` probed the clone's default gateway, got
    nothing, and refused. The clone's local ollama was up the whole time.
    """
    cfg = yaml.safe_load((ROOT / "config" / "memory-index.yaml").read_text(encoding="utf-8"))
    assert not cfg.get("host"), (
        "config/memory-index.yaml is tracked and ships to every clone; a host "
        f"pin there refuses on any machine that is not this one (found {cfg.get('host')!r}). "
        f"Machine pins belong in {oh.MACHINE_HOSTS_FILE}."
    )


def test_the_example_documents_both_roles_and_enables_neither():
    example = ROOT / "config" / "ollama-hosts.example.yaml"
    assert example.is_file(), f"{example.name} must ship as the template"
    text = example.read_text(encoding="utf-8")
    assert "embed" in text and "generate" in text
    payload = yaml.safe_load(text) or {}
    assert not payload.get("embed") and not payload.get("generate"), (
        "the example must document the shape, never switch a pin on"
    )


def test_the_machine_file_is_gitignored():
    """It names one laptop's NAT gateway. Committing it breaks every other clone."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert oh.MACHINE_HOSTS_FILE in [line.strip() for line in ignore]
