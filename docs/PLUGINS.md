# Install as a Claude Code plugin

HEADING OS ships an installable plugin marketplace, so you can try the engine's
core inside Claude Code with two commands, no clone and no toolchain.

Last Updated: 2026-07-09

## The marketplace

The plugins live in their own public repository,
**[mishahanin/heading-os-marketplace](https://github.com/mishahanin/heading-os-marketplace)**.
It is a [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin
marketplace: a git repo carrying a `.claude-plugin/marketplace.json` and one
directory per plugin bundle. It is generated from this engine monorepo (the
source of truth), so you never install anything the engine did not produce.

## Install

Inside Claude Code:

```
/plugin marketplace add mishahanin/heading-os-marketplace
/plugin install heading-core@heading-os-marketplace
```

(The CLI form works too: `claude plugin marketplace add mishahanin/heading-os-marketplace`.)

That is it. `heading-core` is the sovereignty and session bundle: the
`prime`, `state-check`, and `checkpoint` skills, the standalone sovereignty guard
hooks, and the scripts they need. More capability bundles
(`heading-intel`, `heading-comms`, `heading-content`, `heading-crm`,
`heading-ops`) are planned.

## How updates work

The bundles carry no `version`, so every marketplace commit is a new version and
your installed plugins update automatically. Skills reach their bundled scripts
through `${CLAUDE_PLUGIN_ROOT}` (resolved to the plugin's install directory), and
a `SessionStart` hook resolves your data overlay at runtime through
`HEADING_OS_DATA`. No private data is ever bundled: with nothing configured, the
plugin resolves to the read-only demo tree, the same data-less default the
devcontainer uses.

## Plugin or full clone?

Both are first-class; pick by what you want.

- **Plugin install** gives you the sovereignty core inside your existing Claude
  Code, with zero setup. It is the fastest way to feel the engine and to layer
  its guards and session skills onto how you already work.
- **A full clone** ([DEPLOYMENT.md](DEPLOYMENT.md)) gives you the whole engine,
  your own private data overlay, the daemons, and every skill. It is the operator
  setup: the engine you run your company from.

The plugin is the front door; the clone is the house.

## Sovereignty is unchanged

The guard hooks ship in `heading-core`, and the engine's non-bypassable push-time
content scan and the `send_capable -> gated` invariant remain the backstops.
Outbound send stays human-gated everywhere. A plugin install adds capability, it
never adds a path around the human approve click. See
[SECURITY-MODEL.md](SECURITY-MODEL.md).

## For maintainers: publishing

The marketplace repo is a generated distribution artifact. Never hand-edit it.
To cut a new release, run the publisher from the engine, which builds the bundles
fresh, syncs them into a checkout of the marketplace repo, refreshes its README
and license, and pushes:

```
python scripts/dev/publish-marketplace.py --repo-dir ../heading-os-marketplace
```

One-time bootstrap of the marketplace checkout is documented at the top of
`scripts/dev/publish-marketplace.py`. The bundle manifest is
`config/plugin-bundles.yaml`, and the generator is `scripts/dev/build-plugins.py`;
both are described in [EXTENDING.md](EXTENDING.md).
