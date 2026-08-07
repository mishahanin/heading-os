---
slug: 2026-08-07-canopus-gap-and-skip
value: After a slice ships, probe can be asked whether the tests that now guard it would notice if the code under them were wrong, and a contract test that never ran can no longer pass for one that did.
approval_sha: cb9dd05f6284f7bb620ecae9488ffa2d235d1999
contract: tests/contract/2026-08-07-canopus-gap-and-skip/
plan_digest: sha256:347554c912d0e19a3cbb461221836348fa99153636c9560448c4e32dc31077e5
scrutinize_plan: none run. The operator read the plan and the red contract at step 4 and approved them directly on 2026-08-07; no adversarial pass was made over the plan itself. Recorded as it happened rather than left blank, because this field exists to make exactly that visible.
scrutinize_built: sha256:5087e510d2c1ad6f18273c1a70deef2b082457f846f8f96407344a6a62d4a2ac
undo: revert the 17 canopus commits in cb9dd05..HEAD, leaving 73d4be2 and 9b798a7 (sentinel, not this slice) in place; restore .claude/skills/canopus/SKILL.md and references/planning-gate.md from cb9dd05; delete tests/contract/2026-08-07-canopus-gap-and-skip/; re-run .venv/bin/python -m pytest tests/ -q
scope_digest: sha256:53f9600edc5a1d10ec6a13a4a584887b8bdc9a23deed38c39b6b13adf8512436
retired_sha: 1fb545875b2dd31123f2708c251fc973b8f9d9ae
promoted_to: tests/
---
