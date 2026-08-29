"""A bundle could wire a hook into hooks.json without ever shipping the file.

`scripts/dev/build-plugins.py` reads two fields that both name hooks, and until
2026-08-29 it validated one of them. `hooks:` is the copy list: `manifest_sources`
checked each entry exists and `build_bundle` copied each entry. `hook_events:` is
the wiring that `generate_hooks_json` turns into
`python3 "${CLAUDE_PLUGIN_ROOT}/hooks/<name>"` commands, and nothing read it at
all. So the generated hooks.json could register a command for a file the bundle
does not carry, and the build printed nothing.

Two cases, both measured on the pre-fix tree:

  Case A, wired but not copied. A spec listing `prompt-guard.py` under `hooks:`
  while `hook_events` wired both `prompt-guard.py` and `post-write-sanitize.py`::

      completeness_gate -> []
      manifest_sources  -> []
      BUILD EXIT: green
      wired in hooks.json: ['post-write-sanitize.py', 'prompt-guard.py']
      shipped in bundle  : ['prompt-guard.py']
      DANGLING: ['post-write-sanitize.py']

  Case B, a pure typo. `hook_events` naming `prompt-gaurd.py`: build green, and
  hooks.json shipped a command pointing at a filename that exists nowhere in the
  repository.

Why it matters rather than being tidy: the two hooks heading-core wires under
PostToolUse are `prompt-guard.py` and `post-write-sanitize.py`, the sovereignty
guards, and `.github/workflows/publish-marketplace.yml` publishes on every push
to main without a human in the path. A consumer would install a plugin that
DECLARES a PostToolUse guard and runs nothing.

Why no test caught it: `tests/test_build_plugins.py` asserts the forward
direction only (what IS wired appears in hooks.json), and every spec literal in
that file passes `"hook_events": {}`. The gate for this field was green over an
empty corpus, which is why every test below that touches `hook_events` also
asserts the corpus is not empty.

The fix is one-directional on purpose. `hooks:` may still carry a hook that
`hook_events` does not wire, because `checkpoint-statusline.py` does exactly
that and `tests/test_build_plugins.py` pins it: a plugin manifest has no
statusLine key, so the file ships for the consumer to wire by hand.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "dev" / "build-plugins.py"
MANIFEST = ROOT / "config" / "plugin-bundles.yaml"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_plugins_mod", BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["bundles"]


# A spec shaped like heading-core's: real hooks that exist on disk, real wiring.
# `hook_events` is deliberately NON-EMPTY in every fixture below. A literal with
# `hook_events: {}` says nothing whatever about this field, which is precisely
# how the defect reached production.
def _spec_wiring(listed: list[str], wired: list[str]) -> dict:
    return {
        "description": "probe",
        "skills": [],
        "commands": [],
        "scripts": [],
        "hooks": list(listed),
        "hook_events": {
            "PostToolUse": [
                {"matcher": "Write|Edit|MultiEdit", "hooks": list(wired)},
            ]
        },
    }


# --- case A: wired but never copied -------------------------------------------

def test_a_hook_wired_by_hook_events_but_absent_from_the_hooks_list_is_refused(
        builder, tmp_path):
    spec = _spec_wiring(listed=["prompt-guard.py"],
                        wired=["prompt-guard.py", "post-write-sanitize.py"])

    with pytest.raises(SystemExit) as exc:
        builder.build_bundle("probe", spec, tmp_path, ROOT)

    assert exc.value.code == 3, "the same exit code every other missing source uses"


def test_the_refusal_names_the_wired_hook_and_says_it_is_not_in_the_hooks_list(
        builder):
    """Naming the file is what makes the failure actionable, and the WORDING is
    the diagnosis: this one is fixed by adding the name to `hooks:`."""
    spec = _spec_wiring(listed=["prompt-guard.py"],
                        wired=["prompt-guard.py", "post-write-sanitize.py"])

    absent = builder.manifest_sources("probe", spec, ROOT)

    assert len(absent) == 1, f"expected exactly one finding, got {absent}"
    assert "post-write-sanitize.py" in absent[0]
    assert "not in hooks:" in absent[0]
    assert "hook not found" not in absent[0], (
        "the file IS on disk; calling it missing sends the operator to the "
        "wrong fix"
    )


def test_the_bundle_is_not_written_when_a_wired_hook_would_dangle(builder, tmp_path):
    """The refusal happens in the pre-write phase, like every other one."""
    spec = _spec_wiring(listed=["prompt-guard.py"],
                        wired=["prompt-guard.py", "post-write-sanitize.py"])

    with pytest.raises(SystemExit):
        builder.build_bundle("probe", spec, tmp_path, ROOT)

    assert not (tmp_path / "plugins" / "probe" / "hooks" / "hooks.json").exists()
    assert not (tmp_path / "plugins" / "probe" / ".claude-plugin").exists()


# --- case B: a pure typo in hook_events ---------------------------------------

def test_a_misspelled_hook_in_hook_events_is_refused(builder, tmp_path):
    spec = _spec_wiring(listed=["prompt-guard.py"], wired=["prompt-gaurd.py"])

    with pytest.raises(SystemExit) as exc:
        builder.build_bundle("probe", spec, tmp_path, ROOT)

    assert exc.value.code == 3


def test_a_misspelled_hook_is_reported_as_absent_from_disk_not_as_unbundled(builder):
    """The two failures have different fixes, so they must read differently.

    Telling an operator that `prompt-gaurd.py` is "not in hooks:" sends them to
    add a nonexistent filename to the copy list.
    """
    spec = _spec_wiring(listed=["prompt-guard.py"], wired=["prompt-gaurd.py"])

    absent = builder.manifest_sources("probe", spec, ROOT)

    assert len(absent) == 1, f"expected exactly one finding, got {absent}"
    assert "prompt-gaurd.py" in absent[0]
    assert "hook not found" in absent[0]
    assert "not in hooks:" not in absent[0]


def test_a_typo_that_reaches_hooks_json_would_point_at_nothing_in_the_repository():
    """The premise of case B, asserted rather than assumed."""
    assert (ROOT / ".claude" / "hooks" / "prompt-guard.py").is_file()
    assert not (ROOT / ".claude" / "hooks" / "prompt-gaurd.py").exists()


# --- the deliberate asymmetry -------------------------------------------------

def test_a_hook_that_ships_without_being_wired_is_still_allowed(builder):
    """One-directional by design. `checkpoint-statusline.py` ships unwired
    because a plugin manifest has no statusLine key, and
    `tests/test_build_plugins.py` pins that. A fix that refused this direction
    too would break the real manifest."""
    spec = _spec_wiring(listed=["prompt-guard.py", "post-write-sanitize.py"],
                        wired=["prompt-guard.py"])

    assert builder.manifest_sources("probe", spec, ROOT) == []


# --- the positive control -----------------------------------------------------

def test_todays_real_manifest_still_builds_green(builder, tmp_path):
    """The fix must refuse the two cases above and nothing else. heading-core is
    the bundle that carries the wiring, so it is the one that proves it."""
    assert builder.main(["--bundle", "heading-core", "--out", str(tmp_path)]) == 0

    hooks_json = tmp_path / "plugins" / "heading-core" / "hooks" / "hooks.json"
    wired = json.dumps(json.loads(hooks_json.read_text(encoding="utf-8")))
    assert "prompt-guard.py" in wired and "post-write-sanitize.py" in wired


def test_no_real_bundle_names_a_source_the_build_cannot_deliver(builder, manifest):
    assert manifest, "the manifest parsed to nothing"
    for name, spec in manifest.items():
        assert builder.manifest_sources(name, spec, ROOT) == [], (
            f"bundle {name} names a source the build cannot deliver"
        )


# --- the derived rule over the real manifest ----------------------------------

def _wired(spec: dict) -> list[tuple[str, str]]:
    """Read the field independently of the builder, so this rule still holds if
    the builder's own reader is the thing that breaks."""
    return [(event, hook)
            for event, blocks in (spec.get("hook_events") or {}).items()
            for block in blocks
            for hook in (block.get("hooks") or [])]


