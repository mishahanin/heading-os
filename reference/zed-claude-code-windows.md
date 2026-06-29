# Zed on Windows for Claude Code (Subscription)

Operator reference for running Zed as the editor host for Claude Code on Windows, optimised for Anthropic subscription billing (Pro / Max), not API-key billing.

Last Updated: 2026-05-23
Last Verified: 2026-05-23

---

## 1. Bottom line up front

The single most important decision is **how Claude Code is invoked inside Zed**, because Anthropic split the billing on 2026-06-15:

| Invocation path | Billing pool | Recommended for subscription users |
|---|---|---|
| Zed Agent Panel via ACP (`agent: new external agent thread claude-acp`) | Agent SDK credit ($20 / $100 / $200 per month for Pro / Max 5x / Max 20x) | No |
| Zed integrated terminal running `claude` directly | Your full Pro / Max subscription quota | Yes |

If the goal is to maximise subscription value, run Claude Code as a CLI inside Zed's bottom-panel terminal. Use Zed as the editor shell around it, not as a wrapper agent. The Agent Panel is still useful for *non-Anthropic* agents (Gemini CLI, Codex, OpenCode), but it is the wrong path for a Pro / Max subscriber on Anthropic.

A secondary Windows-specific point: ACP authentication via `/login` has known failures on native Windows (immediate thread close, login-failed loop). That alone is a reason to prefer the terminal path until those land in stable, regardless of billing.

---

## 2. What Zed is, in one paragraph

Zed is a GPU-rendered, Rust-written multi-buffer editor from the team behind Atom and Tree-sitter. It targets sub-frame input latency, ships with native LSP, Tree-sitter syntax, Git integration, multiplayer presence, a built-in terminal panel, and an Agent Panel that speaks the Agent Client Protocol (ACP). Windows support is current-generation stable as of 2026; it requires a DirectX 11 capable GPU with hardware acceleration enabled. Settings are pure JSON, not a UI tree, which makes the editor scriptable and idempotent from version control.

---

## 3. Install on Windows

Three install paths, in order of recommendation:

1. **winget** (cleanest, auto-updates via winget):
   ```
   winget install -e --id ZedIndustries.Zed
   ```
2. **Direct installer** from `https://zed.dev/download` (stable) or `https://zed.dev/releases/preview` (preview channel).
3. **Build from source** per `https://zed.dev/docs/development/windows` (only if you need a custom build).

Binary on this machine after install: `C:\Users\<you>\AppData\Local\Programs\Zed\bin\Zed.exe`. CLI shim: `C:\Users\<you>\AppData\Local\Programs\Zed\bin\zed` (use `zed .` from a terminal to open a project).

### Hardware floor

- DirectX 11 capable GPU. Confirm via `dxdiag` → System → DirectX Version.
- Hardware acceleration enabled in Windows graphics settings.
- VMs and remote desktops with emulated adapters will perform poorly; this is a Zed-wide constraint, not a config issue.

### Known Windows quirks worth knowing

- ACP `/login` to Claude Code can fail on native Windows; WSL Ubuntu terminal works as a fallback if you ever do need ACP. Terminal-launched `claude` is unaffected.
- File watcher on Windows is reliable but heavier than on macOS; the `file_scan_exclusions` block below matters more here than on macOS.
- AMD APU + HVCI machines can fall to WARP (software render) under D3D11. If Zed feels sluggish on this class of hardware, that is the hint; document is in `reference_vivaldi_gpu_fix.md` (Vulkan flag fix applies conceptually).

---

## 4. Multiple Claude Code installs — pick one, pin the rest

This machine currently has three Claude Code binaries on PATH:

- `C:\Users\<you>\.local\bin\claude.exe`
- `C:\Users\<you>\AppData\Roaming\npm\claude.cmd` (npm install)
- `C:\Users\<you>\AppData\Local\Microsoft\WinGet\Packages\Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe\claude.exe` (winget)

This is a version-drift trap. When a session opens in Zed's terminal, whichever resolves first on PATH wins, and the others can drift weeks behind. Two clean options:

