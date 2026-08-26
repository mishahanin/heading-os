<!-- version: 1.5.0 | last-updated: 2026-08-20 -->
# HEADING OS — Deployment & Setup

The complete, zero-to-running guide for standing up a HEADING OS workspace from a
clean machine. Every command is generalized — substitute the `<placeholders>` for
your own values. Follow it top to bottom the first time; use the troubleshooting
and command references at the end on return visits.

> Audience: anyone deploying HEADING OS — a solo operator running their own single
> workspace, or someone activating a managed workspace handed to them by an
> administrator. Both paths are covered; differences are called out inline.

---

## 1. Architecture

HEADING OS runs inside Linux (WSL2 Ubuntu on Windows, or native on macOS/Linux) as
**sibling git repositories** under one parent directory. The engine finds its data
sibling automatically because they sit side by side.

```
~/ai/claude-workspaces/
├── .heading-os/            ← engine    (shared code; read-only when managed)
├── .heading-os-data/       ← your data (CRM, knowledge, outputs, context — writable)
└── .heading-os-corporate/  ← corporate (brand, templates, shared content — optional)
```

| Repo | What it holds | Your relationship to it |
|---|---|---|
| **Engine** (`<org>/heading-os`) | All code: skills, scripts, rules, hooks. Zero personal data. Eventually public. | Pull only when managed. The single shared brain everyone runs. |
| **Data overlay** (`<org>/heading-os-data[-<slug>]`) | Your CRM, knowledge, outputs, context, threads. Private. | Work and commit here. This is what backups push. |
| **Corporate** (`<org>/heading-os-corporate`) | Brand assets, templates, shared reference. | Read in place. Populated by `sync-corporate.py`. Optional for solo deploys. |

**Why the split.** The engine is shared (and eventually public) code, so personal
data must never land in it. The data overlay is private and owned by you. A
mechanical guard refuses to commit data-class files into the engine, so the
separation cannot drift by accident.

**Two deployment shapes:**

- **Solo workspace** — you run one workspace for yourself. No identity file needed;
  the engine defaults to a single-user ("master") workspace. Your data overlay is
  `.heading-os-data`.
- **Managed workspace** — an administrator provisions a workspace for you. You get
  an activation packet (identity + secrets) and a private data repo named
  `heading-os-data-<your-slug>`. The engine is read-only for you (you pull updates;
  you never push it).

---

## 2. Placeholders & conventions

| Placeholder | Meaning / example |
|---|---|
| `<org>` | GitHub org or owner that hosts the repos |
| `<slug>` | your workspace slug, e.g. `firstname-lastname` (managed deploys only) |
| `<engine>` | `~/ai/claude-workspaces/.heading-os` — run most commands from here |
| `<old-workspace>` | path to a prior workspace you are migrating from, e.g. `/mnt/c/path/to/old` |

**Know which shell you are in.** A prompt of `PS C:\…>` is Windows PowerShell —
Linux commands fail there. A prompt of `name@host:~$` is Ubuntu — that is where
HEADING OS lives. Enter Ubuntu with `wsl -d Ubuntu`.

**Use `uv`, never system `pip`.** Recent Ubuntu ships only `python3` (no `pip`); the
project environment is managed by `uv`. Install dependencies with `uv sync` and run
every script as `uv run python scripts/…`. A bare `python` will report "command not
found" — that is expected, not an error.

---

## 3. Prerequisites

- Windows 11 (with WSL2), macOS 13+, or a Linux distribution.
- A GitHub account with access to the repos you will clone.
- An Anthropic account — a Claude subscription is preferred, or an API key.
- Roughly 60–90 minutes for a first install.

---

## 4. Platform setup

### 4.0 Fastest path: open in a devcontainer (demo mode)

For a zero-setup, read-only look before you install anything, open the repository in
a devcontainer. Use the VS Code "Reopen in Container" command, or a **GitHub
Codespace** from the repo page. The container defaults to demo mode. It sets `HEADING_OS_DATA` to the bundled
`examples/` tree, installs `uv`, runs a core `uv sync`, and prints demo output from
`scripts/crm-health.py` on first build. No `.env`, no API key, no private data
repository, and nothing can write (demo mode refuses writes by design).

