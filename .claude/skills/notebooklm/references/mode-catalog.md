# NotebookLM - Mode Catalog

Consumed by: `.claude/skills/notebooklm/SKILL.md` Phase 1 mode dispatch. Each section below corresponds to one mode keyword. SKILL.md owns auth (Phase 0), mode parsing, voice rules, and NEVER constraints; this file owns the per-mode flow.

Last Updated: 2026-06-10

All commands use the CLI invocation pattern from SKILL.md:

```
NLM="$(command -v nlm 2>/dev/null || echo C:/Users/<you>/AppData/Roaming/Python/Python314/Scripts/nlm.exe)"
NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" <subcommand> [flags]
```

---

## Mode: `status`

Check auth status and list all notebooks.

1. Run (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook list --json
   ```
2. Parse the JSON response (array of notebook objects with id, title, source_count, updated_at)
3. Display:

```
## NotebookLM Status

Auth: Valid | Account: <your-google-account>@gmail.com

| # | Notebook | Sources | Modified |
|---|----------|---------|----------|
| 1 | [title] | [source_count] | [updated_at] |

Total: [count] notebooks
```

---

## Mode: `create [title]`

Create a new notebook.

1. If no title in arguments: ask "What topic should this notebook cover?"
2. Run (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook create "[title]"
   ```
3. Capture notebook ID from output
4. Display: "Created notebook: **[title]** (ID: `[id]`)"
5. Offer: "Add sources? Provide URLs, text content, or file paths."

---

## Mode: `add [notebook-id] [sources...]`

Add sources to an existing notebook. Supports URLs (bulk), text, and file uploads.

1. If no notebook-id: run `status` mode first, ask which notebook to use
2. Parse sources from arguments:
   - HTTP/HTTPS URLs -> each becomes a `--url` flag
   - Quoted text block -> use `--text` flag
   - Local file paths (.pdf, .txt, .md, .docx) -> use `--file` flag
3. For URLs (most common case), run (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" source add <notebook-id> --url "<url1>" --url "<url2>" --url "<url3>"
   ```
4. After adding, confirm with (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" source list <notebook-id> --json
   ```
5. Display: "Added [N] sources to **[notebook title]**. Total sources: [count]."

---

## Mode: `query [notebook-id] "question"`

Query a notebook with AI-grounded citations from its sources.

1. If no notebook-id: run `status` mode, ask which notebook
2. If no question: ask "What would you like to know from this notebook?"
3. Run (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook query <notebook-id> "<question>" --json
   ```
4. Parse JSON response. Fields are nested under `value`: `value.answer`, `value.sources_used`, `value.conversation_id`
5. Display:

```
## Query Result

[answer text]

### Sources Cited
| # | Source | Title |
|---|--------|-------|
| 1 | [id] | [title] |

_Conversation ID: [conversation_id] (used for follow-up queries)_
```

6. Offer handoffs:
   - "Follow-up question? (same conversation thread)"
   - "Capture as a ZK note? (`/zk add`)"
   - "Get Odin's take on this? (`/odin consult`)"

### Follow-up Queries

If the user asks a follow-up, reuse the conversation_id:
```bash
NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook query <notebook-id> "<follow-up>" --json --conversation-id <conv-id>
```

---

## Mode: `audio [notebook-id]`

Generate an audio overview (AI podcast synthesis) from notebook sources.

**This is the killer feature - no alternative exists in the workspace.**

1. If no notebook-id: run `status` mode, ask which notebook
2. Present format options if not specified:
   - Format: `deep_dive` (default, two hosts discuss), `brief` (quick summary), `critique` (critical analysis), `debate` (opposing views)
   - Length: `short`, `default`, `long`
   Use defaults if user doesn't specify preferences.
3. Start generation (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" audio create <notebook-id> --format <fmt> --length <len> -y
   ```
4. Capture artifact_id from output. NOTE: `audio create` returns plain text (not JSON), e.g. "Created audio overview\n  ID: uuid-here". Extract the ID from the text. If unclear, immediately run `studio status --json` (next step) and find the artifact with status `in_progress`.
5. Poll for completion - repeat every 15 seconds, max 20 iterations (5 minutes). Each poll (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" studio status <notebook-id> --json
   ```
   Parse the JSON, find the artifact matching the artifact_id, check its status:
   - `in_progress`: wait 15 seconds, poll again
   - `completed`: proceed to download (step 6)
   - `failed`: report "Audio generation failed. Try again or check NotebookLM web UI."
   If max iterations reached: "Audio generation timed out after 5 minutes. Check notebooklm.google.com."
6. Download the completed audio (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" download audio <notebook-id> <artifact-id> -o "outputs/content/notebooklm/audio/YYYY-MM-DD-<slug>.mp3"
   ```
   Where `<slug>` is a kebab-case version of the notebook title, max 40 characters.
7. Display:

```
## Audio Overview Generated

Format: [format] | Length: [length]
Saved to: outputs/content/notebooklm/audio/[filename]

Generate a different format? (brief / critique / debate)
```

---

## Mode: `research [topic]`

Run web discovery to find new sources on a topic. Optionally import to notebook and feed to Odin.

1. Parse topic from arguments
2. If a notebook-id is provided: use that notebook. Otherwise create one (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook create "<topic> Research"
   ```
   Capture the new ID.
3. Determine research depth:
   - Default: `fast` (~30 seconds, ~10 sources)
   - If user says "deep", "thorough", or "comprehensive": `deep` (~3-5 minutes, ~40 sources, web only)
4. Start research (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" research start "<topic>" --source web --mode <fast|deep> --notebook-id <nb-id>
   ```
5. Poll for completion (Bash, timeout 30000 each):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" research status <nb-id> --json
   ```
   - Interval: 15s for fast, 30s for deep
   - Max wait: 60s for fast, 360s for deep
   - Check `status` field: `in_progress`, `completed`, or `failed`
6. On completion, display:

```
## Research Discovery: [topic]

Mode: [fast/deep] | Sources found: [N]

| # | Title | URL |
|---|-------|-----|
| 1 | [title] | [url] |
| 2 | [title] | [url] |
...

Options:
- "import all" - add all sources to the notebook
- "import 1, 3, 5" - add selected sources
- "odin 2, 4" - feed selected URLs to /odin learn
- "go deeper" - run deep research (if fast was used)
```

7. On "import [selection]" (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" research import <nb-id> --indices <comma-separated-list>
   ```
8. On "odin [selection]": list the selected URLs and ask for confirmation. On approval, Claude invokes `/odin learn <url>` for each as a separate skill invocation. Do NOT invoke Odin inside this skill.

---

## Mode: `report [notebook-id]`

Generate a briefing report grounded in the notebook's sources.

1. If no notebook-id: run `status` mode, ask which notebook
2. Present format options if not specified:
   - `Briefing Doc` (default - structured executive summary)
   - `Study Guide` (learning-focused with key concepts)
   - `Blog Post` (narrative format)
   - `Create Your Own` (requires a custom prompt)
3. If "Create Your Own": ask for a prompt describing the desired output
4. Start generation (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" report create <notebook-id> --format "<format>" -y
   ```
   If custom prompt: add `--prompt "<prompt>"`
   NOTE: `report create` returns plain text, not JSON. The artifact ID appears in the output text.
5. Poll for completion - same pattern as audio mode (15s interval, max 5 minutes):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" studio status <notebook-id> --json
   ```
6. Download completed report (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" download report <artifact-id> --format pdf -o "outputs/content/notebooklm/reports/YYYY-MM-DD-<slug>.pdf"
   ```
7. Display: "Report saved to `outputs/content/notebooklm/reports/[filename]`"
8. Offer: "Also download as PPTX? Capture this? (`/odin log` for CEO; `/zk distill` to the knowledge base)"

---

## Mode: `describe [notebook-id]`

Get an AI-generated summary of a notebook with topic suggestions.

1. If no notebook-id: run `status` mode, ask which notebook
2. Run (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" notebook describe <notebook-id> --json
   ```
3. Parse JSON: summary text, suggested topics, source overview
4. Display the AI summary and suggested topics
5. Offer: "Query this notebook about any of these topics? Generate an audio overview?"

---

## Mode: `download [notebook-id] [artifact-id]`

Download any artifact from a notebook.

1. If no artifact-id provided, list available artifacts first (Bash, timeout 30000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" studio status <notebook-id> --json
   ```
   Display artifacts with IDs, types, and statuses. Ask which to download.
2. Determine artifact type from the studio status response
3. Map type to download command and default format:
   | Type | Command | Default Format |
   |------|---------|---------------|
   | audio | `download audio` | mp3 (auto) |
   | video | `download video` | mp4 (auto) |
   | report | `download report` | `--format pdf` |
   | slides | `download slide-deck` | `--format pptx` |
   | quiz | `download quiz` | `--format markdown` |
   | flashcards | `download flashcards` | `--format markdown` |
   | infographic | `download infographic` | png (auto) |
   | data-table | `download data-table` | `--format markdown` |
   | mind-map | `download mind-map` | `--format markdown` |
4. Run (Bash, timeout 60000):
   ```bash
   NO_COLOR=1 PYTHONIOENCODING=utf-8 "$NLM" download <type> <notebook-id> <artifact-id> -o "outputs/content/notebooklm/downloads/YYYY-MM-DD-<slug>.<ext>"
   ```
5. Display: "Downloaded to `outputs/content/notebooklm/downloads/[filename]`"
