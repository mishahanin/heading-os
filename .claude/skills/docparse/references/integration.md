# DocParse Integration Guide

Consumed by: `.claude/skills/docparse/SKILL.md`
Last Updated: 2026-04-09

## CLI Interface

### Parse Documents

```bash
python scripts/docparse.py parse \
  --files "path/to/doc.pdf" "other.pptx" \
  [--pages "1-5,10"] \
  [--dpi 150] \
  [--password "secret"] \
  [--no-cache] \
  --output-json /tmp/parsed.json
```

Directories are auto-discovered for supported file types.

### Generate Visual Citation Report

```bash
python scripts/docparse.py report \
  --parse-json /tmp/parsed.json \
  --citations /tmp/citations.json \
  [--output-dir outputs/intel/docparse/2026-04-09/] \
  [--title "Analysis Report"] \
  [--no-pdf]
```

## Python Import Interface

```python
from scripts.docparse import parse_document, find_boxes_for_quote

# Parse a document (cache-aware, returns dict)
doc = parse_document("path/to/doc.pdf", pages="1-5", dpi=150)

# Find bounding boxes for a verbatim quote (returns pixel coordinates)
boxes = find_boxes_for_quote(doc["pages"][0]["text_items"], "exact quote", dpi=150)
```

## Parse Output JSON Schema

```json
{
  "files": [{
    "file": "C:/absolute/path.pdf",
    "file_name": "report.pdf",
    "parsed_at": "2026-04-09T10:30:00+00:00",
    "dpi": 150,
    "pages": [{
      "page_num": 1,
      "width_pt": 595.0,
      "height_pt": 842.0,
      "text": "full page text (use this for reading)",
      "text_items": [
        { "text": "Revenue", "x": 72.0, "y": 144.5, "width": 80.3, "height": 14.0 }
      ]
    }]
  }],
  "summary": { "total_files": 1, "total_pages": 3, "cache_hits": 0, "elapsed_seconds": 2.1 }
}
```

- `text` - concatenated page text for reading/analysis
- `text_items` - individual text elements with bounding boxes (PDF points). For bounding box matching only - do not present to users.
- Coordinates are in PDF points (72 points/inch). Convert to pixels: `px = pt * (dpi / 72)`

## Citations Input JSON Schema

```json
{
  "question": "What was Q3 revenue?",
  "answer_md": "Revenue was **$1.2B** [1], up 12% [2].",
  "citations": [
    { "id": 1, "file": "report.pdf", "page": 3, "quote": "1,200,000", "relevance": "Q3 total revenue" }
  ]
}
```

- `quote` must be character-for-character exact from the parsed text
- `id` matches `[N]` markers in `answer_md`

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Prerequisites missing (run `setup --install`) |
| 2 | Parse or report generation error (including all files not found) |

## Cache Behavior

- Location: `.cache/docparse/`
- Key: SHA256 of resolved file path + size + mtime
- TTL: 7 days
- Invalidation: automatic on file change (mtime or size differs)
- Skip: `--no-cache` flag

## Integration Examples

### From `/deal-strategy` (research phase)

```bash
# Parse prospect's RFP document
python scripts/docparse.py parse \
  --files "datastore/deals/prospect-rfp.pdf" \
  --output-json /tmp/prospect-parsed.json
# Then read the JSON and analyze requirements
```

### From `/validate` (fact-checking)

```bash
# Parse source document for claim verification
python scripts/docparse.py parse \
  --files "datastore/source-doc.pdf" \
  --output-json /tmp/source-parsed.json
# Compare claims against parsed text with page references
```