This is a demo, not a full workspace. For a real deployment, continue with 4.1 onward
(WSL2 or a Unix shell, the toolchain, authentication, and your own private data repo).
The container details are in [`.devcontainer/README.md`](https://github.com/mishahanin/heading-os/blob/main/.devcontainer/README.md).

### 4.1 Windows only — WSL2 + Ubuntu

All work runs inside Linux. On Windows that means WSL2. In an **Administrator**
PowerShell:

```powershell
wsl --install        # reboot when prompted; create a Linux user + password on first launch
```

If WSL is already present, `wsl --install` reports the distribution exists — that is
fine, skip it. Open Ubuntu with `wsl -d Ubuntu`.

**Keep all repos on the Linux filesystem (`~/…`), not `/mnt/c`.** The Windows mount
is an order of magnitude slower for git and Python. It has also surprised more than
one operator with permission quirks. Users on macOS and Linux skip this section.

### 4.2 Toolchain (inside Ubuntu / macOS / Linux)

```bash
# system packages
sudo apt update && sudo apt install -y build-essential curl git ca-certificates gh

# Node via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
nvm install --lts

# uv (Python manager — installs Python for you)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version

# Claude CLI
npm install -g @anthropic-ai/claude-code
which claude    # must resolve under ~/.nvm/... (Linux build), not /mnt/c/...
```

> **Verify the Linux build.** If `which node` or `which claude` points at `/mnt/c/…`,
> your shell is using the Windows install. Re-source nvm
> (`. "$NVM_DIR/nvm.sh"`) and confirm both resolve under `~/.nvm/…` before
> continuing. A Windows-resolved `claude` will not see your Linux workspace.

### 4.3 Authenticate inside Linux

Windows logins do **not** carry into WSL. Authenticate again in Ubuntu:

```bash
gh auth login     # GitHub.com → HTTPS → Yes → Login with a web browser
claude            # pick a theme, then "Claude account with subscription"
```

> **WSL has no default browser.** Both logins fail to auto-open one and instead
> print a URL + a one-time code. Open the URL in your Windows browser manually,
> paste the code, approve. On the Claude trust prompt, **do not trust a system
> folder** such as `/mnt/c/WINDOWS/system32`. If a launch landed you there, exit,
> `cd` to the engine directory, relaunch `claude`, and trust that.

---

## 5. Clone the repositories

```bash
mkdir -p ~/ai/claude-workspaces && cd ~/ai/claude-workspaces

git clone https://github.com/<org>/heading-os.git .heading-os
# managed deploy: your slug-named data repo. solo deploy: a plain data repo you own.
git clone https://github.com/<org>/heading-os-data-<slug>.git .heading-os-data
# optional — corporate content (skip for a solo deploy without one):
git clone https://github.com/<org>/heading-os-corporate.git .heading-os-corporate
```

> **`repository not found` on clone.** GitHub returns this for both a wrong name and
> a private repo you cannot see. Confirm the exact name and your access:
> `gh repo list <org> --limit 200 | grep heading-os`. Watch for look-alike
> characters (an `o` vs a zero). The data repo name uses your **workspace** slug,
> which may differ from your GitHub handle. If the list is empty, your account does
> not yet have access — ask the administrator to grant it.

---

## 6. Activate — identity & secrets

The engine reads two gitignored files at its root: `.workspace-identity.json` and
`.env`.

### Solo deploy

No identity file is required — the engine defaults to a single-user workspace. Create
`.env` from the template and fill in the keys you have. Then set your operator
identity, one file that names who runs the instance (see
[MAKE-IT-YOURS](MAKE-IT-YOURS.html) § 3.1):

```bash
cd <engine>
cp .env.example .env
# edit .env: set the keys you have (Anthropic, and any integrations you use)
cp scripts/operator.example.yaml <data-root>/config/operator.yaml
# edit operator.yaml: name / slug / github_org / voice_reference / email
```

### Managed deploy — option A: packet from the administrator

Drop the two files the administrator sends you into the engine root, then fill the
blank secret values in `.env`.

The identity file is small and non-secret. It MUST have this exact shape:

```json
{
  "role": "exec",
  "slug": "<slug>",
  "type": "exec-workspace",
  "org": "<org>"
}
```

> **The `type` value is load-bearing.** The engine detects a managed workspace by
> matching `"type": "exec-workspace"` exactly. An older or hand-typed value like
> `"exec"` silently breaks detection — backups then target the wrong repo. If yours
> reads anything other than `exec-workspace`, fix it before first backup.

**Confirm both files landed. A missing one is silent.** Both names start with a
dot, and a copy through a Windows file dialog can drop it or append `.txt`:

```bash
cd <engine>
ls -la .workspace-identity.json .env
```

> A MISSING identity file is worse than a wrong one. The engine does not stop. It
> falls back to a single-user CEO workspace and keeps running. `/prime` then
> reports "No workspace-identity.json found — treating as CEO workspace".
> `sync-corporate.py` becomes a no-op. Your first backup aims at the read-only
> engine. Fix the file before you continue. Then restart `claude`, because the
> engine caches the identity for the life of the process.

### Managed deploy — option B: migrate from a prior workspace

If you already ran a prior workspace, both files usually exist there and copy across:

```bash
cp <old-workspace>/.env                    <engine>/.env
cp <old-workspace>/.workspace-identity.json <engine>/.workspace-identity.json
```

Compare `.env` against the engine's `.env.example` (the list of expected keys) and
copy over what you have. Core integrations carry over directly; optional keys can be
added later — those features stay dark until set.

> **Secrets never get committed.** Both files are gitignored. Never paste live
> credentials into chat, tickets, or tracked files. To read a value locally:
> `grep KEY <engine>/.env`.

### AI model integrations (optional)

Claude is wired during the steps above. Three further integrations are optional and
light up specific capabilities; the engine runs without any of them:

| Capability | Model | Credential | Notes |
|---|---|---|---|
| `/recall`, memory index | `bge-m3` | none | Local embeddings via Ollama. No key, no cost. |
| `/council` | Gemini, Grok, Kimi | `GEMINI_API_KEY`, `XAI_API_KEY`, `OLLAMA_API_KEY` | Three independent second-opinion voices. |
| `/deep-research-advance` | Kimi + Perplexity | `OLLAMA_API_KEY`, `PERPLEXITY_API_KEY` | Deep public-web research. |

Recall and Kimi both need a local **Ollama** runtime; Gemini and Grok are pure cloud
APIs. The complete walk-through (install Ollama, pull `bge-m3`, where to get each
key, configure `.env`, and verify each path) is in **[MODELS-SETUP.md](MODELS-SETUP.html)**.

> **These models are third-party clouds** (except the local `bge-m3` embedder).
> Never send private data through `/council`; `/deep-research-advance` is
> hard-scoped to public topics. Details in MODELS-SETUP.

### Other integrations & personalization (optional)

Two more guides cover the rest:

- **[INTEGRATIONS-SETUP.md](INTEGRATIONS-SETUP.html)** — connect Exchange email,
  your Telegram, Google contacts, and the OSINT / web-research APIs (where to get
  each key, how to authenticate, how to verify).
- **[MAKE-IT-YOURS.md](MAKE-IT-YOURS.html)** — personalize the clone. Run
  `/setup-wizard` to generate your voice, business, and personal docs, set your
  identity, and adapt the house terminology to your own.

---

## 7. Install dependencies & arm the secret gate

```bash
cd <engine>
uv sync               # core deps only - the light always-on set
pre-commit install    # commit-time secret scanner (run once per fresh clone)
```

> **The commit hook is local and per-clone.** `pre-commit install` writes into
> `.git/hooks`, which git does not clone. Run it once on every fresh clone. The
> authoritative, unbypassable secret scan runs again at push time regardless.

### Two install paths: core vs. all extras (F-7.1)

Heavy integrations live in **optional-dependency extras**, so the default
`uv sync` stays small. Those are Exchange email, Telegram, browser automation,
document and deck generation, media, YouTube, the dashboard daemon, the Google
and GenAI stack, observability, research scraping.

- **Adopter (recommended start):** `uv sync` installs core only. Arm a capability
  when you first need it: `uv sync --extra email`, `uv sync --extra browser`,
  `uv sync --extra documents`, and so on. Any script that needs a dormant extra
  prints the exact `uv sync --extra <name>` command to run.
- **Operator / full fleet:** `uv sync --all-extras` installs the entire
  integration surface at once. This is what the CEO instance, CI, and the
  provisioning path use, so their environment is unchanged by the split.

The extras and their marker modules: `email`, `telegram`, `browser`, `documents`,
`media`, `dashboard`, `ai-extra`, `observability`, `research` (plus a meta `all`).
Check which are live on this install with
`uv run python scripts/workspace-health.py --section extras` - it prints an
**Armed / Dormant** ladder, one line per extra.

---

## 8. Install plugins (machine-local; not cloned)

Plugins are activated in the engine's `.claude/settings.json`, but the marketplace
must be added and each plugin installed manually — activation only enables an
already-installed plugin.

```bash
# add the marketplace first (one time)
claude plugin marketplace add anthropics/claude-plugins-official

# then install each enabled plugin by name, one command per line
claude plugin install superpowers@claude-plugins-official --scope project
claude plugin install skill-creator@claude-plugins-official --scope project
claude plugin install claude-md-management@claude-plugins-official --scope project
claude plugin install frontend-design@claude-plugins-official --scope project
claude plugin install mattpocock-skills@claude-plugins-official --scope project
claude plugin install code-review@claude-plugins-official --scope project
claude plugin install code-simplifier@claude-plugins-official --scope project
```

The authoritative list is `enabledPlugins` in the engine's `.claude/settings.json`.
Install the entries set to `true`; the ones set to `false` are deliberately off.

> **Paste one line at a time.** Do not fold these into a `for` loop with `\`
> continuations. Windows Terminal can insert a blank line inside a pasted block.
> That blank line breaks the continuation. Bash then prints
> `syntax error near unexpected token`, and one install runs with an empty plugin
> name.

> **Do not use `@latest`.** `plugin@latest` makes Claude look for a *marketplace*
> named "latest" and fails with "not found in marketplace latest". The form is
> always `<plugin>@<marketplace>` — here `@claude-plugins-official`. List registered
> marketplaces with `claude plugin marketplace list`.

---

## 9. First run

```bash
cd <engine>
uv run python scripts/workspace-health.py          # should read mostly OK
uv run python -c "from scripts.utils.paths import get_data_root; print(get_data_root())"
```

The second line must print your `…/.heading-os-data` path. If it prints a path under
`…/examples`, the engine cannot find the data sibling — set
`HEADING_OS_DATA=/abs/path/to/.heading-os-data` in `.env`. Then start a session:

```bash
claude        # trust THIS folder (the engine), then:
/prime
```

A clean `/prime` means everything works. On a freshly provisioned data overlay it
will look sparse — that is expected until you bring records in.

---

## 10. Day-to-day

Launch `claude` from the engine, run `/prime` to load context. Natural language works
as well as slash commands. The essentials:

| Command | Does |
|---|---|
| `/prime` | load context, surface alerts, reorient |
| `/next` | recommend the logical next action |
| `/backup` | commit & push your data overlay |
| `/sync` | pull engine updates + refresh corporate content |

To back up at any time:

```bash
uv run python scripts/push-all.py            # commit + push your data overlay
uv run python scripts/push-all.py --dry-run  # preview; change nothing
```

`push-all.py` detects your workspace type. On a managed workspace it pushes the data
overlay only and never touches the read-only engine. On a solo workspace it pushes
your repo(s) directly, data overlay first. A repository it cannot push right now, say
because it is on a feature branch, is committed locally and reported as skipped. The
run does not abort, so a refusal about one repository never costs you the backup of
the other.

> **Outbound is always human-gated.** Email and messages are drafted and shown to you
> first. Nothing sends to the outside world autonomously.

---

## 11. Keeping current

```bash
cd <engine>
git pull --ff-only origin main                 # update engine code (safe; pull-only)
uv run python scripts/sync-corporate.py        # refresh corporate content in place
```

`/sync` wraps both. Run it whenever you want the latest shared code and content.

---

## 12. Migrating records from a prior workspace

If you are moving from an older workspace, bring contacts, knowledge, threads, and
context across. The import is **non-destructive**: it never deletes and never
overwrites an existing file.

```bash
cd <engine>
git pull --ff-only origin main                 # ensure the current import tool

# dry-run first — writes nothing
uv run python scripts/import-legacy-records.py --from "<old-workspace>" --dry-run

# then import for real, and back up
uv run python scripts/import-legacy-records.py --from "<old-workspace>"
uv run python scripts/push-all.py
```

Point `--from` at the directory that directly contains `crm/contacts`, `knowledge`,
and `context`. Confirm every `->` destination in the dry-run lands inside your
`…/.heading-os-data/…` overlay, never inside the engine. If a destination points at
the engine, stop — see the troubleshooting table.

---

## 13. Troubleshooting reference

| Symptom | Cause & fix |
|---|---|
| `'&&' is not a valid statement separator` / Linux commands error | You are in PowerShell. Enter Ubuntu: `wsl -d Ubuntu`. Run all setup there. |
| `wsl --install` → "distribution already exists" | WSL is already installed. Skip it; just `wsl -d Ubuntu`. |
| `cd~: command not found` | `cd` needs a space: `cd ~`. |
| `python: command not found` | Ubuntu ships only `python3`, no pip. Use `uv run python …` and `uv sync`. Never system pip. |
| Tests fail with `File is not a zip file`, or a logo renders as a text file | The clone was made without git-lfs, so LFS-tracked binaries are pointer files. `sudo apt install git-lfs && git lfs install && git lfs pull` in the engine clone. |
| `uv: command not found` | The installer put `uv` in `~/.local/bin`, not on this shell's PATH. Run `source $HOME/.local/bin/env` (or reopen the shell); confirm with `which uv`. |
| `uv sync` picks the wrong Python, or `Python 3.11 not found` | Pin the interpreter: `uv python install 3.11`, then `uv sync`. Verify with `uv run python --version`. |
| The commit secret gate never fires (a bad commit goes through) | The `pre-commit install` step in §7 was skipped. Run `uv run pre-commit install` in the engine clone; verify with `uv run python scripts/install-hooks.py --check`. |
| `which claude` / `node` shows `/mnt/c/…` | Shell is using the Windows install. Re-source nvm (`. "$NVM_DIR/nvm.sh"`); confirm it resolves under `~/.nvm/…`. |
| Browser won't open during `gh` / `claude` login | WSL has no default browser. Copy the URL + one-time code into your Windows browser manually. |
| Claude trust prompt shows a system path | Don't trust `/mnt/c/WINDOWS/system32`. Exit, `cd <engine>`, relaunch `claude`, trust that. |
| `git clone` → "repository not found" | Wrong name or no access. `gh repo list <org> | grep heading-os`; watch for look-alike characters; confirm access with the administrator. |
| Plugin install → "not found in marketplace latest" | `@latest` is wrong. Add `anthropics/claude-plugins-official`, then install `<plugin>@claude-plugins-official`. |
| Plugin install → `Plugin "" not found in marketplace` | The plugin name reached the command empty, because a pasted multi-line block broke. Run that single install line on its own. |
| `bash: syntax error near unexpected token` right after a paste | The terminal inserted a blank line inside a `\` line continuation, so bash read two broken commands. Paste one physical line at a time. |
| "No marketplaces configured" | `claude plugin marketplace add anthropics/claude-plugins-official` first. |
| `/prime` reports "No workspace-identity.json found — treating as CEO workspace" | On a managed deploy the identity file is missing from the engine root, or its leading dot was dropped on copy. Re-place it (§6), then restart `claude` — the identity is cached per process. |
| `sync-corporate.py` prints "CEO workspace — … nothing to consume" on a managed deploy | Same cause as the row above: the engine does not know this is a managed workspace. Fix the identity file first. |
| `sync-corporate.py` fails on `gh repo clone /heading-os-corporate` | The GitHub org resolved to an empty string. Set `github_org` in `<data-root>/config/operator.yaml`. Note that putting `HEADING_OS_OPERATOR_GITHUB_ORG` in `.env` does not help — the org is read before `.env` is loaded. |
| `get_data_root()` prints a path under `…/examples` | Data sibling not found. Set `HEADING_OS_DATA=/abs/path/.heading-os-data` in `.env`. |
| `push-all.py`: "data overlay resolves to the engine clone" | Sibling not found — set `HEADING_OS_DATA` and retry. |
| `push-all.py` exits `3` and prints "Partial: N of M" | One repository was not pushed, for the reason printed beside its name. This is not a failed backup: everything else went, and the skipped repo is committed locally. Resolve the reason (usually: merge your branch into `main`) and run it again. |
| `push-all.py` exits `3` and prints "NOTHING PUSHED" | Every repository it tried was skipped, so there is no new off-machine copy — treat it as a backup that did not happen. Your work is committed locally. Resolve the reason beside each name (usually: merge your branch into `main`) and run it again. A one-repository workspace (managed, or pre-cutover) can only ever produce this shape. |
| `push-all.py` exits `2` and prints "REFUSING TO PUSH" naming a remote | A repository other than the engine is aimed at the engine's remote, or at a repository GitHub reports as public. Nothing was pushed and nothing was lost. Check where it points with `git -C <repo> remote get-url --push origin` and correct it before running again. This refusal stops the whole run on purpose: a wrong remote makes every repository in the run suspect. |
| `push-all.py` prints "could not verify the visibility of ..." and continues | The GitHub API could not answer (no network, a rate limit, or a repository the token cannot see). The offline check still ran and passed. This is a warning, not a failure. A tokenless run (for example `--dry-run` with no `GH_TOKEN`) prints nothing here: the question cannot be asked at all without a token, which is different from the answer coming back empty. It prints the `NOTE` line in the next row instead. |
| `push-all.py` prints "NOTE: the remote wall reached its lower ceiling for ..." and continues | The wall returned no objection on a repository it did not fully evaluate, and it is saying so rather than letting the silence read as a pass. It names which part did not run: the engine's own push remotes could not be read, the named remote does not resolve to a push URL, or GitHub was never asked about this remote. The last of those is what a tokenless run and a non-GitHub remote both produce, so it is the routine shape on `--dry-run` without `GH_TOKEN`. The offline comparison still ran. This is information, not a failure. Silence on a fully evaluated repository is the signal that everything was checked. |
| Backup pushed into the engine and got a 403 | On a managed workspace the engine is read-only — the 403 is the safety net. Your identity `type` is likely not `exec-workspace`; fix it (§6), then back up again. |
| `git pull --ff-only` says "divergent" | `git log origin/main..HEAD` must be empty. If a local commit misrouted data, relocate it into the overlay first. `git reset --hard origin/main` only after confirming files are safe in the overlay. |
| Multi-line paste mangles (`^[[200~`) | Bracketed-paste artifact. Paste commands one physical line at a time, or join with `&&` / `;`. |
| `/council` says a key is missing, or `/recall` returns nothing | Model integration not set up. See [MODELS-SETUP.md](MODELS-SETUP.html): install Ollama, pull `bge-m3`, add the council keys. |

> **When in doubt, stop.** Halt if a destination, a divergence, or an error touches
> data location or git history. Capture the terminal text before acting. Never
> guess past it.

---

## 14. Command quick-reference

```bash
# enter Linux
wsl -d Ubuntu

# where am I / which interpreter
which claude node uv ; uv --version

# health & data seam
cd <engine>
uv run python scripts/workspace-health.py
uv run python -c "from scripts.utils.paths import get_data_root; print(get_data_root())"

# update engine (safe; pull-only when managed)
git pull --ff-only origin main

# corporate content, read in place
uv run python scripts/sync-corporate.py

# import legacy records (dry-run first)
uv run python scripts/import-legacy-records.py --from "<old-workspace>" --dry-run
uv run python scripts/import-legacy-records.py --from "<old-workspace>"

# back up the data overlay
uv run python scripts/push-all.py

# plugins
claude plugin marketplace list
claude plugin install <plugin>@claude-plugins-official --scope project
```

---

*HEADING OS · Deployment & Setup · maintained by Misha Hanin · see also QUICKSTART for
the short version and the in-workspace `/prime` for live orientation.*
