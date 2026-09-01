import ast
import textwrap
from pathlib import Path
import pytest
from scripts.utils import update_sources
from scripts.utils.update_registry import load_registry, RegistryError

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "config" / "update-registry.yaml"

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

def test_apply_with_both_cmd_and_script_is_rejected(tmp_path):
    reg = _write(tmp_path, """
        components:
          x:
            tier: notify
            current: {via: shell, cmd: "x --version"}
            latest: {via: github_release, repo: x/y}
            apply: {cmd: "true", rollback_cmd: "r", script: scripts/updaters/x.py}
    """)
    with pytest.raises(RegistryError, match="both"):
        load_registry(reg)

def test_non_mapping_top_level_is_rejected(tmp_path):
    reg = _write(tmp_path, "just a scalar string\n")
    with pytest.raises(RegistryError, match="mapping"):
        load_registry(reg)


# ============================================================
# The registry and the source adapters cannot silently diverge
# ============================================================

def _implemented_latest_via() -> set:
    """The `via` values `update_sources.latest_version` actually dispatches on,
    read off its own AST rather than typed here.

    `load_registry` validates the shape of `latest:` and never its `via`, and
    `update-manager.resolve_latest` CATCHES the SourceError an unknown `via`
    raises, prints one stderr line and returns "". A component whose `via` is
    misspelled therefore parks at status `unknown` for ever, and `cmd_check`
    exits non-zero only when EVERY component is unknown - so a single typo is a
    component that quietly stops being version-checked while the run still says
    "checked 4 components; 0 waiting". Nothing in the loading path notices, which
    is why the check has to live here.
    """
    src = (ROOT / "scripts" / "utils" / "update_sources.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "latest_version")
    handled = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "via"
                and all(isinstance(op, ast.Eq) for op in node.ops)):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    handled.add(comparator.value)
    return handled


def test_the_ast_walk_finds_the_adapters_it_claims_to_find():
    """Anchor. An extractor that returned the empty set would let every
    assertion below pass over any registry at all."""
    handled = _implemented_latest_via()
    assert len(handled) >= 3, handled
    for via in handled:
        with pytest.raises(KeyError):
            # Each name reaches its branch and then asks the spec for the key
            # that branch needs, which proves the branch is live rather than a
            # string that merely appears in the file.
            update_sources.latest_version({"via": via})


def test_every_shipped_component_names_a_source_adapter_that_exists():
    handled = _implemented_latest_via()
    comps = load_registry(SHIPPED)
    assert comps, "the shipped registry no longer loads"
    unknown = [(c.name, c.latest.get("via")) for c in comps
               if c.latest.get("via") not in handled]
    assert unknown == [], (
        f"{unknown} name a `latest.via` no adapter in update_sources implements "
        f"(implemented: {sorted(handled)}). Each of these parks at status "
        f"'unknown' on every run, with the component silently never checked."
    )


def test_every_shipped_component_carries_the_field_resolve_current_reads():
    """`resolve_current` reads `current.cmd` and nothing else - not even
    `current.via`, which every entry declares and no code consults. An entry
    with no `cmd` runs `bash -c ""`, resolves to "", and parks at `unknown` the
    same way. The dead `via` key is recorded here rather than removed: two
    spellings of it are already in use across the corpus (`shell` in the shipped
    registry, `cmd` in several test fixtures) and picking one is the operator's
    call, not a test's."""
    for comp in load_registry(SHIPPED):
        cmd = comp.current.get("cmd")
        assert isinstance(cmd, str) and cmd.strip(), (
            f"component {comp.name!r}: `current.cmd` is {cmd!r}; resolve_current "
            f"would run an empty shell command and report the component unknown"
        )
