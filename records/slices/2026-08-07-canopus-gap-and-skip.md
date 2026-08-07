---
slug: 2026-08-07-canopus-gap-and-skip
value: After a slice ships, probe can be asked whether the tests that now guard it would notice if the code under them were wrong, and a contract test that never ran can no longer pass for one that did.
approval_sha: cb9dd05f6284f7bb620ecae9488ffa2d235d1999
contract: tests/contract/2026-08-07-canopus-gap-and-skip/
plan_digest: sha256:347554c912d0e19a3cbb461221836348fa99153636c9560448c4e32dc31077e5
scrutinize_plan: none run. The operator read the plan and the red contract at step 4 and approved them directly on 2026-08-07; no adversarial pass was made over the plan itself. Recorded as it happened rather than left blank, because this field exists to make exactly that visible.
scrutinize_built: sha256:5087e510d2c1ad6f18273c1a70deef2b082457f846f8f96407344a6a62d4a2ac
undo: 'git revert --no-commit $(git log --reverse --format=%H --grep ''^canopus('' cb9dd05f6284f7bb620ecae9488ffa2d235d1999..HEAD), then re-run .venv/bin/python -m pytest tests/ -q. The subject prefix is the selector rather than a count or a contiguous range, deliberately: two other tasks committed into this same range while the slice was building, one of them merging a feature branch, so any count is stale the moment it is written and any range sweeps work that is not this slice''s. Nothing named sentinel: or feat(recall): belongs to this slice, and 9b798a7 in particular must NOT be reverted, since it scrubbed a real identifier out of the public engine. The slice also touched .claude/skills/canopus/SKILL.md and references/planning-gate.md; git show cb9dd05f6284f7bb620ecae9488ffa2d235d1999:<path> reads either at the approval if a revert leaves them wrong.'
scope_digest: sha256:53f9600edc5a1d10ec6a13a4a584887b8bdc9a23deed38c39b6b13adf8512436
retired_sha: d5123a4e73d1fe3dda7b78972e8c6dc5105aa805
promoted_to: tests/
---