1. **Pin winget as the single canonical install**, uninstall the npm one (`npm uninstall -g @anthropic-ai/claude-code`) and remove `.local/bin/claude.exe`. Winget updates with the rest of the system.
2. **Pin the npm install** if you want bleeding-edge releases; remove the winget package and `.local/bin` copy.

Either way, end up with one `claude` on PATH. The mismatch problem is silent and only surfaces as "feature X works on my CLI but not in Zed" later.

---

## 5. Three deployment patterns, and which one to use

### Pattern A — Terminal-first (RECOMMENDED for subscription users)

Architecture: Zed is the editor and file-tree, the bottom panel is a terminal running `claude` against the project root. All Anthropic API traffic goes through Claude Code's own auth (your subscription).

Pros:
- Uses your full Pro / Max quota, not the Agent SDK credit cap.
- No Windows ACP login bug to fight.
- Same `~\.claude\` config, hooks, skills, plugins, MCP servers that work in any other terminal will work here unchanged.
- Lower context-switching tax than running Claude Code in an external Windows Terminal window.

Cons:
- No multi-buffer diff review surface for Claude's edits; you watch them land on disk and review via Zed's git gutter / `Ctrl+Shift+F`.
- Tool approvals come through the terminal prompt, not a UI button.

### Pattern B — Agent Panel via ACP (not recommended for Pro / Max)

Architecture: Zed runs the bundled `@zed-industries/claude-agent-acp` adapter, which in turn drives a vendored Claude Code CLI under the hood. Tool calls, diffs, and approvals come through Zed's Agent Panel UI.

Pros:
- Multi-file diff review surface with syntax highlighting.
- One-button approval per edit.
- Task list in sidebar.

Cons:
- After 2026-06-15, drains the Agent SDK credit pool ($20 / $100 / $200 per month), not your subscription. At realistic Claude Code rates these credits exhaust in a few hours of heavy refactoring.
- Windows ACP login flow is unreliable as of mid-2026.
- Some Claude Code slash commands are not yet exposed via the SDK (plan mode, history resume, checkpointing).

When to use it anyway: lighter, sporadic sessions where the credit will not be exhausted, AND on a non-Windows machine, AND when you want the diff-review UX.

### Pattern C — Hybrid (terminal + Zed's own LLM for inline edits)

Same as Pattern A, plus Zed's built-in edit predictions enabled on a *separate* model (Zed-hosted, Copilot, or local Ollama). Claude Code does the heavy lifting from the terminal; the in-editor predictor handles single-line completions while you type.

Risk to manage: if both AIs are mid-flight on the same file, the inline predictor can stomp Claude Code's writes or vice versa. Most workable rule: turn inline predictions off during long Claude Code runs, on otherwise.

For this workspace (CEO subscription, Windows, Claude Code as primary), Pattern A is the default and Pattern C is an upgrade path if inline completion turns out to be useful.

---

## 6. Settings file layout

Zed has two config layers, both pure JSON:

| Layer | Path | Purpose |
|---|---|---|
| User | `%APPDATA%\Zed\settings.json` (`C:\Users\<you>\AppData\Roaming\Zed\settings.json`) | Editor defaults across all projects |
| Project | `<repo>\.zed\settings.json` | Per-repo overrides; committed to git when shared |

Keymap is user-level only: `%APPDATA%\Zed\keymap.json`.

Themes user-installed: `%APPDATA%\Zed\themes\`.

Resolution order: project settings shallow-merge over user settings, key by key. Arrays are replaced, not concatenated, so for example overriding `file_scan_exclusions` in the project file drops the user-level entries unless you re-list them. The proposed configs in §10 account for this.

---

## 7. Proposed user-level settings.json

Drop this into `%APPDATA%\Zed\settings.json`. Comments are JSON-with-comments style which Zed accepts.

```jsonc
{
  // === Auth / AI posture ===
  // Disable Zed's own AI features at the global switch. Pattern A relies
  // on Claude Code in the terminal, not on Zed-hosted models or edit
  // predictions. Flip to false later if you want to layer in Pattern C.
  "disable_ai": true,

  // Belt-and-braces: even if disable_ai is flipped off, keep edit
  // predictions off by default until you opt in deliberately.
  "edit_predictions": {
    "provider": "none",
    "disabled_globs": [
      "**/.env*",
      "**/*.pem",
      "**/*.key",
      "**/*.cert",
      "**/*.crt",
      "**/.dev.vars",
      "**/secrets.yml",
      "**/secrets.yaml"
    ]
  },

  // === Telemetry ===
  // Off. Server-side billing metadata still flows when you use Zed-hosted
  // models, but you are not using them.
  "telemetry": {
    "diagnostics": false,
    "metrics": false
  },

  // === Keymap base ===
  // VSCode-flavoured bindings underneath, so muscle memory carries over.
  "base_keymap": "VSCode",

  // Vim layer off by default. Toggle with `workspace: toggle vim mode`
  // from the command palette if you ever want it.
  "vim_mode": false,

  // === Fonts ===
  // GT Standard is the 31C brand typeface but is a UI / display face,
  // not monospace; use Lilex (the .ZedMono alias) for the buffer and
  // terminal. Falls back gracefully if Lilex is not installed.
  "ui_font_family": "Segoe UI",
  "ui_font_size": 14,
  "buffer_font_family": ".ZedMono",
  "buffer_font_size": 14,
  "buffer_line_height": "comfortable",

  // === Editor behaviour ===
  // Auto-save on focus change is the safer default when Claude Code is
  // also writing to disk; you never end up with a stale dirty buffer in
  // Zed shadowing a fresh write from the CLI.
  "autosave": "on_focus_change",
  "format_on_save": "on",
  "restore_on_startup": "last_workspace",
  "soft_wrap": "editor_width",
  "show_wrap_guides": true,

  // === Project scanning ===
  // Windows file watcher is heavier than macOS; trim the noise. Re-list
  // the defaults because Zed replaces this array rather than merging.
  "file_scan_exclusions": [
    "**/.git",
    "**/.svn",
    "**/.hg",
    "**/.jj",
    "**/CVS",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/.classpath",
    "**/.settings",
    "**/node_modules",
    "**/.venv",
    "**/venv",
    "**/__pycache__",
    "**/.mypy_cache",
    "**/.pytest_cache",
    "**/.ruff_cache",
    "**/dist",
    "**/build",
    "**/target",
    "**/.next",
    "**/.turbo",
    "**/.sessions",
    "**/_build",
    "**/outputs/browser/firecrawl-cache"
  ],

  // === Terminal (the actual Claude Code host) ===
  "terminal": {
    // Default Windows shell. PowerShell 7 is preferred over the legacy
    // Windows PowerShell 5 because of UTF-8, performance, and ANSI
    // handling. Falls back to powershell.exe if pwsh.exe is not on PATH.
    "shell": {
      "with_arguments": {
        "program": "pwsh.exe",
        "args": ["-NoLogo"]
      }
    },
    // Use the same monospace as the buffer so Claude Code output and
    // editor windows feel like one surface.
    "font_family": ".ZedMono",
    "font_size": 13,
    "line_height": "comfortable",
    "copy_on_select": true,
    "blinking": "terminal_controlled",
    // EDITOR=zed --wait lets git, gh, hg etc. open commit messages
    // in Zed itself; --wait is what blocks the shell until you close
    // the tab.
    "env": {
      "EDITOR": "zed --wait",
      "GIT_EDITOR": "zed --wait"
    },
    // Claude Code looks for this to fire OS notifications when it
    // needs a tool approval; harmless if unused.
    "preferred_notif_channel": "terminal_bell"
  },

  // === Agent Panel ===
  // Even though Pattern A does not lean on the Agent Panel, leave the
  // ACP wiring sane so you can experiment without billing surprises.
  // If you do open a claude-acp thread, default to read-only Ask
  // profile so nothing writes without explicit confirmation.
  "agent": {
    "tool_permissions": {
      "default": "confirm"
    },
    "notify_when_agent_waiting": true,
    "play_sound_when_agent_done": false,
    "single_file_review": true
  },

  // If/when you do want ACP, this block pins the executable. Leave
  // the path null until you decide which install is canonical (see §4).
  // "agent_servers": {
  //   "claude-acp": {
  //     "type": "registry",
  //     "env": {
  //       "CLAUDE_CODE_EXECUTABLE": "C:\\Users\\<you>\\.local\\bin\\claude.exe"
  //     }
  //   }
  // },

  // === UI ===
  "theme": {
    "mode": "dark",
    "light": "One Light",
    "dark": "One Dark"
  },
  "tab_bar": { "show": true },
  "scrollbar": { "show": "auto" },
  "preview_tabs": { "enabled": true },
  "git": {
    "inline_blame": { "enabled": false }
  }
}
```

---

## 8. Proposed project-level `.zed/settings.json`

The repo already has a minimal file. Replace its contents with:

```jsonc
{
  // Inherits everything from user-level settings.json. Only declare
  // overrides specific to this workspace.

  "soft_wrap": "editor_width",
  "show_wrap_guides": true,

  // This workspace has heavy outputs and build trees; extend the
  // scanner exclusions on top of the user-level defaults. Remember:
  // Zed replaces this array, so re-list the user-level set if you
  // want both to apply.
  "file_scan_exclusions": [
    "**/.git",
    "**/.svn",
    "**/CVS",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/node_modules",
    "**/.venv",
    "**/__pycache__",
    "**/.ruff_cache",
    "**/.mypy_cache",
    "**/.pytest_cache",
    "**/_build",
    "**/_archive",
    "**/outputs/projects",
    "**/.sessions",
    "**/outputs/browser/firecrawl-cache",
    "**/datastore/**/*.pdf",
    "**/datastore/**/*.pptx",
    "**/datastore/**/*.docx",
    "**/datastore/**/*.xlsx"
  ],

  // Treat sensitive content as off-limits to inline AI even if edit
  // predictions get re-enabled later. Belt-and-braces.
  "edit_predictions": {
    "disabled_globs": [
      "**/outputs/projects/**",
      "**/.env*",
      "**/crm/contacts/**"
    ]
  }
}
```

Note: the `_secure/` vault was removed in Plan 5. Sensitive closed-project archives now live under `outputs/projects/` (gitignored); the exclusions above keep that path out of Zed's scan and inline-AI surfaces. Session sensitivity is otherwise governed by the fail-closed `SENSITIVE_MODE` flag, not an editor setting.

---

## 9. Proposed `keymap.json`

Drop into `%APPDATA%\Zed\keymap.json`:

```jsonc
[
  {
    "context": "Workspace",
    "bindings": {
      // Quick-open the terminal panel (matches VSCode `Ctrl+\``).
      // Zed already binds this; listed for documentation.
      "ctrl-`": "workspace::ToggleBottomDock",

      // Optional: jump straight into a new Claude Agent thread if you
      // ever choose Pattern B. Uses the Anthropic-recommended
      // shortcut form. Comment out if you never want the ACP route.
      "ctrl-alt-c": ["agent::NewExternalAgentThread", { "agent": "claude-acp" }]
    }
  },
  {
    "context": "Terminal",
    "bindings": {
      // VSCode-style clear; Zed defaults this to ctrl-shift-k.
      "ctrl-shift-l": "terminal::Clear"
    }
  }
]
```

---

## 10. Operating recipe (Pattern A, day-to-day)

1. `cd "c:\ai\claude-workspaces\heading-os"` then `zed .` to open the workspace.
2. Open the terminal panel: `Ctrl+\``.
3. `claude` to start a session in this project root. The CLI will pick up `CLAUDE.md`, `.claude/`, all your skills, hooks, MCP servers — same as any other terminal.
4. Edit files in Zed in parallel with Claude Code. Auto-save on focus change keeps Zed's view in sync when Claude writes to disk.
5. Use `Ctrl+Shift+F` for project-wide search to verify Claude's changes landed where you expected.
6. Use the git gutter and `Ctrl+Shift+G` to review diffs before commit.

