<!-- version: 2.1.0 | last-updated: 2026-08-20 -->
# Record Classification Policy

Last Verified: 2026-08-20

Every workspace record resolves to one of three **routing destinations** (HEADING OS
engine/data separation):

- **engine** — code, shareable to everyone, eventually public (`.heading-os`).
- **private** — CEO data, never shared (`.heading-os-data`).
- **corporate** — content shared down to executives via `heading-os-corporate`.

Single classification input: `config/routing-map.yaml`, resolved by
`get_routing_destination()` in `scripts/utils/workspace.py`; health check
`scripts/classification-health.py`. The longest matching rule key wins; an unmatched
path defaults to `engine`, and a *broken* map fails closed to `private`.

The rest lives in `docs/engine-data-segregation-contract.md` § Record classification:
the full resolution order and its worked example, the older two-value
`corporate` / `ceo-only` collapse still used by exec-sync tooling, what
`/push-updates` publishes, and why some `rules:` keys point at directories that do not
exist on disk yet.

## When Creating New Files

**Always ask "engine, private, or corporate?"** when creating new files in these
directories (the answer is not obvious from the path):
- `context/` (new context documents — mostly `private`, a few `corporate` carve-outs)
- `reference/` (new reference files — `engine` template vs `private` CEO content)
- `knowledge/` (`private` by default; `knowledge/shared/` is `corporate`)
- `datastore/` (`corporate` by default; CEO-only subtrees are `private`)

**Never ask** — these always resolve `private` (CEO data):
- `outputs/` — CEO deliverables
- `crm/contacts/` — personal CRM data
- `plans/`, `threads/` — session/operational state

**Never ask** — these always resolve `engine` (shared code, public):
- `.claude/rules/`, `.claude/skills/`, `.claude/hooks/` — workspace logic
- `scripts/` — utility scripts (CEO-personal scripts get an explicit `private` rule)
- `docs/` — except `docs/superpowers/`, `docs/security/`, CEO-ADMIN/USAGE guides (`private`)
- `config/` — except per-instance/identity configs (`private`)

## After Classification

If the CEO classifies a new file as **private** or **corporate** (i.e. not the engine
default), add an explicit rule for its path under `rules:` in `config/routing-map.yaml`,
then run `scripts/classification-health.py` to confirm. A new file left at the engine
default needs no entry only when it is genuinely shareable code.
