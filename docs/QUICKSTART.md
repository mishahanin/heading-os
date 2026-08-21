<!-- version: 2.5.0 | last-updated: 2026-07-12 -->
# HEADING OS — Quickstart

The one-page version. For the full zero-to-running walk-through, see
**[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## Try it in 60 seconds (no data, no keys)

Want to see the engine run before installing anything? Open the repository in a
devcontainer: in VS Code, "Reopen in Container", or launch a **GitHub Codespace**
from the repo page.

The container defaults to read-only **demo mode**. It sets `HEADING_OS_DATA` to the
bundled `examples/` tree, installs `uv`, and runs a core `uv sync`. On first build
it prints demo output from `scripts/crm-health.py`, the engine behind `/crm radar`.
No `.env`, no API key, no private data repository. Nothing can write, because demo
mode refuses writes by design.

To run it again yourself inside the container:

```bash
uv run python scripts/crm-health.py
```

When you are ready for a real workspace, follow **Install (short form)** below. Details
live in [`.devcontainer/README.md`](../.devcontainer/README.md).

---

## Install as a Claude Code plugin

Want the sovereignty core inside your existing Claude Code, with no clone? Add the
marketplace and install the bundle:

```
/plugin marketplace add mishahanin/heading-os-marketplace
/plugin install heading-core@heading-os-marketplace
```

`heading-core` carries the `prime`, `state-check`, and `checkpoint` skills plus the
sovereignty guard hooks. For what each bundle holds, how updates work, and when to
prefer a plugin over a full clone, see **[PLUGINS.md](PLUGINS.md)**.

---

## Install (short form)

Once you have WSL2/Ubuntu (Windows) or a Unix shell, plus `git`, `gh`, `uv`, Node, and
the Claude CLI:

```bash
mkdir -p ~/ai/claude-workspaces && cd ~/ai/claude-workspaces

# 1. Clone the engine
git clone https://github.com/<org>/heading-os.git .heading-os
cd .heading-os

# 2. Install dependencies (Python managed by uv) + arm the secret gate
uv sync                        # core only - small, fast; arm integrations later
# uv sync --all-extras         # OR: the full integration surface (operator parity)
pre-commit install

# 3. Create your own private data repository (one command)
uv run python scripts/create-data-repo.py

# 4. Wire secrets
cp .env.example .env        # fill in what you use

# 5. Verify, then start
uv run python scripts/workspace-health.py
claude        # then /prime
```

Core `uv sync` installs only the light always-on set. Each integration (email,
Telegram, browser automation, document generation, ...) lives in an optional
extra you arm on demand: `uv sync --extra <name>`. A script that needs a dormant
extra tells you the exact command to run. Want everything at once? Use
`uv sync --all-extras`. See [DEPLOYMENT.md](DEPLOYMENT.md) for the extras ladder.

Full prerequisites, platform setup, authentication, plugins, and troubleshooting are
in [DEPLOYMENT.md](DEPLOYMENT.md).

---

## First session

| Step | Command |
|---|---|
| Start a session | `claude` (trust the engine folder) |
| Load context | `/prime` |
| See what to do next | `/next` |
| Back up your data | `/backup` |

A clean `/prime` means everything works. A fresh data overlay looks sparse — that is
expected until you create or import records.

---

## Everyday essentials

| Command | Does |
|---|---|
| `/prime` | Load context, surface alerts, reorient |
| `/dashboard` | Morning brief — inbox, calendar, pipeline in one view |
| `/osint <target>` | Deep research on a company, person, or market |
| `/meeting-prep <name>` | Dossier + talking points for a meeting |
| `/email-intel` | Triage your inbox (drafts only; never auto-sends) |
| `/crm` | Add, log, find contacts; check who's overdue |
| `/backup` | Commit & push your data overlay |
| `/sync` | Pull engine updates + refresh shared content |

You can also describe what you want in plain language — "research this company",
"draft a reply", "who haven't I followed up with". It routes to the right
capability automatically.

On anything non-trivial the engine first plays back its understanding — objective,
scope, assumptions — and waits for your go before acting. A misread is therefore
caught before it becomes a wrong deliverable. Prefix a message with `!` to skip that and act
immediately; type `/align` to force a round of clarifying questions first. The full
three-phase flow, the escape valves, and the `/align` · `/devil` · `/burst`
escalations are documented in the
[rules reference](RULES-REFERENCE.md#prompt-refinement-in-depth).

> **Outbound is always human-gated.** Email and messages are drafted and shown to you
> first. Nothing sends on its own.

---

## Optional: AI models

A few skills use models beyond Claude, all optional:

- `/recall` and the memory index run on a local `bge-m3` embedder via **Ollama** (no
  key, no cost).
- `/council` asks **Gemini, Grok, and Kimi** in parallel for a second opinion.
- `/deep-research-advance` adds **Perplexity + Kimi** for deep web research.

Install steps, where to get each key, and how to verify are in
**[MODELS-SETUP.md](MODELS-SETUP.md)**.

To connect email, Telegram, Google, and the OSINT keys, see
**[INTEGRATIONS-SETUP.md](INTEGRATIONS-SETUP.md)**. To make the workspace speak in
your own voice and identity, see **[MAKE-IT-YOURS.md](MAKE-IT-YOURS.md)** (`/setup-wizard`).

---

*HEADING OS · Quickstart · see [DEPLOYMENT.md](DEPLOYMENT.md) for the full guide and
the in-workspace `/prime` for live orientation.*
