<!-- version: 2.2.0 | last-updated: 2026-07-01 -->
# HEADING OS — Quickstart

The one-page version. For the full zero-to-running walk-through, see
**[DEPLOYMENT.md](DEPLOYMENT.md)**.

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
uv sync
pre-commit install

# 3. Create your own private data repository (one command)
uv run python scripts/create-data-repo.py

# 4. Wire secrets
cp .env.example .env        # fill in what you use

# 5. Verify, then start
uv run python scripts/workspace-health.py
claude        # then /prime
```

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

You can also just describe what you want in plain language — "research this company",
"draft a reply", "who haven't I followed up with" — and it routes to the right
capability automatically.

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
