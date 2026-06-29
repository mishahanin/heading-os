# Per-skill customization layer

Last Updated: 2026-06-04

This directory holds per-skill customization overrides resolved by `scripts/resolve_customization.py`. It lets a fleet exec tune a shared corporate skill — voice, output paths, persistent facts, extra activation steps — without forking the skill and breaking the corporate sync.

## The three layers

A skill that opts into customization ships a `customize.toml` in its own directory. The resolver merges three layers, highest priority last (later wins):

| # | Layer | Path | Committed? | Synced to execs? |
|---|---|---|---|---|
| 1 | Skill defaults | `.claude/skills/{skill}/customize.toml` | yes (with the skill) | yes (corporate) |
| 2 | Team / org | `config/skill-custom/{skill}.toml` | yes | yes (corporate) |
| 3 | Personal | `config/skill-custom/{skill}.user.toml` | no (gitignored) | never |

The skill name is the basename of the skill directory. Layers 2 and 3 are optional — a missing file is simply an empty layer.

## How to author an override (exec-facing)

To change how a skill behaves for you only, create `config/skill-custom/{skill}.user.toml`. It is gitignored, so it stays on your machine and is never pushed anywhere. For a team-wide change that should reach every exec, edit `config/skill-custom/{skill}.toml` instead (committed, corporate-synced — CEO-approved changes only).

Example — give `/deep-think` a personal default and a couple of always-loaded facts:

```toml
# config/skill-custom/deep-think.user.toml
[workflow]
default_depth = "exhaustive"
persistent_facts = [
  "I prefer second-order analysis surfaced explicitly",
  "Default currency for figures is AED",
]
```

Resolve it to see the merged result:

```
python scripts/resolve_customization.py --skill .claude/skills/deep-think
python scripts/resolve_customization.py --skill .claude/skills/deep-think --key workflow.persistent_facts
```

## Merge rules (structural, no field-name special-casing)

- **Scalars** (string, int, bool, float): the higher layer wins.
- **Tables**: deep merge — keys present in only one layer survive; shared keys recurse.
- **Arrays of tables** where every item carries the *same* identifier (`code`, or `id`): merge by that key — matching keys replace, new keys append.
- **All other arrays** (plain values, or mixed identifier keys): append — base items then override items.

There is no removal mechanism: an override cannot delete a base item. To suppress a default, override it by `code` with a no-op value, or fork the skill.

## Notes

- `*.user.toml` is gitignored (see `.gitignore`); never commit one.
- A malformed team/user TOML is a non-fatal warning — the layer is skipped and the skill proceeds on the layers that parsed. A malformed *defaults* file is fatal (the skill author must fix it).
- Skills consume this on a best-effort basis: if resolution fails, the skill proceeds with its built-in defaults and never blocks.
