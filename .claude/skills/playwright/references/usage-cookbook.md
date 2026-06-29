# Playwright — Usage Cookbook

Consumed by: `.claude/skills/playwright/SKILL.md`.
Last Updated: 2026-06-16

Worked command examples for each `pw.py` subcommand. The SKILL body holds the
Quick-Reference table and the data-path resolution rules; this file holds the verbose
per-command examples so the body stays under the line budget.

All commands run from the workspace root (`cd "$(git rev-parse --show-toplevel)"` first).
`$OUTPUTS_DIR` denotes the resolved data-overlay path (see SKILL § Output Location);
prefer OMITTING `-o` and letting the script auto-place under the DATA overlay.

## Screenshots

```bash
# Basic screenshot
python ".claude/skills/playwright/scripts/pw.py" screenshot "https://example.com"

# Full-page, mobile device, custom output
python ".claude/skills/playwright/scripts/pw.py" screenshot "https://example.com" \
  --full-page --device "iPhone 15" -o "$OUTPUTS_DIR/browser/screenshots/example-mobile.png"

# With visible browser for debugging
python ".claude/skills/playwright/scripts/pw.py" screenshot "https://example.com" --headed
```

## Data extraction

```bash
# Extract all prices from a page
python ".claude/skills/playwright/scripts/pw.py" extract "https://competitor.com/pricing" \
  -s ".price" --all -f json

# Extract navigation links
python ".claude/skills/playwright/scripts/pw.py" extract "https://example.com" \
  -s "nav a" --all -f csv -o "$OUTPUTS_DIR/browser/data/nav-links.csv"

# Wait for dynamic content before extracting
python ".claude/skills/playwright/scripts/pw.py" extract "https://app.example.com" \
  -s ".dashboard-metric" --all -f json --wait-for "selector:.dashboard-metric"
```

## Form filling

```bash
# Fill and submit a contact form
python ".claude/skills/playwright/scripts/pw.py" fill "https://example.com/contact" \
  --fields '{"#name": "Misha Hanin", "#email": "misha@31c.co", "#message": "Test"}' \
  --submit-selector "button[type=submit]" \
  --screenshot-after "$OUTPUTS_DIR/browser/screenshots/form-submitted.png"
```

## PDF generation

```bash
# Generate PDF (requires headless mode, which is default)
python ".claude/skills/playwright/scripts/pw.py" pdf "https://example.com/report" \
  -o "$OUTPUTS_DIR/browser/pdfs/report.pdf" --format A4

# Landscape PDF
python ".claude/skills/playwright/scripts/pw.py" pdf "https://example.com" \
  -o "$OUTPUTS_DIR/browser/pdfs/landscape.pdf" --landscape
```

## Batch screenshots

```bash
# From comma-separated URLs
python ".claude/skills/playwright/scripts/pw.py" batch-screenshots \
  "https://competitor1.com,https://competitor2.com,https://competitor3.com" \
  --output-dir "$OUTPUTS_DIR/browser/competitive-screenshots/"

# From a file (one URL per line)
python ".claude/skills/playwright/scripts/pw.py" batch-screenshots urls.txt \
  --full-page --output-dir "$OUTPUTS_DIR/browser/batch/"
```

## Website monitoring

```bash
# Check if site is up and a specific element exists
python ".claude/skills/playwright/scripts/pw.py" monitor "https://odun.one" \
  --check-selector ".hero-section" --expected-text "Deep Packet Intelligence"

# Monitor with screenshot capture
python ".claude/skills/playwright/scripts/pw.py" monitor "https://odun.one" \
  -o "$OUTPUTS_DIR/browser/monitoring/odun-status.png"
```

## Custom Playwright scripts

For complex multi-step workflows, write a custom Python script and execute it:

```bash
python ".claude/skills/playwright/scripts/pw.py" execute "/tmp/pw-custom-workflow.py"
```

When writing custom scripts, use this pattern:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Your automation logic here
    page.goto("https://example.com")
    page.click("button.login")
    page.fill("#username", "user")
    page.fill("#password", "pass")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")

    # Extract or screenshot
    print(page.title())
    page.screenshot(path=os.environ["OUTPUTS_DIR"] + "/browser/result.png")  # OUTPUTS_DIR resolved via get_outputs_dir(); never a bare cwd-relative outputs/

    browser.close()
```

Save custom scripts to `/tmp/pw-custom-*.py` to avoid cluttering the workspace.
