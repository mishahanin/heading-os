# Roadmap

HEADING OS is `v0.1.0`. The architecture, the security model, and the engine/data seam are stable and load-bearing. This roadmap sketches direction, not dates. Interfaces may change between minor versions while the project is pre-1.0.

## Now
- Hardening the skill catalog and the router as usage surfaces edge cases.
- Expanding the `tests/security` suite so every enforcement layer has a regression test that fails the build on drift.
- Documentation depth: more worked examples of extending the engine with a new skill, rule, and hook.

## Next
- A smoother first-run path for a fresh clone: `/setup-wizard` polish and a shorter zero-to-running walk-through.
- More local-first options for retrieval and recall.
- Clearer extension points for daemons and scheduled tasks.

## Exploring
- Reducing the Claude Code coupling where it can be done without weakening the enforcement layers.
- Community-contributed skills, accepted by invitation through issues.

## Principles that will not change
- Engine and data stay separate, enforced in code.
- Outbound send stays human-gated.
- Console-first: no capability becomes browser-only.

Have an idea? Open an issue to discuss it before sending code. See CONTRIBUTING.md.
