<!-- version: 1.0.0 | last-updated: 2026-06-30 -->
# AI models & integrations

How to connect every AI model HEADING OS uses beyond Claude itself: the local
Ollama runtime, the `bge-m3` embedding model that powers semantic recall, and the
three cloud reasoning models (Gemini, Grok, Kimi) behind the `/council` and
`/deep-research-advance` skills. Follow it once when standing up a clone; return to
the troubleshooting matrix when a model goes dark.

> Claude is configured during the main install (see [DEPLOYMENT](DEPLOYMENT.html)).
> Everything on this page is **optional**: the engine runs without any of it. Each
> integration lights up exactly one set of capabilities, and the rest of HEADING OS
> keeps working when it is absent.

---

## 1. What needs what

Each capability maps to one model, one transport, and one credential. If you only
want a subset, set up only those rows.

| Capability | Skill / script | Model | Transport | Credential | Where to get it |
|---|---|---|---|---|---|
| Semantic recall, memory index | `/recall`, `scripts/memory-index.py` | `bge-m3` | local Ollama | none (runs on your machine) | `ollama pull bge-m3` |
| Second opinion, voice 1 | `/council` (Gemini) | `gemini-3.5-flash` | Google API | `GEMINI_API_KEY` | Google AI Studio |
| Second opinion, voice 2 | `/council` (Grok) | `grok-4.3` | xAI API | `XAI_API_KEY` | xAI console |
| Second opinion, voice 3 | `/council` (Kimi) | `kimi-k2.6:cloud` | Ollama cloud routing | `OLLAMA_API_KEY` | Ollama account |
| Deep web research | `/deep-research-advance` | `kimi-k2.6:cloud` + Perplexity | Ollama cloud + Perplexity API | `OLLAMA_API_KEY`, `PERPLEXITY_API_KEY` | Ollama account, Perplexity |

Two of these ride on Ollama (the local embedder and Kimi), so install Ollama first
if you want either. Gemini and Grok are pure cloud APIs and need no local runtime.

All credentials live in the engine's gitignored `.env`. See [section 6](#6-the-env-block) for
the exact block to paste.

---

## 2. Ollama (local runtime)

Ollama is a local model server. HEADING OS uses it for two distinct jobs:

1. **Embeddings**, fully on your machine, with `bge-m3`. No data leaves the host, no
   API key, zero cost. This is what `/recall` and the memory index run on.