def test_every_hook_the_real_manifest_wires_is_also_in_that_bundles_hooks_list(
        manifest):
    assert manifest, "the manifest parsed to nothing"
    offenders = [
        f"{name}.hook_events.{event} wires {hook}, absent from its hooks: list"
        for name, spec in manifest.items()
        for event, hook in _wired(spec)
        if hook not in set(spec.get("hooks") or [])
    ]
    assert not offenders, offenders


def test_every_hook_the_real_manifest_wires_exists_on_disk(manifest):
    assert manifest, "the manifest parsed to nothing"
    offenders = [
        f"{name}.hook_events.{event} wires {hook}, which is not in .claude/hooks/"
        for name, spec in manifest.items()
        for event, hook in _wired(spec)
        if not (ROOT / ".claude" / "hooks" / hook).is_file()
    ]
    assert not offenders, offenders


# --- anti-vacuity -------------------------------------------------------------

def test_at_least_one_real_bundle_wires_a_non_empty_hook_events_block(manifest):
    """The whole reason this defect survived.

    Four of the five bundles carry `hook_events: {}`. The two derived rules
    above iterate that field, so over an all-empty manifest they iterate nothing
    and pass while proving nothing. If heading-core's wiring is ever emptied,
    this test fails and says why, instead of the suite going quietly green.
    """
    wiring = {name: _wired(spec) for name, spec in manifest.items()}
    non_empty = {name: pairs for name, pairs in wiring.items() if pairs}
    assert non_empty, (
        "no bundle wires any hook_events entry, so the derived rules above ran "
        f"over an empty corpus. Bundles seen: {sorted(wiring)}"
    )
    assert sum(len(p) for p in non_empty.values()) >= 2, (
        f"only one wired hook in the whole manifest: {non_empty}"
    )


def test_the_case_fixtures_themselves_wire_something(builder):
    """The same trap, one level in. A fixture spec with `hook_events: {}` would
    make every case above pass against a builder that never reads the field."""
    for listed, wired in (
        (["prompt-guard.py"], ["prompt-guard.py", "post-write-sanitize.py"]),
        (["prompt-guard.py"], ["prompt-gaurd.py"]),
        (["prompt-guard.py", "post-write-sanitize.py"], ["prompt-guard.py"]),
    ):
        spec = _spec_wiring(listed=listed, wired=wired)
        assert builder.wired_hooks(spec), f"fixture wires nothing: {spec}"
        assert _wired(spec) == builder.wired_hooks(spec), (
            "this file's reader and the builder's disagree about the field"
        )
