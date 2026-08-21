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

Maps each Action Queue `action_type` to one of three risk tiers. `autonomous` is read-only and needs no click. `notify` is reversible, auto-applied with one-click undo. `gated` is an irreversible outbound send and requires an explicit human click. The invariant: any `action_type` in the `send_capable` set floors to `gated` no matter what its `tiers` entry says, and an unknown type also resolves `gated`. This is what makes the [lethal-trifecta control](SECURITY-MODEL.html) impossible to defeat by editing config. The ledger is data; the send-gate is code, and tests assert a tampered ledger cannot auto-send.

## `memory-index.yaml`: the recall index

Drives `scripts/memory-index.py`, the local associative-memory index behind `/recall`. It sets which workspace layers to embed, the on-machine embedder (`bge-m3` via Ollama), and the salience threshold. It also carries the air-gap denylist that keeps sensitive layers out of the index. Runs entirely on-machine at zero API cost. See [memory and ODIN](memory-odin.html).

The `audit:` block tunes the metamemory scan. `audit.near_dup_threshold` (float, default `0.86`) is a cosine-similarity threshold. Above it, two auto-memory files are flagged as a near-duplicate merge candidate. The scan (`scan_redundancy()` in `scripts/utils/memory_health.py`) is advisory-only. It surfaces candidates in the weekly `scripts/memory-hygiene.py` report for a human to resolve via `/dream`. It never enters the hygiene exit-code gate, and it is never auto-applied.

## `llm_fallback.yaml`: model failover

Maps each Anthropic model tier to an ordered fallback chain. A primary call can fail with a retriable error: a 5xx, a 429, a timeout, or a connection reset. The caller then cascades through the chain instead of failing the whole operation. See [AI models](MODELS-SETUP.html).

## `wizard-questions.yaml` and `wizard-templates/`

The `wizard-questions.yaml` bank is the authoritative set of questions `/setup-wizard` asks a fresh clone. Question IDs are immutable once shipped: adding questions is safe, removing them is not. The `wizard-templates/` directory holds the document templates the wizard fills from your answers (business info, personal info, voice, calendar policy). See [make it yours](MAKE-IT-YOURS.html).

## `schemas/`

JSON schemas for CRM records: `crm-contact.schema.json`, `crm-relationship.schema.json`, and `crm-address-book.schema.json`. They validate the shape of contact data written by the `/crm` skill.

## `skill-custom/`

A place for local, per-clone skill overrides that should not ship with the engine. See its `README.md` for the override convention.

## Data root: pinning `HEADING_OS_DATA`

The engine and your private data are two sibling repositories: the engine clone (`.heading-os`) and the data overlay (`.heading-os-data`). The resolver `get_data_root()` in `scripts/utils/paths.py` picks the overlay in this order, first hit wins:

1. The `HEADING_OS_DATA` environment variable, when it points at a real directory.
2. In-tree data, when it already lives inside the engine clone (the transitional single-workspace case).
3. The sibling `../.heading-os-data`.
4. Demo mode, the read-only bundled `examples/`.

A standard side-by-side layout needs no configuration, because the sibling step resolves it automatically.

Set `HEADING_OS_DATA` only to pin the binding explicitly. Pin it when the data repo is not a direct sibling, when you run several clones, or as insurance so resolution can never drift. Two ways to set it, and they are not equal. An exported shell variable (`export HEADING_OS_DATA="/absolute/path/to/.heading-os-data"` in `~/.bashrc`) is the stronger form: every process, hooks and daemons included, inherits it before any Python import runs. A line in the gitignored `.env` is only partial. Only callers that run `load_env()` first honor it, so it does not cover hooks or externally launched daemons. Use the `.env` line as belt-and-suspenders, not as the sole pin. The path is absolute. If you relocate the workspace, update it in both places, or the stale value points at a directory that no longer exists. Confirm the current resolution with `python3 -c "from scripts.utils.paths import get_data_root, data_root_is_demo as d; print(get_data_root()); print('demo?', d())"`, which prints your `.heading-os-data` path and `demo? False` on a correct setup.

## Related

- [Rules reference](RULES-REFERENCE.html): the rules that read these files.
- [Security model](SECURITY-MODEL.html): the classifier and the send-gate in context.
- [Integrations and credentials](INTEGRATIONS-SETUP.html): where secrets go instead of `config/`.
