---
slug: 2026-08-17-checkpoint-unattended
value: A session with work left does not halt because our own hook halted it, and a compaction keeps the objective, the decisions and the next action instead of whatever the summariser happened to keep.
approval_sha: 0c560c734140c83f37c82a9d9c956d7c2090fb0a
contract: tests/contract/2026-08-17-checkpoint-unattended/
plan_digest: sha256:caca4a3cfb51ab6f7c426938402678f7b7a5c68c421ab540b9b4aa6474e7ed76
scrutinize_plan: none run as a skill. The operator read the criteria, the probe table and the gate results at step 4, then decided the mode's name and the switch's shape himself. Recorded as it happened.
scrutinize_built: 'inline self-review only, no subagent and no blinded pass: the session''s instructions forbade dispatching agents. Four defects found and fixed, two of them by live runs rather than by the suite - a state-file race, an auto-undo asymmetry, dead paths in the compaction brief, and a fact label that over-promised. The operator found two more by reading the rendered output: a wrapper that restated the body, and a menu that never said where compaction comes from.'
undo: Revert the implementation commit. The PreCompact registration and the raised Stop timeout go with it, and the mode has no persistent state outside a session's own state file. A session left in the mode ends with 'python scripts/checkpoint-paths.py --unattended off'; otherwise the flag dies with the window.
scope_digest: sha256:b9a07f1f084d85331345103359a14ae4e36c22e9772c6df0d9458bed779bac92
---
