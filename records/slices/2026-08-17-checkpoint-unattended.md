---
slug: 2026-08-17-checkpoint-unattended
value: A session with work left does not halt because our own hook halted it, and a compaction keeps the objective, the decisions and the next action instead of whatever the summariser happened to keep.
approval_sha: 0c560c734140c83f37c82a9d9c956d7c2090fb0a
contract: tests/contract/2026-08-17-checkpoint-unattended/
plan_digest: sha256:caca4a3cfb51ab6f7c426938402678f7b7a5c68c421ab540b9b4aa6474e7ed76
scrutinize_plan: none run as a skill. The operator read the criteria, the probe table and the gate results at step 4, then decided the mode's name and the switch's shape himself. Recorded as it happened.
scrutinize_built: 'two passes. First inline self-review, which found four defects, two of them by live runs rather than by the suite. Then a blinded adversarial pass by three agents over the implementation commit, each with a distinct lens and none given the reasoning behind the code: it found nine more, two measured rather than argued, including one that made the mode inert in 28 of this project''s 44 real sessions and that the frozen contract could not catch because its fixture predated the operation it missed. All nine fixed with a test each. What no pass here corrected: the planner and the implementer were the same context.'
undo: Revert the implementation commit. The PreCompact registration and the raised Stop timeout go with it, and the mode has no persistent state outside a session's own state file. A session left in the mode ends with 'python scripts/checkpoint-paths.py --unattended off'; otherwise the flag dies with the window.
scope_digest: sha256:b9a07f1f084d85331345103359a14ae4e36c22e9772c6df0d9458bed779bac92
retired_sha: 0ae816fb891a9c6f8ef4e1fc2741c5c5e03e147f
promoted_to: tests/test_checkpoint_unattended_contract.py
---

Retired 2026-08-20. `retired_sha` names the last commit at which the contract stood exactly as approved, the authoring convention `canopus_check._window_end` documents alongside a removal sha. After it the product moved past the frozen form on purpose: the no-progress stall fuse was replaced by an explicit done marker on 2026-08-19 and the continuation prose was rewritten twice, so the approved bytes leave four tests red at HEAD against behaviour that was changed deliberately. The file is promoted into the ordinary suite unchanged in substance and is maintained there from now on. The promotion is the step that never happened when the slice shipped; its absence held CI red for three days.
