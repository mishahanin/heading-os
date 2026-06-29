---
name: rfp-response
description: Produce a structured response to a formal Request for Proposals or government tender. Maps requirements to ODUN.ONE capabilities, drafts executive summary, technical response, commercial proposal, and compliance matrix. Use when responding to an RFP, ITT, government procurement, or formal bid. Trigger when the user says "RFP response", "respond to this tender", "bid response", "government tender", or pastes RFP requirements.
argument-hint: "[client]"
allowed-tools: "Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - RFP response
    - tender response
    - bid response
    - government tender
x-31c-capability:
  what: >
    Produces a structured response to a formal RFP or government tender - requirements-to-ODUN.ONE mapping, executive summary, technical response, company credentials, commercial proposal, and a compliance matrix.
  how: >
    Run /rfp-response [client] and paste the RFP requirements; it grounds claims in context/business-info.md and the DataStore, then drafts the full response for your review before submission.
  when: >
    Use for a formal RFP, ITT, or procurement bid. For an informal commercial proposal with pricing use /proposal; for an MOU or term sheet use /partnership-doc.
---
# RFP Response

Produce a structured response to a formal Request for Proposals or government tender.

## Variables

client: [Organization name and country]
deadline: [Submission deadline]
rfp_context: [Summary of requirements, or paste the RFP document below]

---

[PASTE RFP DOCUMENT OR REQUIREMENTS HERE]

---

## Instructions

Before drafting, read:
- `context/business-info.md` — ODUN.ONE modules, use case library, technical specifications, company credentials
- `reference/dpi-market-intelligence.md` — Market positioning and certifications
- `reference/billion-growth-playbook.md` — Pricing model
- `context/strategy.md` — Competitive positioning
- `context/current-data.md` — Proof points (flagship deployment, patent filings, Tribe metrics)

---

## Phase 1: Requirements Analysis

Map each stated requirement to ODUN.ONE capabilities:

| Requirement | Module/Use Case | Notes |
|-------------|-------------|-------|
| [Req 1] | [DataONE / ControlONE / etc.] | [Full/partial compliance, notes] |

Identify:
- Where we're fully compliant (highlight)
- Where we need clarification
- Any requirements where we exceed expectations

---

## Phase 2: Executive Summary

Draft a compelling 1-page executive summary that:
- Opens with why 31C is uniquely positioned for this opportunity
- Emphasizes sovereignty architecture (data never leaves sovereign control)
- References the flagship deployment as proof of production readiness
- States our commitment to this client's long-term success (Partnership for Life)

---

## Phase 3: Technical Response

For each requirement:
- Solution description (which module and use case)
- How it meets the requirement
- Any relevant technical specifications
- Deployment model (on-premises, sovereign architecture)

---

## Phase 4: Company Credentials

- 31C: Cybersecurity company, multi-site HQ, [N]+ Tribe members
- ODUN.ONE: Production deployment in [region] ([date])
- Partner ecosystem: distribution partners ([N]+ dealers), strategic technology alliance
- Research Lab: PhDs in quantum physics, AI/ML, mathematics, cryptography; 1 patent filed
- Hiring standard: 1,500+ interviews for ~20 hires

---

## Phase 5: Commercial Proposal

- Pricing: Based on bandwidth × [$ per Gb/s] perpetual license
- Support: Year 1 included; Years 2-5 at 20% annually
- Bundle recommendation: [Essential / Professional / Enterprise]
- Implementation: Timeline and support model

---

## Phase 6: Compliance Matrix

| RFP Requirement | Compliant | Module/Use Case | Notes |
|----------------|-----------|--------------|-------|

---

## Phase 6.5: Structural pass (optional)

For a long deliverable, you may run the `/editorial-review` structural checklist over the assembled draft before finalizing, to verify the argument arc, claim-to-evidence linkage, and section hierarchy. Reference: `reference/editorial-review.md`. The prose-level voice pass (`humanization.md`) runs as usual after. Skip when the draft is short or already tight.

---

**Output:** Full structured RFP response. Misha reviews and approves before submission.
