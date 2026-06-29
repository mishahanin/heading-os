# Playwright Selector Patterns

CSS selector patterns and Playwright locator strategies for the `extract` / `click` / `fill` subcommands.

Consumed by: `.claude/skills/playwright/SKILL.md`.
Last Updated: 2026-06-16

## Selector Priority (most stable to least)

1. **Data test IDs** -- `[data-testid="login-button"]` -- most stable, won't break with UI changes
2. **Role-based** -- `role=button[name="Submit"]` -- semantic, accessibility-friendly
3. **Text-based** -- `text="Sign In"` or `text=/sign in/i` -- readable but fragile if text changes
4. **CSS selectors** -- `button.primary`, `#submit-btn` -- familiar but break with class/ID changes
5. **XPath** -- `//div[@class="card"]//h2` -- powerful but verbose, last resort

## Common CSS Patterns

```
# Buttons
button[type="submit"]
button.btn-primary
input[type="submit"]
a.button

# Form inputs
input[name="email"]
input[type="password"]
textarea[name="message"]
select[name="country"]

# Navigation
nav a
header a
.navbar-nav a
ul.menu li a

# Content
h1, h2, h3
.article-content p
.card .card-title
table tbody tr

# Pricing pages
.price, .pricing-amount
.plan-name
.feature-list li
```

## Playwright Locator Methods (for custom scripts)

```python
# Preferred -- semantic locators
page.get_by_role("button", name="Submit")
page.get_by_text("Sign In")
page.get_by_label("Email")
page.get_by_placeholder("Enter your email")
page.get_by_test_id("login-form")

# CSS/XPath
page.locator("button.primary")
page.locator("//div[@class='card']")

# Chaining
page.locator(".card").filter(has_text="Premium").locator("button")

# Nth element
page.locator(".item").nth(0)
page.locator(".item").first
page.locator(".item").last
```

## Wait Strategies

```python
# Wait for element to appear
page.wait_for_selector(".dashboard-loaded")

# Wait for navigation
page.wait_for_url("**/dashboard")

# Wait for network idle (all requests finished)
page.wait_for_load_state("networkidle")

# Wait for specific response
page.wait_for_response("**/api/data")
```

## Tips

- Prefer `--wait-for networkidle` for pages that load data via AJAX
- Use `--wait-for selector:.content` when you know a specific element signals readiness
- For SPAs (Single Page Apps), always wait for content selectors rather than page load events
- When extracting data, use `--all` flag to get all matching elements
- Test selectors in browser DevTools (F12 > Console > `document.querySelectorAll("your-selector")`) before using in automation
