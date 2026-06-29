---
name: linkedin-archive
description: Move the latest published LinkedIn content (article/post/comment) plus any attached images from outputs/content/linkedin into the typed datastore archive. Auto-fires on English or Russian "I published it on LinkedIn" phrasing. Asks for the content type if it cannot be inferred. Confirms before mutating disk.
argument-hint: "[slug]"
allowed-tools: "Read, Bash(python3:*), AskUserQuestion"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state: ["datastore/content/linkedin-archive/", "outputs/content/linkedin/"]
  triggers:
    - "i published this on linkedin"
    - "linkedin post is live"
    - "live on linkedin"
    - "опубликовал на linkedin"
    - "выложил на linkedin"
    - "запостил на linkedin"
x-31c-capability:
  what: >
    Moves a just-published LinkedIn piece (article, post, or comment) plus any attached images out of staging and into the typed datastore archive at datastore/content/linkedin-archive/.
  how: >
    Auto-fires on "I published it on LinkedIn" (English or Russian), or run /linkedin-archive. Resolves type and images, shows a dry-run plan, and waits for approval before moving via scripts/linkedin-archive.py (optional auto-commit).
  when: >
    Use right after publishing to file the content. For drafting a post use /linkedin-post; this skill never drafts, it only archives one piece per invocation.
---

# LinkedIn Archive

Move a just-published LinkedIn piece from staging (`outputs/content/linkedin/` plus optional explicit images) into the typed datastore archive (`datastore/content/linkedin-archive/{articles|posts|comments}/{slug}/`).

Script: `scripts/linkedin-archive.py`.

## Workflow

### 1. Resolve type if needed

If the staged file's name does not contain `_linkedin-(post|article|comment)_` and no `type:` frontmatter is set, ask the CEO via `AskUserQuestion`:

- Post
- Article
- Comment

Pass the answer to the script as `--type <choice>`.

### 2. Resolve images if any

Ask the CEO via `AskUserQuestion`:

- No image
- One image
- More than one

If "One image" or "More than one": send a follow-up plain-text turn ("Reply with the path(s) to the image(s)") and wait for the CEO's free-form reply with one path per line. `AskUserQuestion` is multiple-choice only; path collection always happens in a separate text turn.

Pass each image as a separate `--image <path>` argument. Default is no image.

### 3. Dry-run

```bash
python scripts/linkedin-archive.py [--slug <slug>] [--type <type>] [--image <path>]...
```

Exit codes that need handling:

- `2`: nothing found - tell the CEO and stop.
- `3`: destination conflict - surface the existing folder, ask how to proceed.
- `5`: type still ambiguous - re-ask.
- `6`: image path does not exist - re-ask.
- `7`: source untracked - tell CEO to `git add`, then retry.

### 4. Confirm

Show the plan. Ask via `AskUserQuestion`:

- Approve and execute (with auto-commit)
- Approve and execute (no commit)
- Cancel

Wait for explicit approval.

### 5. Execute

```bash
python scripts/linkedin-archive.py [...flags] --execute [--commit]
```

### 6. Confirm done

Report destination folder, files moved (count), and the commit hash if `--commit` was used.

## Undo

The script does not provide an `--undo` flag. To revert a wrong move:

- If `--commit` did NOT fire: `git mv` files back manually, then `rmdir` the empty destination folder.
- If `--commit` fired and nothing else has been committed since: `git reset --hard HEAD~1`. Verify `git status` first; this is destructive.
- If exit code 8 (`--commit` ran but auto-commit failed): files are moved and staged in the index, but no commit landed. Either inspect the failure (often a pre-commit hook), fix it, and run `git commit` manually; or `git restore --staged <paths>` to unstage and decide what to do next.
- If exit code 9 (git command timed out): nothing on disk has been mutated when the timeout fires before any `git mv`; if the timeout fires mid-sequence, run `git status` to see what was already moved and `git mv` back as needed.

## Voice

Concise, operational. No flourish.

## NEVER

- Skip the confirm step.
- Move more than one piece per invocation.
- Use anything other than the script for the actual move.
- Add a trigger phrase that does not contain the word "linkedin" (English or transliterated).
