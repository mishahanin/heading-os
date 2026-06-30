<!-- version: 1.0.0 | last-updated: 2026-07-01 -->
# Security model

How HEADING OS protects your data and your principal. The controls here are
mechanical, enforced by code and tests, not by policy prose. This page explains the
model; the [SECURITY.md](https://github.com/mishahanin/heading-os/blob/main/SECURITY.md)
policy covers how to report a vulnerability.

The whole design answers one question: an agent reads your private data and reads
untrusted text from the outside world all day. How do you keep it from being steered
into leaking the first through a channel to the second?

---

## 1. The threat: the lethal trifecta

An AI agent becomes dangerous to its principal when three capabilities meet in the
same run:

1. **Access to private data** (CRM, the brain, mail, the datastore).
2. **Exposure to untrusted content** (inbound mail, web pages, messages, anything a
   stranger wrote that the agent reads and might be steered by).
3. **The ability to send externally** (email, messages, any outbound channel).

Any one or two legs is recoverable. All three together is the failure mode: untrusted
content instructs the agent, the agent reaches private data, the agent exfiltrates it,
with no human in the loop. HEADING OS handles private data and reads untrusted content
constantly, so legs 1 and 2 cannot be removed without removing the assistant's value.
The mitigation is therefore to keep **leg 3 permanently human-gated**, and to keep the
private data physically out of the shareable engine.

---

## 2. Engine and data, kept apart

The engine clone holds no private or personal data, and your data cannot leave on a
push, regardless of how a file was written. This is not a convention you have to
remember: it is enforced by several mechanical layers, each catching a different way
the boundary could be crossed:

- a static **bypass guard** against direct engine-root writes,
- a **leak guard** that classifies and refuses data-class files in the engine,
- a **data-path redirect** so writes resolve into your data overlay,
- a **build partition** keeping engine and data artifacts separate,
- a runtime **tree-clean check**,
- a **content guard** that scans engine files for real data-class entities,
- and an **unbypassable push-time wall**, in pure code with no skip flag.

The authoritative specification, the honest boundaries, and the proof (the layers are
asserted by tests, not just claimed) are in the
**[engine/data segregation contract](engine-data-segregation-contract.html)**. Read it
before adding code that writes files or touches the data seam.

---

## 3. Outbound send is always human-gated

Every skill, script, and daemon inherits one control: anything that can send to the
outside world is **drafted, queued, and recommended**, never sent autonomously. A
human approves before anything leaves.

This is enforced in code, not prose. Any action type that can send is floored to the
`gated` tier no matter what a config file claims, and an unknown or unclassified type
also resolves `gated` (fail-safe). A `config/tool-risk.json` edited to mark email
"autonomous" still resolves `gated`. The test suite asserts that a tampered ledger
cannot auto-send: the ledger is data, the send-gate is code.

An advisory layer may inspect a queued draft and attach a second opinion, but it can
only annotate. The human click is the only path from draft to sent.

---

## 4. The Action Queue (the approval surface)

The Action Queue is the one lane where proactive skills (cold-sweep, email-intel,
viraid) deposit a drafted action for your go or no-go. It is terminal-native and
daemon-free: the queue file is the source of truth, and you drive it from the CLI or
from chat.

```bash
uv run python scripts/action-queue.py list          # what is waiting
uv run python scripts/action-queue.py show <id>     # inspect one draft
uv run python scripts/action-queue.py approve <id>  # SENDS, synchronously, watched
uv run python scripts/action-queue.py edit <id>     # adjust a draft before approving
uv run python scripts/action-queue.py dismiss <id>  # drop it
uv run python scripts/action-queue.py retry <id>    # re-send a failed one
```

`/queue` is the chat equivalent. Each card carries a risk tier:

| Tier | Meaning | Flow |
|---|---|---|
| `autonomous` | read-only / display (a note, an alert) | surfaced; no click; never sends |
| `notify` | reversible state edit | auto-applied, with one-click undo |
| `gated` | irreversible outbound send | hard review gate: sends only on your explicit approve |

Your typed `approve` IS the human approval click, and the send happens in that same
command. There is no autonomous background send; the gate holds even with every daemon
down.

---

## 5. Secrets never reach a remote

Credentials load only from a gitignored `.env`, never from a tracked file. Two gates
back this:

- **Commit-time scan** (`pre-commit`): a fast local warning that content-scans staged
  files for secrets. It is bypassable (`git commit --no-verify` skips every hook), so
  treat it as a warning, not the wall. **Never pass `--no-verify`.**
- **Push-time content scan** (`push-all.py`): the authoritative, unbypassable gate. It
  scans every file about to leave the machine and refuses the push on any hit. Pure
  code on the sanctioned push path, no skip flag, so it catches a secret even if a
  commit hook was bypassed or absent.

Run `pre-commit install` once per clone to arm the commit-time gate. If a secret is
ever exposed, treat it as compromised: rotate it first, then scrub it from history.

---

## 6. Other controls

- **Hidden-character policy.** Generated text carries zero invisible Unicode
  (zero-width spaces, soft hyphens, and the like). `scripts/sanitize-text.py` scans for
  it, and a post-write hook flags contamination.
- **Forbidden-pattern gates.** The test and lint suite blocks the usual dangerous
  patterns: `eval` / `exec` on input, `pickle` on untrusted data, `shell=True`,
  unsafe YAML loading, disabled TLS verification, and similar.
- **No hope-based waiting.** Every must-complete step (every push) runs under a
  progress watchdog that declares a hang only on real inactivity and verifies its
  postcondition, rather than trusting a wall-clock timeout or a bare exit code.

---

## 7. Your responsibilities

The engine enforces a great deal, but the first line of defense is you:

- Keep API keys, tokens, and passwords in the gitignored `.env` only.
- Keep your real data in your **private** data repository, not the engine clone.
- Run `pre-commit install` once per clone.
- Rotate any exposed secret before scrubbing it.
- When changing code that touches authentication, sending, or the data seam, expect
  (and apply) extra scrutiny.

---

## 8. Reference

| File | Role |
|---|---|
| [`SECURITY.md`](https://github.com/mishahanin/heading-os/blob/main/SECURITY.md) | Reporting policy + posture summary |
| [`engine-data-segregation-contract.md`](engine-data-segregation-contract.html) | The engine/data guarantee, layers, and proof |
| `scripts/action-queue.py` | The terminal-native approval surface |
| `scripts/utils/tool_risk.py`, `config/tool-risk.json` | The tier model and the send-gate invariant |
| `scripts/push-all.py` | The push path with the unbypassable content scan |
| `scripts/sanitize-text.py` | Hidden-character scanner |

---

*HEADING OS · Security model · maintained by 31 Concept · see also
[Extending the engine](EXTENDING.html) for the developer-side gates and
[Architecture](ARCHITECTURE.html) for how the controls sit in the whole.*
