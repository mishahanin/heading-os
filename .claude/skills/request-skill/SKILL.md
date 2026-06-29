---
name: request-skill
disable-model-invocation: true
description: "Request a new skill from the CEO - describe what it should do, and the request is emailed to the admin. EXPLICIT INVOCATION ONLY - sends external email."
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
argument-hint: "[description of the skill you need]"
allowed-tools: "Read, Bash(python3:*)"
model: haiku
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - request skill
    - I need a new skill
x-31c-capability:
  what: >
    Lets an executive request a new skill from the CEO - captures what it
    should do, a use case, and expected output, then emails the request to the
    admin.
  how: >
    Explicit invocation only - run /request-skill <description>. Sends external
    email via scripts/send-email.py to ceo@31c.io. If an existing skill
    already covers it, points you there instead of sending.
  when: >
    Use when you need a capability the workspace does not have yet. Building or
    editing skills directly is the CEO-only /skill-creator.
---
# Request New Skill

Submit a request for a new skill to be created by the CEO/admin. The request is sent via email to the admin with your description, use case, and workspace context.

## Variables

- `$ARGUMENTS` -- Description of the skill needed

## Workflow

1. If `$ARGUMENTS` is empty or too vague, ask:
   - "What should the skill do?" (one sentence)
   - "Give me an example of when you'd use it" (one scenario)
   - "What output do you expect?" (format/action)

2. Read `.workspace-identity.json` to get the requester's name and role.

3. Read `personal/context/personal-info.md` to get the requester's title and email.

4. Compose the request email:

```
Subject: HEADING OS Skill Request from {exec_name} ({exec_title})

Body (HTML):
<h2>New Skill Request</h2>
<p><strong>From:</strong> {exec_name} ({exec_title})</p>
<p><strong>Date:</strong> {today}</p>
<p><strong>Workspace:</strong> {exec_slug}</p>

<h3>Skill Description</h3>
<p>{description from $ARGUMENTS}</p>

<h3>Use Case</h3>
<p>{example scenario}</p>

<h3>Expected Output</h3>
<p>{expected output/action}</p>

<h3>Priority</h3>
<p>{requester's assessment: nice-to-have / would-help-daily / urgent-need}</p>
```

5. Show the drafted request to the user and confirm before sending. Then send via: `python3 scripts/send-email.py --to "ceo@31c.io" --subject "HEADING OS Skill Request from {name}" --body "{html_body}"` (recipient is hardcoded to the CEO; content is the user's own request).

6. Confirm to the user: "Your skill request has been sent to the CEO. You'll hear back when it's ready -- new skills are published via corporate sync."

## Rules

- Always get a clear description before sending. Don't send vague requests.
- Include the exec's context (role, title) so the CEO knows who is asking and why.
- The email goes to ceo@31c.io only.
- If the exec describes something that an EXISTING skill already does, tell them: "This is already available as /[skill-name]. Try it!" and do NOT send the email.
