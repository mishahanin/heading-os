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

That installs `heading-core`, the sovereignty and session bundle: the `prime`,
`state-check`, and `checkpoint` skills, the standalone sovereignty guard hooks,
and the scripts they need.

The marketplace also ships four curated capability bundles, each installed the
same way (`/plugin install <bundle>@heading-os-marketplace`):

- `heading-intel` - parse a document with citations (docparse) and build a
  web-sourced market brief (market-brief).
- `heading-comms` - translate between English and Russian (translate).
- `heading-content` - draft LinkedIn posts and series plus image prompts
  (linkedin-post, linkedin-series, image-prompt).
- `heading-ops` - draft an implementation plan (create-plan), reason through a
  hard decision (deep-think), and run a structural editorial pass
  (editorial-review).

Bundles ship only skills that run without your private data, credentials, or
daemons. Skills that need those stay in the full engine: the `heading-crm`
skills (crm, viraid, google-contacts) need a private CRM overlay or Google
OAuth, and skills like email-intel, telegram, osint, and council need Exchange,
a session, or third-party API keys. To run those, clone the engine
([DEPLOYMENT.md](DEPLOYMENT.md)).

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

### Automatic publishing

You do not have to run the publisher by hand. The `publish-marketplace` GitHub
Action republishes the marketplace whenever a push to `main` touches a bundle
input (the manifest, the build or publish scripts, or any skill or hook a bundle
can carry), and on manual `workflow_dispatch`. It is a no-op when nothing
changed.

The Action pushes to a different repository than the one it runs in, so the
default `GITHUB_TOKEN` cannot authorize it. One-time setup: create a fine-grained
Personal Access Token scoped write to ONLY `mishahanin/heading-os-marketplace`
(Contents: read and write), and add it to the engine repo's secrets as
`MARKETPLACE_PUBLISH_TOKEN` (Settings, then Secrets and variables, then Actions).
Until the secret exists the Action fails on its first step with a clear message
rather than publishing a broken state. The manual publisher above stays available
regardless.
