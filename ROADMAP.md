# Roadmap

HEADING OS is `v0.13.0`. The architecture, the security model, and the engine/data seam are stable and load-bearing. This roadmap sketches direction, not dates. Interfaces may change between minor versions while the project is pre-1.0.

Last reviewed against the code on 2026-08-17. Items below are marked from what the tree actually holds, not from memory of having done them.

## Shipped

Work that stood on this roadmap and is now in the engine, with where to read it.

- **Every enforcement layer carries a regression test that fails the build on drift.** 487 security tests live in [`tests/security/`](tests/security). They run on every commit in the `security-tests` CI job, one of the two checks required on `main`. Each seam layer has its own test: the bypass guard, the leak-path matrix, the credential patterns, the hook dispatcher, the injection corpus.
- **The skill catalog and the router are generated and measured, rather than hand-kept.** [`scripts/generate-skill-router.py`](scripts/generate-skill-router.py) generates the router registry from each skill's own frontmatter. A drift check refuses a hand-edit, in CI and at commit time. 70 skills carry a `triggers.json` routing corpus behind a coverage gate. A nightly judge run tracks routing accuracy.
- **Documentation depth, including the worked examples this roadmap asked for.** [`docs/EXTENDING.md`](docs/EXTENDING.md) walks a skill, a rule, and a script end to end. It then names the gates before "done". [`docs/HOOKS-REFERENCE.md`](docs/HOOKS-REFERENCE.md) and [`docs/RULES-REFERENCE.md`](docs/RULES-REFERENCE.md) carry the other two surfaces. The build standard has its own page in [`docs/CANOPUS.md`](docs/CANOPUS.md). A glossary and a troubleshooting guide were added for a newcomer.
- **More local-first retrieval and recall (v0.8.0).** Recall runs on a local semantic index over every store. It matches across Russian and English, at no marginal cost per search. Speech-to-text and layout-aware document reading are local too. A recording and a contract are read on the operator's own machine, with no cloud transcription service in the path.

## Now

- **Routing precision as usage surfaces edge cases.** The generated registry and the coverage gate hold the mechanical half. What remains is accuracy itself. The nightly trend needs a longer baseline measured by one judge before a number is published. An instrument swap once read as a five-point regression that had never happened.
- **A smoother first-run path for a fresh clone.** [`docs/QUICKSTART.md`](docs/QUICKSTART.md) is one page. Three defects an outside clone hit at v0.8.0 are fixed (issues #96, #97, #98). `/setup-wizard` polish is the half still open.
- **A worked example of adding a daemon or a scheduled task.** The parts exist: unit templates in `scripts/templates/systemd/`, nineteen installers, and two rules that make reboot survival and late-job behaviour mandatory rather than optional. What is missing is one walk-through that adds one end to end.

## Exploring

- Reducing the Claude Code coupling where it can be done without weakening the enforcement layers.
- Community-contributed skills, accepted by invitation through issues. The first outside contribution landed in v0.9.0: the session-checkpoint auto mode and its threshold prompt.

## Principles that will not change

- Engine and data stay separate, enforced in code.
- Outbound send stays human-gated.
- Console-first: no capability becomes browser-only.

Have an idea? Open an issue to discuss it before sending code. See CONTRIBUTING.md.
