#!/usr/bin/env python3
"""Generate Claude Code plugin bundles from the HEADING OS monorepo (F-10.1).

The monorepo is the source of truth. This assembles installable plugin bundles
under a build dir (default: dist/marketplace/) from config/plugin-bundles.yaml,
without modifying any in-repo file.

Per built bundle it:
  - copies the declared skills, hooks, and scripts (plus scripts/utils/),
  - writes .claude-plugin/plugin.json (no version: auto-update per commit),
  - GENERATES hooks/hooks.json from the manifest hook_events (this workspace
    wires hooks in settings.local.json, which does not travel in a plugin),
  - GENERATES a SessionStart env hook that exports WORKSPACE_ROOT (and, when a
    userConfig data path is present, HEADING_OS_DATA) so bundled scripts resolve
    their root from the plugin cache,
  - writes NO root-marker files: in the plugin cache the root resolves from the
    exported WORKSPACE_ROOT plus paths.py's structural fallback, so a CLAUDE.md
    marker would be redundant AND trips `claude plugin validate` (see below),
  - rewrites `python scripts/...` invocations in built SKILL.md to the
    ${CLAUDE_PLUGIN_ROOT} form (the only path that resolves once a plugin is
    copied to ~/.claude/plugins/cache),
  - runs a completeness gate that FAILS the build if any built skill/hook
    references a scripts/ or .claude/hooks/ target that was not bundled.

Finally it writes .claude-plugin/marketplace.json listing the built bundles.

Usage:
  python scripts/dev/build-plugins.py --bundle heading-core
  python scripts/dev/build-plugins.py --all
  python scripts/dev/build-plugins.py --bundle heading-core --out /tmp/mkt
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.markdown import FM_OK, split_frontmatter_raw  # noqa: E402
from scripts.utils.paths import get_workspace_root  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a core dependency
    print("PyYAML is required (uv sync).", file=sys.stderr)
    sys.exit(2)

MARKETPLACE_NAME = "heading-os-marketplace"
OWNER = {"name": "Misha Hanin", "email": "misha.hanin@odinix.com"}

#: The manifest fields that make a bundle worth building. A bundle declaring any
#: one of them has content to deliver; a bundle declaring none of them is a
#: placeholder and `--all` skips it.
#:
#: `hook_events` and `session_start_env` are deliberately NOT here. Neither
#: delivers anything on its own: `hook_events` only wires hooks that must appear
#: in `hooks:`, which `manifest_sources` enforces, and `session_start_env` is a
#: switch on a bundle that already carries something.
CONTENT_FIELDS = ("skills", "hooks", "commands", "scripts")

# Rewrite `python|python3|bash <ws> scripts/` -> `... "${CLAUDE_PLUGIN_ROOT}"/scripts/`
# Only the scripts/ prefix token is touched; the path suffix and args are left intact.
_REWRITE_RE = re.compile(r"\b(python3?|bash)(\s+)scripts/")
_REWRITE_SUB = r'\1\2"${CLAUDE_PLUGIN_ROOT}"/scripts/'

# The same rewrite, with the quotes ESCAPED for a double-quoted YAML scalar.
#
# `allowed-tools` is a double-quoted scalar in every skill and command this
# workspace ships, and it carries `Bash(python scripts/...:*)` patterns. Applying
# the plain substitution there closes the scalar early and the frontmatter stops
# being YAML at all. Measured 2026-08-21 on the built heading-core bundle:
# `yaml.safe_load` raised ParserError on checkpoint/SKILL.md, and had done since
# the generator shipped - a bundle whose skill frontmatter does not parse. The
# body keeps the plain form; only the frontmatter is escaped.
_REWRITE_SUB_YAML = r'\1\2\\"${CLAUDE_PLUGIN_ROOT}\\"/scripts/'

# The frontmatter block comes from `split_frontmatter_raw`, which is
# byte-preserving: `front + body == text`, so nothing is normalised on the way
# through. `\A(---\r?\n.*?\r?\n---\r?\n)(.*)\Z` sat here and did not accept a
# fence carrying trailing whitespace. MEASURED 2026-08-29 on a SKILL.md whose
# opening fence is `--- `: the match failed, so the WHOLE file took the body
# substitution, and the plain form closes the `allowed-tools` double-quoted
# scalar early. That is the exact bundle-breaks-YAML defect `_REWRITE_SUB_YAML`
# above was written to prevent, reachable again through the splitter.

# Reference scanners for the completeness gate.
#
# WHAT THESE SEE, and what they do not. In SKILL.md, a hook, or a command: a
# literal `scripts/foo/bar.py`, a `.claude/hooks/x.py`, and a `python -m
# scripts.foo.bar`. In the skill's other bundled Markdown, only an INVOKED
# `python|bash scripts/x.py` (see `_INVOKE_REF_RE` for why that one is narrower).
#
# They do NOT see an extensionless `scripts/tool`, a `bash scripts/tool.sh`, a
# path built at runtime from pieces, or a bare non-invoked path inside a
# reference file — those can be broken in an installed bundle while this build
# still passes, and the gate's report says "no missing targets", not "no broken
# references". Widening further means guessing at arbitrary shell text, which
# trades a known blind spot for false failures on every build.
_SCRIPT_REF_RE = re.compile(r"scripts/([\w./-]+\.py)")
_HOOK_REF_RE = re.compile(r"\.claude/hooks/([\w./-]+\.py)")
# `python -m scripts.utils.x`. Added 2026-08-24: the dotted form reaches the
# same file as a path reference and the gate could not see it at all.
_DOTTED_REF_RE = re.compile(r"-m\s+scripts\.([\w.]+)")

# The scanner for the skill's OTHER prose - `references/`, `tests.md` - which
# ships in the bundle beside SKILL.md and which neither the gate nor the
# rewriter used to open. Measured on the current manifest before the fix:
# heading-content shipped `linkedin-post/evals/README.md` telling the consumer
# to run `python scripts/run-skill-eval.py`, a script in no bundle at all, and
# the gate printed "no missing targets" over it.
#
# Deliberately NARROWER than `_SCRIPT_REF_RE`. In a reference file a bare
# `scripts/models/user.py` is illustration - `create-plan/references/
# plan-template.md` carries two such lines under "**Files affected:**" - and
# failing the build on example prose is the false-failure cost this file's own
# scanner comment warns about. An invocation prefix is the discriminator: it
# marks a command the reader is being told to type.
_INVOKE_REF_RE = re.compile(r"\b(?:python3?|bash)\s+scripts/([\w./-]+\.py)")

# Directory names never copied into a bundle. `evals/` is the skill's own
# regression corpus (case JSON plus a benchmark file) and the harness that reads
# it is a workspace dev script, so shipping it hands the consumer a README for a
# tool the bundle does not carry.
_SKILL_EXCLUDE_DIRS = ("evals",)
_SKILL_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", *_SKILL_EXCLUDE_DIRS)


def load_manifest(root: Path) -> dict:
    path = root / "config" / "plugin-bundles.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["bundles"]


def _copytree(src: Path, dst: Path, ignore=None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Never ship compiled-bytecode cruft in a bundle (stale, bloated, non-source).
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=ignore or shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def bundled_skill_prose(skill_dir: Path) -> list[Path]:
    """Markdown a bundled skill ships BESIDES its SKILL.md.

    One definition for the gate and the rewriter both, so the set of files the
    gate reads can never drift from the set the bundle actually carries.
    """
    return sorted(
        p for p in skill_dir.rglob("*.md")
        if p.name != "SKILL.md"
        and not any(part in _SKILL_EXCLUDE_DIRS for part in p.relative_to(skill_dir).parts)
    )


def collect_bundled_scripts(spec: dict, root: Path) -> set[str]:
    """Set of repo-relative script paths that WILL be bundled (for the gate)."""
    bundled = {f"scripts/{s}" for s in spec.get("scripts", [])}
    # scripts/utils/ is always bundled wholesale.
    for p in (root / "scripts" / "utils").rglob("*.py"):
        bundled.add(str(p.relative_to(root)))
    return bundled


def completeness_gate(spec: dict, root: Path) -> list[str]:
    """Return a list of missing targets referenced by the bundle's skills, hooks
    and slash commands.

    Commands joined the scan on 2026-08-21, with the field that ships them. A
    command body is one or two `python scripts/...` lines, so a bundled command
    whose script was not bundled is the same broken reference a skill would be,
    and it should fail the build for the same reason.
    """
    bundled_scripts = collect_bundled_scripts(spec, root)
    bundled_hooks = set(spec.get("hooks", []))
    missing: list[str] = []

    sources: list[Path] = []
    prose: list[Path] = []
    for skill in spec.get("skills", []):
        skill_dir = root / ".claude" / "skills" / skill
        sm = skill_dir / "SKILL.md"
        if sm.exists():
            sources.append(sm)
        if skill_dir.is_dir():
            prose.extend(bundled_skill_prose(skill_dir))
    for hook in spec.get("hooks", []):
        hp = root / ".claude" / "hooks" / hook
        if hp.exists():
            sources.append(hp)
    for command in spec.get("commands", []):
        cp = root / ".claude" / "commands" / command
        if cp.exists():
            sources.append(cp)

    for src in sources:
        # `errors="replace"`, not a bare decode. This gate returns a LIST OF
        # NAMED missing targets, and a SKILL.md, hook or command carrying one
        # stray non-UTF-8 byte raised UnicodeDecodeError - a ValueError, caught
        # by nothing between here and the build - so the whole bundle build died
        # on a traceback that named no file. Replacing an undecodable byte
        # cannot manufacture a `scripts/...py` reference (the patterns need
        # ASCII word characters) and cannot hide one either, since a reference
        # made of ASCII decodes unchanged. So the scan still sees exactly what
        # is there, and the build reports rather than dies.
        text = src.read_text(encoding="utf-8", errors="replace")
        for ref in _SCRIPT_REF_RE.findall(text):
            rel = f"scripts/{ref}"
            # Exact repo-relative membership, and nothing looser. Two bypasses
            # used to sit here and each let a dead reference ship:
            #
            #   `Path(ref).name in bundled_script_names` accepted a BASENAME
            #   match, so a bundle carrying `scripts/a/tool.py` passed a skill
            #   referencing `scripts/b/tool.py` — a different file that is not
            #   in the bundle at all.
            #
            #   `if ref.startswith("utils/"): continue` skipped every utils
            #   reference wholesale, so `scripts/utils/does_not_exist.py` was
            #   never reported. The blanket skip was never needed:
            #   `collect_bundled_scripts` already adds every real file under
            #   `scripts/utils/`, so a utils path that exists matches exactly
            #   and one that does not SHOULD fail.
            if rel in bundled_scripts:
                continue
            missing.append(f"{src.relative_to(root)} -> scripts/{ref}")
        for ref in _DOTTED_REF_RE.findall(text):
            rel = "scripts/" + ref.replace(".", "/") + ".py"
            if rel in bundled_scripts:
                continue
            missing.append(f"{src.relative_to(root)} -> {rel} (as -m scripts.{ref})")
        for ref in _HOOK_REF_RE.findall(text):
            if Path(ref).name not in bundled_hooks:
                missing.append(f"{src.relative_to(root)} -> .claude/hooks/{ref}")

    for src in prose:
        # `errors="replace"` for the reason given over the `sources` loop above.
        for ref in set(_INVOKE_REF_RE.findall(
                src.read_text(encoding="utf-8", errors="replace"))):
            if f"scripts/{ref}" in bundled_scripts:
                continue
            missing.append(f"{src.relative_to(root)} -> scripts/{ref}")
    return missing


def generate_hooks_json(spec: dict) -> dict:
    """Build the plugin hooks.json from the manifest hook_events + session_start_env."""
    hooks: dict = {}
    for event, blocks in (spec.get("hook_events") or {}).items():
        event_blocks = []
        for block in blocks:
            cmds = [
                {
                    "type": "command",
                    "command": f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/{name}"',
                }
                for name in block.get("hooks", [])
            ]
            event_blocks.append({"matcher": block.get("matcher", ""), "hooks": cmds})
        if event_blocks:
            hooks[event] = event_blocks
    if spec.get("session_start_env"):
        hooks.setdefault("SessionStart", []).append(
            {
                "matcher": "startup",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session-env.py"',
                    }
                ],
            }
        )
    return {"hooks": hooks}


SESSION_ENV_SCRIPT = '''#!/usr/bin/env python3
"""Generated by build-plugins.py. Exports the plugin's workspace root (and, when
provided, the operator data root) for the rest of the session's Bash calls.

