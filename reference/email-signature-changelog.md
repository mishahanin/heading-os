<!-- version: 1.0.0 | last-updated: 2026-04-28 -->

# Email Signature Changelog

Version history for `reference/email-signature.html`. The HTML file itself does not carry an in-file version marker because every byte is rendered into outgoing email and arbitrary HTML comments could leak into recipient mail clients. This sidecar tracks changes instead.

Last Updated: 2026-04-28

Consumed by: `scripts/send-email.py` (which embeds the signature with inline CID-attached images on every outgoing message)

---

## Format

Each entry: date, version (semver), what changed, who approved.

```
## YYYY-MM-DD - v{major}.{minor}.{patch}

- Description of change
- Approval: {Misha | <name>}
```

PATCH = typo fix or pixel-level adjustment that does not change visual identity.
MINOR = new field added, link refreshed, image swapped, copy reworded.
MAJOR = layout reorganisation, brand identity change, contact info structural change.

---

## History

## 2026-04-28 - v1.0.0

- Initial changelog seeded as part of audit finding L-6 closure
- Current signature reflects:
  - 31 Concept logo (orange corner, GT Standard wordmark)
  - Misha's name + title (Founder & CEO)
  - ceo@31c.io email
  - 31c.io website link
  - Confidentiality notice in small print
- Approval: Misha (signature already in production; this entry is a baseline record, not a change)

---

## How to update

When `reference/email-signature.html` changes:

1. Verify the change renders correctly in Outlook (web), Gmail (web), and Apple Mail. The signature is rendered with inline CID images, so test by sending a real message via `scripts/send-email.py --to <test-address>` and inspecting the received HTML.
2. Bump the appropriate semver level in this changelog and add a new entry at the top of the History section with the date, the change description, and "Approval: Misha".
3. Touch `reference/email-signature.html` in the same commit so the file's git mtime reflects the change.

If a change is approved verbally and not committed within 24 hours, log the verbal approval as a "Pending commit" line under the History entry so the audit trail captures intent.
