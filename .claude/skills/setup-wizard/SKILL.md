---
name: setup-wizard
disable-model-invocation: true
description: Interactive setup wizard for HEADING OS and 31C exec workspaces. Walks the user through ~22 questions (public) or ~9 questions (exec), enriches short answers into rich docs, captures API keys securely, and produces a completion-% dashboard on re-runs. Refuses to run on the CEO master workspace. EXPLICIT INVOCATION ONLY - mutates workspace skeleton (.env, reference/, context/, personal/).
argument-hint: "(no arguments)"
allowed-tools: "Read, Bash(python:*), Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: false
  shared_state: [".setup/answers.json", ".env", "reference/", "context/", "personal/"]
  triggers: ["setup-wizard", "set up my workspace", "configure my workspace", "onboard me", "finish my setup", "personalize workspace"]
x-31c-capability:
  what: >
    Walks a fresh workspace through an interactive per-question setup - about 22
    questions for a public HEADING OS clone or 9 for a 31C exec - enriches short answers
    into full voice/personal/business docs, captures API keys securely into .env,
    and shows a completion-percent dashboard on re-runs.
  how: >
    Explicit invocation only - type /setup-wizard. All writes go through
    scripts/apply-wizard-answers.py into .setup/answers.json and the personalized
    target files. It refuses to run on the CEO master workspace.
  when: >
    Use on a newly provisioned exec workspace or a public HEADING OS clone to personalize
    it, or re-run anytime to edit answers. Not for the CEO master workspace.
---
# Setup Wizard

This skill runs an interactive, per-question setup flow that personalizes a fresh workspace. See `docs/superpowers/specs/2026-04-24-setup-wizard-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-04-24-setup-wizard-design.md`) for the full contract.

## Preconditions

Before starting, Claude MUST:

1. Run `python scripts/apply-wizard-answers.py --status` to detect the audience and current completion state.
2. If exit code is `4` (ceo-master detected), print the abort message from stderr AND stop immediately. Do not proceed. Do not offer `--force-ceo-master` to the end user; it is a test-only override.
3. If exit code is `1`, surface the schema error and stop.

## Source of Truth

**All question data (prompt, example, help, type, id) is returned by `--status` in each `rows` entry.** Use those fields directly. Do NOT `cat`, `Read`, or otherwise open `wizard-questions.yaml` yourself - the YAML lives in different paths depending on workspace layout (`config/` on CEO master, `corporate/config/` on exec) and the apply script already resolves the correct path via its internal resolver. Reading the YAML directly will fail on exec workspaces.

## First-Run Flow (empty answers.json)

Announce:

> Welcome. I'll ask you ~N questions to set up your workspace. After each one I'll show you an example answer so you know what I'm looking for.
>
> Rules:
>   - Type an answer and press Enter.
>   - Type `skip` if you don't want to answer - you can come back later.
>   - Type `example` if you want me to read the example again.
>   - Type `help` if you want me to explain what the question is for.
>   - Nothing is saved until you confirm each answer.
>
> Ready? Let's start.

Then walk through each question from the `--status` rows where `status` is `pending`, in bank order.

## Per-Question Flow

Render each question:

```text
Q<N> of <TOTAL> - <label>
---------------------------
<prompt>

  Example: <example>

Your answer: _
```

Wait for user input. Branch by type.

### Placeholder or List

- `skip` -> `python scripts/apply-wizard-answers.py --skip <id>` -> confirm -> next.
- `example` -> re-render example -> stay.
- `help` -> render the question's `help` field -> stay.
- Any other -> invoke:

```bash
python scripts/apply-wizard-answers.py --question <id> --value-from-stdin
```

with JSON on stdin: `{"value": "<text>"}` (placeholder) or `{"value": ["a","b","c"]}` (list, split on comma).

### Rich

After the user's short answer:

1. Announce: `Drafting your <doc name>...`
2. Compose a full document inline (200-400 words for voice/personal/business; 100-200 for calendar).
3. Display the draft in a visible block, then:

```text
Does this sound like you?
  [y] yes, save it
  [t] mostly - I'll tell you what to tweak
  [r] no, let me give you a different short answer and redraft
  [s] skip this question for now
```

Branches:

- `y`: invoke apply with `{"value":"<short>","draft":"<full>","draft_approved":true}`. Confirm. Advance.
- `t`: ask "What should I change?", read tweak notes, re-render inline, show the same four-choice prompt.
- `r`: invoke apply with `{"archive_draft":true}` to preserve current draft into `draft_previous`, then re-ask the short question prefilling the user's previous short answer.
- `s`: invoke `apply-wizard-answers.py --skip <id>`. Advance.

### Secret

Render prompt + example + storage reassurance.

User pastes key (or `skip`).

1. Light validation (prefix + length). On fail, prompt re-enter.
2. Offer optional live ping (default = no):

```text
Verify this key works now? (y/N)
```

If `y`:

```bash
python scripts/wizard-verify-key.py --provider anthropic --key "<key>"
```

Interpret exit code: `0` validated, `1` invalid (loop back), `2` rate-limited (proceed with note), `3` unknown (proceed with note).

3. On approval, invoke apply with `{"value":"<key>"}`. Confirm with masked value. Advance.

## Re-Run Flow (dashboard)

If any row has `status=answered`, render the dashboard directly:

```text
----------------------------------------------------------
 HEADING OS Setup Wizard - Welcome back, <name>
----------------------------------------------------------
 Audience: <Exec (31C) | Public>
 Progress: <req.answered> of <req.total> required - <completion_pct>%

 Required
  <numbered rows with [x]/[-]/[ ] glyph + display_value>

 Optional (don't affect %)
  <rows>

 What do you want to do?
   [number] - edit that answer
   'all'    - walk through every question again
   'apply'  - re-apply current answers to files
   'done'   - exit

 > _
```

Commands:

- `<number>`: find row, run per-question flow, re-render dashboard.
- `all`: walk all questions in bank order as re-edit.
- `apply`: run `python scripts/apply-wizard-answers.py --all`. Re-render.
- `done`: print one-line summary with full path to `.setup/answers.json`. Exit.
- `help`: re-print commands.

## What This Skill Does NOT Do

- Does NOT invoke other skills.
- Does NOT write to `.setup/answers.json` directly. All writes go through `apply-wizard-answers.py`.
- Does NOT make network calls except the optional live-ping via `wizard-verify-key.py`.
- Does NOT accept `--force-ceo-master`. If audience detection reports ceo-master, abort.

## Files Touched (indirectly)

- Reads: `.setup/answers.json` (via `--status`). Question bank content is delivered inside `--status` rows; Claude never reads the YAML directly.
- Writes (via apply script): `.setup/answers.json`, personalized target files per question, `.env`.

## Completion

On 100% completion, print:

> Setup complete. Your workspace is ready. You can run `/setup-wizard` anytime to edit answers.
