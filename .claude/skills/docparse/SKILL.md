---
name: docparse
description: >
  Parse documents (PDF, DOCX, PPTX, XLSX) with spatial bounding boxes via LiteParse.
  Answer questions with visual citations showing exact source locations on page images.
  Generate 31C-branded HTML/PDF reports. Maintains a parse cache for efficiency.
  Use when: "parse this document", "docparse", "visual citations", "show me where it says",
  "document analysis with citations", "parse with bounding boxes", "extract from this PDF".
  NOT for: plain text extraction without spatial data (use datastore-extract.py),
  email analysis (use /email-intel), web scraping (use /playwright or /firecrawl).
argument-hint: "[file-path or directory] [question]"
allowed-tools: "Bash(python3:*), Read, Write, Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - parse this document
    - docparse
    - document analysis with citations
    - visual citation report
    - show me where it says
    - parse with bounding boxes
    - extract from this PDF
x-31c-capability:
  what: >
    Parses PDF, DOCX, PPTX, and XLSX with spatial bounding boxes and answers a
    question with visual citations - every cited fact traced to its exact
    location on the source page - and can render a 31C-branded HTML/PDF report.
  how: >
    Run /docparse [file-or-directory] ["question"]. Uses scripts/docparse.py
    (cache-aware); add --report for the visual citation report.
  when: >
    Use when you need spatially-grounded citations or to show where a document
    says something. For plain text extraction use datastore-extract.py; for
    email use /email-intel; for web pages use /playwright.
---
# DocParse - Document Parsing with Visual Citations

Parse documents and answer questions with spatially-grounded visual citations. Every cited fact is traced to an exact location on the source page.

## Variables

- `$ARGUMENTS` - file path(s) or directory, optional question in quotes
- Examples:
  - `/docparse datastore/corporate/presentations/deck.pdf`
  - `/docparse datastore/books/ "What are the key principles?"`
  - `/docparse report.pdf --report`

## Phase 0 - Setup & Context

1. Run prerequisite check:
   ```bash
   python scripts/docparse.py setup --check
   ```
   If any check fails, print the install instructions and STOP.

2. Parse `$ARGUMENTS`:
   - Extract file path(s) or directory
   - Extract optional question (quoted string)
   - Check for `--report` flag or if user explicitly asks for "visual citations" / "visual report"
   - Check for `--pages` specification (e.g., `--pages 1-10`)

3. If a directory was given, discover supported files:
   ```bash
   # Use Glob to find PDFs, DOCX, PPTX, XLSX in the directory
   ```

4. For documents over 50 pages, ask the user which page range to parse. Never parse 100+ pages without an explicit `--pages` restriction.

## Phase 1 - Parse

Run the parser:
```bash
python scripts/docparse.py parse \
  --files "<file1>" "<file2>" \
  [--pages "1-10"] \
  [--dpi 150] \
  --output-json /tmp/docparse_parsed.json
```

Read the output JSON and present a summary:
- Number of files and pages parsed
- Cache hits vs fresh parses
- Total text items extracted

## Phase 2 - Analysis

If the user asked a question:

1. Read `/tmp/docparse_parsed.json` focusing on each page's `text` field. Do NOT read raw `text_items` arrays - those are for the bounding box engine only.

2. Analyze the text to answer the question. Identify specific passages that support the answer.

3. For each cited passage, extract an **exact verbatim quote** from the parsed text:
   - Character-for-character exact (this is critical for bounding box lookup)
   - Prefer short quotes under 60 characters
   - Quote evidence (numbers, data values, key phrases), not just labels
   - Include 5-15 citations per answer

4. Write the citations JSON:
   ```bash
   # Write to /tmp/docparse_citations.json
   ```

   Schema:
   ```json
   {
     "question": "What was Q3 revenue?",
     "answer_md": "Revenue was **$1.2B** [1], up 12% year-over-year [2].",
     "citations": [
       {
         "id": 1,
         "file": "report.pdf",
         "page": 3,
         "quote": "1,200,000",
         "relevance": "Q3 total revenue figure showing 12% growth"
       }
     ]
   }
   ```

   Rules for citations:
   - `id` must match the `[N]` markers in `answer_md` (1-indexed)
   - `file` is the filename (not full path)
   - `page` is 1-indexed
   - `quote` must be character-for-character exact from the parsed text
   - `relevance` explains the "so what" - not just restating the quote

5. Present the answer with inline `[N]` citation markers.

If no question was asked, present a summary of the document contents and offer:
- "Ask a question about these documents?"
- "Generate a visual citation report?"

## Phase 3 - Report (optional)

If `--report` was requested, or the user asks for visual citations:

```bash
python scripts/docparse.py report \
  --parse-json /tmp/docparse_parsed.json \
  --citations /tmp/docparse_citations.json \
  --title "Document Analysis Report" \
  [--no-pdf]
```

Present the output:
- HTML report path (self-contained, can be opened in any browser)
- PDF path if generated
- Citation count and page count

## Integration

Other skills can call docparse as a building block:

```bash
# Parse a document (cache-aware)
python scripts/docparse.py parse --files "path/to/doc.pdf" --output-json /tmp/parsed.json

# Read the JSON and use the text content for analysis
```

See `.claude/skills/docparse/references/integration.md` for full details.

## Voice

- Single hyphens only (never --)
- ODUN.ONE and DPI+ when referencing the product
- Report titles and findings use professional, evidence-based language

## NEVER

- Never parse files over 100 pages without explicit `--pages` restriction
- Never expose raw bounding box coordinates in user-facing output
- Never skip the setup check (Phase 0, Step 1)
- Never construct bounding box coordinates manually - always let the script's `find_boxes_for_quote()` handle coordinate mapping
- Never present the parsed JSON's `text_items` arrays directly to the user - use `text` fields for reading
- Never modify the `quote` field to "improve" it - exact match is required for highlighting