For the bridge dashboard or any URL Claude prints, click in the terminal — Zed turns URLs into Ctrl+click links automatically.

---

## 11. Anti-patterns to avoid

- **Do not log into the Agent Panel with the same Anthropic account if you are on Pro / Max.** The ACP path bills against the Agent SDK credit; you will burn the $20 / $100 / $200 ceiling in a single session of real work.
- **Do not enable Zed's edit predictions on this workspace.** Skills, plans, CRM notes, and corporate documents contain sensitive content that should not flow to a model you are not auditing.
- **Do not point `CLAUDE_CODE_EXECUTABLE` at the npm path if you uninstalled npm Claude Code.** ENOENT on every ACP thread.
- **Do not enable `autosave: after_delay` with a short interval while Claude Code is writing.** Race conditions between Zed's writer and Claude Code's writer corrupt files.
- **Do not commit secrets via Zed's quick-commit even though it has a UI for it.** The repo's pre-commit secret scanner still applies; bypassing it is a `security.md` violation.
- **Do not pin `terminal.shell` to `cmd.exe`.** Claude Code's TUI degrades; PowerShell 7 or Git Bash are the only sensible choices.

---

## 12. Privacy posture summary

After applying §7:

- `telemetry.diagnostics: false` → no crash reports / minidumps leave the machine.
- `telemetry.metrics: false` → no file-extension / framework usage data leaves the machine.
- `disable_ai: true` → no Zed-hosted model usage; no prompts to Zed Cloud.
- `edit_predictions.provider: "none"` → no in-editor completions, even if you bring an API key.
- Claude Code itself still talks to Anthropic with your subscription credentials; that is the only LLM traffic.

