# Configuration

Everything in `config/` that shapes how the engine classifies, routes, and fails over. These files are engine code (shareable, no secrets). Real credentials live in the gitignored `.env` and `.sessions/`, never here. See [integrations and credentials](INTEGRATIONS-SETUP.html) for where secrets go.

## The files

| File | What it drives |
|------|----------------|
| `routing-map.yaml` | The engine / data / corporate classifier. |
| `tool-risk.json` | The Action Queue risk ledger and the send-gate. |
| `memory-index.yaml` | The `/recall` associative-memory index. |
| `llm_fallback.yaml` | Model failover chains. |
| `wizard-questions.yaml` | The `/setup-wizard` question bank. |
| `schemas/` | JSON schemas for CRM records. |
| `wizard-templates/` | Document templates the setup wizard fills in. |
| `skill-custom/` | Local per-clone skill overrides. |

## `routing-map.yaml`: the classifier

The single source of truth for where a record belongs. Every path resolves to one of three destinations:

- `engine`: code, shareable, eventually public (`.heading-os`).
- `private`: your private data, never shared (`.heading-os-data`).
- `corporate`: content shared down to a team, not public.

Resolution is longest-match: the most-specific matching key wins, otherwise the map `default` applies. The default is `engine`, but every data directory (`crm/`, `knowledge/`, `outputs/`, `threads/`, `context/`) carries an explicit `private` rule, so real data fails closed. A broken map forces the default to `private`, the safe direction. This is the file the [classification rule](RULES-REFERENCE.html), the leak guard, and the push-time wall all read. Add a rule by appending its path under `rules:`, then run `scripts/classification-health.py` to verify.

## `tool-risk.json`: the send-gate ledger

Maps each Action Queue `action_type` to a risk tier: `autonomous` (read-only, no click), `notify` (reversible, auto-applied with one-click undo), or `gated` (irreversible outbound send, requires an explicit human click). The invariant: any `action_type` in the `send_capable` set floors to `gated` no matter what its `tiers` entry says, and an unknown type also resolves `gated`. This is what makes the [lethal-trifecta control](SECURITY-MODEL.html) impossible to defeat by editing config. The ledger is data; the send-gate is code, and tests assert a tampered ledger cannot auto-send.

## `memory-index.yaml`: the recall index

Drives `scripts/memory-index.py`, the local associative-memory index behind `/recall`: which workspace layers to embed, the on-machine embedder (`bge-m3` via Ollama), the salience threshold, and the air-gap denylist that keeps sensitive layers out of the index. Runs entirely on-machine at zero API cost. See [memory and ODIN](memory-odin.html).

The `audit:` block tunes the metamemory scan: `audit.near_dup_threshold` (float, default `0.86`) is the cosine-similarity threshold above which two auto-memory files are flagged as a near-duplicate merge candidate. The scan (`scan_redundancy()` in `scripts/utils/memory_health.py`) is advisory-only — it surfaces candidates in the weekly `scripts/memory-hygiene.py` report for a human to resolve via `/dream`, never in the hygiene exit-code gate and never auto-applied.

## `llm_fallback.yaml`: model failover

Maps each Anthropic model tier to an ordered fallback chain. When a primary call fails with a retriable error (5xx, 429, timeout, connection reset), the caller cascades through the chain instead of failing the whole operation. See [AI models](MODELS-SETUP.html).

## `wizard-questions.yaml` and `wizard-templates/`

The `wizard-questions.yaml` bank is the authoritative set of questions `/setup-wizard` asks a fresh clone. Question IDs are immutable once shipped: adding questions is safe, removing them is not. The `wizard-templates/` directory holds the document templates the wizard fills from your answers (business info, personal info, voice, calendar policy). See [make it yours](MAKE-IT-YOURS.html).

## `schemas/`

JSON schemas for CRM records: `crm-contact.schema.json`, `crm-relationship.schema.json`, and `crm-address-book.schema.json`. They validate the shape of contact data written by the `/crm` skill.

## `skill-custom/`

A place for local, per-clone skill overrides that should not ship with the engine. See its `README.md` for the override convention.

## Related

- [Rules reference](RULES-REFERENCE.html): the rules that read these files.
- [Security model](SECURITY-MODEL.html): the classifier and the send-gate in context.
- [Integrations and credentials](INTEGRATIONS-SETUP.html): where secrets go instead of `config/`.
