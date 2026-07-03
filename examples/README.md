# Examples: a data-less clone you can run against

This tree is bundled read-only demo data. A fresh engine clone with no private
data repository wired up falls back to it automatically, so you can run a skill
end to end before creating your own data folder.

## Why this works

The engine resolves your private data through a single seam, `get_data_root()`.
It tries, in order: the `HEADING_OS_DATA` environment variable, a sibling
`../.heading-os-data` repository, and finally this `examples/` directory. On a
clone with none of the first two, the data root **is** `examples/`, and every
read-only skill reads the sample files here (`crm/contacts/`, `threads/`,
`knowledge/`, `context/`).

You can force this mode explicitly on any clone:

```bash
export HEADING_OS_DATA="$(pwd)/examples"
```

## A full run: the relationship radar (`/crm`)

`/crm radar` surfaces contacts you have drifted out of touch with. It is
read-only, needs no external API, no models, and no network, so it is the
cleanest skill to see first. In chat you would type `/crm radar`; the skill runs
the same engine you can run directly:

```bash
uv run python scripts/crm-health.py
```

**What it reads:** every contact file under `crm/contacts/`. Here that is the one
bundled sample, `crm/contacts/EXAMPLE-contact.md`:

```markdown
---
name: Example Contact
company: Example Co
tier: prospect
---
```

**What you see** (against the demo data):

```
31C Relationship Radar

RED - Overdue
  Example Contact (Example Co) -  - no recorded touch (cadence: 14)

Total: 1 contacts tracked | 1 red | 0 yellow | 0 green
```

The sample contact has no recorded interaction, so the radar files it under RED
(overdue) against its 14-day cadence. On your own data root the same command
reads your real contacts and reports their health the same way.

## Where your real data goes

When you are ready to leave demo mode, create your own private data folder:

```bash
uv run python scripts/init-data.py
```

From then on `get_data_root()` resolves to your repository, never to this tree.
The engine never contains your data, and this `examples/` folder stays exactly as
shipped. Full setup is in [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md); the house
vocabulary (engine, data, data root, skill) is in
[docs/GLOSSARY.md](../docs/GLOSSARY.md).
