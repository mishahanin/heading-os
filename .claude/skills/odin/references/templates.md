# Odin Brain File Templates
Consumed by: /odin SKILL.md
Last Updated: 2026-06-06

---

## Principle File

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[One clear statement]"
type: principle
sources: ["source-id-1"]
confidence: high|medium|low
keywords: [domain1, domain2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
# Optional temporal-validity fields (R11) -- never delete a superseded note:
superseded_by: "[slug-of-superseding-note]"  # optional
superseded_date: "YYYY-MM-DD"                 # optional; only when superseded_by is set
---

# [Title]

## Principle
[One clear statement]

## Evidence
[Source references with quotes]

## Application
[When and how to apply]

## Boundaries
[When it does NOT work]
```

---

## Position File

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[Clear stance]"
type: position
principles: ["id1", "id2"]
sources: ["id1", "id2"]
confidence: high|medium|low
keywords: [domain1, domain2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
revisit_when: "[Condition that would trigger reconsideration]"
# Optional temporal-validity fields (R11) -- never delete a superseded note:
superseded_by: "[slug-of-new-position]"  # optional; only when a NEW stance replaces this one (not mere enrichment)
superseded_date: "YYYY-MM-DD"            # optional; only when superseded_by is set
valid_until: "YYYY-MM-DD"                # optional; explicit expiry for a time-bound stance
---

# [Title]

## Position
[Clear stance]

## Argument
[Why - grounded in principles and sources]

## Known Weaknesses
[What could be wrong]

## Would Reconsider If
[Conditions for reconsideration]
```

---

## Episode File

Lived evidence — something that happened, dated. Distinct from a `source` (external material) and a `position` (committed belief). An episode is raw experiential evidence Odin can cite as supporting context; it never overrides a principle or position, and it only becomes a belief by maturing into a principle through `reflect` (CEO-confirmed). Schema is deliberately relaxed: NO mandatory `confidence` (an episode is a happening, not a conviction).

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[What happened - short]"
type: episode
date: YYYY-MM-DD
entities: [person-or-company, ...]   # who/what the episode is about (optional)
keywords: [domain1, domain2]
links: ["thread:slug", "crm:slug", "source:id"]   # cross-refs (optional)
status: raw            # raw | graduated   (graduated = matured into a principle)
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# [Title]

## What happened
[Concrete, dated account of the event.]

## What it suggests
[The residue - the emerging pattern or lesson. NOT a committed belief. This is
the seed that may later mature into a principle.]

## Links
[[thread-id|Label]], [[crm-id|Label]], etc.
```

---

## Conflict File

```yaml
---
id: "YYYYMMDDHHmmss"
title: "[What conflicts]"
type: conflict
side_a: {sources: ["id1"], principles: ["id2"]}
side_b: {sources: ["id3"], principles: ["id4"]}
status: open|resolved|watching
resolution: ""
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# [Title]

## The Conflict
[What exactly contradicts]

## Side A
[Arguments and sources]

## Side B
[Arguments and sources]

## Odin's Current Lean
[Where Odin leans and why]
```
