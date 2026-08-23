# Install as a Claude Code plugin

HEADING OS ships an installable plugin marketplace, so you can try the engine's
core inside Claude Code with two commands, no clone and no toolchain.

Last Updated: 2026-08-16

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

That installs `heading-core`, the sovereignty and session bundle. It carries the
`prime`, `state-check`, and `checkpoint` skills, the standalone sovereignty guard
hooks, the session checkpoint hooks (see below), and the scripts they need.

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
daemons. Skills that need those stay in the full engine. There is no CRM
bundle to install. The crm, viraid and google-contacts skills need a private
CRM overlay or Google OAuth. So `heading-crm` is a reserved name in
`config/plugin-bundles.yaml` with no skills in it, and the build skips it.
Skills like email-intel, telegram, osint, and council need Exchange,
a session, or third-party API keys. To run those, clone the engine
([DEPLOYMENT.md](DEPLOYMENT.md)).

## The checkpoint system

`heading-core` also carries the session checkpoint. It writes down what the next
session needs before a long session fills its context. A handoff holds the
objective, the decisions, the files touched, the next steps, and a continuation
prompt. The work then survives the context wall.

Four hooks and one skill. Three of the hooks wire themselves the moment the
plugin is installed:

| Piece | Event | What it does |
|---|---|---|
| `/checkpoint [note]` | you type it | Writes a handoff now. No compact, no clear. |
| `checkpoint-save.py` | PostCompact | Saves a handoff from the compaction summary, redacted. |
| `checkpoint-inject.py` | SessionStart | Puts this session's handoff into the first turn of the resumed session. |
| `checkpoint-offer.py` | Stop | Offers the checkpoint when context crosses your threshold. |
| `checkpoint-statusline.py` | statusLine | Reads context usage and drives the offer. **Wire this one yourself.** |

Everything below your project root is keyed by session id, so several sessions
open on one repository never overwrite each other's handoffs.

### Wiring the status line

Claude Code exposes context-window usage **only** to a `statusLine`, and a plugin
manifest has no `statusLine` key, so this one line is yours to add. In your
project's `.claude/settings.json` (or `~/.claude/settings.json` for every repo):

```json
"statusLine": {
  "type": "command",
  "command": "python3 \"$CLAUDE_PLUGIN_ROOT/hooks/checkpoint-statusline.py\""
}
```

If `$CLAUDE_PLUGIN_ROOT` is not set in your shell, use the installed path under
`~/.claude/plugins/cache/`. Without the status line you still get `/checkpoint`
and the compact-and-resume flow, but no proactive offer.

### Thresholds and auto mode

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_HANDOFF_SOFT_THRESHOLD` | 25 | % used where the offer first appears |
| `CLAUDE_HANDOFF_HARD_THRESHOLD` | 30 | % used where it stops offering "keep going" |
| `CLAUDE_HANDOFF_REMIND_STEP` | 5 | how far context must move before it asks again |
| `CLAUDE_HANDOFF_AUTO` | off | save silently and resume, with no prompt |

These two thresholds are the workspace default, not the last word. Since v0.12 a
single session overrides the pair with `/compact-at N` and keeps it for that
window only; see the section below. A session that sets nothing keeps the
environment pair untouched.

Auto mode is off until you turn it on. With it on, crossing the threshold saves
the checkpoint without asking, and the session carries on. After a compaction the
SessionStart hook tells the assistant to continue on its own. Keep your
compaction point above the soft threshold, so the checkpoint lands first.

No hook can start a compaction from inside Claude Code, and none tries. The Stop
hook reaches the same end from outside. Above the hard threshold, once the
checkpoint is on disk, it submits the literal text `/compact` to the terminal
hosting the session. It reaches that terminal through HERDR, a terminal manager.
Without HERDR hosting your session none of that happens. Claude Code's own
auto-compact then frees the context instead.

The environment variable is the workspace default, decided before launch. The
decision you actually make is a running one, taken part-way into a long piece of
work, and it belongs to one window. So the switch also exists per session:

```
python scripts/checkpoint-paths.py --auto on      # stop asking, this session
python scripts/checkpoint-paths.py --auto off     # ask again, this session
python scripts/checkpoint-paths.py --auto status  # report, change nothing
```

The threshold offer lists this switch as one of its options, so you can pick it
from the prompt instead of remembering it. It overrides the environment default
in both directions. It needs no cleanup either. The state that holds it is keyed
by session, and the pruner removes it with the session.

The same argument applies to the threshold itself, so since 2026-08-21 that is a
per-session switch too. The two variables in the table above are the workspace
default. A session that sets its own number uses that number instead, from the
next pause onward, with no restart:

```
python scripts/checkpoint-paths.py --compact-at 35      # this session compacts at 35%
python scripts/checkpoint-paths.py --compact-at status  # report, change nothing
python scripts/checkpoint-paths.py --compact-at off     # back to the environment default
```

`/compact-at 35` is the same thing as a slash command. The soft reminder is
always 5 points below the hard threshold rather than a second setting, so one
number moves the pair. The accepted range is 15 to 90. Below 15 the derived soft
threshold lands under the always-loaded context floor, and the trigger cascades.
Above 90 there is no window left in which to write the handoff. The command
refuses a number at or below what the session has already used, because that
number would fire at the very next pause.

An accepted number also turns `unattended` on, and that turns `auto` on with it.
So one command is enough, and the hook compacts at your number instead of asking.
This changed on 2026-08-22. Before it, the switch moved the threshold and nothing
else, which left the hook asking at a number nobody acted on. A refusal raises
nothing, `status` and `off` raise nothing, and a stretch already running is left
untouched. Only you lower the mode, with `/unattended off`.

Both slash commands, `/unattended` and `/compact-at`, ship inside the
`heading-core` bundle. They did not until 2026-08-21. The generator had no field
for `.claude/commands/`, so the plugin carried the `/checkpoint` skill and neither
command that skill tells you to run. Add a command to a bundle through the
`commands:` list in `config/plugin-bundles.yaml`, beside `skills:` and `hooks:`.

**[Mahmoud Maatuq](https://github.com/mmaatuq)** contributed the proactive offer
and the hands-off auto mode, and found the concurrent-session collision that the
per-session keying fixes. Thank you.

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
To cut a new release, run the publisher from the engine. It builds the bundles
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
input, and on manual `workflow_dispatch`. A bundle input is the manifest, the
build or publish scripts, or any skill or hook a bundle can carry. It is a no-op
when nothing changed.

The Action pushes to a different repository than the one it runs in, so the
default `GITHUB_TOKEN` cannot authorize it. One-time setup: create a fine-grained
Personal Access Token scoped write to ONLY `mishahanin/heading-os-marketplace`
(Contents: read and write). Add it to the engine repo's secrets as
`MARKETPLACE_PUBLISH_TOKEN`, under Settings, then Secrets and variables, then Actions.
Until the secret exists the Action fails on its first step with a clear message
rather than publishing a broken state. The manual publisher above stays available
regardless.
