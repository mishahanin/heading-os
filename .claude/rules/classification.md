<!-- version: 2.0.0 | last-updated: 2026-06-14 -->
# Record Classification Policy

Last Verified: 2026-06-14

Every workspace record resolves to one of three **routing destinations** (HEADING OS
engine/data separation):

- **engine** — code, shareable to everyone, eventually public (`.heading-os`).
- **private** — CEO data, never shared (`.heading-os-data`).
- **corporate** — content shared down to executives via `heading-os-corporate`.

The older two-value label still used by exec-sync tooling — **corporate** (shared with
execs) vs **ceo-only** (CEO-private) — is now a thin collapse of the three:
`private → ceo-only`; `corporate → corporate`; `engine → corporate` (engine code is the
most-shared thing, so it is not "CEO-private").

Single classification input: `config/routing-map.yaml` (HEADING OS step 7 — replaced
`config/classification.json`, removed 2026-06-14).
Shared resolver: `get_routing_destination()` / `get_classification()` in `scripts/utils/workspace.py`.
Health check: `scripts/classification-health.py`.

## Resolution Order

When a path could match multiple rules in `routing-map.yaml`, the **most-specific
(longest matching) rule key wins**; otherwise the map `default` applies.

1. **Exact / longest-prefix rule key** in `routing-map.yaml` `rules:`. A key ending in
   `/` matches as a directory prefix; a key without a trailing `/` matches that exact
   file or that path as a prefix.
2. **Map default** — `engine`. Unmatched paths resolve shareable, NOT private.

This default direction is deliberate: every DATA directory (`crm/`, `knowledge/`,
`outputs/`, `threads/`, `context/`, `plans/`, `templates/`, `_archive/`, …) carries an
explicit `private` rule so real data fail-closes; only code-ish paths fall through to
the engine default. The hard fail-closed case is a *broken* `routing-map.yaml`:
`load_routing_map()` then forces default `private` so an unreadable map treats everything
as CEO data.

Example: `knowledge/shared/ai/notes.md` → `corporate` because `knowledge/shared/`
(longer) beats the broader `knowledge/` → `private`. `knowledge/ai/notes.md` →
`private` (→ ceo-only) per the `knowledge/` rule.

Adding a rule: append the path under `rules:` in `config/routing-map.yaml` with its
destination and run `scripts/classification-health.py` to verify resolution.

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

## Push Updates

When the CEO invokes `/push-updates`, files whose **routing destination is
`corporate`** that changed since the last build are published to `heading-os-corporate`; a
BUILD.json manifest tracks the build number and execs pull on their hourly sync.

> **Narrowed at cutover (step 8, 2026-06-14):** publish-corporate ships routing
> `corporate` ONLY — content, not code (datastore, knowledge/shared, the two context
> carve-outs, crm config/aliases/address-book, corporate/ daemon config). Engine code
> is NOT published here; execs receive it by cloning the engine repo (`.heading-os`).
> This replaced the prior pre-separation collapse (`corporate` ∪ `engine`). The
> two-value `get_classification` still exists for memory-index/health; publish uses
> the three-value `get_routing_destination` directly.

## Note: pre-creation guards for on-demand directories

Some `rules:` keys map a directory to `private` before that directory exists on disk
(an on-demand path created only when a feature first runs). This is intentional and the
safe direction — the guard ensures the first write lands `private` rather than falling
through to the engine default. A rule key with no current on-disk directory is expected,
not a defect; do not re-flag it as a broken reference.