WORKSPACE_ROOT is the first-honored override in scripts/utils/paths.py, so this
is what makes bundled scripts resolve their root from ~/.claude/plugins/cache
instead of marker-walking (the cache has no CLAUDE.md/.claude markers of the
real workspace). HEADING_OS_DATA is exported only when the operator supplied a
data path via plugin userConfig; otherwise the data root falls back to demo.
"""
import os

env_file = os.environ.get("CLAUDE_ENV_FILE")
plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
if env_file and plugin_root:
    lines = [f'export WORKSPACE_ROOT="{plugin_root}"\\n']
    data_root = os.environ.get("CLAUDE_PLUGIN_OPTION_DATA_ROOT")
    if data_root:
        lines.append(f'export HEADING_OS_DATA="{data_root}"\\n')
    with open(env_file, "a", encoding="utf-8") as f:
        f.writelines(lines)
'''

def rewrite_script_paths(text: str) -> tuple[str, int]:
    """Point every `python scripts/...` line at the plugin cache.

    Frontmatter and body are rewritten differently, because the frontmatter is
    YAML and the body is prose. See `_REWRITE_SUB_YAML` for what the single-form
    version broke and for how long.
    """
    front, body, kind = split_frontmatter_raw(text)
    if front is None or kind != FM_OK:
        return _REWRITE_RE.subn(_REWRITE_SUB, text)

    front_new, front_n = _rewrite_frontmatter(front)
    body_new, body_n = _REWRITE_RE.subn(_REWRITE_SUB, body)
    return front_new + body_new, front_n + body_n


def _rewrite_frontmatter(front: str) -> tuple[str, int]:
    """Rewrite the frontmatter line by line, escaping only inside a quoted scalar.

    A double-quoted value takes the escaped form. Anything else takes the bare
    `${CLAUDE_PLUGIN_ROOT}` with no quotes at all, since adding a quote to a plain
    scalar is the defect this function exists to avoid. The bare form loses the
    protection against a plugin-cache path containing a space, which is the price
    of staying parseable; the quoted-scalar case, which is every case in this
    workspace today, keeps it.
    """
    out: list[str] = []
    total = 0
    for line in front.splitlines(keepends=True):
        if not _REWRITE_RE.search(line):
            out.append(line)
            continue
        _, _, value = line.partition(":")
        quoted = value.lstrip().startswith('"')
        sub = _REWRITE_SUB_YAML if quoted else r"\1\2${CLAUDE_PLUGIN_ROOT}/scripts/"
        new, n = _REWRITE_RE.subn(sub, line)
        out.append(new)
        total += n
    return "".join(out), total


def wired_hooks(spec: dict) -> list[tuple[str, str]]:
    """(event, hook filename) for every hook this bundle's `hook_events` WIRES.

    One reader for the field, shared by the validator below and available to any
    future one, so "what does hooks.json point at" can never be answered
    differently from how `generate_hooks_json` builds it.
    """
    wired: list[tuple[str, str]] = []
    for event, blocks in (spec.get("hook_events") or {}).items():
        for block in blocks:
            for hook in block.get("hooks", []):
                wired.append((event, hook))
    return wired


def manifest_sources(name: str, spec: dict, root: Path) -> list[str]:
    """Every source this bundle names that the built bundle cannot deliver.

    Two ways a name fails to deliver, and this function reports both:

      1. It is not on disk at all (a typo in `config/plugin-bundles.yaml`).
      2. A hook `hook_events` WIRES is not in the `hooks:` list that
         `build_bundle` copies. The generated `hooks.json` then registers a
         command under `${CLAUDE_PLUGIN_ROOT}/hooks/<name>` that the bundle
         never carries, so the consumer declares a hook that cannot run.

    Until 2026-08-29 only (1) was checked, and only over `hooks:`. `hook_events`
    had no validator of any kind, so heading-core could wire `prompt-guard.py`
    and `post-write-sanitize.py` - the sovereignty guards - out of a `hooks:`
    list that omitted either one, and the build stayed green. A pure typo
    (`prompt-gaurd.py`) shipped a hooks.json pointing at a filename that exists
    nowhere in the repository. The docstring already promised what the method
    did not establish; that promise is what this function now keeps.

    The asymmetry is DELIBERATE and one-directional. `hooks:` MAY carry a hook
    that `hook_events` does not wire, because `checkpoint-statusline.py` does
    exactly that: Claude Code exposes context-window usage only to a statusLine
    and a plugin manifest has no statusLine key, so the file must ship as a
    script the consumer wires by hand. Shipping an unwired hook costs one unused
    file; wiring an unshipped hook is a dangling command. Only the second is a
    defect, and `tests/test_build_plugins.py` pins the first as intended.

    The per-component checks in `build_bundle` each raise SystemExit the moment
    they meet a missing entry, and they run AFTER `shutil.rmtree(bundle)` and
    after `plugin.json` is written. So a typo deleted the previous bundle and
    left a half-written one, under a comment promising to "fail fast before
    writing anything". `completeness_gate` checks unbundled REFERENCES, which is
    a different question and does not cover any of this.

    Low severity for (1), stated plainly: the bundle lives under
    `dist/marketplace/`, which is untracked and regenerated by the next
    successful build, and `write_marketplace` never runs on that path. (2) is
    not low severity - `.github/workflows/publish-marketplace.yml` publishes
    automatically, so a dangling guard reaches installers.
    """
    missing = []
    for skill in spec.get("skills", []):
        if not (root / ".claude" / "skills" / skill).is_dir():
            missing.append(f"skill not found: .claude/skills/{skill}")
    for command in spec.get("commands", []):
        if not (root / ".claude" / "commands" / command).is_file():
            missing.append(f"command not found: .claude/commands/{command}")
    for hook in spec.get("hooks", []):
        if not (root / ".claude" / "hooks" / hook).is_file():
            missing.append(f"hook not found: .claude/hooks/{hook}")
    # Existence is reported BEFORE membership, and instead of it, because the
    # two have different fixes. A typo is fixed by correcting the spelling; a
    # wired-but-unlisted hook is fixed by adding it to `hooks:`. Telling an
    # operator to add `prompt-gaurd.py` to `hooks:` sends them to bundle a file
    # that does not exist.
    listed = set(spec.get("hooks", []))
    for event, hook in wired_hooks(spec):
        if not (root / ".claude" / "hooks" / hook).is_file():
            missing.append(
                f"hook not found: .claude/hooks/{hook} (wired by hook_events.{event})")
        elif hook not in listed:
            missing.append(
                f"hook wired but not bundled: {hook} is in hook_events.{event} "
                "and not in hooks:")
    for rel in spec.get("scripts", []):
        if not (root / "scripts" / rel).is_file():
            missing.append(f"script not found: scripts/{rel}")
    return missing


def build_bundle(name: str, spec: dict, out_root: Path, root: Path) -> None:
    # Completeness gate FIRST (fail fast before writing anything).
    missing = completeness_gate(spec, root)
    if missing:
        print(f"[{name}] completeness gate FAILED, unbundled references:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        raise SystemExit(3)

    # Then every source the manifest NAMES, still before anything is written or
    # deleted. All of them are reported at once: raising on the first one makes
    # a manifest with three typos take three runs to fix.
    absent = manifest_sources(name, spec, root)
    if absent:
        # "cannot deliver", not "do not exist": the same list now also reports a
        # hook that hook_events WIRES out of a hooks: list that omits it, which
        # is on disk and still never reaches the bundle.
        print(f"[{name}] manifest names {len(absent)} source(s) it cannot deliver:",
              file=sys.stderr)
        for m in absent:
            print(f"  - {m}", file=sys.stderr)
        raise SystemExit(3)

    bundle = out_root / "plugins" / name
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / ".claude-plugin").mkdir(parents=True, exist_ok=True)

    # plugin.json (no version: auto-update per commit).
    plugin_json = {
        "name": name,
        "description": " ".join((spec.get("description") or "").split()),
        "author": OWNER,
    }
    (bundle / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(plugin_json, indent=2) + "\n", encoding="utf-8"
    )

    # Skills (verbatim), then rewrite script paths in each built SKILL.md.
    rewrites = 0
    for skill in spec.get("skills", []):
        src = root / ".claude" / "skills" / skill
        if not src.is_dir():
            raise SystemExit(f"[{name}] skill not found: {skill}")
        _copytree(src, bundle / "skills" / skill, ignore=_SKILL_IGNORE)
    # Every Markdown the skill ships, not only SKILL.md. A `references/` page is
    # read by the same consumer in the same cache, and `docparse/references/
    # integration.md` carried four `python scripts/docparse.py ...` command
    # lines that the rewriter never opened - so the bundled script was there and
    # the documented way to run it still did not resolve.
    for md in (bundle / "skills").rglob("*.md"):
        # Retried once, then REFUSED -- deliberately not the skip-and-warn that
        # `scripts.utils.repo_files.read_sources` gives a per-file scanner. This
        # walk is over the bundle THIS RUN just wrote, and what it produces is a
        # published artifact that `completeness_gate` above has already certified
        # as complete. Skipping one page would ship it with its `python
        # scripts/...` lines unrewritten -- precisely the defect the comment
        # above describes -- inside a bundle whose whole claim is that every
        # reference resolves. The retry recovers a writer's unlink-and-rewrite
        # window and nothing else; a page that is genuinely gone means something
        # is mutating the build output underneath the build, so no bundle
        # assembled over it can be trusted.
        try:
            text = md.read_text(encoding="utf-8")
        except FileNotFoundError:
            try:
                text = md.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise SystemExit(
                    f"[{name}] {md} vanished between the bundle walk and the "
                    f"script-path rewrite. The bundle changed while it was "
                    f"being built, so it would ship that page unrewritten. "
                    f"Re-run once the tree is quiet."
                ) from exc
        new, n = rewrite_script_paths(text)
        if n:
            md.write_text(new, encoding="utf-8")
            rewrites += n

    # Slash commands, with the same script-path rewrite the skills get.
    #
    # There was no field for these until 2026-08-21, so heading-core shipped the
    # `/checkpoint` skill while `/unattended` and `/compact-at` - the two switches
    # that skill's own body tells the operator to run - stayed behind in
    # `.claude/commands/`. The consumer got instructions naming commands the
    # plugin does not carry. `commands/` is a first-class plugin component, so
    # the fix is a field, not a note in the docs.
    for command in spec.get("commands", []):
        src = root / ".claude" / "commands" / command
        if not src.is_file():
            raise SystemExit(f"[{name}] command not found: .claude/commands/{command}")
        dst = bundle / "commands" / command
        dst.parent.mkdir(parents=True, exist_ok=True)
        text, n = rewrite_script_paths(src.read_text(encoding="utf-8"))
        dst.write_text(text, encoding="utf-8")
        rewrites += n

    # Hooks (verbatim) + generated hooks.json + session-env hook.
    hooks_dir = bundle / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for hook in spec.get("hooks", []):
        src = root / ".claude" / "hooks" / hook
        if not src.is_file():
            raise SystemExit(f"[{name}] hook not found: {hook}")
        shutil.copy2(src, hooks_dir / hook)
    if spec.get("session_start_env"):
        (hooks_dir / "session-env.py").write_text(SESSION_ENV_SCRIPT, encoding="utf-8")
    (hooks_dir / "hooks.json").write_text(
        json.dumps(generate_hooks_json(spec), indent=2) + "\n", encoding="utf-8"
    )

    # Scripts: enumerated + scripts/utils/ wholesale.
    scripts_dir = bundle / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for rel in spec.get("scripts", []):
        src = root / "scripts" / rel
        if not src.is_file():
            raise SystemExit(f"[{name}] script not found: scripts/{rel}")
        dst = scripts_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    _copytree(root / "scripts" / "utils", scripts_dir / "utils")

    # Root resolution in the plugin cache is handled two ways, neither needing a
    # marker file: the generated SessionStart hook exports WORKSPACE_ROOT (the
    # first-honored override in paths.py), and paths.py's structural fallback
    # (_FALLBACK_ROOT = <this file>.parent.parent.parent) already resolves to
    # the bundle root because scripts/utils/paths.py keeps its depth. A CLAUDE.md
    # marker at the plugin root is therefore redundant AND trips
    # `claude plugin validate` (root context warning), so it is NOT written.

    print(f"[{name}] built at {bundle} ({rewrites} script-path rewrites)")


def write_marketplace(names: list[str], manifest: dict, out_root: Path) -> None:
    (out_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    plugins = []
    for name in names:
        spec = manifest[name]
        plugins.append(
            {
                "name": name,
                "source": f"./plugins/{name}",
                "description": " ".join((spec.get("description") or "").split()),
                "category": "heading-os",
            }
        )
    market = {
        "name": MARKETPLACE_NAME,
        "owner": OWNER,
        "description": "HEADING OS: the operations engine an executive runs their company from, as installable plugin bundles.",
        "plugins": plugins,
    }
    (out_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(market, indent=2) + "\n", encoding="utf-8"
    )
    print(f"marketplace.json written for: {', '.join(names)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build HEADING OS plugin bundles.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bundle", help="single bundle name to build")
    g.add_argument("--all", action="store_true", help="build every non-empty bundle")
    ap.add_argument("--out", help="output dir (default: dist/marketplace)")
    args = ap.parse_args(argv)

    root = get_workspace_root()
    manifest = load_manifest(root)
    out_root = Path(args.out).expanduser().resolve() if args.out else root / "dist" / "marketplace"

    if args.bundle:
        if args.bundle not in manifest:
            print(f"unknown bundle: {args.bundle}", file=sys.stderr)
            return 2
        names = [args.bundle]
    else:
        # --all builds every bundle that declares content (skip placeholders).
        #
        # The field list is a NAMED CONSTANT rather than a chain of `or`s,
        # because this filter has now fallen behind the manifest twice. First
        # `commands`, which became a first-class field on 2026-08-21: a
        # commands-only bundle was silently never built and `--all` said nothing
        # about it. Then `scripts`, which `collect_bundled_scripts`,
        # `manifest_sources` and `build_bundle` have all consumed from the
        # start: a scripts-only bundle was skipped here AND left out of
        # `marketplace.json` by `write_marketplace(names, ...)` below, so it was
        # never published either, at exit 0 with no message.
        #
        # `tests/test_a_bundle_filter_that_fell_behind_its_own_manifest.py` now
        # holds this tuple against the fields the rest of the file reads, so a
        # seventh field cannot arrive without this line failing.
        names = [n for n, s in manifest.items()
                 if any(s.get(field) for field in CONTENT_FIELDS)]

    out_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        build_bundle(name, manifest[name], out_root, root)
    write_marketplace(names, manifest, out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