2. **Cloud routing** for Kimi, where Ollama proxies a request to a hosted
   `kimi-k2.6:cloud` model. Here the prompt does leave the machine (see the
   [privacy guardrail](#52-privacy-guardrail-read-before-using-cloud-voices)).

You need Ollama installed and running for either. If you want neither recall nor
Kimi, skip this whole section.

### 2.1 Install

**Linux / WSL2 (Ubuntu):**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

The installer registers a `systemd` service that starts Ollama on boot and keeps it
listening on `http://localhost:11434`. On WSL2 without systemd, start it by hand
with `ollama serve` (see [2.3](#23-start-and-verify)).

**macOS:**

Download the app from [ollama.com/download](https://ollama.com/download), or:

```bash
brew install ollama
```

The macOS app runs Ollama as a menu-bar service on the same `localhost:11434`.

### 2.2 Verify the binary

```bash
ollama --version
```

If the command is not found, the install did not register on your `PATH`. Open a new
shell, or add the install location (`/usr/local/bin` on most systems) to `PATH`.

### 2.3 Start and verify

```bash
# start the server (only needed if no systemd service is running)
ollama serve &

# confirm it answers on the loopback port the engine expects
curl -s http://localhost:11434/api/tags
```

A JSON response (even an empty `{"models":[]}`) means the server is up and reachable
on the host and port the engine reads from `config/memory-index.yaml`. A connection
refused means it is not running.

### 2.4 Keeping Ollama current (optional)

New Ollama releases ship regularly. To update, rerun the install script (Linux) or
update the app (macOS). A `systemd` timer can automate the Linux path; that is an
operator preference and not required by HEADING OS.

---

## 3. Embedding model: `bge-m3` for recall

`/recall` and `scripts/memory-index.py` build a local associative-memory index over
the workspace (Odin brain, threads, CRM, context, outputs, and more), then answer
queries by meaning rather than exact words. The embeddings are computed locally by
`bge-m3` through Ollama. **No API key, no network, no cost.**

### 3.1 Pull the model

```bash
ollama pull bge-m3
```

`bge-m3` is multilingual (Russian and English), 1024-dimensional, and roughly 1.2 GB
on disk. The pull is a one-time download.

### 3.2 Configuration

The embedder is declared in `config/memory-index.yaml`. The two lines that matter:

```yaml
model: bge-m3
host: "http://localhost:11434"
```

`host` is broken out deliberately: pointing it at a remote Ollama instance (for
example a GPU box) is a single-line change. Everything else in that file tunes
ranking, chunking, and which workspace layers get indexed; the defaults are sound.

### 3.3 Build and query

```bash
# build (or incrementally refresh) the index. Safe to rerun; only changed files re-embed.
uv run python scripts/memory-index.py build

# query it directly
uv run python scripts/memory-index.py query "what did we decide about pricing"

# index statistics
uv run python scripts/memory-index.py stats
```

In a Claude session, `/recall <question>` runs the same retrieval and answers only
from the returned sources, with file-path citations, or reports a gap.

The index lives at `.memory-index/` (gitignored, rebuildable). It is a cache, not a
source of truth: delete it and rebuild any time.

> **First full build is slow on CPU.** On a CPU-only machine a from-scratch build of
> a large workspace can take well over an hour. It commits per file and is
> resumable, so it is safe to interrupt and rerun. Incremental refreshes after that
> are fast.

### 3.4 When Ollama is down

Recall degrades gracefully. If Ollama is unreachable, a skill notes "index not
refreshed (ollama down)" and continues on the already-built index; a brain write
still lands and is reindexed on the next successful build. Nothing fails hard
because the embedder is offline.

---

## 4. Cloud reasoning models for `/council`

`/council` asks three independent models the same hard question in parallel, then
sets their answers side by side with Claude's own view. It has two modes: in
**independent** mode each model reasons fresh from the problem; in **critique** mode
each stress-tests a draft you supply. The three voices are Gemini, Grok, and Kimi.

Each voice is a separate credential and a separate `scripts/*-consult.py` wrapper.
`/council` runs whichever voices have a key set and degrades to the others if one is
missing or fails. To run the full council, set all three.

### 4.1 Shared behaviour

- Each wrapper is a pure API wrapper: it prints the model's answer to stdout and
  writes nothing to disk. The skill handles formatting and transcript persistence.
- Exit codes are uniform: `0` success, `2` argument or missing-key error, `3` API
  call failed (network, rate limit, invalid model).
- A missing key is reported clearly at call time, for example
  `GEMINI_API_KEY is missing from .env. Add it before invoking the council.`
- Default models are pinned in each script's `DEFAULT_MODEL`; override per call with
  `--model` on the script, or `--gemini-model` / `--grok-model` / `--kimi-model` on
  `/council`.

---

## 5. The three council voices

### 5.1 Gemini

- **Credential:** `GEMINI_API_KEY`
- **Where to get it:** [Google AI Studio](https://aistudio.google.com/apikey). Sign
  in with a Google account and create an API key. A free tier exists; heavier use
  needs billing enabled on the associated Google Cloud project.
- **Default model:** `gemini-3.5-flash`
- **SDK / transport:** the `google-genai` SDK (pinned in `pyproject.toml`), calling
  Google's hosted API directly. No local runtime.
- **Wrapper:** `scripts/gemini-consult.py`

```bash
uv run python scripts/gemini-consult.py --mode independent \
  --question "Should we anchor pricing per Gbps or per module?"
```

### 5.2 Grok

- **Credential:** `XAI_API_KEY`
- **Where to get it:** the [xAI console](https://console.x.ai). Create an account,
  add billing, and generate an API key. Grok's API is OpenAI-compatible.
- **Default model:** `grok-4.3`
- **SDK / transport:** the `openai` SDK (pinned in `pyproject.toml`) pointed at xAI's
  base URL `https://api.x.ai/v1`. No local runtime.
- **Wrapper:** `scripts/grok-consult.py`

```bash
uv run python scripts/grok-consult.py --mode independent \
  --question "Should we anchor pricing per Gbps or per module?"
```

### 5.3 Kimi

Kimi is reached through Ollama's cloud routing rather than a direct vendor API, so
[Ollama](#2-ollama-local-runtime) must be installed and running.

- **Credential:** `OLLAMA_API_KEY`
- **Where to get it:** your Ollama account at [ollama.com](https://ollama.com). Sign
  in (`ollama signin` on the CLI, or the web app), then create an API key under your
  account keys. `kimi-k2.6:cloud` is a hosted model that Ollama proxies for you.
- **Default model:** `kimi-k2.6:cloud`
- **SDK / transport:** the `openai` SDK pointed at the local Ollama endpoint
  `http://localhost:11434/v1`, which forwards to Ollama's cloud. Some local installs
  accept any non-empty string as the key; the value in `.env` is sent verbatim.
- **Wrapper:** `scripts/kimi-consult.py`

```bash
uv run python scripts/kimi-consult.py --mode independent \
  --question "Should we anchor pricing per Gbps or per module?"
```

> **Kimi is a thinking model.** Its chain of thought consumes the same token budget
> as its answer, so a tiny budget can be spent entirely on reasoning and return an
> empty answer. The wrapper detects this and retries once at a higher ceiling before
> erroring. If you see an empty-answer truncation note, that retry has already run.

### 5.4 Running a subset

```bash
# one voice only (mutually exclusive)
uv run python scripts/gemini-consult.py ...   # or grok-consult / kimi-consult

# inside /council, skip or isolate voices
/council --no-kimi   "question"      # Gemini + Grok only
/council --grok-only "question"      # Grok alone
```

### 5.5 Privacy guardrail (read before using cloud voices)

Gemini, Grok, and Kimi are **third-party clouds outside the 31C data boundary**.
Treat all three as one privacy tier. The question text and any context you pass them
leave your machine.

- Never send private or internal data to a cloud voice: CRM contacts, pipeline data,
  Odin brain content, mail, message history, partner names, or pricing do not belong
  in a council question or in `--domains`.
- `/council` can carry business context by design and runs under your judgement;
  `/deep-research-advance` is hard-scoped to public topics and refuses private ones.
  The embedding model (`bge-m3`) is the only fully local AI model here and is the
  right tool when the content cannot leave the host.

---

## 6. Deep web research: `/deep-research-advance`

`/deep-research-advance` runs a token-heavy acquisition pass over the public web with
**Perplexity**, then a reasoning and verification pass with **Kimi**, before Claude
audits the findings into a cited report. It needs:

- `PERPLEXITY_API_KEY` from [perplexity.ai](https://www.perplexity.ai) (API settings).
- `OLLAMA_API_KEY` and a running Ollama (same Kimi setup as [5.3](#53-kimi)).

The same third-party-cloud guardrail applies, and the skill enforces it: only the
public research question and the gathered web corpus flow to Perplexity and Kimi.
Refuse to run it on anything private.

---

## 7. The `.env` block

Paste this into the engine's `.env` (copy from `.env.example` first) and fill in the
keys you actually use. Leave a line blank or unset to keep that integration dark; the
engine reads what is present and ignores the rest.

```bash
# Local embeddings (bge-m3 for /recall): NO key needed, runs on your machine via Ollama.

# Council voice 1: Gemini (Google AI Studio)
GEMINI_API_KEY=your-gemini-key-here

# Council voice 2: Grok (xAI console, OpenAI-compatible)
XAI_API_KEY=xai-your-key-here

# Council voice 3: Kimi, plus /deep-research-advance reasoning (Ollama cloud routing)
OLLAMA_API_KEY=your-ollama-cloud-key-here

# /deep-research-advance web acquisition (Perplexity)
PERPLEXITY_API_KEY=pplx-your-key-here
```

Which keys are required depends on the capability:

| Want | Required keys |
|---|---|
| `/recall` only | none (just Ollama + `bge-m3`) |
| Full `/council` | `GEMINI_API_KEY`, `XAI_API_KEY`, `OLLAMA_API_KEY` (+ Ollama running) |
| Partial `/council` | only the keys for the voices you keep |
| `/deep-research-advance` | `OLLAMA_API_KEY`, `PERPLEXITY_API_KEY` (+ Ollama running) |

Secrets never get committed: `.env` is gitignored, and a push-time content scan
blocks any credential that slips into a tracked file regardless. Never paste a live
key into chat or a tracked file.

---

## 8. Verify your setup (pre-flight)

Before relying on any model, prove the path end to end.

```bash
# Embeddings / recall
ollama pull bge-m3
uv run python scripts/memory-index.py build
uv run python scripts/memory-index.py query "test"

# Council voices: each should print an answer and exit 0
uv run python scripts/gemini-consult.py --mode independent --question "Reply with the single word OK."
uv run python scripts/grok-consult.py   --mode independent --question "Reply with the single word OK."
uv run python scripts/kimi-consult.py   --mode independent --question "Reply with the single word OK."
```

An exit code of `2` means the key is missing or malformed; `3` means the key is set
but the call failed (wrong key, no billing, network, or, for Kimi, Ollama not
running). A printed answer and exit `0` mean the voice is live.

---

## 9. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `/recall` returns nothing, or "index not refreshed (ollama down)" | Ollama is not running. `ollama serve &`, then `curl -s http://localhost:11434/api/tags`. |
| `memory-index.py` errors mentioning the embed endpoint | `bge-m3` not pulled, or wrong host. `ollama pull bge-m3`; confirm `host:` in `config/memory-index.yaml`. |
| First `memory-index build` runs for a very long time | Expected on CPU for a large workspace. It is resumable and commits per file; let it finish or rerun. |
| `GEMINI_API_KEY is missing from .env` | Key absent. Add it from Google AI Studio. |
| `XAI_API_KEY is missing from .env` | Key absent. Add it from the xAI console. |
| `OLLAMA_API_KEY is missing from .env` | Key absent. Create one in your Ollama account; Kimi rides on Ollama cloud routing. |
| Council voice exits `3` (API failed) | Key set but call failed: wrong key, billing not enabled, rate limit, or (Kimi) Ollama not running. |
| Kimi returns an empty answer / truncation note | Thinking model spent the budget on reasoning. The wrapper already retried at a higher ceiling; rerun, or raise the budget. |
| A council voice is silently absent from the result | That voice has no key set, or failed. `/council` degrades to the remaining voices on purpose. |

---

## 10. Reference

| File | Role |
|---|---|
| `config/memory-index.yaml` | Embedder model + host, ranking, chunking, indexed layers |
| `scripts/memory-index.py` | Builds and queries the local recall index (`bge-m3`) |
| `scripts/utils/embeddings.py` | Local embedding client over Ollama |
| `scripts/gemini-consult.py` | Gemini council wrapper (`GEMINI_API_KEY`) |
| `scripts/grok-consult.py` | Grok council wrapper (`XAI_API_KEY`) |
| `scripts/kimi-consult.py` | Kimi council wrapper (`OLLAMA_API_KEY`, Ollama cloud) |
| `scripts/utils/kimi_transport.py` | Research-only Kimi transport (no business context) |
| `.claude/skills/council/SKILL.md` | `/council` orchestration, modes, flags, transcript |
| `.claude/skills/recall/SKILL.md` | `/recall` retrieval and answer discipline |
| `.claude/skills/deep-research-advance/SKILL.md` | Deep research flow and privacy guardrail |
| `.env.example` | Template listing every credential the engine reads |

---

*HEADING OS · AI models & integrations · maintained by 31 Concept · see also
[DEPLOYMENT](DEPLOYMENT.html) for the core install and [Memory & ODIN](memory-odin.html)
for how recall feeds the brain.*