Run `zed: open telemetry log` from the command palette periodically to confirm nothing is leaking; the file is empty under the settings above.

---

## 13. Verification checklist

After applying the configs:

1. `pwsh.exe -NoLogo -Command "echo OK"` from Zed's terminal panel → returns OK.
2. `which claude` (PowerShell: `Get-Command claude`) → exactly one path. If more than one, finish §4 first.
3. `claude --version` → matches the version of whichever single install you pinned.
4. Open the Agent Panel (status bar ✨ icon) → confirm "Claude Agent" exists as a provider but you do not log in.
5. Open the command palette → `zed: open telemetry log` → empty or near-empty.
6. Open any file outside the workspace → `Ctrl+\` to verify terminal panel opens fast.
7. `claude` in the terminal → starts a session, finds `CLAUDE.md`, lists skills.

---

## 14. Update cadence and signals

Recheck this document quarterly, or sooner if any of these break:

- Anthropic adjusts the Agent SDK credit values or the ACP-vs-CLI split (the $20/$100/$200 ceiling).
- Zed lands native Windows ACP login fix and the WSL fallback becomes unnecessary.
- Zed adds an officially supported "subscription-aware" Anthropic provider that does not route through ACP.
- Claude Code CLI changes its config file layout (`~\.claude\settings.json` schema bump).
- Windows 11 ships a feature update that breaks D3D11 hardware acceleration on this class of hardware.

When any of those happens, advance both `Last Updated` and `Last Verified` dates above and rev §5 accordingly.

---

## 15. Sources

Primary (zed.dev official):
- Windows install: `https://zed.dev/docs/windows`
- External agents / Claude Agent: `https://zed.dev/docs/ai/external-agents`
- Agent panel: `https://zed.dev/docs/ai/agent-panel`
- AI configuration: `https://zed.dev/docs/ai/configuration`
- Edit predictions: `https://zed.dev/docs/ai/edit-prediction`
- AI privacy and security: `https://zed.dev/docs/ai/privacy-and-security`
- Telemetry: `https://zed.dev/docs/telemetry`
- Terminal: `https://zed.dev/docs/terminal`
- Keymap: `https://zed.dev/docs/key-bindings`
- Vim mode: `https://zed.dev/docs/vim`
- All settings reference: `https://zed.dev/docs/reference/all-settings`

Critical context:
- Anthropic billing split announcement (Zed blog): `https://zed.dev/blog/anthropic-subscription-changes`
- Claude Code via ACP launch (Zed blog): `https://zed.dev/blog/claude-code-via-acp`
- Windows ACP auth bug (GitHub issue 39014): `https://github.com/zed-industries/zed/issues/39014`
- Subscription login discussion (GitHub discussion 33333): `https://github.com/zed-industries/zed/discussions/33333`

Third-party guide:
- Claude Code Guides — Zed workflow (2026): `https://claudecodeguides.com/claude-code-for-zed-editor-workflow-guide/`
